"""The Rust engine's stat calculation must agree with src/stats.py exactly.

src/stats.py holds the Champions stat-point rule the rest of the repo uses, so
any disagreement is a bug on one side. Runs under pytest, or standalone with
`python selfplay/tests/test_stats_parity.py`. Needs the extension built
(see selfplay/README.md); skips with that reason under pytest otherwise.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

try:
    import pytest
    engine = pytest.importorskip("selfplay._engine", reason="build with: pip install ./selfplay")
except ImportError:  # standalone, no pytest
    import selfplay._engine as engine

import stats as py_stats  # noqa: E402  (src/stats.py)
from species_data import load_showdown_static  # noqa: E402

STATS = ["hp", "atk", "def", "spa", "spd", "spe"]


def test_calc_stat_exhaustive():
    """Every base stat, both IV extremes, every stat-point value, every nature
    multiplier. Catches float-vs-integer rounding differences in the nature step."""
    mismatches = []
    for base in range(1, 256):
        for iv in (0, 31):
            for points in range(0, 33):
                for pct in (90, 100, 110):
                    py = py_stats.calc_stat(base, iv, points, 50, False, pct / 100)
                    rs = engine.calc_stat(base, iv, points, 50, False, pct)
                    if py != rs:
                        mismatches.append((base, iv, points, pct, py, rs))
                py = py_stats.calc_stat(base, iv, points, 50, True)
                rs = engine.calc_stat(base, iv, points, 50, True)
                if py != rs:
                    mismatches.append((base, iv, points, "hp", py, rs))
    assert not mismatches, mismatches[:10]


def test_compute_stats_every_species_and_nature():
    pokedex, _, natures, _ = load_showdown_static()
    points_cases = [[0] * 6, [32, 32, 2, 0, 0, 0], [2, 0, 0, 32, 0, 32], [4, 4, 4, 4, 4, 4]]
    checked = 0
    for sid in engine.species_ids():
        base = pokedex[sid]["baseStats"]
        for nname, nature in natures.items():
            for pts in points_cases:
                want = py_stats.compute_stats(base, nature, dict(zip(STATS, pts)))
                got = engine.compute_stats(sid, nname, pts)
                assert got == want, (sid, nname, pts, got, want)
                checked += 1
    assert checked > 100_000


if __name__ == "__main__":
    test_calc_stat_exhaustive()
    test_compute_stats_every_species_and_nature()
    print("ok")
