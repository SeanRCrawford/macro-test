//! Speed of the damage features (Battle::damage_table) and of a turn, at
//! positions from random play.
//!
//!     cargo run --release --example bench_damage -- [positions] [repeats]

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::load_dir;
use engine::damage::DamageCache;
use engine::enumerate::{enumerate, EnumConfig};
use std::hint::black_box;
use std::path::PathBuf;
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let n: usize = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(2000);
    let reps: usize = args.get(2).and_then(|a| a.parse().ok()).unwrap_or(20);
    let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    let (teams, _) = load_dir(&dir).expect("corpus");
    let mut rng = Rng::new(1);
    let mut pos = Vec::new();
    let mut game = 0;
    while pos.len() < n {
        game += 1;
        let pick = |rng: &mut Rng| teams[rng.below(teams.len() as u32) as usize].sets.clone();
        let mut b = Battle::new([pick(&mut rng), pick(&mut rng)], Chance::seeded(game)).unwrap();
        b.turn_limit = Some(30);
        while !b.is_over() && pos.len() < n {
            if (0..2).all(|s| matches!(b.requests[s], SideRequest::Move(_))) {
                pos.push(b.clone());
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
    let mut sum = 0.0f32;
    let t = Instant::now();
    for _ in 0..reps {
        for b in &pos {
            let d = black_box(b).damage_table();
            sum += d[0][0][1];
        }
    }
    let us = t.elapsed().as_secs_f64() * 1e6 / (reps * pos.len()) as f64;
    println!("damage_table: {us:.2} us/position ({} positions, checksum {sum:.3})", pos.len());
    let t = Instant::now();
    let mut rng = Rng::new(9);
    for _ in 0..reps {
        for b in &pos {
            let mut x = b.clone();
            let choices = [0, 1].map(|s| {
                let o = x.legal_choices(s);
                Some(o[rng.below(o.len() as u32) as usize].clone())
            });
            x.choose(choices).unwrap();
            black_box(&x);
        }
    }
    let us = t.elapsed().as_secs_f64() * 1e6 / (reps * pos.len()) as f64;
    println!("clone + turn: {us:.2} us/position");

    // The search's workload: every chance outcome of a turn (one tree cell),
    // each needing its damage table.
    let mut rng = Rng::new(5);
    let cells: Vec<Vec<Battle>> = pos
        .iter()
        .map(|b| {
            let choices = [0, 1].map(|s| {
                let o = b.legal_choices(s);
                Some(o[rng.below(o.len() as u32) as usize].clone())
            });
            let e = enumerate(b, &choices, &EnumConfig::default()).unwrap();
            e.outcomes.into_iter().map(|o| o.battle).filter(|b| !b.is_over()).collect()
        })
        .collect();
    let leaves: usize = cells.iter().map(Vec::len).sum();
    let t = Instant::now();
    let plain: Vec<_> = cells.iter().flatten().map(|b| b.damage_table()).collect();
    let us_plain = t.elapsed().as_secs_f64() * 1e6 / leaves as f64;
    let (mut hits, mut misses) = (0, 0);
    let t = Instant::now();
    let mut cached = Vec::with_capacity(leaves);
    for cell in &cells {
        let mut cache = DamageCache::new();
        for b in cell {
            cached.push(b.damage_table_with(Some(&mut cache)));
        }
        hits += cache.hits;
        misses += cache.misses;
    }
    let us_cached = t.elapsed().as_secs_f64() * 1e6 / leaves as f64;
    assert!(plain == cached, "the cache changed a damage table");
    let t = Instant::now();
    let mut cache = DamageCache::new();
    for cell in &cells {
        for b in cell {
            std::hint::black_box(b.damage_table_with(Some(&mut cache)));
        }
    }
    println!("one cache for all cells: {:.2} us/leaf", t.elapsed().as_secs_f64() * 1e6 / leaves as f64);
    let t = Instant::now();
    for cell in &cells {
        for b in cell {
            std::hint::black_box(b.damage_table_with(None));
        }
    }
    println!("plain again: {:.2} us/leaf", t.elapsed().as_secs_f64() * 1e6 / leaves as f64);
    println!(
        "cell outcomes ({leaves}): {us_plain:.2} us/leaf uncached, {us_cached:.2} cached \
         (hit rate {:.1}%), identical",
        100.0 * hits as f64 / (hits + misses).max(1) as f64
    );
}
