//! The training environment (DESIGN.md phase 2): fixed action spaces,
//! per-side observations, and many games stepped together.
//!
//! Self-play: both sides of every game are decided by the caller. Each step
//! takes one action per side (ignored for a side with no decision), runs the
//! games in parallel, and starts a new game wherever one ended.

pub mod action;
pub mod obs;

use crate::battle::{Battle, Outcome};
use crate::chance::{Chance, Rng};
use crate::corpus::CorpusTeam;
use crate::team::PokemonSet;
use action::{Decision, MASK_LEN};
use obs::{FIELD_FLOATS, INT_FIELDS, MON_FLOATS, TOKENS};
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct EnvConfig {
    /// Turns before a game is a draw (DESIGN.md 4.1: 30).
    pub turn_limit: u32,
    /// Show the opponent's stats and brought four (the first training
    /// variant); otherwise Open Team Sheets hide them.
    pub perfect_info: bool,
    pub seed: u64,
    /// Worker threads for stepping; 0 = all cores.
    pub threads: usize,
}

impl Default for EnvConfig {
    fn default() -> Self {
        EnvConfig {
            turn_limit: 30,
            perfect_info: true,
            seed: 0,
            threads: 0,
        }
    }
}

/// How a game ended, for each side: 1 win, -1 loss, 0 draw.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct Finished {
    pub reward: [f32; 2],
    pub turns: u32,
}

struct Game {
    battle: Battle,
    rng: Rng,
    /// Corpus indices of the two teams.
    teams: [usize; 2],
}

/// Draws teams from the corpus by weight.
#[derive(Clone)]
pub struct TeamSampler {
    teams: Arc<Vec<CorpusTeam>>,
    cumulative: Vec<f64>,
}

impl TeamSampler {
    pub fn new(teams: Vec<CorpusTeam>) -> Self {
        let mut total = 0.0;
        let cumulative = teams
            .iter()
            .map(|t| {
                total += t.weight;
                total
            })
            .collect();
        TeamSampler {
            teams: Arc::new(teams),
            cumulative,
        }
    }

    pub fn len(&self) -> usize {
        self.teams.len()
    }

    pub fn is_empty(&self) -> bool {
        self.teams.is_empty()
    }

    pub fn team(&self, i: usize) -> &CorpusTeam {
        &self.teams[i]
    }

    pub fn sample(&self, rng: &mut Rng) -> usize {
        let total = *self.cumulative.last().expect("a non-empty corpus");
        let x = (rng.next_u64() >> 11) as f64 / (1u64 << 53) as f64 * total;
        self.cumulative
            .partition_point(|&c| c <= x)
            .min(self.teams.len() - 1)
    }
}

fn new_game(sampler: &TeamSampler, mut rng: Rng, turn_limit: u32) -> Game {
    let teams = [sampler.sample(&mut rng), sampler.sample(&mut rng)];
    let sets: [Vec<PokemonSet>; 2] = teams.map(|i| sampler.team(i).sets.clone());
    let seed = rng.next_u64();
    let mut battle = Battle::new(sets, Chance::seeded(seed)).expect("corpus teams are supported");
    battle.turn_limit = Some(turn_limit);
    Game { battle, rng, teams }
}

pub struct VecEnv {
    games: Vec<Game>,
    sampler: TeamSampler,
    config: EnvConfig,
}

impl VecEnv {
    pub fn new(n: usize, sampler: TeamSampler, config: EnvConfig) -> Self {
        assert!(!sampler.is_empty(), "no teams to play");
        let mut seeder = Rng::new(config.seed);
        let games = (0..n)
            .map(|_| new_game(&sampler, Rng::new(seeder.next_u64()), config.turn_limit))
            .collect();
        VecEnv {
            games,
            sampler,
            config,
        }
    }

    pub fn len(&self) -> usize {
        self.games.len()
    }

    pub fn is_empty(&self) -> bool {
        self.games.is_empty()
    }

    pub fn battle(&self, i: usize) -> &Battle {
        &self.games[i].battle
    }

    /// The corpus teams game `i` is playing.
    pub fn teams(&self, i: usize) -> [usize; 2] {
        self.games[i].teams
    }

    fn threads(&self) -> usize {
        let n = if self.config.threads == 0 {
            std::thread::available_parallelism().map_or(1, |n| n.get())
        } else {
            self.config.threads
        };
        n.clamp(1, self.games.len().max(1))
    }

    /// Both sides' views of every game. Per game and side (game-major):
    /// `ints` TOKENS*INT_FIELDS, `mons` TOKENS*MON_FLOATS, `field`
    /// FIELD_FLOATS, `masks` MASK_LEN (1 = legal) and `decisions` one
    /// `Decision` each.
    pub fn observe(
        &self,
        ints: &mut [i32],
        mons: &mut [f32],
        field: &mut [f32],
        masks: &mut [u8],
        decisions: &mut [u8],
    ) {
        let n = self.games.len() * 2;
        assert_eq!(ints.len(), n * TOKENS * INT_FIELDS);
        assert_eq!(mons.len(), n * TOKENS * MON_FLOATS);
        assert_eq!(field.len(), n * FIELD_FLOATS);
        assert_eq!(masks.len(), n * MASK_LEN);
        assert_eq!(decisions.len(), n);
        let chunk = CHUNK;
        let perfect = self.config.perfect_info;
        let work: Vec<_> = self
            .games
            .chunks(chunk)
            .zip(ints.chunks_mut(chunk * 2 * TOKENS * INT_FIELDS))
            .zip(mons.chunks_mut(chunk * 2 * TOKENS * MON_FLOATS))
            .zip(field.chunks_mut(chunk * 2 * FIELD_FLOATS))
            .zip(masks.chunks_mut(chunk * 2 * MASK_LEN))
            .zip(decisions.chunks_mut(chunk * 2))
            .collect();
        run_parallel(
            work,
            self.threads(),
            |(((((games, ints), mons), field), masks), decisions)| {
                for (g, game) in games.iter().enumerate() {
                    for side in 0..2 {
                        let k = g * 2 + side;
                        obs::observe(
                            &game.battle,
                            side,
                            perfect,
                            &mut ints[k * TOKENS * INT_FIELDS..(k + 1) * TOKENS * INT_FIELDS],
                            &mut mons[k * TOKENS * MON_FLOATS..(k + 1) * TOKENS * MON_FLOATS],
                            &mut field[k * FIELD_FLOATS..(k + 1) * FIELD_FLOATS],
                        );
                        decisions[k] = action::legal_mask(
                            &game.battle,
                            side,
                            &mut masks[k * MASK_LEN..(k + 1) * MASK_LEN],
                        ) as u8;
                    }
                }
            },
        );
    }

    /// Play one decision in every game: `actions[2 * g + side]` is that
    /// side's action index (ignored when it has nothing to decide). A game
    /// that ends is reported in `finished` and replaced by a new one.
    pub fn step(
        &mut self,
        actions: &[i64],
        finished: &mut [Option<Finished>],
    ) -> Result<(), String> {
        assert_eq!(actions.len(), self.games.len() * 2);
        assert_eq!(finished.len(), self.games.len());
        let chunk = CHUNK;
        let threads = self.threads();
        let sampler = &self.sampler;
        let limit = self.config.turn_limit;
        let errors = std::sync::Mutex::new(Vec::new());
        let work: Vec<_> = self
            .games
            .chunks_mut(chunk)
            .zip(actions.chunks(chunk * 2))
            .zip(finished.chunks_mut(chunk))
            .collect();
        run_parallel(work, threads, |((games, actions), finished)| {
            for (g, game) in games.iter_mut().enumerate() {
                finished[g] = None;
                if let Err(e) = step_game(game, [actions[2 * g], actions[2 * g + 1]]) {
                    errors.lock().expect("errors").push(e);
                    continue;
                }
                if let Some(outcome) = game.battle.outcome {
                    let reward = match outcome {
                        Outcome::Win(0) => [1.0, -1.0],
                        Outcome::Win(_) => [-1.0, 1.0],
                        Outcome::Tie => [0.0, 0.0],
                    };
                    finished[g] = Some(Finished {
                        reward,
                        turns: game.battle.turn.min(limit),
                    });
                    let rng = Rng::new(game.rng.next_u64());
                    *game = new_game(sampler, rng, limit);
                }
            }
        });
        let errors = errors.into_inner().expect("errors");
        match errors.first() {
            Some(e) => Err(e.clone()),
            None => Ok(()),
        }
    }
}

/// Games per work item: small, so threads stay evenly loaded.
const CHUNK: usize = 8;

/// Run `f` on every work item, on `threads` threads pulling from a shared
/// queue.
fn run_parallel<W: Send>(work: Vec<W>, threads: usize, f: impl Fn(W) + Sync) {
    if threads <= 1 {
        work.into_iter().for_each(f);
        return;
    }
    let queue = std::sync::Mutex::new(work);
    std::thread::scope(|scope| {
        for _ in 0..threads {
            scope.spawn(|| loop {
                let item = queue.lock().expect("work queue").pop();
                match item {
                    Some(w) => f(w),
                    None => break,
                }
            });
        }
    });
}

fn step_game(game: &mut Game, actions: [i64; 2]) -> Result<(), String> {
    let mut choices = [None, None];
    for side in 0..2 {
        let d = action::decision(&game.battle, side);
        if d == Decision::None {
            continue;
        }
        let a = actions[side];
        let c = usize::try_from(a)
            .ok()
            .and_then(|a| action::choice(d, a))
            .ok_or_else(|| format!("side {side}: action {a} isn't one for {d:?}"))?;
        choices[side] = Some(c);
    }
    if choices.iter().all(Option::is_none) {
        return Ok(());
    }
    game.battle.choose(choices).map_err(|e| format!("{e:?}"))
}
