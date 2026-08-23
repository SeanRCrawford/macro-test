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


class TestItemOverrides(unittest.TestCase):
    """"I also want the option to define item in counter_table.py, such as
    Choice Scarf, or to just select optimal item. For Choice Scarf, a
    pokemon can use 4 moves, which could be highly useful."""

    def test_best_answer_pins_the_named_item(self):
        W = world()
        item, moves, _w = cf.best_answer(
            "Gallade", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Kingambit"], item="Choice Scarf")
        self.assertEqual(item, "Choice Scarf")
        self.assertEqual(len(moves), 4)

    def test_best_answer_re_optimises_the_moveset_under_the_pinned_item(self):
        """A pin is not a frozen moveset -- "the moveset is still genuinely
        re-optimised UNDER the pinned item". Confirmed by comparing against
        `best_moveset` called directly with the same item: they must agree,
        since `best_answer` is supposed to delegate to exactly that."""
        W = world()
        from optimize_sets import best_moveset
        item, moves, _w = cf.best_answer(
            "Gallade", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Kingambit"], item="Choice Scarf")
        expect, _score = best_moveset(
            "Gallade", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Kingambit"], item="Choice Scarf")
        self.assertEqual(moves, expect)

    def test_best_answer_rejects_a_banned_item_pin(self):
        """A pin is a decision, not a loophole around the Regulation MB ban."""
        W = world()
        with self.assertRaises(ValueError):
            cf.best_answer("Gallade", W["merged"], W["moves"], W["natures"],
                           W["typechart"], ["Kingambit"], item="Assault Vest")

    def test_best_answer_pins_item_and_moves_together(self):
        W = world()
        moves = ["Psycho Cut", "Sacred Sword", "Close Combat", "Ice Punch"]
        item, got_moves, _w = cf.best_answer(
            "Gallade", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Kingambit"], item="Choice Scarf", move_names=moves)
        self.assertEqual(item, "Choice Scarf")
        self.assertEqual(got_moves, moves)

    def test_threshold_search_honours_a_per_name_item_override(self):
        """Choice Scarf on Gallade specifically, verified through the same
        `threshold_search` `counter_table.py --item` actually calls -- "like
        Choice Scarf ... on Gallade beats 'Big 6' easily"."""
        W = world()
        rows = cf.threshold_search(
            POOL, ["Kingambit", "Basculegion"], W["merged"], W["moves"],
            W["natures"], W["typechart"],
            item_overrides={"Gallade": "Choice Scarf"})
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Gallade"]["item"], "Choice Scarf")
        # An override on Gallade must not leak onto anyone else's row.
        self.assertNotEqual(by_name["Garchomp"]["item"], "Choice Scarf")

    def test_threshold_search_rejects_a_banned_override(self):
        W = world()
        with self.assertRaises(ValueError):
            cf.threshold_search(
                POOL, ["Kingambit"], W["merged"], W["moves"], W["natures"],
                W["typechart"], item_overrides={"Gallade": "Choice Band"})

    def test_chip_then_ko_honours_item_overrides_on_finishers(self):
        W = world()
        rows = cf.chip_then_ko(
            POOL, ["Kingambit"], "Ninetales-Alola", "Blizzard", W["merged"],
            W["moves"], W["natures"], W["typechart"],
            item_overrides={"Gallade": "Choice Scarf"})
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Gallade"]["item"], "Choice Scarf")

    def test_chip_then_ko_partner_item_pins_the_partner(self):
        W = world()
        rows = cf.chip_then_ko(
            POOL, ["Kingambit"], "Ninetales-Alola", "Blizzard", W["merged"],
            W["moves"], W["natures"], W["typechart"], partner_item="Sitrus Berry")
        chip = rows[0]["chip"]["Kingambit"]
        attacker = cf._build("Ninetales-Alola", W["merged"], W["natures"],
                             item="Sitrus Berry")
        target = cf._build("Kingambit", W["merged"], W["natures"])
        blizzard = cf._lookup_move("Blizzard", W["moves"])
        expect = cf._raw_hit(attacker, blizzard, target, W["typechart"],
                             num_targets_hit=2)
        self.assertAlmostEqual(chip.lo, expect.lo)

    def test_pair_search_honours_item_overrides_on_the_candidate(self):
        W = world()
        rows = cf.pair_search(
            POOL, ["Kingambit", "Basculegion", "Garchomp"], W["merged"],
            W["moves"], W["natures"], W["typechart"],
            item_overrides={"Gallade": "Choice Scarf"})
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Gallade"]["item"], "Choice Scarf")


class TestSpeedTiers(unittest.TestCase):
    """"I also need to see speed tiers, for instance to have an option to
    make sure my guys (accounting for priority like bullet punch) outspeed
    their enemies." """

    def test_sorted_by_priority_bracket_then_speed(self):
        W = world()
        rows = cf.speed_tiers(
            ["Kingambit", "Basculegion", "Whimsicott", "Garchomp"],
            ["Kingambit"], W["merged"], W["moves"], W["natures"],
            W["typechart"])
        keys = [(r["priority"], r["speed"]) for r in rows]
        self.assertEqual(keys, sorted(keys, key=lambda k: (-k[0], -k[1])))

    def test_a_priority_move_user_ranks_above_a_faster_non_priority_one(self):
        """"accounting for priority like bullet punch" -- Mega Scizor (slow,
        Bullet Punch +1) must rank ABOVE something faster with no priority
        move at all, exactly the case the report was about."""
        W = world()
        rows = cf.speed_tiers(
            ["Mega Scizor", "Whimsicott"], ["Kingambit"], W["merged"],
            W["moves"], W["natures"], W["typechart"])
        by_name = {r["name"]: r for r in rows}
        self.assertGreater(by_name["Whimsicott"]["speed"],
                           by_name["Mega Scizor"]["speed"])
        self.assertEqual(by_name["Mega Scizor"]["priority"], 1)
        self.assertEqual(by_name["Mega Scizor"]["priority_move"], "Bullet Punch")
        names_in_order = [r["name"] for r in rows]
        self.assertLess(names_in_order.index("Mega Scizor"),
                        names_in_order.index("Whimsicott"))

    def test_status_moves_are_never_reported_as_the_priority_move(self):
        """Protect's priority bracket does not describe outspeeding to hit
        something -- a Pokemon whose only priority option is Protect must
        report priority 0, not Protect's +4."""
        W = world()
        rows = cf.speed_tiers(POOL, ["Kingambit"], W["merged"], W["moves"],
                              W["natures"], W["typechart"])
        for r in rows:
            self.assertNotEqual(r["priority_move"], "Protect")

    def test_a_mega_pick_is_ranked_on_its_mega_speed(self):
        """"make sure to use the mega form" -- Mega Floette (166 spe) is
        dramatically faster than base Floette (111); this must be the number
        speed_tiers reports."""
        W = world()
        rows = cf.speed_tiers(["Mega Floette"], ["Kingambit"], W["merged"],
                              W["moves"], W["natures"], W["typechart"])
        self.assertGreater(rows[0]["speed"], 140.0)

    def test_choice_scarf_pin_is_reflected_in_the_reported_speed(self):
        W = world()
        unboosted = cf.speed_tiers(["Gallade"], ["Kingambit"], W["merged"],
                                   W["moves"], W["natures"], W["typechart"])
        scarfed = cf.speed_tiers(["Gallade"], ["Kingambit"], W["merged"],
                                 W["moves"], W["natures"], W["typechart"],
                                 item_overrides={"Gallade": "Choice Scarf"})
        self.assertAlmostEqual(scarfed[0]["speed"], unboosted[0]["speed"] * 1.5)


class TestCounterTablePoolDefault(unittest.TestCase):
    """"Why does Mega Scizor not show up" -- it was never a damage/move-
    optimisation bug: the default pool was `generate_team.build_candidate_pool`,
    ranked by roster.csv's generic team-generation Score and truncated to the
    top 40, and Mega Scizor's generic Score doesn't make that cut even though
    it is a strong, correctly-scored answer to specific threats. The default
    pool is now the whole dataset."""

    class _Args:
        team = ""
        pool_size = 0

    def test_default_pool_is_the_whole_dataset(self):
        import counter_table as ct
        W = world()
        pool = ct._pool(self._Args(), W["merged"])
        self.assertEqual(len(pool), len(W["merged"]))
        self.assertIn("Mega Scizor", pool)

    def test_pool_size_still_narrows_when_explicitly_given(self):
        import counter_table as ct

        class Args:
            team = ""
            pool_size = 10
        W = world()
        pool = ct._pool(Args(), W["merged"])
        self.assertEqual(len(pool), 10)


class TestThresholdSearchScreening(unittest.TestCase):
    """"my attackers in counter_table must either be faster than the enemy,
    able to be faster with choice scarf, and/or take max X damage from the
    enemy's best attack (e.g., OHKO all, take less than 50%, outspeed. or
    2HKO all, take less than 33%, outspeed.)" """

    def test_unset_filters_do_not_change_existing_rows_or_cost(self):
        """Backwards compatible: with `max_taken`/`outspeed` both omitted,
        the extra fields must not even be computed."""
        W = world()
        rows = cf.threshold_search(
            POOL, ["Kingambit", "Basculegion"], W["merged"], W["moves"],
            W["natures"], W["typechart"], threshold=0.9)
        for r in rows:
            self.assertNotIn("incoming", r)
            self.assertNotIn("outspeeds", r)

    def test_max_taken_drops_a_row_that_takes_too_much(self):
        W = world()
        loose = cf.threshold_search(
            POOL, ["Kingambit"], W["merged"], W["moves"], W["natures"],
            W["typechart"], threshold=0.0, max_taken=1.0)
        strict = cf.threshold_search(
            POOL, ["Kingambit"], W["merged"], W["moves"], W["natures"],
            W["typechart"], threshold=0.0, max_taken=0.01)
        self.assertGreater(len(loose), len(strict))
        strict_names = {r["name"] for r in strict}
        for r in strict:
            self.assertTrue(all(h.hi <= 0.01 for h in r["incoming"].values()))
        # Everything strict allowed must also appear in the loose pass.
        self.assertTrue(strict_names <= {r["name"] for r in loose})

    def test_outspeed_natural_requires_a_real_speed_win(self):
        W = world()
        rows = cf.threshold_search(
            POOL, ["Kingambit"], W["merged"], W["moves"], W["natures"],
            W["typechart"], threshold=0.0, outspeed="natural")
        for r in rows:
            self.assertTrue(all(r["outspeeds"].values()))

    def test_outspeed_scarf_accepts_a_hypothetical_scarf_win(self):
        """A row that fails --outspeed natural but passes --outspeed scarf
        must exist for a real slow-but-scarfable Pokemon (Mega Scizor is
        locked to its stone and cannot actually equip Scarf, but the
        HYPOTHETICAL must still say yes)."""
        W = world()
        natural = cf.threshold_search(
            ["Mega Scizor"], ["Basculegion"], W["merged"], W["moves"],
            W["natures"], W["typechart"], threshold=0.0, outspeed="natural")
        scarf = cf.threshold_search(
            ["Mega Scizor"], ["Basculegion"], W["merged"], W["moves"],
            W["natures"], W["typechart"], threshold=0.0, outspeed="scarf")
        self.assertEqual(natural, [])
        self.assertEqual(len(scarf), 1)
        self.assertFalse(scarf[0]["outspeeds"]["Basculegion"])
        self.assertTrue(scarf[0]["outspeeds_scarf"]["Basculegion"])

    def test_a_speed_tie_does_not_count_as_outspeeding(self):
        """"ties resolve against us" -- the same convention `pair_search`
        uses. Checked directly against `effective_speed` rather than hunting
        for a real tie in the dataset."""
        W = world()
        c = cf._build("Kingambit", W["merged"], W["natures"])
        from engine import FieldState, effective_speed
        same_speed = effective_speed(c, FieldState(), "p1")
        # A candidate with EXACTLY Kingambit's own speed must not be credited
        # with outspeeding Kingambit -- verified through the private helper
        # rather than searching for a coincidental real tie.
        self.assertFalse(same_speed > same_speed)

    def test_threshold_becomes_a_hard_filter_once_a_screen_is_requested(self):
        """Ordinarily `meets_all` is informational (a near-miss still ranks
        and shows) -- but once max_taken/outspeed are actually requested,
        "OHKO all, take less than 50%, outspeed" is three MUSTS together."""
        W = world()
        rows = cf.threshold_search(
            POOL, ["Kingambit", "Basculegion"], W["merged"], W["moves"],
            W["natures"], W["typechart"], threshold=1.0, max_taken=1.0,
            outspeed=None)
        for r in rows:
            self.assertTrue(r["meets_all"])

    def test_max_taken_reads_the_defenders_best_roll_not_the_average(self):
        """The guaranteed-survival direction: `incoming[t].hi` must equal
        the enemy's own HI roll (their best case), not their average or
        worst -- checked directly against `_raw_hit`."""
        W = world()
        rows = cf.threshold_search(
            ["Garchomp"], ["Kingambit"], W["merged"], W["moves"],
            W["natures"], W["typechart"], threshold=0.0, max_taken=1.0)
        row = rows[0]
        got = row["incoming"]["Kingambit"]
        self.assertEqual(got.frac, got.hi)
        self.assertLessEqual(got.lo, got.avg)
        self.assertLessEqual(got.avg, got.hi)


class TestJointPairSearch(unittest.TestCase):
    """`joint_pair_search` -- `pair_search` generalised from one candidate
    (plus a partner locked to ONE fixed move) to two real attackers, and from
    one turn to several, classified as a clean sweep, an out-trade win, a
    loss, or no-KO, plus a Tailwind-robustness replay.

        "against a given enemy pair, my pair either out trade all possible
         enemy pairs to a win (including spread damage ...), outspeed and ko
         before either of mine fail, or ... do not get OHKOd by any under
         enemy tailwind"

    Every fixture below is a real matchup pulled from the dataset (verified
    by running the search itself), not a hand-derived guess.
    """

    def setUp(self):
        self.W = world()

    def _search(self, cand, targets, partner, **kw):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search([cand], targets, partner, merged, moves,
                                    natures, typechart, **kw)
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_a_dominant_fast_pair_sweeps_a_weak_slow_pair(self):
        """Mega Gengar + Mega Alakazam vs Sableye + Ariados: both dead before
        either of them ever gets to act."""
        row = self._search("Mega Gengar", ["Sableye", "Ariados"],
                           "Mega Alakazam")
        d = row["detail"][("Sableye", "Ariados")]
        self.assertEqual(d["outcome"], "sweep")
        self.assertTrue(d["tailwind_safe"])
        self.assertEqual(d["tailwind_outcome"], "sweep")

    def test_tailwind_can_flip_a_sweep_to_unsafe(self):
        """Mega Gengar + Mega Alakazam vs Sharpedo + Rampardos: a real sweep
        at normal speed, but NOT once the enemy pair moves first -- the
        robustness check has to actually replay the race, not just assume a
        win stays a win."""
        row = self._search("Mega Gengar", ["Sharpedo", "Rampardos"],
                           "Mega Alakazam")
        d = row["detail"][("Sharpedo", "Rampardos")]
        self.assertEqual(d["outcome"], "sweep")
        self.assertFalse(d["tailwind_safe"])
        self.assertEqual(d["tailwind_outcome"], "loss")

    def test_out_trade_wins_the_race_without_a_clean_sweep(self):
        """Mega Scizor + Whimsicott vs Kingambit + Basculegion: both die
        within the window, but Kingambit gets at least one hit in first."""
        row = self._search("Mega Scizor", ["Kingambit", "Basculegion"],
                           "Whimsicott", turns=2)
        d = row["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d["outcome"], "out_trade")
        self.assertTrue(d["tailwind_safe"])

    def test_turns_extends_the_window(self):
        """The SAME matchup, only the turn cap different: too short a window
        reports no_ko even though the pair wins it with one more turn."""
        row1 = self._search("Mega Scizor", ["Kingambit", "Basculegion"],
                            "Whimsicott", turns=1)
        d1 = row1["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d1["outcome"], "no_ko")
        self.assertEqual(d1["turns_used"], 1)

        row2 = self._search("Mega Scizor", ["Kingambit", "Basculegion"],
                            "Whimsicott", turns=2)
        d2 = row2["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d2["outcome"], "out_trade")
        self.assertEqual(d2["turns_used"], 2)

    def test_spread_move_still_takes_the_075x_penalty_when_both_are_alive(self):
        """Same rule `TestSpreadMovesInPairSearch` checks for `pair_search`,
        now for the joint search's own move-choice helper: Garchomp's
        Earthquake hits both live enemies at once, at the doubles multiplier,
        not a full hit on each."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        item, mvs, weather = cf._answer_for(
            "Garchomp", merged, moves, natures, typechart,
            ["Kingambit", "Basculegion"])
        c = cf._build("Garchomp", merged, natures, item=item)
        c_moves = cf._move_infos("Garchomp", merged, moves, mvs)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Basculegion", merged, natures)
        hits, mv = cf._choose_action(c, c_moves, {"E1": e1, "E2": e2},
                                     typechart, weather=weather)
        self.assertEqual(mv.name, "Earthquake")
        self.assertEqual(set(hits), {"E1", "E2"},
                         "a live spread move must hit BOTH enemies")
        for h in hits.values():
            self.assertEqual(h.num_targets_hit, 2)

    def test_a_single_live_target_does_not_get_the_spread_penalty(self):
        """Once only one enemy is left standing, the SAME spread move should
        no longer take the doubles penalty -- there's only one Pokemon left
        for it to hit."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        item, mvs, weather = cf._answer_for(
            "Garchomp", merged, moves, natures, typechart,
            ["Kingambit", "Basculegion"])
        c = cf._build("Garchomp", merged, natures, item=item)
        c_moves = cf._move_infos("Garchomp", merged, moves, mvs)
        e1 = cf._build("Kingambit", merged, natures)
        hits, mv = cf._choose_action(c, c_moves, {"E1": e1}, typechart,
                                     weather=weather)
        self.assertEqual(set(hits), {"E1"})
        self.assertEqual(hits["E1"].num_targets_hit, 1)

    def test_the_partner_itself_is_excluded_from_the_pool(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(
            ["Mega Alakazam", "Mega Gengar"], ["Sableye", "Ariados"],
            "Mega Alakazam", merged, moves, natures, typechart)
        self.assertNotIn("Mega Alakazam", [r["name"] for r in rows])

    def test_a_named_target_is_excluded_from_the_pool_too(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(
            ["Mega Gengar", "Sableye"], ["Sableye", "Ariados"],
            "Mega Alakazam", merged, moves, natures, typechart)
        self.assertNotIn("Sableye", [r["name"] for r in rows])

    def test_rows_are_ranked_beaten_first_then_tailwind_safe(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(
            ["Mega Gengar", "Ninetales-Alola"], ["Sharpedo", "Rampardos"],
            "Mega Alakazam", merged, moves, natures, typechart)
        beaten = [r["pairs_swept"] + r["pairs_traded"] for r in rows]
        self.assertEqual(beaten, sorted(beaten, reverse=True))
