//! Every random event in a battle goes through `Chance`.
//!
//! The calls mirror Showdown's PRNG wrappers (`random`, `randomChance`,
//! `sample`, `shuffle`), so the engine makes a random decision exactly where
//! Showdown does. Three modes:
//!
//! - `Sampled`: a seeded PRNG, for self-play. Distributions match Showdown's.
//! - `Scripted`: for enumerating a turn's chance outcomes (`enumerate.rs`).
//!   Each draw is split into classes of outcomes that play differently (a
//!   hit or a miss; a damage roll that KOs or doesn't), and the draw takes
//!   the class the script says, or the first class past the script's end,
//!   recording every class's probability. Rolls whose exact value doesn't
//!   matter are banded (`random_banded`, `damage_roll`) and take the
//!   band's middle value.
//! - `Policy`: deterministic outcomes from a threshold `t` in [0, 1]. A chance
//!   of n/d succeeds when n >= d or n/d >= t; `random(n)` returns
//!   ceil(t * n) - 1 clamped to [0, n); `sample` and `shuffle` follow from
//!   that. tools/showdown/gen_fixtures.js installs the same rule in place of
//!   Showdown's PRNG, so whole battles can be compared turn by turn without
//!   matching Showdown's exact random-number sequence. t = 0 makes everything
//!   succeed (crits, secondary effects, misses never happen), t > 1 makes
//!   every uncertain event fail.

#[derive(Debug, Clone)]
pub enum Chance {
    Sampled(Rng),
    Policy { threshold: f64 },
    Scripted(Box<Script>),
}

/// One draw with more than one class of outcome.
#[derive(Debug, Clone)]
pub struct Draw {
    pub taken: u8,
    pub probs: Vec<f64>,
}

/// The classes to take, and what was drawn.
#[derive(Debug, Clone, Default)]
pub struct Script {
    pub forced: Vec<u8>,
    pub trace: Vec<Draw>,
    /// The probability of the classes taken.
    pub prob: f64,
    /// Damage rolls are split into this many equal bands (besides the KO
    /// split): 1 keeps only "KOs" and "doesn't".
    pub roll_bands: u32,
}

impl Script {
    pub fn new(forced: Vec<u8>, roll_bands: u32) -> Self {
        Script {
            forced,
            trace: Vec::new(),
            prob: 1.0,
            roll_bands: roll_bands.clamp(1, 16),
        }
    }

    /// Take a class among `probs` (more than one).
    fn branch(&mut self, probs: Vec<f64>) -> usize {
        let i = self.trace.len();
        let taken = self.forced.get(i).copied().unwrap_or(0);
        assert!((taken as usize) < probs.len(), "script class out of range");
        self.prob *= probs[taken as usize];
        self.trace.push(Draw { taken, probs });
        taken as usize
    }

    /// Take one of the classes [0, b0), [b0, b1), ... [bk, n) (empty ones
    /// dropped) and return its middle value.
    fn banded(&mut self, n: u32, bounds: &[u32]) -> u32 {
        let mut classes: Vec<(u32, u32)> = Vec::with_capacity(bounds.len() + 1);
        let mut lo = 0;
        for &b in bounds.iter().chain(std::iter::once(&n)) {
            let b = b.min(n);
            if b > lo {
                classes.push((lo, b));
                lo = b;
            }
        }
        let pick = if classes.len() == 1 {
            0
        } else {
            let probs = classes.iter().map(|&(a, b)| (b - a) as f64 / n as f64).collect();
            self.branch(probs)
        };
        let (a, b) = classes[pick];
        a + (b - a - 1) / 2
    }
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
            Chance::Scripted(s) => s.branch(vec![1.0 / n as f64; n as usize]) as u32,
        }
    }

    /// `random(n)` where only which class [0, b0), [b0, b1), ... [bk, n) the
    /// value falls in matters (`bounds` ascending). Sampled and policy modes
    /// draw exactly as `random(n)`; scripted mode branches on the classes.
    pub fn random_banded(&mut self, n: u32, bounds: &[u32]) -> u32 {
        match self {
            Chance::Scripted(s) => s.banded(n, bounds),
            _ => self.random(n),
        }
    }

    /// `random(n) < c`, as Showdown's secondaries roll it.
    pub fn roll_under(&mut self, c: u32, n: u32) -> bool {
        self.random_banded(n, &[c]) < c
    }

    /// The damage roll, an index into the 16 rolls (`random(16)`). `split`
    /// is where the rolls change from KOing to not (the number of KOing
    /// rolls, since index 0 is the highest).
    pub fn damage_roll(&mut self, split: Option<u32>) -> u32 {
        match self {
            Chance::Scripted(s) => {
                let bands = s.roll_bands;
                let mut bounds: Vec<u32> = (1..bands).map(|k| k * 16 / bands).collect();
                if let Some(k) = split.filter(|&k| k > 0 && k < 16) {
                    bounds.push(k);
                    bounds.sort_unstable();
                    bounds.dedup();
                }
                s.banded(16, &bounds)
            }
            _ => self.random(16),
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
            Chance::Scripted(_) if numerator >= denominator => true,
            Chance::Scripted(_) if numerator == 0 => false,
            Chance::Scripted(s) => {
                let p = numerator as f64 / denominator as f64;
                s.branch(vec![p, 1.0 - p]) == 0
            }
        }
    }

    /// Showdown's `sample(items)`: an index into a list of `len` items.
    pub fn sample(&mut self, len: usize) -> usize {
        self.random(len as u32) as usize
    }

    /// `shuffle`, except that scripted mode leaves the order unchanged
    /// instead of enumerating it (ties whose order rarely matters).
    pub fn shuffle_minor<T>(&mut self, items: &mut [T]) {
        if !matches!(self, Chance::Scripted(_)) {
            self.shuffle(items);
        }
    }

    /// Showdown's Fisher-Yates `shuffle(items, start, end)`. In policy mode
    /// Showdown's patched PRNG leaves the order unchanged.
    pub fn shuffle<T>(&mut self, items: &mut [T]) {
        if matches!(self, Chance::Policy { .. }) {
            return;
        }
        let n = items.len();
        for i in 0..n.saturating_sub(1) {
            let j = i + self.random((n - i) as u32) as usize;
            items.swap(i, j);
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
