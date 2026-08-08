"""Tests for src/robustness.py -- auditing how punishable a line is."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from robustness import (SEVERE, describe_action, line_report,  # noqa: E402
                        turn_robustness)

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


def greedy_choice(battle, movesets):
    with solver.solver_mode(nash=False):
        act, _s, _x = solver.solve_best_action(battle, "p1", movesets)
    return act


def test_exploitability_is_never_negative_beyond_rounding():
    """No play can beat the equilibrium against a best-responding opponent."""
    battle, ms = fresh()
    rec = turn_robustness(battle, ms, greedy_choice(battle, ms))
    assert rec is not None
    assert rec.exploitability > -1.0, rec.exploitability


def test_worst_case_is_at_most_the_equilibrium():
    battle, ms = fresh()
    rec = turn_robustness(battle, ms, greedy_choice(battle, ms))
    assert rec.worst_case <= rec.equilibrium + 1.0


def test_a_punisher_is_identified():
    """The report has to say WHO punishes, or it is not actionable."""
    battle, ms = fresh()
    rec = turn_robustness(battle, ms, greedy_choice(battle, ms))
    assert rec.punisher, "no punishing action returned"
    assert describe_action(rec.punisher)


def test_uses_the_wide_opponent_move_space_when_present():
    """A play only safe against the four moves we assumed is not safe."""
    import copy
    battle, ms = fresh()
    action = greedy_choice(battle, ms)
    wide = turn_robustness(battle, ms, action)
    narrow_battle = copy.deepcopy(battle)
    narrow_battle.wide_movesets = None
    narrow = turn_robustness(narrow_battle, ms, action)
    assert wide.exploitability >= narrow.exploitability - 1.0, (
        "widening their move space made the play look SAFER, which is backwards")


def test_line_report_walks_multiple_turns_and_ranks_the_worst():
    battle, ms = fresh()
    rep = line_report(battle, ms, lambda b: greedy_choice(b, ms), max_turns=4)
    assert rep.turns, "no turns audited"
    assert rep.worst_turn is not None
    assert rep.worst_turn.exploitability == max(t.exploitability for t in rep.turns)
    assert 0 <= rep.severe_count <= len(rep.turns)


def test_severe_flag_matches_the_threshold():
    battle, ms = fresh()
    rep = line_report(battle, ms, lambda b: greedy_choice(b, ms), max_turns=4)
    for t in rep.turns:
        assert t.severe == (t.exploitability > SEVERE)


def test_a_policy_returning_nothing_ends_the_audit_cleanly():
    battle, ms = fresh()
    rep = line_report(battle, ms, lambda b: None, max_turns=4)
    assert rep.turns == []
    assert rep.mean_exploitability == 0.0
    assert rep.worst_turn is None


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
