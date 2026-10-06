//! The tree search: exact on small games (it reaches the expectiminimax
//! value of the full game tree), and sound on ordinary positions.

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_dir;
use engine::enumerate::{enumerate, EnumConfig};
use engine::env::action::{self, Decision, MASK_LEN};
use engine::env::obs::{MON_FLOATS, TOKENS};
use engine::mcts::{Forest, MctsConfig};
use engine::search::{result_for_side0, solve, LEAF_FIELD, LEAF_INTS, LEAF_MONS};
use std::path::Path;

/// Positions from random play that satisfy `keep`.
fn positions(n: usize, seed: u64, keep: impl Fn(&Battle) -> bool) -> Vec<Battle> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let (teams, _) = load_dir(&dir).unwrap();
    let mut rng = Rng::new(seed);
    let mut out = Vec::new();
    let mut game = 0;
    while out.len() < n && game < 20_000 {
        game += 1;
        let pick = |rng: &mut Rng| teams[rng.below(teams.len() as u32) as usize].sets.clone();
        let mut b = Battle::new([pick(&mut rng), pick(&mut rng)], Chance::seeded(game)).unwrap();
        b.turn_limit = Some(30);
        while !b.is_over() && out.len() < n {
            if keep(&b) {
                out.push(b.clone());
                break;
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

/// Expectiminimax over every legal action pair and chance outcome.
fn exact(b: &Battle) -> f32 {
    let r = result_for_side0(b);
    if !r.is_nan() {
        return r;
    }
    let opts: Vec<Vec<Option<_>>> = (0..2)
        .map(|s| {
            if matches!(b.requests[s], SideRequest::Wait) {
                vec![None]
            } else {
                b.legal_choices(s).into_iter().map(Some).collect()
            }
        })
        .collect();
    let cfg = EnumConfig {
        max_outcomes: 100_000,
        ..EnumConfig::default()
    };
    let mut m = Vec::new();
    for a in &opts[0] {
        for c in &opts[1] {
            let e = enumerate(b, &[a.clone(), c.clone()], &cfg).unwrap();
            m.push(e.outcomes.iter().map(|o| o.prob as f32 * exact(&o.battle)).sum::<f32>());
        }
    }
    solve(&m, opts[0].len(), opts[1].len(), 3000).value
}

/// HP share difference from side 0's view in a leaf's observation.
fn heuristic(mons: &[f32]) -> f32 {
    let view = &mons[..TOKENS * MON_FLOATS];
    let side = |t: std::ops::Range<usize>| t.map(|tok| view[tok * MON_FLOATS + 1]).sum::<f32>();
    ((side(0..6) - side(6..12)) / 6.0).clamp(-1.0, 1.0)
}

/// Run a forest to `budget` with uniform priors over every legal action
/// and the HP heuristic as the value.
fn run(forest: &mut Forest, budget: u64) {
    for _ in 0..100_000 {
        let n = forest.select(budget);
        if n > 0 {
            let mut ints = vec![0i32; n * LEAF_INTS];
            let mut mons = vec![0f32; n * LEAF_MONS];
            let mut field = vec![0f32; n * LEAF_FIELD];
            let mut masks = vec![0u8; n * 2 * MASK_LEN];
            let mut dec = vec![0u8; n * 2];
            forest.policy_inputs(&mut ints, &mut mons, &mut field, &mut masks, &mut dec);
            let m = 1024;
            let mut acts = vec![-1i64; n * 2 * m];
            let mut probs = vec![0f32; n * 2 * m];
            for k in 0..n * 2 {
                let legal: Vec<usize> = (0..MASK_LEN).filter(|&i| masks[k * MASK_LEN + i] == 1).collect();
                assert!(legal.len() <= m);
                for (i, &a) in legal.iter().enumerate() {
                    acts[k * m + i] = a as i64;
                    probs[k * m + i] = 1.0 / legal.len() as f32;
                }
            }
            forest.set_policy(&acts, &probs, m);
        }
        let l = forest.expand();
        if l > 0 {
            let mut ints = vec![0i32; l * LEAF_INTS];
            let mut mons = vec![0f32; l * LEAF_MONS];
            let mut field = vec![0f32; l * LEAF_FIELD];
            forest.leaf_inputs(&mut ints, &mut mons, &mut field);
            let values: Vec<f32> = (0..l).map(|i| heuristic(&mons[i * LEAF_MONS..])).collect();
            forest.set_values(&values);
        }
        if n == 0 && l == 0 {
            return;
        }
    }
    panic!("the search didn't settle");
}

#[test]
fn solves_small_endgames_exactly() {
    // One Pokemon each, and the game ends within two turns (turn cap).
    let mut battles = positions(8, 3, |b| {
        b.turn >= 3
            && (0..2).all(|s| b.sides[s].pokemon_left == 1 && matches!(b.requests[s], SideRequest::Move(_)))
    });
    assert!(battles.len() >= 5, "only {} endgames", battles.len());
    for b in &mut battles {
        b.turn_limit = Some(b.turn + 1);
    }
    let cfg = MctsConfig {
        root_candidates: 64,
        node_candidates: 64,
        max_candidates: 64,
        max_outcomes: 100_000,
        solve_iters: 3000,
        sims_per_wave: 64,
        ..MctsConfig::default()
    };
    let mut forest = Forest::new(battles.clone(), cfg, true, 4);
    run(&mut forest, u64::MAX);
    for (b, r) in battles.iter().zip(forest.results()) {
        let want = exact(b);
        assert!(r.exact, "the search didn't solve the endgame ({} nodes)", r.nodes);
        assert!((r.value - want).abs() < 0.02, "search {} vs exact {}", r.value, want);
    }
}

#[test]
fn searches_ordinary_positions_soundly() {
    let battles = positions(12, 7, |b| {
        b.turn >= 2 && (0..2).all(|s| matches!(b.requests[s], SideRequest::Move(_)))
    });
    let cfg = MctsConfig::default();
    let mut forest = Forest::new(battles.clone(), cfg.clone(), true, 4);
    let budget = 800;
    run(&mut forest, budget);
    let mut deep = 0;
    for (b, r) in battles.iter().zip(forest.results()) {
        assert!(r.value.abs() <= 1.0);
        assert!(r.gap < 0.05, "gap {}", r.gap);
        for s in 0..2 {
            let sum: f32 = r.strategy[s].iter().sum();
            assert!((sum - 1.0).abs() < 1e-3);
            let mut mask = vec![0u8; MASK_LEN];
            assert_ne!(action::legal_mask(b, s, &mut mask), Decision::None);
            assert!(r.candidates[s].iter().all(|&a| mask[a as usize] == 1));
            assert!(r.candidates[s].len() > cfg.root_candidates, "the root never widened");
        }
        // One wave can overshoot the budget a little.
        assert!(r.leaf_evals < budget + 600, "{} leaf evaluations", r.leaf_evals);
        deep += (r.max_depth >= 2) as usize;
    }
    assert!(deep * 2 >= battles.len(), "the search rarely looks past the next turn");
}
