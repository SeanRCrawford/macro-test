"""Tests for src/matrix_game.py against games with independently known answers.

Runs under pytest, or standalone with `python tests/test_matrix_game.py` so it
works in a bare checkout with no test dependencies installed.
"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from matrix_game import solve_matrix, double_oracle, maximin  # noqa: E402


def test_matching_pennies():
    """Classic mixed equilibrium: value 0, both sides uniform."""
    A = [[1, -1], [-1, 1]]
    value, p, q = solve_matrix(A)
    assert abs(value) < 0.02, value
    assert abs(p[0] - 0.5) < 0.02 and abs(q[0] - 0.5) < 0.02, (p, q)


def test_rock_paper_scissors():
    """Value 0, uniform over three actions."""
    A = [[0, -1, 1], [1, 0, -1], [-1, 1, 0]]
    value, p, q = solve_matrix(A)
    assert abs(value) < 0.02, value
    for x in p + q:
        assert abs(x - 1 / 3) < 0.03, (p, q)


def test_saddle_point_is_pure():
    """A = [[4,3],[2,1]] has a saddle at (row 0, col 1) worth 3."""
    A = [[4, 3], [2, 1]]
    value, p, q = solve_matrix(A)
    assert abs(value - 3) < 0.02, value
    assert p[0] > 0.98 and q[1] > 0.98, (p, q)


def test_strictly_dominated_row_gets_no_weight():
    A = [[2, 3], [1, 1]]
    value, p, q = solve_matrix(A)
    assert p[1] < 0.02, p
    assert abs(value - 2) < 0.02, value


def test_maximin_picks_best_worst_case():
    # Row 1 has the highest single payoff but the worst floor.
    A = [[5, 5], [100, -100]]
    best, idx = maximin(A)
    assert idx == 0 and best == 5


def test_double_oracle_matches_full_solve_on_random_games():
    """The correctness claim that justifies the pruning: same value as brute force."""
    rng = random.Random(20260808)
    for trial in range(25):
        n, m = rng.randint(2, 9), rng.randint(2, 9)
        A = [[rng.uniform(-100, 100) for _ in range(m)] for _ in range(n)]
        exact, _, _ = solve_matrix(A)
        res = double_oracle(n, m, lambda i, j: A[i][j])
        assert res.converged, f"trial {trial} did not converge"
        spread = max(max(r) for r in A) - min(min(r) for r in A)
        assert abs(res.value - exact) < 0.02 * spread, (
            f"trial {trial}: DO {res.value:.3f} vs exact {exact:.3f}")


def test_double_oracle_never_evaluates_more_than_the_full_matrix():
    rng = random.Random(1)
    A = [[rng.uniform(-10, 10) for _ in range(12)] for _ in range(12)]
    res = double_oracle(12, 12, lambda i, j: A[i][j])
    assert res.n_evals <= 144


def test_double_oracle_finds_the_saddle_from_a_bad_seed():
    """Seeded on the wrong actions, it should still reach the saddle."""
    A = [[4, 3], [2, 1]]
    res = double_oracle(2, 2, lambda i, j: A[i][j], seed_rows=[1], seed_cols=[0])
    assert res.converged
    assert abs(res.value - 3) < 0.02, res.value


def test_payoff_oracle_is_memoised():
    """Cells must not be recomputed -- in real use one call is one simulated turn."""
    calls = []
    A = [[1, 2], [3, 4]]

    def payoff(i, j):
        calls.append((i, j))
        return A[i][j]

    res = double_oracle(2, 2, payoff)
    assert len(calls) == len(set(calls)), "a cell was evaluated twice"
    assert res.n_evals == len(set(calls))


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
