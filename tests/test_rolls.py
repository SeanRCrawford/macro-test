"""Tests for src/rolls.py and the roll-scenario plumbing.

The framing being tested is that rolls belong in the SAFETY analysis, not in an
average: a play whose value collapses when the roll goes against it is not safe,
however good its mean looks. So the aggregation must be tunable between the mean
and the worst case, and it must actually reach the worst case.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import turn_game  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from rolls import (N_ROLLS, kill_fraction_label, kill_probability,  # noqa: E402
                   risk_aggregate, roll_sensitivity, roll_values, scenarios)
from turn_step import step  # noqa: E402

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


# --- roll reconstruction -------------------------------------------------

def test_roll_values_reconstruct_the_engine_ladder():
    """The engine builds base*mod*(0.85 + 0.01*i); lo and hi determine the rest."""
    base_mod = 100.0
    lo, hi = base_mod * 0.85, base_mod * 1.00
    got = roll_values(lo, hi)
    expected = [base_mod * (0.85 + 0.01 * i) for i in range(N_ROLLS)]
    assert len(got) == N_ROLLS
    for a, b in zip(got, expected):
        assert abs(a - b) < 1e-9, (a, b)


def test_roll_values_handles_a_flat_range():
    assert roll_values(50.0, 50.0) == [50.0] * N_ROLLS


def test_kill_probability_matches_the_corpus_quantity():
    """62.5% = 10/16 is the number the VGC corpus actually states."""
    lo, hi = 85.0, 100.0
    target = roll_values(lo, hi)[6]      # rolls 6..15 kill -> 10 of 16
    assert abs(kill_probability(lo, hi, target) - 10 / 16) < 1e-9
    assert kill_fraction_label(lo, hi, target) == "10/16"


def test_kill_probability_endpoints():
    assert kill_probability(120.0, 140.0, 100.0) == 1.0
    assert kill_probability(10.0, 20.0, 100.0) == 0.0
    # Certain outcomes are not worth labelling as a fraction.
    assert kill_fraction_label(120.0, 140.0, 100.0) is None
    assert kill_fraction_label(10.0, 20.0, 100.0) is None


def test_a_fifteen_sixteenths_kill_differs_from_a_one_sixteenth_kill():
    """The distinction an average roll erases entirely."""
    lo, hi = 85.0, 100.0
    values = roll_values(lo, hi)
    assert kill_probability(lo, hi, values[1]) > kill_probability(lo, hi, values[14])


# --- risk aggregation ----------------------------------------------------

def test_risk_one_is_the_plain_weighted_mean():
    values, weights = [0.0, 100.0], [0.5, 0.5]
    assert abs(risk_aggregate(values, weights, risk=1.0) - 50.0) < 1e-9


def test_low_risk_reads_the_bad_tail():
    values, weights = [-200.0, 100.0], [0.5, 0.5]
    averse = risk_aggregate(values, weights, risk=0.1)
    neutral = risk_aggregate(values, weights, risk=1.0)
    assert averse < neutral
    assert abs(averse - (-200.0)) < 1e-6


def test_a_play_that_collapses_on_a_low_roll_scores_worse_than_a_steady_one():
    """The whole point: equal means, different safety."""
    weights = [5 / 16, 7 / 16, 4 / 16]
    steady = [40.0, 50.0, 60.0]
    spiky = [-150.0, 50.0, 260.0]
    assert abs(risk_aggregate(steady, weights, 1.0)
               - risk_aggregate(spiky, weights, 1.0)) < 40.0   # similar means
    assert risk_aggregate(spiky, weights, 0.4) < risk_aggregate(steady, weights, 0.4)


def test_roll_sensitivity_is_the_spread():
    assert roll_sensitivity([10.0, 50.0, 30.0]) == 40.0
    assert roll_sensitivity([]) == 0.0


def test_scenario_weights_sum_to_one():
    pairs = scenarios(True)
    assert abs(sum(w for _i, w in pairs) - 1.0) < 1e-9
    assert len(pairs) >= 2
    assert scenarios(False) == [(None, 1.0)]


# --- plumbing ------------------------------------------------------------

def test_force_roll_index_survives_deepcopy():
    """Regression: Battle.__deepcopy__ is selective. Dropping this silently
    reverts every simulated branch to the average roll, which looks like 'roll
    scenarios make no difference' rather than like a bug."""
    battle, _ = fresh()
    battle.force_roll_index = 0
    assert getattr(copy.deepcopy(battle), "force_roll_index", "MISSING") == 0


def test_step_applies_the_roll_index_and_low_beats_high():
    """A minimum roll must do strictly less damage than a maximum roll."""
    battle, movesets = fresh()
    from solver import our_candidate_joint_actions, greedy_opponent_joint_action
    ours = our_candidate_joint_actions(battle, battle.p1, battle.p2, movesets, 1)
    theirs = greedy_opponent_joint_action(battle, battle.p2, battle.p1, movesets, 1)

    def enemy_hp_after(roll_index):
        for joint in ours:
            nxt = step(battle, joint, theirs, roll_index=roll_index)
            if nxt is not None:
                return sum(c.current_hp for c in nxt.p2.roster)
        return None

    low, high = enemy_hp_after(0), enemy_hp_after(15)
    assert low is not None and high is not None
    assert low > high, (
        f"max roll left {high} enemy HP, min roll left {low} -- roll_index is "
        "not reaching the damage calculation")


def test_step_does_not_mutate_the_callers_battle():
    battle, movesets = fresh()
    from solver import our_candidate_joint_actions, greedy_opponent_joint_action
    ours = our_candidate_joint_actions(battle, battle.p1, battle.p2, movesets, 1)
    theirs = greedy_opponent_joint_action(battle, battle.p2, battle.p1, movesets, 1)
    step(battle, ours[0], theirs, roll_index=0)
    assert getattr(battle, "force_roll_index", None) is None


def test_scenarios_off_by_default_so_behaviour_is_unchanged():
    assert turn_game.ROLL_SCENARIOS is False


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
