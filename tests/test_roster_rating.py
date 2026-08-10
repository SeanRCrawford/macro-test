"""The shared rating path: cache keys, the record, and the ranking.

`roster_rating` is what makes stage 1 and the substitution loop comparable --
same audit, same record, same cache. These tests pin the parts that would break
that quietly: a key that stops matching an older run's cache, a field the
substitution loop reads going missing, or a ranking that disagrees between the
two callers.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import roster_rating  # noqa: E402
from search_effort import ResultCache  # noqa: E402

TEAM = ["A", "B", "C", "D", "E", "F"]


def verdict(**per_opponent):
    return per_opponent


class TestCacheKey(unittest.TestCase):

    def test_the_full_library_key_carries_no_extra_parts(self):
        """The key is the roster, the tier, the cap and the sets -- nothing
        else. Anything extra silently throws away every cached overnight run,
        so a change here should be a deliberate RATING_SCHEMA bump rather than
        a side effect of adding an argument."""
        self.assertEqual(
            roster_rating.rating_key(TEAM, "standard", 10, None),
            ResultCache.key("team", roster_rating.RATING_SCHEMA, sorted(TEAM),
                            "standard", 10, None))

    def test_member_order_does_not_change_the_key(self):
        self.assertEqual(roster_rating.rating_key(TEAM, "standard", 10, None),
                         roster_rating.rating_key(list(reversed(TEAM)),
                                                  "standard", 10, None))

    def test_everything_that_changes_the_answer_changes_the_key(self):
        base = roster_rating.rating_key(TEAM, "standard", 10, None)
        sets = {"A": {"item": "Life Orb", "moves": ["Dark Pulse"]}}
        self.assertNotEqual(base, roster_rating.rating_key(TEAM, "thorough", 10, None))
        self.assertNotEqual(base, roster_rating.rating_key(TEAM, "standard", 12, None))
        self.assertNotEqual(base, roster_rating.rating_key(TEAM, "standard", 10, sets))

    def test_a_restricted_rating_cannot_be_served_as_a_full_one(self):
        """--vs "Rain" rates against one opponent. Reusing that for a
        full-library rating would compare a team rated against one opponent
        with a team rated against eight."""
        self.assertNotEqual(
            roster_rating.rating_key(TEAM, "standard", 10, None),
            roster_rating.rating_key(TEAM, "standard", 10, None,
                                     opponents=["Rain"]))
        self.assertEqual(
            roster_rating.rating_key(TEAM, "standard", 10, None,
                                     opponents=["Rain", "Sand"]),
            roster_rating.rating_key(TEAM, "standard", 10, None,
                                     opponents=["Sand", "Rain"]))


class TestSummarise(unittest.TestCase):

    def setUp(self):
        self.verdict = verdict(
            Rain={"wins": 80, "total": 90, "exploitability": 40.0,
                  "adjusted_win_rate": 0.5, "robust_win_rate": 0.6,
                  "severe_turns": 2, "our_bring4": ["A", "B", "C", "D"]},
            Sand={"wins": 60, "total": 90, "exploitability": 80.0,
                  "adjusted_win_rate": 0.3, "robust_win_rate": 0.4,
                  "severe_turns": 5, "our_bring4": ["A", "B", "C", "E"]},
            Dead=None)

    def test_the_committed_bring_reaches_the_record(self):
        """Without it the substitution loop has no real-engine evidence for
        which member is not earning its slot."""
        rec = roster_rating.summarise(TEAM, self.verdict)
        self.assertEqual(rec["per_opponent"]["Rain"]["our_bring4"],
                         ["A", "B", "C", "D"])

    def test_totals_and_the_worst_opponent(self):
        rec = roster_rating.summarise(TEAM, self.verdict)
        self.assertEqual((rec["wins"], rec["total"]), (140, 180))
        self.assertAlmostEqual(rec["exploitability"], 60.0)
        self.assertAlmostEqual(rec["adjusted_win_rate"], 0.4)
        self.assertEqual(rec["severe_turns"], 7)
        self.assertEqual(rec["worst_opponent"], "Sand")

    def test_an_opponent_with_no_result_is_skipped_not_counted_as_zero(self):
        rec = roster_rating.summarise(TEAM, self.verdict)
        self.assertNotIn("Dead", rec["per_opponent"])


class TestRankKey(unittest.TestCase):

    def test_adjusted_wins_decide_and_punish_breaks_the_tie(self):
        better = {"adjusted_win_rate": 0.5, "exploitability": 90.0}
        worse = {"adjusted_win_rate": 0.4, "exploitability": 10.0}
        self.assertLess(roster_rating.rank_key(better),
                        roster_rating.rank_key(worse))
        tie_a = {"adjusted_win_rate": 0.5, "exploitability": 30.0}
        tie_b = {"adjusted_win_rate": 0.5, "exploitability": 60.0}
        self.assertLess(roster_rating.rank_key(tie_a),
                        roster_rating.rank_key(tie_b))

    def test_an_unrated_team_sorts_last_rather_than_first(self):
        """A screened-out team has no exploitability. Treating that as 0 would
        rank the teams that never played above the ones that did -- the exact
        failure the ranking comment in generate_overnight warns about."""
        rated = {"adjusted_win_rate": 0.0, "exploitability": 200.0}
        skipped = {"adjusted_win_rate": None, "exploitability": None,
                   "skipped": "below --min-winrate"}
        self.assertLess(roster_rating.rank_key(rated),
                        roster_rating.rank_key(skipped))


if __name__ == "__main__":
    unittest.main()
