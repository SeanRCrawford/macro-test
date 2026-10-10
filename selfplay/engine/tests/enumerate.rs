//! Chance enumeration: outcomes cover the whole probability and agree with
//! sampling.

#![allow(clippy::needless_range_loop)]

use engine::battle::choice::{SideChoice, SideRequest};
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_dir;
use engine::enumerate::{enumerate, EnumConfig};
use std::path::Path;

/// Mid-game positions where both sides choose moves, with a random joint
/// choice for each.
fn positions(n: usize, seed: u64) -> Vec<(Battle, [Option<SideChoice>; 2])> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let (teams, _) = load_dir(&dir).unwrap();
    let mut rng = Rng::new(seed);
    let mut out = Vec::new();
    let mut game = 0;
    while out.len() < n {
        game += 1;
        let pick = |rng: &mut Rng| teams[rng.below(teams.len() as u32) as usize].sets.clone();
        let mut b = Battle::new([pick(&mut rng), pick(&mut rng)], Chance::seeded(game)).unwrap();
        b.turn_limit = Some(30);
        while !b.is_over() && out.len() < n {
            let mut choices = [None, None];
            for s in 0..2 {
                if !matches!(b.requests[s], SideRequest::Wait) {
                    let o = b.legal_choices(s);
                    choices[s] = Some(o[rng.below(o.len() as u32) as usize].clone());
                }
            }
            let both = (0..2).all(|s| matches!(b.requests[s], SideRequest::Move(_)));
            if both && rng.below(3) == 0 {
                out.push((b.clone(), choices.clone()));
            }
            b.choose(choices).unwrap();
        }
    }
    out
}

/// Per Pokemon (side, uid): fainted, HP fraction.
fn summary(b: &Battle) -> Vec<(f64, f64)> {
    let mut v = Vec::new();
    for s in 0..2 {
        let mut mons: Vec<_> = b.sides[s].pokemon.iter().collect();
        mons.sort_by_key(|m| m.uid);
        for m in mons {
            v.push((m.fainted as u8 as f64, m.hp as f64 / m.max_hp().max(1) as f64));
        }
    }
    v
}

#[test]
fn outcomes_cover_the_whole_probability() {
    let cfg = EnumConfig {
        max_outcomes: 100_000,
        ..EnumConfig::default()
    };
    let mut counts = Vec::new();
    for (b, c) in positions(300, 1) {
        let e = enumerate(&b, &c, &cfg).unwrap();
        let total: f64 = e.outcomes.iter().map(|o| o.prob).sum();
        assert!((total - 1.0).abs() < 1e-9, "outcomes sum to {total} ({} outcomes, {} unexplored)", e.outcomes.len(), e.unexplored);
        assert!(e.unexplored < 1e-9);
        counts.push(e.outcomes.len());
    }
    counts.sort();
    eprintln!(
        "outcomes per cell (KO bands): median {}, p90 {}, max {}",
        counts[counts.len() / 2],
        counts[counts.len() * 9 / 10],
        counts.last().unwrap()
    );
}

#[test]
fn exact_enumeration_matches_sampling() {
    // With 16 bands every damage roll is its own outcome, so expectations
    // must agree with plain sampling up to sampling error.
    let cfg = EnumConfig {
        max_outcomes: 4_000,
        roll_bands: 16,
        seed: 0,
    };
    let samples = 400;
    let mut checked = 0;
    let mut worst: f64 = 0.0;
    for (i, (b, c)) in positions(150, 2).into_iter().enumerate() {
        let e = enumerate(&b, &c, &cfg).unwrap();
        if e.unexplored > 0.0 {
            continue;
        }
        let k = summary(&b).len();
        let mut exact = vec![(0.0, 0.0); k];
        for o in &e.outcomes {
            for (x, y) in exact.iter_mut().zip(summary(&o.battle)) {
                x.0 += o.prob * y.0;
                x.1 += o.prob * y.1;
            }
        }
        let mut mean = vec![(0.0, 0.0); k];
        for s in 0..samples {
            let mut bb = b.clone();
            bb.chance = Chance::seeded(((i as u64) << 20) + s);
            bb.choose(c.clone()).unwrap();
            for (x, y) in mean.iter_mut().zip(summary(&bb)) {
                x.0 += y.0 / samples as f64;
                x.1 += y.1 / samples as f64;
            }
        }
        for (x, y) in exact.iter().zip(&mean) {
            // Bernoulli and [0, 1] means over 400 samples: 5 standard errors.
            let tol = 5.0 * (0.25f64 / samples as f64).sqrt();
            worst = worst.max((x.0 - y.0).abs()).max((x.1 - y.1).abs());
            assert!((x.0 - y.0).abs() < tol, "faint chance {} vs sampled {}", x.0, y.0);
            assert!((x.1 - y.1).abs() < tol, "HP {} vs sampled {}", x.1, y.1);
        }
        checked += 1;
    }
    eprintln!("{checked} positions checked, worst difference {worst:.4}");
    assert!(checked >= 20, "only {checked} positions enumerated fully");
}

