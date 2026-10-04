//! Damage must equal Showdown's for every roll, in random Reg M-C situations.
//! Fixtures come from tools/showdown/gen_fixtures.js.
//!
//! A case the engine reports as `Unsupported` (an effect it hasn't
//! implemented) is counted, not failed; any case it does calculate must match
//! exactly.

use engine::damage::{
    calculate, Combatant, DamageCtx, Outcome, SideState, Status, Terrain, Volatiles, Weather,
};
use engine::dex::Dex;
use serde::Deserialize;
use std::collections::BTreeMap;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Mon {
    species: String,
    types: Vec<String>,
    stats: [u16; 6],
    hp: u16,
    boosts: [i8; 5],
    ability: String,
    item: String,
    status: String,
    speed: i32,
    volatiles: Vec<String>,
    helping_hand: u8,
    active_turns: u16,
    times_attacked: u8,
    fallen: u8,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Side {
    reflect: bool,
    light_screen: bool,
    aurora_veil: bool,
    fainted: u8,
}

#[derive(Deserialize)]
#[serde(untagged)]
enum Expected {
    Rolls(Vec<u32>),
    Word(String),
}

#[derive(Deserialize)]
struct Case {
    #[serde(rename = "move")]
    move_id: String,
    weather: String,
    terrain: String,
    sides: [Side; 2],
    crit: bool,
    spread: bool,
    hit: u8,
    mons: Vec<Mon>,
    result: Expected,
}

fn combatant(m: &Mon) -> Combatant {
    let dex = Dex::get();
    let species = dex
        .species_id(&m.species)
        .unwrap_or_else(|| panic!("species {}", m.species));
    let ability = dex
        .ability_id(&m.ability)
        .unwrap_or_else(|| panic!("ability {}", m.ability));
    let item = if m.item.is_empty() {
        None
    } else {
        Some(
            dex.item_id(&m.item)
                .unwrap_or_else(|| panic!("item {}", m.item)),
        )
    };
    let mut c = Combatant::new(species, m.stats, ability, item);
    let types: Vec<_> = m.types.iter().map(|t| dex.type_id(t).unwrap()).collect();
    c.types = [types[0], *types.get(1).unwrap_or(&types[0])];
    c.hp = m.hp;
    // The generator's battles have no queue and nobody newly switched in, so
    // every target counts as having moved (Payback doubles).
    c.moved_this_turn = true;
    c.boosts = [
        0,
        m.boosts[0],
        m.boosts[1],
        m.boosts[2],
        m.boosts[3],
        m.boosts[4],
    ];
    c.status = match m.status.as_str() {
        "" => Status::None,
        "brn" => Status::Burn,
        "par" => Status::Paralysis,
        "psn" => Status::Poison,
        "tox" => Status::Toxic,
        "slp" => Status::Sleep,
        "frz" => Status::Freeze,
        other => panic!("status {other}"),
    };
    c.speed = m.speed;
    c.spe_stat = m.speed.unsigned_abs();
    c.volatiles = Volatiles {
        helping_hand: m.helping_hand,
        charge: m.volatiles.iter().any(|v| v == "charge"),
        flash_fire: m.volatiles.iter().any(|v| v == "flashfire"),
        glaive_rush: m.volatiles.iter().any(|v| v == "glaiverush"),
        gem: m.volatiles.iter().any(|v| v == "gem"),
        semi_invulnerable: None,
    };
    for v in &m.volatiles {
        // choicelock: added by Choice Scarf's ModifyMove; no effect on damage.
        assert!(
            [
                "helpinghand",
                "charge",
                "flashfire",
                "glaiverush",
                "gem",
                "choicelock"
            ]
            .contains(&v.as_str()),
            "volatile {v}"
        );
    }
    c.active_turns = m.active_turns;
    c.times_attacked = m.times_attacked;
    c.fallen = m.fallen;
    c
}

#[test]
fn damage_matches_showdown() {
    let cases: Vec<Case> = serde_json::from_str(include_str!("fixtures/damage.json")).unwrap();
    let dex = Dex::get();
    let mut matched = 0;
    let mut mismatches = Vec::new();
    let mut unsupported: BTreeMap<String, usize> = BTreeMap::new();
    for (n, c) in cases.iter().enumerate() {
        let mons: Vec<Combatant> = c.mons.iter().map(combatant).collect();
        let side = |s: &Side| SideState {
            reflect: s.reflect,
            light_screen: s.light_screen,
            aurora_veil: s.aurora_veil,
            fainted: s.fainted,
        };
        let ctx = DamageCtx {
            actives: [
                Some(&mons[0]),
                Some(&mons[1]),
                Some(&mons[2]),
                Some(&mons[3]),
            ],
            attacker: 0,
            defender: 2,
            weather: match c.weather.as_str() {
                "" => Weather::None,
                "sunnyday" => Weather::Sun,
                "raindance" => Weather::Rain,
                "sandstorm" => Weather::Sand,
                "snowscape" => Weather::Snow,
                other => panic!("weather {other}"),
            },
            terrain: match c.terrain.as_str() {
                "" => Terrain::None,
                "electricterrain" => Terrain::Electric,
                "grassyterrain" => Terrain::Grassy,
                "mistyterrain" => Terrain::Misty,
                "psychicterrain" => Terrain::Psychic,
                other => panic!("terrain {other}"),
            },
            sides: [side(&c.sides[0]), side(&c.sides[1])],
            crit: c.crit,
            spread: c.spread,
            hit: c.hit,
            bypass_protect: false,
            hit_sub: false,
        };
        let move_id = dex
            .move_id(&c.move_id)
            .unwrap_or_else(|| panic!("move {}", c.move_id));
        let expected = match &c.result {
            Expected::Rolls(r) => Outcome::Damage(r.as_slice().try_into().expect("16 rolls")),
            Expected::Word(w) if w == "immune" => Outcome::Immune,
            Expected::Word(w) if w == "nodamage" => Outcome::NoDamage,
            Expected::Word(w) => panic!("result {w}"),
        };
        match calculate(&ctx, move_id) {
            Ok(got) if got == expected => matched += 1,
            Ok(got) => mismatches.push(format!(
                "case {n}: {} {} -> {} {}: got {got:?}, Showdown {expected:?}",
                c.mons[0].species, c.move_id, c.mons[2].species, c.mons[2].ability
            )),
            Err(e) => *unsupported.entry(e.0).or_default() += 1,
        }
    }
    let skipped: usize = unsupported.values().sum();
    let mut reasons: Vec<_> = unsupported.into_iter().collect();
    reasons.sort_by_key(|r| std::cmp::Reverse(r.1));
    eprintln!(
        "{matched} matched, {} mismatched, {skipped} unsupported of {}",
        mismatches.len(),
        cases.len()
    );
    for (why, count) in reasons.iter().take(25) {
        eprintln!("  unsupported x{count}: {why}");
    }
    assert!(
        mismatches.is_empty(),
        "{} mismatches:\n{}",
        mismatches.len(),
        mismatches[..mismatches.len().min(30)].join("\n")
    );
    assert!(
        matched * 10 >= cases.len() * 9,
        "under 90% of cases supported: {matched}/{}",
        cases.len()
    );
}
