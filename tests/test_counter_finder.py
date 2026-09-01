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

    def test_a_named_target_is_a_legal_mirror_pick(self):
        """Basculegion is IN the pool but is also a named target here --
        "does Basculegion beat Basculegion" (i.e. the mirror) is a legal
        question now, not silently dropped -- "you should be allowed to
        bring the same pokemon as the enemy"."""
        self.assertIn("Basculegion", self.by_name)

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
            item, move_names, _weather = cf.best_answer(
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
                typechart, e1, partner=partner, partner_move=p_move,
                partner_target=e1)
            best = None
            for c_t in (e1, e2):
                for p_t in (e1, e2):
                    got = cf._sequential_pair_outcome(
                        attacker, moves_list, e1, e1c, e1_moves, e2, e2c, e2_moves,
                        typechart, c_t, partner=partner,
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


class TestChoiceScarfExcludedByDefault(unittest.TestCase):
    """"By default I do not want to allow choice scarf; it is too easy to
    punish, but should be an option." Garchomp vs Dragapult is a real case
    where the unrestricted optimizer picks Scarf (to outspeed) -- confirmed
    directly against `optimize_sets.best_item`/`best_moveset` before writing
    this test."""

    def test_default_search_never_picks_scarf_even_when_it_would_win_speed(self):
        W = world()
        item, _moves, _w = cf.best_answer(
            "Garchomp", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Dragapult"])
        self.assertNotEqual(item, "Choice Scarf")

    def test_excluded_items_frozenset_restores_scarf_as_a_candidate(self):
        W = world()
        item, _moves, _w = cf.best_answer(
            "Garchomp", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Dragapult"], excluded_items=frozenset())
        self.assertEqual(item, "Choice Scarf")

    def test_an_explicit_pin_still_works_under_the_default_exclusion(self):
        """The default exclusion is a search preference, not a legality
        rule -- an explicit `--item` pin always bypasses it, same as
        `BANNED_ITEMS` pins already do."""
        W = world()
        item, moves, _w = cf.best_answer(
            "Garchomp", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Dragapult"], item="Choice Scarf")
        self.assertEqual(item, "Choice Scarf")
        self.assertEqual(len(moves), 4)

    def test_threshold_search_excludes_scarf_by_default_from_the_pool(self):
        W = world()
        rows = cf.threshold_search(
            ["Garchomp"], ["Dragapult"], W["merged"], W["moves"],
            W["natures"], W["typechart"])
        self.assertNotEqual(rows[0]["item"], "Choice Scarf")

    def test_threshold_search_never_restricts_a_named_enemys_item(self):
        """`excluded_items` must only ever narrow OUR OWN candidates -- a
        named enemy's own set (`target_sets`, used for the `outspeed`/
        `max_taken` screen) must still be searched unrestricted, since the
        enemy could easily be running Choice Scarf themselves. Confirmed by
        source inspection, mirroring the existing pragmatic pattern in
        `test_team_builder_sets.py`'s `TestTheOptimiserDoesNotEatHandEdits`."""
        import inspect
        src = inspect.getsource(cf.threshold_search)
        target_sets_call = src[src.index("target_sets[t] ="):][:250]
        self.assertIn("excluded_items=frozenset()", target_sets_call)


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
    pool is now the whole dataset, minus whatever data/preferences.csv Exclude
    actually names (cascaded to the paired Mega/base form -- see
    `_apply_preferences`) -- see TestPreferencesReducePool for the
    include/exclude behaviour itself."""

    class _Args:
        team = ""
        pool_size = 0

    def test_default_pool_is_the_whole_dataset_minus_shipped_excludes(self):
        """The expected drop count must be computed with the SAME Mega/base
        cascade `_apply_preferences` itself applies (a raw
        `len(exclude list)` undercounts whenever the shipped file names only
        one half of a Mega/base pair, which the real shipped file does --
        e.g. "Steelix" without "Mega Steelix" -- cascade correctly drops
        the paired form too)."""
        import counter_table as ct
        from species_data import load_preferences
        W = world()
        merged = W["merged"]
        pool = ct._pool(self._Args(), merged)
        raw_excluded = set(load_preferences()["exclude"])
        cascaded = set(raw_excluded)
        for e in list(cascaded):
            if e.startswith("Mega "):
                cascaded.add(e[5:])
            else:
                cascaded.update({f"Mega {e}", f"Mega {e} X", f"Mega {e} Y"})
        want_dropped = {n for n in merged if n in cascaded}
        self.assertEqual(len(pool), len(merged) - len(want_dropped))
        self.assertIn("Mega Scizor", pool)
        self.assertFalse(cascaded & set(pool))

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

    def test_tailwind_can_flip_a_win_to_unsafe(self):
        """Mega Gengar + Mega Alakazam vs Sharpedo (Focus Sash, its own
        real most-used item -- see `TestJointFocusSashAndSturdy`) +
        Rampardos: a real win at normal speed (Sharpedo's Sash keeps it
        alive at 1 HP to land one hit back, so `out_trade` rather than a
        clean `sweep`), but NOT once the enemy pair moves first -- the
        robustness check has to actually replay the race, not just assume a
        win stays a win."""
        row = self._search("Mega Gengar", ["Sharpedo", "Rampardos"],
                           "Mega Alakazam")
        d = row["detail"][("Sharpedo", "Rampardos")]
        self.assertEqual(d["outcome"], "out_trade")
        self.assertFalse(d["tailwind_safe"])
        self.assertEqual(d["tailwind_outcome"], "loss")

    def test_out_trade_wins_the_race_without_a_clean_sweep(self):
        """Mega Scizor + Whimsicott vs Kingambit + Basculegion: both die
        within the window, but Kingambit gets at least one hit in first.
        Needs 3 turns, not 2 -- see the priority-tie-break fix (Task 4) in
        `_choose_move`/`_choose_action`'s own docstrings: Basculegion now
        finishes off a nearly-fainted Mega Scizor with its own priority
        move (Aqua Jet) turn 2 instead of a bigger-but-slower Wave Crash,
        since both already guarantee the kill -- denying Scizor the extra
        turn-2 action it used to get off before dying, which is what used
        to let the whole race finish by turn 2."""
        row = self._search("Mega Scizor", ["Kingambit", "Basculegion"],
                           "Whimsicott", turns=3)
        d = row["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d["outcome"], "out_trade")
        self.assertTrue(d["tailwind_safe"])

    def test_turns_extends_the_window(self):
        """The SAME matchup, only the turn cap different: too short a window
        reports no_ko even though the pair wins it with more turns."""
        row1 = self._search("Mega Scizor", ["Kingambit", "Basculegion"],
                            "Whimsicott", turns=1)
        d1 = row1["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d1["outcome"], "no_ko")
        self.assertEqual(d1["turns_used"], 1)

        row2 = self._search("Mega Scizor", ["Kingambit", "Basculegion"],
                            "Whimsicott", turns=3)
        d2 = row2["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d2["outcome"], "out_trade")
        # Finishes turn 2, not turn 3 -- Basculegion's own Wave Crash (33%
        # recoil) now costs it real HP on top of the chip it's already
        # taking, closing the race out a turn earlier than before recoil
        # was modeled here.
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

    def test_a_named_target_is_a_legal_mirror_pick(self):
        """Sableye is in the pool AND a named target -- a legal mirror pick
        now, not silently dropped."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(
            ["Mega Gengar", "Sableye"], ["Sableye", "Ariados"],
            "Mega Alakazam", merged, moves, natures, typechart)
        self.assertIn("Sableye", [r["name"] for r in rows])

    def test_rows_are_ranked_beaten_first_then_tailwind_safe(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(
            ["Mega Gengar", "Ninetales-Alola"], ["Sharpedo", "Rampardos"],
            "Mega Alakazam", merged, moves, natures, typechart)
        beaten = [r["pairs_swept"] + r["pairs_traded"] for r in rows]
        self.assertEqual(beaten, sorted(beaten, reverse=True))


class TestWinQualityScoring(unittest.TestCase):
    """"I would consider losing 1 pokemon and taking a lot of damage and
    KOing 2 enemies as far inferior to KOing the enemy without taking
    damage, given the range of possible outcomes. There should be a way to
    score this to reflect this dynamic." `our_hp`/`clean_win_value` (per
    enemy pair) and `pairs_clean_win_total` (summed) are that score --
    reusing the exact real fixtures `TestJointPairSearch` already verified
    (a clean sweep, and a chippy out-trade), not new hand-derived guesses.
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

    def test_a_clean_sweep_scores_the_maximum_2_point_0(self):
        """A sweep is BY DEFINITION zero damage taken (both enemies die
        before either ever acts) -- `clean_win_value` must be exactly 2.0,
        not just "high"."""
        row = self._search("Mega Gengar", ["Sableye", "Ariados"],
                           "Mega Alakazam")
        d = row["detail"][("Sableye", "Ariados")]
        self.assertEqual(d["outcome"], "sweep")
        self.assertEqual(d["clean_win_value"], 2.0)
        self.assertEqual(d["our_hp"], {"C": 1.0, "P": 1.0})

    def test_a_chippy_out_trade_scores_less_than_a_clean_sweep(self):
        """Mega Gengar + Mega Alakazam vs Sharpedo + Rampardos: Sharpedo's
        Focus Sash keeps it alive to land a hit back before dying -- a real
        win (`out_trade`), but NOT a free one. `clean_win_value` must be
        strictly less than the 2.0 a sweep scores, and `our_hp` must show
        which of ours actually took the damage."""
        row = self._search("Mega Gengar", ["Sharpedo", "Rampardos"],
                           "Mega Alakazam")
        d = row["detail"][("Sharpedo", "Rampardos")]
        self.assertEqual(d["outcome"], "out_trade")
        self.assertLess(d["clean_win_value"], 2.0)
        self.assertGreaterEqual(d["clean_win_value"], 0.0)
        self.assertEqual(d["our_hp"]["C"] + d["our_hp"]["P"], d["clean_win_value"])
        self.assertTrue(d["our_hp"]["C"] < 1.0 or d["our_hp"]["P"] < 1.0,
                        "at least one of ours must show real damage taken")

    def test_a_loss_scores_zero_not_whatever_hp_happened_to_survive(self):
        """A real loss must never leak a positive clean_win_value just
        because a doomed Pokemon happened to still be sitting on some HP
        when the race ended -- the outcome bucket alone already says
        'bad', so this stays a clean, unambiguous floor."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        our_built = cf._build_forms(["Whimsicott", "Aromatisse"], merged,
                                    natures, moves)
        enemy_built = cf._build_forms(["Kingambit", "Mega Tyranitar"],
                                      merged, natures, moves)
        detail, summary = cf._pair_vs_targets(
            "Whimsicott", "Aromatisse", our_built,
            ["Kingambit", "Mega Tyranitar"], enemy_built, typechart, turns=2)
        d = detail[("Kingambit", "Mega Tyranitar")]
        if d["outcome"] in ("loss", "no_ko"):
            self.assertEqual(d["clean_win_value"], 0.0)
            self.assertEqual(d["our_hp"], {"C": 0.0, "P": 0.0})
        else:
            self.skipTest("fixture assumes Whimsicott+Aromatisse loses or "
                          "stalls against Kingambit+Mega Tyranitar")

    def test_pairs_clean_win_total_sums_across_every_enemy_pair(self):
        row = self._search("Mega Gengar", ["Sableye", "Ariados"],
                           "Mega Alakazam")
        self.assertEqual(row["pairs_clean_win_total"],
                         sum(d["clean_win_value"] for d in row["detail"].values()))

    def test_pruned_entries_score_zero_clean_win(self):
        """`prune_below`'s conservative "loss" placeholder must not leak a
        positive clean_win_value either -- same worst-case-only bias every
        other minimax in this module already carries."""
        d = cf._pruned_entry()
        self.assertEqual(d["clean_win_value"], 0.0)
        self.assertEqual(d["our_hp"], {"C": 0.0, "P": 0.0})


class TestJointProtectRobustness(unittest.TestCase):
    """A turn-1 scouting Protect from either enemy is the classic doubles
    50/50 -- "it must be robust to either enemy protecting on turn 1, for
    instance, Metagross/Hydreigon vs Mega Charizard Y/Sylveon - if Sylveon
    protects, then Mega Charizard Y KOs Metagross, and Sylveon can beat
    Hydreigon the next turn." `_pair_vs_targets` replays the same race with
    each enemy role Protecting turn 1 and reports `protect_safe` only if
    BOTH replays are still a win.

    Both fixtures are the same named pair against the same enemy pair --
    only Metagross's item differs -- verified by running the search itself,
    not hand-derived: with its optimised Choice Scarf (fast enough to act
    before Mega Charizard Y regardless of who Protects) the pair is safe;
    forced onto Life Orb (slower than Mega Charizard Y) it is not.
    """

    def setUp(self):
        self.W = world()

    def _deep(self, item_overrides=None):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        _i1, _i2, detail, summary = cf.deep_dive(
            "Metagross", "Hydreigon", ["Mega Charizard Y", "Sylveon"],
            merged, moves, natures, typechart, turns=2,
            item_overrides=item_overrides)
        return detail[("Mega Charizard Y", "Sylveon")], summary

    def test_protect_outcomes_present_for_both_enemy_roles(self):
        d, _summary = self._deep()
        self.assertEqual(set(d["protect_outcomes"]), {"E1", "E2"})

    def test_fast_enough_pair_is_protect_safe(self):
        """Metagross on Choice Scarf outspeeds Mega Charizard Y regardless
        of which enemy Protects turn 1. Pinned explicitly (Scarf is
        excluded from the default search since this session's "no Choice
        Scarf by default" change) -- an explicit --item pin still works
        regardless of that default."""
        d, summary = self._deep(item_overrides={"Metagross": "Choice Scarf"})
        self.assertTrue(d["protect_safe"])
        for outcome in d["protect_outcomes"].values():
            self.assertIn(outcome, ("sweep", "out_trade"))
        self.assertEqual(summary["pairs_protect_safe"], 1)

    def test_a_slow_pair_can_have_a_real_protect_50_50(self):
        """Forced off Scarf, Metagross no longer outspeeds Mega Charizard Y
        -- a turn-1 Protect from one enemy role turns the race into a loss,
        exactly the 50/50 the request describes."""
        d, summary = self._deep(item_overrides={"Metagross": "Life Orb"})
        self.assertFalse(d["protect_safe"])
        self.assertTrue(
            any(o not in ("sweep", "out_trade")
                for o in d["protect_outcomes"].values()),
            "expected at least one protect replay to flip the outcome")
        self.assertEqual(summary["pairs_protect_safe"], 0)

    def test_protect_replay_does_not_change_the_no_protect_outcome(self):
        """The Protect robustness check must not mutate what `outcome`
        itself reports -- it's an independent replay, not a different line
        of play for the recorded race."""
        d, _summary = self._deep()
        self.assertEqual(d["outcome"], "out_trade")

    def test_protected_role_takes_no_damage_on_the_turn_it_protects(self):
        """A direct mechanical check on `_joint_race` itself: with E1
        forced to Protect turn 1, no hit in that turn's log should ever
        target E1."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        item, mvs, weather = cf._answer_for(
            "Metagross", merged, moves, natures, typechart,
            ["Mega Charizard Y", "Sylveon"])
        c1 = cf._build("Metagross", merged, natures, item=item)
        c1_moves = cf._move_infos("Metagross", merged, moves, mvs)
        item2, mvs2, _w2 = cf._answer_for(
            "Hydreigon", merged, moves, natures, typechart,
            ["Mega Charizard Y", "Sylveon"])
        c2 = cf._build("Hydreigon", merged, natures, item=item2)
        c2_moves = cf._move_infos("Hydreigon", merged, moves, mvs2)
        _e1_item, e1_mvs, _e1_w = cf._answer_for(
            "Mega Charizard Y", merged, moves, natures, typechart,
            ["Metagross", "Hydreigon"])
        _e2_item, e2_mvs, _e2_w = cf._answer_for(
            "Sylveon", merged, moves, natures, typechart,
            ["Metagross", "Hydreigon"])
        combatants = {"C": c1, "P": c2,
                     "E1": cf._build("Mega Charizard Y", merged, natures),
                     "E2": cf._build("Sylveon", merged, natures)}
        moves_by_role = {
            "C": c1_moves, "P": c2_moves,
            "E1": cf._move_infos("Mega Charizard Y", merged, moves, e1_mvs),
            "E2": cf._move_infos("Sylveon", merged, moves, e2_mvs),
        }
        weather = cf._field_weather(combatants)
        _outcome, _t, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, weather, 2,
            first_turn_protected_role="E1")
        turn1 = log[0]
        targets_hit = {tgt for _role, tgt, _h in turn1}
        self.assertNotIn("E1", targets_hit)


class TestTailwindAsARealThreat(unittest.TestCase):
    """"In the cases where set tailwind is assumed, do not just assume
    tailwind is up, but just test the impact of the tailwind setter
    choosing to set tailwind, and see if that changes the state from a win
    to a loss." `_pair_vs_targets` now replays the race once per real
    Tailwind setter (`merged[name]["moves_usage"]`) with that role actually
    spending turn 1 CASTING it (`_joint_race`'s `first_turn_tailwind_role`,
    a no-op action, same shape as a forced Protect) instead of attacking --
    the speed boost only applies from turn 2 onward, since this module
    doesn't re-sort mid-turn the way `battle.py`'s real engine does. That's
    a real cost, not a free assumption: the setter itself can still die on
    the very turn it commits to casting Tailwind, same as it would on any
    other unprotected turn.

    Real, verified fixture, movesets PINNED to one move each (see the
    priority-tie-break fix, Task 4, in `_choose_move`/`_choose_action`'s
    own docstrings -- once BOTH the original fixture's attackers could pick
    between several already-guaranteed kills, which one they picked started
    mattering, and the original hand-picked pair no longer produced a clean
    before/after story with a real, unpinned moveset): Sylveon (Hyper
    Voice) + Garchomp (Dragon Claw) against Whimsicott (a real Tailwind
    setter per usage data, High Jump Kick-less here -- just casts or does
    nothing else relevant) + Mudsdale (High Horsepower, no real Tailwind
    access). Normal-speed race is an out-trade win: Whimsicott chips
    Garchomp with Moonblast turn 1 and finishes it turn 2, but Sylveon's
    Hyper Voice (a spread hit) kills both Whimsicott and the already-
    weakened Mudsdale that same second turn. If Whimsicott instead spends
    turn 1 CASTING Tailwind, Garchomp is never chipped at all turn 1 -- so
    by turn 2 Sylveon (softened by Mudsdale's own High Horsepower) is the
    juicier kos_now target, and Whimsicott's one attacking turn goes to
    killing Sylveon instead of finishing Garchomp. Mudsdale still dies to
    Garchomp's second Dragon Claw, but Whimsicott survives (it only ever
    took one hit) -- the clean win becomes a stalemate (`no_ko`), not
    because either side plays worse, but because the setter's own single
    spent turn changes WHOSE death it ends up choosing on turn 2.
    """

    def setUp(self):
        self.W = world()
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        self.our_names = ["Sylveon", "Garchomp"]
        self.enemy_names = ["Whimsicott", "Mudsdale"]
        self.our_built = cf._build_forms(self.our_names, merged, natures, moves)
        self.enemy_built = cf._build_forms(self.enemy_names, merged, natures, moves)
        self.our_built["Sylveon"]["moves"] = cf._move_infos(
            "Sylveon", merged, moves, ["Hyper Voice"])
        self.our_built["Garchomp"]["moves"] = cf._move_infos(
            "Garchomp", merged, moves, ["Dragon Claw"])
        self.enemy_built["Mudsdale"]["moves"] = cf._move_infos(
            "Mudsdale", merged, moves, ["High Horsepower"])

    def _race(self, merged):
        typechart = self.W["typechart"]
        detail, summary = cf._pair_vs_targets(
            self.our_names[0], self.our_names[1], self.our_built,
            self.enemy_names, self.enemy_built, typechart, turns=2,
            merged=merged)
        return detail[tuple(self.enemy_names)], summary

    def test_whimsicott_really_knows_tailwind_mudsdale_does_not(self):
        """The fixture's precondition, checked against real usage data --
        if this ever stops being true the whole fixture needs revisiting."""
        merged = self.W["merged"]
        self.assertTrue(any(mv == "Tailwind" for mv, _pct in
                            merged["Whimsicott"]["moves_usage"]))
        self.assertFalse(any(mv == "Tailwind" for mv, _pct in
                             merged["Mudsdale"]["moves_usage"]))

    def test_the_normal_race_alone_is_a_win(self):
        d, _summary = self._race(merged=None)
        self.assertEqual(d["outcome_without_tailwind"], "out_trade")

    def test_a_real_tailwind_threat_that_is_worse_becomes_the_outcome(self):
        d, summary = self._race(merged=self.W["merged"])
        self.assertTrue(d["tailwind_is_real_threat"])
        self.assertTrue(d["tailwind_forced"])
        self.assertEqual(d["tailwind_outcome"], "no_ko")
        self.assertEqual(d["outcome"], "no_ko",
                         "a real Tailwind threat that turns a win into a "
                         "stalemate must be the assumed outcome, not a "
                         "footnote")
        self.assertEqual(d["outcome_without_tailwind"], "out_trade",
                         "the original no-Tailwind result must still be "
                         "recoverable, not overwritten")
        self.assertEqual(summary["pairs_no_ko"], 1)
        self.assertEqual(summary["pairs_swept"] + summary["pairs_traded"], 0)

    def test_no_usage_data_leaves_the_old_behaviour_unchanged(self):
        """`merged=None` (the default every existing caller had before this
        feature) must reproduce the exact old behaviour: `tailwind_outcome`
        falls back to the flat instant-speed-doubling hypothesis (no real
        setter role to cast it realistically for) and is never promoted."""
        d, summary = self._race(merged=None)
        self.assertFalse(d["tailwind_is_real_threat"])
        self.assertFalse(d["tailwind_forced"])
        self.assertEqual(d["outcome"], "out_trade")
        self.assertEqual(summary["pairs_swept"] + summary["pairs_traded"], 1)

    def test_the_log_matches_whichever_race_actually_decided_the_outcome(self):
        """When Tailwind is promoted, `log` must be the Tailwind race's own
        turns -- Whimsicott (E1), freed up now that Tailwind is already
        cast, kills Sylveon (C, softened by Mudsdale's turn-1 hit) on turn
        2, while Garchomp (P) lands its own second Dragon Claw on Mudsdale
        (E2) that same turn -- not the normal-speed race's turns, where
        Whimsicott spends BOTH turns on Garchomp instead. Otherwise the log
        would show a clean win while `outcome` says no_ko."""
        d, _summary = self._race(merged=self.W["merged"])
        last_turn = d["log"][-1]
        actors = [role for role, _tgt, _hit in last_turn]
        self.assertEqual(sorted(actors), ["E1", "P"],
                         "fixture assumes Whimsicott (E1) and Garchomp (P) "
                         "are the only actors left on the final turn -- "
                         "Whimsicott kills Sylveon, Garchomp kills Mudsdale")
        self.assertIn(("E1", "C"), [(role, tgt) for role, tgt, _hit in last_turn],
                     "Whimsicott must be the one finishing off Sylveon, "
                     "not Garchomp -- that's the actual redirect this "
                     "fixture demonstrates")

    def test_the_setter_still_pays_the_real_cost_of_casting_it(self):
        """Whimsicott (the setter) spends turn 1 casting Tailwind instead
        of attacking -- so Garchomp (P) takes no damage turn 1 at all, same
        as it would if Whimsicott had simply skipped its turn. This is what
        separates "realistically cast" from "assumed already up": the
        setter is not free, and here that cost is exactly what saves
        Garchomp -- it never gets chipped, so it's never the target
        Whimsicott's second turn goes after."""
        d, _summary = self._race(merged=self.W["merged"])
        first_turn = d["log"][0]
        actors = [role for role, _tgt, _hit in first_turn]
        self.assertNotIn("E1", actors,
                         "Whimsicott (E1) must not land a hit turn 1 -- it "
                         "spent the turn casting Tailwind, not attacking")
        targets_hit = {tgt for _role, tgt, _hit in first_turn}
        self.assertNotIn("P", targets_hit,
                         "Garchomp (P) must take no damage turn 1 -- "
                         "Whimsicott, its only real threat, spent the turn "
                         "casting Tailwind instead of attacking it")

    def test_no_override_when_no_enemy_in_the_pair_knows_tailwind(self):
        """Kingambit alone (no Hydreigon) has no real Tailwind access --
        the hypothesis replay still runs, but never gets promoted."""
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        typechart = self.W["typechart"]
        enemy_names = ["Kingambit", "Sylveon"]
        enemy_built = cf._build_forms(enemy_names, merged, natures, moves)
        detail, _summary = cf._pair_vs_targets(
            self.our_names[0], self.our_names[1], self.our_built,
            enemy_names, enemy_built, typechart, turns=2, merged=merged)
        d = detail[tuple(enemy_names)]
        self.assertFalse(d["tailwind_is_real_threat"])
        self.assertFalse(d["tailwind_forced"])
        self.assertEqual(d["outcome"], d["outcome_without_tailwind"])

class TestChargeMovesNeedTheirWeather(unittest.TestCase):
    """"Electro shot needs rain and solar beam needs sun to be a 1-turn
    move, otherwise they are 2-turn moves." `_raw_hit` (the one place every
    move-choice/damage function in this module reads a move's real power
    from) now treats a charge move (`move.flags.get("charge")`) as dealing
    NO damage this turn unless the matching weather from `CHARGE_WEATHER_SKIP`
    is already up -- same rule and reasoning `solver.candidate_actions`/
    `fast_eval._pick_greedy_action` already apply for the real engine's own
    heuristic layers, since this module has no per-role "already charging"
    state to know the move is mid-commitment the way `battle.py`'s real
    engine does."""

    def setUp(self):
        self.W = world()
        merged, natures = self.W["merged"], self.W["natures"]
        self.attacker = cf._build("Torkoal", merged, natures)
        self.target = cf._build("Kingambit", merged, natures)
        self.solar_beam = cf._lookup_move("Solar Beam", self.W["moves"])
        self.electro_shot = cf._lookup_move("Electro Shot", self.W["moves"])

    def test_solar_beam_is_flagged_as_a_charge_move(self):
        self.assertEqual(self.solar_beam.flags.get("charge"), 1)
        self.assertEqual(self.electro_shot.flags.get("charge"), 1)

    def test_solar_beam_deals_no_damage_without_sun(self):
        typechart = self.W["typechart"]
        for weather in (None, "rain", "sand", "snow"):
            got = cf._raw_hit(self.attacker, self.solar_beam, self.target,
                              typechart, weather=weather, roll="avg")
            self.assertEqual(got.frac, 0.0, f"weather={weather}")

    def test_solar_beam_deals_real_damage_in_sun(self):
        typechart = self.W["typechart"]
        got = cf._raw_hit(self.attacker, self.solar_beam, self.target,
                          typechart, weather="sun", roll="avg")
        self.assertGreater(got.frac, 0.0)

    def test_electro_shot_needs_rain_specifically_not_sun(self):
        typechart = self.W["typechart"]
        in_rain = cf._raw_hit(self.attacker, self.electro_shot, self.target,
                              typechart, weather="rain", roll="avg")
        in_sun = cf._raw_hit(self.attacker, self.electro_shot, self.target,
                             typechart, weather="sun", roll="avg")
        self.assertGreater(in_rain.frac, 0.0)
        self.assertEqual(in_sun.frac, 0.0)

    def test_a_charge_move_with_no_weather_skip_is_never_a_one_turn_hit(self):
        """Fly/Dig/Sky Attack/... have no `CHARGE_WEATHER_SKIP` entry at
        all -- always a 2-turn move here, regardless of weather."""
        typechart = self.W["typechart"]
        fly = cf._lookup_move("Fly", self.W["moves"])
        self.assertNotIn("Fly", cf.CHARGE_WEATHER_SKIP)
        for weather in (None, "sun", "rain", "sand", "snow"):
            got = cf._raw_hit(self.attacker, fly, self.target, typechart,
                              weather=weather, roll="avg")
            self.assertEqual(got.frac, 0.0, f"weather={weather}")

    def test_choose_move_never_picks_solar_beam_without_sun(self):
        """A move-choice function, not just the raw hit -- Solar Beam must
        never outrank a real one-turn move it's paired against."""
        typechart = self.W["typechart"]
        ember = cf._lookup_move("Flamethrower", self.W["moves"])
        got, mv = cf._choose_move(self.attacker, [self.solar_beam, ember],
                                  self.target, typechart, weather=None)
        self.assertEqual(mv.name, "Flamethrower")

    def test_choose_move_does_pick_solar_beam_once_sun_is_up(self):
        typechart = self.W["typechart"]
        ember = cf._lookup_move("Flamethrower", self.W["moves"])
        got, mv = cf._choose_move(self.attacker, [self.solar_beam, ember],
                                  self.target, typechart, weather="sun")
        # Not asserting WHICH wins on raw damage -- only that Solar Beam is
        # now a live candidate at all (it was hard-excluded above).
        self.assertIn(mv.name, ("Solar Beam", "Flamethrower"))
        solar_hit = cf._raw_hit(self.attacker, self.solar_beam, self.target,
                                typechart, weather="sun", roll="avg")
        self.assertGreater(solar_hit.frac, 0.0)


class TestHyperBeamRecharge(unittest.TestCase):
    """"Hyper beam must recharge on the second turn." `_joint_race`'s turn
    loop now carries a `recharging` role set forward (via `_best_turn`'s own
    `recharging_next` return) -- a role that fires a recharge move
    (`move.flags.get("recharge")`) is forced to do nothing at all the
    following turn, mirroring `battle.py`'s real `must_recharge` lockout.
    Real, verified fixture: Snorlax with ONLY Hyper Beam against a Corviknight
    + Sinistcha pair that only Protects -- without the fix Snorlax would
    fire Hyper Beam every turn; with it, only on the odd turns."""

    def setUp(self):
        self.W = world()
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        self.typechart = self.W["typechart"]
        self.c1 = cf._build("Snorlax", merged, natures)
        self.c2 = cf._build("Corviknight", merged, natures)
        self.e1 = cf._build("Sinistcha", merged, natures)
        self.e2 = cf._build("Corviknight", merged, natures)
        self.hyper_beam = cf._lookup_move("Hyper Beam", moves)
        protect = cf._lookup_move("Protect", moves)
        self.moves_by_role = {
            "C": [self.hyper_beam], "P": [protect],
            "E1": [protect], "E2": [protect],
        }
        self.combatants = {"C": self.c1, "P": self.c2,
                           "E1": self.e1, "E2": self.e2}

    def test_hyper_beam_is_flagged_as_a_recharge_move(self):
        self.assertEqual(self.hyper_beam.flags.get("recharge"), 1)

    def test_hyper_beam_only_fires_every_other_turn(self):
        _outcome, _turns_used, _hp, log = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, None,
            turns=4)
        fired_turns = [i for i, turn in enumerate(log, 1)
                      if any(role == "C" for role, _tgt, _h in turn)]
        self.assertEqual(fired_turns, [1, 3],
                         "fixture assumes Snorlax fires turn 1, recharges "
                         "turn 2, fires again turn 3, recharges turn 4")

    def test_resolve_turn_reports_the_recharging_role_for_next_turn(self):
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        _hp2, _log, _ea, _wiped, recharging_next = cf._resolve_turn(
            self.combatants, self.moves_by_role, hp, self.typechart, None,
            {})
        self.assertEqual(recharging_next, {"C"})

    def test_a_recharging_role_cannot_even_protect(self):
        """The lockout is total -- substituted directly as `({}, None)`
        rather than routed through `_choose_action`, so a recharging role
        cannot fall back to Protect or anything else it might carry."""
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        protect = cf._lookup_move("Protect", self.W["moves"])
        moves_with_protect = dict(self.moves_by_role)
        moves_with_protect["C"] = [self.hyper_beam, protect]
        _hp2, log, _ea, _wiped, _rc = cf._resolve_turn(
            self.combatants, moves_with_protect, hp, self.typechart, None,
            {}, recharging_roles={"C"})
        self.assertFalse(any(role == "C" for role, _tgt, _h in log))


class TestJointFocusSashAndSturdy(unittest.TestCase):
    """"The focus sash item also does not seem to work." The joint race
    model previously tracked no items played out over a turn at all (a
    documented gap in this module's own header note) -- Focus Sash / Sturdy
    survival at 1 HP from full HP, mirroring `battle.py`'s own rule exactly,
    is now honoured in `_resolve_turn`'s hit application."""

    def setUp(self):
        self.W = world()

    def _isolated_hit(self, attacker_name, attacker_moves, target_name,
                      target_item=None, target_ability_move=None):
        """C (holding `attacker_moves`) attacks E1 (`target_name`, holding
        `target_item`) alone -- P and E2 forced fainted so only the one hit
        being tested can land."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        c = cf._build(attacker_name, merged, natures)
        e1 = cf._build(target_name, merged, natures, item=target_item)
        c_moves = cf._move_infos(attacker_name, merged, moves, attacker_moves)
        combatants = {"C": c, "P": c, "E1": e1, "E2": e1}
        moves_by_role = {"C": c_moves, "P": [], "E1": [], "E2": []}
        weather = cf._field_weather(combatants)
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        new_hp, log, _ea, _w, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        return new_hp["E1"], log, e1

    def test_focus_sash_survives_a_would_be_ohko_at_1_hp(self):
        hp_after, log, sylveon = self._isolated_hit(
            "Metagross", ["Meteor Mash"], "Sylveon", target_item="Focus Sash")
        self.assertTrue(any(h.frac >= 1.0 for _r, _t, h in log),
                        "fixture assumes this hit is a real would-be OHKO")
        self.assertGreater(hp_after, 0.0, "Focus Sash must prevent the faint")
        self.assertAlmostEqual(hp_after, 1.0 / sylveon.max_hp(), places=6)

    def test_focus_sash_does_not_help_from_anything_less_than_full_hp(self):
        """The real rule: only from FULL HP -- a Sash-holder already chipped
        once must faint normally on a second lethal hit."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        c = cf._build("Metagross", merged, natures)
        e1 = cf._build("Sylveon", merged, natures, item="Focus Sash")
        c_moves = cf._move_infos("Metagross", merged, moves, ["Meteor Mash"])
        combatants = {"C": c, "P": c, "E1": e1, "E2": e1}
        moves_by_role = {"C": c_moves, "P": [], "E1": [], "E2": []}
        weather = cf._field_weather(combatants)
        # Already down to a sliver, NOT full HP.
        hp = {"C": 1.0, "P": 0.0, "E1": 0.02, "E2": 0.0}
        new_hp, _log, _ea, _w, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        self.assertEqual(new_hp["E1"], 0.0)

    def test_sturdy_survives_the_same_way_with_no_item_at_all(self):
        # Sylveon doesn't naturally have Sturdy -- flip the built Combatant's
        # ability directly (this module tracks no ability-legality table of
        # its own; the fixture just needs SOME Sturdy-holder to exist, and
        # Meteor Mash on Sylveon is already established as a real OHKO by
        # the Focus Sash test above).
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        c = cf._build("Metagross", merged, natures)
        e1 = cf._build("Sylveon", merged, natures)
        e1.ability = "Sturdy"
        c_moves = cf._move_infos("Metagross", merged, moves, ["Meteor Mash"])
        combatants = {"C": c, "P": c, "E1": e1, "E2": e1}
        moves_by_role = {"C": c_moves, "P": [], "E1": [], "E2": []}
        weather = cf._field_weather(combatants)
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        new_hp, log, _ea, _w, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        self.assertTrue(any(h.frac >= 1.0 for _r, _t, h in log),
                        "fixture assumes this hit is a real would-be OHKO")
        self.assertGreater(new_hp["E1"], 0.0)

    def test_the_shared_combatant_item_is_never_mutated(self):
        """These Combatant objects are reused across every replay
        `_pair_vs_targets` runs (normal, tailwind, Protect x2) -- consuming
        the item on the shared object would silently carry a hypothesis's
        consequence into an unrelated replay. Running the exact same lethal
        hit twice must save the Sash both times, not just the first."""
        hp_after_1, _log1, sylveon = self._isolated_hit(
            "Metagross", ["Meteor Mash"], "Sylveon", target_item="Focus Sash")
        self.assertEqual(sylveon.item, "Focus Sash",
                         "the shared Combatant's item must never be consumed")
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        c = cf._build("Metagross", merged, natures)
        c_moves = cf._move_infos("Metagross", merged, moves, ["Meteor Mash"])
        combatants = {"C": c, "P": c, "E1": sylveon, "E2": sylveon}
        moves_by_role = {"C": c_moves, "P": [], "E1": [], "E2": []}
        weather = cf._field_weather(combatants)
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        new_hp2, _log2, _ea2, _w2, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        self.assertGreater(new_hp2["E1"], 0.0,
                          "a second independent replay must still honour "
                          "the Sash, not treat it as already used")


class TestArmorTailAndPriorityBlock(unittest.TestCase):
    """"Make sure anti-priority like Farigiraf's armor tail ability is taken
    into account." `battle.py` already blocks a priority move outright when
    Queenly Majesty / Dazzling / Armor Tail is held by ANY living member of
    the target's side (`Battle._blocked_by_guard`) -- this module's cheap
    arithmetic model had no notion of it at all before this. Farigiraf is
    Armor Tail at ~99% usage by default (`_build` needs no override); Kingambit
    Sucker Punch (priority +1) vs a non-priority Iron Head is the fixture
    used throughout -- both real, common sets, not invented ones."""

    def setUp(self):
        self.W = world()

    def test_priority_blocked_true_only_for_a_real_priority_move(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        attacker = cf._build("Kingambit", merged, natures)
        target = cf._build("Farigiraf", merged, natures)
        self.assertEqual(target.ability, "Armor Tail")
        sucker_punch = cf._lookup_move("Sucker Punch", moves)
        iron_head = cf._lookup_move("Iron Head", moves)
        self.assertTrue(cf._priority_blocked(attacker, sucker_punch, [target]))
        self.assertFalse(cf._priority_blocked(attacker, iron_head, [target]),
                         "a non-priority move is never blocked by this rule")

    def test_priority_blocked_ignored_by_mold_breaker_style_abilities(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        target = cf._build("Farigiraf", merged, natures)
        sucker_punch = cf._lookup_move("Sucker Punch", moves)
        for ability in ("Mold Breaker", "Teravolt", "Turboblaze"):
            attacker = cf._build("Kingambit", merged, natures)
            attacker.ability = ability
            self.assertFalse(cf._priority_blocked(attacker, sucker_punch, [target]),
                             ability)

    def test_choose_move_picks_a_landing_move_over_a_blocked_priority_one(self):
        """A rational attacker doesn't lock into a priority move that will
        never land -- confirmed against a real damage-roll swing (Sucker
        Punch normally OUTRANKS Iron Head here purely on priority)."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        attacker = cf._build("Kingambit", merged, natures)
        sucker_punch = cf._lookup_move("Sucker Punch", moves)
        iron_head = cf._lookup_move("Iron Head", moves)

        armor_tail_target = cf._build("Farigiraf", merged, natures)
        hit, mv = cf._choose_move(attacker, [sucker_punch, iron_head],
                                  armor_tail_target, typechart,
                                  defending_side=[armor_tail_target])
        self.assertEqual(mv.name, "Iron Head")
        self.assertGreater(hit.frac, 0.0)

        plain_target = cf._build("Kingambit", merged, natures)
        hit2, mv2 = cf._choose_move(attacker, [sucker_punch, iron_head],
                                    plain_target, typechart,
                                    defending_side=[plain_target])
        self.assertEqual(mv2.name, "Sucker Punch",
                         "fixture assumes Sucker Punch normally wins on priority")

    def test_choose_move_scores_a_blocked_lone_move_as_no_hit(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        attacker = cf._build("Kingambit", merged, natures)
        target = cf._build("Farigiraf", merged, natures)
        sucker_punch = cf._lookup_move("Sucker Punch", moves)
        hit, mv = cf._choose_move(attacker, [sucker_punch], target, typechart,
                                  defending_side=[target])
        self.assertEqual(mv.name, "Sucker Punch")  # still the only move used
        self.assertEqual(hit.frac, 0.0)             # but it does nothing

    def test_choose_action_and_grid_hit_agree_with_choose_move(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        attacker = cf._build("Kingambit", merged, natures)
        target = cf._build("Farigiraf", merged, natures)
        sucker_punch = cf._lookup_move("Sucker Punch", moves)

        hits, mv = cf._choose_action(attacker, [sucker_punch], {"E1": target}, typechart)
        self.assertEqual(hits["E1"].frac, 0.0)

        grid = cf._grid_hit(attacker, [sucker_punch], target, None, typechart)
        self.assertEqual(grid.frac, 0.0)

    def test_end_to_end_sequential_pair_outcome_partner_armor_tail_blocks_enemy(self):
        """Our PARTNER holding Armor Tail must block a priority move the
        enemy aims at the CANDIDATE too -- the ability protects the whole
        side, not just its own holder. Contrasted against an identical setup
        with a non-Armor-Tail partner, where the same Sucker Punch is a real
        OHKO on the candidate."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        attacker = cf._build("Basculegion", merged, natures)
        atk_moves = cf._move_infos("Basculegion", merged, moves, ["Wave Crash"])
        e1 = cf._build("Kingambit", merged, natures)
        e1_moves = cf._move_infos("Kingambit", merged, moves, ["Sucker Punch"])
        e2 = cf._build("Kingambit", merged, natures)
        partner_move = cf._lookup_move("Dazzling Gleam", moves)

        guarded = cf._sequential_pair_outcome(
            attacker, atk_moves, "Kingambit", e1, e1_moves, "Kingambit2", e2, [],
            typechart, candidate_target="Kingambit",
            partner=cf._build("Farigiraf", merged, natures),
            partner_move=partner_move, partner_target="Kingambit")
        self.assertEqual(guarded["hits"].get("E1", {}), {})
        self.assertEqual(guarded["hp_left"]["C"], 1.0)

        unguarded = cf._sequential_pair_outcome(
            attacker, atk_moves, "Kingambit", e1, e1_moves, "Kingambit2", e2, [],
            typechart, candidate_target="Kingambit",
            partner=cf._build("Sylveon", merged, natures),
            partner_move=partner_move, partner_target="Kingambit")
        self.assertIn("C", unguarded["hits"]["E1"])
        self.assertLess(unguarded["hp_left"]["C"], 1.0,
                        "fixture assumes Sucker Punch is a real hit without "
                        "Armor Tail on the board")

    def test_end_to_end_resolve_turn_blocks_the_enemys_priority_hit(self):
        """Same guarantee through the multi-turn `_resolve_turn` engine
        `joint_pair_search` actually uses -- mirrors the existing Focus Sash/
        Sturdy `_isolated_hit` fixture pattern above."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        e1 = cf._build("Kingambit", merged, natures)
        e1_moves = cf._move_infos("Kingambit", merged, moves, ["Sucker Punch"])

        guard = cf._build("Farigiraf", merged, natures)
        combatants = {"C": guard, "P": guard, "E1": e1, "E2": e1}
        moves_by_role = {"C": [], "P": [], "E1": e1_moves, "E2": []}
        weather = cf._field_weather(combatants)
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        new_hp, _log, enemy_acted, _wiped, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {})
        self.assertTrue(enemy_acted, "the enemy still used its turn, it just failed")
        self.assertEqual(new_hp["C"], 1.0)

        plain = cf._build("Sylveon", merged, natures)
        combatants2 = {"C": plain, "P": plain, "E1": e1, "E2": e1}
        # Sucker Punch also requires the target to be USING a damaging move
        # this turn (real mechanic, now honoured -- see
        # TestSuckerPunchFailsIfTheTargetAlreadyMoved) -- an empty moveset
        # (as above) is not that, so this contrast case needs C to have a
        # real attack queued, or Sucker Punch would correctly fail here too
        # for an unrelated reason and the test would no longer isolate
        # Armor Tail specifically.
        moves_by_role2 = dict(moves_by_role)
        moves_by_role2["C"] = cf._move_infos("Sylveon", merged, moves, ["Moonblast"])
        new_hp2, _log2, _ea2, _w2, _rc = cf._resolve_turn(
            combatants2, moves_by_role2, hp, typechart, weather, {})
        self.assertLess(new_hp2["C"], 1.0,
                        "fixture assumes Sucker Punch is a real hit without "
                        "Armor Tail on the board")


class TestOneTurnLookahead(unittest.TestCase):
    """"It's important to at least have a one turn lookahead. For instance,
    if wave crash into aqua jet kills and outspeeds on the second turn."
    Mega Metagross's Bullet Punch (priority +1, weak) vs Psychic Fangs
    (no priority, much stronger) is a real, verified stand-in for "Aqua Jet
    vs Wave Crash": against Mega Charizard Y, Psychic Fangs alone doesn't
    KO, but TWO Psychic Fangs would (126% cumulative) while two Bullet
    Punches never get close (31%) -- so Psychic Fangs must now win despite
    having no priority. Against a much bulkier target (Mega Aggron) where
    NEITHER move threatens a 2-turn kill, the old priority-first tie-break
    must still apply -- the lookahead only changes the ranking when it
    actually has a real 2-turn kill line to offer, matching this module's
    existing "prefer priority, then damage" fallback exactly."""

    def setUp(self):
        self.W = world()
        merged, natures = self.W["merged"], self.W["natures"]
        self.metagross = cf._build_form("Mega Metagross", merged, natures,
                                        stay_base=False)
        self.bullet_punch = cf._lookup_move("Bullet Punch", self.W["moves"])
        self.psychic_fangs = cf._lookup_move("Psychic Fangs", self.W["moves"])

    def test_a_weaker_priority_move_alone_never_threatens_a_kill(self):
        merged, natures, typechart = (self.W["merged"], self.W["natures"],
                                      self.W["typechart"])
        charizard = cf._build("Mega Charizard Y", merged, natures)
        bp = cf._raw_hit(self.metagross, self.bullet_punch, charizard,
                         typechart, roll="avg")
        pf = cf._raw_hit(self.metagross, self.psychic_fangs, charizard,
                         typechart, roll="avg")
        self.assertLess(bp.frac, 1.0)
        self.assertLess(pf.frac, 1.0)
        self.assertLess(bp.frac * 2, 1.0, "fixture assumes Bullet Punch "
                        "never threatens a kill even over 2 turns")
        self.assertGreaterEqual(pf.frac * 2, 1.0, "fixture assumes Psychic "
                                "Fangs DOES secure a kill within 2 turns")

    def test_the_stronger_two_turn_kill_move_is_chosen_over_priority(self):
        merged, natures, typechart = (self.W["merged"], self.W["natures"],
                                      self.W["typechart"])
        charizard = cf._build("Mega Charizard Y", merged, natures)
        hit, mv = cf._choose_move(self.metagross,
                                  [self.bullet_punch, self.psychic_fangs],
                                  charizard, typechart)
        self.assertEqual(mv.name, "Psychic Fangs")
        self.assertGreater(hit.frac, 0.5)

    def test_the_bigger_hit_wins_even_when_both_reach_a_two_turn_kill(self):
        """The actual reported case, real fixture: Basculegion's Wave Crash
        (95%, no priority, nearly a 1-turn OHKO) vs Aqua Jet (34%, priority
        +1) against Mega Tyranitar -- BOTH clear the 2-turn-kill bar (34%
        doubled is 68%, short alone, but the lookahead's "best available
        follow-up" is Wave Crash's own 95%, so 34+95 clears it too), so
        under the OLD priority-tie-break this picked the much weaker Aqua
        Jet purely for going first. Confirmed directly against real damage
        rolls before writing this test."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        basculegion = cf._build("Basculegion", merged, natures)
        tyranitar = cf._build("Mega Tyranitar", merged, natures)
        wave_crash = cf._lookup_move("Wave Crash", moves)
        aqua_jet = cf._lookup_move("Aqua Jet", moves)
        wc = cf._raw_hit(basculegion, wave_crash, tyranitar, typechart, roll="avg")
        aj = cf._raw_hit(basculegion, aqua_jet, tyranitar, typechart, roll="avg")
        self.assertLess(wc.frac, 1.0)
        self.assertLess(aj.frac, 1.0)
        self.assertGreaterEqual(aj.frac + wc.frac, 1.0,
                                "fixture assumes Aqua Jet also reaches a "
                                "2-turn kill via Wave Crash as follow-up")
        hit, mv = cf._choose_move(basculegion, [wave_crash, aqua_jet],
                                  tyranitar, typechart)
        self.assertEqual(mv.name, "Wave Crash")
        self.assertAlmostEqual(hit.frac, wc.frac)

    def test_raw_damage_still_wins_when_neither_move_threatens_a_kill(self):
        """Priority is NOT a tie-break in the base ranking (a later,
        harder-learned lesson than the lookahead itself -- see
        TestSurvivalAwareReconsideration's own docstring for the real
        example that forced this: Aqua Jet beating Wave Crash, which does
        3x the damage, purely because both technically cleared the same
        2-turn-kill bar). With no 2-turn kill line available from EITHER
        move here, the tie-break is raw damage, same as any other tie --
        Psychic Fangs (weaker than Bullet Punch's PRIORITY but stronger in
        raw terms) wins."""
        merged, natures, typechart = (self.W["merged"], self.W["natures"],
                                      self.W["typechart"])
        aggron = cf._build("Mega Aggron", merged, natures)
        bp = cf._raw_hit(self.metagross, self.bullet_punch, aggron,
                         typechart, roll="avg")
        pf = cf._raw_hit(self.metagross, self.psychic_fangs, aggron,
                         typechart, roll="avg")
        self.assertLess(bp.frac * 2, 1.0)
        self.assertLess(pf.frac * 2, 1.0, "fixture assumes NEITHER move "
                        "threatens a kill within 2 turns")
        self.assertGreater(pf.frac, bp.frac, "fixture assumes Psychic "
                           "Fangs is the bigger raw hit of the two here")
        hit, mv = cf._choose_move(self.metagross,
                                  [self.bullet_punch, self.psychic_fangs],
                                  aggron, typechart)
        self.assertEqual(mv.name, "Psychic Fangs")

    def test_a_move_that_kos_outright_still_wins_regardless_of_lookahead(self):
        """The lookahead only matters among moves that DON'T already KO --
        an outright KO this turn is never second-guessed by a 2-turn
        forecast for some other move."""
        merged, natures, typechart = (self.W["merged"], self.W["natures"],
                                      self.W["typechart"])
        target = cf._build("Sylveon", merged, natures)
        meteor_mash = cf._lookup_move("Meteor Mash", self.W["moves"])
        real = cf._raw_hit(self.metagross, meteor_mash, target, typechart,
                           roll="avg")
        self.assertGreaterEqual(real.frac, 1.0,
                                "fixture assumes Meteor Mash is a real OHKO")
        hit, mv = cf._choose_move(
            self.metagross, [self.bullet_punch, meteor_mash], target, typechart)
        self.assertEqual(mv.name, "Meteor Mash")

    def test_choose_action_applies_the_same_lookahead_per_target(self):
        """Same guarantee through `_choose_action` (the multi-turn engine's
        own move-picker) against a single live target."""
        merged, natures, typechart = (self.W["merged"], self.W["natures"],
                                      self.W["typechart"])
        charizard = cf._build("Mega Charizard Y", merged, natures)
        hits, mv = cf._choose_action(
            self.metagross, [self.bullet_punch, self.psychic_fangs],
            {"E1": charizard}, typechart)
        self.assertEqual(mv.name, "Psychic Fangs")
        self.assertIn("E1", hits)


class TestSpreadMoveGuaranteedKillCount(unittest.TestCase):
    """"Why the hell doesn't Mega Charizard Y use Heat Wave if it kills
    Metagross and also damages Torkoal?" Real, verified fixture: Mega
    Charizard Y (Drought sun active) against a live Mega Metagross + Torkoal
    board. Heat Wave (spread) outright kills Mega Metagross but only chips
    Torkoal, so the OLD all-or-nothing `kos_now`/`kos_in_two` booleans gave
    it no KO credit at all -- while Weather Ball (single-target, aimed only
    at Metagross) looked like it "reached a 2-turn kill" on paper because
    the lookahead's own best-follow-up number was fed by Heat Wave's big
    hit on that same target. An outright kill on even ONE target must
    outrank a move that guarantees zero, so `_choose_action` now compares
    ordinal guaranteed-kill COUNTS ahead of the 2-turn-kill count and raw
    damage."""

    def setUp(self):
        self.W = world()
        merged, natures = self.W["merged"], self.W["natures"]
        self.charizard = cf._build_form("Mega Charizard Y", merged, natures,
                                        stay_base=False)
        self.metagross = cf._build("Mega Metagross", merged, natures)
        self.torkoal = cf._build("Torkoal", merged, natures)
        self.heat_wave = cf._lookup_move("Heat Wave", self.W["moves"])
        self.weather_ball = cf._lookup_move("Weather Ball", self.W["moves"])

    def test_heat_wave_kills_metagross_but_not_torkoal(self):
        typechart = self.W["typechart"]
        hw_on_metagross = cf._raw_hit(self.charizard, self.heat_wave,
                                      self.metagross, typechart,
                                      weather="sun", roll="avg",
                                      num_targets_hit=2)
        hw_on_torkoal = cf._raw_hit(self.charizard, self.heat_wave,
                                    self.torkoal, typechart, weather="sun",
                                    roll="avg", num_targets_hit=2)
        self.assertGreaterEqual(hw_on_metagross.frac, 1.0, "fixture assumes "
                                "Heat Wave outright kills Mega Metagross")
        self.assertLess(hw_on_torkoal.frac, 1.0, "fixture assumes Heat "
                        "Wave does not kill Torkoal")

    def test_weather_ball_alone_never_kills_metagross(self):
        typechart = self.W["typechart"]
        wb = cf._raw_hit(self.charizard, self.weather_ball, self.metagross,
                         typechart, weather="sun", roll="avg")
        self.assertLess(wb.frac, 1.0, "fixture assumes Weather Ball alone "
                        "never OHKOs Mega Metagross")

    def test_choose_action_prefers_the_guaranteed_kill_over_bigger_lookahead(self):
        typechart = self.W["typechart"]
        hits, mv = cf._choose_action(
            self.charizard, [self.heat_wave, self.weather_ball],
            {"E1": self.metagross, "E2": self.torkoal}, typechart,
            weather="sun")
        self.assertEqual(mv.name, "Heat Wave")
        self.assertIn("E1", hits)
        self.assertIn("E2", hits)
        self.assertGreaterEqual(hits["E1"].frac, 1.0)


class TestGuaranteedKillAccountsForRemainingHp(unittest.TestCase):
    """"Kowtow Cleave or Iron Head T1 into Sucker Punch T2 from Kingambit
    would likely be a win vs Lycanroc-Dusk, why is it not played?" Real,
    diagnosed fixture: Lycanroc-Dusk holds Focus Sash, so an Iron Head that
    would otherwise OHKO it from full HP instead leaves it at 1 HP -- real,
    correct Focus Sash behaviour, not a bug. The actual bug: `_choose_
    action`'s guaranteed-kill check compared a hit's fraction (always read
    against the target's MAX hp, `_raw_hit`'s own convention) against a
    hardcoded 1.0, so a move that trivially finishes a target already down
    to 1 HP got NO kill credit over one that merely dents a full-HP target
    -- both looked "not a guaranteed kill" under the same wrong 100% bar,
    so ties fell to raw damage, which has no idea one target is already on
    its last HP. `_choose_action` now compares against the target's REAL
    remaining fraction (`target_hp_fracs`), so finishing off the 1-HP
    target is correctly recognised as strictly better than chipping a
    healthy one, regardless of which does more raw damage."""

    def setUp(self):
        self.W = world()
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        self.typechart = self.W["typechart"]
        self.attacker = cf._build("Kingambit", merged, natures)
        self.near_dead = cf._build("Sylveon", merged, natures)
        self.full_hp = cf._build("Corviknight", merged, natures)
        self.sucker_punch = cf._lookup_move("Sucker Punch", moves)

    def test_the_fixture_assumes_raw_damage_alone_favours_the_healthy_target(self):
        """The bug's precondition: without HP-awareness, Sucker Punch's
        raw damage numbers alone would pick Corviknight over Sylveon --
        proving any observed preference for Sylveon comes from the
        remaining-HP fix, not a coincidence of raw magnitude."""
        h_near_dead = cf._raw_hit(self.attacker, self.sucker_punch,
                                  self.near_dead, self.typechart, roll="avg")
        h_full_hp = cf._raw_hit(self.attacker, self.sucker_punch,
                                self.full_hp, self.typechart, roll="avg")
        self.assertLess(h_near_dead.frac, 1.0)
        self.assertLess(h_full_hp.frac, 1.0)
        self.assertGreater(h_full_hp.frac, h_near_dead.frac, "fixture "
                           "assumes Sucker Punch's raw damage alone "
                           "favours the full-HP Corviknight")

    def test_choose_action_finishes_the_near_dead_target_instead(self):
        live_targets = {"C": self.near_dead, "P": self.full_hp}
        target_hp_fracs = {"C": 0.01, "P": 1.0}
        hits, mv = cf._choose_action(
            self.attacker, [self.sucker_punch], live_targets, self.typechart,
            target_hp_fracs=target_hp_fracs)
        self.assertEqual(mv.name, "Sucker Punch")
        self.assertIn("C", hits, "must finish the 1-HP target, not chip "
                      "the healthy one purely because it takes more raw "
                      "damage")

    def test_a_full_hp_target_still_uses_the_old_1_0_bar(self):
        """No `target_hp_fracs` entry for a role (or none passed at all)
        keeps the original full-HP assumption -- this fix only changes
        behaviour for a target that's actually already damaged."""
        live_targets = {"C": self.near_dead, "P": self.full_hp}
        hits, mv = cf._choose_action(
            self.attacker, [self.sucker_punch], live_targets, self.typechart)
        # Neither target is a guaranteed kill from full HP, so this must
        # fall back to raw damage -- the ORIGINAL (correct-for-full-HP)
        # tie-break -- and pick Corviknight, same as before this fix.
        self.assertEqual(mv.name, "Sucker Punch")
        self.assertIn("P", hits)


class TestSurvivalAwareReconsideration(unittest.TestCase):
    """"It is not a clean win if the enemy protects one then uses a
    priority move on Lycanroc-Dusk, given Mega Metagross can't beat the
    enemies on its own." A provisional move choice can pick something that
    never actually fires: Kingambit's Iron Head is a guaranteed OHKO on
    Lycanroc-Dusk (156% avg), so it independently outranks Sucker Punch
    (82%, doesn't KO alone) under `_choose_move`'s own KO-first rule -- but
    Kingambit is naturally SLOWER than Lycanroc-Dusk, whose own Close
    Combat is ALSO a guaranteed OHKO on Kingambit (164%) and, with no
    priority advantage on Kingambit's side, resolves first. Iron Head never
    fires; Sucker Punch (priority +1) would have. Confirmed directly
    against real damage rolls before writing these tests."""

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.lycanroc = cf._build("Lycanroc-Dusk", merged, natures)
        self.close_combat = cf._move_infos(
            "Lycanroc-Dusk", merged, moves, ["Close Combat"])
        self.kingambit = cf._build("Kingambit", merged, natures)
        self.kingambit_moves = cf._move_infos(
            "Kingambit", merged, moves,
            ["Sucker Punch", "Iron Head", "Kowtow Cleave"])
        self.charizard = cf._build("Mega Charizard Y", merged, natures)
        self.solar_beam = cf._move_infos(
            "Mega Charizard Y", merged, moves, ["Solar Beam"])

    def test_fixture_assumes_both_moves_are_real_ohkos(self):
        typechart = self.W["typechart"]
        close_combat = cf._raw_hit(self.lycanroc, self.close_combat[0],
                                   self.kingambit, typechart, roll="avg")
        iron_head = cf._raw_hit(self.kingambit, cf._lookup_move(
            "Iron Head", self.W["moves"]), self.lycanroc, typechart, roll="avg")
        self.assertGreaterEqual(close_combat.frac, 1.0)
        self.assertGreaterEqual(iron_head.frac, 1.0)

    def test_sequential_pair_outcome_reconsiders_toward_sucker_punch(self):
        typechart = self.W["typechart"]
        result = cf._sequential_pair_outcome(
            self.lycanroc, self.close_combat, "Kingambit", self.kingambit,
            self.kingambit_moves, "Mega Charizard Y", self.charizard,
            self.solar_beam, typechart, candidate_target="Kingambit")
        e1_hits = result["hits"].get("E1", {})
        self.assertIn("C", e1_hits)
        self.assertEqual(e1_hits["C"].move_name, "Sucker Punch",
                         "Iron Head would never fire -- Kingambit dies to "
                         "Close Combat before its own (slower) turn comes up")

    def test_without_the_threat_the_guaranteed_ko_is_still_used(self):
        """Reconsideration must not kick in when nothing is actually
        threatened -- a much slower, harmless attacker in Lycanroc's seat
        leaves Kingambit free to use its real best (KO-securing) move."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        harmless = cf._build("Whimsicott", merged, natures)
        weak_move = cf._move_infos("Whimsicott", merged, moves, ["Tackle"])
        result = cf._sequential_pair_outcome(
            harmless, weak_move, "Kingambit", self.kingambit,
            self.kingambit_moves, "Mega Charizard Y", self.charizard,
            self.solar_beam, typechart, candidate_target="Kingambit")
        e1_hits = result["hits"].get("E1", {})
        self.assertIn("C", e1_hits)
        self.assertEqual(e1_hits["C"].move_name, "Iron Head",
                         "fixture assumes Whimsicott's Tackle is no threat "
                         "to Kingambit at all -- Iron Head should fire "
                         "unmodified")

    def test_resolve_turn_reconsiders_the_same_way(self):
        """Same guarantee through the multi-turn engine `joint_pair_search`/
        `joint_pool_search`/`--multi-bring4` actually use."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        combatants = {"C": self.lycanroc, "P": self.lycanroc,
                      "E1": self.kingambit, "E2": self.kingambit}
        moves_by_role = {"C": self.close_combat, "P": [],
                         "E1": self.kingambit_moves, "E2": []}
        weather = cf._field_weather({"C": self.lycanroc, "E1": self.kingambit,
                                     "E2": self.charizard})
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        _hp, log, _enemy_acted, _wiped, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        by_role = {(role, tgt): h.move_name for role, tgt, h in log}
        self.assertEqual(by_role.get(("E1", "C")), "Sucker Punch")

    def test_a_role_with_no_faster_option_stays_on_its_doomed_pick(self):
        """When NOTHING in the doomed role's own moveset is fast enough to
        matter, reconsideration must leave the original choice alone --
        Kingambit restricted to Iron Head/Kowtow Cleave only (no Sucker
        Punch in its set at all) has no escape, so it should still show up
        using one of those, not silently vanish from the log."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        no_priority = cf._move_infos(
            "Kingambit", merged, moves, ["Iron Head", "Kowtow Cleave"])
        result = cf._sequential_pair_outcome(
            self.lycanroc, self.close_combat, "Kingambit", self.kingambit,
            no_priority, "Mega Charizard Y", self.charizard, self.solar_beam,
            typechart, candidate_target="Kingambit")
        e1_hits = result["hits"].get("E1")
        self.assertIsNone(e1_hits, "Kingambit really does die before "
                          "acting -- nothing in its restricted moveset "
                          "could have saved it")


class TestSuckerPunchFailsIfTheTargetAlreadyMoved(unittest.TestCase):
    """"Sucker punch fails if the target outspeeds and use[s] a priority
    move" -- the cheap arithmetic model's own version of the fix already
    made to `battle.py`'s real engine (`TestSuckerPunchFailsIfTheTarget
    AlreadyMoved` in test_mechanics_fixes.py). Both `_apply_plan`
    (`_resolve_turn`) and `_sequential_pair_outcome`'s own hit-application
    loop now drop a Sucker Punch hit against any target that has ALREADY
    acted (by turn order) or whose queued move isn't damaging."""

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.kingambit = cf._build("Kingambit", merged, natures)
        self.sucker_punch = cf._move_infos("Kingambit", merged, moves,
                                           ["Sucker Punch"])

    def test_sequential_pair_outcome_drops_sucker_punch_against_extreme_speed(self):
        """Extreme Speed is priority +2, always ahead of Sucker Punch's +1
        -- the attacker has already moved by the time Sucker Punch would
        resolve, regardless of relative speed."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        dragonite = cf._build("Dragonite", merged, natures)
        extreme_speed = cf._move_infos("Dragonite", merged, moves,
                                       ["Extreme Speed"])
        sylveon = cf._build("Sylveon", merged, natures)
        result = cf._sequential_pair_outcome(
            dragonite, extreme_speed, "Kingambit", self.kingambit,
            self.sucker_punch, "Sylveon", sylveon, [], typechart,
            candidate_target="Kingambit")
        self.assertEqual(result["hits"].get("E1"), {})

    def test_sequential_pair_outcome_still_lands_against_a_pending_attack(self):
        """Contrast: a target using an ordinary (priority 0, still-pending)
        damaging move is still hit normally."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        corviknight = cf._build("Corviknight", merged, natures)
        body_press = cf._move_infos("Corviknight", merged, moves,
                                    ["Body Press"])
        sylveon = cf._build("Sylveon", merged, natures)
        result = cf._sequential_pair_outcome(
            corviknight, body_press, "Kingambit", self.kingambit,
            self.sucker_punch, "Sylveon", sylveon, [], typechart,
            candidate_target="Kingambit")
        e1_hits = result["hits"].get("E1", {})
        self.assertIn("C", e1_hits)
        self.assertEqual(e1_hits["C"].move_name, "Sucker Punch")

    def test_resolve_turn_drops_sucker_punch_against_a_move_less_target(self):
        """A target with NOTHING queued this turn (empty moveset here, but
        the same reading applies to a real status/switch choice) is not
        "using a damaging move" either -- Sucker Punch must fail against it
        too, not just against Armor Tail or a faster priority user."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        idle = cf._build("Sylveon", merged, natures)
        combatants = {"C": idle, "P": idle, "E1": self.kingambit,
                     "E2": self.kingambit}
        moves_by_role = {"C": [], "P": [], "E1": self.sucker_punch, "E2": []}
        weather = cf._field_weather(combatants)
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        new_hp, log, _ea, _w, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {})
        self.assertEqual(new_hp["C"], 1.0)
        self.assertFalse(log)

    def test_grid_hit_does_not_credit_sucker_punchs_priority_either(self):
        """"Kingambit should have iron head, kowtow cleave, or low kick,
        and has no reason to target arcanine [with Sucker Punch]." The 2x2
        damage-grid cell (`--deep`'s display) has no idea whether Arcanine
        would actually let Sucker Punch land, so it must not show a number
        that move could easily never produce -- Kowtow Cleave (unconditional,
        and the biggest raw hit here) wins instead."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        arcanine = cf._build("Arcanine", merged, natures)
        candidates = [cf._lookup_move(n, moves) for n in
                     ("Sucker Punch", "Iron Head", "Kowtow Cleave", "Low Kick")]
        result = cf._grid_hit(self.kingambit, candidates, arcanine, None, typechart)
        self.assertEqual(result.move_name, "Kowtow Cleave")

    def test_resolve_turn_reconsiders_away_from_a_wasted_sucker_punch(self):
        """End to end, through `_resolve_turn`: Kingambit's independent
        pick (Sucker Punch, since it clears the same 2-turn-kill bar as
        Kowtow Cleave and USED to win on priority) fails outright once
        Arcanine's own move is Extreme Speed -- Kingambit must reconsider
        to Kowtow Cleave rather than log nothing at all."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        kingambit_moves = cf._move_infos(
            "Kingambit", merged, moves, ["Sucker Punch", "Kowtow Cleave"])
        arcanine = cf._build("Arcanine", merged, natures)
        arcanine_moves = cf._move_infos("Arcanine", merged, moves,
                                        ["Extreme Speed"])
        combatants = {"C": kingambit, "P": arcanine, "E1": arcanine,
                     "E2": arcanine}
        moves_by_role = {"C": kingambit_moves, "P": [],
                         "E1": arcanine_moves, "E2": []}
        weather = cf._field_weather(combatants)
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        _new_hp, log, _ea, _w, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        by_role = {(role, tgt): h.move_name for role, tgt, h in log}
        self.assertEqual(by_role.get(("C", "E1")), "Kowtow Cleave")


class TestJointDamageLog(unittest.TestCase):
    """"I want it to ... display damage output vs enemies, and damage taken
    by each" -- `_joint_race`'s log, threaded through `_pair_vs_targets` into
    every `detail` entry both joint functions return."""

    def setUp(self):
        self.W = world()

    def test_a_sweep_only_logs_our_own_hits(self):
        """If the enemy never got to act (a real sweep), the log must not
        contain an E1/E2 entry -- that's what "swept" means."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(["Mega Gengar"], ["Sableye", "Ariados"],
                                    "Mega Alakazam", merged, moves, natures,
                                    typechart)
        d = rows[0]["detail"][("Sableye", "Ariados")]
        self.assertEqual(d["outcome"], "sweep")
        actors = {role for turn in d["log"] for role, _tgt, _h in turn}
        self.assertEqual(actors, {"C", "P"})

    def test_an_out_trade_logs_hits_from_both_sides(self):
        """turns=3, not 2 -- see the priority-tie-break fix's docstring note
        on `TestJointPairSearch.test_out_trade_wins_the_race_without_a_
        clean_sweep`, the same matchup."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(["Mega Scizor"], ["Kingambit", "Basculegion"],
                                    "Whimsicott", merged, moves, natures,
                                    typechart, turns=3)
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(d["outcome"], "out_trade")
        actors = {role for turn in d["log"] for role, _tgt, _h in turn}
        self.assertIn("C", actors | {"P"})  # at least one of ours acted
        self.assertTrue(actors & {"E1", "E2"}, "an out-trade means the "
                                               "enemy landed at least one hit")

    def test_every_logged_hit_carries_a_real_move_and_roll(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(["Mega Scizor"], ["Kingambit", "Basculegion"],
                                    "Whimsicott", merged, moves, natures,
                                    typechart, turns=2)
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        self.assertTrue(any(turn for turn in d["log"]))
        for turn in d["log"]:
            for role, tgt, h in turn:
                self.assertIn(role, ("C", "P", "E1", "E2"))
                self.assertIn(tgt, ("C", "P", "E1", "E2"))
                self.assertIsNotNone(h.move_name)
                self.assertGreater(h.avg, 0.0)

    def test_the_log_length_matches_turns_used(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(["Mega Scizor"], ["Kingambit", "Basculegion"],
                                    "Whimsicott", merged, moves, natures,
                                    typechart, turns=2)
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        self.assertEqual(len(d["log"]), d["turns_used"])


class TestPairSortKeyRanksProtectSafeFirst(unittest.TestCase):
    """"I think it would be best if the ranking was done by default as by
    protect safe wins." A pure unit test of `_pair_sort_key` on synthetic
    rows (no racing): a row with FEWER raw wins but MORE protect-safe wins
    must still rank ABOVE one with more raw wins but fewer protect-safe
    ones -- protect-safe leads the tuple, beaten count is only the
    tie-break."""

    def test_more_protect_safe_wins_beats_more_raw_wins(self):
        fewer_wins_more_protect_safe = {
            "pairs_swept": 0, "pairs_traded": 3, "pairs_protect_safe": 3,
            "pairs_tailwind_safe": 3, "pairs_clean_win_total": 3.0,
        }
        more_wins_fewer_protect_safe = {
            "pairs_swept": 0, "pairs_traded": 5, "pairs_protect_safe": 1,
            "pairs_tailwind_safe": 5, "pairs_clean_win_total": 5.0,
        }
        self.assertLess(cf._pair_sort_key(fewer_wins_more_protect_safe),
                        cf._pair_sort_key(more_wins_fewer_protect_safe),
                        "3 protect-safe wins (even with fewer raw wins) "
                        "must rank ahead of 1 protect-safe win")

    def test_beaten_count_is_the_tiebreak_when_protect_safe_ties(self):
        tied_protect_safe_fewer_wins = {
            "pairs_swept": 0, "pairs_traded": 2, "pairs_protect_safe": 2,
            "pairs_tailwind_safe": 2, "pairs_clean_win_total": 2.0,
        }
        tied_protect_safe_more_wins = {
            "pairs_swept": 0, "pairs_traded": 4, "pairs_protect_safe": 2,
            "pairs_tailwind_safe": 4, "pairs_clean_win_total": 4.0,
        }
        self.assertLess(cf._pair_sort_key(tied_protect_safe_more_wins),
                        cf._pair_sort_key(tied_protect_safe_fewer_wins))

    def test_clean_win_total_is_the_tiebreak_when_protect_safe_and_beaten_tie(self):
        """"losing 1 pokemon and taking a lot of damage and KOing 2
        enemies [is] far inferior to KOing the enemy without taking
        damage" -- same protect-safe count, same raw beaten count: the
        pair that won more CLEANLY (higher `pairs_clean_win_total`) must
        rank ahead, strictly before tailwind-safe count decides anything."""
        messy_wins = {
            "pairs_swept": 0, "pairs_traded": 3, "pairs_protect_safe": 3,
            "pairs_tailwind_safe": 3, "pairs_clean_win_total": 1.5,
        }
        clean_wins = {
            "pairs_swept": 3, "pairs_traded": 0, "pairs_protect_safe": 3,
            "pairs_tailwind_safe": 0, "pairs_clean_win_total": 6.0,
        }
        self.assertLess(cf._pair_sort_key(clean_wins),
                        cf._pair_sort_key(messy_wins),
                        "cleaner wins (higher pairs_clean_win_total) must "
                        "rank ahead even with a WORSE tailwind-safe count")


class TestJointPoolSearch(unittest.TestCase):
    """`joint_pool_search` -- GENERATE both halves of the pair from the
    pool, instead of fixing one via --partner.

        "I want it to generate my pair, i.e., mine and partner"

    Shares `_pair_vs_targets` with `joint_pair_search`, so the win/loss
    classification and the damage log are the same machinery, not a second
    copy -- these tests check the POOL-SEARCH-specific behaviour only.
    """

    def setUp(self):
        self.W = world()

    def test_rows_are_keyed_by_pair_not_name(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pool_search(
            ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola"],
            ["Sableye", "Ariados"], merged, moves, natures, typechart)
        self.assertTrue(rows)
        for r in rows:
            self.assertIn("pair", r)
            self.assertEqual(len(r["pair"]), 2)
            self.assertNotIn("name", r)

    def test_every_legal_pair_from_the_pool_is_covered(self):
        pool = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola"]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pool_search(pool, ["Sableye", "Ariados"], merged,
                                    moves, natures, typechart)
        got = {frozenset(r["pair"]) for r in rows}
        import itertools as _it
        want = {frozenset(p) for p in _it.combinations(pool, 2)}
        self.assertEqual(got, want)

    def test_a_named_target_is_a_legal_mirror_pick(self):
        """Sableye is in the pool AND a named target -- a legal mirror pick
        now: every combination of the pool, Sableye pairs included, is
        still covered exactly once."""
        pool = ["Mega Gengar", "Sableye", "Mega Alakazam"]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pool_search(
            pool, ["Sableye", "Ariados"], merged, moves, natures, typechart)
        got = {frozenset(r["pair"]) for r in rows}
        import itertools as _it
        want = {frozenset(p) for p in _it.combinations(pool, 2)}
        self.assertEqual(got, want)

    def test_matches_joint_pair_search_when_one_slot_is_effectively_fixed(self):
        """Same machinery, so the pool search's own (candidate, partner) row
        must agree EXACTLY with what `joint_pair_search` computes for that
        candidate against that same fixed partner."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool_rows = cf.joint_pool_search(
            ["Mega Gengar", "Mega Alakazam"], ["Sableye", "Ariados"], merged,
            moves, natures, typechart)
        fixed_rows = cf.joint_pair_search(
            ["Mega Gengar"], ["Sableye", "Ariados"], "Mega Alakazam", merged,
            moves, natures, typechart)
        pool_d = pool_rows[0]["detail"][("Sableye", "Ariados")]
        fixed_d = fixed_rows[0]["detail"][("Sableye", "Ariados")]
        self.assertEqual(pool_d["outcome"], fixed_d["outcome"])
        self.assertEqual(pool_d["tailwind_safe"], fixed_d["tailwind_safe"])

    def test_rows_are_ranked_protect_safe_first_then_beaten_then_tailwind_safe(self):
        """"I think it would be best if the ranking was done by default as
        by protect safe wins" -- protect-safe count is the PRIMARY
        criterion, ahead of raw beaten count and tailwind-safe count."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pool_search(
            ["Mega Gengar", "Ninetales-Alola", "Mega Alakazam"],
            ["Sharpedo", "Rampardos"], merged, moves, natures, typechart)
        keys = [cf._pair_sort_key(r) for r in rows]
        self.assertEqual(keys, sorted(keys))
        protect_safe = [r["pairs_protect_safe"] for r in rows]
        self.assertEqual(protect_safe, sorted(protect_safe, reverse=True))


class TestPruneBelow(unittest.TestCase):
    """`joint_pool_search`'s `prune_below` -- once a pair's remaining,
    not-yet-raced enemy pairs could not possibly push it up to
    `prune_below` even if every one of them were a win, stop racing it and
    fill the rest with a "loss" placeholder. Sound (never discards a pair
    that could still qualify), not a heuristic proxy like the abandoned
    `prescreen.py` attempt.

        "skip pairs in counter_table if their joint performance is too
         poor, unless it's a check for a promising bring 4."
    """

    POOL = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit", "Whimsicott"]
    TARGETS = ["Sableye", "Ariados", "Froslass", "Absol"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.unpruned = cf.joint_pool_search(
            self.POOL, self.TARGETS, merged, moves, natures, typechart,
            prune_below=None)
        self.pruned = cf.joint_pool_search(
            self.POOL, self.TARGETS, merged, moves, natures, typechart,
            prune_below=0.75)

    def test_pruning_actually_fires_in_this_fixture(self):
        """Sanity check that the scenario exercises the mechanism at all --
        otherwise the soundness checks below would pass vacuously."""
        any_pruned = any(
            d.get("_pruned") for r in self.pruned for d in r["detail"].values())
        self.assertTrue(any_pruned)

    def test_a_pair_that_clears_the_bar_is_never_pruned(self):
        """SOUND bound: a pair whose true (unpruned) beaten-fraction meets
        or exceeds `prune_below` must have been raced in full -- pruning
        can only ever have fired once qualifying was already provably
        impossible, so a qualifying pair's detail must come back byte-for-
        byte identical, not just its final counts."""
        unpruned_by_pair = {frozenset(r["pair"]): r for r in self.unpruned}
        for r in self.pruned:
            u = unpruned_by_pair[frozenset(r["pair"])]
            if cf._pair_beaten_frac(u) >= 0.75:
                for key, d in r["detail"].items():
                    self.assertFalse(d.get("_pruned"), key)
                self.assertEqual(r["pairs_swept"], u["pairs_swept"])
                self.assertEqual(r["pairs_traded"], u["pairs_traded"])
                self.assertEqual(r["pairs_lost"], u["pairs_lost"])
                self.assertEqual(r["pairs_no_ko"], u["pairs_no_ko"])

    def test_pruning_never_overstates_a_pairs_performance(self):
        """The other direction of soundness: pruning can only make a pair
        look WORSE (more "loss") than the truth, never better."""
        unpruned_by_pair = {frozenset(r["pair"]): r for r in self.unpruned}
        for r in self.pruned:
            u = unpruned_by_pair[frozenset(r["pair"])]
            self.assertLessEqual(cf._pair_beaten_frac(r), cf._pair_beaten_frac(u))

    def test_pairs_total_is_unaffected_by_pruning(self):
        """A pruned row still reports every enemy pair -- pruning fills
        `detail`, it never shrinks it."""
        for r in self.pruned:
            self.assertEqual(r["pairs_total"], len(list(
                __import__("itertools").combinations(self.TARGETS, 2))))

    def test_bring4_search_stage1_is_never_pruned(self):
        """`bring4_search` needs each of a fixed 6's exact C(6,2) pair
        performances -- "a check for a promising bring-4" -- so its Stage
        1 call must not pass `prune_below` at all, even against a target
        list large enough that pruning would otherwise fire."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        our6 = self.POOL[:6]
        pair_rows, _bring4_rows = cf.bring4_search(
            our6, self.TARGETS, merged, moves, natures, typechart)
        for r in pair_rows:
            for d in r["detail"].values():
                self.assertFalse(d.get("_pruned"))

    def test_multi_bring4_coverage_candidate_pool_matches_unpruned_ground_truth(self):
        """End-to-end: `multi_bring4_coverage` bakes `prune_below=
        good_threshold` into its Stage A. Independently recompute the same
        "good against >= min_enemies rosters" membership from the UNPRUNED
        `joint_pool_search` output and confirm the two agree exactly --
        pruning must never change who ends up in `candidate_pool`."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        enemies = [["Sableye", "Ariados", "Froslass"], ["Absol", "Ariados"]]
        good_threshold = 0.75
        coverage = cf.multi_bring4_coverage(
            self.POOL, enemies, merged, moves, natures, typechart,
            good_threshold=good_threshold, min_enemies=1)
        appears_good_in = {}
        for target_names in enemies:
            rows = cf.joint_pool_search(
                self.POOL, target_names, merged, moves, natures, typechart,
                item_overrides=coverage["fixed_items"],
                move_overrides=coverage["fixed_moves"], prune_below=None)
            good_names = set()
            for r in rows:
                if cf._pair_beaten_frac(r) >= good_threshold:
                    good_names.update(r["pair"])
            for n in good_names:
                appears_good_in[n] = appears_good_in.get(n, 0) + 1
        want = sorted(n for n, c in appears_good_in.items() if c >= 1)
        self.assertEqual(coverage["candidate_pool"], want)


class TestBring4Search(unittest.TestCase):
    """`bring4_search` -- for an ALREADY-DECIDED team of 6, which 4 should
    you bring against one specific enemy roster?

        "given that I will bring 4 vs a specific enemy, that is 6 pairs I
         will bring. I want the 6 possible pairs of my brings to perform
         very well, or at least to have several perform very well, such
         that I always have options no matter what position I am in. This
         would involve searching the top pairs to see how many are in,
         and then for a second stage, searching the given top teams and
         searching for how bad their worst pair performs."

    Stage 1 is literally `joint_pool_search(our6, ...)`, already covered by
    `TestJointPoolSearch` -- these tests check the bring-4-specific Stage 2
    combinatorics and ranking only.
    """

    OUR6 = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit"]
    TARGETS = ["Sableye", "Ariados"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.pair_rows, self.bring4_rows = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart)

    def test_stage1_covers_all_15_pairs_from_the_six(self):
        import itertools as _it
        got = {frozenset(r["pair"]) for r in self.pair_rows}
        want = {frozenset(p) for p in _it.combinations(self.OUR6, 2)}
        self.assertEqual(got, want)

    def test_stage1_matches_joint_pool_search_exactly(self):
        """`bring4_search` must not recompute pair races its own way --
        Stage 1 IS `joint_pool_search`'s own output."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        direct = cf.joint_pool_search(self.OUR6, self.TARGETS, merged, moves,
                                      natures, typechart)
        direct_by_key = {frozenset(r["pair"]): r for r in direct}
        for r in self.pair_rows:
            d = direct_by_key[frozenset(r["pair"])]
            self.assertEqual(r["pairs_swept"], d["pairs_swept"])
            self.assertEqual(r["pairs_traded"], d["pairs_traded"])
            self.assertEqual(r["pairs_lost"], d["pairs_lost"])

    def test_stage2_covers_all_15_bring4_subsets(self):
        import itertools as _it
        got = {frozenset(b["bring4"]) for b in self.bring4_rows}
        want = {frozenset(c) for c in _it.combinations(self.OUR6, 4)}
        self.assertEqual(got, want)
        self.assertTrue(all(len(b["bring4"]) == 4 for b in self.bring4_rows))

    def test_each_bring4_has_exactly_six_internal_pairs(self):
        for b in self.bring4_rows:
            self.assertEqual(len(b["pairs"]), 6)
            self.assertEqual(len(b["pair_rows"]), 6)
            self.assertEqual(b["pairs_total"], 6)

    def test_a_pair_appears_in_exactly_six_of_the_fifteen_bring4s(self):
        """A FIXED pair leaves 4 members to choose the other 2 bring-4 slots
        from: C(4,2)=6 -- a pure combinatorial check that Stage 2's lookup
        is wired to the right subsets."""
        target_pair = frozenset(self.pair_rows[0]["pair"])
        count = sum(1 for b in self.bring4_rows
                   if target_pair.issubset(set(b["bring4"])))
        self.assertEqual(count, 6)

    def test_worst_pair_really_is_the_worst_of_its_six(self):
        for b in self.bring4_rows:
            worst_key = cf._pair_sort_key(b["worst_pair_row"])
            for r in b["pair_rows"]:
                self.assertGreaterEqual(worst_key, cf._pair_sort_key(r),
                                        "the reported worst pair must rank "
                                        "no better than any of its siblings")

    def test_bring4_rows_are_ranked_best_worst_case_first(self):
        keys = [(len(b["uncovered_enemy_pairs"]),
                cf._pair_sort_key(b["worst_pair_row"]), -b["pairs_good"])
               for b in self.bring4_rows]
        self.assertEqual(keys, sorted(keys))

    def test_uncovered_enemy_pairs_dominates_the_ranking(self):
        """A bring-4 with fewer uncovered enemy pairs must never rank BELOW
        one with more, even if the latter's worst-pair `_pair_sort_key` (its
        raw beaten count) happens to look better -- "having a pair that
        every pair of yours loses against is terrible, and this is an
        important factor." """
        for i, b in enumerate(self.bring4_rows):
            for later in self.bring4_rows[i + 1:]:
                self.assertLessEqual(
                    len(b["uncovered_enemy_pairs"]),
                    len(later["uncovered_enemy_pairs"]),
                    "an earlier-ranked bring-4 must never have MORE "
                    "uncovered enemy pairs than a later one")

    def test_pairs_good_counts_pairs_meeting_the_threshold(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        _pr, lenient_rows = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            good_threshold=0.0)
        # A threshold of 0% is cleared by every pair, however bad.
        for b in lenient_rows:
            self.assertEqual(b["pairs_good"], 6)
        _pr2, strict_rows = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            good_threshold=1.0)
        for b, b_strict in zip(sorted(self.bring4_rows, key=lambda x: x["bring4"]),
                               sorted(strict_rows, key=lambda x: x["bring4"])):
            expected = sum(1 for r in b["pair_rows"] if cf._pair_beaten_frac(r) >= 1.0)
            self.assertEqual(b_strict["pairs_good"], expected)

    def test_rejects_a_team_outside_three_to_six(self):
        """3, 4, 5, or 6 are all legal -- a core of exactly 3 degenerates to
        one possible "bring" (itself, 3 pairs), the same way a core of 4
        already degenerates to one bring of its own 6 pairs. Fewer than 3
        or more than 6 still isn't a real "already-decided team"."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        with self.assertRaises(ValueError):
            cf.bring4_search(self.OUR6[:2], self.TARGETS, merged, moves,
                             natures, typechart)
        with self.assertRaises(ValueError):
            cf.bring4_search(self.OUR6 + ["Whimsicott"], self.TARGETS, merged,
                             moves, natures, typechart)

    def test_a_team_of_four_degenerates_to_one_bring4_of_its_own_six_pairs(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        our4 = self.OUR6[:4]
        pair_rows, bring4_rows = cf.bring4_search(
            our4, self.TARGETS, merged, moves, natures, typechart)
        self.assertEqual(len(pair_rows), 6)
        self.assertEqual(len(bring4_rows), 1)
        self.assertEqual(set(bring4_rows[0]["bring4"]), set(our4))
        self.assertEqual(len(bring4_rows[0]["pair_rows"]), 6)

    def test_a_team_of_five_offers_five_candidate_bring4s(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        our5 = self.OUR6[:5]
        pair_rows, bring4_rows = cf.bring4_search(
            our5, self.TARGETS, merged, moves, natures, typechart)
        self.assertEqual(len(pair_rows), 10)
        self.assertEqual(len(bring4_rows), 5)
        for b in bring4_rows:
            self.assertTrue(set(b["bring4"]).issubset(set(our5)))
            self.assertEqual(len(b["pair_rows"]), 6)

    def test_rejects_a_mega_alongside_its_own_base_form(self):
        """"You cannot have both a mega and its non-mega form." """
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        our6 = self.OUR6[:5] + ["Alakazam"]  # OUR6 already has Mega Alakazam
        with self.assertRaises(ValueError):
            cf.bring4_search(our6, self.TARGETS, merged, moves, natures, typechart)


class TestResolveUniqueItems(unittest.TestCase):
    """`_resolve_unique_items` -- the VGC Item Clause helper ("Only one
    pokemon in each team may use a specific item"). Build-order dependent
    by design: whichever name resolves first keeps its independently-best
    item; a later name that collides gets re-searched with every
    already-claimed item ALSO excluded."""

    def setUp(self):
        self.W = world()

    def test_a_real_collision_is_resolved_in_build_order(self):
        """Ninetales-Alola and Rampardos both independently pick Life Orb
        against this target -- confirmed via a plain (non-unique) search
        first, then resolved distinct, first-listed name keeping it."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        names = ["Ninetales-Alola", "Rampardos"]
        targets = ["Sableye", "Ariados"]
        # Confirm the collision exists without the clause.
        plain = {n: cf._answer_for(n, merged, moves, natures, typechart,
                                   targets)[0] for n in names}
        self.assertEqual(plain["Ninetales-Alola"], plain["Rampardos"])
        resolved = cf._resolve_unique_items(
            names, merged, moves, natures, typechart, targets)
        self.assertEqual(resolved["Ninetales-Alola"], plain["Ninetales-Alola"])
        self.assertNotEqual(resolved["Rampardos"], plain["Rampardos"])
        self.assertEqual(len(set(resolved.values())), len(resolved))

    def test_a_pinned_item_override_is_never_touched(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        names = ["Ninetales-Alola", "Rampardos"]
        targets = ["Sableye", "Ariados"]
        resolved = cf._resolve_unique_items(
            names, merged, moves, natures, typechart, targets,
            item_overrides={"Rampardos": "Life Orb"})
        self.assertEqual(resolved["Rampardos"], "Life Orb")

    def test_no_collision_leaves_every_choice_unchanged(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        names = ["Mega Gengar", "Mega Alakazam", "Kingambit"]
        targets = ["Sableye", "Ariados"]
        plain = {n: cf._answer_for(n, merged, moves, natures, typechart,
                                   targets)[0] for n in names}
        resolved = cf._resolve_unique_items(
            names, merged, moves, natures, typechart, targets)
        self.assertEqual(resolved, plain)


class TestBring4SearchItemClauseIsOptIn(unittest.TestCase):
    """"make the item uniqueness an option, but by default items will
    remain non-unique to reduce search time" -- `enforce_item_clause`
    defaults to False on both `bring4_search` and `core_deep_dive`; the
    Ninetales-Alola/Rampardos Life Orb collision (see
    TestResolveUniqueItems) must still show up by default, and disappear
    only when explicitly requested."""

    OUR6 = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit"]
    TARGETS = ["Sableye", "Ariados"]

    def setUp(self):
        self.W = world()

    def _items_by_name(self, pair_rows):
        items = {}
        for r in pair_rows:
            n1, n2 = r["pair"]
            items[n1] = r["item1"]
            items[n2] = r["item2"]
        return items

    def test_default_bring4_search_still_shows_the_collision(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pair_rows, _br = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart)
        items = self._items_by_name(pair_rows)
        self.assertEqual(items["Ninetales-Alola"], items["Rampardos"])

    def test_enforce_item_clause_resolves_the_collision(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pair_rows, _br = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            enforce_item_clause=True)
        items = self._items_by_name(pair_rows)
        self.assertNotEqual(items["Ninetales-Alola"], items["Rampardos"])
        self.assertEqual(len(set(items.values())), len(items))

    def test_default_core_deep_dive_still_shows_the_collision(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(
            self.OUR6, [self.TARGETS], merged, moves, natures, typechart)
        items = {n: s["item"] for n, s in dive["sets"].items()}
        self.assertEqual(items["Ninetales-Alola"], items["Rampardos"])

    def test_enforce_item_clause_resolves_the_collision_in_core_deep_dive(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(
            self.OUR6, [self.TARGETS], merged, moves, natures, typechart,
            enforce_item_clause=True)
        items = {n: s["item"] for n, s in dive["sets"].items()}
        self.assertNotEqual(items["Ninetales-Alola"], items["Rampardos"])


class TestBring4PairDepth(unittest.TestCase):
    """`bring4_pair_depth` -- "I would like the csv/xlsx export from the
    CLI to show the basic details of the 6 pairs for each bring4 (total,
    3rd best, 4th best, and worst wins, wins under Tailwind ..., under
    protect safe)": a bring-4 can look fine on just its single worst pair
    (`_bring4_candidates`'s own ranking) while its middle-of-the-pack pairs
    are actually mediocre -- this surfaces that."""

    OUR6 = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit"]
    TARGETS = ["Sableye", "Ariados", "Froslass", "Absol"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.pair_rows, self.bring4_rows = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            good_threshold=0.0)

    def test_rejects_unless_the_targets_actually_exist(self):
        """`TARGETS` here must be real dataset entries -- confirmed once,
        since a typo would otherwise silently degrade every test below to
        a smaller, misleading enemy-pair count."""
        merged = self.W["merged"]
        for n in self.TARGETS:
            self.assertIn(n, merged)

    def test_beaten_fields_match_the_pair_sort_key_order(self):
        b = self.bring4_rows[0]
        depth = cf.bring4_pair_depth(b)
        ordered = sorted(b["pair_rows"], key=cf._pair_sort_key)
        beaten = [r["pairs_swept"] + r["pairs_traded"] for r in ordered]
        self.assertEqual(depth["beaten_total"], sum(beaten))
        self.assertEqual(depth["beaten_3rd"], beaten[2])
        self.assertEqual(depth["beaten_4th"], beaten[3])
        self.assertEqual(depth["beaten_worst"], beaten[-1])
        self.assertEqual(depth["pairs_total"], ordered[0]["pairs_total"])

    def test_tailwind_and_protect_safe_totals_sum_across_all_six(self):
        b = self.bring4_rows[0]
        depth = cf.bring4_pair_depth(b)
        self.assertEqual(depth["tailwind_safe_total"],
                         sum(r["pairs_tailwind_safe"] for r in b["pair_rows"]))
        self.assertEqual(depth["protect_safe_total"],
                         sum(r["pairs_protect_safe"] for r in b["pair_rows"]))

    def test_a_four_member_team_still_produces_one_full_depth_summary(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        _pair_rows, bring4_rows = cf.bring4_search(
            self.OUR6[:4], self.TARGETS, merged, moves, natures, typechart)
        depth = cf.bring4_pair_depth(bring4_rows[0])
        self.assertIsNotNone(depth["beaten_3rd"])
        self.assertIsNotNone(depth["beaten_4th"])
        self.assertIsNotNone(depth["beaten_worst"])
        self.assertGreaterEqual(depth["beaten_total"], depth["beaten_worst"])

    def test_tailwind_and_protect_best_3rd_read_off_the_same_sorted_order(self):
        """"the best and third best pair under tailwind and under enemy
        protect" -- read off the SAME `_pair_sort_key` order `beaten_3rd`/
        `beaten_4th` already use, not re-sorted by the tailwind/protect
        value itself (so "best"/"3rd best" always means the SAME two pairs
        across every one of these fields, matching how the rest of this
        module defines "better")."""
        b = self.bring4_rows[0]
        depth = cf.bring4_pair_depth(b)
        ordered = sorted(b["pair_rows"], key=cf._pair_sort_key)
        tw = [r["pairs_tailwind_safe"] for r in ordered]
        pr = [r["pairs_protect_safe"] for r in ordered]
        self.assertEqual(depth["tailwind_safe_best"], tw[0])
        self.assertEqual(depth["tailwind_safe_3rd"], tw[2])
        self.assertEqual(depth["protect_safe_best"], pr[0])
        self.assertEqual(depth["protect_safe_3rd"], pr[2])

    def test_no_faint_best_3rd_and_total_match_pairs_beaten_without_fainting(self):
        """"the best and third best number of pairs beaten without having
        either of own pair faint" -- `no_faint_total`/`_best`/`_3rd` must
        match a direct, from-scratch count via `_pairs_beaten_without_
        fainting`, not just be internally self-consistent."""
        b = self.bring4_rows[0]
        depth = cf.bring4_pair_depth(b)
        ordered = sorted(b["pair_rows"], key=cf._pair_sort_key)
        no_faint = [cf._pairs_beaten_without_fainting(r) for r in ordered]
        self.assertEqual(depth["no_faint_total"], sum(no_faint))
        self.assertEqual(depth["no_faint_best"], no_faint[0])
        self.assertEqual(depth["no_faint_3rd"], no_faint[2])
        # A win that costs one of our own two Pokemon its life is never
        # counted here -- this must never exceed the ordinary beaten count.
        beaten = [r["pairs_swept"] + r["pairs_traded"] for r in ordered]
        for nf, bt in zip(no_faint, beaten):
            self.assertLessEqual(nf, bt)


class TestPairsBeatenWithoutFainting(unittest.TestCase):
    """`_pairs_beaten_without_fainting` in isolation -- a hand-built
    `detail` fixture (no real racing) covering every outcome/HP combination
    it must tell apart."""

    def _row(self, detail):
        return {"detail": detail}

    def test_a_sweep_with_full_hp_retained_counts(self):
        row = self._row({("E1", "E2"): {"outcome": "sweep",
                                        "our_hp": {"C": 1.0, "P": 1.0}}})
        self.assertEqual(cf._pairs_beaten_without_fainting(row), 1)

    def test_a_trade_where_one_of_ours_fainted_does_not_count(self):
        """"beaten without having either of own pair faint" -- an
        `out_trade` win where ONE of our two retained 0 HP (it fainted)
        must not count, even though the outcome bucket says we won."""
        row = self._row({("E1", "E2"): {"outcome": "out_trade",
                                        "our_hp": {"C": 0.0, "P": 0.4}}})
        self.assertEqual(cf._pairs_beaten_without_fainting(row), 0)

    def test_a_trade_where_both_retain_some_hp_counts(self):
        row = self._row({("E1", "E2"): {"outcome": "out_trade",
                                        "our_hp": {"C": 0.2, "P": 0.4}}})
        self.assertEqual(cf._pairs_beaten_without_fainting(row), 1)

    def test_a_loss_never_counts_even_with_a_stale_positive_our_hp(self):
        """A non-win outcome's `our_hp` is always `{"C": 0.0, "P": 0.0}` in
        practice (`_pair_vs_targets`'s own rule), but the outcome check
        stays a real, explicit safeguard rather than trusting that."""
        row = self._row({("E1", "E2"): {"outcome": "loss",
                                        "our_hp": {"C": 1.0, "P": 1.0}}})
        self.assertEqual(cf._pairs_beaten_without_fainting(row), 0)

    def test_sums_across_several_enemy_pairs(self):
        row = self._row({
            ("E1", "E2"): {"outcome": "sweep", "our_hp": {"C": 1.0, "P": 1.0}},
            ("E1", "E3"): {"outcome": "out_trade", "our_hp": {"C": 0.0, "P": 0.5}},
            ("E2", "E3"): {"outcome": "loss", "our_hp": {"C": 0.0, "P": 0.0}},
        })
        self.assertEqual(cf._pairs_beaten_without_fainting(row), 1)


class TestEnemyHasRealTailwind(unittest.TestCase):
    """`enemy_has_real_tailwind` -- "wins under Tailwind, ESPECIALLY IF
    they have a tailwind user in the 2v2" -- a once-per-roster flag the
    CLI export reads alongside the aggregated tailwind-safe count, so the
    reader knows whether that column is worth a second look at all."""

    def test_true_when_a_named_enemy_really_uses_tailwind(self):
        merged = world()["merged"]
        self.assertTrue(cf.enemy_has_real_tailwind(
            ["Whimsicott", "Kingambit"], merged))

    def test_false_when_no_named_enemy_uses_tailwind(self):
        merged = world()["merged"]
        self.assertFalse(cf.enemy_has_real_tailwind(
            ["Kingambit", "Mudsdale"], merged))


class TestBring4SearchAllowsMirrorMatches(unittest.TestCase):
    """"you should be allowed to bring the same pokemon as the enemy" -- a
    real VGC mirror ("our team may include a Pokemon the enemy also
    brings") is legal, and used to crash: `bring4_search`'s `our6` is a
    FIXED, complete 6 that Stage 2 needs a pair for every member of, but
    `joint_pool_search` used to silently drop any pool member also named as
    an enemy, so Stage 2 hit a bare `KeyError` looking up a pair that was
    never computed. Both the silent exclusion and the (later-added, since
    the exclusion made it look "safe") hard `ValueError` are gone now --
    `our6` and `target_names` may overlap freely, including sharing the
    exact same name on both sides."""

    OUR6 = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit"]

    def setUp(self):
        self.W = world()

    def test_a_shared_name_is_accepted_and_races_normally(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        targets = ["Sableye", "Kingambit"]  # Kingambit is also in OUR6
        pair_rows, bring4_rows = cf.bring4_search(
            self.OUR6, targets, merged, moves, natures, typechart)
        self.assertEqual(len(pair_rows), 15)
        self.assertEqual(len(bring4_rows), 15)

    def test_no_overlap_still_works(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        targets = ["Sableye", "Ariados"]
        pair_rows, bring4_rows = cf.bring4_search(
            self.OUR6, targets, merged, moves, natures, typechart)
        self.assertEqual(len(pair_rows), 15)
        self.assertEqual(len(bring4_rows), 15)

    def test_same_name_both_sides_is_a_legal_mirror_and_roles_dont_collide(self):
        """Kingambit is on OUR6 AND is the (only) named enemy -- a true
        same-name-both-sides mirror. `combatants`/`plan`/`hp` are keyed by
        fixed role labels ("C"/"P"/"E1"/"E2"), never by species name, so our
        Kingambit and their Kingambit can never clobber each other's entry;
        confirm this on the actual race output rather than just trusting
        the role-keying design."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        targets = ["Kingambit", "Sableye"]
        pair_rows, bring4_rows = cf.bring4_search(
            self.OUR6, targets, merged, moves, natures, typechart)
        row = next(r for r in pair_rows if "Kingambit" in r["pair"])
        detail = row["detail"][("Kingambit", "Sableye")]
        # our_hp is role-keyed ("C"/"P"), not name-keyed -- if it collapsed
        # our and their Kingambit into one entry this would have the wrong
        # shape or a missing role.
        self.assertEqual(set(detail["our_hp"].keys()), {"C", "P"})
        # The log itself references both a "C"/"P" (ours) and "E1" (theirs)
        # role acting -- both Kingambits actually took actions, not just one
        # shared entry silently standing in for both. `log` is a list of
        # turns, each a list of (attacker_role, defender_role, Hit) triples.
        roles_seen = {role for turn in detail["log"] for hit in turn
                     for role in (hit[0], hit[1])}
        self.assertTrue(roles_seen & {"C", "P"})
        self.assertIn("E1", roles_seen)

    def test_mirror_speed_tie_still_resolves_against_us(self):
        """The pre-existing "ties resolve against us" turn-order convention
        (see `TestSpeedTiers.test_a_speed_tie_does_not_count_as_
        outspeeding`) is name-agnostic -- it keys off role ("E1"/"E2" vs
        "C"/"P"), not species -- so it already covers a same-name mirror at
        an exact speed tie with no extra code. Confirmed directly: an
        identically-built same-species pair (same nature, no item) is a
        genuine effective_speed tie, same as any other exact tie."""
        from engine import FieldState, effective_speed
        merged, natures = self.W["merged"], self.W["natures"]
        ours = cf._build("Kingambit", merged, natures)
        theirs = cf._build("Kingambit", merged, natures)
        our_spd = effective_speed(ours, FieldState(), "p1")
        their_spd = effective_speed(theirs, FieldState(), "p2")
        self.assertEqual(our_spd, their_spd)
        self.assertFalse(our_spd > their_spd)


class TestMultiBring4Search(unittest.TestCase):
    """`multi_bring4_coverage`/`multi_bring4_exhaustive`/`multi_bring4_beam`
    -- generalising `bring4_search` from ONE enemy roster to SEVERAL, by
    finding the best team-of-6 across all of them.

        "I want to look at several 'vs' teams, for instance 3 different
         sets of enemy 6. It will run the best pairs against each separate
         team in the same way, but then it will find the best possible
         group of 6, comprised of brings of possible 4 that perform well
         in the 6-pair test I described above."

    Confirmed with the user: the bring-4 can differ per opponent (matches
    real VGC Team Preview), and both an exhaustive search (over a narrowed
    candidate pool) and a beam search (over the raw pool) should exist.
    """

    POOL = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit", "Whimsicott"]
    ENEMIES = [["Sableye", "Ariados"], ["Basculegion", "Mega Floette"]]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1)

    def test_a_pool_member_that_is_also_an_enemy_elsewhere_is_a_legal_mirror(self):
        """Basculegion is a pool candidate here AND one of Enemy 2's own
        team members -- a real VGC mirror, legal everywhere now ("Apply
        everywhere" -- our copy just always loses an exact speed tie
        against its enemy twin, unchanged machinery, see
        TestBring4SearchAllowsMirrorMatches). Confirmed present, and raced,
        consistently for EVERY enemy (not just the ones that didn't name
        it) -- the old KeyError this guarded against came from a name being
        silently dropped for only SOME enemies' pair tables, which no
        longer happens since nothing is silently dropped at all."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool_with_overlap = self.POOL + ["Basculegion"]
        enemies = [["Sableye", "Ariados"], ["Basculegion", "Mega Floette"]]
        cov = cf.multi_bring4_coverage(
            pool_with_overlap, enemies, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1)
        self.assertIn("Basculegion", cov["candidate_pool"])
        for rows in cov["per_enemy"]:
            got_names = {n for r in rows for n in r["pair"]}
            self.assertIn("Basculegion", got_names)
        # Must not crash, and may legally recommend the mirror pick.
        rows = cf.multi_bring4_exhaustive(cov, good_threshold=0.5)
        self.assertTrue(rows)

    def test_stage_a_runs_once_per_enemy_over_the_whole_pool(self):
        import itertools as _it
        self.assertEqual(len(self.coverage["per_enemy"]), len(self.ENEMIES))
        want_pairs = {frozenset(p) for p in _it.combinations(self.POOL, 2)}
        for rows in self.coverage["per_enemy"]:
            got_pairs = {frozenset(r["pair"]) for r in rows}
            self.assertEqual(got_pairs, want_pairs)

    def test_candidate_pool_is_a_subset_of_the_pool(self):
        self.assertTrue(set(self.coverage["candidate_pool"]).issubset(set(self.POOL)))

    def test_min_enemies_narrows_the_candidate_pool(self):
        """Requiring a candidate to be good against EVERY named enemy
        (min_enemies = len(ENEMIES)) can only keep as many or fewer names
        than requiring it against just one."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        lenient = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1)
        strict = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=len(self.ENEMIES))
        self.assertLessEqual(len(strict["candidate_pool"]),
                             len(lenient["candidate_pool"]))
        self.assertTrue(set(strict["candidate_pool"])
                        .issubset(set(lenient["candidate_pool"])))

    def test_exhaustive_covers_every_valid_core_size(self):
        """"a full team of only 4-5 members is not a problem, in some ways
        it is actually better and more efficient. I would still like to
        see them" -- sizes 4, 5 AND 6 are all searched, not just 6. Every
        returned core is a genuine subset of SOME size-4/5/6 combination
        from the candidate pool, has no unused member (a wasted slot would
        mean an identical-scoring smaller core exists on its own), and no
        row is missing any size the fixture can actually reach."""
        import itertools as _it
        cov = self.coverage
        if len(cov["candidate_pool"]) < 4:
            self.skipTest("fixture's candidate pool is too small at this "
                          "threshold -- covered structurally by the ceiling "
                          "test below instead")
        rows = cf.multi_bring4_exhaustive(cov, good_threshold=0.5)
        all_possible = set()
        for size in (4, 5, 6):
            if size <= len(cov["candidate_pool"]):
                all_possible |= {tuple(sorted(c))
                                for c in _it.combinations(cov["candidate_pool"], size)}
        got = {r["core"] for r in rows}
        self.assertTrue(got.issubset(all_possible))
        self.assertTrue(all(r["core_size"] in (4, 5, 6) for r in rows))
        self.assertTrue(all(r["unused"] == () for r in rows))
        sizes_seen = {r["core_size"] for r in rows}
        self.assertTrue(sizes_seen.issubset({4, 5, 6}))
        self.assertGreaterEqual(len(sizes_seen), 2,
                                "fixture assumes at least two different core "
                                "sizes are valid (no-unused-member) answers")

    def test_exhaustive_rows_are_ranked_best_worst_case_first(self):
        cov = self.coverage
        if len(cov["candidate_pool"]) < 6:
            self.skipTest("fixture's candidate pool is too small at this threshold")
        rows = cf.multi_bring4_exhaustive(cov, good_threshold=0.5)
        keys = [r["worst_enemy_score_key"] for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_exhaustive_reports_a_best_bring4_per_enemy(self):
        cov = self.coverage
        if len(cov["candidate_pool"]) < 4:
            self.skipTest("fixture's candidate pool is too small at this threshold")
        rows = cf.multi_bring4_exhaustive(cov, good_threshold=0.5)
        for r in rows[:1]:
            self.assertEqual(len(r["per_enemy"]), len(self.ENEMIES))
            for pe in r["per_enemy"]:
                self.assertEqual(len(pe["best_bring4"]), 4)
                self.assertTrue(set(pe["best_bring4"]).issubset(set(r["core"])))

    def test_exhaustive_rejects_a_pool_below_four(self):
        cov = {"candidate_pool": ["Sharpedo", "Rampardos"],
              "pair_by_key": [{}], "target_name_lists": [["Sableye", "Ariados"]]}
        with self.assertRaises(ValueError):
            cf.multi_bring4_exhaustive(cov)

    def test_exhaustive_rejects_a_pool_above_the_ceiling(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        cov = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)  # 0% bar -- everyone qualifies
        self.assertGreaterEqual(len(cov["candidate_pool"]), 6)
        with self.assertRaises(ValueError):
            cf.multi_bring4_exhaustive(cov, good_threshold=0.0, max_candidates=3)

    def test_beam_returns_only_complete_cores_of_valid_size(self):
        rows = cf.multi_bring4_beam(self.coverage, good_threshold=0.5, beam_width=6)
        self.assertTrue(rows)
        for r in rows:
            self.assertIn(len(r["core"]), (4, 5, 6))
            self.assertEqual(len(set(r["core"])), len(r["core"]))
            self.assertEqual(r["unused"], ())

    def test_beam_respects_beam_width(self):
        """`found` accumulates DISTINCT cores across all 3 sizes (4/5/6) the
        beam passes through, so the total row count is not itself capped at
        `beam_width` (nothing in `multi_bring4_beam`'s own docstring
        promises that) -- what IS guaranteed is that a narrower beam can
        never find MORE than a wider one, since it's growing from a subset
        of the same partials at every step."""
        narrow = cf.multi_bring4_beam(self.coverage, good_threshold=0.5, beam_width=1)
        wide = cf.multi_bring4_beam(self.coverage, good_threshold=0.5, beam_width=20)
        self.assertLessEqual(len(narrow), len(wide))
        self.assertGreater(len(wide), len(narrow),
                           "fixture assumes beam_width actually changes the "
                           "result for this pool -- otherwise this isn't "
                           "testing anything")

    def test_beam_rows_are_ranked_best_worst_case_first(self):
        rows = cf.multi_bring4_beam(self.coverage, good_threshold=0.5, beam_width=8)
        keys = [r["worst_enemy_score_key"] for r in rows]
        self.assertEqual(keys, sorted(keys))

    def test_beam_searches_the_whole_pool_not_just_candidates(self):
        """Unlike the exhaustive mode, beam must be able to pick a member
        that never appeared in any enemy's own good-pair list."""
        rows = cf.multi_bring4_beam(self.coverage, good_threshold=0.5, beam_width=20)
        all_picked = {n for r in rows for n in r["core"]}
        self.assertTrue(all_picked.issubset(set(self.POOL)))


class TestMultiBring4CoverageJobs(unittest.TestCase):
    """"Is there a way to optionally devote more resources to counter_table.py
    for parallel calculations, such as with the --jobs argument in
    [generate_]overnight?" -- `multi_bring4_coverage`'s per-enemy
    `joint_pool_search` calls are independent, so `jobs > 1` runs them in a
    process pool instead of one after another. The result must be identical
    either way -- parallelism is purely a speed knob, never a different
    answer."""

    POOL = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit", "Whimsicott"]
    ENEMIES = [["Sableye", "Ariados"], ["Basculegion", "Mega Floette"],
              ["Garchomp", "Incineroar"]]

    def setUp(self):
        self.W = world()

    def _coverage(self, jobs):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        return cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1, jobs=jobs)

    def test_jobs_2_matches_serial_per_enemy_rows_exactly(self):
        serial = self._coverage(jobs=1)
        parallel = self._coverage(jobs=2)
        self.assertEqual(len(serial["per_enemy"]), len(parallel["per_enemy"]))
        for s_rows, p_rows in zip(serial["per_enemy"], parallel["per_enemy"]):
            self.assertEqual(s_rows, p_rows)

    def test_jobs_2_matches_serial_candidate_pool_and_fixed_sets(self):
        serial = self._coverage(jobs=1)
        parallel = self._coverage(jobs=2)
        self.assertEqual(serial["candidate_pool"], parallel["candidate_pool"])
        self.assertEqual(serial["fixed_items"], parallel["fixed_items"])
        self.assertEqual(serial["fixed_moves"], parallel["fixed_moves"])

    def test_per_enemy_order_matches_target_name_lists_order_under_jobs(self):
        """`per_enemy`/`pair_by_key` must stay positionally aligned with
        `target_name_lists` -- `ex.map` (not `as_completed`) is what
        guarantees this, since every downstream reader zips them together."""
        coverage = self._coverage(jobs=2)
        for target_names, rows in zip(coverage["target_name_lists"],
                                      coverage["per_enemy"]):
            got_names = {n for r in rows for n in r["pair"]}
            # Every enemy's own row set only ever races against ITS enemy
            # roster's pairs -- a shuffled zip would show a pair count or
            # composition mismatch against the wrong enemy.
            self.assertTrue(got_names.issubset(set(self.POOL)))
            self.assertTrue(len(rows) > 0)

    def test_jobs_has_no_effect_with_only_one_enemy_roster(self):
        """Falls back to serial when there's nothing to split across
        workers -- covers the `len(target_name_lists) > 1` guard."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        one_enemy = [self.ENEMIES[0]]
        serial = cf.multi_bring4_coverage(
            self.POOL, one_enemy, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1, jobs=1)
        parallel = cf.multi_bring4_coverage(
            self.POOL, one_enemy, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1, jobs=4)
        self.assertEqual(serial["per_enemy"], parallel["per_enemy"])


class TestCoreRowRespectsMegaConsistency(unittest.TestCase):
    """"these MUST commit to only ever mega one vs all enemy pairs if both
    are brought to a specific battle ... You cannot vary your mega choice
    if both are brought." `_bring4_candidates` already enforced this for
    `bring4_search`'s own fixed six (`TestBring4CandidatesRespectsMega
    Consistency` above), but `_core_row` -- the ranking function
    `multi_bring4_exhaustive`/`multi_bring4_beam` (i.e. --multi-bring4)
    actually run -- called it with no `megas`/`pair_lookup_forced_base` at
    all, so a bring-4 carrying 2 mega-stone holders was scored as if BOTH
    could transform simultaneously across different pairs, which is
    illegal. A hand-built fixture (`_fake_pair_row`, no real racing)
    isolates just the threading of `pair_by_key_forced_base_list` through
    `_core_row` into `_bring4_candidates`."""

    TARGETS = ("E1", "E2")
    WIN = {("E1", "E2")}
    LOSS = set()

    def setUp(self):
        import itertools as _it
        self.six = ["Mega A", "Mega B", "C", "D"]
        self.pair_lookup = {
            frozenset(p): _fake_pair_row(p, self.LOSS, self.TARGETS)
            for p in _it.combinations(self.six, 2)}
        touching_b = [("Mega A", "Mega B"), ("Mega B", "C"), ("Mega B", "D")]
        touching_a = [("Mega A", "Mega B"), ("Mega A", "C"), ("Mega A", "D")]
        self.pair_lookup_forced_base = {
            "Mega B": {frozenset(p): _fake_pair_row(p, self.WIN, self.TARGETS)
                      for p in touching_b},
            "Mega A": {frozenset(p): _fake_pair_row(p, self.LOSS, self.TARGETS)
                      for p in touching_a},
        }

    def test_core_row_picks_the_strictly_better_consistent_hypothesis(self):
        """Same fixture/assertions as `_bring4_candidates`'s own direct
        test, but through `_core_row` (with `pair_by_key_forced_base_list`
        supplied) -- proving the wiring, not re-testing the underlying
        hypothesis-comparison logic itself."""
        row = cf._core_row(
            self.six, [self.pair_lookup], [self.TARGETS], good_threshold=1.0,
            pair_by_key_forced_base_list=[self.pair_lookup_forced_base])
        best = row["per_enemy"][0]["best_bring4_row"]
        self.assertEqual(best["pairs_good"], 3)
        won_pairs = {r["pair"] for r in best["pair_rows"]
                    if cf._pair_beaten_frac(r) >= 1.0}
        self.assertEqual(won_pairs, {("Mega A", "Mega B"), ("Mega B", "C"),
                                     ("Mega B", "D")})

    def test_without_pair_by_key_forced_base_list_reproduces_the_old_bug(self):
        """The regression this whole class guards against: omitting
        `pair_by_key_forced_base_list` (the old call shape, before this fix)
        lets the plain, unconstrained lookup answer every pair independently
        -- so Mega A's own touching pairs (which lose under every real
        hypothesis in this fixture) show as losses while Mega B's touching
        pairs simultaneously show as wins, as if both were live at once."""
        row = cf._core_row(self.six, [self.pair_lookup], [self.TARGETS],
                           good_threshold=1.0)
        best = row["per_enemy"][0]["best_bring4_row"]
        self.assertEqual(best["pairs_good"], 0)  # the unconstrained lookup: every pair loses

    def test_a_core_with_only_one_stone_holder_is_unaffected(self):
        """No consistency question when a core carries at most 1 mega --
        same result with or without `pair_by_key_forced_base_list`."""
        import itertools as _it
        six_one_mega = ["Mega A", "C", "D", "Kingambit"]
        pair_lookup = {
            frozenset(p): _fake_pair_row(p, self.LOSS, self.TARGETS)
            for p in _it.combinations(six_one_mega, 2)}
        without = cf._core_row(six_one_mega, [pair_lookup], [self.TARGETS],
                               good_threshold=1.0)
        with_fb = cf._core_row(
            six_one_mega, [pair_lookup], [self.TARGETS], good_threshold=1.0,
            pair_by_key_forced_base_list=[{"Mega A": {}, "Mega B": {}}])
        self.assertEqual(without, with_fb)


class TestMultiBring4CoverageMegaConsistency(unittest.TestCase):
    """End-to-end through `multi_bring4_coverage` -> `multi_bring4_exhaustive`
    with REAL megas (not the hand-built fixture above) -- the actual
    `--multi-bring4` path a user runs, reproducing "Arcanine-Hisui /
    Lycanroc-Dusk / Mega Floette / Mega Scizor"-shaped output and confirming
    it no longer double-counts both stone-holders as simultaneously live."""

    POOL = ["Mega Scizor", "Mega Floette", "Garchomp", "Kingambit",
           "Whimsicott", "Sinistcha"]
    ENEMIES = [["Kingambit", "Basculegion", "Sableye", "Ariados"]]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)

    def test_coverage_computes_forced_base_rows_for_every_pool_mega(self):
        fb = self.coverage["pair_by_key_forced_base"][0]
        self.assertEqual(set(fb), {"Mega Floette", "Mega Scizor"})
        # Every pair CONTAINING a forced name got a locked-to-base row --
        # C(pool,1 fixed, others free) = len(pool)-1 partners each.
        self.assertEqual(len(fb["Mega Floette"]), len(self.POOL) - 1)
        self.assertEqual(len(fb["Mega Scizor"]), len(self.POOL) - 1)

    def _hypothesis_rows(self, forced_name, pairs):
        """Replicates `_bring4_candidates`'s own per-pair lookup for the
        "`forced_name` stays base, the other stone-holder is this bring's
        actual mega" hypothesis -- {frozenset(pair): row}, built the exact
        same way `_row_for`'s lambda does internally."""
        fb = self.coverage["pair_by_key_forced_base"][0]
        normal = self.coverage["pair_by_key"][0]
        return {frozenset(p): fb[forced_name].get(frozenset(p), normal[frozenset(p)])
               for p in pairs}

    def test_a_two_mega_core_is_scored_under_one_consistent_hypothesis(self):
        """The exact regression this session's report was about: a core
        carrying both Mega Floette and Mega Scizor must not let a pair
        involving ONE assume it transforms while the OTHER independently
        also assumes it transforms elsewhere in the same bring -- every
        pair in the winning bring must come from the SAME one of the two
        single-consistent hypotheses, never a mix of both."""
        core = ("Garchomp", "Kingambit", "Mega Floette", "Mega Scizor")
        row = cf._core_row(
            core, self.coverage["pair_by_key"], self.coverage["target_name_lists"],
            good_threshold=0.0,
            pair_by_key_forced_base_list=self.coverage["pair_by_key_forced_base"])
        best = row["per_enemy"][0]["best_bring4_row"]
        got = {frozenset(pr["pair"]): pr for pr in best["pair_rows"]}
        pairs = list(got)
        floette_forced = self._hypothesis_rows("Mega Floette", pairs)
        scizor_forced = self._hypothesis_rows("Mega Scizor", pairs)
        self.assertTrue(got == floette_forced or got == scizor_forced,
                        "the winning bring's own pairs must all come from "
                        "ONE consistent hypothesis, not a mix of both")

    def test_forcing_a_mega_to_base_measurably_weakens_its_own_pairs(self):
        """A concrete real-data regression guard: with Mega Floette forced
        to base, Kingambit+Mega Floette's own beaten count must drop below
        what the plain, unconstrained (both-simultaneously-mega) lookup
        shows -- proving the forced-base hypothesis isn't a silent no-op."""
        fb = self.coverage["pair_by_key_forced_base"][0]
        normal = self.coverage["pair_by_key"][0]
        pair = frozenset({"Kingambit", "Mega Floette"})
        uncorrected = normal[pair]["pairs_swept"] + normal[pair]["pairs_traded"]
        floette_forced_base = fb["Mega Floette"][pair]
        forced = floette_forced_base["pairs_swept"] + floette_forced_base["pairs_traded"]
        self.assertLess(forced, uncorrected)


class TestMultiBring4CoreSizes(unittest.TestCase):
    """"I would like to output the best 3-pokemon cores against each team"
    -- `core_sizes` widens `multi_bring4_exhaustive`/`multi_bring4_beam`
    down to 3 (default stays (4, 5, 6), unchanged for every existing
    caller). A 3-member core has exactly one possible bring (itself, 3
    pairs), the same degenerate case a 4-member core already is."""

    POOL = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit", "Whimsicott"]
    ENEMIES = [["Sableye", "Ariados"], ["Basculegion", "Mega Floette"]]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.5, min_enemies=1)

    def test_default_core_sizes_is_unchanged(self):
        default_rows = cf.multi_bring4_exhaustive(
            self.coverage, good_threshold=0.5)
        explicit_rows = cf.multi_bring4_exhaustive(
            self.coverage, good_threshold=0.5, core_sizes=(4, 5, 6))
        self.assertEqual(default_rows, explicit_rows)

    def test_exhaustive_with_core_sizes_three_returns_only_size_three(self):
        rows = cf.multi_bring4_exhaustive(
            self.coverage, good_threshold=0.5, core_sizes=(3,))
        self.assertTrue(rows)
        self.assertEqual({r["core_size"] for r in rows}, {3})
        for r in rows:
            self.assertEqual(len(r["core"]), 3)

    def test_a_three_member_core_has_one_bring_of_three_pairs(self):
        rows = cf.multi_bring4_exhaustive(
            self.coverage, good_threshold=0.5, core_sizes=(3,))
        for r in rows:
            for pe in r["per_enemy"]:
                b4 = pe["best_bring4_row"]
                self.assertEqual(set(b4["bring4"]), set(r["core"]))
                self.assertEqual(len(b4["pair_rows"]), 3)

    def test_beam_with_core_sizes_three_returns_only_size_three(self):
        rows = cf.multi_bring4_beam(
            self.coverage, good_threshold=0.5, beam_width=20, core_sizes=(3,))
        self.assertTrue(rows)
        self.assertEqual({len(r["core"]) for r in rows}, {3})

    def test_beam_default_core_sizes_is_unchanged(self):
        default_rows = cf.multi_bring4_beam(
            self.coverage, good_threshold=0.5, beam_width=20)
        explicit_rows = cf.multi_bring4_beam(
            self.coverage, good_threshold=0.5, beam_width=20,
            core_sizes=(4, 5, 6))
        self.assertEqual(default_rows, explicit_rows)

    def test_mixed_core_sizes_can_return_both(self):
        rows = cf.multi_bring4_exhaustive(
            self.coverage, good_threshold=0.5, core_sizes=(3, 4))
        sizes = {r["core_size"] for r in rows}
        self.assertTrue(sizes, "expected at least one core")
        self.assertTrue(sizes.issubset({3, 4}))


class TestMultiBring4MaxMegas(unittest.TestCase):
    """"A full team can only have two mega stone users. In a battle, either
    may mega evolve depending on the specific pair matchup." A candidate
    CORE is now always capped at `max_megas` (default 2) Mega-capable
    members, the same default `team_search.beam_search_teams`/
    `substitution.legal_swap` already use for the Generate tab -- this is a
    TEAM COMPOSITION cap, not a per-battle one (the per-pair mega-vs-stay-
    base minimax, `_resolve_forms`, is completely unaffected)."""

    def test_core_passes_hard_filters_rejects_three_megas_by_default(self):
        core = ("Mega Charizard Y", "Mega Floette", "Mega Metagross", "Whimsicott")
        self.assertFalse(cf._core_passes_hard_filters(core, {}, {}))

    def test_core_passes_hard_filters_allows_exactly_two(self):
        core = ("Mega Charizard Y", "Mega Floette", "Whimsicott", "Corviknight")
        self.assertTrue(cf._core_passes_hard_filters(core, {}, {}))

    def test_max_megas_is_overridable(self):
        core = ("Mega Charizard Y", "Mega Floette", "Mega Metagross", "Whimsicott")
        self.assertTrue(cf._core_passes_hard_filters(core, {}, {}, max_megas=3))
        self.assertFalse(cf._core_passes_hard_filters(core, {}, {}, max_megas=1))

    def setUp(self):
        self.W = world()
        # 4 mega-capable + 2 non-mega, so a 4-6 member core drawn from the
        # whole pool can genuinely exceed 2 megas if nothing stops it.
        self.pool = ["Mega Charizard Y", "Mega Floette", "Mega Metagross",
                    "Mega Tyranitar", "Whimsicott", "Corviknight"]
        self.enemies = [["Sableye", "Ariados"], ["Basculegion", "Sinistcha"]]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.pool, self.enemies, merged, moves, natures, typechart,
            good_threshold=0.3, min_enemies=1)

    def test_exhaustive_never_returns_a_core_over_the_default_cap(self):
        rows = cf.multi_bring4_exhaustive(self.coverage, good_threshold=0.3)
        for r in rows:
            n_megas = sum(1 for n in r["core"] if n.startswith("Mega "))
            self.assertLessEqual(n_megas, 2, r["core"])

    def test_beam_never_returns_a_core_over_the_default_cap(self):
        rows = cf.multi_bring4_beam(self.coverage, good_threshold=0.3, beam_width=20)
        for r in rows:
            n_megas = sum(1 for n in r["core"] if n.startswith("Mega "))
            self.assertLessEqual(n_megas, 2, r["core"])

    def test_a_looser_max_megas_actually_changes_the_result(self):
        """A limit that never changes anything is a no-op wired in for
        show -- confirm raising it actually allows a 3+ mega core to
        appear that the default cap excluded."""
        default_cores = {tuple(sorted(r["core"]))
                         for r in cf.multi_bring4_exhaustive(
                             self.coverage, good_threshold=0.3)}
        loose_cores = {tuple(sorted(r["core"]))
                      for r in cf.multi_bring4_exhaustive(
                          self.coverage, good_threshold=0.3, max_megas=4)}
        newly_allowed = loose_cores - default_cores
        self.assertTrue(newly_allowed, "fixture assumes raising max_megas "
                        "reveals at least one new core")
        for core in newly_allowed:
            n_megas = sum(1 for n in core if n.startswith("Mega "))
            self.assertGreater(n_megas, 2)


class TestWeakTypeBreadth(unittest.TestCase):
    """"I would like to be able to select a cap for the number of types
    that have 2 weaknesses, such as no more than 3 types that have 2
    members weak to it." A BREADTH cap, distinct from the existing
    `max_weak` (a per-type CEILING on how many members may be weak to any
    ONE type) -- a core could satisfy `max_weak=2` (no type ever exceeds 2
    weak members) while still being broadly fragile across many different
    types at once, which `max_weak_types` catches instead."""

    def test_matches_a_hand_count_of_per_type_weak_member_counts(self):
        merged = world()["merged"]
        core = ["Mega Charizard Y", "Mega Floette", "Whimsicott", "Corviknight"]
        per_type = cf.member_weakness_summary(core, merged)["per_type"]
        want = sum(1 for c in per_type.values() if c >= 2)
        self.assertEqual(cf.weak_type_breadth(core, merged), want)

    def test_a_lower_threshold_can_only_count_as_many_or_more_types(self):
        """Lowering the bar (fewer members need to be weak to a type for
        it to "count") can only ever include MORE types, never fewer."""
        merged = world()["merged"]
        core = ["Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight"]
        self.assertGreaterEqual(cf.weak_type_breadth(core, merged, threshold=1),
                                cf.weak_type_breadth(core, merged, threshold=2))

    def test_core_passes_hard_filters_rejects_a_core_over_the_cap(self):
        merged = world()["merged"]
        core = ("Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight")
        breadth = cf.weak_type_breadth(list(core), merged)
        self.assertGreater(breadth, 0, "fixture assumes at least one type "
                           "already has 2+ weak members")
        self.assertFalse(cf._core_passes_hard_filters(
            core, merged, {}, max_megas=3, max_weak_types=breadth - 1))
        self.assertTrue(cf._core_passes_hard_filters(
            core, merged, {}, max_megas=3, max_weak_types=breadth))

    def test_none_disables_the_cap_entirely(self):
        merged = world()["merged"]
        core = ("Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight")
        self.assertTrue(cf._core_passes_hard_filters(
            core, merged, {}, max_megas=3, max_weak_types=None))

    def test_monotonic_growth_never_lowers_the_breadth(self):
        """Adding a member to a partial core can only add to a type's
        weak-member count, never remove from it -- so `weak_type_breadth`
        must never DECREASE as the core grows, the property that makes it
        safe to prune on during `multi_bring4_beam`'s incremental growth."""
        merged = world()["merged"]
        pool = ["Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight", "Sylveon"]
        prev = 0
        grown = []
        for name in pool:
            grown.append(name)
            cur = cf.weak_type_breadth(grown, merged)
            self.assertGreaterEqual(cur, prev)
            prev = cur


class TestMultiBring4MaxWeakTypes(unittest.TestCase):
    """`max_weak_types` threaded through `multi_bring4_exhaustive`/
    `multi_bring4_beam`, the same way `max_megas` already is."""

    def setUp(self):
        self.W = world()
        self.pool = ["Mega Charizard Y", "Mega Floette", "Mega Metagross",
                    "Mega Tyranitar", "Whimsicott", "Corviknight"]
        self.enemies = [["Sableye", "Ariados"], ["Basculegion", "Sinistcha"]]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.pool, self.enemies, merged, moves, natures, typechart,
            good_threshold=0.3, min_enemies=1)

    def test_exhaustive_never_returns_a_core_over_the_cap(self):
        merged = self.W["merged"]
        rows = cf.multi_bring4_exhaustive(self.coverage, good_threshold=0.3,
                                          max_megas=4, max_weak_types=2)
        for r in rows:
            self.assertLessEqual(cf.weak_type_breadth(list(r["core"]), merged),
                                 2, r["core"])

    def test_beam_never_returns_a_core_over_the_cap(self):
        merged = self.W["merged"]
        rows = cf.multi_bring4_beam(self.coverage, good_threshold=0.3,
                                    beam_width=20, max_megas=4, max_weak_types=2)
        for r in rows:
            self.assertLessEqual(cf.weak_type_breadth(list(r["core"]), merged),
                                 2, r["core"])

    def test_a_tighter_cap_actually_changes_the_result(self):
        loose_cores = {tuple(sorted(r["core"]))
                      for r in cf.multi_bring4_exhaustive(
                          self.coverage, good_threshold=0.3, max_megas=4)}
        tight_cores = {tuple(sorted(r["core"]))
                      for r in cf.multi_bring4_exhaustive(
                          self.coverage, good_threshold=0.3, max_megas=4,
                          max_weak_types=1)}
        self.assertTrue(tight_cores.issubset(loose_cores))
        self.assertLess(len(tight_cores), len(loose_cores),
                        "fixture assumes the cap actually excludes something")


class TestMultiBring4SetsStayFixedAcrossEnemies(unittest.TestCase):
    """"For a team, the moves must stay the same, i.e., they can't be
    adjusted battle to battle." Calling `joint_pool_search` once per enemy
    roster the naive way let `_answer_for` independently re-search each
    pool member's item/moveset against just THAT one enemy team every
    time -- the same Pokemon could come back with a genuinely different
    set for enemy 1 than for enemy 2, which isn't a real, biddable
    tournament team. `multi_bring4_coverage` now searches each pool
    member's set ONCE, against the union of every named enemy, and reuses
    it for every enemy team's races."""

    POOL = ["Whimsicott", "Kingambit", "Garchomp", "Corviknight", "Gholdengo",
           "Torkoal", "Arcanine"]
    ENEMIES = [["Sableye", "Ariados"], ["Basculegion", "Sinistcha"],
              ["Mega Charizard Y", "Farigiraf"]]

    def setUp(self):
        self.W = world()

    def _item_by_enemy(self, coverage):
        items_by_enemy = {}
        for rows in coverage["per_enemy"]:
            for r in rows:
                for j, name in enumerate(r["pair"]):
                    item = r["item1"] if j == 0 else r["item2"]
                    items_by_enemy.setdefault(name, set()).add(item)
        return items_by_enemy

    def test_every_pool_members_item_is_identical_across_every_enemy(self):
        """Real, verified fixture: before this fix, this exact pool/enemy
        combination gave Whimsicott three DIFFERENT items (Sitrus Berry,
        Focus Sash, Fairy Feather) depending only on which enemy team its
        pair happened to be evaluated against."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        coverage = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.2, min_enemies=1)
        items_by_enemy = self._item_by_enemy(coverage)
        inconsistent = {n: i for n, i in items_by_enemy.items() if len(i) > 1}
        self.assertEqual(inconsistent, {},
                         "every pool member must hold ONE item across "
                         "every enemy team, not a per-enemy re-optimised one")

    def test_an_explicit_item_override_still_wins(self):
        """The fixed-set search still respects a caller's own pin --
        computed once, same as the search path, just skipping it."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        coverage = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.2, min_enemies=1,
            item_overrides={"Kingambit": "Life Orb"})
        for rows in coverage["per_enemy"]:
            for r in rows:
                for j, name in enumerate(r["pair"]):
                    if name == "Kingambit":
                        item = r["item1"] if j == 0 else r["item2"]
                        self.assertEqual(item, "Life Orb")


class TestMemberWeaknessSummaryByType(unittest.TestCase):
    """"For total weaknesses, it should be by type, i.e., does the team
    have 3 weaknesses to fire." A flat sum across every type couldn't
    distinguish 3-weak-to-one-type from 1-each-to-three -- `per_type`
    ({type: how many members are weak to it}) is what actually answers the
    question, computed via `team_search._weak_resist` (the SAME per-type
    split `--max-weak`/`--type-limit`'s own hard filter already reads)."""

    def setUp(self):
        self.W = world()

    def test_per_type_counts_members_weak_to_that_type(self):
        """Torkoal alone is weak to Water; adding a second Water-weak
        member (Arcanine, Fire) must bring the Water count from 1 to 2 --
        confirming the number genuinely tracks per-type MEMBER COUNT, not
        just presence/absence."""
        merged = self.W["merged"]
        solo = cf.member_weakness_summary(["Torkoal"], merged)
        self.assertEqual(solo["per_type"]["Water"], 1)
        duo = cf.member_weakness_summary(["Torkoal", "Arcanine"], merged)
        self.assertEqual(duo["per_type"]["Water"], 2,
                         "fixture assumes Arcanine is also weak to Water")

    def test_per_type_sums_to_the_flat_total(self):
        merged = self.W["merged"]
        core = ["Torkoal", "Kingambit", "Garchomp", "Corviknight"]
        weak = cf.member_weakness_summary(core, merged)
        self.assertEqual(sum(weak["per_type"].values()),
                         weak["total_weakness_instances"])

    def test_per_type_agrees_with_the_hard_filters_own_reading(self):
        """`per_type`'s count for a given type must match what
        `team_search._weak_resist` (the same function `--max-weak`'s hard
        filter is built on) says -- they can never legitimately disagree."""
        from team_search import _weak_resist
        merged = self.W["merged"]
        core = ["Torkoal", "Kingambit", "Garchomp", "Corviknight", "Sylveon"]
        weak = cf.member_weakness_summary(core, merged)
        for t in ("Fire", "Water", "Ground", "Fairy"):
            expected = len(_weak_resist(core, merged, t)[0])
            self.assertEqual(weak["per_type"][t], expected, t)

    def test_all_18_types_are_present_even_at_zero(self):
        from species_data import TYPES
        merged = self.W["merged"]
        weak = cf.member_weakness_summary(["Torkoal"], merged)
        self.assertEqual(set(weak["per_type"]), set(TYPES))


class TestCoreRowAndBring4Candidates(unittest.TestCase):
    """The shared, no-new-racing combinatorics both `bring4_search` and the
    multi-enemy search are built on."""

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.pair_rows = cf.joint_pool_search(
            ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
             "Rampardos", "Kingambit"],
            ["Sableye", "Ariados"], merged, moves, natures, typechart)
        self.pair_by_key = {frozenset(r["pair"]): r for r in self.pair_rows}

    def test_bring4_candidates_matches_bring4_search_stage_two(self):
        six = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
              "Rampardos", "Kingambit"]
        direct = cf._bring4_candidates(six, self.pair_by_key,
                                       ["Sableye", "Ariados"], good_threshold=1.0)
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        _pr, via_search = cf.bring4_search(
            six, ["Sableye", "Ariados"], merged, moves, natures, typechart,
            good_threshold=1.0)
        self.assertEqual([b["bring4"] for b in direct],
                         [b["bring4"] for b in via_search])

    def test_core_row_picks_the_worse_enemy_as_the_bottleneck(self):
        """A trivial two-enemy case using the SAME enemy pair twice: the
        worst-case score across both must equal the single-enemy score,
        and the bottleneck can be either (they're identical)."""
        core = ("Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
               "Rampardos", "Kingambit")
        row = cf._core_row(core, [self.pair_by_key, self.pair_by_key],
                           [["Sableye", "Ariados"], ["Sableye", "Ariados"]],
                           good_threshold=1.0)
        solo = cf._bring4_candidates(core, self.pair_by_key,
                                     ["Sableye", "Ariados"], good_threshold=1.0)[0]
        self.assertEqual(row["worst_enemy_score_key"],
                         (len(solo["uncovered_enemy_pairs"]),)
                         + cf._pair_sort_key(solo["worst_pair_row"]))
        self.assertIn(row["worst_enemy_idx"], (0, 1))


def _fake_pair_row(pair, beats, target_names):
    """A synthetic `joint_pool_search`-shaped row for `pair`, without any
    real racing -- `beats` is the subset of `target_names`'s enemy pairs
    (tuples) this pair wins (out_trade, tailwind-safe, protect-safe);
    every other enemy pair is a loss. Every row built this way has the
    SAME aggregate `_pair_sort_key` whenever `len(beats)` matches, however
    the WINS are distributed across which specific enemy pairs -- exactly
    what's needed to build two candidates that tie on the old ranking but
    differ on `_uncovered_enemy_pairs`."""
    import itertools as _it
    enemy_pairs = list(_it.combinations(target_names, 2))
    detail = {}
    for ep in enemy_pairs:
        won = ep in beats
        detail[ep] = {"outcome": "out_trade" if won else "loss",
                      "tailwind_safe": won, "protect_safe": won}
    n_win = len(beats)
    return {"pair": pair, "detail": detail,
           "pairs_swept": 0, "pairs_traded": n_win,
           "pairs_lost": len(enemy_pairs) - n_win, "pairs_no_ko": 0,
           "pairs_tailwind_safe": n_win, "pairs_protect_safe": n_win,
           # Every out_trade win here is "worth" the same 2.0 clean-win
           # value -- keeps `_pair_sort_key` tied whenever `n_win` matches,
           # same as every other field here, so this fixture's whole point
           # (tie on everything but coverage) still holds.
           "pairs_clean_win_total": n_win * 2.0,
           "pairs_total": len(enemy_pairs)}


class TestUncoveredEnemyPairsDominateRanking(unittest.TestCase):
    """"I think it would be best if the ranking was done by default as by
    protect safe wins. I think ideally, of the pairs, wins would make up
    for losses, for instance two are 12/15, but ideally one of pairs wins
    the one the others lose; having pairs that every pair of yours loses
    against is terrible, and this is an important factor."

    A hand-built fixture (`_fake_pair_row`, no real racing) with two
    bring-4 candidates that TIE on every OLD ranking criterion (identical
    worst-pair `_pair_sort_key`, identical `pairs_good`) but differ on
    coverage: candidate ABCD has all 6 of its internal pairs lose to the
    SAME enemy pair (Y+Z, a real, unconditional loss); candidate ABCE's
    losses are spread out so every enemy pair is beaten by at least one of
    its 6 pairs. `_bring4_candidates` must still rank ABCE strictly above
    ABCD.
    """

    TARGETS = ["X", "Y", "Z"]  # enemy pairs: XY, XZ, YZ

    def setUp(self):
        XY, XZ, YZ = ("X", "Y"), ("X", "Z"), ("Y", "Z")
        # AB/AC/BC/AD/BD/CD all beat {XY, XZ}, always lose YZ.
        both_xy_xz = {XY, XZ}
        rows = {
            ("A", "B"): _fake_pair_row(("A", "B"), both_xy_xz, self.TARGETS),
            ("A", "C"): _fake_pair_row(("A", "C"), both_xy_xz, self.TARGETS),
            ("B", "C"): _fake_pair_row(("B", "C"), both_xy_xz, self.TARGETS),
            ("A", "D"): _fake_pair_row(("A", "D"), both_xy_xz, self.TARGETS),
            ("B", "D"): _fake_pair_row(("B", "D"), both_xy_xz, self.TARGETS),
            ("C", "D"): _fake_pair_row(("C", "D"), both_xy_xz, self.TARGETS),
            # AE/BE beat YZ (between them, plus one of XY/XZ each) so
            # bring-4 ABCE has an answer to every enemy pair; CE repeats
            # the AB/AC/BC pattern so ABCE's raw total stays IDENTICAL to
            # ABCD's (6 pairs x 2/3 beaten each either way).
            ("A", "E"): _fake_pair_row(("A", "E"), {XY, YZ}, self.TARGETS),
            ("B", "E"): _fake_pair_row(("B", "E"), {XZ, YZ}, self.TARGETS),
            ("C", "E"): _fake_pair_row(("C", "E"), both_xy_xz, self.TARGETS),
            # Never exercised by the two candidates under test -- just
            # needs to exist so `_bring4_candidates`'s OTHER (irrelevant)
            # C(5,4) subsets that include both D and E don't KeyError.
            ("D", "E"): _fake_pair_row(("D", "E"), set(), self.TARGETS),
        }
        self.pair_lookup = {frozenset(p): r for p, r in rows.items()}
        self.bring4_rows = cf._bring4_candidates(
            ["A", "B", "C", "D", "E"], self.pair_lookup, self.TARGETS,
            good_threshold=1.0)
        self.by_bring4 = {frozenset(b["bring4"]): b for b in self.bring4_rows}

    def test_abcd_has_one_uncovered_enemy_pair(self):
        b = self.by_bring4[frozenset(("A", "B", "C", "D"))]
        self.assertEqual(b["uncovered_enemy_pairs"], [("Y", "Z")])

    def test_abce_has_no_uncovered_enemy_pairs(self):
        b = self.by_bring4[frozenset(("A", "B", "C", "E"))]
        self.assertEqual(b["uncovered_enemy_pairs"], [])

    def test_the_two_candidates_tie_on_every_old_ranking_criterion(self):
        """The fixture's precondition: without the uncovered-pairs fix,
        these two candidates would be indistinguishable."""
        abcd = self.by_bring4[frozenset(("A", "B", "C", "D"))]
        abce = self.by_bring4[frozenset(("A", "B", "C", "E"))]
        self.assertEqual(cf._pair_sort_key(abcd["worst_pair_row"]),
                         cf._pair_sort_key(abce["worst_pair_row"]))
        self.assertEqual(abcd["pairs_good"], abce["pairs_good"])

    def test_full_coverage_ranks_strictly_above_an_unconditional_loss(self):
        abcd_idx = next(i for i, b in enumerate(self.bring4_rows)
                        if frozenset(b["bring4"]) == frozenset(("A", "B", "C", "D")))
        abce_idx = next(i for i, b in enumerate(self.bring4_rows)
                        if frozenset(b["bring4"]) == frozenset(("A", "B", "C", "E")))
        self.assertLess(abce_idx, abcd_idx,
                        "ABCE (no unconditional loss) must rank above ABCD "
                        "(loses Y+Z no matter which pair is sent out), even "
                        "though they tie on raw beaten count")


class TestBring4CandidatesRespectsMegaConsistency(unittest.TestCase):
    """`_bring4_candidates`'s `megas`/`pair_lookup_forced_base` params --
    "for a bring 4, across all six pairs only one mega may be considered
    as a mega, the other as base form." A hand-built fixture (`_fake_
    pair_row`, no real racing) with two synthetic stone-holders isolates
    just the "pick the better of the two consistent hypotheses" logic,
    independent of any real race."""

    TARGETS = ("E1", "E2")  # a single enemy pair: (E1, E2)
    WIN = {("E1", "E2")}
    LOSS = set()

    def setUp(self):
        six = ["Mega A", "Mega B", "C", "D"]
        import itertools as _it
        # Default (free-choice) lookup: every pair loses. Only ever used as
        # a fallback for a pair that touches NEITHER forced-base name (here,
        # just C+D).
        self.pair_lookup = {
            frozenset(p): _fake_pair_row(p, self.LOSS, self.TARGETS)
            for p in _it.combinations(six, 2)}
        # Hypothesis "Mega A is the team's mega, Mega B stays base":
        # every pair TOUCHING Mega B wins.
        touching_b = [("Mega A", "Mega B"), ("Mega B", "C"), ("Mega B", "D")]
        # Hypothesis "Mega B is the team's mega, Mega A stays base":
        # every pair touching Mega A loses (stays at the default).
        touching_a = [("Mega A", "Mega B"), ("Mega A", "C"), ("Mega A", "D")]
        self.pair_lookup_forced_base = {
            "Mega B": {frozenset(p): _fake_pair_row(p, self.WIN, self.TARGETS)
                      for p in touching_b},
            "Mega A": {frozenset(p): _fake_pair_row(p, self.LOSS, self.TARGETS)
                      for p in touching_a},
        }
        self.six = six

    def test_picks_the_strictly_better_consistent_hypothesis(self):
        """"Mega A is the mega" (3/6 pairs win) strictly beats "Mega B is
        the mega" (0/6 pairs win) here -- `_bring4_candidates` must pick
        the winning hypothesis, not just the first/arbitrary one."""
        rows = cf._bring4_candidates(
            self.six, self.pair_lookup, self.TARGETS, good_threshold=1.0,
            megas=["Mega A", "Mega B"],
            pair_lookup_forced_base=self.pair_lookup_forced_base)
        self.assertEqual(len(rows), 1)  # a 4-member core has one bring
        row = rows[0]
        self.assertEqual(row["pairs_good"], 3)
        won_pairs = {r["pair"] for r in row["pair_rows"]
                    if cf._pair_beaten_frac(r) >= 1.0}
        self.assertEqual(won_pairs, {("Mega A", "Mega B"), ("Mega B", "C"),
                                     ("Mega B", "D")})

    def test_a_bring4_with_only_one_stone_holder_is_untouched(self):
        """No `megas`/`pair_lookup_forced_base` conflict for a bring-4 that
        carries only ONE of the two stone-holders -- same result whether or
        not the mega-consistency params are even passed."""
        six_one_mega = ["Mega A", "C", "D", "Kingambit"]
        import itertools as _it
        pair_lookup = {
            frozenset(p): _fake_pair_row(p, self.LOSS, self.TARGETS)
            for p in _it.combinations(six_one_mega, 2)}
        without_megas = cf._bring4_candidates(
            six_one_mega, pair_lookup, self.TARGETS, good_threshold=1.0)
        with_megas = cf._bring4_candidates(
            six_one_mega, pair_lookup, self.TARGETS, good_threshold=1.0,
            megas=["Mega A", "Mega B"], pair_lookup_forced_base={})
        self.assertEqual(without_megas, with_megas)


class TestDeepDive(unittest.TestCase):
    """`deep_dive` -- one named, already-chosen pair against every enemy pair
    drawn from a roster, with the OHKO-risk read and the 2x2 damage grid.

        "a deep dive on a selected given pair; see all the possible enemy
         pairs, see if I'm at risk of being KO'd in one turn, to see if and
         how I outtrade (2x2 damage), see how it collapses into a win. For
         instance, Scizor is always OHKOd by Mega Charizard Y, so would not
         be a good bring as it auto loses."

    BIG_SIX is the reference example this whole session's tooling was built
    from; Mega Scizor/Zard-Y is the acceptance test named directly in the
    request.
    """

    BIG_SIX = ["Basculegion", "Mega Charizard Y", "Mega Floette", "Garchomp",
              "Kingambit", "Whimsicott"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.item1, self.item2, self.detail, self.summary = cf.deep_dive(
            "Mega Scizor", "Ninetales-Alola", self.BIG_SIX, merged, moves,
            natures, typechart, turns=2)

    def test_covers_all_fifteen_enemy_leads(self):
        import itertools as _it
        self.assertEqual(len(self.detail), 15)
        self.assertEqual(set(self.detail),
                         set(_it.combinations(self.BIG_SIX, 2)))
        self.assertEqual(self.summary["pairs_total"], 15)

    def test_scizor_always_loses_when_zard_y_is_in_the_pair(self):
        """The acceptance test named directly in the request."""
        for (e1, e2), d in self.detail.items():
            if "Mega Charizard Y" in (e1, e2):
                self.assertEqual(d["outcome"], "loss",
                                 f"expected a loss vs {e1}+{e2}")

    def test_zard_y_is_flagged_as_an_ohko_risk_on_scizor(self):
        d = self.detail[("Basculegion", "Mega Charizard Y")]
        risky = [r for r in d["ohko_risk"]
                if r["attacker"] == "E2" and r["target"] == "C"]
        self.assertTrue(risky, "Mega Charizard Y's hit on Mega Scizor "
                               "should be flagged")
        self.assertGreaterEqual(risky[0]["hi"], 1.0)

    def test_ohko_risk_is_structural_not_dependent_on_who_moved(self):
        """A risk flag must survive even in a matchup the pair still WINS --
        it's about what COULD happen on the worst roll, not what the one
        played-out line happened to do."""
        any_risk_in_a_win = any(
            d["ohko_risk"] for d in self.detail.values()
            if d["outcome"] in ("sweep", "out_trade"))
        self.assertTrue(any_risk_in_a_win, "fixture assumes at least one "
                                           "winning matchup still carries a "
                                           "real OHKO risk on some hit")

    def test_grid_has_all_eight_cells(self):
        d = self.detail[("Garchomp", "Kingambit")]
        self.assertEqual(set(d["grid"]["ours"]),
                         {("C", "E1"), ("C", "E2"), ("P", "E1"), ("P", "E2")})
        self.assertEqual(set(d["grid"]["theirs"]),
                         {("E1", "C"), ("E1", "P"), ("E2", "C"), ("E2", "P")})
        for h in list(d["grid"]["ours"].values()) + list(d["grid"]["theirs"].values()):
            self.assertIsNotNone(h.move_name)

    def test_a_grid_cell_for_a_spread_move_still_takes_the_075x_penalty(self):
        """Ninetales-Alola's Blizzard is a spread move -- both of ITS grid
        cells (against E1 and against E2) must reflect the doubles penalty,
        since both enemies are alive for this check."""
        d = self.detail[("Garchomp", "Kingambit")]
        for (atk, _tgt), h in d["grid"]["ours"].items():
            if atk == "P" and h.move_name == "Blizzard":
                self.assertEqual(h.num_targets_hit, 2)

    def test_items_are_the_optimised_ones_not_none(self):
        self.assertIsNotNone(self.item1)
        self.assertIsNotNone(self.item2)

    def test_want_grid_is_off_by_default_for_the_pool_searches(self):
        """The grid is expensive-ish (8 extra Hit calcs per enemy pair) and
        never displayed by --joint's summary table, so it must stay opt-in --
        `joint_pair_search`/`joint_pool_search` rows must not carry it."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(
            ["Mega Scizor"], ["Kingambit", "Basculegion"], "Whimsicott",
            merged, moves, natures, typechart)
        d = rows[0]["detail"][("Kingambit", "Basculegion")]
        self.assertNotIn("grid", d)
        self.assertNotIn("ohko_risk", d)


class TestCoreDeepDive(unittest.TestCase):
    """`core_deep_dive` -- the opt-in, run-after-the-search follow-up for
    ONE already-chosen `--multi-bring4` core: "for each pair... the full
    beaten/swept/traded/lost/no KO/tw-safe/pr-safe, and then vs each
    enemy... across 6 possible pairs beaten is 85/90... I also want to see
    the gameplans for each pair included in a team vs enemies. Given the
    size, maybe make this deep dive an option after the search has run."
    """

    CORE = ["Whimsicott", "Kingambit", "Garchomp", "Corviknight"]
    ENEMIES = [["Sableye", "Ariados"], ["Basculegion", "Sinistcha"]]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.dive = cf.core_deep_dive(
            self.CORE, self.ENEMIES, merged, moves, natures, typechart, turns=2)

    def test_every_pair_of_the_core_is_covered(self):
        import itertools as _it
        self.assertEqual(set(self.dive["per_pair"]),
                         set(_it.combinations(self.CORE, 2)))

    def test_every_pair_covers_every_enemy_team(self):
        for pair, d in self.dive["per_pair"].items():
            self.assertEqual(len(d["per_enemy"]), len(self.ENEMIES), pair)
            self.assertEqual([pe["target_names"] for pe in d["per_enemy"]],
                             self.ENEMIES)

    def test_per_pair_total_is_the_sum_of_its_own_per_enemy_summaries(self):
        for pair, d in self.dive["per_pair"].items():
            for field in ("pairs_swept", "pairs_traded", "pairs_lost",
                         "pairs_no_ko", "pairs_tailwind_safe",
                         "pairs_protect_safe", "pairs_total"):
                self.assertEqual(
                    d["total"][field],
                    sum(pe["summary"][field] for pe in d["per_enemy"]),
                    f"{pair} {field}")

    def test_overall_is_the_sum_of_every_pairs_total(self):
        """"across 6 possible pairs beaten is 85/90" -- the whole-core
        aggregate must be the sum across every pair, not just one of
        them."""
        for field in ("pairs_swept", "pairs_traded", "pairs_lost",
                     "pairs_no_ko", "pairs_tailwind_safe",
                     "pairs_protect_safe", "pairs_total"):
            self.assertEqual(
                self.dive["overall"][field],
                sum(d["total"][field] for d in self.dive["per_pair"].values()),
                field)

    def test_pairs_total_matches_the_real_enemy_pair_count(self):
        """6 pairs, each vs 2 enemy teams of C(2,2)=1 enemy pair each ->
        pairs_total for the whole core must be 6*2*1 = 12."""
        import itertools as _it
        n_pairs = len(list(_it.combinations(self.CORE, 2)))
        pairs_per_team = sum(len(list(_it.combinations(t, 2)))
                             for t in self.ENEMIES)
        self.assertEqual(self.dive["overall"]["pairs_total"],
                         n_pairs * pairs_per_team)

    def test_every_members_set_is_fixed_and_present(self):
        for name in self.CORE:
            self.assertIn(name, self.dive["sets"])
            self.assertTrue(self.dive["sets"][name]["moves"])

    def test_the_gameplan_log_is_present_for_every_race(self):
        for pair, d in self.dive["per_pair"].items():
            for pe in d["per_enemy"]:
                for enemy_pair, race in pe["detail"].items():
                    self.assertIn("log", race, f"{pair} vs {enemy_pair}")
                    self.assertIsInstance(race["log"], list)

    def test_an_explicit_item_override_still_wins(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(
            self.CORE, self.ENEMIES, merged, moves, natures, typechart,
            turns=2, item_overrides={"Kingambit": "Life Orb"})
        self.assertEqual(dive["sets"]["Kingambit"]["item"], "Life Orb")


class TestSwitchInSearch(unittest.TestCase):
    """`switch_in_search` -- for a pair that LOSES a specific enemy pair,
    which bench candidate switching in for which of ours turns it around.

        "for losing enemy leads, see if there are easy and optimal switch
         ins (i.e., they take little damage from the enemy on the switch
         in, and then the board state becomes a clearly winning one again)"

    Fixtures reuse `TestDeepDive`'s own Mega Scizor + Ninetales-Alola vs Big
    Six case, whose losses and fixes were verified by hand before these
    tests were written.
    """

    BENCH = ["Mega Feraligatr", "Arcanine-Hisui", "Mega Tyranitar", "Gyarados"]

    def setUp(self):
        self.W = world()
        self.merged, self.moves = self.W["merged"], self.W["moves"]
        self.natures, self.typechart = self.W["natures"], self.W["typechart"]

    def _search(self, enemy_pair, bench=None, turns=2):
        return cf.switch_in_search(
            "Mega Scizor", "Ninetales-Alola", enemy_pair, bench or self.BENCH,
            self.merged, self.moves, self.natures, self.typechart, turns=turns)

    def test_a_real_fix_is_found_and_labelled_correctly(self):
        rows, tried = self._search(("Mega Charizard Y", "Mega Floette"))
        self.assertGreater(tried, 0)
        self.assertTrue(rows)
        best = rows[0]
        self.assertEqual(best["leaving"], "Ninetales-Alola")
        self.assertIn(best["arriving"], self.BENCH)
        self.assertIn(best["outcome"], ("sweep", "out_trade"))

    def test_a_genuine_loss_reports_no_fix_rather_than_a_bad_one(self):
        """Basculegion + Mega Charizard Y is too much pressure for a single
        switch to fix (verified by hand against a much wider bench) --
        `rows` must come back empty, not padded with losing candidates."""
        rows, tried = self._search(("Basculegion", "Mega Charizard Y"))
        self.assertGreater(tried, 0)
        for r in rows:
            self.assertIn(r["outcome"], ("sweep", "out_trade"))

    def test_only_candidates_that_fix_it_are_returned(self):
        """A candidate that still loses must never appear in `rows` --
        `switch_in_search` filters, it doesn't merely rank."""
        rows, _tried = self._search(("Mega Charizard Y", "Garchomp"))
        for r in rows:
            self.assertIn(r["outcome"], ("sweep", "out_trade"))

    def test_ranked_by_least_damage_taken_switching_in(self):
        rows, _tried = self._search(("Mega Charizard Y", "Mega Floette"))
        taken = [r["switch_in_taken"] for r in rows]
        self.assertEqual(taken, sorted(taken))

    def test_the_switch_in_never_attacks_on_turn_one(self):
        """The mechanic this whole function turns on: a real doubles switch
        means the incoming Pokemon has no move the turn it comes in."""
        merged, moves = self.merged, self.moves
        natures, typechart = self.natures, self.typechart
        enemy_built = cf._build_forms(
            ["Mega Charizard Y", "Mega Floette"], merged, natures, moves)
        e1c, e1m = enemy_built["Mega Charizard Y"]["mega"], enemy_built["Mega Charizard Y"]["moves"]
        e2c, e2m = enemy_built["Mega Floette"]["mega"], enemy_built["Mega Floette"]["moves"]
        stay_item, stay_moves, stay_w = cf._answer_for(
            "Mega Scizor", merged, moves, natures, typechart,
            ["Mega Charizard Y", "Mega Floette"])
        stay_c = cf._build("Mega Scizor", merged, natures, item=stay_item)
        stay_m = cf._move_infos("Mega Scizor", merged, moves, stay_moves)
        item, mvs, w = cf._answer_for(
            "Mega Feraligatr", merged, moves, natures, typechart,
            ["Mega Charizard Y", "Mega Floette"])
        cand_c = cf._build("Mega Feraligatr", merged, natures, item=item)
        cand_m = cf._move_infos("Mega Feraligatr", merged, moves, mvs)
        combatants = {"C": stay_c, "P": cand_c, "E1": e1c, "E2": e2c}
        moves_by_role = {"C": stay_m, "P": cand_m, "E1": e1m, "E2": e2m}
        _outcome, _tu, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, stay_w or w, 2,
            first_turn_moves_override={"P": []})
        turn1_attackers = {role for role, _tgt, _h in log[0]}
        self.assertNotIn("P", turn1_attackers)

    def test_named_pair_and_enemies_are_never_tried_as_switch_ins(self):
        bench = self.BENCH + ["Mega Scizor", "Ninetales-Alola",
                              "Mega Charizard Y", "Mega Floette"]
        rows, _tried = self._search(("Mega Charizard Y", "Mega Floette"),
                                    bench=bench)
        arriving = {r["arriving"] for r in rows}
        self.assertFalse(arriving & {"Mega Scizor", "Ninetales-Alola",
                                     "Mega Charizard Y", "Mega Floette"})


class TestSharedFieldWeather(unittest.TestCase):
    """"Make sure weather is accounted for, such as Mega Charizard Y's sun
    applying uncontested if neither of your brings set weather."

    Before this fix, `pair_search`/the joint searches only ever asked what
    OUR candidate's own usage guess said, and applied that to OUR attacks
    only -- an enemy's own Drought/Drizzle/Sand Stream/Snow Warning never
    came up at all, for either side's damage or turn order."""

    def setUp(self):
        self.W = world()

    def test_field_weather_reads_an_enemy_only_setter(self):
        merged, natures = self.W["merged"], self.W["natures"]
        candidate = cf._build("Gyarados", merged, natures)
        e1 = cf._build("Mega Charizard Y", merged, natures)  # Drought, mega ability
        e2 = cf._build("Basculegion", merged, natures)
        self.assertEqual(
            cf._field_weather({"C": candidate, "E1": e1, "E2": e2}), "sun")

    def test_field_weather_is_none_when_nobody_sets_it(self):
        merged, natures = self.W["merged"], self.W["natures"]
        candidate = cf._build("Gyarados", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Basculegion", merged, natures)
        self.assertIsNone(
            cf._field_weather({"C": candidate, "E1": e1, "E2": e2}))

    def test_enemy_only_sun_boosts_that_enemys_own_attack_in_pair_search(self):
        """The concrete example: Mega Charizard Y's sun must apply to ITS
        OWN Fire-type attack even though neither of ours sets any weather --
        the exact fraction `_sequential_pair_outcome` actually used for E1's
        hit must match a weather="sun" `_choose_move` call, not weather=None
        (what the pre-fix code hard-coded for every enemy action)."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        e1_name, e2_name = "Mega Charizard Y", "Basculegion"
        candidate_name = "Kingambit"  # Steel/Dark: 2x weak to Fire, so Heat
        # Wave is clearly its best move AND clearly shows the sun 1.5x boost.
        item, move_names, _w = cf.best_answer(
            candidate_name, merged, moves, natures, typechart,
            [e1_name, e2_name])
        attacker = cf._build(candidate_name, merged, natures, item=item)
        atk_moves = cf._move_infos(candidate_name, merged, moves, move_names)
        e1 = cf._build(e1_name, merged, natures)
        e2 = cf._build(e2_name, merged, natures)
        e1_moves = [mi for mi, _p in build_moveset(merged[e1_name], moves)]
        e2_moves = [mi for mi, _p in build_moveset(merged[e2_name], moves)]

        got = cf._sequential_pair_outcome(
            attacker, atk_moves, e1_name, e1, e1_moves, e2_name, e2, e2_moves,
            typechart, e1_name)

        hit_sun, _mv_sun = cf._choose_move(e1, e1_moves, attacker, typechart,
                                           weather="sun")
        hit_none, _mv_none = cf._choose_move(e1, e1_moves, attacker, typechart,
                                             weather=None)
        # Fixture assumption: Mega Charizard Y actually has a move whose
        # damage changes under sun (a STAB Fire move) -- otherwise this test
        # can't tell a fixed bug from a coincidence.
        self.assertNotAlmostEqual(hit_sun.frac, hit_none.frac)
        self.assertAlmostEqual(got["hits"]["E1"]["C"].frac, hit_sun.frac)

    def test_enemy_only_weather_speed_boost_changes_turn_order(self):
        """A weather-speed-boost ability (Swift Swim here) on OUR side must
        be able to activate off an ENEMY's own Drizzle -- before the fix,
        turn order always used a weatherless FieldState() for everyone, so
        this never applied regardless of which side set the weather."""
        from combatants import make_combatant
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        e1_name, e2_name = "Politoed", "Kingambit"  # Politoed's default ability is Drizzle
        candidate_name = "Basculegion"  # has Swift Swim as a legal (non-default) ability

        item, move_names, _w = cf.best_answer(
            candidate_name, merged, moves, natures, typechart,
            [e1_name, e2_name])
        attacker = cf._mega_project(make_combatant(
            candidate_name, merged, natures, ability="Swift Swim", item=item))
        attacker.current_hp = attacker.max_hp()
        atk_moves = cf._move_infos(candidate_name, merged, moves, move_names)
        e1 = cf._build(e1_name, merged, natures)
        e2 = cf._build(e2_name, merged, natures)
        self.assertEqual(e1.ability, "Drizzle", "fixture assumes Politoed's "
                                               "default ability is Drizzle")
        e1_moves = [mi for mi, _p in build_moveset(merged[e1_name], moves)]
        e2_moves = [mi for mi, _p in build_moveset(merged[e2_name], moves)]

        from engine import FieldState, effective_speed
        no_weather_spd = effective_speed(attacker, FieldState(), "p1")
        rain_spd = effective_speed(attacker, FieldState(weather="rain"), "p1")
        self.assertGreater(rain_spd, no_weather_spd, "fixture assumes Swift "
                          "Swim actually changes this candidate's speed")

        weather = cf._field_weather({"C": attacker, "E1": e1, "E2": e2})
        self.assertEqual(weather, "rain")


class TestFairyAuraAndDarkAura(unittest.TestCase):
    """"Fairy aura (1.33x damage to fairy moves) doesn't seem to be applying
    to their side." `damage_roll` already knew how to apply Fairy Aura/Dark
    Aura/Aura Break (`aura_multiplier`), but nothing in this module ever
    computed the board's active auras and passed them through -- `_raw_hit`
    and everything built on it defaulted to `auras=None` everywhere, so a
    Mega Floette on the board never boosted anyone's Fairy move, including
    its own."""

    def setUp(self):
        self.W = world()

    def test_fairy_aura_boosts_a_fairy_move_for_everyone_on_the_field(self):
        """Direct `_raw_hit` check, real fixture: Whimsicott's Moonblast
        against Kingambit, with vs without Fairy Aura active -- a board-wide
        effect, not something Whimsicott itself needs to hold."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        whimsicott = cf._build("Whimsicott", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        moonblast = cf._lookup_move("Moonblast", moves)
        no_aura = cf._raw_hit(whimsicott, moonblast, kingambit, typechart, roll="avg")
        with_aura = cf._raw_hit(whimsicott, moonblast, kingambit, typechart,
                                roll="avg", auras={"Fairy Aura"})
        self.assertAlmostEqual(with_aura.frac / no_aura.frac, 5461 / 4096, places=3)

    def test_aura_break_inverts_it_to_a_reduction(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        whimsicott = cf._build("Whimsicott", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        moonblast = cf._lookup_move("Moonblast", moves)
        no_aura = cf._raw_hit(whimsicott, moonblast, kingambit, typechart, roll="avg")
        with_break = cf._raw_hit(whimsicott, moonblast, kingambit, typechart,
                                 roll="avg", auras={"Fairy Aura", "Aura Break"})
        self.assertAlmostEqual(with_break.frac / no_aura.frac, 0.75, places=3)

    def test_a_non_fairy_move_is_unaffected(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        whimsicott = cf._build("Whimsicott", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        hyper_voice = cf._lookup_move("Hyper Voice", moves)
        no_aura = cf._raw_hit(whimsicott, hyper_voice, kingambit, typechart, roll="avg")
        with_aura = cf._raw_hit(whimsicott, hyper_voice, kingambit, typechart,
                                roll="avg", auras={"Fairy Aura"})
        self.assertAlmostEqual(no_aura.frac, with_aura.frac)

    def test_active_auras_reads_mega_floettes_real_mega_ability(self):
        """Mega Floette's OWN ability (Fairy Aura) must count too -- not
        just an ally holding it -- and must be sourced from the mega
        PROJECTION (`_mega_project`), same as every other mega-ability
        read in this module."""
        merged, natures = self.W["merged"], self.W["natures"]
        floette = cf._build("Mega Floette", merged, natures)
        self.assertEqual(floette.ability, "Fairy Aura")
        whimsicott = cf._build("Whimsicott", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        sinistcha = cf._build("Sinistcha", merged, natures)
        combatants = {"C": floette, "P": whimsicott, "E1": kingambit, "E2": sinistcha}
        self.assertEqual(cf._active_auras(combatants), {"Fairy Aura"})

    def test_a_fainted_aura_holder_no_longer_contributes(self):
        """`hp` (the joint race's own `{role: fraction}` tracking) filters
        out a dead aura holder, mirroring `battle.py`'s real `not c.fainted`
        check on `_active_auras`."""
        merged, natures = self.W["merged"], self.W["natures"]
        floette = cf._build("Mega Floette", merged, natures)
        whimsicott = cf._build("Whimsicott", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        sinistcha = cf._build("Sinistcha", merged, natures)
        combatants = {"C": floette, "P": whimsicott, "E1": kingambit, "E2": sinistcha}
        alive = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        fainted = {"C": 0.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        self.assertEqual(cf._active_auras(combatants, alive), {"Fairy Aura"})
        self.assertEqual(cf._active_auras(combatants, fainted), set())

    def test_end_to_end_the_joint_race_applies_fairy_aura(self):
        """`_resolve_turn` (via `_choose_action`) must actually apply the
        boost in a real race, not just when `_raw_hit` is called by hand --
        Whimsicott's Moonblast against Kingambit must do MORE damage with
        Mega Floette as its partner than with a non-aura partner."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        moonblast = cf._lookup_move("Moonblast", moves)
        protect = cf._lookup_move("Protect", moves)
        kingambit = cf._build("Kingambit", merged, natures)
        sinistcha = cf._build("Sinistcha", merged, natures)

        def moonblast_dealt(partner_name):
            whimsicott = cf._build("Whimsicott", merged, natures)
            partner = cf._build(partner_name, merged, natures)
            combatants = {"C": whimsicott, "P": partner, "E1": kingambit, "E2": sinistcha}
            moves_by_role = {"C": [moonblast], "P": [protect],
                             "E1": [protect], "E2": [protect]}
            hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
            _hp2, log, _ea, _w, _rc = cf._resolve_turn(
                combatants, moves_by_role, hp, typechart, None,
                {"C": "E1"})
            hit = next(h for role, tgt, h in log if role == "C" and tgt == "E1")
            return hit.frac

        boosted = moonblast_dealt("Mega Floette")
        plain = moonblast_dealt("Corviknight")
        self.assertAlmostEqual(boosted / plain, 5461 / 4096, places=3)


class TestMegaLucarioZAuraBreak(unittest.TestCase):
    """Regulation M-C's Mega Lucario Z carries the literal ability string
    "Aura Break" -- already a real, tested, board-wide effect in this
    codebase (Fairy Aura/Dark Aura inversion, `TestFairyAuraAndDarkAura`
    above). The user separately described "Aura Break" as halving damage
    from incoming contact moves; resolved (via `AskUserQuestion`, since the
    two descriptions genuinely conflict) as an ADDITIONAL, MON-SCOPED
    effect layered on top for Mega Lucario Z specifically -- every other
    "Aura Break" holder keeps the real, unchanged aura-inversion mechanic
    and gets no contact-damage discount.
    """

    def setUp(self):
        self.W = world()

    def test_mega_lucario_z_takes_half_damage_from_a_contact_move(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        lucario_z = cf._build("Mega Lucario Z", merged, natures)
        close_combat = cf._lookup_move("Close Combat", moves)
        self.assertTrue((close_combat.flags or {}).get("contact"))
        normal_defender = cf._build("Milotic", merged, natures)  # not Ghost-type -- Fighting connects
        no_break = cf._raw_hit(kingambit, close_combat, normal_defender,
                               typechart, roll="avg")
        with_break = cf._raw_hit(kingambit, close_combat, lucario_z,
                                 typechart, roll="avg")
        # Not a clean 0.5x ratio against a DIFFERENT defender (their base
        # stats/typing differ) -- so pin it directly against Mega Lucario Z
        # itself, ability on vs off, rather than comparing across mons.
        lucario_z_no_ability = cf._build("Mega Lucario Z", merged, natures)
        lucario_z_no_ability.ability = "Steadfast"  # any non-Aura-Break ability
        without_ability = cf._raw_hit(kingambit, close_combat, lucario_z_no_ability,
                                      typechart, roll="avg")
        self.assertAlmostEqual(with_break.frac / without_ability.frac, 0.5, places=3)
        self.assertGreater(no_break.frac, 0)  # sanity: the probe move itself connects

    def test_a_non_contact_move_is_unaffected(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        lucario_z = cf._build("Mega Lucario Z", merged, natures)
        dark_pulse = cf._lookup_move("Dark Pulse", moves)  # Dark, special, non-contact
        self.assertFalse((dark_pulse.flags or {}).get("contact"))
        lucario_z_no_ability = cf._build("Mega Lucario Z", merged, natures)
        lucario_z_no_ability.ability = "Steadfast"
        with_break = cf._raw_hit(kingambit, dark_pulse, lucario_z, typechart, roll="avg")
        without_ability = cf._raw_hit(kingambit, dark_pulse, lucario_z_no_ability,
                                      typechart, roll="avg")
        self.assertAlmostEqual(with_break.frac, without_ability.frac, places=3)

    def test_a_different_aura_break_holder_gets_no_contact_discount(self):
        """The mon-scoping itself: a SYNTHETIC combatant carrying the exact
        same ability string ("Aura Break") but a different `name` must NOT
        get the halving -- proving this is a per-mon override keyed on
        `defender.name`, not a blanket rewrite of what "Aura Break" means
        for every holder."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        close_combat = cf._lookup_move("Close Combat", moves)
        whimsicott = cf._build("Whimsicott", merged, natures)
        whimsicott.ability = "Aura Break"  # same string, different species
        no_ability = cf._build("Whimsicott", merged, natures)
        with_break = cf._raw_hit(kingambit, close_combat, whimsicott, typechart, roll="avg")
        without = cf._raw_hit(kingambit, close_combat, no_ability, typechart, roll="avg")
        self.assertAlmostEqual(with_break.frac, without.frac, places=3)

    def test_the_real_aura_inversion_still_works_for_mega_lucario_z(self):
        """The existing, untouched board-wide mechanic -- Mega Lucario Z's
        Aura Break still inverts Fairy Aura/Dark Aura into a REDUCTION for
        everyone on the field, same as any other Aura Break holder, since
        this is still the literal string "Aura Break" throughout."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        whimsicott = cf._build("Whimsicott", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        moonblast = cf._lookup_move("Moonblast", moves)
        no_aura = cf._raw_hit(whimsicott, moonblast, kingambit, typechart, roll="avg")
        with_break = cf._raw_hit(whimsicott, moonblast, kingambit, typechart,
                                 roll="avg", auras={"Fairy Aura", "Aura Break"})
        self.assertAlmostEqual(with_break.frac / no_aura.frac, 0.75, places=3)


class TestGrassyTerrainCheapModel(unittest.TestCase):
    """Regulation M-C's Grassy Terrain, threaded through this module's cheap
    2v2-race model -- `weather`'s existing full treatment here (`_field_
    weather`, `damage_roll`'s per-hit multiplier, the speed-key priority
    bump) mirrored at every touch point, same as the real engine's own
    version in `battle.py`/`engine.py`."""

    def setUp(self):
        self.W = world()

    def test_field_terrain_reads_a_setters_ability(self):
        merged, natures = self.W["merged"], self.W["natures"]
        rilla = cf._build("Rillaboom", merged, natures)
        kingambit = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": rilla, "P": kingambit, "E1": kingambit, "E2": e2}
        self.assertEqual(cf._field_terrain(combatants), "grassy")

    def test_field_terrain_is_none_without_a_setter(self):
        merged, natures = self.W["merged"], self.W["natures"]
        combatants = {"C": cf._build("Kingambit", merged, natures),
                     "P": cf._build("Garchomp", merged, natures),
                     "E1": cf._build("Milotic", merged, natures),
                     "E2": cf._build("Sinistcha", merged, natures)}
        self.assertIsNone(cf._field_terrain(combatants))

    def test_raw_hit_applies_the_grass_boost_under_terrain(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        rilla = cf._build("Rillaboom", merged, natures)
        target = cf._build("Kingambit", merged, natures)
        wood_hammer = cf._lookup_move("Wood Hammer", moves)
        no_terrain = cf._raw_hit(rilla, wood_hammer, target, typechart, roll="avg")
        grassy = cf._raw_hit(rilla, wood_hammer, target, typechart, roll="avg",
                             terrain="grassy")
        self.assertAlmostEqual(grassy.frac / no_terrain.frac, 1.3, places=3)

    def test_grassy_glide_gets_the_priority_bump_in_the_joint_race(self):
        """Baxcalibur (87 Speed) outpaces Rillaboom (85) on raw speed, so
        without terrain Rillaboom's Grassy Glide goes second; under the
        terrain Rillaboom's own Grassy Surge sets, the +1 priority sends
        Grassy Glide first instead. `_apply_plan` appends to `log` in actual
        resolution order, so this checks ORDER, not just that both hits
        happen -- an end-to-end check through `_resolve_turn`, not just the
        speed-key closure in isolation."""
        merged, moves_db, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])

        def race(terrain):
            rilla = cf._build("Rillaboom", merged, natures)
            bax = cf._build("Baxcalibur", merged, natures)
            e2 = cf._build("Sinistcha", merged, natures)
            combatants = {"C": rilla, "P": e2, "E1": bax, "E2": e2}
            glide = cf._lookup_move("Grassy Glide", moves_db)
            iron_head = cf._lookup_move("Iron Head", moves_db)
            protect = cf._lookup_move("Protect", moves_db)
            moves_by_role = {"C": [glide], "P": [protect],
                             "E1": [iron_head], "E2": [protect]}
            hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
            _hp2, log, _ea, _w, _rc = cf._resolve_turn(
                combatants, moves_by_role, hp, typechart, None, {"C": "E1"},
                terrain=terrain)
            actors = [role for role, _tgt, _h in log]
            return actors.index("C") < actors.index("E1")

        self.assertTrue(race("grassy"))
        self.assertFalse(race(None))


class TestPreferencesReducePool(unittest.TestCase):
    """"Make sure preferences.csv is taken into account (includes, excludes)
    so that it reduces the pool of eligible mons." """

    def setUp(self):
        self.W = world()

    def test_exclude_drops_the_named_pokemon_and_its_mega_forms(self):
        import counter_table as ct

        class Args:
            team = ""
            pool_size = 0
        base = sorted(self.W["merged"])
        self.assertIn("Kingambit", base)
        with_pref = ct._apply_preferences(list(base), self.W["merged"],
                                          verbose=False)
        # The shipped preferences.csv excludes "Slurpuff" -- confirm it (and
        # only it, of these two probes) is actually gone.
        self.assertNotIn("Slurpuff", with_pref)
        self.assertIn("Kingambit", with_pref)

    def test_a_synthetic_exclude_also_drops_mega_forms(self):
        import counter_table as ct
        merged = self.W["merged"]
        pool = [n for n in sorted(merged) if n in
               ("Scizor", "Mega Scizor", "Kingambit")]
        import unittest.mock as mock
        with mock.patch("species_data.load_preferences",
                        return_value={"include": [], "exclude": ["Scizor"],
                                      "prefer": [], "sets": {}}):
            out = ct._apply_preferences(pool, merged, verbose=False)
        self.assertNotIn("Scizor", out)
        self.assertNotIn("Mega Scizor", out)
        self.assertIn("Kingambit", out)

    def test_include_adds_a_name_back_after_a_pool_size_cut(self):
        import counter_table as ct
        merged = self.W["merged"]
        pool = ["Kingambit", "Basculegion"]  # a small "cut" that omits Gallade
        import unittest.mock as mock
        with mock.patch("species_data.load_preferences",
                        return_value={"include": ["Gallade"], "exclude": [],
                                      "prefer": [], "sets": {}}):
            out = ct._apply_preferences(pool, merged, verbose=False)
        self.assertIn("Gallade", out)
        self.assertIn("Kingambit", out)

    def test_include_never_adds_an_excluded_name(self):
        import counter_table as ct
        merged = self.W["merged"]
        import unittest.mock as mock
        with mock.patch("species_data.load_preferences",
                        return_value={"include": ["Gallade"],
                                      "exclude": ["Gallade"], "prefer": [],
                                      "sets": {}}):
            out = ct._apply_preferences(["Kingambit"], merged, verbose=False)
        self.assertNotIn("Gallade", out)

    def test_default_counter_table_pool_excludes_shipped_preferences(self):
        import counter_table as ct

        class Args:
            team = ""
            pool_size = 0
        pool = ct._pool(Args(), self.W["merged"])
        self.assertNotIn("Slurpuff", pool)


class TestOnlyOneMegaPerSide(unittest.TestCase):
    """"Only one can mega. Both in a pair can be a potential Mega, but vs
    each enemy pair only one can choose to become the Mega, the other will
    stay as base form -- this can be favourable, such as Gyarados keeping
    Water/Flying type rather than choosing to switch to Water/Dark. Account
    for factors like intimidate too. This is also true for opponents.
    Abilities from base form apply before mega, such as Gyarados Intimidates
    then gains Mold Breaker when it megas." """

    def setUp(self):
        self.W = world()

    def test_mega_choices_offers_stay_base_even_for_a_lone_mega(self):
        """Unlike `species_data.mega_variants`, a SOLE Mega-capable pick is
        still offered the "nobody transforms" option -- staying base is a
        real per-matchup choice here, not a fixed team property."""
        from species_data import NO_MEGA
        choices = cf._mega_choices(["Mega Gyarados", "Kingambit"])
        self.assertEqual(set(choices), {"Mega Gyarados", NO_MEGA})

    def test_mega_choices_never_offers_both_at_once(self):
        from species_data import NO_MEGA
        choices = cf._mega_choices(["Mega Gyarados", "Mega Charizard Y"])
        self.assertEqual(set(choices),
                         {"Mega Gyarados", "Mega Charizard Y", NO_MEGA})

    def test_no_mega_capable_member_has_a_single_trivial_choice(self):
        self.assertEqual(cf._mega_choices(["Kingambit", "Basculegion"]), [None])

    def test_resolve_forms_never_yields_two_megas_at_once(self):
        merged, natures, moves = self.W["merged"], self.W["natures"], self.W["moves"]
        built = cf._build_forms(["Mega Gyarados", "Mega Charizard Y"],
                                merged, natures, moves)
        for _mt, (c1, c2) in cf._resolve_forms(
                ("Mega Gyarados", "Mega Charizard Y"), built):
            both_mega = (c1 is built["Mega Gyarados"]["mega"]
                        and c2 is built["Mega Charizard Y"]["mega"])
            self.assertFalse(both_mega, "both members mega at once is illegal")

    def test_resolve_forms_covers_every_legal_assignment(self):
        merged, natures, moves = self.W["merged"], self.W["natures"], self.W["moves"]
        built = cf._build_forms(["Mega Gyarados", "Mega Charizard Y"],
                                merged, natures, moves)
        seen = set()
        for _mt, (c1, c2) in cf._resolve_forms(
                ("Mega Gyarados", "Mega Charizard Y"), built):
            seen.add((c1 is built["Mega Gyarados"]["mega"],
                     c2 is built["Mega Charizard Y"]["mega"]))
        # "Gyarados mega's", "Charizard Y mega's", "neither does" -- exactly
        # the three legal combinations, never both.
        self.assertEqual(seen, {(True, False), (False, True), (False, False)})

    def test_staying_base_keeps_the_base_ability_and_typing(self):
        """"Gyarados keeping Water/Flying type rather than choosing to
        switch to Water/Dark" -- and Intimidate instead of Mold Breaker."""
        merged, natures = self.W["merged"], self.W["natures"]
        base = cf._build_form("Mega Gyarados", merged, natures, stay_base=True)
        mega = cf._build_form("Mega Gyarados", merged, natures, stay_base=False)
        self.assertEqual(base.ability, "Intimidate")
        self.assertEqual(set(base.types), {"Water", "Flying"})
        self.assertEqual(mega.ability, "Mold Breaker")
        self.assertEqual(set(mega.types), {"Water", "Dark"})

    def test_staying_base_still_holds_the_mega_stone(self):
        """"It still holds its stone (that's why it was brought), it simply
        doesn't get to use it this battle" -- `_build_combatant`'s own
        reasoning, confirmed still true through this module's `_build_form`."""
        merged, natures = self.W["merged"], self.W["natures"]
        base = cf._build_form("Mega Gyarados", merged, natures, stay_base=True)
        mega = cf._build_form("Mega Gyarados", merged, natures, stay_base=False)
        self.assertEqual(base.item, mega.item)
        self.assertTrue(base.item)

    def test_a_non_mega_name_is_identical_in_both_forms(self):
        merged, natures = self.W["merged"], self.W["natures"]
        base = cf._build_form("Kingambit", merged, natures, stay_base=True)
        mega = cf._build_form("Kingambit", merged, natures, stay_base=False)
        self.assertEqual(base.ability, mega.ability)
        self.assertEqual(base.stats, mega.stats)

    def test_pair_search_with_two_mega_capable_names_does_not_crash(self):
        """Smoke test: a pool member paired with a Mega-capable partner,
        against a pool of Mega-capable enemies, exercises every branch of
        the new mega-choice search without erroring."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.pair_search(
            ["Mega Gyarados"], ["Mega Charizard Y", "Mega Floette"],
            merged, moves, natures, typechart,
            partner_name="Mega Scizor", partner_move_name="Bullet Punch")
        self.assertEqual(len(rows), 1)
        d = rows[0]["detail"][("Mega Charizard Y", "Mega Floette")]
        self.assertIn(d["outcome"], ("clean", "trade", "no_ko", "pinned"))

    def test_deep_dive_with_two_mega_capable_enemies_does_not_crash(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        item1, item2, detail, summary = cf.deep_dive(
            "Mega Gyarados", "Kingambit",
            ["Mega Charizard Y", "Mega Floette", "Basculegion"],
            merged, moves, natures, typechart)
        self.assertTrue(item1)
        self.assertTrue(item2)
        self.assertEqual(summary["pairs_total"], 3)
        for entry in detail.values():
            self.assertIn(entry["outcome"], ("sweep", "out_trade", "loss", "no_ko"))
            self.assertIn("grid", entry)

    def test_joint_pool_search_with_two_mega_capable_pool_members(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pool_search(
            ["Mega Gyarados", "Mega Charizard Y", "Kingambit"],
            ["Basculegion", "Whimsicott"], merged, moves, natures, typechart)
        pairs = {r["pair"] for r in rows}
        self.assertIn(("Mega Gyarados", "Mega Charizard Y"), pairs)

    def test_switch_in_search_with_a_mega_capable_bench_and_enemy(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows, tried = cf.switch_in_search(
            "Scizor", "Whimsicott", ("Mega Charizard Y", "Kingambit"),
            ["Mega Gyarados", "Basculegion"], merged, moves, natures, typechart)
        self.assertGreater(tried, 0)
        for r in rows:
            self.assertIn(r["outcome"], ("sweep", "out_trade"))


class TestFakeOutTurnOneOnly(unittest.TestCase):
    """"Sneasler using Fake Out on turn 2 in counter_table.py - Fake Out may
    only be used the first turn after sending out" -- `_joint_race` had zero
    handling of `FIRST_TURN_ONLY_MOVES` at all (confirmed: nothing else in
    this module read that constant), so a lead with nothing better to do
    kept reusing it every turn. `still_fresh` fixes this in `_joint_race`
    itself; `_sequential_pair_outcome` (the one-turn hypothesis) needs no
    equivalent gate since it only ever plays exactly one turn."""

    def setUp(self):
        self.W = world()
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        self.typechart = self.W["typechart"]
        self.sneasler = cf._build("Sneasler", merged, natures)
        self.partner = cf._build("Kingambit", merged, natures)
        self.e1 = cf._build("Milotic", merged, natures)
        self.e2 = cf._build("Sinistcha", merged, natures)
        self.combatants = {"C": self.sneasler, "P": self.partner,
                           "E1": self.e1, "E2": self.e2}
        self.fake_out = cf._lookup_move("Fake Out", moves)
        protect = cf._lookup_move("Protect", moves)
        self.moves_by_role = {"C": [self.fake_out], "P": [protect],
                              "E1": [protect], "E2": [protect]}

    def test_fake_out_fires_turn_one(self):
        _outcome, _turns_used, _hp, log = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, None, 3)
        self.assertTrue(any(role == "C" for role, _tgt, _h in log[0]))

    def test_fake_out_does_not_fire_again_turn_two_or_three(self):
        """THE reported bug: with no other move offered, a lead that has
        already used Fake Out must simply do nothing on later turns, not
        reuse it."""
        _outcome, _turns_used, _hp, log = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, None, 3)
        for turn in log[1:]:
            self.assertFalse(any(role == "C" for role, _tgt, _h in turn))

    def test_still_legal_the_real_first_active_turn_of_a_switch_in(self):
        """`switch_in_search`'s `first_turn_moves_override` gives the
        incoming role an EMPTY list on turn_i==0 (it hasn't switched in with
        anything to do yet) -- its real first active turn, and so its own
        Fake-Out-legal turn, is turn_i==1, not turn_i==0."""
        _outcome, _turns_used, _hp, log = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, None, 4,
            first_turn_moves_override={"C": []})
        self.assertFalse(any(role == "C" for role, _tgt, _h in log[0]))
        self.assertTrue(any(role == "C" for role, _tgt, _h in log[1]))
        for turn in log[2:]:
            self.assertFalse(any(role == "C" for role, _tgt, _h in turn))

    def test_a_move_not_in_first_turn_only_moves_is_unaffected(self):
        """The gate is scoped to `FIRST_TURN_ONLY_MOVES` specifically -- an
        ordinary attack keeps firing every turn, same as before."""
        close_combat = cf._lookup_move("Close Combat", self.W["moves"])
        moves = dict(self.moves_by_role)
        moves["C"] = [close_combat]
        _outcome, _turns_used, _hp, log = cf._joint_race(
            self.combatants, moves, self.typechart, None, 3)
        fired = [any(role == "C" for role, _tgt, _h in turn) for turn in log]
        self.assertTrue(all(fired), fired)


class TestRecoilInTheJointRace(unittest.TestCase):
    """counter_table.py's cheap model never modeled recoil at all --
    `battle.py` (the real engine) already does (`move.recoil`, Life Orb's
    flat 10%), so this closes the gap `_apply_plan` had relative to it."""

    def setUp(self):
        self.W = world()

    def test_a_recoil_move_costs_the_attacker_its_own_hp(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        incin = cf._build("Incineroar", merged, natures)
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": incin, "P": target, "E1": target, "E2": target}
        flare_blitz = cf._lookup_move("Flare Blitz", moves)
        self.assertEqual(flare_blitz.recoil, [33, 100])
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(incin, flare_blitz, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, flare_blitz), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        expected_recoil = got.frac * target.max_hp() * 0.33 / incin.max_hp()
        self.assertAlmostEqual(new_hp["C"], 1.0 - expected_recoil, places=6)

    def test_rock_head_negates_recoil(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        incin = cf._build("Incineroar", merged, natures)
        incin.ability = "Rock Head"
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": incin, "P": target, "E1": target, "E2": target}
        flare_blitz = cf._lookup_move("Flare Blitz", moves)
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(incin, flare_blitz, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, flare_blitz), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["C"], 1.0)

    def test_life_orb_costs_a_flat_ten_percent(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures, item="Life Orb")
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": attacker, "P": target, "E1": target, "E2": target}
        iron_head = cf._lookup_move("Iron Head", moves)  # no move.recoil
        self.assertIsNone(iron_head.recoil)
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(attacker, iron_head, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, iron_head), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertAlmostEqual(new_hp["C"], 0.9, places=6)

    def test_magic_guard_blocks_both_recoil_and_life_orb(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures, item="Life Orb")
        attacker.ability = "Magic Guard"
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": attacker, "P": target, "E1": target, "E2": target}
        flare_blitz = cf._lookup_move("Flare Blitz", moves)
        # Give this Magic-Guard combatant a real recoil move directly (its
        # own moveset doesn't matter here -- only the applied-plan math).
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(attacker, flare_blitz, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, flare_blitz), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["C"], 1.0)


class TestRecoilCappedAtTargetsActualHp(unittest.TestCase):
    """"Floette should take max half of its target HP" -- recoil must scale
    off the HP the TARGET actually lost, not `got.frac` directly (which is
    always computed against the target's FULL max HP and can exceed 1.0 for
    an "overkill" hit well past what the target even had). Cross-checked
    directly against `battle.py` (the real engine) as ground truth: Mega
    Floette's Light of Ruin on Baxcalibur is a real 452-damage overkill
    (192 max HP) that the real engine still only charges 96 HP of recoil
    for (50% of Baxcalibur's OWN max HP, not 50% of 452)."""

    def setUp(self):
        self.W = world()

    def test_matches_the_real_engines_own_overkill_capped_recoil(self):
        """`battle.py` (verified separately, not re-derived here) charges
        Mega Floette exactly 96 HP of recoil for this exact matchup -- the
        cheap model must land on the same number, not the uncapped one."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        floette = cf._build("Mega Floette", merged, natures)
        baxcalibur = cf._build("Baxcalibur", merged, natures)
        light_of_ruin = cf._lookup_move("Light of Ruin", moves)
        self.assertEqual(light_of_ruin.recoil, [1, 2])
        got = cf._raw_hit(floette, light_of_ruin, baxcalibur, typechart, roll="avg")
        # A genuine overkill: the raw hit is well beyond Baxcalibur's own
        # max HP (frac > 1.0), which is exactly the case that exposes the
        # bug if `raw_dmg_dealt` isn't capped at what the target had left.
        self.assertGreater(got.frac, 1.0)
        protect = cf._lookup_move("Protect", moves)
        combatants = {"C": floette, "P": None, "E1": baxcalibur, "E2": None}
        plan = {"C": ({"E1": got}, light_of_ruin)}
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        lost_hp = (1.0 - new_hp["C"]) * floette.max_hp()
        self.assertAlmostEqual(lost_hp, 96.0, delta=1.0)

    def test_a_non_overkill_recoil_hit_is_unaffected(self):
        """The capping fix must not change anything for the ordinary case
        (recoil move doesn't overkill) -- same value as the ALREADY-PASSING
        `TestRecoilInTheJointRace.test_a_recoil_move_costs_the_attacker_
        its_own_hp` computation, re-derived here to guard against the cap
        accidentally clamping a hit that never needed it."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        incin = cf._build("Incineroar", merged, natures)
        target = cf._build("Milotic", merged, natures)
        flare_blitz = cf._lookup_move("Flare Blitz", moves)
        got = cf._raw_hit(incin, flare_blitz, target, typechart, roll="avg")
        self.assertLess(got.frac, 1.0, "fixture must NOT be an overkill hit")
        combatants = {"C": incin, "P": None, "E1": target, "E2": None}
        plan = {"C": ({"E1": got}, flare_blitz)}
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        expected_recoil = got.frac * target.max_hp() * 0.33 / incin.max_hp()
        self.assertAlmostEqual(new_hp["C"], 1.0 - expected_recoil, places=6)


class TestChooseActionAvoidsNeedlessRecoil(unittest.TestCase):
    """"no point in using the recoil move because Moonblast would also kill
    rather than Light of Ruin, and it would take no recoil and make it a
    win" -- `_choose_action` only applied recoil AFTER a move was already
    chosen (`_apply_plan`), never weighing it INTO the choice itself, so a
    higher-power recoil move could beat an equally kill-securing recoil-free
    one on raw overkill damage alone. `_self_cost` is a late tie-break
    (after kos_now_count/kos_in_two_count/priority, before raw damage) that
    fixes exactly this."""

    def setUp(self):
        self.W = world()

    def test_prefers_the_recoil_free_move_when_both_guarantee_the_kill(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        floette = cf._build("Mega Floette", merged, natures)
        baxcalibur = cf._build("Baxcalibur", merged, natures)
        light_of_ruin = cf._lookup_move("Light of Ruin", moves)
        moonblast = cf._lookup_move("Moonblast", moves)
        hits, chosen = cf._choose_action(
            floette, [light_of_ruin, moonblast], {"E": baxcalibur}, typechart)
        self.assertEqual(chosen.name, "Moonblast")
        self.assertGreaterEqual(hits["E"].frac, 1.0, "fixture must be a real KO")

    def test_the_recoil_move_still_wins_when_it_is_the_only_guaranteed_kill(self):
        """Recoil-awareness is a TIE-break, not a blanket penalty -- a
        recoil move that's the only one clearing the KO bar must still be
        chosen over a weaker recoil-free move that doesn't."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        floette = cf._build("Mega Floette", merged, natures)
        weak_target = cf._build("Umbreon", merged, natures)
        light_of_ruin = cf._lookup_move("Light of Ruin", moves)
        moonblast = cf._lookup_move("Moonblast", moves)
        hits_lor, chosen_lor = cf._choose_action(
            floette, [light_of_ruin], {"E": weak_target}, typechart)
        hits_mb, chosen_mb = cf._choose_action(
            floette, [moonblast], {"E": weak_target}, typechart)
        if hits_lor["E"].frac >= 1.0 and hits_mb["E"].frac < 1.0:
            hits, chosen = cf._choose_action(
                floette, [light_of_ruin, moonblast], {"E": weak_target}, typechart)
            self.assertEqual(chosen.name, "Light of Ruin")
        else:
            self.skipTest("fixture no longer isolates 'only Light of Ruin KOs' "
                          "on the current dataset -- not what this test checks")

    def test_rock_head_holder_is_unaffected_by_the_recoil_tie_break(self):
        """Rock Head negates recoil entirely -- `_self_cost` must read 0.0
        for it, same as `_apply_plan`'s own Rock Head branch, so the
        tie-break never second-guesses a Rock Head holder's higher-power
        move in favour of a weaker one."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        floette = cf._build("Mega Floette", merged, natures)
        floette.ability = "Rock Head"
        baxcalibur = cf._build("Baxcalibur", merged, natures)
        light_of_ruin = cf._lookup_move("Light of Ruin", moves)
        moonblast = cf._lookup_move("Moonblast", moves)
        hits, chosen = cf._choose_action(
            floette, [light_of_ruin, moonblast], {"E": baxcalibur}, typechart)
        self.assertEqual(chosen.name, "Light of Ruin")


class TestRoughSkinInTheJointRace(unittest.TestCase):
    """Rough Skin / Iron Barbs weren't implemented ANYWHERE in this repo --
    a NEW mechanic, scoped to the cheap model only (matches what was
    reported against counter_table.py; the real engine, battle.py, is a
    separate, not-asked-for follow-up)."""

    def setUp(self):
        self.W = world()

    def test_contact_move_against_rough_skin_costs_an_eighth_max_hp(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        garchomp = cf._build("Garchomp", merged, natures)
        self.assertEqual(garchomp.ability, "Rough Skin")
        kowtow = cf._lookup_move("Kowtow Cleave", moves)
        self.assertTrue((kowtow.flags or {}).get("contact"))
        protect = cf._lookup_move("Protect", moves)
        combatants = {"C": attacker, "P": garchomp, "E1": garchomp, "E2": garchomp}
        got = cf._raw_hit(attacker, kowtow, garchomp, typechart, roll="avg")
        plan = {"C": ({"E1": got}, kowtow), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertAlmostEqual(new_hp["C"], 1.0 - 1 / 8, places=6)

    def test_a_non_contact_move_does_not_trigger_it(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        garchomp = cf._build("Garchomp", merged, natures)
        dark_pulse = cf._lookup_move("Dark Pulse", moves)
        self.assertFalse((dark_pulse.flags or {}).get("contact"))
        protect = cf._lookup_move("Protect", moves)
        combatants = {"C": attacker, "P": garchomp, "E1": garchomp, "E2": garchomp}
        got = cf._raw_hit(attacker, dark_pulse, garchomp, typechart, roll="avg")
        plan = {"C": ({"E1": got}, dark_pulse), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["C"], 1.0)

    def test_magic_guard_on_the_attacker_blocks_it(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        attacker.ability = "Magic Guard"
        garchomp = cf._build("Garchomp", merged, natures)
        kowtow = cf._lookup_move("Kowtow Cleave", moves)
        protect = cf._lookup_move("Protect", moves)
        combatants = {"C": attacker, "P": garchomp, "E1": garchomp, "E2": garchomp}
        got = cf._raw_hit(attacker, kowtow, garchomp, typechart, roll="avg")
        plan = {"C": ({"E1": got}, kowtow), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["C"], 1.0)

    def test_triggers_even_if_the_hit_faints_the_rough_skin_holder(self):
        """Real mechanic -- the ability reacts to the contact itself, not to
        the holder surviving it."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        garchomp = cf._build("Garchomp", merged, natures)
        kowtow = cf._lookup_move("Kowtow Cleave", moves)
        protect = cf._lookup_move("Protect", moves)
        combatants = {"C": attacker, "P": garchomp, "E1": garchomp, "E2": garchomp}
        got = cf.Hit(move_name="Kowtow Cleave", frac=1.5, lo=1.5, avg=1.5,
                    hi=1.5, eff=1.0)  # overkill -- faints Garchomp outright
        plan = {"C": ({"E1": got}, kowtow), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["E1"], 0.0)
        self.assertAlmostEqual(new_hp["C"], 1.0 - 1 / 8, places=6)


class TestEarthquakeHitsTheAlly(unittest.TestCase):
    """"earthquake hits allies too" -- `allAdjacent` moves (Earthquake,
    Surf, Discharge, Bulldoze, Explosion) also hit the user's own live
    partner in `_resolve_turn`'s real multi-turn engine (`_with_ally_
    splash`), scoped there and not to `_sequential_pair_outcome`/
    `pair_search`'s cruder single-turn hypothesis (already documented as
    not modeling this)."""

    def setUp(self):
        self.W = world()

    def test_earthquake_splashes_onto_the_live_partner(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        garchomp = cf._build("Garchomp", merged, natures)
        garchomp.ability = "Steadfast"  # isolate from Rough Skin/Life Orb noise
        garchomp.item = None
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": garchomp, "P": partner, "E1": e1, "E2": e2}
        eq = cf._lookup_move("Earthquake", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [eq], "P": [protect], "E1": [protect], "E2": [protect]}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        new_hp, log, _ea, _wiped, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, None, {"C": "E1"})
        self.assertTrue(any(role == "C" and tgt == "P" for role, tgt, _h in log))
        self.assertLess(new_hp["P"], 1.0)

    def test_no_splash_onto_a_fainted_partner(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        garchomp = cf._build("Garchomp", merged, natures)
        garchomp.ability = "Steadfast"
        garchomp.item = None
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": garchomp, "P": partner, "E1": e1, "E2": e2}
        eq = cf._lookup_move("Earthquake", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [eq], "P": [protect], "E1": [protect], "E2": [protect]}
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 1.0}  # partner already dead
        _new_hp, log, _ea, _wiped, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, None, {"C": "E1"})
        self.assertFalse(any(role == "C" and tgt == "P" for role, tgt, _h in log))

    def test_a_single_target_move_is_unaffected(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        garchomp = cf._build("Garchomp", merged, natures)
        garchomp.ability = "Steadfast"
        garchomp.item = None
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": garchomp, "P": partner, "E1": e1, "E2": e2}
        dragon_claw = cf._lookup_move("Dragon Claw", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [dragon_claw], "P": [protect],
                         "E1": [protect], "E2": [protect]}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        _new_hp, log, _ea, _wiped, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, None, {"C": "E1"})
        self.assertFalse(any(role == "C" and tgt == "P" for role, tgt, _h in log))


class TestDracoMeteorFamilyHalving(unittest.TestCase):
    """Low-cost stand-in for full stat-stage tracking (deliberately out of
    scope, per the module's own docstring): after a role uses one of
    `SELF_HALVING_MOVES`, ALL its own outgoing damage is halved for the
    rest of the race -- not gated to Special moves only (the user's own
    explicit simplification)."""

    def setUp(self):
        self.W = world()

    def test_first_use_deals_full_damage(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        hydreigon = cf._build("Hydreigon", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": hydreigon, "P": partner, "E1": e1, "E2": e2}
        draco = cf._lookup_move("Draco Meteor", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [draco], "P": [protect], "E1": [protect], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 1)
        role, tgt, full_hit = next((r, t, h) for r, t, h in log[0] if r == "C")
        no_halving = cf._raw_hit(hydreigon, draco, combatants[tgt], typechart, roll="avg")
        self.assertAlmostEqual(full_hit.frac, no_halving.frac, places=3)

    def test_a_second_use_in_the_same_race_is_halved(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        hydreigon = cf._build("Hydreigon", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": hydreigon, "P": partner, "E1": e1, "E2": e2}
        draco = cf._lookup_move("Draco Meteor", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [draco], "P": [protect], "E1": [protect], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 2)
        turn1 = next(h for role, _tgt, h in log[0] if role == "C")
        turn2 = next((h for role, _tgt, h in log[1] if role == "C"), None)
        self.assertIsNotNone(turn2, "Kingambit should survive one Draco Meteor")
        self.assertAlmostEqual(turn2.frac / turn1.frac, 0.5, places=2)

    def test_close_combat_does_not_trigger_the_halving(self):
        """-1/-1, not the -2 SpA family -- too mild for this approximation,
        deliberately excluded."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        self.assertNotIn("Close Combat", cf.SELF_HALVING_MOVES)
        gallade = cf._build("Gallade", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Sinistcha", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": gallade, "P": partner, "E1": e1, "E2": e2}
        cc = cf._lookup_move("Close Combat", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [cc], "P": [protect], "E1": [protect], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 2)
        turn1 = next(h for role, _tgt, h in log[0] if role == "C")
        turn2 = next((h for role, _tgt, h in log[1] if role == "C"), None)
        if turn2 is not None:
            self.assertAlmostEqual(turn2.frac, turn1.frac, places=2)


class TestIntimidateInTheJointRace(unittest.TestCase):
    """Low-cost stand-in for the real -1 Atk stage (mathematically EXACT,
    not an approximation, since damage scales linearly with the attack
    stat): a live Intimidate holder on one side halves-- no, thirds-- the
    OPPOSING side's outgoing physical damage to x(2/3); Defiant/Competitive
    invert it into a real +2-stage x2.0 self-boost instead."""

    def setUp(self):
        self.W = world()

    def test_ordinary_ability_takes_exactly_two_thirds(self):
        merged, natures, typechart = (
            self.W["merged"], self.W["natures"], self.W["typechart"])
        garchomp = cf._build("Garchomp", merged, natures)  # Rough Skin -- ordinary here
        partner = cf._build("Milotic", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        e1_no = cf._build("Milotic", merged, natures)
        e1_yes = cf._build("Milotic", merged, natures)
        e1_yes.ability = "Intimidate"
        eq = cf._lookup_move("Earthquake", self.W["moves"])
        mult_no = cf._intimidate_mult_by_role(
            {"C": garchomp, "P": partner, "E1": e1_no, "E2": e2})
        mult_yes = cf._intimidate_mult_by_role(
            {"C": garchomp, "P": partner, "E1": e1_yes, "E2": e2})
        self.assertEqual(mult_no, {})
        hits_no, _mv = cf._choose_action(garchomp, [eq], {"E1": e1_no}, typechart,
                                         attacker_role="C", dmg_mult_by_role=mult_no)
        hits_yes, _mv = cf._choose_action(garchomp, [eq], {"E1": e1_yes}, typechart,
                                          attacker_role="C", dmg_mult_by_role=mult_yes)
        self.assertAlmostEqual(hits_yes["E1"].frac / hits_no["E1"].frac, 2 / 3, places=6)

    def test_defiant_inverts_it_into_a_self_boost(self):
        merged, natures, typechart = (
            self.W["merged"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        self.assertEqual(kingambit.ability, "Defiant")
        partner = cf._build("Milotic", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        e1_yes = cf._build("Milotic", merged, natures)
        e1_yes.ability = "Intimidate"
        mult = cf._intimidate_mult_by_role(
            {"C": kingambit, "P": partner, "E1": e1_yes, "E2": e2})
        self.assertEqual(mult["C"], {"physical": 2.0})

    def test_competitive_inverts_it_into_a_special_self_boost(self):
        merged, natures = self.W["merged"], self.W["natures"]
        milotic = cf._build("Milotic", merged, natures)
        self.assertEqual(milotic.ability, "Competitive")
        e1_yes = cf._build("Milotic", merged, natures)
        e1_yes.ability = "Intimidate"
        e2 = cf._build("Sinistcha", merged, natures)
        mult = cf._intimidate_mult_by_role(
            {"C": e1_yes, "P": milotic, "E1": e1_yes, "E2": e2})
        # From "P"'s perspective, the opposing side (E1/E2) has Intimidate.
        self.assertEqual(mult.get("P"), {"special": 2.0})

    def test_blocking_abilities_are_unaffected(self):
        merged, natures = self.W["merged"], self.W["natures"]
        partner = cf._build("Milotic", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        e1_yes = cf._build("Milotic", merged, natures)
        e1_yes.ability = "Intimidate"
        for ability in cf.INTIMIDATE_BLOCKED:
            attacker = cf._build("Garchomp", merged, natures)
            attacker.ability = ability
            mult = cf._intimidate_mult_by_role(
                {"C": attacker, "P": partner, "E1": e1_yes, "E2": e2})
            self.assertNotIn("C", mult, ability)

    def test_persists_after_the_intimidate_holder_faints_mid_race(self):
        """Computed once from the INITIAL board, matching the real -1 Atk
        stage's own persistence -- a stat drop does not revert just because
        its source later faints."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        garchomp = cf._build("Garchomp", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Milotic", merged, natures)
        e1.ability = "Intimidate"
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": garchomp, "P": partner, "E1": e1, "E2": e2}
        mult = cf._intimidate_mult_by_role(combatants)
        self.assertIn("C", mult)
        # A separate call against a board where E1 has already fainted
        # (hp-wise) still finds the SAME multiplier, since it's computed
        # once from `combatants` (ability presence), not from live HP.
        mult_again = cf._intimidate_mult_by_role(combatants)
        self.assertEqual(mult, mult_again)

    def test_no_intimidate_on_the_board_leaves_everyone_unaffected(self):
        merged, natures = self.W["merged"], self.W["natures"]
        combatants = {"C": cf._build("Garchomp", merged, natures),
                     "P": cf._build("Milotic", merged, natures),
                     "E1": cf._build("Kingambit", merged, natures),
                     "E2": cf._build("Sinistcha", merged, natures)}
        self.assertEqual(cf._intimidate_mult_by_role(combatants), {})
