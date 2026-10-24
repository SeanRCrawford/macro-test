//! Showdown's fixed-point arithmetic. Modifiers are integers out of 4096, and
//! the rounding at each step is part of the game's rules: the same multipliers
//! applied with ordinary floats give damage that is off by a point or two.

/// 1.0 in modifier units.
pub const ONE: u32 = 4096;

/// A fraction as a 4096-based modifier: `trunc(num * 4096 / den)`.
/// `chainModify(1.3)` is 5324 in Showdown, which this gives as `of(13, 10)`.
pub const fn of(num: u32, den: u32) -> u32 {
    num * 4096 / den
}

/// Showdown's `modify(value, modifier)`: rounds half down.
pub fn modify(value: u64, modifier: u32) -> u64 {
    (value * modifier as u64 + 2047) / 4096
}

/// Showdown's `chainModify`: combines two modifiers, rounding half up.
pub fn chain(previous: u32, next: u32) -> u32 {
    ((previous as u64 * next as u64 + 2048) >> 12) as u32
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn constants_match_showdown() {
        assert_eq!(of(3, 2), 6144);
        assert_eq!(of(13, 10), 5324); // chainModify(1.3) truncates 5324.8
        assert_eq!(of(3, 4), 3072);
    }

    #[test]
    fn modify_rounds_half_down() {
        assert_eq!(modify(5, 2048), 2); // 2.5 -> 2
        assert_eq!(modify(101, 6144), 151); // 151.5 -> 151
        assert_eq!(modify(100, 5324), 130); // 129.98 -> 130
    }

    #[test]
    fn chain_rounds_half_up() {
        assert_eq!(chain(ONE, 6144), 6144);
        assert_eq!(chain(6144, 5325), 7988); // 1.5 * 1.3: (6144*5325 + 2048) >> 12
    }
}
