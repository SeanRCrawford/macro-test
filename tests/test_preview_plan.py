"""Team preview: one committed bring, chosen the same way the workbook does.

The Plan sheet reads cached JSON; this reads live search results. The logic
cannot simply be shared, so it is duplicated deliberately and pinned here --
if the two ever disagree about which bring to commit to, the app and the
workbook would recommend different plans from the same analysis.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from export_search import committed_plan  # noqa: E402
from preview_plan import committed  # noqa: E402


def _line(outcome, punish, enemy=("E1", "E2", "E3", "E4")):
    return {"outcome": outcome, "mean_exploitability": punish,
            "enemy_bring": list(enemy), "adjusted_value": 0.5 if outcome == "win" else 0.0}


def _rec(bring, lines):
    return {"our_bring4": list(bring), "audit": list(lines),
            "severe_turns": 1, "solver_wins": 80, "solver_total": 90}


class TestCommitted(unittest.TestCase):

    def test_picks_the_bring_with_the_most_wins(self):
        results = [_rec(["A"], [_line("win", 10), _line("loss", 10)]),
                   _rec(["B"], [_line("win", 90), _line("win", 90)])]
        self.assertEqual(committed(results)["bring"], ["B"])

    def test_ties_on_wins_break_to_the_lower_punish(self):
        results = [_rec(["A"], [_line("win", 90)]),
                   _rec(["B"], [_line("win", 10)])]
        self.assertEqual(committed(results)["bring"], ["B"])

    def test_reports_the_record_against_their_configurations(self):
        plan = committed([_rec(["A"], [_line("win", 10), _line("loss", 20),
                                       _line("win", 10)])])
        self.assertEqual((plan["wins"], plan["of"]), (2, 3))
        self.assertTrue(plan["audited"])

    def test_names_the_brings_that_beat_the_plan(self):
        plan = committed([_rec(["A"], [
            _line("win", 10),
            _line("loss", 20, enemy=("X1", "X2", "X3", "X4"))])])
        self.assertEqual(plan["losing_to"], ["X1 / X2 / X3 / X4"])

    def test_an_unaudited_result_is_flagged_not_silently_scored(self):
        """Quick strength does no punish analysis; saying 0 would be a lie."""
        plan = committed([_rec(["A"], [])])
        self.assertFalse(plan["audited"])
        self.assertIsNone(plan["wins"])
        self.assertEqual(plan["solver_total"], 90)

    def test_no_results_at_all_returns_none(self):
        self.assertIsNone(committed([]))
        self.assertIsNone(committed(None))

    def test_it_agrees_with_the_workbook_on_the_same_data(self):
        """The app and the .xlsx must never recommend different plans."""
        results = [_rec(["A", "B", "C", "D"],
                        [_line("win", 10), _line("loss", 50)]),
                   _rec(["A", "B", "C", "E"],
                        [_line("win", 40), _line("win", 40)])]
        live = committed(results)
        # committed_plan reads the cache row shape: bring under "bring".
        row = {"ours": "T", "theirs": "O",
               "candidates": [{"bring": r["our_bring4"], "audit": r["audit"]}
                              for r in results]}
        cached = committed_plan(row)
        self.assertIsNotNone(cached)
        cand, wins, total, _adjusted = cached
        self.assertEqual(live["bring"], cand["bring"])
        self.assertEqual((live["wins"], live["of"]), (wins, total))


if __name__ == "__main__":
    unittest.main()
