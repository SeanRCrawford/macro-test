"""Tests for src/preview.py against hand-checkable cases.

The properties that matter are the endpoint behaviours: the whole argument for
a soft weighting is that it CONTAINS both of the things it replaces, so it can
ship incrementally rather than as a flag day.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from preview import (cvar, downside_score, plausibility_weights,  # noqa: E402
                     ranked_brings)


def test_weights_are_a_distribution():
    for tau in (10.0, 120.0, 1000.0, float("inf")):
        w = plausibility_weights([100.0, -50.0, 0.0, 250.0], tau)
        assert abs(sum(w) - 1.0) < 1e-9, (tau, sum(w))
        assert all(x >= 0 for x in w)


def test_infinite_tau_is_uniform_the_existing_behaviour():
    """tau -> infinity must reproduce the current sweep exactly, or section 6
    cannot ship incrementally."""
    w = plausibility_weights([10.0, -300.0, 55.0], float("inf"))
    assert w == [1 / 3, 1 / 3, 1 / 3]


def test_zero_tau_is_a_point_mass_on_their_best_bring():
    """tau -> 0 is the Nash-support endpoint: all weight on one column."""
    w = plausibility_weights([10.0, -300.0, 55.0], 0.0)
    assert w == [0.0, 1.0, 0.0]      # -300 is worst for us, so best for them


def test_brings_better_for_them_are_more_likely():
    margins = [200.0, 0.0, -200.0]   # last is best for them
    w = plausibility_weights(margins, tau=120.0)
    assert w[2] > w[1] > w[0]


def test_weights_are_stable_for_large_margins():
    """Softmax without a max-subtraction overflows here."""
    w = plausibility_weights([1e5, -1e5], tau=1.0)
    assert abs(sum(w) - 1.0) < 1e-9
    assert all(x == x for x in w)     # no NaN


def test_cvar_with_alpha_one_is_the_weighted_mean():
    values = [10.0, 20.0, 30.0]
    weights = [0.2, 0.3, 0.5]
    expected = 10 * 0.2 + 20 * 0.3 + 30 * 0.5
    assert abs(cvar(values, weights, alpha=1.0) - expected) < 1e-9


def test_cvar_with_tiny_alpha_approaches_the_worst_outcome():
    values = [-500.0, 10.0, 20.0]
    weights = [0.1, 0.4, 0.5]
    assert abs(cvar(values, weights, alpha=1e-6) - (-500.0)) < 1e-6


def test_cvar_reads_the_worst_tail_not_the_best():
    values = [-100.0, 0.0, 100.0]
    weights = [1 / 3, 1 / 3, 1 / 3]
    assert cvar(values, weights, alpha=0.34) < cvar(values, weights, alpha=1.0)


def test_cvar_ignores_zero_weight_entries():
    """An impossible bring must not drag the score down."""
    values = [-9999.0, 10.0, 20.0]
    weights = [0.0, 0.5, 0.5]
    assert cvar(values, weights, alpha=0.5) == 10.0


def test_uniform_weights_with_tiny_alpha_recover_worst_case_over_all():
    """The exact behaviour the current sweep has today."""
    margins = [50.0, -120.0, 30.0, 5.0]
    n = len(margins)
    got = cvar(margins, [1 / n] * n, alpha=1e-9)
    assert abs(got - min(margins)) < 1e-6


def test_a_catastrophic_but_unlikely_bring_still_shows_in_the_tail():
    """Why the weighting is soft rather than a hard support cutoff: an absurd
    bring gets a small weight, not deletion, so a disaster still surfaces."""
    margins = [80.0, 90.0, 100.0, -3000.0]   # last is terrible for us
    soft = downside_score(margins, tau=120.0, alpha=0.3)
    without = downside_score(margins[:3], tau=120.0, alpha=0.3)
    assert soft < without, (soft, without)


def test_downside_score_prefers_the_bring_with_the_better_bad_case():
    safe = [40.0, 45.0, 50.0, 55.0]        # never great, never bad
    spiky = [200.0, 210.0, 220.0, -400.0]  # better on average, one disaster
    assert downside_score(safe) > downside_score(spiky)


def test_ranked_brings_is_sorted_and_labelled():
    margins = [200.0, -200.0, 0.0]
    rows = ranked_brings(margins, labels=["a", "b", "c"])
    assert [r["label"] for r in rows] == ["b", "c", "a"]
    probs = [r["probability"] for r in rows]
    assert probs == sorted(probs, reverse=True)
    assert abs(sum(probs) - 1.0) < 1e-9


def test_empty_input_is_handled():
    assert plausibility_weights([]) == []
    assert downside_score([]) == 0.0
    assert cvar([], []) == 0.0


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
