//! Fast doubles battle engine for Gen 9 Champions VGC self-play.
//!
//! Pure Rust with no Python dependency, so `cargo test` and benchmarks run on
//! their own; `selfplay-pybind` exposes it to Python. See ../DESIGN.md.

pub mod damage;
pub mod dex;
pub mod fixed;
pub mod stats;
pub mod team;
