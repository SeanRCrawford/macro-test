"""The Nash solver must be a drop-in replacement for the greedy one.

Two properties that are easy to get wrong and produce SILENT failures rather
than errors:

  1. `solve_best_action` returns (action, score, sim), and several callers use
     `sim is None` as "the solver found nothing, stop here" -- committed_plan
     and matchup_search both do. A Nash path returning None unconditionally
     makes every one of those bail on turn one, which looks exactly like a
     legitimately dead position.
  2. The solver has to apply on EVERY turn of a played-out battle, not only the
     first. It is selected by a module global, so anything that captures a
     decision function once, or restores the global mid-playout, would quietly
     revert to greedy for the remaining turns.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import solver  # noqa: E402
import turn_game  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh():
    w = world()
    return setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)


def test_nash_path_returns_a_usable_sim():
    """Regression: callers read `sim is None` as a bail-out signal."""
    battle, movesets = fresh()
    with solver.solver_mode(nash=True, depth=1):
        action, _score, sim = solver.solve_best_action(battle, "p1", movesets)
    assert action, "no action returned"
    assert sim is not None, (
        "Nash path returned sim=None -- committed_plan and matchup_search treat "
        "that as 'solver found nothing' and stop on turn one")


def test_greedy_and_nash_agree_on_the_shape_of_the_return():
    battle, movesets = fresh()
    with solver.solver_mode(nash=False):
        greedy = solver.solve_best_action(battle, "p1", movesets)
    with solver.solver_mode(nash=True, depth=1):
        nash = solver.solve_best_action(battle, "p1", movesets)
    assert len(greedy) == len(nash) == 3
    for got in (greedy, nash):
        assert got[0], "no action"
        assert isinstance(got[1], (int, float))
        assert got[2] is not None


def test_solver_mode_restores_the_previous_setting():
    before = (solver.NASH_SOLVER, solver.NASH_DEPTH)
    with solver.solver_mode(nash=True, depth=2):
        assert solver.NASH_SOLVER is True and solver.NASH_DEPTH == 2
    assert (solver.NASH_SOLVER, solver.NASH_DEPTH) == before


def test_solver_mode_restores_even_when_the_body_raises():
    before = (solver.NASH_SOLVER, solver.NASH_DEPTH)
    try:
        with solver.solver_mode(nash=True, depth=2):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert (solver.NASH_SOLVER, solver.NASH_DEPTH) == before


def test_nash_applies_to_every_turn_of_a_played_out_battle():
    """One equilibrium solve per turn, not just the first."""
    from matchup_search import play_out_worst_case
    w = world()
    names = list(w["teams"])
    our4 = w["teams"][names[1]][:4]
    enemy4 = w["teams"][names[0]][:4]

    calls = {"n": 0}
    original = turn_game.solve_turn

    def counting(*args, **kwargs):
        if not kwargs.get("inner"):
            calls["n"] += 1
        return original(*args, **kwargs)

    turn_game.solve_turn = counting
    try:
        with solver.solver_mode(nash=True, depth=1):
            _winner, turns, _battle = play_out_worst_case(
                our4, enemy4, w["merged"], w["moves"], w["natures"],
                w["typechart"], max_turns=8)
    finally:
        turn_game.solve_turn = original

    assert calls["n"] >= turns, (
        f"only {calls['n']} equilibrium solves for {turns} turns -- the solver "
        "reverted to greedy partway through the battle")


def test_greedy_default_does_not_invoke_the_matrix_solver():
    """The default must stay off: the flip is a deliberate, separate decision."""
    assert solver.NASH_SOLVER is False, "NASH_SOLVER shipped enabled"

    from matchup_search import play_out_worst_case
    w = world()
    names = list(w["teams"])
    calls = {"n": 0}
    original = turn_game.solve_turn

    def counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    turn_game.solve_turn = counting
    try:
        play_out_worst_case(w["teams"][names[1]][:4], w["teams"][names[0]][:4],
                            w["merged"], w["moves"], w["natures"],
                            w["typechart"], max_turns=6)
    finally:
        turn_game.solve_turn = original
    assert calls["n"] == 0, f"matrix solver ran {calls['n']} times by default"


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
