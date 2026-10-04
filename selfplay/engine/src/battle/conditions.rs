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
    /// An onHit returning nothing on success (undefined) and false on
    /// failure.
    pub(super) fn or_undefined(self) -> HitRes {
        if self == HitRes::Bool(true) {
            HitRes::Undefined
        } else {
            self
        }
    }

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
        if self.rank() > right.rank()
            || (self.truthy() && !right.truthy() && right != HitRes::Num(0))
        {
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

/// What caused a stat change, where an ability cares.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(super) enum BoostCause {
    Intimidate,
    MirrorArmor,
    Other,
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
    SpeedBoost,
    Weather,
    Terrain,
    GrassyHeal,
    TrickRoom,
    Side(usize, SideCondition),
    Leftovers,
    Healer,
    Status(Status),
    Volatile(VolatileId),
    /// Harvest, Moody (28) and Hunger Switch (29).
    Ability,
}

/// Showdown's comparePriority for event handlers (priority is 0 for every
/// Residual handler the engine has).
fn compare_handlers(a: &Residual, b: &Residual) -> Ordering {
    a.order
        .cmp(&b.order)
        .then(b.speed.cmp(&a.speed))
        .then(a.sub_order.cmp(&b.sub_order))
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
        if key == "frz" && self.effective_weather() == crate::damage::Weather::Sun {
            return false;
        }
        // Immunity: Sand Rush to sandstorm; TryAddVolatile: Inner Focus to
        // flinching.
        let ab = self.ability_id(t);
        // Dig and Dive's onImmunity: no sandstorm underground or underwater.
        let hidden = super::moves::semi_invulnerable(m)
            .is_some_and(|id| matches!(Dex::get().move_data(id).id.as_str(), "dig" | "dive"));
        if (key == "sandstorm"
            && (hidden || matches!(ab, "sandrush" | "sandveil" | "sandforce" | "overcoat")))
            || (key == "powder" && (ab == "overcoat" || self.item_of(t) == Some("safetygoggles")))
            || (key == "flinch" && ab == "innerfocus")
        {
            return false;
        }
        key.is_empty() || !Dex::get().immune_to(key, m.types)
    }

    /// `trySetStatus`: fails if the target already has a status.
    pub(super) fn try_set_status(&mut self, t: MonRef, status: Status) -> bool {
        self.try_set_status_from(t, status, None)
    }

    /// `trySetStatus` with the Pokemon causing it (Corrosion, Synchronize,
    /// Flower Veil care).
    pub(super) fn try_set_status_from(
        &mut self,
        t: MonRef,
        status: Status,
        source: Option<MonRef>,
    ) -> bool {
        let current = self.mon(t).status;
        self.set_status_from(
            t,
            if current == Status::None {
                status
            } else {
                current
            },
            source,
        )
    }

    /// `setStatus` (`Status::None` cures).
    pub(super) fn set_status(&mut self, t: MonRef, status: Status) -> bool {
        self.set_status_from(t, status, None)
    }

    pub(super) fn set_status_from(
        &mut self,
        t: MonRef,
        status: Status,
        source: Option<MonRef>,
    ) -> bool {
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
            let key = if status == Status::Toxic {
                "psn"
            } else {
                status_id(status)
            };
            // Corrosion poisons regardless of type.
            let corrosive = matches!(status, Status::Poison | Status::Toxic)
                && source.is_some_and(|s| self.ability_is(s, "corrosion"));
            if !corrosive && !self.run_status_immunity(t, key) {
                return false;
            }
            if self.terrain_blocks_status(t, status) {
                return false;
            }
            // SetStatus: Thermal Exchange and Water Bubble can't be burned;
            // Sweet Veil keeps its side awake.
            if status == Status::Burn
                && (self.ability_is(t, "thermalexchange") || self.ability_is(t, "waterbubble"))
            {
                return false;
            }
            if status == Status::Sleep && self.side_has_ability(t.side, "sweetveil") {
                return false;
            }
            // Flower Veil (onAllySetStatus): Grass types on its side, from
            // others.
            if source.is_some_and(|s| s != t)
                && self
                    .mon(t)
                    .has_type(Dex::get().type_id("Grass").expect("Grass"))
                && self.side_has_ability(t.side, "flowerveil")
            {
                return false;
            }
            // Purifying Salt: no status at all; Insomnia, Vital Spirit,
            // Limber and Immunity each keep one out; Leaf Guard in sun.
            let blocked = match self.ability_id(t) {
                "purifyingsalt" => true,
                "insomnia" | "vitalspirit" => status == Status::Sleep,
                "limber" => status == Status::Paralysis,
                "immunity" => matches!(status, Status::Poison | Status::Toxic),
                "leafguard" => self.effective_weather() == crate::damage::Weather::Sun,
                _ => false,
            };
            if blocked {
                return false;
            }
        }
        // onStart
        let state = match status {
            Status::Sleep => StatusState {
                time: [2, 3, 3][self.chance.sample(3)],
                stage: 0,
            },
            Status::Freeze => StatusState { time: 3, stage: 0 },
            _ => StatusState::default(),
        };
        let m = self.mon_mut(t);
        m.status = status;
        m.status_state = state;
        if status != Status::None {
            // AfterSetStatus: Synchronize (priority 0) passes it back, then
            // Lum Berry (-1).
            if let Some(s) = source.filter(|&s| s != t) {
                if self.ability_is(t, "synchronize")
                    && !matches!(status, Status::Sleep | Status::Freeze)
                {
                    self.try_set_status_from(s, status, Some(t));
                }
            }
            self.after_set_status(t);
        }
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
                // allyswitch's onRestart: 1 in `counter` to keep going.
                VolatileId::AllySwitch => {
                    let counter = v.counter.max(1);
                    if !self.chance.chance(1, counter) {
                        self.mon_mut(t).volatiles.remove(VolatileId::AllySwitch);
                        return HitRes::Bool(false);
                    }
                    let v = self.mon_mut(t).volatiles.get_mut(id).expect("allyswitch");
                    if v.counter < 729 {
                        v.counter *= 3;
                    }
                    v.duration = Some(2);
                    HitRes::Bool(true)
                }
                // stockpile's onRestart: up to three layers.
                VolatileId::Stockpile => {
                    if v.counter >= 3 {
                        return HitRes::Bool(false);
                    }
                    v.counter += 1;
                    self.boost(t, &[(1, 1), (3, 1)], Some(t));
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
        // TryAddVolatile: Own Tempo (confusion), Sweet Veil (Yawn, for its
        // side).
        if (id == VolatileId::Confusion && self.ability_is(t, "owntempo"))
            || (id == VolatileId::Yawn
                && (self.side_has_ability(t.side, "sweetveil")
                    || (self.side_has_ability(t.side, "flowerveil")
                        && self
                            .mon(t)
                            .has_type(Dex::get().type_id("Grass").expect("Grass")))
                    || matches!(
                        self.ability_id(t),
                        "purifyingsalt" | "insomnia" | "vitalspirit"
                    )
                    || (self.ability_is(t, "leafguard")
                        && self.effective_weather() == crate::damage::Weather::Sun)))
        {
            return HitRes::Null;
        }
        // TryAddVolatile: Aroma Veil guards its side (itself included).
        if matches!(
            id,
            VolatileId::Disable | VolatileId::Encore | VolatileId::Taunt | VolatileId::HealBlock
        ) && (0..ACTIVE_PER_SIDE).any(|p| {
            self.occupant(t.side, p)
                .is_some_and(|a| self.mon(a).hp > 0 && self.ability_is(a, "aromaveil"))
        }) {
            return HitRes::Null;
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
            VolatileId::Stall | VolatileId::AllySwitch => 3,
            VolatileId::HelpingHand | VolatileId::Stockpile => 1,
            // confusion's onStart: 2-5 turns.
            VolatileId::Confusion => self.chance.random_range(2, 6),
            VolatileId::PartiallyTrapped => {
                duration = Some(self.chance.random_range(5, 7) as u8);
                0
            }
            _ => 0,
        };
        // focusenergy / dragoncheer's onStart: not both.
        if (id == VolatileId::FocusEnergy && self.mon(t).volatiles.has(VolatileId::DragonCheer))
            || (id == VolatileId::DragonCheer && self.mon(t).volatiles.has(VolatileId::FocusEnergy))
        {
            return HitRes::Bool(false);
        }
        let counter = if id == VolatileId::DragonCheer {
            self.mon(t)
                .has_type(Dex::get().type_id("Dragon").expect("Dragon")) as u32
        } else {
            counter
        };
        let counter = if id == VolatileId::Substitute {
            // substitute's onStart: a quarter of max HP; it frees a bound
            // Pokemon.
            self.mon_mut(t)
                .volatiles
                .remove(VolatileId::PartiallyTrapped);
            (self.mon(t).max_hp() / 4) as u32
        } else {
            counter
        };
        match id {
            // taunt's onStart: a turn longer if it already acted this turn.
            VolatileId::Taunt if self.mon(t).active_turns > 0 && !self.will_move(t) => {
                duration = Some(4)
            }
            // disable's onStart: on the last move, with PP; a turn shorter if
            // the target is still to move.
            VolatileId::Disable => {
                if self.will_move(t) {
                    duration = Some(4);
                }
                let m = self.mon(t);
                let Some(last) = m.last_move else {
                    return HitRes::Bool(false);
                };
                if m.move_slot(last).is_some_and(|s| m.moves[s].pp == 0) {
                    return HitRes::Bool(false);
                }
                move_id = Some(last);
            }
            VolatileId::Roost => {
                let dex = Dex::get();
                let (flying, normal) = (
                    dex.type_id("Flying").expect("Flying"),
                    dex.type_id("Normal").expect("Normal"),
                );
                self.mon_mut(t).start_roost(flying, normal);
            }
            _ => {}
        }
        self.effect_order += 1;
        let effect_order = self.effect_order;
        self.mon_mut(t).volatiles.0.push(Volatile {
            id,
            duration,
            counter,
            move_id,
            effect_order,
            target_loc: 0,
        });
        // stockpile's onStart: +1 Defense and Sp. Def.
        if id == VolatileId::Stockpile {
            self.boost(t, &[(1, 1), (3, 1)], Some(t));
        }
        HitRes::Bool(true)
    }

    /// The residual abilities: Harvest, Moody and Hunger Switch.
    fn ability_residual(&mut self, r: MonRef) {
        match self.ability_id(r) {
            // Harvest: in sun, or half the time, the last berry grows back.
            "harvest" => {
                if self.effective_weather() == crate::damage::Weather::Sun || self.chance.chance(1, 2) {
                    let m = self.mon(r);
                    if let Some(last) = m.last_item.filter(|&i| m.hp > 0 && m.item.is_none() && Dex::get().item(i).is_berry) {
                        let m = self.mon_mut(r);
                        m.item = Some(last);
                        m.last_item = None;
                    }
                }
            }
            // Moody: +2 to a random stat not maxed, -1 to another not
            // minimized.
            "moody" => {
                let boosts = self.mon(r).boosts;
                let up: Vec<usize> = (0..5).filter(|&i| boosts[i] < 6).collect();
                let plus = (!up.is_empty()).then(|| up[self.chance.sample(up.len())]);
                let down: Vec<usize> = (0..5).filter(|&i| boosts[i] > -6 && Some(i) != plus).collect();
                let minus = (!down.is_empty()).then(|| down[self.chance.sample(down.len())]);
                let mut b: Vec<(usize, i8)> = Vec::new();
                // The boost object lists stats in order.
                for i in 0..5 {
                    if Some(i) == plus {
                        b.push((i, 2));
                    } else if Some(i) == minus {
                        b.push((i, -1));
                    }
                }
                if !b.is_empty() {
                    self.boost(r, &b, Some(r));
                }
            }
            // Hunger Switch: Morpeko flips between its formes.
            "hungerswitch" => {
                let dex = Dex::get();
                let name = dex.species(self.mon(r).species).name.as_str();
                let to = match name {
                    "Morpeko" => "Morpeko-Hangry",
                    "Morpeko-Hangry" => "Morpeko",
                    _ => return,
                };
                let id = dex.species_id(to).expect("Morpeko forme");
                let m = self.mon_mut(r);
                m.species = id;
                m.set_types(dex.species(id).types);
            }
            _ => {}
        }
    }

    /// A volatile's onEnd when its duration runs out in the residual phase.
    fn end_volatile(&mut self, t: MonRef, id: VolatileId) {
        self.mon_mut(t).volatiles.remove(id);
        match id {
            VolatileId::Roost => self.mon_mut(t).end_roost(),
            // twoturnmove's onEnd drops the charging move's volatile.
            VolatileId::TwoTurnMove => {
                let m = self.mon_mut(t);
                m.volatiles
                    .0
                    .retain(|v| !matches!(v.id, VolatileId::Charging(_)));
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
        let Some(last) = m.last_move else {
            return HitRes::Bool(false);
        };
        let Some(slot) = m.move_slot(last) else {
            return HitRes::Bool(false);
        };
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
        // changeAction, unless a Mental Herb is about to cure it.
        if queued.is_some_and(|q| q != last)
            && self.item_of(t) != Some("mentalherb")
            && self.change_move_action(t, slot).is_err()
        {
            return HitRes::Bool(false);
        }
        HitRes::Bool(true)
    }

    /// The StallMove event: stall's onStallMove, if the user has it.
    pub(super) fn stall_move(&mut self, user: MonRef) -> bool {
        let Some(v) = self.mon_mut(user).volatiles.get_mut(VolatileId::Stall) else {
            return true;
        };
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
                let early = self.ability_is(user, "earlybird");
                let m = self.mon_mut(user);
                if early {
                    m.status_state.time -= 1;
                }
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
        if self.mon(user).volatiles.has(VolatileId::Flinch) {
            // The Flinch event: Steadfast.
            if self.ability_is(user, "steadfast") {
                self.boost(user, &[(4, 1)], Some(user));
            }
            return false;
        }
        let v = &self.mon(user).volatiles;
        // disable (priority 7)
        if v.0
            .iter()
            .any(|x| x.id == VolatileId::Disable && x.move_id == Some(move_id))
            && !data.flags.has("cantusetwice")
        {
            return false;
        }
        // throatchop (priority 6): no sound moves; healblock: no healing ones.
        if v.has(VolatileId::ThroatChop) && data.flags.has("sound") {
            return false;
        }
        if v.has(VolatileId::HealBlock) && data.flags.has("heal") {
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
        (0..ACTIVE_PER_SIDE)
            .filter_map(|p| self.sides[foe].occupant(p))
            .any(|m| {
                m.hp > 0
                    && !m.fainted
                    && m.volatiles.has(VolatileId::Imprison)
                    && m.move_slot(move_id).is_some()
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
        // Berserk's onDamage: confusion counts as a single-hit move.
        self.mon_mut(r).berserk_checked = false;
        let d = self.on_move_damage(r, damage);
        // Disguise takes it (0); otherwise at least 1.
        let d = if self.mon(r).disguise_busted {
            d
        } else {
            d.max(1)
        };
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
    pub(super) fn boost(
        &mut self,
        t: MonRef,
        boosts: &[(usize, i8)],
        source: Option<MonRef>,
    ) -> HitRes {
        self.boost_by(t, boosts, source, BoostCause::Other)
    }

    /// `battle.boost` with the effect behind it (some abilities only react
    /// to Intimidate, and Mirror Armor doesn't bounce its own reflection).
    pub(super) fn boost_by(
        &mut self,
        t: MonRef,
        boosts: &[(usize, i8)],
        source: Option<MonRef>,
        cause: BoostCause,
    ) -> HitRes {
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
        let ability = self.ability_id(t);
        // ChangeBoost: Contrary, Simple.
        let sign = match ability {
            "contrary" => -1,
            "simple" => 2,
            _ => 1,
        };
        // getCappedBoost
        let mut capped: Vec<(usize, i8)> = boosts
            .iter()
            .filter(|b| b.1 != 0)
            .map(|&(stat, n)| {
                (
                    stat,
                    (m.boosts[stat] + n * sign).clamp(-6, 6) - m.boosts[stat],
                )
            })
            .collect();
        // TryBoost
        let from_other = source.is_some_and(|s| s != t);
        match ability {
            "clearbody" | "whitesmoke" if source != Some(t) => capped.retain(|b| b.1 >= 0),
            _ if source != Some(t)
                && self
                    .mon(t)
                    .has_type(Dex::get().type_id("Grass").expect("Grass"))
                && self.side_has_ability(t.side, "flowerveil") =>
            {
                capped.retain(|b| b.1 >= 0)
            }
            "keeneye" | "illuminate" if source != Some(t) => {
                capped.retain(|b| !(b.0 == 5 && b.1 < 0))
            }
            // Guard Dog (TryBoost priority 2): Intimidate raises Attack instead.
            "guarddog" if cause == BoostCause::Intimidate && capped.iter().any(|b| b.0 == 0) => {
                capped.retain(|b| b.0 != 0);
                self.boost(t, &[(0, 1)], Some(t));
            }
            "hypercutter" if source != Some(t) => capped.retain(|b| !(b.0 == 0 && b.1 < 0)),
            "scrappy" | "innerfocus" | "oblivious" | "owntempo"
                if cause == BoostCause::Intimidate =>
            {
                capped.retain(|b| b.0 != 0);
            }
            "mirrorarmor" if from_other && cause != BoostCause::MirrorArmor => {
                let source = source.expect("from_other");
                let mut kept = Vec::new();
                let mut bounce = Vec::new();
                for b in capped {
                    if b.1 < 0 && self.mon(t).boosts[b.0] != -6 {
                        bounce.push(b);
                    } else {
                        kept.push(b);
                    }
                }
                capped = kept;
                for b in bounce {
                    if self.mon(source).hp > 0 {
                        self.boost_by(source, &[b], Some(t), BoostCause::MirrorArmor);
                    }
                }
            }
            _ => {}
        }
        let intimidated = capped.iter().any(|b| b.0 == 0 && b.1 != 0);
        let (raises, lowers) = (
            capped.iter().any(|b| b.1 > 0),
            capped.iter().any(|b| b.1 < 0),
        );
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
        if success.truthy() {
            let m = self.mon_mut(t);
            m.stats_raised_this_turn |= raises;
            m.stats_lowered_this_turn |= lowers;
        }
        // AfterBoost: Rattled answers Intimidate.
        if cause == BoostCause::Intimidate && intimidated && self.ability_is(t, "rattled") {
            self.boost(t, &[(4, 1)], Some(t));
        }
        success
    }

    // --- HP -------------------------------------------------------------------

    /// A partial trap's source, by its (side << 8 | uid) code.
    pub(super) fn trap_source(&self, code: u32) -> Option<MonRef> {
        let side = (code >> 8) as usize;
        let uid = (code & 0xFF) as usize;
        self.sides[side]
            .pokemon
            .iter()
            .any(|m| m.uid == uid)
            .then_some(MonRef { side, uid })
    }

    /// leechseed's onResidual: the seeded Pokemon loses an eighth, and
    /// whoever stands in the seeder's slot gets it (Big Root; Liquid Ooze
    /// turns it into damage).
    fn leech_seed(&mut self, r: MonRef) {
        let Some(v) = self
            .mon(r)
            .volatiles
            .0
            .iter()
            .find(|v| v.id == VolatileId::LeechSeed)
        else {
            return;
        };
        let slot = v.target_loc as usize;
        let Some(seeder) = self.occupant(slot / 2, slot % 2) else {
            return;
        };
        if self.mon(seeder).fainted || self.mon(seeder).hp == 0 {
            return;
        }
        let amount = (self.mon(r).max_hp() / 8) as u32;
        let HitRes::Num(dealt) = self.effect_damage(r, amount) else {
            return;
        };
        if dealt == 0 {
            return;
        }
        let mut heal = dealt;
        if self.item_of(seeder) == Some("bigroot") {
            heal = crate::fixed::modify(heal as u64, 5324) as u32;
        }
        if self.ability_is(r, "liquidooze") {
            self.effect_damage(seeder, heal);
        } else {
            self.heal(seeder, heal);
        }
    }

    /// An active Pokemon with HP on `side` has `ability` (Ally events reach
    /// the holder itself too).
    pub(super) fn side_has_ability(&self, side: usize, ability: &str) -> bool {
        (0..ACTIVE_PER_SIDE).any(|p| {
            self.occupant(side, p)
                .is_some_and(|a| self.mon(a).hp > 0 && self.ability_is(a, ability))
        })
    }

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
            self.faint_queue.push((r, None));
        }
    }

    /// `battle.heal`.
    pub(super) fn heal(&mut self, t: MonRef, amount: u32) -> HitRes {
        let amount = if amount != 0 && amount <= 1 {
            1
        } else {
            amount
        };
        if amount == 0 {
            return HitRes::Num(0);
        }
        let m = self.mon_mut(t);
        if m.hp == 0 || !m.is_active || m.hp >= m.max_hp() {
            return HitRes::Bool(false);
        }
        // TryHeal: Heal Block.
        if m.volatiles.has(VolatileId::HealBlock) {
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
        // Magic Guard: only moves do damage.
        if self.ability_is(t, "magicguard") {
            return HitRes::Bool(false);
        }
        // Berserk's onDamage: not a move, so berries may go.
        self.mon_mut(t).berserk_checked = true;
        if !self.mon(t).is_active {
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
        // Revival Blessing's slot condition lasts the turn.
        for side in &mut self.sides {
            side.revival_blessing = [false; ACTIVE_PER_SIDE];
        }
        let mut handlers = Vec::new();
        // Field, then each side's conditions followed by its actives.
        if self.field.trick_room > 0 {
            handlers.push(Residual {
                mon: None,
                what: ResidualKind::TrickRoom,
                order: 27,
                speed: 0,
                sub_order: 1,
            });
        }
        if self.field.weather != crate::damage::Weather::None {
            handlers.push(Residual {
                mon: None,
                what: ResidualKind::Weather,
                order: 1,
                speed: 0,
                sub_order: 5,
            });
        }
        if self.field.terrain != crate::damage::Terrain::None {
            handlers.push(Residual {
                mon: None,
                what: ResidualKind::Terrain,
                order: 27,
                speed: 0,
                sub_order: 7,
            });
        }
        for side in 0..2 {
            for c in SideCondition::ALL {
                if self.sides[side].condition(c) > 0 && !c.is_hazard() {
                    let (order, sub_order) = c.residual_order();
                    handlers.push(Residual {
                        mon: None,
                        what: ResidualKind::Side(side, c),
                        order,
                        speed: 0,
                        sub_order,
                    });
                }
            }
            for pos in 0..ACTIVE_PER_SIDE {
                let Some(r) = self.occupant(side, pos) else {
                    continue;
                };
                let m = self.mon(r);
                let order = match m.status {
                    Status::Burn => Some(10),
                    Status::Poison | Status::Toxic => Some(9),
                    _ => None,
                };
                if let Some(order) = order {
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::Status(m.status),
                        order,
                        speed: m.speed,
                        sub_order: 0,
                    });
                }
                if self.ability_is(r, "speedboost") {
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::SpeedBoost,
                        order: 28,
                        speed: m.speed,
                        sub_order: 2,
                    });
                }
                let ability = match self.ability_id(r) {
                    "harvest" | "moody" => Some((28, 2)),
                    "hungerswitch" => Some((29, 7)),
                    _ => None,
                };
                if let Some((order, sub_order)) = ability {
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::Ability,
                        order,
                        speed: m.speed,
                        sub_order,
                    });
                }
                if self.item_of(r) == Some("whiteherb") {
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::WhiteHerb,
                        order: 29,
                        speed: m.speed,
                        sub_order: 8,
                    });
                }
                if matches!(self.ability_id(r), "healer" | "hydration" | "shedskin") {
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::Healer,
                        order: 5,
                        speed: m.speed,
                        sub_order: 3,
                    });
                }
                if self.item_of(r) == Some("leftovers") {
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::Leftovers,
                        order: 5,
                        speed: m.speed,
                        sub_order: 4,
                    });
                }
                for v in &m.volatiles.0 {
                    if v.duration.is_some()
                        || matches!(v.id, VolatileId::LeechSeed | VolatileId::SaltCure)
                    {
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
                    handlers.push(Residual {
                        mon: Some(r),
                        what: ResidualKind::GrassyHeal,
                        order: 5,
                        speed: m.speed,
                        sub_order: 2,
                    });
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
                ResidualKind::TrickRoom
                | ResidualKind::Side(..)
                | ResidualKind::Weather
                | ResidualKind::Terrain => unreachable!(),
                ResidualKind::WhiteHerb => self.white_herb(h.mon),
                ResidualKind::SpeedBoost => {
                    if self.mon(h.mon).active_turns > 0 && self.ability_is(h.mon, "speedboost") {
                        self.boost(h.mon, &[(4, 1)], Some(h.mon));
                    }
                }
                ResidualKind::GrassyHeal => {
                    if self.field.terrain != crate::damage::Terrain::Grassy {
                        continue;
                    }
                    self.grassy_heal(h.mon);
                }
                // Healer, Hydration, Shed Skin (order 5, subOrder 3).
                ResidualKind::Healer => match self.ability_id(h.mon) {
                    "healer" => {
                        for a in self.adjacent_allies(h.mon) {
                            if self.mon(a).status != Status::None && self.chance.chance(1, 2) {
                                self.cure_status(a);
                            }
                        }
                    }
                    "hydration"
                        if self.mon(h.mon).status != Status::None
                            && self.effective_weather() == crate::damage::Weather::Rain =>
                    {
                        self.cure_status(h.mon);
                    }
                    "shedskin"
                        if self.mon(h.mon).hp > 0
                            && self.mon(h.mon).status != Status::None
                            && self.chance.chance(33, 100) =>
                    {
                        self.cure_status(h.mon);
                    }
                    _ => {}
                },
                ResidualKind::Leftovers => {
                    if self.item_of(h.mon) != Some("leftovers") {
                        continue;
                    }
                    self.leftovers(h.mon);
                }
                ResidualKind::Volatile(VolatileId::LeechSeed) => self.leech_seed(h.mon),
                ResidualKind::Volatile(VolatileId::SaltCure) => {
                    let dex = Dex::get();
                    let m = self.mon(h.mon);
                    let hard = m.has_type(dex.type_id("Water").expect("Water"))
                        || m.has_type(dex.type_id("Steel").expect("Steel"));
                    let amount = (m.max_hp() / if hard { 8 } else { 16 }) as u32;
                    self.effect_damage(h.mon, amount);
                }
                ResidualKind::Volatile(id) => {
                    let m = self.mon_mut(h.mon);
                    let Some(v) = m.volatiles.get_mut(id) else {
                        continue;
                    };
                    let Some(d) = v.duration.as_mut() else {
                        continue;
                    };
                    *d -= 1;
                    if *d == 0 {
                        self.end_volatile(h.mon, id);
                        continue;
                    }
                    // partiallytrapped's onResidual: ends with its source gone,
                    // else an eighth (a sixth with Binding Band).
                    if id == VolatileId::PartiallyTrapped {
                        let (source, divisor) = (v.counter, v.target_loc as u16);
                        let gone = self.trap_source(source).is_none_or(|s| {
                            let s = self.mon(s);
                            !s.is_active || s.hp == 0 || s.active_turns == 0
                        });
                        if gone {
                            self.mon_mut(h.mon).volatiles.remove(id);
                        } else {
                            let amount = (self.mon(h.mon).max_hp() / divisor) as u32;
                            self.effect_damage(h.mon, amount);
                        }
                    }
                    // encore's onResidual: ends once the move is out of PP.
                    if id == VolatileId::Encore {
                        let m = self.mon_mut(h.mon);
                        let mv = m
                            .volatiles
                            .0
                            .iter()
                            .find(|v| v.id == id)
                            .and_then(|v| v.move_id);
                        let has_pp = mv
                            .and_then(|mv| m.moves.iter().find(|s| s.id == mv))
                            .is_some_and(|s| s.pp > 0);
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
                        // Heatproof's onDamage halves burn damage.
                        Status::Burn if self.ability_is(h.mon, "heatproof") => {
                            (max / 16).max(1) / 2
                        }
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
                    // Poison Heal's onDamage (priority 1): poison heals instead.
                    if matches!(status, Status::Poison | Status::Toxic) && self.ability_is(h.mon, "poisonheal") {
                        let heal = (self.mon(h.mon).max_hp() / 8) as u32;
                        self.heal(h.mon, heal);
                        continue;
                    }
                    self.effect_damage(h.mon, amount);
                }
                ResidualKind::Ability => self.ability_residual(h.mon),
            }
            self.faint_messages()?;
            if self.is_over() {
                return Ok(());
            }
        }
        Ok(())
    }
}
