"""The fast screen, and the trap it exists to avoid.

Reported: "these teams are not good, they're just losing to the preset teams",
and "I may need a fast, robust way to screen for teams, e.g. just checking for
super obvious punish."

The obvious reading of that -- screen on the punish -- is measurably backwards,
and this file pins the reason so nobody re-implements it. On three real teams,
turn-1 exploitability ranked the deliberately bad one FIRST (3.5, against 49.5
and 66.6 for real teams): a team with no threats gives a best-responding
opponent nothing to gain. So the screen uses the maximin value of the opening --
what we can guarantee -- which orders the same three teams correctly.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import punish_screen  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None
JUNK = ["Slurpuff", "Maushold", "Milotic", "Sinistcha", "Whimsicott", "Florges"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def screen(team, opponents=("Rain", "Big 6")):
    w = world()
    return punish_screen.screen_team(
        list(team), {n: w["teams"][n] for n in opponents},
        w["merged"], w["moves"], w["natures"], w["typechart"])


class TestItDiscriminates(unittest.TestCase):

    def test_a_real_team_beats_a_junk_team(self):
        """The whole point. If this inverts, the screen is throwing away the
        teams worth auditing and keeping the ones that cannot threaten."""
        good = screen(world()["teams"]["Big 6"])
        junk = screen(JUNK)
        self.assertGreater(good["guaranteed"], junk["guaranteed"])

    def test_the_punish_alone_would_have_got_it_backwards(self):
        """Documents the measurement, not just the conclusion: the junk team is
        LESS punishable, because it never threatens anything."""
        good = screen(world()["teams"]["Big 6"])
        junk = screen(JUNK)
        self.assertLess(junk["punish"], good["punish"])

    def test_the_default_floor_sits_between_them(self):
        self.assertTrue(punish_screen.is_hopeless(screen(JUNK)))
        self.assertFalse(
            punish_screen.is_hopeless(screen(world()["teams"]["Big 6"])))


class TestWhatItReports(unittest.TestCase):

    def setUp(self):
        self.result = screen(world()["teams"]["NAIC"])

    def test_it_names_the_opening_that_hurts_most(self):
        name, their_lead, our_bring = self.result["worst_vs"]
        self.assertIn(name, ("Rain", "Big 6"))
        self.assertEqual(len(their_lead), 2)
        self.assertEqual(len(our_bring), 4)

    def test_every_opponent_gets_a_number(self):
        self.assertEqual(set(self.result["per_opponent"]), {"Rain", "Big 6"})

    def test_the_headline_is_the_worst_opponent(self):
        self.assertEqual(self.result["guaranteed"],
                         min(self.result["per_opponent"].values()))

    def test_a_second_opinion_cannot_make_us_worse(self):
        """We pick our best answer, so widening OUR choices can only help --
        being punishable with one bring is not being punishable."""
        w = world()
        narrow = punish_screen.screen_team(
            list(w["teams"]["NAIC"]), {"Rain": w["teams"]["Rain"]},
            w["merged"], w["moves"], w["natures"], w["typechart"],
            our_brings=1)
        wide = punish_screen.screen_team(
            list(w["teams"]["NAIC"]), {"Rain": w["teams"]["Rain"]},
            w["merged"], w["moves"], w["natures"], w["typechart"],
            our_brings=3)
        self.assertGreaterEqual(wide["guaranteed"], narrow["guaranteed"])

    def test_more_of_their_leads_cannot_make_us_look_better(self):
        """...and THEY pick the worst for us, so widening theirs can only hurt."""
        w = world()
        narrow = punish_screen.screen_team(
            list(w["teams"]["NAIC"]), {"Rain": w["teams"]["Rain"]},
            w["merged"], w["moves"], w["natures"], w["typechart"],
            leads_per_opponent=1)
        wide = punish_screen.screen_team(
            list(w["teams"]["NAIC"]), {"Rain": w["teams"]["Rain"]},
            w["merged"], w["moves"], w["natures"], w["typechart"],
            leads_per_opponent=3)
        self.assertLessEqual(wide["guaranteed"], narrow["guaranteed"])


class TestWiring(unittest.TestCase):

    def test_rate_team_skips_a_hopeless_team_without_auditing_it(self):
        import roster_rating
        w = world()
        record = roster_rating.rate_team(
            JUNK, {**w, "teams": {"Big 6": w["teams"]["Big 6"]}},
            effort="standard", turns=10, punish_floor=0.0)   # reject everything
        self.assertIn("skipped", record)
        self.assertIn("opening already lost", record["skipped"])
        self.assertIsNone(record["adjusted_win_rate"])

    def test_no_floor_means_no_screen(self):
        """It must stay opt-in: the default path may not silently drop teams."""
        import roster_rating
        w = world()
        record = roster_rating.rate_team(
            JUNK, {**w, "teams": {"Big 6": w["teams"]["Big 6"]}},
            effort="quick", turns=6, punish_floor=None)
        self.assertNotIn("skipped", record)

    def test_an_accepted_team_still_reports_its_opening(self):
        """A floor you only see when it fires is one you cannot calibrate. The
        number has to survive onto the teams that PASSED, or the only evidence
        about where to put the floor is the teams you already threw away."""
        import roster_rating
        w = world()
        record = roster_rating.rate_team(
            JUNK, {**w, "teams": {"Big 6": w["teams"]["Big 6"]}},
            effort="quick", turns=6, punish_floor=-1e9)   # reject nothing
        self.assertNotIn("skipped", record)
        self.assertIsInstance(record.get("opening_guaranteed"), float)

    def test_a_team_a_LATER_screen_rejected_still_reports_its_opening(self):
        """Found by running, not reading: on a live run all four teams passed
        the opening screen and were then dropped by --min-winrate, so the
        calibration summary saw nothing. A record that exits through a
        different screen is still evidence about where this floor belongs."""
        import roster_rating
        w = world()
        record = roster_rating.rate_team(
            JUNK, {**w, "teams": {"Big 6": w["teams"]["Big 6"]}},
            effort="quick", turns=6, punish_floor=-1e9,   # opening screen passes
            min_winrate=1.01)                             # this one rejects
        self.assertIn("below --min-winrate", record["skipped"])
        self.assertIsInstance(record.get("opening_guaranteed"), float)

    def test_without_the_screen_there_is_no_number_to_report(self):
        """It must not appear as a stale or invented value when the screen did
        not run -- the progress line keys off its presence."""
        import roster_rating
        w = world()
        record = roster_rating.rate_team(
            JUNK, {**w, "teams": {"Big 6": w["teams"]["Big 6"]}},
            effort="quick", turns=6, punish_floor=None)
        self.assertIsNone(record.get("opening_guaranteed"))


if __name__ == "__main__":
    unittest.main()
