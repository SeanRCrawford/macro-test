"""Tests for src/team_rating.py -- rating a bring by how punishable it is."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from robustness import LineReport, TurnRobustness  # noqa: E402
from team_rating import (BringRating, plausible_leads,  # noqa: E402
                         rank_bringings, rate_bring)


def fake_report(*exploitabilities):
    rep = LineReport()
    for i, e in enumerate(exploitabilities, start=1):
        rep.turns.append(TurnRobustness(turn=i, exploitability=e, regret=0.0,
                                        equilibrium=0.0, worst_case=-e))
    return rep


def test_weighting_ignores_implausible_leads():
    """The flaw in worst-case-over-90: a lead no good player picks should not
    drag a team's rating down."""
    rating = BringRating(our_bring=["a"], per_lead=[
        (("x", "y"), 0.95, fake_report(10.0, 10.0)),     # likely, handled well
        (("w", "z"), 0.05, fake_report(500.0, 500.0)),   # absurd, handled badly
    ])
    assert rating.weighted_exploitability < 40.0, rating.weighted_exploitability


def test_worst_lead_is_the_one_handled_worst():
    good, bad = ("g1", "g2"), ("b1", "b2")
    rating = BringRating(our_bring=["a"], per_lead=[
        (good, 0.5, fake_report(5.0)),
        (bad, 0.5, fake_report(300.0)),
    ])
    assert rating.worst_lead[0] == bad


def test_severe_and_total_turns_are_counted_across_leads():
    rating = BringRating(our_bring=["a"], per_lead=[
        (("x", "y"), 0.5, fake_report(5.0, 500.0)),
        (("w", "z"), 0.5, fake_report(500.0)),
    ])
    assert rating.total_turns == 3
    assert rating.severe_turns == 2


def test_ranking_puts_the_least_punishable_first():
    tough = BringRating(our_bring=["tough"], per_lead=[(("x",), 1.0, fake_report(10.0))])
    soft = BringRating(our_bring=["soft"], per_lead=[(("x",), 1.0, fake_report(200.0))])
    assert rank_bringings([soft, tough])[0].our_bring == ["tough"]


def test_empty_rating_is_zero_not_a_crash():
    rating = BringRating(our_bring=["a"])
    assert rating.weighted_exploitability == 0.0
    assert rating.worst_lead is None
    assert rating.total_turns == 0


def test_plausible_leads_returns_the_most_likely_first_and_truncates():
    margins = [300.0, -200.0, 0.0, 50.0]
    labels = ["a", "b", "c", "d"]
    got = plausible_leads(margins, labels, top_k=2)
    assert len(got) == 2
    assert got[0]["label"] == "b"          # worst for us = best for them
    assert got[0]["probability"] >= got[1]["probability"]


class _StubBattle:
    """Minimal stand-in: line_report only needs is_over() before choose()."""

    def is_over(self):
        return False


def test_rate_bring_skips_leads_that_produce_no_turns():
    def build(our4, lead):
        return _StubBattle(), {}

    def choose(_battle):
        return None

    rating = rate_bring(["a"], [(("x", "y"), 1.0)], build, choose)
    assert rating.per_lead == [], "a lead with no auditable turns was recorded"


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
