"""BLAS thread caps must be set, and must reach worker processes.

An overnight run died with

    OpenBLAS error: Memory Allocation still failed after 10 retries, giving up

because OpenBLAS sizes its thread pool to the machine's core count in EVERY
process, and the search runs one process per core. The variables are read once
when the library loads, so the two ways to get this wrong are setting them too
late (after pandas is imported) and setting them only in the parent.

The import-order test is the important one: it fails if someone sorts the
imports in an entry point and moves `blas_limits` below `_harness`, which would
silently restore the crash on a big machine while passing every other test.
"""
import ast
import concurrent.futures as cf
import os
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

import blas_limits  # noqa: E402

VARS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")

# Entry points that start a process pool, and therefore must cap BLAS first.
ENTRY_POINTS = [
    os.path.join(ROOT, "tools", "search_teams.py"),
    os.path.join(ROOT, "tools", "generate_overnight.py"),
    os.path.join(ROOT, "src", "generate_team.py"),
]

# Modules that reach numpy. blas_limits must be imported before any of these.
NUMPY_REACHING = {"pandas", "numpy", "species_data", "_harness",
                  "matchup_search", "generate_team", "team_search", "solver"}


def _probe(_ignored):
    return {v: os.environ.get(v) for v in VARS}


def _import_order(path):
    """Top-level module names in the order they are first imported."""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=path)
    order = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            order.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            order.append(node.module.split(".")[0])
    return order


class TestBlasLimits(unittest.TestCase):

    def test_every_variable_is_capped(self):
        for var in VARS:
            with self.subTest(var=var):
                self.assertEqual(os.environ.get(var), "1")

    def test_the_cap_reaches_worker_processes(self):
        """Parent-only capping is the half-fix that still crashes."""
        with cf.ProcessPoolExecutor(max_workers=2) as pool:
            for seen in pool.map(_probe, range(2)):
                for var in VARS:
                    self.assertEqual(seen[var], "1", f"{var} lost in worker")

    def test_entry_points_cap_before_importing_anything_numpy_backed(self):
        """The ordering is load-bearing: these vars are read once, at load."""
        for path in ENTRY_POINTS:
            with self.subTest(entry=os.path.basename(path)):
                order = _import_order(path)
                self.assertIn("blas_limits", order,
                              f"{path} starts a pool but never caps BLAS")
                first_numpy = next((i for i, m in enumerate(order)
                                    if m in NUMPY_REACHING), None)
                if first_numpy is not None:
                    self.assertLess(
                        order.index("blas_limits"), first_numpy,
                        f"{path} imports {order[first_numpy]} before "
                        f"blas_limits; the cap will be ignored")

    def test_an_explicit_user_setting_is_not_overridden(self):
        """setdefault, not assignment -- someone tuning their own run wins."""
        import importlib
        original = os.environ.get("OMP_NUM_THREADS")
        os.environ["OMP_NUM_THREADS"] = "4"
        try:
            importlib.reload(blas_limits)
            self.assertEqual(os.environ["OMP_NUM_THREADS"], "4")
        finally:
            if original is None:
                del os.environ["OMP_NUM_THREADS"]
            else:
                os.environ["OMP_NUM_THREADS"] = original
            importlib.reload(blas_limits)


class TestWorkersAdvice(unittest.TestCase):

    def test_zero_means_one_per_core(self):
        self.assertEqual(blas_limits.workers_advice(0, cpu_count=16)[0], 16)

    def test_a_small_request_is_honoured_without_a_warning(self):
        workers, warning = blas_limits.workers_advice(4, cpu_count=16)
        self.assertEqual(workers, 4)
        self.assertIsNone(warning)

    def test_a_large_request_warns_about_memory_rather_than_capping(self):
        """Silently capping would hide why a run is slower than the flag says."""
        workers, warning = blas_limits.workers_advice(0, cpu_count=32)
        self.assertEqual(workers, 32)
        self.assertIsNotNone(warning)
        self.assertIn("RAM", warning)

    def test_negative_or_absurd_input_still_yields_a_usable_count(self):
        self.assertGreaterEqual(blas_limits.workers_advice(-5, cpu_count=8)[0], 1)


if __name__ == "__main__":
    unittest.main()
