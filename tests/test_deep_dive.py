"""One position, audited at full strength.

The exhaustive tier is expensive because of BREADTH -- 8 brings against all 90
of their bring-4s. Once you have led, the breadth is gone and only depth is
left, so the same analysis fits in seconds. These tests pin the two things that
would silently make it a different analysis: that lead ORDER is respected (the
whole question is "having already led X"), and that the opponent's wider move
space is attached (a line that only survives the moves we planned for is not
safe).

Runs against the real dataset, so it is slower than the pure-logic tests, but
a deep dive that quietly analysed the wrong position would be worse than none.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import blas_limits  # noqa: E402,F401  (before pandas; see its docstring)
from deep_dive import audit_position, build_position, summarise  # noqa: E402
from species_data import build_merged_dataset  # noqa: E402

OURS = ["Mega Charizard Y", "Kingambit", "Mega Aerodactyl", "Garchomp"]
THEIRS = ["Archaludon", "Mega Swampert", "Pelipper", "Mega Metagross"]


class TestDeepDive(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        (cls.merged, _usage, cls.moves,
         cls.natures, cls.typechart) = build_merged_dataset()

    def _build(self, our4=None, enemy4=None):
        return build_position(our4 or OURS, enemy4 or THEIRS, self.merged,
                              self.moves, self.natures, self.typechart)

    def test_lead_order_is_respected(self):
        """'Having already led X' is the entire question being asked."""
        battle, _ms = self._build()
        self.assertEqual([c.name for c in battle.p1.active], OURS[:2])
        self.assertEqual([c.name for c in battle.p2.active], THEIRS[:2])

    def test_reordering_our_bring_changes_who_leads(self):
        swapped = [OURS[2], OURS[3], OURS[0], OURS[1]]
        battle, _ms = self._build(our4=swapped)
        self.assertEqual([c.name for c in battle.p1.active], swapped[:2])

    def test_the_opponents_wider_move_space_is_attached(self):
        """A line that only survives the four moves we assumed is not safe."""
        battle, movesets = self._build()
        wide = getattr(battle, "wide_movesets", None)
        self.assertIsNotNone(wide)
        wider = [n for n in THEIRS
                 if len(wide.get(n, [])) > len(movesets.get(n, []))]
        self.assertTrue(wider, "no opponent had a widened move space")

    def test_audit_returns_a_line_with_per_turn_detail(self):
        report = audit_position(OURS, THEIRS, self.merged, self.moves,
                                self.natures, self.typechart, max_turns=4)
        self.assertTrue(report.turns, "no turns audited")
        first = report.turns[0]
        for attr in ("exploitability", "expected_loss", "equilibrium",
                     "punisher", "equilibrium_reply", "events", "kos"):
            self.assertTrue(hasattr(first, attr), attr)
        self.assertIn(report.outcome,
                      {"win", "loss", "draw", "unresolved"})

    def test_the_engine_log_is_captured_per_turn(self):
        """The damage numbers are the point -- a plan you cannot see is faith."""
        report = audit_position(OURS, THEIRS, self.merged, self.moves,
                                self.natures, self.typechart, max_turns=3)
        self.assertTrue(any(t.events for t in report.turns),
                        "no turn captured an engine log")

    def test_the_turn_cap_is_honoured(self):
        report = audit_position(OURS, THEIRS, self.merged, self.moves,
                                self.natures, self.typechart, max_turns=2)
        self.assertLessEqual(len(report.turns), 2)

    def test_it_is_deterministic(self):
        """Same position, same seed, same line -- or the UI is untrustworthy."""
        a = audit_position(OURS, THEIRS, self.merged, self.moves,
                           self.natures, self.typechart, max_turns=3)
        b = audit_position(OURS, THEIRS, self.merged, self.moves,
                           self.natures, self.typechart, max_turns=3)
        self.assertEqual([t.exploitability for t in a.turns],
                         [t.exploitability for t in b.turns])

    def test_summarise_returns_plain_types_for_a_ui(self):
        report = audit_position(OURS, THEIRS, self.merged, self.moves,
                                self.natures, self.typechart, max_turns=3)
        info = summarise(report)
        self.assertEqual(
            set(info),
            {"outcome", "turns", "mean_exploitability", "mean_expected_loss",
             "worst_exploitability", "worst_turn", "severe_turns",
             "final_margin"})
        for key, value in info.items():
            self.assertIsInstance(value, (str, int, float, type(None)), key)


if __name__ == "__main__":
    unittest.main()
