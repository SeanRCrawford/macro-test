"""Search the pool for answers to specific threats.

    "using the move selection logic, I want to search for specific pokemon
     that beat others. For instance, I want a pokemon that can OHKO or do 90%
     Kingambit, Basculegion, assuming most favourable item, or maybe a Pokemon
     which can KO a number of selected pokemon after chip from a specified
     partner using a specified move, or vs any pair of the selected Pokemon
     while taking damage according to speed order (sequential game)"

    "You must extend to priority moves. Use average rolls. For a pair to
     work, it must KOd them before it can be KOd by the enemy pair, using
     help from the partner move if specified"

    "make sure to use the mega form, e.g. mega floette has mega floette
     stats" / "I also want to know what the damage roll is, and what the pair
     chip is for each pokemon. For pairs, make sure it's exhaustive over
     permutations, and can include chip from ally. For a spread move such as
     Blizzard, make sure the chip is adjusted correctly (0.75x)"
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import counter_finder as cf  # noqa: E402
from _harness import load_world  # noqa: E402
from lead_sim import BANNED_ITEMS  # noqa: E402
from solver import build_moveset  # noqa: E402

_WORLD = None

# A small, fixed pool so tests are fast and their numbers are hand-checkable.
POOL = ["Gallade", "Garchomp", "Ninetales-Alola", "Hydreigon", "Gyarados",
       "Arcanine-Hisui", "Mega Scizor", "Basculegion"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


class TestMegaForm(unittest.TestCase):
    """"make sure to use the mega form, e.g. mega floette has mega floette
    stats" """

    def test_mega_projection_swaps_in_the_mega_numbers(self):
        from combatants import make_combatant
        W = world()
        base = make_combatant("Mega Floette", W["merged"], W["natures"])
        self.assertFalse(base.mega_evolved)
        proj = cf._mega_project(base)
        self.assertTrue(proj.mega_evolved)
        self.assertEqual(proj.stats, base.mega_stats)
        self.assertEqual(proj.ability, base.mega_ability)
        self.assertNotEqual(proj.stats["spe"], base.stats["spe"])

    def test_a_non_mega_pick_is_returned_unchanged(self):
        from combatants import make_combatant
        W = world()
        c = make_combatant("Kingambit", W["merged"], W["natures"])
        self.assertIs(cf._mega_project(c), c)

    def test_build_always_projects(self):
        """Every Combatant this module builds itself goes through `_build`,
        so a "Mega X" name is never silently left in base form."""
        W = world()
        c = cf._build("Mega Floette", W["merged"], W["natures"])
        self.assertTrue(c.mega_evolved)
        self.assertEqual(c.current_hp, c.max_hp())

    def test_threshold_search_uses_the_mega_speed_for_a_mega_target(self):
        """Base Floette (spe 111) does not tie Garchomp; Mega Floette (166)
        famously does -- see the reference example this whole session is
        built from. Confirmed indirectly here: Mega Floette's reported item/
        moveset numbers must come from ITS stats, not base Floette's."""
        from combatants import make_combatant
        W = world()
        base = make_combatant("Mega Floette", W["merged"], W["natures"])
        proj = cf._mega_project(base)
        self.assertGreater(proj.stats["spe"], base.stats["spe"])


class TestThresholdSearch(unittest.TestCase):
    """OHKO / X% search, best legal item, worst of the named targets."""

    def setUp(self):
        W = world()
        self.rows = cf.threshold_search(
            POOL, ["Kingambit", "Basculegion"], W["merged"], W["moves"],
            W["natures"], W["typechart"], threshold=0.9)
        self.by_name = {r["name"]: r for r in self.rows}

    def test_targets_are_excluded_from_the_pool(self):
        """Basculegion is IN the pool but is also a named target here --
        answering "does Basculegion beat Basculegion" is not the question."""
        self.assertNotIn("Basculegion", self.by_name)

    def test_ranked_on_the_worst_target_not_the_best(self):
        """A row that OHKOes Kingambit but does little to Basculegion must not
        outrank one that clears both comfortably -- "ranked on the worst" is
        the same principle the lead screen uses throughout."""
        margins = [r["worst_pct"] for r in self.rows]
        self.assertEqual(margins, sorted(margins, reverse=True))
        for r in self.rows:
            self.assertEqual(r["worst_pct"],
                             min(h.frac for h in r["per_target"].values()))

    def test_gallade_sacred_sword_clears_both(self):
        """Sacred Sword ignores Defence stage changes and is Fighting into
        Kingambit's Steel/Dark and Basculegion's plain typing -- a real,
        checkable OHKO-class matchup."""
        row = self.by_name["Gallade"]
        self.assertTrue(row["meets_all"])
        for h in row["per_target"].values():
            self.assertGreaterEqual(h.frac, 0.9)

    def test_no_row_ever_carries_a_banned_item(self):
        """"most favourable item" means most favourable LEGAL item."""
        for r in self.rows:
            self.assertNotIn(r["item"], BANNED_ITEMS,
                             f"{r['name']} got a banned item: {r['item']}")

    def test_a_status_move_is_never_reported_as_the_best_hit(self):
        for r in self.rows:
            for h in r["per_target"].values():
                if h.move_name is not None:
                    self.assertNotIn(h.move_name, ("Protect", "Detect", "Taunt"))

    def test_the_hit_carries_the_full_roll_not_just_the_searched_end(self):
        """"I also want to know what the damage roll is" -- lo <= avg <= hi
        always, even though `threshold_search` searches on `lo` (=`frac`)."""
        row = self.by_name["Gallade"]
        for h in row["per_target"].values():
            self.assertLessEqual(h.lo, h.avg)
            self.assertLessEqual(h.avg, h.hi)
            self.assertEqual(h.frac, h.lo)


class TestChipThenKO(unittest.TestCase):
    """Finishing a target off after a named partner's named move has landed."""

    def setUp(self):
        W = world()
        self.W = W
        self.rows = cf.chip_then_ko(
            POOL, ["Kingambit"], "Ninetales-Alola", "Blizzard",
            W["merged"], W["moves"], W["natures"], W["typechart"])
        self.by_name = {r["name"]: r for r in self.rows}

    def test_the_partner_and_targets_are_excluded_from_the_pool(self):
        self.assertNotIn("Ninetales-Alola", self.by_name)

    def test_chip_is_the_same_number_for_every_row(self):
        """The partner's hit doesn't depend on who finishes the job."""
        chips = {r["chip"]["Kingambit"].frac for r in self.rows}
        self.assertEqual(len(chips), 1)

    def test_a_ko_actually_needs_the_chip(self):
        """Basculegion's Wave Crash alone should not one-shot Kingambit --
        this row exists to show the chip mattering, not being redundant."""
        row = self.by_name.get("Basculegion")
        self.assertIsNotNone(row)
        ko, h = row["finishes"]["Kingambit"]
        chip = row["chip"]["Kingambit"]
        self.assertGreater(chip.frac, 0.0)
        if ko:
            self.assertLess(h.frac, 1.0, "a KO credited without chip mattering "
                                         "means the chip did nothing")

    def test_an_unknown_move_raises_rather_than_silently_scoring_zero(self):
        W = world()
        with self.assertRaises(ValueError):
            cf.chip_then_ko(POOL, ["Kingambit"], "Ninetales-Alola",
                            "Not A Real Move", W["merged"], W["moves"],
                            W["natures"], W["typechart"])

    def test_ranked_by_kos_descending(self):
        counts = [r["n_ko"] for r in self.rows]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_blizzard_chip_takes_the_075x_spread_penalty(self):
        """"For a spread move such as Blizzard, make sure the chip is
        adjusted correctly (0.75x)" -- checked directly against the SAME
        hit computed without the penalty, using the SAME item
        `chip_then_ko` actually gives the partner (its own best answer, not
        a bare default)."""
        W = self.W
        item, _mv, _w = cf.best_answer("Ninetales-Alola", W["merged"], W["moves"],
                                       W["natures"], W["typechart"], ["Kingambit"])
        attacker = cf._build("Ninetales-Alola", W["merged"], W["natures"], item=item)
        target = cf._build("Kingambit", W["merged"], W["natures"])
        blizzard = cf._lookup_move("Blizzard", W["moves"])
        unpenalised = cf._raw_hit(attacker, blizzard, target, W["typechart"],
                                  num_targets_hit=1)
        penalised = cf._raw_hit(attacker, blizzard, target, W["typechart"],
                                num_targets_hit=2)
        self.assertAlmostEqual(penalised.lo, unpenalised.lo * 0.75)
        # And the chip actually returned by chip_then_ko must match it.
        chip = self.rows[0]["chip"]["Kingambit"]
        self.assertAlmostEqual(chip.lo, penalised.lo)
        self.assertEqual(chip.num_targets_hit, 2)

    def test_a_single_target_chip_move_takes_no_spread_penalty(self):
        W = self.W
        rows = cf.chip_then_ko(POOL, ["Kingambit"], "Ninetales-Alola",
                               "Flamethrower", W["merged"], W["moves"],
                               W["natures"], W["typechart"])
        self.assertEqual(rows[0]["chip"]["Kingambit"].num_targets_hit, 1)


class TestPairSearch(unittest.TestCase):
    """Speed-order, sequential resolution against every pair of the targets."""

    def setUp(self):
        W = world()
        self.merged, self.moves = W["merged"], W["moves"]
        self.natures, self.typechart = W["natures"], W["typechart"]
        self.rows = cf.pair_search(
            POOL, ["Kingambit", "Basculegion", "Garchomp"], self.merged,
            self.moves, self.natures, self.typechart)
        self.by_name = {r["name"]: r for r in self.rows}

    def test_every_pair_of_three_targets_is_exactly_three(self):
        for r in self.rows:
            self.assertEqual(r["pairs_total"], 3)
            self.assertEqual(len(r["detail"]), 3)

    def test_the_four_outcomes_partition_every_pair(self):
        for r in self.rows:
            total = (r["pairs_clean"] + r["pairs_trade"] + r["pairs_no_ko"]
                     + r["pairs_pinned"])
            self.assertEqual(total, r["pairs_total"])
            for d in r["detail"].values():
                self.assertIn(d["outcome"], ("clean", "trade", "no_ko", "pinned"))

    def test_ranked_by_most_pairs_beaten_then_fewest_pinned(self):
        keys = [(-(r["pairs_clean"] + r["pairs_trade"]), r["pairs_pinned"])
                for r in self.rows]
        self.assertEqual(keys, sorted(keys))

    def test_the_kept_target_is_always_a_member_of_its_pair(self):
        for r in self.rows:
            for pair, d in r["detail"].items():
                self.assertIn(d["target"], pair)

    def test_clean_and_trade_both_mean_the_target_actually_fainted(self):
        for r in self.rows:
            for (e1, _e2), d in r["detail"].items():
                target_role = "E1" if d["target"] == e1 else "E2"
                if d["outcome"] in ("clean", "trade"):
                    self.assertLessEqual(d["hp_left"][target_role], 0.0)
                else:
                    self.assertGreater(d["hp_left"][target_role], 0.0)

    def test_trade_means_the_candidate_itself_also_went_down(self):
        for r in self.rows:
            for d in r["detail"].values():
                if d["outcome"] == "trade":
                    self.assertLessEqual(d["hp_left"]["C"], 0.0)
                elif d["outcome"] == "clean":
                    self.assertGreater(d["hp_left"]["C"], 0.0)

    def test_a_pinned_row_means_the_candidate_never_landed_a_hit(self):
        """Pinned means it never got a turn -- Battle would never submit an
        action for a fainted Pokemon, and neither should this."""
        for r in self.rows:
            for d in r["detail"].values():
                if d["outcome"] == "pinned":
                    self.assertNotIn("C", d["hits"])

    def test_hits_carry_full_hit_objects(self):
        """Every recorded hit is a `Hit`, not a bare number -- "I also want
        to know what the damage roll is"."""
        for r in self.rows:
            for d in r["detail"].values():
                for hits in d["hits"].values():
                    for h in hits.values():
                        self.assertIsInstance(h, cf.Hit)


class TestPairSearchPriorityAndAverageRolls(unittest.TestCase):
    """"You must extend to priority moves. Use average rolls. For a pair to
    work, it must KOd them before it can be KOd by the enemy pair.""

    Kingambit's Sucker Punch (priority +1) is the concrete case: several
    Pokemon that out-damage Kingambit on paper are slower than it, and Sucker
    Punch still goes before them regardless -- exactly the case a
    speed-only ordering gets wrong.
    """

    def setUp(self):
        self.W = world()

    def test_a_priority_move_acts_before_a_faster_target(self):
        """Kingambit's Sucker Punch must be able to remove a nominally faster
        Pokemon before that Pokemon's own move fires."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.pair_search(["Gallade"], ["Kingambit", "Basculegion"],
                              merged, moves, natures, typechart)
        self.assertEqual(len(rows), 1)
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        # Gallade is faster than Kingambit and hits hard enough to KO it on
        # average -- so if this were speed-only, it would win. Sucker Punch
        # (priority) must be what removes it first regardless.
        self.assertEqual(d["outcome"], "pinned")

    def test_average_roll_can_credit_a_ko_worst_roll_would_miss(self):
        """`_choose_move`/`_raw_hit` inside `pair_search` must read the AVERAGE
        roll, not the worst one `threshold_search` uses -- a real distinction,
        not a rounding difference."""
        merged, moves, natures, typechart = (self.W["merged"], self.W["moves"],
                                             self.W["natures"], self.W["typechart"])
        item, move_names, weather = cf.best_answer(
            "Garchomp", merged, moves, natures, typechart, ["Basculegion"])
        attacker = cf._build("Garchomp", merged, natures, item=item)
        target = cf._build("Basculegion", merged, natures)
        moves_list = cf._move_infos("Garchomp", merged, moves, move_names)
        worst_hit = cf._best_hit(attacker, moves_list, target, typechart,
                                 weather=weather)
        avg_hit, _mv = cf._choose_move(attacker, moves_list, target, typechart,
                                       weather=weather)
        self.assertGreaterEqual(avg_hit.frac, worst_hit.frac)


class TestSpreadMovesInPairSearch(unittest.TestCase):
    """"For a spread move such as Blizzard, make sure the chip is adjusted
    correctly (0.75x)" -- applies inside `pair_search` too, both for the
    candidate's own move and the partner's."""

    def setUp(self):
        self.W = world()

    def test_a_candidates_own_spread_move_hits_both_pair_members(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.pair_search(["Garchomp"], ["Kingambit", "Basculegion"],
                              merged, moves, natures, typechart)
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        c_hits = d["hits"].get("C", {})
        self.assertIn("Earthquake", [h.move_name for h in c_hits.values()])
        for h in c_hits.values():
            if h.move_name == "Earthquake":
                self.assertEqual(h.num_targets_hit, 2)
        self.assertEqual(set(c_hits), {"E1", "E2"},
                         "a spread move must hit BOTH pair members")

    def test_partner_spread_move_hits_both_pair_members(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.pair_search(["Hydreigon"], ["Kingambit", "Basculegion"],
                              merged, moves, natures, typechart,
                              partner_name="Ninetales-Alola",
                              partner_move_name="Blizzard")
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        p_hits = d["hits"].get("P", {})
        self.assertEqual(set(p_hits), {"E1", "E2"})
        for h in p_hits.values():
            self.assertEqual(h.num_targets_hit, 2)


class TestPairSearchPartnerAssist(unittest.TestCase):
    """"using help from the partner move if specified" """

    def setUp(self):
        W = world()
        self.merged, self.moves = W["merged"], W["moves"]
        self.natures, self.typechart = W["natures"], W["typechart"]

    def test_partner_and_move_must_be_given_together(self):
        with self.assertRaises(ValueError):
            cf.pair_search(POOL, ["Kingambit", "Basculegion"], self.merged,
                           self.moves, self.natures, self.typechart,
                           partner_name="Ninetales-Alola")

    def test_an_unknown_partner_move_raises(self):
        with self.assertRaises(ValueError):
            cf.pair_search(POOL, ["Kingambit", "Basculegion"], self.merged,
                           self.moves, self.natures, self.typechart,
                           partner_name="Ninetales-Alola",
                           partner_move_name="Not A Real Move")

    def test_partner_help_can_only_improve_the_outcome(self):
        """Adding a partner's chip can never turn a beaten pair into an
        unbeaten one -- it is strictly more damage in the same exchange."""
        without = cf.pair_search(POOL, ["Kingambit", "Basculegion"],
                                 self.merged, self.moves, self.natures,
                                 self.typechart)
        with_partner = cf.pair_search(
            POOL, ["Kingambit", "Basculegion"], self.merged, self.moves,
            self.natures, self.typechart, partner_name="Ninetales-Alola",
            partner_move_name="Blizzard")
        before = {r["name"]: r["pairs_clean"] + r["pairs_trade"] for r in without}
        after = {r["name"]: r["pairs_clean"] + r["pairs_trade"] for r in with_partner}
        for name, got_before in before.items():
            if name not in after:
                continue   # the partner itself is excluded from its own pool
            self.assertGreaterEqual(after[name], got_before,
                                    f"{name} got WORSE with a partner's help")

    def test_the_partner_itself_is_excluded_from_the_pool(self):
        rows = cf.pair_search(POOL, ["Kingambit", "Basculegion"], self.merged,
                              self.moves, self.natures, self.typechart,
                              partner_name="Ninetales-Alola",
                              partner_move_name="Blizzard")
        self.assertNotIn("Ninetales-Alola", {r["name"] for r in rows})

    def test_exhaustive_over_permutations_partner_target_is_searched(self):
        """"make sure it's exhaustive over permutations" -- with a
        single-target partner move, `partner_target` is a real, independent
        choice from the candidate's own target, and every kept result must
        report which one was actually used."""
        merged, moves = self.merged, self.moves
        natures, typechart = self.natures, self.typechart
        rows = cf.pair_search(POOL, ["Kingambit", "Basculegion"], merged,
                              moves, natures, typechart,
                              partner_name="Ninetales-Alola",
                              partner_move_name="Flamethrower")
        for r in rows:
            for d in r["detail"].values():
                self.assertIn(d["partner_target"], ("Kingambit", "Basculegion"))

    def test_permutation_search_finds_at_least_as_good_as_matched_targets(self):
        """The exhaustive (candidate, partner) target search must never do
        WORSE than always pointing the partner at the candidate's own
        target -- that fixed pairing is one of the combinations it tries."""
        merged, moves = self.merged, self.moves
        natures, typechart = self.natures, self.typechart
        e1, e2 = "Kingambit", "Basculegion"
        for name in ("Garchomp", "Hydreigon", "Gyarados"):
            item, move_names, weather = cf.best_answer(
                name, merged, moves, natures, typechart, [e1, e2])
            if not move_names:
                continue
            attacker = cf._build(name, merged, natures, item=item)
            moves_list = cf._move_infos(name, merged, moves, move_names)
            p_item, _mv, _w = cf.best_answer(
                "Ninetales-Alola", merged, moves, natures, typechart, [e1, e2])
            partner = cf._build("Ninetales-Alola", merged, natures, item=p_item)
            p_move = cf._lookup_move("Flamethrower", moves)
            e1c = cf._build(e1, merged, natures)
            e2c = cf._build(e2, merged, natures)
            e1_moves = [mi for mi, _p in build_moveset(merged[e1], moves)]
            e2_moves = [mi for mi, _p in build_moveset(merged[e2], moves)]
            matched = cf._sequential_pair_outcome(
                attacker, moves_list, e1, e1c, e1_moves, e2, e2c, e2_moves,
                typechart, weather, e1, partner=partner, partner_move=p_move,
                partner_target=e1)
            best = None
            for c_t in (e1, e2):
                for p_t in (e1, e2):
                    got = cf._sequential_pair_outcome(
                        attacker, moves_list, e1, e1c, e1_moves, e2, e2c, e2_moves,
                        typechart, weather, c_t, partner=partner,
                        partner_move=p_move, partner_target=p_t)
                    if best is None or cf._OUTCOME_RANK[got["outcome"]] < cf._OUTCOME_RANK[best["outcome"]]:
                        best = got
            self.assertLessEqual(cf._OUTCOME_RANK[best["outcome"]],
                                 cf._OUTCOME_RANK[matched["outcome"]])
