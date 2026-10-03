//! Using a move: Showdown's runMove, useMove/useMoveInner, getTarget,
//! getMoveTargets, trySpreadMoveHit and its hit steps, and the hit loop.
//!
//! Only what `support` allows is reachable: for now, plain attacks (a damaging
//! move with no secondary, self, status or other side effect).

use super::state::{Mon, ACTIVE_PER_SIDE};
use super::{Battle, BattleError, MonRef, Res};
use crate::damage::{self, ActiveMove, Combatant, DamageCtx, Outcome, SideState};
use crate::dex::{Dex, MoveId, MoveTarget};

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
        let mut target = target;
        if am.target != base_target {
            target = self.random_target(user, am.target);
        }
        if self.mon(user).hp == 0 {
            return Ok(false);
        }
        let Some(target) = target else { return Ok(false) };
        let targets = self.get_move_targets(user, &am, Some(target));
        if targets.is_empty() {
            return Ok(false);
        }
        self.try_spread_move_hit(user, &am, targets)
    }

    /// `trySpreadMoveHit` and its hit steps, for a plain attack.
    fn try_spread_move_hit(&mut self, user: MonRef, am: &ActiveMove, targets: Vec<MonRef>) -> Res<bool> {
        let spread = targets.len() > 1;
        let data = Dex::get().move_data(am.id);
        let mut targets = targets;

        // hitStepTypeImmunity
        let view = self.damage_view();
        targets.retain(|&t| {
            let ctx = self.damage_ctx(&view, user, t, false, spread);
            damage::run_immunity(&ctx, am)
        });
        let mut failed = targets.is_empty();

        // hitStepAccuracy
        let mut hit = Vec::new();
        for &t in &targets {
            if let Some(acc) = data.accuracy {
                let acc_boost = self.mon(user).boosts[5] as i32;
                let eva_boost = self.mon(t).boosts[6] as i32;
                let boost = (acc_boost - eva_boost).clamp(-6, 6);
                let mut accuracy = acc as u32;
                if boost > 0 {
                    accuracy = accuracy * (3 + boost as u32) / 3;
                } else if boost < 0 {
                    accuracy = accuracy * 3 / (3 + (-boost) as u32);
                }
                if !self.chance.chance(accuracy, 100) {
                    failed = true;
                    continue;
                }
            }
            hit.push(t);
        }
        let targets = hit;
        if targets.is_empty() {
            if !failed {
                self.mon_mut(user).move_this_turn_result = None;
            }
            return Ok(false);
        }

        // hitStepMoveHitLoop: one hit (multi-hit moves aren't supported yet).
        // spreadMoveHit -> getSpreadDamage -> spreadDamage
        let view = self.damage_view();
        let mut dealt = Vec::new();
        for &t in &targets {
            let crit_ratio = data.crit_ratio.clamp(0, 4) as usize;
            let crit = match data.will_crit {
                Some(c) => c,
                None => crit_ratio > 0 && self.chance.chance(1, [0, 24, 8, 2, 1][crit_ratio]),
            };
            let ctx = self.damage_ctx(&view, user, t, crit, spread);
            let outcome = damage::damage_for(&ctx, am).map_err(|e| BattleError::Unsupported(e.0))?;
            let amount = match outcome {
                Outcome::Damage(rolls) => {
                    let r = self.chance.random(16) as usize;
                    Some(rolls[r])
                }
                Outcome::Immune => None,
                Outcome::NoDamage => Some(0),
            };
            dealt.push((t, amount));
        }
        let mut total = 0u32;
        for &(t, amount) in &dealt {
            let Some(d) = amount else { continue };
            total += self.apply_damage(t, d);
        }
        self.faint_messages()?;
        // applyRecoilDamage: Struggle's recoil is a quarter of max HP, direct.
        if total > 0 && data.id == "struggle" {
            let max = self.mon(user).max_hp() as u32;
            let recoil = ((max as f64 / 4.0).round() as u32).max(1);
            self.apply_damage(user, recoil);
        }
        for &(t, amount) in &dealt {
            if amount.is_some() && t != user {
                self.mon_mut(t).times_attacked = self.mon(t).times_attacked.saturating_add(1);
            }
        }
        Ok(dealt.iter().any(|(_, a)| a.is_some()))
    }

    /// `spreadDamage` + `pokemon.damage` for one target. Returns HP lost.
    pub(super) fn apply_damage(&mut self, t: MonRef, amount: u32) -> u32 {
        let m = self.mon_mut(t);
        if m.hp == 0 || amount == 0 {
            return 0;
        }
        let d = amount.max(1).min(m.hp as u32) as u16;
        m.hp -= d;
        m.hurt_this_turn = Some(m.hp);
        if m.hp == 0 && !m.faint_queued {
            m.switch_flag = None;
            m.faint_queued = true;
            self.faint_queue.push(t);
        }
        d as u32
    }
}
