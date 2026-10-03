//! Using a move: Showdown's runMove, useMove/useMoveInner, getTarget,
//! getMoveTargets, trySpreadMoveHit and its hit steps, and the hit loop.
//!
//! Only what `support` allows is reachable: attacks and status moves whose
//! effects are stat stages, statuses, flinch, Protect, recoil, drain and
//! healing.

use super::conditions::{status_from_id, HitRes};
use super::state::{Mon, VolatileId, ACTIVE_PER_SIDE};
use super::{Battle, BattleError, MonRef, Res};
use crate::damage::{self, ActiveMove, Combatant, DamageCtx, Outcome, SideState, Status};
use crate::dex::{Category, Dex, HitEffect, MoveData, MoveId, MoveTarget};

/// A move being used: Showdown's ActiveMove plus per-use flags.
pub(super) struct MoveUse {
    pub am: ActiveMove,
    pub data: &'static MoveData,
    /// `move.selfDropped`: the `self` boosts already applied this use.
    pub self_dropped: bool,
    /// `move.spreadHit`: aimed at more than one target (spread damage).
    pub spread: bool,
}

/// The move a queued Move action uses: Struggle when the slot is `usize::MAX`.
pub(super) fn move_for_slot(m: &Mon, slot: usize) -> MoveId {
    match m.moves.get(slot) {
        Some(s) => s.id,
        None => Dex::get().move_id("struggle").expect("Struggle"),
    }
}

impl Battle {
    /// Showdown's `getAtLoc` from `user`'s point of view.
    fn at_loc(&self, user: MonRef, loc: i8) -> Option<MonRef> {
        let side = if loc < 0 { user.side } else { 1 - user.side };
        self.occupant(side, loc.unsigned_abs() as usize - 1)
    }

    fn loc_of(&self, user: MonRef, target: MonRef) -> i8 {
        super::choice::loc_of(user.side, target.side, self.mon(target).position)
    }

    /// `side.foes()`: the opposing actives with HP left.
    fn foes(&self, user: MonRef) -> Vec<MonRef> {
        let side = 1 - user.side;
        (0..ACTIVE_PER_SIDE)
            .filter(|&p| self.sides[side].occupant(p).is_some_and(|m| m.hp > 0))
            .map(|p| self.mon_ref(side, p))
            .collect()
    }

    /// `adjacentAllies()`: the other active on the user's side, if it has HP.
    fn adjacent_allies(&self, user: MonRef) -> Vec<MonRef> {
        (0..ACTIVE_PER_SIDE)
            .filter(|&p| self.sides[user.side].occupant(p).is_some_and(|m| m.hp > 0 && m.uid != user.uid))
            .map(|p| self.mon_ref(user.side, p))
            .collect()
    }

    /// `getRandomTarget`.
    fn random_target(&mut self, user: MonRef, target: MoveTarget) -> Option<MonRef> {
        match target {
            MoveTarget::SelfTarget | MoveTarget::All | MoveTarget::AllySide | MoveTarget::AllyTeam | MoveTarget::AdjacentAllyOrSelf => Some(user),
            MoveTarget::AdjacentAlly => {
                let allies = self.adjacent_allies(user);
                if allies.is_empty() {
                    None
                } else {
                    Some(allies[self.chance.sample(allies.len())])
                }
            }
            _ => {
                let foes = self.foes(user);
                if foes.is_empty() {
                    let side = 1 - user.side;
                    self.occupant(side, 0)
                } else {
                    Some(foes[self.chance.sample(foes.len())])
                }
            }
        }
    }

    /// `getTarget`.
    fn get_target(&mut self, user: MonRef, target_type: MoveTarget, loc: i8) -> Option<MonRef> {
        let self_loc = self.loc_of(user, user);
        if matches!(target_type, MoveTarget::AdjacentAlly | MoveTarget::Any | MoveTarget::Normal) && loc == self_loc {
            return None;
        }
        let user_pos = self.mon(user).position;
        if target_type != MoveTarget::RandomNormal && super::choice::valid_target_loc(loc, user_pos, target_type) && loc != 0 {
            if let Some(t) = self.at_loc(user, loc) {
                let tm = self.mon(t);
                if tm.fainted && t.side == user.side {
                    return Some(if target_type == MoveTarget::AdjacentAllyOrSelf { user } else { t });
                }
                if !tm.fainted {
                    return Some(t);
                }
            }
        }
        self.random_target(user, target_type)
    }

    /// `getMoveTargets` for moves that target Pokemon.
    fn get_move_targets(&mut self, user: MonRef, am: &ActiveMove, target: Option<MonRef>) -> Vec<MonRef> {
        match am.target {
            MoveTarget::AllAdjacent => {
                let mut t = self.adjacent_allies(user);
                t.extend(self.foes(user));
                t
            }
            MoveTarget::AllAdjacentFoes => self.foes(user),
            _ => {
                let mut target = target;
                let needs_retarget = match target {
                    None => true,
                    Some(t) => self.mon(t).fainted && t.side != user.side,
                };
                if needs_retarget {
                    target = self.random_target(user, am.target);
                }
                match target {
                    Some(t) if !self.mon(t).fainted => vec![t],
                    _ => Vec::new(),
                }
            }
        }
    }

    /// A damage-calculation view of the field with `attacker` hitting `defender`.
    fn damage_view(&self) -> [Option<Combatant>; 4] {
        let mut out: [Option<Combatant>; 4] = Default::default();
        for side in 0..2 {
            for pos in 0..ACTIVE_PER_SIDE {
                let Some(m) = self.sides[side].occupant(pos) else { continue };
                if m.hp == 0 {
                    continue;
                }
                let mut c = Combatant::new(m.species, m.stats, m.ability, m.item);
                c.types = m.types;
                c.hp = m.hp;
                c.boosts = [0, m.boosts[0], m.boosts[1], m.boosts[2], m.boosts[3], m.boosts[4]];
                c.status = m.status;
                c.speed = m.speed;
                c.active_turns = m.active_turns;
                c.times_attacked = m.times_attacked;
                out[side * 2 + pos] = Some(c);
            }
        }
        out
    }

    fn damage_ctx<'a>(&self, view: &'a [Option<Combatant>; 4], attacker: MonRef, defender: MonRef, crit: bool, spread: bool) -> DamageCtx<'a> {
        DamageCtx {
            actives: [view[0].as_ref(), view[1].as_ref(), view[2].as_ref(), view[3].as_ref()],
            attacker: attacker.side * 2 + self.mon(attacker).position,
            defender: defender.side * 2 + self.mon(defender).position,
            weather: self.field.weather,
            terrain: self.field.terrain,
            sides: [0, 1].map(|s| SideState { fainted: self.sides[s].total_fainted, ..SideState::default() }),
            crit,
            spread,
            hit: 1,
        }
    }

    /// `runMove`.
    pub(super) fn run_move(&mut self, user: MonRef, slot: usize, target_loc: i8) -> Res<()> {
        self.mon_mut(user).active_move_actions += 1;
        let move_id = move_for_slot(self.mon(user), slot);
        let data = Dex::get().move_data(move_id);
        let target = self.get_target(user, data.target, target_loc);
        if !self.before_move(user, move_id, data) {
            self.mon_mut(user).move_this_turn_result = Some(false);
            return Ok(());
        }
        let is_struggle = data.id == "struggle";
        if !is_struggle {
            let m = self.mon_mut(user);
            let s = &mut m.moves[slot];
            s.used = true;
            if s.pp == 0 {
                m.move_this_turn_result = Some(false);
                return Ok(());
            }
            s.pp -= 1;
        }
        let m = self.mon_mut(user);
        m.last_move = Some(move_id);
        m.move_this_turn = Some(move_id);

        // useMove
        m.move_this_turn_result = None;
        let result = self.use_move(user, move_id, target)?;
        let m = self.mon_mut(user);
        if m.move_this_turn_result.is_none() {
            m.move_this_turn_result = Some(result);
        }
        self.faint_messages()?;
        Ok(())
    }

    /// `useMoveInner` for moves that target Pokemon.
    fn use_move(&mut self, user: MonRef, move_id: MoveId, target: Option<MonRef>) -> Res<bool> {
        let data = Dex::get().move_data(move_id);
        let base_target = data.target;
        // ModifyType / ModifyMove, through the damage module so both agree.
        let view = self.damage_view();
        // ModifyType/ModifyMove only need someone on the field as the nominal
        // target; a fainted (ally) target isn't in the damage view.
        let defender = target
            .filter(|&t| self.mon(t).hp > 0)
            .or_else(|| self.foes(user).first().copied())
            .unwrap_or(user);
        let ctx = self.damage_ctx(&view, user, defender, false, false);
        let am = damage::prepare_move(&ctx, move_id).map_err(|e| BattleError::Unsupported(e.0))?;
        // frz's onModifyMove: a defrosting move thaws its user.
        if data.flags.has("defrost") && self.mon(user).status == Status::Freeze {
            self.cure_status(user);
        }
        self.choice_lock(user, move_id);
        let mut target = target;
        if am.target != base_target {
            target = self.random_target(user, am.target);
        }
        if self.mon(user).hp == 0 {
            return Ok(false);
        }
        let Some(target) = target else { return Ok(false) };
        if matches!(am.target, MoveTarget::All | MoveTarget::AllySide | MoveTarget::FoeSide | MoveTarget::AllyTeam) {
            return Ok(self.try_move_hit(user, data));
        }
        let targets = self.get_move_targets(user, &am, Some(target));
        if targets.is_empty() {
            return Ok(false);
        }
        let last_target = *targets.last().expect("targets");
        let mut mv = MoveUse { am, data, self_dropped: false, spread: false };
        let result = self.try_spread_move_hit(user, &mut mv, targets)?;
        if result {
            self.after_move_secondary_self(user, last_target, data.category == Category::Status);
        }
        Ok(result)
    }

    /// `tryMoveHit` for moves that hit a side or the field: Tailwind and
    /// Trick Room (runMoveEffects' sideCondition / pseudoWeather).
    fn try_move_hit(&mut self, user: MonRef, data: &MoveData) -> bool {
        match data.id.as_str() {
            "tailwind" => {
                let side = &mut self.sides[user.side];
                if side.tailwind > 0 {
                    return false;
                }
                side.tailwind = 4;
                true
            }
            "trickroom" => {
                // onFieldRestart ends it; using it again succeeds either way.
                self.field.trick_room = if self.field.trick_room > 0 { 0 } else { 5 };
                true
            }
            other => unreachable!("support lets through side/field move {other}"),
        }
    }

    /// `trySpreadMoveHit` and its hit steps.
    fn try_spread_move_hit(&mut self, user: MonRef, mv: &mut MoveUse, targets: Vec<MonRef>) -> Res<bool> {
        let data = mv.data;
        let mut targets = targets;
        mv.spread = targets.len() > 1;

        // Try and PrepareHit
        if data.id == "fakeout" && self.mon(user).active_move_actions > 1 {
            return Ok(false);
        }
        if data.has_key("stallingMove") && !(self.will_act() && self.stall_move(user)) {
            return Ok(false);
        }

        let mut failed = false;
        let mut step = |b: &mut Battle, targets: &mut Vec<MonRef>, f: &mut dyn FnMut(&mut Battle, MonRef) -> HitRes| {
            let results: Vec<HitRes> = targets.iter().map(|&t| f(b, t)).collect();
            failed |= results.contains(&HitRes::Bool(false));
            let mut i = 0;
            targets.retain(|_| {
                i += 1;
                results[i - 1].hit()
            });
        };

        // hitStepTryHitEvent: Protect blocks moves with the protect flag.
        step(self, &mut targets, &mut |b, t| {
            if b.mon(t).volatiles.has(VolatileId::Protect) && data.flags.has("protect") {
                HitRes::NotFail
            } else {
                HitRes::Bool(true)
            }
        });
        // hitStepTypeImmunity
        if !targets.is_empty() {
            let view = self.damage_view();
            let am = mv.am.clone();
            step(self, &mut targets, &mut |b, t| {
                let ctx = b.damage_ctx(&view, user, t, false, false);
                HitRes::Bool(damage::run_immunity(&ctx, &am))
            });
        }
        // hitStepTryImmunity: powder moves don't affect Grass types.
        step(self, &mut targets, &mut |b, t| {
            HitRes::Bool(!(data.flags.has("powder") && t != user && Dex::get().immune_to("powder", b.mon(t).types)))
        });
        // hitStepAccuracy
        step(self, &mut targets, &mut |b, t| HitRes::Bool(b.accuracy_check(user, t, data)));

        if targets.is_empty() {
            if !failed {
                self.mon_mut(user).move_this_turn_result = None;
            }
            return Ok(false);
        }
        self.move_hit_loop(user, mv, targets)
    }

    /// hitStepAccuracy for one target.
    fn accuracy_check(&mut self, user: MonRef, t: MonRef, data: &MoveData) -> bool {
        let Some(acc) = data.accuracy else { return true };
        let always = (data.id == "toxic" && self.mon(user).has_type(Dex::get().type_id("Poison").expect("Poison")))
            || (data.target == MoveTarget::SelfTarget && data.category == Category::Status);
        if always {
            return true;
        }
        // ModifyBoost: Unaware ignores the other side's accuracy/evasion.
        let unaware = |b: &Battle, r: MonRef| Dex::get().ability(b.mon(r).ability).id == "unaware";
        let acc_boost = if unaware(self, t) { 0 } else { self.mon(user).boosts[5] as i32 };
        let eva_boost = if unaware(self, user) { 0 } else { self.mon(t).boosts[6] as i32 };
        let boost = (acc_boost.clamp(-6, 6) - eva_boost).clamp(-6, 6);
        let mut accuracy = acc as u32;
        if boost > 0 {
            accuracy = accuracy * (3 + boost as u32) / 3;
        } else if boost < 0 {
            accuracy = accuracy * 3 / (3 + (-boost) as u32);
        }
        self.chance.chance(accuracy, 100)
    }

    /// hitStepMoveHitLoop, for a single hit (multi-hit moves aren't supported).
    fn move_hit_loop(&mut self, user: MonRef, mv: &mut MoveUse, targets: Vec<MonRef>) -> Res<bool> {
        let effect = &mv.data.primary;
        let (move_damage, hit_targets) = self.spread_move_hit(targets.iter().map(|&t| Some(t)).collect(), user, mv, effect, true, false, false)?;
        if move_damage.iter().all(|&d| d == HitRes::Bool(false)) {
            return Ok(false);
        }
        let mut total = 0u32;
        for &d in &move_damage {
            if let HitRes::Num(n) = d {
                total += n;
            }
        }
        self.each_update();
        self.faint_messages()?;
        if total > 0 {
            self.apply_recoil(user, mv.data, total);
        }
        for (i, t) in hit_targets.iter().enumerate() {
            if let Some(t) = *t {
                if t != user && matches!(move_damage[i], HitRes::Num(_)) {
                    let m = self.mon_mut(t);
                    m.times_attacked = m.times_attacked.saturating_add(1);
                }
            }
        }
        self.each_update();
        // afterMoveSecondaryEvent: frz's onAfterMoveSecondary thaws the target.
        if mv.data.has_key("thawsTarget") {
            for t in hit_targets.into_iter().flatten() {
                if self.mon(t).status == Status::Freeze {
                    self.cure_status(t);
                }
            }
        }
        Ok(true)
    }

    /// `applyRecoilDamage`.
    fn apply_recoil(&mut self, user: MonRef, data: &MoveData, total: u32) {
        if data.id == "struggle" {
            // Struggle's recoil is direct damage, a quarter of max HP.
            let max = self.mon(user).max_hp() as f64;
            let recoil = ((max / 4.0).round() as u32).max(1);
            if self.mon(user).hp > 0 {
                self.apply_damage(user, recoil);
            }
        } else if let Some((n, d)) = data.recoil {
            let recoil = ((total as f64 * n as f64 / d as f64).round() as u32).max(1);
            self.effect_damage(user, recoil);
        }
    }

    /// `spreadMoveHit`: one hit of `effect` (the move itself, a secondary or
    /// a `self` effect) on each target. Returns the per-target results and
    /// the targets still standing in for later steps (None: dropped).
    #[allow(clippy::too_many_arguments)]
    fn spread_move_hit(
        &mut self,
        targets: Vec<Option<MonRef>>,
        user: MonRef,
        mv: &mut MoveUse,
        effect: &'static HitEffect,
        primary: bool,
        is_secondary: bool,
        is_self: bool,
    ) -> Res<(Vec<HitRes>, Vec<Option<MonRef>>)> {
        let mut targets = targets;
        // TryHit (no supported move has one) and TryPrimaryHit (no handlers).
        let mut damage = vec![HitRes::Bool(true); targets.len()];
        for i in 0..targets.len() {
            if !damage[i].truthy() {
                targets[i] = None;
            }
        }

        // getSpreadDamage
        let spread = mv.spread;
        if primary && mv.data.category != Category::Status {
            let view = self.damage_view();
            for i in 0..targets.len() {
                let Some(t) = targets[i] else { continue };
                let crit_ratio = mv.data.crit_ratio.clamp(0, 4) as usize;
                let crit = match mv.data.will_crit {
                    Some(c) => c,
                    None => crit_ratio > 0 && self.chance.chance(1, [0, 24, 8, 2, 1][crit_ratio]),
                };
                let ctx = self.damage_ctx(&view, user, t, crit, spread);
                let outcome = damage::damage_for(&ctx, &mv.am).map_err(|e| BattleError::Unsupported(e.0))?;
                damage[i] = match outcome {
                    Outcome::Damage(rolls) => HitRes::Num(rolls[self.chance.random(16) as usize]),
                    Outcome::Immune => HitRes::Bool(false),
                    Outcome::NoDamage => HitRes::Undefined,
                };
            }
        } else {
            for i in 0..targets.len() {
                if targets[i].is_some() {
                    damage[i] = HitRes::Undefined;
                }
            }
        }
        for i in 0..targets.len() {
            if damage[i] == HitRes::Bool(false) {
                targets[i] = None;
            }
        }

        // battle.spreadDamage
        for i in 0..targets.len() {
            let d = match damage[i] {
                HitRes::Num(n) => n,
                _ => continue,
            };
            let Some(t) = targets[i] else {
                damage[i] = HitRes::Num(0);
                continue;
            };
            if self.mon(t).hp == 0 {
                damage[i] = HitRes::Num(0);
                continue;
            }
            if !self.mon(t).is_active {
                damage[i] = HitRes::Bool(false);
                continue;
            }
            let d = if d == 0 { 0 } else { self.on_move_damage(t, d.max(1)).max(1) };
            let dealt = self.apply_damage(t, d);
            if dealt != 0 {
                let m = self.mon_mut(t);
                m.hurt_this_turn = Some(m.hp);
            }
            damage[i] = HitRes::Num(dealt);
            if dealt > 0 {
                if let Some((n, den)) = mv.data.drain {
                    let amount = (dealt as f64 * n as f64 / den as f64).round() as u32;
                    self.heal(user, amount);
                }
            }
        }
        for i in 0..targets.len() {
            if damage[i] == HitRes::Bool(false) {
                targets[i] = None;
            }
        }

        self.run_move_effects(&mut damage, &targets, mv, effect, primary)?;
        for i in 0..targets.len() {
            if !damage[i].hit() {
                targets[i] = None;
            }
        }

        if let Some(self_effect) = effect.self_effect.as_deref() {
            if !mv.self_dropped {
                self.self_drops(&targets, user, mv, self_effect, is_secondary)?;
            }
        }
        if primary {
            self.secondaries(&targets, user, mv)?;
        }

        // DamagingHit: a Fire-type attack thaws a frozen target.
        if !is_secondary && !is_self && mv.am.move_type == Dex::get().type_id("Fire").expect("Fire") && mv.am.category != Category::Status {
            for i in 0..targets.len() {
                if let (Some(t), HitRes::Num(_)) = (targets[i], damage[i]) {
                    if self.mon(t).status == Status::Freeze {
                        self.cure_status(t);
                    }
                }
            }
        }
        Ok((damage, targets))
    }

    /// `runMoveEffects`.
    fn run_move_effects(
        &mut self,
        damage: &mut [HitRes],
        targets: &[Option<MonRef>],
        mv: &MoveUse,
        effect: &HitEffect,
        primary: bool,
    ) -> Res<()> {
        let mut did_anything = damage.iter().copied().reduce(HitRes::combine).unwrap_or(HitRes::Undefined);
        for i in 0..targets.len() {
            let Some(t) = targets[i] else { continue };
            let mut did_something = HitRes::Undefined;
            if !effect.boosts.is_empty() && !self.mon(t).fainted {
                let r = self.boost(t, &effect.boosts);
                did_something = did_something.combine(r);
            }
            if let (true, Some((n, d))) = (primary, mv.data.heal) {
                if !self.mon(t).fainted {
                    let m = self.mon(t);
                    let healed = if m.hp >= m.max_hp() {
                        HitRes::Bool(false)
                    } else {
                        let amount = (m.max_hp() as f64 * n as f64 / d as f64).round() as u32;
                        self.heal(t, amount)
                    };
                    if !healed.hit() {
                        damage[i] = damage[i].combine(HitRes::Bool(false));
                        did_anything = did_anything.combine(HitRes::Null);
                        continue;
                    }
                    did_something = HitRes::Bool(true);
                }
            }
            if let Some(status) = &effect.status {
                let status = status_from_id(status).ok_or_else(|| BattleError::Unsupported(format!("status {status}")))?;
                let r = self.try_set_status(t, status);
                if !r && mv.data.primary.status.is_some() {
                    damage[i] = damage[i].combine(HitRes::Bool(false));
                    did_anything = did_anything.combine(HitRes::Null);
                    continue;
                }
                did_something = did_something.combine(HitRes::Bool(r));
            }
            if let Some(v) = &effect.volatile_status {
                let id = VolatileId::parse(v).ok_or_else(|| BattleError::Unsupported(format!("volatile {v}")))?;
                let r = self.add_volatile(t, id);
                did_something = did_something.combine(r);
            }
            // onHit: Protect and Detect start (or extend) the stall counter.
            if primary && mv.data.has_key("stallingMove") {
                self.add_volatile(t, VolatileId::Stall);
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if did_something == HitRes::Undefined {
                did_something = HitRes::Bool(true);
            }
            damage[i] = damage[i].combine(if did_something == HitRes::Null { HitRes::Bool(false) } else { did_something });
            did_anything = did_anything.combine(did_something);
        }
        Ok(())
    }

    /// `selfDrops`.
    fn self_drops(&mut self, targets: &[Option<MonRef>], user: MonRef, mv: &mut MoveUse, self_effect: &'static HitEffect, is_secondary: bool) -> Res<()> {
        for t in targets {
            if t.is_none() || mv.self_dropped {
                continue;
            }
            if !is_secondary && !self_effect.boosts.is_empty() {
                let roll = self.chance.random(100);
                if self_effect.chance.is_none_or(|c| roll < c as u32) {
                    self.spread_move_hit(vec![Some(user)], user, mv, self_effect, false, is_secondary, true)?;
                }
                if mv.data.multihit.is_none() {
                    mv.self_dropped = true;
                }
            } else {
                self.spread_move_hit(vec![Some(user)], user, mv, self_effect, false, is_secondary, true)?;
            }
        }
        Ok(())
    }

    /// `secondaries`.
    fn secondaries(&mut self, targets: &[Option<MonRef>], user: MonRef, mv: &mut MoveUse) -> Res<()> {
        for &t in targets {
            let Some(t) = t else { continue };
            for sec in &mv.data.secondaries {
                let roll = self.chance.random(100);
                if sec.chance.is_none_or(|c| roll < c as u32) {
                    self.spread_move_hit(vec![Some(t)], user, mv, sec, false, true, false)?;
                }
            }
        }
        Ok(())
    }

    /// `spreadDamage` + `pokemon.damage` for one target. Returns HP lost.
    pub(super) fn apply_damage(&mut self, t: MonRef, amount: u32) -> u32 {
        let m = self.mon_mut(t);
        if m.hp == 0 || amount == 0 {
            return 0;
        }
        let d = amount.max(1).min(m.hp as u32) as u16;
        m.hp -= d;
        if m.hp == 0 && !m.faint_queued {
            m.switch_flag = None;
            m.faint_queued = true;
            self.faint_queue.push(t);
        }
        d as u32
    }
}
