"""Tests for src/matching.py against hand-checkable assignment problems."""
import itertools
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matching import max_weight_matching  # noqa: E402


def brute_force(weights):
    """Reference implementation: try every injective row->column map."""
    n, m = len(weights), len(weights[0])
    best = float("-inf")
    k = min(n, m)
    for rows in itertools.combinations(range(n), k):
        for cols in itertools.permutations(range(m), k):
            total = sum(weights[r][c] for r, c in zip(rows, cols))
            best = max(best, total)
    return best


def test_identity_picks_the_diagonal():
    w = [[10, 1, 1], [1, 10, 1], [1, 1, 10]]
    total, pairs = max_weight_matching(w)
    assert total == 30
    assert sorted(pairs) == [(0, 0), (1, 1), (2, 2)]


def test_greedy_would_be_wrong_here():
    """Row 0's best column is also row 1's only good one; greedy loses 8."""
    w = [[10, 9], [10, 0]]
    total, _ = max_weight_matching(w)
    assert total == 19  # row0->col1 (9) + row1->col0 (10), not 10 + 0


def test_columns_are_used_at_most_once():
    w = [[5, 5], [5, 5], [5, 5]]
    _total, pairs = max_weight_matching(w)
    cols = [c for _r, c in pairs]
    assert len(cols) == len(set(cols))


def test_more_rows_than_columns_leaves_surplus_unmatched():
    w = [[1], [7], [3]]
    total, pairs = max_weight_matching(w)
    assert total == 7
    assert pairs == [(1, 0)]


def test_more_columns_than_rows_matches_every_row():
    w = [[1, 9, 4]]
    total, pairs = max_weight_matching(w)
    assert total == 9
    assert pairs == [(0, 1)]


def test_negative_weights_still_force_an_assignment():
    """A bad answer is still the answer -- coverage must not silently opt out."""
    w = [[-5, -9], [-8, -2]]
    total, pairs = max_weight_matching(w)
    assert len(pairs) == 2
    assert total == -7  # -5 + -2


def test_empty_inputs():
    assert max_weight_matching([]) == (0.0, [])
    assert max_weight_matching([[]]) == (0.0, [])


def test_matches_brute_force_on_random_instances():
    rng = random.Random(20260808)
    for _ in range(60):
        n, m = rng.randint(1, 5), rng.randint(1, 5)
        w = [[rng.uniform(-50, 50) for _ in range(m)] for _ in range(n)]
        total, pairs = max_weight_matching(w)
        assert abs(total - brute_force(w)) < 1e-9
        assert abs(total - sum(w[r][c] for r, c in pairs)) < 1e-9


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
