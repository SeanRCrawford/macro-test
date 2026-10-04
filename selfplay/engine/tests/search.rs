//! One-turn matrix search: the matrix-game solver, and expanding positions
//! into leaves.

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_dir;
use engine::enumerate::EnumConfig;
use engine::env::action::{self, Decision, MASK_LEN};
use engine::env::obs::{MON_FLOATS, TOKENS};
use engine::search::{expand, solve, solve_all, LEAF_MONS};
use std::path::Path;

#[test]
fn solver_finds_equilibria() {
    // Rock-paper-scissors: uniform, value 0.
    let rps = [0.0, -1.0, 1.0, 1.0, 0.0, -1.0, -1.0, 1.0, 0.0];
    let s = solve(&rps, 3, 3, 2000);
    assert!(s.row.iter().chain(&s.col).all(|&p| (p - 1.0 / 3.0).abs() < 0.01), "{s:?}");
    assert!(s.value.abs() < 0.01 && s.gap < 0.01);
    // A dominated row and column get no weight.
    let m = [0.6, 0.5, 0.9, 0.4, 0.3, 0.8];
    let s = solve(&m, 2, 3, 2000);
    assert!(s.row[1] < 0.01 && s.col[2] < 0.01 && s.gap < 0.01, "{s:?}");
    assert!((s.value - 0.5).abs() < 0.01);
    // A 4x4 corner of an analysis-board matrix (side A's win chance): the
    // robust row, never below 61%, carries most of the mix, and the row
    // that is best against B1 but 30% against B2 doesn't.
    let m = [
        0.61, 0.68, 0.61, 0.67, //
        0.70, 0.40, 0.80, 0.39, //
        0.73, 0.56, 0.56, 0.57, //
        0.78, 0.30, 0.76, 0.69,
    ];
    let s = solve(&m, 4, 4, 5000);
    assert!(s.gap < 0.005, "{s:?}");
    assert!(s.row[0] > 0.5, "{s:?}");
    assert!(s.value > 0.6);
}

fn positions(n: usize, seed: u64) -> Vec<Battle> {
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
            if b.turn > 0 && rng.below(4) == 0 {
                out.push(b.clone());
            }
            let mut choices = [None, None];
            for (s, c) in choices.iter_mut().enumerate() {
                if !matches!(b.requests[s], SideRequest::Wait) {
                    let o = b.legal_choices(s);
                    *c = Some(o[rng.below(o.len() as u32) as usize].clone());
                }
            }
            b.choose(choices).unwrap();
        }
    }
    out
}

/// Up to `k` legal actions per side (the first ones in index order).
fn candidates(b: &Battle, k: usize) -> [Vec<i64>; 2] {
    let mut mask = vec![0u8; MASK_LEN];
    [0, 1].map(|side| {
        if action::legal_mask(b, side, &mut mask) == Decision::None {
            return vec![];
        }
        (0..MASK_LEN).filter(|&i| mask[i] == 1).take(k).map(|i| i as i64).collect()
    })
}

#[test]
fn expansion_covers_every_cell() {
    let battles = positions(40, 5);
    let roots: Vec<_> = battles.iter().map(|b| (b, candidates(b, 4))).collect();
    let cfg = EnumConfig {
        max_outcomes: 16,
        ..EnumConfig::default()
    };
    let e = expand(&roots, &cfg, true, 4).unwrap();
    assert_eq!(e.roots.len(), roots.len());
    assert_eq!(e.mons.len(), e.len() * LEAF_MONS);
    assert!(e.mons.iter().all(|x| x.is_finite()));
    for (r, (_, c)) in e.roots.iter().zip(&roots) {
        let k: Vec<usize> = (0..2).map(|s| c[s].len().max(1)).collect();
        assert_eq!(r.candidates[0].len() * r.candidates[1].len(), k[0] * k[1]);
        for cell in &e.cells[r.first_cell..r.first_cell + k[0] * k[1]] {
            assert!(cell.leaves >= 1);
            let p: f64 = e.probs[cell.first_leaf..cell.first_leaf + cell.leaves]
                .iter()
                .map(|&p| p as f64)
                .sum();
            assert!((p + cell.unexplored - 1.0).abs() < 1e-4, "{p} + {}", cell.unexplored);
        }
    }
    // A heuristic leaf value (side 0's HP share minus side 1's) gives
    // matrices the solver settles.
    let hp = |leaf: usize, view: usize| -> f32 {
        let m = &e.mons[leaf * LEAF_MONS + view * TOKENS * MON_FLOATS..][..TOKENS * MON_FLOATS];
        let side = |t: std::ops::Range<usize>| t.map(|tok| m[tok * MON_FLOATS + 1]).sum::<f32>();
        (side(0..6) - side(6..12)) / 6.0
    };
    let values: Vec<f32> = (0..e.len()).map(|i| (hp(i, 0) - hp(i, 1)) / 2.0).collect();
    let solved = solve_all(&e, &values, 2000);
    let worst = solved.iter().map(|(_, s)| s.gap).fold(0.0f32, f32::max);
    assert!(worst < 0.01, "solver gap {worst}");
}
