//! Static game data: species, natures, the type chart.
//!
//! Built once from `data/dex.json` (exported from Showdown's champions mod by
//! `tools/showdown/export_dex.js`) and embedded in the binary, so the engine
//! has no runtime file, Node or Python dependency. Battle code refers to
//! entries by small integer ids; string lookups happen only at the boundary.

use serde::Deserialize;
use std::collections::HashMap;
use std::sync::OnceLock;

const DEX_JSON: &str = include_str!("../../data/dex.json");

/// Stat order used everywhere in the engine: HP, Atk, Def, SpA, SpD, Spe.
pub const STAT_NAMES: [&str; 6] = ["hp", "atk", "def", "spa", "spd", "spe"];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct SpeciesId(pub u16);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct NatureId(pub u8);

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct TypeId(pub u8);

#[derive(Debug)]
pub struct Species {
    pub id: String,
    pub name: String,
    pub num: i32,
    pub types: [TypeId; 2], // a mono-type species repeats its type
    pub base_stats: [u16; 6],
    pub abilities: Vec<String>,
    pub weight_kg: f32,
    pub base_species: Option<String>,
    pub forme: Option<String>,
    pub required_item: Option<String>,
}

impl Species {
    pub fn is_mono_type(&self) -> bool {
        self.types[0] == self.types[1]
    }
}

#[derive(Debug, Clone, Copy)]
pub struct Nature {
    pub plus: Option<usize>,  // index into STAT_NAMES, never 0 (HP)
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
    pub species: Vec<Species>,
    pub natures: Vec<Nature>,
    pub nature_names: Vec<String>,
    species_index: HashMap<String, SpeciesId>,
    nature_index: HashMap<String, NatureId>,
    type_index: HashMap<String, TypeId>,
}

#[derive(Deserialize)]
struct RawDex {
    source: String,
    types: Vec<String>,
    typechart: HashMap<String, HashMap<String, u8>>,
    natures: HashMap<String, RawNature>,
    species: HashMap<String, RawSpecies>,
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
    weightkg: f32,
    base_species: Option<String>,
    forme: Option<String>,
    required_item: Option<String>,
}

/// Showdown's id format: lowercase ASCII letters and digits only.
pub fn to_id(name: &str) -> String {
    name.chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .map(|c| c.to_ascii_lowercase())
        .collect()
}

fn stat_index(name: &str) -> usize {
    STAT_NAMES
        .iter()
        .position(|s| *s == name)
        .unwrap_or_else(|| panic!("dex.json: unknown stat {name:?}"))
}

impl Dex {
    /// The process-wide dex, parsed from the embedded JSON on first use.
    pub fn get() -> &'static Dex {
        static DEX: OnceLock<Dex> = OnceLock::new();
        DEX.get_or_init(|| Dex::from_json(DEX_JSON).expect("embedded dex.json is invalid"))
    }

    pub fn from_json(text: &str) -> Result<Dex, String> {
        let raw: RawDex = serde_json::from_str(text).map_err(|e| e.to_string())?;

        let type_index: HashMap<String, TypeId> = raw
            .types
            .iter()
            .enumerate()
            .map(|(i, t)| (t.clone(), TypeId(i as u8)))
            .collect();
        let n = raw.types.len();
        let mut effectiveness = vec![vec![2u8; n]; n];
        let lookup = |t: &String| type_index.get(t).map(|id| id.0 as usize).ok_or_else(|| format!("typechart: unknown type {t}"));
        for (defending, row) in &raw.typechart {
            let d = lookup(defending)?;
            for (attacking, code) in row {
                let a = lookup(attacking)?;
                effectiveness[a][d] = match code {
                    0 => 2,
                    1 => 4,
                    2 => 1,
                    3 => 0,
                    other => return Err(format!("typechart: unknown code {other}")),
                };
            }
        }

        // Sorted so ids are stable across runs (HashMap order is not).
        let mut species_ids: Vec<&String> = raw.species.keys().collect();
        species_ids.sort();
        let mut species = Vec::with_capacity(species_ids.len());
        let mut species_index = HashMap::new();
        for (i, sid) in species_ids.into_iter().enumerate() {
            let r = &raw.species[sid];
            let t = r
                .types
                .iter()
                .map(|t| type_index.get(t).copied().ok_or_else(|| format!("{sid}: unknown type {t}")))
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
                weight_kg: r.weightkg,
                base_species: r.base_species.clone(),
                forme: r.forme.clone(),
                required_item: r.required_item.clone(),
            });
            species_index.insert(sid.clone(), SpeciesId(i as u16));
        }

        let mut nature_names: Vec<String> = raw.natures.keys().cloned().collect();
        nature_names.sort();
        let mut natures = Vec::new();
        let mut nature_index = HashMap::new();
        for (i, nid) in nature_names.iter().enumerate() {
            let r = &raw.natures[nid];
            natures.push(Nature {
                plus: r.plus.as_deref().map(stat_index),
                minus: r.minus.as_deref().map(stat_index),
            });
            nature_index.insert(nid.clone(), NatureId(i as u8));
        }

        Ok(Dex {
            source: raw.source,
            type_names: raw.types,
            effectiveness,
            species,
            natures,
            nature_names,
            species_index,
            nature_index,
            type_index,
        })
    }

    pub fn species_id(&self, name: &str) -> Option<SpeciesId> {
        self.species_index.get(&to_id(name)).copied()
    }

    pub fn species(&self, id: SpeciesId) -> &Species {
        &self.species[id.0 as usize]
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
        let row = &self.effectiveness[attacking.0 as usize];
        let a = row[defending[0].0 as usize];
        if defending[0] == defending[1] {
            a * 2
        } else {
            a * row[defending[1].0 as usize]
        }
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
    }

    #[test]
    fn champions_megas_present() {
        let dex = Dex::get();
        let mega = dex.species(dex.species_id("Dragonite-Mega").unwrap());
        assert_eq!(mega.base_stats, [91, 124, 115, 145, 125, 100]);
        assert_eq!(mega.required_item.as_deref(), Some("Dragoninite"));
    }

    #[test]
    fn type_chart() {
        let dex = Dex::get();
        let t = |n: &str| dex.type_id(n).unwrap();
        // Ground vs Charizard (Fire/Flying): immune.
        assert_eq!(dex.effectiveness_quarters(t("ground"), [t("Fire"), t("Flying")]), 0);
        // Ice vs Garchomp (Dragon/Ground): 4x.
        assert_eq!(dex.effectiveness_quarters(t("Ice"), [t("Dragon"), t("Ground")]), 16);
        // Fighting vs Gholdengo (Steel/Ghost): immune; Fire vs pure Grass: 2x.
        assert_eq!(dex.effectiveness_quarters(t("Fighting"), [t("Steel"), t("Ghost")]), 0);
        assert_eq!(dex.effectiveness_quarters(t("Fire"), [t("Grass"), t("Grass")]), 8);
        // Water vs Water/Dragon: x0.25.
        assert_eq!(dex.effectiveness_quarters(t("Water"), [t("Water"), t("Dragon")]), 1);
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
