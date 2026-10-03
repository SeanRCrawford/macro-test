"""Self-play VGC engine. The battle rules live in Rust (`selfplay._engine`);
see selfplay/DESIGN.md for the plan and selfplay/README.md to build."""
from selfplay._engine import calc_stat, compute_stats, dex_source, species_ids

__all__ = ["calc_stat", "compute_stats", "dex_source", "species_ids"]
