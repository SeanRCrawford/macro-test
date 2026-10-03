//! Python bindings: `selfplay._engine`. Kept thin -- logic lives in the engine
//! crate so it stays testable and benchmarkable without Python.

use engine::dex::{Dex, STAT_NAMES};
use engine::stats;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::HashMap;

/// One level-50 stat. `nature_percent` is 110, 100 or 90.
#[pyfunction]
#[pyo3(signature = (base, iv, stat_points, level, is_hp, nature_percent=100))]
fn calc_stat(base: u16, iv: u8, stat_points: u16, level: u8, is_hp: bool, nature_percent: u16) -> u16 {
    stats::calc_stat(base, iv, stat_points, level, is_hp, nature_percent)
}

/// All six stats for a species, keyed hp/atk/def/spa/spd/spe.
/// `stat_points` and `ivs` are in that order.
#[pyfunction]
#[pyo3(signature = (species, nature, stat_points, ivs=None, level=stats::LEVEL))]
fn compute_stats(
    species: &str,
    nature: &str,
    stat_points: [u16; 6],
    ivs: Option<[u8; 6]>,
    level: u8,
) -> PyResult<HashMap<&'static str, u16>> {
    let dex = Dex::get();
    let sid = dex
        .species_id(species)
        .ok_or_else(|| PyValueError::new_err(format!("unknown species {species:?}")))?;
    let nid = dex
        .nature_id(nature)
        .ok_or_else(|| PyValueError::new_err(format!("unknown nature {nature:?}")))?;
    let s = stats::compute_stats(sid, nid, stat_points, ivs.unwrap_or([stats::DEFAULT_IV; 6]), level);
    Ok(STAT_NAMES.iter().copied().zip(s).collect())
}

/// Species ids in the embedded dex, sorted.
#[pyfunction]
fn species_ids() -> Vec<String> {
    Dex::get().species.iter().map(|s| s.id.clone()).collect()
}

/// Where the embedded dex came from, e.g. "poke-env 0.16.1 gen9 static data".
#[pyfunction]
fn dex_source() -> &'static str {
    &Dex::get().source
}

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calc_stat, m)?)?;
    m.add_function(wrap_pyfunction!(compute_stats, m)?)?;
    m.add_function(wrap_pyfunction!(species_ids, m)?)?;
    m.add_function(wrap_pyfunction!(dex_source, m)?)?;
    Ok(())
}
