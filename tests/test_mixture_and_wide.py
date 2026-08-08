"""The two robustness fixes: play the mixture, widen the opponent's move space.

Both exist because exploitability -- not win rate -- is the metric that matches
this project's aim. Measured (tools/measure_robustness.py, lower is better):

    greedy                                    65.1
    Nash argmax,  narrow opponent space       75.5    worse than greedy
    Nash mixture, narrow opponent space       58.7
    Nash mixture, WIDE opponent space         43.0    the shipped configuration
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from turn_game import solve_turn  # noqa: E402

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


def test_sampling_is_on_by_default():
    """Nash is already opt-in; if it is on, its mixture is the whole point."""
    assert solver.NASH_SAMPLE is True


def test_sampling_actually_varies_the_chosen_action():
    """A deterministic argmax would give the same play every time -- which is
    exactly the exploitable behaviour this replaces."""
    battle, movesets = fresh()
    sol = solve_turn(battle, "p1", movesets)
    if not sol.is_mixed:
        return  # nothing to vary at this decision point
    seen = set()
    with solver.solver_mode(nash=True, depth=1):
        for seed in range(12):
            solver.seed_nash_sampling(seed)
            chosen, _s, _sim = solver.solve_best_action(battle, "p1", movesets)
            seen.add(frozenset((a.combatant.name, a.kind,
                                a.move.name if a.move else None) for a in chosen))
    assert len(seen) > 1, "sampling never produced a different play"


def test_sampling_is_reproducible_when_seeded():
    battle, movesets = fresh()
    picks = []
    with solver.solver_mode(nash=True, depth=1):
        for _ in range(2):
            solver.seed_nash_sampling(1234)
            chosen, _s, _sim = solver.solve_best_action(battle, "p1", movesets)
            picks.append(frozenset((a.combatant.name, a.kind,
                                    a.move.name if a.move else None) for a in chosen))
    assert picks[0] == picks[1]


def test_sampling_only_returns_actions_from_the_support():
    battle, movesets = fresh()
    sol = solve_turn(battle, "p1", movesets)
    support = {frozenset((a.combatant.name, a.kind, a.move.name if a.move else None)
                         for a in act) for act, _p in sol.support()}
    with solver.solver_mode(nash=True, depth=1):
        for seed in range(10):
            solver.seed_nash_sampling(seed)
            chosen, _s, _sim = solver.solve_best_action(battle, "p1", movesets)
            sig = frozenset((a.combatant.name, a.kind,
                             a.move.name if a.move else None) for a in chosen)
            assert sig in support, f"sampled {sig} outside the equilibrium support"


def test_wide_movesets_widen_only_the_opponent():
    battle, movesets = fresh()
    assert getattr(battle, "wide_movesets", None)
    wide = solve_turn(battle, "p1", movesets)
    narrow_battle = copy.deepcopy(battle)
    narrow_battle.wide_movesets = None
    narrow = solve_turn(narrow_battle, "p1", movesets)
    assert len(wide.their_actions) > len(narrow.their_actions), (
        "widening did not enlarge the opponent's action space")
    assert len(wide.our_actions) == len(narrow.our_actions), (
        "our own action space changed -- we know our own four moves")


def test_wide_movesets_survive_deepcopy():
    """Fourth instance of this pattern: movesets, force_roll_index, and now
    wide_movesets all have to be carried explicitly by Battle.__deepcopy__."""
    battle, _ = fresh()
    assert getattr(copy.deepcopy(battle), "wide_movesets", None)


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
