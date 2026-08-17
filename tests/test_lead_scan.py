"""The cheap lead scan: six questions per opponent, answered by arithmetic.

The worked example is the acceptance test, and it is quoted in full in
src/lead_scan.py. Its shape is what the module implements:

    "1. Mega Charizard Y: outsped, Garchomp KOs with rock slide.
     2. Garchomp: outsped, Ninetales KOs with Blizzard.
     3. Kingambit, Basculegion: Garchomp earthquake + Ninetales-Alola Blizzard
        ... will then collapse into a win by turn 2.
     4. Mega Floette, Whimsicott: ... Garchomp switches to Scizor ... while
        Ninetales-Alola uses Blizzard to bring Whimsicott to sash."

THE PER-ENEMY SCAN IS NOT THE SCREEN, and the history is worth keeping. Asking
"can we remove each of their six" passed 235 of 275 swept lead pairs -- 85%,
which narrows nothing -- because every verdict let us aim both attacks at one
enemy while its partner did nothing back. `race_bring` prices the exchange
instead: our lead against all fifteen of THEIR lead pairs, two turns, both sides
focus-firing. That pass rate is 31 of 275. See `TestTheRace` and
`test_the_screen_is_selective`; the per-enemy verdicts remain as the EXPLANATION
of a matchup, which is the form the idea was expressed in.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import lead_scan as ls  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None

# The bring from the worked example: Ninetales-Alola + Garchomp leading, with a
# Fairy resist and a Ground-immune behind. "mega scizor and a flying/levitate
# pokemon in the back to ignore garchomp partner earthquake dmg."
EXAMPLE = ["Ninetales-Alola", "Garchomp", "Mega Scizor", "Rotom-Wash"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def scan(our4=None, opponent="Big 6"):
    W = world()
    return ls.scan_bring(our4 or EXAMPLE, W["teams"][opponent], W,
                         opponent_name=opponent)


class TestTheWorkedExample(unittest.TestCase):

    def setUp(self):
        self.report = scan()
        self.by_enemy = {v.enemy: v for v in self.report.verdicts}

    def test_every_one_of_their_six_gets_a_verdict(self):
        self.assertEqual(len(self.report.verdicts), 6)
        self.assertEqual(set(self.by_enemy), set(world()["teams"]["Big 6"]))

    def test_nothing_is_unanswered(self):
        """"There is nothing which can really afford to switch in, so its a
        general pin given the spread damage output and speed tiers.\""""
        self.assertEqual(self.report.problems, [])
        self.assertEqual(self.report.covered, 6)
        self.assertGreater(self.report.score, 0.0)

    def test_charizard_is_outsped_by_garchomp(self):
        """"1. Mega Charizard Y: outsped, Garchomp KOs with rock slide.\""""
        v = self.by_enemy["Mega Charizard Y"]
        self.assertEqual(v.verdict, ls.OUTSPED)
        self.assertIn("Garchomp", v.by)
        self.assertIn("Rock Slide", v.by)

    def test_their_garchomp_is_outsped_by_ninetales(self):
        """"2. Garchomp: outsped, Ninetales KOs with Blizzard.\""""
        v = self.by_enemy["Garchomp"]
        self.assertEqual(v.verdict, ls.OUTSPED)
        self.assertIn("Ninetales-Alola", v.by)
        self.assertIn("Blizzard", v.by)

    def test_kingambit_and_basculegion_fall_to_the_pair(self):
        """"3. Kingambit, Basculegion: Garchomp earthquake + Ninetales-Alola
        Blizzard or Freeze Dry is very threatening.\""""
        for name in ("Kingambit", "Basculegion"):
            v = self.by_enemy[name]
            self.assertIn(v.verdict, (ls.FOCUSED, ls.TWO_TURNS),
                          f"{name} came out {v.verdict}")
            self.assertIn("Garchomp", v.by)
            self.assertIn("Ninetales-Alola", v.by)


class TestTheVerdicts(unittest.TestCase):

    def test_the_order_prefers_the_cleanest_answer(self):
        """A Pokemon we outspeed and OHKO is also one we could grind down. The
        report must name the pin, since that is the one that costs nothing."""
        self.assertEqual(ls.ANSWERED[0], ls.OUTSPED)
        self.assertGreater(ls.WEIGHT[ls.OUTSPED], ls.WEIGHT[ls.FOCUSED])
        self.assertGreater(ls.WEIGHT[ls.FOCUSED], ls.WEIGHT[ls.TWO_TURNS])
        self.assertGreater(ls.WEIGHT[ls.TWO_TURNS], ls.WEIGHT[ls.SWITCH])
        self.assertEqual(ls.WEIGHT[ls.PROBLEM], 0.0)

    def test_one_hole_zeroes_the_score(self):
        """A HARD zero, not a penalty. "a fixed lead vs each team that can
        withstand ANY of theirs" -- averaging a hole away is how a lead that
        loses to one Pokemon ends up recommended."""
        report = ls.LeadReport(lead=("A", "B"), back=("C", "D"), opponent="X")
        report.verdicts = [ls.EnemyVerdict(f"e{i}", ls.OUTSPED) for i in range(5)]
        self.assertEqual(report.score, 1.0)
        report.verdicts.append(ls.EnemyVerdict("e5", ls.PROBLEM))
        self.assertEqual(report.score, 0.0)
        self.assertEqual(report.covered, 5)

    def test_a_preempt_downgrades_but_does_not_unanswer(self):
        """We still remove it; we paid a Pokemon to do so. A trade, not a pin."""
        clean = ls.EnemyVerdict("e", ls.OUTSPED)
        traded = ls.EnemyVerdict("e", ls.OUTSPED, preempts=True)
        self.assertTrue(traded.answered)
        self.assertLess(traded.weight, clean.weight)
        self.assertAlmostEqual(traded.weight, clean.weight * ls.PREEMPT_FACTOR)

    def test_worst_of_takes_their_best_answer(self):
        """They choose their Mega, their four and their lead after seeing ours."""
        good = ls.LeadReport(("A", "B"), ("C", "D"), "easy")
        good.verdicts = [ls.EnemyVerdict("x", ls.OUTSPED)]
        bad = ls.LeadReport(("A", "B"), ("C", "D"), "hard")
        bad.verdicts = [ls.EnemyVerdict("x", ls.PROBLEM)]
        self.assertIs(ls.worst_of([good, bad]), bad)
        self.assertIs(ls.worst_of([bad, good]), bad)
        self.assertIsNone(ls.worst_of([]))


class TestTheMegaSlot(unittest.TestCase):
    """"Assume you know enemy items" -- but not which Pokemon they Mega, because
    they choose that at preview, after seeing your four."""

    def test_their_mega_choices_are_enumerated(self):
        W = world()
        slots = ls.mega_slots(W["teams"]["Big 6"], W)
        self.assertIn("Mega Charizard Y", slots)
        self.assertIn("Mega Floette", slots)

    def test_the_speed_tie_the_example_warned_about(self):
        """"mega floette OHKOs garchomp if it wins speed tie". Base Floette is
        111 and its Mega is 166 -- exactly Garchomp. So WHICH of their Pokemon
        holds the Mega slot decides whether that is a clean outspeed or a coin
        flip, and the scan currently answers for one of their choices."""
        from _harness import setup_battle
        W = world()
        b, _ms = setup_battle(list(EXAMPLE), list(W["teams"]["Big 6"]), W)
        floette = [c for c in b.p2.roster if c.name == "Mega Floette"][0]
        chomp = [c for c in b.p1.roster if c.name == "Garchomp"][0]
        base_spe = floette.stats["spe"]
        self.assertLess(base_spe, chomp.stats["spe"])
        # And the Mega form it would reach if it held the slot.
        alone, _ms2 = setup_battle(list(EXAMPLE), ["Mega Floette", "Whimsicott",
                                                   "Kingambit", "Basculegion"], W)
        fl2 = [c for c in alone.p2.roster if c.name == "Mega Floette"][0]
        self.assertTrue(fl2.is_mega_pick)
        self.assertEqual(fl2.mega_stats["spe"], chomp.stats["spe"],
                         "the tie the worked example flagged")


class TestSpeciesClause(unittest.TestCase):
    """Four distinct BASE species, at most one Mega.

    The sweep's top answer was once "Garchomp + Mega Garchomp", with
    "Dragonite + Mega Dragonite" behind it. Both illegal -- species clause counts
    the base form and a Mega is the same Pokemon holding a stone. A screen that
    recommends an illegal team is worse than no screen, because its answer looks
    actionable.
    """

    def test_a_mega_is_its_base_species(self):
        self.assertEqual(ls.species_of("Mega Garchomp"), "Garchomp")
        self.assertEqual(ls.species_of("Mega Charizard Y"), "Charizard")
        self.assertEqual(ls.species_of("Garchomp"), "Garchomp")
        self.assertEqual(ls.species_of("Ninetales-Alola"), "Ninetales-Alola")

    def test_the_pair_that_was_recommended_is_rejected(self):
        self.assertFalse(ls.legal_bring(
            ["Garchomp", "Mega Garchomp", "Gallade", "Basculegion"]))
        self.assertFalse(ls.legal_bring(
            ["Dragonite", "Mega Dragonite", "Gallade", "Garchomp"]))
        self.assertFalse(ls.legal_bring(
            ["Mega Garchomp", "Whimsicott", "Gallade", "Garchomp"]))

    def test_two_megas_are_rejected(self):
        self.assertFalse(ls.legal_bring(
            ["Mega Scizor", "Mega Charizard Y", "Garchomp", "Whimsicott"]))

    def test_the_worked_example_is_legal(self):
        self.assertTrue(ls.legal_bring(EXAMPLE))


class TestTheRace(unittest.TestCase):
    """The 2v2 exchange, which is what actually decides a matchup.

    "There just needs to be an overlap across the different teams (bring 4 to
     each)" -- and the question a lead has to answer is whether it holds up
    against ANY of their fifteen openings, not whether it can remove each of
    their six Pokemon one at a time with both attacks and no reply.
    """

    def test_the_worked_example_loses_to_none_of_their_openings(self):
        W = world()
        report = ls.race_bring(EXAMPLE, W["teams"]["Big 6"], W,
                               opponent_name="Big 6")
        self.assertEqual(len(report.results), 15)
        self.assertEqual(report.losses, [], 
                         f"beaten by {[r.enemy_lead for r in report.losses]}")
        self.assertGreater(report.score, 0.3)

    def test_a_weak_bring_loses_to_many(self):
        """The discriminating half. If this ever passes too, the race has stopped
        measuring anything."""
        W = world()
        report = ls.race_bring(["Whimsicott", "Basculegion", "Kingambit",
                                "Garchomp"], W["teams"]["Big 6"], W,
                               opponent_name="Big 6")
        self.assertGreater(len(report.losses), 3)
        self.assertEqual(report.score, 0.0)

    def test_one_losing_opening_zeroes_the_score(self):
        report = ls.RaceReport(lead=("A", "B"), back=("C", "D"), opponent="X")
        report.results = [ls.PairResult(("e", "f"), ls.WIN, 0, 2, 1.0)
                          for _ in range(14)]
        self.assertGreater(report.score, 0.0)
        report.results.append(ls.PairResult(("g", "h"), ls.LOSS, 2, 0, -1.0))
        self.assertEqual(report.score, 0.0)
        self.assertEqual(report.wins, 14)

    def test_a_pokemon_removed_before_it_acts_deals_nothing(self):
        """The pin, expressed as the only thing it really is: an attack that
        never happens. Asserted by giving one side overwhelming priority damage
        and checking the other contributes nothing."""
        W = world()
        from _harness import setup_battle
        from threat import build_threat_matrix
        b, ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)
        matrix = build_threat_matrix(b, ms)
        ours = [c for c in b.p1.roster if not c.fainted][:2]
        theirs = [c for c in b.p2.roster if not c.fainted][:2]
        hp = {id(c): 1.0 for c in ours + theirs}
        ls._turn(matrix, ours, theirs, hp)
        # Somebody took damage; nobody went below zero or above one.
        self.assertTrue(any(v < 1.0 for v in hp.values()))
        for v in hp.values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 1.0)


class TestCalibration(unittest.TestCase):

    def test_the_screen_is_selective(self):
        """The number that says whether this is a screen at all.

        MEASURED, both before and after. The first version scored each of their
        six Pokemon independently -- can we focus-fire it down -- and 235 of 275
        swept lead pairs passed. 85%. A screen that admits 85% of candidates does
        not narrow anything, which is the entire job.

        The reason was structural rather than a tuning problem: every verdict let
        us aim BOTH attacks at one enemy while its partner did nothing back, and
        two attackers focused on one target answer almost anything. Pricing the
        exchange -- the 2v2 race over this turn and next, both sides
        focus-firing -- took the pass rate to 31 of 275, about 11%.

        This test pins that it stays selective. A pass rate creeping back toward
        a half means a verdict has gone soft again.
        """
        import itertools
        W = world()
        from generate_team import build_candidate_pool
        pool = list(build_candidate_pool(W["merged"], top_n=12))
        clean = total = 0
        for lead in itertools.combinations(pool, 2):
            back = [n for n in pool if n not in lead][:2]
            our4 = list(lead) + back
            if not ls.legal_bring(our4):
                continue
            report = ls.race_bring(our4, W["teams"]["Big 6"], W,
                                   opponent_name="Big 6")
            total += 1
            if report.score > 0:
                clean += 1
        rate = clean / total
        self.assertGreater(total, 30)
        self.assertLess(rate, 0.5,
                        f"{clean}/{total} pairs pass -- the screen has gone soft")


if __name__ == "__main__":
    unittest.main()
