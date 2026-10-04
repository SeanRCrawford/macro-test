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
    /// Disabled by a foe's Imprison ("hidden": the last active Pokemon's
    /// request still shows it).
    pub imprisoned: bool,
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
    /// Locked into `move_id` for a few turns.
    Encore,
    /// No sound moves for two turns.
    ThroatChop,
    /// Unburden: doubled Speed once the item is gone.
    Unburden,
    /// Glaive Rush: hit for sure and for double damage until it moves again.
    GlaiveRush,
    /// `counter` is the turns of confusion left.
    Confusion,
    Yawn,
    Taunt,
    /// Disables `move_id`.
    Disable,
    /// Flying type lost for the turn.
    Roost,
    SpikyShield,
    KingsShield,
    BanefulBunker,
    PerishSong,
    Imprison,
    /// Charging a two-turn move (`twoturnmove`): locks the next turn's move.
    TwoTurnMove,
    /// The charging move's own volatile (id: the move), holding its target.
    Charging(MoveId),
    /// Hyper Beam and friends: the next turn is lost.
    MustRecharge,
    /// Flash Fire's boost to Fire moves.
    FlashFire,
    /// Focus Energy: +2 crit ratio.
    FocusEnergy,
    /// Dragon Cheer: +1 crit ratio, +2 (`counter` 1) if it started on a
    /// Dragon type.
    DragonCheer,
    /// Leech Seed; `target_loc` holds the seeder's slot (side * 2 + slot).
    LeechSeed,
    /// No healing (Psychic Noise: two turns).
    HealBlock,
    /// A substitute; `counter` is its HP.
    Substitute,
    /// Salt Cure: an eighth (Water/Steel) or a sixteenth each turn.
    SaltCure,
    /// No Retreat: can't switch out.
    NoRetreat,
    /// Charge: the next Electric move has double power.
    Charge,
    /// Bound by Infestation and the like: `counter` is the source
    /// (side << 8 | uid), `target_loc` the damage divisor (Binding Band: 6).
    PartiallyTrapped,
    /// A Gem's boost to this move.
    Gem,
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
            "encore" => VolatileId::Encore,
            "throatchop" => VolatileId::ThroatChop,
            "unburden" => VolatileId::Unburden,
            "glaiverush" => VolatileId::GlaiveRush,
            "mustrecharge" => VolatileId::MustRecharge,
            "confusion" => VolatileId::Confusion,
            "yawn" => VolatileId::Yawn,
            "taunt" => VolatileId::Taunt,
            "disable" => VolatileId::Disable,
            "roost" => VolatileId::Roost,
            "spikyshield" => VolatileId::SpikyShield,
            "kingsshield" => VolatileId::KingsShield,
            "banefulbunker" => VolatileId::BanefulBunker,
            "perishsong" => VolatileId::PerishSong,
            "imprison" => VolatileId::Imprison,
            "gem" => VolatileId::Gem,
            "focusenergy" => VolatileId::FocusEnergy,
            "dragoncheer" => VolatileId::DragonCheer,
            "leechseed" => VolatileId::LeechSeed,
            "healblock" => VolatileId::HealBlock,
            "partiallytrapped" => VolatileId::PartiallyTrapped,
            "substitute" => VolatileId::Substitute,
            "saltcure" => VolatileId::SaltCure,
            "noretreat" => VolatileId::NoRetreat,
            "charge" => VolatileId::Charge,
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
            VolatileId::Encore => "encore",
            VolatileId::ThroatChop => "throatchop",
            VolatileId::Unburden => "unburden",
            VolatileId::GlaiveRush => "glaiverush",
            VolatileId::Confusion => "confusion",
            VolatileId::Yawn => "yawn",
            VolatileId::Taunt => "taunt",
            VolatileId::Disable => "disable",
            VolatileId::Roost => "roost",
            VolatileId::SpikyShield => "spikyshield",
            VolatileId::KingsShield => "kingsshield",
            VolatileId::BanefulBunker => "banefulbunker",
            VolatileId::PerishSong => "perishsong",
            VolatileId::Imprison => "imprison",
            VolatileId::TwoTurnMove => "twoturnmove",
            VolatileId::Charging(m) => Dex::get().move_data(m).id.as_str(),
            VolatileId::MustRecharge => "mustrecharge",
            VolatileId::FlashFire => "flashfire",
            VolatileId::Gem => "gem",
            VolatileId::FocusEnergy => "focusenergy",
            VolatileId::DragonCheer => "dragoncheer",
            VolatileId::LeechSeed => "leechseed",
            VolatileId::HealBlock => "healblock",
            VolatileId::PartiallyTrapped => "partiallytrapped",
            VolatileId::Substitute => "substitute",
            VolatileId::SaltCure => "saltcure",
            VolatileId::NoRetreat => "noretreat",
            VolatileId::Charge => "charge",
        }
    }

    /// The condition's `duration`.
    /// onResidualOrder (None: no order, sorts last).
    pub fn residual_order(self) -> Option<u64> {
        match self {
            VolatileId::Encore => Some(16),
            VolatileId::ThroatChop => Some(22),
            VolatileId::Taunt => Some(15),
            VolatileId::Disable => Some(17),
            VolatileId::Yawn => Some(23),
            VolatileId::PerishSong => Some(24),
            VolatileId::LeechSeed => Some(8),
            VolatileId::HealBlock => Some(20),
            VolatileId::PartiallyTrapped | VolatileId::SaltCure => Some(13),
            VolatileId::Roost => Some(25),
            _ => None,
        }
    }

    pub fn duration(self) -> Option<u8> {
        match self {
            VolatileId::Flinch
            | VolatileId::Protect
            | VolatileId::FollowMe
            | VolatileId::RagePowder
            | VolatileId::HelpingHand
            | VolatileId::Gem => Some(1),
            VolatileId::Stall | VolatileId::ThroatChop => Some(2),
            VolatileId::Encore => Some(3),
            VolatileId::ChoiceLock
            | VolatileId::Unburden
            | VolatileId::GlaiveRush
            | VolatileId::Confusion
            | VolatileId::Imprison
            | VolatileId::Charging(_)
            | VolatileId::FlashFire
            | VolatileId::FocusEnergy
            | VolatileId::DragonCheer
            | VolatileId::LeechSeed
            | VolatileId::Substitute
            | VolatileId::SaltCure
            | VolatileId::NoRetreat
            | VolatileId::Charge => None,
            VolatileId::HealBlock => Some(2),
            // durationCallback: 5 or 6, rolled when it starts.
            VolatileId::PartiallyTrapped => Some(5),
            VolatileId::TwoTurnMove | VolatileId::MustRecharge => Some(2),
            VolatileId::Roost
            | VolatileId::SpikyShield
            | VolatileId::KingsShield
            | VolatileId::BanefulBunker => Some(1),
            VolatileId::Yawn => Some(2),
            VolatileId::Taunt => Some(3),
            VolatileId::PerishSong => Some(4),
            VolatileId::Disable => Some(5),
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
    /// A charging move's target location.
    pub target_loc: i8,
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

/// A move the Pokemon is locked into this turn.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LockedMove {
    Move(MoveId),
    Recharge,
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
    /// `baseSpecies`: the forme leaving the field returns to (Mega, Hero
    /// and Busted formes are permanent; Stance Change's aren't).
    pub base_species: SpeciesId,
    /// Disguise took a hit and busts at the next Update.
    pub disguise_busted: bool,
    /// Berserk's checkedBerserk: a healing berry may be eaten (false while
    /// a single-hit attack's AfterMoveSecondary is still to come).
    pub berserk_checked: bool,
    pub types: [TypeId; 2],
    /// Stored stats for the current species; [HP] is max HP.
    pub stats: [u16; 6],
    pub hp: u16,
    pub status: Status,
    pub status_state: StatusState,
    /// Trace is still looking for an ability to copy.
    pub trace_seek: bool,
    /// Protean / Libero already changed its type since switching in.
    pub protean_used: bool,
    /// Supreme Overlord's count of fallen allies, taken on start.
    pub fallen: u8,
    /// Supersweet Syrup already went off (once per battle).
    pub syrup_triggered: bool,
    /// Leaving by Baton Pass: the replacement copies boosts and volatiles.
    pub baton_passing: bool,
    /// Leaving by Shed Tail: the replacement gets the substitute.
    pub shed_tailing: bool,
    /// A boost raised / lowered a stat this turn.
    pub stats_raised_this_turn: bool,
    pub stats_lowered_this_turn: bool,
    /// The types to restore when Roost ends.
    pub roost_types: Option<[TypeId; 2]>,
    /// atk, def, spa, spd, spe, accuracy, evasion.
    pub boosts: [i8; 7],
    pub ability: AbilityId,
    /// `baseAbility`: what leaving the field restores (the Mega's ability
    /// after Mega Evolution).
    pub base_ability: AbilityId,
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
    /// The queued move's `originalTarget` (uid of whoever was at its target
    /// location when the action was resolved), for Stalwart.
    pub original_target: Option<(usize, usize)>,
    /// BeforeSwitchOut already ran for a pending self-switch.
    pub skip_before_switch_out: bool,
    pub active_turns: u16,
    pub active_move_actions: u16,
    pub newly_switched: bool,
    pub being_called_back: bool,
    /// The Mega forme this Pokemon can still become.
    pub can_mega_evo: Option<SpeciesId>,
    pub last_move: Option<MoveId>,
    /// `lastMoveTargetLoc`: where the last move was aimed.
    pub last_move_target_loc: i8,
    pub move_this_turn: Option<MoveId>,
    /// `moveThisTurnResult`: Showdown's `undefined` (None), `null` (Some(None):
    /// nothing happened, e.g. blocked by Protect) or whether the move worked.
    pub move_this_turn_result: Option<Option<bool>>,
    pub move_last_turn_result: Option<Option<bool>>,
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
                MoveSlot {
                    id,
                    pp,
                    max_pp: pp,
                    disabled: false,
                    imprisoned: false,
                    used: false,
                }
            })
            .collect();
        Mon {
            set: set.clone(),
            species: set.species,
            base_species: set.species,
            disguise_busted: false,
            berserk_checked: true,
            types: dex.species(set.species).types,
            stats,
            hp: stats[0],
            status: Status::None,
            status_state: StatusState::default(),
            roost_types: None,
            trace_seek: false,
            protean_used: false,
            fallen: 0,
            syrup_triggered: false,
            stats_raised_this_turn: false,
            stats_lowered_this_turn: false,
            baton_passing: false,
            shed_tailing: false,
            boosts: [0; 7],
            ability: set.ability,
            base_ability: set.ability,
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
            original_target: None,
            skip_before_switch_out: false,
            active_turns: 0,
            active_move_actions: 0,
            newly_switched: false,
            being_called_back: false,
            can_mega_evo: mega_forme(set),
            last_move: None,
            last_move_target_loc: 0,
            move_this_turn: None,
            move_this_turn_result: None,
            move_last_turn_result: None,
            times_attacked: 0,
            hurt_this_turn: None,
            speed: stats[5] as i32,
        }
    }

    /// Roost's onType: the Flying type is gone while it lasts (a pure
    /// Flying type becomes Normal).
    pub fn start_roost(&mut self, flying: TypeId, normal: TypeId) {
        let t = self.types;
        self.roost_types = Some(t);
        self.types = match (t[0] == flying, t[1] == flying) {
            (true, true) => [normal, normal],
            (true, false) => [t[1], t[1]],
            (false, true) => [t[0], t[0]],
            (false, false) => t,
        };
    }

    /// `setType`: the new types; while roosting, Flying is still left out
    /// (roost's onType filters the current types).
    pub fn set_types(&mut self, types: [TypeId; 2]) {
        self.types = types;
        if self.roost_types.is_some() {
            let dex = Dex::get();
            self.start_roost(
                dex.type_id("Flying").expect("Flying"),
                dex.type_id("Normal").expect("Normal"),
            );
        }
    }

    pub fn end_roost(&mut self) {
        if let Some(t) = self.roost_types.take() {
            self.types = t;
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
        self.end_roost();
        self.boosts = [0; 7];
        self.volatiles = Volatiles::default();
        self.last_move = None;
        self.move_this_turn = None;
        self.move_last_turn_result = None;
        self.move_this_turn_result = None;
        self.newly_switched = true;
        self.being_called_back = false;
        self.times_attacked = 0;
        self.hurt_this_turn = None;
        self.trace_seek = false;
        self.protean_used = false;
        self.fallen = 0;
        self.stats_raised_this_turn = false;
        self.stats_lowered_this_turn = false;
        self.ability = self.base_ability;
        self.switch_flag = None;
        self.force_switch_flag = false;
        // Champions: a Mega stays Mega after fainting or switching, and
        // setSpecies restores its types and stats (Soak, Speed Swap, Stance
        // Change).
        self.species = self.base_species;
        self.types = Dex::get().species(self.species).types;
        let stats = crate::stats::compute_stats(self.species, self.set.nature, self.set.points);
        self.stats = [
            self.stats[0],
            stats[1],
            stats[2],
            stats[3],
            stats[4],
            stats[5],
        ];
    }

    /// `getLockedMove`: a charged move to finish, or a turn to recharge.
    pub fn locked_move(&self) -> Option<LockedMove> {
        for v in &self.volatiles.0 {
            match v.id {
                VolatileId::TwoTurnMove => return v.move_id.map(LockedMove::Move),
                VolatileId::MustRecharge => return Some(LockedMove::Recharge),
                _ => {}
            }
        }
        None
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
    /// Each side condition's remaining duration (0: not up), by
    /// `SideCondition as usize`.
    pub conditions: [u8; SideCondition::COUNT],
    /// Each side condition's effectOrder (creation order), which orders
    /// hazards' SwitchIn handlers.
    pub condition_order: [u64; SideCondition::COUNT],
}

impl Side {
    pub fn condition(&self, c: SideCondition) -> u8 {
        self.conditions[c as usize]
    }
}

/// The side conditions the engine implements.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SideCondition {
    Reflect,
    LightScreen,
    Tailwind,
    WideGuard,
    AuroraVeil,
    QuickGuard,
    /// Entry hazards: the value is the layer count (1 for the single-layer
    /// ones); they have no duration.
    StealthRock,
    Spikes,
    ToxicSpikes,
    StickyWeb,
}

impl SideCondition {
    pub const COUNT: usize = 10;
    pub const ALL: [SideCondition; 10] = [
        SideCondition::Reflect,
        SideCondition::LightScreen,
        SideCondition::Tailwind,
        SideCondition::WideGuard,
        SideCondition::AuroraVeil,
        SideCondition::QuickGuard,
        SideCondition::StealthRock,
        SideCondition::Spikes,
        SideCondition::ToxicSpikes,
        SideCondition::StickyWeb,
    ];

    pub fn is_hazard(self) -> bool {
        matches!(
            self,
            SideCondition::StealthRock
                | SideCondition::Spikes
                | SideCondition::ToxicSpikes
                | SideCondition::StickyWeb
        )
    }

    /// Most layers a hazard stacks to.
    pub fn max_layers(self) -> u8 {
        match self {
            SideCondition::Spikes => 3,
            SideCondition::ToxicSpikes => 2,
            _ => 1,
        }
    }

    pub fn id(self) -> &'static str {
        match self {
            SideCondition::Reflect => "reflect",
            SideCondition::LightScreen => "lightscreen",
            SideCondition::Tailwind => "tailwind",
            SideCondition::WideGuard => "wideguard",
            SideCondition::AuroraVeil => "auroraveil",
            SideCondition::QuickGuard => "quickguard",
            SideCondition::StealthRock => "stealthrock",
            SideCondition::Spikes => "spikes",
            SideCondition::ToxicSpikes => "toxicspikes",
            SideCondition::StickyWeb => "stickyweb",
        }
    }

    /// onSideResidualOrder and SubOrder (no order: last; subOrder 4 is the
    /// side-condition default).
    pub fn residual_order(self) -> (u64, u8) {
        match self {
            SideCondition::Reflect => (26, 1),
            SideCondition::LightScreen => (26, 2),
            SideCondition::Tailwind => (26, 5),
            SideCondition::WideGuard | SideCondition::QuickGuard => (4_294_967_296, 4),
            // Hazards have no Residual handler (see `is_hazard`).
            SideCondition::StealthRock
            | SideCondition::Spikes
            | SideCondition::ToxicSpikes
            | SideCondition::StickyWeb => (4_294_967_296, 4),
            SideCondition::AuroraVeil => (26, 10),
        }
    }
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
