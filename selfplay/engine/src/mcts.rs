//! Simultaneous-move tree search with a learned prior and value (DESIGN.md
//! 4.16): the search the bot plays and trains with.
//!
//! Every node is one simultaneous decision, solved as a matrix game:
//!
//! - Rows and columns are each side's candidate joint actions: the policy's
//!   most probable ones, more added as the node is visited (progressive
//!   widening by the prior, as AlphaZero-style searches expand).
//! - A cell (one pair of actions) is the exact expectation over the turn's
//!   chance outcomes, enumerated with KO bands (`enumerate.rs`, Nessie): each
//!   outcome is worth its value-network estimate, its subtree's value once
//!   it has one, or the result if the game ended.
//! - The node's value is the matrix's equilibrium value and its strategies
//!   the equilibrium mixes (regret matching+, full information, warm
//!   started). Solving the matrix, rather than averaging sampled returns per
//!   side ("decoupled UCT"), is what makes the opponent a best responder:
//!   decoupled UCT needn't converge to an equilibrium (Lisy et al. 2013).
//!
//! Simulations only decide where to spend effort, never what values are:
//!
//! - At a node each side samples an action from its equilibrium mix plus a
//!   prior bonus that fades with visits, `x(a) + c P(a) sqrt(N) / (1 + n(a))`
//!   (the PUCT exploration term; Jaxcalibur samples edges with a similar
//!   prior-plus-regret rule).
//! - At a cell an outcome is sampled in proportion to its probability times
//!   the uncertainty of its value (binary entropy of the win chance), divided
//!   by sqrt(1 + visits): Nessie's "reach x entropy" ordering, so close
//!   positions get refined and decided ones don't.
//! - Reaching an outcome that isn't a node yet makes it one: its state is
//!   replayed from the parent (outcomes are stored as the chance classes
//!   taken, not as states) and it waits for the policy.
//!
//! A node's backed-up value blends its own static estimate, weighted like
//! `static_weight` visits, with its solved value, so a freshly expanded node
//! (a 2 x 2 matrix) doesn't swing its parent on thin evidence.
//!
//! Many roots (one per game) are searched together in waves, so the network
//! sees large batches: `select` runs each tree's simulations and lists the new
//! nodes that need a policy; `set_policy` gives them their candidates;
//! `expand` plays the new cells' chance outcomes and writes their positions;
//! `set_values` takes the value network's estimates and backs everything up.

use crate::battle::choice::SideChoice;
use crate::battle::Battle;
use crate::chance::{Chance, Rng, Script};
use crate::enumerate::{enumerate, EnumConfig};
use crate::env::action::{self, Decision, MASK_LEN};
use crate::env::obs::{self, FIELD_FLOATS, INT_FIELDS, MON_FLOATS, TOKENS};
use crate::damage::DamageCache;
use crate::env::run_parallel;
use crate::search::{result_for_side0, solve_warm, LEAF_FIELD, LEAF_INTS, LEAF_MONS};
use std::sync::Mutex;

const NONE: u32 = u32::MAX;

#[derive(Debug, Clone)]
pub struct MctsConfig {
    /// Candidates per side a new root starts with.
    pub root_candidates: usize,
    /// Candidates per side any other new node starts with.
    pub node_candidates: usize,
    /// Most candidates per side at any node.
    pub max_candidates: usize,
    /// Progressive widening: a node visited N times may have
    /// `start + floor(widen * sqrt(N))` candidates per side.
    pub widen: f32,
    /// Weight of the prior's exploration bonus in action selection.
    pub c_explore: f32,
    /// Outcome selection weight for a decided position (0 uncertainty).
    pub chance_floor: f32,
    /// A node's own static estimate counts as this many visits of evidence.
    pub static_weight: f32,
    /// Chance outcomes enumerated per cell (the rest renormalised away).
    pub max_outcomes: usize,
    /// Damage-roll bands besides the KO split.
    pub roll_bands: u32,
    /// Regret-matching iterations per matrix solve.
    pub solve_iters: usize,
    /// No node deeper than this many decisions below the root.
    pub max_depth: usize,
    /// Simulations per tree per wave.
    pub sims_per_wave: usize,
    /// Widen the root by best reply (Nessie's double oracle) instead of by
    /// the prior: probe every remaining action against the opponent's mix
    /// and add the best if it gains more than `oracle_eps`.
    pub root_oracle: bool,
    pub oracle_eps: f32,
    /// A safety net against a policy's blind spot: the greedy damage action
    /// (each Pokemon's highest expected damage) is always among the root's
    /// starting candidates.
    pub root_greedy: bool,
    /// The root's double oracle considers the prior's first this many
    /// actions per side (0: every legal action).
    pub oracle_pool: usize,
    /// Endgames: a node where neither side has more than this many Pokemon
    /// left is solved full width (0: off). Its candidates grow only by best
    /// reply over every legal action, with no cap, and it counts as solved
    /// once no action outside them gains against the other side's
    /// equilibrium mix (each such reply checked exactly, down to the end of
    /// the game or the turn cap).
    pub endgame: usize,
    /// Chance outcomes enumerated per cell at endgame nodes (a cell whose
    /// outcomes are cut off can't be exact).
    pub endgame_outcomes: usize,
    /// A tree whose root is an endgame may spend this many times the budget.
    pub endgame_budget: f32,
    /// Nodes shallower than this are searched full width like endgames
    /// (best reply over every legal action; Nessie's root): 1 is the root.
    pub full_depth: usize,
    /// Chance outcomes per probe cell (screening replies for the double
    /// oracle). A probe that becomes a candidate, or that an endgame's proof
    /// needs, is redone with the node's full number.
    pub probe_outcomes: usize,
    pub seed: u64,
}

impl Default for MctsConfig {
    fn default() -> Self {
        MctsConfig {
            root_candidates: 4,
            node_candidates: 2,
            max_candidates: 12,
            widen: 0.5,
            c_explore: 1.0,
            chance_floor: 0.1,
            static_weight: 1.0,
            max_outcomes: 16,
            roll_bands: 1,
            solve_iters: 200,
            max_depth: 8,
            sims_per_wave: 4,
            root_oracle: false,
            oracle_eps: 0.005,
            root_greedy: false,
            oracle_pool: 24,
            endgame: 0,
            endgame_outcomes: 64,
            endgame_budget: 4.0,
            full_depth: 0,
            probe_outcomes: 8,
            seed: 0,
        }
    }
}

#[derive(Debug, Clone)]
struct Child {
    prob: f32,
    /// The chance classes taken (replays the outcome from the parent).
    path: Vec<u8>,
    /// Side 0's result if this outcome ended the game, else NaN.
    terminal: f32,
    /// The value network's estimate (NaN until evaluated).
    static_v: f32,
    node: u32,
    visits: u32,
}

#[derive(Debug, Clone)]
struct Cell {
    node: u32,
    /// The action pair (side 0, side 1).
    actions: [i64; 2],
    children: Vec<Child>,
    ready: bool,
    value: f32,
    exact: bool,
    /// Some chance outcomes were left out (the cell can't be exact).
    truncated: bool,
    /// Nothing left to refine below it (exact, or as far as it can go).
    settled: bool,
    /// Chance outcomes its expansion keeps.
    cap: u32,
    visits: u32,
}

/// How a node widens beyond its starting candidates.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Widen {
    /// By the prior, up to `max_candidates`.
    Prior,
    /// The root's double oracle over the prior's first `oracle_pool`
    /// actions; the prior's next action when no reply gains.
    RootOracle,
    /// An endgame: by best reply over every legal action, uncapped, and
    /// solvable exactly.
    Full,
}

struct Node {
    battle: Battle,
    depth: u16,
    /// (cell, child) this node is an outcome of; NONE for the root.
    parent: (u32, u16),
    /// Each side's legal actions (every one), most probable first, with
    /// prior probabilities; `[(-1, 1)]` for a side with nothing to decide.
    ranked: [Vec<(i64, f32)>; 2],
    has_prior: bool,
    /// The candidates are the first `k[s]` of `ranked[s]`.
    k: [usize; 2],
    /// Candidate cells: `rows[i][j]` pairs side 0's i-th candidate with
    /// side 1's j-th.
    rows: Vec<Vec<u32>>,
    /// Other action pairs' cells (the double oracle's probes), by actions.
    probes: crate::dex::FastMap<(i64, i64), u32>,
    widen: Widen,
    /// Visits at which each side's best reply is next looked for (Full).
    oracle_due: [u32; 2],
    strategy: [Vec<f32>; 2],
    solved: f32,
    gap: f32,
    value: f32,
    static_v: f32,
    visits: u32,
    action_visits: [Vec<u32>; 2],
    exact: bool,
    dirty: bool,
}

impl Node {
    fn new(battle: Battle, depth: u16, parent: (u32, u16), static_v: f32) -> Self {
        Node {
            battle,
            depth,
            parent,
            ranked: [Vec::new(), Vec::new()],
            has_prior: false,
            k: [0, 0],
            rows: Vec::new(),
            probes: Default::default(),
            widen: Widen::Prior,
            oracle_due: [0, 0],
            strategy: [Vec::new(), Vec::new()],
            solved: f32::NAN,
            gap: 0.0,
            value: static_v,
            static_v,
            visits: 0,
            action_visits: [Vec::new(), Vec::new()],
            exact: false,
            dirty: false,
        }
    }

    fn cell(&self, i: usize, j: usize) -> u32 {
        self.rows[i][j]
    }

    /// The opponent's candidates (index, weight) in its mix above `min`.
    fn support(&self, side: usize, min: f32) -> Vec<(usize, f32)> {
        (0..self.k[side])
            .filter_map(|j| {
                let w = self.strategy[side].get(j).copied().unwrap_or(0.0);
                (w > min).then_some((j, w))
            })
            .collect()
    }

    /// The action pair of `side`'s action `a` against the other side's
    /// candidate `j`.
    fn pair(&self, side: usize, a: i64, j: usize) -> [i64; 2] {
        let b = self.ranked[1 - side][j].0;
        if side == 0 {
            [a, b]
        } else {
            [b, a]
        }
    }
}

/// Mix weight below which an action is out of the support the double
/// oracle replies to (Full nodes, which must be exact, use the lower one).
const SUPPORT: f32 = 0.01;
const SUPPORT_FULL: f32 = 0.001;

/// What one double-oracle step did.
enum Step {
    /// Probes are being valued (`true` if this step made new ones).
    Busy(bool),
    /// A best reply joined the candidates.
    Added,
    /// No action outside the candidates gains.
    NoGain,
}

/// Side 0's chance to win, as an entropy in [0, 1] (1 at 50%).
fn uncertainty(v: f32) -> f32 {
    let q = ((v + 1.0) / 2.0).clamp(1e-6, 1.0 - 1e-6);
    -(q * q.ln() + (1.0 - q) * (1.0 - q).ln()) / std::f32::consts::LN_2
}

fn sample(weights: &[f32], rng: &mut Rng) -> Option<usize> {
    let total: f32 = weights.iter().sum();
    if total.is_nan() || total <= 0.0 {
        return None;
    }
    let mut x = (rng.next_u64() >> 40) as f32 / (1u64 << 24) as f32 * total;
    for (i, &w) in weights.iter().enumerate() {
        x -= w;
        if x <= 0.0 && w > 0.0 {
            return Some(i);
        }
    }
    weights.iter().rposition(|&w| w > 0.0)
}

fn choices(b: &Battle, actions: [i64; 2]) -> [Option<SideChoice>; 2] {
    [0, 1].map(|side| {
        let d = action::decision(b, side);
        if d == Decision::None {
            return None;
        }
        usize::try_from(actions[side]).ok().and_then(|a| action::choice(d, a))
    })
}

struct SimTree {
    nodes: Vec<Node>,
    cells: Vec<Cell>,
    rng: Rng,
    leaf_evals: u64,
    sims: u64,
    stalls: u32,
    done: bool,
}

impl SimTree {
    /// This tree's budget: an endgame root may spend more.
    fn budget(&self, budget: u64, cfg: &MctsConfig) -> u64 {
        let left = |s: usize| self.nodes[0].battle.sides[s].pokemon_left;
        if cfg.endgame > 0 && left(0).max(left(1)) <= cfg.endgame {
            (budget as f64 * cfg.endgame_budget.max(1.0) as f64) as u64
        } else {
            budget
        }
    }

    fn child_value(&self, c: &Child) -> f32 {
        if !c.terminal.is_nan() {
            c.terminal
        } else if c.node != NONE {
            self.nodes[c.node as usize].value
        } else {
            c.static_v
        }
    }

    fn child_exact(&self, c: &Child) -> bool {
        !c.terminal.is_nan() || (c.node != NONE && self.nodes[c.node as usize].exact)
    }

    fn cell_actions(&self, cell: &Cell) -> [i64; 2] {
        cell.actions
    }

    fn new_cell(&mut self, node: u32, actions: [i64; 2], cap: usize, want: &mut Vec<u32>) -> u32 {
        let id = self.cells.len() as u32;
        self.cells.push(Cell {
            node,
            actions,
            children: Vec::new(),
            ready: false,
            value: 0.0,
            exact: false,
            truncated: false,
            settled: false,
            cap: cap.min(u32::MAX as usize) as u32,
            visits: 0,
        });
        want.push(id);
        id
    }

    /// Chance outcomes a node's candidate cells keep.
    fn cap(&self, ni: usize, cfg: &MctsConfig) -> usize {
        if self.nodes[ni].widen == Widen::Full {
            cfg.endgame_outcomes.max(cfg.max_outcomes)
        } else {
            cfg.max_outcomes
        }
    }

    /// Whether a cell is as precise as the node's candidate cells.
    fn full_precision(&self, c: u32, cap: usize) -> bool {
        let cell = &self.cells[c as usize];
        cell.cap as usize >= cap || (cell.ready && !cell.truncated)
    }

    /// The cell of an action pair at a node outside its candidate matrix:
    /// the probe made earlier, or a new (screening) one.
    fn probe_cell(&mut self, ni: usize, actions: [i64; 2], cfg: &MctsConfig, want: &mut Vec<u32>) -> u32 {
        if let Some(&c) = self.nodes[ni].probes.get(&(actions[0], actions[1])) {
            return c;
        }
        let cap = cfg.probe_outcomes.max(1).min(self.cap(ni, cfg));
        let c = self.new_cell(ni as u32, actions, cap, want);
        self.nodes[ni].probes.insert((actions[0], actions[1]), c);
        c
    }

    /// Make `ranked[side][pos]` the side's next candidate, with its cells
    /// against the other side's candidates (taken from the probes when
    /// they exist).
    fn add_candidate(&mut self, ni: usize, side: usize, pos: usize, cfg: &MctsConfig, want: &mut Vec<u32>) {
        let cap = self.cap(ni, cfg);
        let n = &mut self.nodes[ni];
        let k = n.k[side];
        n.ranked[side].swap(k, pos);
        n.k[side] += 1;
        // The mixes will change: both sides' best replies are due again.
        n.oracle_due = [0, 0];
        n.strategy[side].push(0.0);
        n.action_visits[side].push(0);
        let a = n.ranked[side][k].0;
        let other = n.k[1 - side];
        let mut new = Vec::with_capacity(other);
        for j in 0..other {
            let actions = self.nodes[ni].pair(side, a, j);
            let c = match self.nodes[ni].probes.remove(&(actions[0], actions[1])) {
                Some(c) if self.full_precision(c, cap) => c,
                _ => self.new_cell(ni as u32, actions, cap, want),
            };
            new.push(c);
        }
        let n = &mut self.nodes[ni];
        if side == 0 {
            n.rows.push(new);
        } else {
            for (row, c) in n.rows.iter_mut().zip(new) {
                row.push(c);
            }
        }
    }

    /// Side 0's value of `side`'s action `a` against the other side's
    /// `support` (renormalised), and whether every cell it needs is ready.
    fn reply_value(&self, ni: usize, side: usize, a: i64, support: &[(usize, f32)]) -> Option<f32> {
        let n = &self.nodes[ni];
        let (mut v, mut w) = (0.0f64, 0.0f64);
        for &(j, y) in support {
            let actions = n.pair(side, a, j);
            let c = *n.probes.get(&(actions[0], actions[1]))?;
            let cell = &self.cells[c as usize];
            if !cell.ready {
                return None;
            }
            v += y as f64 * cell.value as f64;
            w += y as f64;
        }
        (w > 0.0).then(|| (v / w) as f32)
    }

    /// One double-oracle step for `side` at a node: probe every action of
    /// its pool outside the candidates against the other side's mix, then
    /// add the one that gains most over the node's value, if any gains
    /// more than `oracle_eps`.
    fn oracle_step(&mut self, ni: usize, side: usize, cfg: &MctsConfig, want: &mut Vec<u32>) -> Step {
        let n = &self.nodes[ni];
        let full = n.widen == Widen::Full;
        let support = n.support(1 - side, if full { SUPPORT_FULL } else { SUPPORT });
        let len = n.ranked[side].len();
        let end = if full || cfg.oracle_pool == 0 { len } else { cfg.oracle_pool.min(len) };
        let pool: Vec<(usize, i64)> = (n.k[side]..end.max(n.k[side])).map(|p| (p, n.ranked[side][p].0)).collect();
        let before = want.len();
        for &(_, a) in &pool {
            for &(j, _) in &support {
                let actions = self.nodes[ni].pair(side, a, j);
                self.probe_cell(ni, actions, cfg, want);
            }
        }
        let made = want.len() > before;
        let mut best: Option<(usize, f32)> = None;
        let v = self.nodes[ni].solved;
        let sign = if side == 0 { 1.0 } else { -1.0 };
        for &(p, a) in &pool {
            let Some(q) = self.reply_value(ni, side, a, &support) else {
                return Step::Busy(made);
            };
            let gain = sign * (q - v);
            if best.is_none_or(|b| gain > b.1) {
                best = Some((p, gain));
            }
        }
        match best {
            Some((p, gain)) if gain > cfg.oracle_eps => {
                self.add_candidate(ni, side, p, cfg, want);
                Step::Added
            }
            _ => Step::NoGain,
        }
    }

    /// Give a node its ranked actions and initial candidates.
    fn init_node(&mut self, node: u32, ranked: [Vec<(i64, f32)>; 2], cfg: &MctsConfig, want: &mut Vec<u32>) {
        let n = &mut self.nodes[node as usize];
        let start = if n.depth == 0 { cfg.root_candidates } else { cfg.node_candidates };
        let left = |s: usize| n.battle.sides[s].pokemon_left;
        let endgame = cfg.endgame > 0 && left(0).max(left(1)) <= cfg.endgame;
        n.widen = if endgame || (n.depth as usize) < cfg.full_depth {
            Widen::Full
        } else if cfg.root_oracle && n.depth == 0 {
            Widen::RootOracle
        } else {
            Widen::Prior
        };
        let cap = if n.widen == Widen::Full { usize::MAX } else { cfg.max_candidates.max(1) };
        n.ranked = ranked;
        n.has_prior = true;
        for s in 0..2 {
            n.k[s] = n.ranked[s].len().min(start.max(1)).min(cap);
            n.strategy[s] = vec![1.0 / n.k[s].max(1) as f32; n.k[s]];
            n.action_visits[s] = vec![0; n.k[s]];
        }
        let (k0, k1) = (n.k[0], n.k[1]);
        let mut rows = Vec::with_capacity(k0);
        for i in 0..k0 {
            let mut row = Vec::with_capacity(k1);
            for j in 0..k1 {
                let n = &self.nodes[node as usize];
                let actions = [n.ranked[0][i].0, n.ranked[1][j].0];
                let cap = self.cap(node as usize, cfg);
                row.push(self.new_cell(node, actions, cap, want));
            }
            rows.push(row);
        }
        self.nodes[node as usize].rows = rows;
    }

    /// Whether a node's matrix and backed-up values must be redone at
    /// once: a candidate whose cells were all probed already joined it.
    fn resolve_now(&mut self, ni: usize, cfg: &MctsConfig) {
        self.nodes[ni].dirty = true;
        self.backup(cfg);
    }

    /// One simulation from the root: widen a node, or walk down to an
    /// outcome that becomes a new node. Returns whether it produced work.
    fn simulate(&mut self, cfg: &MctsConfig, want_policy: &mut Vec<u32>, want_cells: &mut Vec<u32>) -> bool {
        let mut node = 0u32;
        loop {
            let ni = node as usize;
            if !self.nodes[ni].has_prior || self.nodes[ni].exact {
                return false;
            }
            self.nodes[ni].visits += 1;
            let solved = !self.nodes[ni].solved.is_nan();
            match self.nodes[ni].widen {
                Widen::Full if solved => {
                    // Look for a best reply when due (backing off while none
                    // gains).
                    let visits = self.nodes[ni].visits;
                    let side = (visits % 2) as usize;
                    if visits >= self.nodes[ni].oracle_due[side] {
                        let before = want_cells.len();
                        match self.oracle_step(ni, side, cfg, want_cells) {
                            Step::Busy(true) => return true,
                            Step::Busy(false) => {}
                            Step::Added => {
                                if want_cells.len() == before {
                                    self.resolve_now(ni, cfg);
                                }
                                self.nodes[ni].oracle_due[side] = visits + 1;
                                return true;
                            }
                            Step::NoGain => self.nodes[ni].oracle_due[side] = visits * 2,
                        }
                    }
                }
                Widen::Full => {}
                widen => {
                    // Progressive widening.
                    let n = &self.nodes[ni];
                    let start = if n.depth == 0 { cfg.root_candidates } else { cfg.node_candidates };
                    let allowed = start + (cfg.widen * (n.visits as f32).sqrt()) as usize;
                    let deficit: [usize; 2] = [0, 1].map(|s| {
                        let target = allowed.min(n.ranked[s].len()).min(cfg.max_candidates.max(1));
                        target.saturating_sub(n.k[s])
                    });
                    if deficit[0] > 0 || deficit[1] > 0 {
                        let side = if deficit[0] > deficit[1] || (deficit[0] == deficit[1] && n.visits.is_multiple_of(2)) {
                            0
                        } else {
                            1
                        };
                        if widen == Widen::RootOracle && solved {
                            let before = want_cells.len();
                            return match self.oracle_step(ni, side, cfg, want_cells) {
                                Step::Busy(made) => made,
                                Step::Added => {
                                    if want_cells.len() == before {
                                        self.resolve_now(ni, cfg);
                                    }
                                    true
                                }
                                Step::NoGain => {
                                    // No reply gains: the prior's next.
                                    let k = self.nodes[ni].k[side];
                                    self.add_candidate(ni, side, k, cfg, want_cells);
                                    if want_cells.len() == before {
                                        self.resolve_now(ni, cfg);
                                    }
                                    true
                                }
                            };
                        }
                        let k = self.nodes[ni].k[side];
                        self.add_candidate(ni, side, k, cfg, want_cells);
                        return true;
                    }
                }
            }
            // Each side's action: its equilibrium mix plus the prior's bonus,
            // sampled jointly over the cells that can still change (ready,
            // not settled).
            let n = &self.nodes[ni];
            let w: [Vec<f32>; 2] = [0, 1].map(|s| {
                (0..n.k[s])
                    .map(|a| {
                        let prior = n.ranked[s][a].1.max(1e-4);
                        n.strategy[s][a]
                            + cfg.c_explore * prior * (n.visits as f32).sqrt()
                                / (1.0 + n.action_visits[s][a] as f32)
                    })
                    .collect()
            });
            let (k0, k1) = (n.k[0], n.k[1]);
            let joint: Vec<f32> = (0..k0 * k1)
                .map(|ij| {
                    let (a, b) = (ij / k1, ij % k1);
                    let c = &self.cells[n.cell(a, b) as usize];
                    if c.ready && !c.settled {
                        w[0][a] * w[1][b]
                    } else {
                        0.0
                    }
                })
                .collect();
            let ci = match sample(&joint, &mut self.rng) {
                Some(ij) => {
                    let (a, b) = (ij / k1, ij % k1);
                    let n = &mut self.nodes[ni];
                    n.action_visits[0][a] += 1;
                    n.action_visits[1][b] += 1;
                    n.cell(a, b) as usize
                }
                None if self.nodes[ni].widen == Widen::Full && solved => {
                    // The candidates are settled: prove the node, by making
                    // sure every reply is probed and none gains, then
                    // settling the most threatening probe still open.
                    let mut busy = false;
                    for side in 0..2 {
                        let before = want_cells.len();
                        match self.oracle_step(ni, side, cfg, want_cells) {
                            Step::Busy(made) => {
                                if made {
                                    return true;
                                }
                                busy = true;
                            }
                            Step::Added => {
                                if want_cells.len() == before {
                                    self.resolve_now(ni, cfg);
                                }
                                return true;
                            }
                            Step::NoGain => {}
                        }
                    }
                    if busy {
                        return false;
                    }
                    // The proof needs the screening probes at full precision.
                    if self.refine_probes(ni, cfg, want_cells) {
                        return true;
                    }
                    match self.open_probe(ni) {
                        Some(c) => c,
                        None => return false,
                    }
                }
                None => return false,
            };
            self.cells[ci].visits += 1;
            // An outcome: likely and undecided first.
            let w: Vec<f32> = self.cells[ci]
                .children
                .iter()
                .map(|c| {
                    if self.child_exact(c) {
                        0.0
                    } else {
                        c.prob * (cfg.chance_floor + uncertainty(self.child_value(c)))
                            / (1.0 + c.visits as f32).sqrt()
                    }
                })
                .collect();
            let Some(k) = sample(&w, &mut self.rng) else {
                // Every outcome is decided: exact, unless some were left out.
                let cell = &mut self.cells[ci];
                cell.settled = true;
                cell.exact = !cell.truncated;
                return false;
            };
            self.cells[ci].children[k].visits += 1;
            let child_node = self.cells[ci].children[k].node;
            if child_node != NONE {
                node = child_node;
                continue;
            }
            let depth = self.nodes[ni].depth + 1;
            if depth as usize > cfg.max_depth {
                return false;
            }
            // Replay the outcome to make it a node.
            let actions = self.cell_actions(&self.cells[ci]);
            let mut b = self.nodes[ni].battle.clone();
            let c = choices(&b, actions);
            let path = self.cells[ci].children[k].path.clone();
            b.chance = Chance::Scripted(Box::new(Script::new(path, cfg.roll_bands)));
            let ok = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| b.choose(c).is_ok()))
                .unwrap_or(false);
            if !ok {
                // Shouldn't happen (the outcome was played once already):
                // keep its static value and never come back.
                self.cells[ci].children[k].terminal = self.cells[ci].children[k].static_v;
                return false;
            }
            b.chance = Chance::Sampled(Rng::new(self.rng.next_u64()));
            let static_v = self.cells[ci].children[k].static_v;
            let id = self.nodes.len() as u32;
            self.nodes.push(Node::new(b, depth, (ci as u32, k as u16), static_v));
            // The simulation that creates a node is its first visit: its first
            // solved value then weighs as much as its static estimate.
            self.nodes[id as usize].visits = 1;
            self.cells[ci].children[k].node = id;
            want_policy.push(id);
            return true;
        }
    }

    /// Redo at full precision the screening probes a Full node's proof
    /// needs (each action outside the candidates against the other side's
    /// support). Returns whether it made any.
    fn refine_probes(&mut self, ni: usize, cfg: &MctsConfig, want: &mut Vec<u32>) -> bool {
        let cap = self.cap(ni, cfg);
        let mut redo = Vec::new();
        let n = &self.nodes[ni];
        for side in 0..2 {
            let support = n.support(1 - side, SUPPORT_FULL);
            for p in n.k[side]..n.ranked[side].len() {
                let a = n.ranked[side][p].0;
                for &(j, _) in &support {
                    let actions = n.pair(side, a, j);
                    if let Some(&c) = n.probes.get(&(actions[0], actions[1])) {
                        if !self.full_precision(c, cap) {
                            redo.push(actions);
                        }
                    }
                }
            }
        }
        for actions in &redo {
            let c = self.new_cell(ni as u32, *actions, cap, want);
            self.nodes[ni].probes.insert((actions[0], actions[1]), c);
        }
        !redo.is_empty()
    }

    /// At a Full node whose candidates are settled: the open probe (ready,
    /// not settled) of the reply that comes closest to gaining, to be
    /// settled next.
    fn open_probe(&self, ni: usize) -> Option<usize> {
        let n = &self.nodes[ni];
        let mut best: Option<(f32, usize)> = None;
        for side in 0..2 {
            let support = n.support(1 - side, SUPPORT_FULL);
            let sign = if side == 0 { 1.0 } else { -1.0 };
            for p in n.k[side]..n.ranked[side].len() {
                let a = n.ranked[side][p].0;
                let Some(q) = self.reply_value(ni, side, a, &support) else { continue };
                let gain = sign * (q - n.solved);
                // Its heaviest open cell.
                let open = support
                    .iter()
                    .filter_map(|&(j, y)| {
                        let actions = n.pair(side, a, j);
                        let c = *n.probes.get(&(actions[0], actions[1]))?;
                        let cell = &self.cells[c as usize];
                        (cell.ready && !cell.settled).then_some((y, c as usize))
                    })
                    .max_by(|x, y| x.0.total_cmp(&y.0));
                if let Some((_, c)) = open {
                    if best.is_none_or(|b| gain > b.0) {
                        best = Some((gain, c));
                    }
                }
            }
        }
        best.map(|b| b.1)
    }

    /// Recompute a cell's value from its outcomes.
    fn refresh_cell(&mut self, ci: usize) {
        let (mut v, mut p, mut exact) = (0.0f64, 0.0f64, true);
        for c in &self.cells[ci].children {
            v += c.prob as f64 * self.child_value(c) as f64;
            p += c.prob as f64;
            exact &= self.child_exact(c);
        }
        let cell = &mut self.cells[ci];
        cell.value = if p > 0.0 { (v / p) as f32 } else { 0.0 };
        cell.exact = exact && !cell.truncated;
        cell.settled |= cell.exact;
    }

    /// Whether a Full node's equilibrium is proven: every action outside
    /// the candidates, valued exactly against the other side's mix, gains
    /// at most `oracle_eps`.
    fn certified(&self, ni: usize, cfg: &MctsConfig) -> bool {
        let n = &self.nodes[ni];
        (0..2).all(|side| {
            let support = n.support(1 - side, SUPPORT_FULL);
            let sign = if side == 0 { 1.0 } else { -1.0 };
            (n.k[side]..n.ranked[side].len()).all(|p| {
                let a = n.ranked[side][p].0;
                let exact = support.iter().all(|&(j, _)| {
                    let actions = n.pair(side, a, j);
                    n.probes
                        .get(&(actions[0], actions[1]))
                        .is_some_and(|&c| self.cells[c as usize].exact)
                });
                exact
                    && self
                        .reply_value(ni, side, a, &support)
                        .is_some_and(|q| sign * (q - n.solved) <= cfg.oracle_eps)
            })
        })
    }

    /// Solve every node whose cells changed, deepest first, and carry the
    /// new values up to the root.
    fn backup(&mut self, cfg: &MctsConfig) {
        let mut buckets: Vec<Vec<u32>> = vec![Vec::new(); cfg.max_depth + 2];
        for (i, n) in self.nodes.iter().enumerate() {
            if n.dirty {
                buckets[n.depth as usize].push(i as u32);
            }
        }
        for d in (0..buckets.len()).rev() {
            let list = std::mem::take(&mut buckets[d]);
            for id in list {
                let ni = id as usize;
                if !self.nodes[ni].dirty {
                    continue;
                }
                self.nodes[ni].dirty = false;
                let (k0, k1) = (self.nodes[ni].k[0], self.nodes[ni].k[1]);
                let mut m = Vec::with_capacity(k0 * k1);
                let mut all_ready = true;
                let mut all_exact = true;
                for i in 0..k0 {
                    for j in 0..k1 {
                        let cell = &self.cells[self.nodes[ni].cell(i, j) as usize];
                        all_ready &= cell.ready;
                        all_exact &= cell.exact;
                        m.push(cell.value);
                    }
                }
                if !all_ready {
                    continue;
                }
                let n = &self.nodes[ni];
                let s = solve_warm(&m, k0, k1, cfg.solve_iters, Some((&n.strategy[0], &n.strategy[1])));
                let n = &mut self.nodes[ni];
                n.strategy = [s.row, s.col];
                n.solved = s.value;
                n.gap = s.gap;
                n.value = if n.static_v.is_nan() {
                    s.value
                } else {
                    let w = cfg.static_weight;
                    (w * n.static_v + n.visits as f32 * s.value) / (w + n.visits as f32)
                };
                let full_width = n.k[0] == n.ranked[0].len() && n.k[1] == n.ranked[1].len();
                let exact = all_exact
                    && (full_width || (n.widen == Widen::Full && self.certified(ni, cfg)));
                let n = &mut self.nodes[ni];
                n.exact = exact;
                if n.exact {
                    n.value = s.value;
                }
                let (pc, _) = n.parent;
                if pc != NONE {
                    self.refresh_cell(pc as usize);
                    let parent = self.cells[pc as usize].node as usize;
                    self.nodes[parent].dirty = true;
                    buckets[self.nodes[parent].depth as usize].push(parent as u32);
                }
            }
        }
        if self.nodes[0].exact {
            self.done = true;
        }
    }
}

/// One expanded cell's outcomes, before their values are known.
struct Expanded {
    children: Vec<Child>,
    /// Some outcomes were left out by the cap.
    truncated: bool,
    ints: Vec<i32>,
    mons: Vec<f32>,
    field: Vec<f32>,
    /// Indices of the children that need a value (not terminal).
    leaves: Vec<u16>,
    crash: Option<String>,
}

pub struct RootResult {
    /// Candidate actions per side (action indices; -1: nothing to decide).
    pub candidates: [Vec<i64>; 2],
    pub priors: [Vec<f32>; 2],
    /// Rows: side 0's candidates.
    pub matrix: Vec<f32>,
    pub strategy: [Vec<f32>; 2],
    pub value: f32,
    pub gap: f32,
    /// How often each candidate was explored.
    pub visits: [Vec<u32>; 2],
    pub nodes: usize,
    pub cells: usize,
    pub leaf_evals: u64,
    pub max_depth: usize,
    pub exact: bool,
    /// The root is an endgame (searched full width).
    pub endgame: bool,
    /// Each side's legal actions at the root.
    pub legal: [usize; 2],
    /// The principal line: at each node, both sides' most likely actions,
    /// side 0's value there, and the probability of the most likely outcome
    /// that follows (the line continues through it while it is a node).
    pub pv: Vec<PvStep>,
}

#[derive(Debug, Clone)]
pub struct PvStep {
    pub actions: [i64; 2],
    pub value: f32,
    pub outcome_prob: f32,
}

impl SimTree {
    fn principal_line(&self) -> Vec<PvStep> {
        let mut out = Vec::new();
        let mut node = 0usize;
        while out.len() < 16 {
            let n = &self.nodes[node];
            if !n.has_prior || n.k[0] == 0 || n.k[1] == 0 || n.solved.is_nan() {
                break;
            }
            let best = |s: usize| {
                (0..n.k[s])
                    .max_by(|&a, &b| n.strategy[s][a].total_cmp(&n.strategy[s][b]))
                    .unwrap_or(0)
            };
            let (a, b) = (best(0), best(1));
            let cell = &self.cells[n.cell(a, b) as usize];
            let next = cell.children.iter().max_by(|x, y| x.prob.total_cmp(&y.prob));
            out.push(PvStep {
                actions: cell.actions,
                value: n.value,
                outcome_prob: next.map_or(0.0, |c| c.prob),
            });
            match next {
                Some(c) if c.node != NONE => node = c.node as usize,
                _ => break,
            }
        }
        out
    }
}

/// Many trees, one per root position, searched together.
pub struct Forest {
    trees: Vec<SimTree>,
    cfg: MctsConfig,
    perfect_info: bool,
    threads: usize,
    want_policy: Vec<(u32, u32)>,
    want_cells: Vec<(u32, u32)>,
    /// The last expansion's leaves: (tree, cell, child).
    leaves: Vec<(u32, u32, u16)>,
    leaf_ints: Vec<i32>,
    leaf_mons: Vec<f32>,
    leaf_field: Vec<f32>,
    /// Cells expanded but not yet valued.
    expanded: Vec<(u32, u32)>,
    pub crashes: Vec<String>,
}

impl Forest {
    pub fn new(roots: Vec<Battle>, cfg: MctsConfig, perfect_info: bool, threads: usize) -> Self {
        crate::env::install_panic_hook();
        let mut seeder = Rng::new(cfg.seed);
        let mut want_policy = Vec::new();
        let trees = roots
            .into_iter()
            .enumerate()
            .map(|(t, b)| {
                let none = (0..2).all(|s| action::decision(&b, s) == Decision::None);
                if !none {
                    want_policy.push((t as u32, 0));
                }
                SimTree {
                    nodes: vec![Node::new(b, 0, (NONE, 0), f32::NAN)],
                    cells: Vec::new(),
                    rng: Rng::new(seeder.next_u64()),
                    leaf_evals: 0,
                    sims: 0,
                    stalls: 0,
                    done: none,
                }
            })
            .collect();
        Forest {
            trees,
            cfg,
            perfect_info,
            threads: threads.max(1),
            want_policy,
            want_cells: Vec::new(),
            leaves: Vec::new(),
            leaf_ints: Vec::new(),
            leaf_mons: Vec::new(),
            leaf_field: Vec::new(),
            expanded: Vec::new(),
            crashes: Vec::new(),
        }
    }

    pub fn len(&self) -> usize {
        self.trees.len()
    }

    pub fn is_empty(&self) -> bool {
        self.trees.is_empty()
    }

    /// Run each unfinished tree's simulations (trees that have spent
    /// `budget` leaf evaluations rest). Returns how many new nodes need a
    /// policy (`policy_inputs`, then `set_policy`).
    pub fn select(&mut self, budget: u64) -> usize {
        let cfg = &self.cfg;
        let out = Mutex::new((Vec::new(), Vec::new()));
        let work: Vec<(usize, &mut SimTree)> = self
            .trees
            .iter_mut()
            .enumerate()
            .filter(|(_, t)| !t.done && t.leaf_evals < t.budget(budget, cfg) && t.nodes[0].has_prior)
            .collect();
        run_parallel(work, self.threads, |(ti, t)| {
            let (mut wp, mut wc) = (Vec::new(), Vec::new());
            let mut produced = false;
            for _ in 0..cfg.sims_per_wave {
                t.sims += 1;
                produced |= t.simulate(cfg, &mut wp, &mut wc);
            }
            if produced {
                t.stalls = 0;
            } else {
                t.stalls += 1;
                // Nothing left that a simulation can reach (solved, or
                // everything pending at the depth limit).
                if t.stalls >= 8 {
                    t.done = true;
                }
            }
            let mut o = out.lock().expect("select");
            o.0.extend(wp.into_iter().map(|n| (ti as u32, n)));
            o.1.extend(wc.into_iter().map(|c| (ti as u32, c)));
        });
        let (wp, wc) = out.into_inner().expect("select");
        self.want_policy.extend(wp);
        self.want_cells.extend(wc);
        self.want_policy.len()
    }

    /// Both views, legal-action masks and decisions of the nodes waiting for
    /// a policy, laid out like `VecEnv::observe`.
    pub fn policy_inputs(&self, ints: &mut [i32], mons: &mut [f32], field: &mut [f32], masks: &mut [u8], decisions: &mut [u8]) {
        let n = self.want_policy.len();
        assert_eq!(ints.len(), n * LEAF_INTS);
        assert_eq!(mons.len(), n * LEAF_MONS);
        assert_eq!(field.len(), n * LEAF_FIELD);
        assert_eq!(masks.len(), n * 2 * MASK_LEN);
        assert_eq!(decisions.len(), n * 2);
        for (k, &(t, id)) in self.want_policy.iter().enumerate() {
            let b = &self.trees[t as usize].nodes[id as usize].battle;
            let table = self.perfect_info.then(|| b.damage_table());
            for side in 0..2 {
                let j = k * 2 + side;
                let r = |w: usize| j * w..(j + 1) * w;
                obs::observe(
                    b,
                    side,
                    self.perfect_info,
                    table.as_ref(),
                    &mut ints[r(TOKENS * INT_FIELDS)],
                    &mut mons[r(TOKENS * MON_FLOATS)],
                    &mut field[r(FIELD_FLOATS)],
                );
                decisions[j] = action::legal_mask(b, side, &mut masks[r(MASK_LEN)]) as u8;
            }
        }
    }

    /// The policy for each waiting node: `actions` and `probs` are
    /// [nodes, 2, m], each side's legal actions most probable first (-1
    /// padding). A side with nothing to decide gets the single action -1.
    pub fn set_policy(&mut self, actions: &[i64], probs: &[f32], m: usize) {
        let want = std::mem::take(&mut self.want_policy);
        assert_eq!(actions.len(), want.len() * 2 * m);
        let cfg = self.cfg.clone();
        for (k, &(t, id)) in want.iter().enumerate() {
            let tree = &mut self.trees[t as usize];
            let b = &tree.nodes[id as usize].battle;
            let mut ranked: [Vec<(i64, f32)>; 2] = [0, 1].map(|side| {
                if action::decision(b, side) == Decision::None {
                    return vec![(-1, 1.0)];
                }
                let base = (k * 2 + side) * m;
                let mut r: Vec<(i64, f32)> = (0..m)
                    .filter(|&i| actions[base + i] >= 0)
                    .map(|i| (actions[base + i], probs[base + i]))
                    .collect();
                // Every legal action, the policy's ranking first: the double
                // oracle can reach any of them, and a node is only exact
                // when all of them are accounted for.
                let mut mask = vec![0u8; MASK_LEN];
                action::legal_mask(b, side, &mut mask);
                let listed: std::collections::HashSet<i64> = r.iter().map(|x| x.0).collect();
                r.retain(|x| mask.get(x.0 as usize) == Some(&1));
                r.extend((0..MASK_LEN).filter(|&i| mask[i] == 1 && !listed.contains(&(i as i64))).map(|i| (i as i64, 0.0)));
                r
            });
            let mut wc = Vec::new();
            if ranked.iter().any(|r| r.is_empty()) {
                // No legal action reported for a side that must decide:
                // leave the node as a leaf.
                tree.nodes[id as usize].exact = true;
                continue;
            }
            if cfg.root_greedy && id == 0 {
                for (side, r) in ranked.iter_mut().enumerate() {
                    if action::decision(b, side) != Decision::Slots {
                        continue;
                    }
                    if let Some(c) = crate::env::policy::greedy(b, side) {
                        promote(r, action::index(&c) as i64, cfg.root_candidates.max(1));
                    }
                }
            }
            tree.init_node(id, ranked, &cfg, &mut wc);
            self.want_cells.extend(wc.into_iter().map(|c| (t, c)));
        }
    }

    /// Play every waiting cell through its chance outcomes. Returns how many
    /// positions need a value (`leaf_inputs`, then `set_values`); with none,
    /// the cells are finished here.
    pub fn expand(&mut self) -> usize {
        let cells = std::mem::take(&mut self.want_cells);
        let cfg = EnumConfig {
            max_outcomes: self.cfg.max_outcomes,
            roll_bands: self.cfg.roll_bands,
            seed: self.cfg.seed,
        };

        let perfect = self.perfect_info;
        let results: Vec<Mutex<Option<Expanded>>> = cells.iter().map(|_| Mutex::new(None)).collect();
        let trees = &self.trees;
        run_parallel((0..cells.len()).collect(), self.threads, |i| {
            let (t, c) = cells[i];
            let tree = &trees[t as usize];
            let cell = &tree.cells[c as usize];
            let b = &tree.nodes[cell.node as usize].battle;
            let actions = tree.cell_actions(cell);
            let ecfg = EnumConfig {
                max_outcomes: cell.cap as usize,
                ..cfg.clone()
            };
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| expand_cell(b, actions, &ecfg, perfect)));
            let e = r.unwrap_or_else(|panic| {
                let (location, backtrace) = crate::env::take_last_panic();
                Expanded {
                    children: Vec::new(),
                    truncated: true,
                    ints: Vec::new(),
                    mons: Vec::new(),
                    field: Vec::new(),
                    leaves: Vec::new(),
                    crash: Some(
                        serde_json::json!({
                            "panic": crate::env::panic_message(panic.as_ref()),
                            "location": location,
                            "backtrace": backtrace,
                            "tree_search_cell": actions,
                            "position": b.snapshot(),
                        })
                        .to_string(),
                    ),
                }
            });
            *results[i].lock().expect("expand") = Some(e);
        });
        self.leaves.clear();
        self.leaf_ints.clear();
        self.leaf_mons.clear();
        self.leaf_field.clear();
        for (&(t, c), r) in cells.iter().zip(results) {
            let mut e = r.into_inner().expect("expand").expect("expanded");
            if let Some(crash) = e.crash.take() {
                self.crashes.push(crash);
            }
            for &k in &e.leaves {
                self.leaves.push((t, c, k));
            }
            self.leaf_ints.append(&mut e.ints);
            self.leaf_mons.append(&mut e.mons);
            self.leaf_field.append(&mut e.field);
            let tree = &mut self.trees[t as usize];
            tree.leaf_evals += e.leaves.len() as u64;
            tree.cells[c as usize].children = e.children;
            tree.cells[c as usize].truncated = e.truncated;
        }
        self.expanded = cells;
        if self.leaves.is_empty() {
            self.finish(&[]);
        }
        self.leaves.len()
    }

    /// The positions waiting for a value: both views each.
    pub fn leaf_inputs(&self, ints: &mut [i32], mons: &mut [f32], field: &mut [f32]) {
        ints.copy_from_slice(&self.leaf_ints);
        mons.copy_from_slice(&self.leaf_mons);
        field.copy_from_slice(&self.leaf_field);
    }

    pub fn num_leaves(&self) -> usize {
        self.leaves.len()
    }

    /// Side 0's value of each waiting position: finish the cells and back up.
    pub fn set_values(&mut self, values: &[f32]) {
        assert_eq!(values.len(), self.leaves.len());
        self.finish(values);
    }

    fn finish(&mut self, values: &[f32]) {
        for (&(t, c, k), &v) in self.leaves.iter().zip(values) {
            self.trees[t as usize].cells[c as usize].children[k as usize].static_v = v;
        }
        let mut touched = Vec::new();
        for &(t, c) in &std::mem::take(&mut self.expanded) {
            let tree = &mut self.trees[t as usize];
            tree.cells[c as usize].ready = true;
            tree.refresh_cell(c as usize);
            let node = tree.cells[c as usize].node as usize;
            tree.nodes[node].dirty = true;
            touched.push(t);
        }
        self.leaves.clear();
        touched.sort_unstable();
        touched.dedup();
        let cfg = &self.cfg;
        let work: Vec<&mut SimTree> = self
            .trees
            .iter_mut()
            .enumerate()
            .filter(|(i, _)| touched.binary_search(&(*i as u32)).is_ok())
            .map(|(_, t)| t)
            .collect();
        run_parallel(work, self.threads, |t| t.backup(cfg));
    }

    /// Whether every tree is finished or has spent `budget`.
    pub fn finished(&self, budget: u64) -> bool {
        self.trees.iter().all(|t| t.done || t.leaf_evals >= t.budget(budget, &self.cfg))
    }

    pub fn results(&self) -> Vec<RootResult> {
        self.trees
            .iter()
            .map(|t| {
                let r = &t.nodes[0];
                let (k0, k1) = (r.k[0], r.k[1]);
                let mut matrix = Vec::with_capacity(k0 * k1);
                for i in 0..k0 {
                    for j in 0..k1 {
                        matrix.push(t.cells[r.cell(i, j) as usize].value);
                    }
                }
                RootResult {
                    candidates: [0, 1].map(|s| r.ranked[s][..r.k[s]].iter().map(|x| x.0).collect()),
                    priors: [0, 1].map(|s| r.ranked[s][..r.k[s]].iter().map(|x| x.1).collect()),
                    matrix,
                    strategy: r.strategy.clone(),
                    value: r.solved,
                    gap: r.gap,
                    visits: r.action_visits.clone(),
                    nodes: t.nodes.len(),
                    cells: t.cells.len(),
                    leaf_evals: t.leaf_evals,
                    max_depth: t.nodes.iter().map(|n| n.depth as usize).max().unwrap_or(0),
                    exact: r.exact,
                    endgame: r.widen == Widen::Full,
                    legal: [r.ranked[0].len(), r.ranked[1].len()],
                    pv: t.principal_line(),
                }
            })
            .collect()
    }
}

/// Put `action` among the first `start` of `ranked` (keeping its prior, or
/// taking the prior of the one it displaces when the policy left it out).
fn promote(ranked: &mut Vec<(i64, f32)>, action: i64, start: usize) {
    let at = start.min(ranked.len()).saturating_sub(1);
    match ranked.iter().position(|r| r.0 == action) {
        Some(p) if p < start => {}
        Some(p) => {
            let r = ranked.remove(p);
            ranked.insert(at, r);
        }
        None => {
            let prior = ranked.get(at).map_or(1.0, |r| r.1);
            ranked.insert(at.min(ranked.len()), (action, prior));
        }
    }
}

/// A cell's outcomes and the observations of those that need a value.
fn expand_cell(b: &Battle, actions: [i64; 2], cfg: &EnumConfig, perfect: bool) -> Expanded {
    let c = choices(b, actions);
    let e = enumerate(b, &c, cfg).expect("legal candidate actions");
    let mut out = Expanded {
        children: Vec::with_capacity(e.outcomes.len()),
        truncated: e.unexplored > 1e-6,
        ints: Vec::new(),
        mons: Vec::new(),
        field: Vec::new(),
        leaves: Vec::new(),
        crash: None,
    };
    let terminal: Vec<f32> = e.outcomes.iter().map(|o| result_for_side0(&o.battle)).collect();
    let n = 2 * terminal.iter().filter(|t| t.is_nan()).count();
    let (wi, wm, wf) = (TOKENS * INT_FIELDS, TOKENS * MON_FLOATS, FIELD_FLOATS);
    out.ints = vec![0; n * wi];
    out.mons = vec![0.0; n * wm];
    out.field = vec![0.0; n * wf];
    // The outcomes mostly differ only in HP: they share damage calculations.
    let mut cache = DamageCache::new();
    let mut j = 0;
    for (o, terminal) in e.outcomes.into_iter().zip(terminal) {
        if terminal.is_nan() {
            out.leaves.push(out.children.len() as u16);
            let table = perfect.then(|| o.battle.damage_table_with(Some(&mut cache)));
            for side in 0..2 {
                obs::observe(
                    &o.battle,
                    side,
                    perfect,
                    table.as_ref(),
                    &mut out.ints[j * wi..(j + 1) * wi],
                    &mut out.mons[j * wm..(j + 1) * wm],
                    &mut out.field[j * wf..(j + 1) * wf],
                );
                j += 1;
            }
        }
        out.children.push(Child {
            prob: o.prob as f32,
            path: o.path,
            terminal,
            static_v: f32::NAN,
            node: NONE,
            visits: 0,
        });
    }
    out
}
