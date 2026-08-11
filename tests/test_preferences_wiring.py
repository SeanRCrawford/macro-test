"""preferences.csv has to mean the same thing in both entry points.

Reported: "make sure the overnight.bat includes and excludes pokemon based on
the preferences.csv file". It did not. EXCLUDE worked, because that is applied
when the candidate pool is built -- but INCLUDE and PREFER live in the beam
search, and `generate_overnight` was not passing them, so an "Include" entry was
merely allowed into the pool rather than required in every team, and "Prefer"
did nothing at all. `generate_team.py` had always passed both.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from species_data import load_preferences  # noqa: E402
from team_search import beam_search_teams, build_candidate_pool  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
TOOLS = os.path.join(os.path.dirname(__file__), "..", "tools")


def matrix_for(pool, enemy_pairs, margin=1.0):
    """A flat screener matrix: every pair equally good, so nothing but the
    preferences can decide what the beam keeps."""
    import itertools
    return {p: {k: margin for k in enemy_pairs}
            for p in itertools.combinations(pool, 2)}


class TestTheBeamHonoursThem(unittest.TestCase):

    def setUp(self):
        self.pool = [f"P{i}" for i in range(10)]
        self.enemy_pairs = [("Foe", ("E1", "E2"))]
        self.matrix = matrix_for(self.pool, self.enemy_pairs)
        self.merged = {n: {"types": ["Normal"], "score": 100.0,
                           "defensive_chart": None} for n in self.pool}

    def test_every_team_contains_an_included_pokemon(self):
        finals = beam_search_teams(self.pool, self.matrix, self.enemy_pairs,
                                   self.merged, beam_width=5,
                                   must_include=["P7"])
        self.assertTrue(finals)
        for _score, team in finals:
            self.assertIn("P7", team)

    def test_without_must_include_it_is_only_eligible(self):
        """The distinction the pipeline was getting wrong: being in the pool is
        not the same as being in the team."""
        finals = beam_search_teams(self.pool, self.matrix, self.enemy_pairs,
                                   self.merged, beam_width=5)
        self.assertFalse(all("P7" in team for _s, team in finals))


class TestBothEntryPointsPassThem(unittest.TestCase):
    """Read from source: the failure was a missing argument, and only the call
    site can show whether it is there."""

    @staticmethod
    def call_site(path, needle="beam_search_teams("):
        text = open(path, encoding="utf-8").read()
        start = text.index(needle)
        return text[start:start + 400]

    def test_generate_overnight_passes_include_and_prefer(self):
        call = self.call_site(os.path.join(TOOLS, "generate_overnight.py"))
        self.assertIn("must_include=", call)
        self.assertIn("prefer=", call)

    def test_generate_team_still_does(self):
        call = self.call_site(os.path.join(SRC, "generate_team.py"))
        self.assertIn("must_include=", call)
        self.assertIn("prefer=", call)


class TestTheRealFile(unittest.TestCase):

    def test_exclusions_reach_the_pool_including_mega_forms(self):
        """Excluding "Garchomp" must also exclude "Mega Garchomp" -- they are
        the same Pokemon."""
        from species_data import build_merged_dataset
        merged, *_ = build_merged_dataset()
        prefs = dict(load_preferences())
        prefs["exclude"] = list(prefs["exclude"]) + ["Garchomp"]
        pool = build_candidate_pool(merged, top_n=60, prefs=prefs)
        self.assertNotIn("Garchomp", pool)
        self.assertFalse([n for n in pool if n.startswith("Mega Garchomp")])

    def test_the_shipped_file_parses_into_the_three_lists_plus_sets(self):
        prefs = load_preferences()
        self.assertEqual(set(prefs), {"include", "exclude", "prefer", "sets"})
        for key in ("include", "exclude", "prefer"):
            self.assertIsInstance(prefs[key], list)
        self.assertIsInstance(prefs["sets"], dict)


class TestTheBatchFilePassesTheScreens(unittest.TestCase):

    def test_punish_screen_reaches_stage_1(self):
        text = open(os.path.join(os.path.dirname(__file__), "..",
                                 "overnight.bat"), encoding="utf-8").read()
        self.assertIn('"--punish-screen"', text)
        stage1 = text[text.index("generate_overnight.py"):]
        self.assertIn("%PUNISHSCR%", stage1[:600])


if __name__ == "__main__":
    unittest.main()
