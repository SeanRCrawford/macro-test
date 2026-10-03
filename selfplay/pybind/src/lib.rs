//! Python bindings: `selfplay._engine`. Kept thin -- logic lives in the engine
//! crate so it stays testable and benchmarkable without Python.

use engine::dex::{Dex, STAT_NAMES};
use engine::stats;
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

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calc_stat, m)?)?;
    m.add_function(wrap_pyfunction!(compute_stats, m)?)?;
    m.add_function(wrap_pyfunction!(species_ids, m)?)?;
    m.add_function(wrap_pyfunction!(dex_source, m)?)?;
    Ok(())
}
