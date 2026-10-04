//! Static game data: species, moves, items, abilities, conditions, natures and
//! the type chart.
//!
//! Built once from `data/dex.json` (exported from Showdown's champions mod by
//! `tools/showdown/export_dex.js`) and embedded in the binary, so the engine
//! has no runtime file, Node or Python dependency. Battle code refers to
//! entries by small integer ids; string lookups happen only at the boundary.
//!
//! Showdown implements most effects as event handlers (functions), which
//! can't be exported. Each move, item, ability and condition instead keeps the
//! NAMES of its handlers (`handlers`) and their numeric ordering fields
//! (`onBasePowerPriority` and so on, in `hooks`). The engine implements
//! handlers by id and uses the names to tell when an effect it hasn't
//! implemented would matter.

use serde::Deserialize;
use serde_json::Value;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

/// FxHash (rustc's hasher): the dex's string lookups sit on the battle's
/// hot path, where SipHash's DoS resistance buys nothing.
#[derive(Default, Clone, Copy)]
pub struct FxHasher {
    hash: u64,
}

impl Hasher for FxHasher {
    fn write(&mut self, bytes: &[u8]) {
        const K: u64 = 0x51_7c_c1_b7_27_22_0a_95;
        let mut chunks = bytes.chunks_exact(8);
        for c in &mut chunks {
            let w = u64::from_le_bytes(c.try_into().unwrap());
            self.hash = (self.hash.rotate_left(5) ^ w).wrapping_mul(K);
        }
        for &b in chunks.remainder() {
            self.hash = (self.hash.rotate_left(5) ^ b as u64).wrapping_mul(K);
        }
    }
    fn write_u8(&mut self, b: u8) {
        self.write(&[b]);
    }
    fn finish(&self) -> u64 {
        self.hash
    }
}

pub type FastMap<K, V> = HashMap<K, V, BuildHasherDefault<FxHasher>>;
use std::sync::OnceLock;

const DEX_JSON: &str = include_str!("../../data/dex.json");

/// Stat order used everywhere in the engine: HP, Atk, Def, SpA, SpD, Spe.
pub const STAT_NAMES: [&str; 6] = ["hp", "atk", "def", "spa", "spd", "spe"];
/// Boost stages, in `Mon::boosts` order.
pub const BOOST_NAMES: [&str; 7] = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"];
pub const HP: usize = 0;
pub const ATK: usize = 1;
pub const DEF: usize = 2;
pub const SPA: usize = 3;
pub const SPD: usize = 4;
pub const SPE: usize = 5;

macro_rules! id_type {
    ($name:ident, $int:ty) => {
        #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, PartialOrd, Ord)]
        pub struct $name(pub $int);
    };
}
id_type!(SpeciesId, u16);
id_type!(MoveId, u16);
id_type!(ItemId, u16);
id_type!(AbilityId, u16);
id_type!(ConditionId, u16);
id_type!(NatureId, u8);
id_type!(TypeId, u8);

/// Names and numeric ordering fields of an effect's event handlers.
#[derive(Debug, Default, Clone)]
pub struct Handlers {
    /// e.g. "onBasePower", "basePowerCallback", "condition.onSourceModifyDamage".
    pub names: Vec<String>,
    /// e.g. "onBasePowerPriority" -> 15. Absent means Showdown's default (0 or none).
    pub hooks: FastMap<String, i32>,
    /// Each handler's sort keys, looked up once by name (the damage
    /// module's hot path).
    pub info: FastMap<String, HookInfo>,
}

/// A handler's Order, Priority and SubOrder fields.
#[derive(Debug, Clone, Copy, Default)]
pub struct HookInfo {
    pub order: Option<i32>,
    pub priority: i32,
    pub sub_order: i32,
}

impl Handlers {
    pub fn has(&self, name: &str) -> bool {
        self.info.contains_key(name)
    }
    pub fn hook(&self, name: &str) -> i32 {
        self.hooks.get(name).copied().unwrap_or(0)
    }
}

#[derive(Debug)]
pub struct Species {
    pub id: String,
    pub name: String,
    pub num: i32,
    pub types: [TypeId; 2], // a mono-type species repeats its type
    pub base_stats: [u16; 6],
    pub abilities: Vec<String>,
    /// Weight in hectograms, as Showdown stores it (weightkg * 10).
    pub weight_hg: u32,
    pub base_species: String,
    pub forme: Option<String>,
    pub required_item: Option<String>,
    /// Showdown's `isNonstandard` ("Past", "Future", "CAP"...); None is legal.
    pub nonstandard: Option<String>,
    /// e.g. "Mythical", "Restricted Legendary".
    pub tags: Vec<String>,
    /// Formes that exist only in battle (Megas): the forme a team must use instead.
    pub battle_only: Option<String>,
    /// Moves it can legally have in this format, sorted. Empty when illegal.
    pub learnset: Vec<MoveId>,
}

impl Species {
    pub fn is_mono_type(&self) -> bool {
        self.types[0] == self.types[1]
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Category {
    Physical,
    Special,
    Status,
}

/// Showdown's move target field.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MoveTarget {
    Normal,
    SelfTarget,
    AdjacentAlly,
    AdjacentAllyOrSelf,
    AdjacentFoe,
    AllAdjacentFoes,
    AllAdjacent,
    All,
    AllySide,
    FoeSide,
    AllyTeam,
    Allies,
    Any,
    RandomNormal,
    Scripted,
}

impl MoveTarget {
    fn parse(s: &str) -> Result<Self, String> {
        Ok(match s {
            "normal" => Self::Normal,
            "self" => Self::SelfTarget,
            "adjacentAlly" => Self::AdjacentAlly,
            "adjacentAllyOrSelf" => Self::AdjacentAllyOrSelf,
            "adjacentFoe" => Self::AdjacentFoe,
            "allAdjacentFoes" => Self::AllAdjacentFoes,
            "allAdjacent" => Self::AllAdjacent,
            "all" => Self::All,
            "allySide" => Self::AllySide,
            "foeSide" => Self::FoeSide,
            "allyTeam" => Self::AllyTeam,
            "allies" => Self::Allies,
            "any" => Self::Any,
            "randomNormal" => Self::RandomNormal,
            "scripted" => Self::Scripted,
            other => return Err(format!("unknown move target {other:?}")),
        })
    }

    /// Hits every adjacent foe (and for `AllAdjacent`, the ally too).
    pub fn is_spread(self) -> bool {
        matches!(self, Self::AllAdjacentFoes | Self::AllAdjacent)
    }
}

/// Move flags as a bit set. Bit positions follow `FLAG_NAMES`.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct MoveFlags(pub u64);

pub const FLAG_NAMES: [&str; 38] = [
    "allyanim",
    "bite",
    "bullet",
    "bypasssub",
    "cantusetwice",
    "charge",
    "contact",
    "dance",
    "defrost",
    "distance",
    "failcopycat",
    "failencore",
    "failinstruct",
    "failmefirst",
    "failmimic",
    "futuremove",
    "gravity",
    "heal",
    "metronome",
    "minimize",
    "mirror",
    "mustpressure",
    "noassist",
    "nonsky",
    "noparentalbond",
    "nosketch",
    "nosleeptalk",
    "pledgecombo",
    "powder",
    "protect",
    "pulse",
    "punch",
    "recharge",
    "reflectable",
    "slicing",
    "snatch",
    "sound",
    "wind",
];

impl MoveFlags {
    pub fn bit(name: &str) -> Option<u64> {
        FLAG_NAMES
            .iter()
            .position(|f| *f == name)
            .map(|i| 1u64 << i)
    }
    pub fn has(self, name: &str) -> bool {
        Self::bit(name).is_some_and(|b| self.0 & b != 0)
    }
}

/// Showdown's `ignoreImmunity`: true ignores every type immunity; an object
/// names the move types whose immunity it ignores.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum IgnoreImmunity {
    #[default]
    No,
    All,
    /// Bit per TypeId.
    Types(u32),
}

/// Showdown's HitEffect: what a move (or one of its secondaries, or its
/// `self` part) does to a target besides damage.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct HitEffect {
    /// Percent chance, for secondaries (None: always).
    pub chance: Option<u8>,
    /// (index into BOOST_NAMES, stages), in data order.
    pub boosts: Vec<(usize, i8)>,
    /// A status id: brn, par, psn, tox, slp, frz.
    pub status: Option<String>,
    pub volatile_status: Option<String>,
    pub self_effect: Option<Box<HitEffect>>,
    /// Every key of the effect's data, sorted (for `support`).
    pub keys: Vec<String>,
}

impl HitEffect {
    fn parse(r: &serde_json::Map<String, Value>) -> Result<HitEffect, String> {
        let mut boosts = Vec::new();
        if let Some(b) = r.get("boosts").and_then(Value::as_object) {
            // serde_json keeps object keys sorted, not in data order; Showdown
            // applies boosts in data order, which only matters for effects that
            // react to each boost (none supported yet). Use BOOST_NAMES order.
            for (i, name) in BOOST_NAMES.iter().enumerate() {
                if let Some(n) = b.get(*name).and_then(Value::as_i64) {
                    boosts.push((i, n as i8));
                }
            }
            if b.len() != boosts.len() {
                return Err(format!("unknown boost in {b:?}"));
            }
        }
        let self_effect = match r.get("self").and_then(Value::as_object) {
            Some(o) => Some(Box::new(HitEffect::parse(o)?)),
            None => None,
        };
        let mut keys: Vec<String> = r.keys().cloned().collect();
        keys.sort();
        Ok(HitEffect {
            chance: r
                .get("chance")
                .and_then(Value::as_u64)
                .map(|c| c.min(255) as u8),
            boosts,
            status: r.get("status").and_then(Value::as_str).map(String::from),
            volatile_status: r
                .get("volatileStatus")
                .and_then(Value::as_str)
                .map(String::from),
            self_effect,
            keys,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FixedDamage {
    Level,
    Amount(u16),
}

#[derive(Debug)]
pub struct MoveData {
    pub id: String,
    pub name: String,
    pub num: i32,
    pub move_type: TypeId,
    pub category: Category,
    pub base_power: u16,
    /// None: never misses.
    pub accuracy: Option<u8>,
    pub priority: i8,
    pub target: MoveTarget,
    pub flags: MoveFlags,
    pub has_secondaries: bool,
    pub has_recoil: bool,
    /// The move's own hit effect: `boosts`, `status`, `volatileStatus` and
    /// `self` at the top level of its data.
    pub primary: HitEffect,
    /// `secondary`/`secondaries`, each with its chance.
    pub secondaries: Vec<HitEffect>,
    /// `recoil`, `drain`, `heal`: fractions (numerator, denominator).
    pub recoil: Option<(u32, u32)>,
    pub drain: Option<(u32, u32)>,
    pub heal: Option<(u32, u32)>,
    pub has_crash_damage: bool,
    pub crit_ratio: u8,
    pub will_crit: Option<bool>,
    pub multihit: Option<(u8, u8)>,
    pub override_offensive_stat: Option<usize>,
    pub override_defensive_stat: Option<usize>,
    /// Foul Play: use the target's attacking stat.
    pub override_offensive_target: bool,
    pub ignore_defensive: bool,
    pub ignore_offensive: bool,
    pub ignore_ability: bool,
    pub ignore_immunity: IgnoreImmunity,
    pub fixed_damage: Option<FixedDamage>,
    pub ohko: bool,
    pub nonstandard: Option<String>,
    /// Base PP after the champions mod's cap of 20.
    pub pp: u8,
    pub no_pp_boosts: bool,
    /// Every data key the move has (sorted), so the engine can tell which
    /// effects (secondary, self, boosts, status, drain...) it would need.
    pub keys: Vec<String>,
    pub handlers: Handlers,
    /// The move's own condition (Reflect's screen, Helping Hand's boost...).
    pub condition: Handlers,
    /// Handlers on nested data other than `condition` ("secondary.onHit").
    pub nested_handlers: Vec<String>,
    /// `selfSwitch: true` (U-turn); Baton Pass's "copyvolatile" and the like
    /// leave it false.
    pub self_switch: bool,
    /// `selfBoost` (Clanging Scales): applied to the user after the move.
    pub self_boost: Option<HitEffect>,
}

impl MoveData {
    pub fn has_key(&self, key: &str) -> bool {
        self.keys.binary_search_by(|k| k.as_str().cmp(key)).is_ok()
    }

    /// PP in battle: the champions mod's `calculatePP`, (pp / 5 + 1) * 4
    /// (always "3 PP Ups"), unless the move can't be boosted.
    pub fn max_pp(&self) -> u8 {
        if self.no_pp_boosts {
            self.pp
        } else {
            (self.pp / 5 + 1) * 4
        }
    }
}

#[derive(Debug)]
pub struct ItemData {
    pub id: String,
    pub name: String,
    pub num: i32,
    pub is_berry: bool,
    /// `onTakeItem: false`: Knock Off and similar can't remove it.
    pub take_forbidden: bool,
    /// Still works for a Klutz holder.
    pub ignore_klutz: bool,
    /// Base species name -> Mega forme name, for Mega Stones.
    pub mega_stone: FastMap<String, String>,
    pub nonstandard: Option<String>,
    pub handlers: Handlers,
}

#[derive(Debug)]
pub struct AbilityData {
    pub id: String,
    pub name: String,
    pub num: i32,
    /// Mold Breaker and similar ignore it.
    pub breakable: bool,
    /// Cloud Nine / Air Lock.
    pub suppress_weather: bool,
    /// `onCriticalHit: false` (Battle Armor, Shell Armor).
    pub blocks_crit: bool,
    pub nonstandard: Option<String>,
    pub handlers: Handlers,
    /// The volatile the ability creates (Flash Fire's boost).
    pub condition: Handlers,
    /// Flag names (breakable, notrace, failroleplay...).
    pub flags: Vec<String>,
}

#[derive(Debug)]
pub struct ConditionData {
    pub id: String,
    pub name: String,
    pub handlers: Handlers,
}

#[derive(Debug, Clone, Copy)]
pub struct Nature {
    pub plus: Option<usize>, // index into STAT_NAMES, never 0 (HP)
    pub minus: Option<usize>,
}

impl Nature {
    /// Nature multiplier for stat `i` as a percentage: 110, 100 or 90.
    pub fn percent(&self, i: usize) -> u16 {
        if self.plus == self.minus {
            100 // neutral natures (Hardy, Docile, ...) list no plus or minus
        } else if self.plus == Some(i) {
            110
        } else if self.minus == Some(i) {
            90
        } else {
            100
        }
    }
}

pub struct Dex {
    pub source: String,
    pub type_names: Vec<String>,
    /// effectiveness[attacking][defending]: 0 immune, 1 resist, 2 neutral, 4 super.
    /// Stored as multiples of 1/2 so products over two defending types stay integral.
    pub effectiveness: Vec<Vec<u8>>,
    /// Per type: the non-type keys it's immune to (brn, powder, sandstorm...).
    pub immunities: Vec<Vec<String>>,
    pub species: Vec<Species>,
    pub moves: Vec<MoveData>,
    pub items: Vec<ItemData>,
    pub abilities: Vec<AbilityData>,
    pub conditions: Vec<ConditionData>,
    pub natures: Vec<Nature>,
    pub nature_names: Vec<String>,
    species_index: FastMap<String, SpeciesId>,
    move_index: FastMap<String, MoveId>,
    item_index: FastMap<String, ItemId>,
    ability_index: FastMap<String, AbilityId>,
    condition_index: FastMap<String, ConditionId>,
    nature_index: FastMap<String, NatureId>,
    type_index: FastMap<String, TypeId>,
}

#[derive(Deserialize)]
struct RawDex {
    source: String,
    types: Vec<String>,
    typechart: HashMap<String, HashMap<String, u8>>,
    immunities: HashMap<String, Vec<String>>,
    natures: HashMap<String, RawNature>,
    species: HashMap<String, RawSpecies>,
    moves: HashMap<String, HashMap<String, Value>>,
    items: HashMap<String, HashMap<String, Value>>,
    abilities: HashMap<String, HashMap<String, Value>>,
    conditions: HashMap<String, HashMap<String, Value>>,
}

#[derive(Deserialize)]
struct RawNature {
    plus: Option<String>,
    minus: Option<String>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct RawSpecies {
    name: String,
    num: i32,
    types: Vec<String>,
    base_stats: [u16; 6],
    abilities: HashMap<String, String>,
    weightkg: f64,
    base_species: Option<String>,
    forme: Option<String>,
    required_item: Option<String>,
    is_nonstandard: Option<String>,
    #[serde(default)]
    tags: Vec<String>,
    battle_only: Option<Value>,
    #[serde(default)]
    learnset: Vec<String>,
}

/// Showdown's id format: lowercase ASCII letters and digits only.
pub fn to_id(name: &str) -> String {
    name.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_lowercase())
        .collect()
}

fn stat_index(name: &str) -> Result<usize, String> {
    STAT_NAMES
        .iter()
        .position(|s| *s == name)
        .ok_or_else(|| format!("unknown stat {name:?}"))
}

/// Handler names and Priority/Order/SubOrder fields from a raw entry. With a
/// prefix ("condition."), reads only that nested object's names.
fn handlers_of(raw: &HashMap<String, Value>, nested: Option<&str>) -> Handlers {
    let mut h = Handlers::default();
    let all: Vec<String> = raw
        .get("handlers")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect()
        })
        .unwrap_or_default();
    let hooks_src = match nested {
        Some(key) => raw
            .get(key)
            .and_then(Value::as_object)
            .cloned()
            .unwrap_or_default(),
        None => raw.iter().map(|(k, v)| (k.clone(), v.clone())).collect(),
    };
    h.names = match nested {
        Some(key) => {
            let prefix = format!("{key}.");
            all.iter()
                .filter_map(|n| n.strip_prefix(&prefix).map(String::from))
                .collect()
        }
        None => all.into_iter().filter(|n| !n.contains('.')).collect(),
    };
    for (k, v) in hooks_src {
        if k.ends_with("Priority") || k.ends_with("Order") {
            if let Some(n) = v.as_i64() {
                h.hooks.insert(k, n as i32);
            }
        }
    }
    for n in &h.names {
        let info = HookInfo {
            order: h.hooks.get(&format!("{n}Order")).copied(),
            priority: h.hook(&format!("{n}Priority")),
            sub_order: h.hook(&format!("{n}SubOrder")),
        };
        h.info.insert(n.clone(), info);
    }
    h
}

/// A `[numerator, denominator]` field.
fn fraction(raw: &HashMap<String, Value>, key: &str) -> Result<Option<(u32, u32)>, String> {
    match raw.get(key) {
        None => Ok(None),
        Some(Value::Array(a)) if a.len() == 2 => match (a[0].as_u64(), a[1].as_u64()) {
            (Some(n), Some(d)) if d > 0 => Ok(Some((n as u32, d as u32))),
            _ => Err(format!("{key} {a:?}")),
        },
        Some(other) => Err(format!("{key} {other}")),
    }
}

fn str_field<'a>(raw: &'a HashMap<String, Value>, key: &str) -> Option<&'a str> {
    raw.get(key).and_then(Value::as_str)
}

fn num_field(raw: &HashMap<String, Value>, key: &str) -> Option<i64> {
    raw.get(key).and_then(Value::as_i64)
}

fn bool_field(raw: &HashMap<String, Value>, key: &str) -> bool {
    raw.get(key).and_then(Value::as_bool).unwrap_or(false)
}

/// Sorted keys, so ids are stable across runs (HashMap order is not).
fn sorted_keys<V>(map: &HashMap<String, V>) -> Vec<&String> {
    let mut keys: Vec<&String> = map.keys().collect();
    keys.sort();
    keys
}

impl Dex {
    /// The process-wide dex, parsed from the embedded JSON on first use.
    pub fn get() -> &'static Dex {
        static DEX: OnceLock<Dex> = OnceLock::new();
        DEX.get_or_init(|| Dex::from_json(DEX_JSON).expect("embedded dex.json is invalid"))
    }

    pub fn from_json(text: &str) -> Result<Dex, String> {
        let raw: RawDex = serde_json::from_str(text).map_err(|e| e.to_string())?;

        let type_index: FastMap<String, TypeId> = raw
            .types
            .iter()
            .enumerate()
            .map(|(i, t)| (t.clone(), TypeId(i as u8)))
            .collect();
        let lookup_type = |t: &str| {
            type_index
                .get(t)
                .copied()
                .ok_or_else(|| format!("unknown type {t}"))
        };
        let n = raw.types.len();
        let mut effectiveness = vec![vec![2u8; n]; n];
        for (defending, row) in &raw.typechart {
            let d = lookup_type(defending)?.0 as usize;
            for (attacking, code) in row {
                let a = lookup_type(attacking)?.0 as usize;
                effectiveness[a][d] = match code {
                    0 => 2,
                    1 => 4,
                    2 => 1,
                    3 => 0,
                    other => return Err(format!("typechart: unknown code {other}")),
                };
            }
        }

        let mut species = Vec::new();
        let mut species_index = FastMap::default();
        for (i, sid) in sorted_keys(&raw.species).into_iter().enumerate() {
            let r = &raw.species[sid];
            let t = r
                .types
                .iter()
                .map(|t| lookup_type(t).map_err(|e| format!("{sid}: {e}")))
                .collect::<Result<Vec<TypeId>, String>>()?;
            let types = match t.as_slice() {
                [a] => [*a, *a],
                [a, b] => [*a, *b],
                _ => return Err(format!("{sid}: {} types", t.len())),
            };
            let mut slots: Vec<(&String, &String)> = r.abilities.iter().collect();
            slots.sort(); // "0", "1", "H", "S"
            species.push(Species {
                id: sid.clone(),
                name: r.name.clone(),
                num: r.num,
                types,
                base_stats: r.base_stats,
                abilities: slots.into_iter().map(|(_, a)| a.clone()).collect(),
                weight_hg: (r.weightkg * 10.0).round() as u32,
                base_species: r.base_species.clone().unwrap_or_else(|| r.name.clone()),
                forme: r.forme.clone(),
                required_item: r.required_item.clone(),
                nonstandard: r.is_nonstandard.clone(),
                tags: r.tags.clone(),
                battle_only: r.battle_only.as_ref().and_then(|v| match v {
                    Value::String(s) => Some(s.clone()),
                    Value::Array(a) => a.first().and_then(Value::as_str).map(String::from),
                    _ => None,
                }),
                learnset: Vec::new(), // filled once moves are indexed
            });
            species_index.insert(sid.clone(), SpeciesId(i as u16));
        }

        let mut moves = Vec::new();
        let mut move_index = FastMap::default();
        for (i, mid) in sorted_keys(&raw.moves).into_iter().enumerate() {
            let r = &raw.moves[mid];
            let ctx = |e: String| format!("move {mid}: {e}");
            let mut flags = 0u64;
            if let Some(obj) = r.get("flags").and_then(Value::as_object) {
                for name in obj.keys() {
                    flags |=
                        MoveFlags::bit(name).ok_or_else(|| ctx(format!("unknown flag {name}")))?;
                }
            }
            let category = match str_field(r, "category") {
                Some("Physical") => Category::Physical,
                Some("Special") => Category::Special,
                Some("Status") => Category::Status,
                other => return Err(ctx(format!("category {other:?}"))),
            };
            let multihit = match r.get("multihit") {
                None => None,
                Some(Value::Number(n)) => {
                    let n = n.as_u64().unwrap_or(1) as u8;
                    Some((n, n))
                }
                Some(Value::Array(a)) if a.len() == 2 => Some((
                    a[0].as_u64().unwrap_or(1) as u8,
                    a[1].as_u64().unwrap_or(1) as u8,
                )),
                Some(other) => return Err(ctx(format!("multihit {other}"))),
            };
            let ignore_immunity = match r.get("ignoreImmunity") {
                // hitStepTypeImmunity: unset means "ignore" for status moves.
                None if category == Category::Status => IgnoreImmunity::All,
                None | Some(Value::Bool(false)) => IgnoreImmunity::No,
                Some(Value::Bool(true)) => IgnoreImmunity::All,
                Some(Value::Object(o)) => {
                    let mut bits = 0u32;
                    for t in o.keys() {
                        bits |= 1 << lookup_type(t).map_err(ctx)?.0;
                    }
                    IgnoreImmunity::Types(bits)
                }
                Some(other) => return Err(ctx(format!("ignoreImmunity {other}"))),
            };
            let fixed_damage = match r.get("damage") {
                None => None,
                Some(Value::String(s)) if s == "level" => Some(FixedDamage::Level),
                Some(Value::Number(n)) => Some(FixedDamage::Amount(n.as_u64().unwrap_or(0) as u16)),
                Some(other) => return Err(ctx(format!("damage {other}"))),
            };
            let stat = |key: &str| str_field(r, key).map(stat_index).transpose().map_err(ctx);
            moves.push(MoveData {
                id: mid.clone(),
                name: str_field(r, "name").unwrap_or(mid).to_string(),
                num: num_field(r, "num").unwrap_or(0) as i32,
                move_type: lookup_type(str_field(r, "type").unwrap_or("")).map_err(ctx)?,
                category,
                base_power: num_field(r, "basePower").unwrap_or(0) as u16,
                accuracy: num_field(r, "accuracy").map(|a| a as u8),
                priority: num_field(r, "priority").unwrap_or(0) as i8,
                target: MoveTarget::parse(str_field(r, "target").unwrap_or("")).map_err(ctx)?,
                flags: MoveFlags(flags),
                has_secondaries: r.get("secondary").is_some_and(|v| !v.is_null())
                    || r.get("secondaries")
                        .and_then(Value::as_array)
                        .is_some_and(|a| !a.is_empty()),
                has_recoil: r.contains_key("recoil"),
                primary: HitEffect::parse(&r.iter().map(|(k, v)| (k.clone(), v.clone())).collect())
                    .map_err(ctx)?,
                secondaries: {
                    let list: Vec<Value> = match (r.get("secondary"), r.get("secondaries")) {
                        (_, Some(Value::Array(a))) => a.clone(),
                        (Some(v @ Value::Object(_)), _) => vec![v.clone()],
                        _ => Vec::new(),
                    };
                    list.iter()
                        .map(|v| HitEffect::parse(v.as_object().expect("secondary object")))
                        .collect::<Result<_, _>>()
                        .map_err(ctx)?
                },
                recoil: fraction(r, "recoil").map_err(ctx)?,
                drain: fraction(r, "drain").map_err(ctx)?,
                heal: fraction(r, "heal").map_err(ctx)?,
                has_crash_damage: bool_field(r, "hasCrashDamage"),
                crit_ratio: num_field(r, "critRatio").unwrap_or(1) as u8,
                will_crit: r.get("willCrit").and_then(Value::as_bool),
                multihit,
                override_offensive_stat: stat("overrideOffensiveStat")?,
                override_defensive_stat: stat("overrideDefensiveStat")?,
                override_offensive_target: str_field(r, "overrideOffensivePokemon")
                    == Some("target"),
                ignore_defensive: bool_field(r, "ignoreDefensive"),
                ignore_offensive: bool_field(r, "ignoreOffensive"),
                ignore_ability: bool_field(r, "ignoreAbility"),
                ignore_immunity,
                fixed_damage,
                ohko: r.get("ohko").is_some_and(|v| v.as_bool() != Some(false)),
                nonstandard: str_field(r, "isNonstandard").map(String::from),
                pp: num_field(r, "pp").unwrap_or(0) as u8,
                no_pp_boosts: bool_field(r, "noPPBoosts"),
                keys: {
                    let mut k: Vec<String> = r.keys().cloned().collect();
                    k.sort();
                    k
                },
                handlers: handlers_of(r, None),
                condition: handlers_of(r, Some("condition")),
                // true, or Baton Pass's "copyvolatile" (not Shed Tail's).
                self_switch: matches!(r.get("selfSwitch"), Some(Value::Bool(true)))
                    || matches!(
                        r.get("selfSwitch").and_then(|v| v.as_str()),
                        Some("copyvolatile" | "shedtail")
                    ),
                self_boost: match r.get("selfBoost").and_then(Value::as_object) {
                    Some(o) => Some(HitEffect::parse(o).map_err(ctx)?),
                    None => None,
                },
                nested_handlers: r
                    .get("handlers")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .filter(|n| n.contains('.') && !n.starts_with("condition."))
                            .map(String::from)
                            .collect()
                    })
                    .unwrap_or_default(),
            });
            move_index.insert(mid.clone(), MoveId(i as u16));
        }

        for (sid, r) in &raw.species {
            let id = species_index[sid].0 as usize;
            let mut learnset = r
                .learnset
                .iter()
                .map(|m| {
                    move_index
                        .get(m)
                        .copied()
                        .ok_or_else(|| format!("{sid}: learnset move {m}"))
                })
                .collect::<Result<Vec<MoveId>, String>>()?;
            learnset.sort();
            species[id].learnset = learnset;
        }

        let mut items = Vec::new();
        let mut item_index = FastMap::default();
        for (i, iid) in sorted_keys(&raw.items).into_iter().enumerate() {
            let r = &raw.items[iid];
            let mega_stone = r
                .get("megaStone")
                .and_then(Value::as_object)
                .map(|o| {
                    o.iter()
                        .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_string()))
                        .collect()
                })
                .unwrap_or_default();
            items.push(ItemData {
                id: iid.clone(),
                name: str_field(r, "name").unwrap_or(iid).to_string(),
                num: num_field(r, "num").unwrap_or(0) as i32,
                is_berry: bool_field(r, "isBerry"),
                take_forbidden: r.get("onTakeItem").and_then(Value::as_bool) == Some(false),
                ignore_klutz: bool_field(r, "ignoreKlutz"),
                mega_stone,
                nonstandard: str_field(r, "isNonstandard").map(String::from),
                handlers: handlers_of(r, None),
            });
            item_index.insert(iid.clone(), ItemId(i as u16));
        }

        let mut abilities = Vec::new();
        let mut ability_index = FastMap::default();
        for (i, aid) in sorted_keys(&raw.abilities).into_iter().enumerate() {
            let r = &raw.abilities[aid];
            let breakable = r
                .get("flags")
                .and_then(Value::as_object)
                .is_some_and(|f| f.get("breakable").and_then(Value::as_i64) == Some(1));
            abilities.push(AbilityData {
                id: aid.clone(),
                name: str_field(r, "name").unwrap_or(aid).to_string(),
                num: num_field(r, "num").unwrap_or(0) as i32,
                breakable,
                suppress_weather: bool_field(r, "suppressWeather"),
                blocks_crit: r.get("onCriticalHit").and_then(Value::as_bool) == Some(false),
                nonstandard: str_field(r, "isNonstandard").map(String::from),
                handlers: handlers_of(r, None),
                condition: handlers_of(r, Some("condition")),
                flags: r
                    .get("flags")
                    .and_then(Value::as_object)
                    .map(|f| {
                        f.iter()
                            .filter(|(_, v)| v.as_i64() == Some(1))
                            .map(|(k, _)| k.clone())
                            .collect()
                    })
                    .unwrap_or_default(),
            });
            ability_index.insert(aid.clone(), AbilityId(i as u16));
        }

        let mut conditions = Vec::new();
        let mut condition_index = FastMap::default();
        for (i, cid) in sorted_keys(&raw.conditions).into_iter().enumerate() {
            let r = &raw.conditions[cid];
            conditions.push(ConditionData {
                id: cid.clone(),
                name: str_field(r, "name").unwrap_or(cid).to_string(),
                handlers: handlers_of(r, None),
            });
            condition_index.insert(cid.clone(), ConditionId(i as u16));
        }

        let mut nature_names: Vec<String> = raw.natures.keys().cloned().collect();
        nature_names.sort();
        let mut natures = Vec::new();
        let mut nature_index = FastMap::default();
        for (i, nid) in nature_names.iter().enumerate() {
            let r = &raw.natures[nid];
            natures.push(Nature {
                plus: r.plus.as_deref().map(stat_index).transpose()?,
                minus: r.minus.as_deref().map(stat_index).transpose()?,
            });
            nature_index.insert(nid.clone(), NatureId(i as u8));
        }

        Ok(Dex {
            source: raw.source,
            immunities: raw
                .types
                .iter()
                .map(|t| raw.immunities.get(t).cloned().unwrap_or_default())
                .collect(),
            type_names: raw.types,
            effectiveness,
            species,
            moves,
            items,
            abilities,
            conditions,
            natures,
            nature_names,
            species_index,
            move_index,
            item_index,
            ability_index,
            condition_index,
            nature_index,
            type_index,
        })
    }

    /// `dex.getImmunity(key, types)` for a non-type key (a status, weather or
    /// "powder"): false when any of the types is immune.
    pub fn immune_to(&self, key: &str, types: [TypeId; 2]) -> bool {
        // The typeless "???" type (TypeId(255)) has no immunities.
        types.iter().any(|t| {
            self.immunities
                .get(t.0 as usize)
                .is_some_and(|im| im.iter().any(|k| k == key))
        })
    }

    /// A Mega Stone's onTakeItem: it stays with a Pokemon that can use it.
    /// Most stones check the holder's base species; Floettite and Meowsticite
    /// check its exact forme against the stone's formes. Either way:
    pub fn mega_stone_stays(&self, item: ItemId, holder: SpeciesId) -> bool {
        let stone = &self.item(item).mega_stone;
        let sp = self.species(holder);
        stone.contains_key(&sp.base_species)
            || stone.contains_key(&sp.name)
            || stone.values().any(|v| *v == sp.name)
    }

    pub fn species_id(&self, name: &str) -> Option<SpeciesId> {
        self.species_index.get(&to_id(name)).copied()
    }
    pub fn species(&self, id: SpeciesId) -> &Species {
        &self.species[id.0 as usize]
    }
    pub fn move_id(&self, name: &str) -> Option<MoveId> {
        self.move_index.get(&to_id(name)).copied()
    }
    pub fn move_data(&self, id: MoveId) -> &MoveData {
        &self.moves[id.0 as usize]
    }
    pub fn item_id(&self, name: &str) -> Option<ItemId> {
        self.item_index.get(&to_id(name)).copied()
    }
    pub fn item(&self, id: ItemId) -> &ItemData {
        &self.items[id.0 as usize]
    }
    pub fn ability_id(&self, name: &str) -> Option<AbilityId> {
        self.ability_index.get(&to_id(name)).copied()
    }
    pub fn ability(&self, id: AbilityId) -> &AbilityData {
        &self.abilities[id.0 as usize]
    }
    pub fn condition_id(&self, name: &str) -> Option<ConditionId> {
        self.condition_index.get(&to_id(name)).copied()
    }
    pub fn condition(&self, id: ConditionId) -> &ConditionData {
        &self.conditions[id.0 as usize]
    }
    pub fn nature_id(&self, name: &str) -> Option<NatureId> {
        self.nature_index.get(&to_id(name)).copied()
    }
    pub fn nature(&self, id: NatureId) -> &Nature {
        &self.natures[id.0 as usize]
    }

    pub fn type_id(&self, name: &str) -> Option<TypeId> {
        // Type names are stored capitalised ("Fire"); accept any case.
        let mut c = name.to_ascii_lowercase();
        if let Some(f) = c.get_mut(0..1) {
            f.make_ascii_uppercase();
        }
        self.type_index.get(&c).copied()
    }

    /// Effectiveness of an `attacking`-type move against a defender with
    /// `defending` types, in quarters: 0, 1 (x0.25), 2 (x0.5), 4, 8, 16 (x4).
    pub fn effectiveness_quarters(&self, attacking: TypeId, defending: [TypeId; 2]) -> u8 {
        let a = self.eff(attacking, defending[0]);
        if defending[0] == defending[1] {
            a * 2
        } else {
            a * self.eff(attacking, defending[1])
        }
    }

    /// Showdown's per-type effectiveness step for one defending type:
    /// +1 super effective, 0 neutral or immune, -1 resisted.
    pub fn type_mod(&self, attacking: TypeId, defending: TypeId) -> i8 {
        match self.eff(attacking, defending) {
            4 => 1,
            1 => -1,
            _ => 0,
        }
    }

    /// True if `defending` is immune to `attacking` by the type chart alone.
    pub fn type_immune(&self, attacking: TypeId, defending: TypeId) -> bool {
        self.eff(attacking, defending) == 0
    }

    /// One type-chart cell; the typeless "???" type is neutral either way.
    fn eff(&self, attacking: TypeId, defending: TypeId) -> u8 {
        self.effectiveness
            .get(attacking.0 as usize)
            .and_then(|row| row.get(defending.0 as usize))
            .copied()
            .unwrap_or(2)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_embedded_dex() {
        let dex = Dex::get();
        assert_eq!(dex.type_names.len(), 18);
        assert_eq!(dex.natures.len(), 25);
        assert!(dex.species.len() > 1000);
        let chomp = dex.species(dex.species_id("Garchomp").unwrap());
        assert_eq!(chomp.base_stats, [108, 130, 95, 80, 85, 102]);
        assert_eq!(chomp.abilities, vec!["Sand Veil", "Rough Skin"]);
        assert_eq!(chomp.weight_hg, 950);
    }

    #[test]
    fn champions_megas_present() {
        let dex = Dex::get();
        let mega = dex.species(dex.species_id("Dragonite-Mega").unwrap());
        assert_eq!(mega.base_stats, [91, 124, 115, 145, 125, 100]);
        assert_eq!(mega.required_item.as_deref(), Some("Dragoninite"));
        let z = dex.species(dex.species_id("Lucario-Mega-Z").unwrap());
        assert_eq!(z.abilities, vec!["Aura Guard"]);
        assert_eq!(z.battle_only.as_deref(), Some("Lucario"));
        let chomp = dex.species(dex.species_id("Garchomp").unwrap());
        assert!(chomp.nonstandard.is_none());
        assert!(chomp.learnset.contains(&dex.move_id("Earthquake").unwrap()));
        assert!(!chomp.learnset.contains(&dex.move_id("Moonblast").unwrap()));
        let koraidon = dex.species(dex.species_id("Koraidon").unwrap());
        assert!(koraidon.tags.iter().any(|t| t == "Restricted Legendary"));
        let stone = dex.item(dex.item_id("Salamencite").unwrap());
        assert_eq!(
            stone.mega_stone.get("Salamence").map(String::as_str),
            Some("Salamence-Mega")
        );
    }

    #[test]
    fn moves_items_abilities() {
        let dex = Dex::get();
        let fake_out = dex.move_data(dex.move_id("Fake Out").unwrap());
        assert_eq!(
            (fake_out.base_power, fake_out.priority, fake_out.category),
            (40, 3, Category::Physical)
        );
        assert!(fake_out.flags.has("contact") && !fake_out.flags.has("sound"));
        assert!(fake_out.handlers.has("onDisableMove")); // Champions: only usable on the first turn
        let rock_slide = dex.move_data(dex.move_id("Rock Slide").unwrap());
        assert!(rock_slide.target.is_spread() && rock_slide.has_secondaries);
        let charcoal = dex.item(dex.item_id("Charcoal").unwrap());
        assert_eq!(charcoal.handlers.hook("onBasePowerPriority"), 15);
        let multiscale = dex.ability(dex.ability_id("Multiscale").unwrap());
        assert!(multiscale.breakable && multiscale.handlers.has("onSourceModifyDamage"));
        assert!(
            dex.ability(dex.ability_id("Cloud Nine").unwrap())
                .suppress_weather
        );
        assert!(
            dex.ability(dex.ability_id("Shell Armor").unwrap())
                .blocks_crit
        );
        let flash_fire = dex.ability(dex.ability_id("Flash Fire").unwrap());
        assert_eq!(flash_fire.condition.hook("onModifyAtkPriority"), 5);
        let reflect = dex.move_data(dex.move_id("Reflect").unwrap());
        assert!(reflect.condition.has("onAnyModifyDamage"));
        let sand = dex.condition(dex.condition_id("sandstorm").unwrap());
        assert_eq!(sand.handlers.hook("onModifySpDPriority"), 10);
    }

    #[test]
    fn type_chart() {
        let dex = Dex::get();
        let t = |n: &str| dex.type_id(n).unwrap();
        // Ground vs Charizard (Fire/Flying): immune.
        assert_eq!(
            dex.effectiveness_quarters(t("ground"), [t("Fire"), t("Flying")]),
            0
        );
        // Ice vs Garchomp (Dragon/Ground): 4x.
        assert_eq!(
            dex.effectiveness_quarters(t("Ice"), [t("Dragon"), t("Ground")]),
            16
        );
        // Fighting vs Gholdengo (Steel/Ghost): immune; Fire vs pure Grass: 2x.
        assert_eq!(
            dex.effectiveness_quarters(t("Fighting"), [t("Steel"), t("Ghost")]),
            0
        );
        assert_eq!(
            dex.effectiveness_quarters(t("Fire"), [t("Grass"), t("Grass")]),
            8
        );
        // Water vs Water/Dragon: x0.25.
        assert_eq!(
            dex.effectiveness_quarters(t("Water"), [t("Water"), t("Dragon")]),
            1
        );
        assert_eq!(dex.type_mod(t("Ice"), t("Ground")), 1);
        assert!(dex.type_immune(t("Normal"), t("Ghost")));
    }

    #[test]
    fn neutral_natures() {
        let dex = Dex::get();
        let hardy = dex.nature(dex.nature_id("Hardy").unwrap());
        assert!((0..6).all(|i| hardy.percent(i) == 100));
        let adamant = dex.nature(dex.nature_id("adamant").unwrap());
        assert_eq!([adamant.percent(1), adamant.percent(3)], [110, 90]);
    }
}
