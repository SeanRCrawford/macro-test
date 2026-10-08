//! Engine speed of the tree search (DESIGN.md 4.16): the trees' own work
//! (selection, chance enumeration, observations, backup) with the network
//! replaced by uniform priors and an HP-share value.
//!
//!     cargo run --release --example bench_mcts -- [roots] [budget] [threads]

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_dir;
use engine::env::action::MASK_LEN;
use engine::env::obs::{MON_FLOATS, TOKENS};
use engine::mcts::{Forest, MctsConfig};
use engine::search::{LEAF_FIELD, LEAF_INTS, LEAF_MONS};
use std::path::PathBuf;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: usize = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(64);
    let budget: u64 = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(800);
    let threads: usize = args.get(3).and_then(|a| a.parse().ok()).unwrap_or(1);
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let (teams, _) = load_dir(&dir).expect("corpus");
    let mut rng = Rng::new(3);
    let mut roots = Vec::new();
    let mut game = 0;
    while roots.len() < n {
        game += 1;
        let pick = |rng: &mut Rng| teams[rng.below(teams.len() as u32) as usize].sets.clone();
        let mut b = Battle::new([pick(&mut rng), pick(&mut rng)], Chance::seeded(game)).unwrap();
        b.turn_limit = Some(30);
        let stop = 2 + rng.below(6);
        while !b.is_over() {
            if b.turn >= stop && (0..2).all(|s| matches!(b.requests[s], SideRequest::Move(_))) {
                roots.push(b.clone());
                break;
            }
            let choices = [0, 1].map(|s| {
                (!matches!(b.requests[s], SideRequest::Wait)).then(|| {
                    let o = b.legal_choices(s);
                    o[rng.below(o.len() as u32) as usize].clone()
                })
            });
            b.choose(choices).unwrap();
        }
    }
    let mut forest = Forest::new(roots, MctsConfig::default(), true, threads);
    let t = Instant::now();
    let (mut leaves, mut policies) = (0usize, 0usize);
    loop {
        let k = forest.select(budget);
        if k > 0 {
            policies += k;
            let mut ints = vec![0i32; k * LEAF_INTS];
            let mut mons = vec![0f32; k * LEAF_MONS];
            let mut field = vec![0f32; k * LEAF_FIELD];
            let mut masks = vec![0u8; k * 2 * MASK_LEN];
            let mut dec = vec![0u8; k * 2];
            forest.policy_inputs(&mut ints, &mut mons, &mut field, &mut masks, &mut dec);
            let m = 64;
            let mut acts = vec![-1i64; k * 2 * m];
            let mut probs = vec![0f32; k * 2 * m];
            for j in 0..k * 2 {
                let legal: Vec<usize> = (0..MASK_LEN).filter(|&i| masks[j * MASK_LEN + i] == 1).take(m).collect();
                for (i, &a) in legal.iter().enumerate() {
                    acts[j * m + i] = a as i64;
                    probs[j * m + i] = 1.0 / legal.len() as f32;
                }
            }
            forest.set_policy(&acts, &probs, m);
        }
        let l = forest.expand();
        if l > 0 {
            leaves += l;
            let mut ints = vec![0i32; l * LEAF_INTS];
            let mut mons = vec![0f32; l * LEAF_MONS];
            let mut field = vec![0f32; l * LEAF_FIELD];
            forest.leaf_inputs(&mut ints, &mut mons, &mut field);
            let values: Vec<f32> = (0..l)
                .map(|i| {
                    let v = &mons[i * LEAF_MONS..];
                    let side = |r: std::ops::Range<usize>| r.map(|t| v[t * MON_FLOATS + 1]).sum::<f32>();
                    ((side(0..6) - side(6..TOKENS)) / 6.0).clamp(-1.0, 1.0)
                })
                .collect();
            forest.set_values(&values);
        }
        if forest.finished(budget) {
            break;
        }
    }
    let secs = t.elapsed().as_secs_f64();
    println!(
        "{n} roots, budget {budget}, {threads} threads: {secs:.2}s, {leaves} leaves ({:.1} us/leaf), \
         {policies} policy positions, {:.0} ms/root",
        secs * 1e6 / leaves as f64,
        secs * 1000.0 / n as f64
    );
}
