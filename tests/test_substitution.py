"""The substitution loop's decisions: which member to drop, what to bring in.

These pin the DECISIONS, not the ratings -- everything here runs on a
hand-written screener matrix, so the suite stays fast and does not depend on the
dataset. What the loop does with a rating (accept only an improvement) is
covered by test_roster_rating.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import fast_eval  # noqa: E402
import substitution  # noqa: E402

TEAM = ["A", "B", "C", "D", "E", "F"]
# Two opponents, one lead pair each, so the arithmetic below is checkable by eye.
K1 = ("Rain", ("x1", "x2"))
K2 = ("Sand", ("y1", "y2"))
KEYS = [K1, K2]


def matrix(rows):
    """{(our_a, our_b): {enemy_key: margin}} with every unlisted pair at -50."""
    import itertools
    full = {}
    for pair in itertools.combinations(TEAM + ["G", "H", "Mega A"], 2):
        full[pair] = {K1: -50.0, K2: -50.0}
    for pair, margins in rows.items():
        full[pair] = {**full.get(pair, {}), **margins}
    return full


def pm_for(rows):
    pm = substitution.PairMatrix({}, {}, {}, {}, base=matrix(rows))
    return pm


class TestPairMatrix(unittest.TestCase):

    def test_pair_order_does_not_matter(self):
        pm = pm_for({("A", "B"): {K1: 12.0}})
        self.assertEqual(pm.margin(("B", "A"), K1), 12.0)
        self.assertEqual(pm.computed, 0)

    def test_missing_margins_are_simulated_once_and_memoised(self):
        calls = []
        original = fast_eval.fast_pair_score

        def fake(our_pair, enemy_pair, *args, **kwargs):
            calls.append((our_pair, enemy_pair))
            return {"margin": 7.0}

        fast_eval.fast_pair_score = fake
        try:
            pm = substitution.PairMatrix({}, {}, {}, {}, base={})
            self.assertEqual(pm.margin(("A", "B"), K1), 7.0)
            self.assertEqual(pm.margin(("B", "A"), K1), 7.0)   # served from memo
        finally:
            fast_eval.fast_pair_score = original
        self.assertEqual(len(calls), 1)
        self.assertEqual(pm.computed, 1)

    def test_best_margin_is_the_best_pair_not_the_average(self):
        """Team preview: we choose our lead after seeing their six."""
        pm = pm_for({("A", "B"): {K1: 5.0}, ("C", "D"): {K1: 40.0}})
        self.assertEqual(pm.best_margin(TEAM, K1), 40.0)
        self.assertEqual(pm.best_margin(TEAM, K1, exclude=("C",)), 5.0)


class TestDropRanking(unittest.TestCase):

    def test_a_member_no_committed_bring_contains_is_dropped_first(self):
        """Real-engine evidence beats the screener's opinion.

        F contributes the team's best answer to both threats, but the audit
        never brought it. That is the strongest signal available: the engine,
        choosing freely, left it on the bench against every opponent.
        """
        pm = pm_for({("E", "F"): {K1: 90.0, K2: 90.0},
                     ("A", "B"): {K1: 10.0, K2: 10.0}})
        record = {"per_opponent": {
            "Rain": {"our_bring4": ["A", "B", "C", "D"]},
            "Sand": {"our_bring4": ["A", "B", "C", "E"]}}}
        ranking = substitution.drop_ranking(TEAM, KEYS, pm, record=record)
        self.assertEqual(ranking[0]["name"], "F")
        self.assertEqual(ranking[0]["brought_by"], 0)
        self.assertEqual(ranking[0]["rated_opponents"], 2)

    def test_without_a_rating_it_falls_back_to_marginal_coverage(self):
        """A team that has never been rated must still be improvable."""
        pm = pm_for({("A", "B"): {K1: 30.0, K2: 30.0},
                     ("C", "D"): {K1: 20.0, K2: 20.0}})
        ranking = substitution.drop_ranking(TEAM, KEYS, pm)
        self.assertIsNone(ranking[0]["brought_by"])
        # E and F are in no pair that answers anything, so they are the
        # redundant ones. A and B carry both threats and must be dropped last.
        self.assertIn(ranking[0]["name"], {"E", "F"})
        self.assertEqual({d["name"] for d in ranking[-2:]}, {"A", "B"})

    def test_members_tied_on_marginal_value_break_on_what_they_do_answer(self):
        """Every member the best pair does not need scores zero marginal value,
        so without a further tie-break the drop choice fell to list order.
        C at least beats one threat in its own pair; E beats nothing."""
        pm = pm_for({("A", "B"): {K1: 30.0, K2: 30.0},
                     ("C", "D"): {K1: 20.0, K2: 20.0}})
        ranking = substitution.drop_ranking(TEAM, KEYS, pm)
        by_name = {d["name"]: d for d in ranking}
        self.assertEqual(by_name["C"]["marginal"], by_name["E"]["marginal"])
        self.assertGreater(by_name["C"]["support"], by_name["E"]["support"])
        self.assertLess([d["name"] for d in ranking].index("E"),
                        [d["name"] for d in ranking].index("C"))

    def test_a_keystone_is_not_dropped_ahead_of_a_redundant_member(self):
        """C is the only answer to Sand. E is in no useful pair at all."""
        pm = pm_for({("A", "B"): {K1: 30.0, K2: -50.0},
                     ("C", "D"): {K1: 25.0, K2: 15.0}})
        record = {"per_opponent": {
            "Rain": {"our_bring4": ["A", "B", "C", "E"]},
            "Sand": {"our_bring4": ["A", "B", "C", "E"]}}}
        ranking = substitution.drop_ranking(TEAM, KEYS, pm, record=record)
        by_name = {d["name"]: d for d in ranking}
        self.assertEqual(by_name["C"]["keystone_for"], 1)
        self.assertEqual(by_name["E"]["keystone_for"], 0)
        self.assertLess([d["name"] for d in ranking].index("E"),
                        [d["name"] for d in ranking].index("C"))

    def test_never_brought_and_no_information_are_different_answers(self):
        """The bug this helper exists to stop: a member that was never brought
        is ABSENT from the counts, not present with a zero, so reading them with
        a plain .get() reports "no information" for the most damning evidence
        there is -- and the caller then skips the warning it should have shown.
        """
        record = {"per_opponent": {"Rain": {"our_bring4": ["A", "B", "C", "D"]}}}
        self.assertEqual(substitution.brought_count(record, "A"), 1)
        self.assertEqual(substitution.brought_count(record, "F"), 0)
        self.assertIsNone(substitution.brought_count({}, "A"))
        self.assertIsNone(substitution.brought_count(
            {"per_opponent": {"Rain": {"wins": 90}}}, "A"))

    def test_appearances_are_absent_not_zero_for_an_older_record(self):
        """A record written before our_bring4 existed has NO opinion.

        Reading it as "every member was never brought" would let a stale cache
        entry drive the drop choice with a signal it does not carry.
        """
        record = {"per_opponent": {"Rain": {"wins": 90, "total": 90}}}
        self.assertEqual(substitution.bring_appearances(record), {})
        pm = pm_for({("A", "B"): {K1: 30.0}})
        ranking = substitution.drop_ranking(TEAM, KEYS, pm, record=record)
        self.assertTrue(all(d["brought_by"] is None for d in ranking))


class TestLegality(unittest.TestCase):

    def test_a_mega_and_its_base_form_are_the_same_species(self):
        self.assertFalse(substitution.legal_swap(TEAM, "B", "Mega A"))
        self.assertTrue(substitution.legal_swap(TEAM, "A", "Mega A"))

    def test_the_two_mega_limit_holds(self):
        team = ["Mega A", "Mega B", "C", "D", "E", "F"]
        self.assertFalse(substitution.legal_swap(team, "C", "Mega G"))
        self.assertTrue(substitution.legal_swap(team, "Mega B", "Mega G"))

    def test_a_member_already_on_the_team_is_not_a_replacement(self):
        self.assertFalse(substitution.legal_swap(TEAM, "A", "B"))
        self.assertFalse(substitution.legal_swap(TEAM, "A", "A"))


class TestAiming(unittest.TestCase):

    def test_opponent_weights_aim_at_the_matchups_we_are_failing(self):
        record = {"per_opponent": {
            "Rain": {"adjusted_win_rate": 0.9},
            "Sand": {"adjusted_win_rate": 0.1}}}
        weights = substitution.opponent_weights(record, ["Rain", "Sand"])
        self.assertGreater(weights["Sand"], weights["Rain"])
        self.assertGreaterEqual(weights["Rain"],
                                substitution.MIN_OPPONENT_WEIGHT)

    def test_a_matchup_we_already_win_still_counts_for_something(self):
        """Otherwise a swap that wrecks a 90/90 matchup looks free."""
        record = {"per_opponent": {"Rain": {"adjusted_win_rate": 1.0}}}
        weights = substitution.opponent_weights(record, ["Rain"])
        self.assertEqual(weights["Rain"], substitution.MIN_OPPONENT_WEIGHT)

    def test_an_unrated_opponent_gets_full_weight(self):
        weights = substitution.opponent_weights({}, ["Rain"])
        self.assertEqual(weights["Rain"], 1.0)

    def test_replacements_are_ranked_by_the_weighted_gain(self):
        """G answers the threat we are failing; H answers the one we win."""
        pm = pm_for({("A", "B"): {K1: 10.0, K2: 10.0},
                     ("B", "G"): {K2: 80.0},
                     ("B", "H"): {K1: 80.0}})
        weights = substitution.key_weights(
            KEYS, {"Rain": substitution.MIN_OPPONENT_WEIGHT, "Sand": 1.0})
        ranked = substitution.replacement_ranking(TEAM, "F", ["G", "H"], KEYS,
                                                  pm, weights=weights)
        self.assertEqual(ranked[0]["candidate"], "G")
        self.assertGreater(ranked[0]["gain"], 0)
        self.assertNotIn("F", ranked[0]["team"])
        self.assertIn("G", ranked[0]["team"])


class TestRecordGuard(unittest.TestCase):
    """The first real run of the loop accepted a swap that raised adjusted wins
    0.426 -> 0.467 while the record fell 75/90 -> 56/90. The two numbers come
    from different pilots and can move in opposite directions; the record is the
    one the Plan sheet is read on, so it is checked first."""

    def test_a_swap_that_gives_up_record_is_measured_as_such(self):
        before = {"wins": 75, "total": 90, "adjusted_win_rate": 0.426}
        after = {"wins": 56, "total": 90, "adjusted_win_rate": 0.467}
        self.assertAlmostEqual(substitution.lost_record(before, after),
                               19 / 90)

    def test_gaining_record_is_a_negative_loss(self):
        before = {"wins": 56, "total": 90}
        after = {"wins": 75, "total": 90}
        self.assertLess(substitution.lost_record(before, after), 0)

    def test_an_unplayed_candidate_is_unknown_not_zero(self):
        """A screened-out candidate has no record. Reading it as 0 wins would
        reject it for the wrong reason, and reading it as 'no loss' would let it
        through -- so it is None and the caller decides."""
        self.assertIsNone(substitution.record_rate({"wins": 0, "total": 0}))
        self.assertIsNone(substitution.lost_record({"wins": 75, "total": 90},
                                                   {"wins": 0, "total": 0}))


class TestPropose(unittest.TestCase):

    def setUp(self):
        self.pm = pm_for({("A", "B"): {K1: 30.0, K2: 30.0},
                          ("B", "G"): {K1: 60.0}, ("B", "H"): {K2: 60.0}})

    def test_the_same_six_is_never_proposed_twice(self):
        """Two different (drop, candidate) pairs can build the same roster."""
        proposals = substitution.propose(TEAM, ["G", "H"], KEYS, self.pm,
                                         drops=6, swaps=4, limit=99)
        fingerprints = [frozenset(p["team"]) for p in proposals]
        self.assertEqual(len(fingerprints), len(set(fingerprints)))
        self.assertNotIn(frozenset(TEAM), fingerprints)

    def test_every_proposal_is_a_legal_six(self):
        proposals = substitution.propose(TEAM, ["G", "H", "Mega A"], KEYS,
                                         self.pm, drops=6, swaps=4, limit=99)
        for p in proposals:
            self.assertEqual(len(p["team"]), 6, p)
            self.assertEqual(len(set(p["team"])), 6, p)
            self.assertTrue(substitution.legal_swap(TEAM, p["drop"],
                                                    p["candidate"]), p)

    def test_the_budget_is_respected_and_the_best_guess_comes_first(self):
        proposals = substitution.propose(TEAM, ["G", "H"], KEYS, self.pm,
                                         drops=2, swaps=4, limit=2)
        self.assertEqual(len(proposals), 2)
        self.assertGreaterEqual(proposals[0]["gain"], proposals[1]["gain"])
        self.assertIn("drop_evidence", proposals[0])


if __name__ == "__main__":
    unittest.main()
