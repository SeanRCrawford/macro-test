"""The beam's scoring function, and the terms parked inside it.

"The generator's fast objective must be better." A replacement was written --
score the WORST third of threats rather than the mean, require a real margin
before calling a threat answered, and credit having a second independent answer
-- and then measured the only way that counts: play the top teams each objective
proposes against the preset library with the real engine
(tools/measure_objective.py).

    old   1927/2208  (87.3%)   best team 92%
    new   1885/2208  (85.4%)   best team 89%

It lost. So the terms ship at zero and these tests pin two things: that the
default is byte-identical to the objective that measured better, and that the
parked terms still work when switched on, so the next attempt starts from
something that runs.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import team_search  # noqa: E402

TEAM = ["A", "B", "C", "D", "E", "F"]
K1, K2, K3 = [("Foe", (f"x{i}", f"y{i}")) for i in range(3)]
KEYS = [K1, K2, K3]
MERGED = {n: {"types": ["Normal"], "score": 100.0, "defensive_chart": None}
          for n in TEAM}


def matrix(rows, default=-50.0):
    full = {p: {k: default for k in KEYS}
            for p in itertools.combinations(TEAM, 2)}
    for pair, margins in rows.items():
        full[pair].update(margins)
    return full


class TestTheDefaultIsTheMeasuredOne(unittest.TestCase):

    def test_the_parked_terms_are_off(self):
        self.assertEqual(team_search.W_DOWNSIDE, 0.0)
        self.assertEqual(team_search.W_DEPTH, 0.0)
        self.assertEqual(team_search.SAFE_MARGIN, 0.0)

    def test_the_total_reduces_to_the_original_formula(self):
        """pairs_won * MATCHUP_WEIGHT + MARGIN_WEIGHT * mean coverage + synergy.
        If this drifts, the beam silently changed and the 87.3% no longer
        describes it."""
        mtx = matrix({("A", "B"): {K1: 40.0, K2: 5.0},
                      ("C", "D"): {K2: 30.0, K3: -10.0}})
        got = team_search.score_team(TEAM, mtx, KEYS, MERGED)
        expected = (got["pairs_won"] * team_search.MATCHUP_WEIGHT
                    + team_search.MARGIN_WEIGHT * got["coverage"]
                    + got["synergy"])
        self.assertAlmostEqual(got["total"], expected)

    def test_answered_equals_pairs_won_at_the_shipped_threshold(self):
        mtx = matrix({("A", "B"): {K1: 0.5, K2: 40.0}})
        got = team_search.score_team(TEAM, mtx, KEYS, MERGED)
        self.assertEqual(got["answered"], got["pairs_won"])


class TestTheParkedTerms(unittest.TestCase):
    """They are off, not broken. Switched on they must still do what the
    comment claims, or the next person inherits a dead end."""

    def setUp(self):
        self._saved = (team_search.SAFE_MARGIN, team_search.W_DOWNSIDE,
                       team_search.W_DEPTH)
        team_search.SAFE_MARGIN, team_search.W_DOWNSIDE, team_search.W_DEPTH = (
            20.0, 6.0, 120.0)

    def tearDown(self):
        (team_search.SAFE_MARGIN, team_search.W_DOWNSIDE,
         team_search.W_DEPTH) = self._saved

    def test_a_coin_flip_margin_is_not_an_answer(self):
        mtx = matrix({("A", "B"): {K1: 5.0, K2: 40.0, K3: 40.0}})
        got = team_search.score_team(TEAM, mtx, KEYS, MERGED)
        self.assertEqual(got["pairs_won"], 3)      # all three beat zero...
        self.assertEqual(got["answered"], 2)       # ...but one is a coin flip

    def test_the_score_is_made_of_the_threats_handled_worst(self):
        """A lopsided team and an even one with the same MEAN must not tie."""
        even = matrix({("A", "B"): {K1: 30.0, K2: 30.0, K3: 30.0}})
        lopsided = matrix({("A", "B"): {K1: 89.0, K2: 1.0, K3: 0.0}})
        self.assertGreater(
            team_search.score_team(TEAM, even, KEYS, MERGED)["downside"],
            team_search.score_team(TEAM, lopsided, KEYS, MERGED)["downside"])

    def test_a_second_independent_answer_counts(self):
        one = matrix({("A", "B"): {K1: 40.0}})
        two = matrix({("A", "B"): {K1: 40.0}, ("C", "D"): {K1: 40.0}})
        self.assertEqual(
            team_search.score_team(TEAM, one, KEYS, MERGED)["answer_depth"], 0.0)
        self.assertGreater(
            team_search.score_team(TEAM, two, KEYS, MERGED)["answer_depth"], 0.0)

    def test_two_answers_sharing_a_pokemon_are_one_answer(self):
        """Two pairs that both need the same Mega are not a backup."""
        shared = matrix({("A", "B"): {K1: 40.0}, ("A", "C"): {K1: 40.0}})
        self.assertEqual(
            team_search.score_team(TEAM, shared, KEYS, MERGED)["answer_depth"],
            0.0)


class TestWorstThird(unittest.TestCase):

    def test_it_reads_the_bottom_third(self):
        self.assertAlmostEqual(team_search._worst_third([9, 1, 5, 3, 7, 11]), 2.0)

    def test_a_short_list_still_has_a_worst(self):
        self.assertEqual(team_search._worst_third([4.0]), 4.0)
        self.assertEqual(team_search._worst_third([]), 0.0)


if __name__ == "__main__":
    unittest.main()
