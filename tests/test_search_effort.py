"""Tests for src/search_effort.py -- effort tiers and the resumable cache.

The cache exists so a multi-hour search survives being killed. Two properties
matter more than the rest: a re-run with the SAME settings must skip completed
work, and a re-run with DIFFERENT settings must NOT reuse it. A cache that
silently serves results computed under a cheaper tier is worse than no cache.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from search_effort import (TIER_ORDER, TIERS, ResultCache, batches,  # noqa: E402
                           relative_cost, tier)


def test_every_tier_is_ordered_and_complete():
    assert set(TIER_ORDER) == set(TIERS)
    for name in TIER_ORDER:
        t = TIERS[name]
        for field in ("label", "verify_top", "robustness", "leads", "turns", "blurb"):
            assert field in t, (name, field)


def test_quick_tier_reproduces_the_original_behaviour():
    """The cheap end must lose nothing -- no robustness audit, as before."""
    assert tier("quick")["robustness"] is False


def test_effort_increases_monotonically_along_the_order():
    costs = [relative_cost(n) for n in TIER_ORDER]
    assert costs == sorted(costs), costs
    assert costs[0] == 1.0
    assert costs[-1] > costs[0]


def test_unknown_tier_falls_back_rather_than_crashing():
    assert tier("nonsense")["label"] == TIERS["standard"]["label"]


def test_cache_round_trips_through_disk():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.json")
        c = ResultCache(path)
        c.put("k", {"value": 1})
        c.save()
        assert ResultCache(path).get("k") == {"value": 1}


def test_key_depends_on_every_input():
    base = ResultCache.key("bring", "A", "B", "standard", 10)
    assert ResultCache.key("bring", "A", "B", "standard", 10) == base
    for changed in (ResultCache.key("bring", "A", "C", "standard", 10),
                    ResultCache.key("bring", "A", "B", "thorough", 10),
                    ResultCache.key("bring", "A", "B", "standard", 12)):
        assert changed != base, "cache key ignored a setting that changes the answer"


def test_a_different_effort_tier_does_not_reuse_results():
    """The failure mode that makes a cache worse than useless."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.json")
        c = ResultCache(path)
        c.put(ResultCache.key("bring", "A", "B", "quick", 10), {"cheap": True})
        c.save()
        fresh = ResultCache(path)
        assert fresh.get(ResultCache.key("bring", "A", "B", "exhaustive", 10)) is None


def test_a_corrupt_cache_file_is_survivable():
    """Killing a run mid-write must not make the next run unstartable."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write('{"truncated": ')
        c = ResultCache(path)
        assert len(c) == 0
        c.put("k", 1)
        c.save()
        assert ResultCache(path).get("k") == 1


def test_save_is_atomic_enough_to_leave_valid_json():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.json")
        c = ResultCache(path)
        for i in range(50):
            c.put(f"k{i}", {"i": i})
        c.save()
        with open(path, encoding="utf-8") as fh:
            assert len(json.load(fh)) == 50
        assert not [f for f in os.listdir(d) if f.endswith(".tmp")], "temp file left behind"


def test_cache_with_no_path_is_a_no_op_not_an_error():
    c = ResultCache(None)
    c.put("k", 1)
    c.save()
    assert c.get("k") == 1


def test_batches_cover_everything_exactly_once():
    items = list(range(10))
    for size in (1, 3, 10, 25):
        out = [x for b in batches(items, size) for x in b]
        assert out == items, size


def test_batches_handles_a_zero_size():
    assert [b for b in batches([1, 2], 0)] == [[1], [2]]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
