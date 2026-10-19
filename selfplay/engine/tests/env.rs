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
    let mut pruned = 0;
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
            // The bot's actions: every legal choice (no two sharing an
            // index) but attacks on its own partner for nothing.
            let b = env.battle(k / 2);
            let all = b.legal_choices(k % 2);
            let kept = all.iter().filter(|c| !action::wasted(b, k % 2, c)).count();
            assert_eq!(legal.len(), if kept == 0 { all.len() } else { kept });
            assert!(legal.iter().all(|&i| all.iter().any(|c| action::index(c) == i)));
            pruned += all.len() - legal.len();
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
    assert!(pruned > 0, "no attack on a partner was left out");
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
        engine::env::obs::observe(env.battle(g), 0, false, None, &mut ints, &mut mons, &mut field);
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

#[test]
fn greedy_beats_random() {
    // Side 0 greedy, side 1 random: greedy should win most games.
    let n = 64;
    let mut env = VecEnv::new(n, sampler(), EnvConfig::default());
    let mut masks = vec![0u8; MASK_LEN];
    let mut greedy = vec![-1i64; n * 2];
    let mut finished: Vec<Option<Finished>> = vec![None; n];
    let mut rng = Rng::new(9);
    let (mut games, mut wins) = (0, 0.0);
    while games < 300 {
        env.greedy_actions(&mut greedy);
        let mut actions = vec![-1i64; n * 2];
        for g in 0..n {
            actions[2 * g] = greedy[2 * g];
            let b = env.battle(g);
            if action::legal_mask(b, 1, &mut masks) != Decision::None {
                let legal: Vec<usize> = (0..MASK_LEN).filter(|&i| masks[i] == 1).collect();
                actions[2 * g + 1] = legal[rng.below(legal.len() as u32) as usize] as i64;
            }
        }
        env.step(&actions, &mut finished).unwrap();
        for f in finished.iter().flatten() {
            games += 1;
            wins += (f.reward[0] + 1.0) as f64 / 2.0;
        }
    }
    let rate = wins / games as f64;
    eprintln!("greedy vs random: {rate:.3} over {games} games");
    assert!(rate > 0.7, "greedy only won {rate:.3}");
}

#[test]
fn a_panicking_game_is_reported_and_replaced() {
    let n = 16;
    let mut env = VecEnv::new(n, sampler(), EnvConfig::default());
    let mut greedy = vec![-1i64; n * 2];
    let mut finished: Vec<Option<Finished>> = vec![None; n];
    for _ in 0..3 {
        env.greedy_actions(&mut greedy);
        env.step(&greedy, &mut finished).unwrap();
    }
    env.inject_panic(11);
    env.greedy_actions(&mut greedy);
    env.step(&greedy, &mut finished).unwrap();
    let f = finished[11].expect("the panicking game ends");
    assert_eq!(f.reward, [0.0, 0.0]);
    assert_eq!(env.battle(11).turn, 0, "replaced by a new game");
    let crashes = env.take_crashes();
    assert_eq!(crashes.len(), 1);
    let r: serde_json::Value = serde_json::from_str(&crashes[0]).unwrap();
    assert_eq!(r["panic"], "injected panic");
    assert!(r["location"].as_str().unwrap().contains("mod.rs"), "{r}");
    assert_eq!(r["actions"].as_array().unwrap().len(), 4);
    assert!(env.take_crashes().is_empty());
}

/// The bot's mask leaves out a damaging move into its own partner (High
/// Horsepower into Garchomp, Rock Slide is a spread move and stays), unless
/// the partner gains from it (Earth Eater heals on a Ground hit). The engine
/// still takes both: they're legal on Showdown.
#[test]
fn attacks_on_a_partner_are_left_out_unless_it_gains() {
    use engine::battle::choice::SideChoice;
    use engine::battle::Battle;
    use engine::chance::Chance;
    use engine::team::parse_paste;
    let team = |ally: &str| {
        parse_paste(&format!(
            "Excadrill @ Focus Sash\nAbility: Sand Rush\nLevel: 50\n- High Horsepower\n- Iron Head\n- Rock Slide\n- Protect\n\n\
             Garchomp @ Sitrus Berry\nAbility: {ally}\nLevel: 50\n- Dragon Claw\n- Earthquake\n- Rock Slide\n- Protect\n\n\
             Incineroar @ Sitrus Berry\nAbility: Intimidate\nLevel: 50\n- Fake Out\n- Protect\n- Flare Blitz\n- Parting Shot\n\n\
             Pelipper @ Damp Rock\nAbility: Drizzle\nLevel: 50\n- Hurricane\n- Protect\n- Tailwind\n- Weather Ball"
        ))
        .unwrap()
    };
    for (ally, kept) in [("Rough Skin", false), ("Earth Eater", true)] {
        let mut b = Battle::new([team(ally), team("Rough Skin")], Chance::policy(0.5)).unwrap();
        let preview = [0, 1].map(|_| Some(SideChoice::from_showdown("team 1234").unwrap()));
        b.choose(preview).unwrap();
        let into_partner = SideChoice::from_showdown("move 1 -2, move 4").unwrap(); // Excadrill HH -> Garchomp
        let into_foe = SideChoice::from_showdown("move 1 1, move 4").unwrap();
        assert!(b.legal_choices(0).contains(&into_partner));
        let mut mask = vec![0u8; MASK_LEN];
        action::play_mask(&b, 0, &mut mask);
        assert_eq!(mask[action::index(&into_partner)] == 1, kept, "{ally}");
        assert_eq!(mask[action::index(&into_foe)], 1);
        let spread = SideChoice::from_showdown("move 3, move 4").unwrap();
        assert_eq!(mask[action::index(&spread)], 1);
    }
}
