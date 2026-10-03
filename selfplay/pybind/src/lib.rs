//! Python bindings: `selfplay._engine`. Kept thin -- logic lives in the engine
//! crate so it stays testable and benchmarkable without Python.

use engine::dex::{Dex, STAT_NAMES};
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

#[pymodule]
fn _engine(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(calc_stat, m)?)?;
    m.add_function(wrap_pyfunction!(compute_stats, m)?)?;
    m.add_function(wrap_pyfunction!(species_ids, m)?)?;
    m.add_function(wrap_pyfunction!(dex_source, m)?)?;
    m.add_function(wrap_pyfunction!(parse_team, m)?)?;
    m.add_function(wrap_pyfunction!(validate_team, m)?)?;
    Ok(())
}
