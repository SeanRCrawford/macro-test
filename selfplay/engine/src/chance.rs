//! Every random event in a battle goes through `Chance`.
//!
//! The calls mirror Showdown's PRNG wrappers (`random`, `randomChance`,
//! `sample`, `shuffle`), so the engine makes a random decision exactly where
//! Showdown does. Two modes:
//!
//! - `Sampled`: a seeded PRNG, for self-play. Distributions match Showdown's.
//! - `Policy`: deterministic outcomes from a threshold `t` in [0, 1]. A chance
//!   of n/d succeeds when n >= d or n/d >= t; `random(n)` returns
//!   ceil(t * n) - 1 clamped to [0, n); `sample` and `shuffle` follow from
//!   that. tools/showdown/gen_fixtures.js installs the same rule in place of
//!   Showdown's PRNG, so whole battles can be compared turn by turn without
//!   matching Showdown's exact random-number sequence. t = 0 makes everything
//!   succeed (crits, secondary effects, misses never happen), t > 1 makes
//!   every uncertain event fail.

#[derive(Debug, Clone, Copy)]
pub enum Chance {
    Sampled(Rng),
    Policy { threshold: f64 },
}

impl Chance {
    pub fn seeded(seed: u64) -> Self {
        Chance::Sampled(Rng::new(seed))
    }

    pub fn policy(threshold: f64) -> Self {
        Chance::Policy { threshold }
    }

    /// Showdown's `random(n)`: an integer in [0, n).
    pub fn random(&mut self, n: u32) -> u32 {
        if n <= 1 {
            return 0;
        }
        match self {
            Chance::Sampled(rng) => rng.below(n),
            Chance::Policy { threshold } => policy_index(*threshold, n),
        }
    }

    /// Showdown's `random(m, n)`: an integer in [m, n).
    pub fn random_range(&mut self, m: u32, n: u32) -> u32 {
        m + self.random(n - m)
    }

    /// Showdown's `randomChance(numerator, denominator)`.
    pub fn chance(&mut self, numerator: u32, denominator: u32) -> bool {
        match self {
            Chance::Sampled(rng) => rng.below(denominator) < numerator,
            Chance::Policy { threshold } => {
                numerator >= denominator || numerator as f64 / denominator as f64 >= *threshold
            }
        }
    }

    /// Showdown's `sample(items)`: an index into a list of `len` items.
    pub fn sample(&mut self, len: usize) -> usize {
        self.random(len as u32) as usize
    }

    /// Showdown's Fisher-Yates `shuffle(items, start, end)`. In policy mode
    /// Showdown's patched PRNG leaves the order unchanged.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        if let Chance::Sampled(rng) = self {
            let n = items.len();
            for i in 0..n.saturating_sub(1) {
                let j = i + rng.below((n - i) as u32) as usize;
                items.swap(i, j);
            }
        }
    }
}

fn policy_index(threshold: f64, n: u32) -> u32 {
    let v = (threshold * n as f64).ceil() as i64 - 1;
    v.clamp(0, n as i64 - 1) as u32
}

/// xoshiro256** seeded through splitmix64: small, fast, good quality.
#[derive(Debug, Clone, Copy)]
pub struct Rng {
    s: [u64; 4],
}

impl Rng {
    pub fn new(seed: u64) -> Self {
        let mut x = seed;
        let mut next = || {
            x = x.wrapping_add(0x9E37_79B9_7F4A_7C15);
            let mut z = x;
            z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
            z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
            z ^ (z >> 31)
        };
        Rng {
            s: [next(), next(), next(), next()],
        }
    }

    pub fn next_u64(&mut self) -> u64 {
        let result = self.s[1].wrapping_mul(5).rotate_left(7).wrapping_mul(9);
        let t = self.s[1] << 17;
        self.s[2] ^= self.s[0];
        self.s[3] ^= self.s[1];
        self.s[1] ^= self.s[2];
        self.s[0] ^= self.s[3];
        self.s[2] ^= t;
        self.s[3] = self.s[3].rotate_left(45);
        result
    }

    /// Uniform in [0, n) (Lemire's multiply-shift, bias-free).
    pub fn below(&mut self, n: u32) -> u32 {
        loop {
            let m = (self.next_u64() >> 32) * n as u64;
            if (m as u32) >= (n.wrapping_neg() % n) {
                return (m >> 32) as u32;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn policy_threshold_semantics() {
        let mut half = Chance::policy(0.5);
        assert!(
            half.chance(1, 2) && half.chance(9, 10) && !half.chance(1, 24) && !half.chance(3, 10)
        );
        assert_eq!(half.random(100), 49); // secondaries "random(100) < chance" succeed iff chance >= 50
        assert_eq!(half.random(16), 7);
        let mut all = Chance::policy(0.0);
        assert!(all.chance(1, 24));
        assert_eq!(all.random(100), 0);
        let mut none = Chance::policy(1.01);
        assert!(!none.chance(99, 100) && none.chance(100, 100));
        assert_eq!(none.random(100), 99);
    }

    #[test]
    fn sampled_is_roughly_uniform() {
        let mut c = Chance::seeded(7);
        let mut counts = [0u32; 4];
        for _ in 0..40_000 {
            counts[c.random(4) as usize] += 1;
        }
        assert!(
            counts.iter().all(|&k| (9_000..11_000).contains(&k)),
            "{counts:?}"
        );
    }
}
