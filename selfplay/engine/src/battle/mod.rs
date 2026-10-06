//! The doubles battle loop, mirroring Showdown's sim/battle.ts and
//! sim/battle-queue.ts: choices become queued actions, the queue runs one
//! action at a time, and a mid-turn decision (a faint, U-turn) stops the loop
//! with a pending request; `choose` resumes it. The remaining queue lives in
//! the battle state, so a battle can be copied at any decision point.
//!
//! Mechanics are added in Showdown's terms. Anything a team brings that isn't
//! implemented yet is refused when the battle is created (see `support`), so
//! a battle never silently ignores an effect.

pub mod choice;
mod conditions;
mod field;
mod items;
mod moves;
pub mod snapshot;
pub mod state;
pub mod support;

use crate::chance::Chance;
use crate::dex::Dex;
use crate::stats;
use crate::team::PokemonSet;
use choice::{SideChoice, SideRequest, SlotChoice, SlotRequest};
use state::{Attacker, Field, Mon, Side, SwitchFlag, ACTIVE_PER_SIDE};
use std::cmp::Ordering;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BattleError {
    /// The choice isn't legal for the current request.
    Illegal(String),
    /// The battle reached an effect the engine doesn't implement.
    Unsupported(String),
}

pub type Res<T> = Result<T, BattleError>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Outcome {
    Win(usize),
    Tie,
}

/// A Pokemon by side and its original team index (stable across switches,
/// unlike its list position).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MonRef {
    pub side: usize,
    pub uid: usize,
}

#[derive(Debug, Clone, PartialEq)]
enum ActionKind {
    Team {
        side: usize,
        index: usize,
        uid: usize,
    },
    Start,
    BeforeTurn,
    Move {
        mon: MonRef,
        slot: usize,
        target_loc: i8,
    },
    MegaEvo {
        mon: MonRef,
    },
    /// Revival Blessing's choice: `target` (fainted) comes back.
    Revive {
        mon: MonRef,
        target: MonRef,
    },
    /// A move's priorityChargeCallback (Chilly Reception's message).
    PriorityCharge {
        mon: MonRef,
    },
    Switch {
        mon: MonRef,
        target: MonRef,
    },
    RunSwitch {
        mon: MonRef,
    },
    Residual,
}

#[derive(Debug, Clone, PartialEq)]
struct Action {
    kind: ActionKind,
    order: u32,
    priority: f64,
    speed: i32,
    /// FractionalPriority, rolled once as the action is resolved.
    fractional: f64,
}

/// Showdown's default order when an action has none.
const NO_ORDER: u32 = u32::MAX;

#[derive(Debug, Clone)]
pub struct Battle {
    pub sides: [Side; 2],
    pub field: Field,
    pub turn: u32,
    pub chance: Chance,
    pub requests: [SideRequest; 2],
    pub outcome: Option<Outcome>,
    /// Training's turn cap: a game still going after this many turns is a
    /// draw. None (Showdown) plays on.
    pub turn_limit: Option<u32>,
    /// The six sets each side brought to team preview, by uid.
    pub teams: [Vec<PokemonSet>; 2],
    queue: Vec<Action>,
    mid_turn: bool,
    /// Queued faints, with the user of the move that caused each (if any).
    faint_queue: Vec<(MonRef, Option<MonRef>)>,
    /// Set while move damage is dealt: the move's user, for the faint queue.
    move_damage_by: Option<MonRef>,
    /// The user of the move being run. It stays in the damage calculation
    /// at 0 HP, as in Showdown: Explosion's user faints before it hits, and
    /// a spread contact move into Spiky Shield can KO its user before the
    /// other target is hit.
    attacking_user: Option<MonRef>,
    /// The user of a move with ignoreAbility (Mold Breaker), while it runs:
    /// other Pokemon's breakable abilities are suppressed.
    mold_breaker: Option<MonRef>,
    /// Shell Side Arm went physical this use (and makes contact).
    ssa_physical: bool,
    mega_used: [bool; 2],
    /// The six as brought, while team-preview actions pick the four.
    benched: [Vec<Mon>; 2],
    /// Effects created so far (Showdown's effectOrder counter).
    effect_order: u64,
}

impl Battle {
    /// A battle at team preview. Each team is the six as brought.
    pub fn new(teams: [Vec<PokemonSet>; 2], chance: Chance) -> Res<Battle> {
        for team in &teams {
            for set in team {
                support::check_set(set).map_err(BattleError::Unsupported)?;
            }
        }
        let sets = teams.clone();
        let sides = teams.map(|team| {
            let pokemon: Vec<Mon> = team
                .iter()
                .enumerate()
                .map(|(i, set)| {
                    let mut m = Mon::new(set);
                    m.position = i;
                    m.uid = i;
                    m
                })
                .collect();
            Side {
                pokemon_left: pokemon.len(),
                pokemon,
                ..Side::default()
            }
        });
        let mut b = Battle {
            sides,
            field: Field::default(),
            turn: 0,
            chance,
            requests: [SideRequest::TeamPreview, SideRequest::TeamPreview],
            outcome: None,
            queue: Vec::new(),
            mid_turn: true,
            faint_queue: Vec::new(),
            move_damage_by: None,
            attacking_user: None,
            mold_breaker: None,
            ssa_physical: false,
            mega_used: [false; 2],
            turn_limit: None,
            teams: sets,
            effect_order: 0,
            benched: [Vec::new(), Vec::new()],
        };
        b.push_action(ActionKind::Start);
        Ok(b)
    }

    /// This side has Mega Evolved this battle.
    pub fn mega_used(&self, side: usize) -> bool {
        self.mega_used[side]
    }

    pub fn is_over(&self) -> bool {
        self.outcome.is_some()
    }

    pub fn mon(&self, r: MonRef) -> &Mon {
        self.sides[r.side]
            .pokemon
            .iter()
            .find(|m| m.uid == r.uid)
            .expect("MonRef to a Pokemon on its side")
    }

    fn mon_mut(&mut self, r: MonRef) -> &mut Mon {
        self.sides[r.side]
            .pokemon
            .iter_mut()
            .find(|m| m.uid == r.uid)
            .expect("MonRef to a Pokemon on its side")
    }

    fn mon_ref(&self, side: usize, position: usize) -> MonRef {
        MonRef {
            side,
            uid: self.sides[side].pokemon[position].uid,
        }
    }

    /// The Pokemon in a slot (`side.active[pos]`), fainted or not.
    pub fn occupant(&self, side: usize, pos: usize) -> Option<MonRef> {
        self.sides[side]
            .occupant(pos)
            .map(|m| MonRef { side, uid: m.uid })
    }

    /// Showdown's `getAllActive()`: slot occupants that haven't fainted.
    fn all_active(&self) -> MonList {
        let mut out = MonList::default();
        for side in 0..2 {
            for pos in 0..ACTIVE_PER_SIDE {
                if let Some(r) = self.occupant(side, pos) {
                    if !self.mon(r).fainted {
                        out.push(r);
                    }
                }
            }
        }
        out
    }

    // --- Choices ------------------------------------------------------------

    /// Submit both sides' decisions for the current requests. A side whose
    /// request is `Wait` must pass `None`.
    pub fn choose(&mut self, choices: [Option<SideChoice>; 2]) -> Res<()> {
        if self.is_over() {
            return Err(BattleError::Illegal("the battle is over".into()));
        }
        for side in 0..2 {
            let needed = !matches!(self.requests[side], SideRequest::Wait);
            match (&choices[side], needed) {
                (None, true) => {
                    return Err(BattleError::Illegal(format!("side {side} must choose")))
                }
                (Some(_), false) => {
                    return Err(BattleError::Illegal(format!(
                        "side {side} has nothing to choose"
                    )))
                }
                (Some(c), true) if !self.legal_choices(side).contains(c) => {
                    return Err(BattleError::Illegal(format!(
                        "side {side}: {c:?} isn't legal"
                    )));
                }
                _ => {}
            }
        }
        self.commit_choices(choices)
    }

    /// Showdown's commitChoices: queue the new actions, sorted, ahead of
    /// whatever was left of the turn, then run.
    fn commit_choices(&mut self, choices: [Option<SideChoice>; 2]) -> Res<()> {
        self.update_speed()?;
        let old = std::mem::take(&mut self.queue);
        for (side, choice) in choices.into_iter().enumerate() {
            match choice {
                Some(SideChoice::Team(order)) => {
                    for (index, &i) in order.iter().enumerate() {
                        let uid = self.sides[side].pokemon[i as usize].uid;
                        self.add_action(ActionKind::Team { side, index, uid }, 1, -(index as f64))?;
                    }
                }
                Some(SideChoice::Slots(slots)) => {
                    for (pos, slot) in slots.into_iter().enumerate() {
                        self.add_slot_choice(side, pos, slot)?;
                    }
                }
                None => {}
            }
        }
        let mut q = std::mem::take(&mut self.queue);
        self.sort_actions(&mut q);
        q.extend(old);
        self.queue = q;
        self.requests = [SideRequest::Wait, SideRequest::Wait];
        self.turn_loop()
    }

    fn add_slot_choice(&mut self, side: usize, pos: usize, choice: SlotChoice) -> Res<()> {
        let mon = self.mon_ref(side, pos);
        match choice {
            SlotChoice::Pass => {}
            SlotChoice::Switch { index }
                if matches!(self.requests[side], SideRequest::Switch(_))
                    && self.sides[side].revival_blessing[pos] =>
            {
                // chooseSwitch: the reviver stays in.
                self.sides[side].pokemon[pos].switch_flag = None;
                let target = self.mon_ref(side, index as usize);
                self.add_action(ActionKind::Revive { mon, target }, 6, 0.0)?;
            }
            SlotChoice::Switch { index } => {
                // resolveAction clears the switch flag once the switch is queued
                // (keeping Baton Pass as the switch's sourceEffect).
                let flag = self.sides[side].pokemon[pos].switch_flag.take();
                if let Some(state::SwitchFlag::Move(m)) = flag {
                    let id = &Dex::get().move_data(m).id;
                    self.sides[side].pokemon[pos].baton_passing = id == "batonpass";
                    self.sides[side].pokemon[pos].shed_tailing = id == "shedtail";
                }
                let target = self.mon_ref(side, index as usize);
                let order = if matches!(self.requests[side], SideRequest::Switch(_)) {
                    3
                } else {
                    103
                };
                self.add_action(ActionKind::Switch { mon, target }, order, 0.0)?;
            }
            SlotChoice::Move { slot, target, mega } => {
                if mega {
                    self.add_action(ActionKind::MegaEvo { mon }, 104, 0.0)?;
                }
                let m = self.mon(mon);
                // A locked move aims where it was aimed before.
                if m.locked_move().is_some() {
                    let charged = m
                        .volatiles
                        .0
                        .iter()
                        .find(|v| matches!(v.id, state::VolatileId::Charging(_)))
                        .map(|v| v.target_loc);
                    let target_loc = charged
                        .filter(|&l| l != 0)
                        .unwrap_or(m.last_move_target_loc);
                    self.add_action(
                        ActionKind::Move {
                            mon,
                            slot: moves::LOCKED_SLOT,
                            target_loc,
                        },
                        200,
                        0.0,
                    )?;
                    return Ok(());
                }
                let slot = if m
                    .moves
                    .iter()
                    .any(|s| s.pp > 0 && !s.disabled && !s.imprisoned)
                {
                    slot as usize
                } else {
                    usize::MAX
                };
                self.add_action(
                    ActionKind::Move {
                        mon,
                        slot,
                        target_loc: target,
                    },
                    200,
                    0.0,
                )?;
                let id = moves::move_for_slot(self.mon(mon), slot);
                if Dex::get()
                    .move_data(id)
                    .handlers
                    .has("priorityChargeCallback")
                {
                    self.add_action(ActionKind::PriorityCharge { mon }, 107, 0.0)?;
                }
            }
        }
        Ok(())
    }

    /// `queue.cancelMove`: drop the Pokemon's queued move.
    pub(super) fn cancel_move(&mut self, r: MonRef) {
        self.queue
            .retain(|a| !matches!(a.kind, ActionKind::Move { mon, .. } if mon == r));
    }

    /// Round's onTry: every queued Round goes to the front (prioritizeAction,
    /// order 3), each later one ahead of the earlier, with double power.
    pub(super) fn prioritize_rounds(&mut self) {
        let mut i = 0;
        while i < self.queue.len() {
            if let ActionKind::Move { mon, slot, .. } = self.queue[i].kind {
                let id = moves::move_for_slot(self.mon(mon), slot);
                if Dex::get().move_data(id).id == "round" {
                    let mut a = self.queue.remove(i);
                    a.order = 3;
                    self.queue.insert(0, a);
                    self.mon_mut(mon).round_boost = true;
                }
            }
            i += 1;
        }
    }

    /// `queue.prioritizeAction(resolveAction(move))`: the move goes first
    /// (order 3) (Instruct).
    pub(super) fn prioritize_move(&mut self, mon: MonRef, slot: usize, target_loc: i8) -> Res<()> {
        self.add_action(
            ActionKind::Move {
                mon,
                slot,
                target_loc,
            },
            3,
            0.0,
        )?;
        let action = self.queue.pop().expect("just added");
        self.queue.insert(0, action);
        Ok(())
    }

    /// Showdown's resolveAction + getActionSpeed, appended to the queue.
    fn add_action(&mut self, kind: ActionKind, order: u32, priority: f64) -> Res<()> {
        let fractional = self.fractional_priority(&kind);
        self.set_original_target(&kind);
        let mut action = Action {
            kind,
            order,
            priority,
            speed: 1,
            fractional,
        };
        self.action_speed(&mut action)?;
        self.queue.push(action);
        Ok(())
    }

    fn push_action(&mut self, kind: ActionKind) {
        let order = match kind {
            ActionKind::Start => 2,
            ActionKind::BeforeTurn => 4,
            ActionKind::Residual => 300,
            _ => NO_ORDER,
        };
        self.queue.push(Action {
            kind,
            order,
            priority: 0.0,
            speed: 1,
            fractional: 0.0,
        });
    }

    /// resolveAction's `originalTarget = pokemon.getAtLoc(targetLoc)`.
    fn set_original_target(&mut self, kind: &ActionKind) {
        if let ActionKind::Move {
            mon, target_loc, ..
        } = *kind
        {
            let t = if target_loc == 0 {
                None
            } else {
                self.at_loc(mon, target_loc)
            };
            let uid = t.map(|t| (t.side, self.mon(t).uid));
            self.mon_mut(mon).original_target = uid;
        }
    }

    /// The FractionalPriority event for a move action: Quick Claw.
    fn fractional_priority(&mut self, kind: &ActionKind) -> f64 {
        let ActionKind::Move { mon, slot, .. } = *kind else {
            return 0.0;
        };
        // Quick Draw (priority -1); a success leaves Quick Claw (-2) nothing
        // to do, without a roll.
        let id = moves::move_for_slot(self.mon(mon), slot);
        let attack = Dex::get().move_data(id).category != crate::dex::Category::Status;
        // Stall (priority 0) moves last among equals.
        let base = if self.ability_is(mon, "stall") {
            -0.1
        } else {
            0.0
        };
        if self.ability_is(mon, "quickdraw") && attack && self.chance.chance(3, 10) {
            return 0.1;
        }
        if self.item_of(mon) == Some("quickclaw") && self.chance.chance(1, 5) {
            return 0.1;
        }
        base
    }

    /// `getActionSpeed`: a move's priority, and the acting Pokemon's speed.
    fn action_speed(&mut self, action: &mut Action) -> Res<()> {
        let mon = match &action.kind {
            ActionKind::Move { mon, slot, .. } => {
                let m = self.mon(*mon);
                let id = moves::move_for_slot(m, *slot);
                action.priority = self.move_priority(*mon, id) as f64 + action.fractional;
                Some(*mon)
            }
            ActionKind::MegaEvo { mon }
            | ActionKind::PriorityCharge { mon }
            | ActionKind::Revive { mon, .. }
            | ActionKind::Switch { mon, .. }
            | ActionKind::RunSwitch { mon } => Some(*mon),
            _ => None,
        };
        action.speed = match mon {
            Some(r) => self.action_speed_of(r)?,
            None => 1,
        };
        Ok(())
    }

    /// `pokemon.getActionSpeed()`: modified Speed (Trick Room isn't in yet).
    fn action_speed_of(&self, r: MonRef) -> Res<i32> {
        let m = self.mon(r);
        // An inactive (fainted) Pokemon's ModifySpe event finds no handlers.
        if !m.is_active {
            let spe = boosted(m.stats[5], m.boosts[4]).min(10_000) as i32;
            return Ok(if self.field.trick_room > 0 { -spe } else { spe });
        }
        // ModifySpe: Choice Scarf and Tailwind chain their modifiers.
        let mut modifier = self.speed_modifier(r);
        if m.volatiles.has(state::VolatileId::Unburden)
            && m.item.is_none()
            && self.ability_is(r, "unburden")
        {
            modifier = crate::fixed::chain(modifier, 8192);
        }
        // Weather Speed abilities.
        let weather = self.effective_weather();
        let doubled = match Dex::get().ability(m.ability).id.as_str() {
            "swiftswim" => weather == crate::damage::Weather::Rain,
            "chlorophyll" => weather == crate::damage::Weather::Sun,
            "sandrush" => weather == crate::damage::Weather::Sand,
            "slushrush" => weather == crate::damage::Weather::Snow,
            "surgesurfer" => self.field.terrain == crate::damage::Terrain::Electric,
            "quickfeet" if m.status != crate::damage::Status::None => {
                modifier = crate::fixed::chain(modifier, 6144);
                false
            }
            _ => false,
        };
        if doubled {
            modifier = crate::fixed::chain(modifier, 8192);
        }
        if self.sides[r.side].condition(state::SideCondition::Tailwind) > 0 {
            modifier = crate::fixed::chain(modifier, 8192);
        }
        let mut spe =
            crate::fixed::modify(boosted(m.stats[5], m.boosts[4]) as u64, modifier) as u32;
        // par's onModifySpe (Quick Feet isn't supported).
        // par's onModifySpe, unless Quick Feet.
        if m.status == crate::damage::Status::Paralysis && !self.ability_is(r, "quickfeet") {
            spe = spe * 50 / 100;
        }
        let spe = spe.min(10_000) as i32;
        // The champions mod's getActionSpeed: Trick Room negates (no 13-bit
        // truncation).
        Ok(if self.field.trick_room > 0 { -spe } else { spe })
    }

    /// The ModifyPriority events for a move: Grassy Glide.
    pub(crate) fn move_priority(&self, user: MonRef, move_id: crate::dex::MoveId) -> i8 {
        let data = Dex::get().move_data(move_id);
        let mut p = data.priority;
        if data.id == "grassyglide"
            && self.field.terrain == crate::damage::Terrain::Grassy
            && self.grounded(user)
        {
            p += 1;
        }
        if data.category == crate::dex::Category::Status && self.ability_is(user, "prankster") {
            p += 1;
        }
        // Gale Wings: Flying moves at full HP.
        let m = self.mon(user);
        if data.move_type == Dex::get().type_id("Flying").expect("Flying")
            && m.hp == m.max_hp()
            && self.ability_is(user, "galewings")
        {
            p += 1;
        }
        p
    }

    /// `field.effectiveWeather()`: none while a Cloud Nine or Air Lock
    /// Pokemon is out (its suppressWeather flag; Mold Breaker can't touch it).
    pub(crate) fn effective_weather(&self) -> crate::damage::Weather {
        let dex = Dex::get();
        let suppressed = (0..2).any(|side| {
            (0..ACTIVE_PER_SIDE).any(|pos| {
                self.sides[side].slot_filled[pos] && {
                    let m = &self.sides[side].pokemon[pos];
                    !m.fainted && dex.ability(m.ability).suppress_weather
                }
            })
        });
        if suppressed {
            crate::damage::Weather::None
        } else {
            self.field.weather
        }
    }

    pub(crate) fn ability_is(&self, r: MonRef, id: &str) -> bool {
        self.ability_id(r) == id
    }

    /// The ability's id as events see it: "" while a Mold Breaker move
    /// suppresses it (`suppressingAbility`).
    pub(crate) fn ability_id(&self, r: MonRef) -> &'static str {
        let m = self.mon(r);
        let ab = Dex::get().ability(m.ability);
        if ab.breakable && self.mold_breaker.is_some_and(|u| u != r) {
            return "";
        }
        // ignoringAbility: a notransform ability does nothing once its
        // holder has transformed.
        if m.transformed && ab.flags.iter().any(|f| f == "notransform") {
            return "";
        }
        ab.id.as_str()
    }

    /// The move a Pokemon's queued Move action will use (`queue.willMove`).
    fn queued_move(&self, r: MonRef) -> Option<crate::dex::MoveId> {
        if self.mon(r).fainted {
            return None;
        }
        self.queue.iter().find_map(|a| match a.kind {
            ActionKind::Move { mon, slot, .. } if mon == r => {
                Some(moves::move_for_slot(self.mon(r), slot))
            }
            _ => None,
        })
    }

    /// `queue.changeAction`: replace a Pokemon's queued actions with a move
    /// (Encore), aimed at a random target and inserted by priority.
    fn change_move_action(&mut self, r: MonRef, slot: usize) -> Res<()> {
        self.queue.retain(|a| !action_belongs_to(a, r));
        let move_id = moves::move_for_slot(self.mon(r), slot);
        let target_loc = self.random_target_loc(r, move_id);
        self.insert_action(
            ActionKind::Move {
                mon: r,
                slot,
                target_loc,
            },
            200,
        )
    }

    /// `queue.willMove(pokemon)`.
    /// `swapPosition` with the ally (Ally Switch): false if there's no
    /// (unfainted) ally to trade places with.
    pub(super) fn swap_position(&mut self, r: MonRef) -> bool {
        let side = r.side;
        let pos = self.mon(r).position;
        let new_pos = 1 - pos;
        match self.occupant(side, new_pos) {
            Some(o) if !self.mon(o).fainted => {}
            _ => return false,
        }
        self.sides[side].pokemon.swap(pos, new_pos);
        self.sides[side].pokemon[pos].position = pos;
        self.sides[side].pokemon[new_pos].position = new_pos;
        true
    }

    /// The DragOut event: Guard Dog and Suction Cups refuse.
    pub(super) fn resists_drag(&self, r: MonRef) -> bool {
        self.ability_is(r, "guarddog") || self.ability_is(r, "suctioncups")
    }

    /// The active move's contact flag (Shell Side Arm may add it).
    pub(super) fn contact(&self, data: &crate::dex::MoveData) -> bool {
        data.flags.has("contact") || (data.id == "shellsidearm" && self.ssa_physical)
    }

    /// The active move's category is Physical (Shell Side Arm may switch).
    pub(super) fn physical(&self, data: &crate::dex::MoveData) -> bool {
        if data.id == "shellsidearm" {
            self.ssa_physical
        } else {
            data.category == crate::dex::Category::Physical
        }
    }

    fn will_move(&self, r: MonRef) -> bool {
        !self.mon(r).fainted
            && self
                .queue
                .iter()
                .any(|a| matches!(a.kind, ActionKind::Move { mon, .. } if mon == r))
    }

    /// The integer priority (`action.move.priority`) and move of a queued
    /// Move action.
    fn queued_move_priority(&self, r: MonRef) -> Option<(crate::dex::MoveId, f64)> {
        if self.mon(r).fainted {
            return None;
        }
        self.queue.iter().find_map(|a| match a.kind {
            ActionKind::Move { mon, slot, .. } if mon == r => Some((
                moves::move_for_slot(self.mon(r), slot),
                a.priority - a.fractional,
            )),
            _ => None,
        })
    }

    /// Quash: the target's move goes last (order 201).
    fn quash(&mut self, r: MonRef) -> bool {
        if self.mon(r).fainted {
            return false;
        }
        match self
            .queue
            .iter_mut()
            .find(|a| matches!(a.kind, ActionKind::Move { mon, .. } if mon == r))
        {
            Some(a) => {
                a.order = 201;
                true
            }
            None => false,
        }
    }

    /// After You: `prioritizeAction` puts the target's move next (order 3).
    fn after_you(&mut self, r: MonRef) -> bool {
        if self.mon(r).fainted {
            return false;
        }
        let Some(i) = self
            .queue
            .iter()
            .position(|a| matches!(a.kind, ActionKind::Move { mon, .. } if mon == r))
        else {
            return false;
        };
        let mut a = self.queue.remove(i);
        a.order = 3;
        self.queue.insert(0, a);
        true
    }

    /// `queue.willAct()`: a move or switch is still to come this turn.
    fn will_act(&self) -> bool {
        self.queue
            .iter()
            .any(|a| matches!(a.kind, ActionKind::Move { .. } | ActionKind::Switch { .. }))
    }

    /// `battle.updateSpeed()`: refresh every active Pokemon's `speed`.
    fn update_speed(&mut self) -> Res<()> {
        for r in self.all_active() {
            let s = self.action_speed_of(r)?;
            self.mon_mut(r).speed = s;
        }
        Ok(())
    }

    /// Showdown's speedSort: selection sort that shuffles exact ties. When
    /// a turn's chance outcomes are enumerated, ties between event handlers
    /// keep the order they were collected in (otherwise every sort of tied
    /// handlers doubles the outcomes); ties in the action queue
    /// (`sort_actions`) are still enumerated.
    fn speed_sort<T>(&mut self, list: &mut [T], cmp: impl Fn(&T, &T) -> Ordering) {
        self.speed_sort_ties(list, cmp, false);
    }

    /// The action queue's speedSort: a speed tie is a real chance outcome.
    fn sort_actions(&mut self, list: &mut [Action]) {
        self.speed_sort_ties(list, compare_priority, true);
    }

    fn speed_sort_ties<T>(
        &mut self,
        list: &mut [T],
        cmp: impl Fn(&T, &T) -> Ordering,
        enumerate_ties: bool,
    ) {
        let mut sorted = 0;
        while sorted + 1 < list.len() {
            let mut next = vec![sorted];
            for i in sorted + 1..list.len() {
                match cmp(&list[next[0]], &list[i]) {
                    Ordering::Less => {}
                    Ordering::Greater => next = vec![i],
                    Ordering::Equal => next.push(i),
                }
            }
            for (k, &index) in next.iter().enumerate() {
                if index != sorted + k {
                    list.swap(sorted + k, index);
                }
            }
            if next.len() > 1 {
                let tied = &mut list[sorted..sorted + next.len()];
                if enumerate_ties {
                    self.chance.shuffle(tied);
                } else {
                    self.chance.shuffle_minor(tied);
                }
            }
            sorted += next.len();
        }
    }

    /// `queue.insertChoice` for an action created mid-turn (runSwitch):
    /// placed where it would sort, ties broken at random.
    fn insert_action(&mut self, kind: ActionKind, order: u32) -> Res<()> {
        let fractional = self.fractional_priority(&kind);
        self.set_original_target(&kind);
        let mut action = Action {
            kind,
            order,
            priority: 0.0,
            speed: 1,
            fractional,
        };
        if let ActionKind::RunSwitch { mon } | ActionKind::Move { mon, .. } = action.kind {
            let s = self.action_speed_of(mon)?;
            self.mon_mut(mon).speed = s;
        }
        self.action_speed(&mut action)?;
        let mut first = None;
        let mut last = None;
        for (i, cur) in self.queue.iter().enumerate() {
            let c = compare_priority(&action, cur);
            if c != Ordering::Greater && first.is_none() {
                first = Some(i);
            }
            if c == Ordering::Less {
                last = Some(i);
                break;
            }
        }
        match first {
            None => self.queue.push(action),
            Some(f) => {
                let l = last.unwrap_or(self.queue.len());
                let index = if f == l {
                    f
                } else {
                    self.chance.random_range(f as u32, l as u32 + 1) as usize
                };
                self.queue.insert(index, action);
            }
        }
        Ok(())
    }

    // --- The turn loop -------------------------------------------------------

    fn turn_loop(&mut self) -> Res<()> {
        if !self.mid_turn {
            self.insert_action(ActionKind::BeforeTurn, 4)?;
            self.push_action(ActionKind::Residual);
            self.mid_turn = true;
        }
        while !self.queue.is_empty() {
            let action = self.queue.remove(0);
            let stop = self.run_action(action)?;
            if stop || self.is_over() {
                return Ok(());
            }
        }
        self.end_turn()?;
        self.mid_turn = false;
        self.queue.clear();
        Ok(())
    }

    /// Returns true when the loop must stop for a request.
    fn run_action(&mut self, action: Action) -> Res<bool> {
        static TRACE: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
        if *TRACE.get_or_init(|| std::env::var_os("SELFPLAY_TRACE").is_some()) {
            eprintln!(
                "turn {} run {:?} | queue {:?}",
                self.turn,
                action.kind,
                self.queue.iter().map(|a| &a.kind).collect::<Vec<_>>()
            );
        }
        let is_start = matches!(action.kind, ActionKind::Start);
        // HP before the action, for EmergencyExit after a residual (every
        // active) or a runSwitch (the Pokemon coming in).
        let hp_before: Vec<(MonRef, u16)> = match action.kind {
            ActionKind::Residual => self
                .all_active()
                .into_iter()
                .filter(|&r| self.mon(r).hp > 0)
                .map(|r| (r, self.mon(r).hp))
                .collect(),
            ActionKind::RunSwitch { mon } => vec![(mon, self.mon(mon).hp)],
            _ => Vec::new(),
        };
        match action.kind {
            ActionKind::Team { side, index, uid } => {
                if index == 0 {
                    self.benched[side] = std::mem::take(&mut self.sides[side].pokemon);
                }
                let pos = self.benched[side]
                    .iter()
                    .position(|m| m.uid == uid)
                    .expect("chosen Pokemon");
                let mut m = self.benched[side][pos].clone();
                m.position = index;
                self.sides[side].pokemon.push(m);
                return Ok(false);
            }
            ActionKind::Start => {
                for side in 0..2 {
                    let n = self.sides[side].pokemon.len();
                    self.sides[side].pokemon_left = n;
                    self.benched[side].clear();
                }
                for side in 0..2 {
                    for pos in 0..ACTIVE_PER_SIDE {
                        let r = self.mon_ref(side, pos);
                        self.switch_in(r, pos)?;
                    }
                }
                self.mid_turn = true;
            }
            ActionKind::BeforeTurn => {}
            ActionKind::Move {
                mon,
                slot,
                target_loc,
            } => {
                let m = self.mon(mon);
                if !m.is_active || m.fainted {
                    return Ok(false);
                }
                self.run_move(mon, slot, target_loc, action.priority as i8)?;
            }
            ActionKind::MegaEvo { mon } => self.run_mega_evo(mon)?,
            ActionKind::Revive { mon, target } => {
                let side = mon.side;
                self.sides[side].pokemon_left += 1;
                // A fainted Pokemon still in its slot comes straight back in
                // (queued last).
                if self.mon(target).position < ACTIVE_PER_SIDE {
                    let mut action = Action {
                        kind: ActionKind::Switch {
                            mon: target,
                            target,
                        },
                        order: 3,
                        priority: 0.0,
                        speed: 1,
                        fractional: 0.0,
                    };
                    self.action_speed(&mut action)?;
                    self.queue.push(action);
                }
                let t = self.mon_mut(target);
                t.fainted = false;
                t.faint_queued = false;
                t.status = crate::damage::Status::None;
                t.hp = (t.max_hp() / 2).max(1);
                let pos = self.mon(mon).position;
                if pos < ACTIVE_PER_SIDE {
                    self.sides[side].revival_blessing[pos] = false;
                }
            }
            ActionKind::PriorityCharge { mon } => {
                let m = self.mon(mon);
                if m.is_active && !m.fainted {
                    self.add_volatile(mon, state::VolatileId::ChillyReception);
                }
            }
            ActionKind::Switch { mon, target } => {
                let pos = self.mon(mon).position;
                assert!(
                    pos < ACTIVE_PER_SIDE,
                    "switch for {mon:?} at position {pos}; target {target:?}; list {:?}",
                    self.sides[mon.side]
                        .pokemon
                        .iter()
                        .map(|m| (m.uid, m.position))
                        .collect::<Vec<_>>()
                );
                self.switch_in(target, pos)?;
            }
            ActionKind::RunSwitch { mon } => self.run_switch(mon)?,
            ActionKind::Residual => {
                self.update_speed()?;
                self.residual()?;
            }
        }

        // Phazing: Red Card drags in a random replacement.
        for side in 0..2 {
            for pos in 0..ACTIVE_PER_SIDE {
                let Some(r) = self.occupant(side, pos) else {
                    continue;
                };
                if self.mon(r).force_switch_flag {
                    if self.mon(r).hp > 0 {
                        self.drag_in(side, pos)?;
                    }
                    if let Some(r) = self.occupant(side, pos) {
                        self.mon_mut(r).force_switch_flag = false;
                    }
                }
            }
        }
        self.clear_force_switch_flags();
        self.faint_messages()?;
        if self.is_over() {
            return Ok(true);
        }
        if self.queue.is_empty() {
            self.check_fainted();
        } else if self
            .queue
            .first()
            .is_some_and(|a| matches!(a.kind, ActionKind::Switch { .. }) && a.order == 3)
        {
            // More forced switches (instaswitch) already queued.
            return Ok(false);
        }
        if !is_start {
            self.each_update();
            for &(r, before) in &hp_before {
                self.emergency_exit(r, before);
            }
        }

        let switches: Vec<bool> = (0..2)
            .map(|s| {
                (0..ACTIVE_PER_SIDE).any(|p| {
                    self.sides[s]
                        .occupant(p)
                        .is_some_and(|m| m.switch_flag.is_some())
                })
            })
            .collect();
        let mut any = false;
        for side in 0..2 {
            if switches[side] && self.switchable(side).is_empty() {
                // Revival Blessing's slot keeps its (fake) switch.
                let mut revive = false;
                for p in 0..ACTIVE_PER_SIDE.min(self.sides[side].pokemon.len()) {
                    if self.sides[side].slot_filled[p] && self.sides[side].revival_blessing[p] {
                        revive = true;
                        continue;
                    }
                    self.sides[side].pokemon[p].switch_flag = None;
                }
                any |= revive;
            } else if switches[side] {
                any = true;
                // BeforeSwitchOut (no handlers yet) runs now for a Pokemon
                // leaving by its own move, not again when it switches.
                for p in 0..ACTIVE_PER_SIDE.min(self.sides[side].pokemon.len()) {
                    let m = &mut self.sides[side].pokemon[p];
                    if self.sides[side].slot_filled[p]
                        && !self.sides[side].revival_blessing[p]
                        && m.hp > 0
                        && m.switch_flag.is_some()
                        && !m.skip_before_switch_out
                    {
                        m.skip_before_switch_out = true;
                    }
                }
            }
        }
        if any {
            self.make_switch_request();
            return Ok(true);
        }

        // Gen 8+: speeds update after every action and the queue re-sorts.
        if self
            .queue
            .first()
            .is_some_and(|a| matches!(a.kind, ActionKind::Move { .. }))
        {
            self.update_speed()?;
            let mut q = std::mem::take(&mut self.queue);
            for a in q.iter_mut() {
                self.action_speed(a)?;
            }
            self.sort_actions(&mut q);
            self.queue = q;
        }
        Ok(false)
    }

    fn clear_force_switch_flags(&mut self) {
        for side in 0..2 {
            for m in self.sides[side].pokemon.iter_mut() {
                m.force_switch_flag = false;
            }
        }
    }

    /// `checkFainted`: fainted actives must be replaced.
    fn check_fainted(&mut self) {
        for side in 0..2 {
            for p in 0..ACTIVE_PER_SIDE {
                if self.sides[side].slot_filled[p] && self.sides[side].pokemon[p].fainted {
                    self.sides[side].pokemon[p].switch_flag = Some(SwitchFlag::Replace);
                }
            }
        }
    }

    fn make_switch_request(&mut self) {
        for side in 0..2 {
            let force = [0, 1].map(|p| {
                self.sides[side]
                    .occupant(p)
                    .is_some_and(|m| m.switch_flag.is_some())
            });
            self.requests[side] = if force.iter().any(|&f| f) {
                SideRequest::Switch(force)
            } else {
                SideRequest::Wait
            };
        }
    }

    /// `copyVolatileFrom(old, 'copyvolatile')`.
    fn copy_volatiles(&mut self, from: MonRef, to: MonRef) {
        use state::VolatileId as V;
        let src = self.mon(from).clone();
        let m = self.mon_mut(to);
        m.clear_volatile();
        m.boosts = src.boosts;
        for v in &src.volatiles.0 {
            if matches!(
                v.id,
                V::ChoiceLock
                    | V::Encore
                    | V::GlaiveRush
                    | V::Yawn
                    | V::Disable
                    | V::Imprison
                    | V::FlashFire
                    | V::SaltCure
                    | V::Minimize
                    | V::DestinyBond
                    | V::Stockpile
                    | V::SmackDown
            ) {
                continue;
            }
            m.volatiles.0.push(*v);
        }
        // roost's onType applies to the new Pokemon too.
        if m.volatiles.has(V::Roost) {
            let dex = Dex::get();
            m.start_roost(
                dex.type_id("Flying").expect("Flying"),
                dex.type_id("Normal").expect("Normal"),
            );
        }
    }

    /// `dragIn`: a random bench Pokemon replaces the one at `pos`.
    fn drag_in(&mut self, side: usize, pos: usize) -> Res<()> {
        let bench = self.switchable(side);
        if bench.is_empty() {
            return Ok(());
        }
        let i = bench[self.chance.sample(bench.len())];
        // DragOut: Guard Dog and Suction Cups stay.
        if self
            .occupant(side, pos)
            .is_some_and(|o| self.resists_drag(o))
        {
            return Ok(());
        }
        let r = self.mon_ref(side, i);
        self.switch_in_inner(r, pos, true)
    }

    /// Showdown's switchIn. `incoming` takes list position `pos`.
    fn switch_in(&mut self, incoming: MonRef, pos: usize) -> Res<()> {
        self.switch_in_inner(incoming, pos, false)
    }

    fn switch_in_inner(&mut self, incoming: MonRef, pos: usize, is_drag: bool) -> Res<()> {
        let side = incoming.side;
        let new_pos = self.mon(incoming).position;
        let old_active = self.occupant(side, pos);
        if let Some(old) = old_active {
            let o = self.mon_mut(old);
            if o.hp > 0 {
                o.being_called_back = true;
                // BeforeSwitchOut, then Update (Sitrus can still trigger).
                if !o.skip_before_switch_out && !is_drag {
                    self.each_update();
                }
                self.mon_mut(old).skip_before_switch_out = false;
                // SwitchOut: Regenerator heals a third.
                if self.ability_is(old, "regenerator") {
                    let m = self.mon_mut(old);
                    if m.hp > 0 && m.hp < m.max_hp() {
                        m.hp = (m.hp + m.max_hp() / 3).min(m.max_hp());
                    }
                }
                // Zero to Hero: Palafin leaves as Palafin-Hero for good.
                if self.ability_is(old, "zerotohero") {
                    let dex = Dex::get();
                    let m = self.mon_mut(old);
                    if dex.species(m.species).name == "Palafin" {
                        let hero = dex.species_id("Palafin-Hero").expect("Palafin-Hero");
                        m.species = hero;
                        m.base_species = hero;
                        m.types = dex.species(hero).types;
                    }
                }
                // Natural Cure: clearStatus.
                if self.ability_is(old, "naturalcure") {
                    let m = self.mon_mut(old);
                    m.status = crate::damage::Status::None;
                    m.status_state = state::StatusState::default();
                }
            }
            let o = self.mon_mut(old);
            // Leaving the field clears volatiles and boosts.
            if o.hp > 0 {
                self.queue.retain(|a| !action_belongs_to(a, old));
                // copyVolatileFrom: Baton Pass hands over boosts and volatiles
                // (bar the noCopy ones).
                if self.mon(old).baton_passing {
                    self.copy_volatiles(old, incoming);
                } else if self.mon(old).shed_tailing {
                    // copyVolatileFrom(old, 'shedtail'): only the substitute.
                    let sub = self
                        .mon(old)
                        .volatiles
                        .0
                        .iter()
                        .find(|v| v.id == state::VolatileId::Substitute)
                        .copied();
                    let m = self.mon_mut(incoming);
                    m.clear_volatile();
                    if let Some(v) = sub {
                        m.volatiles.0.push(v);
                    }
                }
                let o = self.mon_mut(old);
                o.clear_volatile();
                o.baton_passing = false;
                o.shed_tailing = false;
            }
            let o = self.mon_mut(old);
            o.is_active = false;
            if o.fainted {
                o.status = crate::damage::Status::None;
            }
            self.sides[side].pokemon.swap(pos, new_pos);
            self.sides[side].pokemon[new_pos].position = new_pos;
        } else if new_pos != pos {
            self.sides[side].pokemon.swap(pos, new_pos);
            self.sides[side].pokemon[new_pos].position = new_pos;
        }
        self.sides[side].slot_filled[pos] = true;
        // Illusion's onBeforeSwitchIn: disguised while a Pokemon later in
        // the list is still standing.
        let later_standing = self.sides[side].pokemon[pos + 1..]
            .iter()
            .any(|p| !p.fainted);
        let m = &mut self.sides[side].pokemon[pos];
        m.illusion = Dex::get().ability(m.ability).id == "illusion" && later_standing;
        m.position = pos;
        m.is_active = true;
        m.revealed = true;
        m.active_turns = 0;
        m.active_move_actions = 0;
        m.newly_switched = true;
        for s in m.moves.iter_mut() {
            s.used = false;
        }
        if is_drag {
            // runSwitch happens at once for a drag.
            return self.run_switch(incoming);
        }
        self.insert_action(ActionKind::RunSwitch { mon: incoming }, 101)?;
        Ok(())
    }

    /// `runSwitch`: switch-in effects for every Pokemon that just came in.
    fn run_switch(&mut self, first: MonRef) -> Res<()> {
        let mut switchers = vec![first];
        while let Some(ActionKind::RunSwitch { mon }) = self.queue.first().map(|a| a.kind.clone()) {
            self.queue.remove(0);
            switchers.push(mon);
        }
        self.switch_in_event(&switchers)
    }

    fn run_mega_evo(&mut self, r: MonRef) -> Res<()> {
        let Some(forme) = self.mon(r).can_mega_evo else {
            return Ok(());
        };
        let dex = Dex::get();
        let m = self.mon_mut(r);
        let sp = dex.species(forme);
        m.species = forme;
        m.base_species = forme;
        m.types = sp.types;
        let new_stats = stats::compute_stats(forme, m.set.nature, m.set.points);
        // Showdown keeps HP as is; only the other stats change.
        m.stats = [
            m.stats[0],
            new_stats[1],
            new_stats[2],
            new_stats[3],
            new_stats[4],
            new_stats[5],
        ];
        let mega_ability = dex.ability_id(&sp.abilities[0]).expect("mega ability");
        m.base_ability = mega_ability;
        self.set_ability(r, mega_ability);
        let m = self.mon_mut(r);
        // setAbility runs the new ability's Start.
        if m.hp > 0 {
            if let Some(e) = field::start_effect(&dex.ability(m.ability).id) {
                self.ability_start(r, e);
            }
        }
        for p in self.sides[r.side].pokemon.iter_mut() {
            p.can_mega_evo = None;
        }
        self.mega_used[r.side] = true;
        // formeChange: Mega Evolution counts as an action.
        self.mon_mut(r).move_this_turn_result = Some(Some(true));
        // AfterMega: White Herb's onAnyAfterMega.
        self.any_white_herb(r);
        Ok(())
    }

    /// `faintMessages`: process queued faints, then check for a winner.
    fn faint_messages(&mut self) -> Res<()> {
        if self.faint_queue.is_empty() {
            return Ok(());
        }
        let length = self.faint_queue.len();
        let mut last = None;
        let mut last_source = None;
        while !self.faint_queue.is_empty() {
            let (r, source) = self.faint_queue.remove(0);
            last = Some(r);
            last_source = source;
            let m = self.mon_mut(r);
            if m.fainted {
                continue;
            }
            // destinybond's onFaint: a foe's move takes the foe down too.
            let bond = m.volatiles.has(state::VolatileId::DestinyBond);
            if let Some(s) = source.filter(|s| bond && s.side != r.side) {
                self.faint(s);
            }
            let m = self.mon_mut(r);
            m.fainted = true;
            m.is_active = false;
            m.clear_volatile();
            // status becomes 'fnt': no paralysis speed drop for its (still
            // queued, never run) action when the queue re-sorts.
            m.status = crate::damage::Status::None;
            m.status_state = state::StatusState::default();
            let side = &mut self.sides[r.side];
            side.pokemon_left = side.pokemon_left.saturating_sub(1);
            if side.total_fainted < 100 {
                side.total_fainted += 1;
            }
            side.fainted_this_turn = true;
        }
        self.check_win(last);
        if self.outcome.is_some() {
            return Ok(());
        }
        // AfterFaint, for the last one only: Moxie (onSourceAfterFaint).
        if let Some(s) = last_source {
            if self.ability_is(s, "moxie") && self.mon(s).is_active {
                self.boost(s, &[(0, length as i8)], Some(s));
            }
            // Eelevate: getBestStat(unboosted, unmodified), the first on ties.
            if self.ability_is(s, "eelevate") && self.mon(s).is_active {
                let stats = self.mon(s).stats;
                let best = (1..6).fold(1, |b, i| if stats[i] > stats[b] { i } else { b });
                self.boost(s, &[(best - 1, length as i8)], Some(s));
            }
        }
        Ok(())
    }

    /// `checkWin`. When both sides run out at once, the side whose Pokemon
    /// fainted last wins (gen 5+).
    fn check_win(&mut self, last_fainted: Option<MonRef>) {
        let left = [self.sides[0].pokemon_left, self.sides[1].pokemon_left];
        self.outcome = match left {
            [0, 0] => Some(last_fainted.map_or(Outcome::Tie, |r| Outcome::Win(r.side))),
            [_, 0] => Some(Outcome::Win(0)),
            [0, _] => Some(Outcome::Win(1)),
            _ => None,
        };
        if self.outcome.is_some() {
            self.requests = [SideRequest::Wait, SideRequest::Wait];
        }
    }

    fn end_turn(&mut self) -> Res<()> {
        self.turn += 1;
        if self.turn_limit.is_some_and(|l| self.turn > l) {
            self.outcome = Some(Outcome::Tie);
            self.requests = [SideRequest::Wait, SideRequest::Wait];
            return Ok(());
        }
        for side in 0..2 {
            for p in 0..ACTIVE_PER_SIDE {
                if !self.sides[side].slot_filled[p] {
                    continue;
                }
                let r = self.mon_ref(side, p);
                // Attacks are last turn's now; those by Pokemon gone from the
                // field are forgotten.
                let attackers = std::mem::take(&mut self.mon_mut(r).attacked_by);
                let attackers = attackers
                    .into_iter()
                    .filter(|a| self.mon(a.source).is_active)
                    .map(|a| Attacker {
                        this_turn: false,
                        ..a
                    })
                    .collect();
                self.mon_mut(r).attacked_by = attackers;
                let locked = self.choice_locked_move(r);
                // A foe's imprison: its moves (but Struggle) can't be chosen.
                let imprisoned: Vec<crate::dex::MoveId> = self.sides[side].pokemon[p]
                    .moves
                    .iter()
                    .map(|s| s.id)
                    .filter(|&id| {
                        Dex::get().move_data(id).id != "struggle" && self.imprisoned(r, id)
                    })
                    .collect();
                let m = &mut self.sides[side].pokemon[p];
                m.move_this_turn = None;
                m.newly_switched = false;
                m.move_last_turn_result = m.move_this_turn_result;
                m.move_this_turn_result = None;
                if self.turn != 1 {
                    m.hurt_this_turn = None;
                    m.round_boost = false;
                    m.stats_raised_this_turn = false;
                    m.stats_lowered_this_turn = false;
                }
                // DisableMove: Gigaton Hammer and Blood Moon ("cantusetwice")
                // can't be chosen right after being used.
                // Fake Out's onDisableMove: only usable on the first turn out.
                // Encore and Throat Chop's onDisableMove.
                let last = m.last_move;
                let acted = m.active_move_actions > 0;
                let encored = m
                    .volatiles
                    .0
                    .iter()
                    .find(|v| v.id == state::VolatileId::Encore)
                    .and_then(|v| v.move_id)
                    .filter(|&e| m.move_slot(e).is_some());
                let throat_chopped = m.volatiles.has(state::VolatileId::ThroatChop);
                let taunted = m.volatiles.has(state::VolatileId::Taunt);
                let heal_blocked = m.volatiles.has(state::VolatileId::HealBlock);
                let gravity = self.field.gravity > 0;
                let disabled = m
                    .volatiles
                    .0
                    .iter()
                    .find(|v| v.id == state::VolatileId::Disable)
                    .and_then(|v| v.move_id);
                for s in m.moves.iter_mut() {
                    let data = Dex::get().move_data(s.id);
                    s.disabled = (data.flags.has("cantusetwice") && last == Some(s.id))
                        || (matches!(data.id.as_str(), "fakeout" | "firstimpression") && acted)
                        || locked.is_some_and(|l| l != s.id)
                        || encored.is_some_and(|e| e != s.id)
                        || (throat_chopped && data.flags.has("sound"))
                        || (taunted && data.category == crate::dex::Category::Status)
                        || (heal_blocked && data.flags.has("heal"))
                        || (gravity && data.flags.has("gravity"))
                        || disabled == Some(s.id);
                    s.imprisoned = imprisoned.contains(&s.id);
                }
                if !m.fainted {
                    m.active_turns += 1;
                }
            }
            let side = &mut self.sides[side];
            side.fainted_last_turn = side.fainted_this_turn;
            side.fainted_this_turn = false;
        }
        self.make_move_request();
        Ok(())
    }

    fn make_move_request(&mut self) {
        for side in 0..2 {
            let slots = [0, 1].map(|p| {
                let m = self.sides[side].occupant(p)?;
                if m.fainted {
                    return None;
                }
                // Locked (charging or recharging): one option, no switching.
                if let Some(locked) = m.locked_move() {
                    let id = match locked {
                        state::LockedMove::Move(id) => id,
                        state::LockedMove::Recharge => Dex::get().move_id("struggle").unwrap(),
                    };
                    let only = choice::MoveOption {
                        slot: 0,
                        id,
                        pp: 0,
                        disabled: false,
                        hidden: false,
                        target: crate::dex::MoveTarget::SelfTarget,
                    };
                    return Some(SlotRequest {
                        moves: vec![only],
                        struggle: false,
                        can_mega: false,
                        trapped: true,
                    });
                }
                // isLastActive: no unfainted active after it on its side.
                let last_active = (p + 1..ACTIVE_PER_SIDE)
                    .all(|q| self.sides[side].occupant(q).is_none_or(|o| o.fainted));
                let mut moves: Vec<choice::MoveOption> = m
                    .moves
                    .iter()
                    .enumerate()
                    .map(|(i, s)| {
                        let off = s.disabled || s.pp == 0;
                        choice::MoveOption {
                            slot: i as u8,
                            id: s.id,
                            pp: s.pp,
                            disabled: off || (s.imprisoned && !last_active),
                            hidden: !off && s.imprisoned && last_active,
                            target: request_target(m, s.id),
                        }
                    })
                    .collect();
                let struggle = moves.iter().all(|o| o.disabled);
                if struggle {
                    let id = Dex::get().move_id("struggle").unwrap();
                    moves = vec![choice::MoveOption {
                        slot: 0,
                        id,
                        pp: 0,
                        disabled: false,
                        hidden: false,
                        target: Dex::get().move_data(id).target,
                    }];
                }
                // TrapPokemon: a foe's Shadow Tag (Ghost types and other
                // Shadow Tag users go free).
                let r = MonRef { side, uid: m.uid };
                // Shed Shell's onTrapPokemon (priority -10) frees it.
                let shadow_tag = !self.ability_is(r, "shadowtag")
                    && self
                        .adjacent_foes(r)
                        .into_iter()
                        .any(|f| self.ability_is(f, "shadowtag"));
                // partiallytrapped's onTrapPokemon: while its source is in.
                let bound = m
                    .volatiles
                    .0
                    .iter()
                    .find(|v| v.id == state::VolatileId::PartiallyTrapped)
                    .and_then(|v| self.trap_source(v.counter))
                    .is_some_and(|s| self.mon(s).is_active);
                let no_retreat = m.volatiles.has(state::VolatileId::NoRetreat);
                let trapped =
                    // Shed Shell and Run Away (onTrapPokemon, priority -10) free it.
                    (shadow_tag || bound || no_retreat)
                        && self.item_of(r) != Some("shedshell")
                        && !self.ability_is(r, "runaway")
                        && !Dex::get().immune_to("trapped", m.types);
                // Struggling counts as a locked move: no Mega Evolution.
                Some(SlotRequest {
                    moves,
                    struggle,
                    can_mega: m.can_mega_evo.is_some() && !struggle,
                    trapped,
                })
            });
            self.requests[side] = if slots.iter().any(Option::is_some) {
                SideRequest::Move(slots)
            } else {
                SideRequest::Wait
            };
        }
    }
}

fn action_belongs_to(a: &Action, r: MonRef) -> bool {
    match &a.kind {
        ActionKind::Move { mon, .. }
        | ActionKind::MegaEvo { mon }
        | ActionKind::PriorityCharge { mon }
        | ActionKind::Revive { mon, .. }
        | ActionKind::Switch { mon, .. }
        | ActionKind::RunSwitch { mon } => *mon == r,
        _ => false,
    }
}

/// The target a request shows: Curse's nonGhostTarget is self.
fn request_target(m: &Mon, id: crate::dex::MoveId) -> crate::dex::MoveTarget {
    let dex = Dex::get();
    let data = dex.move_data(id);
    if data.id == "curse" && !m.has_type(dex.type_id("Ghost").expect("Ghost")) {
        return crate::dex::MoveTarget::SelfTarget;
    }
    data.target
}

/// Up to four Pokemon, without allocating (`all_active`).
#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct MonList {
    buf: [Option<MonRef>; 4],
    len: usize,
}

impl MonList {
    fn push(&mut self, r: MonRef) {
        self.buf[self.len] = Some(r);
        self.len += 1;
    }
    pub(crate) fn iter(&self) -> impl Iterator<Item = &MonRef> + '_ {
        self.buf[..self.len].iter().flatten()
    }
}

impl IntoIterator for MonList {
    type Item = MonRef;
    type IntoIter = std::iter::Flatten<std::array::IntoIter<Option<MonRef>, 4>>;
    fn into_iter(self) -> Self::IntoIter {
        self.buf.into_iter().flatten()
    }
}

/// Showdown's comparePriority for queue actions.
fn compare_priority(a: &Action, b: &Action) -> Ordering {
    a.order
        .cmp(&b.order)
        .then(
            b.priority
                .partial_cmp(&a.priority)
                .unwrap_or(Ordering::Equal),
        )
        .then(b.speed.cmp(&a.speed))
}

/// A stat with its stage applied (`getStat`'s boost step).
pub(crate) fn boosted(stat: u16, boost: i8) -> u32 {
    const TABLE: [(u32, u32); 7] = [(1, 1), (3, 2), (2, 1), (5, 2), (3, 1), (7, 2), (4, 1)];
    let b = boost.clamp(-6, 6);
    let (num, den) = TABLE[b.unsigned_abs() as usize];
    if b >= 0 {
        stat as u32 * num / den
    } else {
        stat as u32 * den / num
    }
}
