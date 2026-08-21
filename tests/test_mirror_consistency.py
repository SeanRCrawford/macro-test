"""If A beats B, then B must lose to A. The same position, mirrored.

Challenged directly, and correctly: "if one team uses a given lead and sees a
win, why does the other team in its own search see a win vs that specific lead
as well. It must work 2 ways."

It did not. The ladder of measurements, 28 mirrored matchups at --turns 24
(tools/measure_side_bias.py):

    pilot / seat rule / info                contradictions   direction
    greedy                                       78%         21 p1 /  0 p2
    equilibrium, ours samples + theirs modal     43%          3 p1 /  9 p2
    equilibrium, both seats sample               29%          0 p1 /  8 p2
    equilibrium, shipped rule, symmetric info    29%          4 p1 /  4 p2
    equilibrium, both seats sample + sym info    14%          2 p1 /  2 p2

Two separate things, and only one of them is a bug:

  * THE SEAT RULE WAS A BUG. Our action was sampled from mixture `p` while
    theirs was the single modal action of `q` -- a pure strategy, which is not
    what the equilibrium prescribes. Fixed: both seats now sample.

  * THE WIDER OPPONENT MOVE SPACE IS NOT. `_attach_movesets` deliberately gives
    the opponent six plausible moves per Pokemon against our known four,
    because assuming they hold the usage-standard four is self-fulfilling and
    measured at 10 points of win rate. That is correct modelling that happens
    to ride with the SEAT, which is why it shows up as a p2 lean. Strip it and
    the direction balances exactly.

The 14% that remains is balanced both ways: genuine near-ties, not a seat
advantage.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import matchup_search as ms  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None
# One pair is enough for the invariants; the sweep lives in the measure tool.
PAIRS = [("Rain", "Big 6"), ("Sand", "NAIC")]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def play(a4, b4, pilot, turns=18):
    w = world()
    winner, _t, _b = ms.play_out_pair(
        list(a4), list(b4), w["merged"], w["moves"], w["natures"],
        w["typechart"], max_turns=turns, pilot=pilot)
    return winner


class TestTheSeatsArePlayedTheSameWay(unittest.TestCase):
    """The bug, at the level it actually lived: one function, two rules."""

    def test_both_seats_come_from_their_own_mixture(self):
        import solver
        from turn_game import TurnSolution
        calls = []
        real = solver._pick_from_mixture

        def spy(actions, dist):
            calls.append(list(dist))
            return actions[0]

        fake = TurnSolution(our_actions=["ours-a", "ours-b"],
                            their_actions=["theirs-a", "theirs-b"],
                            p=[0.7, 0.3], q=[0.4, 0.6],
                            value=0.0, maximin_value=0.0, maximin_index=0,
                            n_sims=0)
        import turn_game
        real_solve = turn_game.solve_turn
        solver._pick_from_mixture = spy
        turn_game.solve_turn = lambda *a, **k: fake
        try:
            ours, theirs = ms._equilibrium_joint_actions(object(), {})
        finally:
            solver._pick_from_mixture = real
            turn_game.solve_turn = real_solve
        self.assertEqual(ours, "ours-a")
        self.assertEqual(theirs, "theirs-a")
        # THE ASSERTION THAT MATTERS: both seats went through the mixture, and
        # each with its OWN distribution.
        self.assertEqual(calls, [[0.7, 0.3], [0.4, 0.6]])

    def test_neither_seat_takes_a_bare_modal_action(self):
        """Reading the source, because the defect was a second code path that
        never called the sampler at all."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                "matchup_search.py"), encoding="utf-8").read()
        body = src[src.index("def _equilibrium_joint_actions"):]
        body = body[:body.index("\ndef ")]
        # One shared helper, not one rule per seat.
        self.assertEqual(body.count("_pick_from_mixture"), 1, body)


class TestAPlayoutIsReproducible(unittest.TestCase):
    """Mixture sampling is seeded per decision. Without this a 'record' depends
    on how many games ran before it, and two runs of the same sweep disagree
    for reasons that have nothing to do with the teams."""

    def test_the_same_matchup_gives_the_same_answer_every_time(self):
        w = world()
        a4 = list(w["teams"]["Rain"])[:4]
        b4 = list(w["teams"]["Big 6"])[:4]
        got = {play(a4, b4, ms.EQUILIBRIUM_PILOT) for _ in range(4)}
        self.assertEqual(len(got), 1, got)


class TestTheMirror(unittest.TestCase):

    @staticmethod
    def mirrored(a4, b4, pilot):
        """(forward, reversed) both named from A's point of view."""
        fwd = play(a4, b4, pilot)
        rev = play(b4, a4, pilot)
        flip = {"p1": "p2", "p2": "p1"}
        return fwd, flip.get(rev, rev)

    def test_greedy_still_hands_p1_the_win_both_ways(self):
        """The bug this whole thread started from, pinned so nobody 'fixes' the
        equilibrium pilot by quietly making greedy the default again."""
        w = world()
        a4 = list(w["teams"]["Rain"])[:4]
        b4 = list(w["teams"]["Big 6"])[:4]
        fwd, rev = self.mirrored(a4, b4, ms.GREEDY_PILOT)
        self.assertEqual(fwd, "p1")
        self.assertEqual(rev, "p2")      # i.e. p1 won that one too
        self.assertNotEqual(fwd, rev)

    def test_equilibrium_does_not_hand_p1_the_win_on_that_same_matchup(self):
        """The claim the fix actually supports, and no more.

        Not "equilibrium always mirrors": it does not, and asserting that on a
        cherry-picked pair would be dishonest. 14% of matchups still contradict
        once the deliberate information asymmetry is stripped, and those point
        both ways -- near-ties, not a seat advantage. What the fix does buy is
        that the SEAT stops deciding, which is checkable on the pair where
        greedy's seat advantage is visible.
        """
        w = world()
        a4 = list(w["teams"]["Rain"])[:4]
        b4 = list(w["teams"]["Big 6"])[:4]
        fwd, rev = self.mirrored(a4, b4, ms.EQUILIBRIUM_PILOT)
        self.assertFalse(fwd == "p1" and rev == "p2",
                         "equilibrium reproduced greedy's p1-wins-both-ways")


class TestTheMeasurementToolSaysWhichWay(unittest.TestCase):
    """A contradiction count without a direction cannot tell a seat advantage
    from chaos, and those need opposite responses."""

    def test_it_reports_the_direction(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "tools",
                                "measure_side_bias.py"), encoding="utf-8").read()
        self.assertIn("p1 wins both", src)
        self.assertIn("p2 wins both", src)

    def test_it_can_strip_the_deliberate_information_asymmetry(self):
        """Without this switch the tool's direction column is unreadable: the
        opponent's wider move space is correct modelling that rides with the
        seat, so it looks exactly like a bug."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "tools",
                                "measure_side_bias.py"), encoding="utf-8").read()
        self.assertIn("--symmetric-info", src)
        self.assertIn("build_wide_movesets", src)


if __name__ == "__main__":
    unittest.main()
