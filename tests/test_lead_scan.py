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
        actual lines/moves and the logic as to why it is sound"."""
        ours = [self._mon("p1", "Garchomp"), self._mon("p1", "Dragonite")]
        theirs = [self._mon("p2", "Whimsicott"), self._mon("p2", "Mega Floette")]
        _v, _od, _td, _m, _plan, log = ls.move_race(
            self.ms, self.b.typechart, self.b.field, self.b, ours, theirs,
            want_log=True)
        text = "\n".join(log)
        self.assertIn("Turn 1", text)
        self.assertIn("Turn 2", text)
        self.assertIn("(spread)", text)
        self.assertIn("Earthquake", text)
        self.assertIn("removed before it acted", text,
                      "the pin has to be visible in the line")

    def test_nobody_protects_twice_in_a_row(self):
        """The first version fixed one plan across both turns, so Mega Floette
        Protected twice -- illegal, and it meant the Earthquake that was supposed
        to remove it never landed."""
        ours = [self._mon("p1", "Garchomp"), self._mon("p1", "Dragonite")]
        theirs = [self._mon("p2", "Whimsicott"), self._mon("p2", "Mega Floette")]
        _v, _od, _td, _m, plan, log = ls.move_race(
            self.ms, self.b.typechart, self.b.field, self.b, ours, theirs,
            want_log=True)
        first, _sep, second = plan.partition(" then ")
        if first != "both attack":
            self.assertNotEqual(first, second, f"double Protect: {plan}")

    def test_the_move_choice_is_known_bad_and_is_not_the_scorer(self):
        """RECORDED AS A DEFECT, not asserted as correct.

        `_best_joint` maximises net HP, and measured on Ninetales-Alola +
        Garchomp against their Garchomp + Kingambit it plays Garchomp Earthquake
        -- 71% onto Kingambit and 48% onto OUR OWN Ninetales -- because +23% net
        beats Rock Slide's Rock-into-Dark/Steel. Kingambit then removes the
        Ninetales it damaged. A net-HP objective cannot see that crossing a KO
        threshold on your own side is categorically worse than the HP it costs.

        So `race_bring` scores on the calibrated threat-matrix race and uses
        `move_race` only for the turn-by-turn. This test pins that separation: if
        the scorer ever starts coming from move_race, this fails until the move
        choice is fixed.
        """
        import inspect
        src = inspect.getsource(ls.race_bring)
        self.assertIn("race_robust(matrix, lead, pair", src)
        self.assertIn("supplies the LINES", src)
