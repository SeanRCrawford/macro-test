//! Team parsing and validation must agree with Showdown's Teams.import and
//! TeamValidator. Fixtures come from tools/showdown/gen_fixtures.js.

use engine::dex::{to_id, Dex};
use engine::team::{parse_paste, validate, ProblemKind};
use serde::Deserialize;
use std::path::Path;

#[derive(Deserialize)]
struct ParsedSet {
    name: String,
    species: String,
    item: String,
    ability: String,
    nature: String,
    moves: Vec<String>,
    points: Option<[u16; 6]>,
}

#[derive(Deserialize)]
struct ParseCase {
    file: String,
    sets: Vec<ParsedSet>,
}

#[derive(Deserialize)]
struct ValidateCase {
    mutation: String,
    text: String,
    valid: bool,
    problems: Vec<String>,
}

#[derive(Deserialize)]
struct Fixtures {
    parse: Vec<ParseCase>,
    validate: Vec<ValidateCase>,
}

fn fixtures() -> Fixtures {
    serde_json::from_str(include_str!("fixtures/teams.json")).unwrap()
}

#[test]
fn repo_teams_parse_like_showdown() {
    let dex = Dex::get();
    let teams_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/teams");
    for case in fixtures().parse {
        let text = std::fs::read_to_string(teams_dir.join(&case.file)).unwrap();
        let team = parse_paste(&text).unwrap_or_else(|e| panic!("{}: {e}", case.file));
        assert_eq!(team.len(), case.sets.len(), "{}", case.file);
        for (got, want) in team.iter().zip(&case.sets) {
            let ctx = format!("{} / {}", case.file, want.name);
            assert_eq!(dex.species(got.species).id, want.species, "{ctx}");
            assert_eq!(got.item.map(|i| dex.item(i).id.clone()).unwrap_or_default(), want.item, "{ctx}");
            // Showdown's importer keeps a Mega's ability as written; its
            // validator (and the engine's parser) rewrite it to the base
            // species' ability.
            let mega_ability = got.item.and_then(|i| dex.item(i).mega_stone.get(&dex.species(got.species).name))
                .and_then(|m| dex.species_id(m))
                .is_some_and(|m| dex.species(m).abilities.iter().any(|a| to_id(a) == want.ability));
            if !mega_ability {
                assert_eq!(dex.ability(got.ability).id, want.ability, "{ctx}");
            }
            // Showdown ignores the repo's "Nature: X" lines (empty nature);
            // the engine reads them.
            if !want.nature.is_empty() {
                assert_eq!(to_id(&dex.nature_names[got.nature.0 as usize]), want.nature, "{ctx}");
            } else {
                assert!(text.contains("Nature: "), "{ctx}: no nature");
            }
            let moves: Vec<_> = got.moves.iter().map(|m| dex.move_data(*m).id.clone()).collect();
            assert_eq!(moves, want.moves, "{ctx}");
            match want.points {
                Some(p) => assert_eq!(got.points, p, "{ctx}"),
                None => assert!(got.points_filled, "{ctx}: points should be filled from usage"),
            }
        }
        let problems = validate(&team);
        assert!(problems.is_empty(), "{}: {problems:?}", case.file);
    }
}

#[test]
fn validation_agrees_with_showdown() {
    let mut disagreements = Vec::new();
    let cases = fixtures().validate;
    for (n, case) in cases.iter().enumerate() {
        let team = parse_paste(&case.text).unwrap_or_else(|e| panic!("case {n}: {e}"));
        let problems = validate(&team);
        // The usage-stats item rule is ours, not Showdown's.
        let ours: Vec<_> = problems.iter().filter(|p| p.kind != ProblemKind::ItemNotInUsage).collect();
        if ours.is_empty() != case.valid {
            disagreements.push(format!(
                "case {n} ({}): Showdown valid={} {:?}; engine {:?}",
                case.mutation, case.valid, case.problems, ours
            ));
        }
        if case.mutation == "unused legal item" {
            assert!(problems.iter().any(|p| p.kind == ProblemKind::ItemNotInUsage), "case {n}");
        }
    }
    assert!(disagreements.is_empty(), "{} of {}:\n{}", disagreements.len(), cases.len(), disagreements.join("\n"));
}
