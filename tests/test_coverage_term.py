"""Tests for the answer-preservation term wired into solver.heuristic_eval."""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from threat import (build_threat_matrix, coverage_differential,  # noqa: E402
                    coverage_for)

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


def test_coverage_differential_is_antisymmetric():
    battle, movesets = fresh()
    T = build_threat_matrix(battle, movesets)
    assert abs(coverage_differential(T) - -(-coverage_differential(T))) < 1e-9
    # And the evaluation built on it must stay zero-sum.
    assert abs(solver.heuristic_eval(battle, "p1")
               + solver.heuristic_eval(battle, "p2")) < 1e-6


def test_movesets_survive_deepcopy():
    """Regression: Battle.__deepcopy__ is selective, and dropping movesets makes
    the coverage term silently zero in every simulated state."""
    battle, _ = fresh()
    assert getattr(battle, "movesets", None)
    assert getattr(copy.deepcopy(battle), "movesets", None), \
        "movesets lost across deepcopy -- coverage term would vanish in search"


def test_term_is_off_without_movesets_and_on_with_them():
    """Tests the mechanism, independent of whether it is enabled by default.

    COVERAGE_WEIGHT currently ships at 0.0 -- the term is measured and parked,
    not deleted -- so this forces it on rather than relying on the default.
    """
    battle, _ = fresh()
    original = solver.COVERAGE_WEIGHT
    try:
        solver.COVERAGE_WEIGHT = 0.25
        with_term = solver.heuristic_eval(battle, "p1")
        bare = copy.deepcopy(battle)
        bare.movesets = None
        without_term = solver.heuristic_eval(bare, "p1")
        assert with_term != without_term, "coverage term had no effect at all"
    finally:
        solver.COVERAGE_WEIGHT = original


def test_default_is_off_so_the_cost_is_not_paid_silently():
    """The term costs 4.35x per matrix cell; enabling it must be deliberate."""
    assert solver.COVERAGE_WEIGHT == 0.0, (
        "COVERAGE_WEIGHT was enabled -- re-run tools/measure_headtohead.py and "
        "tools/measure_coverage.py before shipping that")


def our_coverage_without(battle, movesets, name):
    """Our answer quality per threat, after losing the named Pokemon."""
    sim = copy.deepcopy(battle)
    for c in sim.p1.roster:
        if c.name == name:
            c.current_hp, c.fainted = 0, True
    T = build_threat_matrix(sim, movesets)
    return coverage_for(T, T.theirs, T.ours)


def test_losing_a_keystone_collapses_our_coverage():
    """The section 4b claim: an 'even' trade that removes our only answer to a
    threat costs far more than trading a Pokemon nothing depends on.

    Asserted on OUR coverage, which is the quantity that expresses the claim.
    coverage_differential wraps this for zero-sum antisymmetry and is a weaker
    signal -- see test_differential_is_antisymmetric_but_a_weaker_signal.
    """
    battle, movesets = fresh()
    T = build_threat_matrix(battle, movesets)
    base = coverage_for(T, T.theirs, T.ours)

    # Rank our Pokemon by how much coverage each one carries.
    drops = {c.name: base - our_coverage_without(battle, movesets, c.name)
             for c in T.ours}

    # The mon that answers the most threats best must be the costliest to lose.
    keystones = set()
    for enemy in T.theirs:
        ranked = T.answers_to(enemy)
        if len(ranked) >= 2 and ranked[0][1] - ranked[1][1] > 20:
            keystones.add(ranked[0][0].name)
    assert keystones, "no uniquely-answered threat in this matchup"

    worst_spare = max((d for n, d in drops.items() if n not in keystones),
                      default=None)
    assert worst_spare is not None, "every Pokemon is a keystone here"
    for k in keystones:
        assert drops[k] > worst_spare, (
            f"losing keystone {k} cost {drops[k]:.1f} coverage, no more than the "
            f"most costly non-keystone at {worst_spare:.1f}")


def test_coverage_does_not_reward_losing_a_pokemon():
    """Regression: with fewer answerers than threats the matching could simply
    drop the threat it handled worst, making a loss look like an improvement."""
    battle, movesets = fresh()
    T = build_threat_matrix(battle, movesets)
    base = coverage_for(T, T.theirs, T.ours)
    for c in T.ours:
        after = our_coverage_without(battle, movesets, c.name)
        assert after <= base + 1e-9, (
            f"losing {c.name} RAISED our coverage {base:.1f} -> {after:.1f}")


def test_differential_is_antisymmetric_but_a_weaker_signal():
    """Documents a real limitation rather than hiding it.

    The differential subtracts how well THEY answer US, and that half moves when
    our roster changes, so it does not rank keystone losses as cleanly as our
    coverage alone. It is kept because the leaf value must be exactly zero-sum;
    the weight is set low (solver.COVERAGE_WEIGHT) so it breaks ties rather than
    overruling material.
    """
    battle, movesets = fresh()
    T = build_threat_matrix(battle, movesets)
    ours = coverage_for(T, T.theirs, T.ours)
    theirs = coverage_for(T, T.ours, T.theirs)
    assert abs(coverage_differential(T) - (ours - theirs)) < 1e-9


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
