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
pub mod snapshot;
mod moves;
pub mod state;
pub mod support;

use crate::chance::Chance;
use crate::dex::Dex;
use crate::stats;
use crate::team::PokemonSet;
use choice::{SideChoice, SideRequest, SlotChoice, SlotRequest};
use state::{Field, Mon, Side, SwitchFlag, ACTIVE_PER_SIDE};
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
    Team { side: usize, index: usize, uid: usize },
    Start,
    BeforeTurn,
    Move { mon: MonRef, slot: usize, target_loc: i8 },
    MegaEvo { mon: MonRef },
    Switch { mon: MonRef, target: MonRef },
    RunSwitch { mon: MonRef },
    Residual,
}

#[derive(Debug, Clone, PartialEq)]
struct Action {
    kind: ActionKind,
    order: u32,
    priority: f64,
    speed: i32,
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
    queue: Vec<Action>,
    mid_turn: bool,
    faint_queue: Vec<MonRef>,
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
            Side { pokemon_left: pokemon.len(), pokemon, ..Side::default() }
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
            mega_used: [false; 2],
            effect_order: 0,
            benched: [Vec::new(), Vec::new()],
        };
        b.push_action(ActionKind::Start);
        Ok(b)
    }

    pub fn is_over(&self) -> bool {
        self.outcome.is_some()
    }

    pub fn mon(&self, r: MonRef) -> &Mon {
        self.sides[r.side].pokemon.iter().find(|m| m.uid == r.uid).expect("MonRef to a Pokemon on its side")
    }

    fn mon_mut(&mut self, r: MonRef) -> &mut Mon {
        self.sides[r.side].pokemon.iter_mut().find(|m| m.uid == r.uid).expect("MonRef to a Pokemon on its side")
    }

    fn mon_ref(&self, side: usize, position: usize) -> MonRef {
        MonRef { side, uid: self.sides[side].pokemon[position].uid }
    }

    /// The Pokemon in a slot (`side.active[pos]`), fainted or not.
    pub fn occupant(&self, side: usize, pos: usize) -> Option<MonRef> {
        self.sides[side].occupant(pos).map(|m| MonRef { side, uid: m.uid })
    }

    /// Showdown's `getAllActive()`: slot occupants that haven't fainted.
    fn all_active(&self) -> Vec<MonRef> {
        let mut out = Vec::new();
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
                (None, true) => return Err(BattleError::Illegal(format!("side {side} must choose"))),
                (Some(_), false) => return Err(BattleError::Illegal(format!("side {side} has nothing to choose"))),
                (Some(c), true) if !self.legal_choices(side).contains(c) => {
                    return Err(BattleError::Illegal(format!("side {side}: {c:?} isn't legal")));
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
        self.speed_sort(&mut q, compare_priority);
        q.extend(old);
        self.queue = q;
        self.requests = [SideRequest::Wait, SideRequest::Wait];
        self.turn_loop()
    }

    fn add_slot_choice(&mut self, side: usize, pos: usize, choice: SlotChoice) -> Res<()> {
        let mon = self.mon_ref(side, pos);
        match choice {
            SlotChoice::Pass => {}
            SlotChoice::Switch { index } => {
                // resolveAction clears the switch flag once the switch is queued.
                self.sides[side].pokemon[pos].switch_flag = None;
                let target = self.mon_ref(side, index as usize);
                let order = if matches!(self.requests[side], SideRequest::Switch(_)) { 3 } else { 103 };
                self.add_action(ActionKind::Switch { mon, target }, order, 0.0)?;
            }
            SlotChoice::Move { slot, target, mega } => {
                if mega {
                    self.add_action(ActionKind::MegaEvo { mon }, 104, 0.0)?;
                }
                let m = self.mon(mon);
                let slot = if m.moves.iter().any(|s| s.pp > 0 && !s.disabled) { slot as usize } else { usize::MAX };
                self.add_action(ActionKind::Move { mon, slot, target_loc: target }, 200, 0.0)?;
            }
        }
        Ok(())
    }

    /// Showdown's resolveAction + getActionSpeed, appended to the queue.
    fn add_action(&mut self, kind: ActionKind, order: u32, priority: f64) -> Res<()> {
        let mut action = Action { kind, order, priority, speed: 1 };
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
        self.queue.push(Action { kind, order, priority: 0.0, speed: 1 });
    }

    /// `getActionSpeed`: a move's priority, and the acting Pokemon's speed.
    fn action_speed(&mut self, action: &mut Action) -> Res<()> {
        let mon = match &action.kind {
            ActionKind::Move { mon, slot, .. } => {
                let m = self.mon(*mon);
                let id = moves::move_for_slot(m, *slot);
                action.priority = Dex::get().move_data(id).priority as f64;
                Some(*mon)
            }
            ActionKind::MegaEvo { mon } | ActionKind::Switch { mon, .. } | ActionKind::RunSwitch { mon } => Some(*mon),
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
        // ModifySpe: Choice Scarf and Tailwind chain their modifiers.
        let mut modifier = self.speed_modifier(r);
        if self.sides[r.side].tailwind > 0 {
            modifier = crate::fixed::chain(modifier, 8192);
        }
        let mut spe = crate::fixed::modify(boosted(m.stats[5], m.boosts[4]) as u64, modifier) as u32;
        // par's onModifySpe (Quick Feet isn't supported).
        if m.status == crate::damage::Status::Paralysis {
            spe = spe * 50 / 100;
        }
        let spe = spe.min(10_000);
        // getActionSpeed: Trick Room inverts, then `trunc(speed, 13)`.
        let spe = if self.field.trick_room > 0 { 10_000 - spe } else { spe };
        Ok((spe % 8192) as i32)
    }

    /// `queue.willMove(pokemon)`.
    fn will_move(&self, r: MonRef) -> bool {
        !self.mon(r).fainted && self.queue.iter().any(|a| matches!(a.kind, ActionKind::Move { mon, .. } if mon == r))
    }

    /// `queue.willAct()`: a move or switch is still to come this turn.
    fn will_act(&self) -> bool {
        self.queue.iter().any(|a| matches!(a.kind, ActionKind::Move { .. } | ActionKind::Switch { .. }))
    }

    /// `battle.updateSpeed()`: refresh every active Pokemon's `speed`.
    fn update_speed(&mut self) -> Res<()> {
        for r in self.all_active() {
            let s = self.action_speed_of(r)?;
            self.mon_mut(r).speed = s;
        }
        Ok(())
    }

    /// Showdown's speedSort: selection sort that shuffles exact ties.
    fn speed_sort<T>(&mut self, list: &mut [T], cmp: impl Fn(&T, &T) -> Ordering) {
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
                self.chance.shuffle(&mut list[sorted..sorted + next.len()]);
            }
            sorted += next.len();
        }
    }

    /// `queue.insertChoice` for an action created mid-turn (runSwitch):
    /// placed where it would sort, ties broken at random.
    fn insert_action(&mut self, kind: ActionKind, order: u32) -> Res<()> {
        let mut action = Action { kind, order, priority: 0.0, speed: 1 };
        if let ActionKind::RunSwitch { mon } = action.kind {
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
                let index = if f == l { f } else { self.chance.random_range(f as u32, l as u32 + 1) as usize };
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
        if std::env::var_os("SELFPLAY_TRACE").is_some() {
            eprintln!("turn {} run {:?} | queue {:?}", self.turn, action.kind, self.queue.iter().map(|a| &a.kind).collect::<Vec<_>>());
        }
        let is_start = matches!(action.kind, ActionKind::Start);
        match action.kind {
            ActionKind::Team { side, index, uid } => {
                if index == 0 {
                    self.benched[side] = std::mem::take(&mut self.sides[side].pokemon);
                }
                let pos = self.benched[side].iter().position(|m| m.uid == uid).expect("chosen Pokemon");
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
            ActionKind::Move { mon, slot, target_loc } => {
                let m = self.mon(mon);
                if !m.is_active || m.fainted {
                    return Ok(false);
                }
                self.run_move(mon, slot, target_loc)?;
            }
            ActionKind::MegaEvo { mon } => self.run_mega_evo(mon)?,
            ActionKind::Switch { mon, target } => {
                let pos = self.mon(mon).position;
                assert!(
                    pos < ACTIVE_PER_SIDE,
                    "switch for {mon:?} at position {pos}; target {target:?}; list {:?}",
                    self.sides[mon.side].pokemon.iter().map(|m| (m.uid, m.position)).collect::<Vec<_>>()
                );
                self.switch_in(target, pos)?;
            }
            ActionKind::RunSwitch { mon } => self.run_switch(mon)?,
            ActionKind::Residual => {
                self.update_speed()?;
                self.residual()?;
            }
        }

        self.clear_force_switch_flags();
        self.faint_messages()?;
        if self.is_over() {
            return Ok(true);
        }
        if self.queue.is_empty() {
            self.check_fainted();
        } else if self.queue.first().is_some_and(|a| matches!(a.kind, ActionKind::Switch { .. }) && a.order == 3) {
            // More forced switches (instaswitch) already queued.
            return Ok(false);
        }
        if !is_start {
            self.each_update();
        }

        let switches: Vec<bool> = (0..2)
            .map(|s| (0..ACTIVE_PER_SIDE).any(|p| self.sides[s].occupant(p).is_some_and(|m| m.switch_flag.is_some())))
            .collect();
        let mut any = false;
        for side in 0..2 {
            if switches[side] && self.switchable(side).is_empty() {
                for p in 0..ACTIVE_PER_SIDE.min(self.sides[side].pokemon.len()) {
                    self.sides[side].pokemon[p].switch_flag = None;
                }
            } else if switches[side] {
                any = true;
                // BeforeSwitchOut (no handlers yet) runs now for a Pokemon
                // leaving by its own move, not again when it switches.
                for p in 0..ACTIVE_PER_SIDE.min(self.sides[side].pokemon.len()) {
                    let m = &mut self.sides[side].pokemon[p];
                    if self.sides[side].slot_filled[p] && m.hp > 0 && m.switch_flag.is_some() && !m.skip_before_switch_out {
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
        if self.queue.first().is_some_and(|a| matches!(a.kind, ActionKind::Move { .. })) {
            self.update_speed()?;
            let mut q = std::mem::take(&mut self.queue);
            for a in q.iter_mut() {
                self.action_speed(a)?;
            }
            self.speed_sort(&mut q, compare_priority);
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
            let force = [0, 1].map(|p| self.sides[side].occupant(p).is_some_and(|m| m.switch_flag.is_some()));
            self.requests[side] = if force.iter().any(|&f| f) { SideRequest::Switch(force) } else { SideRequest::Wait };
        }
    }

    /// Showdown's switchIn. `incoming` takes list position `pos`.
    fn switch_in(&mut self, incoming: MonRef, pos: usize) -> Res<()> {
        let side = incoming.side;
        let new_pos = self.mon(incoming).position;
        let old_active = self.occupant(side, pos);
        if let Some(old) = old_active {
            let o = self.mon_mut(old);
            if o.hp > 0 {
                o.being_called_back = true;
                // BeforeSwitchOut, then Update (Sitrus can still trigger).
                if !o.skip_before_switch_out {
                    self.each_update();
                }
                self.mon_mut(old).skip_before_switch_out = false;
            }
            let o = self.mon_mut(old);
            // Leaving the field clears volatiles and boosts.
            if o.hp > 0 {
                self.queue.retain(|a| !action_belongs_to(a, old));
                let o = self.mon_mut(old);
                o.clear_volatile();
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
        let m = &mut self.sides[side].pokemon[pos];
        m.position = pos;
        m.is_active = true;
        m.active_turns = 0;
        m.active_move_actions = 0;
        m.newly_switched = true;
        for s in m.moves.iter_mut() {
            s.used = false;
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
        let Some(forme) = self.mon(r).can_mega_evo else { return Ok(()) };
        let dex = Dex::get();
        let m = self.mon_mut(r);
        let sp = dex.species(forme);
        m.species = forme;
        m.types = sp.types;
        let new_stats = stats::compute_stats(forme, m.set.nature, m.set.points);
        // Showdown keeps HP as is; only the other stats change.
        m.stats = [m.stats[0], new_stats[1], new_stats[2], new_stats[3], new_stats[4], new_stats[5]];
        m.ability = dex.ability_id(&sp.abilities[0]).expect("mega ability");
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
        Ok(())
    }

    /// `faintMessages`: process queued faints, then check for a winner.
    fn faint_messages(&mut self) -> Res<()> {
        if self.faint_queue.is_empty() {
            return Ok(());
        }
        let mut last = None;
        while !self.faint_queue.is_empty() {
            let r = self.faint_queue.remove(0);
            last = Some(r);
            let m = self.mon_mut(r);
            if m.fainted {
                continue;
            }
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
        for side in 0..2 {
            for p in 0..ACTIVE_PER_SIDE {
                if !self.sides[side].slot_filled[p] {
                    continue;
                }
                let r = self.mon_ref(side, p);
                let locked = self.choice_locked_move(r);
                let m = &mut self.sides[side].pokemon[p];
                m.move_this_turn = None;
                m.newly_switched = false;
                m.move_last_turn_result = m.move_this_turn_result;
                m.move_this_turn_result = None;
                if self.turn != 1 {
                    m.hurt_this_turn = None;
                }
                // DisableMove: Gigaton Hammer and Blood Moon ("cantusetwice")
                // can't be chosen right after being used.
                // Fake Out's onDisableMove: only usable on the first turn out.
                let last = m.last_move;
                let acted = m.active_move_actions > 0;
                for s in m.moves.iter_mut() {
                    let data = Dex::get().move_data(s.id);
                    s.disabled = (data.flags.has("cantusetwice") && last == Some(s.id))
                        || (data.id == "fakeout" && acted)
                        || locked.is_some_and(|l| l != s.id);
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
                let mut moves: Vec<choice::MoveOption> = m
                    .moves
                    .iter()
                    .enumerate()
                    .map(|(i, s)| choice::MoveOption {
                        slot: i as u8,
                        id: s.id,
                        pp: s.pp,
                        disabled: s.disabled || s.pp == 0,
                        target: Dex::get().move_data(s.id).target,
                    })
                    .collect();
                let struggle = moves.iter().all(|o| o.disabled);
                if struggle {
                    let id = Dex::get().move_id("struggle").unwrap();
                    moves = vec![choice::MoveOption { slot: 0, id, pp: 0, disabled: false, target: Dex::get().move_data(id).target }];
                }
                Some(SlotRequest { moves, struggle, can_mega: m.can_mega_evo.is_some(), trapped: false })
            });
            self.requests[side] = if slots.iter().any(Option::is_some) { SideRequest::Move(slots) } else { SideRequest::Wait };
        }
    }
}

fn action_belongs_to(a: &Action, r: MonRef) -> bool {
    match &a.kind {
        ActionKind::Move { mon, .. } | ActionKind::MegaEvo { mon } | ActionKind::Switch { mon, .. } | ActionKind::RunSwitch { mon } => *mon == r,
        _ => false,
    }
}

/// Showdown's comparePriority for queue actions.
fn compare_priority(a: &Action, b: &Action) -> Ordering {
    a.order
        .cmp(&b.order)
        .then(b.priority.partial_cmp(&a.priority).unwrap_or(Ordering::Equal))
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
