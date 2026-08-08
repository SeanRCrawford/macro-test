"""Tests for the leaf value in src/solver.py.

The property that matters is antisymmetry: solving a turn as a zero-sum matrix
game is only coherent if our gain is exactly the opponent's loss. These build
real battle states rather than mocking, because the asymmetry being guarded
against came from the interaction between the `seen` filter and the roster, and
a mock would not reproduce it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle, step  # noqa: E402
from solver import (heuristic_eval, symmetric_eval, leaf_value,  # noqa: E402
                    our_candidate_joint_actions, greedy_opponent_joint_action)

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def sample_states(n_teams=2, max_turns=4):
    """A handful of genuinely reachable mid-battle states."""
    w = world()
    states = []
    for team_name in list(w["teams"])[:n_teams]:
        battle, movesets = setup_battle(OUR_TEAM, enemy_bring(team_name, w), w)
        for _ in range(max_turns):
            if battle.is_over():
                break
            states.append(battle)
            ours = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                               movesets, battle.turn_num + 1)
            greedy = greedy_opponent_joint_action(battle, battle.p2, battle.p1,
                                                  movesets, battle.turn_num + 1)
            if not ours or not greedy:
                break
            nxt = step(battle, ours[0], greedy)
            if nxt is None:
                break
            battle = nxt
    return states


def test_symmetric_eval_is_antisymmetric():
    for state in sample_states():
        a = symmetric_eval(state, "p1")
        b = symmetric_eval(state, "p2")
        assert abs(a + b) < 1e-9, f"not antisymmetric: {a} vs {b}"


def test_raw_heuristic_is_antisymmetric_at_source():
    """The real fix: every term reads common knowledge, so no wrapper is needed.

    Averaging (symmetric_eval) is NOT sufficient on its own -- over a one-sided
    heuristic it turns a side asymmetry into a reveal incentive, weighting hidden
    mons 0.5x and revealed ones 1.0x. This asserts the terms themselves agree.
    """
    worst = max(abs(heuristic_eval(s, "p1") + heuristic_eval(s, "p2"))
                for s in sample_states())
    assert worst < 1e-6, f"heuristic_eval is one-sided again by {worst:.1f} points"


def test_revealing_our_own_mon_is_not_worth_points():
    """Regression: the reveal incentive that the averaging-only fix introduced.

    A hidden Pokemon is necessarily at full HP and is placeholdered for threat
    value, so flipping `revealed` must not move the score at all.
    """
    w = world()
    battle, _ = setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)
    hidden = [c for c in battle.p1.roster
              if c not in battle.p1.active and not getattr(c, "revealed", False)]
    assert hidden, "expected some benched, unrevealed Pokemon to test with"

    before = heuristic_eval(battle, "p1")
    hidden[0].revealed = True
    after = heuristic_eval(battle, "p1")
    assert abs(after - before) < 1e-9, (
        f"revealing a benched mon changed the score by {after - before:+.1f}")


def test_symmetric_eval_keeps_the_scale_of_the_raw_heuristic():
    """Halving a difference of same-scale quantities must not rescale things."""
    for state in sample_states():
        raw = abs(heuristic_eval(state, "p1"))
        sym = abs(symmetric_eval(state, "p1"))
        assert sym <= raw + 800.0, (sym, raw)


def test_leaf_value_follows_the_flag():
    state = sample_states(n_teams=1, max_turns=1)[0]
    original = solver.SYMMETRIC_EVAL
    try:
        solver.SYMMETRIC_EVAL = True
        assert leaf_value(state, "p1") == symmetric_eval(state, "p1")
        solver.SYMMETRIC_EVAL = False
        assert leaf_value(state, "p1") == heuristic_eval(state, "p1")
    finally:
        solver.SYMMETRIC_EVAL = original


def test_terminal_states_stay_decisive_and_opposite():
    """A lost position must read as lost from one side and won from the other."""
    w = world()
    battle, _ = setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)
    for c in battle.p1.roster:
        c.current_hp = 0
        c.fainted = True
    assert symmetric_eval(battle, "p1") < -1000
    assert symmetric_eval(battle, "p2") > 1000
    assert abs(symmetric_eval(battle, "p1") + symmetric_eval(battle, "p2")) < 1e-9


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
