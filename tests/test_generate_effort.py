"""Generation must be able to rank by exploitability, not only by win count.

The beam search and the verification block both measure a generated team
against opponents this codebase wrote, which rewards beating our own bot. The
--effort flag is what turns on the unbiased question. These tests pin the
dispatch (does the flag actually reach the search?) rather than the numbers,
so they stay fast and do not depend on the dataset.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import matchup_search  # noqa: E402
import generate_team  # noqa: E402
from search_effort import tier  # noqa: E402


class _Recorder:
    """Stands in for search_robust_composition and records how it was called."""

    def __init__(self, exploitability=None):
        self.calls = []
        self.exploitability = exploitability

    def __call__(self, our_pool6, enemy_roster, *args, **kwargs):
        self.calls.append(kwargs)
        rec = {
            "our_bring4": list(our_pool6)[:4],
            "solver_wins": 88, "solver_total": 90, "solver_losses": [],
        }
        if self.exploitability is not None:
            rec["exploitability"] = self.exploitability
            rec["severe_turns"] = 2
            rec["hardest_lead"] = list(enemy_roster)[:2]
            rec["worst_turn"] = {"turn": 3, "exploitability": self.exploitability,
                                 "our_play": "A move", "punished_by": "B answer"}
        return [rec]


class TestGenerateEffort(unittest.TestCase):

    def setUp(self):
        self.original = matchup_search.search_robust_composition
        self.teams = {"Rain": ["E1", "E2", "E3", "E4", "E5", "E6"]}
        self.team = ["A", "B", "C", "D", "E", "F"]

    def tearDown(self):
        matchup_search.search_robust_composition = self.original

    def _verify(self, effort, exploitability=None):
        rec = _Recorder(exploitability)
        matchup_search.search_robust_composition = rec
        out = generate_team.verify_with_solver(
            self.team, self.teams, {}, {}, {}, {}, {}, [],
            max_turns=8, all_backs=True, effort=effort)
        return rec, out

    def test_quick_effort_does_not_request_a_rating(self):
        """The default must stay exactly as cheap as it was."""
        rec, out = self._verify("quick")
        self.assertFalse(rec.calls[0]["rate_robustness"])
        self.assertEqual(rec.calls[0]["verify_top"], 1)
        self.assertIsNone(out["Rain"]["exploitability"])

    def test_no_effort_at_all_behaves_like_before(self):
        rec, _out = self._verify(None)
        self.assertFalse(rec.calls[0]["rate_robustness"])
        self.assertEqual(rec.calls[0]["verify_top"], 1)

    def test_standard_effort_requests_a_rating_with_the_tier_settings(self):
        rec, _out = self._verify("standard")
        settings = tier("standard")
        self.assertTrue(rec.calls[0]["rate_robustness"])
        self.assertEqual(rec.calls[0]["verify_top"], settings["verify_top"])
        self.assertEqual(rec.calls[0]["robustness_leads"], settings["leads"])
        self.assertEqual(rec.calls[0]["robustness_turns"], settings["turns"])

    def test_thorough_audits_more_widely_than_standard(self):
        std, _ = self._verify("standard")
        thr, _ = self._verify("thorough")
        self.assertGreater(thr.calls[0]["verify_top"], std.calls[0]["verify_top"])
        self.assertGreater(thr.calls[0]["robustness_leads"], std.calls[0]["robustness_leads"])
        self.assertGreater(thr.calls[0]["robustness_turns"], std.calls[0]["robustness_turns"])

    def test_rating_reaches_the_result_record(self):
        _rec, out = self._verify("standard", exploitability=42.5)
        self.assertAlmostEqual(out["Rain"]["exploitability"], 42.5)
        self.assertEqual(out["Rain"]["severe_turns"], 2)
        self.assertEqual(out["Rain"]["worst_turn"]["turn"], 3)
        # The win count is still reported alongside, not replaced.
        self.assertEqual(out["Rain"]["wins"], 88)

    def test_an_unknown_effort_name_falls_back_rather_than_crashing(self):
        """A typo must not silently skip verification or raise mid-run."""
        rec, _out = self._verify("nonsense")
        self.assertEqual(rec.calls[0]["rate_robustness"],
                         tier("nonsense")["robustness"])

    def test_robustness_arguments_are_never_zero(self):
        """search_robust_composition treats 0 leads/turns as nothing to audit."""
        for name in ("standard", "thorough", "exhaustive"):
            rec, _ = self._verify(name)
            self.assertGreaterEqual(rec.calls[0]["robustness_leads"], 1, name)
            self.assertGreaterEqual(rec.calls[0]["robustness_turns"], 1, name)


if __name__ == "__main__":
    unittest.main()
