//! Crash hunt: many games between corpus teams, each side mixing the
//! greedy-damage baseline with random legal choices (a trained policy
//! reaches positions random play rarely does). A panicking game is reported
//! with its seed and choices so it can be replayed.
//!
//!     cargo run --release --example stress -- [games] [threads] [greedy share]

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_dir;
use engine::env::policy::greedy;
use engine::env::TeamSampler;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let games: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(100_000);
    let threads: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(4);
    let share: f64 = args.get(3).and_then(|a| a.parse().ok()).unwrap_or(0.7);
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let sampler = TeamSampler::new(load_dir(&dir).expect("corpus").0);
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
                let mut rng = Rng::new(g);
                let teams = [sampler.sample(&mut rng), sampler.sample(&mut rng)];
                let mut log = Vec::new();
                let r = catch_unwind(AssertUnwindSafe(|| {
                    let sets = teams.map(|t| sampler.team(t).sets.clone());
                    let mut b = Battle::new(sets, Chance::seeded(g)).unwrap();
                    b.turn_limit = Some(30);
                    while !b.is_over() {
                        let mut choices = [None, None];
                        for (side, c) in choices.iter_mut().enumerate() {
                            if matches!(b.requests[side], SideRequest::Wait) {
                                continue;
                            }
                            let pick = if (rng.next_u64() >> 11) as f64 / (1u64 << 53) as f64 <= share {
                                greedy(&b, side)
                            } else {
                                None
                            };
                            *c = Some(pick.unwrap_or_else(|| {
                                let o = b.legal_choices(side);
                                o[rng.below(o.len() as u32) as usize].clone()
                            }));
                        }
                        log.push(
                            choices
                                .iter()
                                .enumerate()
                                .map(|(s, c)| c.as_ref().map(|c| c.to_showdown(&b.requests[s])))
                                .collect::<Vec<_>>(),
                        );
                        b.choose(choices).expect("legal choice");
                    }
                }));
                if let Err(e) = r {
                    let msg = e
                        .downcast_ref::<&str>()
                        .map(|s| s.to_string())
                        .or_else(|| e.downcast_ref::<String>().cloned())
                        .unwrap_or_default();
                    crashes.fetch_add(1, Ordering::Relaxed);
                    println!(
                        "CRASH game {g}: {msg}\n  teams: {} | {}\n  choices: {:?}",
                        sampler.team(teams[0]).name,
                        sampler.team(teams[1]).name,
                        log
                    );
                }
            });
        }
    });
    println!(
        "{games} games, {} crashes",
        crashes.load(Ordering::Relaxed)
    );
}
