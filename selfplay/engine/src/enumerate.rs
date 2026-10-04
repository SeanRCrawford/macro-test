//! A turn's chance outcomes, enumerated with their probabilities
//! (DESIGN.md 4.4), for search: Nessie scores each cell of the payoff matrix
//! as the probability-weighted value of the positions it can lead to.
//!
//! The turn is replayed from a copy of the position with a `Scripted` chance
//! that forces the class taken at each draw. The first run takes the first
//! class everywhere and records every draw's classes; each class not taken
//! is a sibling to replay later, most probable branch first, so a cap on
//! the number of outcomes keeps (roughly) the likeliest and reports the mass
//! left out.

use crate::battle::choice::SideChoice;
use crate::battle::{Battle, Res};
use crate::chance::{Chance, Rng, Script};
use std::cmp::Ordering;
use std::collections::BinaryHeap;

#[derive(Debug, Clone)]
pub struct EnumConfig {
    /// At most this many outcomes; the rest is reported as `unexplored`.
    pub max_outcomes: usize,
    /// Bands per damage roll besides the KO split (1: "KOs" or "doesn't").
    pub roll_bands: u32,
    /// Seeds the sampled chance each outcome continues with.
    pub seed: u64,
}

impl Default for EnumConfig {
    fn default() -> Self {
        EnumConfig {
            max_outcomes: 64,
            roll_bands: 1,
            seed: 0,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ChanceOutcome {
    pub prob: f64,
    pub battle: Battle,
    /// The class taken at each branching draw.
    pub path: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct Enumeration {
    pub outcomes: Vec<ChanceOutcome>,
    /// Probability of the outcomes left out by the cap.
    pub unexplored: f64,
}

struct Pending {
    prob: f64,
    prefix: Vec<u8>,
}

impl PartialEq for Pending {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}
impl Eq for Pending {}
impl PartialOrd for Pending {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for Pending {
    fn cmp(&self, other: &Self) -> Ordering {
        // Most probable first; ties in path order, so the result is stable.
        self.prob
            .total_cmp(&other.prob)
            .then_with(|| other.prefix.cmp(&self.prefix))
    }
}

/// Every outcome of playing `choices` from `battle`, up to the cap.
pub fn enumerate(
    battle: &Battle,
    choices: &[Option<SideChoice>; 2],
    cfg: &EnumConfig,
) -> Res<Enumeration> {
    let mut heap = BinaryHeap::new();
    heap.push(Pending {
        prob: 1.0,
        prefix: Vec::new(),
    });
    let mut outcomes = Vec::new();
    let mut total = 0.0;
    while let Some(Pending { prefix, .. }) = heap.pop() {
        if outcomes.len() >= cfg.max_outcomes {
            break;
        }
        let mut b = battle.clone();
        b.chance = Chance::Scripted(Box::new(Script::new(prefix.clone(), cfg.roll_bands)));
        b.choose(choices.clone())?;
        let Chance::Scripted(script) = std::mem::replace(
            &mut b.chance,
            Chance::Sampled(Rng::new(cfg.seed ^ outcomes.len() as u64)),
        ) else {
            unreachable!("the script stays in place");
        };
        // Siblings: every class not taken past the forced prefix.
        let mut p = 1.0;
        for (i, d) in script.trace.iter().enumerate() {
            if i >= prefix.len() {
                for (c, &q) in d.probs.iter().enumerate() {
                    if c != d.taken as usize && q > 0.0 {
                        let mut next: Vec<u8> = script.trace[..i].iter().map(|d| d.taken).collect();
                        next.push(c as u8);
                        heap.push(Pending {
                            prob: p * q,
                            prefix: next,
                        });
                    }
                }
            }
            p *= d.probs[d.taken as usize];
        }
        total += script.prob;
        outcomes.push(ChanceOutcome {
            prob: script.prob,
            battle: b,
            path: script.trace.iter().map(|d| d.taken).collect(),
        });
    }
    Ok(Enumeration {
        outcomes,
        unexplored: (1.0 - total).max(0.0),
    })
}
