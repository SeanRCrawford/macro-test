//! Replay a game the training environment reported as panicking (a line of
//! `engine_crashes.jsonl`), printing each turn's choices and the panic.
//!
//!     cargo run --release --example replay_crash -- engine_crashes.jsonl [line]
//!
//! SELFPLAY_TRACE=1 also traces the action queue.

use engine::battle::Battle;
use engine::chance::Chance;
use engine::corpus::load_dir;
use engine::env::action::{self, Decision};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let file = args.get(1).expect("a crash report file");
    let line: usize = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(0);
    let text = std::fs::read_to_string(file).expect("read the report");
    let report: serde_json::Value = serde_json::from_str(
        text.lines().filter(|l| !l.trim().is_empty()).nth(line).expect("no such line"),
    )
    .expect("a JSON report");
    println!("reported panic: {}", report["panic"]);
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let (teams, _) = load_dir(&dir).expect("corpus");
    let sets = [0, 1].map(|s| {
        let name = report["teams"][s].as_str().expect("team name");
        teams
            .iter()
            .find(|t| t.name == name)
            .unwrap_or_else(|| panic!("team {name} isn't in the corpus"))
            .sets
            .clone()
    });
    let seed = report["seed"].as_u64().expect("seed");
    let actions: Vec<[i64; 2]> = serde_json::from_value(report["actions"].clone()).expect("actions");
    let mut b = Battle::new(sets, Chance::seeded(seed)).expect("battle");
    b.turn_limit = Some(30);
    for (i, a) in actions.iter().enumerate() {
        let mut choices = [None, None];
        let mut shown = Vec::new();
        for side in 0..2 {
            let d = action::decision(&b, side);
            if d == Decision::None {
                continue;
            }
            let c = action::choice(d, a[side] as usize).expect("a valid action");
            shown.push(format!("p{}: {}", side + 1, c.to_showdown(&b.requests[side])));
            choices[side] = Some(c);
        }
        println!("turn {} step {i}: {}", b.turn, shown.join(" | "));
        let last = i + 1 == actions.len();
        if last {
            println!("state before the panic:\n{}", b.snapshot());
        }
        let r = catch_unwind(AssertUnwindSafe(|| b.choose(choices)));
        match r {
            Ok(Ok(())) => {}
            Ok(Err(e)) => {
                println!("error: {e:?}");
                return;
            }
            Err(_) => {
                println!("reproduced the panic at step {i}");
                return;
            }
        }
    }
    println!("replayed every action without a panic");
}
