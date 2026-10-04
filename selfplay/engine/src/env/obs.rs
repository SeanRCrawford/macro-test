//! What one side sees (DESIGN.md 4.5), as fixed-size arrays for a network.
//!
//! Twelve Pokemon tokens, each side's six in team order (by uid): the
//! observer's first, then the opponent's. Each token has `INT_FIELDS`
//! dex ids (0 = none, else id + 1) for embeddings and `MON_FLOATS`
//! features. The field gets `FIELD_FLOATS` features.
//!
//! Open Team Sheets show both teams' species, moves, items and abilities.
//! What stays hidden from the observer, unless `perfect_info`: the
//! opponent's stats (stat points) and which four it brought, until each
//! one appears.

use crate::battle::choice::SideRequest;
use crate::battle::state::{Mon, SideCondition, VolatileId, ACTIVE_PER_SIDE};
use crate::battle::Battle;
use crate::damage::{Status, Terrain, Weather};
use crate::dex::{Dex, TypeId};
use crate::stats::compute_stats;

pub const TOKENS: usize = 12;
/// species, ability, item, 4 moves, 2 types.
pub const INT_FIELDS: usize = 9;
pub const MON_FLOATS: usize = 40 + VOLATILE_FLAGS + DAMAGE_FLOATS + PARTY_FLOATS;
/// For an active Pokemon: the expected damage of each of its 4 moves aimed at
/// each target location (0, 1, 2, -1, -2), as `Battle::estimate_damage`
/// gives it. Only with `perfect_info`, since it uses the targets' stats.
const DAMAGE_FLOATS: usize = 20;
pub const DAMAGE_AT: usize = 40 + VOLATILE_FLAGS;
/// One-hot party position (the index a switch choice names), for a brought
/// Pokemon the observer can see.
const PARTY_FLOATS: usize = 6;
pub const PARTY_AT: usize = DAMAGE_AT + DAMAGE_FLOATS;
pub const FIELD_FLOATS: usize = 19 + 2 * SIDE_FLOATS;
const SIDE_FLOATS: usize = SideCondition::COUNT + 8;
const VOLATILE_FLAGS: usize = 50;

/// Sizes of the id vocabularies (each id is offset by 1; 0 is padding).
pub fn vocab_sizes() -> [usize; 5] {
    let d = Dex::get();
    [
        d.species.len() + 1,
        d.abilities.len() + 1,
        d.items.len() + 1,
        d.moves.len() + 1,
        d.type_names.len() + 2,
    ]
}

/// Each volatile's feature slot (the charging move's own volatile shares
/// one).
fn volatile_slot(v: VolatileId) -> usize {
    use VolatileId as V;
    match v {
        V::Flinch => 0,
        V::Protect => 1,
        V::Stall => 2,
        V::ChoiceLock => 3,
        V::FollowMe => 4,
        V::RagePowder => 5,
        V::HelpingHand => 6,
        V::Encore => 7,
        V::ThroatChop => 8,
        V::Unburden => 9,
        V::GlaiveRush => 10,
        V::Confusion => 11,
        V::Yawn => 12,
        V::Taunt => 13,
        V::Disable => 14,
        V::Roost => 15,
        V::SpikyShield => 16,
        V::KingsShield => 17,
        V::BanefulBunker => 18,
        V::PerishSong => 19,
        V::Imprison => 20,
        V::TwoTurnMove => 21,
        V::Charging(_) => 22,
        V::MustRecharge => 23,
        V::FlashFire => 24,
        V::FocusEnergy => 25,
        V::DragonCheer => 26,
        V::LeechSeed => 27,
        V::HealBlock => 28,
        V::Substitute => 29,
        V::SaltCure => 30,
        V::NoRetreat => 31,
        V::Charge => 32,
        V::PartiallyTrapped => 33,
        V::Gem => 34,
        V::Minimize => 35,
        V::DestinyBond => 36,
        V::AllySwitch => 37,
        V::Stockpile => 38,
        V::ChillyReception => 39,
        V::Curse => 40,
        V::Endure => 41,
        V::SmackDown => 42,
    }
}

fn type_int(t: TypeId) -> i32 {
    if t == crate::damage::TYPELESS {
        Dex::get().type_names.len() as i32 + 1
    } else {
        t.0 as i32 + 1
    }
}

/// Write `side`'s view of `battle`: `ints` is TOKENS * INT_FIELDS,
/// `mons` is TOKENS * MON_FLOATS, `field` is FIELD_FLOATS.
pub fn observe(
    battle: &Battle,
    side: usize,
    perfect_info: bool,
    damage: Option<&[[[f32; 5]; 4]; 4]>,
    ints: &mut [i32],
    mons: &mut [f32],
    field: &mut [f32],
) {
    ints.fill(0);
    mons.fill(0.0);
    field.fill(0.0);
    // The damage features (perfect_info only), computed here unless given.
    let own_table;
    let table = match (perfect_info, damage) {
        (false, _) => None,
        (true, Some(t)) => Some(t),
        (true, None) => {
            own_table = battle.damage_table();
            Some(&own_table)
        }
    };
    for (t, s) in [side, 1 - side].into_iter().enumerate() {
        let own = s == side;
        for uid in 0..6 {
            let tok = t * 6 + uid;
            let int = &mut ints[tok * INT_FIELDS..(tok + 1) * INT_FIELDS];
            let f = &mut mons[tok * MON_FLOATS..(tok + 1) * MON_FLOATS];
            let Some(set) = battle.teams[s].get(uid) else {
                continue;
            };
            let mon = battle.sides[s].pokemon.iter().find(|m| m.uid == uid);
            // A Pokemon the observer hasn't seen: only the team sheet.
            let hidden = !own && !perfect_info && !mon.is_some_and(|m| m.revealed);
            match mon.filter(|_| !hidden) {
                Some(m) => {
                    mon_features(battle, m, own || perfect_info, int, f);
                    if let Some(table) = table {
                        if m.is_active && !m.fainted && m.position < ACTIVE_PER_SIDE {
                            let d = &table[s * 2 + m.position];
                            for k in 0..4 {
                                for t in 0..5 {
                                    f[DAMAGE_AT + k * 5 + t] = d[k][t];
                                }
                            }
                        }
                    }
                }
                None => sheet_features(
                    set,
                    own || perfect_info,
                    mon.is_some(),
                    own || perfect_info,
                    int,
                    f,
                ),
            }
        }
    }
    field_features(battle, side, field);
}

/// A Pokemon from its team sheet alone (not brought, or not seen yet).
fn sheet_features(
    set: &crate::team::PokemonSet,
    stats_known: bool,
    brought: bool,
    brought_known: bool,
    int: &mut [i32],
    f: &mut [f32],
) {
    let d = Dex::get();
    let sp = d.species(set.species);
    int[0] = set.species.0 as i32 + 1;
    int[1] = set.ability.0 as i32 + 1;
    int[2] = set.item.map_or(0, |i| i.0 as i32 + 1);
    for (k, m) in set.moves.iter().take(4).enumerate() {
        int[3 + k] = m.0 as i32 + 1;
    }
    int[7] = type_int(sp.types[0]);
    int[8] = type_int(sp.types[1]);
    let stats = compute_stats(set.species, set.nature, set.points);
    f[0] = 1.0; // present
    f[1] = 1.0; // full HP
    f[2] = stats[0] as f32 / 250.0;
    if stats_known {
        for k in 1..6 {
            f[2 + k] = stats[k] as f32 / 250.0;
        }
        f[8] = 1.0;
    }
    f[26] = (brought && brought_known) as u8 as f32;
    for k in 0..set.moves.len().min(4) {
        f[32 + k] = 1.0; // full PP
    }
}

fn mon_features(battle: &Battle, m: &Mon, own: bool, int: &mut [i32], f: &mut [f32]) {
    let d = Dex::get();
    int[0] = m.species.0 as i32 + 1;
    int[1] = m.ability.0 as i32 + 1;
    int[2] = m.item.map_or(0, |i| i.0 as i32 + 1);
    for (k, s) in m.moves.iter().take(4).enumerate() {
        int[3 + k] = s.id.0 as i32 + 1;
    }
    int[7] = type_int(m.types[0]);
    int[8] = type_int(m.types[1]);
    f[0] = 1.0;
    f[1] = m.hp as f32 / m.max_hp().max(1) as f32;
    f[2] = m.max_hp() as f32 / 250.0;
    if own {
        for k in 1..6 {
            f[2 + k] = m.stats[k] as f32 / 250.0;
        }
        f[8] = 1.0;
    }
    for k in 0..7 {
        f[9 + k] = m.boosts[k] as f32 / 6.0;
    }
    let status = match m.status {
        Status::None => None,
        Status::Burn => Some(0),
        Status::Paralysis => Some(1),
        Status::Poison => Some(2),
        Status::Toxic => Some(3),
        Status::Sleep => Some(4),
        Status::Freeze => Some(5),
    };
    if let Some(k) = status {
        f[16 + k] = 1.0;
    }
    f[22] = m.status_state.stage as f32 / 15.0;
    if own {
        f[23] = m.status_state.time.max(0) as f32 / 3.0;
    }
    if m.is_active && m.position < ACTIVE_PER_SIDE {
        f[24 + m.position] = 1.0;
    }
    f[26] = 1.0; // brought
    f[27] = m.revealed as u8 as f32;
    f[28] = m.fainted as u8 as f32;
    f[29] = (m.species != m.set.species && !m.transformed) as u8 as f32;
    f[30] = (own && m.can_mega_evo.is_some()) as u8 as f32;
    f[31] = m.transformed as u8 as f32;
    if m.position < PARTY_FLOATS {
        f[PARTY_AT + m.position] = 1.0;
    }
    for (k, s) in m.moves.iter().take(4).enumerate() {
        f[32 + k] = s.pp as f32 / s.max_pp.max(1) as f32;
        f[36 + k] = (own && (s.disabled || s.imprisoned)) as u8 as f32;
    }
    for v in &m.volatiles.0 {
        f[40 + volatile_slot(v.id)] = 1.0;
    }
    let _ = (battle, d);
}

fn field_features(battle: &Battle, side: usize, f: &mut [f32]) {
    let fl = &battle.field;
    let w = match fl.weather {
        Weather::None => 0,
        Weather::Sun => 1,
        Weather::Rain => 2,
        Weather::Sand => 3,
        Weather::Snow => 4,
    };
    f[w] = 1.0;
    f[5] = fl.weather_turns as f32 / 8.0;
    let t = match fl.terrain {
        Terrain::None => 0,
        Terrain::Electric => 1,
        Terrain::Grassy => 2,
        Terrain::Misty => 3,
        Terrain::Psychic => 4,
    };
    f[6 + t] = 1.0;
    f[11] = fl.terrain_turns as f32 / 8.0;
    f[12] = fl.trick_room as f32 / 5.0;
    f[13] = fl.gravity as f32 / 5.0;
    f[14] = battle.turn as f32 / 30.0;
    let request = match battle.requests[side] {
        _ if battle.is_over() => 0,
        SideRequest::Wait => 0,
        SideRequest::TeamPreview => 1,
        SideRequest::Move(_) => 2,
        SideRequest::Switch(_) => 3,
    };
    f[15 + request] = 1.0;
    for (k, s) in [side, 1 - side].into_iter().enumerate() {
        let o = 19 + k * SIDE_FLOATS;
        let sd = &battle.sides[s];
        for (i, c) in SideCondition::ALL.iter().enumerate() {
            let v = sd.condition(*c) as f32;
            f[o + i] = if c.is_hazard() { v / 3.0 } else { v / 8.0 };
        }
        let o = o + SideCondition::COUNT;
        f[o] = sd.total_fainted as f32 / 4.0;
        f[o + 1] = sd.pokemon_left as f32 / 6.0;
        f[o + 2] = battle.mega_used(s) as u8 as f32;
        f[o + 3] = sd.fainted_last_turn as u8 as f32;
        for p in 0..ACTIVE_PER_SIDE {
            f[o + 4 + p] = sd.revival_blessing[p] as u8 as f32;
            f[o + 6 + p] = sd.healing_wish[p] as u8 as f32;
        }
    }
}
