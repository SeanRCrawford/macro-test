//! One-turn matrix search (DESIGN.md 4.14), as mikumiku37 and Nessie play
//! it: the turn is a simultaneous-move game between both sides' candidate
//! joint actions. Each cell (our action, theirs) is worth the
//! probability-weighted value of the positions its chance outcomes lead to;
//! the matrix game's equilibrium is the mix to play.
//!
//! The network lives in Python, so search runs in three steps: `expand`
//! plays every cell's chance outcomes and writes each leaf's observation
//! (both sides' views), the caller evaluates the leaves in one batch, and
//! `cell_values` plus `solve` turn the leaf values into each root's matrix
//! and equilibrium.

use crate::battle::{Battle, Outcome};
use crate::enumerate::{enumerate, EnumConfig};
use crate::env::action::{self, Decision};
use crate::env::obs::{self, FIELD_FLOATS, INT_FIELDS, MON_FLOATS, TOKENS};
use crate::env::run_parallel;
use std::sync::Mutex;

/// Floats and ints of one leaf (both views, side 0 first).
pub const LEAF_INTS: usize = 2 * TOKENS * INT_FIELDS;
pub const LEAF_MONS: usize = 2 * TOKENS * MON_FLOATS;
pub const LEAF_FIELD: usize = 2 * FIELD_FLOATS;

#[derive(Debug, Clone, Copy, Default)]
pub struct Cell {
    pub first_leaf: usize,
    pub leaves: usize,
    /// Probability left out by the outcome cap.
    pub unexplored: f64,
}

#[derive(Debug, Clone, Default)]
pub struct Root {
    pub first_cell: usize,
    /// Candidate action indices per side (`[-1]` for a side with nothing to
    /// decide). Cells are row-major: side 0's candidate, then side 1's.
    pub candidates: [Vec<i64>; 2],
}

/// The leaves of every root's cells.
#[derive(Debug, Clone, Default)]
pub struct Expansion {
    pub roots: Vec<Root>,
    pub cells: Vec<Cell>,
    pub probs: Vec<f32>,
    /// The result for side 0 if the leaf ended the game, else NaN.
    pub terminal: Vec<f32>,
    pub ints: Vec<i32>,
    pub mons: Vec<f32>,
    pub field: Vec<f32>,
}

impl Expansion {
    pub fn len(&self) -> usize {
        self.probs.len()
    }

    pub fn is_empty(&self) -> bool {
        self.probs.is_empty()
    }

    fn append(&mut self, mut other: Expansion) {
        let (c0, l0) = (self.cells.len(), self.probs.len());
        for r in &mut other.roots {
            r.first_cell += c0;
        }
        for c in &mut other.cells {
            c.first_leaf += l0;
        }
        self.roots.append(&mut other.roots);
        self.cells.append(&mut other.cells);
        self.probs.append(&mut other.probs);
        self.terminal.append(&mut other.terminal);
        self.ints.append(&mut other.ints);
        self.mons.append(&mut other.mons);
        self.field.append(&mut other.field);
    }
}

/// Expand one root: every pair of candidates, every chance outcome.
pub fn expand_root(
    battle: &Battle,
    candidates: [Vec<i64>; 2],
    cfg: &EnumConfig,
    perfect_info: bool,
) -> Result<Expansion, String> {
    let mut choices: [Vec<Option<_>>; 2] = [Vec::new(), Vec::new()];
    let mut cands = candidates.clone();
    for side in 0..2 {
        let d = action::decision(battle, side);
        if d == Decision::None {
            cands[side] = vec![-1];
            choices[side] = vec![None];
            continue;
        }
        if cands[side].is_empty() {
            return Err(format!("side {side} has a decision but no candidates"));
        }
        for &a in &cands[side] {
            let c = usize::try_from(a)
                .ok()
                .and_then(|a| action::choice(d, a))
                .ok_or_else(|| format!("side {side}: action {a} isn't one for {d:?}"))?;
            choices[side].push(Some(c));
        }
    }
    let mut out = Expansion {
        roots: vec![Root {
            first_cell: 0,
            candidates: cands,
        }],
        ..Expansion::default()
    };
    let mut ints = vec![0i32; TOKENS * INT_FIELDS];
    let mut mons = vec![0f32; TOKENS * MON_FLOATS];
    let mut field = vec![0f32; FIELD_FLOATS];
    for a in &choices[0] {
        for b in &choices[1] {
            let e = enumerate(battle, &[a.clone(), b.clone()], cfg).map_err(|e| format!("{e:?}"))?;
            out.cells.push(Cell {
                first_leaf: out.probs.len(),
                leaves: e.outcomes.len(),
                unexplored: e.unexplored,
            });
            for o in e.outcomes {
                let b = &o.battle;
                out.probs.push(o.prob as f32);
                out.terminal.push(result_for_side0(b));
                let table = perfect_info.then(|| b.damage_table());
                for side in 0..2 {
                    obs::observe(b, side, perfect_info, table.as_ref(), &mut ints, &mut mons, &mut field);
                    out.ints.extend_from_slice(&ints);
                    out.mons.extend_from_slice(&mons);
                    out.field.extend_from_slice(&field);
                }
            }
        }
    }
    Ok(out)
}

/// Expand many roots in parallel.
pub fn expand(
    roots: &[(&Battle, [Vec<i64>; 2])],
    cfg: &EnumConfig,
    perfect_info: bool,
    threads: usize,
) -> Result<Expansion, String> {
    let results: Vec<Mutex<Option<Result<Expansion, String>>>> =
        roots.iter().map(|_| Mutex::new(None)).collect();
    let work: Vec<usize> = (0..roots.len()).collect();
    run_parallel(work, threads.max(1), |i| {
        let (b, c) = &roots[i];
        let r = expand_root(b, c.clone(), cfg, perfect_info);
        *results[i].lock().expect("result") = Some(r);
    });
    let mut out = Expansion::default();
    for r in results {
        out.append(r.into_inner().expect("result").expect("expanded")?);
    }
    Ok(out)
}

/// Each cell's value for side 0: the mean of its leaves' values (the
/// terminal result where a leaf ended the game, else `leaf_values`),
/// weighted by probability and renormalised over the outcomes explored.
pub fn cell_values(e: &Expansion, leaf_values: &[f32]) -> Vec<f32> {
    assert_eq!(leaf_values.len(), e.len());
    e.cells
        .iter()
        .map(|c| {
            let (mut v, mut p) = (0.0f64, 0.0f64);
            for i in c.first_leaf..c.first_leaf + c.leaves {
                let x = if e.terminal[i].is_nan() {
                    leaf_values[i]
                } else {
                    e.terminal[i]
                };
                v += e.probs[i] as f64 * x as f64;
                p += e.probs[i] as f64;
            }
            if p > 0.0 {
                (v / p) as f32
            } else {
                0.0
            }
        })
        .collect()
}

/// A zero-sum matrix game's approximate equilibrium.
#[derive(Debug, Clone)]
pub struct Solution {
    /// Side 0's mix over the rows (it maximises).
    pub row: Vec<f32>,
    /// Side 1's mix over the columns (it minimises).
    pub col: Vec<f32>,
    /// Side 0's value against side 1's mix, under its own mix.
    pub value: f32,
    /// Exploitability: what a best reply gains against each mix, summed.
    /// 0 at an exact equilibrium.
    pub gap: f32,
}

/// Solve the `rows` x `cols` game `m` (row-major, side 0's payoff) by
/// regret matching+ with linearly weighted averages.
pub fn solve(m: &[f32], rows: usize, cols: usize, iters: usize) -> Solution {
    solve_warm(m, rows, cols, iters, None)
}

/// `solve`, starting from a previous solution's mixes (`warm`: side 0's and
/// side 1's, possibly shorter than the matrix when actions were added since):
/// when a node's matrix changes a little, the old equilibrium is a good
/// first iterate and far fewer iterations are needed.
pub fn solve_warm(
    m: &[f32],
    rows: usize,
    cols: usize,
    iters: usize,
    warm: Option<(&[f32], &[f32])>,
) -> Solution {
    assert_eq!(m.len(), rows * cols);
    let at = |i: usize, j: usize| m[i * cols + j] as f64;
    let mut rr = vec![0.0f64; rows];
    let mut rc = vec![0.0f64; cols];
    if let Some((x0, y0)) = warm {
        // Regrets proportional to the old mix make it the first iterate;
        // their scale (a few iterations' worth) lets it move if it should.
        const WARM: f64 = 4.0;
        for (r, &p) in rr.iter_mut().zip(x0) {
            *r = WARM * p as f64;
        }
        for (r, &p) in rc.iter_mut().zip(y0) {
            *r = WARM * p as f64;
        }
    }
    let mut sr = vec![0.0f64; rows];
    let mut sc = vec![0.0f64; cols];
    let norm = |r: &[f64]| -> Vec<f64> {
        let t: f64 = r.iter().sum();
        if t > 0.0 {
            r.iter().map(|x| x / t).collect()
        } else {
            vec![1.0 / r.len() as f64; r.len()]
        }
    };
    for it in 1..=iters.max(1) {
        let x = norm(&rr);
        // Side 1 (minimising) replies to x.
        let col_payoff: Vec<f64> = (0..cols)
            .map(|j| (0..rows).map(|i| x[i] * at(i, j)).sum())
            .collect();
        let y = norm(&rc);
        let ev_c: f64 = (0..cols).map(|j| y[j] * col_payoff[j]).sum();
        for j in 0..cols {
            rc[j] = (rc[j] + ev_c - col_payoff[j]).max(0.0);
        }
        let y = norm(&rc);
        let row_payoff: Vec<f64> = (0..rows)
            .map(|i| (0..cols).map(|j| y[j] * at(i, j)).sum())
            .collect();
        let ev_r: f64 = (0..rows).map(|i| x[i] * row_payoff[i]).sum();
        for i in 0..rows {
            rr[i] = (rr[i] + row_payoff[i] - ev_r).max(0.0);
        }
        let w = it as f64;
        for i in 0..rows {
            sr[i] += w * x[i];
        }
        for j in 0..cols {
            sc[j] += w * y[j];
        }
    }
    let x = norm(&sr);
    let y = norm(&sc);
    let best_row = (0..rows)
        .map(|i| (0..cols).map(|j| y[j] * at(i, j)).sum::<f64>())
        .fold(f64::NEG_INFINITY, f64::max);
    let best_col = (0..cols)
        .map(|j| (0..rows).map(|i| x[i] * at(i, j)).sum::<f64>())
        .fold(f64::INFINITY, f64::min);
    let value: f64 = (0..rows)
        .map(|i| (0..cols).map(|j| x[i] * y[j] * at(i, j)).sum::<f64>())
        .sum();
    Solution {
        row: x.iter().map(|&v| v as f32).collect(),
        col: y.iter().map(|&v| v as f32).collect(),
        value: value as f32,
        gap: (best_row - best_col) as f32,
    }
}

/// Each root's matrix and equilibrium, from the leaf values.
pub fn solve_all(e: &Expansion, leaf_values: &[f32], iters: usize) -> Vec<(Vec<f32>, Solution)> {
    let v = cell_values(e, leaf_values);
    e.roots
        .iter()
        .map(|r| {
            let (rows, cols) = (r.candidates[0].len(), r.candidates[1].len());
            let m = v[r.first_cell..r.first_cell + rows * cols].to_vec();
            let s = solve(&m, rows, cols, iters);
            (m, s)
        })
        .collect()
}

/// One cell to expand: a node of the tree and an action index per side (-1
/// for a side with nothing to decide).
#[derive(Debug, Clone, Copy)]
pub struct CellRequest {
    pub node: usize,
    pub actions: [i64; 2],
}

/// The leaves of a list of cells (in request order). `nodes` holds each
/// leaf's node id in the tree when the leaves were kept, else is empty.
#[derive(Debug, Clone, Default)]
pub struct CellLeaves {
    pub cells: Vec<Cell>,
    pub probs: Vec<f32>,
    pub terminal: Vec<f32>,
    pub nodes: Vec<usize>,
    pub ints: Vec<i32>,
    pub mons: Vec<f32>,
    pub field: Vec<f32>,
}

/// Positions a search has reached: roots copied from games, then the leaves
/// it chose to keep (for deepening). Search from any node, at any depth.
#[derive(Debug, Clone, Default)]
pub struct Tree {
    pub nodes: Vec<Battle>,
    pub perfect_info: bool,
    /// Reports of cells whose turn panicked (an engine bug), until taken.
    /// Such a cell comes back with no outcomes.
    pub crashes: Vec<String>,
    /// Test hook: make this request index of the next `expand` panic.
    #[doc(hidden)]
    pub inject_panic: Option<usize>,
}

impl Tree {
    pub fn new(perfect_info: bool) -> Self {
        crate::env::install_panic_hook();
        Tree {
            nodes: Vec::new(),
            perfect_info,
            crashes: Vec::new(),
            inject_panic: None,
        }
    }

    pub fn add(&mut self, b: Battle) -> usize {
        self.nodes.push(b);
        self.nodes.len() - 1
    }

    /// Both views of each node, as `VecEnv::observe` writes a game's.
    pub fn observe(
        &self,
        ids: &[usize],
        ints: &mut [i32],
        mons: &mut [f32],
        field: &mut [f32],
        masks: &mut [u8],
        decisions: &mut [u8],
    ) {
        let n = ids.len();
        assert_eq!(ints.len(), n * LEAF_INTS);
        assert_eq!(mons.len(), n * LEAF_MONS);
        assert_eq!(field.len(), n * LEAF_FIELD);
        assert_eq!(masks.len(), n * 2 * action::MASK_LEN);
        assert_eq!(decisions.len(), n * 2);
        for (k, &id) in ids.iter().enumerate() {
            let b = &self.nodes[id];
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
                decisions[j] = action::legal_mask(b, side, &mut masks[r(action::MASK_LEN)]) as u8;
            }
        }
    }

    /// Play each requested cell through its chance outcomes, in parallel.
    /// With `keep`, every leaf becomes a node of the tree.
    pub fn expand(
        &mut self,
        cells: &[CellRequest],
        cfg: &EnumConfig,
        keep: bool,
        threads: usize,
    ) -> Result<CellLeaves, String> {
        type One = (CellLeaves, Vec<Battle>);
        let results: Vec<Mutex<Option<Result<One, String>>>> =
            cells.iter().map(|_| Mutex::new(None)).collect();
        let crashes = Mutex::new(Vec::new());
        let inject = self.inject_panic.take();
        let tree = &*self;
        run_parallel((0..cells.len()).collect(), threads.max(1), |i| {
            let r = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                if inject == Some(i) {
                    panic!("injected panic");
                }
                tree.expand_cell(cells[i], cfg, keep)
            }));
            let r = r.unwrap_or_else(|panic| {
                let (location, backtrace) = crate::env::take_last_panic();
                let req = cells[i];
                let b = &tree.nodes[req.node];
                let actions: Vec<Option<String>> = (0..2)
                    .map(|side| {
                        let d = action::decision(b, side);
                        usize::try_from(req.actions[side])
                            .ok()
                            .and_then(|a| action::choice(d, a))
                            .map(|c| c.to_showdown(&b.requests[side]))
                    })
                    .collect();
                crashes.lock().expect("crashes").push(
                    serde_json::json!({
                        "panic": crate::env::panic_message(panic.as_ref()),
                        "location": location,
                        "backtrace": backtrace,
                        "search_cell": actions,
                        "position": b.snapshot(),
                    })
                    .to_string(),
                );
                Ok((
                    CellLeaves {
                        cells: vec![Cell {
                            first_leaf: 0,
                            leaves: 0,
                            unexplored: 1.0,
                        }],
                        ..CellLeaves::default()
                    },
                    Vec::new(),
                ))
            });
            *results[i].lock().expect("result") = Some(r);
        });
        self.crashes.extend(crashes.into_inner().expect("crashes"));
        let mut out = CellLeaves::default();
        for r in results {
            let (mut one, battles) = r.into_inner().expect("result").expect("expanded")?;
            let l0 = out.probs.len();
            for c in &mut one.cells {
                c.first_leaf += l0;
            }
            out.cells.append(&mut one.cells);
            out.probs.append(&mut one.probs);
            out.terminal.append(&mut one.terminal);
            out.ints.append(&mut one.ints);
            out.mons.append(&mut one.mons);
            out.field.append(&mut one.field);
            for b in battles {
                out.nodes.push(self.add(b));
            }
        }
        Ok(out)
    }

    fn expand_cell(
        &self,
        req: CellRequest,
        cfg: &EnumConfig,
        keep: bool,
    ) -> Result<(CellLeaves, Vec<Battle>), String> {
        let battle = self
            .nodes
            .get(req.node)
            .ok_or_else(|| format!("no node {}", req.node))?;
        let mut choices = [None, None];
        for side in 0..2 {
            let d = action::decision(battle, side);
            if d == Decision::None {
                continue;
            }
            let a = req.actions[side];
            choices[side] = Some(
                usize::try_from(a)
                    .ok()
                    .and_then(|a| action::choice(d, a))
                    .ok_or_else(|| format!("side {side}: action {a} isn't one for {d:?}"))?,
            );
        }
        let e = enumerate(battle, &choices, cfg).map_err(|e| format!("{e:?}"))?;
        let mut out = CellLeaves {
            cells: vec![Cell {
                first_leaf: 0,
                leaves: e.outcomes.len(),
                unexplored: e.unexplored,
            }],
            ..CellLeaves::default()
        };
        let mut ints = vec![0i32; TOKENS * INT_FIELDS];
        let mut mons = vec![0f32; TOKENS * MON_FLOATS];
        let mut field = vec![0f32; FIELD_FLOATS];
        let mut kept = Vec::new();
        for o in e.outcomes {
            let b = &o.battle;
            out.probs.push(o.prob as f32);
            out.terminal.push(result_for_side0(b));
            let table = self.perfect_info.then(|| b.damage_table());
            for side in 0..2 {
                obs::observe(b, side, self.perfect_info, table.as_ref(), &mut ints, &mut mons, &mut field);
                out.ints.extend_from_slice(&ints);
                out.mons.extend_from_slice(&mons);
                out.field.extend_from_slice(&field);
            }
            if keep {
                kept.push(o.battle);
            }
        }
        Ok((out, kept))
    }
}

/// Side 0's result if the battle is over (1, -1, 0), else NaN.
pub fn result_for_side0(b: &Battle) -> f32 {
    match b.outcome {
        None => f32::NAN,
        Some(Outcome::Win(0)) => 1.0,
        Some(Outcome::Win(_)) => -1.0,
        Some(Outcome::Tie) => 0.0,
    }
}
