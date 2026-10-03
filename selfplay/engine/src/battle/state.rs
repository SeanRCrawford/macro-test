//! Battle state: Pokemon, sides and the field.
//!
//! Field names follow Showdown's (`activeTurns`, `switchFlag`...) so the
//! turn code can be compared line by line with sim/battle.ts and friends.

use crate::damage::{Status, Terrain, Weather};
use crate::dex::{AbilityId, Dex, ItemId, MoveId, SpeciesId, TypeId};
use crate::stats::compute_stats;
use crate::team::{mega_forme, PokemonSet};

pub const ACTIVE_PER_SIDE: usize = 2;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MoveSlot {
    pub id: MoveId,
    pub pp: u8,
    pub max_pp: u8,
    pub disabled: bool,
    /// Used since the Pokemon last switched in (Last Resort).
    pub used: bool,
}

/// The volatile conditions the engine implements.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VolatileId {
    Flinch,
    Protect,
    /// Protect's consecutive-use counter.
    Stall,
    /// Locked into one move by a Choice item.
    ChoiceLock,
    FollowMe,
    RagePowder,
    /// Helping Hand's boost; `counter` is how many times it was used.
    HelpingHand,
}

impl VolatileId {
    pub fn parse(id: &str) -> Option<VolatileId> {
        Some(match id {
            "flinch" => VolatileId::Flinch,
            "protect" => VolatileId::Protect,
            "stall" => VolatileId::Stall,
            "choicelock" => VolatileId::ChoiceLock,
            "followme" => VolatileId::FollowMe,
            "ragepowder" => VolatileId::RagePowder,
            "helpinghand" => VolatileId::HelpingHand,
            _ => return None,
        })
    }

    pub fn id(self) -> &'static str {
        match self {
            VolatileId::Flinch => "flinch",
            VolatileId::Protect => "protect",
            VolatileId::Stall => "stall",
            VolatileId::ChoiceLock => "choicelock",
            VolatileId::FollowMe => "followme",
            VolatileId::RagePowder => "ragepowder",
            VolatileId::HelpingHand => "helpinghand",
        }
    }

    /// The condition's `duration`.
    pub fn duration(self) -> Option<u8> {
        match self {
            VolatileId::Flinch | VolatileId::Protect | VolatileId::FollowMe | VolatileId::RagePowder | VolatileId::HelpingHand => {
                Some(1)
            }
            VolatileId::Stall => Some(2),
            VolatileId::ChoiceLock => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Volatile {
    pub id: VolatileId,
    pub duration: Option<u8>,
    /// Stall's success counter (1 in `counter`).
    pub counter: u32,
    /// The move a Choice lock holds the Pokemon to.
    pub move_id: Option<MoveId>,
    /// Showdown's `effectOrder`: creation order, a tiebreak for redirection.
    pub effect_order: u64,
}

/// Volatile conditions in the order they were added (Showdown iterates
/// `pokemon.volatiles` in insertion order, which orders tied handlers).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct Volatiles(pub Vec<Volatile>);

impl Volatiles {
    pub fn has(&self, id: VolatileId) -> bool {
        self.0.iter().any(|v| v.id == id)
    }

    pub fn get_mut(&mut self, id: VolatileId) -> Option<&mut Volatile> {
        self.0.iter_mut().find(|v| v.id == id)
    }

    /// Returns whether it was there.
    pub fn remove(&mut self, id: VolatileId) -> bool {
        let before = self.0.len();
        self.0.retain(|v| v.id != id);
        self.0.len() != before
    }
}

/// `statusState`: sleep and freeze turns, Toxic's stage.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct StatusState {
    pub time: i8,
    pub stage: u8,
}

/// Why a Pokemon must leave the field before the turn can continue.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SwitchFlag {
    /// Fainted, or otherwise needs replacing.
    Replace,
    /// A move (U-turn...) switches its user out.
    Move(MoveId),
}

#[derive(Debug, Clone)]
pub struct Mon {
    /// The set as brought (base species, original ability and item).
    pub set: PokemonSet,
    /// Current species (changes on Mega Evolution).
    pub species: SpeciesId,
    pub types: [TypeId; 2],
    /// Stored stats for the current species; [HP] is max HP.
    pub stats: [u16; 6],
    pub hp: u16,
    pub status: Status,
    pub status_state: StatusState,
    /// atk, def, spa, spd, spe, accuracy, evasion.
    pub boosts: [i8; 7],
    pub ability: AbilityId,
    pub item: Option<ItemId>,
    pub moves: Vec<MoveSlot>,
    pub volatiles: Volatiles,
    /// Original team index; stable across switches.
    pub uid: usize,
    /// Index in its side's `pokemon` list. Actives are always 0 and 1.
    pub position: usize,
    pub is_active: bool,
    pub fainted: bool,
    /// Queued to faint (HP hit 0) but not yet processed by `faint_messages`.
    pub faint_queued: bool,
    pub switch_flag: Option<SwitchFlag>,
    pub force_switch_flag: bool,
    /// BeforeSwitchOut already ran for a pending self-switch.
    pub skip_before_switch_out: bool,
    pub active_turns: u16,
    pub active_move_actions: u16,
    pub newly_switched: bool,
    pub being_called_back: bool,
    /// The Mega forme this Pokemon can still become.
    pub can_mega_evo: Option<SpeciesId>,
    pub last_move: Option<MoveId>,
    pub move_this_turn: Option<MoveId>,
    /// `moveThisTurnResult`: Some(true) moved, Some(false) failed, None not yet / no result.
    pub move_this_turn_result: Option<bool>,
    pub move_last_turn_result: Option<bool>,
    pub times_attacked: u8,
    /// HP after this Pokemon was last damaged this turn (`hurtThisTurn`).
    pub hurt_this_turn: Option<u16>,
    /// Showdown's `pokemon.speed`: action speed as of the last `updateSpeed`.
    pub speed: i32,
}

impl Mon {
    pub fn new(set: &PokemonSet) -> Self {
        let dex = Dex::get();
        let stats = compute_stats(set.species, set.nature, set.points);
        let moves = set
            .moves
            .iter()
            .map(|&id| {
                let pp = dex.move_data(id).max_pp();
                MoveSlot { id, pp, max_pp: pp, disabled: false, used: false }
            })
            .collect();
        Mon {
            set: set.clone(),
            species: set.species,
            types: dex.species(set.species).types,
            stats,
            hp: stats[0],
            status: Status::None,
            status_state: StatusState::default(),
            boosts: [0; 7],
            ability: set.ability,
            item: set.item,
            moves,
            volatiles: Volatiles::default(),
            uid: 0,
            position: 0,
            is_active: false,
            fainted: false,
            faint_queued: false,
            switch_flag: None,
            force_switch_flag: false,
            skip_before_switch_out: false,
            active_turns: 0,
            active_move_actions: 0,
            newly_switched: false,
            being_called_back: false,
            can_mega_evo: mega_forme(set),
            last_move: None,
            move_this_turn: None,
            move_this_turn_result: None,
            move_last_turn_result: None,
            times_attacked: 0,
            hurt_this_turn: None,
            speed: stats[5] as i32,
        }
    }

    pub fn max_hp(&self) -> u16 {
        self.stats[0]
    }

    pub fn has_type(&self, t: TypeId) -> bool {
        self.types.contains(&t)
    }

    /// `clearVolatile`: what leaving the field (or fainting) resets.
    pub fn clear_volatile(&mut self) {
        self.boosts = [0; 7];
        self.volatiles = Volatiles::default();
        self.last_move = None;
        self.move_this_turn = None;
        self.move_last_turn_result = None;
        self.move_this_turn_result = None;
        self.newly_switched = true;
        self.being_called_back = false;
        self.times_attacked = 0;
        self.switch_flag = None;
        self.force_switch_flag = false;
        // Champions: a Mega stays Mega after fainting or switching.
    }

    pub fn move_slot(&self, id: MoveId) -> Option<usize> {
        self.moves.iter().position(|m| m.id == id)
    }
}

#[derive(Debug, Clone, Default)]
pub struct Side {
    /// Before team preview: all six. After: the four brought, actives first.
    pub pokemon: Vec<Mon>,
    /// Pokemon that haven't fainted.
    pub pokemon_left: usize,
    pub total_fainted: u8,
    /// Index into `pokemon` of the Pokemon that fainted this turn, if any.
    pub fainted_this_turn: bool,
    pub fainted_last_turn: bool,
    /// Showdown's `side.active[slot]` is set: the Pokemon at list position
    /// `slot` occupies it (fainted or not) until replaced.
    pub slot_filled: [bool; ACTIVE_PER_SIDE],
    /// Tailwind's remaining duration (0: not up).
    pub tailwind: u8,
}

impl Side {
    /// The Pokemon in a slot, fainted or not.
    pub fn occupant(&self, slot: usize) -> Option<&Mon> {
        if self.slot_filled[slot] {
            self.pokemon.get(slot)
        } else {
            None
        }
    }
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Field {
    pub weather: Weather,
    pub weather_turns: u8,
    pub terrain: Terrain,
    pub terrain_turns: u8,
    /// Trick Room's remaining duration (0: not up).
    pub trick_room: u8,
}
