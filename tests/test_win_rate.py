"""A sound win rate: what it has to satisfy.

Reported: "these winrates are not sound". `wins / 90` breaks three things at
once, and each has a test class here.

The first two classes need no simulation at all -- they are properties of the
aggregation, and they are exactly the properties that make the number mean
something. A hand-built matrix says more about whether the maths is right than
any amount of real battles would.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import win_rate  # noqa: E402


class TestTheInterval(unittest.TestCase):
    """P3: a proportion without its uncertainty is not a measurement."""

    def test_it_never_leaves_the_unit_interval(self):
        """The reason it is Wilson and not the normal approximation: at 20/20
        the normal upper bound is 1.0 + something, which is not a probability."""
        for k, n in ((20, 20), (0, 20), (1, 3), (19, 20)):
            lo, hi = win_rate.wilson(k, n)
            self.assertGreaterEqual(lo, 0.0, (k, n))
            self.assertLessEqual(hi, 1.0, (k, n))
            self.assertLessEqual(lo, hi)

    def test_it_brackets_the_estimate(self):
        for k, n in ((5, 20), (10, 20), (15, 20)):
            lo, hi = win_rate.wilson(k, n)
            self.assertLessEqual(lo, k / n)
            self.assertGreaterEqual(hi, k / n)

    def test_more_evidence_narrows_it(self):
        narrow = win_rate.wilson(50, 100)
        wide = win_rate.wilson(5, 10)
        self.assertLess(narrow[1] - narrow[0], wide[1] - wide[0])

    def test_no_evidence_is_total_ignorance(self):
        self.assertEqual(win_rate.wilson(0, 0), (0.0, 1.0))

    def test_a_perfect_record_is_not_certainty(self):
        """8/8 is not 100%. Reporting it as such is how a screen throws away a
        better team for a worse one with a luckier sample."""
        lo, hi = win_rate.wilson(8, 8)
        self.assertLess(lo, 1.0)
        self.assertGreater(lo, 0.5)


class TestTheAggregatesAreOrdered(unittest.TestCase):
    """P2: the three readings must stand in a fixed relation, or the middle one
    is not the equilibrium it claims to be.

        worst_case <= game_value <= max over rows of the row mean

    game_value >= worst_case because mixing can only help the maximiser, and
    the worst case forbids mixing. It is <= the best row mean because the
    column player is choosing adversarially rather than uniformly.
    """

    MATRICES = [
        [[0.9, 0.1], [0.2, 0.8]],           # a genuine mixing game
        [[1.0, 0.0], [0.0, 1.0]],           # matching pennies: value 0.5
        [[0.7, 0.6], [0.3, 0.2]],           # row 0 dominates
        [[0.5]],                            # degenerate
        [[0.99, 0.99], [0.98, 0.01]],       # one catastrophic cell
    ]

    def test_the_ordering_holds(self):
        for A in self.MATRICES:
            r = win_rate.aggregate(A, iters=2000)
            best_row_mean = max(sum(row) / len(row) for row in A)
            # Tolerance is the SOLVER's, not the maths'. Both inequalities are
            # exact; regret matching converges to about 1e-4, and on a game
            # whose value sits exactly on the bound (the first matrix below is
            # worth 0.5, and so is its best row mean) a 1e-6 tolerance measures
            # the iteration count rather than the property.
            tol = 1e-3
            self.assertLessEqual(r["worst_case"], r["game_value"] + tol, A)
            self.assertLessEqual(r["game_value"], best_row_mean + tol, A)

    def test_matching_pennies_is_a_coin_flip(self):
        """The textbook case, and the one where the uniform mean happens to
        agree with the truth -- useful precisely because it isolates the
        others."""
        r = win_rate.aggregate([[1.0, 0.0], [0.0, 1.0]], iters=5000)
        self.assertAlmostEqual(r["game_value"], 0.5, places=1)
        self.assertAlmostEqual(r["worst_case"], 0.0, places=6)

    def test_the_uniform_mean_can_be_wildly_optimistic(self):
        """THE reported defect, as a number. A team that beats 9 of their 10
        brings and loses to the one they will always pick averages 90% and is
        worth nothing."""
        A = [[1.0] * 9 + [0.0]]
        r = win_rate.aggregate(A, iters=2000)
        self.assertAlmostEqual(r["uniform"], 0.9, places=6)
        self.assertAlmostEqual(r["game_value"], 0.0, places=2)

    def test_mixing_rescues_what_a_single_bring_cannot(self):
        """And the converse, which is why worst_case alone is not the answer
        either: two brings that each lose to something can be mixed into a
        coin flip."""
        A = [[1.0, 0.0], [0.0, 1.0]]
        r = win_rate.aggregate(A, iters=5000)
        self.assertAlmostEqual(r["worst_case"], 0.0, places=6)
        self.assertGreater(r["game_value"], 0.4)

    def test_it_names_what_they_should_bring(self):
        """The actionable half: their equilibrium mixture is the scouting
        report."""
        A = [[1.0] * 9 + [0.0]]
        r = win_rate.aggregate(A, iters=2000)
        self.assertAlmostEqual(r["their_mix"][9], 1.0, places=2)

    def test_the_mixtures_are_distributions(self):
        for A in self.MATRICES:
            r = win_rate.aggregate(A, iters=2000)
            for mix in (r["our_mix"], r["their_mix"]):
                self.assertAlmostEqual(sum(mix), 1.0, places=3, msg=A)
                self.assertTrue(all(x >= -1e-9 for x in mix), mix)

    def test_an_empty_matrix_does_not_raise(self):
        self.assertEqual(win_rate.aggregate([])["game_value"], 0.0)


class TestTheEstimate(unittest.TestCase):

    def test_timeouts_count_against_us_and_stay_visible(self):
        """Conservative, matching the existing search -- but a matchup that
        times out half the time is telling you something a win rate cannot."""
        e = win_rate.Estimate(wins=5, losses=3, timeouts=2)
        self.assertEqual(e.n, 10)
        self.assertAlmostEqual(e.p, 0.5)
        self.assertAlmostEqual(e.timeout_rate, 0.2)

    def test_an_empty_estimate_is_zero_not_a_crash(self):
        self.assertEqual(win_rate.Estimate().p, 0.0)


class TestQuadrature(unittest.TestCase):
    """The speed-up: weighted deterministic scenarios instead of sampling.

    Suggested directly -- "with 16 damage rolls, could instead use 3 - worst,
    lower third, upper third". rolls.py already brackets the 16 that way and
    carries the share of the distribution each stands in for; crossing them
    with the two speed-tie branches gives six games instead of twenty-four.
    """

    def test_the_weights_are_a_distribution_over_the_sixteen_rolls(self):
        """If they do not sum to 1 the 'probability' is scaled by an arbitrary
        constant, and every comparison silently inherits it."""
        self.assertAlmostEqual(sum(w for _i, w in win_rate.ROLL_SCENARIOS), 1.0)
        self.assertAlmostEqual(sum(w for _t, w in win_rate.TIE_SCENARIOS), 1.0)

    def test_the_scenarios_bracket_the_range(self):
        indices = [i for i, _w in win_rate.ROLL_SCENARIOS]
        self.assertEqual(min(indices), 0)     # every roll low
        self.assertEqual(max(indices), 15)    # every roll high

    def test_all_scenarios_won_is_one_and_none_is_zero(self):
        won = win_rate.Quadrature([(True, False, 0.5), (True, False, 0.5)])
        lost = win_rate.Quadrature([(False, False, 0.5), (False, False, 0.5)])
        self.assertEqual(won.p, 1.0)
        self.assertEqual(lost.p, 0.0)

    def test_the_weights_actually_weight(self):
        """A win on the 7/16 mid scenario is worth more than one on the 4/16
        high scenario. Counting scenarios instead of weighting them would make
        this 50%."""
        q = win_rate.Quadrature([(True, False, 7 / 16), (False, False, 4 / 16)])
        self.assertAlmostEqual(q.p, 7 / 11)

    def test_the_spread_says_when_the_dice_decide(self):
        """The honest uncertainty for a deterministic method: not a confidence
        interval, but 'does this line depend on the roll'."""
        split = win_rate.Quadrature([(True, False, 0.5), (False, False, 0.5)])
        firm = win_rate.Quadrature([(True, False, 0.5), (True, False, 0.5)])
        self.assertEqual(split.spread, 1.0)
        self.assertEqual(firm.spread, 0.0)

    def test_it_walks_and_quacks_like_an_estimate(self):
        """aggregate() and evaluate() read `.p` off whatever they are given, so
        the two estimators have to be interchangeable."""
        q = win_rate.Quadrature([(True, False, 0.5), (False, False, 0.5)])
        for attr in ("p", "n", "wins", "losses", "timeouts", "interval",
                     "timeout_rate"):
            self.assertTrue(hasattr(q, attr), attr)
        self.assertAlmostEqual(win_rate.aggregate([[q]])["game_value"], q.p,
                               places=2)

    def test_an_unknown_method_is_refused(self):
        with self.assertRaises(ValueError):
            win_rate.cell_estimator("vibes")


class TestLazyEvaluation(unittest.TestCase):
    """Double oracle over the bring matrix: correct, and MEASURED NOT TO HELP.

    The idea was that the equilibrium is supported on a handful of brings, so
    most cells need never be simulated. Measured on the shapes the pipeline
    actually has (matrix_game.double_oracle, counting distinct payoff calls):

        random    6x20    107/120   89%
        random    8x90    720/720  100%
        random    8x360  2880/2880 100%
        clustered 8x90    556/720   77%   (their brings in 6 archetypes)

    Nothing, or nearly nothing. The reason is structural: finding the column
    player's best response to a mixture requires SCANNING every column, so with
    few rows the oracle touches the whole matrix on its first iteration. Real
    matrices cluster (many of their brings share a lead) and that buys 23%,
    which is not a speed-up worth a second code path.

    It is kept because it is correct and because a much wider matrix -- every
    lead ORDERING, not just every bring -- may change the arithmetic. It is not
    the answer to "how do I make this fast"; quadrature is.
    """

    def fake_matrix(self, A):
        def fake(our4, their4, world, **kw):
            p = A[our4[0]][their4[0]]
            return win_rate.Quadrature([(True, False, p), (False, False, 1 - p)])
        return fake

    def test_it_agrees_with_the_full_matrix_on_the_value(self):
        """The only claim it makes: laziness changes the cost, not the answer."""
        A = [[0.9, 0.1, 0.5], [0.2, 0.8, 0.5], [0.4, 0.4, 0.4]]
        real = win_rate.matchup_win_prob_quadrature
        win_rate.matchup_win_prob_quadrature = self.fake_matrix(A)
        try:
            lazy = win_rate.evaluate_lazily([[0], [1], [2]], [[0], [1], [2]],
                                            {}, method="quadrature")
        finally:
            win_rate.matchup_win_prob_quadrature = real
        full = win_rate.aggregate(A, iters=5000)
        self.assertAlmostEqual(lazy["game_value"], full["game_value"], places=2)

    def test_it_reports_what_it_actually_evaluated(self):
        """So the negative result above stays visible instead of being assumed
        away by whoever reads the function name."""
        A = [[0.9, 0.1, 0.5], [0.2, 0.8, 0.5], [0.4, 0.4, 0.4]]
        real = win_rate.matchup_win_prob_quadrature
        win_rate.matchup_win_prob_quadrature = self.fake_matrix(A)
        try:
            res = win_rate.evaluate_lazily([[0], [1], [2]], [[0], [1], [2]],
                                           {}, method="quadrature")
        finally:
            win_rate.matchup_win_prob_quadrature = real
        self.assertEqual(res["n_cells"], 9)
        self.assertLessEqual(res["n_cells_evaluated"], 9)
        self.assertGreater(res["n_cells_evaluated"], 0)


class TestAgainstTheEngine(unittest.TestCase):
    """P1, end to end: the cells have to come from real games, and the whole
    point is that a single deterministic playout does not tell you the rate."""

    @classmethod
    def setUpClass(cls):
        from _harness import load_world
        cls.w = load_world()
        cls.ours = list(cls.w["teams"]["Rain"])[:4]
        cls.theirs = list(cls.w["teams"]["Big 6"])[:4]

    def test_a_matchup_estimate_is_a_real_proportion(self):
        est = win_rate.matchup_win_prob(self.ours, self.theirs, self.w,
                                        n_samples=6, turns=14)
        self.assertEqual(est.n, 6)
        self.assertEqual(est.wins + est.losses + est.timeouts, 6)
        self.assertTrue(0.0 <= est.p <= 1.0)
        lo, hi = est.interval
        self.assertLessEqual(lo, est.p)
        self.assertGreaterEqual(hi, est.p)

    def test_the_same_seed_gives_the_same_answer(self):
        """Two teams compared today must compare the same way tomorrow."""
        a = win_rate.matchup_win_prob(self.ours, self.theirs, self.w,
                                      n_samples=4, turns=12, seed=7)
        b = win_rate.matchup_win_prob(self.ours, self.theirs, self.w,
                                      n_samples=4, turns=12, seed=7)
        self.assertEqual((a.wins, a.losses, a.timeouts),
                         (b.wins, b.losses, b.timeouts))

    def test_the_playout_underneath_is_genuinely_stochastic(self):
        """The precondition P1 rests on: if the engine ignores the seed, an
        n-sample estimate is one deterministic game counted n times.

        Checked on the TURN COUNT, not the winner. A decisive matchup honestly
        returns the same winner from every seed -- that is a real 100%, not a
        stuck RNG -- so demanding the winner vary would fail on correct
        behaviour. Damage rolls changing the LENGTH of the game is the property
        that actually distinguishes the two.
        """
        from matchup_search import play_out_pair
        outcomes = set()
        for seed in range(8):
            winner, turns, _b = play_out_pair(
                self.ours, self.theirs, self.w["merged"], self.w["moves"],
                self.w["natures"], self.w["typechart"], max_turns=18,
                rng_seed=seed)
            outcomes.add((winner, turns))
        self.assertGreater(len(outcomes), 1,
                           "every seed gave an identical game -- the rolls are "
                           "not varying, so a 'probability' over them is one "
                           "deterministic result counted n times")

    def test_a_decisive_matchup_is_allowed_to_be_certain(self):
        """The other half of the above: p = 1.0 is a legitimate answer, and the
        interval is what keeps it honest about the sample size."""
        est = win_rate.matchup_win_prob(self.ours, self.theirs, self.w,
                                        n_samples=6, turns=18)
        if est.p in (0.0, 1.0):
            lo, hi = est.interval
            self.assertLess(hi - lo, 1.0)
            self.assertTrue(lo > 0.0 or hi < 1.0)

    def test_evaluate_reports_what_it_cost(self):
        res = win_rate.evaluate([self.ours], [self.theirs], self.w,
                                method="sample", n_samples=4, turns=12)
        self.assertEqual(res["n_cells"], 1)
        self.assertEqual(res["n_games"], 4)
        self.assertIn("game_value", res)

    def test_one_cell_makes_all_three_aggregates_agree(self):
        """A sanity anchor: with a single bring each there is nothing to choose
        and nothing to mix, so the three readings must coincide."""
        res = win_rate.evaluate([self.ours], [self.theirs], self.w,
                                method="sample", n_samples=4, turns=12)
        self.assertAlmostEqual(res["uniform"], res["worst_case"], places=6)
        self.assertAlmostEqual(res["uniform"], res["game_value"], places=2)


if __name__ == "__main__":
    unittest.main()
