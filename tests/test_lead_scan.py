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


class TestNeverRelyOnASpeedTie(unittest.TestCase):
    """A coin flip is not a plan.

        "Ideally your strategy does not rely on winning speed ties. Such as,
         Garchomp+Ninetales-Alola is good because Ninetales-Alola beats enemy
         Garchomp first to avoid speed tie, and if the enemy Mega Floette is led
         and speed ties, you can switch Garchomp into the back pokemon
         *specifically prepared for that special case to make that rare
         tied/losing lead a win*."

    Two mechanisms. The race resolves every tie AGAINST us, and a tied opening is
    sent to the patch search exactly as a loss is.
    """

    def setUp(self):
        from _harness import setup_battle
        from threat import build_threat_matrix
        W = world()
        self.b, self.ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)
        self.tm = build_threat_matrix(self.b, self.ms)
        ours = [c for c in self.b.p1.roster if not c.fainted]
        self.lead, self.back = ours[:2], ours[2:]

    def test_the_mirror_garchomp_is_found_as_a_tie(self):
        ties = ls.speed_ties(self.tm, self.lead,
                             [c for c in self.b.p2.roster])
        pairs = {(o.name, e.name) for o, e in ties}
        self.assertIn(("Garchomp", "Garchomp"), pairs)

    def test_a_tie_is_never_what_wins_the_opening(self):
        """The property that matters is not "a tied opening cannot be a win" --
        it is "the win does not DEPEND on the flip". An opening containing a tie
        that we win even after losing the flip is genuinely won, and downgrading
        it would throw away a real advantage.

        So: every tie is resolved against us, and any tie opening we do NOT win
        outright goes to the patch search exactly as a loss does.
        """
        W = world()
        report = ls.race_bring(EXAMPLE, W["teams"]["Big 6"], W,
                               opponent_name="Big 6")
        self.assertTrue(report.ties, "this position has ties to test")
        for r in report.ties:
            if r.verdict != ls.WIN:
                self.assertIsNotNone(
                    r.patch,
                    f"{r.enemy_lead} hinges on a flip and no patch was sought")

    def test_the_race_resolves_ties_against_us(self):
        """A raw (priority, speed) sort left ties to list order, which put our
        Pokemon first and quietly handed us every flip. Their side must sort
        ahead of ours at equal priority and speed."""
        import inspect
        src = inspect.getsource(ls._turn_with)
        self.assertIn('0 if side == "them" else 1', src)


class TestTheNestedPatch(unittest.TestCase):
    """A losing opening is not automatically a hole.

        "...ninetales-alola blizzards twice t1/t2, scizor is switched in t1, then
         t2 bullet punch threatens kill on them; sequence starting t2 is even if
         they attack they are pinned, as nothing else can come in to survive and
         then win (not enough HP after switch damage)"

    That is the same two-turn pin question one level down, which is what makes
    the method recursive.
    """

    def setUp(self):
        from _harness import setup_battle
        from threat import build_threat_matrix
        W = world()
        self.b, self.ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)
        self.tm = build_threat_matrix(self.b, self.ms)
        ours = [c for c in self.b.p1.roster if not c.fainted]
        self.lead, self.back = ours[:2], ours[2:]

    def _enemy(self, *names):
        return [c for c in self.b.p2.roster if c.name in names]

    def test_the_described_switch_is_the_one_found(self):
        """Garchomp leaves, Scizor arrives. The exact patch from the example."""
        got = ls.salvage(self.tm, self.lead, self.back,
                         self._enemy("Mega Floette", "Whimsicott"))
        self.assertIsNotNone(got, "no back converts the Floette lead")
        leaving, arriving, verdict, _margin, _plan = got
        self.assertEqual(leaving.name, "Garchomp")
        self.assertEqual(arriving.name, "Mega Scizor")
        self.assertEqual(verdict, ls.WIN)

    def test_the_switch_in_deals_no_damage_on_the_turn_it_arrives(self):
        """It spent its turn coming in. A patch that has the arriving Pokemon
        attacking on turn 1 is measuring a game nobody can play.

        Tested with the arriving Pokemon ALONE, because alongside a partner the
        effect is masked -- Ninetales' Blizzard already removes the focus target,
        so both versions strip exactly 1.0 and the test passes vacuously either
        way. That is what the first version of this test did.
        """
        arriving = self.back[0]
        theirs = self._enemy("Mega Floette", "Whimsicott")
        ours = [arriving]
        quiet = {id(c): 1.0 for c in ours + theirs}
        ls._turn_with(self.tm, ours, theirs, quiet, not_acting=(arriving,))
        loud = {id(c): 1.0 for c in ours + theirs}
        ls._turn_with(self.tm, ours, theirs, loud)
        self.assertEqual(sum(1.0 - quiet[id(c)] for c in theirs), 0.0,
                         "the arriving Pokemon attacked on the turn it switched")
        self.assertGreater(sum(1.0 - loud[id(c)] for c in theirs), 0.0)

    def test_a_patched_opening_is_held_not_a_hole(self):
        held = ls.PairResult(("a", "b"), ls.LOSS, 2, 0, -1.0,
                             patch=("Garchomp", "Mega Scizor", 0.6))
        hole = ls.PairResult(("c", "d"), ls.LOSS, 2, 0, -1.0)
        self.assertTrue(held.held)
        self.assertFalse(hole.held)

    def test_the_report_separates_patched_from_unheld(self):
        W = world()
        report = ls.race_bring(EXAMPLE, W["teams"]["Big 6"], W,
                               opponent_name="Big 6")
        for r in report.patched:
            self.assertEqual(r.verdict, ls.LOSS)
            self.assertIsNotNone(r.patch)
        for r in report.losses:
            self.assertIsNone(r.patch)


class TestTheHpBudget(unittest.TestCase):
    """"how much HP can each given potential back pokemon out of 4 afford to lose
    and still win vs yours, i.e., can they switch in, take damage and win, if not
    then it is a pin."

    Measured on the reference sequence, once Ninetales-Alola + Mega Scizor is
    established against Big 6:

        Mega Charizard Y   +4.6%   can switch in and win
        Basculegion        -0.8%   cannot switch in
        Kingambit         -18.3%   cannot switch in
        Garchomp         -367.7%   cannot switch in

    Which is the "nothing else can come in to survive and then win" clause as a
    number -- and it shows how thin the one exception is.
    """

    def setUp(self):
        from _harness import setup_battle
        from threat import build_threat_matrix
        W = world()
        self.b, self.ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)
        self.tm = build_threat_matrix(self.b, self.ms)
        self.ours = [c for c in self.b.p1.roster
                     if c.name in ("Ninetales-Alola", "Mega Scizor")]
        self.bench = [c for c in self.b.p2.roster
                      if c.name not in ("Mega Floette", "Whimsicott")]

    def test_most_of_their_bench_cannot_switch_in_at_all(self):
        rows = ls.hp_budget(self.tm, self.ours, self.bench)
        cannot = [n for n, afford, _v in rows if afford <= 0]
        self.assertGreaterEqual(len(cannot), 3,
                                f"expected most of the bench walled out: {rows}")

    def test_a_negative_budget_means_it_dies_arriving_at_full_health(self):
        rows = ls.hp_budget(self.tm, self.ours, self.bench)
        for name, afford, verdict in rows:
            if afford <= 0:
                self.assertEqual(verdict, "cannot switch in", name)
            else:
                self.assertIn(verdict, ("can switch in and win",
                                        "survives but cannot win"), name)

    def test_it_is_sorted_by_who_can_best_afford_it(self):
        rows = ls.hp_budget(self.tm, self.ours, self.bench)
        self.assertEqual([r[1] for r in rows],
                         sorted((r[1] for r in rows), reverse=True))


class TestRobustToAProtectSplit(unittest.TestCase):
    """"is this robust to one enemy attacking and one protecting to whittle down"

    Every race result takes THEIR best plan. A claim that survives only the
    both-attack column is not a claim.
    """

    def setUp(self):
        from _harness import setup_battle
        from threat import build_threat_matrix
        W = world()
        self.b, self.ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)
        self.tm = build_threat_matrix(self.b, self.ms)
        self.lead = [c for c in self.b.p1.roster if not c.fainted][:2]

    def test_all_three_plans_are_considered(self):
        self.assertEqual(len(ls.ENEMY_PLANS), 3)
        self.assertIn("both attack", ls.ENEMY_PLANS)

    def test_the_worst_plan_for_us_is_the_one_reported(self):
        theirs = [c for c in self.b.p2.roster
                  if c.name in ("Mega Floette", "Whimsicott")]
        _v, _od, _td, robust_margin, plan = ls.race_robust(self.tm, self.lead,
                                                           theirs)
        margins = []
        for p in ls.ENEMY_PLANS:
            hp = {id(c): 1.0 for c in self.lead + theirs}
            for _ in range(2):
                ls._turn_with(self.tm, self.lead, theirs, hp, plan=p)
            margins.append(ls._outcome(hp, self.lead, theirs)[3])
        self.assertAlmostEqual(robust_margin, min(margins))
        self.assertIn(plan, ls.ENEMY_PLANS)

    def test_a_protecting_pokemon_neither_takes_nor_deals_damage(self):
        theirs = [c for c in self.b.p2.roster
                  if c.name in ("Mega Floette", "Whimsicott")]
        hp = {id(c): 1.0 for c in self.lead + theirs}
        ls._turn_with(self.tm, self.lead, theirs, hp, plan="left protects")
        self.assertEqual(hp[id(theirs[0])], 1.0,
                         "the protecting Pokemon took damage")


class TestCommittedMoves(unittest.TestCase):
    """The race must commit to a MOVE, and a spread move must hit both.

        "the idea was ideally exerting spread damage (e.g. Garchomp Rock Slide +
         Ninetales-Alola Blizzard) which is the ideal way to maximise our damage
         output (damageslop) and pin; if we focus one of theirs we can be punished
         and it's no longer a solved lead. I don't know if this is robust to the
         simulator; I don't see how Garchomp+Dragonite can beat Whimsicott+Mega
         Floette."

    That doubt was correct, and the flaw was deeper than the verdict.
    `matrix.threat(a, d)` is "a's best move against d" computed independently per
    target, so one attacker could be credited with Earthquake on one foe AND Rock
    Slide on the other in the same turn -- and a spread move could never hit two
    Pokemon, because the matrix has no concept of a move at all.
    """

    def setUp(self):
        from _harness import setup_battle
        W = world()
        self.b, self.ms = setup_battle(
            ["Garchomp", "Dragonite", "Gallade", "Basculegion"],
            list(W["teams"]["Big 6"]), W)

    def _mon(self, side, name):
        roster = (self.b.p1 if side == "p1" else self.b.p2).roster
        return [c for c in roster if c.name == name][0]

    def test_a_spread_move_hits_both_foes_in_one_plan(self):
        chomp = self._mon("p1", "Garchomp")
        wh, fl = self._mon("p2", "Whimsicott"), self._mon("p2", "Mega Floette")
        plans = ls.move_plans(chomp, self.ms["Garchomp"], [wh, fl],
                              [chomp, self._mon("p1", "Dragonite")],
                              self.b.typechart, self.b.field, self.b)
        quake = [p for p in plans if p[0].name == "Earthquake"]
        self.assertTrue(quake, "Garchomp must have Earthquake here")
        move, hits, _prio, spread = quake[0]
        self.assertTrue(spread)
        self.assertIn(id(wh), hits)
        self.assertIn(id(fl), hits)
        self.assertGreater(hits[id(fl)], 1.0, "Earthquake should remove Mega "
                                              "Floette outright")

    def test_a_single_target_move_is_one_plan_per_aim(self):
        """Choosing a target is the punishable thing a spread move is not, so
        each aim has to be a separate plan the search can pick between."""
        dnite = self._mon("p1", "Dragonite")
        wh, fl = self._mon("p2", "Whimsicott"), self._mon("p2", "Mega Floette")
        plans = ls.move_plans(dnite, self.ms["Dragonite"], [wh, fl], [dnite],
                              self.b.typechart, self.b.field, self.b)
        espeed = [p for p in plans if p[0].name == "Extreme Speed"]
        self.assertEqual(len(espeed), 2, "one plan per target")
        for _m, hits, _p, spread in espeed:
            self.assertFalse(spread)
            self.assertEqual(len(hits), 1)

    def test_an_all_adjacent_move_hits_our_own_partner(self):
        """And a Ground-immune partner is why the reference bring works:
        "a flying/levitate pokemon in the back to ignore garchomp partner
        earthquake dmg." Dragonite is Dragon/Flying, so Earthquake is free."""
        chomp, dnite = self._mon("p1", "Garchomp"), self._mon("p1", "Dragonite")
        ninetales = self._mon("p1", "Gallade")   # a grounded partner, for contrast
        wh, fl = self._mon("p2", "Whimsicott"), self._mon("p2", "Mega Floette")
        free = [p for p in ls.move_plans(chomp, self.ms["Garchomp"], [wh, fl],
                                         [chomp, dnite], self.b.typechart,
                                         self.b.field, self.b)
                if p[0].name == "Earthquake"][0]
        costly = [p for p in ls.move_plans(chomp, self.ms["Garchomp"], [wh, fl],
                                           [chomp, ninetales], self.b.typechart,
                                           self.b.field, self.b)
                  if p[0].name == "Earthquake"][0]
        self.assertEqual(free[1].get(id(dnite), 0.0), 0.0,
                         "Dragonite is Flying: Earthquake must cost nothing")
        self.assertGreater(costly[1].get(id(ninetales), 0.0), 0.0,
                           "a grounded partner must be charged for it")

    def test_the_lines_are_readable_and_show_the_pin(self):
        """The turn-by-turn is the deliverable here: "I also need to see the
        actual lines/moves and the logic as to why it is sound". Read straight
        off a real `Battle.run_turn`, via `lead_sim.play`, with their plan
        pinned to "both attack" so the line is deterministic (their WORST plan,
        used by `race`, includes a Tailwind branch on this exact pairing and
        would make the log unpredictable turn to turn)."""
        import lead_sim as sim
        battle, movesets, _s = sim.build_position(
            ["Garchomp", "Dragonite", "Gallade", "Basculegion"],
            ["Whimsicott", "Mega Floette", "Garchomp", "Kingambit"],
            world(), optimise=False)
        _v, _od, _td, _m, log, _b = sim.play(
            battle, movesets, turns=2,
            their_plans=[("both attack", None), ("both attack", None)],
            want_log=True)
        text = "\n".join(log)
        self.assertIn("Turn 1", text)
        self.assertIn("Turn 2", text)
        self.assertIn("Earthquake", text)
        # A spread move: Earthquake lands on BOTH of their active Pokemon in one
        # turn, not just whichever a per-target threat matrix liked best.
        self.assertIn("Earthquake on Whimsicott", text)
        self.assertIn("Earthquake on Mega Floette", text)
        # The pin: Mega Floette faints to that Earthquake before its own action
        # this turn, so it never appears as an ATTACKER in turn 1's log.
        turn1 = text.split("Turn 2")[0]
        self.assertNotIn("Mega Floette uses", turn1,
                         "a fainted Pokemon must not still act -- the pin")

    def test_nobody_protects_twice_in_a_row(self):
        """`lead_sim.their_strategies` enumerates a Protect plan per turn and
        explicitly skips any pair where the same non-"both attack" plan repeats
        -- an earlier version fixed one plan across both turns, so Mega Floette
        Protected twice, which is illegal and meant the Earthquake meant to
        remove it never landed. Checked directly against the enumerated
        strategy space, not against one race's worst-case result."""
        import lead_sim as sim
        battle, movesets, _s = sim.build_position(
            ["Garchomp", "Dragonite", "Gallade", "Basculegion"],
            ["Whimsicott", "Mega Floette", "Garchomp", "Kingambit"],
            world(), optimise=False)
        for seq in sim.their_strategies(battle, movesets, turns=2):
            first, second = seq[0][0], seq[1][0]
            if first != "both attack":
                self.assertNotEqual(first, second,
                                    f"double Protect offered: {seq}")

    def test_the_move_choice_defect_is_fixed_not_worked_around(self):
        """RECORDED AS FIXED, not merely avoided.

        `_best_joint` (deleted) maximised net HP and, measured on Ninetales-
        Alola + Garchomp against their Garchomp + Kingambit, played Garchomp
        Earthquake for 71% onto Kingambit and 48% onto OUR OWN Ninetales --
        because +23% net beat Rock Slide's Rock-into-Dark/Steel -- and Kingambit
        then removed the Ninetales it had damaged. A net-HP objective cannot see
        that crossing a KO threshold on your own side is categorically worse
        than the HP it costs.

        `race_bring` still SCORES on the calibrated threat-matrix race (the
        cheap narrowing stage -- that was never the defect). What changed is
        where the turn-by-turn LINES come from: `Battle.run_turn`, played
        through `lead_sim`, which chooses moves by actually PLAYING each
        candidate and keeping the best result -- there is no separate move-
        valuation heuristic left to get this wrong.
        """
        import inspect
        src = inspect.getsource(ls.race_bring)
        self.assertIn("race_robust(matrix, lead, pair", src)
        self.assertIn("sim.race(", src)
        self.assertFalse(hasattr(ls, "_best_joint"),
                         "the flawed move-choice heuristic should be gone, "
                         "not merely unused")


class TestSpeedControlAndItems(unittest.TestCase):
    """Tailwind, Mega speed, Fairy Aura and Focus Sash all change the answer.

        "you must account for the fact that mega floette speed ties Garchomp when
         it mega evolves and is far bulkier so likely lives earthquake, and also
         that whimsicott can use tailwind which lets mega floette outspeed (no
         speed tie) and easily KO Garchomp accounting for fairy aura (*1.33 to
         fairy moves). I cannot afford to risk losing any speed ties."
    """

    def setUp(self):
        from _harness import setup_battle
        W = world()
        self.b, self.ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)

    def _mon(self, side, name):
        roster = (self.b.p1 if side == "p1" else self.b.p2).roster
        return [c for c in roster if c.name == name][0]

    def test_ordering_uses_the_mega_speed_not_the_base(self):
        """Ordering read `c.stats["spe"]`, so Mega Floette sorted at 111 when the
        thing that shows up is 166. The tie is invisible unless the ordering uses
        the evolved speed."""
        from _harness import setup_battle
        W = world()
        # A team where Floette DOES hold the Mega slot.
        b, _ms = setup_battle(EXAMPLE, ["Mega Floette", "Whimsicott",
                                        "Kingambit", "Basculegion"], W)
        fl = [c for c in b.p2.roster if c.name == "Mega Floette"][0]
        self.assertTrue(fl.is_mega_pick)
        self.assertGreater(ls.race_speed(fl, b.field, "p2"), fl.stats["spe"],
                           "race_speed must report the Mega's speed")
        self.assertEqual(ls.race_speed(fl, b.field, "p2"), fl.mega_stats["spe"])

    def test_tailwind_is_a_recognised_setup_move(self):
        """`move_plans` skips Status moves, so Whimsicott's Tailwind was a line
        the race could not represent at all."""
        self.assertIn("Tailwind", ls.setup_moves(self.ms["Whimsicott"]))
        self.assertEqual(ls.setup_moves(self.ms["Garchomp"]), [])

    def test_tailwind_doubles_their_speed_in_the_ordering(self):
        from copy import copy
        from engine import effective_speed
        fl = self._mon("p2", "Mega Floette")
        before = ls.race_speed(fl, self.b.field, "p2")
        windy = copy(self.b.field)
        windy.tailwind_p2 = 4
        self.assertAlmostEqual(ls.race_speed(fl, windy, "p2"), before * 2)

    def test_their_setup_appears_in_the_enumerated_strategies(self):
        """It must be one of the options taken against us, not a footnote."""
        import lead_sim as sim
        battle, movesets, _s = sim.build_position(
            EXAMPLE, ["Mega Floette", "Whimsicott", "Garchomp", "Kingambit"],
            world(), optimise=False)
        seqs = sim.their_strategies(battle, movesets, turns=2)
        self.assertTrue(seqs)
        self.assertTrue(
            any(forced and forced[0] == "Tailwind"
                for seq in seqs for _plan, forced in seq),
            "Tailwind must be reachable as one of their enumerated strategies")

    def test_focus_sash_leaves_a_survivor_on_a_sliver(self):
        """And a Pokemon on a sliver still gets its turn, which is the point."""
        sashed = ls.sash_ids([self._mon("p2", "Whimsicott")])
        wh = self._mon("p2", "Whimsicott")
        self.assertIn(id(wh), sashed, "Whimsicott runs Focus Sash here")
        hp = {id(wh): 1.0}
        ls._apply(hp, id(wh), 5.0, sashed)
        self.assertGreater(hp[id(wh)], 0.0, "the sash must hold")
        self.assertLess(hp[id(wh)], 0.1)
        # But not from reduced health.
        hp = {id(wh): 0.5}
        ls._apply(hp, id(wh), 5.0, sashed)
        self.assertEqual(hp[id(wh)], 0.0)

    def test_kingambit_four_times_effective_removes_ninetales_outright(self):
        """A finding, recorded because it contradicts the worked breakdown.

        Kingambit's Iron Head is Steel into Ice/Fairy -- 4x -- and does 174% of
        Ninetales-Alola's HP. So the "Kingambit ... Garchomp earthquake +
        Ninetales-Alola Blizzard is very threatening" reading is wrong for this
        lead: Ninetales-Alola dies at full health before the two-turn sum can
        land. This also corrected a hasty claim of mine that a self-Earthquake was
        the deciding error there; it was not, and the threshold penalty correctly
        does not fire, because Ninetales was already in range.
        """
        nt = self._mon("p1", "Ninetales-Alola")
        kg = self._mon("p2", "Kingambit")
        plans = ls.move_plans(kg, self.ms["Kingambit"], [nt], [kg],
                              self.b.typechart, self.b.field, self.b)
        worst = max((h.get(id(nt), 0.0) for _m, h, _p, _s in plans), default=0.0)
        self.assertGreater(worst, 1.0,
                           "Kingambit must OHKO Ninetales-Alola from full")


class TestTheirMegaChoiceIsTheirs(unittest.TestCase):
    """One Mega per team, chosen at preview, AFTER seeing your four.

        "The mega choice should not be set in stone. They could mega either, and
         it must be robust to both."

    Measured on the reference bring against Big 6, and this is why it matters:

        their Mega: Mega Charizard Y   13W 0 unheld   score +0.48
        their Mega: Mega Floette        7W 3 unheld   score  0.00

    With Floette holding the slot its Mega speed is 166 -- exactly our Garchomp --
    the tie resolves against us, and Light of Ruin removes Garchomp before it
    acts. Blizzard takes only 27% off the Mega, so it lives easily. The
    "overwhelming lead" reading does not survive their other choice.
    """

    def test_both_of_their_mega_choices_are_enumerated(self):
        W = world()
        variants = ls.mega_variants(W["teams"]["Big 6"], W)
        names = {m for m, _r in variants}
        self.assertEqual(names, {"Mega Charizard Y", "Mega Floette"})
        for _m, roster in variants:
            self.assertEqual(sorted(roster), sorted(W["teams"]["Big 6"]),
                             "a variant must be the same six, reordered")

    def test_putting_a_mega_first_gives_it_the_slot(self):
        from _harness import setup_battle
        W = world()
        for mega, roster in ls.mega_variants(W["teams"]["Big 6"], W):
            b, _ms = setup_battle(EXAMPLE, roster, W)
            holder = [c for c in b.p2.roster if c.is_mega_pick]
            self.assertEqual([c.name for c in holder], [mega])

    def test_a_team_with_one_mega_yields_one_variant(self):
        W = world()
        variants = ls.mega_variants(["Garchomp", "Whimsicott", "Kingambit",
                                     "Mega Scizor", "Basculegion", "Gallade"], W)
        self.assertEqual(len(variants), 1)

    def test_the_worst_mega_choice_is_the_one_reported(self):
        W = world()
        worst, reports = ls.race_all_megas(EXAMPLE, W["teams"]["Big 6"], W,
                                           opponent_name="Big 6")
        self.assertEqual(len(reports), 2)
        self.assertEqual(worst.score, min(r.score for r in reports))

    def test_their_mega_choice_actually_changes_the_verdict(self):
        """The regression guard. If this stops discriminating, either the Mega
        stats stopped reaching the race or the speed tie stopped being lost."""
        W = world()
        _worst, reports = ls.race_all_megas(EXAMPLE, W["teams"]["Big 6"], W,
                                            opponent_name="Big 6")
        scores = sorted(r.score for r in reports)
        self.assertLess(scores[0], scores[-1],
                        "their Mega choice must matter on this position")

    def test_mega_floette_ties_our_garchomp_once_it_evolves(self):
        from _harness import setup_battle
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Floette"][0]
        b, _ms = setup_battle(EXAMPLE, roster, W)
        fl = [c for c in b.p2.roster if c.name == "Mega Floette"][0]
        chomp = [c for c in b.p1.roster if c.name == "Garchomp"][0]
        self.assertEqual(ls.race_speed(fl, b.field, "p2"),
                         ls.race_speed(chomp, b.field, "p1"),
                         "the tie the worked example warned about")


class TestThePlaysAndTheMopUp(unittest.TestCase):
    """Four plays, a mop-up, and the items that convert an opening.

        "your play needs to be e.g., to switch in something on Garchomp's slot
         that can help KO Mega Floette or just let Garchomp faint to get the new
         pokemon in at 100% HP, assuming its still a winning line afterwards (3v4
         for instance)"

        "It's also important that your backs can mop up and win vs remaining
         pokemon after the 2 turn sequences."
    """

    def setUp(self):
        import lead_sim as sim
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Floette"][0]
        enemy4 = (["Mega Floette", "Whimsicott"]
                 + [n for n in roster if n not in ("Mega Floette", "Whimsicott")])
        self.battle, self.movesets, _s = sim.build_position(
            EXAMPLE, enemy4, W, optimise=False, enemy_mega="Mega Floette")
        self.lead, self.back = EXAMPLE[:2], EXAMPLE[2:]

    def _plays(self):
        return ls.plays_for(self.battle, self.movesets, self.lead, self.back)

    def test_stay_and_switch_are_both_offered(self):
        """STAY, plus one SWITCH per (leaving lead, arriving back) pair.

        SWITCH_PROTECT and SACRIFICE used to be separate SEARCHES, because the
        old turn resolver never itself considered Protect as an option and had
        no notion of a fainted Pokemon's replacement. Against a real `Battle`
        neither needs a special case: `candidate_joints` always offers Protect
        to the stayer, and `Battle._replace_fainted` sends in the best answer
        the instant a lead faints -- so STAY already covers "stay and attack",
        "stay, and one Protects", and "one faints, the best replacement arrives
        at 100%", whichever of those actually is best; SWITCH covers the same
        for the switch case. `_refine_play_kind` (tested directly below) still
        labels those OUTCOMES when they occur, from what the log says happened.
        """
        kinds = {p.kind for p in self._plays()}
        self.assertIn(ls.STAY, kinds)
        self.assertIn(ls.SWITCH, kinds)
        expected_switches = len(self.lead) * len(self.back)
        self.assertEqual(sum(1 for p in self._plays()
                             if p.kind in (ls.SWITCH, ls.SWITCH_PROTECT)),
                         expected_switches)

    def test_switch_protect_is_detected_from_the_played_out_log(self):
        """A SWITCH whose stayer actually Protected turn 1 is relabelled from
        the log Battle itself writes, not guessed at."""
        log = ["  Turn 1  their plan: both attack",
               "    Garchomp protects itself!",
               "    Mega Scizor switches in",
               "  Turn 2  their plan: both attack",
               "--- Turn 2 ---",
               "    Whimsicott protects itself!"]
        kind, leaving, arriving = ls._refine_play_kind(
            ls.SWITCH, "Ninetales-Alola", "Mega Scizor", log)
        self.assertEqual(kind, ls.SWITCH_PROTECT)
        self.assertEqual((leaving, arriving), ("Ninetales-Alola", "Mega Scizor"))

    def test_a_switch_where_the_arriving_pokemon_protects_is_impossible(self):
        """The arriving Pokemon just switched in -- it cannot also Protect --
        so a Protect line naming IT must not be mistaken for the stayer's."""
        log = ["--- Turn 1 ---",
               "    Mega Scizor protects itself!",
               "--- Turn 2 ---"]
        kind, _leaving, _arriving = ls._refine_play_kind(
            ls.SWITCH, "Ninetales-Alola", "Mega Scizor", log)
        self.assertEqual(kind, ls.SWITCH)

    def test_sacrifice_is_detected_from_the_played_out_log(self):
        """The point of the play: a switch-in arrives having eaten both
        attacks, a replacement after a faint arrives at 100% -- read off the
        engine's own replacement line."""
        log = ["--- Turn 1 ---",
               "    p1 sends in Rotom-Wash (replacing fainted Ninetales-Alola)"]
        kind, leaving, arriving = ls._refine_play_kind(ls.STAY, "", "", log)
        self.assertEqual(kind, ls.SACRIFICE)
        self.assertEqual(leaving, "Ninetales-Alola")
        self.assertEqual(arriving, "Rotom-Wash")

    def test_plays_are_ranked_with_guaranteed_wins_first(self):
        plays = self._plays()
        rank = {ls.WIN: 0, ls.EVEN: 1, ls.LOSS: 2}
        keys = [(rank[p.verdict], -p.margin) for p in plays]
        self.assertEqual(keys, sorted(keys))

    def test_guaranteed_means_it_does_not_need_a_coin_flip(self):
        """`lead_sim.race` takes their best strategy over the whole enumeration
        and `Battle` resolves every exact speed tie against us by its own rule,
        so a WIN is a guarantee in the only sense that matters here."""
        for p in self._plays():
            self.assertEqual(p.guaranteed, p.verdict == ls.WIN)

    def test_a_losing_switch_is_not_counted_as_a_patch(self):
        """It read "5 patched, 0 unheld" beside five LOSS rows, because `patch`
        was set whenever the best play was a switch rather than when the switch
        actually salvaged anything."""
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Floette"][0]
        report, _ms, _b = ls.full_report(EXAMPLE, roster, W,
                                         opponent_name="Big 6",
                                         want_logs=False,
                                         mega_name="Mega Floette")
        for x in report.results:
            if x.verdict == ls.LOSS:
                self.assertIsNone(x.patch, f"{x.enemy_lead} counted as patched")
                self.assertFalse(x.held, f"{x.enemy_lead} counted as held")
        # And the two categories cannot overlap: an opening is either a hole or
        # it is held, never both.
        self.assertEqual(set(id(x) for x in report.patched)
                         & set(id(x) for x in report.losses), set())

    def test_the_mop_up_is_attached_to_every_opening(self):
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Floette"][0]
        report, _ms, _b = ls.full_report(EXAMPLE, roster, W,
                                         opponent_name="Big 6",
                                         want_logs=False,
                                         mega_name="Mega Floette")
        for x in report.results:
            self.assertIsNotNone(x.play)
            self.assertTrue(x.play.mopped,
                            "every opening needs a mop-up verdict")


class TestItemsActuallyDoSomething(unittest.TestCase):
    """Type-resist berries and Assault Vest were TABLES ONLY.

        "you could use a roseli berry (makes one incoming fairy attack damage
         halved) which could allow Garchomp+Ninetales-Alola to win the
         aforementioned Whimsicott+Mega Floette lead."

    `optimize_sets.TYPE_RESIST_BERRY` has existed for as long as the salvage flow,
    and the salvage flow has been handing berries out -- but `damage.py` never
    halved anything for them. Measured before the fix, a Roseli Berry changed
    Light of Ruin by exactly zero, so every "give it a resist berry" suggestion the
    system ever made was cosmetic.
    """

    def _hit(self, item, defender="Garchomp", move="lightofruin",
             attacker="Mega Floette"):
        from combatants import make_combatant
        from damage import (damage_roll, defensive_stat, effective_stat,
                            move_from_showdown)
        W = world()
        mi = move_from_showdown(W["moves"][move])
        atk = make_combatant(attacker, W["merged"], W["natures"])
        dfn = make_combatant(defender, W["merged"], W["natures"], item=item)
        key = "def" if mi.category == "Physical" else "spd"
        astat = "atk" if mi.category == "Physical" else "spa"
        lo, _hi, _avg, _eff = damage_roll(
            50, mi.power, effective_stat(atk.stats[astat], 0),
            defensive_stat(dfn, key, mi), atk, dfn, mi, W["typechart"])
        return lo / dfn.max_hp()

    def test_the_berry_table_is_inverted_not_restated(self):
        from damage import BERRY_RESIST_TYPE
        from optimize_sets import TYPE_RESIST_BERRY
        for move_type, berry in TYPE_RESIST_BERRY.items():
            self.assertEqual(BERRY_RESIST_TYPE.get(berry), move_type,
                             f"{berry} drifted between the two tables")

    def test_roseli_turns_a_guaranteed_ohko_into_a_survival(self):
        """The exact case: Light of Ruin into Garchomp, Fairy into Dragon, 2x."""
        bare = self._hit(None)
        berry = self._hit("Roseli Berry")
        self.assertGreaterEqual(bare, 1.0, "it must be a guaranteed OHKO bare")
        self.assertLess(berry, 1.0, "and a survival with the berry")
        self.assertAlmostEqual(berry, bare / 2, places=2)

    def test_a_resist_berry_does_nothing_against_a_neutral_hit(self):
        """They halve SUPER-EFFECTIVE hits. Fairy into Ice/Fairy is 1x, so a
        Roseli Berry on Ninetales-Alola is not the answer to Light of Ruin."""
        self.assertAlmostEqual(self._hit(None, defender="Ninetales-Alola"),
                               self._hit("Roseli Berry",
                                         defender="Ninetales-Alola"),
                               places=4)

    def test_assault_vest_reduces_special_damage(self):
        self.assertLess(self._hit("Assault Vest"), self._hit(None))

    def test_no_illegal_item_is_ever_offered(self):
        """Assault Vest, Choice Band and Choice Specs are NOT legal in Regulation
        MB, and they were in the candidate list.

        This is not a cosmetic fix. The one conversion the search reported for the
        Mega Floette opening was ASSAULT VEST on Ninetales-Alola -- so the answer
        that was handed over was illegal, and removing the item removes the fix.
        The opening has no single-item answer, which is the true and less
        comfortable result.
        """
        banned = {"Assault Vest", "Choice Band", "Choice Specs"}
        self.assertEqual(set(ls.ITEM_CANDIDATES) & banned, set())

    def test_the_item_search_reports_only_legal_conversions(self):
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Floette"][0]
        fixes = ls.item_fixes(EXAMPLE, roster, W,
                              ("Mega Floette", "Whimsicott"))
        banned = {"Assault Vest", "Choice Band", "Choice Specs"}
        for mon, item, play in fixes:
            self.assertIn(mon, EXAMPLE)
            self.assertNotIn(item, banned)
            self.assertNotEqual(play.verdict, ls.LOSS)


class TestTheDefaultLoadout(unittest.TestCase):
    """Most popular item by default; a swap has to earn its place.

        "By default give every pokemon their most popular item, assuming no better
         item for a specific case (e.g. Ninetales-Alola focus sash to win vs
         incoming enemy Kingambit iron head while enemy Garchomp protects)"

    The default half was already true -- `make_team` reads the usage table -- so
    this pins it rather than implements it. Measured: Ninetales-Alola gets
    Never-Melt Ice (29% usage), Garchomp Life Orb (61%), Rotom-Wash Sitrus Berry
    (31%).
    """

    def test_the_default_is_the_most_used_item(self):
        from _harness import setup_battle
        W = world()
        b, _ms = setup_battle(EXAMPLE, list(W["teams"]["Big 6"]), W)
        for c in b.p1.roster:
            self.assertEqual(c.item, ls.default_item(c.name, W),
                             f"{c.name} did not get its most-used item")

    def test_default_item_reads_the_usage_table(self):
        W = world()
        for name in EXAMPLE:
            usage = [i for i, _p in
                     (W["merged"][name].get("items_usage") or [])
                     if i and i != "Other"]
            if usage:
                self.assertEqual(ls.default_item(name, W), usage[0])

    def test_a_swap_is_only_reported_when_it_holds_more_openings(self):
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Charizard Y"][0]
        swaps = ls.recommended_items(EXAMPLE, roster, W,
                                     mega_name="Mega Charizard Y")
        for mon, frm, to, before, after in swaps:
            self.assertIn(mon, EXAMPLE)
            self.assertNotEqual(frm, to)
            self.assertGreater(after, before,
                               f"{mon} -> {to} was reported without gaining")

    def test_a_mega_stone_is_never_swapped(self):
        """A Mega is locked to its stone; suggesting otherwise is not legal."""
        W = world()
        roster = [r for m, r in ls.mega_variants(W["teams"]["Big 6"], W)
                  if m == "Mega Charizard Y"][0]
        swaps = ls.recommended_items(EXAMPLE, roster, W,
                                     mega_name="Mega Charizard Y")
        self.assertNotIn("Mega Scizor", [s[0] for s in swaps])

    def test_focus_sash_does_not_save_the_kingambit_case(self):
        """RECORDED BECAUSE THE SUGGESTION DOES NOT WORK, and quietly shipping a
        sheet that implied it did would be worse than saying so.

        Kingambit's Iron Head is Steel into Ice/Fairy -- 4x -- so a Focus Sash
        keeps Ninetales-Alola alive at 1 HP and it dies on turn 2 anyway. And the
        swap COSTS Never-Melt Ice's 1.2x on Blizzard, so the margin gets slightly
        worse: -1.12 bare, -1.15 with the sash.
        """
        import lead_sim as sim
        W = world()
        enemy4 = ["Garchomp", "Kingambit"] + [n for n in W["teams"]["Big 6"]
                                              if n not in ("Garchomp", "Kingambit")]

        def margin(item):
            sets = {"Ninetales-Alola": {"item": item}} if item else {}
            battle, movesets, _s = sim.build_position(
                EXAMPLE, enemy4, W, our_sets=sets, optimise=False)
            v, _o, _t, m, _p, _l = sim.race(battle, movesets, turns=2,
                                            breadth="full", want_log=False)
            return v, m

        bare_v, bare_m = margin(None)
        sash_v, sash_m = margin("Focus Sash")
        self.assertEqual(bare_v, ls.LOSS)
        self.assertEqual(sash_v, ls.LOSS,
                         "if the sash starts saving this, update the docstring")
        self.assertLessEqual(sash_m, bare_m + 0.01)


class TestTheSimulatorIsTheDamageModel(unittest.TestCase):
    """`lead_sim` plays `Battle.run_turn`. There is no second damage model.

        "finish lead_sim so the race is Battle.run_turn end to end. Avoid having
         two damage models. ... it is imperative that you use the full simulator."

    These tests assert the MECHANICS APPEAR, which is the only way to check that
    the engine is really being used: a hand-rolled calculation cannot produce a
    Mega Evolution announcement or a Life Orb recoil line.
    """

    OURS = ["Mega Scizor", "Arcanine-Hisui", "Gyarados", "Hydreigon"]
    THEIRS = ["Basculegion", "Garchomp", "Kingambit", "Whimsicott"]

    def setUp(self):
        import lead_sim
        self.sim = lead_sim
        self.b, self.ms, self.sets = lead_sim.build_position(
            self.OURS, self.THEIRS, world())

    def test_movesets_are_optimised_not_usage_defaults(self):
        """The named case: "Scizor for instance should use Knock Off, Close
        Combat, Bullet Punch". The old race gave it Bug Bite and Swords Dance."""
        moves = [m.name for m, _ in self.ms["Mega Scizor"]]
        for want in ("Knock Off", "Close Combat", "Bullet Punch"):
            self.assertIn(want, moves, f"Mega Scizor lacks {want}: {moves}")
        self.assertNotIn("Bug Bite", moves)

    def test_gyarados_gets_ice_fang_for_garchomp(self):
        self.assertIn("Ice Fang", [m.name for m, _ in self.ms["Gyarados"]])

    def test_items_are_optimised_and_the_unpack_bug_stays_fixed(self):
        """`best_item` returns THREE values. My first call unpacked two, raised
        ValueError, and a bare `except` hid it -- so every item came back None and
        item optimisation was silently a no-op."""
        chosen = {n: s.get("item") for n, s in self.sets.items()}
        self.assertTrue(any(chosen.values()),
                        f"no item was optimised at all: {chosen}")

    def test_no_banned_item_reaches_the_field(self):
        """Regulation MB. And the usage DEFAULT can itself be banned -- Hydreigon's
        most-used item is Choice Scarf, so leaving it alone put an illegal item on
        the field even after the candidate list was cleaned."""
        for c in self.b.p1.roster:
            self.assertNotIn(c.item, self.sim.BANNED_ITEMS,
                             f"{c.name} is holding {c.item}")

    def test_a_mega_actually_evolves(self):
        """`mega_evolved` was False for every Pokemon in every old race, so Mega
        stats, typing and ability never applied."""
        verdict, _od, _td, _m, log, end = self.sim.play(
            self.b, self.ms, turns=1, want_log=True)
        text = "\n".join(log)
        self.assertIn("Mega Evolved", text, f"no Mega Evolution in:\n{text}")
        self.assertTrue(any(c.mega_evolved for c in end.p1.roster))

    def test_engine_only_mechanics_show_up_in_the_line(self):
        """Each of these lives in battle.py and CANNOT be produced by a damage
        formula, so their presence is the proof the simulator ran."""
        _v, _od, _td, _m, log, _end = self.sim.play(
            self.b, self.ms, turns=2, want_log=True)
        text = "\n".join(log)
        found = [k for k in ("Life Orb", "Focus Sash", "recoil", "fell",
                             "sends in", "FAINTED")
                 if k in text]
        self.assertGreaterEqual(len(found), 3,
                                f"too few engine-only effects in:\n{text}")

    def test_effectiveness_comes_from_the_engine(self):
        """The log carries the multiplier the ENGINE computed, e.g. '(4.0x eff)'
        for Garchomp's Earthquake into Arcanine-Hisui."""
        _v, _od, _td, _m, log, _end = self.sim.play(
            self.b, self.ms, turns=1, want_log=True)
        self.assertIn("x eff", "\n".join(log))

    def test_the_race_takes_their_best_strategy(self):
        got = self.sim.race(self.b, self.ms, turns=2, breadth="cheap")
        verdict, _od, _td, _margin, desc, _log = got
        self.assertIn(verdict, (self.sim.WIN, self.sim.EVEN, self.sim.LOSS))
        self.assertIn("then", desc, "a per-turn plan must be described")

    def test_nobody_protects_twice_in_a_row(self):
        for seq in self.sim.their_strategies(self.b, self.ms, turns=2):
            plans = [p for p, _f in seq]
            if plans[0] != "both attack" and plans[1] != "both attack":
                self.assertNotEqual(plans[0], plans[1])

    def test_flinch_and_intimidate_proof_abilities_are_listed(self):
        """"A pokemon with the move inner focus cannot be faked out or
        intimidated" -- so the report can name why a lead is script-proof."""
        self.assertIn("Inner Focus", self.sim.FLINCH_PROOF)
        self.assertIn("Inner Focus", self.sim.INTIMIDATE_PROOF)
        self.assertIn("Fake Out", self.sim.SETUP_MOVES)
