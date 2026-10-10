//! Teams: Showdown paste parsing, Reg M-C validation, Mega formes and team
//! preview.
//!
//! Parsing follows Showdown's `Teams.import` (`parseExportedTeamLine`),
//! including ignoring lines it doesn't recognise. Stat points go in the `EVs:`
//! line, as Showdown's Champions export writes them. One extension: a
//! `Nature: Adamant` line is read as the nature. Several of the repo's pastes
//! use that form, which Showdown itself ignores (leaving the nature neutral). A set with no `EVs:` line gets the species'
//! most common Reg M-C spread from the usage stats (the existing tools fill
//! missing details from usage the same way), and is marked as filled.
//!
//! Validation follows Showdown's validator for
//! `[Gen 9 Champions] VGC 2026 Reg M-C` (Flat Rules), checked against it by
//! tests/team_fixtures.rs. One rule is the user's rather than Showdown's: an
//! item must also appear in the Reg M-C usage stats. Showdown additionally
//! allows 12 items nobody uses (Focus Band, Metronome, a few berries...).

use crate::dex::{to_id, AbilityId, Dex, ItemId, MoveId, NatureId, SpeciesId, STAT_NAMES};
use crate::stats::{MAX_POINTS, MAX_TOTAL_POINTS};
use serde_json::Value;
use std::collections::{HashMap, HashSet};
use std::sync::OnceLock;

const POOL_JSON: &str = include_str!("../../data/regmc_pool.json");

pub const TEAM_SIZE: usize = 6;
pub const BRING: usize = 4;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PokemonSet {
    /// Nickname, or the species name when there is none.
    pub name: String,
    pub species: SpeciesId,
    pub item: Option<ItemId>,
    pub ability: AbilityId,
    pub nature: NatureId,
    /// Stat points, HP/Atk/Def/SpA/SpD/Spe.
    pub points: [u16; 6],
    /// True when the paste had no `EVs:` line and `points` came from usage.
    pub points_filled: bool,
    pub moves: Vec<MoveId>,
}

/// Reg M-C usage data needed here: legal items and default spreads.
struct Pool {
    items: HashSet<String>,
    /// Species id -> spreads (nature, points), most used first.
    spreads: HashMap<String, Vec<(String, [u16; 6])>>,
}

fn pool() -> &'static Pool {
    static POOL: OnceLock<Pool> = OnceLock::new();
    POOL.get_or_init(|| {
        let raw: Value = serde_json::from_str(POOL_JSON).expect("embedded regmc_pool.json");
        let items = raw["items"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| to_id(v.as_str().unwrap()))
            .collect();
        let mut spreads = HashMap::new();
        for (id, entry) in raw["species"].as_object().unwrap() {
            let Some(list) = entry.get("spreads").and_then(Value::as_array) else {
                continue;
            };
            let parsed = list
                .iter()
                .map(|s| {
                    let nature = s[0].as_str().unwrap().to_string();
                    let pts: Vec<u16> = s[1]
                        .as_array()
                        .unwrap()
                        .iter()
                        .map(|p| p.as_u64().unwrap() as u16)
                        .collect();
                    (nature, [pts[0], pts[1], pts[2], pts[3], pts[4], pts[5]])
                })
                .collect();
            spreads.insert(id.clone(), parsed);
        }
        Pool { items, spreads }
    })
}

/// The forme this set Mega Evolves into, if its item is its Mega Stone.
pub fn mega_forme(set: &PokemonSet) -> Option<SpeciesId> {
    let dex = Dex::get();
    let item = dex.item(set.item?);
    let forme = item.mega_stone.get(&dex.species(set.species).name)?;
    dex.species_id(forme)
}

/// Parse a Showdown export (one or more sets separated by blank lines).
pub fn parse_paste(text: &str) -> Result<Vec<PokemonSet>, String> {
    let mut sets = Vec::new();
    let mut block: Vec<&str> = Vec::new();
    for line in text.lines().map(str::trim).chain(std::iter::once("")) {
        if line.is_empty() {
            if !block.is_empty() {
                sets.push(parse_set(&block)?);
                block.clear();
            }
        } else {
            block.push(line);
        }
    }
    Ok(sets)
}

fn parse_set(lines: &[&str]) -> Result<PokemonSet, String> {
    let dex = Dex::get();
    let first = lines[0];
    let (head, item) = match first.split_once(" @ ") {
        Some((h, i)) => (h.trim(), Some(i.trim())),
        None => (first.trim(), None),
    };
    // Gender suffix, then "Nickname (Species)".
    let head = head
        .strip_suffix(" (M)")
        .or_else(|| head.strip_suffix(" (F)"))
        .unwrap_or(head)
        .trim();
    let (name, species_name) = match head.strip_suffix(')').and_then(|h| h.rsplit_once(" (")) {
        Some((nick, sp)) if dex.species_id(sp).is_some() => (nick.trim().to_string(), sp.trim()),
        _ => (head.to_string(), head),
    };
    let species = dex
        .species_id(species_name)
        .ok_or_else(|| format!("unknown species {species_name:?}"))?;
    let item = match item {
        None | Some("") => None,
        Some(i) => Some(
            dex.item_id(i)
                .ok_or_else(|| format!("{species_name}: unknown item {i:?}"))?,
        ),
    };

    let mut ability = None;
    let mut nature = None;
    let mut points = None;
    let mut moves = Vec::new();
    for line in &lines[1..] {
        if let Some(a) = line
            .strip_prefix("Ability:")
            .or_else(|| line.strip_prefix("Trait:"))
        {
            let a = a.trim();
            ability = Some(
                dex.ability_id(a)
                    .ok_or_else(|| format!("{species_name}: unknown ability {a:?}"))?,
            );
        } else if let Some(e) = line.strip_prefix("EVs:") {
            points = Some(parse_points(e).map_err(|err| format!("{species_name}: {err}"))?);
        } else if let Some(n) = nature_line(line) {
            nature = Some(
                dex.nature_id(n)
                    .ok_or_else(|| format!("{species_name}: unknown nature {n:?}"))?,
            );
        } else if let Some(m) = line.strip_prefix('-').or_else(|| line.strip_prefix('~')) {
            let m = m.trim();
            moves.push(
                dex.move_id(m)
                    .ok_or_else(|| format!("{species_name}: unknown move {m:?}"))?,
            );
        }
        // Anything else (Level, Shiny, IVs, Hidden Power...) has no effect in
        // this format; Showdown ignores unknown lines too.
    }
    // Showdown's defaults: first listed ability, a neutral nature.
    let sp = dex.species(species);
    let ability = match ability {
        Some(a) => a,
        None => dex
            .ability_id(sp.abilities.first().ok_or("species without abilities")?)
            .unwrap(),
    };
    let nature = nature.unwrap_or_else(|| dex.nature_id("Serious").unwrap());
    let (species, ability) = normalize_forme(species, item, ability);
    let mut set = PokemonSet {
        name,
        species,
        item,
        ability,
        nature,
        points: points.unwrap_or([0; 6]),
        points_filled: false,
        moves,
    };
    if points.is_none() {
        set.points = default_points(&set);
        set.points_filled = true;
    }
    Ok(set)
}

/// Showdown's validator rewrites two Mega-related shorthands:
/// - a Mega forme written as the species, holding its stone, becomes the base
///   species ("Garchomp-Mega @ Garchompite" is a Garchomp);
/// - the Mega's ability on a stone holder becomes the base species' first
///   ability (a Salamence "with Aerilate" is an Intimidate Salamence until it
///   Mega Evolves).
fn normalize_forme(
    species: SpeciesId,
    item: Option<ItemId>,
    ability: AbilityId,
) -> (SpeciesId, AbilityId) {
    let dex = Dex::get();
    let mut species = species;
    let sp = dex.species(species);
    if let (Some(base), Some(required), Some(it)) = (&sp.battle_only, &sp.required_item, item) {
        if dex.item(it).name == *required {
            species = dex.species_id(base).unwrap_or(species);
        }
    }
    let sp = dex.species(species);
    let ab = &dex.ability(ability).name;
    if !sp.abilities.contains(ab) {
        let mega = item
            .and_then(|it| dex.item(it).mega_stone.get(&sp.name))
            .and_then(|m| dex.species_id(m));
        if let Some(m) = mega {
            if dex.species(m).abilities.first() == Some(ab) {
                return (species, dex.ability_id(&sp.abilities[0]).unwrap_or(ability));
            }
        }
    }
    (species, ability)
}

/// The nature named by "Adamant Nature" (Showdown's /^[A-Za-z]+ [Nn]ature/)
/// or by the repo's "Nature: Adamant".
fn nature_line(line: &str) -> Option<&str> {
    if let Some(n) = line.strip_prefix("Nature:") {
        return Some(n.trim());
    }
    let (word, rest) = line.split_once(' ')?;
    let is_word = !word.is_empty() && word.chars().all(|c| c.is_ascii_alphabetic());
    (is_word && (rest.starts_with("Nature") || rest.starts_with("nature"))).then_some(word)
}

/// "32 HP / 32 Atk / 2 SpD" -> [32, 32, 0, 0, 2, 0].
fn parse_points(text: &str) -> Result<[u16; 6], String> {
    let mut out = [0u16; 6];
    for part in text.split('/') {
        let part = part.trim();
        let (n, stat) = part
            .split_once(' ')
            .ok_or_else(|| format!("bad stat points {part:?}"))?;
        let n: u16 = n.parse().map_err(|_| format!("bad stat points {part:?}"))?;
        let idx = match stat.trim().to_ascii_lowercase().as_str() {
            "hp" => 0,
            "atk" => 1,
            "def" => 2,
            "spa" => 3,
            "spd" => 4,
            "spe" => 5,
            other => return Err(format!("unknown stat {other:?}")),
        };
        out[idx] = n;
    }
    Ok(out)
}

/// The most common Reg M-C spread for this set's species (its Mega forme's
/// entry when it holds its stone), preferring one with the set's nature.
fn default_points(set: &PokemonSet) -> [u16; 6] {
    let dex = Dex::get();
    let nature = &dex.nature_names[set.nature.0 as usize];
    let keys = [mega_forme(set), Some(set.species)];
    for key in keys.into_iter().flatten() {
        if let Some(list) = pool().spreads.get(&dex.species(key).id) {
            if let Some((_, p)) = list.iter().find(|(n, _)| to_id(n) == *nature) {
                return *p;
            }
            if let Some((_, p)) = list.first() {
                return *p;
            }
        }
    }
    [0; 6]
}

/// A rule a team breaks. `kind` groups problems for comparison with
/// Showdown's validator, which words them differently.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Problem {
    pub kind: ProblemKind,
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ProblemKind {
    TeamSize,
    Species,
    SpeciesClause,
    Item,
    /// Legal on Showdown, but absent from the Reg M-C usage stats.
    ItemNotInUsage,
    ItemClause,
    Ability,
    Move,
    StatPoints,
}

/// Every Reg M-C rule the team breaks. Empty means legal.
pub fn validate(team: &[PokemonSet]) -> Vec<Problem> {
    let dex = Dex::get();
    let mut out = Vec::new();
    let mut add = |kind, message: String| out.push(Problem { kind, message });
    if team.len() != TEAM_SIZE {
        add(
            ProblemKind::TeamSize,
            format!("team has {} Pokemon, needs {TEAM_SIZE}", team.len()),
        );
    }
    let mut nums = HashSet::new();
    let mut items = HashSet::new();
    for set in team {
        let sp = dex.species(set.species);
        if let Some(base) = &sp.battle_only {
            add(
                ProblemKind::Species,
                format!("{} only exists in battle; use {base}", sp.name),
            );
        } else if sp.nonstandard.is_some()
            || sp
                .tags
                .iter()
                .any(|t| t == "Mythical" || t == "Restricted Legendary")
        {
            add(ProblemKind::Species, format!("{} is not allowed", sp.name));
        }
        if !nums.insert(sp.num) {
            add(
                ProblemKind::SpeciesClause,
                format!("two {}", sp.base_species),
            );
        }
        if let Some(it) = set.item {
            let item = dex.item(it);
            if item.nonstandard.is_some() {
                add(ProblemKind::Item, format!("{} is not allowed", item.name));
            } else if !pool().items.contains(&item.id) {
                add(
                    ProblemKind::ItemNotInUsage,
                    format!("{} is not in the Reg M-C usage stats", item.name),
                );
            }
            if !items.insert(it) {
                add(ProblemKind::ItemClause, format!("two {}", item.name));
            }
        }
        let ability = dex.ability(set.ability);
        if !sp.abilities.iter().any(|a| to_id(a) == ability.id) || ability.nonstandard.is_some() {
            add(
                ProblemKind::Ability,
                format!("{} can't have {}", sp.name, ability.name),
            );
        }
        let mut seen = HashSet::new();
        if set.moves.is_empty() || set.moves.len() > 4 {
            add(
                ProblemKind::Move,
                format!("{} has {} moves", sp.name, set.moves.len()),
            );
        }
        for &m in &set.moves {
            let mv = dex.move_data(m);
            if !seen.insert(m) {
                add(
                    ProblemKind::Move,
                    format!("{} has {} twice", sp.name, mv.name),
                );
            } else if sp.learnset.binary_search(&m).is_err() {
                add(
                    ProblemKind::Move,
                    format!("{} can't learn {}", sp.name, mv.name),
                );
            }
        }
        let total: u16 = set.points.iter().sum();
        if let Some(i) = set.points.iter().position(|&p| p > MAX_POINTS) {
            add(
                ProblemKind::StatPoints,
                format!(
                    "{} has {} points in {}",
                    sp.name, set.points[i], STAT_NAMES[i]
                ),
            );
        }
        if total > MAX_TOTAL_POINTS {
            add(
                ProblemKind::StatPoints,
                format!("{} has {total} stat points", sp.name),
            );
        }
    }
    out
}

/// Every team-preview choice: the two leads in order (left, right) and the
/// two in the back. Showdown's `team abcd` with indices into the team of 6.
/// The back pair's order doesn't matter, so it is listed once: 6*5*6 = 180.
pub fn preview_choices() -> Vec<[u8; 4]> {
    let mut out = Vec::with_capacity(180);
    for a in 0..6u8 {
        for b in 0..6u8 {
            if b == a {
                continue;
            }
            let rest: Vec<u8> = (0..6u8).filter(|&x| x != a && x != b).collect();
            for i in 0..rest.len() {
                for j in i + 1..rest.len() {
                    out.push([a, b, rest[i], rest[j]]);
                }
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const CHOMPCHU: &str = include_str!("../../../data/teams/ChompChu.txt");

    #[test]
    fn parses_repo_paste() {
        let team = parse_paste(CHOMPCHU).unwrap();
        assert_eq!(team.len(), 6);
        let dex = Dex::get();
        let chomp = &team[2];
        assert_eq!(dex.species(chomp.species).name, "Garchomp");
        assert_eq!(dex.item(chomp.item.unwrap()).name, "Garchompite Z");
        assert_eq!(
            dex.species(mega_forme(chomp).unwrap()).name,
            "Garchomp-Mega-Z"
        );
        assert!(validate(&team).is_empty(), "{:?}", validate(&team));
    }

    #[test]
    fn nickname_gender_and_points() {
        let text = "Bob (Indeedee-F) (F) @ Psychic Seed\r\nAbility: Psychic Surge\r\nEVs: 32 HP / 2 Def / 32 SpD\r\nSassy Nature\r\n- Follow Me\r\n- Helping Hand\r\n";
        let set = &parse_paste(text).unwrap()[0];
        let dex = Dex::get();
        assert_eq!(set.name, "Bob");
        assert_eq!(dex.species(set.species).name, "Indeedee-F");
        assert_eq!(set.points, [32, 0, 2, 0, 32, 0]);
        assert!(!set.points_filled);
    }

    #[test]
    fn missing_points_filled_from_usage() {
        let text = "Garchomp @ Choice Scarf\nAbility: Rough Skin\nJolly Nature\n- Earthquake\n";
        let set = &parse_paste(text).unwrap()[0];
        assert!(set.points_filled);
        assert_eq!(set.points.iter().sum::<u16>(), 66);
    }

    #[test]
    fn catches_rule_breaks() {
        let dex = Dex::get();
        let mut team = parse_paste(CHOMPCHU).unwrap();
        team[1].item = team[0].item; // item clause
        team[0].moves.push(dex.move_id("Moonblast").unwrap()); // fifth move, unlearnable
        team[3].points = [33, 33, 0, 0, 0, 0];
        let kinds: HashSet<ProblemKind> = validate(&team).into_iter().map(|p| p.kind).collect();
        assert!(kinds.contains(&ProblemKind::ItemClause));
        assert!(kinds.contains(&ProblemKind::Move));
        assert!(kinds.contains(&ProblemKind::StatPoints));
    }

    #[test]
    fn preview_choice_count() {
        let c = preview_choices();
        assert_eq!(c.len(), 180);
        assert_eq!(c.iter().collect::<HashSet<_>>().len(), 180);
    }
}
