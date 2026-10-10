//! data/support.json lists what the battle engine can play, for the fixture
//! generator. Regenerate with `UPDATE_SUPPORT=1 cargo test --test battle_support`.

use engine::battle::support;
use std::path::Path;

#[test]
fn support_json_is_current() {
    let (moves, abilities, items) = support::lists();
    let text = serde_json::to_string_pretty(&serde_json::json!({
        "moves": moves, "abilities": abilities, "items": items,
    }))
    .unwrap()
        + "\n";
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/support.json");
    if std::env::var("UPDATE_SUPPORT").is_ok() {
        std::fs::write(&path, &text).unwrap();
    }
    let current = std::fs::read_to_string(&path).unwrap_or_default();
    assert!(
        current == text,
        "data/support.json is stale: run UPDATE_SUPPORT=1 cargo test --test battle_support"
    );
}
