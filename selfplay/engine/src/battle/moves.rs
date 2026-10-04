//! Using a move: Showdown's runMove, useMove/useMoveInner, getTarget,
//! getMoveTargets, trySpreadMoveHit and its hit steps, and the hit loop.
//!
//! Only what `support` allows is reachable: attacks and status moves whose
//! effects are stat stages, statuses, flinch, Protect, recoil, drain and
//! healing.

use super::conditions::{status_from_id, HitRes};
use super::state::{
    Attacker, LockedMove, Mon, SideCondition, SwitchFlag, Volatile, VolatileId, ACTIVE_PER_SIDE,
};
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
    /// `move.hasBounced`: reflected by Magic Bounce (can't bounce again).
    pub bounced: bool,
    /// King's Rock added a 10% flinch secondary.
    pub kings_rock: bool,
    /// Targets whose protection the move got through (getMoveHitData's
    /// bypassProtect).
    pub bypassed: Vec<MonRef>,
    /// Targets this hit landed a critical hit on (getMoveHitData's crit).
    pub crit_on: Vec<MonRef>,
    /// `move.hitTargets`: who the move went on to hit (Magician).
    pub hit_targets: Vec<MonRef>,
    /// `move.totalDamage` (Shell Bell).
    pub total_damage: u32,
}

/// The secondary King's Rock adds.
static KINGS_ROCK_FLINCH: std::sync::LazyLock<HitEffect> = std::sync::LazyLock::new(|| HitEffect {
    chance: Some(10),
    volatile_status: Some("flinch".into()),
    keys: vec!["chance".into(), "volatileStatus".into()],
    ..HitEffect::default()
});

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
    pub(super) fn at_loc(&self, user: MonRef, loc: i8) -> Option<MonRef> {
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
    pub(super) fn adjacent_allies(&self, user: MonRef) -> Vec<MonRef> {
        (0..ACTIVE_PER_SIDE)
            .filter(|&p| {
                self.sides[user.side]
                    .occupant(p)
                    .is_some_and(|m| m.hp > 0 && m.uid != user.uid)
            })
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
    /// `move.tracksTarget` (Snipe Shot), or set by Stalwart / Propeller Tail.
    fn tracks_target(&self, user: MonRef, _target_type: MoveTarget) -> bool {
        self.ability_is(user, "stalwart")
            || self.ability_is(user, "propellertail")
            || self
                .mon(user)
                .move_this_turn
                .is_some_and(|m| Dex::get().move_data(m).has_key("tracksTarget"))
    }

    fn get_target(&mut self, user: MonRef, target_type: MoveTarget, loc: i8) -> Option<MonRef> {
        // Stalwart and Propeller Tail follow the original target while it
        // is on the field.
        if self.tracks_target(user, target_type) {
            if let Some((side, uid)) = self.mon(user).original_target {
                if let Some(t) = self
                    .all_active()
                    .into_iter()
                    .find(|&t| t.side == side && self.mon(t).uid == uid && self.mon(t).is_active)
                {
                    return Some(t);
                }
            }
        }
        let self_loc = self.loc_of(user, user);
        if matches!(
            target_type,
            MoveTarget::AdjacentAlly | MoveTarget::Any | MoveTarget::Normal
        ) && loc == self_loc
        {
            return None;
        }
        let user_pos = self.mon(user).position;
        if target_type != MoveTarget::RandomNormal
            && super::choice::valid_target_loc(loc, user_pos, target_type)
            && loc != 0
        {
            if let Some(t) = self.at_loc(user, loc) {
                let tm = self.mon(t);
                if tm.fainted && t.side == user.side {
                    return Some(if target_type == MoveTarget::AdjacentAllyOrSelf {
                        user
                    } else {
                        t
                    });
                }
                if !tm.fainted {
                    return Some(t);
                }
            }
        }
        self.random_target(user, target_type)
    }

    /// `getMoveTargets` for moves that target Pokemon.
    fn get_move_targets(
        &mut self,
        user: MonRef,
        am: &ActiveMove,
        target: Option<MonRef>,
    ) -> Vec<MonRef> {
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
                    .filter(|&p| {
                        self.sides[side]
                            .occupant(p)
                            .is_some_and(|m| m.hp > 0 && !m.fainted)
                    })
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
                // Stalwart and Propeller Tail keep their target.
                let tracks = self.ability_is(user, "stalwart")
                    || self.ability_is(user, "propellertail")
                    || Dex::get().move_data(am.id).has_key("tracksTarget");
                if let (Some(t), false) = (target, tracks) {
                    target = Some(self.redirect_target(user, am, t));
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
    fn redirect_target(&mut self, user: MonRef, am: &ActiveMove, target: MonRef) -> MonRef {
        let target_type = am.target;
        let mut handlers: Vec<(MonRef, VolatileId, i32, u64)> = Vec::new();
        for f in self.foes(user) {
            let m = self.mon(f);
            for v in &m.volatiles.0 {
                if matches!(v.id, VolatileId::FollowMe | VolatileId::RagePowder) {
                    handlers.push((f, v.id, m.speed, v.effect_order));
                }
            }
        }
        let user_pos = self.mon(user).position;
        if !handlers.is_empty() {
            self.speed_sort(&mut handlers, |a, b| b.2.cmp(&a.2).then(a.3.cmp(&b.3)));
            for (f, id, _, _) in handlers {
                if id == VolatileId::RagePowder && !self.run_status_immunity(user, "powder") {
                    continue;
                }
                if super::choice::valid_target_loc(self.loc_of(user, f), user_pos, target_type) {
                    return f;
                }
            }
        }
        // onAnyRedirectTarget (priority 0): Lightning Rod and Storm Drain.
        let dex = Dex::get();
        let rod = if am.move_type == dex.type_id("Electric").expect("Electric") {
            "lightningrod"
        } else if am.move_type == dex.type_id("Water").expect("Water") {
            "stormdrain"
        } else {
            return target;
        };
        let mut rods: Vec<(MonRef, i32)> = self
            .all_active()
            .into_iter()
            .filter(|&r| self.ability_is(r, rod))
            .map(|r| (r, self.mon(r).speed))
            .collect();
        self.speed_sort(&mut rods, |a, b| b.1.cmp(&a.1));
        let redirect_type = if matches!(
            target_type,
            MoveTarget::RandomNormal | MoveTarget::AdjacentFoe
        ) {
            MoveTarget::Normal
        } else {
            target_type
        };
        for (r, _) in rods {
            if r != user
                && super::choice::valid_target_loc(self.loc_of(user, r), user_pos, redirect_type)
            {
                return r;
            }
        }
        target
    }

    /// `pokemon.effectiveWeather()` for the moving Pokemon's move: Mega Sol
    /// makes it sun.
    fn move_weather(&self, user: MonRef) -> crate::damage::Weather {
        if self.ability_is(user, "megasol") {
            crate::damage::Weather::Sun
        } else {
            self.effective_weather()
        }
    }

    /// Protean / Libero's onPrepareHit: once per switch-in, the user becomes
    /// the move's type.
    fn protean(&mut self, user: MonRef, move_type: crate::dex::TypeId) {
        if !(self.ability_is(user, "protean") || self.ability_is(user, "libero"))
            || self.mon(user).protean_used
        {
            return;
        }
        let m = self.mon_mut(user);
        if move_type != damage::TYPELESS && m.types != [move_type, move_type] {
            m.set_types([move_type, move_type]);
            m.protean_used = true;
        }
    }

    /// The Invulnerability event: `t` is out of reach (charging Fly, Dig,
    /// Dive, Bounce, Phantom Force or Shadow Force), unless the move reaches
    /// it or No Guard (onAnyInvulnerability) is in play.
    fn invulnerable(&self, user: MonRef, t: MonRef, data: &MoveData) -> bool {
        let Some(charging) = semi_invulnerable(self.mon(t)) else {
            return false;
        };
        if self.ability_is(user, "noguard") || self.ability_is(t, "noguard") {
            return false;
        }
        if data.id == "toxic"
            && self
                .mon(user)
                .has_type(Dex::get().type_id("Poison").expect("Poison"))
        {
            return false;
        }
        let reaches: &[&str] = match Dex::get().move_data(charging).id.as_str() {
            "fly" | "bounce" => &[
                "gust",
                "twister",
                "skyuppercut",
                "thunder",
                "hurricane",
                "smackdown",
                "thousandarrows",
            ],
            "dig" => &["earthquake", "magnitude"],
            "dive" => &["surf", "whirlpool"],
            _ => &[],
        };
        !reaches.contains(&data.id.as_str())
    }

    /// Magician's onAfterMoveSecondarySelf.
    fn magician(&mut self, user: MonRef, mv: &MoveUse, data: &MoveData) {
        let u = self.mon(user);
        if u.switch_flag == Some(SwitchFlag::Replace)
            || u.item.is_some()
            || u.volatiles.has(VolatileId::Gem)
            || data.category == Category::Status
        {
            return;
        }
        let mut hit: Vec<(MonRef, i32)> = mv
            .hit_targets
            .iter()
            .map(|&t| (t, self.mon(t).speed))
            .collect();
        self.speed_sort(&mut hit, |a, b| b.1.cmp(&a.1));
        for (t, _) in hit {
            if t == user {
                continue;
            }
            let Some(item) = self.take_item(t, user) else {
                continue;
            };
            let u = self.mon(user);
            if u.hp == 0 || !u.is_active {
                self.mon_mut(t).item = Some(item);
                continue;
            }
            self.set_item(user, item);
            return;
        }
    }

    /// HitProtect: Unseen Fist and Piercing Drill's contact moves get
    /// through protections.
    pub(super) fn hits_through_protect(&self, user: MonRef) -> bool {
        self.ability_is(user, "unseenfist") || self.ability_is(user, "piercingdrill")
    }

    /// `battle.skillSwap(source, target)`.
    pub(super) fn skill_swap(&mut self, source: MonRef, target: MonRef) -> bool {
        let dex = Dex::get();
        let (sa, ta) = (self.mon(source).ability, self.mon(target).ability);
        let fails =
            |a: crate::dex::AbilityId| dex.ability(a).flags.iter().any(|f| f == "failskillswap");
        if self.mon(source).fainted || self.mon(target).fainted || fails(sa) || fails(ta) {
            return false;
        }
        self.set_ability(source, ta);
        self.set_ability(target, sa);
        for (r, a) in [(target, sa), (source, ta)] {
            if let Some(e) = super::field::start_effect(&dex.ability(a).id) {
                self.ability_start(r, e);
            }
        }
        true
    }

    /// onFoeTryMove: Armor Tail, Queenly Majesty, Dazzling.
    fn blocks_priority(&self, r: MonRef) -> bool {
        ["armortail", "queenlymajesty", "dazzling"]
            .iter()
            .any(|a| self.ability_is(r, a))
    }

    /// DeductPP: each targeted foe with Pressure costs a PP more.
    fn pressure(&mut self, user: MonRef, move_id: MoveId, targets: &[MonRef]) {
        let extra = targets
            .iter()
            .filter(|&&t| t.side != user.side && self.ability_is(t, "pressure"))
            .count() as u8;
        let m = self.mon_mut(user);
        if let (true, Some(i)) = (extra > 0, m.move_slot(move_id)) {
            m.moves[i].pp = m.moves[i].pp.saturating_sub(extra);
        }
    }

    /// TryHit abilities that take the move in: true stops it on `t`.
    fn absorbs(
        &mut self,
        user: MonRef,
        t: MonRef,
        move_type: crate::dex::TypeId,
        data: &MoveData,
    ) -> bool {
        let dex = Dex::get();
        let is = |name: &str| move_type == dex.type_id(name).expect("type");
        match self.ability_id(t) {
            "voltabsorb" if is("Electric") => {}
            "eartheater" if is("Ground") => {}
            "bulletproof" => return data.flags.has("bullet"),
            "sturdy" => return data.ohko,
            "overcoat" => {
                return data.flags.has("powder") && !dex.immune_to("powder", self.mon(t).types)
            }
            "waterabsorb" if is("Water") => {}
            "sapsipper" if is("Grass") => {
                self.boost(t, &[(0, 1)], Some(user));
                return true;
            }
            "lightningrod" if is("Electric") => {
                self.boost(t, &[(2, 1)], Some(user));
                return true;
            }
            "stormdrain" if is("Water") => {
                self.boost(t, &[(2, 1)], Some(user));
                return true;
            }
            "dryskin" if is("Water") => {}
            "telepathy" => return t.side == user.side && data.category != Category::Status,
            "oblivious" => return data.id == "taunt",
            _ => return false,
        }
        let amount = (self.mon(t).max_hp() / 4) as u32;
        self.heal(t, amount);
        true
    }

    /// A damage-calculation view of the field with `attacker` hitting `defender`.
    fn damage_view(&self) -> [Option<Combatant>; 4] {
        let mut out: [Option<Combatant>; 4] = Default::default();
        for side in 0..2 {
            for pos in 0..ACTIVE_PER_SIDE {
                let Some(m) = self.sides[side].occupant(pos) else {
                    continue;
                };
                if m.hp == 0 && self.selfdestruct_user != Some(self.mon_ref(side, pos)) {
                    continue;
                }
                let mut c = Combatant::new(m.species, m.stats, m.ability, m.item);
                c.types = m.types;
                c.hp = m.hp;
                c.boosts = [
                    0,
                    m.boosts[0],
                    m.boosts[1],
                    m.boosts[2],
                    m.boosts[3],
                    m.boosts[4],
                ];
                c.status = m.status;
                c.speed = m.speed;
                c.active_turns = m.active_turns;
                c.times_attacked = m.times_attacked;
                c.move_last_turn_failed = m.move_last_turn_result == Some(Some(false));
                c.volatiles.glaive_rush = m.volatiles.has(VolatileId::GlaiveRush);
                c.volatiles.flash_fire = m.volatiles.has(VolatileId::FlashFire);
                c.volatiles.gem = m.volatiles.has(VolatileId::Gem);
                c.volatiles.charge = m.volatiles.has(VolatileId::Charge);
                c.volatiles.semi_invulnerable = semi_invulnerable(m);
                c.volatiles.minimize = m.volatiles.has(VolatileId::Minimize);
                c.volatiles.smack_down = m.volatiles.has(VolatileId::SmackDown);
                c.fallen = m.fallen;
                c.stats_lowered_this_turn = m.stats_lowered_this_turn;
                // getStat('spe'): the action speed without Trick Room's sign.
                c.spe_stat = self
                    .action_speed_of(self.mon_ref(side, pos))
                    .map_or(0, |s| s.unsigned_abs());
                c.moved_this_turn = !m.newly_switched && !self.will_move(self.mon_ref(side, pos));
                c.will_move = self.will_move(self.mon_ref(side, pos));
                c.hurt_this_turn = m.hurt_this_turn.is_some_and(|h| h > 0);
                // Avalanche: who damaged it this turn, by where they stand.
                for a in m.attacked_by.iter().filter(|a| a.this_turn && a.damage > 0) {
                    if let Some(p) = (0..ACTIVE_PER_SIDE).find(|&p| {
                        self.sides[a.source.side].slot_filled[p]
                            && self.mon_ref(a.source.side, p) == a.source
                    }) {
                        c.damaged_by |= 1 << (a.source.side * 2 + p);
                    }
                }
                c.volatiles.helping_hand = m
                    .volatiles
                    .0
                    .iter()
                    .find(|v| v.id == VolatileId::HelpingHand)
                    .map_or(0, |v| v.counter as u8);
                out[side * 2 + pos] = Some(c);
            }
        }
        out
    }

    fn damage_ctx<'a>(
        &self,
        view: &'a [Option<Combatant>; 4],
        attacker: MonRef,
        defender: MonRef,
        crit: bool,
        spread: bool,
    ) -> DamageCtx<'a> {
        DamageCtx {
            actives: [
                view[0].as_ref(),
                view[1].as_ref(),
                view[2].as_ref(),
                view[3].as_ref(),
            ],
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
            bypass_protect: false,
            hit_sub: false,
            gravity: self.field.gravity > 0,
        }
    }

    /// `runMove`.
    pub(super) fn run_move(
        &mut self,
        user: MonRef,
        slot: usize,
        target_loc: i8,
        priority: i8,
    ) -> Res<()> {
        self.mon_mut(user).active_move_actions += 1;
        // A recharge turn: mustrecharge's BeforeMove (priority 11) stops
        // whatever move comes (Encore may have swapped one in).
        if self.mon(user).volatiles.has(VolatileId::MustRecharge) {
            let m = self.mon_mut(user);
            m.volatiles.remove(VolatileId::GlaiveRush);
            m.volatiles.remove(VolatileId::MustRecharge);
            m.volatiles.remove(VolatileId::DestinyBond);
            if m.volatiles.remove(VolatileId::TwoTurnMove) {
                m.volatiles
                    .0
                    .retain(|v| !matches!(v.id, VolatileId::Charging(_)));
            }
            m.move_this_turn_result = Some(None);
            return Ok(());
        }
        let move_id = move_for_slot(self.mon(user), slot);
        let data = Dex::get().move_data(move_id);
        let target = self.get_target(user, data.target, target_loc);
        let can_move = self.before_move(user, move_id, data);
        // destinybond's BeforeMove (priority -1) and MoveAborted: it lasts
        // only until the user's next move.
        if !can_move || data.id != "destinybond" {
            self.mon_mut(user).volatiles.remove(VolatileId::DestinyBond);
        }
        if !can_move {
            // MoveAborted: twoturnmove ends (and with it the charge).
            let m = self.mon_mut(user);
            if m.volatiles.remove(VolatileId::TwoTurnMove) {
                m.volatiles
                    .0
                    .retain(|v| !matches!(v.id, VolatileId::Charging(_)));
            }
            m.move_this_turn_result = Some(Some(false));
            self.charge_spent(user, data);
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
        // AfterMove: charge ends after an Electric move, then White Herb's
        // onAnyAfterMove.
        self.charge_spent(user, data);
        if self.mon(user).is_active || target.is_some_and(|t| self.mon(t).is_active) {
            self.any_white_herb(user);
        }
        self.faint_messages()?;
        Ok(())
    }

    /// charge's onAfterMove / onMoveAborted: used up by an Electric move.
    fn charge_spent(&mut self, user: MonRef, data: &MoveData) {
        if data.move_type == Dex::get().type_id("Electric").expect("Electric")
            && data.id != "charge"
        {
            self.mon_mut(user).volatiles.remove(VolatileId::Charge);
        }
    }

    /// `useMoveInner` for moves that target Pokemon.
    fn use_move(
        &mut self,
        user: MonRef,
        move_id: MoveId,
        target: Option<MonRef>,
        priority: i8,
    ) -> Res<bool> {
        self.use_move_inner(user, move_id, target, priority, false)
    }

    /// `useMove` for a move Magic Bounce sends back: no PP, no BeforeMove,
    /// but its result is the bouncer's moveThisTurnResult.
    /// The bounced move keeps the original's priority (useMoveInner copies
    /// `activeMove.priority`), which Armor Tail and Psychic Terrain see.
    fn bounce_move(
        &mut self,
        user: MonRef,
        move_id: MoveId,
        target: MonRef,
        priority: i8,
    ) -> Res<()> {
        self.mon_mut(user).move_this_turn_result = None;
        let r = self.use_move_inner(user, move_id, Some(target), priority, true)?;
        let m = self.mon_mut(user);
        if m.move_this_turn_result.is_none() {
            m.move_this_turn_result = Some(Some(r));
        }
        Ok(())
    }

    fn use_move_inner(
        &mut self,
        user: MonRef,
        move_id: MoveId,
        target: Option<MonRef>,
        priority: i8,
        bounced: bool,
    ) -> Res<bool> {
        let saved = self.mold_breaker;
        let r = self.use_move_body(user, move_id, target, priority, bounced);
        self.mold_breaker = saved;
        r
    }

    fn use_move_body(
        &mut self,
        user: MonRef,
        move_id: MoveId,
        target: Option<MonRef>,
        priority: i8,
        bounced: bool,
    ) -> Res<bool> {
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
        let mut am =
            damage::prepare_move(&ctx, move_id).map_err(|e| BattleError::Unsupported(e.0))?;
        // Round moved up by another's Round: sourceEffect round.
        if data.id == "round" {
            am.round_boost = std::mem::take(&mut self.mon_mut(user).round_boost);
        }
        // Curse's onModifyMove: on itself unless a Ghost; a Ghost aiming at
        // nothing or an ally curses a random foe.
        if data.id == "curse" {
            let ghost = Dex::get().type_id("Ghost").expect("Ghost");
            if !self.mon(user).has_type(ghost) {
                am.target = MoveTarget::SelfTarget;
            } else if target.is_none_or(|t| t != user && t.side == user.side) {
                am.target = MoveTarget::RandomNormal;
            }
        }
        // Shell Side Arm's onModifyMove: physical (and contact) if that
        // would do more, a coin flip on a tie.
        if data.id == "shellsidearm" {
            self.ssa_physical = false;
            if defender != user {
                let (u, d) = (self.mon(user), self.mon(defender));
                let base = 2 * 50 / 5 + 2;
                let calc = |a: u32, b: u32| base * 90 * a / b / 50;
                let physical = calc(
                    super::boosted(u.stats[1], u.boosts[0]),
                    super::boosted(d.stats[2], d.boosts[1]),
                );
                let special = calc(
                    super::boosted(u.stats[3], u.boosts[2]),
                    super::boosted(d.stats[4], d.boosts[3]),
                );
                if physical > special || (physical == special && self.chance.chance(1, 2)) {
                    am.category = Category::Physical;
                    am.contact = true;
                    self.ssa_physical = true;
                }
            }
        }
        // Mold Breaker's onModifyMove: the move ignores breakable abilities.
        self.mold_breaker = am.ignore_ability.then_some(user);
        // Stance Change (onModifyMove priority 1): Blade to attack, Shield
        // for King's Shield (not permanent).
        if self.ability_is(user, "stancechange")
            && !self.mon(user).transformed
            && Dex::get().species(self.mon(user).species).base_species == "Aegislash"
        {
            let forme = match (data.category, data.id.as_str()) {
                (_, "kingsshield") => Some("Aegislash"),
                (Category::Status, _) => None,
                _ => Some("Aegislash-Blade"),
            };
            if let Some(name) = forme {
                let dex = Dex::get();
                let id = dex.species_id(name).expect("Aegislash forme");
                let m = self.mon_mut(user);
                if m.species != id {
                    // formeChange -> setSpecies: the forme's types and stats.
                    m.species = id;
                    m.set_types(dex.species(id).types);
                    let stats = crate::stats::compute_stats(id, m.set.nature, m.set.points);
                    m.stats = [m.stats[0], stats[1], stats[2], stats[3], stats[4], stats[5]];
                }
            }
        }
        // frz's onModifyMove: a defrosting move thaws its user.
        if data.flags.has("defrost") && self.mon(user).status == Status::Freeze {
            self.cure_status(user);
        }
        // throatchop's onModifyMove (it stops a bounced sound move too) ends
        // ModifyMove before the Choice item's lock.
        if data.flags.has("sound") && self.mon(user).volatiles.has(VolatileId::ThroatChop) {
            return Ok(false);
        }
        // healblock's onModifyMove.
        if data.flags.has("heal") && self.mon(user).volatiles.has(VolatileId::HealBlock) {
            return Ok(false);
        }
        self.choice_lock(user, move_id);
        let mut target = target;
        if am.target != base_target {
            target = self.random_target(user, am.target);
        }
        if self.mon(user).hp == 0 {
            return Ok(false);
        }
        let Some(target) = target else {
            return Ok(false);
        };
        if matches!(
            am.target,
            MoveTarget::All | MoveTarget::AllySide | MoveTarget::FoeSide | MoveTarget::AllyTeam
        ) {
            if !bounced && am.target == MoveTarget::All {
                let foes = self.foes(user);
                self.pressure(user, move_id, &foes);
            }
            // TryMove: Armor Tail and friends let field-wide moves through,
            // except Perish Song (and Flower Shield, Rototiller).
            if am.target == MoveTarget::All
                && priority > 0
                && data.id == "perishsong"
                && self.foes(user).into_iter().any(|f| self.blocks_priority(f))
            {
                return Ok(false);
            }
            if !bounced && data.flags.has("mustpressure") {
                let foes = self.foes(user);
                self.pressure(user, move_id, &foes);
            }
            return self.try_move_hit(user, data, am.move_type, bounced, priority);
        }
        let targets = self.get_move_targets(user, &am, Some(target));
        // A charged move's second turn comes from lockedmove: no Pressure.
        if !bounced && self.mon(user).locked_move().is_none() {
            let pressure_targets = if data.flags.has("mustpressure") {
                self.foes(user)
            } else {
                targets.clone()
            };
            self.pressure(user, move_id, &pressure_targets);
        }
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
        // Double Shock / Burn Up's onTryMove: only an Electric / Fire type.
        let needs = match data.id.as_str() {
            "doubleshock" => Some("Electric"),
            "burnup" => Some("Fire"),
            _ => None,
        };
        if needs.is_some_and(|t| {
            !self
                .mon(user)
                .has_type(Dex::get().type_id(t).expect("type"))
        }) {
            // onTryMove returns null: not a failure for Stomping Tantrum.
            self.mon_mut(user).move_this_turn_result = Some(None);
            return Ok(false);
        }
        // TryMove: a foe's Armor Tail stops priority moves aimed at its side.
        if priority > 0
            && self
                .foes(user)
                .into_iter()
                .any(|f| f.side == last_target.side && self.blocks_priority(f))
        {
            return Ok(false);
        }
        let explodes = matches!(
            data.id.as_str(),
            "explosion" | "selfdestruct" | "mistyexplosion"
        );
        // TryMove: Damp (onAnyTryMove) stops them.
        if explodes
            && self
                .all_active()
                .into_iter()
                .any(|r| self.ability_is(r, "damp"))
        {
            self.selfdestruct_user = None;
            return Ok(false);
        }
        // Explosion: the user faints before the hit (but still attacks).
        if explodes {
            self.faint(user);
            self.selfdestruct_user = Some(user);
        }
        if targets.is_empty() {
            self.selfdestruct_user = None;
            return Ok(false);
        }
        // King's Rock's onModifyMove (after Sheer Force drops the move's
        // secondaries) adds a flinch unless one is there.
        let kings_rock = self.item_of(user) == Some("kingsrock")
            && data.category != Category::Status
            && (am.has_sheer_force
                || !data
                    .secondaries
                    .iter()
                    .any(|s| s.volatile_status.as_deref() == Some("flinch")));
        let mut mv = MoveUse {
            am,
            data,
            self_dropped: false,
            spread: false,
            self_switch: data.self_switch,
            priority,
            hit: 1,
            bounced,
            kings_rock,
            total_damage: 0,
            bypassed: Vec::new(),
            crit_on: Vec::new(),
            hit_targets: Vec::new(),
        };
        let result = self.try_spread_move_hit(user, &mut mv, targets);
        self.selfdestruct_user = None;
        let result = result?;
        // MoveFail: High Jump Kick's crash, Steel Beam's recoil.
        if !result {
            let before = self.mon(user).hp;
            if data.has_key("hasCrashDamage") {
                let amount = (self.mon(user).max_hp() / 2) as u32;
                self.effect_damage(user, amount);
            } else if data.has_key("mindBlownRecoil") && data.multihit.is_none() {
                let amount = (self.mon(user).max_hp() as u32).div_ceil(2);
                self.effect_damage(user, amount);
            }
            if user != last_target && data.category != Category::Status {
                self.emergency_exit(user, before);
            }
        }
        // selfBoost (Clanging Scales), once the move worked.
        if let (true, Some(sb)) = (result, data.self_boost.as_ref()) {
            self.spread_move_hit(vec![Some(user)], user, &mut mv, sb, false, false, true)?;
        }
        // AfterMoveSecondarySelf doesn't run for a Sheer Force move.
        if result && !(mv.am.has_sheer_force && self.ability_is(user, "sheerforce")) {
            // Fell Stinger (the move's own handler first).
            let before = self.mon(user).hp;
            if data.id == "fellstinger"
                && (self.mon(last_target).fainted || self.mon(last_target).hp == 0)
            {
                self.boost(user, &[(0, 3)], Some(user));
            }
            // Magician (ability, before the items): steal from the fastest
            // target hit.
            // The event's handlers are collected first: an item Magician
            // just stole doesn't join in.
            let held = self.mon(user).item.is_some();
            if self.ability_is(user, "magician") {
                self.magician(user, &mv, data);
            }
            if held {
                self.after_move_secondary_self(
                    user,
                    last_target,
                    data.category == Category::Status,
                    mv.total_damage,
                );
            }
            if user != last_target && data.category != Category::Status {
                self.emergency_exit(user, before);
            }
        }
        Ok(result)
    }

    /// A charge move's onTryMove. None: not a charge move. Some(true): it
    /// hits this turn; Some(false): it started charging.
    fn charge_move(&mut self, user: MonRef, move_id: MoveId, data: &MoveData) -> Option<bool> {
        let weather = self.move_weather(user);
        let (boosts, instant) = match data.id.as_str() {
            "electroshot" => (true, weather == crate::damage::Weather::Rain),
            "meteorbeam" => (true, false),
            "solarbeam" | "solarblade" => (false, weather == crate::damage::Weather::Sun),
            "phantomforce" | "shadowforce" | "fly" | "bounce" | "dig" | "dive" => (false, false),
            _ => return None,
        };
        let m = self.mon(user);
        if m.volatiles.has(VolatileId::Charging(move_id)) {
            self.mon_mut(user)
                .volatiles
                .remove(VolatileId::Charging(move_id));
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
        for (id, mv) in [
            (VolatileId::TwoTurnMove, Some(move_id)),
            (VolatileId::Charging(move_id), None),
        ] {
            if self.mon(user).volatiles.has(id) {
                continue;
            }
            self.effect_order += 1;
            let effect_order = self.effect_order;
            self.mon_mut(user).volatiles.0.push(Volatile {
                id,
                duration: id.duration(),
                counter: 0,
                move_id: mv,
                effect_order,
                target_loc,
            });
        }
        Some(false)
    }

    /// The protecting volatile's onTryHit: whether it blocks `data` on `t`,
    /// with the contact punishments of Spiky Shield, King's Shield and
    /// Baneful Bunker.
    fn protect_blocks(&mut self, user: MonRef, t: MonRef, data: &MoveData) -> bool {
        let v = &self.mon(t).volatiles;
        let kind = [
            VolatileId::Protect,
            VolatileId::SpikyShield,
            VolatileId::KingsShield,
            VolatileId::BanefulBunker,
        ]
        .into_iter()
        .find(|&k| v.has(k));
        let Some(kind) = kind else { return false };
        // checkMoveBypassesProtect (King's Shield lets status moves through).
        if !data.flags.has("protect")
            || (kind == VolatileId::KingsShield && data.category == Category::Status)
        {
            return false;
        }
        // HitProtect: Unseen Fist's contact moves go through.
        if self.contact(data) && self.hits_through_protect(user) {
            return false;
        }
        if self.contact(data) {
            match kind {
                VolatileId::SpikyShield => {
                    let amount = (self.mon(user).max_hp() / 8) as u32;
                    self.effect_damage(user, amount);
                }
                VolatileId::KingsShield => {
                    self.boost(user, &[(0, -1)], Some(t));
                }
                VolatileId::BanefulBunker => {
                    self.try_set_status_from(user, Status::Poison, Some(t));
                }
                _ => {}
            }
        }
        true
    }

    /// `tryMoveHit` for moves that hit a side or the field
    /// (runMoveEffects' sideCondition / pseudoWeather, and onHitSide).
    fn try_move_hit(
        &mut self,
        user: MonRef,
        data: &MoveData,
        move_type: crate::dex::TypeId,
        bounced: bool,
        priority: i8,
    ) -> Res<bool> {
        // Entry hazards: TryHitSide lets a foe's Magic Bounce send them back.
        let hazard = match data.id.as_str() {
            "stealthrock" => Some(SideCondition::StealthRock),
            "spikes" => Some(SideCondition::Spikes),
            "toxicspikes" => Some(SideCondition::ToxicSpikes),
            "stickyweb" => Some(SideCondition::StickyWeb),
            _ => None,
        };
        if let Some(c) = hazard {
            self.protean(user, move_type);
            if !bounced {
                let mut bouncers: Vec<(MonRef, i32)> = self
                    .foes(user)
                    .into_iter()
                    .filter(|&f| self.ability_is(f, "magicbounce"))
                    .map(|f| (f, self.mon(f).speed))
                    .collect();
                self.speed_sort(&mut bouncers, |a, b| b.1.cmp(&a.1));
                if let Some(&(holder, _)) = bouncers.first() {
                    self.bounce_move(
                        holder,
                        Dex::get().move_id(&data.id).expect("move id"),
                        user,
                        priority,
                    )?;
                    return Ok(false);
                }
            }
            return Ok(self.add_hazard(1 - user.side, c));
        }
        Ok(self.try_move_hit_inner(user, data, move_type))
    }

    fn try_move_hit_inner(
        &mut self,
        user: MonRef,
        data: &MoveData,
        move_type: crate::dex::TypeId,
    ) -> bool {
        let add = |b: &mut Battle, c: SideCondition, turns: u8| {
            let d = &mut b.sides[user.side].conditions[c as usize];
            if *d > 0 {
                return false;
            }
            *d = turns;
            true
        };
        // onTry, then PrepareHit (Protean, Libero).
        let try_ok = match data.id.as_str() {
            "auroraveil" => self.effective_weather() == crate::damage::Weather::Snow,
            "wideguard" | "quickguard" => self.will_act(),
            _ => true,
        };
        if try_ok {
            self.protean(user, move_type);
        }
        // Reflect and Light Screen's durationCallback: Light Clay makes 8.
        let screen_turns = if self.item_of(user) == Some("lightclay") {
            8
        } else {
            5
        };
        match data.id.as_str() {
            "tailwind" => add(self, SideCondition::Tailwind, 4),
            "reflect" => add(self, SideCondition::Reflect, screen_turns),
            "lightscreen" => add(self, SideCondition::LightScreen, screen_turns),
            // onTry: only in snow.
            "auroraveil" => {
                self.effective_weather() == crate::damage::Weather::Snow
                    && add(self, SideCondition::AuroraVeil, screen_turns)
            }
            "wideguard" | "quickguard" => {
                // onTry: fails as the last to act; onHitSide adds stall even
                // if the guard was already up.
                if !self.will_act() {
                    return false;
                }
                let c = if data.id == "wideguard" {
                    SideCondition::WideGuard
                } else {
                    SideCondition::QuickGuard
                };
                add(self, c, 1);
                self.add_volatile(user, VolatileId::Stall);
                true
            }
            "courtchange" => {
                // Swap the sides' screens, Tailwind and hazards (state and all).
                let swap = [
                    SideCondition::LightScreen,
                    SideCondition::Reflect,
                    SideCondition::Spikes,
                    SideCondition::Tailwind,
                    SideCondition::ToxicSpikes,
                    SideCondition::StealthRock,
                    SideCondition::StickyWeb,
                    SideCondition::AuroraVeil,
                ];
                let mut success = false;
                for c in swap {
                    let i = c as usize;
                    success |= self.sides[0].conditions[i] > 0 || self.sides[1].conditions[i] > 0;
                    let (a, b) = (self.sides[0].conditions[i], self.sides[1].conditions[i]);
                    self.sides[0].conditions[i] = b;
                    self.sides[1].conditions[i] = a;
                    let (a, b) = (
                        self.sides[0].condition_order[i],
                        self.sides[1].condition_order[i],
                    );
                    self.sides[0].condition_order[i] = b;
                    self.sides[1].condition_order[i] = a;
                }
                success
            }
            "haze" => {
                for r in self.all_active() {
                    self.mon_mut(r).boosts = [0; 7];
                }
                true
            }
            "raindance" => self.set_weather(crate::damage::Weather::Rain, user),
            "sunnyday" => self.set_weather(crate::damage::Weather::Sun, user),
            "sandstorm" => self.set_weather(crate::damage::Weather::Sand, user),
            "snowscape" => self.set_weather(crate::damage::Weather::Snow, user),
            // Snow, then out (selfSwitch makes it a success if anyone can
            // come in).
            "chillyreception" => {
                let snow = self.set_weather(crate::damage::Weather::Snow, user);
                let ok = !self.switchable(user.side).is_empty() || snow;
                if ok && self.mon(user).hp > 0 && !self.switchable(user.side).is_empty() {
                    let id = Dex::get().move_id(&data.id).expect("move id");
                    self.mon_mut(user).switch_flag = Some(SwitchFlag::Move(id));
                }
                ok
            }
            "electricterrain" => self.set_terrain(crate::damage::Terrain::Electric, user),
            "grassyterrain" => self.set_terrain(crate::damage::Terrain::Grassy, user),
            "mistyterrain" => self.set_terrain(crate::damage::Terrain::Misty, user),
            "psychicterrain" => self.set_terrain(crate::damage::Terrain::Psychic, user),
            // onHitField: every active Pokemon not already counting down
            // starts; Good as Gold and Soundproof (TryHit) keep it out, but
            // count as a success.
            "perishsong" => {
                let mut result = false;
                for r in self.all_active() {
                    // Invulnerability (a miss), then TryHit: either way it
                    // counts as a success.
                    let missed = self.invulnerable(user, r, data);
                    if missed
                        || (r != user
                            && (self.ability_is(r, "goodasgold")
                                || self.ability_is(r, "soundproof")
                                || self.absorbs(user, r, move_type, data)))
                    {
                        result = true;
                    } else if !self.mon(r).volatiles.has(VolatileId::PerishSong) {
                        self.add_volatile(r, VolatileId::PerishSong);
                        result = true;
                    }
                }
                result
            }
            // Gravity: five turns; anything in the sky comes down.
            "gravity" => {
                if self.field.gravity > 0 {
                    return false;
                }
                self.field.gravity = 5;
                for r in self.all_active() {
                    let sky = semi_invulnerable(self.mon(r)).is_some_and(|c| {
                        matches!(Dex::get().move_data(c).id.as_str(), "fly" | "bounce")
                    });
                    if sky {
                        let m = self.mon_mut(r);
                        m.volatiles.remove(VolatileId::TwoTurnMove);
                        m.volatiles
                            .0
                            .retain(|v| !matches!(v.id, VolatileId::Charging(_)));
                        self.cancel_move(r);
                    }
                }
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
    fn try_spread_move_hit(
        &mut self,
        user: MonRef,
        mv: &mut MoveUse,
        targets: Vec<MonRef>,
    ) -> Res<bool> {
        let data = mv.data;
        let priority = mv.priority;
        let spread_target = mv.am.target;
        let spread_type = mv.am.move_type;
        let bounced = mv.bounced;
        let bounce_move_id = mv.am.id;
        let mut bounce_err: Option<BattleError> = None;
        let mut sure_hit = false;
        let mut targets = targets;
        mv.spread = targets.len() > 1;

        // Try and PrepareHit
        if matches!(data.id.as_str(), "fakeout" | "firstimpression")
            && self.mon(user).active_move_actions > 1
        {
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
            let attacking = self
                .queued_move(targets[0])
                .is_some_and(|m| Dex::get().move_data(m).category != Category::Status);
            if !attacking || self.mon(targets[0]).volatiles.has(VolatileId::MustRecharge) {
                return Ok(false);
            }
        }
        if data.id == "poltergeist" && self.mon(targets[0]).item.is_none() {
            return Ok(false);
        }
        match data.id.as_str() {
            "rest" => {
                let m = self.mon(user);
                if m.status == Status::Sleep
                    || m.hp >= m.max_hp()
                    || matches!(self.ability_id(user), "insomnia" | "vitalspirit")
                {
                    return Ok(false);
                }
            }
            "lastresort" => {
                let m = self.mon(user);
                let has = m
                    .moves
                    .iter()
                    .any(|s| Dex::get().move_data(s.id).id == "lastresort");
                let others_used = m
                    .moves
                    .iter()
                    .all(|s| Dex::get().move_data(s.id).id == "lastresort" || s.used);
                if m.moves.len() < 2 || !has || !others_used {
                    return Ok(false);
                }
            }
            "noretreat" if self.mon(user).volatiles.has(VolatileId::NoRetreat) => return Ok(false),
            // onTryHit: someone has to come in.
            "healingwish" if self.switchable(user.side).is_empty() => return Ok(false),
            // onTryHit: someone must have fainted.
            "revivalblessing" if !self.sides[user.side].pokemon.iter().any(|m| m.fainted) => {
                return Ok(false)
            }
            "round" => self.prioritize_rounds(),
            "stockpile"
                if self
                    .mon(user)
                    .volatiles
                    .0
                    .iter()
                    .any(|v| v.id == VolatileId::Stockpile && v.counter >= 3) =>
            {
                return Ok(false)
            }
            "upperhand" => {
                let ok = self.queued_move_priority(targets[0]).is_some_and(|(m, p)| {
                    p > 0.1 && Dex::get().move_data(m).category != Category::Status
                });
                if !ok {
                    return Ok(false);
                }
            }
            _ => {}
        }
        if data.has_key("stallingMove") && !(self.will_act() && self.stall_move(user)) {
            return Ok(false);
        }
        // The move's onPrepareHit: Destiny Bond fails if still up from last
        // time; Ally Switch may fail on consecutive use.
        match data.id.as_str() {
            "destinybond" if self.mon_mut(user).volatiles.remove(VolatileId::DestinyBond) => {
                return Ok(false)
            }
            "allyswitch" if !self.add_volatile(user, VolatileId::AllySwitch).truthy() => {
                return Ok(false)
            }
            _ => {}
        }
        // PrepareHit: Protean and Libero.
        if !bounced {
            self.protean(user, spread_type);
        }

        // HitProtect: Unseen Fist's contact moves get through Protect and
        // friends (and Wide Guard), noted for the damage.
        if data.flags.has("protect") && self.contact(data) && self.hits_through_protect(user) {
            for &t in &targets {
                let v = &self.mon(t).volatiles;
                let shielded = v.has(VolatileId::Protect)
                    || v.has(VolatileId::SpikyShield)
                    || v.has(VolatileId::BanefulBunker)
                    || (v.has(VolatileId::KingsShield) && data.category != Category::Status)
                    || (self.sides[t.side].condition(SideCondition::WideGuard) > 0
                        && matches!(
                            spread_target,
                            MoveTarget::AllAdjacent | MoveTarget::AllAdjacentFoes
                        ));
                if shielded {
                    mv.bypassed.push(t);
                }
            }
        }
        let mut failed = false;
        let mut step = |b: &mut Battle,
                        targets: &mut Vec<MonRef>,
                        f: &mut dyn FnMut(&mut Battle, MonRef) -> HitRes| {
            let results: Vec<HitRes> = targets.iter().map(|&t| f(b, t)).collect();
            failed |= results.contains(&HitRes::Bool(false));
            let mut i = 0;
            targets.retain(|_| {
                i += 1;
                results[i - 1].hit()
            });
        };

        // hitStepInvulnerabilityEvent: a target charging Fly, Dig, Dive,
        // Bounce, Phantom Force or Shadow Force can't be hit, bar the moves
        // that reach it and No Guard (onAnyInvulnerability, priority 1).
        if data.id != "helpinghand" {
            step(self, &mut targets, &mut |b, t| {
                HitRes::Bool(!b.invulnerable(user, t, data))
            });
        }
        // hitStepTryHitEvent: Psychic Terrain (priority 4) stops priority
        // moves on grounded foes; Protect blocks moves with the protect flag.
        step(self, &mut targets, &mut |b, t| {
            if b.psychic_terrain_blocks(user, t, priority, data.target == MoveTarget::SelfTarget) {
                HitRes::Bool(false)
            } else if data.flags.has("protect")
                && !(b.contact(data) && b.hits_through_protect(user))
                && b.sides[t.side].condition(SideCondition::WideGuard) > 0
                && matches!(
                    spread_target,
                    MoveTarget::AllAdjacent | MoveTarget::AllAdjacentFoes
                )
            {
                // Wide Guard (priority 4).
                HitRes::NotFail
            } else if priority > 0
                && data.flags.has("protect")
                && !(b.contact(data) && b.hits_through_protect(user))
                && b.sides[t.side].condition(SideCondition::QuickGuard) > 0
            {
                // Quick Guard (priority 4).
                HitRes::NotFail
            } else if b.protect_blocks(user, t, data) {
                // Protect and its variants (priority 3).
                HitRes::NotFail
            } else if t != user
                && !bounced
                && data.flags.has("reflectable")
                && b.ability_is(t, "magicbounce")
            {
                // Magic Bounce (priority 1) sends the move back, there and then.
                if let Err(e) = b.bounce_move(t, bounce_move_id, user, priority) {
                    bounce_err.get_or_insert(e);
                }
                HitRes::Bool(false)
            } else if t != user
                && b.ability_is(t, "flashfire")
                && spread_type == Dex::get().type_id("Fire").expect("Fire")
            {
                // Flash Fire absorbs Fire moves (and sets move.accuracy = true,
                // so the move can't miss its other targets).
                b.add_volatile(t, VolatileId::FlashFire);
                sure_hit = true;
                HitRes::Bool(false)
            } else if t != user && data.flags.has("sound") && b.ability_is(t, "soundproof") {
                HitRes::Bool(false)
            } else if t != user && b.absorbs(user, t, spread_type, data) {
                // Volt/Water Absorb, Sap Sipper, Lightning Rod, Storm Drain,
                // Telepathy, Oblivious (Taunt).
                HitRes::Bool(false)
            } else if data.category == Category::Status
                && t != user
                && b.ability_is(t, "goodasgold")
            {
                // Good as Gold (priority 0).
                HitRes::Bool(false)
            } else {
                HitRes::Bool(true)
            }
        });
        if let Some(e) = bounce_err {
            return Err(e);
        }
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
        let prankster_boosted =
            data.category == Category::Status && self.ability_is(user, "prankster");
        step(self, &mut targets, &mut |b, t| {
            let types = b.mon(t).types;
            let powder =
                data.flags.has("powder") && t != user && Dex::get().immune_to("powder", types);
            let prankster = prankster_boosted
                && t.side != user.side
                && Dex::get().immune_to("prankster", types);
            // onTryImmunity: Endeavor needs a target with more HP; Leech
            // Seed doesn't take on Grass types.
            let own = match data.id.as_str() {
                "endeavor" => b.mon(user).hp >= b.mon(t).hp,
                "leechseed" => b
                    .mon(t)
                    .has_type(Dex::get().type_id("Grass").expect("Grass")),
                "worryseed" => matches!(
                    Dex::get().ability(b.mon(t).ability).id.as_str(),
                    "truant" | "insomnia"
                ),
                _ => false,
            };
            HitRes::Bool(!(powder || prankster || own))
        });
        // hitStepAccuracy
        step(self, &mut targets, &mut |b, t| {
            HitRes::Bool(sure_hit || b.accuracy_check(user, t, data))
        });
        // hitStepBreakProtect: Feint lifts protections.
        if data.has_key("breaksProtect") {
            for &t in &targets {
                let m = self.mon_mut(t);
                let mut broke = false;
                for v in [
                    VolatileId::Protect,
                    VolatileId::SpikyShield,
                    VolatileId::KingsShield,
                    VolatileId::BanefulBunker,
                ] {
                    broke |= m.volatiles.remove(v);
                }
                // Crafty Shield and Mat Block aren't in yet.
                for c in [SideCondition::QuickGuard, SideCondition::WideGuard] {
                    let guard = &mut self.sides[t.side].conditions[c as usize];
                    broke |= *guard > 0;
                    *guard = 0;
                }
                if broke {
                    self.mon_mut(t).volatiles.remove(VolatileId::Stall);
                }
            }
        }

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
        // OHKO moves: 30% (Sheer Cold 20% unless the user is Ice), no
        // modifiers; Sheer Cold can't hit Ice types. All levels are equal.
        if data.ohko {
            let ice = Dex::get().type_id("Ice").expect("Ice");
            if data.id == "sheercold" && self.mon(t).has_type(ice) {
                return false;
            }
            let acc = if data.id == "sheercold" && !self.mon(user).has_type(ice) {
                20
            } else {
                30
            };
            if self.mon(t).volatiles.has(VolatileId::GlaiveRush)
                || self.ability_is(user, "noguard")
                || self.ability_is(t, "noguard")
            {
                return true;
            }
            return self.chance.chance(acc, 100);
        }
        let Some(mut acc) = data.accuracy else {
            return true;
        };
        // The Accuracy event: glaiverush (anything hits its holder), No
        // Guard (on either side of the move) and Minimize's weakness.
        if (self.mon(t).volatiles.has(VolatileId::Minimize) && data.flags.has("minimize"))
            || self.mon(t).volatiles.has(VolatileId::GlaiveRush)
            || self.ability_is(user, "noguard")
            || self.ability_is(t, "noguard")
        {
            return true;
        }
        // onModifyMove: weather-dependent accuracy.
        match (data.id.as_str(), self.move_weather(user)) {
            ("thunder" | "hurricane", crate::damage::Weather::Rain)
            | ("blizzard", crate::damage::Weather::Snow) => return true,
            ("thunder" | "hurricane", crate::damage::Weather::Sun) => acc = 50,
            _ => {}
        }
        let always = (data.id == "toxic"
            && self
                .mon(user)
                .has_type(Dex::get().type_id("Poison").expect("Poison")))
            || (data.target == MoveTarget::SelfTarget && data.category == Category::Status);
        if always {
            return true;
        }
        // ModifyBoost: Unaware ignores the other side's accuracy/evasion.
        let unaware = |b: &Battle, r: MonRef| b.ability_is(r, "unaware");
        let acc_boost = if unaware(self, t) {
            0
        } else {
            self.mon(user).boosts[5] as i32
        };
        let eva_boost = if unaware(self, user) || self.ignores_evasion(user, data) {
            0
        } else {
            self.mon(t).boosts[6] as i32
        };
        let boost = (acc_boost.clamp(-6, 6) - eva_boost).clamp(-6, 6);
        // ModifyAccuracy: Compound Eyes (priority -1), then Wide Lens, Zoom
        // Lens and the target's Bright Powder (-2) by holder Speed, chained.
        let mut accuracy = acc as u32;
        if let Some(modifier) = self.accuracy_modifier(user, t, self.physical(data)) {
            accuracy = crate::fixed::modify(accuracy as u64, modifier) as u32;
        }
        if boost > 0 {
            accuracy = accuracy * (3 + boost as u32) / 3;
        } else if boost < 0 {
            accuracy = accuracy * 3 / (3 + (-boost) as u32);
        }
        self.chance.chance(accuracy, 100)
    }

    /// Triple Axel's later hits: boosts on a fractional accuracy, then
    /// ModifyAccuracy, then the Accuracy event.
    fn multi_accuracy(&mut self, user: MonRef, t: MonRef, data: &MoveData) -> bool {
        let Some(acc) = data.accuracy else {
            return true;
        };
        const TABLE: [f64; 7] = [1.0, 4.0 / 3.0, 5.0 / 3.0, 2.0, 7.0 / 3.0, 8.0 / 3.0, 3.0];
        let unaware = |b: &Battle, r: MonRef| b.ability_is(r, "unaware");
        let mut a = acc as f64;
        let acc_boost = if unaware(self, t) {
            0
        } else {
            self.mon(user).boosts[5].clamp(-6, 6)
        };
        a = if acc_boost > 0 {
            a * TABLE[acc_boost as usize]
        } else {
            a / TABLE[(-acc_boost) as usize]
        };
        if !self.ignores_evasion(user, data) && !unaware(self, user) {
            let eva = self.mon(t).boosts[6].clamp(-6, 6);
            if eva > 0 {
                a /= TABLE[eva as usize];
            } else if eva < 0 {
                a *= TABLE[(-eva) as usize];
            }
        }
        if let Some(modifier) = self.accuracy_modifier(user, t, self.physical(data)) {
            a = (((a * modifier as f64).trunc() + 2047.0) / 4096.0).trunc();
        }
        if self.mon(t).volatiles.has(VolatileId::GlaiveRush)
            || self.ability_is(user, "noguard")
            || self.ability_is(t, "noguard")
        {
            return true;
        }
        self.chance.chance(a.ceil() as u32, 100)
    }

    /// `move.ignoreEvasion`: the move's own, or Keen Eye / Illuminate's.
    fn ignores_evasion(&self, user: MonRef, data: &MoveData) -> bool {
        data.has_key("ignoreEvasion")
            || self.ability_is(user, "keeneye")
            || self.ability_is(user, "illuminate")
    }

    /// The ModifyAccuracy chain, if any handler applies: Compound Eyes and
    /// Sand Veil (priority -1), then Wide Lens, Zoom Lens and the target's
    /// Bright Powder (-2), by holder Speed.
    fn accuracy_modifier(&mut self, user: MonRef, t: MonRef, physical: bool) -> Option<u32> {
        let mut mods: Vec<(i8, i32, u8, u32)> = Vec::new();
        let (us, ts) = (self.mon(user).speed, self.mon(t).speed);
        if self.ability_is(user, "compoundeyes") {
            mods.push((-1, us, 7, 5325));
        }
        // gravity's onModifyAccuracy (a field effect, priority 0).
        if self.field.gravity > 0 {
            mods.push((0, 0, 5, 6840));
        }
        if self.ability_is(user, "hustle") && physical {
            mods.push((-1, us, 7, 3277));
        }
        let weather = self.effective_weather();
        if (self.ability_is(t, "sandveil") && weather == crate::damage::Weather::Sand)
            || (self.ability_is(t, "snowcloak") && weather == crate::damage::Weather::Snow)
        {
            mods.push((-1, ts, 7, 3277));
        }
        if self.ability_is(t, "tangledfeet") && self.mon(t).volatiles.has(VolatileId::Confusion) {
            mods.push((-1, ts, 7, 2048));
        }
        match self.item_of(user) {
            Some("widelens") => mods.push((-2, us, 8, 4505)),
            Some("zoomlens") if !self.will_move(t) => mods.push((-2, us, 8, 4915)),
            _ => {}
        }
        if self.item_of(t) == Some("brightpowder") {
            mods.push((-2, ts, 8, 3686));
        }
        if mods.is_empty() {
            return None;
        }
        self.speed_sort(&mut mods, |a, b| {
            b.0.cmp(&a.0).then(b.1.cmp(&a.1)).then(a.2.cmp(&b.2))
        });
        Some(
            mods.iter()
                .fold(crate::fixed::ONE, |m, x| crate::fixed::chain(m, x.3)),
        )
    }

    /// hitStepMoveHitLoop, for a single hit (multi-hit moves aren't supported).
    fn move_hit_loop(&mut self, user: MonRef, mv: &mut MoveUse, targets: Vec<MonRef>) -> Res<bool> {
        let effect = &mv.data.primary;
        mv.hit_targets = targets.clone();
        // How many hits: fixed, or 2-5 weighted (no Loaded Dice / Skill Link).
        // Beat Up's onModifyMove: a hit per able ally (the user always
        // counts), in party order; each hit's power from its base Attack.
        let beat_up: Vec<u32> = if mv.data.id == "beatup" {
            self.sides[user.side]
                .pokemon
                .iter()
                .filter(|a| a.uid == self.mon(user).uid || (!a.fainted && a.status == Status::None))
                .map(|a| 5 + Dex::get().species(a.set.species).base_stats[1] as u32 / 10)
                .collect()
        } else {
            Vec::new()
        };
        // Parental Bond's onPrepareHit: a second hit for single-hit attacks.
        if self.ability_is(user, "parentalbond")
            && mv.data.category != Category::Status
            && mv.data.multihit.is_none()
            && !mv.spread
            && !["noparentalbond", "charge", "futuremove"]
                .iter()
                .any(|f| mv.data.flags.has(f))
        {
            mv.am.parental_bond = true;
        }
        let target_hits = match mv.data.multihit {
            _ if !beat_up.is_empty() => beat_up.len() as u8,
            _ if mv.am.parental_bond => 2,
            None => 1,
            Some((a, b)) if a == b => a,
            // Skill Link: the most hits.
            Some((_, b)) if self.ability_is(user, "skilllink") => b,
            Some((2, 5)) => {
                [2, 2, 2, 2, 2, 2, 2, 3, 3, 3, 3, 3, 3, 3, 4, 4, 4, 5, 5, 5][self.chance.sample(20)]
            }
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
            if hit > 1 && self.mon(user).status == Status::Sleep && !mv.data.has_key("sleepUsable")
            {
                break;
            }
            if targets.iter().all(|&t| self.mon(t).hp == 0) {
                break;
            }
            if hit > 1
                && mv.data.has_key("multiaccuracy")
                && !self.ability_is(user, "skilllink")
                && !self.multi_accuracy(user, targets[0], mv.data)
            {
                break;
            }
            mv.hit = hit;
            // move.totalDamage so far (Innards Out adds it).
            mv.total_damage = total;
            if let Some(&bp) = beat_up.get(hit as usize - 1) {
                mv.am.base_power = bp;
            }
            let (md, tc) = self.spread_move_hit(
                targets.iter().map(|&t| Some(t)).collect(),
                user,
                mv,
                effect,
                true,
                false,
                false,
            )?;
            move_damage = md;
            hit_targets = tc;
            if move_damage.iter().all(|&d| d == HitRes::Bool(false)) {
                break;
            }
            for (i, &md) in move_damage.iter().enumerate() {
                damage[i] = if md == HitRes::Bool(true) || !md.truthy() {
                    HitRes::Num(0)
                } else {
                    md
                };
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
        mv.total_damage = total;
        self.faint_messages()?;
        if total > 0 {
            self.apply_recoil(user, mv.data, total);
        }
        for (i, t) in hit_targets.iter().enumerate() {
            if let Some(t) = *t {
                if t != user {
                    // gotAttacked, with the last hit's damage.
                    let damage = if let HitRes::Num(d) = move_damage[i] {
                        d
                    } else {
                        0
                    };
                    let m = self.mon_mut(t);
                    m.attacked_by.push(Attacker {
                        source: user,
                        damage,
                        this_turn: true,
                    });
                    if matches!(move_damage[i], HitRes::Num(_)) {
                        m.times_attacked = m.times_attacked.saturating_add(hit - 1);
                    }
                }
            }
        }
        self.each_update();
        let hit_list: Vec<MonRef> = hit_targets.iter().flatten().copied().collect();
        // What each target took (Berserk): the whole move for a multi-hit
        // one, else the hit.
        let multihit = mv.data.multihit.is_some() || mv.am.parental_bond;
        let taken: Vec<u32> = (0..hit_targets.len())
            .filter(|&i| hit_targets[i].is_some())
            .map(|i| {
                if multihit {
                    total
                } else if let HitRes::Num(d) = damage[i] {
                    d
                } else {
                    0
                }
            })
            .collect();
        // Champions: Sheer Force doesn't suppress AfterMoveSecondary.
        self.after_move_secondary(&hit_list, &taken, total, user, mv.data);
        // EmergencyExit for targets that dropped to half or below.
        if !(mv.am.has_sheer_force && self.ability_is(user, "sheerforce")) {
            for (i, t) in hit_targets.iter().enumerate() {
                let (Some(t), HitRes::Num(d)) = (*t, damage[i]) else {
                    continue;
                };
                let cur = if hit_targets.len() == 1 { total } else { d };
                let m = self.mon(t);
                if m.hp > 0 {
                    let before =
                        (m.hurt_this_turn.unwrap_or(0) as u32 + cur).min(u16::MAX as u32) as u16;
                    self.emergency_exit(t, before);
                }
            }
        }
        Ok(true)
    }

    /// `applyRecoilDamage`.
    fn apply_recoil(&mut self, user: MonRef, data: &MoveData, total: u32) {
        let before = self.mon(user).hp;
        self.apply_recoil_inner(user, data, total);
        if data.id == "struggle" || data.recoil.is_some() || data.has_key("mindBlownRecoil") {
            self.emergency_exit(user, before);
        }
    }

    fn apply_recoil_inner(&mut self, user: MonRef, data: &MoveData, total: u32) {
        if data.id == "struggle" {
            // Struggle's recoil is direct damage, a quarter of max HP.
            let max = self.mon(user).max_hp() as f64;
            let recoil = ((max / 4.0).round() as u32).max(1);
            if self.mon(user).hp > 0 {
                self.apply_damage(user, recoil);
            }
        } else if data.has_key("mindBlownRecoil") {
            // Steel Beam: half max HP (rounded), not 'recoil', so Rock Head
            // doesn't stop it.
            let amount = (self.mon(user).max_hp() as u32).div_ceil(2);
            self.effect_damage(user, amount);
        } else if let Some((n, d)) = data.recoil {
            let recoil = ((total as f64 * n as f64 / d as f64).round() as u32).max(1);
            // Rock Head's onDamage stops recoil.
            if !self.ability_is(user, "rockhead") {
                self.effect_damage(user, recoil);
            }
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
                    "yawn"
                        if self.mon(t).status != Status::None
                            || !self.run_status_immunity(t, "slp") =>
                    {
                        return Ok((vec![HitRes::Bool(false)], targets));
                    }
                    "disable"
                        if self
                            .mon(t)
                            .last_move
                            .is_none_or(|m| Dex::get().move_data(m).id == "struggle") =>
                    {
                        return Ok((vec![HitRes::Bool(false)], targets));
                    }
                    "psychicfangs" | "brickbreak" | "ragingbull" => {
                        for c in [
                            SideCondition::Reflect,
                            SideCondition::LightScreen,
                            SideCondition::AuroraVeil,
                        ] {
                            self.sides[t.side].conditions[c as usize] = 0;
                        }
                    }
                    // substitute / shedtail's onTryHit (NOT_FAIL).
                    "substitute" => {
                        let m = self.mon(t);
                        if m.volatiles.has(VolatileId::Substitute)
                            || m.hp as u32 * 4 <= m.max_hp() as u32
                            || m.max_hp() == 1
                        {
                            return Ok((vec![HitRes::Bool(false)], targets));
                        }
                    }
                    "shedtail" => {
                        let m = self.mon(t);
                        if self.switchable(t.side).is_empty()
                            || m.volatiles.has(VolatileId::Substitute)
                            || m.hp as u32 <= (m.max_hp() as u32).div_ceil(2)
                        {
                            return Ok((vec![HitRes::Bool(false)], targets));
                        }
                    }
                    "roleplay" => {
                        let dex = Dex::get();
                        let (ta, sa) = (
                            dex.ability(self.mon(t).ability),
                            dex.ability(self.mon(user).ability),
                        );
                        if ta.id == sa.id
                            || ta.flags.iter().any(|f| f == "failroleplay")
                            || sa.flags.iter().any(|f| f == "cantsuppress")
                        {
                            return Ok((vec![HitRes::Bool(false)], targets));
                        }
                    }
                    "worryseed"
                        if Dex::get()
                            .ability(self.mon(t).ability)
                            .flags
                            .iter()
                            .any(|f| f == "cantsuppress") =>
                    {
                        return Ok((vec![HitRes::Bool(false)], targets));
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
        // TryPrimaryHit: the user's Normal Gem.
        let mut damage = vec![HitRes::Bool(true); targets.len()];
        if primary {
            for t in targets.iter().flatten() {
                if *t != user
                    && mv.am.category != Category::Status
                    && mv.am.move_type == Dex::get().type_id("Normal").expect("Normal")
                    && self.item_of(user) == Some("normalgem")
                    && self.use_item(user)
                {
                    self.add_volatile(user, VolatileId::Gem);
                }
            }
        }
        // TryPrimaryHit: substitute (priority -1) takes the hit; its target
        // drops out of the rest (Showdown's null target) but still counts for
        // the user's own effects and secondaries' rolls.
        let mut subbed = vec![false; targets.len()];
        if primary
            && !matches!(
                mv.am.target,
                MoveTarget::All | MoveTarget::AllyTeam | MoveTarget::AllySide | MoveTarget::FoeSide
            )
        {
            for i in 0..targets.len() {
                let Some(t) = targets[i] else { continue };
                if t == user
                    || !self.mon(t).volatiles.has(VolatileId::Substitute)
                    || mv.data.flags.has("bypasssub")
                    || mv.am.infiltrates
                {
                    continue;
                }
                if self.hit_substitute(user, t, mv)? {
                    subbed[i] = true;
                } else {
                    damage[i] = HitRes::Null;
                }
            }
        }
        for i in 0..targets.len() {
            if !damage[i].truthy() || subbed[i] {
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
                damage[i] = self.get_damage(user, t, mv, &view, spread, false)?;
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
            // Berserk's onDamage: a single-hit attack holds healing berries
            // until its AfterMoveSecondary.
            self.mon_mut(t).berserk_checked = mv.data.multihit.is_some() || mv.am.parental_bond;
            // The Damage event: Disguise and Endure can bring it to 0 (still a
            // hit for DamagingHit); spreadDamage only clamps a nonzero amount.
            let d = if d == 0 {
                0
            } else {
                self.on_move_damage(t, d.max(1))
            };
            self.move_damage_by = Some(user);
            let dealt = self.apply_damage(t, d);
            self.move_damage_by = None;
            if dealt != 0 {
                let m = self.mon_mut(t);
                m.hurt_this_turn = Some(m.hp);
            }
            damage[i] = HitRes::Num(dealt);
            if dealt > 0 {
                if let Some((n, den)) = mv.data.drain {
                    let mut amount = (dealt as f64 * n as f64 / den as f64).round() as u32;
                    // TryHeal: Big Root.
                    if self.item_of(user) == Some("bigroot") {
                        amount = crate::fixed::modify(amount as u64, 5324) as u32;
                    }
                    // Liquid Ooze (onSourceTryHeal) turns it into damage.
                    if self.ability_is(t, "liquidooze") {
                        self.effect_damage(user, amount);
                    } else {
                        self.heal(user, amount);
                    }
                }
            }
        }
        for i in 0..targets.len() {
            if damage[i] == HitRes::Bool(false) {
                targets[i] = None;
            }
        }

        self.run_move_effects(
            &mut damage,
            &targets,
            mv,
            user,
            effect,
            primary && !skip_boosts,
            primary,
            is_secondary,
        )?;
        // Double Shock's self.onHit: Electric becomes "???".
        if is_self && matches!(mv.data.id.as_str(), "doubleshock" | "burnup") {
            let lost = Dex::get()
                .type_id(if mv.data.id == "burnup" {
                    "Fire"
                } else {
                    "Electric"
                })
                .expect("type");
            let m = self.mon_mut(user);
            let t = m
                .types
                .map(|t| if t == lost { damage::TYPELESS } else { t });
            m.set_types(t);
        }
        for i in 0..targets.len() {
            if !damage[i].hit() {
                targets[i] = None;
            }
        }

        // Sheer Force deleted the move's `self` along with its secondaries.
        let sheer_force = mv.am.has_sheer_force && primary;
        if let (Some(self_effect), false) = (effect.self_effect.as_deref(), sheer_force) {
            if !mv.self_dropped {
                let with_subs: Vec<Option<MonRef>> = (0..targets.len())
                    .map(|i| targets[i].or(subbed[i].then_some(user)))
                    .collect();
                self.self_drops(&with_subs, user, mv, self_effect, is_secondary)?;
            }
        }
        if primary {
            self.secondaries(&targets, &subbed, user, mv)?;
        }
        // forceSwitch: Roar, Whirlwind, Dragon Tail, Circle Throw.
        if primary && mv.data.has_key("forceSwitch") {
            for t in targets.iter().flatten() {
                if self.mon(*t).hp > 0
                    && self.mon(user).hp > 0
                    && !self.switchable(t.side).is_empty()
                    && !self.resists_drag(*t)
                {
                    self.mon_mut(*t).force_switch_flag = true;
                }
            }
        }

        // DamagingHit for the targets that took damage.
        if !is_secondary && !is_self {
            let damaged: Vec<MonRef> = (0..targets.len())
                .filter_map(|i| targets[i].filter(|_| matches!(damage[i], HitRes::Num(_))))
                .collect();
            let mut pending_exit = None;
            let dealt: Vec<u32> = (0..targets.len())
                .filter_map(|i| match (targets[i], damage[i]) {
                    (Some(_), HitRes::Num(n)) => Some(n),
                    _ => None,
                })
                .collect();
            if !damaged.is_empty() {
                let user_hp = self.mon(user).hp;
                pending_exit = Some(user_hp);
                self.damaging_hit(
                    user,
                    &damaged,
                    &dealt,
                    mv.total_damage,
                    mv.data,
                    mv.am.move_type,
                );
                // AfterHit: Knock Off takes the item (the champions mod's
                // spreadMoveHit doesn't need the user to still have HP).
                if mv.data.id == "knockoff" {
                    for &t in &damaged {
                        self.take_item(t, user);
                    }
                }
                // Ice Spinner ends the terrain.
                if mv.data.id == "icespinner" {
                    self.clear_terrain();
                }
                // Stone Axe and Ceaseless Edge lay hazards; Mortal Spin clears
                // the user's side (none of them with Sheer Force).
                if !mv.am.has_sheer_force {
                    for _ in &damaged {
                        match mv.data.id.as_str() {
                            "stoneaxe" => {
                                self.add_hazard(1 - user.side, SideCondition::StealthRock);
                            }
                            "ceaselessedge" => {
                                self.add_hazard(1 - user.side, SideCondition::Spikes);
                            }
                            "mortalspin" => {
                                self.mon_mut(user).volatiles.remove(VolatileId::LeechSeed);
                                self.mon_mut(user)
                                    .volatiles
                                    .remove(VolatileId::PartiallyTrapped);
                                for c in SideCondition::ALL.into_iter().filter(|c| c.is_hazard()) {
                                    self.sides[user.side].conditions[c as usize] = 0;
                                }
                            }
                            _ => {}
                        }
                    }
                }
            }
            // EmergencyExit for the user hurt during DamagingHit (Rocky
            // Helmet, Rough Skin...).
            if let Some(before) = pending_exit {
                self.emergency_exit(user, before);
            }
        }
        Ok((damage, targets))
    }

    /// Emergency Exit (onEmergencyExit): dropping to half or below from
    /// above it, its holder switches out if it can.
    pub(super) fn emergency_exit(&mut self, r: MonRef, before: u16) {
        let m = self.mon(r);
        let max = m.max_hp() as u32;
        if m.hp == 0
            || m.hp as u32 * 2 > max
            || before as u32 * 2 <= max
            || !self.ability_is(r, "emergencyexit")
        {
            return;
        }
        if self.switchable(r.side).is_empty() || m.force_switch_flag || m.switch_flag.is_some() {
            return;
        }
        self.mon_mut(r).switch_flag = Some(SwitchFlag::Replace);
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
        let mut did_anything = damage
            .iter()
            .copied()
            .reduce(HitRes::combine)
            .unwrap_or(HitRes::Undefined);
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
                let status = status_from_id(status)
                    .ok_or_else(|| BattleError::Unsupported(format!("status {status}")))?;
                let r = self.try_set_status_from(t, status, Some(user));
                if !r && mv.data.primary.status.is_some() {
                    damage[i] = damage[i].combine(HitRes::Bool(false));
                    did_anything = did_anything.combine(HitRes::Null);
                    continue;
                }
                did_something = did_something.combine(HitRes::Bool(r));
            }
            if let Some(v) = &effect.volatile_status {
                let id = VolatileId::parse(v)
                    .ok_or_else(|| BattleError::Unsupported(format!("volatile {v}")))?;
                let r = self.add_volatile(t, id);
                if id == VolatileId::PartiallyTrapped && r.truthy() {
                    let code = ((user.side as u32) << 8) | self.mon(user).uid as u32;
                    let divisor = if self.item_of(user) == Some("bindingband") {
                        6
                    } else {
                        8
                    };
                    if let Some(v) = self.mon_mut(t).volatiles.get_mut(id) {
                        v.counter = code;
                        v.target_loc = divisor;
                    }
                }
                if id == VolatileId::LeechSeed && r.truthy() {
                    let slot = (user.side * 2 + self.mon(user).position) as i8;
                    if let Some(v) = self.mon_mut(t).volatiles.get_mut(id) {
                        v.target_loc = slot;
                    }
                }
                did_something = did_something.combine(r);
            }
            // onHit: Protect and Detect start (or extend) the stall counter.
            if primary && mv.data.has_key("stallingMove") {
                self.add_volatile(t, VolatileId::Stall);
                did_something = did_something.combine(HitRes::Bool(true));
            }
            // Secondaries' onHit: Dire Claw's random status, Throat Chop.
            if is_secondary && mv.data.id == "direclaw" {
                let status =
                    [Status::Poison, Status::Paralysis, Status::Sleep][self.chance.sample(3)];
                self.try_set_status_from(t, status, Some(user));
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if is_secondary && mv.data.id == "triattack" {
                let status =
                    [Status::Burn, Status::Paralysis, Status::Freeze][self.chance.sample(3)];
                self.try_set_status_from(t, status, Some(user));
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if is_secondary && mv.data.id == "burningjealousy" && self.mon(t).stats_raised_this_turn
            {
                self.try_set_status_from(t, Status::Burn, Some(user));
                did_something = did_something.combine(HitRes::Bool(true));
            }
            // Eerie Spell: the target's last move loses 3 PP.
            if is_secondary && mv.data.id == "eeriespell" && self.mon(t).hp > 0 {
                let m = self.mon_mut(t);
                if let Some(i) = m.last_move.and_then(|l| m.move_slot(l)) {
                    m.moves[i].pp = m.moves[i].pp.saturating_sub(3);
                }
                did_something = did_something.combine(HitRes::Bool(true));
            }
            if is_secondary && mv.data.id == "alluringvoice" && self.mon(t).stats_raised_this_turn {
                self.add_volatile(t, VolatileId::Confusion);
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
            if primary {
                did_something =
                    did_something.combine(self.misc_on_hit(user, t, mv.data.id.as_str()));
            }
            // The Hit event: Anger Point after a critical hit.
            if primary
                && mv.crit_on.contains(&t)
                && self.mon(t).hp > 0
                && self.ability_is(t, "angerpoint")
            {
                self.boost(t, &[(0, 12)], Some(t));
            }
            // onHit: Trick swaps items.
            if primary && matches!(mv.data.id.as_str(), "trick" | "switcheroo") {
                let r = self.trick(user, t);
                did_something = did_something.combine(HitRes::Bool(r));
            }
            // onHit: Parting Shot lowers Attack and Sp. Atk, and doesn't
            // switch out if neither drops.
            if primary && mv.data.id == "partingshot" {
                if !self.boost(t, &[(0, -1), (2, -1)], Some(user)).truthy()
                    && !self.ability_is(t, "mirrorarmor")
                {
                    mv.self_switch = false;
                }
                did_something = did_something.combine(HitRes::Bool(true));
            }
            // selfdestruct: 'ifHit' (Memento, Final Gambit).
            if primary
                && matches!(
                    mv.data.id.as_str(),
                    "memento" | "finalgambit" | "healingwish"
                )
                && damage[i] != HitRes::Bool(false)
            {
                self.faint(user);
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
            damage[i] = damage[i].combine(if did_something == HitRes::Null {
                HitRes::Bool(false)
            } else {
                did_something
            });
            did_anything = did_anything.combine(did_something);
        }
        let failed = !did_anything.truthy()
            && did_anything != HitRes::Num(0)
            && effect.self_effect.is_none();
        if !failed && mv.self_switch && self.mon(user).hp > 0 {
            self.mon_mut(user).switch_flag = Some(SwitchFlag::Move(mv.data_id()));
        }
        Ok(())
    }

    /// onHit for Heal Pulse, Psych Up, Speed Swap, Pain Split and Soak.
    fn misc_on_hit(&mut self, user: MonRef, t: MonRef, id: &str) -> HitRes {
        match id {
            "healpulse" => {
                let max = self.mon(t).max_hp() as u64;
                let amount = if self.ability_is(user, "megalauncher") {
                    crate::fixed::modify(max, 3072)
                } else {
                    max.div_ceil(2)
                };
                if self.heal(t, amount as u32).truthy() {
                    HitRes::Bool(true)
                } else {
                    HitRes::NotFail
                }
            }
            "psychup" => {
                // Copy the boosts, then Dragon Cheer and Focus Energy (Dragon
                // Cheer keeps the target's hasDragonType).
                let boosts = self.mon(t).boosts;
                let cheer = self
                    .mon(t)
                    .volatiles
                    .0
                    .iter()
                    .find(|v| v.id == VolatileId::DragonCheer)
                    .map(|v| v.counter);
                let focus = self.mon(t).volatiles.has(VolatileId::FocusEnergy);
                let m = self.mon_mut(user);
                m.boosts = boosts;
                m.volatiles.remove(VolatileId::DragonCheer);
                m.volatiles.remove(VolatileId::FocusEnergy);
                if let Some(c) = cheer {
                    self.add_volatile(user, VolatileId::DragonCheer);
                    if let Some(v) = self
                        .mon_mut(user)
                        .volatiles
                        .get_mut(VolatileId::DragonCheer)
                    {
                        v.counter = c;
                    }
                }
                if focus {
                    self.add_volatile(user, VolatileId::FocusEnergy);
                }
                HitRes::Undefined
            }
            "speedswap" => {
                let (a, b) = (self.mon(user).stats[5], self.mon(t).stats[5]);
                self.mon_mut(user).stats[5] = b;
                self.mon_mut(t).stats[5] = a;
                HitRes::Undefined
            }
            "painsplit" => {
                let (th, uh) = (self.mon(t).hp as u32, self.mon(user).hp as u32);
                let average = ((th + uh) / 2).max(1);
                // sethp: target.hp - (targetHP - average), then the user.
                for r in [t, user] {
                    let m = self.mon_mut(r);
                    if m.hp > 0 {
                        m.hp = average.clamp(1, m.max_hp() as u32) as u16;
                    }
                }
                HitRes::Undefined
            }
            // Baton Pass's onHit: nobody to pass to (the -fail is NOT_FAIL).
            "batonpass" if self.switchable(user.side).is_empty() => HitRes::NotFail,
            // substitute / shedtail's onHit (after the volatile is up).
            "substitute" => {
                let cost = (self.mon(t).max_hp() / 4).max(1) as u32;
                self.apply_damage(t, cost);
                HitRes::Undefined
            }
            "shedtail" => {
                let cost = (self.mon(t).max_hp() as u32).div_ceil(2);
                self.apply_damage(t, cost);
                HitRes::Undefined
            }
            "clearsmog" => {
                self.mon_mut(t).boosts = [0; 7];
                HitRes::Undefined
            }
            "worryseed" => {
                let insomnia = Dex::get().ability_id("insomnia").expect("insomnia");
                self.set_ability(t, insomnia);
                if self.mon(t).status == Status::Sleep {
                    self.cure_status(t);
                }
                HitRes::Undefined
            }
            "roleplay" => {
                let a = self.mon(t).ability;
                self.set_ability(user, a);
                if let Some(e) = super::field::start_effect(&Dex::get().ability(a).id) {
                    self.ability_start(user, e);
                }
                HitRes::Undefined
            }
            "acupressure" => {
                let stats: Vec<usize> = (0..7).filter(|&s| self.mon(t).boosts[s] < 6).collect();
                if stats.is_empty() {
                    return HitRes::Bool(false);
                }
                let s = stats[self.chance.sample(stats.len())];
                self.boost(t, &[(s, 2)], Some(user));
                HitRes::Undefined
            }
            "skillswap" => {
                if !self.skill_swap(user, t) {
                    return HitRes::Bool(false);
                }
                HitRes::Undefined
            }
            "simplebeam" | "entrainment" => {
                let dex = Dex::get();
                let new = if id == "simplebeam" {
                    dex.ability_id("simple").expect("simple")
                } else {
                    self.mon(user).ability
                };
                let cur = dex.ability(self.mon(t).ability);
                let cant = cur.flags.iter().any(|f| f == "cantsuppress") || cur.id == "truant";
                let bad = if id == "simplebeam" {
                    cant || cur.id == "simple"
                } else {
                    t == user
                        || self.mon(t).ability == new
                        || cant
                        || dex.ability(new).flags.iter().any(|f| f == "noentrain")
                };
                if bad || self.mon(t).hp == 0 {
                    return HitRes::Bool(false);
                }
                self.set_ability(t, new);
                if let Some(e) = super::field::start_effect(&dex.ability(new).id) {
                    self.ability_start(t, e);
                }
                HitRes::Undefined
            }
            "quash" => HitRes::Bool(self.quash(t)).or_undefined(),
            "afteryou" => HitRes::Bool(self.after_you(t)).or_undefined(),
            "bellydrum" => {
                let m = self.mon(t);
                let max = m.max_hp() as u32;
                if m.hp as u32 * 2 <= max || m.boosts[0] >= 6 || max == 1 {
                    return HitRes::Bool(false);
                }
                self.apply_damage(t, (max / 2).max(1));
                self.boost(t, &[(0, 12)], Some(t));
                HitRes::Undefined
            }
            "topsyturvy" => {
                let m = self.mon_mut(t);
                if m.boosts.iter().all(|&b| b == 0) {
                    return HitRes::Bool(false);
                }
                for b in m.boosts.iter_mut() {
                    *b = -*b;
                }
                HitRes::Undefined
            }
            "moonlight" | "synthesis" | "morningsun" => {
                use crate::damage::Weather;
                let factor = match self.move_weather(t) {
                    Weather::Sun => 2732,
                    Weather::Rain | Weather::Sand | Weather::Snow => 1024,
                    _ => 2048,
                };
                let amount = crate::fixed::modify(self.mon(t).max_hp() as u64, factor) as u32;
                if self.heal(t, amount).truthy() {
                    HitRes::Bool(true)
                } else {
                    HitRes::NotFail
                }
            }
            "rest" => {
                if !self.set_status_from(t, Status::Sleep, Some(user)) {
                    return HitRes::Bool(false);
                }
                if self.mon(t).status == Status::Sleep {
                    self.mon_mut(t).status_state.time = 3;
                }
                let max = self.mon(t).max_hp() as u32;
                self.heal(t, max);
                HitRes::Undefined
            }
            "strengthsap" => {
                let m = self.mon(t);
                if m.boosts[0] == -6 {
                    return HitRes::Bool(false);
                }
                let atk = super::boosted(m.stats[1], m.boosts[0]);
                let success = self.boost(t, &[(0, -1)], Some(user)).truthy();
                // TryHeal: Big Root.
                let amount = if self.item_of(user) == Some("bigroot") {
                    crate::fixed::modify(atk as u64, 5324) as u32
                } else {
                    atk
                };
                let healed = if self.ability_is(t, "liquidooze") {
                    self.effect_damage(user, amount);
                    false
                } else {
                    self.heal(user, amount).truthy()
                };
                HitRes::Bool(healed || success)
            }
            "magicpowder" => {
                let psychic = Dex::get().type_id("Psychic").expect("Psychic");
                let m = self.mon_mut(t);
                if m.types == [psychic, psychic] {
                    return HitRes::Bool(false);
                }
                m.set_types([psychic, psychic]);
                HitRes::Undefined
            }
            "curse" => {
                let ghost = Dex::get().type_id("Ghost").expect("Ghost");
                if !self.mon(user).has_type(ghost) {
                    return HitRes::Bool(
                        self.boost(user, &[(4, -1), (0, 1), (1, 1)], Some(user))
                            .truthy(),
                    );
                }
                // onTryHit: not twice.
                if self.mon(t).volatiles.has(VolatileId::Curse) {
                    return HitRes::Bool(false);
                }
                // directDamage: half the user's max HP, no Magic Guard.
                let cost = (self.mon(user).max_hp() / 2) as u32;
                self.apply_damage(user, cost);
                let t = if t.side == user.side {
                    match self.random_target(user, MoveTarget::RandomNormal) {
                        Some(r) if r.side != user.side => r,
                        _ => return HitRes::Bool(false),
                    }
                } else {
                    t
                };
                self.mon_mut(t).volatiles.remove(VolatileId::Curse);
                self.add_volatile(t, VolatileId::Curse);
                HitRes::Undefined
            }
            "transform" => HitRes::Bool(self.transform_into(user, t)),
            // Instruct: the target uses its last move again, right now.
            "instruct" => {
                let m = self.mon(t);
                let Some(last) = m.last_move else {
                    return HitRes::Bool(false);
                };
                let data = Dex::get().move_data(last);
                let slot = m.move_slot(last);
                if ["failinstruct", "charge", "recharge"]
                    .iter()
                    .any(|f| data.flags.has(f))
                    || slot.is_some_and(|s| m.moves[s].pp == 0)
                {
                    return HitRes::Bool(false);
                }
                let loc = m.last_move_target_loc;
                if self
                    .prioritize_move(t, slot.unwrap_or(usize::MAX), loc)
                    .is_err()
                {
                    return HitRes::Bool(false);
                }
                HitRes::Undefined
            }
            // slotCondition: the next hurt Pokemon to come in here is healed.
            "healingwish" => {
                let pos = self.mon(user).position;
                self.sides[user.side].healing_wish[pos] = true;
                HitRes::Bool(true)
            }
            // slotCondition: the user's next switch choice revives.
            "revivalblessing" => {
                let pos = self.mon(user).position;
                self.sides[user.side].revival_blessing[pos] = true;
                HitRes::Bool(true)
            }
            "allyswitch" => {
                if self.swap_position(user) {
                    HitRes::Undefined
                } else {
                    HitRes::NotFail
                }
            }
            // Bug Bite: steal and eat the target's berry.
            "bugbite" | "pluck" => {
                let berry = self.mon(t).item.filter(|&i| Dex::get().item(i).is_berry);
                if self.mon(user).hp > 0 && berry.is_some() {
                    if let Some(item) = self.take_item(t, user) {
                        self.eat_effect(user, item);
                    }
                }
                HitRes::Undefined
            }
            "soak" => {
                let water = Dex::get().type_id("Water").expect("Water");
                let m = self.mon_mut(t);
                if m.types == [water, water] {
                    return HitRes::Null;
                }
                m.set_types([water, water]);
                HitRes::Undefined
            }
            _ => HitRes::Undefined,
        }
    }

    /// `getDamage` for one target: the crit roll, the calculation, a resist
    /// berry eaten on the way (not when hitting a substitute) and the
    /// damage roll.
    #[allow(clippy::too_many_arguments)]
    fn get_damage(
        &mut self,
        user: MonRef,
        t: MonRef,
        mv: &mut MoveUse,
        view: &[Option<Combatant>; 4],
        spread: bool,
        hit_sub: bool,
    ) -> Res<HitRes> {
        // ModifyCritRatio: Scope Lens, and Leek for Farfetch'd/Sirfetch'd.
        let mut ratio = mv.data.crit_ratio as i32;
        if self.mon(user).volatiles.has(VolatileId::FocusEnergy) {
            ratio += 2;
        }
        if let Some(v) = self
            .mon(user)
            .volatiles
            .0
            .iter()
            .find(|v| v.id == VolatileId::DragonCheer)
        {
            ratio += 1 + v.counter as i32;
        }
        match self.item_of(user) {
            Some("scopelens") => ratio += 1,
            Some("leek") => {
                let base =
                    crate::dex::to_id(&Dex::get().species(self.mon(user).species).base_species);
                if base == "farfetchd" || base == "sirfetchd" {
                    ratio += 2;
                }
            }
            _ => {}
        }
        if self.ability_is(user, "superluck") {
            ratio += 1;
        }
        // Merciless: poisoned targets always take critical hits.
        if self.ability_is(user, "merciless")
            && matches!(self.mon(t).status, Status::Poison | Status::Toxic)
        {
            ratio = ratio.max(5);
        }
        let crit_ratio = ratio.clamp(0, 4) as usize;
        // getDamage returns OHKO, damageCallback and fixed damage before
        // the crit roll.
        let fixed = mv.data.ohko
            || mv.data.fixed_damage.is_some()
            || mv.data.handlers.has("damageCallback");
        let crit = match mv.data.will_crit {
            _ if fixed => false,
            Some(c) => c,
            None => crit_ratio > 0 && self.chance.chance(1, [0, 24, 8, 2, 1][crit_ratio]),
        };
        mv.crit_on.retain(|&c| c != t);
        if crit {
            mv.crit_on.push(t);
        }
        let mut ctx = self.damage_ctx(view, user, t, crit, spread);
        ctx.bypass_protect = mv.bypassed.contains(&t);
        ctx.hit_sub = hit_sub;
        ctx.hit = mv.hit;
        // Fickle Beam's onBasePower: 30% to double.
        mv.am.fickle_beam = mv.data.id == "ficklebeam" && self.chance.chance(3, 10);
        let outcome =
            damage::damage_for(&ctx, &mv.am).map_err(|e| BattleError::Unsupported(e.0))?;
        if !hit_sub
            && matches!(outcome, Outcome::Damage(_))
            && damage::eats_resist_berry(&ctx, &mv.am).map_err(|e| BattleError::Unsupported(e.0))?
        {
            self.eat_resist_berry(t);
        }
        // Final Gambit's damageCallback faints its user.
        if mv.data.id == "finalgambit" && matches!(outcome, Outcome::Damage(_)) {
            self.faint(user);
        }
        Ok(match outcome {
            Outcome::Damage(rolls) => HitRes::Num(rolls[self.chance.random(16) as usize]),
            Outcome::Immune => HitRes::Bool(false),
            Outcome::NoDamage => HitRes::Undefined,
        })
    }

    /// substitute's onTryPrimaryHit: the damage goes to the substitute
    /// (recoil and drain from it, AfterSubDamage). False when the move does
    /// no damage (a status move), which fails against it.
    fn hit_substitute(&mut self, user: MonRef, t: MonRef, mv: &mut MoveUse) -> Res<bool> {
        if mv.data.category == Category::Status {
            return Ok(false);
        }
        let view = self.damage_view();
        let spread = mv.spread;
        let HitRes::Num(mut d) = self.get_damage(user, t, mv, &view, spread, true)? else {
            return Ok(false);
        };
        let Some(v) = self.mon_mut(t).volatiles.get_mut(VolatileId::Substitute) else {
            return Ok(true);
        };
        d = d.min(v.counter);
        v.counter -= d;
        if v.counter == 0 {
            self.mon_mut(t).volatiles.remove(VolatileId::Substitute);
        }
        if d > 0 {
            self.apply_recoil(user, mv.data, d);
        }
        if let Some((n, den)) = mv.data.drain {
            let mut amount = (d * n).div_ceil(den);
            if self.item_of(user) == Some("bigroot") {
                amount = crate::fixed::modify(amount as u64, 5324) as u32;
            }
            if self.ability_is(t, "liquidooze") {
                self.effect_damage(user, amount);
            } else {
                self.heal(user, amount);
            }
        }
        // AfterSubDamage: the move's (Ice Spinner, Steel Roller, Stone Axe,
        // Ceaseless Edge, Mortal Spin), then Air Balloon's.
        let user_hp = self.mon(user).hp > 0;
        match mv.data.id.as_str() {
            "icespinner" if user_hp => self.clear_terrain(),
            "steelroller" => self.clear_terrain(),
            "stoneaxe" if user_hp && !mv.am.has_sheer_force => {
                self.add_hazard(1 - user.side, SideCondition::StealthRock);
            }
            "ceaselessedge" if user_hp && !mv.am.has_sheer_force => {
                self.add_hazard(1 - user.side, SideCondition::Spikes);
            }
            "mortalspin" if user_hp && !mv.am.has_sheer_force => {
                self.mon_mut(user).volatiles.remove(VolatileId::LeechSeed);
                for c in SideCondition::ALL.into_iter().filter(|c| c.is_hazard()) {
                    self.sides[user.side].conditions[c as usize] = 0;
                }
                self.mon_mut(user)
                    .volatiles
                    .remove(VolatileId::PartiallyTrapped);
            }
            _ => {}
        }
        if self.item_of(t) == Some("airballoon") {
            self.mon_mut(t).item = None;
            self.after_use_item(t);
        }
        Ok(true)
    }

    /// `selfDrops`.
    fn self_drops(
        &mut self,
        targets: &[Option<MonRef>],
        user: MonRef,
        mv: &mut MoveUse,
        self_effect: &'static HitEffect,
        is_secondary: bool,
    ) -> Res<()> {
        for t in targets {
            if t.is_none() || mv.self_dropped {
                continue;
            }
            if !is_secondary && !self_effect.boosts.is_empty() {
                let roll = self.chance.random(100);
                if self_effect.chance.is_none_or(|c| roll < c as u32) {
                    self.spread_move_hit(
                        vec![Some(user)],
                        user,
                        mv,
                        self_effect,
                        false,
                        is_secondary,
                        true,
                    )?;
                }
                if mv.data.multihit.is_none() && !mv.am.parental_bond {
                    mv.self_dropped = true;
                }
            } else {
                self.spread_move_hit(
                    vec![Some(user)],
                    user,
                    mv,
                    self_effect,
                    false,
                    is_secondary,
                    true,
                )?;
            }
        }
        Ok(())
    }

    /// `secondaries`.
    fn secondaries(
        &mut self,
        targets: &[Option<MonRef>],
        subbed: &[bool],
        user: MonRef,
        mv: &mut MoveUse,
    ) -> Res<()> {
        let secondaries: &[HitEffect] = if mv.am.has_sheer_force {
            &[]
        } else {
            &mv.data.secondaries
        };
        for (i, &t) in targets.iter().enumerate() {
            // A substitute took the hit: each secondary still rolls, and only
            // its effect on the user (self) happens.
            if subbed.get(i).copied().unwrap_or(false) {
                for sec in secondaries {
                    let roll = self.chance.random(100);
                    if let (true, Some(se)) = (
                        sec.chance.is_none_or(|c| roll < c as u32),
                        sec.self_effect.as_deref(),
                    ) {
                        self.self_drops(&[Some(user)], user, mv, se, true)?;
                    }
                }
                if mv.kings_rock {
                    self.chance.random(100);
                }
                continue;
            }
            let Some(t) = t else { continue };
            // ModifySecondaries: Shield Dust keeps only effects on the user.
            let dust = self.ability_is(t, "shielddust");
            for sec in secondaries {
                if dust && sec.self_effect.is_none() {
                    continue;
                }
                let roll = self.chance.random(100);
                if sec.chance.is_none_or(|c| roll < c as u32) {
                    self.spread_move_hit(vec![Some(t)], user, mv, sec, false, true, false)?;
                }
            }
            // King's Rock's onModifyMove adds a 10% flinch.
            if mv.kings_rock && !dust {
                let roll = self.chance.random(100);
                if roll < 10 {
                    self.spread_move_hit(
                        vec![Some(t)],
                        user,
                        mv,
                        &KINGS_ROCK_FLINCH,
                        false,
                        true,
                        false,
                    )?;
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
            let by = self.move_damage_by;
            self.faint_queue.push((t, by));
        }
        d as u32
    }
}

/// The semi-invulnerable move a Pokemon is charging, if any.
pub(super) fn semi_invulnerable(m: &super::state::Mon) -> Option<MoveId> {
    m.volatiles.0.iter().find_map(|v| match v.id {
        VolatileId::Charging(id)
            if matches!(
                Dex::get().move_data(id).id.as_str(),
                "phantomforce" | "shadowforce" | "fly" | "bounce" | "dig" | "dive"
            ) =>
        {
            Some(id)
        }
        _ => None,
    })
}
