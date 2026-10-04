//! The training environment: random masked play through VecEnv.

use engine::chance::Rng;
use engine::corpus::load_dir;
use engine::env::action::{self, Decision, MASK_LEN};
use engine::env::obs::{FIELD_FLOATS, INT_FIELDS, MON_FLOATS, TOKENS};
use engine::env::{EnvConfig, Finished, TeamSampler, VecEnv};
use std::path::Path;

fn sampler() -> TeamSampler {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../data/corpus");
    TeamSampler::new(load_dir(&dir).unwrap().0)
}

#[test]
fn every_legal_choice_has_its_own_index() {
    let env = VecEnv::new(64, sampler(), EnvConfig::default());
    let mut mask = vec![0u8; MASK_LEN];
    for g in 0..env.len() {
        for side in 0..2 {
            let b = env.battle(g);
            action::legal_mask(b, side, &mut mask);
            let legal = b.legal_choices(side);
            assert_eq!(mask.iter().filter(|&&m| m == 1).count(), legal.len());
            for c in legal {
                let d = action::decision(b, side);
                assert_eq!(action::choice(d, action::index(&c)), Some(c));
            }
        }
    }
}

#[test]
fn random_masked_play() {
    let n = 32;
    let mut env = VecEnv::new(
        n,
        sampler(),
        EnvConfig {
            perfect_info: false,
            threads: 4,
            ..EnvConfig::default()
        },
    );
    let mut ints = vec![0i32; n * 2 * TOKENS * INT_FIELDS];
    let mut mons = vec![0f32; n * 2 * TOKENS * MON_FLOATS];
    let mut field = vec![0f32; n * 2 * FIELD_FLOATS];
    let mut masks = vec![0u8; n * 2 * MASK_LEN];
    let mut decisions = vec![0u8; n * 2];
    let mut finished: Vec<Option<Finished>> = vec![None; n];
    let mut rng = Rng::new(5);
    let mut games = 0;
    for _ in 0..3000 {
        env.observe(&mut ints, &mut mons, &mut field, &mut masks, &mut decisions);
        assert!(mons.iter().chain(&field).all(|x| x.is_finite()));
        let mut actions = vec![-1i64; n * 2];
        for k in 0..n * 2 {
            if decisions[k] == Decision::None as u8 {
                continue;
            }
            let legal: Vec<usize> = masks[k * MASK_LEN..(k + 1) * MASK_LEN]
                .iter()
                .enumerate()
                .filter(|(_, &m)| m == 1)
                .map(|(i, _)| i)
                .collect();
            assert!(!legal.is_empty(), "a decision with nothing legal");
            // No two legal choices share an index.
            assert_eq!(legal.len(), env.battle(k / 2).legal_choices(k % 2).len());
            actions[k] = legal[rng.below(legal.len() as u32) as usize] as i64;
        }
        env.step(&actions, &mut finished).unwrap();
        for f in finished.iter().flatten() {
            games += 1;
            assert_eq!(f.reward[0], -f.reward[1]);
            assert!(f.turns <= 30);
        }
    }
    assert!(games > 50, "only {games} games finished");
}

#[test]
fn hidden_information_stays_hidden() {
    // Under Open Team Sheets the opponent's stats are hidden, and a brought
    // Pokemon that hasn't appeared looks like one left at home.
    let env = VecEnv::new(
        8,
        sampler(),
        EnvConfig {
            perfect_info: false,
            ..EnvConfig::default()
        },
    );
    let mut ints = vec![0i32; TOKENS * INT_FIELDS];
    let mut mons = vec![0f32; TOKENS * MON_FLOATS];
    let mut field = vec![0f32; FIELD_FLOATS];
    for g in 0..env.len() {
        engine::env::obs::observe(env.battle(g), 0, false, &mut ints, &mut mons, &mut field);
        for tok in 6..12 {
            let f = &mons[tok * MON_FLOATS..(tok + 1) * MON_FLOATS];
            assert_eq!(f[8], 0.0, "opponent stats shown");
            assert!(f[3..8].iter().all(|&x| x == 0.0));
        }
        for tok in 0..6 {
            assert_eq!(mons[tok * MON_FLOATS + 8], 1.0, "own stats hidden");
        }
    }
}
