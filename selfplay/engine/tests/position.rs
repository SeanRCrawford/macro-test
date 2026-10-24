//! Battle::position and Battle::from_position: a battle rebuilt from its
//! position must look the same to both sides and play on exactly like the
//! original, given the same random draws and choices.

use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus;
use engine::env::obs::{observe, FIELD_FLOATS, INT_FIELDS, MON_FLOATS, TOKENS};
use serde_json::Value;
use std::path::Path;

fn corpus_teams() -> Vec<corpus::CorpusTeam> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    corpus::load_dir(&dir).expect("data/corpus").0
}

/// The first path where two JSON values differ.
fn diff(a: &Value, b: &Value, path: &str) -> Option<String> {
    match (a, b) {
        (Value::Object(x), Value::Object(y)) => {
            for k in x.keys().chain(y.keys()) {
                let (va, vb) = (x.get(k).unwrap_or(&Value::Null), y.get(k).unwrap_or(&Value::Null));
                if let Some(d) = diff(va, vb, &format!("{path}.{k}")) {
                    return Some(d);
                }
            }
            None
        }
        (Value::Array(x), Value::Array(y)) if x.len() == y.len() => {
            x.iter().zip(y).enumerate().find_map(|(i, (va, vb))| diff(va, vb, &format!("{path}[{i}]")))
        }
        _ if a == b => None,
        _ => Some(format!("{path}: {a} vs {b}")),
    }
}

fn view(b: &Battle, side: usize, perfect: bool) -> (Vec<i32>, Vec<f32>, Vec<f32>) {
    let mut ints = vec![0; TOKENS * INT_FIELDS];
    let mut mons = vec![0.0; TOKENS * MON_FLOATS];
    let mut field = vec![0.0; FIELD_FLOATS];
    observe(b, side, perfect, None, &mut ints, &mut mons, &mut field);
    (ints, mons, field)
}

fn choose(b: &mut Battle, rng: &mut Rng) -> Vec<Option<engine::battle::choice::SideChoice>> {
    let picks: Vec<_> = (0..2)
        .map(|s| {
            let legal = b.legal_choices(s);
            (!legal.is_empty()).then(|| legal[rng.below(legal.len() as u32) as usize].clone())
        })
        .collect();
    b.choose([picks[0].clone(), picks[1].clone()]).expect("legal choices");
    picks
}

fn state(b: &Battle) -> Value {
    if b.position_is_complete() {
        b.position()
    } else {
        b.snapshot()
    }
}

#[test]
fn rebuilt_positions_play_on_like_the_original() {
    let teams = corpus_teams();
    let mut rng = Rng::new(20261012);
    let (mut checked, mut switches, mut forward) = (0, 0, 0);
    for game in 0..120 {
        let a = rng.below(teams.len() as u32) as usize;
        let c = rng.below(teams.len() as u32) as usize;
        let sets = [teams[a].sets.clone(), teams[c].sets.clone()];
        let mut b = Battle::new(sets, Chance::seeded(game)).unwrap();
        b.turn_limit = Some(40);
        while !b.is_over() {
            if b.position_is_complete() {
                let pos = b.position();
                let mut r = Battle::from_position(&pos, b.chance.clone())
                    .unwrap_or_else(|e| panic!("game {game} turn {}: {e:?}", b.turn));
                r.turn_limit = b.turn_limit;
                if let Some(d) = diff(&pos, &r.position(), "") {
                    panic!("game {game} turn {}: rebuilt position differs at {d}", b.turn);
                }
                for side in 0..2 {
                    for perfect in [true, false] {
                        let (x, y) = (view(&b, side, perfect), view(&r, side, perfect));
                        if let Some(i) = (0..x.0.len()).find(|&i| x.0[i] != y.0[i]) {
                            panic!("game {game} turn {}: side {side} ids differ at token {} field {}",
                                b.turn, i / INT_FIELDS, i % INT_FIELDS);
                        }
                        if let Some(i) = (0..x.1.len()).find(|&i| x.1[i] != y.1[i]) {
                            panic!("game {game} turn {}: side {side} (perfect {perfect}) token {} feature {}: {} vs {}",
                                b.turn, i / MON_FLOATS, i % MON_FLOATS, x.1[i], y.1[i]);
                        }
                        if let Some(i) = (0..x.2.len()).find(|&i| x.2[i] != y.2[i]) {
                            panic!("game {game} turn {}: side {side} field feature {i}: {} vs {}",
                                b.turn, x.2[i], y.2[i]);
                        }
                    }
                    assert_eq!(b.legal_choices(side), r.legal_choices(side), "game {game} turn {}", b.turn);
                }
                checked += 1;
                if pos["requests"].as_array().unwrap().iter().any(|q| q.get("switch").is_some()) {
                    switches += 1;
                }
                // Play both on with the same choices and draws.
                let mut o = b.clone();
                let mut step_rng = Rng::new(game * 1000 + b.turn as u64);
                for step in 0..4 {
                    if o.is_over() {
                        assert!(r.is_over());
                        break;
                    }
                    let mut rng2 = step_rng;
                    if std::env::var_os("POSITION_DEBUG").is_some() {
                        let norm = |b: &Battle| {
                            let mut b = b.clone();
                            for sd in b.sides.iter_mut() {
                                for m in sd.pokemon.iter_mut() {
                                    m.original_target = None;
                                }
                            }
                            format!("{b:#?}")
                        };
                        let (x, y) = (norm(&o), norm(&r));
                        let mut shown = 0;
                        for (i, (lx, ly)) in x.lines().zip(y.lines()).enumerate() {
                            if lx != ly && shown < 40 {
                                eprintln!("step {step} line {i}: {lx}  |  {ly}");
                                shown += 1;
                            }
                        }
                    }
                    choose(&mut o, &mut step_rng);
                    choose(&mut r, &mut rng2);
                    if let Some(d) = diff(&state(&o), &state(&r), "") {
                        panic!("game {game} from turn {}, step {step}: rebuilt battle went another way at {d}",
                            b.turn);
                    }
                    forward += 1;
                }
            }
            choose(&mut b, &mut rng);
        }
    }
    eprintln!("{checked} positions rebuilt ({switches} at switch requests), {forward} steps played on");
    assert!(checked > 1000 && switches > 50);
}
