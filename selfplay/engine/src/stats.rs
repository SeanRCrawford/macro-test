//! Level-50 stat calculation under the Champions stat-point rule.
//!
//! Mirrors src/stats.py: each stat point is a flat +1 added AFTER the normal
//! formula (base / IV / level / nature), not the usual EV/4 inside it. The
//! nature multiplier uses integer math (x110/100, x90/100) as the games and
//! Showdown do; tests/test_stats_parity.py checks this agrees with the Python.

use crate::dex::{Dex, NatureId, SpeciesId};

pub const LEVEL: u8 = 50;
pub const DEFAULT_IV: u8 = 31;

/// One stat. `nature_percent` is 110, 100 or 90 (always 100 for HP).
pub fn calc_stat(base: u16, iv: u8, stat_points: u16, level: u8, is_hp: bool, nature_percent: u16) -> u16 {
    let inner = (2 * base as u32 + iv as u32) * level as u32 / 100;
    let normal = if is_hp {
        if base == 1 {
            return 1; // Shedinja
        }
        inner + level as u32 + 10
    } else {
        (inner + 5) * nature_percent as u32 / 100
    };
    (normal + stat_points as u32) as u16
}

/// All six stats, in HP/Atk/Def/SpA/SpD/Spe order.
pub fn compute_stats(species: SpeciesId, nature: NatureId, stat_points: [u16; 6], ivs: [u8; 6], level: u8) -> [u16; 6] {
    let dex = Dex::get();
    let base = dex.species(species).base_stats;
    let nature = dex.nature(nature);
    std::array::from_fn(|i| {
        let pct = if i == 0 { 100 } else { nature.percent(i) };
        calc_stat(base[i], ivs[i], stat_points[i], level, i == 0, pct)
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jolly_garchomp_hand_verified() {
        // Same case as src/stats.py's __main__: 0-point Jolly Garchomp is
        // 183/150/115/90/105/134, then hp2/atk32/spe32 add flat.
        let dex = Dex::get();
        let s = compute_stats(
            dex.species_id("Garchomp").unwrap(),
            dex.nature_id("Jolly").unwrap(),
            [2, 32, 0, 0, 0, 32],
            [DEFAULT_IV; 6],
            LEVEL,
        );
        assert_eq!(s, [185, 182, 115, 90, 105, 166]);
    }

    #[test]
    fn shedinja_hp_is_one() {
        assert_eq!(calc_stat(1, 31, 32, 50, true, 100), 1);
    }
}
