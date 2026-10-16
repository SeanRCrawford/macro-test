//! Stat calculation for Pokemon Champions (level 50, stat points).
//!
//! Mirrors Showdown's champions-mod `statModify`: there are no IVs or EVs.
//! Each stat point adds 1 to the stat BEFORE the nature, so the nature
//! multiplier also scales the points:
//!
//!   HP    = base + points + 75
//!   other = trunc((base + points + 20) * nature_percent / 100)
//!
//! src/stats.py adds the points after the nature instead, so it reads up to 3
//! lower on a boosted stat (Jolly Garchomp with 32 Speed points: 169 here and
//! on Showdown, 166 there). Checked against Showdown by tests/stats_fixtures.rs.

use crate::dex::{Dex, NatureId, SpeciesId};

/// Most stat points in one stat; a spread has at most `MAX_TOTAL_POINTS`.
pub const MAX_POINTS: u16 = 32;
pub const MAX_TOTAL_POINTS: u16 = 66;

/// One stat. `nature_percent` is 110, 100 or 90 (ignored for HP).
pub fn calc_stat(base: u16, points: u16, is_hp: bool, nature_percent: u16) -> u16 {
    if is_hp {
        return base + points + 75;
    }
    let raw = (base + points + 20) as u32;
    (raw * nature_percent as u32 / 100) as u16
}

/// All six stats, in HP/Atk/Def/SpA/SpD/Spe order.
pub fn compute_stats(species: SpeciesId, nature: NatureId, points: [u16; 6]) -> [u16; 6] {
    let dex = Dex::get();
    let base = dex.species(species).base_stats;
    let nature = dex.nature(nature);
    std::array::from_fn(|i| calc_stat(base[i], points[i], i == 0, nature.percent(i)))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jolly_garchomp_matches_showdown() {
        let dex = Dex::get();
        let s = compute_stats(
            dex.species_id("Garchomp").unwrap(),
            dex.nature_id("Jolly").unwrap(),
            [2, 32, 0, 0, 0, 32],
        );
        assert_eq!(s, [185, 182, 115, 90, 105, 169]);
    }
}
