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
    actions: Vec<i64>,
    finished: Vec<Option<Finished>>,
}

#[pymethods]
impl VecEnv {
    /// `corpus_dir`: a folder of Showdown pastes (weighted by placement).
    #[new]
    #[pyo3(signature = (num_envs, corpus_dir, seed=0, turn_limit=30, perfect_info=true, threads=0))]
    fn new(
        num_envs: usize,
        corpus_dir: &str,
        seed: u64,
        turn_limit: u32,
        perfect_info: bool,
        threads: usize,
    ) -> PyResult<Self> {
        let (teams, _) = corpus::load_dir(std::path::Path::new(corpus_dir))
            .map_err(|e| PyValueError::new_err(format!("{corpus_dir}: {e}")))?;
        if teams.is_empty() {
            return Err(PyValueError::new_err(format!("{corpus_dir}: no playable teams")));
        }
        let config = EnvConfig { turn_limit, perfect_info, seed, threads };
        let env = engine::env::VecEnv::new(num_envs, TeamSampler::new(teams), config);
        Ok(VecEnv {
            env,
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

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calc_stat, m)?)?;
    m.add_function(wrap_pyfunction!(compute_stats, m)?)?;
    m.add_function(wrap_pyfunction!(species_ids, m)?)?;
    m.add_function(wrap_pyfunction!(dex_source, m)?)?;
    m.add_function(wrap_pyfunction!(parse_team, m)?)?;
    m.add_function(wrap_pyfunction!(validate_team, m)?)?;
    m.add_class::<VecEnv>()?;
    Ok(())
}
