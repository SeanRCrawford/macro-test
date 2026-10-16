//! Speed benchmark (DESIGN.md 4.7): random legal play between corpus teams,
//! weighted by placement, with the 30-turn training cap, on one thread.
//!
//!     cargo run --release --example bench -- [games] [corpus dir]

#![allow(clippy::needless_range_loop)]

use engine::battle::choice::SideRequest;
use engine::battle::Battle;
use engine::chance::{Chance, Rng};
use engine::corpus::{load_dir, CorpusTeam};
use std::path::PathBuf;
use std::time::{Duration, Instant};

fn pick<'a>(teams: &'a [CorpusTeam], total: f64, rng: &mut Rng) -> &'a CorpusTeam {
    let mut x = (rng.next_u64() >> 11) as f64 / (1u64 << 53) as f64 * total;
    for t in teams {
        x -= t.weight;
        if x < 0.0 {
            return t;
        }
    }
    teams.last().unwrap()
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let games: u64 = args.get(1).and_then(|a| a.parse().ok()).unwrap_or(5000);
    let dir = args
        .get(2)
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../data/corpus"));
    let (teams, _) = load_dir(&dir).expect("corpus");
    let total: f64 = teams.iter().map(|t| t.weight).sum();
    let mut rng = Rng::new(1);

    let (mut turns, mut decisions, mut draws) = (0u64, 0u64, 0u64);
    let (mut choose, mut legal) = (Duration::ZERO, Duration::ZERO);
    let start = Instant::now();
    for game in 0..games {
        let a = pick(&teams, total, &mut rng).sets.clone();
        let b = pick(&teams, total, &mut rng).sets.clone();
        let mut battle = Battle::new([a, b], Chance::seeded(game)).expect("supported teams");
        battle.turn_limit = Some(30);
        while !battle.is_over() {
            let t = Instant::now();
            let mut choices = [None, None];
            for side in 0..2 {
                if matches!(battle.requests[side], SideRequest::Wait) {
                    continue;
                }
                let options = battle.legal_choices(side);
                choices[side] = Some(options[rng.below(options.len() as u32) as usize].clone());
            }
            legal += t.elapsed();
            let t = Instant::now();
            battle.choose(choices).expect("legal choice");
            choose += t.elapsed();
            decisions += 1;
        }
        turns += battle.turn.min(30) as u64;
        draws += (battle.outcome == Some(engine::battle::Outcome::Tie)) as u64;
    }
    let secs = start.elapsed().as_secs_f64();
    println!(
        "{games} games, {turns} turns, {decisions} decisions, {draws} draws at the cap, {secs:.2}s"
    );
    println!(
        "{:.0} turns/s, {:.0} games/s; choose {:.1} us/turn, legal choices {:.1} us/turn",
        turns as f64 / secs,
        games as f64 / secs,
        choose.as_secs_f64() * 1e6 / turns as f64,
        legal.as_secs_f64() * 1e6 / turns as f64,
    );
}
