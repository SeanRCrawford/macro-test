"""The ranking numbers: wins against a strong opponent, discounted by punish.

Two things are pinned here, and both were live bugs before they were:

  1. A line must be advanced against the opponent's EQUILIBRIUM reply, not
     against the best response to the move we just made. The latter is
     clairvoyant -- it sees our action before choosing -- and iterating it beat
     every team tested (0 wins in 24 audited lines that the same teams won
     180/180 against the standard model).
  2. The punish discount must use the MEAN exploitability of a line, not its
     worst turn. Worst turns routinely exceed a Pokemon's value, so a
     worst-turn discount floored every score at zero and the column carried no
     information at all.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from robustness import SEVERE, LineReport, TurnRobustness  # noqa: E402
from solver import KO_WEIGHT  # noqa: E402
from team_rating import BringRating  # noqa: E402


def _turn(exploitability, expected_loss=None):
    """expected_loss defaults to exploitability so the simple cases read plainly."""
    return TurnRobustness(
        turn=1, exploitability=exploitability, regret=0.0, equilibrium=0.0,
        worst_case=-exploitability,
        expected_loss=exploitability if expected_loss is None else expected_loss)


def _line(outcome, exploitabilities):
    rep = LineReport(turns=[_turn(e) for e in exploitabilities])
    rep.outcome = outcome
    rep.length = len(exploitabilities)
    return rep


def _rating(*lines):
    """lines: (outcome, probability, [exploitabilities])"""
    r = BringRating(our_bring=["A", "B", "C", "D"])
    for outcome, prob, exps in lines:
        r.per_lead.append((("X", "Y"), prob, _line(outcome, exps)))
    return r


class TestAdjustedWins(unittest.TestCase):

    def test_an_unpunishable_win_scores_full_marks(self):
        self.assertAlmostEqual(_rating(("win", 1.0, [0.0, 0.0])).adjusted_win_rate, 1.0)

    def test_a_win_giving_away_a_pokemon_per_turn_scores_nothing(self):
        """The discount is anchored to the engine's own price of a Pokemon."""
        self.assertAlmostEqual(
            _rating(("win", 1.0, [KO_WEIGHT, KO_WEIGHT])).adjusted_win_rate, 0.0)

    def test_the_discount_uses_the_mean_not_the_worst_turn(self):
        """One spike must not zero a line that is otherwise clean.

        Worst-turn discounting was tried first and floored every real team at
        zero, because worst turns routinely exceed KO_WEIGHT.
        """
        # Worst turn is 2x a Pokemon, which a worst-turn discount floors at 0.
        # The mean is only half a Pokemon, so the line keeps half its weight.
        spiky = _rating(("win", 1.0, [0.0, 0.0, 0.0, 2 * KO_WEIGHT]))
        self.assertAlmostEqual(spiky.adjusted_win_rate, 0.5)

    def test_losses_and_unresolved_lines_contribute_nothing(self):
        for outcome in ("loss", "draw", "unresolved"):
            with self.subTest(outcome=outcome):
                r = _rating((outcome, 1.0, [0.0]))
                self.assertAlmostEqual(r.adjusted_win_rate, 0.0)
                self.assertAlmostEqual(r.robust_win_rate, 0.0)

    def test_a_line_that_cannot_close_is_not_a_win(self):
        """Hitting the turn cap with both sides alive is not a victory."""
        self.assertFalse(_line("unresolved", [0.0]).won)
        self.assertTrue(_line("win", [0.0]).won)

    def test_leads_are_weighted_by_plausibility(self):
        """A lead no good player brings must not dominate the rating."""
        r = _rating(("win", 0.9, [0.0]), ("loss", 0.1, [0.0]))
        self.assertAlmostEqual(r.robust_win_rate, 0.9)

    def test_clean_wins_exclude_any_line_with_a_severe_turn(self):
        r = _rating(("win", 0.5, [0.0]), ("win", 0.5, [SEVERE + 1]))
        self.assertAlmostEqual(r.robust_win_rate, 1.0)
        self.assertAlmostEqual(r.reliable_wins, 0.5)

    def test_outcomes_are_reported_so_unresolved_is_visible(self):
        r = _rating(("win", 1.0, [0.0]), ("loss", 1.0, [0.0]),
                    ("unresolved", 1.0, [0.0]))
        self.assertEqual(r.outcomes,
                         {"win": 1, "loss": 1, "draw": 0, "unresolved": 1})

    def test_no_audited_leads_is_zero_not_a_crash(self):
        empty = BringRating(our_bring=["A"])
        self.assertEqual(empty.adjusted_win_rate, 0.0)
        self.assertEqual(empty.robust_win_rate, 0.0)
        self.assertEqual(empty.reliable_wins, 0.0)

    def test_a_clean_win_outranks_a_punishable_one(self):
        """The whole point: both teams win, one of them is worth more."""
        clean = _rating(("win", 1.0, [5.0]))
        shaky = _rating(("win", 1.0, [120.0]))
        self.assertEqual(clean.robust_win_rate, shaky.robust_win_rate)
        self.assertGreater(clean.adjusted_win_rate, shaky.adjusted_win_rate)

    def test_the_discount_reads_expected_loss_not_worst_case(self):
        """A punish they cannot reliably find must not be charged in full.

        The turn below is catastrophic IF they read us (exploitability 4x a
        Pokemon) but costs almost nothing against their actual equilibrium
        mixture. The line should keep nearly all its weight.
        """
        rep = LineReport(turns=[_turn(4 * KO_WEIGHT, expected_loss=1.0)])
        rep.outcome, rep.length = "win", 1
        r = BringRating(our_bring=["A"])
        r.per_lead.append(((("X", "Y")), 1.0, rep))
        self.assertAlmostEqual(r.adjusted_win_rate, 1.0 - 1.0 / KO_WEIGHT)
        # The worst case is still reported, undiscounted, for the audit trail.
        self.assertAlmostEqual(rep.mean_exploitability, 4 * KO_WEIGHT)

    def test_negative_exploitability_noise_cannot_inflate_a_score(self):
        """Convergence noise gives small negative values; they must not pay."""
        r = _rating(("win", 1.0, [-2.0, -1.0]))
        self.assertLessEqual(r.adjusted_win_rate, 1.0)


class TestEquilibriumReplyIsSeparate(unittest.TestCase):

    def test_punisher_and_equilibrium_reply_are_distinct_fields(self):
        """Scoring a turn and advancing a line need different opponents."""
        rec = TurnRobustness(turn=1, exploitability=10.0, regret=0.0,
                             equilibrium=0.0, worst_case=-10.0,
                             punisher="best_response",
                             equilibrium_reply="equilibrium")
        self.assertNotEqual(rec.punisher, rec.equilibrium_reply)


if __name__ == "__main__":
    unittest.main()
