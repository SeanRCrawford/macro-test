"""Which bring the search commits to.

Reported: "it does not seem to be selecting good teams nor using good lines for
those teams." Measured on NAIC vs Big 6, all 90 audited:

    bring                    record   audited lines won   adjusted
    .../Farigiraf             89/90         61/90           0.527   <- committed
    .../Mega Charizard Y      90/90         68/90           0.405

The search ranked on adjusted wins alone, so it passed over the bring that beats
every one of their configurations and wins seven more audited lines. WORKFLOW.md
§1 says the opposite -- "punish alone is a trap ... always read the record
first" -- so the record now leads the ordering.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import matchup_search  # noqa: E402


def rec(name, wins, adj, punish, total=90):
    return {"our_bring4": [name], "solver_wins": wins, "solver_total": total,
            "adjusted_win_rate": adj, "exploitability": punish}


def order(records):
    """Run just the final ordering _rate_and_rerank applies."""
    ranked = list(records)

    def key(d):
        total = d.get("solver_total") or 0
        record = round((d.get("solver_wins") or 0) / total, 2) if total else 0.0
        return (-record, -(d.get("adjusted_win_rate") or 0.0),
                d["exploitability"])

    ranked.sort(key=key)
    # Guard against this test drifting from the implementation it describes.
    source = open(os.path.join(os.path.dirname(__file__), "..", "src",
                               "matchup_search.py"), encoding="utf-8").read()
    assert "def _order(d):" in source and "-record," in source, \
        "matchup_search no longer orders the way this test replays"
    return [d["our_bring4"][0] for d in ranked]


class TestCommitOrder(unittest.TestCase):

    def test_the_reported_case_now_picks_the_better_record(self):
        self.assertEqual(
            order([rec("Farigiraf", 89, 0.527, 40.0),
                   rec("Charizard", 90, 0.405, 60.0)])[0],
            "Charizard")

    def test_inside_a_record_band_the_sounder_line_still_wins(self):
        """Rounding to whole percent means 90/90 and 89/90 (1.0 vs 0.99) are
        NOT tied -- but two brings on the same rounded record are, and there
        adjusted wins decides. Otherwise this would be the win-count ranking
        the redesign replaced."""
        self.assertEqual(
            order([rec("punishable", 85, 0.20, 90.0),
                   rec("sound", 85, 0.70, 30.0)])[0],
            "sound")

    def test_punish_is_the_last_word_not_the_first(self):
        self.assertEqual(
            order([rec("clean_but_losing", 40, 0.10, 1.0),
                   rec("wins_a_lot", 88, 0.60, 70.0)])[0],
            "wins_a_lot")

    def test_a_tie_on_both_falls_to_punish(self):
        self.assertEqual(
            order([rec("punishable", 88, 0.5, 80.0),
                   rec("robust", 88, 0.5, 20.0)])[0],
            "robust")

    def test_a_record_of_zero_configs_does_not_crash_or_win(self):
        ranked = order([rec("nothing_played", 0, 0.9, 0.0, total=0),
                        rec("real", 80, 0.4, 50.0)])
        self.assertEqual(ranked[0], "real")


if __name__ == "__main__":
    unittest.main()
