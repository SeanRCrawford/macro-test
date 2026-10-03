//! Statuses, volatiles, stat stages, healing and the residual phase:
//! Showdown's pokemon.setStatus/addVolatile, battle.boost/heal/damage, the
//! status and volatile conditions in data/conditions.ts (with the champions
//! mod's sleep, freeze and paralysis changes) and fieldEvent('Residual').

use super::state::{SideCondition, StatusState, Volatile, VolatileId, ACTIVE_PER_SIDE};
use super::{Battle, MonRef, Res};
use crate::damage::Status;
use crate::dex::{Dex, MoveData, MoveId};
use std::cmp::Ordering;

/// Showdown's loosely typed hit results (`number | boolean | null |
/// undefined | NOT_FAIL`), so `combineResults` can be mirrored exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum HitRes {
    Undefined,
    /// `NOT_FAIL` (''): nothing happened, but not a failure.
    NotFail,
    Null,
    Bool(bool),
    Num(u32),
}

impl HitRes {
    pub(super) fn truthy(self) -> bool {
        match self {
            HitRes::Bool(b) => b,
            HitRes::Num(n) => n != 0,
            _ => false,
        }
    }

    /// `x || x === 0`: hit, possibly for no damage.
    pub(super) fn hit(self) -> bool {
        self.truthy() || self == HitRes::Num(0)
    }

    fn rank(self) -> u8 {
        match self {
            HitRes::Undefined => 0,
            HitRes::NotFail => 1,
            HitRes::Null => 2,
            HitRes::Bool(_) => 3,
            HitRes::Num(_) => 4,
        }
    }

    /// `battleActions.combineResults`.
    pub(super) fn combine(self, right: HitRes) -> HitRes {
        if self.rank() > right.rank() || (self.truthy() && !right.truthy() && right != HitRes::Num(0)) {
            self
        } else if let (HitRes::Num(a), HitRes::Num(b)) = (self, right) {
            HitRes::Num(a + b)
        } else {
            right
        }
    }
}

pub(super) fn status_from_id(id: &str) -> Option<Status> {
    Some(match id {
        "brn" => Status::Burn,
        "par" => Status::Paralysis,
        "psn" => Status::Poison,
        "tox" => Status::Toxic,
        "slp" => Status::Sleep,
        "frz" => Status::Freeze,
        _ => return None,
    })
}

pub fn status_id(s: Status) -> &'static str {
    match s {
        Status::None => "",
        Status::Burn => "brn",
        Status::Paralysis => "par",
        Status::Poison => "psn",
        Status::Toxic => "tox",
        Status::Sleep => "slp",
        Status::Freeze => "frz",
    }
}

/// A Residual handler (fieldEvent): whose, what, and its sort keys.
#[derive(Debug, Clone, Copy)]
struct Residual {
    mon: Option<MonRef>,
    what: ResidualKind,
    order: u64,
    speed: i32,
    sub_order: u8,
}

#[derive(Debug, Clone, Copy)]
enum ResidualKind {
    WhiteHerb,
    Weather,
    Terrain,
    GrassyHeal,
    TrickRoom,
    Side(usize, SideCondition),
    Leftovers,
    Status(Status),
    Volatile(VolatileId),
}

/// Showdown's comparePriority for event handlers (priority is 0 for every
/// Residual handler the engine has).
fn compare_handlers(a: &Residual, b: &Residual) -> Ordering {
    a.order.cmp(&b.order).then(b.speed.cmp(&a.speed)).then(a.sub_order.cmp(&b.sub_order))
}

struct ResidualOn {
    mon: MonRef,
    what: ResidualKind,
}

/// Handlers without an order sort after every ordered one.
const NO_HANDLER_ORDER: u64 = 4_294_967_296;

impl Battle {
    // --- Statuses -------------------------------------------------------------

    /// `runStatusImmunity` (no supported ability or effect adds an Immunity
    /// handler yet).
    pub(super) fn run_status_immunity(&self, t: MonRef, key: &str) -> bool {
        let m = self.mon(t);
        if m.fainted {
            return false;
        }
        if key == "frz" && self.field.weather == crate::damage::Weather::Sun {
            return false;
        }
        key.is_empty() || !Dex::get().immune_to(key, m.types)
    }

    /// `trySetStatus`: fails if the target already has a status.
    pub(super) fn try_set_status(&mut self, t: MonRef, status: Status) -> bool {
        let current = self.mon(t).status;
        self.set_status(t, if current == Status::None { status } else { current })
    }

    /// `setStatus` (`Status::None` cures).
    pub(super) fn set_status(&mut self, t: MonRef, status: Status) -> bool {
        let m = self.mon(t);
        if m.hp == 0 {
            return false;
        }
        if !m.is_active && status != Status::None {
            return false;
        }
        if m.status == status {
            return false;
        }
        if status != Status::None {
            let key = if status == Status::Toxic { "psn" } else { status_id(status) };
            if !self.run_status_immunity(t, key) {
                return false;
            }
            if self.terrain_blocks_status(t, status) {
                return false;
            }
        }
        // onStart
        let state = match status {
            Status::Sleep => StatusState { time: [2, 3, 3][self.chance.sample(3)], stage: 0 },
            Status::Freeze => StatusState { time: 3, stage: 0 },
            _ => StatusState::default(),
        };
        let m = self.mon_mut(t);
        m.status = status;
        m.status_state = state;
        true
    }

    /// `cureStatus`.
    pub(super) fn cure_status(&mut self, t: MonRef) -> bool {
        let m = self.mon(t);
        if m.hp == 0 || m.status == Status::None {
            return false;
        }
        self.set_status(t, Status::None);
        true
    }

    // --- Volatiles ------------------------------------------------------------

    /// `addVolatile`. Returns Showdown's result (false when already present
    /// and the condition has no onRestart).
    pub(super) fn add_volatile(&mut self, t: MonRef, id: VolatileId) -> HitRes {
        if self.mon(t).hp == 0 {
            return HitRes::Bool(false);
        }
        if let Some(v) = self.mon_mut(t).volatiles.get_mut(id) {
            return match id {
                // stall's onRestart
                VolatileId::Stall => {
                    if v.counter < 729 {
                        v.counter *= 3;
                    }
                    v.duration = Some(2);
                    HitRes::Bool(true)
                }
                // helpinghand's onRestart: the multiplier grows again.
                VolatileId::HelpingHand => {
                    v.counter += 1;
                    HitRes::Bool(true)
                }
                _ => HitRes::Bool(false),
            };
        }
        if !self.run_status_immunity(t, id.id()) {
            return HitRes::Bool(false);
        }
        if id == VolatileId::Encore {
            return self.start_encore(t);
        }
        // TryAddVolatile: Misty Terrain stops confusion and Electric Terrain
        // stops Yawn on grounded Pokemon.
        let terrain = self.field.terrain;
        if ((id == VolatileId::Confusion && terrain == crate::damage::Terrain::Misty)
            || (id == VolatileId::Yawn && terrain == crate::damage::Terrain::Electric))
            && self.grounded(t)
        {
            return HitRes::Null;
        }
        let mut duration = id.duration();
        let mut move_id = None;
        let counter = match id {
            VolatileId::Stall => 3,
            VolatileId::HelpingHand => 1,
            // confusion's onStart: 2-5 turns.
            VolatileId::Confusion => self.chance.random_range(2, 6),
            _ => 0,
        };
        match id {
            // taunt's onStart: a turn longer if it already acted this turn.
            VolatileId::Taunt if self.mon(t).active_turns > 0 && !self.will_move(t) => duration = Some(4),
            // disable's onStart: on the last move, with PP; a turn shorter if
            // the target is still to move.
            VolatileId::Disable => {
                if self.will_move(t) {
                    duration = Some(4);
                }
                let m = self.mon(t);
                let Some(last) = m.last_move else { return HitRes::Bool(false) };
                if m.move_slot(last).is_some_and(|s| m.moves[s].pp == 0) {
                    return HitRes::Bool(false);
                }
                move_id = Some(last);
            }
            VolatileId::Roost => {
                let dex = Dex::get();
                let (flying, normal) = (dex.type_id("Flying").expect("Flying"), dex.type_id("Normal").expect("Normal"));
                self.mon_mut(t).start_roost(flying, normal);
            }
            _ => {}
        }
        self.effect_order += 1;
        let effect_order = self.effect_order;
        self.mon_mut(t).volatiles.0.push(Volatile { id, duration, counter, move_id, effect_order, target_loc: 0 });
        HitRes::Bool(true)
    }

    /// A volatile's onEnd when its duration runs out in the residual phase.
    fn end_volatile(&mut self, t: MonRef, id: VolatileId) {
        self.mon_mut(t).volatiles.remove(id);
        match id {
            VolatileId::Roost => self.mon_mut(t).end_roost(),
            // twoturnmove's onEnd drops the charging move's volatile.
            VolatileId::TwoTurnMove => {
                let m = self.mon_mut(t);
                m.volatiles.0.retain(|v| !matches!(v.id, VolatileId::Charging(_)));
            }
            // yawn: the target falls asleep.
            VolatileId::Yawn => {
                self.try_set_status(t, Status::Sleep);
            }
            // perishsong: the count reached zero.
            VolatileId::PerishSong => self.faint(t),
            _ => {}
        }
    }

    /// Encore's onStart: lock the target into its last move, and switch its
    /// queued move to that one (or last a turn longer if it won't move).
    fn start_encore(&mut self, t: MonRef) -> HitRes {
        let m = self.mon(t);
        let Some(last) = m.last_move else { return HitRes::Bool(false) };
        let Some(slot) = m.move_slot(last) else { return HitRes::Bool(false) };
        if Dex::get().move_data(last).flags.has("failencore") || m.moves[slot].pp == 0 {
            return HitRes::Bool(false);
        }
        self.effect_order += 1;
        let effect_order = self.effect_order;
        let queued = self.queued_move(t);
        let duration = if queued.is_none() { 4 } else { 3 };
        self.mon_mut(t).volatiles.0.push(Volatile {
            id: VolatileId::Encore,
            duration: Some(duration),
            counter: 0,
            move_id: Some(last),
            effect_order,
            target_loc: 0,
        });
        if queued.is_some_and(|q| q != last) {
            // changeAction (Mental Herb isn't supported).
            if self.change_move_action(t, slot).is_err() {
                return HitRes::Bool(false);
            }
        }
        HitRes::Bool(true)
    }

    /// The StallMove event: stall's onStallMove, if the user has it.
    pub(super) fn stall_move(&mut self, user: MonRef) -> bool {
        let Some(v) = self.mon_mut(user).volatiles.get_mut(VolatileId::Stall) else { return true };
        let counter = v.counter.max(1);
        let success = self.chance.chance(1, counter);
        if !success {
            self.mon_mut(user).volatiles.remove(VolatileId::Stall);
        }
        success
    }

    // --- BeforeMove -----------------------------------------------------------

    /// The BeforeMove event: sleep and freeze (priority 10), flinch (8),
    /// paralysis (1). False: the Pokemon can't move.
    pub(super) fn before_move(&mut self, user: MonRef, move_id: MoveId, data: &MoveData) -> bool {
        // glaiverush (priority 100) ends as its user moves again.
        self.mon_mut(user).volatiles.remove(VolatileId::GlaiveRush);
        match self.mon(user).status {
            Status::Sleep => {
                let m = self.mon_mut(user);
                m.status_state.time -= 1;
                if m.status_state.time <= 0 {
                    self.cure_status(user);
                } else if !data.has_key("sleepUsable") {
                    return false;
                }
            }
            Status::Freeze if !data.flags.has("defrost") => {
                let m = self.mon_mut(user);
                m.status_state.time -= 1;
                if m.status_state.time <= 0 || self.chance.chance(1, 4) {
                    self.cure_status(user);
                } else {
                    return false;
                }
            }
            _ => {}
        }
        let v = &self.mon(user).volatiles;
        if v.has(VolatileId::Flinch) {
            return false;
        }
        // disable (priority 7)
        if v.0.iter().any(|x| x.id == VolatileId::Disable && x.move_id == Some(move_id)) && !data.flags.has("cantusetwice") {
            return false;
        }
        // throatchop (priority 6): no sound moves.
        if v.has(VolatileId::ThroatChop) && data.flags.has("sound") {
            return false;
        }
        // taunt (priority 5): no status moves.
        if v.has(VolatileId::Taunt) && data.category == crate::dex::Category::Status {
            return false;
        }
        // A foe's imprison (priority 4): not the moves it knows too.
        if data.id != "struggle" && self.imprisoned(user, move_id) {
            return false;
        }
        // confusion (priority 3)
        if let Some(c) = self.mon_mut(user).volatiles.get_mut(VolatileId::Confusion) {
            c.counter -= 1;
            if c.counter == 0 {
                self.mon_mut(user).volatiles.remove(VolatileId::Confusion);
            } else if self.chance.chance(33, 100) {
                self.confusion_self_hit(user);
                return false;
            }
        }
        if self.mon(user).status == Status::Paralysis && self.chance.chance(1, 8) {
            return false;
        }
        // choicelock (priority 0)
        self.choice_lock_allows(user, move_id)
    }

    /// Whether a foe with Imprison knows this move.
    pub(super) fn imprisoned(&self, user: MonRef, move_id: MoveId) -> bool {
        let foe = 1 - user.side;
        (0..ACTIVE_PER_SIDE).filter_map(|p| self.sides[foe].occupant(p)).any(|m| {
            m.hp > 0 && !m.fainted && m.volatiles.has(VolatileId::Imprison) && m.move_slot(move_id).is_some()
        })
    }

    /// Confusion's self-hit: getConfusionDamage (a 40 BP typeless physical
    /// hit with its own stats), dealt as move damage (Focus Sash applies).
    fn confusion_self_hit(&mut self, r: MonRef) {
        let m = self.mon(r);
        let atk = super::boosted(m.stats[1], m.boosts[0]) as u64;
        let def = super::boosted(m.stats[2], m.boosts[1]).max(1) as u64;
        let base = ((22 * 40 * atk) / def) / 50 + 2;
        let base = base & 0xFFFF;
        let roll = self.chance.random(16) as u64;
        let damage = (base * (100 - roll) / 100).max(1) as u32;
        if self.mon(r).hp == 0 || !self.mon(r).is_active {
            return;
        }
        let d = self.on_move_damage(r, damage).max(1);
        let dealt = self.apply_damage(r, d);
        if dealt != 0 {
            let m = self.mon_mut(r);
            m.hurt_this_turn = Some(m.hp);
        }
    }

    // --- Stat stages ----------------------------------------------------------

    /// `battle.boost`: Num(0) if the target has no HP, false if it can't be
    /// boosted, null if every stage was already capped, true otherwise.
    /// `source` is who caused it (Defiant and Competitive react to foes).
    pub(super) fn boost(&mut self, t: MonRef, boosts: &[(usize, i8)], source: Option<MonRef>) -> HitRes {
        let m = self.mon(t);
        if m.hp == 0 {
            return HitRes::Num(0);
        }
        if !m.is_active {
            return HitRes::Bool(false);
        }
        if self.sides[1 - t.side].pokemon_left == 0 {
            return HitRes::Bool(false);
        }
        // getCappedBoost, then boostBy one stat at a time.
        let capped: Vec<(usize, i8)> =
            boosts.iter().filter(|b| b.1 != 0).map(|&(stat, n)| (stat, (m.boosts[stat] + n).clamp(-6, 6) - m.boosts[stat])).collect();
        let mut success = HitRes::Null;
        for (stat, n) in capped {
            let m = self.mon_mut(t);
            let cur = m.boosts[stat];
            let new = (cur + n).clamp(-6, 6);
            if new == cur {
                continue;
            }
            m.boosts[stat] = new;
            success = HitRes::Bool(true);
            // AfterEachBoost: Defiant / Competitive answer a foe's drop.
            if n < 0 && source.is_some_and(|s| s.side != t.side) {
                match Dex::get().ability(self.mon(t).ability).id.as_str() {
                    "defiant" => {
                        self.boost(t, &[(0, 2)], Some(t));
                    }
                    "competitive" => {
                        self.boost(t, &[(2, 2)], Some(t));
                    }
                    _ => {}
                }
            }
        }
        success
    }

    // --- HP -------------------------------------------------------------------

    /// `pokemon.faint()`: HP to 0 and queued to faint.
    pub(super) fn faint(&mut self, r: MonRef) {
        let m = self.mon(r);
        if m.fainted || m.faint_queued {
            return;
        }
        let hp = m.hp as u32;
        if hp > 0 {
            self.apply_damage(r, hp);
        } else {
            let m = self.mon_mut(r);
            m.switch_flag = None;
            m.faint_queued = true;
            self.faint_queue.push(r);
        }
    }

    /// `battle.heal`.
    pub(super) fn heal(&mut self, t: MonRef, amount: u32) -> HitRes {
        let amount = if amount != 0 && amount <= 1 { 1 } else { amount };
        if amount == 0 {
            return HitRes::Num(0);
        }
        let m = self.mon_mut(t);
        if m.hp == 0 || !m.is_active || m.hp >= m.max_hp() {
            return HitRes::Bool(false);
        }
        let healed = (amount as u16).min(m.max_hp() - m.hp);
        m.hp += healed;
        HitRes::Num(healed as u32)
    }

    /// `battle.damage` from a non-move effect (residual, recoil): at least 1,
    /// counts as being hurt this turn.
    pub(super) fn effect_damage(&mut self, t: MonRef, amount: u32) -> HitRes {
        let m = self.mon(t);
        if m.hp == 0 {
            return HitRes::Num(0);
        }
        if !m.is_active {
            return HitRes::Bool(false);
        }
        let d = self.apply_damage(t, amount.max(1));
        let m = self.mon_mut(t);
        m.hurt_this_turn = Some(m.hp);
        HitRes::Num(d)
    }

    // --- Residual -------------------------------------------------------------

    /// fieldEvent('Residual'): status damage, then volatile durations, in
    /// handler order; faints are processed after each handler.
    pub(super) fn residual(&mut self) -> Res<()> {
        let mut handlers = Vec::new();
        // Field, then each side's conditions followed by its actives.
        if self.field.trick_room > 0 {
            handlers.push(Residual { mon: None, what: ResidualKind::TrickRoom, order: 27, speed: 0, sub_order: 1 });
        }
        if self.field.weather != crate::damage::Weather::None {
            handlers.push(Residual { mon: None, what: ResidualKind::Weather, order: 1, speed: 0, sub_order: 5 });
        }
        if self.field.terrain != crate::damage::Terrain::None {
            handlers.push(Residual { mon: None, what: ResidualKind::Terrain, order: 27, speed: 0, sub_order: 7 });
        }
        for side in 0..2 {
            for c in SideCondition::ALL {
                if self.sides[side].condition(c) > 0 {
                    let (order, sub_order) = c.residual_order();
                    handlers.push(Residual { mon: None, what: ResidualKind::Side(side, c), order, speed: 0, sub_order });
                }
            }
            for pos in 0..ACTIVE_PER_SIDE {
                let Some(r) = self.occupant(side, pos) else { continue };
                let m = self.mon(r);
                let order = match m.status {
                    Status::Burn => Some(10),
                    Status::Poison | Status::Toxic => Some(9),
                    _ => None,
                };
                if let Some(order) = order {
                    handlers.push(Residual { mon: Some(r), what: ResidualKind::Status(m.status), order, speed: m.speed, sub_order: 0 });
                }
                if self.item_of(r) == Some("whiteherb") {
                    handlers.push(Residual { mon: Some(r), what: ResidualKind::WhiteHerb, order: 29, speed: m.speed, sub_order: 8 });
                }
                if self.item_of(r) == Some("leftovers") {
                    handlers.push(Residual { mon: Some(r), what: ResidualKind::Leftovers, order: 5, speed: m.speed, sub_order: 4 });
                }
                for v in &m.volatiles.0 {
                    if v.duration.is_some() {
                        handlers.push(Residual {
                            mon: Some(r),
                            what: ResidualKind::Volatile(v.id),
                            order: v.id.residual_order().unwrap_or(NO_HANDLER_ORDER),
                            speed: m.speed,
                            sub_order: 2,
                        });
                    }
                }
                if self.field.terrain == crate::damage::Terrain::Grassy {
                    handlers.push(Residual { mon: Some(r), what: ResidualKind::GrassyHeal, order: 5, speed: m.speed, sub_order: 2 });
                }
            }
        }
        self.speed_sort(&mut handlers, compare_handlers);
        for h in handlers {
            let Some(mon) = h.mon else {
                // Field and side conditions: count down, end at zero.
                // A condition whose duration ran out ends without the
                // faint check that follows other handlers.
                let ended = match h.what {
                    ResidualKind::Weather => self.weather_residual(),
                    ResidualKind::Terrain => self.terrain_residual(),
                    ResidualKind::TrickRoom => {
                        self.field.trick_room = self.field.trick_room.saturating_sub(1);
                        self.field.trick_room == 0
                    }
                    ResidualKind::Side(side, c) => {
                        let d = &mut self.sides[side].conditions[c as usize];
                        *d = d.saturating_sub(1);
                        *d == 0
                    }
                    _ => unreachable!(),
                };
                if ended {
                    continue;
                }
                self.faint_messages()?;
                if self.is_over() {
                    return Ok(());
                }
                continue;
            };
            let h = ResidualOn { mon, what: h.what };
            let m = self.mon(h.mon);
            if m.fainted {
                continue;
            }
            match h.what {
                ResidualKind::TrickRoom | ResidualKind::Side(..) | ResidualKind::Weather | ResidualKind::Terrain => unreachable!(),
                ResidualKind::WhiteHerb => self.white_herb(h.mon),
                ResidualKind::GrassyHeal => {
                    if self.field.terrain != crate::damage::Terrain::Grassy {
                        continue;
                    }
                    self.grassy_heal(h.mon);
                }
                ResidualKind::Leftovers => {
                    if self.item_of(h.mon) != Some("leftovers") {
                        continue;
                    }
                    self.leftovers(h.mon);
                }
                ResidualKind::Volatile(id) => {
                    let m = self.mon_mut(h.mon);
                    let Some(v) = m.volatiles.get_mut(id) else { continue };
                    let Some(d) = v.duration.as_mut() else { continue };
                    *d -= 1;
                    if *d == 0 {
                        self.end_volatile(h.mon, id);
                        continue;
                    }
                    // encore's onResidual: ends once the move is out of PP.
                    if id == VolatileId::Encore {
                        let mv = v.move_id;
                        let has_pp = mv.and_then(|mv| m.moves.iter().find(|s| s.id == mv)).is_some_and(|s| s.pp > 0);
                        if !has_pp {
                            m.volatiles.remove(id);
                        }
                    }
                }
                ResidualKind::Status(status) => {
                    if m.status != status {
                        continue;
                    }
                    let max = m.max_hp() as u32;
                    let amount = match status {
                        Status::Burn => max / 16,
                        Status::Poison => max / 8,
                        _ => {
                            let m = self.mon_mut(h.mon);
                            if m.status_state.stage < 15 {
                                m.status_state.stage += 1;
                            }
                            (max / 16).max(1) * m.status_state.stage as u32
                        }
                    };
                    self.effect_damage(h.mon, amount);
                }
            }
            self.faint_messages()?;
            if self.is_over() {
                return Ok(());
            }
        }
        Ok(())
    }
}
