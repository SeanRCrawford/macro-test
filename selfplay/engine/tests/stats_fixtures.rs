//! Stats must equal Showdown's for every Reg M-C species and listed spread.
//! Fixtures come from tools/showdown/gen_fixtures.js.

use engine::dex::Dex;
use engine::stats::compute_stats;
use serde::Deserialize;

#[derive(Deserialize)]
struct Case {
    species: String,
    nature: String,
    points: [u16; 6],
    stats: [u16; 6],
}

#[test]
fn stats_match_showdown() {
    let cases: Vec<Case> = serde_json::from_str(include_str!("fixtures/stats.json")).unwrap();
    let dex = Dex::get();
    let mut failures = Vec::new();
    for c in &cases {
        let sid = dex
            .species_id(&c.species)
            .unwrap_or_else(|| panic!("{} not in dex", c.species));
        let nid = dex.nature_id(&c.nature).unwrap();
        let got = compute_stats(sid, nid, c.points);
        if got != c.stats {
            failures.push(format!(
                "{} {} {:?}: got {:?}, Showdown {:?}",
                c.species, c.nature, c.points, got, c.stats
            ));
        }
    }
    assert!(cases.len() > 10_000);
    assert!(
        failures.is_empty(),
        "{} of {} differ:\n{}",
        failures.len(),
        cases.len(),
        failures[..failures.len().min(20)].join("\n")
    );
}
