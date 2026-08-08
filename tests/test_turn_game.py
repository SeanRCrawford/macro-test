"""Tests for src/turn_game.py -- solving one turn as a matrix game."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import turn_game  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from turn_game import solve_turn  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]

_WORLD = None
_SOL = {}


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh(team_index=0):
    w = world()
    name = list(w["teams"])[team_index]
    return setup_battle(OUR_TEAM, enemy_bring(name, w), w)


def solution(team_index=0, depth=1):
    key = (team_index, depth)
    if key not in _SOL:
        battle, movesets = fresh(team_index)
        _SOL[key] = solve_turn(battle, "p1", movesets, depth=depth)
    return _SOL[key]


def test_strategies_are_probability_distributions():
    sol = solution()
    for dist in (sol.p, sol.q):
        assert abs(sum(dist) - 1.0) < 1e-6, sum(dist)
        assert all(x >= -1e-9 for x in dist), dist


def test_equilibrium_is_at_least_the_best_pure_worst_case():
    """Mixing can only help: the equilibrium value cannot be below maximin."""
    for i in range(3):
        sol = solution(i)
        assert sol.value >= sol.maximin_value - 1.0, (
            f"team {i}: equilibrium {sol.value:.1f} below maximin "
            f"{sol.maximin_value:.1f}")


def test_mixing_gain_is_never_negative():
    for i in range(3):
        assert solution(i).mixing_gain >= -1.0


def test_best_action_is_in_the_support():
    sol = solution()
    assert sol.best_action is not None
    assert sol.best_action in [a for a, _prob in sol.support()]


def test_solution_is_zero_sum_consistent_between_sides():
    """Solving the same turn from p2's seat must give the negated value.

    This is the payoff of making the leaf value antisymmetric in Phase A1: if it
    fails, the matrix is not really zero-sum and the equilibrium is meaningless.
    """
    battle, movesets = fresh()
    ours = solve_turn(battle, "p1", movesets)
    theirs = solve_turn(battle, "p2", movesets)
    assert abs(ours.value + theirs.value) < 25.0, (ours.value, theirs.value)


def test_exploitability_of_a_pure_action_is_never_negative():
    """No single action can beat the equilibrium against a best-responding
    opponent -- that is what equilibrium means."""
    sol = solution()
    rows = {i for (i, _j) in sol._matrix}
    for i in rows:
        exploit = sol.exploitability_of(i)
        assert exploit is None or exploit >= -1.0, (i, exploit)


def test_equilibrium_is_no_more_exploitable_than_the_greedy_pick():
    """The point of the whole exercise, as a property rather than a slogan."""
    sol = solution()
    rows = {i for (i, _j) in sol._matrix}
    worst_case = {i: sol.exploitability_of(i) for i in rows}
    best_pure = min(v for v in worst_case.values() if v is not None)
    assert best_pure >= -1.0
    assert sol.value >= sol.maximin_value - 1.0


def test_brute_force_and_double_oracle_agree():
    """The pruning must not change the answer -- section 3's correctness claim,
    on real battle matrices rather than the random ones in test_matrix_game."""
    battle, movesets = fresh()
    original = turn_game.BRUTE_FORCE_CELLS
    try:
        turn_game.BRUTE_FORCE_CELLS = 0          # force double oracle
        via_do = solve_turn(battle, "p1", movesets)
        turn_game.BRUTE_FORCE_CELLS = 10 ** 9    # force full matrix
        via_brute = solve_turn(battle, "p1", movesets)
    finally:
        turn_game.BRUTE_FORCE_CELLS = original
    assert abs(via_do.value - via_brute.value) < 5.0, (
        f"double oracle {via_do.value:.1f} vs brute force {via_brute.value:.1f}")
    assert via_do.n_sims < via_brute.n_sims, (
        f"double oracle simulated {via_do.n_sims} vs brute {via_brute.n_sims}")


def test_depth_two_costs_more_and_still_solves():
    battle, movesets = fresh()
    shallow = solve_turn(battle, "p1", movesets, depth=1)
    deep = solve_turn(battle, "p1", movesets, depth=2)
    assert deep.n_sims > shallow.n_sims
    assert deep.our_actions


def test_terminal_position_returns_a_value_without_crashing():
    battle, movesets = fresh()
    for c in battle.p2.roster:
        c.current_hp, c.fainted = 0, True
    sol = solve_turn(battle, "p1", movesets)
    assert sol.value > 1000


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
