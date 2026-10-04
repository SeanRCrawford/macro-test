//! The team corpus loads: every legal paste is playable, and placements are
//! read from the names.

use engine::corpus::{load_dir, Placement};
use std::path::Path;

#[test]
fn corpus_loads() {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let (teams, rejected) = load_dir(&dir).unwrap();
    for r in &rejected {
        eprintln!("rejected {}: {}", r.name, r.reason);
    }
    // The only rejects are illegal pastes (Archaludon with Precipice Blades).
    assert!(
        rejected.iter().all(|r| r.reason.contains("can't learn")),
        "{rejected:?}"
    );
    assert!(teams.len() >= 450, "only {} teams", teams.len());
    let champions = teams
        .iter()
        .filter(|t| t.placement == Placement::Champion)
        .count();
    assert!(champions >= 25, "{champions} champion teams");
}
