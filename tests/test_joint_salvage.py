"""One change that fixes ALL the losses, not one change per loss.

The Lead/Back search hands back a set of enemy brings a team loses to. Salvaging
them one at a time gives one team per loss, and the fix for one matchup
routinely breaks another. `salvage_all_losses` scores each candidate change
against every losing bring.

The engine is stubbed here: these pin the SEARCH (what gets tried, how it is
scored and ranked), not the battle simulation, so the suite stays fast.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import salvage  # noqa: E402

OUR4 = ["A", "B", "C", "D"]
LOSS1 = ["X1", "X2", "X3", "X4"]
LOSS2 = ["Y1", "Y2", "Y3", "Y4"]
LOSS3 = ["Z1", "Z2", "Z3", "Z4"]


class Harness(unittest.TestCase):
    """Replaces the trial builder and the engine with scripted stand-ins."""

    def setUp(self):
        self.trials = [("A", "item", "Chople Berry", {"A": {"item": "Chople Berry"}}),
                       ("B", "evs", "bulk spread", {"B": {"evs": {"hp": 66}}}),
                       ("C", "support", "Tailwind in for Ice Punch",
                        {"C": {"moves": ["Tailwind"]}})]
        # {(change label, enemy first name): (winner, turns)}; anything unlisted
        # loses, which is the baseline for every matchup below.
        self.results = {}
        self._build = salvage._build_trials
        self._play = salvage.play_out_worst_case
        salvage._build_trials = lambda *a, **k: list(self.trials)

        def fake_play(our, enemy, *a, **k):
            sets = k.get("our_sets") or {}
            label = next((t[2] for t in self.trials
                          if all(sets.get(m) == s for m, s in t[3].items())), None)
            return self.results.get((label, enemy[0]), ("p2", 5)) + (None,)

        salvage.play_out_worst_case = fake_play

    def tearDown(self):
        salvage._build_trials = self._build
        salvage.play_out_worst_case = self._play

    def run_salvage(self, brings, **kwargs):
        return salvage.salvage_all_losses(OUR4, brings, {}, {}, {}, {}, 8,
                                          our_sets={}, **kwargs)


class TestRanking(Harness):

    def test_the_change_that_fixes_the_most_comes_first(self):
        self.results[("Chople Berry", "X1")] = ("p1", 4)
        self.results[("bulk spread", "X1")] = ("p1", 4)
        self.results[("bulk spread", "Y1")] = ("p1", 4)
        self.results[("bulk spread", "Z1")] = ("p1", 4)
        res = self.run_salvage([LOSS1, LOSS2, LOSS3])
        self.assertEqual(res["fixes"][0]["change"], "bulk spread")
        self.assertEqual(res["fixes"][0]["fixed"], 3)
        self.assertEqual(res["fixes"][0]["of"], 3)
        self.assertEqual(res["fixes"][1]["change"], "Chople Berry")
        self.assertEqual(res["fixes"][1]["fixed"], 1)

    def test_a_change_that_fixes_nothing_is_not_reported(self):
        res = self.run_salvage([LOSS1, LOSS2])
        self.assertEqual(res["fixes"], [])
        self.assertEqual(res["trials"], 3)

    def test_breaking_a_matchup_is_counted_and_breaks_the_tie(self):
        """A change that fixes two and breaks one is not the same as a change
        that fixes two cleanly, and must not outrank it."""
        # Both fix LOSS1 and LOSS2; the berry also turns a WON matchup into a loss.
        won = ["W1", "W2", "W3", "W4"]
        for label in ("Chople Berry", "bulk spread"):
            self.results[(label, "X1")] = ("p1", 4)
            self.results[(label, "Y1")] = ("p1", 4)
        self.results[(None, "W1")] = ("p1", 3)            # baseline: already won
        self.results[("bulk spread", "W1")] = ("p1", 3)   # ...still won, unchanged
        self.results[("Chople Berry", "W1")] = ("p2", 6)  # ...but the berry loses it
        res = self.run_salvage([LOSS1, LOSS2, won])
        self.assertEqual(res["fixes"][0]["change"], "bulk spread")
        self.assertEqual(res["fixes"][0]["regressed"], 0)
        berry = next(f for f in res["fixes"] if f["change"] == "Chople Berry")
        self.assertEqual(berry["regressed"], 1)

    def test_per_matchup_detail_says_which_ones_moved(self):
        self.results[("bulk spread", "X1")] = ("p1", 4)
        res = self.run_salvage([LOSS1, LOSS2])
        fix = res["fixes"][0]
        self.assertEqual([m["improved"] for m in fix["per_matchup"]], [True, False])
        self.assertEqual(fix["per_matchup"][0]["enemy"], LOSS1)


class TestRequireAll(Harness):

    def test_partial_fixes_are_dropped(self):
        self.results[("Chople Berry", "X1")] = ("p1", 4)
        res = self.run_salvage([LOSS1, LOSS2], require_all=True)
        self.assertEqual(res["fixes"], [])

    def test_a_complete_fix_still_comes_through(self):
        for enemy in ("X1", "Y1"):
            self.results[("bulk spread", enemy)] = ("p1", 4)
        res = self.run_salvage([LOSS1, LOSS2], require_all=True)
        self.assertEqual(len(res["fixes"]), 1)
        self.assertEqual(res["fixes"][0]["fixed"], 2)

    def test_it_stops_paying_for_a_candidate_that_has_already_failed(self):
        """The whole point of require_all: abandon on the first miss."""
        played = []
        inner = salvage.play_out_worst_case

        def counting(our, enemy, *a, **k):
            played.append(enemy[0])
            return inner(our, enemy, *a, **k)

        salvage.play_out_worst_case = counting
        try:
            self.run_salvage([LOSS1, LOSS2, LOSS3], require_all=True)
        finally:
            salvage.play_out_worst_case = inner
        # 3 baselines, then each of 3 candidates gives up after its first miss.
        self.assertEqual(len(played), 3 + 3)


class TestEdges(Harness):

    def test_no_losses_is_not_an_error(self):
        res = self.run_salvage([])
        self.assertEqual(res, {"baseline": [], "fixes": [], "trials": 0})

    def test_the_same_change_is_never_tried_twice(self):
        """Trials are built per matchup and pooled -- without deduplication the
        identical swap would be replayed once per losing bring."""
        res = self.run_salvage([LOSS1, LOSS2, LOSS3])
        self.assertEqual(res["trials"], len(self.trials))


if __name__ == "__main__":
    unittest.main()
