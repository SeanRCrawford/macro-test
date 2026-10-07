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
    visits: u32,
}

struct Node {
    battle: Battle,
    depth: u16,
    /// (cell, child) this node is an outcome of; NONE for the root.
    parent: (u32, u16),
    /// Each side's legal actions, most probable first, with prior
    /// probabilities; `[(-1, 1)]` for a side with nothing to decide.
    ranked: [Vec<(i64, f32)>; 2],
    has_prior: bool,
    /// The candidates are the first `k[s]` of `ranked[s]`.
    k: [usize; 2],
    /// Cell ids by (i, j), stride `max_candidates`.
    cells: Vec<u32>,
    strategy: [Vec<f32>; 2],
    solved: f32,
    gap: f32,
    value: f32,
    static_v: f32,
    visits: u32,
    action_visits: [Vec<u32>; 2],
    exact: bool,
    dirty: bool,
    /// The root's pending double-oracle probe.
    probe: Option<Probe>,
}

/// Cells probing actions against the opponent's support: (the probed
/// action, the opponent's candidate index, cell).
struct Probe {
    side: usize,
    cells: Vec<(i64, usize, u32)>,
}

impl Node {
    fn new(battle: Battle, depth: u16, parent: (u32, u16), static_v: f32, stride: usize) -> Self {
        Node {
            battle,
            depth,
            parent,
            ranked: [Vec::new(), Vec::new()],
            has_prior: false,
            k: [0, 0],
            cells: vec![NONE; stride * stride],
            strategy: [Vec::new(), Vec::new()],
            solved: f32::NAN,
            gap: 0.0,
            value: static_v,
            static_v,
            visits: 0,
            action_visits: [Vec::new(), Vec::new()],
            exact: false,
            dirty: false,
            probe: None,
        }
    }
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
    fn stride(cfg: &MctsConfig) -> usize {
        cfg.max_candidates.max(cfg.root_candidates).max(1)
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

    fn new_cell(&mut self, node: u32, actions: [i64; 2], want: &mut Vec<u32>) -> u32 {
        let id = self.cells.len() as u32;
        self.cells.push(Cell {
            node,
            actions,
            children: Vec::new(),
            ready: false,
            value: 0.0,
            exact: false,
            visits: 0,
        });
        want.push(id);
        id
    }

    /// Add (pending) cells for these candidate pairs of a node.
    fn add_cells(&mut self, node: u32, pairs: Vec<[usize; 2]>, stride: usize, want: &mut Vec<u32>) {
        for [i, j] in pairs {
            let n = &self.nodes[node as usize];
            let actions = [n.ranked[0][i].0, n.ranked[1][j].0];
            let id = self.new_cell(node, actions, want);
            self.nodes[node as usize].cells[i * stride + j] = id;
        }
    }

    /// The root's double oracle for `side`: probe every action not yet a
    /// candidate against the opponent's current mix (first call), then add
    /// the best reply, or the prior's next action if no reply gains
    /// (second call, once the probes are valued). Returns whether it made
    /// work, or None while probes are still being valued.
    fn oracle(&mut self, side: usize, cfg: &MctsConfig, want: &mut Vec<u32>) -> Option<bool> {
        let stride = Self::stride(cfg);
        let other = 1 - side;
        let n = &self.nodes[0];
        let Some(probe) = &n.probe else {
            // Probe against the opponent's support.
            let support: Vec<usize> = (0..n.k[other])
                .filter(|&j| n.strategy[other][j] > 0.01)
                .collect();
            let pool: Vec<usize> = (n.k[side]..n.ranked[side].len()).collect();
            let mut cells = Vec::new();
            for &a in &pool {
                for &j in &support {
                    let n = &self.nodes[0];
                    let mut actions = [0i64; 2];
                    actions[side] = n.ranked[side][a].0;
                    actions[other] = n.ranked[other][j].0;
                    let id = self.new_cell(0, actions, want);
                    cells.push((actions[side], j, id));
                }
            }
            self.nodes[0].probe = Some(Probe { side, cells });
            return Some(true);
        };
        if probe.side != side || probe.cells.iter().any(|&(_, _, c)| !self.cells[c as usize].ready) {
            return None;
        }
        // Each probed action's value against the opponent's mix.
        let y = &n.strategy[other];
        let mut score: Vec<(i64, f64, f64)> = Vec::new(); // (action, weighted sum, weight)
        for &(a, j, c) in &probe.cells {
            let w = y.get(j).copied().unwrap_or(0.0) as f64;
            match score.iter_mut().find(|s| s.0 == a) {
                Some(s) => {
                    s.1 += w * self.cells[c as usize].value as f64;
                    s.2 += w;
                }
                None => score.push((a, w * self.cells[c as usize].value as f64, w)),
            }
        }
        let v = if n.solved.is_nan() { 0.0 } else { n.solved as f64 };
        let sign = if side == 0 { 1.0 } else { -1.0 };
        let best = score
            .iter()
            .filter(|s| s.2 > 0.0)
            .map(|s| (s.0, sign * (s.1 / s.2 - v)))
            .max_by(|a, b| a.1.total_cmp(&b.1));
        let k = n.k[side];
        let chosen = match best {
            Some((a, gain)) if gain > cfg.oracle_eps as f64 => a,
            _ => n.ranked[side][k].0,
        };
        let probe = self.nodes[0].probe.take().expect("probe");
        let n = &mut self.nodes[0];
        let pos = n.ranked[side].iter().position(|r| r.0 == chosen).expect("probed action");
        n.ranked[side].swap(k, pos);
        n.k[side] += 1;
        n.strategy[side].push(0.0);
        n.action_visits[side].push(0);
        for j in 0..n.k[other] {
            let reuse = probe.cells.iter().find(|&&(a, pj, _)| a == chosen && pj == j).map(|&(_, _, c)| c);
            let id = match reuse {
                Some(c) => c,
                None => {
                    let mut actions = [0i64; 2];
                    actions[side] = chosen;
                    actions[other] = self.nodes[0].ranked[other][j].0;
                    self.new_cell(0, actions, want)
                }
            };
            let (i0, j0) = if side == 0 { (k, j) } else { (j, k) };
            self.nodes[0].cells[i0 * stride + j0] = id;
        }
        Some(true)
    }

    /// Give a node its ranked actions and initial candidates.
    fn init_node(&mut self, node: u32, ranked: [Vec<(i64, f32)>; 2], cfg: &MctsConfig, want: &mut Vec<u32>) {
        let stride = Self::stride(cfg);
        let n = &mut self.nodes[node as usize];
        let start = if n.depth == 0 { cfg.root_candidates } else { cfg.node_candidates };
        n.ranked = ranked;
        n.has_prior = true;
        for s in 0..2 {
            n.k[s] = n.ranked[s].len().min(start.max(1)).min(stride);
            n.strategy[s] = vec![1.0 / n.k[s].max(1) as f32; n.k[s]];
            n.action_visits[s] = vec![0; n.k[s]];
        }
        let (k0, k1) = (n.k[0], n.k[1]);
        let pairs = (0..k0).flat_map(|i| (0..k1).map(move |j| [i, j])).collect();
        self.add_cells(node, pairs, stride, want);
    }

    /// One simulation from the root: widen a node, or walk down to an
    /// outcome that becomes a new node. Returns whether it produced work.
    fn simulate(&mut self, cfg: &MctsConfig, want_policy: &mut Vec<u32>, want_cells: &mut Vec<u32>) -> bool {
        let stride = Self::stride(cfg);
        let mut node = 0u32;
        loop {
            let ni = node as usize;
            if !self.nodes[ni].has_prior || self.nodes[ni].exact {
                return false;
            }
            self.nodes[ni].visits += 1;
            // Progressive widening by the prior.
            let n = &self.nodes[ni];
            let start = if n.depth == 0 { cfg.root_candidates } else { cfg.node_candidates };
            let allowed = start + (cfg.widen * (n.visits as f32).sqrt()) as usize;
            let deficit: [usize; 2] = [0, 1].map(|s| {
                let target = allowed.min(n.ranked[s].len()).min(stride);
                target.saturating_sub(n.k[s])
            });
            if deficit[0] > 0 || deficit[1] > 0 {
                let side = if deficit[0] > deficit[1] || (deficit[0] == deficit[1] && n.visits.is_multiple_of(2)) {
                    0
                } else {
                    1
                };
                if cfg.root_oracle && ni == 0 && !self.nodes[0].solved.is_nan() {
                    return self.oracle(side, cfg, want_cells).unwrap_or(false);
                }
                let n = &mut self.nodes[ni];
                let new = n.k[side];
                n.k[side] += 1;
                n.strategy[side].push(0.0);
                n.action_visits[side].push(0);
                let other = n.k[1 - side];
                let pairs = (0..other)
                    .map(|o| if side == 0 { [new, o] } else { [o, new] })
                    .collect();
                self.add_cells(node, pairs, stride, want_cells);
                return true;
            }
            // Each side's action: its equilibrium mix plus the prior's bonus,
            // sampled jointly over the cells that can still change (ready,
            // not yet exact).
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
                    let cid = n.cells[a * stride + b];
                    let open = cid != NONE && {
                        let c = &self.cells[cid as usize];
                        c.ready && !c.exact
                    };
                    if open {
                        w[0][a] * w[1][b]
                    } else {
                        0.0
                    }
                })
                .collect();
            let Some(ij) = sample(&joint, &mut self.rng) else {
                return false;
            };
            let (a, b) = (ij / k1, ij % k1);
            let cid = self.nodes[ni].cells[a * stride + b];
            let ci = cid as usize;
            self.nodes[ni].action_visits[0][a] += 1;
            self.nodes[ni].action_visits[1][b] += 1;
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
                self.cells[ci].exact = true;
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
            self.nodes.push(Node::new(b, depth, (cid, k as u16), static_v, stride));
            // The simulation that creates a node is its first visit: its first
            // solved value then weighs as much as its static estimate.
            self.nodes[id as usize].visits = 1;
            self.cells[ci].children[k].node = id;
            want_policy.push(id);
            return true;
        }
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
        cell.exact = exact;
    }

    /// Solve every node whose cells changed, deepest first, and carry the
    /// new values up to the root.
    fn backup(&mut self, cfg: &MctsConfig) {
        let stride = Self::stride(cfg);
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
                        let cid = self.nodes[ni].cells[i * stride + j];
                        let cell = &self.cells[cid as usize];
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
                n.exact = all_exact && n.k[0] == n.ranked[0].len() && n.k[1] == n.ranked[1].len();
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
    fn principal_line(&self, stride: usize) -> Vec<PvStep> {
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
            let cid = n.cells[a * stride + b];
            if cid == NONE {
                break;
            }
            let cell = &self.cells[cid as usize];
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
        let stride = SimTree::stride(&cfg);
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
                    nodes: vec![Node::new(b, 0, (NONE, 0), f32::NAN, stride)],
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
            .filter(|(_, t)| !t.done && t.leaf_evals < budget && t.nodes[0].has_prior)
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
            let ranked: [Vec<(i64, f32)>; 2] = [0, 1].map(|side| {
                if action::decision(b, side) == Decision::None {
                    return vec![(-1, 1.0)];
                }
                let base = (k * 2 + side) * m;
                (0..m)
                    .filter(|&i| actions[base + i] >= 0)
                    .map(|i| (actions[base + i], probs[base + i]))
                    .collect()
            });
            let mut wc = Vec::new();
            if ranked.iter().any(|r| r.is_empty()) {
                // No legal action reported for a side that must decide:
                // leave the node as a leaf.
                tree.nodes[id as usize].exact = true;
                continue;
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
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| expand_cell(b, actions, &cfg, perfect)));
            let e = r.unwrap_or_else(|panic| {
                let (location, backtrace) = crate::env::take_last_panic();
                Expanded {
                    children: Vec::new(),
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
        self.trees.iter().all(|t| t.done || t.leaf_evals >= budget)
    }

    pub fn results(&self) -> Vec<RootResult> {
        let stride = SimTree::stride(&self.cfg);
        self.trees
            .iter()
            .map(|t| {
                let r = &t.nodes[0];
                let (k0, k1) = (r.k[0], r.k[1]);
                let mut matrix = Vec::with_capacity(k0 * k1);
                for i in 0..k0 {
                    for j in 0..k1 {
                        let c = r.cells[i * stride + j];
                        matrix.push(if c == NONE { f32::NAN } else { t.cells[c as usize].value });
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
                    pv: t.principal_line(stride),
                }
            })
            .collect()
    }
}

/// A cell's outcomes and the observations of those that need a value.
fn expand_cell(b: &Battle, actions: [i64; 2], cfg: &EnumConfig, perfect: bool) -> Expanded {
    let c = choices(b, actions);
    let e = enumerate(b, &c, cfg).expect("legal candidate actions");
    let mut out = Expanded {
        children: Vec::with_capacity(e.outcomes.len()),
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
