//! Python bindings: `selfplay._engine`. Kept thin -- logic lives in the engine
//! crate so it stays testable and benchmarkable without Python.

// The engine allocates a lot and many threads step games at once: mimalloc
// scales where the system allocator's locks don't.
#[global_allocator]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

use engine::corpus;
use engine::dex::{Dex, STAT_NAMES};
use engine::env::action::{MASK_LEN, PREVIEW_ACTIONS, SLOT_ACTIONS};
use engine::env::obs::{self, FIELD_FLOATS, INT_FIELDS, MON_FLOATS, TOKENS};
use engine::env::{EnvConfig, Finished, TeamSampler};
use engine::search;
use pyo3::buffer::{Element, PyBuffer};
use engine::stats;
use engine::team;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

/// One stat. `nature_percent` is 110, 100 or 90 (ignored for HP).
#[pyfunction]
#[pyo3(signature = (base, points, is_hp, nature_percent=100))]
fn calc_stat(base: u16, points: u16, is_hp: bool, nature_percent: u16) -> u16 {
    stats::calc_stat(base, points, is_hp, nature_percent)
}

/// All six stats for a species, keyed hp/atk/def/spa/spd/spe.
/// `points` (stat points) are in that order.
#[pyfunction]
fn compute_stats(species: &str, nature: &str, points: [u16; 6]) -> PyResult<HashMap<&'static str, u16>> {
    let dex = Dex::get();
    let sid = dex
        .species_id(species)
        .ok_or_else(|| PyValueError::new_err(format!("unknown species {species:?}")))?;
    let nid = dex
        .nature_id(nature)
        .ok_or_else(|| PyValueError::new_err(format!("unknown nature {nature:?}")))?;
    let s = stats::compute_stats(sid, nid, points);
    Ok(STAT_NAMES.iter().copied().zip(s).collect())
}

/// Species ids in the embedded dex, sorted.
#[pyfunction]
fn species_ids() -> Vec<String> {
    Dex::get().species.iter().map(|s| s.id.clone()).collect()
}

/// Where the embedded dex came from: the Showdown commit and mod.
#[pyfunction]
fn dex_source() -> &'static str {
    &Dex::get().source
}

/// Parse a Showdown paste into a list of dicts (ids, stat points, and whether
/// the points were filled from usage because the paste had none).
#[pyfunction]
fn parse_team(py: Python<'_>, text: &str) -> PyResult<Vec<Py<pyo3::types::PyDict>>> {
    let dex = Dex::get();
    let team = team::parse_paste(text).map_err(PyValueError::new_err)?;
    team.iter()
        .map(|set| {
            let d = pyo3::types::PyDict::new(py);
            d.set_item("name", &set.name)?;
            d.set_item("species", &dex.species(set.species).name)?;
            d.set_item("item", set.item.map(|i| dex.item(i).name.clone()))?;
            d.set_item("ability", &dex.ability(set.ability).name)?;
            let nature = &dex.nature_names[set.nature.0 as usize];
            d.set_item("nature", format!("{}{}", nature[..1].to_uppercase(), &nature[1..]))?;
            d.set_item("points", set.points.to_vec())?;
            d.set_item("points_filled", set.points_filled)?;
            d.set_item("moves", set.moves.iter().map(|m| dex.move_data(*m).name.clone()).collect::<Vec<_>>())?;
            d.set_item("mega", team::mega_forme(set).map(|m| dex.species(m).name.clone()))?;
            Ok(d.unbind())
        })
        .collect()
}

/// Reg M-C problems with a Showdown paste: (kind, message) pairs. Empty is legal.
#[pyfunction]
fn validate_team(text: &str) -> PyResult<Vec<(String, String)>> {
    let team = team::parse_paste(text).map_err(PyValueError::new_err)?;
    Ok(team::validate(&team).into_iter().map(|p| (format!("{:?}", p.kind), p.message)).collect())
}

/// An engine bug ended games (or search cells) early: keep a record
/// (engine/examples/replay_crash.rs replays game records) and carry on.
fn report_crashes(crashes: &[String]) {
    if crashes.is_empty() {
        return;
    }
    use std::io::Write;
    let path = "engine_crashes.jsonl";
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(path) {
        for c in crashes {
            let _ = writeln!(f, "{c}");
        }
    }
    eprintln!(
        "warning: {} engine panic(s) were skipped (a game counted as a draw, or a search cell \
         left out); details appended to {path} (please send it in)",
        crashes.len()
    );
}

/// A writable, C-contiguous numpy array of `len` elements, as a slice.
///
/// Safety: the caller keeps `buf` alive and nothing else touches the
/// array while the slice is in use (the env writes it with the GIL
/// released).
#[allow(clippy::mut_from_ref)]
unsafe fn writable<'a, T: Element>(buf: &'a PyBuffer<T>, len: usize, what: &str) -> PyResult<&'a mut [T]> {
    if buf.readonly() || !buf.is_c_contiguous() || buf.item_count() != len {
        return Err(PyValueError::new_err(format!(
            "{what}: expected a writable C-contiguous array of {len} elements, got {}",
            buf.item_count()
        )));
    }
    Ok(std::slice::from_raw_parts_mut(buf.buf_ptr() as *mut T, len))
}

/// Copy `src` into a writable, C-contiguous numpy array of the same length.
fn fill<T: Element + Copy>(py: Python<'_>, dst: &Bound<'_, PyAny>, src: &[T], what: &str) -> PyResult<()> {
    let buf = PyBuffer::<T>::get(dst)?;
    if buf.item_count() != src.len() {
        return Err(PyValueError::new_err(format!(
            "{what}: expected {} elements, got {}",
            src.len(),
            buf.item_count()
        )));
    }
    buf.copy_from_slice(py, src)
}

/// Many self-play games stepped together (engine::env::VecEnv). Arrays are
/// numpy arrays the caller allocates (see `sizes`); `observe` and `step`
/// fill them in place.
#[pyclass(module = "selfplay._engine")]
struct VecEnv {
    env: engine::env::VecEnv,
    rejected: Vec<(String, String)>,
    actions: Vec<i64>,
    finished: Vec<Option<Finished>>,
}

#[pymethods]
impl VecEnv {
    /// `corpus`: folders of Showdown pastes, each "PATH" (teams weighted by
    /// tournament placement) or "PATH=WEIGHT" (every team that weight).
    #[new]
    #[pyo3(signature = (num_envs, corpus, seed=0, turn_limit=30, perfect_info=true, threads=0))]
    fn new(
        num_envs: usize,
        corpus: Vec<String>,
        seed: u64,
        turn_limit: u32,
        perfect_info: bool,
        threads: usize,
    ) -> PyResult<Self> {
        let (teams, rejected) = corpus::load_sources(&corpus)
            .map_err(|e| PyValueError::new_err(format!("{corpus:?}: {e}")))?;
        if teams.is_empty() {
            return Err(PyValueError::new_err(format!("{corpus:?}: no playable teams")));
        }
        let config = EnvConfig { turn_limit, perfect_info, seed, threads };
        let env = engine::env::VecEnv::new(num_envs, TeamSampler::new(teams), config);
        Ok(VecEnv {
            env,
            rejected: rejected.into_iter().map(|r| (r.name, r.reason)).collect(),
            actions: vec![-1; num_envs * 2],
            finished: vec![None; num_envs],
        })
    }

    #[getter]
    fn num_envs(&self) -> usize {
        self.env.len()
    }

    /// Array shapes and action-space sizes.
    #[staticmethod]
    fn sizes(py: Python<'_>) -> PyResult<Py<pyo3::types::PyDict>> {
        let d = pyo3::types::PyDict::new(py);
        d.set_item("tokens", TOKENS)?;
        d.set_item("int_fields", INT_FIELDS)?;
        d.set_item("mon_floats", MON_FLOATS)?;
        d.set_item("damage_at", obs::DAMAGE_AT)?;
        d.set_item("party_at", obs::PARTY_AT)?;
        d.set_item("field_floats", FIELD_FLOATS)?;
        d.set_item("mask_len", MASK_LEN)?;
        d.set_item("slot_actions", SLOT_ACTIONS)?;
        d.set_item("preview_actions", PREVIEW_ACTIONS)?;
        let [species, abilities, items, moves, types] = obs::vocab_sizes();
        d.set_item("vocab_species", species)?;
        d.set_item("vocab_abilities", abilities)?;
        d.set_item("vocab_items", items)?;
        d.set_item("vocab_moves", moves)?;
        d.set_item("vocab_types", types)?;
        Ok(d.unbind())
    }

    /// Fill both sides' observations (game-major, side-minor): ints
    /// [n,2,tokens,int_fields] int32, mons [n,2,tokens,mon_floats] float32,
    /// field [n,2,field_floats] float32, masks [n,2,mask_len] uint8,
    /// decisions [n,2] uint8 (0 none, 1 team preview, 2 moves/switches).
    fn observe(
        &mut self,
        py: Python<'_>,
        ints: &Bound<'_, PyAny>,
        mons: &Bound<'_, PyAny>,
        field: &Bound<'_, PyAny>,
        masks: &Bound<'_, PyAny>,
        decisions: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let n = self.env.len() * 2;
        let (bi, bm, bf, bk, bd) = (
            PyBuffer::<i32>::get(ints)?,
            PyBuffer::<f32>::get(mons)?,
            PyBuffer::<f32>::get(field)?,
            PyBuffer::<u8>::get(masks)?,
            PyBuffer::<u8>::get(decisions)?,
        );
        // Written in place, in parallel, with the GIL released.
        let (i, m, f, k, d) = unsafe {
            (
                writable(&bi, n * TOKENS * INT_FIELDS, "ints")?,
                writable(&bm, n * TOKENS * MON_FLOATS, "mons")?,
                writable(&bf, n * FIELD_FLOATS, "field")?,
                writable(&bk, n * MASK_LEN, "masks")?,
                writable(&bd, n, "decisions")?,
            )
        };
        let env = &self.env;
        py.detach(|| env.observe(i, m, f, k, d));
        Ok(())
    }

    /// Play `actions` [n,2] int64 (an index per side; ignored where a side
    /// has nothing to decide). Games that end are replaced; for those,
    /// `done` [n] uint8 is 1, `reward` [n,2] float32 is +1/-1/0 per side and
    /// `turns` [n] int32 how long they lasted.
    fn step(
        &mut self,
        py: Python<'_>,
        actions: &Bound<'_, PyAny>,
        done: &Bound<'_, PyAny>,
        reward: &Bound<'_, PyAny>,
        turns: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let buf = PyBuffer::<i64>::get(actions)?;
        if buf.item_count() != self.actions.len() {
            return Err(PyValueError::new_err("actions: expected shape [num_envs, 2]"));
        }
        buf.copy_to_slice(py, &mut self.actions)?;
        let VecEnv { env, actions: a, finished, .. } = self;
        py.detach(|| env.step(a, finished)).map_err(PyValueError::new_err)?;
        report_crashes(&self.env.take_crashes());
        let n = self.env.len();
        let mut d = vec![0u8; n];
        let mut r = vec![0f32; n * 2];
        let mut t = vec![0i32; n];
        for (g, f) in self.finished.iter().enumerate() {
            if let Some(f) = f {
                d[g] = 1;
                r[2 * g..2 * g + 2].copy_from_slice(&f.reward);
                t[g] = f.turns as i32;
            }
        }
        fill(py, done, &d, "done")?;
        fill(py, reward, &r, "reward")?;
        fill(py, turns, &t, "turns")
    }

    /// A uniformly random legal action for every side with a decision (-1
    /// elsewhere), given `observe`'s masks and decisions, into `out` [n,2]
    /// int64. The random-play baseline.
    fn random_actions(
        &self,
        py: Python<'_>,
        masks: &Bound<'_, PyAny>,
        decisions: &Bound<'_, PyAny>,
        out: &Bound<'_, PyAny>,
        seed: u64,
    ) -> PyResult<()> {
        let n = self.env.len() * 2;
        let (bk, bd) = (PyBuffer::<u8>::get(masks)?, PyBuffer::<u8>::get(decisions)?);
        let (masks, decisions) = unsafe { (writable(&bk, n * MASK_LEN, "masks")?, writable(&bd, n, "decisions")?) };
        let mut rng = engine::chance::Rng::new(seed);
        let mut actions = vec![-1i64; n];
        for (k, a) in actions.iter_mut().enumerate() {
            if decisions[k] == 0 {
                continue;
            }
            let mask = &masks[k * MASK_LEN..(k + 1) * MASK_LEN];
            let legal = mask.iter().filter(|&&m| m == 1).count() as u32;
            if legal == 0 {
                continue;
            }
            let mut pick = rng.below(legal);
            for (i, &m) in mask.iter().enumerate() {
                if m == 1 {
                    if pick == 0 {
                        *a = i as i64;
                        break;
                    }
                    pick -= 1;
                }
            }
        }
        fill(py, out, &actions, "out")
    }

    /// The greedy-damage baseline's action for every side with a decision
    /// (-1 elsewhere), into `out` [n,2] int64.
    fn greedy_actions(&self, py: Python<'_>, out: &Bound<'_, PyAny>) -> PyResult<()> {
        let mut actions = vec![-1i64; self.env.len() * 2];
        let env = &self.env;
        py.detach(|| env.greedy_actions(&mut actions));
        fill(py, out, &actions, "out")
    }

    /// The corpus teams' names, in index order.
    fn team_names(&self) -> Vec<String> {
        let s = self.env.sampler();
        (0..s.len()).map(|i| s.team(i).name.clone()).collect()
    }

    /// The corpus teams' sampling weights, in index order.
    fn team_weights(&self) -> Vec<f64> {
        let s = self.env.sampler();
        (0..s.len()).map(|i| s.team(i).weight).collect()
    }

    /// Pastes left out of the corpus, with the reason: (name, reason).
    fn rejected(&self) -> Vec<(String, String)> {
        self.rejected.clone()
    }

    /// Play only these (side 0 team, side 1 team) index pairs, cycling;
    /// every game restarts. An empty list goes back to sampling.
    fn set_matchups(&mut self, pairs: Vec<(usize, usize)>) -> PyResult<()> {
        let n = self.env.sampler().len();
        if pairs.iter().any(|&(a, b)| a >= n || b >= n) {
            return Err(PyValueError::new_err("no such team"));
        }
        self.env.set_matchups(pairs.into_iter().map(|(a, b)| [a, b]).collect());
        Ok(())
    }

    /// The (side 0, side 1) team indices game `i` is playing.
    fn game_teams(&self, i: usize) -> PyResult<(usize, usize)> {
        if i >= self.env.len() {
            return Err(PyValueError::new_err("no such game"));
        }
        let [a, b] = self.env.teams(i);
        Ok((a, b))
    }

    /// Game `i` as `side` sees it, as JSON for people (the play screen).
    fn view(&self, i: usize, side: usize) -> PyResult<String> {
        if i >= self.env.len() || side > 1 {
            return Err(PyValueError::new_err("no such game or side"));
        }
        Ok(self.env.battle(i).view(side).to_string())
    }

    /// A tree search (engine::mcts) over the current positions of `games`.
    #[pyo3(signature = (games, root_candidates=4, node_candidates=2, max_candidates=12, widen=0.5,
                        c_explore=1.0, chance_floor=0.1, static_weight=1.0, max_outcomes=16,
                        roll_bands=1, solve_iters=200, max_depth=8, sims_per_wave=4, root_oracle=false,
                        oracle_eps=0.005, root_greedy=false, oracle_pool=24, endgame=0,
                        endgame_outcomes=64, endgame_budget=4.0, full_depth=0, max_wave_scale=16,
                        max_probes=64, probe_outcomes=8, seed=0))]
    #[allow(clippy::too_many_arguments)]
    fn mcts(
        &self,
        games: Vec<usize>,
        root_candidates: usize,
        node_candidates: usize,
        max_candidates: usize,
        widen: f32,
        c_explore: f32,
        chance_floor: f32,
        static_weight: f32,
        max_outcomes: usize,
        roll_bands: u32,
        solve_iters: usize,
        max_depth: usize,
        sims_per_wave: usize,
        root_oracle: bool,
        oracle_eps: f32,
        root_greedy: bool,
        oracle_pool: usize,
        endgame: usize,
        endgame_outcomes: usize,
        endgame_budget: f32,
        full_depth: usize,
        max_wave_scale: usize,
        max_probes: usize,
        probe_outcomes: usize,
        seed: u64,
    ) -> PyResult<MctsForest> {
        if let Some(&g) = games.iter().find(|&&g| g >= self.env.len()) {
            return Err(PyValueError::new_err(format!("no game {g}")));
        }
        let cfg = engine::mcts::MctsConfig {
            root_candidates,
            node_candidates,
            max_candidates,
            widen,
            c_explore,
            chance_floor,
            static_weight,
            max_outcomes,
            roll_bands,
            solve_iters,
            max_depth,
            sims_per_wave,
            root_oracle,
            oracle_eps,
            root_greedy,
            oracle_pool,
            endgame,
            endgame_outcomes,
            endgame_budget,
            full_depth,
            max_wave_scale,
            max_probes,
            probe_outcomes,
            seed,
        };
        let roots = games.iter().map(|&g| self.env.battle(g).clone()).collect();
        Ok(MctsForest {
            forest: engine::mcts::Forest::new(roots, cfg, self.env.config().perfect_info, self.env.worker_threads()),
        })
    }

    /// A search tree whose roots are copies of `games` (node i is games[i]).
    fn search_tree(&self, games: Vec<usize>) -> PyResult<SearchTree> {
        let mut tree = search::Tree::new(self.env.config().perfect_info);
        for g in games {
            if g >= self.env.len() {
                return Err(PyValueError::new_err(format!("no game {g}")));
            }
            tree.add(self.env.battle(g).clone());
        }
        Ok(SearchTree { tree, threads: self.env.worker_threads(), last: search::CellLeaves::default() })
    }

    /// Game `i` as a Showdown-style JSON snapshot (for debugging).
    fn snapshot(&self, i: usize) -> PyResult<String> {
        if i >= self.env.len() {
            return Err(PyValueError::new_err("no such game"));
        }
        Ok(self.env.battle(i).snapshot().to_string())
    }

    /// The legal choices of game `i`'s `side`, as Showdown choice strings
    /// with their action indices.
    fn legal_choices(&self, i: usize, side: usize) -> PyResult<Vec<(usize, String)>> {
        if i >= self.env.len() || side > 1 {
            return Err(PyValueError::new_err("no such game or side"));
        }
        let b = self.env.battle(i);
        Ok(b.legal_choices(side)
            .into_iter()
            .map(|c| (engine::env::action::index(&c), c.to_showdown(&b.requests[side])))
            .collect())
    }
}

/// Positions for search (engine::search::Tree): roots from a VecEnv, plus
/// the leaves an expansion keeps. Observe nodes, expand cells (node, side 0
/// action, side 1 action) through their chance outcomes, read the leaves.
#[pyclass(module = "selfplay._engine")]
struct SearchTree {
    tree: search::Tree,
    threads: usize,
    last: search::CellLeaves,
}

#[pymethods]
impl SearchTree {
    fn __len__(&self) -> usize {
        self.tree.nodes.len()
    }

    /// Both views of each node in `ids`, laid out like VecEnv.observe.
    #[allow(clippy::too_many_arguments)]
    fn observe(
        &self,
        py: Python<'_>,
        ids: Vec<usize>,
        ints: &Bound<'_, PyAny>,
        mons: &Bound<'_, PyAny>,
        field: &Bound<'_, PyAny>,
        masks: &Bound<'_, PyAny>,
        decisions: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        if let Some(&i) = ids.iter().find(|&&i| i >= self.tree.nodes.len()) {
            return Err(PyValueError::new_err(format!("no node {i}")));
        }
        let n = ids.len();
        let (bi, bm, bf, bk, bd) = (
            PyBuffer::<i32>::get(ints)?,
            PyBuffer::<f32>::get(mons)?,
            PyBuffer::<f32>::get(field)?,
            PyBuffer::<u8>::get(masks)?,
            PyBuffer::<u8>::get(decisions)?,
        );
        let (i, m, f, k, d) = unsafe {
            (
                writable(&bi, n * search::LEAF_INTS, "ints")?,
                writable(&bm, n * search::LEAF_MONS, "mons")?,
                writable(&bf, n * search::LEAF_FIELD, "field")?,
                writable(&bk, n * 2 * MASK_LEN, "masks")?,
                writable(&bd, n * 2, "decisions")?,
            )
        };
        let tree = &self.tree;
        py.detach(|| tree.observe(&ids, i, m, f, k, d));
        Ok(())
    }

    /// Expand `cells` [n, 3] int64 (node, side 0 action, side 1 action; -1
    /// where a side has nothing to decide), up to `max_outcomes` each. With
    /// `keep`, the leaves become nodes. Returns the number of leaves; read
    /// them with `leaves`.
    #[pyo3(signature = (cells, max_outcomes=16, roll_bands=1, seed=0, keep=false))]
    fn expand(
        &mut self,
        py: Python<'_>,
        cells: &Bound<'_, PyAny>,
        max_outcomes: usize,
        roll_bands: u32,
        seed: u64,
        keep: bool,
    ) -> PyResult<usize> {
        let buf = PyBuffer::<i64>::get(cells)?;
        if buf.item_count() % 3 != 0 {
            return Err(PyValueError::new_err("cells: expected shape [n, 3]"));
        }
        let mut c = vec![0i64; buf.item_count()];
        buf.copy_to_slice(py, &mut c)?;
        let reqs: Vec<search::CellRequest> = c
            .chunks(3)
            .map(|x| search::CellRequest { node: x[0].max(0) as usize, actions: [x[1], x[2]] })
            .collect();
        let cfg = engine::enumerate::EnumConfig { max_outcomes, roll_bands, seed };
        let (tree, threads) = (&mut self.tree, self.threads);
        let out = py.detach(|| tree.expand(&reqs, &cfg, keep, threads)).map_err(PyValueError::new_err)?;
        report_crashes(&std::mem::take(&mut self.tree.crashes));
        self.last = out;
        Ok(self.last.probs.len())
    }

    /// The last expansion: leaf observations (both views), probabilities,
    /// side 0's result where a leaf ended the game (else NaN), each cell's
    /// first leaf and leaf count [cells] int64, the probability its outcome
    /// cap left out [cells] float32, and (if kept) each leaf's node [leaves].
    #[allow(clippy::too_many_arguments)]
    fn leaves(
        &self,
        py: Python<'_>,
        ints: &Bound<'_, PyAny>,
        mons: &Bound<'_, PyAny>,
        field: &Bound<'_, PyAny>,
        probs: &Bound<'_, PyAny>,
        terminal: &Bound<'_, PyAny>,
        first: &Bound<'_, PyAny>,
        count: &Bound<'_, PyAny>,
        unexplored: &Bound<'_, PyAny>,
        nodes: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let e = &self.last;
        fill(py, ints, &e.ints, "ints")?;
        fill(py, mons, &e.mons, "mons")?;
        fill(py, field, &e.field, "field")?;
        fill(py, probs, &e.probs, "probs")?;
        fill(py, terminal, &e.terminal, "terminal")?;
        let f: Vec<i64> = e.cells.iter().map(|c| c.first_leaf as i64).collect();
        let n: Vec<i64> = e.cells.iter().map(|c| c.leaves as i64).collect();
        let u: Vec<f32> = e.cells.iter().map(|c| c.unexplored as f32).collect();
        fill(py, first, &f, "first")?;
        fill(py, count, &n, "count")?;
        fill(py, unexplored, &u, "unexplored")?;
        let ids: Vec<i64> = e.nodes.iter().map(|&i| i as i64).collect();
        fill(py, nodes, &ids, "nodes")
    }

    /// Node `i` as a Showdown-style JSON snapshot.
    fn snapshot(&self, i: usize) -> PyResult<String> {
        self.tree.nodes.get(i).map(|b| b.snapshot().to_string()).ok_or_else(|| PyValueError::new_err("no such node"))
    }

    /// The legal choices of node `i`'s `side`, as Showdown choice strings
    /// with their action indices.
    fn legal_choices(&self, i: usize, side: usize) -> PyResult<Vec<(usize, String)>> {
        let b = self.tree.nodes.get(i).filter(|_| side < 2).ok_or_else(|| PyValueError::new_err("no such node or side"))?;
        Ok(b.legal_choices(side)
            .into_iter()
            .map(|c| (engine::env::action::index(&c), c.to_showdown(&b.requests[side])))
            .collect())
    }
}

/// The tree search over several positions (engine::mcts::Forest), stepped
/// in waves by `selfplay.mcts`: `select` -> (`policy_inputs`, `set_policy`)
/// -> `expand` -> (`leaf_inputs`, `set_values`).
#[pyclass(module = "selfplay._engine")]
struct MctsForest {
    forest: engine::mcts::Forest,
}

#[pymethods]
impl MctsForest {
    fn __len__(&self) -> usize {
        self.forest.len()
    }

    /// Run the simulations of every tree that hasn't spent `budget` value
    /// evaluations; returns how many new nodes need a policy.
    fn select(&mut self, py: Python<'_>, budget: u64) -> usize {
        let f = &mut self.forest;
        py.detach(|| f.select(budget))
    }

    /// Both views, masks [n,2,mask_len] and decisions [n,2] of the nodes
    /// waiting for a policy.
    fn policy_inputs(
        &self,
        py: Python<'_>,
        ints: &Bound<'_, PyAny>,
        mons: &Bound<'_, PyAny>,
        field: &Bound<'_, PyAny>,
        masks: &Bound<'_, PyAny>,
        decisions: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        let (bi, bm, bf, bk, bd) = (
            PyBuffer::<i32>::get(ints)?,
            PyBuffer::<f32>::get(mons)?,
            PyBuffer::<f32>::get(field)?,
            PyBuffer::<u8>::get(masks)?,
            PyBuffer::<u8>::get(decisions)?,
        );
        let n = bd.item_count() / 2;
        let (i, m, f, k, d) = unsafe {
            (
                writable(&bi, n * search::LEAF_INTS, "ints")?,
                writable(&bm, n * search::LEAF_MONS, "mons")?,
                writable(&bf, n * search::LEAF_FIELD, "field")?,
                writable(&bk, n * 2 * MASK_LEN, "masks")?,
                writable(&bd, n * 2, "decisions")?,
            )
        };
        let forest = &self.forest;
        py.detach(|| forest.policy_inputs(i, m, f, k, d));
        Ok(())
    }

    /// Each waiting node's ranked legal actions per side: `actions` int64 and
    /// `probs` float32, [n, 2, m], most probable first, -1 padding.
    fn set_policy(&mut self, py: Python<'_>, actions: &Bound<'_, PyAny>, probs: &Bound<'_, PyAny>, m: usize) -> PyResult<()> {
        let (ba, bp) = (PyBuffer::<i64>::get(actions)?, PyBuffer::<f32>::get(probs)?);
        let mut a = vec![0i64; ba.item_count()];
        let mut p = vec![0f32; bp.item_count()];
        ba.copy_to_slice(py, &mut a)?;
        bp.copy_to_slice(py, &mut p)?;
        if a.len() != p.len() || m == 0 || a.len() % (2 * m) != 0 {
            return Err(PyValueError::new_err("actions and probs: expected shape [n, 2, m]"));
        }
        self.forest.set_policy(&a, &p, m);
        Ok(())
    }

    /// Play the waiting cells' chance outcomes; returns how many positions
    /// need a value.
    fn expand(&mut self, py: Python<'_>) -> usize {
        let f = &mut self.forest;
        let n = py.detach(|| f.expand());
        report_crashes(&std::mem::take(&mut self.forest.crashes));
        n
    }

    /// Both views of the positions waiting for a value.
    /// The waiting positions from `start` on, as many as the arrays hold.
    #[pyo3(signature = (ints, mons, field, start=0))]
    fn leaf_inputs(
        &self,
        py: Python<'_>,
        ints: &Bound<'_, PyAny>,
        mons: &Bound<'_, PyAny>,
        field: &Bound<'_, PyAny>,
        start: usize,
    ) -> PyResult<()> {
        let (bi, bm, bf) = (PyBuffer::<i32>::get(ints)?, PyBuffer::<f32>::get(mons)?, PyBuffer::<f32>::get(field)?);
        let n = bm.item_count() / search::LEAF_MONS;
        if start + n > self.forest.num_leaves() {
            return Err(PyValueError::new_err(format!(
                "leaves {start}..{} of {}",
                start + n,
                self.forest.num_leaves()
            )));
        }
        let (i, m, f) = unsafe {
            (
                writable(&bi, n * search::LEAF_INTS, "ints")?,
                writable(&bm, n * search::LEAF_MONS, "mons")?,
                writable(&bf, n * search::LEAF_FIELD, "field")?,
            )
        };
        let forest = &self.forest;
        py.detach(|| forest.leaf_inputs_from(start, i, m, f));
        Ok(())
    }

    /// Side 0's value of each waiting position; backs the trees up.
    fn set_values(&mut self, py: Python<'_>, values: &Bound<'_, PyAny>) -> PyResult<()> {
        let buf = PyBuffer::<f32>::get(values)?;
        if buf.item_count() != self.forest.num_leaves() {
            return Err(PyValueError::new_err("values: one per waiting position"));
        }
        let mut v = vec![0f32; buf.item_count()];
        buf.copy_to_slice(py, &mut v)?;
        let f = &mut self.forest;
        py.detach(|| f.set_values(&v));
        Ok(())
    }

    /// Whether every tree is solved or has spent `budget`.
    fn finished(&self, budget: u64) -> bool {
        self.forest.finished(budget)
    }

    /// Per root: candidates and priors (per side), matrix (rows: side 0),
    /// row and col (equilibrium mixes), value, gap, visits (per side),
    /// nodes, cells, leaf_evals, max_depth, exact, and pv: the principal
    /// line as ((side 0 action, side 1 action), value, outcome probability).
    fn results(&self, py: Python<'_>) -> PyResult<Vec<Py<pyo3::types::PyDict>>> {
        self.forest
            .results()
            .into_iter()
            .map(|r| {
                let d = pyo3::types::PyDict::new(py);
                d.set_item("candidates", (r.candidates[0].clone(), r.candidates[1].clone()))?;
                d.set_item("priors", (r.priors[0].clone(), r.priors[1].clone()))?;
                d.set_item("endgame", r.endgame)?;
                d.set_item("legal", (r.legal[0], r.legal[1]))?;
                d.set_item("matrix", r.matrix)?;
                d.set_item("row", r.strategy[0].clone())?;
                d.set_item("col", r.strategy[1].clone())?;
                d.set_item("value", r.value)?;
                d.set_item("gap", r.gap)?;
                d.set_item("visits", (r.visits[0].clone(), r.visits[1].clone()))?;
                d.set_item("nodes", r.nodes)?;
                d.set_item("cells", r.cells)?;
                d.set_item("leaf_evals", r.leaf_evals)?;
                d.set_item("max_depth", r.max_depth)?;
                d.set_item("exact", r.exact)?;
                let pv: Vec<((i64, i64), f32, f32)> = r
                    .pv
                    .iter()
                    .map(|p| ((p.actions[0], p.actions[1]), p.value, p.outcome_prob))
                    .collect();
                d.set_item("pv", pv)?;
                Ok(d.unbind())
            })
            .collect()
    }
}

/// Solve a zero-sum matrix game (`matrix`: rows x cols, row-major, the row
/// player's payoff) by regret matching+. Returns (row mix, col mix, value,
/// gap), the gap being the exploitability of the pair of mixes.
#[pyfunction]
#[pyo3(signature = (matrix, rows, cols, iters=1000))]
fn solve_matrix(matrix: Vec<f32>, rows: usize, cols: usize, iters: usize) -> PyResult<(Vec<f32>, Vec<f32>, f32, f32)> {
    if matrix.len() != rows * cols || rows == 0 || cols == 0 {
        return Err(PyValueError::new_err("matrix: expected rows * cols values"));
    }
    let s = search::solve(&matrix, rows, cols, iters);
    Ok((s.row, s.col, s.value, s.gap))
}

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calc_stat, m)?)?;
    m.add_function(wrap_pyfunction!(compute_stats, m)?)?;
    m.add_function(wrap_pyfunction!(species_ids, m)?)?;
    m.add_function(wrap_pyfunction!(dex_source, m)?)?;
    m.add_function(wrap_pyfunction!(parse_team, m)?)?;
    m.add_function(wrap_pyfunction!(validate_team, m)?)?;
    m.add_class::<VecEnv>()?;
    m.add_class::<SearchTree>()?;
    m.add_class::<MctsForest>()?;
    m.add_function(wrap_pyfunction!(solve_matrix, m)?)?;
    Ok(())
}
