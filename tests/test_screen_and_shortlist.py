"""What the lead/back search shortlists, and what the screen throws away.

Reported as two questions: "is the lead/back generation really robust, does it
check every reasonable option? I feel as though I can see better ones" and "how
does the 'opening already lost' work".

Three separate defects came out of them, all of the same shape -- a number that
was standing in for a different number:

  1. The screener scored the LEAD PAIR only, so all six configs sharing a lead
     scored identically and the back two was decided by `itertools` emission
     order. On one real pairing the six tied backs ranged from 4/15 to 8/15
     under the equilibrium pilot: the best available back was twice the worst,
     picked by accident.
  2. `punish_screen` read `worst_case` while its own docstring said MAXIMIN.
     `worst_case` is the worst case of the ONE row sampled from our equilibrium
     mixture, and in a mixed position the individual pure rows can each look
     dreadful -- that is what mixing is for. It rejected a position that goes on
     to win 100% of the time (-278.0 sampled against +20.1 maximin).
  3. `fast_playout` never took the edited sets, so stage 1 ranked candidates on
     DEFAULT usage sets. Changing an ability or hand-picking four moves could
     not affect which candidates were shortlisted.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from fast_eval import fast_pair_score  # noqa: E402
from punish_screen import DEFAULT_FLOOR, opening_turn  # noqa: E402

_WORLD = None
OUR6 = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf",
        "Pelipper", "Archaludon"]
THEIR4 = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def dbs():
    w = world()
    return w["merged"], w["moves"], w["natures"], w["typechart"]


class TestTheCandidateSpaceIsComplete(unittest.TestCase):
    """The enumeration itself was never the problem, and saying so matters:
    all 90 (bring-4, lead-2) combinations are generated. What was wrong was the
    ORDER they were ranked in, which decides which few get verified."""

    def our_configs(self, pool=OUR6):
        out, seen = [], set()
        for bring4 in itertools.combinations(pool, 4):
            for lead2 in itertools.combinations(bring4, 2):
                key = tuple(list(lead2)
                            + [x for x in bring4 if x not in lead2])
                if key not in seen:
                    seen.add(key)
                    out.append(list(key))
        return out

    def test_all_ninety_are_generated(self):
        self.assertEqual(len(self.our_configs()), 90)

    def test_every_bring_of_four_appears(self):
        brings = {frozenset(c) for c in self.our_configs()}
        self.assertEqual(len(brings), 15)

    def test_every_lead_pair_appears_for_each_bring(self):
        by_bring = {}
        for c in self.our_configs():
            by_bring.setdefault(frozenset(c), set()).add(tuple(sorted(c[:2])))
        self.assertTrue(all(len(v) == 6 for v in by_bring.values()))


class TestTheScreenerCanSeeTheBack(unittest.TestCase):

    def margins(self, pair):
        merged, moves, natures, typechart = dbs()
        cache = {}
        return [fast_pair_score(pair, lead, merged, moves, natures, typechart,
                                cache)["margin"]
                for lead in itertools.combinations(
                    ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"], 2)]

    def test_different_backs_score_differently(self):
        """The premise of the tie-break: our pairs are not interchangeable, so
        scoring the back pair separates configs the lead score cannot."""
        a = min(self.margins(("Farigiraf", "Archaludon")))
        b = min(self.margins(("Mega Aerodactyl", "Pelipper")))
        self.assertNotAlmostEqual(a, b, places=3)

    def test_the_search_records_a_back_margin(self):
        import inspect
        import matchup_search
        src = inspect.getsource(matchup_search.search_robust_composition)
        self.assertIn("screen_back_margin", src)

    def test_the_sort_uses_it_as_a_tie_break_only(self):
        """The primary key must stay `worst_margin`, so this cannot reorder two
        candidates the screener could genuinely tell apart."""
        import inspect
        import matchup_search
        src = inspect.getsource(matchup_search.search_robust_composition)
        self.assertIn('-d["worst_margin"]', src)
        self.assertIn("screen_back_margin", src.split("scored.sort")[1])


class TestTheScreenUsesTheMaximin(unittest.TestCase):

    def setUp(self):
        merged, moves, natures, typechart = dbs()
        self.rec = opening_turn(
            ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"],
            THEIR4, merged, moves, natures, typechart)

    def test_turn_robustness_exposes_a_maximin(self):
        self.assertIsNotNone(getattr(self.rec, "maximin", None))

    def test_maximin_is_worst_case_plus_regret(self):
        """The identity that makes the two numbers comparable, and the reason
        the gap has a name."""
        self.assertAlmostEqual(self.rec.maximin,
                               self.rec.worst_case + self.rec.regret, places=3)

    def test_the_maximin_is_never_below_the_sampled_row(self):
        self.assertGreaterEqual(self.rec.maximin, self.rec.worst_case - 1e-6)

    def test_this_winning_opening_is_no_longer_screened_out(self):
        """The position goes on to win 100% of the time. It was being rejected
        as hopeless."""
        self.assertLess(self.rec.worst_case, DEFAULT_FLOOR)      # the old test
        self.assertGreater(self.rec.maximin, DEFAULT_FLOOR)      # the new one

    def test_a_genuinely_lost_opening_is_still_screened_out(self):
        """Not a looser floor -- a different statistic. This one loses in five
        turns and must still be rejected."""
        merged, moves, natures, typechart = dbs()
        rec = opening_turn(
            ["Garchomp", "Mega Aerodactyl", "Mega Charizard Y", "Farigiraf"],
            THEIR4, merged, moves, natures, typechart)
        self.assertLess(rec.maximin, DEFAULT_FLOOR)

    def test_the_screen_reads_maximin_not_worst_case(self):
        import inspect
        import punish_screen
        src = inspect.getsource(punish_screen.screen_team)
        self.assertIn("rec.maximin", src)
        self.assertNotIn("rec.worst_case", src)


class TestStageOneSeesTheEditedSets(unittest.TestCase):

    def score(self, sets):
        merged, moves, natures, typechart = dbs()
        return fast_pair_score(("Arcanine-Hisui", "Hydreigon"),
                               ("Pelipper", "Archaludon"),
                               merged, moves, natures, typechart, {},
                               our_sets=sets)["margin"]

    def test_crippling_a_lead_changes_the_screen_score(self):
        """Before the fix this was impossible: fast_playout built every team
        from default usage sets, so the two calls returned the same number."""
        self.assertNotAlmostEqual(self.score(None),
                                  self.score({"Hydreigon": {"moves": ["Protect"]}}),
                                  places=3)

    def test_crippling_makes_it_worse_not_merely_different(self):
        self.assertLess(self.score({"Hydreigon": {"moves": ["Protect"]}}),
                        self.score(None))

    def test_the_moveset_cache_is_keyed_by_the_chosen_moves(self):
        """A bare name key would hand one side's edited set to the other side's
        Pokemon of the same name in a mirror, and would return the default four
        for an edited Pokemon whose name was already cached."""
        merged, moves, natures, typechart = dbs()
        shared = {}
        plain = fast_pair_score(("Arcanine-Hisui", "Hydreigon"),
                                ("Pelipper", "Archaludon"), merged, moves,
                                natures, typechart, shared)["margin"]
        edited = fast_pair_score(("Arcanine-Hisui", "Hydreigon"),
                                 ("Pelipper", "Archaludon"), merged, moves,
                                 natures, typechart, shared,
                                 our_sets={"Hydreigon": {"moves": ["Protect"]}})["margin"]
        self.assertNotAlmostEqual(plain, edited, places=3)


if __name__ == "__main__":
    unittest.main()
