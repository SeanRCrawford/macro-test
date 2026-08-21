"""Estimated time to complete, and why it is not `relative_cost`.

Asked for: "make sure sections have an estimated time to complete the
calculation". The obvious implementation is to multiply `search_effort.
relative_cost` by something, and it is wrong -- badly enough to be worse than
the prose it replaces. Measured, one (our team x one opponent) pairing:

    tier         relative_cost    measured    real ratio
    quick                  1.0      25.0 s         1.0x
    standard              17.0      39.8 s         1.6x
    thorough              73.0      94.8 s         3.8x

`relative_cost` models the audit and treats the screen as free. The screen plays
90 of our configurations against 90 of theirs and is most of the wall clock at
the cheap tiers. Told "17x", you budget a night for forty minutes of work.

The model here is fitted on quick and standard and predicts thorough to within
3.4%. That out-of-sample point is the only reason to believe a two-constant fit.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import time_estimate as te  # noqa: E402

# The measurements the constants were fitted to, kept here so a change to the
# constants that breaks them fails loudly.
# (tier, brings override, audit_all, measured seconds) -- every real
# observation the constants are fitted to, so changing them fails loudly.
MEASURED = [
    ("quick", None, False, 25.0),
    ("standard", None, False, 39.8),
    ("thorough", None, False, 94.8),
    ("standard", 1, False, 16.5),      # the one that broke the 2-term model
    ("standard", 1, True, 252.8),
    ("thorough+", None, False, 804.8),
]


class TestItReproducesTheMeasurements(unittest.TestCase):

    def test_every_measurement_is_within_tolerance(self):
        """Absolute OR relative: the worst relative error is +32% on a 16.5 s
        observation, which is 5 s. An estimate that is 5 s out on a 16 s job is
        fine; one that is 32% out on an hour is not, so both are allowed and
        the large observations are held to the tighter bound."""
        for tier, brings, audit_all, measured in MEASURED:
            predicted = te.tier_seconds(tier, brings=brings,
                                        audit_all=audit_all)
            close = (abs(predicted - measured) < 6.0
                     or abs(predicted - measured) / measured < 0.20)
            self.assertTrue(close, f"{tier} brings={brings} all={audit_all}: "
                                   f"predicted {predicted:.1f}s against "
                                   f"{measured:.1f}s")

    def test_the_big_observations_are_tight(self):
        for tier, brings, audit_all, measured in MEASURED:
            if measured < 100:
                continue
            predicted = te.tier_seconds(tier, brings=brings,
                                        audit_all=audit_all)
            self.assertLess(abs(predicted - measured) / measured, 0.05, tier)


class TestItBeatsRelativeCost(unittest.TestCase):
    """The reason this module exists rather than a one-line multiplication."""

    def test_relative_cost_is_wildly_wrong_as_a_time_ratio(self):
        from search_effort import relative_cost
        real = 39.8 / 25.0                       # standard against quick
        self.assertGreater(relative_cost("standard") / real, 8.0)

    def test_this_model_is_far_closer_than_scaling_relative_cost(self):
        """Compared head to head on absolute error, which is what a caption
        promising "about 7 min" is actually claiming. A ratio comparison was
        tried first and is the wrong test: it compounds the error on quick with
        the error on the tier and fails on arithmetic rather than on accuracy.
        """
        from search_effort import relative_cost
        for tier, measured in (("standard", 39.8), ("thorough", 94.8)):
            model = abs(te.tier_seconds(tier) - measured) / measured
            naive = abs(25.0 * relative_cost(tier) - measured) / measured
            self.assertLess(model, 0.20, tier)
            self.assertGreater(naive, 3.0, tier)
            self.assertLess(model, naive / 10, tier)


class TestBringsCostsTwice(unittest.TestCase):
    """The flaw the refit fixed, pinned so it cannot come back.

    The first model treated everything before the audit as a constant, and a
    verify_top=1 pairing then measured at 16.5 s -- below that "constant" 25 s.
    Verification plays verify_top x their configs full games, so `--brings`
    appears in TWO terms, not one.
    """

    def test_raising_brings_costs_more_than_the_audit_term_alone(self):
        audit_only = te.PER_AUDIT_UNIT * (90 - 3) * 2 * 16
        real = (te.tier_seconds("standard", brings=90)
                - te.tier_seconds("standard", brings=3))
        self.assertGreater(real, audit_only * 1.1)

    def test_verification_scales_with_brings_even_at_quick(self):
        """Quick does no audit at all, so any growth here is verification."""
        self.assertGreater(te.tier_seconds("quick", brings=90),
                           2 * te.tier_seconds("quick", brings=3))

    def test_a_fixed_enemy_lead_is_much_cheaper(self):
        """Six of their configs instead of 90 -- the 'guaranteed enemy lead'
        mode, which shrinks verification and the audit together."""
        self.assertLess(te.tier_seconds("standard", enemy_configs=6),
                        te.tier_seconds("standard"))


class TestTheKnobsMoveItTheRightWay(unittest.TestCase):

    def test_auditing_all_ninety_costs_more(self):
        self.assertGreater(te.tier_seconds("standard", audit_all=True),
                           te.tier_seconds("standard"))

    def test_the_equilibrium_tier_is_much_dearer(self):
        self.assertGreater(te.tier_seconds("thorough+"),
                           5 * te.tier_seconds("thorough"))

    def test_more_pairings_cost_proportionally_more(self):
        one = te.tier_seconds("standard", pairings=1)
        eight = te.tier_seconds("standard", pairings=8)
        self.assertAlmostEqual(eight / one, 8.0, places=5)

    def test_workers_divide_but_never_below_one_pairing(self):
        """Eight workers cannot make a single pairing finish in an eighth of
        the time, and an estimate that says so is worse than none."""
        one = te.tier_seconds("standard", pairings=1, jobs=8)
        self.assertAlmostEqual(one, te.tier_seconds("standard", pairings=1),
                               places=5)

    def test_quick_pays_the_screen_too(self):
        """The whole point: the cheapest tier is not near-free."""
        self.assertGreater(te.tier_seconds("quick"), 20.0)


class TestHumanise(unittest.TestCase):

    def test_seconds_are_rounded_to_five(self):
        self.assertEqual(te.humanise(41), "about 40 s")

    def test_minutes(self):
        self.assertEqual(te.humanise(420), "about 7 min")

    def test_hours(self):
        self.assertEqual(te.humanise(9000), "about 2.5 h")

    def test_none_is_not_a_crash(self):
        self.assertEqual(te.humanise(None), "unknown")

    def test_it_never_implies_false_precision(self):
        """"about 412 s" invites someone to time it, get 500, and conclude the
        estimator is broken rather than approximate."""
        self.assertNotIn("412", te.humanise(412))


class TestCalibration(unittest.TestCase):

    def test_a_slower_machine_raises_the_factor(self):
        self.assertGreater(te.calibrate(1.0, predicted=100, actual=200), 1.0)

    def test_a_faster_machine_lowers_it(self):
        self.assertLess(te.calibrate(1.0, predicted=100, actual=50), 1.0)

    def test_one_odd_run_nudges_rather_than_replaces(self):
        """A warm cache returning instantly must not convince the estimator
        that everything is now free."""
        self.assertGreater(te.calibrate(1.0, predicted=100, actual=1), 0.5)

    def test_it_is_bounded(self):
        self.assertLessEqual(te.calibrate(1.0, predicted=1, actual=10_000_000),
                             5.0)

    def test_rubbish_input_is_ignored(self):
        self.assertEqual(te.calibrate(1.3, predicted=0, actual=10), 1.3)
        self.assertEqual(te.calibrate(1.3, predicted=10, actual=0), 1.3)


class TestTheLineBudget(unittest.TestCase):
    """Measured: 3.1 s at 0 win-games, 6.6 s at 4, 10.0 s at 12."""

    def test_it_matches_the_measurements(self):
        for games, measured in ((0, 3.1), (4, 6.6), (12, 10.0)):
            self.assertAlmostEqual(te.line_seconds(games), measured, delta=1.0,
                                   msg=f"{games} games")

    def test_the_cost_is_sub_linear_in_the_game_cap(self):
        """`win_samples` is a CAP: adaptive sampling stops once the Wilson
        interval is tight, so tripling it does not triple the cost. A linear
        model missed the middle measurement by 18%."""
        self.assertLess(te.line_seconds(12) - te.line_seconds(0),
                        3 * (te.line_seconds(4) - te.line_seconds(0)))

    def test_a_45s_budget_does_not_cover_all_fifteen_leads(self):
        """The fact worth printing. The default budget answers the question for
        a handful of their leads, and the panel used to imply it answered it
        for all of them."""
        self.assertLess(te.leads_in_budget(45, 8), 15)

    def test_more_games_reach_fewer_leads(self):
        self.assertLess(te.leads_in_budget(60, 24), te.leads_in_budget(60, 4))

    def test_it_never_claims_zero_leads(self):
        self.assertGreaterEqual(te.leads_in_budget(1, 24), 1)


class TestTheAppUsesIt(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                    "app.py"), encoding="utf-8").read()

    def test_the_shared_effort_control_shows_an_estimate(self):
        self.assertIn("estimate_caption(effort, pairings, audit_all, jobs)",
                      self.src)

    def test_it_no_longer_reports_relative_cost_as_a_time(self):
        """The caption used to read "roughly 17x the quick setting", which is
        the number this module exists to replace."""
        self.assertNotIn("x the quick setting", self.src)

    def test_finished_runs_feed_the_calibration(self):
        self.assertIn("note_actual(", self.src)

    def test_the_budgeted_panels_say_the_budget_is_the_time(self):
        self.assertGreaterEqual(self.src.count("the budget IS the time"), 3)


if __name__ == "__main__":
    unittest.main()
