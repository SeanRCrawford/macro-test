//! Using a move: Showdown's runMove, useMove/useMoveInner, getTarget,
//! getMoveTargets, trySpreadMoveHit and its hit steps, and the hit loop.
//!
//! Only what `support` allows is reachable: attacks and status moves whose
//! effects are stat stages, statuses, flinch, Protect, recoil, drain and
//! healing.

use super::conditions::{status_from_id, HitRes};
use super::state::{LockedMove, Mon, SideCondition, SwitchFlag, Volatile, VolatileId, ACTIVE_PER_SIDE};
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
    /// `move.selfSwitch` (U-turn, Parting Shot...), which Parting Shot can drop.
    pub self_switch: bool,
    /// The move's priority as last sorted (after ModifyPriority).
    pub priority: i8,
    /// `move.hit`: which hit of a multi-hit move this is.
    pub hit: u8,
}

impl MoveUse {
    fn data_id(&self) -> MoveId {
        self.am.id
    }
}

/// The move a queued Move action uses: Struggle when the slot is `usize::MAX`.
/// A Move action's slot for the move the Pokemon is locked into.
pub(super) const LOCKED_SLOT: usize = usize::MAX - 1;

/// The move a queued Move action uses: Struggle when the slot is `usize::MAX`
/// (and, as a stand-in with the same priority and category, for a recharge
/// turn); the locked move for `LOCKED_SLOT`.
pub(super) fn move_for_slot(m: &Mon, slot: usize) -> MoveId {
    if slot == LOCKED_SLOT {
        if let Some(LockedMove::Move(id)) = m.locked_move() {
            return id;
        }
    }
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
            MoveTarget::SelfTarget
            | MoveTarget::All
            | MoveTarget::AllySide
            | MoveTarget::AllyTeam
            | MoveTarget::AdjacentAllyOrSelf
            | MoveTarget::Allies => Some(user),
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

    /// resolveAction for a move with no target: `getRandomTarget`'s location.
    pub(super) fn random_target_loc(&mut self, user: MonRef, move_id: MoveId) -> i8 {
        let target = Dex::get().move_data(move_id).target;
        match self.random_target(user, target) {
            Some(t) => self.loc_of(user, t),
            None => 0,
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
            // alliesAndSelf()
            MoveTarget::Allies => {
                let side = user.side;
                (0..ACTIVE_PER_SIDE)
                    .filter(|&p| self.sides[side].occupant(p).is_some_and(|m| m.hp > 0 && !m.fainted))
                    .map(|p| self.mon_ref(side, p))
                    .collect()
            }
            _ => {
                let mut target = target;
                let needs_retarget = match target {
                    None => true,
                    Some(t) => self.mon(t).fainted && t.side != user.side,
                };
                if needs_retarget {
                    target = self.random_target(user, am.target);
                }
                if let Some(t) = target {
                    target = Some(self.redirect_target(user, am.target, t));
                }
                match target {
                    Some(t) if !self.mon(t).fainted => vec![t],
                    _ => Vec::new(),
                }
            }
        }
    }

    /// The RedirectTarget event: the first foe with Follow Me or Rage Powder
    /// (in Speed order, then creation order) that the move could target.
    fn redirect_target(&mut self, user: MonRef, target_type: MoveTarget, target: MonRef) -> MonRef {
        let mut handlers: Vec<(MonRef, VolatileId, i32, u64)> = Vec::new();
        for f in self.foes(user) {
            let m = self.mon(f);
            for v in &m.volatiles.0 {
                if matches!(v.id, VolatileId::FollowMe | VolatileId::RagePowder) {
                    handlers.push((f, v.id, m.speed, v.effect_order));
                }
            }
        }
        if handlers.is_empty() {
            return target;
        }
        self.speed_sort(&mut handlers, |a, b| b.2.cmp(&a.2).then(a.3.cmp(&b.3)));
        let user_pos = self.mon(user).position;
        for (f, id, _, _) in handlers {
            if id == VolatileId::RagePowder && !self.run_status_immunity(user, "powder") {
                continue;
            }
            if super::choice::valid_target_loc(self.loc_of(user, f), user_pos, target_type) {
                return f;
            }
        }
        target
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
                c.move_last_turn_failed = m.move_last_turn_result == Some(Some(false));
                c.volatiles.glaive_rush = m.volatiles.has(VolatileId::GlaiveRush);
                c.volatiles.helping_hand =
                    m.volatiles.0.iter().find(|v| v.id == VolatileId::HelpingHand).map_or(0, |v| v.counter as u8);
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
            sides: [0, 1].map(|s| SideState {
                fainted: self.sides[s].total_fainted,
                reflect: self.sides[s].condition(SideCondition::Reflect) > 0,
                light_screen: self.sides[s].condition(SideCondition::LightScreen) > 0,
                aurora_veil: self.sides[s].condition(SideCondition::AuroraVeil) > 0,
            }),
            crit,
            spread,
            hit: 1,
        }
    }

    /// `runMove`.
    pub(super) fn run_move(&mut self, user: MonRef, slot: usize, target_loc: i8, priority: i8) -> Res<()> {
        self.mon_mut(user).active_move_actions += 1;
        // A recharge turn: mustrecharge's BeforeMove (priority 11) stops
        // whatever move comes (Encore may have swapped one in).
        if self.mon(user).volatiles.has(VolatileId::MustRecharge) {
            let m = self.mon_mut(user);
            m.volatiles.remove(VolatileId::GlaiveRush);
            m.volatiles.remove(VolatileId::MustRecharge);
            if m.volatiles.remove(VolatileId::TwoTurnMove) {
                m.volatiles.0.retain(|v| !matches!(v.id, VolatileId::Charging(_)));
            }
            m.move_this_turn_result = Some(None);
            return Ok(());
        }
        let move_id = move_for_slot(self.mon(user), slot);
        let data = Dex::get().move_data(move_id);
        let target = self.get_target(user, data.target, target_loc);
        if !self.before_move(user, move_id, data) {
            // MoveAborted: twoturnmove ends (and with it the charge).
            let m = self.mon_mut(user);
            if m.volatiles.remove(VolatileId::TwoTurnMove) {
                m.volatiles.0.retain(|v| !matches!(v.id, VolatileId::Charging(_)));
            }
            m.move_this_turn_result = Some(Some(false));
            return Ok(());
        }
        let is_struggle = data.id == "struggle";
        // A locked move (the second turn of a charge) uses no PP.
        if !is_struggle && self.mon(user).locked_move().is_none() {
            let m = self.mon_mut(user);
            let s = &mut m.moves[slot];
            s.used = true;
            if s.pp == 0 {
                m.move_this_turn_result = Some(Some(false));
                return Ok(());
            }
            s.pp -= 1;
        }
        let m = self.mon_mut(user);
        m.last_move = Some(move_id);
        m.last_move_target_loc = target_loc;
        m.move_this_turn = Some(move_id);

        // useMove
        m.move_this_turn_result = None;
        let result = self.use_move(user, move_id, target, priority)?;
        let m = self.mon_mut(user);
        if m.move_this_turn_result.is_none() {
            m.move_this_turn_result = Some(Some(result));
        }
        // AfterMove: White Herb's onAnyAfterMove.
        if self.mon(user).is_active || target.is_some_and(|t| self.mon(t).is_active) {
            self.any_white_herb(user);
        }
        self.faint_messages()?;
        Ok(())
    }

    /// `useMoveInner` for moves that target Pokemon.
    fn use_move(&mut self, user: MonRef, move_id: MoveId, target: Option<MonRef>, priority: i8) -> Res<bool> {
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
        // useMoveInner aims at the last target (or keeps the chosen one).
        let last_target = targets.last().copied().unwrap_or(target);
        // TryMove: a two-turn move charges now unless its charge is done or
        // the weather lets it fire at once.
        if let Some(fire_now) = self.charge_move(user, move_id, data) {
            if !fire_now {
                self.mon_mut(user).move_this_turn_result = Some(None);
                return Ok(false);
            }
        }
        // TryMove: a foe's Armor Tail stops priority moves aimed at its side.
        if priority > 0 && self.foes(user).into_iter().any(|f| f.side == last_target.side && self.ability_is(f, "armortail")) {
            return Ok(false);
        }
        if targets.is_empty() {
            return Ok(false);
        }
        let mut mv = MoveUse { am, data, self_dropped: false, spread: false, self_switch: data.self_switch, priority, hit: 1 };
        let result = self.try_spread_move_hit(user, &mut mv, targets)?;
        // selfBoost (Clanging Scales), once the move worked.
        if let (true, Some(sb)) = (result, data.self_boost.as_ref()) {
            self.spread_move_hit(vec![Some(user)], user, &mut mv, sb, false, false, true)?;
        }
        if result {
            self.after_move_secondary_self(user, last_target, data.category == Category::Status);
        }
        Ok(result)
    }

    /// A charge move's onTryMove. None: not a charge move. Some(true): it
    /// hits this turn; Some(false): it started charging.
    fn charge_move(&mut self, user: MonRef, move_id: MoveId, data: &MoveData) -> Option<bool> {
        let weather = self.field.weather;
        let (boosts, instant) = match data.id.as_str() {
            "electroshot" => (true, weather == crate::damage::Weather::Rain),
            "meteorbeam" => (true, false),
            "solarbeam" | "solarblade" => (false, weather == crate::damage::Weather::Sun),
            _ => return None,
        };
        let m = self.mon(user);
        if m.volatiles.has(VolatileId::Charging(move_id)) {
            self.mon_mut(user).volatiles.remove(VolatileId::Charging(move_id));
            return Some(true);
        }
        if boosts {
            self.boost(user, &[(2, 1)], Some(user));
        }
        if instant {
            return Some(true);
        }
        // addVolatile('twoturnmove'): its onStart adds the move's own volatile
        // holding the target.
        let target_loc = self.mon(user).last_move_target_loc;
        for (id, mv) in [(VolatileId::TwoTurnMove, Some(move_id)), (VolatileId::Charging(move_id), None)] {
            if self.mon(user).volatiles.has(id) {
                continue;
            }
            self.effect_order += 1;
            let effect_order = self.effect_order;
            self.mon_mut(user).volatiles.0.push(Volatile { id, duration: id.duration(), counter: 0, move_id: mv, effect_order, target_loc });
        }
        Some(false)
    }

    /// The protecting volatile's onTryHit: whether it blocks `data` on `t`,
    /// with the contact punishments of Spiky Shield, King's Shield and
    /// Baneful Bunker.
    fn protect_blocks(&mut self, user: MonRef, t: MonRef, data: &MoveData) -> bool {
        let v = &self.mon(t).volatiles;
        let kind = [VolatileId::Protect, VolatileId::SpikyShield, VolatileId::KingsShield, VolatileId::BanefulBunker]
            .into_iter()
            .find(|&k| v.has(k));
        let Some(kind) = kind else { return false };
        // checkMoveBypassesProtect (King's Shield lets status moves through).
        if !data.flags.has("protect") || (kind == VolatileId::KingsShield && data.category == Category::Status) {
            return false;
        }
        if data.flags.has("contact") {
            match kind {
                VolatileId::SpikyShield => {
                    let amount = (self.mon(user).max_hp() / 8) as u32;
                    self.effect_damage(user, amount);
                }
                VolatileId::KingsShield => {
                    self.boost(user, &[(0, -1)], Some(t));
                }
                VolatileId::BanefulBunker => {
                    self.try_set_status(user, Status::Poison);
                }
                _ => {}
            }
        }
        true
    }

    /// `tryMoveHit` for moves that hit a side or the field
    /// (runMoveEffects' sideCondition / pseudoWeather, and onHitSide).
    fn try_move_hit(&mut self, user: MonRef, data: &MoveData) -> bool {
        let add = |b: &mut Battle, c: SideCondition, turns: u8| {
            let d = &mut b.sides[user.side].conditions[c as usize];
            if *d > 0 {
                return false;
            }
            *d = turns;
            true
        };
        // Reflect and Light Screen's durationCallback: Light Clay makes 8.
        let screen_turns = if self.item_of(user) == Some("lightclay") { 8 } else { 5 };
        match data.id.as_str() {
            "tailwind" => add(self, SideCondition::Tailwind, 4),
            "reflect" => add(self, SideCondition::Reflect, screen_turns),
            "lightscreen" => add(self, SideCondition::LightScreen, screen_turns),
            // onTry: only in snow.
            "auroraveil" => self.field.weather == crate::damage::Weather::Snow && add(self, SideCondition::AuroraVeil, screen_turns),
            "wideguard" => {
                // onTry: fails as the last to act; onHitSide adds stall even
                // if Wide Guard was already up.
                if !self.will_act() {
                    return false;
                }
                add(self, SideCondition::WideGuard, 1);
                self.add_volatile(user, VolatileId::Stall);
                true
            }
            "raindance" => self.set_weather(crate::damage::Weather::Rain, user),
            "sunnyday" => self.set_weather(crate::damage::Weather::Sun, user),
            "sandstorm" => self.set_weather(crate::damage::Weather::Sand, user),
            "snowscape" => self.set_weather(crate::damage::Weather::Snow, user),
            "electricterrain" => self.set_terrain(crate::damage::Terrain::Electric, user),
            "grassyterrain" => self.set_terrain(crate::damage::Terrain::Grassy, user),
            "mistyterrain" => self.set_terrain(crate::damage::Terrain::Misty, user),
            "psychicterrain" => self.set_terrain(crate::damage::Terrain::Psychic, user),
            // onHitField: every active Pokemon not already counting down
            // starts; Good as Gold's TryHit keeps it out (but counts).
            "perishsong" => {
                let mut result = false;
                for r in self.all_active() {
                    if r != user && self.ability_is(r, "goodasgold") {
                        result = true;
                    } else if !self.mon(r).volatiles.has(VolatileId::PerishSong) {
                        self.add_volatile(r, VolatileId::PerishSong);
                        result = true;
                    }
                }
                result
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
        let priority = mv.priority;
        let spread_target = mv.am.target;
        let mut targets = targets;
        mv.spread = targets.len() > 1;

        // Try and PrepareHit
        if matches!(data.id.as_str(), "fakeout" | "firstimpression") && self.mon(user).active_move_actions > 1 {
            return Ok(false);
        }
        if data.id == "clangoroussoul" {
            let m = self.mon(user);
            if m.hp as u32 * 100 <= m.max_hp() as u32 * 33 || m.max_hp() == 1 {
                return Ok(false);
            }
        }
        if data.id == "steelroller" && self.field.terrain == crate::damage::Terrain::None {
            return Ok(false);
        }
        // Sucker Punch fails unless its target is about to use an attack.
        if data.id == "suckerpunch" {
            let attacking = self.queued_move(targets[0]).is_some_and(|m| Dex::get().move_data(m).category != Category::Status);
            if !attacking || self.mon(targets[0]).volatiles.has(VolatileId::MustRecharge) {
                return Ok(false);
            }
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

        // hitStepTryHitEvent: Psychic Terrain (priority 4) stops priority
        // moves on grounded foes; Protect blocks moves with the protect flag.
        step(self, &mut targets, &mut |b, t| {
            if b.psychic_terrain_blocks(user, t, priority, data.target == MoveTarget::SelfTarget) {
                HitRes::Bool(false)
            } else if data.flags.has("protect")
                && b.sides[t.side].condition(SideCondition::WideGuard) > 0
                && matches!(spread_target, MoveTarget::AllAdjacent | MoveTarget::AllAdjacentFoes)
            {
                // Wide Guard (priority 4).
                HitRes::NotFail
            } else if b.protect_blocks(user, t, data) {
                // Protect and its variants (priority 3).
                HitRes::NotFail
            } else if data.category == Category::Status && t != user && b.ability_is(t, "goodasgold") {
                // Good as Gold (priority 0).
                HitRes::Bool(false)
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
        // hitStepTryImmunity: powder moves don't affect Grass types, and
        // Prankster-boosted moves don't affect Dark-type foes.
        let prankster_boosted = data.category == Category::Status && self.ability_is(user, "prankster");
        step(self, &mut targets, &mut |b, t| {
            let types = b.mon(t).types;
            let powder = data.flags.has("powder") && t != user && Dex::get().immune_to("powder", types);
            let prankster = prankster_boosted && t.side != user.side && Dex::get().immune_to("prankster", types);
            HitRes::Bool(!(powder || prankster))
        });
        // hitStepAccuracy
        step(self, &mut targets, &mut |b, t| HitRes::Bool(b.accuracy_check(user, t, data)));

        if targets.is_empty() {
            if !failed {
                self.mon_mut(user).move_this_turn_result = Some(None);
            }
            return Ok(false);
        }
        self.move_hit_loop(user, mv, targets)
    }

    /// hitStepAccuracy for one target.
    fn accuracy_check(&mut self, user: MonRef, t: MonRef, data: &MoveData) -> bool {
        let Some(mut acc) = data.accuracy else { return true };
        // glaiverush's onAccuracy: anything hits its holder.
        if self.mon(t).volatiles.has(VolatileId::GlaiveRush) {
            return true;
        }
        // onModifyMove: weather-dependent accuracy.
        match (data.id.as_str(), self.field.weather) {
            ("thunder" | "hurricane", crate::damage::Weather::Rain) | ("blizzard", crate::damage::Weather::Snow) => return true,
            ("thunder" | "hurricane", crate::damage::Weather::Sun) => acc = 50,
            _ => {}
        }
        let always = (data.id == "toxic" && self.mon(user).has_type(Dex::get().type_id("Poison").expect("Poison")))
            || (data.target == MoveTarget::SelfTarget && data.category == Category::Status);
        if always {
            return true;
        }
        // ModifyBoost: Unaware ignores the other side's accuracy/evasion.
        let unaware = |b: &Battle, r: MonRef| Dex::get().ability(b.mon(r).ability).id == "unaware";
        let acc_boost = if unaware(self, t) { 0 } else { self.mon(user).boosts[5] as i32 };
        let eva_boost = if unaware(self, user) || data.has_key("ignoreEvasion") { 0 } else { self.mon(t).boosts[6] as i32 };
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
        // How many hits: fixed, or 2-5 weighted (no Loaded Dice / Skill Link).
        let target_hits = match mv.data.multihit {
            None => 1,
            Some((a, b)) if a == b => a,
            Some((2, 5)) => [2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 5, 5, 5][self.chance.sample(20)],
            Some((a, b)) => self.chance.random_range(a as u32, b as u32 + 1) as u8,
        };
        let n = targets.len();
        let mut damage = vec![HitRes::Num(0); n];
        let mut move_damage = Vec::new();
        let mut hit_targets = Vec::new();
        let mut total = 0u32;
        let mut hit: u8 = 1;
        while hit <= target_hits {
            if damage.contains(&HitRes::Bool(false)) {
                break;
            }
            if hit > 1 && self.mon(user).status == Status::Sleep && !mv.data.has_key("sleepUsable") {
                break;
            }
            if targets.iter().all(|&t| self.mon(t).hp == 0) {
                break;
            }
            mv.hit = hit;
            let (md, tc) = self.spread_move_hit(targets.iter().map(|&t| Some(t)).collect(), user, mv, effect, true, false, false)?;
            move_damage = md;
            hit_targets = tc;
            if move_damage.iter().all(|&d| d == HitRes::Bool(false)) {
                break;
            }
            for (i, &md) in move_damage.iter().enumerate() {
                damage[i] = if md == HitRes::Bool(true) || !md.truthy() { HitRes::Num(0) } else { md };
                if let HitRes::Num(x) = damage[i] {
                    total += x;
                }
            }
            self.each_update();
            if self.mon(user).hp == 0 && n == 1 {
                hit += 1;
                break;
            }
            hit += 1;
        }
        if hit == 1 {
            return Ok(false);
        }
        self.faint_messages()?;
        if total > 0 {
            self.apply_recoil(user, mv.data, total);
        }
        for (i, t) in hit_targets.iter().enumerate() {
            if let Some(t) = *t {
                if t != user && matches!(move_damage[i], HitRes::Num(_)) {
                    let m = self.mon_mut(t);
                    m.times_attacked = m.times_attacked.saturating_add(hit - 1);
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
        // The move's TryHit. Helping Hand fails on an ally that has already
        // moved; Psychic Fangs and Brick Break break the target's screens;
        // Clangorous Soul raises its stats here (or fails).
        let mut skip_boosts = false;
        if primary {
            if let Some(t) = targets[0] {
                match mv.data.id.as_str() {
                    "helpinghand" if !self.mon(t).newly_switched && !self.will_move(t) => {
                        return Ok((vec![HitRes::Bool(false)], targets));
                    }
                    "yawn" if self.mon(t).status != Status::None || !self.run_status_immunity(t, "slp") => {
                        return Ok((vec![HitRes::Bool(false)], targets));
                    }
                    "disable" if self.mon(t).last_move.is_none_or(|m| Dex::get().move_data(m).id == "struggle") => {
                        return Ok((vec![HitRes::Bool(false)], targets));
                    }
                    "psychicfangs" | "brickbreak" => {
                        for c in [SideCondition::Reflect, SideCondition::LightScreen, SideCondition::AuroraVeil] {
                            self.sides[t.side].conditions[c as usize] = 0;
                        }
                    }
                    "clangoroussoul" => {
                        if !self.boost(t, &mv.data.primary.boosts, Some(user)).truthy() {
                            return Ok((vec![HitRes::Bool(false)], targets));
                        }
                        skip_boosts = true;
                    }
                    _ => {}
                }
            }
        }
        // TryPrimaryHit (no handlers).
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
                // A target already at 0 HP (from an earlier hit) takes nothing.
                if self.mon(t).hp == 0 {
                    damage[i] = HitRes::Num(0);
                    continue;
                }
                let crit_ratio = mv.data.crit_ratio.clamp(0, 4) as usize;
                let crit = match mv.data.will_crit {
                    Some(c) => c,
                    None => crit_ratio > 0 && self.chance.chance(1, [0, 24, 8, 2, 1][crit_ratio]),
                };
                let mut ctx = self.damage_ctx(&view, user, t, crit, spread);
                ctx.hit = mv.hit;
                let outcome = damage::damage_for(&ctx, &mv.am).map_err(|e| BattleError::Unsupported(e.0))?;
                if matches!(outcome, Outcome::Damage(_)) && damage::eats_resist_berry(&ctx, &mv.am).map_err(|e| BattleError::Unsupported(e.0))? {
                    self.eat_resist_berry(t);
                }
                // Final Gambit's damageCallback faints its user.
                if mv.data.id == "finalgambit" && matches!(outcome, Outcome::Damage(_)) {
                    self.faint(user);
                }
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

        self.run_move_effects(&mut damage, &targets, mv, user, effect, primary && !skip_boosts, primary, is_secondary)?;
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

        // DamagingHit for the targets that took damage.
        if !is_secondary && !is_self {
            let damaged: Vec<MonRef> =
                (0..targets.len()).filter_map(|i| targets[i].filter(|_| matches!(damage[i], HitRes::Num(_)))).collect();
            if !damaged.is_empty() {
                let fire = mv.am.move_type == Dex::get().type_id("Fire").expect("Fire") && mv.am.category != Category::Status;
                self.damaging_hit(user, &damaged, mv.data.flags.has("contact"), fire);
                // AfterHit: Knock Off takes the item (the champions mod's
                // spreadMoveHit doesn't need the user to still have HP).
                if mv.data.id == "knockoff" {
                    for &t in &damaged {
                        self.take_item(t, t);
                    }
                }
            }
        }
        Ok((damage, targets))
    }

    /// `runMoveEffects`.
    #[allow(clippy::too_many_arguments)]
    fn run_move_effects(
        &mut self,
        damage: &mut [HitRes],
        targets: &[Option<MonRef>],
        mv: &mut MoveUse,
        user: MonRef,
        effect: &HitEffect,
        with_boosts: bool,
        primary: bool,
        is_secondary: bool,
    ) -> Res<()> {
        let mut did_anything = damage.iter().copied().reduce(HitRes::combine).unwrap_or(HitRes::Undefined);
        for i in 0..targets.len() {
            let Some(t) = targets[i] else { continue };
            let mut did_something = HitRes::Undefined;
            if (with_boosts || !primary) && !effect.boosts.is_empty() && !self.mon(t).fainted {
                let r = self.boost(t, &effect.boosts, Some(user));
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
            // Secondaries' onHit: Dire Claw's random status, Throat Chop.
            if is_secondary && mv.data.id == "direclaw" {
                let status = [Status::Poison, Status::Paralysis, Status::Sleep][self.chance.sample(3)];
                self.try_set_status(t, status);
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if is_secondary && mv.data.id == "throatchop" {
                self.add_volatile(t, VolatileId::ThroatChop);
                did_something = did_something.combine(HitRes::Bool(true));
            }
            // onHit: Clangorous Soul costs a third of max HP; Steel Roller ends
            // the terrain.
            if primary && mv.data.id == "clangoroussoul" {
                let cost = (self.mon(user).max_hp() as u32 * 33 / 100).max(1);
                self.apply_damage(user, cost);
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if primary && mv.data.id == "steelroller" {
                self.clear_terrain();
                did_something = did_something.combine(HitRes::Bool(true));
            }
            // onHit: Trick swaps items.
            if primary && mv.data.id == "trick" {
                let r = self.trick(user, t);
                did_something = did_something.combine(HitRes::Bool(r));
            }
            // onHit: Parting Shot lowers Attack and Sp. Atk, and doesn't
            // switch out if neither drops.
            if primary && mv.data.id == "partingshot" {
                if !self.boost(t, &[(0, -1), (2, -1)], Some(user)).truthy() {
                    mv.self_switch = false;
                }
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if primary && mv.self_switch {
                did_something = if self.switchable(user.side).is_empty() {
                    did_something.combine(HitRes::Bool(false))
                } else {
                    HitRes::Bool(true)
                };
            }
            if did_something == HitRes::Undefined {
                did_something = HitRes::Bool(true);
            }
            damage[i] = damage[i].combine(if did_something == HitRes::Null { HitRes::Bool(false) } else { did_something });
            did_anything = did_anything.combine(did_something);
        }
        let failed = !did_anything.truthy() && did_anything != HitRes::Num(0) && effect.self_effect.is_none();
        if !failed && mv.self_switch && self.mon(user).hp > 0 {
            self.mon_mut(user).switch_flag = Some(SwitchFlag::Move(mv.data_id()));
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
