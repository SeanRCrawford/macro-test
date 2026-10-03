//! Whole battles must play out exactly as in Showdown. The fixtures record
//! Showdown's state at every decision of random battles between supported
//! teams, with Showdown's PRNG replaced by the same threshold policy as
//! Chance::Policy. The engine replays the same choices and must reach the
//! same state, offer the same number of legal choices, and end the same way.
//! Fixtures come from tools/showdown/gen_fixtures.js.

use engine::battle::choice::SideChoice;
use engine::battle::Battle;
use engine::chance::Chance;
use engine::team::parse_paste;
use serde::Deserialize;
use serde_json::Value;

#[derive(Deserialize)]
struct Step {
    snapshot: Value,
    counts: [Option<usize>; 2],
    choices: [Option<String>; 2],
}

#[derive(Deserialize)]
struct Fixture {
    threshold: f64,
    teams: [String; 2],
    steps: Vec<Step>,
    #[serde(rename = "final")]
    final_state: Value,
}

/// The first place two JSON values differ, as a path.
fn first_difference(a: &Value, b: &Value, path: &str) -> Option<String> {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            for (k, v) in x {
                let w = y.get(k).unwrap_or(&Value::Null);
                if let Some(d) = first_difference(v, w, &format!("{path}.{k}")) {
                    return Some(d);
                }
            }
            for k in y.keys() {
                if !x.contains_key(k) {
                    return Some(format!("{path}.{k}: missing in engine"));
                }
            }
            None
        }
        (Value::Array(x), Value::Array(y)) => {
            if x.len() != y.len() {
                return Some(format!("{path}: engine has {} entries, Showdown {}", x.len(), y.len()));
            }
            x.iter().zip(y).enumerate().find_map(|(i, (v, w))| first_difference(v, w, &format!("{path}[{i}]")))
        }
        _ if a == b => None,
        _ => Some(format!("{path}: engine {a}, Showdown {b}")),
    }
}

#[test]
fn battles_match_showdown() {
    let fixtures: Vec<Fixture> = serde_json::from_str(include_str!("fixtures/battles.json")).unwrap();
    let mut failures = Vec::new();
    let mut decisions = 0;
    'battles: for (n, f) in fixtures.iter().enumerate() {
        let teams = [parse_paste(&f.teams[0]).unwrap(), parse_paste(&f.teams[1]).unwrap()];
        let mut b = match Battle::new(teams, Chance::policy(f.threshold)) {
            Ok(b) => b,
            Err(e) => {
                failures.push(format!("battle {n}: {e:?}"));
                continue;
            }
        };
        for (i, step) in f.steps.iter().enumerate() {
            let ctx = format!("battle {n} (t={}) decision {i} turn {}", f.threshold, step.snapshot["turn"]);
            if let Some(d) = first_difference(&b.snapshot(), &step.snapshot, "") {
                failures.push(format!("{ctx}: {d}"));
                continue 'battles;
            }
            for side in 0..2 {
                let got = (!matches!(b.requests[side], engine::battle::choice::SideRequest::Wait)).then(|| b.legal_choices(side).len());
                let got = got.filter(|&c| c > 0);
                if got != step.counts[side] {
                    let req = &b.requests[side];
                    let listed: Vec<String> = b.legal_choices(side).iter().map(|c| c.to_showdown(req)).collect();
                    failures.push(format!(
                        "{ctx}: side {side} has {got:?} legal choices, Showdown {:?}\n  engine: {listed:?}\n  request: {req:?}",
                        step.counts[side]
                    ));
                    continue 'battles;
                }
            }
            let choices = [0, 1].map(|s| step.choices[s].as_deref().map(|c| SideChoice::from_showdown(c).unwrap()));
            if let Err(e) = b.choose(choices) {
                failures.push(format!("{ctx}: choosing {:?} failed: {e:?}", step.choices));
                continue 'battles;
            }
            decisions += 1;
        }
        if let Some(d) = first_difference(&b.snapshot(), &f.final_state, "") {
            failures.push(format!("battle {n} final state: {d}"));
        }
    }
    eprintln!("{} battles, {decisions} decisions replayed, {} diverged", fixtures.len(), failures.len());
    assert!(failures.is_empty(), "{}", failures.join("\n"));
}
