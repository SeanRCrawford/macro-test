//! Crash hunt through chance enumeration, as search explores the game: at
//! positions from mixed greedy/random play, enumerate every chance outcome
//! of a few random joint actions, replaying each branch on its own so a
//! panicking branch is pinned down exactly.
//!
//!     cargo run --release --example stress_enum -- [games] [threads]
//!     SELFPLAY_TRACE=1 cargo run --release --example stress_enum -- replay GAME STEP CELL PATH
//!
//! A crash prints the arguments for `replay` (PATH is the classes taken,
//! comma-separated).

use engine::battle::choice::{SideChoice, SideRequest};
use engine::battle::Battle;
use engine::chance::{Chance, Rng, Script};
use engine::corpus::load_sources;
use engine::env::policy::greedy;
use engine::env::TeamSampler;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

const CELLS: usize = 3;

/// Out of 10, how often a side plays the greedy baseline (SHARE, default 7).
fn share() -> u32 {
    std::env::var("SHARE").ok().and_then(|s| s.parse().ok()).unwrap_or(7)
}
const MAX_BRANCHES: usize = 400;

fn message(e: &(dyn std::any::Any + Send)) -> String {
    e.downcast_ref::<&str>()
        .map(|s| s.to_string())
        .or_else(|| e.downcast_ref::<String>().cloned())
        .unwrap_or_default()
}

/// Run one branch: the turn from `b` with `choices`, taking `path`.
/// Returns the classes drawn (with their counts) or the panic message.
fn branch(b: &Battle, choices: &[Option<SideChoice>; 2], path: &[u8]) -> Result<Vec<(u8, u8)>, String> {
    let mut x = b.clone();
    x.chance = Chance::Scripted(Box::new(Script::new(path.to_vec(), 1)));
    let r = catch_unwind(AssertUnwindSafe(|| {
        x.choose(choices.clone()).expect("legal choice");
        x
    }));
    match r {
        Ok(x) => match &x.chance {
            Chance::Scripted(s) => Ok(s.trace.iter().map(|d| (d.taken, d.probs.len() as u8)).collect()),
            _ => unreachable!(),
        },
        Err(e) => Err(message(e.as_ref())),
    }
}

/// Every branch of the turn, depth first. Returns the first panic: (path,
/// message).
fn hunt(b: &Battle, choices: &[Option<SideChoice>; 2]) -> Option<(Vec<u8>, String)> {
    let mut stack = vec![Vec::new()];
    let mut runs = 0;
    while let Some(prefix) = stack.pop() {
        runs += 1;
        if runs > MAX_BRANCHES {
            return None;
        }
        match branch(b, choices, &prefix) {
            Err(m) => return Some((prefix, m)),
            Ok(trace) => {
                for i in prefix.len()..trace.len() {
                    for c in 1..trace[i].1 {
                        let mut p: Vec<u8> = trace[..i].iter().map(|t| t.0).collect();
                        p.push(c);
                        stack.push(p);
                    }
                }
            }
        }
    }
    None
}

/// The game's position at every step, with the joint choices played and
/// the cells tried there; calls `visit(step, battle, cells)` at each.
fn play(sampler: &TeamSampler, g: u64, mut visit: impl FnMut(usize, &Battle, &[[Option<SideChoice>; 2]]) -> bool) {
    let mut rng = Rng::new(g);
    let teams = [sampler.sample(&mut rng), sampler.sample(&mut rng)];
    let sets = teams.map(|t| sampler.team(t).sets.clone());
    let mut b = Battle::new(sets, Chance::seeded(g)).unwrap();
    b.turn_limit = Some(30);
    let mut step = 0;
    while !b.is_over() {
        let pick = |b: &Battle, side: usize, rng: &mut Rng| -> Option<SideChoice> {
            if matches!(b.requests[side], SideRequest::Wait) {
                return None;
            }
            if rng.below(10) < share() {
                if let Some(c) = greedy(b, side) {
                    return Some(c);
                }
            }
            let o = b.legal_choices(side);
            Some(o[rng.below(o.len() as u32) as usize].clone())
        };
        if !matches!(b.requests[0], SideRequest::TeamPreview) {
            let cells: Vec<[Option<SideChoice>; 2]> = (0..CELLS)
                .map(|_| [pick(&b, 0, &mut rng), pick(&b, 1, &mut rng)])
                .collect();
            if !visit(step, &b, &cells) {
                return;
            }
        }
        let choices = [pick(&b, 0, &mut rng), pick(&b, 1, &mut rng)];
        b.choose(choices).expect("legal choice");
        step += 1;
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    // The default corpus folders (python/selfplay/env.py DEFAULT_CORPUS).
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..");
    let mut sources = vec![root.join("data/corpus").display().to_string()];
    for d in ["../data/teams", "../data/my_teams"] {
        if root.join(d).is_dir() {
            sources.push(format!("{}=4", root.join(d).display()));
        }
    }
    let sampler = TeamSampler::new(load_sources(&sources).expect("corpus").0);
    if args.get(1).map(String::as_str) == Some("replay") {
        let g: u64 = args[2].parse().unwrap();
        let want: usize = args[3].parse().unwrap();
        let cell: usize = args[4].parse().unwrap();
        let path: Vec<u8> = args
            .get(5)
            .map(|p| p.split(',').filter(|s| !s.is_empty()).map(|x| x.parse().unwrap()).collect())
            .unwrap_or_default();
        play(&sampler, g, |step, b, cells| {
            if step < want {
                return true;
            }
            println!("{}", b.snapshot());
            let c = &cells[cell];
            println!(
                "choices: {:?}",
                c.iter()
                    .enumerate()
                    .map(|(s, x)| x.as_ref().map(|x| x.to_showdown(&b.requests[s])))
                    .collect::<Vec<_>>()
            );
            println!("{:?}", branch(b, c, &path));
            false
        });
        return;
    }
    let games: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(20_000);
    let threads: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(4);
    std::panic::set_hook(Box::new(|_| {}));
    let next = AtomicU64::new(0);
    let crashes = AtomicU64::new(0);
    std::thread::scope(|s| {
        for _ in 0..threads {
            s.spawn(|| loop {
                let g = next.fetch_add(1, Ordering::Relaxed);
                if g >= games {
                    break;
                }
                if g.is_multiple_of(1000) {
                    eprintln!("game {g}");
                }
                let r = catch_unwind(AssertUnwindSafe(|| {
                    play(&sampler, g, |step, b, cells| {
                        for (i, c) in cells.iter().enumerate() {
                            if let Some((path, m)) = hunt(b, c) {
                                crashes.fetch_add(1, Ordering::Relaxed);
                                let p: Vec<String> = path.iter().map(|x| x.to_string()).collect();
                                println!("CRASH {m}: replay {g} {step} {i} {}", p.join(","));
                                return false;
                            }
                        }
                        true
                    })
                }));
                if let Err(e) = r {
                    println!("CRASH in plain play, game {g}: {}", message(e.as_ref()));
                }
            });
        }
    });
    println!("{games} games, {} crashes", crashes.load(Ordering::Relaxed));
}
