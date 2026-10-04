//! Fast doubles battle engine for Gen 9 Champions VGC self-play.
//!
//! Pure Rust with no Python dependency, so `cargo test` and benchmarks run on
//! their own; `selfplay-pybind` exposes it to Python. See ../DESIGN.md.

// Loops over side indices mirror Showdown's code; iterator forms obscure that.
#![allow(clippy::needless_range_loop)]

pub mod battle;
pub mod chance;
pub mod corpus;
pub mod damage;
pub mod dex;
pub mod env;
pub mod fixed;
pub mod stats;
pub mod team;
