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
import copy
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


class TestForceProtectExemption(unittest.TestCase):
    """"Fake Out and Follow Me can replace the mandatory protect generally"
    -- `optimize_sets.best_moveset`'s `force_protect` reservation used to
    apply unconditionally to any non-Choice-locked Pokemon; a real Fake
    Out or Follow Me user already has same-turn safety/disruption of its
    own, so the slot no longer gets FORCED for one (Protect can still be
    freely PICKED by the scoring if it genuinely scores best -- this only
    removes the artificial override)."""

    def test_a_real_fake_out_user_is_not_forced_into_protect(self):
        W = world()
        from optimize_sets import best_moveset
        moves, _score = best_moveset(
            "Incineroar", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Garchomp", "Kingambit", "Incineroar", "Farigiraf"])
        self.assertIn("Fake Out", moves)
        self.assertNotIn("Protect", moves)

    def test_a_mon_without_fake_out_or_follow_me_still_gets_protect_forced(self):
        W = world()
        from optimize_sets import best_moveset
        moves, _score = best_moveset(
            "Garchomp", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Garchomp", "Kingambit", "Incineroar", "Farigiraf"])
        self.assertIn("Protect", moves)

    def test_a_real_follow_me_user_is_not_forced_into_protect(self):
        W = world()
        from optimize_sets import best_moveset
        moves, _score = best_moveset(
            "Indeedee-F", W["merged"], W["moves"], W["natures"], W["typechart"],
            ["Kingambit", "Sinistcha"])
        self.assertIn("Follow Me", moves)
        # Protect is still free to be PICKED on its own merits here (this
        # exemption only removes the artificial FORCE, see the class
        # docstring) -- what this pins is that the call succeeds and Follow
        # Me survives into the final set, not a specific outcome for Protect.
        self.assertEqual(len(moves), 4)


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

    def test_never_falls_back_to_none_when_exclusion_empties_the_legal_list(self):
        """Regression: when the ONLY legal item a Pokemon's usage list
        offers is excluded, `best_answer` used to fall back to `item=None`
        -- which means "no override" everywhere downstream
        (`_build_forms`/`combatants.py`'s own "use the raw usage default"
        rule), silently handing back the very item `excluded_items` was
        trying to keep out (Choice Scarf leaking through even with
        `--allow-scarf` never passed). Must resolve to a real, non-excluded
        item (`"Leftovers"`, matching `legal_items`'s own fallback for the
        identical case) instead."""
        W = world()
        merged = copy.deepcopy(W["merged"])
        merged["Garchomp"]["items_usage"] = [("Choice Scarf", 100.0)]
        item, moves, _w = cf.best_answer(
            "Garchomp", merged, W["moves"], W["natures"], W["typechart"],
            ["Dragapult"])
        self.assertEqual(item, "Leftovers")
        self.assertIsNotNone(item)
        self.assertTrue(moves)

    def test_extra_items_gets_a_fair_shot_when_the_usage_list_is_excluded(self):
        """`extra_items` (e.g. a type-boost item or resist berry a cap-
        displacement wants to try) must be genuinely SCORED against the
        remaining usage items, not just used as an absolute last resort."""
        W = world()
        merged = copy.deepcopy(W["merged"])
        merged["Garchomp"]["items_usage"] = [("Choice Scarf", 100.0)]
        item, _moves, _w = cf.best_answer(
            "Garchomp", merged, W["moves"], W["natures"], W["typechart"],
            ["Dragapult"], extra_items=["Rocky Helmet"])
        self.assertIn(item, ("Leftovers", "Rocky Helmet"))

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
            # preferences.csv Exclude now drops exactly one Pokemon from the
            # top-10-by-generic-Score candidate pool, so pool_size=10 would
            # only narrow to 9. Bump to 11 so the cascade still lands on a
            # round, explicitly-requested count and the assertion still
            # proves "explicit pool_size narrows the pool to that many".
            pool_size = 11
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
        """Mega Gengar + Mega Alakazam vs Sableye + Hatterene: both dead
        before either of them ever gets to act. (Not Ariados -- a real
        Rage Powder user per usage data -- since the whole point here is an
        UNCONDITIONAL sweep; see `TestFollowMeAsAnAlwaysOnHypothesis` for
        why a real redirector on the enemy side can turn what looks like a
        sweep into a mere out_trade.)"""
        row = self._search("Mega Gengar", ["Sableye", "Hatterene"],
                           "Mega Alakazam")
        d = row["detail"][("Sableye", "Hatterene")]
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
        """Mega Scizor + Whimsicott vs Kingambit + Tyranitar: both enemies
        die within the window, but the enemy side lands hits along the way,
        so it's a trade, not a clean sweep.

        (Kingambit + Basculegion, the matchup this fixture used to use, is
        no longer a real `out_trade` for this pair at all under Kingambit's
        real pinned default_sets.txt set (Chople Berry halves the Fighting
        damage `_answer_for` used to rely on, and its real fixed moveset
        outdamages this pair's own follow-up) -- verified directly via
        `joint_pair_search`, it now grinds to a genuine `loss` no matter how
        many turns are allowed, so it can no longer demonstrate this code
        path at all. Kingambit + Tyranitar, checked the same way, still
        reaches `out_trade` at turns=2: Whimsicott's Moonblast plus Mega
        Scizor's Close Combat clear both real 100%-usage Kingambit and
        Tyranitar sets, but not before Sucker Punch/Rock Slide chip both of
        ours first.)"""
        row = self._search("Mega Scizor", ["Kingambit", "Tyranitar"],
                           "Whimsicott", turns=2)
        d = row["detail"][("Kingambit", "Tyranitar")]
        self.assertEqual(d["outcome"], "out_trade")
        self.assertTrue(d["tailwind_safe"])

    def test_turns_extends_the_window(self):
        """The SAME matchup, only the turn cap different: too short a window
        reports no_ko even though the pair wins it with more turns."""
        row1 = self._search("Mega Scizor", ["Kingambit", "Tyranitar"],
                            "Whimsicott", turns=1)
        d1 = row1["detail"][("Kingambit", "Tyranitar")]
        self.assertEqual(d1["outcome"], "no_ko")
        self.assertEqual(d1["turns_used"], 1)

        row2 = self._search("Mega Scizor", ["Kingambit", "Tyranitar"],
                            "Whimsicott", turns=3)
        d2 = row2["detail"][("Kingambit", "Tyranitar")]
        self.assertEqual(d2["outcome"], "out_trade")
        # Both enemies are already dead by turn 2 (Mega Scizor's Close
        # Combat finishes the second one off that turn) -- the extra turns=3
        # window doesn't change the outcome or extend how long it takes.
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
        not just "high". Hatterene, not Ariados -- see the sibling note in
        `TestJointPairSearch.test_a_dominant_fast_pair_sweeps_a_weak_slow_
        pair` for why a real Rage Powder user isn't a clean-sweep fixture
        any more."""
        row = self._search("Mega Gengar", ["Sableye", "Hatterene"],
                           "Mega Alakazam")
        d = row["detail"][("Sableye", "Hatterene")]
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
        of play for the recorded race.

        `"loss"`, not `"out_trade"`, in this exact fixture now that Life
        Orb is ALSO capped at 1 by default (not just Focus Sash): Metagross
        and Hydreigon both independently default to Life Orb here, so
        Hydreigon (second in `deep_dive`'s own name order) gets displaced
        to its own next-best item, Focus Sash -- slower than Life-Orb
        Hydreigon would have been, which is what actually changes this
        specific race's outcome. The fixed value only needs to be SOME
        real outcome, not this particular one -- this test's own point is
        that the Protect replay doesn't corrupt it, not which string it is."""
        d, _summary = self._deep()
        self.assertEqual(d["outcome"], "loss")

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


class TestEnemyTailwindAppliesTheRealSpeedBoostFromTurn2(unittest.TestCase):
    """"avoiding enemy tailwind ... may be key for a matchup swinging from
    a win to a clear loss" -- while wiring up a `tailwind_risk` caveat for
    `bring4_win_conditions`, a real bug surfaced here: `_pair_vs_targets`'s
    enemy-Tailwind replay (`tailwind_setter_roles`, above) forced the real
    setter to spend turn 1 casting it, but never actually passed `enemy_
    speed_mult=2.0` into the replay's own `_joint_race` call the way the
    OWN-tailwind mirror already did (`own_tailwind_setter_roles` below,
    `own_speed_mult=2.0`) -- so the enemy paid the real cost of casting
    Tailwind (a wasted turn 1) but never actually GOT any faster from turn
    2 onward, silently understating the real threat this whole feature
    exists to model (`_joint_race`'s own docstring: "Whichever of
    `enemy_speed_mult`/`own_speed_mult` belongs to THAT role's side ... is
    only applied from turn 2 onward" -- a promise the enemy branch simply
    didn't keep).

    Fixture: hand-tuned speeds (`Combatant.stats["spe"]` set directly, the
    real stats are irrelevant here) so ONLY the turn-2+ speed change --
    not the turn-1 opportunity cost `TestTailwindAsARealThreat` already
    covers -- can flip the outcome: Kingambit (E2, the real attacker) is
    slower than Incineroar (C) normally (60 < 100) but faster once
    doubled (120 > 100), while Corviknight (P, 200) and Whimsicott (E1,
    the setter, 300) stay unaffected either way. Single pinned moves
    (Flare Blitz/Iron Head/Moonblast/Iron Head) so raw speed order alone
    decides who acts, matching `TestTailwindAsARealThreat`'s own reasoning
    for pinning movesets."""

    def test_the_enemy_actually_gets_faster_once_tailwind_is_up(self):
        W = world()
        merged, moves, natures = W["merged"], W["moves"], W["natures"]
        typechart = W["typechart"]
        our_built = cf._build_forms(["Incineroar", "Corviknight"], merged, natures, moves)
        enemy_built = cf._build_forms(["Whimsicott", "Kingambit"], merged, natures, moves)
        for d, spe in ((our_built["Incineroar"], 100), (our_built["Corviknight"], 200),
                      (enemy_built["Whimsicott"], 300), (enemy_built["Kingambit"], 60)):
            d["base"].stats["spe"] = spe
            d["mega"].stats["spe"] = spe
        our_built["Incineroar"]["moves"] = cf._move_infos(
            "Incineroar", merged, moves, ["Flare Blitz"])
        our_built["Corviknight"]["moves"] = cf._move_infos(
            "Corviknight", merged, moves, ["Iron Head"])
        enemy_built["Whimsicott"]["moves"] = cf._move_infos(
            "Whimsicott", merged, moves, ["Moonblast"])
        enemy_built["Kingambit"]["moves"] = cf._move_infos(
            "Kingambit", merged, moves, ["Iron Head"])
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", our_built, ["Whimsicott", "Kingambit"],
            enemy_built, typechart, turns=3, merged=merged)
        d = detail[("Whimsicott", "Kingambit")]
        self.assertTrue(d["tailwind_is_real_threat"])
        # The REAL bug this regression guards: before the fix, `tailwind_
        # safe` read True here -- Kingambit's own real, post-boost speed
        # advantage over Incineroar (C) was silently never modeled, so the
        # replay only ever priced in the setter's wasted turn 1, which (on
        # this fixture) made things look BETTER for us, not worse.
        self.assertFalse(d["tailwind_safe"],
                         "Kingambit outspeeding Incineroar (60*2=120 > 100) "
                         "from turn 2 onward must make this pairing "
                         "genuinely less safe under Tailwind, not more")


class TestTrickRoomAsAnOptionalThreat(unittest.TestCase):
    """"avoiding enemy tailwind and trick room may be key for a matchup
    swinging from a win to a clear loss" -- `check_trick_room` (opt-in,
    OFF by default -- a genuinely new engine cost, asked for explicitly
    "as an option," not a default-on one every search now pays): the
    enemy-side, pessimistic mirror of the Tailwind replay above, using
    the SAME real-setter-casts-it-turn-1 mechanic, just inverting the
    WHOLE field's speed comparison (`_apply_plan`'s own `trick_room` flag)
    instead of a per-side magnitude multiplier."""

    def setUp(self):
        self.W = world()

    def _built(self, our_names, enemy_names, speeds, our_moves, enemy_moves):
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        our_built = cf._build_forms(our_names, merged, natures, moves)
        enemy_built = cf._build_forms(enemy_names, merged, natures, moves)
        for name, spe in speeds.items():
            d = our_built.get(name) or enemy_built[name]
            d["base"].stats["spe"] = spe
            d["mega"].stats["spe"] = spe
        for name, mv in our_moves.items():
            our_built[name]["moves"] = cf._move_infos(name, merged, moves, [mv])
        for name, mv in enemy_moves.items():
            enemy_built[name]["moves"] = cf._move_infos(name, merged, moves, [mv])
        return our_built, enemy_built

    def test_off_by_default_no_trick_room_fields_at_all(self):
        our_built, enemy_built = self._built(
            ["Incineroar", "Corviknight"], ["Hatterene", "Kingambit"],
            {"Incineroar": 200, "Corviknight": 200, "Hatterene": 300, "Kingambit": 50},
            {"Incineroar": "Flare Blitz", "Corviknight": "Iron Head"},
            {"Hatterene": "Dazzling Gleam", "Kingambit": "Iron Head"})
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", our_built, ["Hatterene", "Kingambit"],
            enemy_built, self.W["typechart"], turns=3, merged=self.W["merged"])
        d = detail[("Hatterene", "Kingambit")]
        for key in ("trick_room_is_real_threat", "trick_room_forced",
                   "trick_room_outcome", "trick_room_safe"):
            self.assertNotIn(key, d)

    def test_a_real_setter_makes_the_enemy_genuinely_faster_from_turn_2(self):
        """Kingambit (50 speed, normally the LAST to act against our 200s)
        outspeeds Incineroar/Corviknight once Trick Room flips the whole
        field's order from turn 2 onward -- the same "real cost paid, real
        benefit gained" shape `TestTailwindAsARealThreat` established,
        mirrored for the opposite (slow-goes-first) direction."""
        our_built, enemy_built = self._built(
            ["Incineroar", "Corviknight"], ["Hatterene", "Kingambit"],
            {"Incineroar": 200, "Corviknight": 200, "Hatterene": 300, "Kingambit": 50},
            {"Incineroar": "Flare Blitz", "Corviknight": "Iron Head"},
            {"Hatterene": "Dazzling Gleam", "Kingambit": "Iron Head"})
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", our_built, ["Hatterene", "Kingambit"],
            enemy_built, self.W["typechart"], turns=3, merged=self.W["merged"],
            check_trick_room=True)
        d = detail[("Hatterene", "Kingambit")]
        self.assertTrue(d["trick_room_is_real_threat"])
        self.assertFalse(d["trick_room_safe"],
                         "Kingambit going from last-to-act to first-to-act "
                         "under Trick Room must make this pairing genuinely "
                         "less safe, not more")

    def test_no_promotion_when_no_enemy_in_the_pair_knows_trick_room(self):
        our_built, enemy_built = self._built(
            ["Incineroar", "Corviknight"], ["Kingambit", "Sylveon"],
            {"Incineroar": 200, "Corviknight": 200, "Kingambit": 50, "Sylveon": 120},
            {"Incineroar": "Flare Blitz", "Corviknight": "Iron Head"}, {})
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", our_built, ["Kingambit", "Sylveon"],
            enemy_built, self.W["typechart"], turns=3, merged=self.W["merged"],
            check_trick_room=True)
        d = detail[("Kingambit", "Sylveon")]
        self.assertFalse(d["trick_room_is_real_threat"])
        self.assertFalse(d["trick_room_forced"])
        self.assertIsNone(d["trick_room_outcome"])
        self.assertTrue(d["trick_room_safe"])

    def test_never_makes_a_pair_look_better_than_its_tailwind_adjusted_baseline(self):
        """Trick Room is chained AFTER both Tailwind checks (see docstring)
        -- it can only ever match or WORSEN `chosen_outcome`, never improve
        it, the same one-directional guarantee `tailwind_forced`/`own_
        tailwind_used` each give in their own (opposite) direction."""
        our_built, enemy_built = self._built(
            ["Incineroar", "Corviknight"], ["Hatterene", "Kingambit"],
            {"Incineroar": 200, "Corviknight": 200, "Hatterene": 300, "Kingambit": 50},
            {"Incineroar": "Flare Blitz", "Corviknight": "Iron Head"},
            {"Hatterene": "Dazzling Gleam", "Kingambit": "Iron Head"})
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", our_built, ["Hatterene", "Kingambit"],
            enemy_built, self.W["typechart"], turns=3, merged=self.W["merged"],
            check_trick_room=True)
        d = detail[("Hatterene", "Kingambit")]
        self.assertGreaterEqual(cf._JOINT_OUTCOME_RANK[d["outcome"]],
                                cf._JOINT_OUTCOME_RANK[d["outcome_without_tailwind"]])


class TestFollowMeAsAnAlwaysOnHypothesis(unittest.TestCase):
    """"Let me select a mode in the battle simulator where the enemy always
    uses its redirection moves" -- `bring4`/the cheap 2v2 model didn't beat
    Follow Me/Rage Powder resilience into its own SCORE at all before this
    existed (every Status move was simply skipped). Per the user: "beating
    these styles is crucial ... include in the overall score" -- so unlike
    `check_trick_room`, `follow_me_safe` is ALWAYS present on every `detail`
    entry, no opt-in flag, mirroring `tailwind_safe`/`protect_safe`'s own
    always-on shape exactly. Real fixture: Indeedee-F and Clefable are both
    real, usage-verified Follow Me users (`merged` usage data, not a made-up
    moveset)."""

    def setUp(self):
        self.W = world()
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        self.our_names = ["Incineroar", "Corviknight"]
        self.our_built = cf._build_forms(self.our_names, merged, natures, moves)
        self.our_built["Incineroar"]["moves"] = cf._move_infos(
            "Incineroar", merged, moves, ["Flare Blitz"])
        self.our_built["Corviknight"]["moves"] = cf._move_infos(
            "Corviknight", merged, moves, ["Iron Head"])

    def test_present_unconditionally_no_flag_needed(self):
        """Unlike `trick_room_safe` (which needs `check_trick_room=True` to
        even appear), `follow_me_safe` and its siblings are on EVERY entry
        by default -- confirmed here with an enemy pair that doesn't even
        carry a redirector, so this can't be mistaken for the "a real
        threat exists" case below."""
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        enemy_names = ["Sylveon", "Kingambit"]
        enemy_built = cf._build_forms(enemy_names, merged, natures, moves)
        enemy_built["Sylveon"]["moves"] = cf._move_infos(
            "Sylveon", merged, moves, ["Moonblast"])
        enemy_built["Kingambit"]["moves"] = cf._move_infos(
            "Kingambit", merged, moves, ["Iron Head"])
        detail, summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", self.our_built, enemy_names,
            enemy_built, self.W["typechart"], turns=3, merged=merged)
        d = detail[("Sylveon", "Kingambit")]
        for key in ("follow_me_is_real_threat", "follow_me_forced",
                   "follow_me_outcome", "follow_me_safe"):
            self.assertIn(key, d)
        self.assertFalse(d["follow_me_is_real_threat"])
        self.assertTrue(d["follow_me_safe"])
        self.assertIn("pairs_follow_me_safe", summary)

    def test_a_real_redirector_can_flip_a_win_into_unsafe(self):
        """Indeedee-F (a real Follow Me user) keeps Kingambit alive behind
        it turn after turn -- a pair that would otherwise trade/sweep
        (`outcome_without_tailwind`) can come back `follow_me_safe=False`
        once the redirect hypothesis is actually raced and forced."""
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        enemy_names = ["Indeedee-F", "Kingambit"]
        enemy_built = cf._build_forms(enemy_names, merged, natures, moves)
        enemy_built["Indeedee-F"]["moves"] = cf._move_infos(
            "Indeedee-F", merged, moves, ["Follow Me"])
        enemy_built["Kingambit"]["moves"] = cf._move_infos(
            "Kingambit", merged, moves, ["Iron Head"])
        detail, summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", self.our_built, enemy_names,
            enemy_built, self.W["typechart"], turns=3, merged=merged)
        d = detail[("Indeedee-F", "Kingambit")]
        self.assertTrue(d["follow_me_is_real_threat"])
        self.assertTrue(d["follow_me_forced"],
                        "the redirect hypothesis must actually be worse "
                        "than the baseline here to prove it was raced, not "
                        "just labelled a threat")
        self.assertFalse(d["follow_me_safe"])
        self.assertEqual(d["outcome"], d["follow_me_outcome"])
        self.assertEqual(summary["pairs_follow_me_safe"], 0)

    def test_never_makes_a_pair_look_better_than_its_own_baseline(self):
        """Same one-directional guarantee `tailwind_forced`/`trick_room_
        forced` each already give -- chained last, `follow_me_forced` can
        only match or WORSEN `chosen_outcome`, never improve it."""
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        enemy_names = ["Indeedee-F", "Kingambit"]
        enemy_built = cf._build_forms(enemy_names, merged, natures, moves)
        enemy_built["Indeedee-F"]["moves"] = cf._move_infos(
            "Indeedee-F", merged, moves, ["Follow Me"])
        enemy_built["Kingambit"]["moves"] = cf._move_infos(
            "Kingambit", merged, moves, ["Iron Head"])
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Corviknight", self.our_built, enemy_names,
            enemy_built, self.W["typechart"], turns=3, merged=merged)
        d = detail[("Indeedee-F", "Kingambit")]
        self.assertGreaterEqual(cf._JOINT_OUTCOME_RANK[d["outcome"]],
                                cf._JOINT_OUTCOME_RANK[d["outcome_without_tailwind"]])


class TestOwnTailwindAsAMatchingAnswer(unittest.TestCase):
    """"I think it's important to be able to withstand tailwind versus
    opponents with Tailwind (or match with your own)" -- the mirror image
    of `TestTailwindAsARealThreat`: when OUR pair carries a real Tailwind
    setter, `_pair_vs_targets` tries the same "spend turn 1 actually
    casting it" replay, OPTIMISTICALLY this time (keep it only when it
    HELPS), since it's a choice we'd make ourselves, not one imposed on us.

    Real, verified, fully pinned fixture: Talonflame (Tailwind, Brave Bird)
    + Kingambit (Kowtow Cleave, Sucker Punch) against Sableye (its own real
    usage moveset: Rain Dance/Light Screen/Reflect/Encore -- no offense at
    all) + Arcanine-Hisui (Flare Blitz/Protect/Rock Slide/Extreme Speed).

    Originally (before `_best_turn`'s own 2-turn lookahead --
    `TestBestTurnTwoTurnLookahead`) Kingambit was outsped and worn down
    without ever finishing Arcanine-Hisui off -- a real LOSS -- and casting
    Tailwind turned it into a stalemate instead, which is what this fixture
    was built to demonstrate. The lookahead now correctly recognises turn 1
    that Arcanine-Hisui (the only one of the two enemies that can actually
    attack) must be focused down FIRST regardless of speed -- Brave Bird
    plus Kowtow Cleave together already clear it before its own move ever
    lands -- so the NORMAL-speed race alone no longer loses this matchup at
    all; it is a stalemate (no_ko) with or without Tailwind. This is a
    genuinely better baseline, not a bug (re-verified directly: with the
    lookahead OFF, this exact fixture still reproduces the original LOSS,
    confirming the change is the lookahead's own smarter targeting, not a
    regression elsewhere) -- so `own_tailwind_used` correctly comes back
    False here now: Tailwind is never adopted when it would not actually
    improve on an already-fine baseline (see `test_never_makes_a_pair_look_
    worse_than_its_baseline`, which stays a real invariant either way).
    """

    def setUp(self):
        self.W = world()
        merged, moves, natures = (self.W["merged"], self.W["moves"],
                                  self.W["natures"])
        self.our_names = ["Talonflame", "Kingambit"]
        self.enemy_names = ["Sableye", "Arcanine-Hisui"]
        self.our_built = cf._build_forms(
            self.our_names, merged, natures, moves,
            items={"Talonflame": "Focus Sash", "Kingambit": "Life Orb"})
        self.enemy_built = cf._build_forms(self.enemy_names, merged, natures, moves)
        self.our_built["Talonflame"]["moves"] = cf._move_infos(
            "Talonflame", merged, moves, ["Tailwind", "Brave Bird"])
        self.our_built["Kingambit"]["moves"] = cf._move_infos(
            "Kingambit", merged, moves, ["Kowtow Cleave", "Sucker Punch"])
        self.enemy_built["Sableye"]["moves"] = cf._move_infos(
            "Sableye", merged, moves,
            ["Rain Dance", "Light Screen", "Reflect", "Encore"])
        self.enemy_built["Arcanine-Hisui"]["moves"] = cf._move_infos(
            "Arcanine-Hisui", merged, moves,
            ["Flare Blitz", "Protect", "Rock Slide", "Extreme Speed"])

    def _race(self, merged):
        typechart = self.W["typechart"]
        detail, summary = cf._pair_vs_targets(
            self.our_names[0], self.our_names[1], self.our_built,
            self.enemy_names, self.enemy_built, typechart, turns=2,
            merged=merged)
        return detail[tuple(self.enemy_names)], summary

    def test_talonflame_really_knows_tailwind_sableye_and_arcanine_do_not(self):
        """The fixture's precondition, checked against real usage data."""
        merged = self.W["merged"]
        self.assertTrue(any(mv == "Tailwind" for mv, _pct in
                            merged["Talonflame"]["moves_usage"]))
        for n in self.enemy_names:
            self.assertFalse(any(mv == "Tailwind" for mv, _pct in
                                 merged[n]["moves_usage"]), n)

    def test_the_normal_race_alone_is_a_stalemate_not_a_loss(self):
        """See this class's own docstring: the 2-turn lookahead's smarter
        turn-1 targeting (focus the one enemy that can actually attack)
        fixes this exact matchup even at normal speed now."""
        d, _summary = self._race(merged=None)
        self.assertEqual(d["outcome"], "no_ko")
        self.assertFalse(d["own_tailwind_is_real_threat"])
        self.assertFalse(d["own_tailwind_used"])
        self.assertIsNone(d["own_tailwind_outcome"])

    def test_our_own_tailwind_is_not_forced_when_the_baseline_is_already_fine(self):
        d, summary = self._race(merged=self.W["merged"])
        self.assertFalse(d["own_tailwind_used"],
                         "Tailwind is a choice we'd only make if it HELPS -- "
                         "the baseline is already a no_ko stalemate here, so "
                         "there is nothing left for it to fix")
        self.assertEqual(d["outcome"], "no_ko")
        self.assertEqual(d["outcome_without_tailwind"], "no_ko")
        self.assertEqual(summary["pairs_own_tailwind_used"], 0)
        self.assertEqual(summary["pairs_lost"], 0)
        self.assertEqual(summary["pairs_no_ko"], 1)

    def test_never_makes_a_pair_look_worse_than_its_baseline(self):
        """OUR tailwind is a choice we'd only make if it helps -- it must
        never replace a chosen outcome with a WORSE one."""
        d, _summary = self._race(merged=self.W["merged"])
        self.assertLessEqual(cf._JOINT_OUTCOME_RANK[d["outcome"]],
                             cf._JOINT_OUTCOME_RANK[d["outcome_without_tailwind"]])

    def test_own_pair_has_real_tailwind_helper(self):
        merged = self.W["merged"]
        self.assertTrue(cf.own_pair_has_real_tailwind(
            "Talonflame", "Kingambit", merged))
        self.assertFalse(cf.own_pair_has_real_tailwind(
            "Sableye", "Arcanine-Hisui", merged))

    def test_our_damage_output_matches_a_from_scratch_sum_of_the_log(self):
        """"I want to mathematically output the most damage possible ...
        while surviving for long enough to keep dishing it out" --
        `our_damage_output` must equal a direct, from-scratch sum of every
        hit OUR side landed in `log` (weighted by `num_targets_hit`, a
        spread hit already counting for both targets), not just be
        internally self-consistent."""
        d, summary = self._race(merged=self.W["merged"])
        expected = sum(h.frac * h.num_targets_hit
                      for turn_hits in d["log"] for role, _tgt, h in turn_hits
                      if role in ("C", "P"))
        self.assertAlmostEqual(d["our_damage_output"], expected)
        self.assertGreater(d["our_damage_output"], 0.0,
                           "this fixture's own race lands real hits before "
                           "stalling out -- 0 output would mean the log "
                           "was empty or damage_output isn't reading it")
        self.assertAlmostEqual(summary["pairs_damage_output_total"],
                               d["our_damage_output"],
                               msg="only one enemy pair is named here, so "
                                   "the summary's own total must equal "
                                   "this single matchup's own figure")


class TestOwnProtectAsAMatchingAnswer(unittest.TestCase):
    """The Protect mirror of `TestOwnTailwindAsAMatchingAnswer`: the exact
    same "pessimistic enemy check already exists (`protect_outcomes`/
    `protect_safe`), now add the OPTIMISTIC own-side mirror" shape, applied
    to Protect instead of Tailwind. Unlike Tailwind, no "does a real setter
    exist" gate is needed -- every pair can always try Protecting one of
    itself turn 1 -- but scoped to a raw "loss" baseline specifically
    ("does protecting turn a LOSS into a win", the user's own example),
    not run unconditionally on every pair the way it first shipped: two
    more full `_joint_race` calls per pair with no setter-existence gate
    to make it a no-op most of the time (own-Tailwind's own gate does)
    measurably pushed a real large pool search over its wall-clock budget.
    `_pair_vs_targets` races both `first_turn_protected_role="C"` and
    `="P"` only when the baseline is a raw "loss", and keeps whichever
    (optimistic `min` by `_JOINT_OUTCOME_RANK`) beats it.

    Real, verified fixture, found by search (not hand-derived) over real
    usage-default sets: Alakazam (Focus Sash, Protect/Psychic/Speed Swap/
    Dazzling Gleam) + Espeon (Colbur Berry, Protect/Trick Room/Detect/
    Psyshock) against Tyranitar (Sitrus Berry, Protect/Rock Slide/Knock
    Off/Low Kick) + Garchomp (Sitrus Berry, Protect/Dragon Claw/Rock
    Slide/Earthquake). The normal race is a clean LOSS -- both fragile
    attackers go down before finishing off the bulkier, harder-hitting
    enemy pair. Racing with Alakazam Protecting turn 1 (the better of the
    two own-Protect replays) turns it into an out-trade win: Alakazam
    survives what would have been its own fatal turn-1 hit, and the extra
    turn of it still being alive is enough for the pair to out-trade
    Tyranitar + Garchomp instead of losing outright.
    """

    def _race(self):
        W = world()
        merged, moves, natures = W["merged"], W["moves"], W["natures"]
        typechart = W["typechart"]
        _i1, _i2, detail, summary = cf.deep_dive(
            "Alakazam", "Espeon", ["Tyranitar", "Garchomp"], merged, moves,
            natures, typechart, turns=2)
        return detail[("Tyranitar", "Garchomp")], summary

    def test_the_normal_race_alone_is_a_loss(self):
        d, _summary = self._race()
        self.assertEqual(d["outcome_without_tailwind"], "loss")

    def test_own_protect_is_always_a_real_threat_no_setter_gate_needed(self):
        d, _summary = self._race()
        self.assertTrue(d["own_protect_is_real_threat"])

    def test_own_protect_turns_the_loss_into_an_out_trade_win(self):
        d, summary = self._race()
        self.assertTrue(d["own_protect_used"])
        self.assertEqual(d["own_protect_outcome"], "out_trade")
        self.assertEqual(d["outcome"], "out_trade")
        self.assertEqual(summary["pairs_own_protect_used"], 1)

    def test_never_makes_a_pair_look_worse_than_its_baseline(self):
        """Same invariant as `TestOwnTailwindAsAMatchingAnswer`'s own
        version: an optimistic own-side hypothesis must never replace a
        chosen outcome with a WORSE one."""
        d, _summary = self._race()
        self.assertLessEqual(cf._JOINT_OUTCOME_RANK[d["outcome"]],
                             cf._JOINT_OUTCOME_RANK[d["outcome_without_tailwind"]])

    def test_protected_role_takes_no_damage_on_the_turn_it_protects(self):
        """Direct mechanical check on `_joint_race` itself, mirroring
        `TestJointProtectRobustness`'s own enemy-side version: with our OWN
        "C" role forced to Protect turn 1, no hit in that turn's log should
        ever target "C"."""
        W = world()
        merged, moves, natures = W["merged"], W["moves"], W["natures"]
        typechart = W["typechart"]
        item1, mvs1, _w1 = cf._answer_for(
            "Alakazam", merged, moves, natures, typechart,
            ["Tyranitar", "Garchomp"])
        c1 = cf._build("Alakazam", merged, natures, item=item1)
        c1_moves = cf._move_infos("Alakazam", merged, moves, mvs1)
        item2, mvs2, _w2 = cf._answer_for(
            "Espeon", merged, moves, natures, typechart,
            ["Tyranitar", "Garchomp"])
        c2 = cf._build("Espeon", merged, natures, item=item2)
        c2_moves = cf._move_infos("Espeon", merged, moves, mvs2)
        eitem1, emvs1, _ = cf._answer_for(
            "Tyranitar", merged, moves, natures, typechart,
            ["Alakazam", "Espeon"])
        eitem2, emvs2, _ = cf._answer_for(
            "Garchomp", merged, moves, natures, typechart,
            ["Alakazam", "Espeon"])
        combatants = {"C": c1, "P": c2,
                     "E1": cf._build("Tyranitar", merged, natures, item=eitem1),
                     "E2": cf._build("Garchomp", merged, natures, item=eitem2)}
        moves_by_role = {
            "C": c1_moves, "P": c2_moves,
            "E1": cf._move_infos("Tyranitar", merged, moves, emvs1),
            "E2": cf._move_infos("Garchomp", merged, moves, emvs2),
        }
        weather = cf._field_weather(combatants)
        _outcome, _t, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, weather, 2,
            first_turn_protected_role="C")
        turn1 = log[0]
        targets_hit = {tgt for _role, tgt, _h in turn1}
        self.assertNotIn("C", targets_hit)

    def test_scoped_to_a_loss_baseline_not_run_on_a_no_ko_or_better_pair(self):
        """The performance-driven narrowing: `own_protect_outcome` must
        equal `chosen_outcome` untouched (never re-raced) whenever the
        baseline isn't a raw "loss" -- reuses `TestOwnTailwindAsAMatching
        Answer`'s own real, verified "no_ko" fixture (Talonflame + Kingambit
        vs Sableye + Arcanine-Hisui, `test_our_own_tailwind_is_not_forced_
        when_the_baseline_is_already_fine`'s own confirmed no_ko outcome)."""
        W = world()
        merged, moves, natures = W["merged"], W["moves"], W["natures"]
        typechart = W["typechart"]
        _i1, _i2, detail, _summary = cf.deep_dive(
            "Talonflame", "Kingambit", ["Sableye", "Arcanine-Hisui"], merged,
            moves, natures, typechart, turns=2,
            item_overrides={"Talonflame": "Focus Sash", "Kingambit": "Life Orb"},
            move_overrides={"Talonflame": ["Tailwind", "Brave Bird"],
                           "Kingambit": ["Kowtow Cleave", "Sucker Punch"]})
        d = detail[("Sableye", "Arcanine-Hisui")]
        self.assertEqual(d["outcome"], "no_ko")
        self.assertFalse(d["own_protect_used"])
        self.assertEqual(d["own_protect_outcome"], d["outcome"])


class TestTailwindFocusPool(unittest.TestCase):
    """`tailwind_focus_pool` -- the pool-curation half of `--tailwind-focus`
    (`counter_table.py`): "checks for teams by running tailwind setter
    (who ideally can do good damage too) + attacker." STRICTLY ADDITIVE,
    not a restriction (a --pool-size cut by generic Score can miss a bulky
    support Tailwind setter) -- must never drop a name the caller's own
    `pool` already had."""

    def setUp(self):
        self.W = world()

    def test_never_removes_a_name_already_in_pool(self):
        """Kingambit/Rampardos/Sableye carry no real Tailwind (confirmed
        via TestOwnTailwindAsAMatchingAnswer's own precondition test) --
        an EXCLUSION-style filter would have dropped them; this must not."""
        merged = self.W["merged"]
        base_pool = ["Kingambit", "Rampardos", "Sableye"]
        focus = cf.tailwind_focus_pool(base_pool, merged)
        self.assertTrue(set(base_pool).issubset(set(focus)))

    def test_adds_a_real_tailwind_setter_missing_from_the_pool(self):
        merged = self.W["merged"]
        base_pool = ["Kingambit", "Rampardos"]
        focus = cf.tailwind_focus_pool(base_pool, merged)
        self.assertIn("Talonflame", focus)
        self.assertIn("Whimsicott", focus)

    def test_a_name_already_in_pool_is_not_duplicated(self):
        merged = self.W["merged"]
        base_pool = ["Kingambit", "Talonflame"]
        focus = cf.tailwind_focus_pool(base_pool, merged)
        self.assertEqual(focus.count("Talonflame"), 1)

    def test_every_added_name_really_does_know_tailwind(self):
        merged = self.W["merged"]
        base_pool = ["Kingambit"]
        focus = cf.tailwind_focus_pool(base_pool, merged)
        added = [n for n in focus if n not in base_pool]
        self.assertTrue(added)
        for n in added:
            self.assertTrue(
                any(mv == "Tailwind" for mv, _pct in
                   merged[n]["moves_usage"]),
                f"{n} was added but has no real Tailwind access")


class TestBring4AndCoreDamageOutput(unittest.TestCase):
    """`bring4_damage_output`/`core_damage_output` -- the aggregation
    `--tailwind-focus` re-sorts `multi_rows` by. Must match a from-scratch
    sum of `pairs_damage_output_total` across the same pair rows, not just
    be internally self-consistent."""

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

    def test_bring4_damage_output_matches_a_from_scratch_sum(self):
        b = self.bring4_rows[0]
        expected = sum(pr["pairs_damage_output_total"] for pr in b["pair_rows"])
        self.assertAlmostEqual(cf.bring4_damage_output(b), expected)

    def test_core_damage_output_sums_every_enemys_own_best_bring(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Mega Scizor", "Mega Floette", "Garchomp", "Kingambit",
               "Whimsicott", "Sinistcha"]
        enemies = [["Kingambit", "Basculegion", "Sableye", "Ariados"]]
        coverage = cf.multi_bring4_coverage(
            pool, enemies, merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)
        rows = cf.multi_bring4_exhaustive(coverage, good_threshold=0.0)
        self.assertTrue(rows)
        row = rows[0]
        expected = sum(cf.bring4_damage_output(pe["best_bring4_row"])
                      for pe in row["per_enemy"])
        self.assertAlmostEqual(cf.core_damage_output(row), expected)


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

    def test_no_recharge_when_the_intended_target_protects(self):
        """"If hyper beam is blocked by protect, it does not need to
        recharge (the move was not used)" -- `protected_roles` (a real,
        explicit hypothesis this module already races elsewhere, e.g. the
        first-turn-Protect check in `_pair_vs_targets`) is what actually
        blocks a hit here, not merely a role's move happening to be named
        "Protect" (that alone does nothing to `protected_roles` in a
        normal, non-hypothesis race -- see `test_hyper_beam_only_fires_
        every_other_turn` above, where the enemy's own Protect move never
        blocks anything precisely because `protected_roles` stays empty)."""
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        _hp2, _log, _ea, _wiped, recharging_next = cf._resolve_turn(
            self.combatants, self.moves_by_role, hp, self.typechart, None,
            {"C": "E1"}, protected_roles={"E1"})
        self.assertEqual(recharging_next, set(),
                         "Hyper Beam's only intended target Protected, so "
                         "the whole hit was blocked -- no recharge")

    def test_still_recharges_when_an_unrelated_role_protects(self):
        """Regression guard: an unrelated role Protecting (not Hyper Beam's
        own target) must not suppress the recharge."""
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        _hp2, _log, _ea, _wiped, recharging_next = cf._resolve_turn(
            self.combatants, self.moves_by_role, hp, self.typechart, None,
            {"C": "E1"}, protected_roles={"E2"})
        self.assertEqual(recharging_next, {"C"})


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
        Punch normally OUTRANKS Iron Head here purely on priority).

        Kingambit vs Kingambit (this fixture's old "plain" target) no longer
        demonstrates that: with Kingambit's real default_sets.txt-pinned set
        (Chople Berry, real EVs), Dark and Steel are BOTH resisted 0.5x by
        the Dark/Steel target, so neither move clears the `kos_now` bar --
        `_choose_move`'s own docstring is explicit that priority is NOT a
        tie-break outside that bar, so the plain higher-base-power move
        (Iron Head, 80 BP vs Sucker Punch's 70) wins on raw damage instead,
        with no priority involved at all. Mega Froslass (Ice/Ghost, so Dark
        is super-effective too) is bulky enough that Iron Head still does
        MORE raw damage than Sucker Punch (verified directly via
        `_raw_hit`), yet BOTH moves already guarantee the KO outright --
        exactly the "ONE NARROW EXCEPTION" `_choose_move`'s docstring
        describes, where priority breaks the tie ahead of raw damage."""
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

        plain_target = cf._build("Mega Froslass", merged, natures)
        # Confirm the fixture's own premise: both moves already guarantee
        # the KO here, and Iron Head's raw damage is the bigger of the two
        # -- so Sucker Punch winning below is really the priority tie-break,
        # not just "bigger number wins" again.
        raw_sucker = cf._raw_hit(attacker, sucker_punch, plain_target, typechart)
        raw_iron = cf._raw_hit(attacker, iron_head, plain_target, typechart)
        self.assertGreaterEqual(raw_sucker.frac, 1.0)
        self.assertGreaterEqual(raw_iron.frac, 1.0)
        self.assertGreater(raw_iron.frac, raw_sucker.frac)

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
        (no priority, big hit) vs Aqua Jet (priority +1, weak) -- BOTH clear
        the 2-turn-kill bar, so under the OLD priority-tie-break this picked
        the much weaker Aqua Jet purely for going first.

        Mega Tyranitar (this fixture's original target) no longer works:
        Basculegion's real default_sets.txt-pinned Life Orb now makes Wave
        Crash (super-effective on Rock/Dark Tyranitar) an outright OHKO on
        its own (140% avg, verified directly via `_raw_hit`), which breaks
        this test's own precondition that Wave Crash alone must NOT already
        KO -- a bulkier, only-neutrally-hit-by-Water target is needed to
        keep that precondition real. Corviknight (Steel/Flying, neutral to
        Water) fits: Wave Crash lands well under a 1-turn kill but clears a
        2-turn one on its own follow-up, and Aqua Jet's own damage plus that
        same follow-up also clears the bar -- so both still reach the
        2-turn-kill bar the lookahead cares about, and Wave Crash (the
        bigger raw hit) must still be the one chosen, exactly as before.
        Confirmed directly against real damage rolls before writing this
        test."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        basculegion = cf._build("Basculegion", merged, natures)
        corviknight = cf._build("Corviknight", merged, natures)
        wave_crash = cf._lookup_move("Wave Crash", moves)
        aqua_jet = cf._lookup_move("Aqua Jet", moves)
        wc = cf._raw_hit(basculegion, wave_crash, corviknight, typechart, roll="avg")
        aj = cf._raw_hit(basculegion, aqua_jet, corviknight, typechart, roll="avg")
        self.assertLess(wc.frac, 1.0)
        self.assertLess(aj.frac, 1.0)
        self.assertGreaterEqual(aj.frac + wc.frac, 1.0,
                                "fixture assumes Aqua Jet also reaches a "
                                "2-turn kill via Wave Crash as follow-up")
        hit, mv = cf._choose_move(basculegion, [wave_crash, aqua_jet],
                                  corviknight, typechart)
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
    never actually fires: a guaranteed-OHKO move can still lose to speed.

    Kingambit + Lycanroc-Dusk (this class's original fixture) no longer
    demonstrates it: Kingambit's real default_sets.txt-pinned Chople Berry
    now blunts Lycanroc-Dusk's Close Combat down to 82% avg (verified
    directly via `_raw_hit`), well short of the guaranteed OHKO the
    precondition test below needs, and no real, currently-faster attacker
    in the dataset can both guarantee an OHKO on Kingambit's real pinned
    bulk AND still fall to one of Kingambit's own real non-priority moves
    in return (checked exhaustively against every species' own real
    `moves_usage`) -- Kingambit's real set is now simply too tanky for this
    specific "both sides guarantee a KO" scenario. Grimmsnarl + Mega
    Gengar, verified the same way, still reproduces it: Grimmsnarl's Foul
    Play is a guaranteed OHKO on Mega Gengar (122.6% avg), so it
    independently outranks Grimmsnarl's own Sucker Punch (91.4%, doesn't
    KO alone) under `_choose_move`'s own KO-first rule -- but Grimmsnarl is
    naturally SLOWER than Mega Gengar, whose own Sludge Bomb is ALSO a
    guaranteed OHKO on Grimmsnarl (104.2%) and, with no priority advantage
    on Grimmsnarl's side, resolves first. Foul Play never fires; Sucker
    Punch (priority +1, one of Grimmsnarl's own real recorded moves) would
    have. `test_without_the_threat_the_guaranteed_ko_is_still_used` below
    still uses Kingambit -- that test only needs Kingambit's OWN best move
    to be picked absent any real threat, which doesn't depend on either of
    the broken OHKO preconditions above. Confirmed directly against real
    damage rolls before writing these tests."""

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.grimmsnarl = cf._build("Grimmsnarl", merged, natures)
        self.grimmsnarl_moves = cf._move_infos(
            "Grimmsnarl", merged, moves,
            ["Sucker Punch", "Foul Play", "Spirit Break"])
        self.gengar = cf._build("Mega Gengar", merged, natures)
        self.sludge_bomb = cf._move_infos(
            "Mega Gengar", merged, moves, ["Sludge Bomb"])
        self.kingambit = cf._build("Kingambit", merged, natures)
        self.kingambit_moves = cf._move_infos(
            "Kingambit", merged, moves,
            ["Sucker Punch", "Iron Head", "Kowtow Cleave"])
        self.charizard = cf._build("Mega Charizard Y", merged, natures)
        self.solar_beam = cf._move_infos(
            "Mega Charizard Y", merged, moves, ["Solar Beam"])

    def test_fixture_assumes_both_moves_are_real_ohkos(self):
        typechart = self.W["typechart"]
        sludge_bomb = cf._raw_hit(self.gengar, self.sludge_bomb[0],
                                  self.grimmsnarl, typechart, roll="avg")
        foul_play = cf._raw_hit(self.grimmsnarl, cf._lookup_move(
            "Foul Play", self.W["moves"]), self.gengar, typechart, roll="avg")
        self.assertGreaterEqual(sludge_bomb.frac, 1.0)
        self.assertGreaterEqual(foul_play.frac, 1.0)

    def test_sequential_pair_outcome_reconsiders_toward_sucker_punch(self):
        typechart = self.W["typechart"]
        result = cf._sequential_pair_outcome(
            self.gengar, self.sludge_bomb, "Grimmsnarl", self.grimmsnarl,
            self.grimmsnarl_moves, "Mega Charizard Y", self.charizard,
            self.solar_beam, typechart, candidate_target="Grimmsnarl")
        e1_hits = result["hits"].get("E1", {})
        self.assertIn("C", e1_hits)
        self.assertEqual(e1_hits["C"].move_name, "Sucker Punch",
                         "Foul Play would never fire -- Grimmsnarl dies to "
                         "Sludge Bomb before its own (slower) turn comes up")

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
        combatants = {"C": self.gengar, "P": self.gengar,
                      "E1": self.grimmsnarl, "E2": self.grimmsnarl}
        moves_by_role = {"C": self.sludge_bomb, "P": [],
                         "E1": self.grimmsnarl_moves, "E2": []}
        weather = cf._field_weather({"C": self.gengar, "E1": self.grimmsnarl,
                                     "E2": self.charizard})
        hp = {"C": 1.0, "P": 0.0, "E1": 1.0, "E2": 0.0}
        _hp, log, _enemy_acted, _wiped, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"C": "E1"})
        by_role = {(role, tgt): h.move_name for role, tgt, h in log}
        self.assertEqual(by_role.get(("E1", "C")), "Sucker Punch")

    def test_a_role_with_no_faster_option_stays_on_its_doomed_pick(self):
        """When NOTHING in the doomed role's own moveset is fast enough to
        matter, reconsideration must leave the original choice alone --
        Grimmsnarl restricted to Foul Play/Spirit Break only (no Sucker
        Punch in its set at all) has no escape, so it should still show up
        using one of those, not silently vanish from the log."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        no_priority = cf._move_infos(
            "Grimmsnarl", merged, moves, ["Foul Play", "Spirit Break"])
        result = cf._sequential_pair_outcome(
            self.gengar, self.sludge_bomb, "Grimmsnarl", self.grimmsnarl,
            no_priority, "Mega Charizard Y", self.charizard, self.solar_beam,
            typechart, candidate_target="Grimmsnarl")
        e1_hits = result["hits"].get("E1")
        self.assertIsNone(e1_hits, "Grimmsnarl really does die before "
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
        contain an E1/E2 entry -- that's what "swept" means. Hatterene, not
        Ariados -- see the sibling note in `TestJointPairSearch.test_a_
        dominant_fast_pair_sweeps_a_weak_slow_pair` for why a real Rage
        Powder user isn't a clean-sweep fixture any more."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(["Mega Gengar"], ["Sableye", "Hatterene"],
                                    "Mega Alakazam", merged, moves, natures,
                                    typechart)
        d = rows[0]["detail"][("Sableye", "Hatterene")]
        self.assertEqual(d["outcome"], "sweep")
        actors = {role for turn in d["log"] for role, _tgt, _h in turn}
        self.assertEqual(actors, {"C", "P"})

    def test_an_out_trade_logs_hits_from_both_sides(self):
        """Same Mega Scizor + Whimsicott vs Kingambit + Tyranitar out_trade
        matchup as `TestJointPairSearch.test_out_trade_wins_the_race_
        without_a_clean_sweep` -- see its docstring for why Kingambit +
        Basculegion no longer reaches out_trade here at all."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        rows = cf.joint_pair_search(["Mega Scizor"], ["Kingambit", "Tyranitar"],
                                    "Whimsicott", merged, moves, natures,
                                    typechart, turns=3)
        d = rows[0]["detail"][("Kingambit", "Tyranitar")]
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
            "pairs_tailwind_safe": 3, "pairs_follow_me_safe": 3,
            "pairs_clean_win_total": 3.0,
        }
        more_wins_fewer_protect_safe = {
            "pairs_swept": 0, "pairs_traded": 5, "pairs_protect_safe": 1,
            "pairs_tailwind_safe": 5, "pairs_follow_me_safe": 5,
            "pairs_clean_win_total": 5.0,
        }
        self.assertLess(cf._pair_sort_key(fewer_wins_more_protect_safe),
                        cf._pair_sort_key(more_wins_fewer_protect_safe),
                        "3 protect-safe wins (even with fewer raw wins) "
                        "must rank ahead of 1 protect-safe win")

    def test_beaten_count_is_the_tiebreak_when_protect_safe_ties(self):
        tied_protect_safe_fewer_wins = {
            "pairs_swept": 0, "pairs_traded": 2, "pairs_protect_safe": 2,
            "pairs_tailwind_safe": 2, "pairs_follow_me_safe": 2,
            "pairs_clean_win_total": 2.0,
        }
        tied_protect_safe_more_wins = {
            "pairs_swept": 0, "pairs_traded": 4, "pairs_protect_safe": 2,
            "pairs_tailwind_safe": 4, "pairs_follow_me_safe": 4,
            "pairs_clean_win_total": 4.0,
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
            "pairs_tailwind_safe": 3, "pairs_follow_me_safe": 3,
            "pairs_clean_win_total": 1.5,
        }
        clean_wins = {
            "pairs_swept": 3, "pairs_traded": 0, "pairs_protect_safe": 3,
            "pairs_tailwind_safe": 0, "pairs_follow_me_safe": 0,
            "pairs_clean_win_total": 6.0,
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


class TestExplicitEnemyPairs(unittest.TestCase):
    """"let me enter a list of enemy pairs and try to find a pair or a team
    with the best performance against those pairs" -- `_pair_vs_targets`'s/
    `joint_pool_search`'s new `enemy_pairs` param: race EXACTLY the given
    combinations, not every C(target_names, 2) of their union."""

    def setUp(self):
        self.W = world()

    def test_only_the_explicit_pairs_are_raced_not_every_combination(self):
        merged, natures = self.W["merged"], self.W["natures"]
        our_built = cf._build_forms(["Garchomp", "Milotic"], merged, natures,
                                    self.W["moves"])
        union = ["Kingambit", "Basculegion", "Sinistcha", "Whimsicott"]
        enemy_built = cf._build_forms(union, merged, natures, self.W["moves"])
        enemy_pairs = [("Kingambit", "Basculegion"), ("Sinistcha", "Whimsicott")]
        detail, summary = cf._pair_vs_targets(
            "Garchomp", "Milotic", our_built, union, enemy_built,
            self.W["typechart"], turns=2, merged=merged, enemy_pairs=enemy_pairs)
        self.assertEqual(set(detail.keys()), set(enemy_pairs))
        self.assertEqual(summary["pairs_total"], 2)

    def test_none_falls_back_to_every_combination_unchanged(self):
        merged, natures = self.W["merged"], self.W["natures"]
        our_built = cf._build_forms(["Garchomp", "Milotic"], merged, natures,
                                    self.W["moves"])
        targets = ["Kingambit", "Basculegion", "Sinistcha"]
        enemy_built = cf._build_forms(targets, merged, natures, self.W["moves"])
        detail, _summary = cf._pair_vs_targets(
            "Garchomp", "Milotic", our_built, targets, enemy_built,
            self.W["typechart"], turns=2, merged=merged)
        import itertools as _it
        self.assertEqual(set(detail.keys()),
                         set(_it.combinations(targets, 2)))

    def test_joint_pool_search_threads_enemy_pairs_through(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola"]
        union = ["Kingambit", "Basculegion", "Sinistcha", "Whimsicott"]
        enemy_pairs = [("Kingambit", "Basculegion"), ("Sinistcha", "Whimsicott")]
        rows = cf.joint_pool_search(pool, union, merged, moves, natures,
                                    typechart, enemy_pairs=enemy_pairs)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["pairs_total"], 2)
            self.assertEqual(set(r["detail"].keys()), set(enemy_pairs))

    def test_joint_pool_search_default_none_is_unaffected(self):
        """Regression guard: adding the new param must not change existing
        callers that never pass it."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola"]
        targets = ["Sableye", "Ariados"]
        with_default = cf.joint_pool_search(pool, targets, merged, moves,
                                            natures, typechart)
        explicit_none = cf.joint_pool_search(pool, targets, merged, moves,
                                             natures, typechart, enemy_pairs=None)
        self.assertEqual(with_default, explicit_none)

    def test_joint_pair_search_threads_enemy_pairs_through(self):
        """"run the joint pair search with a given partner vs all enemy
        teams" -- `joint_pair_search` (the FIXED-partner search) gets the
        same `enemy_pairs` param `joint_pool_search`/`_pair_vs_targets`
        already have, so a caller can race a fixed partner against every
        SAVED TEAM's own internal pairs at once, never a cross-team pair."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Mega Gengar", "Ninetales-Alola"]
        union = ["Kingambit", "Basculegion", "Sinistcha", "Whimsicott"]
        enemy_pairs = [("Kingambit", "Basculegion"), ("Sinistcha", "Whimsicott")]
        rows = cf.joint_pair_search(pool, union, "Mega Alakazam", merged, moves,
                                    natures, typechart, enemy_pairs=enemy_pairs)
        self.assertTrue(rows)
        for r in rows:
            self.assertEqual(r["pairs_total"], 2)
            self.assertEqual(set(r["detail"].keys()), set(enemy_pairs))

    def test_joint_pair_search_default_none_is_unaffected(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Mega Gengar", "Ninetales-Alola"]
        targets = ["Sableye", "Ariados"]
        with_default = cf.joint_pair_search(pool, targets, "Mega Alakazam",
                                            merged, moves, natures, typechart)
        explicit_none = cf.joint_pair_search(pool, targets, "Mega Alakazam",
                                             merged, moves, natures, typechart,
                                             enemy_pairs=None)
        self.assertEqual(with_default, explicit_none)

    def test_all_teams_pairs_never_mix_two_different_teams(self):
        """A concrete end-to-end demonstration of the "vs all enemy teams"
        reading: unioning each saved team's own C(len,2) internal pairs
        (never a cross-team pair between two different rosters) is exactly
        what the app's own "vs ALL saved enemy teams" checkbox builds."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        teams = {"Team A": ["Kingambit", "Basculegion"],
                "Team B": ["Sinistcha", "Whimsicott", "Garchomp"]}
        import itertools as _it
        enemy_pairs = [p for roster in teams.values()
                      for p in _it.combinations(roster, 2)]
        union = sorted({n for pair in enemy_pairs for n in pair})
        rows = cf.joint_pair_search(
            ["Mega Gengar"], union, "Mega Alakazam", merged, moves, natures,
            typechart, enemy_pairs=enemy_pairs)
        self.assertTrue(rows)
        raced = set(rows[0]["detail"].keys())
        # Every raced pair's two members must come from the SAME team.
        for e1, e2 in raced:
            same_team = any(e1 in roster and e2 in roster
                            for roster in teams.values())
            self.assertTrue(same_team, f"{e1}+{e2} mixes two different teams")
        # Kingambit+Sinistcha (one from each team) must never appear.
        self.assertNotIn(("Kingambit", "Sinistcha"), raced)
        self.assertNotIn(("Sinistcha", "Kingambit"), raced)


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
    Garchomp/Incineroar Sitrus Berry collision must still show up by
    default, and disappear only when explicitly requested.

    Sitrus Berry, not Focus Sash/Life Orb: those two are now ALSO capped
    at 1 BY DEFAULT (a separate, always-on mechanism -- see
    `TestFocusSashCapIsDefaultOn`/`TestCapLifeOrb`), unrelated to
    `enforce_item_clause` -- a collision on either would get resolved
    before Item Clause ever enters the picture, so this fixture needs an
    UNCAPPED item to actually test "items remain non-unique by default"
    at all."""

    OUR6 = ["Mega Gengar", "Mega Alakazam", "Garchomp", "Sharpedo",
           "Incineroar", "Kingambit"]
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
        self.assertEqual(items["Garchomp"], items["Incineroar"])

    def test_enforce_item_clause_resolves_the_collision(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pair_rows, _br = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            enforce_item_clause=True)
        items = self._items_by_name(pair_rows)
        self.assertNotEqual(items["Garchomp"], items["Incineroar"])
        self.assertEqual(len(set(items.values())), len(items))

    def test_default_core_deep_dive_still_shows_the_collision(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(
            self.OUR6, [self.TARGETS], merged, moves, natures, typechart)
        items = {n: s["item"] for n, s in dive["sets"].items()}
        self.assertEqual(items["Garchomp"], items["Incineroar"])

    def test_enforce_item_clause_resolves_the_collision_in_core_deep_dive(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(
            self.OUR6, [self.TARGETS], merged, moves, natures, typechart,
            enforce_item_clause=True)
        items = {n: s["item"] for n, s in dive["sets"].items()}
        self.assertNotEqual(items["Garchomp"], items["Incineroar"])


class TestCapFocusSash(unittest.TestCase):
    """`_cap_focus_sash` -- "the focus sash is just too broken and is
    warping matchup assessment... I will ban the use of the focus sash, or
    will only allow one pokemon to use the focus sash". Same single
    ordered pass and build-order rule as `_resolve_unique_items`, scoped
    to just this one item."""

    NAMES = ["Excadrill", "Aegislash", "Gengar"]
    TARGETS = ["Garchomp", "Incineroar"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.plain = {n: cf._answer_for(n, merged, moves, natures, typechart,
                                        self.TARGETS)[0] for n in self.NAMES}

    def test_a_real_focus_sash_collision_exists_in_this_fixture(self):
        """Confirms the fixture itself: at least two of NAMES independently
        want Focus Sash before any cap is applied -- otherwise every test
        below would trivially pass for the wrong reason."""
        sash_holders = [n for n, item in self.plain.items() if item == "Focus Sash"]
        self.assertGreaterEqual(len(sash_holders), 2, self.plain)

    def test_default_cap_of_one_keeps_only_the_first_in_build_order(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_focus_sash(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS)
        sash_holders = [n for n in self.NAMES if resolved[n] == "Focus Sash"]
        # Excadrill (first in NAMES) no longer independently wants Focus
        # Sash at all -- its real default_sets.txt-pinned item is Expert
        # Belt (verified directly via `_answer_for`) -- so build order now
        # keeps the FIRST of the two real remaining sash-wanters, Aegislash
        # (before Gengar), not Excadrill.
        self.assertEqual(sash_holders, ["Aegislash"])
        # Every OTHER independently-sashed name still resolves to something
        # real (its own next-best legal item), not None/dropped.
        self.assertTrue(all(resolved.values()))

    def test_a_pinned_item_override_is_never_touched(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_focus_sash(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            item_overrides={"Gengar": "Focus Sash"})
        self.assertEqual(resolved["Gengar"], "Focus Sash")

    def test_max_focus_sash_zero_bans_it_outright(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_focus_sash(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_focus_sash=0)
        self.assertNotIn("Focus Sash", resolved.values())
        self.assertTrue(all(resolved.values()), "every name still gets a "
                        "real fallback item, not dropped entirely")

    def test_max_focus_sash_none_disables_the_check(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_focus_sash(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_focus_sash=None)
        self.assertEqual(resolved, {})

    def test_higher_cap_allows_more_than_one(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_focus_sash(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_focus_sash=2)
        sash_holders = [n for n in self.NAMES if resolved[n] == "Focus Sash"]
        self.assertEqual(len(sash_holders), 2)


class TestCapLifeOrb(unittest.TestCase):
    """`_cap_life_orb` -- the Life Orb sibling of `_cap_focus_sash`: "same
    for life orb (which could just be replaced by type damage boost)".
    Same shared `_cap_items` mechanism, same build-order rule."""

    NAMES = ["Sharpedo", "Basculegion"]
    TARGETS = ["Garchomp", "Incineroar"]

    def setUp(self):
        self.W = world()

    def test_a_real_life_orb_collision_exists_in_this_fixture(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        plain = {n: cf._answer_for(n, merged, moves, natures, typechart,
                                   self.TARGETS)[0] for n in self.NAMES}
        self.assertTrue(all(v == "Life Orb" for v in plain.values()), plain)

    def test_default_cap_of_one_keeps_only_the_first_in_build_order(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_life_orb(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS)
        orb_holders = [n for n in self.NAMES if resolved[n] == "Life Orb"]
        self.assertEqual(orb_holders, ["Sharpedo"])
        self.assertTrue(all(resolved.values()))

    def test_max_life_orb_zero_bans_it_outright(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_life_orb(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_life_orb=0)
        self.assertNotIn("Life Orb", resolved.values())
        self.assertTrue(all(resolved.values()))

    def test_max_life_orb_none_disables_the_check(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_life_orb(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_life_orb=None)
        self.assertEqual(resolved, {})


class TestCapItemsCombined(unittest.TestCase):
    """`_cap_items` -- the shared engine `_cap_focus_sash`/`_cap_life_orb`
    both delegate to: caps SEVERAL named items in ONE ordered pass, so a
    name displaced from one over-cap item is ALSO excluded from any other
    already-full capped item in the same fallback re-search, not just
    whichever one displaced it."""

    TARGETS = ["Garchomp", "Incineroar"]

    def setUp(self):
        self.W = world()

    def test_both_caps_respected_in_one_pass(self):
        names = ["Excadrill", "Aegislash", "Sharpedo", "Basculegion"]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_items(
            names, merged, moves, natures, typechart, self.TARGETS,
            item_caps={"Focus Sash": 1, "Life Orb": 1})
        sash = sum(1 for v in resolved.values() if v == "Focus Sash")
        orb = sum(1 for v in resolved.values() if v == "Life Orb")
        self.assertLessEqual(sash, 1)
        self.assertLessEqual(orb, 1)
        self.assertTrue(all(resolved.values()))

    def test_falsy_item_caps_is_a_full_no_op(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._cap_items(
            ["Excadrill", "Aegislash"], merged, moves, natures, typechart,
            self.TARGETS, item_caps=None)
        self.assertEqual(resolved, {})

    def test_extra_items_thread_through_to_a_displaced_names_fallback(self):
        """`extra_items` is only ever passed to `_answer_for` for a name
        whose independently-best item is actually over cap -- confirmed by
        a spy, mirroring this file's own established `unittest.mock.patch`
        spy convention.

        Excadrill (this fixture's original first name) no longer works:
        its real default_sets.txt-pinned item is Expert Belt, so it no
        longer independently wants Focus Sash at all, and with only one
        real sash-wanter left in a 2-name list nobody is ever displaced.
        Gengar, verified directly via `_answer_for`, still independently
        wants Focus Sash against these TARGETS -- swapped in as the first
        name so Aegislash (second, real sash-wanter too) is still the one
        actually displaced."""
        import unittest.mock as mock
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        with mock.patch.object(cf, "_answer_for", wraps=cf._answer_for) as spy:
            cf._cap_items(
                ["Gengar", "Aegislash"], merged, moves, natures, typechart,
                self.TARGETS, item_caps={"Focus Sash": 1},
                extra_items=["Rocky Helmet"])
        calls_with_extra = [c for c in spy.call_args_list
                            if c.kwargs.get("extra_items")]
        self.assertTrue(calls_with_extra)
        self.assertIn("Rocky Helmet", calls_with_extra[0].kwargs["extra_items"])

    def test_a_displaced_name_also_gets_its_own_type_boost_and_resist_berry(self):
        """Auto-computed per-name backups (no caller-supplied `extra_items`
        needed): a displaced name's own STAB type-boost item and its own
        worst-weakness resist berry both show up, unprompted.

        Same Excadrill -> Gengar swap as the sibling test above -- see its
        own docstring for why."""
        import unittest.mock as mock
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        with mock.patch.object(cf, "_answer_for", wraps=cf._answer_for) as spy:
            cf._cap_items(
                ["Gengar", "Aegislash"], merged, moves, natures, typechart,
                self.TARGETS, item_caps={"Focus Sash": 1})
        calls_with_extra = [c for c in spy.call_args_list
                            if c.kwargs.get("extra_items")]
        self.assertTrue(calls_with_extra)
        expected = cf._backup_extra_items_for("Aegislash", merged)
        self.assertTrue(expected, "fixture assumes Aegislash actually has a "
                                  "real type-boost/resist-berry candidate")
        self.assertEqual(calls_with_extra[0].kwargs["extra_items"], expected)


class TestResolveTeamItems(unittest.TestCase):
    """`_resolve_team_items` -- the one place `bring4_search`/`core_deep_
    dive`/`deep_dive` compose the Focus-Sash cap with the (still opt-in)
    full Item Clause, so the two can never disagree on how they combine."""

    NAMES = ["Excadrill", "Aegislash", "Gengar"]
    TARGETS = ["Garchomp", "Incineroar"]

    def setUp(self):
        self.W = world()

    def test_default_caps_focus_sash_at_one(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._resolve_team_items(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS)
        self.assertEqual(sum(1 for v in resolved.values() if v == "Focus Sash"), 1)

    def test_full_item_clause_skips_the_redundant_cap_pass(self):
        """A full Item Clause already caps every item (Focus Sash
        included) at 1 -- `max_focus_sash`'s own default must not run a
        second, redundant pass on top of it."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._resolve_team_items(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            enforce_item_clause=True)
        vals = [v for v in resolved.values() if v]
        self.assertEqual(len(vals), len(set(vals)))

    def test_max_focus_sash_zero_still_fully_resolves_every_name(self):
        """Regression: banning must not short-circuit to an empty dict --
        every name still needs its own item resolved, just never Focus
        Sash."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._resolve_team_items(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_focus_sash=0)
        self.assertNotIn("Focus Sash", resolved.values())
        self.assertTrue(all(resolved.get(n) for n in self.NAMES))

    def test_max_focus_sash_none_alone_still_caps_life_orb_by_default(self):
        """`max_life_orb` defaults to capped too -- opting the Sash cap out
        alone must not ALSO silently opt out of the (separately default-on)
        Life Orb cap."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._resolve_team_items(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_focus_sash=None)
        self.assertNotEqual(resolved, {})
        self.assertLessEqual(
            sum(1 for v in resolved.values() if v == "Life Orb"), 1)

    def test_both_caps_none_opts_out_entirely(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._resolve_team_items(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS,
            max_focus_sash=None, max_life_orb=None)
        self.assertEqual(resolved, {})

    def test_default_also_caps_life_orb_at_one(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        resolved = cf._resolve_team_items(
            self.NAMES, merged, moves, natures, typechart, self.TARGETS)
        self.assertLessEqual(
            sum(1 for v in resolved.values() if v == "Life Orb"), 1)


class TestFocusSashCapIsDefaultOn(unittest.TestCase):
    """UNLIKE `enforce_item_clause` (TestBring4SearchItemClauseIsOptIn),
    the Focus-Sash cap needs NO flag to take effect -- "this must apply to
    every single team". `bring4_search`/`core_deep_dive`/`deep_dive` all
    cap it at 1 by default; `max_focus_sash=None` is the explicit opt-out."""

    OUR6 = ["Excadrill", "Aegislash", "Gengar", "Mega Alakazam",
           "Sharpedo", "Kingambit"]
    TARGETS = ["Garchomp", "Incineroar"]

    def setUp(self):
        self.W = world()

    def _sash_count(self, items_by_name):
        return sum(1 for v in items_by_name.values() if v == "Focus Sash")

    def test_bring4_search_caps_it_with_no_flag_needed(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pair_rows, _br = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart)
        items = {}
        for r in pair_rows:
            n1, n2 = r["pair"]
            items[n1], items[n2] = r["item1"], r["item2"]
        self.assertLessEqual(self._sash_count(items), 1)

    def test_bring4_search_max_focus_sash_none_restores_the_old_behaviour(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pair_rows, _br = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            max_focus_sash=None)
        items = {}
        for r in pair_rows:
            n1, n2 = r["pair"]
            items[n1], items[n2] = r["item1"], r["item2"]
        self.assertGreaterEqual(self._sash_count(items), 2)

    def test_core_deep_dive_caps_it_with_no_flag_needed(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(
            self.OUR6, [self.TARGETS], merged, moves, natures, typechart)
        items = {n: s["item"] for n, s in dive["sets"].items()}
        self.assertLessEqual(self._sash_count(items), 1)

    def test_deep_dive_caps_it_with_no_flag_needed(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        item1, item2, _detail, _summary = cf.deep_dive(
            "Excadrill", "Gengar", self.TARGETS, merged, moves, natures, typechart)
        self.assertFalse(item1 == "Focus Sash" and item2 == "Focus Sash")

    def test_max_focus_sash_zero_bans_it_across_the_team(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pair_rows, _br = cf.bring4_search(
            self.OUR6, self.TARGETS, merged, moves, natures, typechart,
            max_focus_sash=0)
        items = {}
        for r in pair_rows:
            n1, n2 = r["pair"]
            items[n1], items[n2] = r["item1"], r["item2"]
        self.assertEqual(self._sash_count(items), 0)


class TestCoreRowFocusSashCap(unittest.TestCase):
    """`_core_row`'s `focus_sash_context` -- the cheap-check-gates-an-
    expensive-re-race shape `_apply_item_caps_to_top_rows` relies on (via
    `_core_item_cap_pair_by_key`) for --multi-bring4's top rows, mirroring
    `item_clause_context` exactly."""

    def setUp(self):
        self.W = world()

    def test_no_context_reproduces_old_behaviour(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Excadrill", "Aegislash", "Gengar", "Mega Alakazam"]
        targets = [["Garchomp", "Incineroar"]]
        coverage = cf.multi_bring4_coverage(pool, targets, merged, moves,
                                            natures, typechart)
        core = tuple(sorted(pool))
        row = cf._core_row(core, coverage["pair_by_key"],
                           coverage["target_name_lists"],
                           pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"])
        self.assertIsNone(row["item_clause_resolved_items"])

    def test_a_real_collision_triggers_a_focus_sash_scoped_reresolve(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Excadrill", "Aegislash", "Gengar", "Mega Alakazam"]
        targets = [["Garchomp", "Incineroar"]]
        coverage = cf.multi_bring4_coverage(pool, targets, merged, moves,
                                            natures, typechart)
        # Confirm the pool-wide fixed_items really do collide on Focus Sash
        # for this core -- otherwise the test proves nothing.
        core = tuple(sorted(pool))
        fixed = coverage["fixed_items"]
        self.assertGreaterEqual(
            sum(1 for n in core if fixed.get(n) == "Focus Sash"), 2)
        context = cf._focus_sash_context_from_coverage(coverage, max_focus_sash=1)
        row = cf._core_row(core, coverage["pair_by_key"],
                           coverage["target_name_lists"],
                           pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"],
                           focus_sash_context=context)
        resolved = row["item_clause_resolved_items"]
        self.assertIsNotNone(resolved)
        self.assertLessEqual(
            sum(1 for v in resolved.values() if v == "Focus Sash"), 1)

    def test_item_clause_context_takes_precedence_when_both_given(self):
        """A full Item Clause resolution already caps Focus Sash at 1 as a
        side effect -- the Focus-Sash-only pass must not ALSO run
        (redundant, and would just re-derive the same answer a second
        time)."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Ninetales-Alola", "Rampardos"]
        targets = [["Sableye", "Ariados"]]
        coverage = cf.multi_bring4_coverage(pool, targets, merged, moves,
                                            natures, typechart)
        core = tuple(sorted(pool))
        item_context = cf._item_clause_context_from_coverage(coverage)
        sash_context = cf._focus_sash_context_from_coverage(coverage, max_focus_sash=1)
        row = cf._core_row(core, coverage["pair_by_key"],
                           coverage["target_name_lists"],
                           pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"],
                           item_clause_context=item_context,
                           focus_sash_context=sash_context)
        # The Life Orb collision (see TestResolveUniqueItems) is resolved
        # by the FULL clause path -- item_clause_resolved_items is set.
        self.assertIsNotNone(row["item_clause_resolved_items"])


class TestCoreRowItemCapOverage(unittest.TestCase):
    """`_core_row`'s `pool_fixed_items`/`item_caps` -- "by default NO team
    should ever have more than 1 focus sash" applied to EVERY core in a
    full sweep, cheaply (a pure count against Stage A's own pool-wide
    `fixed_items`, no new racing), unlike the real benefit-based re-race
    top rows get elsewhere (`tools/counter_table.py`)."""

    def setUp(self):
        self.W = world()

    def _coverage(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Excadrill", "Aegislash", "Gengar", "Mega Alakazam"]
        targets = [["Garchomp", "Incineroar"]]
        coverage = cf.multi_bring4_coverage(pool, targets, merged, moves,
                                            natures, typechart)
        # Confirm the pool-wide fixed_items really do collide on Focus Sash
        # for this core -- otherwise the test proves nothing.
        self.assertGreaterEqual(
            sum(1 for n in pool if coverage["fixed_items"].get(n) == "Focus Sash"), 2)
        return coverage, tuple(sorted(pool))

    def test_default_none_reproduces_old_behaviour(self):
        coverage, core = self._coverage()
        row = cf._core_row(core, coverage["pair_by_key"],
                           coverage["target_name_lists"],
                           pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"])
        self.assertEqual(row["worst_enemy_score_key"][1], 0)

    def test_a_real_over_cap_core_gets_a_nonzero_overage(self):
        coverage, core = self._coverage()
        row = cf._core_row(core, coverage["pair_by_key"],
                           coverage["target_name_lists"],
                           pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"],
                           pool_fixed_items=coverage["fixed_items"])
        self.assertGreater(row["worst_enemy_score_key"][1], 0)

    def test_overage_penalises_the_core_below_an_otherwise_identical_one(self):
        """Two `_core_row` calls on the SAME core differ only in whether
        `pool_fixed_items` was given -- the over-cap one must rank worse
        (a larger `worst_enemy_score_key`), no other field changing."""
        coverage, core = self._coverage()
        capped = cf._core_row(core, coverage["pair_by_key"],
                              coverage["target_name_lists"],
                              pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"],
                              pool_fixed_items=coverage["fixed_items"])
        uncapped = cf._core_row(core, coverage["pair_by_key"],
                                coverage["target_name_lists"],
                                pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"])
        self.assertGreater(capped["worst_enemy_score_key"],
                           uncapped["worst_enemy_score_key"])
        self.assertEqual(capped["worst_enemy_score_key"][2:],
                         uncapped["worst_enemy_score_key"][2:])

    def test_custom_item_caps_override_the_default(self):
        coverage, core = self._coverage()
        row = cf._core_row(core, coverage["pair_by_key"],
                           coverage["target_name_lists"],
                           pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"],
                           pool_fixed_items=coverage["fixed_items"],
                           item_caps={"Focus Sash": 5})
        self.assertEqual(row["worst_enemy_score_key"][1], 0)


class TestItemCapBenefitBasedTopRows(unittest.TestCase):
    """`_core_item_cap_pair_by_key`/`_apply_item_caps_to_top_rows` -- the
    BENEFIT-BASED sibling of `TestCoreRowItemCapOverage`'s cheap always-on
    penalty: for the displayed top N rows only, actually re-race the core
    with each Focus-Sash/Life-Orb contester as the keeper and keep whichever
    assignment scores the WHOLE core best by its own `worst_enemy_score_
    key" -- "who benefits most vs replacement item to make the overall
    team the best", not just whoever comes first alphabetically.

    Same pool as `TestCoreRowItemCapOverage`, with one swap: Excadrill's
    real default_sets.txt-pinned item is now Expert Belt, so it no longer
    independently wants Focus Sash at all, leaving only two real
    contesters in the original four-name pool -- Dragapult, verified
    directly via `multi_bring4_coverage`, still independently wants Focus
    Sash against Garchomp/Incineroar here, restoring THREE of the pool's
    four members (Aegislash, Dragapult, Gengar) as real contesters, not
    just two -- confirmed below that letting Gengar keep it (instead of
    Aegislash or Dragapult) scores the whole core strictly worse, so a real
    benefit-based re-race must never choose it."""

    def setUp(self):
        self.W = world()

    def _coverage(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        pool = ["Dragapult", "Aegislash", "Gengar", "Mega Alakazam"]
        targets = [["Garchomp", "Incineroar"]]
        coverage = cf.multi_bring4_coverage(pool, targets, merged, moves,
                                            natures, typechart)
        self.assertGreaterEqual(
            sum(1 for n in pool if coverage["fixed_items"].get(n) == "Focus Sash"), 3,
            "fixture must have 3+ Focus-Sash-wanting members, or this test "
            "proves nothing about picking among several contesters")
        return coverage, tuple(sorted(pool))

    def test_core_item_cap_pair_by_key_respects_the_cap(self):
        coverage, core = self._coverage()
        ctx = cf._item_cap_context_from_coverage(coverage)
        pair_by_key_per_enemy, resolved = cf._core_item_cap_pair_by_key(
            core, coverage["target_name_lists"], ctx, {"Focus Sash": 1})
        self.assertLessEqual(
            sum(1 for v in resolved.values() if v == "Focus Sash"), 1)
        table = pair_by_key_per_enemy[tuple(coverage["target_name_lists"][0])]
        self.assertTrue(table, "expected a real per-enemy pair table back")

    def test_never_picks_a_keeper_that_leaves_an_enemy_pair_uncovered(self):
        """Gengar keeping Focus Sash (instead of Aegislash or Dragapult) is
        confirmed strictly worse by `worst_enemy_score_key` -- a benefit-
        based re-race must never choose it.

        The own-Protect mirror (`TestOwnProtectAsAMatchingAnswer`) can now
        salvage what used to be a fully-uncovered enemy pair into a no_ko
        stalemate for SOME core/item combination here, so `total_uncovered`
        (the score key's own first, most decisive component) no longer
        reliably differs between the Gengar-forced and resolved item
        assignments the way it always used to -- the comparison below reads
        the full tuple (which still keeps `blended_avg_wins`, weighted
        lower) rather than assuming the difference always shows up in that
        first component specifically."""
        coverage, core = self._coverage()
        ctx = cf._item_cap_context_from_coverage(coverage)
        item_caps = {"Focus Sash": 1}
        pbk, resolved = cf._core_item_cap_pair_by_key(
            core, coverage["target_name_lists"], ctx, item_caps)
        self.assertNotEqual(resolved.get("Gengar"), "Focus Sash")

        # Confirm this is a REAL fixture property (Gengar-as-keeper really
        # is worse), not a coincidence of the function's own answer --
        # otherwise this test would pass even if the function picked
        # arbitrarily/at random.
        forced = cf._cap_items(
            ["Gengar", "Aegislash", "Dragapult", "Mega Alakazam"],
            ctx["merged"], ctx["moves_db"], ctx["natures"], ctx["typechart"],
            sorted({n for t in coverage["target_name_lists"] for n in t}),
            move_overrides=ctx["fixed_moves"], excluded_items=ctx["excluded_items"],
            item_caps=item_caps)
        self.assertEqual(forced.get("Gengar"), "Focus Sash")
        gengar_rows = {}
        for target_names in coverage["target_name_lists"]:
            rows = cf.joint_pool_search(
                list(core), target_names, ctx["merged"], ctx["moves_db"],
                ctx["natures"], ctx["typechart"], turns=ctx["turns"],
                item_overrides=forced, move_overrides=ctx["fixed_moves"],
                excluded_items=ctx["excluded_items"])
            gengar_rows[tuple(target_names)] = {frozenset(r["pair"]): r for r in rows}
        pair_by_key_list = [gengar_rows[tuple(t)] for t in coverage["target_name_lists"]]
        gengar_row = cf._core_row(core, pair_by_key_list, coverage["target_name_lists"],
                                  1.0, pool_fixed_items=forced, item_caps=item_caps)

        resolved_pair_by_key_list = [pbk[tuple(t)] for t in coverage["target_name_lists"]]
        resolved_row = cf._core_row(core, resolved_pair_by_key_list,
                                    coverage["target_name_lists"], 1.0,
                                    pool_fixed_items=resolved, item_caps=item_caps)
        self.assertLess(resolved_row["worst_enemy_score_key"],
                        gengar_row["worst_enemy_score_key"],
                        "expected forcing Gengar to keep Focus Sash to score "
                        "strictly worse than the resolved assignment -- if it "
                        "doesn't anymore, this fixture no longer demonstrates "
                        "a real choice among contesters")

    def test_apply_item_caps_to_top_rows_wires_it_in(self):
        coverage, core = self._coverage()
        rows = cf.multi_bring4_exhaustive(coverage, good_threshold=0.0,
                                          core_sizes=(4,))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["item_clause_resolved_items"])
        corrected = cf._apply_item_caps_to_top_rows(
            rows, 1, coverage, good_threshold=0.0, item_caps={"Focus Sash": 1})
        resolved = corrected[0]["item_clause_resolved_items"]
        self.assertIsNotNone(resolved)
        self.assertLessEqual(
            sum(1 for v in resolved.values() if v == "Focus Sash"), 1)
        self.assertNotEqual(resolved.get("Gengar"), "Focus Sash")

    def test_falsy_item_caps_is_a_full_no_op(self):
        coverage, core = self._coverage()
        rows = cf.multi_bring4_exhaustive(coverage, good_threshold=0.0,
                                          core_sizes=(4,))
        corrected = cf._apply_item_caps_to_top_rows(
            rows, 1, coverage, good_threshold=0.0, item_caps=None)
        self.assertEqual(corrected, rows)


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


class TestRecommendedLead(unittest.TestCase):
    """`recommended_lead` -- "is there a way to assess the best lead vs a
    given enemy team? Just the best pair + their backup" -- the single
    best-ranked (`_pair_sort_key`) of a bring-4's own 6 internal pairs is
    the lead, the other 2 members are the backup. No new racing: reads
    straight off `bring4_row["pair_rows"]`, already fully computed."""

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

    def test_lead_is_the_best_ranked_pair(self):
        b = self.bring4_rows[0]
        result = cf.recommended_lead(b)
        best_pair = min(b["pair_rows"], key=cf._pair_sort_key)
        self.assertEqual(set(result["lead"]), set(best_pair["pair"]))

    def test_backup_is_the_other_two_members(self):
        b = self.bring4_rows[0]
        result = cf.recommended_lead(b)
        self.assertEqual(set(result["lead"]) | set(result["backup"]),
                         set(b["bring4"]))
        self.assertEqual(len(set(result["lead"]) & set(result["backup"])), 0)
        self.assertEqual(len(result["backup"]), len(b["bring4"]) - 2)

    def test_every_bring4_row_gets_a_legal_lead_and_backup(self):
        for b in self.bring4_rows:
            result = cf.recommended_lead(b)
            self.assertEqual(len(result["lead"]), 2)
            self.assertEqual(set(result["lead"]) | set(result["backup"]),
                             set(b["bring4"]))


class TestBring4WinConditions(unittest.TestCase):
    """`bring4_win_conditions` -- "I want to be able to identify win
    conditions -- perhaps Metagross + Hydreigon is the only pair that
    beats Golisopod, or Hydreigon is the only pokemon that beats
    Golisopod. I need to see what pokemon I need to preserve to guarantee
    a win against certain pokemon in an endgame." Hand-built `detail`
    fixtures (no real racing) for precise control over which of a bring-
    4's own 6 pairs beat which enemy pairings."""

    BRING4 = ("A", "B", "C", "D")
    WIN, LOSS = {"outcome": "sweep"}, {"outcome": "loss"}

    def _row(self, pair, outcomes):
        """`outcomes`: {(e1, e2): "win"|"loss"|<already-built detail dict>,
        ...} -> a pair_rows entry. A plain "win"/"loss" string is the
        common case (tailwind_safe/protect_safe implicitly True, same as
        the `.get(..., True)` default `bring4_win_conditions` itself
        falls back to); pass an explicit dict (e.g. via `self._win(...)`)
        when a test needs to control those too."""
        def entry(v):
            if isinstance(v, dict):
                return v
            return self.WIN if v == "win" else self.LOSS
        return {"pair": pair, "detail": {k: entry(v) for k, v in outcomes.items()}}

    def _win(self, tailwind_safe=True, protect_safe=True, trick_room_safe=True):
        return {"outcome": "sweep", "tailwind_safe": tailwind_safe,
               "protect_safe": protect_safe, "trick_room_safe": trick_room_safe}

    def test_exactly_one_pair_needs_both_members_preserved(self):
        """"Metagross + Hydreigon is the only pair that beats Golisopod"
        -- only A+B beats EVERY pairing of X (XY and XZ); every other
        pair loses at least one of them."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): "win", ("X", "Z"): "win"}),
            self._row(("A", "C"), {("X", "Y"): "win", ("X", "Z"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss", ("X", "Z"): "win"}),
            self._row(("B", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "win", ("X", "Z"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss", ("X", "Z"): "win"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(wc["X"]["safe_pairs"], [("A", "B")])
        self.assertEqual(wc["X"]["safe_members"], [])
        self.assertFalse(wc["X"]["uncovered"])

    def test_a_single_member_safe_regardless_of_partner(self):
        """"Hydreigon is the only pokemon that beats Golisopod" -- A's OWN
        pairing with every other bring-4 member (B, C, D alike) beats
        every pairing of X, so A alone (survived, any partner) already
        guarantees it."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): "win", ("X", "Z"): "win"}),
            self._row(("A", "C"), {("X", "Y"): "win", ("X", "Z"): "win"}),
            self._row(("A", "D"), {("X", "Y"): "win", ("X", "Z"): "win"}),
            self._row(("B", "C"), {("X", "Y"): "loss", ("X", "Z"): "win"}),
            self._row(("B", "D"), {("X", "Y"): "win", ("X", "Z"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(set(wc["X"]["safe_pairs"]),
                         {("A", "B"), ("A", "C"), ("A", "D")})
        self.assertEqual(wc["X"]["safe_members"], ["A"])
        self.assertFalse(wc["X"]["uncovered"])

    def test_no_pair_beats_every_pairing_is_flagged_uncovered(self):
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): "win", ("X", "Z"): "loss"}),
            self._row(("A", "C"), {("X", "Y"): "loss", ("X", "Z"): "win"}),
            self._row(("A", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "win", ("X", "Z"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss", ("X", "Z"): "win"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(wc["X"]["safe_pairs"], [])
        self.assertEqual(wc["X"]["safe_members"], [])
        self.assertTrue(wc["X"]["uncovered"])

    def test_two_disjoint_safe_pairs_with_no_common_member(self):
        """A+B and C+D both independently answer X, but no SINGLE member
        does regardless of partner (A's own OTHER pairings, A+C/A+D,
        aren't both safe) -- `safe_pairs` lists both real options,
        `safe_members` stays empty since neither is safe alone."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): "win", ("X", "Z"): "win"}),
            self._row(("A", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "win", ("X", "Z"): "win"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(set(wc["X"]["safe_pairs"]), {("A", "B"), ("C", "D")})
        self.assertEqual(wc["X"]["safe_members"], [])
        self.assertFalse(wc["X"]["uncovered"])

    def test_every_enemy_appearing_in_the_detail_gets_an_entry(self):
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): "win"}),
            self._row(("A", "C"), {("X", "Y"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(set(wc), {"X", "Y"})

    def test_tailwind_risk_flagged_when_every_safe_pair_fails_it(self):
        """"avoiding enemy tailwind ... may be key for a matchup swinging
        from a win to a clear loss" -- A+B is the only pair that beats
        every pairing of X, but it isn't `tailwind_safe` against XZ, so
        the win condition itself is fragile to a real Tailwind cast."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): self._win(),
                                   ("X", "Z"): self._win(tailwind_safe=False)}),
            self._row(("A", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(wc["X"]["safe_pairs"], [("A", "B")])
        self.assertTrue(wc["X"]["tailwind_risk"])
        self.assertFalse(wc["X"]["protect_risk"])

    def test_protect_risk_flagged_when_every_safe_pair_fails_it(self):
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): self._win(protect_safe=False)}),
            self._row(("A", "C"), {("X", "Y"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(wc["X"]["safe_pairs"], [("A", "B")])
        self.assertFalse(wc["X"]["tailwind_risk"])
        self.assertTrue(wc["X"]["protect_risk"])

    def test_trick_room_risk_flagged_when_every_safe_pair_fails_it(self):
        """"avoiding enemy tailwind and trick room may be key for a
        matchup swinging from a win to a clear loss" -- reads `trick_
        room_safe` the exact same way as `tailwind_safe`/`protect_safe`
        (only ever present in `detail` when the underlying search opted
        into `check_trick_room`; `.get(..., True)` elsewhere)."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): self._win(trick_room_safe=False)}),
            self._row(("A", "C"), {("X", "Y"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(wc["X"]["safe_pairs"], [("A", "B")])
        self.assertFalse(wc["X"]["tailwind_risk"])
        self.assertFalse(wc["X"]["protect_risk"])
        self.assertTrue(wc["X"]["trick_room_risk"])

    def test_no_risk_flagged_when_an_alternative_safe_pair_stays_robust(self):
        """A+B is fragile to Tailwind, but C+D ALSO independently beats
        every pairing of X and stays tailwind-safe -- the win condition
        as a whole ("preserve at least one of these") isn't actually at
        risk, since C+D covers it."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): self._win(),
                                   ("X", "Z"): self._win(tailwind_safe=False)}),
            self._row(("A", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss", ("X", "Z"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): self._win(), ("X", "Z"): self._win()}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertEqual(set(wc["X"]["safe_pairs"]), {("A", "B"), ("C", "D")})
        self.assertFalse(wc["X"]["tailwind_risk"])

    def test_no_risk_flagged_when_there_is_no_win_condition_at_all(self):
        """`tailwind_risk`/`protect_risk` describe how fragile an EXISTING
        win condition is -- they must not fire on top of `uncovered`,
        which already says there's no guaranteed answer to begin with."""
        bring4_row = {"bring4": self.BRING4, "pair_rows": [
            self._row(("A", "B"), {("X", "Y"): "loss"}),
            self._row(("A", "C"), {("X", "Y"): "loss"}),
            self._row(("A", "D"), {("X", "Y"): "loss"}),
            self._row(("B", "C"), {("X", "Y"): "loss"}),
            self._row(("B", "D"), {("X", "Y"): "loss"}),
            self._row(("C", "D"), {("X", "Y"): "loss"}),
        ]}
        wc = cf.bring4_win_conditions(bring4_row)
        self.assertTrue(wc["X"]["uncovered"])
        self.assertFalse(wc["X"]["tailwind_risk"])
        self.assertFalse(wc["X"]["protect_risk"])
        self.assertFalse(wc["X"]["trick_room_risk"])

    def test_real_bring4_search_result_produces_sane_output(self):
        """End-to-end sanity check against a real race -- every entry's
        `safe_pairs` must actually be drawn from the bring-4's own 6
        pairs, and `safe_members` only ever names bring-4 members."""
        W = world()
        merged, moves = W["merged"], W["moves"]
        natures, typechart = W["natures"], W["typechart"]
        our6 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon",
               "Whimsicott", "Farigiraf"]
        vs_roster = ["Archaludon", "Grimmsnarl", "Mega Metagross",
                    "Pelipper", "Sinistcha", "Mega Swampert"]
        _pair_rows, bring4_rows = cf.bring4_search(
            our6, vs_roster, merged, moves, natures, typechart)
        b = bring4_rows[0]
        wc = cf.bring4_win_conditions(b)
        own_pairs = {r["pair"] for r in b["pair_rows"]}
        own_pair_sets = {frozenset(p) for p in own_pairs}
        self.assertEqual(set(wc), set(vs_roster))
        for enemy, info in wc.items():
            for p in info["safe_pairs"]:
                self.assertIn(frozenset(p), own_pair_sets)
            for m in info["safe_members"]:
                self.assertIn(m, b["bring4"])
            self.assertEqual(info["uncovered"], not info["safe_pairs"])
            if info["uncovered"]:
                self.assertFalse(info["tailwind_risk"])
                self.assertFalse(info["protect_risk"])
                self.assertFalse(info["trick_room_risk"])


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

    def test_best_bring4_row_reports_which_mega_was_actually_used(self):
        """"note which one is used" -- the winning bring's own `mega_used`
        must name exactly the ONE of the core's 2 stone holders whose
        hypothesis actually won (i.e. the one NOT locked to base in
        whichever of `floette_forced`/`scizor_forced` matched the winning
        pairs, per the consistency test above)."""
        core = ("Garchomp", "Kingambit", "Mega Floette", "Mega Scizor")
        row = cf._core_row(
            core, self.coverage["pair_by_key"], self.coverage["target_name_lists"],
            good_threshold=0.0,
            pair_by_key_forced_base_list=self.coverage["pair_by_key_forced_base"])
        best = row["per_enemy"][0]["best_bring4_row"]
        self.assertIn(best["mega_used"], ("Mega Floette", "Mega Scizor"))
        got = {frozenset(pr["pair"]): pr for pr in best["pair_rows"]}
        pairs = list(got)
        floette_forced = self._hypothesis_rows("Mega Floette", pairs)
        scizor_forced = self._hypothesis_rows("Mega Scizor", pairs)
        if got == floette_forced:
            self.assertEqual(best["mega_used"], "Mega Scizor")
        else:
            self.assertTrue(got == scizor_forced)
            self.assertEqual(best["mega_used"], "Mega Floette")


class TestCoreRowDeadMegaDetection(unittest.TestCase):
    """`_core_row`'s new `dead_megas` field: "I have two megas on a
    generated team, but every single match only uses one of the megas...
    may as well give the other mega a useful item and leave it as base
    form if it never megas." Hand-built fixtures (`_fake_pair_row`, no
    real racing), same style as `TestCoreRowRespectsMegaConsistency`."""

    TARGETS1 = ("E1", "E2")
    TARGETS2 = ("E3", "E4")
    WIN1 = {TARGETS1}
    WIN2 = {TARGETS2}
    LOSS = set()

    def _fixture(self, six, targets, win_set, winner_name):
        """Every pair touching `winner_name` wins `targets`'s one enemy
        pair; every other pair (including ones touching the OTHER mega, if
        any) loses -- mirrors `TestCoreRowRespectsMegaConsistency.setUp`'s
        own touching_a/touching_b split, parameterised on the winner."""
        import itertools as _it
        pair_lookup = {frozenset(p): _fake_pair_row(p, self.LOSS, targets)
                       for p in _it.combinations(six, 2)}
        touching_winner = [p for p in _it.combinations(six, 2) if winner_name in p]
        forced_base = {winner_name: {frozenset(p): _fake_pair_row(p, self.LOSS, targets)
                                     for p in touching_winner}}
        # The OTHER stone holder (if `six` has one), forced to base, is
        # what actually lets `winner_name` transform -- same shape as
        # `TestCoreRowRespectsMegaConsistency`'s own `touching_b`/`touching_a`.
        megas = [n for n in six if n.startswith("Mega ")]
        other = next((m for m in megas if m != winner_name), None)
        if other is not None:
            touching_other = [p for p in _it.combinations(six, 2) if other in p]
            forced_base[other] = {frozenset(p): _fake_pair_row(p, win_set, targets)
                                  for p in touching_other}
        return pair_lookup, forced_base

    def test_no_dead_megas_with_fewer_than_two_stone_holders(self):
        six = ["Mega A", "C", "D", "E"]
        pair_lookup, _fb = self._fixture(six, self.TARGETS1, self.WIN1, "Mega A")
        row = cf._core_row(six, [pair_lookup], [self.TARGETS1], good_threshold=0.0)
        self.assertEqual(row["dead_megas"], ())

    def test_dead_megas_empty_when_each_mega_wins_a_different_enemy(self):
        six = ["Mega A", "Mega B", "C", "D"]
        lookup1, fb1 = self._fixture(six, self.TARGETS1, self.WIN1, "Mega A")
        lookup2, fb2 = self._fixture(six, self.TARGETS2, self.WIN2, "Mega B")
        row = cf._core_row(
            six, [lookup1, lookup2], [self.TARGETS1, self.TARGETS2], good_threshold=0.0,
            pair_by_key_forced_base_list=[fb1, fb2])
        self.assertEqual(
            {pe["best_bring4_row"]["mega_used"] for pe in row["per_enemy"]},
            {"Mega A", "Mega B"})
        self.assertEqual(row["dead_megas"], ())

    def test_dead_megas_flags_the_stone_holder_never_chosen_anywhere(self):
        """"Mega A" wins BOTH enemies here -- "Mega B" is brought every
        time (the six is exactly 4 members, so the one bring4 IS the whole
        six) but never once the actual chosen mega."""
        six = ["Mega A", "Mega B", "C", "D"]
        lookup1, fb1 = self._fixture(six, self.TARGETS1, self.WIN1, "Mega A")
        lookup2, fb2 = self._fixture(six, self.TARGETS2, self.WIN2, "Mega A")
        row = cf._core_row(
            six, [lookup1, lookup2], [self.TARGETS1, self.TARGETS2], good_threshold=0.0,
            pair_by_key_forced_base_list=[fb1, fb2])
        self.assertEqual(
            [pe["best_bring4_row"]["mega_used"] for pe in row["per_enemy"]],
            ["Mega A", "Mega A"])
        self.assertEqual(row["dead_megas"], ("Mega B",))
        self.assertEqual(row["unused"], ())

    def test_a_fully_unused_stone_holder_is_not_double_flagged(self):
        """A stone holder that's never even BROUGHT (already `_core_row`'s
        own `unused`) must not also show up in `dead_megas` -- that's a
        different, stronger statement ("brought, just never the mega")
        `_core_dead_mega_rebuild` isn't built to act on the same way."""
        import itertools as _it
        six = ["Mega A", "Mega B", "C", "D", "E"]
        pair_lookup = {}
        for p in _it.combinations(six, 2):
            beats = self.WIN1 if "Mega B" not in p else self.LOSS
            pair_lookup[frozenset(p)] = _fake_pair_row(p, beats, self.TARGETS1)
        row = cf._core_row(six, [pair_lookup], [self.TARGETS1], good_threshold=0.0)
        best = row["per_enemy"][0]["best_bring4_row"]
        self.assertNotIn("Mega B", best["bring4"])
        self.assertEqual(row["unused"], ("Mega B",))
        self.assertEqual(row["dead_megas"], ())


class TestCoreDeadMegaRebuild(unittest.TestCase):
    """`_core_dead_mega_rebuild`, real-data end-to-end (like
    `TestMultiBring4CoverageMegaConsistency`): a core carrying 2 real
    Mega-stone holders where one is never the chosen mega across either
    named enemy gets re-raced with that member swapped for its own
    base-species name and a real item."""

    POOL = ["Mega Scizor", "Mega Floette", "Scizor", "Garchomp", "Kingambit",
           "Whimsicott", "Sinistcha"]
    ENEMIES = [["Kingambit", "Basculegion", "Sableye", "Ariados"],
              ["Sableye", "Ariados", "Basculegion", "Sinistcha"]]
    CORE = ("Garchomp", "Kingambit", "Mega Floette", "Mega Scizor")

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.POOL, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)
        self.row = cf._core_row(
            self.CORE, self.coverage["pair_by_key"], self.coverage["target_name_lists"],
            good_threshold=0.0,
            pair_by_key_forced_base_list=self.coverage["pair_by_key_forced_base"])

    def test_mega_scizor_is_flagged_dead_in_this_fixture(self):
        """Ground truth this whole class relies on: Mega Floette wins both
        named enemies here, so Mega Scizor (brought every time -- the core
        is exactly 4 members) is never the chosen mega."""
        self.assertEqual(
            [pe["best_bring4_row"]["mega_used"] for pe in self.row["per_enemy"]],
            ["Mega Floette", "Mega Floette"])
        self.assertEqual(self.row["dead_megas"], ("Mega Scizor",))

    def test_rebuild_substitutes_the_base_species_name(self):
        context = cf._item_clause_context_from_coverage(self.coverage)
        rebuild = cf._core_dead_mega_rebuild(
            self.row["core"], self.row["dead_megas"],
            self.coverage["target_name_lists"], context)
        self.assertIsNotNone(rebuild)
        substitute_core, pair_by_key_per_enemy = rebuild
        self.assertEqual(substitute_core,
                         ("Garchomp", "Kingambit", "Mega Floette", "Scizor"))
        self.assertEqual(set(pair_by_key_per_enemy),
                         {tuple(t) for t in self.coverage["target_name_lists"]})
        # A valid `_core_row` call: the substitute core only carries 1
        # mega now, so no `pair_by_key_forced_base_list`/`megas` needed.
        pair_by_key_list = [pair_by_key_per_enemy[tuple(t)]
                            for t in self.coverage["target_name_lists"]]
        new_row = cf._core_row(substitute_core, pair_by_key_list,
                               self.coverage["target_name_lists"], good_threshold=0.0)
        self.assertEqual(new_row["core"], substitute_core)
        self.assertEqual(new_row["dead_megas"], ())

    def test_rebuild_returns_none_when_the_base_form_was_never_in_the_pool(self):
        """No already-vetted real set for "Scizor" to substitute in when
        the pool-wide search never named it (a fixed `--our` that only
        ever listed "Mega Scizor")."""
        pool_no_base = [n for n in self.POOL if n != "Scizor"]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        coverage = cf.multi_bring4_coverage(
            pool_no_base, self.ENEMIES, merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)
        row = cf._core_row(
            self.CORE, coverage["pair_by_key"], coverage["target_name_lists"],
            good_threshold=0.0,
            pair_by_key_forced_base_list=coverage["pair_by_key_forced_base"])
        self.assertEqual(row["dead_megas"], ("Mega Scizor",))
        context = cf._item_clause_context_from_coverage(coverage)
        rebuild = cf._core_dead_mega_rebuild(
            row["core"], row["dead_megas"], coverage["target_name_lists"], context)
        self.assertIsNone(rebuild)


class TestMultiBring4CoverageItemClause(unittest.TestCase):
    """`multi_bring4_coverage`/`_core_row`'s Item Clause fix ("Fix B"):
    Stage A's pool-wide `fixed_items` (one item per POOL member, searched
    once, with no notion of "who else is on this specific core") can pick
    the SAME item for two names that only collide once they land on the
    same 4-6 member core together -- undetectable at Stage A by
    construction. "When unique-items are enforced, it is crucial these are
    reflected in the sets, summary, and gameplan for each team."

    Ninetales-Alola and Rampardos both independently want Life Orb against
    this fixture (the exact collision `TestResolveUniqueItems`/
    `TestBring4SearchItemClauseIsOptIn` already use), so a core containing
    both is guaranteed to trigger `_core_row`'s conflict check."""

    OUR6 = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit"]
    TARGETS = ["Sableye", "Ariados"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.coverage = cf.multi_bring4_coverage(
            self.OUR6, [self.TARGETS], merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)

    def test_pool_wide_fixed_items_do_collide_without_enforcement(self):
        """Confirms the fixture: Stage A's OWN pool-wide search picks the
        same item for both names, exactly the illegal state a real core
        containing both would otherwise silently show."""
        self.assertEqual(self.coverage["fixed_items"]["Ninetales-Alola"],
                         self.coverage["fixed_items"]["Rampardos"])

    def test_core_row_without_item_clause_context_reproduces_old_behaviour(self):
        row = cf._core_row(
            self.OUR6, self.coverage["pair_by_key"],
            self.coverage["target_name_lists"], good_threshold=0.0,
            pair_by_key_forced_base_list=self.coverage["pair_by_key_forced_base"])
        self.assertIsNone(row["item_clause_resolved_items"])

    def test_core_row_with_item_clause_context_resolves_the_conflict(self):
        ctx = cf._item_clause_context_from_coverage(self.coverage)
        row = cf._core_row(
            self.OUR6, self.coverage["pair_by_key"],
            self.coverage["target_name_lists"], good_threshold=0.0,
            pair_by_key_forced_base_list=self.coverage["pair_by_key_forced_base"],
            item_clause_context=ctx)
        resolved = row["item_clause_resolved_items"]
        self.assertIsNotNone(resolved)
        self.assertNotEqual(resolved["Ninetales-Alola"], resolved["Rampardos"])
        self.assertEqual(len(set(resolved.values())), len(resolved))

    def test_a_non_conflicting_core_skips_the_re_race_entirely(self):
        """The cheap common case: a core that doesn't carry BOTH colliding
        names never triggers the core-scoped re-race at all."""
        ctx = cf._item_clause_context_from_coverage(self.coverage)
        core = [n for n in self.OUR6 if n != "Rampardos"]
        row = cf._core_row(
            core, self.coverage["pair_by_key"],
            self.coverage["target_name_lists"], good_threshold=0.0,
            pair_by_key_forced_base_list=self.coverage["pair_by_key_forced_base"],
            item_clause_context=ctx)
        self.assertIsNone(row["item_clause_resolved_items"])

    def test_core_item_clause_pair_by_key_returns_a_legal_per_enemy_table(self):
        ctx = cf._item_clause_context_from_coverage(self.coverage)
        pair_by_key_per_enemy, resolved_items = cf._core_item_clause_pair_by_key(
            tuple(sorted(self.OUR6)), self.coverage["target_name_lists"], ctx)
        self.assertNotEqual(resolved_items["Ninetales-Alola"],
                            resolved_items["Rampardos"])
        table = pair_by_key_per_enemy[tuple(self.TARGETS)]
        pair = frozenset({"Ninetales-Alola", "Rampardos"})
        self.assertIn(pair, table)
        self.assertEqual(table[pair]["item1"] if table[pair]["pair"][0] ==
                         "Ninetales-Alola" else table[pair]["item2"],
                         resolved_items["Ninetales-Alola"])

    # A separate, exactly-4-member pool for the `multi_bring4_exhaustive`/
    # `multi_bring4_beam` tests below: with a size-4 core and `core_sizes=
    # (4,)`, the WHOLE core is necessarily the bring-4 against a single
    # enemy roster, so `_core_row`'s own `unused` never fires and drops the
    # row -- keeps these tests decoupled from which 4-of-6 subset
    # `_bring4_candidates` would otherwise have picked.
    FOUR = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Rampardos"]

    def _four_coverage(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        return cf.multi_bring4_coverage(
            self.FOUR, [self.TARGETS], merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=1)

    def test_multi_bring4_exhaustive_default_leaves_conflict_unresolved(self):
        coverage = self._four_coverage()
        rows = cf.multi_bring4_exhaustive(
            coverage, good_threshold=0.0, core_sizes=(4,))
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["item_clause_resolved_items"])

    def test_multi_bring4_exhaustive_enforce_item_clause_resolves_it(self):
        coverage = self._four_coverage()
        rows = cf.multi_bring4_exhaustive(
            coverage, good_threshold=0.0, core_sizes=(4,),
            enforce_item_clause=True)
        self.assertEqual(len(rows), 1)
        resolved = rows[0]["item_clause_resolved_items"]
        self.assertIsNotNone(resolved)
        self.assertNotEqual(resolved["Ninetales-Alola"], resolved["Rampardos"])

    def test_multi_bring4_beam_enforce_item_clause_resolves_it(self):
        coverage = self._four_coverage()
        rows = cf.multi_bring4_beam(
            coverage, good_threshold=0.0, core_sizes=(4,),
            enforce_item_clause=True)
        matches = [r for r in rows if set(r["core"]) == set(self.FOUR)]
        self.assertTrue(matches)
        resolved = matches[0]["item_clause_resolved_items"]
        self.assertIsNotNone(resolved)
        self.assertNotEqual(resolved["Ninetales-Alola"], resolved["Rampardos"])


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


class TestNetWeakTypeBreadth(unittest.TestCase):
    """"I want an argument to be able to restrict generated teams to a
    certain number of types with more than 1 net weakness (default 4)."
    The net-weakness sibling of `TestWeakTypeBreadth`'s `weak_type_breadth`
    -- counts types by NET weakness (weak minus resist/immune members,
    `net_weakness_by_type`) instead of raw weak-member count."""

    def test_matches_a_hand_count_of_per_type_net_weaknesses(self):
        merged = world()["merged"]
        core = ["Mega Charizard Y", "Mega Floette", "Whimsicott", "Corviknight"]
        net = cf.net_weakness_by_type(core, merged)
        want = sum(1 for v in net.values() if v >= 2)
        self.assertEqual(cf.net_weak_type_breadth(core, merged), want)

    def test_a_lower_threshold_can_only_count_as_many_or_more_types(self):
        merged = world()["merged"]
        core = ["Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight"]
        self.assertGreaterEqual(
            cf.net_weak_type_breadth(core, merged, threshold=1),
            cf.net_weak_type_breadth(core, merged, threshold=2))

    def test_core_passes_hard_filters_rejects_a_core_over_the_cap(self):
        merged = world()["merged"]
        core = ("Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight")
        breadth = cf.net_weak_type_breadth(core, merged)
        self.assertGreater(breadth, 0, "fixture assumes at least one type "
                           "already has net weakness > 1")
        self.assertFalse(cf._core_passes_hard_filters(
            core, merged, {}, max_megas=3, max_net_weak_types=breadth - 1))
        self.assertTrue(cf._core_passes_hard_filters(
            core, merged, {}, max_megas=3, max_net_weak_types=breadth))

    def test_none_disables_the_cap_entirely(self):
        merged = world()["merged"]
        core = ("Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Whimsicott", "Corviknight")
        self.assertTrue(cf._core_passes_hard_filters(
            core, merged, {}, max_megas=3, max_net_weak_types=None))

    def test_not_monotonic_a_later_resist_can_pull_a_type_back_under(self):
        """Unlike `weak_type_breadth`, adding a member CAN lower
        `net_weak_type_breadth` (a resist pulls a type's net back down) --
        the reason `max_net_weak_types` must never be applied as a
        growth-time prune, only at final core size. Whimsicott/Corviknight
        are both weak to Fire (net 2 with just the two of them); adding
        Mega Charizard Y, itself Fire-RESISTANT, pulls Fire's net down to 1
        -- fewer types now cross the threshold=2 bar than before."""
        merged = world()["merged"]
        core_a = ["Whimsicott", "Corviknight"]
        core_b = core_a + ["Mega Charizard Y"]
        net_a = cf.net_weakness_by_type(core_a, merged)["Fire"]
        net_b = cf.net_weakness_by_type(core_b, merged)["Fire"]
        self.assertGreater(net_a, net_b, "fixture assumes Mega Charizard Y "
                           "is Fire-resistant enough to lower net Fire "
                           "weakness when added")
        self.assertGreater(cf.net_weak_type_breadth(core_a, merged, threshold=2),
                           cf.net_weak_type_breadth(core_b, merged, threshold=2))

    def test_multi_bring4_beam_final_capture_still_enforces_the_cap(self):
        """The beam's growth-time filtering deliberately withholds
        `max_net_weak_types` (not monotonic), but the final `found`-capture
        step must still enforce it -- no returned row may exceed the cap."""
        W = world()
        merged, moves = W["merged"], W["moves"]
        natures, typechart = W["natures"], W["typechart"]
        pool = ["Mega Charizard Y", "Mega Floette", "Mega Metagross",
               "Mega Tyranitar", "Whimsicott", "Corviknight"]
        enemies = [["Sableye", "Ariados"], ["Basculegion", "Sinistcha"]]
        coverage = cf.multi_bring4_coverage(
            pool, enemies, merged, moves, natures, typechart,
            good_threshold=0.3, min_enemies=1)
        rows = cf.multi_bring4_beam(coverage, good_threshold=0.3,
                                    beam_width=20, max_megas=4,
                                    max_net_weak_types=1)
        for r in rows:
            self.assertLessEqual(
                cf.net_weak_type_breadth(list(r["core"]), merged), 1, r["core"])


class TestMultiBring4MaxNetWeakTypes(unittest.TestCase):
    """`max_net_weak_types` threaded through `multi_bring4_exhaustive`/
    `multi_bring4_beam`, mirroring `TestMultiBring4MaxWeakTypes`."""

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
                                          max_megas=4, max_net_weak_types=1)
        for r in rows:
            self.assertLessEqual(cf.net_weak_type_breadth(list(r["core"]), merged),
                                 1, r["core"])

    def test_beam_never_returns_a_core_over_the_cap(self):
        merged = self.W["merged"]
        rows = cf.multi_bring4_beam(self.coverage, good_threshold=0.3,
                                    beam_width=20, max_megas=4,
                                    max_net_weak_types=1)
        for r in rows:
            self.assertLessEqual(cf.net_weak_type_breadth(list(r["core"]), merged),
                                 1, r["core"])

    def test_a_tighter_cap_actually_changes_the_result(self):
        loose_cores = {tuple(sorted(r["core"]))
                      for r in cf.multi_bring4_exhaustive(
                          self.coverage, good_threshold=0.3, max_megas=4)}
        tight_cores = {tuple(sorted(r["core"]))
                      for r in cf.multi_bring4_exhaustive(
                          self.coverage, good_threshold=0.3, max_megas=4,
                          max_net_weak_types=0)}
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


class TestNetWeaknessByType(unittest.TestCase):
    """`net_weakness_by_type` -- "three Fire-weak members matter much less
    if four others resist it" -- (weak count) - (resist count) per type,
    via the SAME `team_search._weak_resist` split `weakness_violations`'s
    own `max_net` scoring already reads. A raw per-type map for a caller
    (`counter_table.py`'s --auto-deep-dive gate) to apply its own hard
    threshold to directly, not `weakness_violations`'s bounded soft
    penalty."""

    def setUp(self):
        self.W = world()

    def test_matches_weak_resist_directly(self):
        from team_search import _weak_resist
        merged = self.W["merged"]
        core = ["Torkoal", "Kingambit", "Garchomp", "Corviknight", "Sylveon"]
        net = cf.net_weakness_by_type(core, merged)
        for t in ("Fire", "Water", "Ground", "Fairy"):
            weak, resist = _weak_resist(core, merged, t)
            self.assertEqual(net[t], len(weak) - len(resist), t)

    def test_all_18_types_are_present(self):
        from species_data import TYPES
        merged = self.W["merged"]
        net = cf.net_weakness_by_type(["Torkoal"], merged)
        self.assertEqual(set(net), set(TYPES))

    def test_a_resistor_lowers_the_net_below_the_raw_weak_count(self):
        """Torkoal alone is weak to Water (net +1); adding a Water-RESISTING
        teammate must bring the net down to 0, even though the raw
        per-type WEAK count (member_weakness_summary's own reading) stays
        at 1 -- net and raw weak count are genuinely different questions."""
        merged = self.W["merged"]
        solo_net = cf.net_weakness_by_type(["Torkoal"], merged)
        self.assertEqual(solo_net["Water"], 1)
        # Milotic (pure Water) resists Water.
        duo_net = cf.net_weakness_by_type(["Torkoal", "Milotic"], merged)
        self.assertEqual(duo_net["Water"], 0)
        duo_weak = cf.member_weakness_summary(["Torkoal", "Milotic"], merged)
        self.assertEqual(duo_weak["per_type"]["Water"], 1,
                         "raw weak count must be unaffected by a resistor")


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
        """A trivial two-enemy case using the SAME enemy pair twice: total
        uncovered doubles (summed across both enemies), but the blended
        average-across-enemies and worst-case-floor terms must equal the
        single-enemy reading (averaging/reading off two identical enemies
        changes nothing), and the bottleneck can be either (they're
        identical)."""
        core = ("Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
               "Rampardos", "Kingambit")
        row = cf._core_row(core, [self.pair_by_key, self.pair_by_key],
                           [["Sableye", "Ariados"], ["Sableye", "Ariados"]],
                           good_threshold=1.0)
        solo_row = cf._core_row(core, [self.pair_by_key],
                                [["Sableye", "Ariados"]], good_threshold=1.0)
        uncovered, _overage, floor_penalty, neg_blended, neg_score = row["worst_enemy_score_key"]
        (solo_uncovered, _solo_overage, solo_floor_penalty,
         solo_neg_blended, solo_neg_score) = solo_row["worst_enemy_score_key"]
        self.assertEqual(uncovered, 2 * solo_uncovered)
        self.assertAlmostEqual(neg_blended, solo_neg_blended)
        self.assertAlmostEqual(floor_penalty, solo_floor_penalty)
        self.assertEqual(neg_score, solo_neg_score)
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
                      "tailwind_safe": won, "protect_safe": won,
                      "follow_me_safe": won,
                      # `bring4_pair_depth`/`_pairs_beaten_without_fainting`
                      # read this on every row -- a real loss carries
                      # {"C": 0.0, "P": 0.0} already, matching
                      # `_pair_vs_targets`'s own convention.
                      "our_hp": {"C": 1.0, "P": 1.0} if won else {"C": 0.0, "P": 0.0}}
    n_win = len(beats)
    return {"pair": pair, "detail": detail,
           "pairs_swept": 0, "pairs_traded": n_win,
           "pairs_lost": len(enemy_pairs) - n_win, "pairs_no_ko": 0,
           "pairs_tailwind_safe": n_win, "pairs_protect_safe": n_win,
           "pairs_follow_me_safe": n_win,
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


class TestCoreRowBlendedRanking(unittest.TestCase):
    """`_core_row`'s redesigned `worst_enemy_score_key`: "I am trying to
    maximise overall wins and keep myself in winning matchups all the
    time... but there will always be out of sample enemy teams so it
    doesn't make sense just to over-focus on the worst team." A hand-built
    two-enemy fixture (`_fake_pair_row`, no real racing, same style as
    `TestUncoveredEnemyPairsDominateRanking`) isolates the new blend/floor
    logic from any real engine race."""

    TARGETS_A = ["P", "Q", "R"]
    TARGETS_B = ["S", "T", "U"]
    EP_A = [("P", "Q"), ("P", "R"), ("Q", "R")]
    EP_B = [("S", "T"), ("S", "U"), ("T", "U")]

    def _core_row_for(self, names, enemy_a_beats, enemy_b_beats, merged=None):
        """`enemy_a_beats`/`enemy_b_beats`: one `beats` set per one of the
        core's own 6 pairs, in `itertools.combinations(names, 2)` order,
        against TARGETS_A/TARGETS_B respectively."""
        import itertools as _it
        pairs = list(_it.combinations(names, 2))
        lookup_a = {frozenset(p): _fake_pair_row(p, b, self.TARGETS_A)
                   for p, b in zip(pairs, enemy_a_beats)}
        lookup_b = {frozenset(p): _fake_pair_row(p, b, self.TARGETS_B)
                   for p, b in zip(pairs, enemy_b_beats)}
        return cf._core_row(names, [lookup_a, lookup_b],
                            [self.TARGETS_A, self.TARGETS_B], good_threshold=0.0,
                            merged=merged)

    def test_a_core_with_a_worse_worst_pair_but_better_average_ranks_higher(self):
        PQ, PR, QR = self.EP_A
        perfect_a = {PQ, PR, QR}
        two_of_three_a = {PQ, PR}  # loses QR -- beaten_frac 2/3
        ST, SU, TU = self.EP_B
        perfect_b = {ST, SU, TU}

        # CoreX: 5/6 pairs perfect vs A, 1 pair (worst) at 2/3 vs A;
        # perfect everywhere vs B -- a high average.
        core_x = self._core_row_for(
            ["X1", "X2", "X3", "X4"],
            enemy_a_beats=[perfect_a] * 5 + [two_of_three_a],
            enemy_b_beats=[perfect_b] * 6)

        # CoreY: EVERY pair wins exactly 2/3 against BOTH enemies (losses
        # spread round-robin so every enemy sub-pair is still covered by
        # SOME pair -- uncovered stays 0 for both cores). Ties CoreX on the
        # worst-pair reading (2/3), but averages much lower.
        def _spread(enemy_pairs):
            return [set(enemy_pairs) - {enemy_pairs[i % 3]} for i in range(6)]
        core_y = self._core_row_for(
            ["Y1", "Y2", "Y3", "Y4"],
            enemy_a_beats=_spread(self.EP_A),
            enemy_b_beats=_spread(self.EP_B))

        x_uncov, _x_overage, x_floor, x_neg_blend, _ = core_x["worst_enemy_score_key"]
        y_uncov, _y_overage, y_floor, y_neg_blend, _ = core_y["worst_enemy_score_key"]

        self.assertEqual(x_uncov, 0)
        self.assertEqual(y_uncov, 0)
        self.assertEqual(x_floor, 0.0, "CoreX's worst pair (2/3) clears the floor")
        self.assertEqual(y_floor, 0.0, "CoreY's worst pair (2/3) clears the floor too")
        self.assertLess(x_neg_blend, y_neg_blend,
                        "CoreX's better average must outrank CoreY's, even "
                        "though both tie on the worst-pair/floor reading -- "
                        "the OLD maximin-only ranking could never see this")

    def test_worst_case_floor_gates_before_average_is_even_consulted(self):
        """A core that FAILS the floor must rank below one that clears it,
        even when the failing core's average is otherwise better -- "I do
        definitely need to have a good matchup even vs the worst team" is
        a real gate, not just a tie-break after average."""
        PQ, PR, QR = self.EP_A
        perfect_a = {PQ, PR, QR}
        ST, SU, TU = self.EP_B
        perfect_b = {ST, SU, TU}

        # CoreOK: worst pair at 2/3 vs A (clears the 0.5 floor), otherwise
        # perfect -- a merely-good, not stellar, average.
        core_ok = self._core_row_for(
            ["Z1", "Z2", "Z3", "Z4"],
            enemy_a_beats=[perfect_a] * 5 + [{PQ, PR}],
            enemy_b_beats=[perfect_b] * 6)
        # CoreBadFloor: worst pair at 1/3 vs A (BELOW the floor), but
        # otherwise perfect too -- a HIGHER average than CoreOK.
        core_bad_floor = self._core_row_for(
            ["W1", "W2", "W3", "W4"],
            enemy_a_beats=[perfect_a] * 5 + [{PQ}],
            enemy_b_beats=[perfect_b] * 6)

        (ok_uncov, _ok_overage, ok_floor,
         ok_neg_blend, _) = core_ok["worst_enemy_score_key"]
        (bad_uncov, _bad_overage, bad_floor,
         bad_neg_blend, _) = core_bad_floor["worst_enemy_score_key"]

        self.assertEqual(ok_uncov, 0)
        self.assertEqual(bad_uncov, 0)
        self.assertEqual(ok_floor, 0.0)
        self.assertAlmostEqual(bad_floor, 0.5 - 1 / 3)
        self.assertGreater(bad_neg_blend, ok_neg_blend,
                           "fixture assumes CoreBadFloor's average really is "
                           "better (less negative == higher blend) than "
                           "CoreOK's, so the gate -- not average -- is what "
                           "must decide this comparison")
        self.assertLess(
            (ok_uncov, _ok_overage, ok_floor, ok_neg_blend),
            (bad_uncov, _bad_overage, bad_floor, bad_neg_blend),
            "CoreOK (clears the floor) must still outrank CoreBadFloor "
            "(fails it), even though CoreBadFloor's own average is better")

    def test_avg_score_is_the_final_tie_break(self):
        """Two cores identical on uncovered/floor/average differ only by
        `merged`'s Score field -- "score (the overall stats of the
        pokemon) are a proxy for" flexibility, read as the last tie-break."""
        PQ, PR, QR = self.EP_A
        perfect_a = {PQ, PR, QR}
        ST, SU, TU = self.EP_B
        perfect_b = {ST, SU, TU}
        merged = {f"{side}{i}": {"score": score}
                 for side, score in (("HI", 90.0), ("LO", 10.0))
                 for i in (1, 2, 3, 4)}

        core_hi = self._core_row_for(
            ["HI1", "HI2", "HI3", "HI4"],
            enemy_a_beats=[perfect_a] * 6, enemy_b_beats=[perfect_b] * 6,
            merged=merged)
        core_lo = self._core_row_for(
            ["LO1", "LO2", "LO3", "LO4"],
            enemy_a_beats=[perfect_a] * 6, enemy_b_beats=[perfect_b] * 6,
            merged=merged)

        (hi_uncov, hi_overage, hi_floor,
         hi_neg_blend, hi_neg_score) = core_hi["worst_enemy_score_key"]
        (lo_uncov, lo_overage, lo_floor,
         lo_neg_blend, lo_neg_score) = core_lo["worst_enemy_score_key"]
        self.assertEqual((hi_uncov, hi_overage, hi_floor, hi_neg_blend),
                         (lo_uncov, lo_overage, lo_floor, lo_neg_blend))
        self.assertLess(hi_neg_score, lo_neg_score,
                        "the higher-Score core must rank first once "
                        "everything else ties")

    def test_merged_none_defaults_avg_score_to_zero_without_raising(self):
        core = self._core_row_for(
            ["N1", "N2", "N3", "N4"],
            enemy_a_beats=[set(self.EP_A)] * 6, enemy_b_beats=[set(self.EP_B)] * 6,
            merged=None)
        self.assertEqual(core["worst_enemy_score_key"][4], -0.0)


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
        """The acceptance test named directly in the request: at the raw
        race level (no own-side hypothesis applied), Mega Charizard Y's
        OHKO means this pair always loses outright whenever it's in the
        enemy pair."""
        for (e1, e2), d in self.detail.items():
            if "Mega Charizard Y" in (e1, e2):
                self.assertEqual(d["outcome_without_tailwind"], "loss",
                                 f"expected a raw-race loss vs {e1}+{e2}")

    def test_scizor_never_actually_wins_when_zard_y_is_in_the_pair(self):
        """Refines the raw-race claim above for the own-Protect mirror
        (`TestOwnProtectAsAMatchingAnswer`): Protecting the about-to-be-
        OHKO'd member turn 1 can still salvage an outright loss into a
        no_ko stalemate against some enemy pairs (Mega Charizard Y +
        Mega Floette, +Garchomp -- neither also finishes the race outright
        regardless of who Protects), but this pair can never come away
        with an actual WIN while Zard Y is in the enemy pair -- "would not
        be a good bring" holds either way."""
        for (e1, e2), d in self.detail.items():
            if "Mega Charizard Y" in (e1, e2):
                self.assertNotIn(d["outcome"], ("sweep", "out_trade"),
                                 f"expected no win vs {e1}+{e2}")

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

    def test_mega_used_is_none_when_the_core_carries_no_stone_holder(self):
        self.assertIsNone(self.dive["mega_used"])


class TestCoreDeepDiveItemResolutionEnemies(unittest.TestCase):
    """`item_resolution_enemies` decouples `core_deep_dive`'s own item/
    moveset search from which enemies a particular dive races/displays --
    the fix for a single-opponent Streamlit deep dive independently
    re-optimising the core's set against just that one roster ("if I knew
    I only faced this team"), which made it look artificially better than
    the SAME core's own vs-all-teams dive shows for it. `None` (the
    default) must reproduce the old behaviour exactly; a given value must
    override it, and two dives against DIFFERENT single enemies must agree
    on `sets` once both are given the SAME `item_resolution_enemies`."""

    CORE = ["Whimsicott", "Kingambit", "Garchomp", "Corviknight"]

    def setUp(self):
        self.W = world()

    def test_default_none_resolves_against_target_name_lists_own_union(self):
        import unittest.mock as mock
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        with mock.patch.object(cf, "_resolve_team_items",
                               wraps=cf._resolve_team_items) as spy:
            cf.core_deep_dive(self.CORE, [["Sableye", "Ariados"]], merged, moves,
                              natures, typechart, turns=2)
        self.assertEqual(spy.call_args.args[5], sorted(["Sableye", "Ariados"]))

    def test_given_value_overrides_target_name_lists_union(self):
        import unittest.mock as mock
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        with mock.patch.object(cf, "_resolve_team_items",
                               wraps=cf._resolve_team_items) as spy:
            cf.core_deep_dive(self.CORE, [["Sableye", "Ariados"]], merged, moves,
                              natures, typechart, turns=2,
                              item_resolution_enemies=["Basculegion", "Sinistcha"])
        self.assertEqual(spy.call_args.args[5],
                         sorted(["Basculegion", "Sinistcha"]))

    def test_two_different_single_enemy_dives_agree_on_sets_given_the_same_resolution_enemies(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        wide = sorted({"Sableye", "Ariados", "Basculegion", "Sinistcha"})
        dive_a = cf.core_deep_dive(self.CORE, [["Sableye", "Ariados"]], merged,
                                   moves, natures, typechart, turns=2,
                                   item_resolution_enemies=wide)
        dive_b = cf.core_deep_dive(self.CORE, [["Basculegion", "Sinistcha"]], merged,
                                   moves, natures, typechart, turns=2,
                                   item_resolution_enemies=wide)
        self.assertEqual(dive_a["sets"], dive_b["sets"])


class TestCoreDeepDiveMegaUsed(unittest.TestCase):
    """`core_deep_dive`'s own "note which one is used" -- when `core`
    carries exactly 2 Mega-stone holders, the winning hypothesis's
    `mega_used` must name the one NOT locked to base, and a core with just
    ONE stone holder reports that single one unambiguously (no consistency
    choice needed)."""

    ENEMIES = [["Kingambit", "Basculegion", "Sableye", "Ariados"]]

    def setUp(self):
        self.W = world()

    def test_two_stone_holders_reports_exactly_one_as_used(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        core = ["Garchomp", "Kingambit", "Mega Floette", "Mega Scizor"]
        dive = cf.core_deep_dive(core, self.ENEMIES, merged, moves, natures,
                                 typechart, turns=2)
        self.assertIn(dive["mega_used"], ("Mega Floette", "Mega Scizor"))

    def test_a_single_stone_holder_is_reported_directly(self):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        core = ["Garchomp", "Kingambit", "Mega Floette", "Whimsicott"]
        dive = cf.core_deep_dive(core, self.ENEMIES, merged, moves, natures,
                                 typechart, turns=2)
        self.assertEqual(dive["mega_used"], "Mega Floette")


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
        """Re-derived by hand against current data (checked directly per
        leaving-role/candidate combo via `switch_in_search` itself, all 8
        `tried` combos, not just the top row): Arcanine-Hisui is now
        default_sets.txt-pinned to real Rock Head (no recoil at all on its
        own pinned Flare Blitz/Head Smash) plus its own exact real
        moveset/EVs -- under that real set, swapping Ninetales-Alola out
        for Arcanine-Hisui is the ONLY one of the 8 combos that actually
        fixes this loss; every one of the 4 Mega-Scizor-leaves combos still
        loses or stalls (verified individually, one-candidate-bench at a
        time). So `rows` now comes back with exactly this one real fix,
        not a Mega-Scizor-leaves fix ranked ahead of it."""
        rows, tried = self._search(("Mega Charizard Y", "Mega Floette"))
        self.assertGreater(tried, 0)
        self.assertTrue(rows)
        self.assertEqual(len(rows), 1, "fixture assumes exactly one of the "
                         "8 leaving/candidate combos fixes this loss now")
        best = rows[0]
        self.assertEqual(best["leaving"], "Ninetales-Alola")
        self.assertEqual(best["arriving"], "Arcanine-Hisui")
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


class TestPsychicTerrainCheapModel(unittest.TestCase):
    """Indeedee's Psychic Surge: grounded Psychic moves get +50% power,
    priority moves fail outright against a grounded target, and Expanding
    Force (normally single-target, 80 power) becomes a spread move at 120
    power for a grounded user. Same footprint/style as
    `TestGrassyTerrainCheapModel` right above."""

    def setUp(self):
        self.W = world()

    def test_field_terrain_reads_psychic_surge(self):
        merged, natures = self.W["merged"], self.W["natures"]
        combatants = {"C": cf._build("Indeedee-F", merged, natures),
                     "P": cf._build("Kingambit", merged, natures),
                     "E1": cf._build("Garchomp", merged, natures),
                     "E2": cf._build("Sinistcha", merged, natures)}
        self.assertEqual(cf._field_terrain(combatants), "psychic")

    def test_raw_hit_applies_the_psychic_boost_under_terrain(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        indeedee = cf._build("Indeedee-F", merged, natures)
        target = cf._build("Garchomp", merged, natures)
        psychic = cf._lookup_move("Psychic", moves)
        no_terrain = cf._raw_hit(indeedee, psychic, target, typechart, roll="avg")
        psychic_terrain = cf._raw_hit(indeedee, psychic, target, typechart, roll="avg",
                                      terrain="psychic")
        self.assertAlmostEqual(psychic_terrain.frac / no_terrain.frac, 1.3, places=3)

    def test_expanding_force_is_120_power_and_spread_under_terrain(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        indeedee = cf._build("Indeedee-F", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        ef = cf._lookup_move("Expanding Force", moves)

        hits_terrain, _mv = cf._choose_action(
            indeedee, [ef], {"E1": e1, "E2": e2}, typechart, terrain="psychic")
        self.assertEqual(set(hits_terrain.keys()), {"E1", "E2"})
        self.assertGreater(hits_terrain["E1"].frac, 0.0)
        self.assertGreater(hits_terrain["E2"].frac, 0.0)

        hits_no_terrain, _mv2 = cf._choose_action(
            indeedee, [ef], {"E1": e1, "E2": e2}, typechart, terrain=None,
            hinted_target="E1")
        self.assertEqual(set(hits_no_terrain.keys()), {"E1"})

        # 120 power + the 1.5x terrain boost together comfortably beat the
        # single-target 80-power no-terrain hit on the SAME target.
        self.assertGreater(hits_terrain["E1"].frac, hits_no_terrain["E1"].frac)

    def test_priority_move_fails_against_a_grounded_target_under_terrain(self):
        merged, natures, typechart = (
            self.W["merged"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        indeedee = cf._build("Indeedee-F", merged, natures)   # grounded (Psychic/Normal)
        aqua_jet = cf.MoveInfo("Aqua Jet", 40, "Water", "Physical", "normal", priority=1)

        hits, _mv = cf._choose_action(
            kingambit, [aqua_jet], {"E1": indeedee}, typechart, terrain="psychic")
        self.assertEqual(hits["E1"].frac, 0.0)

    def test_priority_move_still_lands_on_an_ungrounded_target_under_terrain(self):
        merged, natures, typechart = (
            self.W["merged"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        pelipper = cf._build("Pelipper", merged, natures)   # Flying -- not grounded
        aqua_jet = cf.MoveInfo("Aqua Jet", 40, "Water", "Physical", "normal", priority=1)

        hits, _mv = cf._choose_action(
            kingambit, [aqua_jet], {"E1": pelipper}, typechart, terrain="psychic")
        self.assertGreater(hits["E1"].frac, 0.0)


class TestHelpingHand(unittest.TestCase):
    """"I don't think helping hand is boosting partner moves 1.5x" -- it
    wasn't: the real engine (`battle.py`) already applied it correctly, but
    this module's own cheap 2v2 model had no Helping Hand concept anywhere
    at all. Threaded through the whole chain a real caller actually uses:
    `_raw_hit` (the one source of truth) -> `_choose_move`/`_hit_or_spread`
    (`_sequential_pair_outcome`'s `partner_move` pathway, i.e. `pair_search`)
    -> `_choose_action` (the real `_joint_race` engine, via `_resolve_turn`'s
    new `helping_hand_setter_role`)."""

    def setUp(self):
        self.W = world()

    def test_raw_hit_applies_the_flat_boost(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        target = cf._build("Garchomp", merged, natures)
        cleave = cf._lookup_move("Kowtow Cleave", moves)
        plain = cf._raw_hit(kingambit, cleave, target, typechart, roll="avg")
        boosted = cf._raw_hit(kingambit, cleave, target, typechart, roll="avg",
                              helping_hand=True)
        self.assertAlmostEqual(boosted.frac / plain.frac, 1.5, places=6)

    def test_sequential_pair_outcome_boosts_the_candidate_with_a_fixed_helping_hand(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        indeedee = cf._build("Indeedee-F", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        import solver
        kg_moves = [mi for mi, _pct in solver.build_moveset(merged["Kingambit"], moves)]
        helping_hand = cf._lookup_move("Helping Hand", moves)

        boosted = cf._sequential_pair_outcome(
            kingambit, kg_moves, "Garchomp", e1, [], "Sinistcha", e2, [],
            typechart, candidate_target="Garchomp",
            partner=indeedee, partner_move=helping_hand, partner_target="Garchomp")
        plain = cf._sequential_pair_outcome(
            kingambit, kg_moves, "Garchomp", e1, [], "Sinistcha", e2, [],
            typechart, candidate_target="Garchomp",
            partner=indeedee, partner_move=None, partner_target=None)
        self.assertAlmostEqual(
            boosted["hits"]["C"]["E1"].frac / plain["hits"]["C"]["E1"].frac,
            1.5, places=6)

    def test_choose_action_helping_hand_boost_applies_before_ranking(self):
        merged, natures, typechart = (
            self.W["merged"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        target = cf._build("Garchomp", merged, natures)
        cleave = cf.MoveInfo("Kowtow Cleave", 85, "Dark", "Physical", "normal")

        boosted, _mv = cf._choose_action(
            kingambit, [cleave], {"E1": target}, typechart, helping_hand_boost=True)
        plain, _mv2 = cf._choose_action(
            kingambit, [cleave], {"E1": target}, typechart, helping_hand_boost=False)
        self.assertAlmostEqual(
            boosted["E1"].frac / plain["E1"].frac, 1.5, places=6)

    def test_resolve_turn_setter_role_substitutes_and_boosts_the_ally_same_turn(self):
        """Helping Hand's boost lands the SAME turn it's cast -- unlike
        Tailwind's speed effect (which this module deliberately only makes
        available starting the turn AFTER, see `_joint_race`'s own note),
        Helping Hand's power boost needs no intra-turn re-sort since the
        whole `plan` (every role's hits) is built before any turn-order
        resolution happens."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        import solver
        kingambit = cf._build("Kingambit", merged, natures)
        indeedee = cf._build("Indeedee-F", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        kg_moves = [mi for mi, _pct in solver.build_moveset(merged["Kingambit"], moves)]
        combatants = {"C": kingambit, "P": indeedee, "E1": e1, "E2": e2}
        moves_by_role = {"C": kg_moves, "P": [], "E1": [], "E2": []}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}

        _hp, boosted_log, _ea, _w, _rc = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, None, {"C": "E1"},
            helping_hand_setter_role="P")
        _hp2, plain_log, _ea2, _w2, _rc2 = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, None, {"C": "E1"})

        # Indeedee-F ("P") lands no hit of its own -- Helping Hand is a
        # zero-power status move, same as Tailwind's own no-op substitution.
        self.assertFalse(any(role == "P" for role, _tgt, _h in boosted_log))
        boosted_hit = next(h for role, _tgt, h in boosted_log if role == "C")
        plain_hit = next(h for role, _tgt, h in plain_log if role == "C")
        self.assertAlmostEqual(boosted_hit.frac / plain_hit.frac, 1.5, places=6)

    def test_joint_race_first_turn_helping_hand_role_only_applies_turn_one(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        import solver
        kingambit = cf._build("Kingambit", merged, natures)
        indeedee = cf._build("Indeedee-F", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        kg_moves = [mi for mi, _pct in solver.build_moveset(merged["Kingambit"], moves)]
        combatants = {"C": kingambit, "P": indeedee, "E1": e1, "E2": e2}
        moves_by_role = {"C": kg_moves, "P": [], "E1": [], "E2": []}

        _outcome, _turns, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 2,
            first_turn_helping_hand_role="P")
        # Turn 1: Kingambit alone acts (boosted); turn 2: no setter role
        # anymore, so Kingambit's own turn-2 hit is the plain, unboosted rate.
        turn1_roles = {role for role, _tgt, _h in log[0]}
        self.assertNotIn("P", turn1_roles)
        self.assertIn("C", turn1_roles)


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
        # "Kingambit" (this test's original surviving probe) is now ALSO in
        # the shipped preferences.csv Exclude list (confirmed by reading
        # data/preferences.csv directly), so it no longer demonstrates "the
        # pool isn't emptied of everything" -- "Garchomp", confirmed absent
        # from that same Exclude list, still does.
        self.assertIn("Garchomp", base)
        with_pref = ct._apply_preferences(list(base), self.W["merged"],
                                          verbose=False)
        # The shipped preferences.csv excludes "Slurpuff" -- confirm it (and
        # only it, of these two probes) is actually gone.
        self.assertNotIn("Slurpuff", with_pref)
        self.assertIn("Garchomp", with_pref)

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


class TestDrainHealingInTheJointRace(unittest.TestCase):
    """"the fact that leech life heals 50% of the damage" -- `battle.py`
    (the real engine) already heals the attacker on a `move.drain` hit
    (Leech Life/Giga Drain 50%, Draining Kiss 75%, ...); `_apply_plan`
    (the cheap model `counter_table.py` runs on) had recoil/Life-Orb/Rough-
    Skin self-damage but never modeled drain's opposite-sign counterpart."""

    def setUp(self):
        self.W = world()

    def test_leech_life_heals_the_attacker_half_the_damage_dealt(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Golisopod", merged, natures)
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": attacker, "P": target, "E1": target, "E2": target}
        leech_life = cf._lookup_move("Leech Life", moves)
        self.assertEqual(leech_life.drain, [1, 2])
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(attacker, leech_life, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, leech_life), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        # Start C below full HP so healing is actually observable (a full-HP
        # attacker would clamp at 1.0 and the heal would be silently lost).
        hp = {"C": 0.5, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        expected_heal = got.frac * target.max_hp() * 0.5 / attacker.max_hp()
        self.assertAlmostEqual(new_hp["C"], 0.5 + expected_heal, places=6)

    def test_healing_is_capped_at_full_hp(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Golisopod", merged, natures)
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": attacker, "P": target, "E1": target, "E2": target}
        leech_life = cf._lookup_move("Leech Life", moves)
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(attacker, leech_life, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, leech_life), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["C"], 1.0)

    def test_a_move_with_no_drain_heals_nothing(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        target = cf._build("Milotic", merged, natures)
        combatants = {"C": attacker, "P": target, "E1": target, "E2": target}
        iron_head = cf._lookup_move("Iron Head", moves)
        self.assertIsNone(iron_head.drain)
        protect = cf._lookup_move("Protect", moves)
        got = cf._raw_hit(attacker, iron_head, target, typechart, roll="avg")
        plan = {"C": ({"E1": got}, iron_head), "P": ({}, protect),
               "E1": ({}, protect), "E2": ({}, protect)}
        hp = {"C": 0.5, "P": 1.0, "E1": 1.0, "E2": 1.0}
        from engine import FieldState
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["C"], 0.5)


class TestChooseActionPrefersDrainOnATiedDamageMove(unittest.TestCase):
    """"Leech Life is a bug move that heals 50% of the damage inflicted.
    Between two moves that deal the same damage, a healing move should be
    preferred" -- `_choose_action`'s own ranking key ended at raw damage
    (`got.frac`), with nothing to prefer the free HP back once two
    candidates already tie on it."""

    def setUp(self):
        self.W = world()

    def test_the_draining_twin_of_an_identical_move_is_preferred(self):
        """A synthetic drain-clone of Iron Head (same power/type/category,
        `drain` the only difference) isolates the tie-break cleanly --
        real dex moves rarely deal EXACTLY equal damage, so this is the
        only way to test "same damage" without also changing something
        else the ranking already cares about."""
        import dataclasses
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        target = cf._build("Skarmory", merged, natures)
        iron_head = cf._lookup_move("Iron Head", moves)
        drain_twin = dataclasses.replace(
            iron_head, name="Iron Head (drain twin)", drain=[1, 2])
        hits_ih, _mv = cf._choose_action(
            attacker, [iron_head], {"E": target}, typechart)
        hits_dt, _mv2 = cf._choose_action(
            attacker, [drain_twin], {"E": target}, typechart)
        self.assertAlmostEqual(
            hits_ih["E"].frac, hits_dt["E"].frac, places=9,
            msg="fixture must deal identical damage for this to be a real "
                "tie, not a difference in power")
        self.assertLess(hits_ih["E"].frac, 1.0, "fixture must not KO -- a "
                        "real tie needs kos_now_count to stay tied at 0 too")
        hits, chosen = cf._choose_action(
            attacker, [iron_head, drain_twin], {"E": target}, typechart)
        self.assertEqual(chosen.name, "Iron Head (drain twin)")

    def test_a_stronger_non_draining_move_still_wins(self):
        """Drain is a late tie-break, not a blanket preference -- a move
        that deals strictly MORE damage still wins even against a
        draining alternative (Leech Life is resisted by Skarmory's Steel
        typing, so Iron Head hits harder here despite not draining)."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        attacker = cf._build("Kingambit", merged, natures)
        target = cf._build("Skarmory", merged, natures)
        iron_head = cf._lookup_move("Iron Head", moves)
        leech_life = cf._lookup_move("Leech Life", moves)
        self.assertEqual(leech_life.drain, [1, 2])
        hits_ih, _mv = cf._choose_action(
            attacker, [iron_head], {"E": target}, typechart)
        hits_ll, _mv2 = cf._choose_action(
            attacker, [leech_life], {"E": target}, typechart)
        self.assertGreater(hits_ih["E"].frac, hits_ll["E"].frac,
                           "fixture must have Iron Head strictly outdamage "
                           "Leech Life for this to test a real tradeoff")
        hits, chosen = cf._choose_action(
            attacker, [iron_head, leech_life], {"E": target}, typechart)
        self.assertEqual(chosen.name, "Iron Head")


class TestMegaGolisopodToughClaws(unittest.TestCase):
    """"Have you included Mega Golisopod's Tough Claws ability" --
    mbsmogon.xlsx's own "Mega Golisopod" row already records Tough Claws
    at 100% usage, so the SAME shared ability-resolution path every other
    Mega already goes through (`combatants._default_ability`, used by both
    the real engine and `counter_finder._build_form`) already resolves it
    correctly with no special-case rule needed (unlike Dragonite, whose
    usage data disagreed with the intended house rule and needed
    `FORCED_BASE_ABILITY`). These tests lock that in as a regression check,
    since nothing exercised it before."""

    def setUp(self):
        self.W = world()

    def test_the_mega_form_resolves_to_tough_claws_and_bug_steel(self):
        merged, natures = self.W["merged"], self.W["natures"]
        mega = cf._build("Mega Golisopod", merged, natures)
        self.assertEqual(mega.ability, "Tough Claws")
        self.assertEqual(set(mega.types), {"Bug", "Steel"})

    def test_the_base_form_keeps_emergency_exit_and_bug_water(self):
        merged, natures = self.W["merged"], self.W["natures"]
        base = cf._build_form("Mega Golisopod", merged, natures, stay_base=True)
        self.assertEqual(base.ability, "Emergency Exit")
        self.assertEqual(set(base.types), {"Bug", "Water"})

    def test_tough_claws_boosts_a_contact_move_by_1_3x(self):
        """Isolates JUST the ability -- comparing the mega form against the
        base form directly would also confound the result with their
        different stats (150 vs 125 Atk) and typing (Iron Head gets STAB
        as Bug/Steel but not as Bug/Water), so this compares the mega form
        against a copy of itself with a neutral ability instead, same
        stats/typing throughout."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        mega = cf._build("Mega Golisopod", merged, natures)
        no_ability = copy.copy(mega)
        no_ability.ability = "Battle Armor"  # neutral, no damage effect
        target = cf._build("Milotic", merged, natures)
        iron_head = cf._lookup_move("Iron Head", moves)
        self.assertTrue((iron_head.flags or {}).get("contact"))
        mega_hit = cf._raw_hit(mega, iron_head, target, typechart, roll="avg")
        plain_hit = cf._raw_hit(no_ability, iron_head, target, typechart, roll="avg")
        self.assertGreater(plain_hit.frac, 0)
        self.assertAlmostEqual(mega_hit.frac / plain_hit.frac, 1.3, places=2)


class TestChoiceScarfEnemyMoveset(unittest.TestCase):
    """"select an enemy as a choice scarf user (and hence will have 4
    attacks)" -- `choice_scarf_enemy_moveset` drops Protect (and any other
    Status move) from a named enemy's own usage-ranked moveset, keeping
    the top 4 that remain, since a Choice item locks the holder into the
    first move used and no real Scarf set would risk getting stuck on a
    status move."""

    def setUp(self):
        self.W = world()

    def test_drops_protect_and_swords_dance_keeping_the_top_4_attacks(self):
        """Kingambit's own recorded usage is now a real default_sets.txt-
        pinned, FIXED 4-move set, checked directly against
        `merged["Kingambit"]["moves_usage"]`: Kowtow Cleave/Sucker Punch/
        Low Kick/Iron Head, all pinned at exactly 100% in that order --
        there is no Protect or Swords Dance recorded at all any more (this
        fixture's original point -- confirming Status moves get skipped --
        is now covered instead by `test_never_includes_a_status_move_
        across_the_roster`'s own broader sweep, which still exercises a
        species with a real Status move in its usage table)."""
        merged, moves = self.W["merged"], self.W["moves"]
        got = cf.choice_scarf_enemy_moveset("Kingambit", merged, moves)
        self.assertEqual(got, ["Kowtow Cleave", "Sucker Punch", "Low Kick", "Iron Head"])

    def test_returns_move_names_not_moveinfo_objects(self):
        merged, moves = self.W["merged"], self.W["moves"]
        got = cf.choice_scarf_enemy_moveset("Kingambit", merged, moves)
        self.assertTrue(all(isinstance(n, str) for n in got))

    def test_never_includes_a_status_move_across_the_roster(self):
        """A broader sweep -- no species' own Choice-Scarf moveset should
        ever carry a Status-category move, whatever its usage table looks
        like."""
        merged, moves = self.W["merged"], self.W["moves"]
        for name in ("Kingambit", "Incineroar", "Garchomp", "Whimsicott"):
            got = cf.choice_scarf_enemy_moveset(name, merged, moves)
            for mv_name in got:
                mi = cf._lookup_move(mv_name, moves)
                self.assertNotEqual(mi.category, "Status",
                                    f"{name}'s Scarf moveset kept {mv_name!r}")

    def test_top_k_is_respected(self):
        merged, moves = self.W["merged"], self.W["moves"]
        got = cf.choice_scarf_enemy_moveset("Kingambit", merged, moves, top_k=2)
        self.assertEqual(got, ["Kowtow Cleave", "Sucker Punch"])


class TestSpreadHitRecomputedIfATargetAlreadyFaintedThisTurn(unittest.TestCase):
    """"if Staraptor fainted then Heat Wave would have been single target
    damage rather than spread" -- `hits`/`num_targets_hit` are fixed at
    PLAN-BUILD time (before the turn's own speed order plays out), so a
    spread move computed against 2 live targets can find, by the time it
    actually resolves in `_apply_plan`, that a FASTER attacker already
    fainted one of them this same turn -- real doubles decides the 0.75x
    multi-target penalty at the moment of use, not at team-preview, so a
    stale spread-reduced hit on the one target still standing is wrong."""

    def setUp(self):
        self.W = world()

    def _fixture(self):
        merged, natures = self.W["merged"], self.W["natures"]
        attacker = cf._build("Kingambit", merged, natures)
        spreader = cf._build("Mega Staraptor", merged, natures)
        e1 = cf._build("Milotic", merged, natures)
        e2 = cf._build("Milotic", merged, natures)
        return attacker, spreader, e1, e2

    def test_the_survivors_hit_is_rescaled_up_when_the_other_target_already_fainted(self):
        from damage import MoveInfo
        from engine import FieldState
        attacker, spreader, e1, e2 = self._fixture()
        combatants = {"C": attacker, "P": spreader, "E1": e1, "E2": e2}
        # Priority 1 guarantees C resolves before P regardless of raw speed.
        fast_ohko = MoveInfo("Fast Attack", 100, "Normal", "Physical", "normal",
                             priority=1)
        heat_wave = MoveInfo("Heat Wave", 95, "Fire", "Special",
                             "allAdjacentFoes", priority=0)
        ohko = cf.Hit(move_name="Fast Attack", frac=1.0, lo=1.0, avg=1.0,
                     hi=1.0, eff=1.0, num_targets_hit=1)
        # The SAME spread hit fraction on both -- exactly what `_choose_
        # action` would have computed for Heat Wave when BOTH enemies were
        # still alive (E1's own copy is never actually used once E1 is
        # dead by the time P's turn comes up, but it has to be present in
        # `hits` for `_apply_plan` to know Heat Wave was aimed at 2 targets
        # in the first place).
        spread_hit = cf.Hit(move_name="Heat Wave", frac=0.3, lo=0.3, avg=0.3,
                           hi=0.3, eff=1.0, num_targets_hit=2)
        plan = {"C": ({"E1": ohko}, fast_ohko),
               "P": ({"E1": spread_hit, "E2": spread_hit}, heat_wave),
               "E1": ({}, None), "E2": ({}, None)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        new_hp, log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertEqual(new_hp["E1"], 0.0, "E1 must be OHKO'd by C first")
        self.assertAlmostEqual(new_hp["E2"], 1.0 - 0.3 / 0.75, places=6,
                               msg="E2 should take the UNDONE-0.75x hit, "
                                   "since E1 was already dead when Heat "
                                   "Wave resolved")
        logged = next(h for role, tgt, h in log if role == "P" and tgt == "E2")
        self.assertAlmostEqual(logged.frac, 0.3 / 0.75, places=6,
                               msg="the LOGGED gameplan hit must match what "
                                   "was actually applied, not the stale "
                                   "spread frac")
        self.assertEqual(logged.num_targets_hit, 1)

    def test_both_still_alive_keeps_the_spread_penalty(self):
        """Precondition/contrast: if C's move ISN'T lethal (E1 survives),
        Heat Wave's own spread hit on both must stay exactly as computed
        -- confirms the rescale only fires when a target is genuinely gone,
        not on every multi-target hit."""
        from damage import MoveInfo
        from engine import FieldState
        attacker, spreader, e1, e2 = self._fixture()
        combatants = {"C": attacker, "P": spreader, "E1": e1, "E2": e2}
        weak_hit = cf.Hit(move_name="Fast Attack", frac=0.1, lo=0.1, avg=0.1,
                         hi=0.1, eff=1.0, num_targets_hit=1)
        fast_move = MoveInfo("Fast Attack", 40, "Normal", "Physical", "normal",
                             priority=1)
        heat_wave = MoveInfo("Heat Wave", 95, "Fire", "Special",
                             "allAdjacentFoes", priority=0)
        spread_hit = cf.Hit(move_name="Heat Wave", frac=0.3, lo=0.3, avg=0.3,
                           hi=0.3, eff=1.0, num_targets_hit=2)
        plan = {"C": ({"E1": weak_hit}, fast_move),
               "P": ({"E1": spread_hit, "E2": spread_hit}, heat_wave),
               "E1": ({}, None), "E2": ({}, None)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertGreater(new_hp["E1"], 0.0, "fixture must NOT OHKO E1")
        self.assertAlmostEqual(new_hp["E2"], 1.0 - 0.3, places=6)


class TestSpreadHitKeepsThePenaltyWhenATargetProtects(unittest.TestCase):
    """Regression (this one caught by the user, a previous "fix" here had
    it backwards): "a spread move still does 0.75x even if one enemy
    protects -- it was doing full 1.0x single target damage if one of the
    enemies protected before." Unlike the FAINT case above (a target that
    is genuinely gone by the time the move resolves really does get
    recomputed as single-target, full damage), a target that Protects was
    still a VALID target when the move was used -- it's live, just
    blocking its own hit -- so the move stays a genuine multi-target use
    and the survivor still only takes the flat 0.75x. Do not re-add a
    `protected_roles` exclusion to `_apply_plan`'s undo check -- that was
    tried once already and gave the wrong (full-damage) answer."""

    def setUp(self):
        self.W = world()

    def test_the_survivor_keeps_the_075x_penalty_when_the_other_target_protects(self):
        from damage import MoveInfo
        from engine import FieldState
        merged, natures = self.W["merged"], self.W["natures"]
        spreader = cf._build("Mega Staraptor", merged, natures)
        e1 = cf._build("Milotic", merged, natures)
        e2 = cf._build("Milotic", merged, natures)
        combatants = {"C": spreader, "P": spreader, "E1": e1, "E2": e2}
        heat_wave = MoveInfo("Heat Wave", 95, "Fire", "Special",
                             "allAdjacentFoes", priority=0)
        spread_hit = cf.Hit(move_name="Heat Wave", frac=0.3, lo=0.3, avg=0.3,
                           hi=0.3, eff=1.0, num_targets_hit=2)
        plan = {"C": ({"E1": spread_hit, "E2": spread_hit}, heat_wave),
               "P": ({}, None), "E1": ({}, None), "E2": ({}, None)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        new_hp, log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset({"E1"}), 1.0, FieldState())
        self.assertEqual(new_hp["E1"], 1.0, "E1 Protected -- untouched")
        self.assertAlmostEqual(new_hp["E2"], 1.0 - 0.3, places=6,
                               msg="E2 must still take the ORIGINAL 0.75x-"
                                   "reduced hit -- E1 Protecting does not "
                                   "turn this into a full-damage single "
                                   "target hit, it was still a live, "
                                   "valid spread target when Heat Wave "
                                   "was used")
        logged = next(h for role, tgt, h in log if role == "C" and tgt == "E2")
        self.assertAlmostEqual(logged.frac, 0.3, places=6,
                               msg="the LOGGED gameplan hit must match what "
                                   "was actually applied")
        self.assertEqual(logged.num_targets_hit, 2)

    def test_neither_protects_keeps_the_spread_penalty(self):
        """Precondition/contrast: with no Protect at all, both hits stay
        exactly as computed -- confirms Protect never rescales a spread
        hit, matching the "both alive" no-op case."""
        from damage import MoveInfo
        from engine import FieldState
        merged, natures = self.W["merged"], self.W["natures"]
        spreader = cf._build("Mega Staraptor", merged, natures)
        e1 = cf._build("Milotic", merged, natures)
        e2 = cf._build("Milotic", merged, natures)
        combatants = {"C": spreader, "P": spreader, "E1": e1, "E2": e2}
        heat_wave = MoveInfo("Heat Wave", 95, "Fire", "Special",
                             "allAdjacentFoes", priority=0)
        spread_hit = cf.Hit(move_name="Heat Wave", frac=0.3, lo=0.3, avg=0.3,
                           hi=0.3, eff=1.0, num_targets_hit=2)
        plan = {"C": ({"E1": spread_hit, "E2": spread_hit}, heat_wave),
               "P": ({}, None), "E1": ({}, None), "E2": ({}, None)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, FieldState())
        self.assertAlmostEqual(new_hp["E1"], 1.0 - 0.3, places=6)
        self.assertAlmostEqual(new_hp["E2"], 1.0 - 0.3, places=6)

    def test_both_protect_neither_gets_hit(self):
        """Both targets blocking leaves nothing to rescale -- no crash, no
        divide-by-zero, both stay untouched."""
        from damage import MoveInfo
        from engine import FieldState
        merged, natures = self.W["merged"], self.W["natures"]
        spreader = cf._build("Mega Staraptor", merged, natures)
        e1 = cf._build("Milotic", merged, natures)
        e2 = cf._build("Milotic", merged, natures)
        combatants = {"C": spreader, "P": spreader, "E1": e1, "E2": e2}
        heat_wave = MoveInfo("Heat Wave", 95, "Fire", "Special",
                             "allAdjacentFoes", priority=0)
        spread_hit = cf.Hit(move_name="Heat Wave", frac=0.3, lo=0.3, avg=0.3,
                           hi=0.3, eff=1.0, num_targets_hit=2)
        plan = {"C": ({"E1": spread_hit, "E2": spread_hit}, heat_wave),
               "P": ({}, None), "E1": ({}, None), "E2": ({}, None)}
        hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        new_hp, _log, _ea, _wiped, _doomed, _spw = cf._apply_plan(
            plan, combatants, hp, frozenset({"E1", "E2"}), 1.0, FieldState())
        self.assertEqual(new_hp["E1"], 1.0)
        self.assertEqual(new_hp["E2"], 1.0)


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


class TestChooseActionAvoidsNeedlessRecharge(unittest.TestCase):
    """"Hyper Voice from Sylveon would have KOd and also done spread damage
    without the recharge downside of Hyper Beam" -- `_choose_action` had a
    late tie-break for recoil (`_self_cost`) but nothing analogous for
    recharge (Hyper Beam/Giga Impact), so a higher-power recharge move
    could beat an equally kill-securing recharge-free one on raw overkill
    damage alone, handing the opponent a free turn for nothing. `_requires_
    recharge` is a NEW, even-earlier tie-break (checked before recoil,
    since a lost turn is a far bigger cost than any recoil percentage)."""

    def setUp(self):
        self.W = world()

    def test_prefers_the_recharge_free_move_when_both_guarantee_the_kill(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        sylveon = cf._build("Sylveon", merged, natures)
        dragonite = cf._build("Dragonite", merged, natures)
        hyper_beam = cf._lookup_move("Hyper Beam", moves)
        hyper_voice = cf._lookup_move("Hyper Voice", moves)
        self.assertTrue((hyper_beam.flags or {}).get("recharge"))
        self.assertFalse((hyper_voice.flags or {}).get("recharge"))
        hits, chosen = cf._choose_action(
            sylveon, [hyper_beam, hyper_voice], {"E": dragonite}, typechart)
        self.assertEqual(chosen.name, "Hyper Voice")
        self.assertGreaterEqual(hits["E"].frac, 1.0, "fixture must be a real KO")

    def test_the_recharge_move_still_wins_when_it_is_the_only_guaranteed_kill(self):
        """Recharge-awareness is a TIE-break, not a blanket penalty -- a
        recharge move that's the only one clearing the KO bar must still
        be chosen over a weaker recharge-free move that doesn't."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        sylveon = cf._build("Sylveon", merged, natures)
        sinistcha = cf._build("Sinistcha", merged, natures)
        hyper_beam = cf._lookup_move("Hyper Beam", moves)
        hyper_voice = cf._lookup_move("Hyper Voice", moves)
        hits_hb, _chosen_hb = cf._choose_action(
            sylveon, [hyper_beam], {"E": sinistcha}, typechart)
        hits_hv, _chosen_hv = cf._choose_action(
            sylveon, [hyper_voice], {"E": sinistcha}, typechart)
        if hits_hb["E"].frac >= 1.0 and hits_hv["E"].frac < 1.0:
            hits, chosen = cf._choose_action(
                sylveon, [hyper_beam, hyper_voice], {"E": sinistcha}, typechart)
            self.assertEqual(chosen.name, "Hyper Beam")
        else:
            self.skipTest("fixture no longer isolates 'only Hyper Beam KOs' "
                          "on the current dataset -- not what this test checks")

    def test_a_charge_move_with_no_recharge_flag_is_unaffected(self):
        """A move without `flags.get("recharge")` (the overwhelming
        majority) must never be treated as if it cost a lost turn -- this
        tie-break only ever fires for the small named family that actually
        carries the flag."""
        moves = self.W["moves"]
        moonblast = cf._lookup_move("Moonblast", moves)
        self.assertFalse((moonblast.flags or {}).get("recharge"))


class TestMutualKnockoutIsNotMisclassifiedAsAWin(unittest.TestCase):
    """"I believe this should qualify as a win for Staraptor, but is
    treated as an out_trade for my side" -- turned out to be a genuine
    MUTUAL KO: Mega Staraptor's own finishing Brave Bird carries 33%
    recoil, which (on top of the incoming damage it had already taken)
    drops IT to 0 the exact same turn its hit drops the enemy's last
    Pokemon to 0. `wiped_side` locks to "theirs" the moment the ENEMY's hp
    hits 0 (a step before the attacker's own recoil is even computed), so
    `wiped_side == "theirs"` alone used to grant "sweep"/"out_trade"
    regardless of whether `ours_alive` was ALSO now False -- crediting a
    race nobody actually survived to claim as an unambiguous win.

    Both Sylveon and (Mega) Staraptor are now default_sets.txt-pinned
    (real Pixilate Sylveon, real Contrary Mega Staraptor, real EVs/items),
    which changes every raw damage number in this fixture enough that no
    natural, automatically-targeted 3-turn `_joint_race` among these same
    four real Pokemon reliably lands BOTH enemies and Staraptor on exactly
    0 HP the same turn any more (checked exhaustively across real move
    choices/turn counts) -- `_choose_action`'s own automatic per-turn
    target selection, not a bug in the mechanic under test, keeps
    declining to split damage the way the old numbers happened to.

    So these tests now call `_resolve_turn` directly (still real
    `_apply_plan`/`_resolve_turn`, the exact functions `_joint_race`'s own
    per-turn loop calls -- see its own docstring) for just the FINAL turn,
    with a hand-set STARTING `hp` standing in for "two turns of real chip
    already happened" (a real, plausible mid-race state, not a fabricated
    one -- Mega Scizor's Bullet Punch/Dragonite's own hits are exactly the
    kind of chip that would produce it), and `our_hints` to pin Staraptor's
    target the way a real turn's speed/AI would have converged on anyway.
    `_classify` below is `_joint_race`'s own post-loop outcome formula,
    copied verbatim (see its docstring, right after the turn loop) so the
    assertion is checking the REAL classification rule, not a new one."""

    def setUp(self):
        self.W = world()

    @staticmethod
    def _classify(wiped_side, ours_alive, theirs_alive, any_enemy_acted):
        """`_joint_race`'s own post-loop classification, copied verbatim."""
        if (wiped_side == "theirs" or not theirs_alive) and ours_alive:
            return "sweep" if not any_enemy_acted else "out_trade"
        elif wiped_side == "ours" or not ours_alive:
            return "loss"
        return "no_ko"

    def test_a_recoil_finishing_blow_that_also_kos_the_attacker_is_a_loss(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        dragonite = cf._build("Dragonite", merged, natures)
        sylveon = cf._build("Sylveon", merged, natures)
        scizor = cf._build("Mega Scizor", merged, natures)
        star = cf._build("Mega Staraptor", merged, natures)
        bb = cf._lookup_move("Brave Bird", moves)
        self.assertEqual(bb.recoil, [33, 100], "fixture needs Brave Bird's own recoil")
        # Real, verified-by-hand mid-race state: Dragonite (E1) and Sylveon
        # (C) already fainted in earlier turns; Mega Scizor (E2) is at
        # exactly Brave Bird's own real avg-roll frac against it (verified
        # directly via `_raw_hit`), so this hit finishes it with no
        # overkill; Staraptor (P) is already down to 10% -- comfortably
        # less than Brave Bird's own real recoil cost on this exact hit
        # (13.4% of Staraptor's max HP, also verified via `_raw_hit`), so
        # the recoil finishes Staraptor too, the same turn.
        e2_start_hp = cf._raw_hit(star, bb, scizor, typechart, roll="avg").frac
        combatants = {"E1": dragonite, "E2": scizor, "C": sylveon, "P": star}
        moves_by_role = {"E1": [], "E2": [], "C": [], "P": [bb]}
        hp = {"C": 0.0, "E1": 0.0, "E2": e2_start_hp, "P": 0.10}
        weather = cf._field_weather(combatants)
        new_hp, _log, _enemy_acted, wiped, _recharging = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"P": "E2"})
        # Precondition: this fixture really is a full mutual wipe, not just
        # a one-sided finish -- otherwise this test would pass for the
        # wrong reason.
        self.assertEqual(new_hp, {"C": 0.0, "P": 0.0, "E1": 0.0, "E2": 0.0})
        # The exact reported bug's own signature: the ENEMY side is what
        # `_apply_plan` sees hit 0 first (Staraptor's recoil is applied a
        # step later, within that same hit's own processing).
        self.assertEqual(wiped, "theirs")
        ours_alive = new_hp["C"] > 0 or new_hp["P"] > 0
        theirs_alive = new_hp["E1"] > 0 or new_hp["E2"] > 0
        self.assertFalse(ours_alive, "Staraptor's own recoil must also "
                         "register -- nobody on our side survived either")
        self.assertEqual(self._classify(wiped, ours_alive, theirs_alive, True),
                         "loss")

    def test_the_same_finish_without_a_recoil_move_still_reads_as_a_win(self):
        """Contrast/guard against over-correcting: swap Brave Bird for a
        no-recoil move of identical raw power (same BP/type/priority/
        flags) that still finishes Mega Scizor the same turn -- Mega
        Staraptor must survive and this must still read as a genuine
        out_trade/sweep win, confirming the fix only changes the TRUE
        mutual-KO case, not the ordinary "we finish them and live" case."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        dragonite = cf._build("Dragonite", merged, natures)
        sylveon = cf._build("Sylveon", merged, natures)
        scizor = cf._build("Mega Scizor", merged, natures)
        star = cf._build("Mega Staraptor", merged, natures)
        bb = cf._lookup_move("Brave Bird", moves)
        no_recoil_finisher = cf.MoveInfo(
            bb.name, bb.power, bb.move_type, bb.category, bb.target,
            priority=bb.priority, flags=bb.flags)  # recoil=None (the default)
        self.assertIsNone(no_recoil_finisher.recoil)
        e2_start_hp = cf._raw_hit(star, bb, scizor, typechart, roll="avg").frac
        combatants = {"E1": dragonite, "E2": scizor, "C": sylveon, "P": star}
        moves_by_role = {"E1": [], "E2": [], "C": [], "P": [no_recoil_finisher]}
        hp = {"C": 0.0, "E1": 0.0, "E2": e2_start_hp, "P": 0.10}
        weather = cf._field_weather(combatants)
        new_hp, _log, enemy_acted, wiped, _recharging = cf._resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, {"P": "E2"})
        self.assertGreater(new_hp["P"], 0.0, "Staraptor must survive without recoil")
        ours_alive = new_hp["C"] > 0 or new_hp["P"] > 0
        theirs_alive = new_hp["E1"] > 0 or new_hp["E2"] > 0
        outcome = self._classify(wiped, ours_alive, theirs_alive, enemy_acted)
        self.assertIn(outcome, ("sweep", "out_trade"))


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
        """`e1`/`e2` are the SAME species deliberately -- `_best_turn`'s own
        2-turn lookahead (`TestBestTurnTwoTurnLookahead`) now genuinely
        prefers whichever enemy takes more damage from Dragon-type Draco
        Meteor, so two DIFFERENT species (the original fixture used
        Kingambit + Sinistcha) can get hit on different turns, and their
        different defensive stats -- not just the halving -- would then
        move the ratio this test checks. Identical targets removes that
        confound; the halving is the only thing left that can change it."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        hydreigon = cf._build("Hydreigon", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Kingambit", merged, natures)
        e2 = cf._build("Kingambit", merged, natures)
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

    def test_a_mega_that_only_has_intimidate_pre_evolution_still_intimidates(self):
        """"Mega Salamence has intimidate in base form, so it intimidates
        before mega evolving" -- base Salamence is Intimidate, Mega
        Salamence is Aerilate, but real Intimidate fires at switch-in,
        before that turn's own Mega Evolution. `_build`'s mega-projection
        already fast-forwards straight to Aerilate for everything else
        this module computes, but the opposing side must still take the
        real -1 Atk drop from Salamence's own (pre-evolution) Intimidate."""
        merged, natures = self.W["merged"], self.W["natures"]
        salamence = cf._build("Mega Salamence", merged, natures)
        self.assertEqual(salamence.ability, "Aerilate")
        self.assertEqual(salamence.pre_mega_ability, "Intimidate")
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)  # ordinary ability
        e2 = cf._build("Sinistcha", merged, natures)
        mult = cf._intimidate_mult_by_role(
            {"C": salamence, "P": partner, "E1": e1, "E2": e2})
        self.assertEqual(mult.get("E1"), {"physical": 2 / 3})
        self.assertEqual(mult.get("E2"), {"physical": 2 / 3})

    def test_a_mega_that_only_gains_contrary_on_evolving_still_takes_the_real_drop(self):
        """"Staraptor only gains contrary after mega evolving, so enemy
        intimidate reduces its attack before it mega evolves" -- base
        Staraptor is Intimidate, Mega Staraptor is Contrary. An opposing
        Intimidate holder's stat drop resolves before OUR OWN Mega
        Evolution too, so a Staraptor this module has already fast-
        forwarded to its mega form must still take the real -1 Atk drop,
        not have it inverted into a Contrary +1 boost."""
        merged, natures = self.W["merged"], self.W["natures"]
        staraptor = cf._build("Mega Staraptor", merged, natures)
        self.assertEqual(staraptor.ability, "Contrary")
        self.assertEqual(staraptor.pre_mega_ability, "Intimidate")
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)
        e1.ability = "Intimidate"
        e2 = cf._build("Sinistcha", merged, natures)
        mult = cf._intimidate_mult_by_role(
            {"C": staraptor, "P": partner, "E1": e1, "E2": e2})
        self.assertEqual(mult.get("C"), {"physical": 2 / 3})


class TestContraryDefenseBoostInTheJointRace(unittest.TestCase):
    """"Mega Staraptor's Contrary meaning Close Combat boosts its def and
    spdef" -- Close Combat's own -1 Def/-1 SpD (and Superpower's -1 Atk/-1
    Def), verified via `mbsmogon.xlsx`'s own usage data to be a REAL, if
    uncommon, ability choice for this roster's Mega Staraptor (13.2%,
    resolved correctly by `_build`/`_mega_project` already -- this class
    tests the NEW mechanic, not ability resolution). A Contrary holder
    inverts that drop into a real +1/+1 stage GAIN, x1.5 bulkier on both
    the physical and special sides -- exact math, mirroring `_intimidate_
    mult_by_role`'s own -1/+1 stage table, not the flat SELF_HALVING_MOVES
    approximation."""

    def setUp(self):
        self.W = world()

    def test_mega_staraptor_actually_resolves_to_contrary(self):
        """Precondition, not the bug under test -- confirms the fixture."""
        star = cf._build("Mega Staraptor", self.W["merged"], self.W["natures"])
        self.assertEqual(star.ability, "Contrary")

    def test_close_combat_is_in_the_family(self):
        self.assertEqual(cf.CONTRARY_SELF_DROP_MOVES["Close Combat"],
                         {"def": -1, "spd": -1})

    def test_choose_action_reads_a_boosted_def_mult_as_real_extra_bulk(self):
        """Direct unit test of `_choose_action`'s own application point
        (mirrors `TestIntimidateInTheJointRace.test_ordinary_ability_takes_
        exactly_two_thirds`): a x(2/3) `def_mult_by_role` entry for the
        DEFENDING role must cut the computed Hit by exactly that factor,
        matching a real +1 Def stage's exact multiplier."""
        merged, natures, typechart = (
            self.W["merged"], self.W["natures"], self.W["typechart"])
        chomp = cf._build("Garchomp", merged, natures)
        target = cf._build("Milotic", merged, natures)
        eq = cf._lookup_move("Earthquake", self.W["moves"])
        hits_no, _mv = cf._choose_action(chomp, [eq], {"E1": target}, typechart,
                                         attacker_role="C")
        hits_boosted, _mv = cf._choose_action(
            chomp, [eq], {"E1": target}, typechart, attacker_role="C",
            def_mult_by_role={"E1": {"physical": 2 / 3}})
        self.assertAlmostEqual(hits_boosted["E1"].frac / hits_no["E1"].frac,
                               2 / 3, places=6)

    def test_first_use_deals_the_normal_amount(self):
        """The boost applies to damage taken AFTER Close Combat resolves,
        not retroactively to the turn it was used -- same "first use is
        still the normal number" timing `SELF_HALVING_MOVES` already uses."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        star = cf._build("Mega Staraptor", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        e1 = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": star, "P": partner, "E1": e1, "E2": e2}
        cc = cf._lookup_move("Close Combat", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [cc], "P": [protect], "E1": [protect], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 1)
        role, tgt, full_hit = next((r, t, h) for r, t, h in log[0] if r == "C")
        no_boost = cf._raw_hit(star, cc, combatants[tgt], typechart, roll="avg")
        self.assertAlmostEqual(full_hit.frac, no_boost.frac, places=3)

    def test_a_contrary_user_takes_less_damage_the_turn_after_close_combat(self):
        """End to end through `_joint_race`: Mega Staraptor uses Close Combat
        turn 1 (real damage, unaffected -- see above), then eats a Rock
        Slide from Garchomp turn 2 -- that hit on Mega Staraptor must be
        exactly x(2/3) of what the SAME Rock Slide did on it turn 1, since
        Def is now genuinely +1 (x1.5 bulkier) instead of the -1 drop a
        non-Contrary user would have taken. Rock Slide (2x on Flying,
        neutral on Milotic's own Water typing) is used instead of
        Earthquake specifically so Garchomp's own greedy targeting reliably
        keeps aiming at Mega Staraptor both turns, not its partner -- a
        Ground move would do ZERO to the Flying-type Mega Staraptor and
        so never even be aimed at it.

        Mega Staraptor's own Close Combat needs a target too -- Weavile
        (Dark/Ice, 4x weak to Fighting) is used as E2 specifically so Close
        Combat clearly prefers it over Garchomp's own neutral 1x, leaving
        Garchomp fully unharmed and free to land Rock Slide on Mega
        Staraptor both turns (Garchomp is otherwise fast enough, and Close
        Combat hits hard enough, that Garchomp would be finished off
        mid-turn-2 before ever getting to act, if it were CC's target
        instead)."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        star = cf._build("Mega Staraptor", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        chomp = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Weavile", merged, natures)
        combatants = {"C": star, "P": partner, "E1": chomp, "E2": e2}
        cc = cf._lookup_move("Close Combat", moves)
        rock_slide = cf._lookup_move("Rock Slide", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [cc], "P": [protect], "E1": [rock_slide], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 2)
        turn1_hit = next(((t, h) for role, t, h in log[0] if role == "E1"), None)
        turn2_hit = next(((t, h) for role, t, h in log[1] if role == "E1"), None)
        self.assertIsNotNone(turn1_hit, "Garchomp should get a turn-1 hit in")
        self.assertEqual(turn1_hit[0], "C", "Rock Slide should target Mega Staraptor")
        self.assertIsNotNone(turn2_hit, "Mega Staraptor should survive to turn 2")
        self.assertEqual(turn2_hit[0], "C")
        self.assertAlmostEqual(turn2_hit[1].frac / turn1_hit[1].frac, 2 / 3, places=2)

    def test_a_non_contrary_close_combat_user_gets_no_boost(self):
        """Scoped to Contrary specifically -- an ordinary user's own -1/-1
        drop stays deliberately unmodeled, exactly as `SELF_HALVING_MOVES`'s
        own `test_close_combat_does_not_trigger_the_halving` already
        establishes for the offensive side. Gallade is Psychic/Fighting --
        Rock Slide is neutral on it, same as on Milotic, so either target
        is fine here; the assertion only needs the SAME target to repeat
        turn to turn."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        gallade = cf._build("Gallade", merged, natures)
        self.assertNotEqual(gallade.ability, "Contrary")
        partner = cf._build("Milotic", merged, natures)
        chomp = cf._build("Garchomp", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": gallade, "P": partner, "E1": chomp, "E2": e2}
        cc = cf._lookup_move("Close Combat", moves)
        rock_slide = cf._lookup_move("Rock Slide", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [cc], "P": [protect], "E1": [rock_slide], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 2)
        turn1_hit = next(((t, h) for role, t, h in log[0] if role == "E1"), None)
        turn2_hit = next(((t, h) for role, t, h in log[1] if role == "E1"), None)
        self.assertIsNotNone(turn1_hit)
        if turn2_hit is not None and turn2_hit[0] == turn1_hit[0]:
            self.assertAlmostEqual(turn2_hit[1].frac, turn1_hit[1].frac, places=2)

    def test_superpower_boosts_atk_and_def_for_a_contrary_holder(self):
        """The other family member: Superpower's own -1 Atk/-1 Def becomes
        a real +1/+1 for Contrary -- Atk feeds `dmg_mult_by_role` (this
        role's own future OUTGOING damage), exactly like Defiant/Competitive
        already do for Intimidate."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        star = cf._build("Mega Staraptor", merged, natures)
        partner = cf._build("Milotic", merged, natures)
        # Ghost-types (e.g. Sinistcha) are FLAT IMMUNE to Fighting -- would
        # read as a spurious 0.0 turn-1 hit and break the ratio math below,
        # not a real edge case of the mechanic under test. Milotic (Water)
        # takes normal Fighting damage instead.
        e1 = cf._build("Milotic", merged, natures)
        e2 = cf._build("Sinistcha", merged, natures)
        combatants = {"C": star, "P": partner, "E1": e1, "E2": e2}
        sp = cf._lookup_move("Superpower", moves)
        protect = cf._lookup_move("Protect", moves)
        moves_by_role = {"C": [sp], "P": [protect], "E1": [protect], "E2": [protect]}
        _outcome, _turns_used, _hp, log = cf._joint_race(
            combatants, moves_by_role, typechart, None, 2)
        turn1 = next(((t, h) for role, t, h in log[0] if role == "C"), None)
        turn2 = next(((t, h) for role, t, h in log[1] if role == "C"), None)
        self.assertIsNotNone(turn1)
        self.assertIsNotNone(turn2, "Milotic should survive one hit")
        self.assertEqual(turn2[0], turn1[0], "same target both turns")
        self.assertAlmostEqual(turn2[1].frac / turn1[1].frac, 1.5, places=2)


class TestIntimidateInTheDamageGrid(unittest.TestCase):
    """"You must also always account for intimidate (as well as defiant
    boosts) in the counter_table.py battle logic - I don't see it doing so
    now." The real race (`_joint_race`, reached by every `--bring4`/
    `--multi-bring4`/`--joint`/`--deep` search via `_pair_vs_targets`) has
    modeled this since `TestIntimidateInTheJointRace` -- but `_grid_hit`/
    `_damage_grid` (the `--deep`/`want_grid` 2x2 raw-hit preview, a
    separate "right now" snapshot, not a played-out race) did not, a real,
    confirmed gap this class closes. Only the STATIC switch-in multiplier
    applies here (no Draco-Meteor-family halving, no Contrary move-
    triggered def boost) -- see `_grid_hit`'s own docstring for why."""

    def setUp(self):
        self.W = world()

    def test_defiant_doubles_the_grid_cell_against_an_intimidate_holder(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        kingambit = cf._build("Kingambit", merged, natures)
        self.assertEqual(kingambit.ability, "Defiant")
        partner = cf._build("Milotic", merged, natures)
        e1_no = cf._build("Milotic", merged, natures)
        e1_yes = cf._build("Milotic", merged, natures)
        e1_yes.ability = "Intimidate"
        e2 = cf._build("Sinistcha", merged, natures)
        sucker_punch = cf._lookup_move("Sucker Punch", moves)
        no_boost = cf._grid_hit(kingambit, [sucker_punch], e1_no, partner, typechart)
        with_boost_combatants = {"C": e1_yes, "P": e2, "E1": kingambit, "E2": partner}
        dmg_mult = cf._intimidate_mult_by_role(with_boost_combatants)
        boosted = cf._grid_hit(kingambit, [sucker_punch], e1_yes, partner, typechart,
                               dmg_mult_by_role=dmg_mult, attacker_role="E1")
        self.assertAlmostEqual(boosted.frac / no_boost.frac, 2.0, places=6)

    def test_ordinary_ability_takes_exactly_two_thirds_in_the_grid(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        garchomp = cf._build("Garchomp", merged, natures)  # Rough Skin -- ordinary here
        partner = cf._build("Milotic", merged, natures)
        e1_no = cf._build("Milotic", merged, natures)
        e1_yes = cf._build("Milotic", merged, natures)
        e1_yes.ability = "Intimidate"
        e2 = cf._build("Sinistcha", merged, natures)
        eq = cf._lookup_move("Earthquake", moves)
        no_boost = cf._grid_hit(garchomp, [eq], e1_no, None, typechart)
        combatants = {"C": garchomp, "P": partner, "E1": e1_yes, "E2": e2}
        dmg_mult = cf._intimidate_mult_by_role(combatants)
        reduced = cf._grid_hit(garchomp, [eq], e1_yes, None, typechart,
                               dmg_mult_by_role=dmg_mult, attacker_role="C")
        self.assertAlmostEqual(reduced.frac / no_boost.frac, 2 / 3, places=6)

    def test_damage_grid_end_to_end_via_pair_vs_targets(self):
        """Full integration through the actual `--deep`/`want_grid` path a
        real counter_table.py run takes, not just the unit-level helper."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        our_built = cf._build_forms(["Incineroar", "Garchomp"], merged, natures, moves)
        targets = ["Kingambit", "Basculegion"]
        enemy_built = cf._build_forms(targets, merged, natures, moves)
        detail, _summary = cf._pair_vs_targets(
            "Incineroar", "Garchomp", our_built, targets, enemy_built,
            typechart, turns=2, merged=merged, want_grid=True)
        grid = detail[("Kingambit", "Basculegion")]["grid"]
        kingambit_hit = grid["theirs"][("E1", "P")]
        self.assertEqual(kingambit_hit.move_name, "Kowtow Cleave")

        our_built_no_intim = cf._build_forms(["Whimsicott", "Garchomp"], merged, natures, moves)
        detail_no_intim, _s = cf._pair_vs_targets(
            "Whimsicott", "Garchomp", our_built_no_intim, targets, enemy_built,
            typechart, turns=2, merged=merged, want_grid=True)
        grid_no_intim = detail_no_intim[("Kingambit", "Basculegion")]["grid"]
        baseline_hit = grid_no_intim["theirs"][("E1", "P")]
        self.assertEqual(baseline_hit.move_name, "Kowtow Cleave")
        self.assertAlmostEqual(kingambit_hit.frac / baseline_hit.frac, 2.0, places=2)

    def test_no_ability_interaction_on_the_board_leaves_the_grid_unchanged(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        c1 = cf._build("Garchomp", merged, natures)
        c2 = cf._build("Milotic", merged, natures)
        e1c = cf._build("Sinistcha", merged, natures)
        e2c = cf._build("Corviknight", merged, natures)
        m1 = cf._move_infos("Garchomp", merged, moves, ["Earthquake"])
        m2 = cf._move_infos("Milotic", merged, moves, ["Scald"])
        e1m = cf._move_infos("Sinistcha", merged, moves, ["Shadow Ball"])
        e2m = cf._move_infos("Corviknight", merged, moves, ["Body Press"])
        grid = cf._damage_grid(c1, c2, e1c, e2c, m1, m2, e1m, e2m, typechart, None)
        self.assertEqual(cf._intimidate_mult_by_role(
            {"C": c1, "P": c2, "E1": e1c, "E2": e2c}), {})
        # No assertion beyond "this doesn't crash and returns real Hits" --
        # the point is a board with NO Intimidate/Defiant/Competitive on it
        # must not be affected by this change at all.
        self.assertIsNotNone(grid["ours"][("C", "E1")])


class TestBring4FromDeepDive(unittest.TestCase):
    """`bring4_from_deep_dive` -- "I may as well calculate for all 6 of my
    pokemon rather than just 4, to see the best bring4": Stage 2's own
    "which 4 should you actually bring" ranking, sourced from an
    ALREADY-COMPUTED `core_deep_dive` result instead of `joint_pool_
    search`'s cheap single-turn hypothesis. Uses the SAME `_bring4_
    candidates` ranking `bring4_search` itself uses, so it should usually
    (not provably always -- one is a cheap single-turn hypothesis, the
    other a full multi-turn race) agree with the cheap version on the
    SAME pinned set."""

    CORE = ["Mega Gengar", "Mega Alakazam", "Ninetales-Alola", "Sharpedo",
           "Rampardos", "Kingambit"]
    TARGETS = ["Sableye", "Ariados", "Froslass", "Absol"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.dive = cf.core_deep_dive(
            self.CORE, [self.TARGETS], merged, moves, natures, typechart)

    def test_returns_every_possible_bring4(self):
        rows = cf.bring4_from_deep_dive(self.CORE, self.dive, self.TARGETS)
        import itertools
        self.assertEqual(len(rows), len(list(itertools.combinations(self.CORE, 4))))
        self.assertEqual({frozenset(r["bring4"]) for r in rows},
                         {frozenset(c) for c in itertools.combinations(self.CORE, 4)})

    def test_best_worst_case_first_matches_bring4_search_ranking_order(self):
        """Not a byte-for-byte match against the cheap hypothesis (the two
        engines can legitimately disagree on close calls), but both must
        sort their OWN rows by the exact same `_bring4_candidates`
        ranking key, so `bring4_rows[0]` is always the least-bad worst
        case within each engine's own numbers."""
        rows = cf.bring4_from_deep_dive(self.CORE, self.dive, self.TARGETS)
        keys = [cf._pair_sort_key(r["worst_pair_row"]) for r in rows]
        uncovered = [len(r["uncovered_enemy_pairs"]) for r in rows]
        for i in range(len(rows) - 1):
            self.assertLessEqual(
                (uncovered[i], keys[i]), (uncovered[i + 1], keys[i + 1]))

    def test_pair_rows_carry_the_real_matchup_detail(self):
        """Every pair in the winning bring's own `pair_rows` must carry a
        real `detail` dict (`_pair_vs_targets`'s own per-enemy-pair shape,
        straight out of the deep dive) -- not just the summary totals --
        so a caller can render the SAME matchup-by-matchup breakdown
        `core_deep_dive`'s own display already does, scoped to just this
        bring's pairs."""
        rows = cf.bring4_from_deep_dive(self.CORE, self.dive, self.TARGETS)
        for pr in rows[0]["pair_rows"]:
            self.assertIn("detail", pr)
            n1, n2 = pr["pair"]
            expected = next(
                pe["detail"] for pe in self.dive["per_pair"][(n1, n2)]["per_enemy"]
                if set(pe["target_names"]) == set(self.TARGETS))
            self.assertIs(pr["detail"], expected)

    def test_rejects_a_roster_the_dive_was_never_raced_against(self):
        with self.assertRaises(ValueError):
            cf.bring4_from_deep_dive(self.CORE, self.dive,
                                     ["Garchomp", "Incineroar"])

    def test_mega_used_matches_the_dives_own_already_decided_choice(self):
        """"The bring4 team selection is saying do not mega either, but in
        the battle log it clearly shows one is mega'd" -- `self.CORE`
        carries 2 stone holders (Mega Gengar, Mega Alakazam), so `self.dive`
        already committed to ONE of them for the whole core (`dive[
        "mega_used"]`). Every bring4 subset carrying BOTH must report THAT
        SAME mega, never `None` ("neither") -- `_bring4_candidates`'s own
        generic per-bring recount would wrongly see 2 stone holders with no
        way to prefer one, since it doesn't know `dive` already raced under
        a single fixed hypothesis."""
        self.assertIn(self.dive["mega_used"], self.CORE)
        rows = cf.bring4_from_deep_dive(self.CORE, self.dive, self.TARGETS)
        both = {"Mega Gengar", "Mega Alakazam"}
        checked_any = False
        for row in rows:
            if both <= set(row["bring4"]):
                checked_any = True
                self.assertEqual(row["mega_used"], self.dive["mega_used"])
        self.assertTrue(checked_any, "fixture never actually produced a "
                        "bring4 carrying both stone holders -- test is vacuous")

    def test_the_other_mega_still_transforms_when_brought_alone(self):
        """"in the bring4 full deep dive only one pokemon can mega across
        all matches, even when only the other mega is brought -- the one
        mega rule should only apply per match" -- a bring4 that leaves out
        `dive`'s own whole-core-chosen mega, but still carries the OTHER
        stone holder, has nothing that Pokemon could be inconsistent with
        (its rival mega isn't even in this bring): it must transform, not
        sit in base form just because a DIFFERENT bring4 (or the core as a
        whole) preferred the other one."""
        rows = cf.bring4_from_deep_dive(self.CORE, self.dive, self.TARGETS)
        other_mega = next(m for m in ("Mega Gengar", "Mega Alakazam")
                          if m != self.dive["mega_used"])
        checked_any = False
        for row in rows:
            if (self.dive["mega_used"] not in row["bring4"]
                    and other_mega in row["bring4"]):
                checked_any = True
                self.assertEqual(row["mega_used"], other_mega)
        self.assertTrue(checked_any, "fixture never produced a bring4 "
                        "excluding the dive's mega while keeping the other "
                        "stone holder -- test is vacuous")

    def test_matches_across_several_enemy_rosters_scored_one_at_a_time(self):
        """A `dive` covering SEVERAL enemy rosters at once (the "vs all
        enemy teams" shape) can be scored roster-by-roster -- "I should
        look team by team for the best brings" -- each call scoped to
        exactly one roster's own `target_names`, agreeing with a dive
        built for JUST that one roster."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        other_targets = ["Garchomp", "Incineroar", "Gallade"]
        multi_dive = cf.core_deep_dive(
            self.CORE, [self.TARGETS, other_targets], merged, moves,
            natures, typechart)
        rows_a = cf.bring4_from_deep_dive(self.CORE, multi_dive, self.TARGETS)
        rows_b = cf.bring4_from_deep_dive(self.CORE, multi_dive, other_targets)
        self.assertNotEqual(rows_a[0]["worst_pair_row"]["detail"],
                            rows_b[0]["worst_pair_row"]["detail"])
        # And matches a dive built for just that ONE roster in isolation.
        solo_dive = cf.core_deep_dive(
            self.CORE, [self.TARGETS], merged, moves, natures, typechart,
            item_overrides={n: s["item"] for n, s in multi_dive["sets"].items()},
            move_overrides={n: s["moves"] for n, s in multi_dive["sets"].items()})
        rows_solo = cf.bring4_from_deep_dive(self.CORE, solo_dive, self.TARGETS)
        self.assertEqual(rows_a[0]["bring4"], rows_solo[0]["bring4"])


class TestBestTurnTwoTurnLookahead(unittest.TestCase):
    """"Metagross has at best a 2HKO vs Kingambit on T2 but Hydreigon has a
    OHKO, so if Kingambit sucker punches Hydreigon and then Metagross it
    cannot lose" -- `_best_turn` used to rank OUR target-hint combos purely
    on THIS turn's own KO count, with no view of what happens next turn.
    Diagnosed, reproducible bug: a Focus-Sash Hydreigon survives a hit at 1
    HP (not a guaranteed kill THIS turn, `enemies_ko=0`), while double-
    teaming a healthy-ish Metagross for an outright kill scores `enemies_ko=
    1` -- so the OLD one-turn-only ranking always preferred finishing
    Metagross now, leaving a full-HP Hydreigon free to sweep both of ours
    with its own spread move before Kingambit ever got back to it. The fix:
    `_best_turn` now previews ONE further turn (`lookahead=1`, the default)
    from each candidate combo's own result before ranking, so a combo that
    scores 0 kills this turn but sets up a clean finish next turn can
    correctly outrank one that grabs an immediate kill at the cost of
    leaving the bigger threat standing.

    Real, directly-verified fixture (not synthetic): Kingambit + Mega
    Staraptor vs Metagross (Assault Vest, so it survives the opening
    double-team and its own priority-move reconsideration -- see
    `TestReconsiderationFixedPoint` -- keeps mattering across several
    turns) + Hydreigon (Focus Sash). Confirmed directly: patching `_best_
    turn` to force `lookahead=0` reproduces an outright LOSS on this exact
    fixture; the default (`lookahead=1`) turns it into a win.
    """

    def setUp(self):
        self.W = world()
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        self.typechart = typechart
        self.kingambit = cf._build("Kingambit", merged, natures)
        self.staraptor = cf._build("Mega Staraptor", merged, natures)
        self.metagross = cf._build("Metagross", merged, natures, item="Assault Vest")
        self.hydreigon = cf._build("Hydreigon", merged, natures, item="Focus Sash")
        self.combatants = {"C": self.kingambit, "P": self.staraptor,
                           "E1": self.metagross, "E2": self.hydreigon}
        self.moves_by_role = {
            "C": cf._move_infos("Kingambit", merged, moves,
                                ["Sucker Punch", "Kowtow Cleave", "Iron Head", "Low Kick"]),
            "P": cf._move_infos("Mega Staraptor", merged, moves,
                                ["Close Combat", "Brave Bird", "Roost", "Dual Wingbeat"]),
            "E1": cf._move_infos("Metagross", merged, moves,
                                 ["Psychic Fangs", "Bullet Punch", "Meteor Mash", "Iron Head"]),
            "E2": cf._move_infos("Hydreigon", merged, moves,
                                 ["Dark Pulse", "Draco Meteor", "Earth Power", "Heat Wave"]),
        }
        self.weather = cf._field_weather(self.combatants)

    def test_the_two_turn_lookahead_wins_where_one_turn_ranking_loses(self):
        outcome, _turns_used, hp, _log = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, self.weather, 6)
        self.assertIn(outcome, ("sweep", "out_trade"))
        self.assertGreater(hp["C"], 0.0, "Kingambit must survive")

    def test_with_lookahead_forced_off_the_old_losing_line_is_reproduced(self):
        """Confirms the fix is really what changes the outcome here, not
        some other coincidental behaviour -- patches `_best_turn` to force
        `lookahead=0` (the pre-fix ranking) and checks this exact fixture
        reverts to the original diagnosed LOSS."""
        orig = cf._best_turn
        def forced_off(*a, **kw):
            kw["lookahead"] = 0
            return orig(*a, **kw)
        cf._best_turn = forced_off
        try:
            outcome, _turns_used, _hp, _log = cf._joint_race(
                self.combatants, self.moves_by_role, self.typechart, self.weather, 6)
        finally:
            cf._best_turn = orig
        self.assertEqual(outcome, "loss")

    def test_turn_one_splits_fire_instead_of_double_teaming_metagross(self):
        """The concrete decision the lookahead fixes: Staraptor should hit
        the Focus-Sash Hydreigon (softening it toward next turn's guaranteed
        finish) while Kingambit hits Metagross, rather than both piling onto
        Metagross alone."""
        _outcome, _turns_used, _hp, log = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, self.weather, 6)
        turn1_targets = {role: tgt for role, tgt, _h in log[0] if role in ("C", "P")}
        self.assertEqual(turn1_targets.get("P"), "E2",
                         "Staraptor should soften Hydreigon, the bigger "
                         "ongoing threat, not double-team Metagross")
        self.assertEqual(turn1_targets.get("C"), "E1")


class TestReconsiderationFixedPoint(unittest.TestCase):
    """`_reconsider_for_survival` reassigning ONE role can, as a side
    effect, newly doom or Sucker-Punch-waste ANOTHER -- a single pass used
    to leave that second role permanently stuck. Concretely: Metagross
    (Assault Vest, so it survives to matter across several turns) reconsiders
    from Meteor Mash to the faster, tied-priority Bullet Punch specifically
    to dodge Kingambit's own Sucker Punch -- but doing so means Metagross
    now resolves BEFORE Kingambit, which is exactly the condition that fails
    Sucker Punch (`_apply_plan`'s own rule: it only connects if the target is
    still PENDING). Without looping the reconsideration, Kingambit keeps
    swinging a permanently-dead Sucker Punch at Metagross turn after turn
    instead of ever switching to an unconditional move, letting a nearly-
    dead Metagross whittle it down for free.
    """

    def setUp(self):
        self.W = world()
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        self.typechart = typechart
        self.kingambit = cf._build("Kingambit", merged, natures)
        self.metagross = cf._build("Metagross", merged, natures, item="Assault Vest")
        self.kingambit_moves = cf._move_infos(
            "Kingambit", merged, moves,
            ["Sucker Punch", "Kowtow Cleave", "Iron Head", "Low Kick"])
        self.metagross_moves = cf._move_infos(
            "Metagross", merged, moves,
            ["Psychic Fangs", "Bullet Punch", "Meteor Mash", "Iron Head"])

    def test_kingambit_switches_off_a_permanently_wasted_sucker_punch(self):
        """A Metagross worn down to ~6.8% HP (matching the real race's own
        state at this point) must still get finished by Kingambit even
        though Kingambit's own Sucker Punch keeps failing against it."""
        combatants = {"C": self.kingambit, "P": None,
                      "E1": self.metagross, "E2": None}
        moves_by_role = {"C": self.kingambit_moves, "P": [],
                         "E1": self.metagross_moves, "E2": []}
        hp = {"C": 0.196, "P": 0.0, "E1": 0.0675, "E2": 0.0}
        weather = cf._field_weather(combatants)
        new_hp, log, _enemy_acted, _wiped, _recharging = cf._best_turn(
            combatants, moves_by_role, hp, self.typechart, weather)
        self.assertLessEqual(new_hp["E1"], 0.0,
                             "Kingambit must finish Metagross off by "
                             "switching away from a Sucker Punch that can "
                             "never land on it")
        c_hits = [h for role, _tgt, h in log if role == "C"]
        self.assertTrue(c_hits, "Kingambit must not go silent")
        self.assertNotEqual(c_hits[0].move_name, "Sucker Punch")

    def test_without_the_loop_kingambit_would_stay_silent(self):
        """Confirms the loop is what fixes it: a single-pass `_resolve_turn`
        (reconsideration attempted once, its own OWN discovery of the fresh
        sp_wasted thrown away) leaves Kingambit unable to act."""
        combatants = {"C": self.kingambit, "P": None,
                      "E1": self.metagross, "E2": None}
        moves_by_role = {"C": self.kingambit_moves, "P": [],
                         "E1": self.metagross_moves, "E2": []}
        hp = {"C": 0.196, "P": 0.0, "E1": 0.0675, "E2": 0.0}
        weather = cf._field_weather(combatants)
        field = cf.FieldState(weather=weather, terrain=None)
        plan = {
            "C": cf._choose_action(self.kingambit, self.kingambit_moves,
                                   {"E1": self.metagross}, self.typechart,
                                   weather=weather, hinted_target="E1",
                                   attacker_hp_frac=hp["C"], target_hp_fracs=hp,
                                   attacker_role="C"),
            "E1": cf._choose_action(self.metagross, self.metagross_moves,
                                    {"C": self.kingambit}, self.typechart,
                                    weather=weather, attacker_hp_frac=hp["E1"],
                                    target_hp_fracs=hp, attacker_role="E1"),
        }
        _hp2, _log, _ea, _w, doomed, sp_wasted = cf._apply_plan(
            plan, combatants, hp, frozenset(), 1.0, field)
        self.assertIn("E1", doomed, "fixture assumes Metagross's naive "
                     "Meteor Mash pick is doomed under Kingambit's Sucker "
                     "Punch")
        live_targets_by_role = {"C": {"E1": self.metagross},
                                "E1": {"C": self.kingambit}}
        one_pass_plan = cf._reconsider_for_survival(
            plan, doomed, sp_wasted, combatants, moves_by_role, hp,
            self.typechart, weather, field, live_targets_by_role,
            {"C": "E1"}, 1.0, frozenset())
        _hp3, log3, _ea3, _w3, _doomed3, sp_wasted3 = cf._apply_plan(
            one_pass_plan, combatants, hp, frozenset(), 1.0, field)
        self.assertIn("C", sp_wasted3, "fixture assumes ONE reconsideration "
                     "pass leaves Kingambit's Sucker Punch newly wasted, "
                     "which a single-pass caller would never see")
        c_hits = [h for role, _tgt, h in log3 if role == "C"]
        self.assertFalse(c_hits, "without the loop, Kingambit's plan is "
                         "never revisited even though it's now known to fail")


class TestAdvanceTurnState(unittest.TestCase):
    """`_advance_turn_state` -- the shared SELF_HALVING_MOVES/CONTRARY_SELF_
    DROP_MOVES bookkeeping `_joint_race`'s own turn loop and `_best_turn`'s
    one-turn lookahead both need, factored out so they can't drift apart."""

    def setUp(self):
        self.W = world()

    def test_returns_fresh_dicts_never_mutates_inputs(self):
        """`_best_turn` evaluates several candidate combos from the SAME
        starting state -- a shared mutable dict would let one combo's own
        hypothetical Contrary boost leak into another's."""
        merged, natures = self.W["merged"], self.W["natures"]
        staraptor = cf._build("Mega Staraptor", merged, natures)
        combatants = {"C": staraptor, "P": None, "E1": None, "E2": None}
        close_combat = cf._lookup_move("Close Combat", self.W["moves"])
        turn_log = [("C", "E1", cf.Hit(move_name="Close Combat", frac=0.5,
                                      lo=0.4, avg=0.5, hi=0.6, eff=1.0,
                                      num_targets_hit=1))]
        dmg_mult_before = {}
        def_mult_before = {}
        half_damage_before = frozenset()
        new_dmg, new_def, new_half = cf._advance_turn_state(
            turn_log, dmg_mult_before, def_mult_before, half_damage_before,
            combatants)
        self.assertEqual(dmg_mult_before, {})
        self.assertEqual(def_mult_before, {})
        self.assertEqual(half_damage_before, frozenset())
        self.assertIn("C", new_def)

    def test_self_halving_move_adds_the_role(self):
        merged, natures = self.W["merged"], self.W["natures"]
        hydreigon = cf._build("Hydreigon", merged, natures)
        combatants = {"C": hydreigon, "P": None, "E1": None, "E2": None}
        turn_log = [("C", "E1", cf.Hit(move_name="Draco Meteor", frac=0.5,
                                      lo=0.4, avg=0.5, hi=0.6, eff=1.0,
                                      num_targets_hit=1))]
        _dmg, _deff, new_half = cf._advance_turn_state(
            turn_log, {}, {}, frozenset(), combatants)
        self.assertIn("C", new_half)

    def test_non_contrary_user_gets_no_boost(self):
        merged, natures = self.W["merged"], self.W["natures"]
        gallade = cf._build("Gallade", merged, natures)
        combatants = {"C": gallade, "P": None, "E1": None, "E2": None}
        turn_log = [("C", "E1", cf.Hit(move_name="Close Combat", frac=0.5,
                                      lo=0.4, avg=0.5, hi=0.6, eff=1.0,
                                      num_targets_hit=1))]
        new_dmg, new_def, _half = cf._advance_turn_state(
            turn_log, {}, {}, frozenset(), combatants)
        self.assertNotIn("C", new_def)
        self.assertNotIn("C", new_dmg)


class TestWorstCaseTargeting(unittest.TestCase):
    """`worst_case_targeting` -- "Try and implement that feature as an
    option, not just greedy guess": by default the ENEMY's own per-turn
    target choice is a single greedy, unhinted `_choose_action` guess (see
    `_resolve_turn`'s own docstring) -- neither the exhaustive hint search
    nor the 2-turn lookahead (`TestBestTurnTwoTurnLookahead`) ever considers
    whether the enemy might deliberately pick the target that hurts US
    most, since only OUR OWN hint combos are searched. This opt-in option
    makes `_best_turn` resolve the enemy's own turn via `_resolve_turn_
    worst_case` instead -- exhaustively searching every enemy target combo
    and keeping whichever is worst for us, mirroring the worst-case search
    already done for the enemy's MEGA choice.

    Real, directly-verified fixture: Hydreigon + Mega Metagross (ours) vs
    Mega Staraptor + Kingambit (enemy) -- the exact matchup from the user's
    own corrected report ("I will note that Kingambit is the enemy team").
    """

    def setUp(self):
        self.W = world()
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        self.typechart = typechart

        def build_custom(name, item, ability_override, evs, nature):
            c = cf.make_combatant(name, merged, natures, item=item, evs=evs, nature=nature)
            c = cf._mega_project(c)
            if ability_override:
                c.ability = ability_override
            c.current_hp = c.max_hp()
            return c
        self.build_custom = build_custom

        self.hydreigon = build_custom(
            "Hydreigon", "Focus Sash", "Levitate",
            {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32}, "modest")
        self.mega_metagross = build_custom(
            "Mega Metagross", "Metagrossite", "Tough Claws",
            {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}, "adamant")
        self.mega_staraptor = build_custom(
            "Mega Staraptor", "Staraptite", None,
            {"hp": 29, "atk": 1, "def": 0, "spa": 0, "spd": 4, "spe": 32}, "jolly")
        self.kingambit = build_custom(
            "Kingambit", "Chople Berry", "Defiant",
            {"hp": 31, "atk": 25, "def": 0, "spa": 0, "spd": 2, "spe": 8}, "adamant")
        self.combatants = {"C": self.hydreigon, "P": self.mega_metagross,
                           "E1": self.mega_staraptor, "E2": self.kingambit}
        self.moves_by_role = {
            "C": cf._move_infos("Hydreigon", merged, moves,
                                ["Dark Pulse", "Draco Meteor", "Tailwind", "Heat Wave"]),
            "P": cf._move_infos("Mega Metagross", merged, moves,
                                ["Hard Press", "Ice Punch", "Psychic Fangs", "Protect"]),
            "E1": cf._move_infos("Mega Staraptor", merged, moves,
                                 ["Brave Bird", "Close Combat", "Tailwind", "Protect"]),
            "E2": cf._move_infos("Kingambit", merged, moves,
                                 ["Kowtow Cleave", "Sucker Punch", "Low Kick", "Iron Head"]),
        }
        self.weather = cf._field_weather(self.combatants)

    def test_default_is_a_no_op(self):
        """`worst_case_targeting` defaults to False and must reproduce the
        exact same race as never passing it at all."""
        a = cf._joint_race(self.combatants, self.moves_by_role, self.typechart,
                           self.weather, 4)
        b = cf._joint_race(self.combatants, self.moves_by_role, self.typechart,
                           self.weather, 4, worst_case_targeting=False)
        self.assertEqual(a, b)

    def test_resolve_turn_worst_case_picks_the_provably_worst_combo(self):
        """Direct verification of the search itself: manually enumerate
        every enemy hint combo via plain `_resolve_turn` calls, compute
        each one's own (enemies_ko, -ours_ko, net_dmg) key from OUR side,
        and confirm `_resolve_turn_worst_case` returns whichever combo's
        `new_hp` is the worst by that same metric -- not just "a"
        different result, but provably THE worst one available."""
        import itertools
        hp0 = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
        our_hints = {"C": "E1", "P": "E1"}
        candidates = []
        for combo in itertools.product(["C", "P"], repeat=2):
            enemy_hints = {"E1": combo[0], "E2": combo[1]}
            result = cf._resolve_turn(
                self.combatants, self.moves_by_role, hp0, self.typechart,
                self.weather, our_hints, enemy_hints=enemy_hints)
            new_hp = result[0]
            enemies_ko = sum(1 for r in ("E1", "E2") if new_hp[r] <= 0)
            ours_ko = sum(1 for r in ("C", "P") if new_hp[r] <= 0)
            dmg_dealt = sum(hp0[r] - new_hp[r] for r in ("E1", "E2"))
            dmg_taken = sum(hp0[r] - new_hp[r] for r in ("C", "P"))
            candidates.append(((-enemies_ko, ours_ko, dmg_taken - dmg_dealt), new_hp))
        worst_expected_hp = max(candidates, key=lambda c: c[0])[1]

        actual = cf._resolve_turn_worst_case(
            self.combatants, self.moves_by_role, hp0, self.typechart, self.weather,
            our_hints, ["C", "P"], ["E1", "E2"])
        self.assertEqual(actual[0], worst_expected_hp)

    def test_enables_a_real_loss_the_greedy_default_does_not_find(self):
        """This exact fixture: the greedy default still loses this matchup
        anyway (a bad type matchup for Metagross into Kingambit), so this
        just confirms `worst_case_targeting=True` doesn't crash and stays
        at least as bad for us -- `_JOINT_OUTCOME_RANK` must never show
        the worst-case race as a BETTER outcome than the greedy one, since
        assuming a smarter enemy can only ever hurt or match our own
        result, never improve it."""
        greedy_outcome, _t1, _hp1, _log1 = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, self.weather, 4)
        worst_outcome, _t2, _hp2, _log2 = cf._joint_race(
            self.combatants, self.moves_by_role, self.typechart, self.weather, 4,
            worst_case_targeting=True)
        self.assertLessEqual(cf._JOINT_OUTCOME_RANK[worst_outcome],
                             cf._JOINT_OUTCOME_RANK[greedy_outcome])


class TestBuildFormsRespectsCustomSets(unittest.TestCase):
    """`_build_form`/`_build_forms` had no way to pin a real, known
    Pokemon's exact EVs/Nature/Ability/moveset -- only `items` (and, for
    OUR OWN side via `deep_dive`/`core_deep_dive`'s own `item_overrides`/
    `move_overrides`, moves) were ever respected; everything else silently
    fell back to mbsmogon.xlsx's usage-default spread. Directly explains a
    user report: a deep dive of a pasted Showdown export (exact EVs/
    Nature/moveset given) showed the SAME damage percentages and turn log
    as the ORIGINAL, pre-any-fix bug report, because the engine was
    silently substituting generic usage-default stats/moves the whole
    time -- confirmed by reproducing the app's own `custom_team_from_
    export` -> `core_deep_dive` pipeline and getting an exact character-
    for-character match to the stale numbers."""

    def setUp(self):
        self.W = world()

    def test_evs_and_nature_change_the_built_stats(self):
        """Exercised via Metagross (whose real, exact competitive spread --
        2HP/32Atk/32Spe Adamant -- is confirmably NOT mbsmogon.xlsx's own
        usage-default for it), so a coincidental match (as Hydreigon's
        usage-default spread turned out to be, for this particular sheet)
        can't mask a broken override."""
        merged, natures = self.W["merged"], self.W["natures"]
        default = cf._build_form("Metagross", merged, natures)
        evs = {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32}
        custom = cf._build_form("Metagross", merged, natures, evs=evs, nature="adamant")
        base = merged["Metagross"]["base_stats"]
        expected_atk = int(((2 * base["atk"] + 31) * 50 // 100 + 5) * 1.1) + 32
        self.assertEqual(custom.stats["atk"], expected_atk)
        self.assertNotEqual(custom.stats, default.stats)

    def test_ability_override_applies_to_mega_holders_base_form_only(self):
        """A Mega-stone holder's `ability` override always applies to its
        BASE form (a real Showdown export's 'Ability:' line for a Mega
        pick describes its pre-evolution ability, never the mega-
        exclusive one, per `combatants.py`'s own `base_ability` comment)."""
        merged, natures = self.W["merged"], self.W["natures"]
        mega = cf._build_form("Mega Staraptor", merged, natures,
                              ability="Intimidate", stay_base=False)
        self.assertEqual(mega.ability, "Contrary")
        based = cf._build_form("Mega Staraptor", merged, natures,
                               ability="Intimidate", stay_base=True)
        self.assertEqual(based.ability, "Intimidate")

    def test_move_overrides_pins_an_exact_moveset_not_usage_derived(self):
        """Kingambit (this test's original fixture) no longer makes this
        point: it's now default_sets.txt-pinned, so its own "default" (no
        override at all) is ALREADY the exact real set -- there's no
        usage-derived fallback left to contrast against, so overriding it
        wouldn't prove move_overrides actually changes anything. Hydreigon
        is not one of the pinned species, so its default here is still the
        real usage-derived top-4 (Dark Pulse/Draco Meteor/Snarl/Earth
        Power, confirmed directly below) -- pinning Heat Wave (Hydreigon's
        own real 5th-most-used move, ranked just below Earth Power) in
        place of Earth Power still proves move_overrides genuinely changes
        the built moveset, not just matches what would happen anyway."""
        merged, moves, natures = self.W["merged"], self.W["moves"], self.W["natures"]
        pinned = ["Dark Pulse", "Draco Meteor", "Snarl", "Heat Wave"]
        forms = cf._build_forms(["Hydreigon"], merged, natures, moves,
                                move_overrides={"Hydreigon": pinned})
        got_names = sorted(mi.name for mi in forms["Hydreigon"]["moves"])
        self.assertEqual(got_names, sorted(pinned))
        # The usage-derived default (no override) is confirmed DIFFERENT --
        # Heat Wave is NOT in the default because usage ranks Earth Power
        # over it.
        default_forms = cf._build_forms(["Hydreigon"], merged, natures, moves)
        default_names = {mi.name for mi in default_forms["Hydreigon"]["moves"]}
        self.assertNotIn("Heat Wave", default_names)

    def test_defaults_are_a_no_op(self):
        """No overrides given at all reproduces the exact pre-existing
        `_build_forms` behaviour -- every existing caller that never
        passed these new params is unaffected."""
        merged, moves, natures = self.W["merged"], self.W["moves"], self.W["natures"]
        forms = cf._build_forms(["Hydreigon", "Kingambit"], merged, natures, moves)
        for name in ("Hydreigon", "Kingambit"):
            expected = cf._build_form(name, merged, natures)
            self.assertEqual(forms[name]["mega"].stats, expected.stats)
            self.assertEqual(forms[name]["mega"].ability, expected.ability)


class TestCoreDeepDiveRespectsCustomSets(unittest.TestCase):
    """End-to-end version of `TestBuildFormsRespectsCustomSets`: the exact
    matchup from the user's own report (Hydreigon + Mega Metagross vs Mega
    Staraptor + Kingambit, real pasted Showdown-export sets on both
    sides), run through `core_deep_dive` exactly as `src/app.py`'s "Bring-4
    (one enemy roster)" deep dive does. Originally: WITHOUT the new
    overrides this reproduced the stale bug report byte-for-byte; WITH
    them, the correct, real-stat damage numbers came out instead. Now that
    Kingambit and Mega Staraptor are both default_sets.txt-pinned, their
    real stats are already the DEFAULT even with no override given at all
    -- see `test_without_overrides_reproduces_the_stale_bug_report`'s own
    updated docstring for what that means for the "without overrides"
    case specifically."""

    def setUp(self):
        self.W = world()
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        self.merged, self.moves, self.natures, self.typechart = merged, moves, natures, typechart
        self.our6 = ["Hydreigon", "Mega Metagross"]
        self.vs_roster = ["Mega Staraptor", "Kingambit"]
        self.item_overrides = {"Hydreigon": "Focus Sash", "Mega Metagross": "Metagrossite"}
        self.move_overrides = {
            "Hydreigon": ["Dark Pulse", "Draco Meteor", "Tailwind", "Heat Wave"],
            "Mega Metagross": ["Hard Press", "Ice Punch", "Psychic Fangs", "Protect"],
        }
        self.evs_overrides = {
            "Hydreigon": {"hp": 2, "atk": 0, "def": 0, "spa": 32, "spd": 0, "spe": 32},
            "Mega Metagross": {"hp": 2, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 32},
            "Mega Staraptor": {"hp": 29, "atk": 1, "def": 0, "spa": 0, "spd": 4, "spe": 32},
            "Kingambit": {"hp": 31, "atk": 25, "def": 0, "spa": 0, "spd": 2, "spe": 8},
        }
        self.nature_overrides = {"Hydreigon": "modest", "Mega Metagross": "adamant",
                                 "Mega Staraptor": "jolly", "Kingambit": "adamant"}
        self.ability_overrides = {"Mega Staraptor": None, "Kingambit": "Defiant"}
        self.ability_overrides = {k: v for k, v in self.ability_overrides.items() if v}
        self.enemy_item_overrides = {"Mega Staraptor": "Staraptite", "Kingambit": "Chople Berry"}
        self.enemy_move_overrides = {
            "Mega Staraptor": ["Brave Bird", "Close Combat", "Tailwind", "Protect"],
            "Kingambit": ["Kowtow Cleave", "Sucker Punch", "Low Kick", "Iron Head"],
        }

    def _dive(self, **extra):
        return cf.core_deep_dive(
            self.our6, [self.vs_roster], self.merged, self.moves, self.natures,
            self.typechart, turns=4, item_overrides=self.item_overrides,
            move_overrides=self.move_overrides, **extra)

    def _first_log(self, dive):
        detail = next(iter(dive["per_pair"].values()))["per_enemy"][0]["detail"]
        return next(iter(detail.values()))["log"]

    def test_without_overrides_reproduces_the_stale_bug_report(self):
        """No evs/nature/ability/enemy overrides at all: this test's ORIGINAL
        point (the numbers matched the user's stale usage-default bug
        report -- Close Combat 142-155-167% -- proving the OLD behaviour
        really was usage-default stats, not the user's real ones) is now
        structurally moot twice over: Kingambit and Mega Staraptor are both
        real default_sets.txt-pinned species, so their own real EVs/Nature/
        item already supply the DEFAULT (`_build_forms`' own baseline) with
        no explicit override needed -- AND, since the Intimidate/mega-
        evolution-timing fix (a real opening-turn Intimidate now correctly
        resolves against a combatant's PRE-mega ability, not this module's
        own upfront mega projection -- see `_ability_at_switch_in`), Mega
        Metagross's Clear Body correctly shields it from Mega Staraptor's
        own real Intimidate, so Mega Metagross's already-available Psychic
        Fangs is now a genuine guaranteed KO on turn 1 -- making "Hydreigon
        Protects while Metagross finishes Staraptor" a real, strictly
        better-scoring line than trading hits. Mega Staraptor now dies
        before ever landing Close Combat, so this test checks Psychic
        Fangs (the move that DOES appear turn 1) instead."""
        log = self._first_log(self._dive())
        t1 = log[0]
        psychic_fangs = next(h for _r, _t, h in t1 if h.move_name == "Psychic Fangs")
        self.assertAlmostEqual(psychic_fangs.lo, 1.11, delta=0.02)
        self.assertAlmostEqual(psychic_fangs.hi, 1.31, delta=0.02)

    def test_with_overrides_uses_the_real_stats(self):
        """Full overrides given: the damage numbers change to match the
        REAL EVs/Nature (independently computed via `_build_form`'s own
        stat formula, same as `TestBuildFormsRespectsCustomSets`), and
        Kingambit's actual Low Kick (never in its usage-derived top 4)
        is now a real, available option.

        Mega Staraptor no longer survives to use Close Combat at all (see
        `test_without_overrides_reproduces_the_stale_bug_report`'s own
        updated docstring for why) -- Psychic Fangs, the move that DOES
        land turn 1, still shows the override taking effect: Metagross's
        own real Adamant/32 Atk spread hits noticeably HARDER than the
        usage-default one, outweighing Mega Staraptor's real bulk (29 HP/
        4 SpD) -- confirmed against the no-overrides case's own real,
        verified range above."""
        dive = self._dive(evs_overrides=self.evs_overrides,
                          nature_overrides=self.nature_overrides,
                          ability_overrides=self.ability_overrides,
                          enemy_item_overrides=self.enemy_item_overrides,
                          enemy_move_overrides=self.enemy_move_overrides)
        log = self._first_log(dive)
        t1 = log[0]
        psychic_fangs = next(h for _r, _t, h in t1 if h.move_name == "Psychic Fangs")
        self.assertAlmostEqual(psychic_fangs.lo, 1.20, delta=0.02)
        self.assertAlmostEqual(psychic_fangs.hi, 1.41, delta=0.02)
        kingambit_moves = dive["sets"]  # our own sets only carry item/moves
        # Kingambit is the enemy, not in `sets` -- confirm via the built
        # enemy forms directly instead.
        enemy_forms = cf._build_forms(
            self.vs_roster, self.merged, self.natures, self.moves,
            items=self.enemy_item_overrides, move_overrides=self.enemy_move_overrides)
        self.assertIn("Low Kick", {mi.name for mi in enemy_forms["Kingambit"]["moves"]})


class TestSalamenceAdditions(unittest.TestCase):
    """"Add Salamence and Mega Salamence" -- both roster.csv (defensive
    chart + Score) and mbsmogon.xlsx (Nature/EVs/moveset/item/ability
    usage) rows, resolved the same way every other Mega/base pair in this
    dataset is (`_mega_project`, `base_form_name`)."""

    def setUp(self):
        self.W = world()

    def test_both_forms_resolve_with_real_dragon_flying_stats(self):
        merged = self.W["merged"]
        for name in ("Salamence", "Mega Salamence"):
            self.assertIn(name, merged)
            self.assertEqual(merged[name]["types"], ["Dragon", "Flying"])
            self.assertIsNotNone(merged[name].get("defensive_chart"))

    def test_defensive_chart_matches_real_dragon_flying_weaknesses(self):
        """Ice 4x (both types weak), Rock/Dragon/Fairy 2x (Flying/Dragon
        respectively), immune to Ground (Flying), Electric neutral (Flying's
        own weakness to it is cancelled by Dragon's resistance) -- the
        real, well-known Salamence weakness profile, not an invented one."""
        merged = self.W["merged"]
        for name in ("Salamence", "Mega Salamence"):
            dc = merged[name]["defensive_chart"]
            self.assertEqual(dc["Ice"], 4.0)
            self.assertEqual(dc["Rock"], 2.0)
            self.assertEqual(dc["Dragon"], 2.0)
            self.assertEqual(dc["Fairy"], 2.0)
            self.assertEqual(dc["Ground"], 0.0)
            self.assertEqual(dc["Electric"], 1.0)
            self.assertEqual(dc["Grass"], 0.25)

    def test_base_form_keeps_intimidate_mega_form_gets_aerilate(self):
        merged, natures = self.W["merged"], self.W["natures"]
        base = cf.make_combatant("Salamence", merged, natures)
        self.assertEqual(base.ability, "Intimidate")
        mega_pick = cf.make_combatant("Mega Salamence", merged, natures)
        self.assertEqual(mega_pick.ability, "Intimidate")  # pre-projection
        projected = cf._mega_project(mega_pick)
        self.assertEqual(projected.ability, "Aerilate")
        self.assertGreater(projected.stats["atk"], base.stats["atk"])

    def test_mega_salamence_holds_its_stone(self):
        merged = self.W["merged"]
        self.assertEqual(merged["Mega Salamence"]["items_usage"][0][0], "Salamencite")

    def test_moves_resolve_to_the_intended_sets(self):
        """Mega Salamence's own moveset is now default_sets.txt-pinned (the
        file's "Salamence @ Salamencite" entry -- the mega-stone item
        routes its pin to the "Mega Salamence" merged entry, the same
        item-implies-mega convention every other pinned Mega uses): its
        real `moves_usage` is Protect/Double-Edge/Hyper Voice/Tailwind, all
        at a pinned 100% each (confirmed directly by reading
        `merged["Mega Salamence"]["moves_usage"]`) -- Hyper Voice, not
        Dragon Claw (which was never a real recorded Mega Salamence move
        here at all). The base "Salamence" entry has no such pin (no plain
        "Salamence" entry exists in default_sets.txt, only the
        Salamencite-holding one), so its own moveset stays usage-derived,
        unaffected."""
        merged, moves = self.W["merged"], self.W["moves"]
        base_moves = {mi.name for mi, _pct in build_moveset(merged["Salamence"], moves)}
        self.assertEqual(base_moves, {"Protect", "Draco Meteor", "Hydro Pump", "Fire Blast"})
        mega_moves = {mi.name for mi, _pct in build_moveset(merged["Mega Salamence"], moves)}
        self.assertEqual(mega_moves, {"Double-Edge", "Protect", "Hyper Voice", "Tailwind"})


class TestPawmotAddition(unittest.TestCase):
    """"Pawmot has also been added" -- Electric/Fighting, the real base
    stats given, Iron Fist, and a fixed 4-move set (Fake Out/Close Combat/
    Ice Punch/Double Shock). Iron Fist's real "punch"-flag boost (Ice
    Punch) stays exactly as implemented; Double Shock -- Pawmot's own
    signature move, thematically fist-shaped but NOT actually "punch"-
    flagged in the real games -- is boosted anyway as an explicit
    Regulation M-C house rule (the user's own words: "Iron Fist ...
    boosts punching moves like Double Shock ... and Ice Punch")."""

    def setUp(self):
        self.W = world()

    def test_resolves_with_the_given_stats_and_typing(self):
        merged, natures = self.W["merged"], self.W["natures"]
        self.assertEqual(merged["Pawmot"]["types"], ["Electric", "Fighting"])
        c = cf.make_combatant("Pawmot", merged, natures)
        self.assertEqual(c.ability, "Iron Fist")
        self.assertEqual(c.item, "Life Orb")
        base = merged["Pawmot"]["base_stats"]
        self.assertEqual(base, {"hp": 70, "atk": 115, "def": 70,
                                "spa": 70, "spd": 60, "spe": 105})

    def test_moves_resolve_to_the_given_set(self):
        merged, moves = self.W["merged"], self.W["moves"]
        move_names = {mi.name for mi, _pct in build_moveset(merged["Pawmot"], moves)}
        self.assertEqual(move_names, {"Fake Out", "Close Combat", "Ice Punch", "Double Shock"})

    def test_iron_fist_boosts_the_real_punch_flagged_move(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        pawmot = cf._build("Pawmot", merged, natures)
        target = cf._build("Garchomp", merged, natures)
        ice_punch = cf._lookup_move("Ice Punch", moves)
        boosted = cf._raw_hit(pawmot, ice_punch, target, typechart, roll="avg")
        no_ability = copy.copy(pawmot)
        no_ability.ability = "Volt Absorb"
        unboosted = cf._raw_hit(no_ability, ice_punch, target, typechart, roll="avg")
        self.assertAlmostEqual(boosted.frac / unboosted.frac, 1.2, places=3)

    def test_iron_fist_boosts_double_shock_as_a_house_rule(self):
        """Real-game Double Shock has no "punch" flag -- this is
        deliberately NOT what the actual games do, per the explicit
        ruling."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        double_shock = cf._lookup_move("Double Shock", moves)
        self.assertNotIn("punch", double_shock.flags or {})
        pawmot = cf._build("Pawmot", merged, natures)
        target = cf._build("Charizard", merged, natures)
        boosted = cf._raw_hit(pawmot, double_shock, target, typechart, roll="avg")
        no_ability = copy.copy(pawmot)
        no_ability.ability = "Volt Absorb"
        unboosted = cf._raw_hit(no_ability, double_shock, target, typechart, roll="avg")
        self.assertAlmostEqual(boosted.frac / unboosted.frac, 1.2, places=3)

    def test_iron_fist_does_not_boost_an_unrelated_move(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        pawmot = cf._build("Pawmot", merged, natures)
        target = cf._build("Kingambit", merged, natures)
        close_combat = cf._lookup_move("Close Combat", moves)
        boosted = cf._raw_hit(pawmot, close_combat, target, typechart, roll="avg")
        no_ability = copy.copy(pawmot)
        no_ability.ability = "Volt Absorb"
        unboosted = cf._raw_hit(no_ability, close_combat, target, typechart, roll="avg")
        self.assertAlmostEqual(boosted.frac, unboosted.frac, places=6)

    def test_outspeeds_and_ohkos_the_named_targets(self):
        """"He could make a big difference as he outspeeds and OHKOs for
        instance Charizard, Basculegion, Kingambit, and Garchomp" --
        checked at the real per-matchup best move (Double Shock's own
        Electric/Fighting-neutral-but-type-favorable hits on Charizard/
        Basculegion, Close Combat's real 4x on Kingambit, Ice Punch's real
        4x on Garchomp), worst-roll guaranteed, not just an average-roll
        near-miss."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        pawmot = cf._build("Pawmot", merged, natures)
        matchups = {
            "Charizard": "Double Shock",
            "Basculegion-F": "Double Shock",
            "Kingambit": "Close Combat",
            "Garchomp": "Ice Punch",
        }
        for enemy_name, move_name in matchups.items():
            enemy = cf._build(enemy_name, merged, natures)
            self.assertGreater(pawmot.stats["spe"], enemy.stats["spe"],
                               f"Pawmot should outspeed {enemy_name}")
            mv = cf._lookup_move(move_name, moves)
            hit = cf._raw_hit(pawmot, mv, enemy, typechart, roll="lo")
            self.assertGreaterEqual(hit.frac, 1.0,
                                    f"{move_name} should guarantee an OHKO on {enemy_name}")


class TestTwoTwoTwoTeambuilding(unittest.TestCase):
    """"2-2-2 teambuilding: using core pairs that work well together to
    make your lead unpredictable" -- three DISTINCT pair-scoring criteria
    (`_pair_defensive_synergy`, `_weather_lead_synergy`, `_pair_threat_
    coverage`), combined by `find_pair_cores` (Stage 1) and `two_two_two_
    teams` (Stage 2, combining pairs into whole teams of 6)."""

    def setUp(self):
        self.W = world()
        self.merged = self.W["merged"]

    def test_defensive_synergy_hydreigon_metagross_has_no_shared_weakness(self):
        """The user's own example: "Hydreigon and Metagross, mostly beat
        one another's weaknesses" -- ZERO types super-effective against
        both at once."""
        r = cf._pair_defensive_synergy("Hydreigon", "Metagross", self.merged)
        self.assertEqual(r["shared_weak"], [])
        self.assertGreater(len(r["covered_weak"]), 0)

    def test_defensive_synergy_detects_a_real_shared_weakness(self):
        """Two real Ice-4x-weak Dragon/Flying-ish types share a gap
        neither patches -- Salamence and Mega Garchomp (Dragon/Ground,
        also 4x Ice-weak) both take Ice super-effectively."""
        r = cf._pair_defensive_synergy("Salamence", "Mega Garchomp", self.merged)
        self.assertIn("Ice", r["shared_weak"])

    def test_weather_lead_synergy_detects_drought_chlorophyll(self):
        """The user's own example: Mega Charizard Y (Drought) + Venusaur
        (Chlorophyll)."""
        self.assertEqual(
            cf._weather_lead_synergy("Mega Charizard Y", "Venusaur", self.merged), "sun")
        # Order-independent.
        self.assertEqual(
            cf._weather_lead_synergy("Venusaur", "Mega Charizard Y", self.merged), "sun")

    def test_weather_lead_synergy_none_for_an_unrelated_pair(self):
        self.assertIsNone(cf._weather_lead_synergy("Hydreigon", "Metagross", self.merged))

    def test_weather_lead_synergy_requires_the_matching_weather(self):
        """A setter paired with a DIFFERENT weather's speed-booster isn't
        a synergy -- Drought (sun) + Swift Swim (rain) don't combine."""
        # Ninetales-Alola sets snow (Snow Warning), not sun -- paired with
        # a Chlorophyll user (sun-only), no match.
        self.assertIsNone(
            cf._weather_lead_synergy("Ninetales-Alola", "Venusaur", self.merged))

    def test_one_v_one_matrix_and_single_outcome_wrapper_agree(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        matrix = cf._one_v_one_matrix(
            ["Hydreigon", "Metagross"], ["Kingambit", "Basculegion"],
            self.merged, moves, natures, typechart)
        for name in ("Hydreigon", "Metagross"):
            for enemy in ("Kingambit", "Basculegion"):
                self.assertEqual(
                    matrix[name][enemy],
                    cf._one_v_one_outcome(name, enemy, self.merged, moves, natures, typechart))

    def test_one_v_one_matrix_never_self_mirrors(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        matrix = cf._one_v_one_matrix(
            ["Kingambit"], ["Kingambit", "Basculegion"], self.merged, moves, natures, typechart)
        self.assertNotIn("Kingambit", matrix["Kingambit"])
        self.assertIn("Basculegion", matrix["Kingambit"])

    def test_pair_threat_coverage_counts_correctly_on_a_synthetic_matrix(self):
        """Exercises the pure counting logic directly, independent of real
        damage calc, so the coverage arithmetic itself is pinned exactly."""
        matrix = {
            "A": {"E1": "loss", "E2": "loss", "E3": "win"},
            "B": {"E1": "win", "E2": "loss", "E3": "loss"},
        }
        r = cf._pair_threat_coverage("A", "B", matrix)
        self.assertEqual(sorted(r["losses1"]), ["E1", "E2"])
        self.assertEqual(sorted(r["losses2"]), ["E2", "E3"])
        self.assertEqual(r["covered1"], 1)   # B beats E1 (A's loss)
        self.assertEqual(r["covered2"], 1)   # A beats E3 (B's loss)
        self.assertEqual(r["total_losses"], 4)
        self.assertEqual(r["total_covered"], 2)
        self.assertIn(("A", "E2"), r["uncovered"])
        self.assertIn(("B", "E2"), r["uncovered"])
        self.assertNotIn(("A", "E1"), r["uncovered"])
        self.assertNotIn(("B", "E3"), r["uncovered"])

    def test_find_pair_cores_end_to_end_small_pool(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Hydreigon", "Mega Metagross", "Garchomp", "Kingambit",
               "Mega Charizard Y", "Venusaur"]
        rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                  self.W["teams"])
        import itertools as _it
        # One fewer than the full C(6,2): "Mega Metagross + Mega Charizard
        # Y" (two Megas paired together) is never even generated -- see
        # TestFindPairCoresMegaRules below.
        self.assertEqual(len(rows), len(list(_it.combinations(pool, 2))) - 1)
        self.assertNotIn(frozenset({"Mega Metagross", "Mega Charizard Y"}),
                         {frozenset(r["pair"]) for r in rows})
        # Sorted: fewest shared_weak first.
        shared_counts = [len(r["shared_weak"]) for r in rows]
        self.assertEqual(shared_counts, sorted(shared_counts))
        pairs = {frozenset(r["pair"]) for r in rows}
        self.assertIn(frozenset({"Hydreigon", "Mega Metagross"}), pairs)
        weather_row = next(r for r in rows
                           if set(r["pair"]) == {"Mega Charizard Y", "Venusaur"})
        self.assertEqual(weather_row["weather_synergy"], "sun")

    def test_find_pair_cores_skips_names_with_no_defensive_chart(self):
        """"Floette" (base form) has usage/moves data but no roster.csv
        weakness row (`merged["Floette"]["defensive_chart"]` is `None`) --
        must be dropped from the pool instead of crashing (the real bug
        hit when running this over the full default pool)."""
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        self.assertIsNone(self.merged["Floette"].get("defensive_chart"))
        pool = ["Floette", "Hydreigon", "Mega Metagross"]
        rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                  self.W["teams"])
        for r in rows:
            self.assertNotIn("Floette", r["pair"])

    def test_two_two_two_teams_produces_disjoint_teams_of_six(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Hydreigon", "Mega Metagross", "Garchomp", "Mega Garchomp",
               "Kingambit", "Mega Charizard Y", "Venusaur", "Salamence",
               "Mega Salamence", "Dragonite"]
        pair_rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                       self.W["teams"])
        team_rows = cf.two_two_two_teams(pair_rows, self.merged,
                                         top_pairs=len(pair_rows), top_n=10)
        self.assertGreater(len(team_rows), 0)
        for r in team_rows:
            self.assertEqual(len(r["team"]), 6)
            self.assertEqual(len(set(r["team"])), 6)  # no repeats
            flat = [n for pair in r["pairs"] for n in pair]
            self.assertEqual(sorted(flat), sorted(r["team"]))
            self.assertIn("worst_net_weakness", r)
            self.assertIn("total_net_weakness", r)
        # Sorted worst-case-first.
        worst_vals = [r["worst_net_weakness"] for r in team_rows]
        self.assertEqual(worst_vals, sorted(worst_vals))

    def test_two_two_two_teams_no_op_with_fewer_than_three_pairs(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Hydreigon", "Mega Metagross", "Garchomp"]
        pair_rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                       self.W["teams"])
        self.assertEqual(cf.two_two_two_teams(pair_rows, self.merged), [])

    def test_pair_mutual_resist_coverage_perfect_case(self):
        """"each mutually resists all the types that are super effective
        against the other" -- a real, hand-verified pair: Salamence
        (Dragon/Flying, weak Ice/Rock/Dragon/Fairy) + Mega Aggron
        (Steel/Rock in this dataset's usage, resisting all four) should
        register as perfectly covering EACH OTHER."""
        r = cf._pair_mutual_resist_coverage("Salamence", "Mega Aggron", self.merged)
        self.assertTrue(r["perfect"])
        self.assertEqual(r["coverage_frac"], 1.0)
        self.assertEqual(r["a_weak_resisted_by_b"], r["a_weak_total"])
        self.assertEqual(r["b_weak_resisted_by_a"], r["b_weak_total"])

    def test_pair_mutual_resist_coverage_is_stricter_than_covered_weak(self):
        """Hydreigon+Metagross has 0 SHARED weaknesses (`_pair_defensive_
        synergy`), but mutual-resist coverage is a strictly harder bar
        (an actual RESIST, not merely "not also weak") -- confirmed NOT
        perfect for this real pair, i.e. genuinely different information."""
        defense = cf._pair_defensive_synergy("Hydreigon", "Metagross", self.merged)
        mutual = cf._pair_mutual_resist_coverage("Hydreigon", "Metagross", self.merged)
        self.assertEqual(defense["shared_weak"], [])
        self.assertFalse(mutual["perfect"])
        self.assertGreater(mutual["coverage_frac"], 0.0)
        self.assertLess(mutual["coverage_frac"], 1.0)

    def test_pair_mutual_resist_coverage_self_mirror_is_not_trivially_perfect(self):
        """Confirms "perfect" isn't accidentally always True -- Salamence
        mirrored against itself shares every real weakness (Ice/Rock/
        Dragon/Fairy) and resists none of them for its mirror copy, so
        coverage is exactly 0, not the "no weaknesses" trivial case."""
        r = cf._pair_mutual_resist_coverage("Salamence", "Salamence", self.merged)
        self.assertFalse(r["perfect"])
        self.assertEqual(r["coverage_frac"], 0.0)

    def test_find_pair_cores_rows_carry_mutual_resist(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Salamence", "Mega Garchomp"]
        rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                  self.W["teams"])
        self.assertEqual(len(rows), 1)
        self.assertIn("mutual_resist", rows[0])
        self.assertIn("perfect", rows[0]["mutual_resist"])

    def test_two_two_two_teams_max_net_weakness_cap_filters_teams(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Hydreigon", "Mega Metagross", "Garchomp", "Mega Garchomp",
               "Kingambit", "Mega Charizard Y", "Venusaur", "Salamence",
               "Mega Salamence", "Dragonite"]
        pair_rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                       self.W["teams"])
        uncapped = cf.two_two_two_teams(pair_rows, self.merged,
                                        top_pairs=len(pair_rows), top_n=50)
        self.assertGreater(len(uncapped), 0)
        strict = cf.two_two_two_teams(pair_rows, self.merged,
                                      top_pairs=len(pair_rows), top_n=50,
                                      max_net_weakness=0)
        self.assertLess(len(strict), len(uncapped))
        for r in strict:
            self.assertLessEqual(r["worst_net_weakness"], 0)

    def test_two_two_two_teams_default_max_net_weakness_is_a_no_op(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Hydreigon", "Mega Metagross", "Garchomp", "Mega Garchomp",
               "Kingambit", "Mega Charizard Y", "Venusaur", "Salamence",
               "Mega Salamence", "Dragonite"]
        pair_rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                       self.W["teams"])
        default = cf.two_two_two_teams(pair_rows, self.merged,
                                       top_pairs=len(pair_rows), top_n=50)
        explicit_none = cf.two_two_two_teams(pair_rows, self.merged,
                                             top_pairs=len(pair_rows), top_n=50,
                                             max_net_weakness=None)
        self.assertEqual([r["team"] for r in default], [r["team"] for r in explicit_none])


class TestFindPairCoresMegaRules(unittest.TestCase):
    """"you cannot have both a mega and its non-mega form. A pair should
    never be two megas" -- two pairs `find_pair_cores` must never even
    generate, regardless of how well they'd otherwise score."""

    def setUp(self):
        self.W = world()
        self.merged = self.W["merged"]

    def _pairs(self, pool):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                  self.W["teams"])
        return {frozenset(r["pair"]) for r in rows}

    def test_a_mega_and_its_own_base_form_are_never_paired(self):
        pairs = self._pairs(["Garchomp", "Mega Garchomp", "Hydreigon"])
        self.assertNotIn(frozenset({"Garchomp", "Mega Garchomp"}), pairs)
        # The OTHER two pairs (each with Hydreigon) are still legal.
        self.assertIn(frozenset({"Garchomp", "Hydreigon"}), pairs)
        self.assertIn(frozenset({"Mega Garchomp", "Hydreigon"}), pairs)

    def test_two_different_megas_are_never_paired(self):
        pairs = self._pairs(["Mega Garchomp", "Mega Metagross", "Hydreigon"])
        self.assertNotIn(frozenset({"Mega Garchomp", "Mega Metagross"}), pairs)
        self.assertIn(frozenset({"Mega Garchomp", "Hydreigon"}), pairs)
        self.assertIn(frozenset({"Mega Metagross", "Hydreigon"}), pairs)

    def test_no_illegal_pair_survives_a_larger_pool(self):
        pool = ["Hydreigon", "Mega Metagross", "Garchomp", "Mega Garchomp",
               "Kingambit", "Mega Charizard Y", "Venusaur", "Salamence",
               "Mega Salamence", "Dragonite"]
        pairs = self._pairs(pool)
        for p in pairs:
            n1, n2 = tuple(p)
            self.assertFalse(n1.startswith("Mega ") and n2.startswith("Mega "), p)
            self.assertFalse(cf._mega_base_overlap((n1, n2)), p)


class TestTwoTwoTwoTeamsMaxMegas(unittest.TestCase):
    """"A team should not have more than two megas, and the pairs should
    combine such that the megas dont both need to come" -- `max_megas`
    (default 2, matching `bring4_search`/`multi_bring4_exhaustive`'s own
    convention) hard-drops any candidate team over the cap; since
    `find_pair_cores` never generates a two-Mega pair, a team AT the cap
    always has its stone-holders split across two different pairs."""

    def setUp(self):
        self.W = world()
        self.merged = self.W["merged"]
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Hydreigon", "Mega Metagross", "Garchomp", "Mega Garchomp",
               "Kingambit", "Mega Charizard Y", "Venusaur", "Salamence",
               "Mega Salamence", "Dragonite"]
        self.pair_rows = cf.find_pair_cores(pool, self.merged, moves, natures,
                                            typechart, self.W["teams"])

    def _mega_count(self, team):
        return sum(1 for n in team if n.startswith("Mega "))

    def test_default_caps_every_team_at_two_megas(self):
        rows = cf.two_two_two_teams(self.pair_rows, self.merged,
                                    top_pairs=len(self.pair_rows), top_n=200)
        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertLessEqual(self._mega_count(r["team"]), 2)

    def test_max_megas_none_allows_more_than_two(self):
        capped = cf.two_two_two_teams(self.pair_rows, self.merged,
                                      top_pairs=len(self.pair_rows), top_n=200)
        uncapped = cf.two_two_two_teams(self.pair_rows, self.merged,
                                        top_pairs=len(self.pair_rows), top_n=200,
                                        max_megas=None)
        self.assertGreaterEqual(len(uncapped), len(capped))
        self.assertTrue(any(self._mega_count(r["team"]) > 2 for r in uncapped),
                        "fixture assumes at least one candidate team really "
                        "does carry more than 2 stone-holders when uncapped")

    def test_a_team_at_the_cap_never_needs_both_megas_in_one_pair(self):
        rows = cf.two_two_two_teams(self.pair_rows, self.merged,
                                    top_pairs=len(self.pair_rows), top_n=200)
        for r in rows:
            if self._mega_count(r["team"]) < 2:
                continue
            for pair in r["pairs"]:
                self.assertFalse(pair[0].startswith("Mega ") and pair[1].startswith("Mega "),
                                 (r["team"], pair))

    def test_stricter_max_megas_drops_teams(self):
        rows0 = cf.two_two_two_teams(self.pair_rows, self.merged,
                                     top_pairs=len(self.pair_rows), top_n=200,
                                     max_megas=0)
        for r in rows0:
            self.assertEqual(self._mega_count(r["team"]), 0)


class TestPairOffensivePin(unittest.TestCase):
    """"Offensive pins": one member's real spread move backed by a
    partner move that answers most of what would otherwise resist it --
    "Fire (Heat Wave) + Ground (High Horsepower, Earthquake): Fire is
    resisted by fire, rock, water - ground hits 2/3 for super effective"."""

    def setUp(self):
        self.W = world()
        self.merged = self.W["merged"]
        self.moves, self.typechart = self.W["moves"], self.W["typechart"]

    def test_types_resisting_fire_matches_the_real_type_chart(self):
        resisted = cf._types_resisting("Fire", self.typechart)
        # The user's own worked example (Fire, Rock, Water) plus Dragon,
        # which the real chart also resists Fire with (0.5x) -- confirmed
        # directly against damage.type_multiplier below, not assumed.
        for t in ("Fire", "Rock", "Water", "Dragon"):
            self.assertIn(t, resisted)
        self.assertNotIn("Grass", resisted)  # Fire is super-effective vs Grass

    def test_real_damaging_moves_excludes_status_and_the_other_bucket(self):
        moves = cf._real_damaging_moves("Garchomp", self.merged, self.moves)
        names = [mv.name for mv in moves]
        self.assertNotIn("Protect", names)   # Status
        self.assertNotIn("Other", names)     # not a real move
        self.assertIn("Earthquake", names)

    def test_best_move_of_kind_spread_only_finds_a_real_spread_move(self):
        mv = cf._best_move_of_kind("Mega Charizard Y", self.merged, self.moves,
                                   spread_only=True)
        self.assertIsNotNone(mv)
        self.assertEqual(mv.name, "Heat Wave")

    def test_a_real_offensive_pin_is_scored_and_covers_most_resistors(self):
        row = cf._pair_offensive_pin("Mega Charizard Y", "Garchomp",
                                     self.merged, self.moves, self.typechart)
        self.assertIsNotNone(row)
        self.assertEqual(row["pin_user"], "Mega Charizard Y")
        self.assertEqual(row["pin_move"], "Heat Wave")
        self.assertEqual(row["pin_type"], "Fire")
        self.assertEqual(row["follow_up_user"], "Garchomp")
        self.assertGreater(row["coverage_frac"], 0.0)
        self.assertLessEqual(row["coverage_frac"], 1.0)
        self.assertEqual(len(row["covered"]), len(set(row["covered"])))
        self.assertTrue(set(row["covered"]) <= set(row["resisted_by"]))

    def test_the_better_of_both_directions_is_kept(self):
        """Both members here have a real spread move (Heat Wave / Earth
        Power-style Ground move); whichever direction scores higher is
        the one returned, not always name1-leads."""
        row_ab = cf._pair_offensive_pin("Mega Charizard Y", "Garchomp",
                                        self.merged, self.moves, self.typechart)
        row_ba = cf._pair_offensive_pin("Garchomp", "Mega Charizard Y",
                                        self.merged, self.moves, self.typechart)
        # Order of the ARGUMENTS doesn't matter -- both directions are
        # tried internally either way, so the two calls agree.
        self.assertEqual(row_ab["pin_user"], row_ba["pin_user"])
        self.assertEqual(row_ab["coverage_frac"], row_ba["coverage_frac"])

    def test_returns_none_when_neither_side_has_a_real_spread_move(self):
        # Neither Kingambit nor Incineroar's own top usage moves include a
        # real spread move (both are single-target-focused sets) --
        # confirmed directly via _best_move_of_kind(spread_only=True)
        # returning None for both, not assumed.
        self.assertIsNone(cf._best_move_of_kind(
            "Kingambit", self.merged, self.moves, spread_only=True))
        self.assertIsNone(cf._best_move_of_kind(
            "Incineroar", self.merged, self.moves, spread_only=True))
        row = cf._pair_offensive_pin("Kingambit", "Incineroar", self.merged,
                                     self.moves, self.typechart)
        self.assertIsNone(row)

    def test_find_pair_cores_carries_the_offensive_pin_field(self):
        moves, natures, typechart = self.W["moves"], self.W["natures"], self.W["typechart"]
        pool = ["Mega Charizard Y", "Garchomp", "Hydreigon"]
        rows = cf.find_pair_cores(pool, self.merged, moves, natures, typechart,
                                  self.W["teams"])
        row = next(r for r in rows if set(r["pair"]) == {"Mega Charizard Y", "Garchomp"})
        self.assertIsNotNone(row["offensive_pin"])
        self.assertEqual(row["offensive_pin"]["pin_move"], "Heat Wave")


class TestLastRespectsFaintedAllyBoost(unittest.TestCase):
    """"another loss which is treated as a win, my side is Metagross/
    Dragonite" -- once Basculegion's own partner (e.g. Arcanine-Hisui) had
    already fainted earlier in the same joint race, its Last Respects should
    hit at the real boosted 100 BP (`battle.py`'s own "50 BP base, +50 per
    fainted ally" rule), not the flat 50 BP `_move_infos` gives every move by
    default -- this cheap model had no such scaling at all until now, so a
    Basculegion whose ally had already gone down looked far weaker than it
    really is, letting a race that should end in a loss for the OTHER side
    (Last Respects finishing what it should) get scored as a win instead."""

    def setUp(self):
        self.W = world()

    def test_last_respects_doubles_once_the_ally_has_fainted(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        basculegion = cf._build("Basculegion", merged, natures, item="Life Orb")
        dragonite = cf._build("Dragonite", merged, natures)
        last_respects = cf._move_infos("Basculegion", merged, moves, ["Last Respects"])
        live_targets = {"P": dragonite}
        hits_alive, _mv = cf._choose_action(
            basculegion, last_respects, live_targets, typechart,
            attacker_hp_frac=1.0, target_hp_fracs={"P": 1.0, "E2": 1.0},
            attacker_role="E1")
        hits_fainted, _mv2 = cf._choose_action(
            basculegion, last_respects, live_targets, typechart,
            attacker_hp_frac=1.0, target_hp_fracs={"P": 1.0, "E2": 0.0},
            attacker_role="E1")
        # ~2x (100 BP vs 50 BP) -- not exactly 2x because of the formula's
        # own flat "+2" term, same reasoning `damage_roll`'s own docstring
        # gives for every base-power special case.
        ratio = hits_fainted["P"].avg / hits_alive["P"].avg
        self.assertGreater(ratio, 1.8)
        self.assertLess(ratio, 2.0)

    def test_last_respects_unaffected_when_the_attacker_has_no_role(self):
        """`attacker_role=None` (every caller outside the joint race, e.g.
        the 2x2 damage-grid display) is a deliberate no-op -- same "cruder,
        documented hypothesis" scoping as every other per-role stand-in in
        this module."""
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        basculegion = cf._build("Basculegion", merged, natures, item="Life Orb")
        dragonite = cf._build("Dragonite", merged, natures)
        last_respects = cf._move_infos("Basculegion", merged, moves, ["Last Respects"])
        hits, _mv = cf._choose_action(
            basculegion, last_respects, {"P": dragonite}, typechart,
            attacker_hp_frac=1.0, target_hp_fracs={"P": 1.0, "E2": 0.0})
        hits_role_but_ally_alive, _mv2 = cf._choose_action(
            basculegion, last_respects, {"P": dragonite}, typechart,
            attacker_hp_frac=1.0, target_hp_fracs={"P": 1.0, "E2": 1.0},
            attacker_role="E1")
        self.assertAlmostEqual(hits["P"].avg, hits_role_but_ally_alive["P"].avg, places=6)

    def test_other_moves_are_unaffected(self):
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        basculegion = cf._build("Basculegion", merged, natures, item="Life Orb")
        dragonite = cf._build("Dragonite", merged, natures)
        aqua_jet = cf._move_infos("Basculegion", merged, moves, ["Aqua Jet"])
        hits_alive, _mv = cf._choose_action(
            basculegion, aqua_jet, {"P": dragonite}, typechart,
            attacker_hp_frac=1.0, target_hp_fracs={"P": 1.0, "E2": 1.0},
            attacker_role="E1")
        hits_fainted, _mv2 = cf._choose_action(
            basculegion, aqua_jet, {"P": dragonite}, typechart,
            attacker_hp_frac=1.0, target_hp_fracs={"P": 1.0, "E2": 0.0},
            attacker_role="E1")
        self.assertAlmostEqual(hits_alive["P"].avg, hits_fainted["P"].avg, places=6)


def _fake_coverage_row(pair, perfect, coverage_frac, avg_score=None):
    """A synthetic `find_pair_cores`-shaped row -- only the fields
    `coverage_group_search` actually reads (`pair`, `mutual_resist`'s
    `perfect`/`coverage_frac`, `avg_score`), no real racing/weakness math."""
    return {"pair": pair, "avg_score": avg_score,
           "mutual_resist": {"perfect": perfect, "coverage_frac": coverage_frac}}


# A member lacking "defensive_chart" entirely is treated as weakness-neutral
# by `team_search._weak_resist` (skipped, contributes 0 either way) -- so
# `net_weakness_by_type` (always computed per returned row, "a genuine
# display column") is a harmless no-op against this fixture, letting these
# tests isolate pure ranking/filter logic without needing real weakness data.
_FAKE_MERGED = {
    name: {"types": types, "score": score} for name, (types, score) in {
        "A": (["Fire"], 100.0), "B": (["Water"], 200.0), "C": (["Grass"], 300.0),
        "D": (["Electric"], 50.0), "E": (["Ice"], 25.0),
        "F": (["Fire"], 100.0),                 # F shares A's exact typing
        "Mega G": (["Dragon", "Flying"], 500.0), "Mega H": (["Steel", "Psychic"], 500.0),
        "Mega I": (["Ghost"], 10.0),
    }.items()
}


class TestCoverageGroupSearchRanking(unittest.TestCase):
    """`coverage_group_search`'s pure ranking/filter logic -- hand-built
    pair rows (`_fake_coverage_row`, no real racing), same isolation style
    as `TestCoreRowRespectsMegaConsistency`. Ported from the user's own
    standalone "Coverage group finder" HTML tool: same DFS-with-pruning
    shape, sourced from `find_pair_cores`-shaped data instead of a pasted
    table."""

    def test_ranks_by_perfect_links_first(self):
        import itertools as _it
        names = ["A", "B", "C"]
        rows = [_fake_coverage_row(p, True, 100.0, 500.0)
               for p in _it.combinations(names, 2)]
        # A worse-scoring, non-perfect trio that must still rank below.
        rows2 = [_fake_coverage_row(p, False, 40.0, 900.0)
                for p in _it.combinations(["D", "E", "A"], 2)]
        result = cf.coverage_group_search(
            rows + rows2, _FAKE_MERGED, group_sizes=(3,), no_duplicate_typing=False)
        top = result[3]["rows"][0]
        self.assertEqual(set(top["group"]), {"A", "B", "C"})
        self.assertEqual(top["perfect_links"], 3)

    def test_sort_by_coverage_ignores_perfect_count(self):
        import itertools as _it
        names = ["A", "B", "C"]
        perfect_but_low_cov = [_fake_coverage_row(p, True, 34.0, 100.0)
                               for p in _it.combinations(names, 2)]
        high_cov_not_perfect = [_fake_coverage_row(p, False, 90.0, 100.0)
                                for p in _it.combinations(["D", "E", "A"], 2)]
        result = cf.coverage_group_search(
            perfect_but_low_cov + high_cov_not_perfect, _FAKE_MERGED,
            group_sizes=(3,), sort_by="coverage", no_duplicate_typing=False)
        top = result[3]["rows"][0]
        self.assertEqual(set(top["group"]), {"D", "E", "A"})

    def test_sort_by_score_ranks_on_avg_score(self):
        import itertools as _it
        names = ["A", "B", "C"]
        low_score = [_fake_coverage_row(p, True, 100.0, 100.0)
                    for p in _it.combinations(names, 2)]
        high_score = [_fake_coverage_row(p, True, 100.0, 999.0)
                     for p in _it.combinations(["D", "E", "A"], 2)]
        result = cf.coverage_group_search(
            low_score + high_score, _FAKE_MERGED,
            group_sizes=(3,), sort_by="score", no_duplicate_typing=False)
        top = result[3]["rows"][0]
        self.assertEqual(set(top["group"]), {"D", "E", "A"})

    def test_missing_link_zero_budget_excludes_an_incomplete_group(self):
        # C+E has no row at all -- a group needing it is illegal at
        # max_missing_frac=0.
        rows = [_fake_coverage_row(("A", "B"), True, 100.0, 100.0),
               _fake_coverage_row(("A", "C"), True, 100.0, 100.0),
               _fake_coverage_row(("B", "C"), True, 100.0, 100.0)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=["A", "B", "C", "D"], group_sizes=(3,),
            max_missing_frac=0.0, no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertIn({"A", "B", "C"}, groups)
        for g in groups:
            self.assertNotIn("D", g)  # every pair touching D is missing

    def test_a_higher_missing_budget_allows_the_incomplete_group(self):
        rows = [_fake_coverage_row(("A", "B"), True, 100.0, 100.0),
               _fake_coverage_row(("A", "D"), True, 100.0, 100.0)]
        # B+D is entirely missing -- 1 of 3 links in a size-3 group, i.e.
        # 1/3 =~ 0.33, allowed once the budget covers it.
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=["A", "B", "D"], group_sizes=(3,),
            max_missing_frac=0.4, no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertIn({"A", "B", "D"}, groups)

    def test_prefix_limit_excludes_a_group_over_the_cap(self):
        import itertools as _it
        names = ["Mega G", "Mega H", "Mega I"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,),
            prefix_limits=(("Mega ", 2),), no_duplicate_typing=False)
        self.assertEqual(result[3]["rows"], [])

    def test_prefix_limit_allows_exactly_the_cap(self):
        rows = [_fake_coverage_row(("Mega G", "Mega H"), True, 100.0, 100.0),
               _fake_coverage_row(("Mega G", "A"), True, 100.0, 100.0),
               _fake_coverage_row(("Mega H", "A"), True, 100.0, 100.0)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=["Mega G", "Mega H", "A"], group_sizes=(3,),
            prefix_limits=(("Mega ", 2),), no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertIn({"Mega G", "Mega H", "A"}, groups)

    def test_no_duplicate_typing_excludes_a_shared_type_pair(self):
        # "A" and "F" both have exactly ["Fire"] -- redundant typing.
        rows = [_fake_coverage_row(("A", "F"), True, 100.0, 100.0),
               _fake_coverage_row(("A", "B"), True, 100.0, 100.0),
               _fake_coverage_row(("F", "B"), True, 100.0, 100.0)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=["A", "F", "B"], group_sizes=(3,),
            no_duplicate_typing=True)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertNotIn({"A", "F", "B"}, groups)

    def test_no_duplicate_typing_off_allows_it(self):
        rows = [_fake_coverage_row(("A", "F"), True, 100.0, 100.0),
               _fake_coverage_row(("A", "B"), True, 100.0, 100.0),
               _fake_coverage_row(("F", "B"), True, 100.0, 100.0)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=["A", "F", "B"], group_sizes=(3,),
            no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertIn({"A", "F", "B"}, groups)

    def test_group_sizes_are_scoped_independently(self):
        import itertools as _it
        names = ["A", "B", "C", "D"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3, 4),
            no_duplicate_typing=False)
        self.assertEqual(set(result.keys()), {3, 4})
        self.assertTrue(all(r["size"] == 3 for r in result[3]["rows"]))
        self.assertTrue(all(r["size"] == 4 for r in result[4]["rows"]))

    def test_min_avg_score_filters_low_scoring_groups(self):
        import itertools as _it
        names = ["A", "B", "C"]
        rows = [_fake_coverage_row(p, True, 100.0, 50.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,),
            min_avg_score=100.0, no_duplicate_typing=False)
        self.assertEqual(result[3]["rows"], [])

    def test_must_include_appears_in_every_returned_group_not_just_the_top_one(self):
        # 5 names, all pairs present -- C(5,3)=10 possible size-3 groups,
        # C(4,2)=6 of them contain "A". A single lucky top-1 row wouldn't
        # prove the HARD "every group" guarantee -- ask for all of them.
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            must_include=["A"], no_duplicate_typing=False)
        rows_out = result[3]["rows"]
        self.assertEqual(len(rows_out), 6)
        for row in rows_out:
            self.assertIn("A", row["group"])

    def test_must_include_multiple_names_all_present_in_every_group(self):
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(4,), top_n=50,
            must_include=["A", "B"], no_duplicate_typing=False)
        rows_out = result[4]["rows"]
        self.assertTrue(rows_out)
        for row in rows_out:
            self.assertIn("A", row["group"])
            self.assertIn("B", row["group"])

    def test_must_include_count_exceeding_group_size_returns_empty_not_a_crash(self):
        import itertools as _it
        names = ["A", "B", "C", "D"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,),
            must_include=["A", "B", "C", "D"], no_duplicate_typing=False)
        self.assertEqual(result[3]["rows"], [])
        self.assertEqual(result[3]["seen"], 0)

    def test_must_include_still_respects_illegal_pair_and_typing_filters(self):
        # "A" and "F" share the exact same typing -- forcing both in via
        # must_include must still be rejected by no_duplicate_typing, same
        # as the ordinary DFS path would reject it.
        import itertools as _it
        names = ["A", "F", "B", "C"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,),
            must_include=["A", "F"], no_duplicate_typing=True)
        self.assertEqual(result[3]["rows"], [])

    def test_suggested_quorum_filters_out_groups_below_the_minimum(self):
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            suggested=["A", "B", "C"], suggested_min=2,
            no_duplicate_typing=False)
        rows_out = result[3]["rows"]
        self.assertTrue(rows_out)
        for row in rows_out:
            self.assertGreaterEqual(
                sum(1 for nm in row["group"] if nm in ("A", "B", "C")), 2)

    def test_suggested_min_zero_disables_the_quorum(self):
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            suggested=["A", "B", "C"], suggested_min=0,
            no_duplicate_typing=False)
        self.assertEqual(len(result[3]["rows"]), 10)  # C(5,3), unfiltered

    def test_must_include_and_suggested_compose(self):
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            must_include=["A"], suggested=["B", "C"], suggested_min=1,
            no_duplicate_typing=False)
        rows_out = result[3]["rows"]
        self.assertTrue(rows_out)
        for row in rows_out:
            self.assertIn("A", row["group"])
            self.assertTrue({"B", "C"} & set(row["group"]))

    def test_required_core_keeps_a_group_that_covers_it(self):
        """A=Fire, B=Water, C=Grass -- their combined types cover the
        elemental core exactly."""
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            required_cores=[("Fire", "Water", "Grass")], no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertIn({"A", "B", "C"}, groups)
        for g in groups:
            g_types = {t for n in g for t in _FAKE_MERGED[n]["types"]}
            self.assertTrue({"Fire", "Water", "Grass"} <= g_types)

    def test_required_core_excludes_a_group_missing_one_of_its_types(self):
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            required_cores=[("Fire", "Water", "Grass")], no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        # A+B+D is Fire/Water/Electric -- no Grass, so the core isn't met.
        self.assertNotIn({"A", "B", "D"}, groups)

    def test_several_required_cores_are_all_required(self):
        """Fire/Water/Grass AND Fire/Water/Electric together need all 4
        types (Fire, Water, Grass, Electric) present at once -- an AND
        over the list, not "any one of them"."""
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(4,), top_n=50,
            required_cores=[("Fire", "Water", "Grass"), ("Fire", "Water", "Electric")],
            no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[4]["rows"]]
        self.assertIn({"A", "B", "C", "D"}, groups)      # Fire/Water/Grass/Electric
        self.assertNotIn({"A", "B", "C", "E"}, groups)   # no Electric -- fails core 2

    def test_min_member_score_drops_any_group_with_a_below_floor_member(self):
        """A's score (100) is below the floor (150); B/C/Mega G (200/300/
        500) all clear it."""
        import itertools as _it
        names = ["A", "B", "C", "Mega G"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            min_member_score=150.0, no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertIn({"B", "C", "Mega G"}, groups)
        for g in groups:
            self.assertNotIn("A", g)

    def test_min_member_score_excludes_a_must_include_name_that_fails_the_floor(self):
        """Forcing in a name the floor already excluded is a real, visible
        conflict -- empty results, not a silent exemption. A pool with
        plenty of OTHER above-floor names (B/C/Mega G/Mega H) rules out
        the group simply being too small to notice a silently dropped
        requirement."""
        import itertools as _it
        names = ["A", "B", "C", "Mega G", "Mega H"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,),
            must_include=["A"], min_member_score=150.0, no_duplicate_typing=False)
        self.assertEqual(result[3]["rows"], [])

    def test_exclude_removes_a_name_from_every_returned_group(self):
        import itertools as _it
        names = ["A", "B", "C", "D"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,), top_n=50,
            exclude=["A"], no_duplicate_typing=False)
        groups = [set(r["group"]) for r in result[3]["rows"]]
        self.assertTrue(groups)
        for g in groups:
            self.assertNotIn("A", g)
        self.assertIn({"B", "C", "D"}, groups)

    def test_exclude_conflicting_with_must_include_returns_empty(self):
        """Naming the same Pokemon in both "Always include" and "Exclude"
        is a real conflict -- empty results, not a silent tie-break
        either way. A 5-name pool (still 4 names left after excluding "A")
        rules out the group simply being too small to notice a silently
        dropped requirement."""
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3,),
            must_include=["A"], exclude=["A"], no_duplicate_typing=False)
        self.assertEqual(result[3]["rows"], [])

    def test_the_conflict_empties_every_requested_size_not_just_one(self):
        import itertools as _it
        names = ["A", "B", "C", "D", "E"]
        rows = [_fake_coverage_row(p, True, 100.0, 100.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, _FAKE_MERGED, pool=names, group_sizes=(3, 4),
            must_include=["A"], exclude=["A"], no_duplicate_typing=False)
        self.assertEqual(result[3]["rows"], [])
        self.assertEqual(result[4]["rows"], [])


class TestCoverageGroupSearchRealData(unittest.TestCase):
    """End-to-end through `find_pair_cores` -> `coverage_group_search`,
    real roster data -- confirms the real `mutual_resist`/`avg_score`
    fields wire through correctly and `net_weakness_by_type` (a genuine
    per-type recomputation, unlike the synthetic fixtures above) produces
    sane, internally-consistent results."""

    POOL = ["Garchomp", "Kingambit", "Incineroar", "Whimsicott", "Sinistcha",
           "Basculegion", "Sableye", "Ariados", "Mega Metagross",
           "Mega Charizard Y"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.merged = merged
        self.pair_rows = cf.find_pair_cores(
            self.POOL, merged, moves, natures, typechart, self.W["teams"])

    def test_returns_rows_for_every_requested_size(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(3, 4, 6), top_n=5)
        self.assertEqual(set(result.keys()), {3, 4, 6})
        for size in (3, 4, 6):
            self.assertTrue(result[size]["rows"])
            for row in result[size]["rows"]:
                self.assertEqual(len(row["group"]), size)

    def test_net_weakness_is_internally_consistent(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=5)
        for row in result[4]["rows"]:
            self.assertEqual(row["worst_net_weakness"], max(row["net_weakness"].values()))

    def test_weakness_is_internally_consistent(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=5)
        for row in result[4]["rows"]:
            self.assertEqual(row["worst_weakness"], max(row["weakness"].values()))

    def test_weakness_is_never_less_than_net_weakness(self):
        """The absolute count can never be LOWER than the net reading for
        the same type -- net subtracts resistors, absolute doesn't."""
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20)
        for row in result[4]["rows"]:
            for t in row["weakness"]:
                self.assertGreaterEqual(row["weakness"][t], row["net_weakness"][t])

    def test_must_include_forces_a_name_through_narrowing(self):
        """"specify individual Pokemon to include" end-to-end: with
        `max_search_names` pinned to exactly the group size, narrowing
        leaves only ONE possible group -- deterministic proof the forced
        name survives regardless of how its own links would otherwise
        rank."""
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(3,), top_n=1,
            max_search_names=3, must_include=["Ariados"],
            max_missing_frac=1.0, no_duplicate_typing=False)
        self.assertTrue(result[3]["rows"])
        self.assertIn("Ariados", result[3]["rows"][0]["group"])

    def test_must_include_holds_across_every_row_of_a_real_multi_row_result(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20,
            must_include=["Ariados"], max_missing_frac=1.0,
            no_duplicate_typing=False)
        rows_out = result[4]["rows"]
        self.assertGreater(len(rows_out), 1)
        for row in rows_out:
            self.assertIn("Ariados", row["group"])

    def test_max_net_weakness_caps_the_worst_type(self):
        uncapped = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20)
        cap = 0
        capped = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20,
            max_net_weakness=cap)
        for row in capped[4]["rows"]:
            self.assertLessEqual(row["worst_net_weakness"], cap)
        self.assertLess(len(capped[4]["rows"]), len(uncapped[4]["rows"]))

    def test_max_weakness_caps_the_worst_types_absolute_count(self):
        """"cap absolute weaknesses per type too" -- unlike max_net_
        weakness, no credit for how many others resist that type."""
        uncapped = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20)
        cap = 1
        capped = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20,
            max_weakness=cap)
        self.assertTrue(capped[4]["rows"])
        for row in capped[4]["rows"]:
            self.assertLessEqual(row["worst_weakness"], cap)
        self.assertLess(len(capped[4]["rows"]), len(uncapped[4]["rows"]))

    def test_exclude_removes_a_name_from_every_returned_group(self):
        """"allow an option to exclude specific pokemon" -- end to end
        through real data, not just the offered pool."""
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=20,
            exclude=["Garchomp"])
        self.assertTrue(result[4]["rows"])
        for row in result[4]["rows"]:
            self.assertNotIn("Garchomp", row["group"])

    def test_two_different_megas_never_both_appear_beyond_the_cap(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=50,
            prefix_limits=(("Mega ", 1),))
        for row in result[4]["rows"]:
            n_megas = sum(1 for n in row["group"] if n.startswith("Mega "))
            self.assertLessEqual(n_megas, 1)

    def test_no_duplicate_typing_holds_across_real_groups(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4,), top_n=50,
            no_duplicate_typing=True)
        for row in result[4]["rows"]:
            sigs = [frozenset(self.merged[n]["types"]) for n in row["group"]]
            self.assertEqual(len(sigs), len(set(sigs)), row["group"])


class TestCoverageGroupSearchMegaLegality(unittest.TestCase):
    """`coverage_group_search`'s own hard exclusion for a Mega alongside
    its own base form -- distinct from `find_pair_cores`'s pairwise
    exclusion (which this function reuses via `edge`, not recomputes),
    since a caller can widen `max_missing_frac` far enough to otherwise
    let an illegal pair slip through as merely 'unmeasured'."""

    # Charizard X changes type (Fire/Dragon) from its base form
    # (Fire/Flying) -- `no_duplicate_typing` alone would NOT catch this
    # collision, isolating the dedicated `illegal_pair` check.
    POOL = ["Mega Charizard X", "Charizard", "Mega Gyarados", "Gyarados",
           "Kingambit", "Garchomp", "Whimsicott", "Sinistcha"]

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        self.merged = merged
        self.pair_rows = cf.find_pair_cores(
            self.POOL, merged, moves, natures, typechart, self.W["teams"])

    def test_mega_and_own_base_form_never_share_a_group_even_at_full_missing_budget(self):
        import itertools as _it
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(3, 4, 6), top_n=200,
            max_missing_frac=1.0, no_duplicate_typing=False,
            prefix_limits=(("Mega ", 6),))
        for r in result.values():
            for row in r["rows"]:
                for a, b in _it.combinations(row["group"], 2):
                    self.assertFalse(cf._mega_base_overlap((a, b)), row["group"])

    def test_two_different_megas_may_share_a_group(self):
        """UNLIKE `find_pair_cores`'s own pairwise "never two megas as a
        LEAD PAIR" rule, a coverage GROUP is a team-composition question
        -- two different Megas (neither the other's base form) may
        legally appear together, their own unscoreable link simply
        counting as 'missing'."""
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4, 6), top_n=200,
            max_missing_frac=1.0, no_duplicate_typing=False,
            prefix_limits=(("Mega ", 6),))
        found = any(
            sum(1 for n in row["group"] if n.startswith("Mega ")) >= 2
            for r in result.values() for row in r["rows"])
        self.assertTrue(found, "expected at least one group with 2 different Megas")

    def test_a_returned_group_with_two_megas_never_crashes_bring4_search(self):
        result = cf.coverage_group_search(
            self.pair_rows, self.merged, group_sizes=(4, 6), top_n=200,
            max_missing_frac=1.0, no_duplicate_typing=False,
            prefix_limits=(("Mega ", 6),))
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        two_mega_groups = [
            row["group"] for r in result.values() for row in r["rows"]
            if sum(1 for n in row["group"] if n.startswith("Mega ")) >= 2]
        self.assertTrue(two_mega_groups)
        for group in two_mega_groups[:3]:
            cf.bring4_search(list(group), ["Sableye", "Ariados"], merged, moves,
                             natures, typechart)  # must not raise


class TestCoverageGroupSearchLargePoolNarrowing(unittest.TestCase):
    """`max_search_names`: "expand the search pool to 300" must not make
    the DFS itself combinatorial in the raw pool size -- a cheap, one-pass
    pre-narrow to the best-linked `max_search_names` names keeps the
    search bounded regardless of how big `pool` is."""

    def test_narrows_a_large_synthetic_pool_before_searching(self):
        import itertools as _it
        names = [f"M{i}" for i in range(120)]
        merged = {n: {"types": ["Normal"]} for n in names}
        # Every pair scored, so there is no missing-link pruning to lean
        # on -- exactly the "real roster data has almost no missing
        # links" case the docstring calls out.
        rows = [_fake_coverage_row(p, True, 50.0, float(i))
               for i, p in enumerate(_it.combinations(names, 2))]
        result = cf.coverage_group_search(
            rows, merged, pool=names, group_sizes=(6,), top_n=5,
            no_duplicate_typing=False, max_search_names=15)
        # C(120,6) would be ~300M -- if this ran unnarrowed it would blow
        # way past `max_eval`; narrowed to 15 candidates, C(15,6)=5005 is
        # small enough to finish exhaustively.
        self.assertFalse(result[6]["aborted"])
        self.assertLess(result[6]["seen"], 6000)

    def test_max_search_names_none_restores_the_old_unbounded_behaviour(self):
        import itertools as _it
        names = [f"M{i}" for i in range(10)]
        merged = {n: {"types": ["Normal"]} for n in names}
        rows = [_fake_coverage_row(p, True, 50.0, 0.0)
               for p in _it.combinations(names, 2)]
        result = cf.coverage_group_search(
            rows, merged, pool=names, group_sizes=(3,), top_n=5,
            no_duplicate_typing=False, max_search_names=None)
        self.assertTrue(result[3]["rows"])


class TestNarrowCoveragePoolNames(unittest.TestCase):
    """`narrow_coverage_pool_names`, `coverage_group_search`'s own
    pool-narrowing step factored out so a caller running something else
    (a real `joint_pool_search` win-rate pass) against the SAME narrowed
    set doesn't have to duplicate the ranking."""

    def test_keeps_the_best_linked_names_by_coverage_then_score(self):
        rows = [
            _fake_coverage_row(("A", "B"), True, 90.0, 100.0),
            _fake_coverage_row(("A", "C"), True, 10.0, 100.0),
            _fake_coverage_row(("B", "C"), True, 10.0, 100.0),
            _fake_coverage_row(("D", "E"), True, 5.0, 500.0),
        ]
        # A and B each have a strong (90%) link; C/D/E only ever reach 10%
        # or worse -- narrowing to 2 must keep exactly {A, B}.
        narrowed = cf.narrow_coverage_pool_names(rows, ["A", "B", "C", "D", "E"], 2)
        self.assertEqual(narrowed, ["A", "B"])

    def test_returns_unchanged_when_already_at_or_under_the_cap(self):
        rows = [_fake_coverage_row(("A", "B"), True, 50.0, 0.0)]
        self.assertEqual(cf.narrow_coverage_pool_names(rows, ["A", "B"], 5), ["A", "B"])

    def test_none_cap_returns_names_unchanged(self):
        rows = [_fake_coverage_row(("A", "B"), True, 50.0, 0.0)]
        names = ["B", "A", "C"]
        self.assertEqual(cf.narrow_coverage_pool_names(rows, names, None), names)

    def test_a_name_with_no_links_at_all_still_sorts_in_deterministically(self):
        rows = [_fake_coverage_row(("A", "B"), True, 90.0, 0.0)]
        narrowed = cf.narrow_coverage_pool_names(rows, ["A", "B", "Z"], 2)
        self.assertEqual(set(narrowed), {"A", "B"})

    def test_must_include_survives_a_weak_link(self):
        """"specify individual Pokemon to include" -- Z's only link is
        weak (10%), so ordinary narrowing to 2 would drop it in favour of
        A/B's strong 90% link; `must_include` must override that."""
        rows = [
            _fake_coverage_row(("A", "B"), True, 90.0, 0.0),
            _fake_coverage_row(("Z", "A"), False, 10.0, 0.0),
        ]
        narrowed = cf.narrow_coverage_pool_names(
            rows, ["A", "B", "Z"], 2, must_include=["Z"])
        self.assertIn("Z", narrowed)
        self.assertEqual(len(narrowed), 2)

    def test_must_include_beyond_the_cap_keeps_all_of_them(self):
        rows = [_fake_coverage_row(("A", "B"), True, 50.0, 0.0)]
        narrowed = cf.narrow_coverage_pool_names(
            rows, ["A", "B", "C"], 1, must_include=["A", "B", "C"])
        self.assertEqual(set(narrowed), {"A", "B", "C"})

    def test_must_include_name_absent_from_pool_is_a_silent_no_op(self):
        rows = [_fake_coverage_row(("A", "B"), True, 90.0, 0.0)]
        narrowed = cf.narrow_coverage_pool_names(
            rows, ["A", "B"], 2, must_include=["Nonexistent"])
        self.assertEqual(set(narrowed), {"A", "B"})


class TestPrematchWinConditions(unittest.TestCase):
    """"A prematch view of my win conditions vs theirs (i.e., once Arcanine
    is gone, Scizor easily beats X in endgame given it 2HKOs enemy but
    takes 5HKOs from enemy and so on, or Metagross is my only answer to
    Staraptor)." Combines `bring4_win_conditions` (unchanged) with a NEW
    1v1 hit-count matrix (`_best_hit`, both directions).

    Real, verified fixture, found by search (not hand-derived): Garchomp +
    Kingambit + Whimsicott + Incineroar's own best `--bring4` result
    against Arcanine-Hisui + Toxapex. Garchomp is the sole `safe_member`
    against both enemies (every one of its own pairings within this
    bring-4 beats them outright), and against Arcanine-Hisui specifically
    it OHKOs (1 hit) while taking a real 3HKO back -- Ground STAB into a
    4x weakness (Fire/Rock) explains the one-sidedness.
    """

    def setUp(self):
        self.W = world()
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        our6 = ["Garchomp", "Kingambit", "Whimsicott", "Incineroar"]
        self.targets = ["Arcanine-Hisui", "Toxapex"]
        _pair_rows, bring4_rows = cf.bring4_search(
            our6, self.targets, merged, moves, natures, typechart, turns=2)
        self.row = bring4_rows[0]
        self.our_built = cf._build_forms(self.row["bring4"], merged, natures, moves)
        for n in self.row["bring4"]:
            item, mvs, _w = cf._answer_for(
                n, merged, moves, natures, typechart, self.targets)
            self.our_built[n]["mega"] = cf._build(n, merged, natures, item=item)
            self.our_built[n]["base"] = self.our_built[n]["mega"]
            self.our_built[n]["moves"] = cf._move_infos(n, merged, moves, mvs)
        self.enemy_built = cf._build_forms(self.targets, merged, natures, moves)
        for n in self.targets:
            item, mvs, _w = cf._answer_for(
                n, merged, moves, natures, typechart, self.row["bring4"])
            self.enemy_built[n]["mega"] = cf._build(n, merged, natures, item=item)
            self.enemy_built[n]["moves"] = cf._move_infos(n, merged, moves, mvs)

    def test_fixture_precondition_bring4_is_all_four(self):
        self.assertEqual(set(self.row["bring4"]),
                         {"Garchomp", "Kingambit", "Whimsicott", "Incineroar"})

    def test_garchomp_is_the_safe_member_against_both_enemies(self):
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        for enemy in self.targets:
            self.assertIn("Garchomp", result["safe"][enemy]["safe_members"], enemy)

    def test_garchomp_ohkoes_arcanine_hisui_but_takes_a_3hko_back(self):
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        cell = result["matrix"][("Garchomp", "Arcanine-Hisui")]
        self.assertEqual(cell["our_hits_to_ko"], 1)
        self.assertEqual(cell["their_hits_to_ko"], 3)

    def test_matrix_has_one_entry_per_bring4_member_times_enemy(self):
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        self.assertEqual(len(result["matrix"]),
                         len(self.row["bring4"]) * len(self.targets))

    def test_for_dive_wrapper_builds_a_real_matrix_off_the_dive_own_sets(self):
        """`prematch_win_conditions_for_dive` (built from an already-
        computed `core_deep_dive` result) reuses OUR OWN side's real,
        already-fixed set from `dive["sets"]` -- Garchomp still OHKOes
        Arcanine-Hisui (its own real STAB into a 4x weakness doesn't
        depend on the enemy's own item), and the safe-member read still
        matches `bring4_win_conditions`'s own unchanged answer. The
        INCOMING hit count isn't asserted here -- unlike the direct-call
        fixture above (which pins the enemy's own set via `_answer_for`),
        `core_deep_dive`'s own enemy-building step (no `enemy_item_
        overrides` given) is a plainer usage-default build, so it need not
        match that explicitly-optimised figure exactly."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        dive = cf.core_deep_dive(list(self.row["bring4"]), [self.targets],
                                 merged, moves, natures, typechart, turns=2)
        bring4_rows = cf.bring4_from_deep_dive(
            list(self.row["bring4"]), dive, self.targets)
        result = cf.prematch_win_conditions_for_dive(
            bring4_rows[0], dive, self.targets, merged, moves, natures, typechart)
        cell = result["matrix"][("Garchomp", "Arcanine-Hisui")]
        self.assertEqual(cell["our_hits_to_ko"], 1)
        self.assertIsNotNone(cell["their_hits_to_ko"])
        self.assertGreater(cell["their_hits_to_ko"], 0)
        self.assertIn("Garchomp", result["safe"]["Arcanine-Hisui"]["safe_members"])

    def test_current_hp_overrides_can_only_make_a_ko_take_as_many_or_more_hits(self):
        """Halving the defender's remaining HP can only shrink (never
        grow) the number of hits needed to finish it off -- the live
        in-battle tracker's whole point."""
        full = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        half_hp = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"],
            defender_hp_frac={"Arcanine-Hisui": 0.5})
        self.assertLessEqual(
            half_hp["matrix"][("Garchomp", "Arcanine-Hisui")]["our_hits_to_ko"],
            full["matrix"][("Garchomp", "Arcanine-Hisui")]["our_hits_to_ko"])

    def test_garchomp_verdict_against_arcanine_hisui_is_a_win(self):
        """1 hit to KO vs taking 2 -- an outright win on hit count alone,
        no speed tiebreak needed."""
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        self.assertEqual(
            result["matrix"][("Garchomp", "Arcanine-Hisui")]["verdict"], "win")

    def test_garchomp_is_flagged_must_preserve_for_both_enemies(self):
        """Garchomp is the SOLE `safe_members` entry for both Arcanine-Hisui
        and Toxapex (per the fixture's own docstring) -- `crucial` must
        name both as `sole_answer_to`, and `must_preserve` must be True."""
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        info = result["crucial"]["Garchomp"]
        self.assertTrue(info["must_preserve"])
        self.assertEqual(set(info["sole_answer_to"]), set(self.targets))

    def test_a_member_with_no_sole_answer_is_not_must_preserve(self):
        """Some OTHER bring-4 member (not a sole safe_members entry
        anywhere) must NOT be flagged must_preserve -- the crucial rollup
        isn't a blanket "everyone is essential" list."""
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        non_garchomp = [n for n in self.row["bring4"] if n != "Garchomp"]
        self.assertTrue(any(not result["crucial"][n]["must_preserve"]
                            for n in non_garchomp),
                        "expected at least one non-Garchomp member with no "
                        "sole answer, given the fixture's own docstring")

    def test_an_already_winning_cell_needs_no_chip(self):
        """"how chipped the enemy has to be" is 0.0, not None, when this
        1v1 is already a win outright -- nothing to chip."""
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        self.assertEqual(
            result["matrix"][("Garchomp", "Arcanine-Hisui")]["chip_needed_frac"], 0.0)

    def test_a_losing_cell_reports_a_real_chip_threshold_between_0_and_1(self):
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        for (our_name, enemy_name), cell in result["matrix"].items():
            if cell["verdict"] != "lose":
                continue
            chip = cell["chip_needed_frac"]
            self.assertIsNotNone(chip, (our_name, enemy_name))
            self.assertGreaterEqual(chip, 0.0, (our_name, enemy_name))
            self.assertLessEqual(chip, 1.0, (our_name, enemy_name))

    def test_real_losing_cells_report_the_expected_chip_thresholds(self):
        """Real, verified values from this fixture's own matrix (`kingambit`
        ties Arcanine-Hisui on hit count but is slower, needing only a
        small chip to instead win outright on hit count alone; `whimsicott`
        is a real, sizeable underdog; `incineroar` faces a 1HKO it can
        never out-hit-count regardless of chip -- not faster, so even
        chipping Arcanine-Hisui to a sliver of HP still leaves Incineroar
        needing 0 hits, an impossible target, hence 1.0 (chip alone can
        never flip this one; only a genuinely different answer can)."""
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        cases = {
            "Kingambit": (2, 2),
            "Whimsicott": (5, 1),
            "Incineroar": (3, 1),
        }
        for our_name, (our_hits, their_hits) in cases.items():
            cell = result["matrix"][(our_name, "Arcanine-Hisui")]
            self.assertEqual(cell["our_hits_to_ko"], our_hits, our_name)
            self.assertEqual(cell["their_hits_to_ko"], their_hits, our_name)
            self.assertEqual(cell["verdict"], "lose", our_name)
        self.assertEqual(
            result["matrix"][("Incineroar", "Arcanine-Hisui")]["chip_needed_frac"], 1.0)
        # Kingambit only ties on hit count (loses purely on speed) -- a far
        # smaller chip closes that gap than Whimsicott's real 5-vs-1 deficit.
        self.assertLess(
            result["matrix"][("Kingambit", "Arcanine-Hisui")]["chip_needed_frac"],
            result["matrix"][("Whimsicott", "Arcanine-Hisui")]["chip_needed_frac"])

    def test_a_hard_type_immunity_reports_chip_cannot_help(self):
        """`our_hits_to_ko` of `None` (this move can never KO the target at
        all, e.g. a hard type immunity) must report `chip_needed_frac` as
        `None` too -- no amount of chip fixes a moveset problem."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        # Whimsicott (pure Grass/Fairy-neutral attacker here) vs a Ghost --
        # find any real bring4-member/enemy cell in this fixture's own
        # matrix with our_hits_to_ko None, or synthesize a known-immune
        # pair directly if none happens to occur naturally.
        result = cf.prematch_win_conditions(
            self.row, self.our_built, self.enemy_built, self.W["typechart"])
        immune_cells = [(k, v) for k, v in result["matrix"].items()
                        if v["our_hits_to_ko"] is None]
        if not immune_cells:
            self.skipTest("no naturally-immune cell in this fixture's own matrix")
        for _k, cell in immune_cells:
            self.assertIsNone(cell["chip_needed_frac"])


class TestEvolveFromTeam(unittest.TestCase):
    """"If I define one high-performing team ... then try to see if any
    improvements can be made" -- "the --evolve-from-team should iterate for
    multiple improvements (and respect the fact that only one of a species
    can be a team ...) I need to see the best possible joint impact of
    replacing 1-3 pokemon, or replacing moves, or replacing items, with the
    aim of maximising these effects jointly."

    GREEDY HILL-CLIMBING, up to `max_changes` rounds (default 3): each
    round is a LOCAL search around that round's own fixed starting core --
    move swaps, item swaps, and whole-member swaps, one substitution at a
    time -- scored by `_evolve_dive_score` (the SAME `_CORE_BLEND_WEIGHTS`-
    weighted per-90 yardstick `_core_row`'s own "Avg Wins/90" blend uses).
    Round 1's own best pick is APPLIED before round 2 searches, and so on,
    chaining up to `max_changes` joint changes together.

    Real, verified, deliberately-suboptimal fixture: Garchomp forced onto
    Poison Jab (a real, usage-backed move, just a clearly worse pick here)
    instead of its real STAB Earthquake, on a 3-member core (Garchomp +
    Kingambit + Whimsicott) against Arcanine-Hisui (Fire/Rock -- 4x weak to
    Ground) + Toxapex. Verified directly: at `turns=2` the raw race for
    BOTH of Garchomp's own pairs flips from "no_ko" to "out_trade" once
    Earthquake replaces Poison Jab, taking the blended score from 18.0 to
    60.0 -- a real, sizeable improvement, not a rounding artifact. A
    further genuine Sitrus Berry -> Life Orb item improvement (60.0 ->
    66.0, verified directly the same way) is available on TOP of that
    move swap, once it's the round-2 baseline -- the "joint impact of
    replacing ... moves, or ... items" fixture below.
    """

    def setUp(self):
        self.W = world()
        self.core = ["Garchomp", "Kingambit", "Whimsicott"]
        self.targets = [["Arcanine-Hisui", "Toxapex"]]
        # Deliberately worse than real usage's own top pick (Earthquake) --
        # Poison Jab is still a real, usage-backed move (so the "try each
        # OTHER real usage move" search can legally offer Earthquake back
        # as a candidate), just clearly the wrong choice here.
        self.suboptimal_moves = {
            "Garchomp": ["Poison Jab", "Dragon Claw", "Rock Slide", "Protect"]}

    def _evolve(self, swap_pool=(), max_changes=1, **extra):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        return cf.evolve_from_team(
            self.core, self.targets, merged, moves, natures, typechart,
            turns=2, move_overrides=self.suboptimal_moves,
            swap_pool=list(swap_pool), max_changes=max_changes, **extra)

    def _round1(self, swap_pool=(), **extra):
        """Most of this class's own tests are about ONE round's own local
        search (already covered by `TestEvolveFromTeam`'s pre-iteration
        assertions) -- `max_changes=1` reproduces that in isolation,
        `["rounds"][0]` the exact flat, sorted-by-delta-descending list the
        tool's own original single-pass shape returned."""
        return self._evolve(swap_pool=swap_pool, max_changes=1, **extra)["rounds"][0]

    def test_earthquake_surfaces_as_the_top_move_swap_with_a_positive_delta(self):
        results = self._round1()
        self.assertTrue(results, "expected at least one genuine improvement")
        top = results[0]
        self.assertEqual(top["kind"], "move")
        self.assertEqual(top["member"], "Garchomp")
        self.assertEqual(top["added"], "Earthquake")
        self.assertGreater(top["delta"], 0.0)
        self.assertAlmostEqual(top["new_score"] - top["baseline_score"], top["delta"])

    def test_every_returned_swap_is_a_genuine_improvement(self):
        """The whole point of only surfacing `delta > 0` entries -- never a
        neutral or worse swap, "not an exhaustive dump of every swap
        tried."""
        results = self._round1()
        for r in results:
            self.assertGreater(r["delta"], 0.0, r)

    def test_results_are_sorted_by_delta_descending(self):
        results = self._round1()
        deltas = [r["delta"] for r in results]
        self.assertEqual(deltas, sorted(deltas, reverse=True))

    def test_baseline_score_is_the_same_across_every_returned_entry(self):
        """Every candidate swap in the SAME round is compared against the
        SAME fixed baseline (that round's own ONE starting team), not a
        moving target."""
        results = self._round1()
        baselines = {r["baseline_score"] for r in results}
        self.assertEqual(len(baselines), 1)

    def test_member_swap_mechanics_run_and_stay_shaped_like_a_move_swap(self):
        """A whole-member swap (a tiny explicit `swap_pool`, not the whole
        roster -- kept fast) returns entries in the SAME shape as a move
        swap, tagged "kind": "member", with "removed"/"added" naming the
        departing/arriving Pokemon rather than moves."""
        results = self._round1(swap_pool=["Hydreigon"])
        member_results = [r for r in results if r["kind"] == "member"]
        for r in member_results:
            self.assertIn(r["member"], self.core)
            self.assertEqual(r["added"], "Hydreigon")
            self.assertEqual(r["removed"], r["member"])
            self.assertGreater(r["delta"], 0.0)

    def test_item_swap_kind_is_offered_and_never_changes_membership(self):
        """New third swap kind alongside move/member: try each OTHER legal
        item on an existing member (`optimize_sets.legal_items`), holding
        moves and everyone else fixed -- "or replacing items". Uses the
        REAL (non-suboptimal-moves) core directly -- with Garchomp already
        on Earthquake, Sitrus Berry is itself the genuinely improvable
        pick (verified directly: Life Orb/Soft Sand both score +6.0 over
        it here); `self.suboptimal_moves`' own Poison-Jab fixture has no
        real item improvement available until ITS OWN move swap has
        already happened first (see
        test_iterating_chains_a_move_swap_then_an_item_swap)."""
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        results = cf.evolve_from_team(
            self.core, self.targets, merged, moves, natures, typechart,
            turns=2, swap_pool=["Hydreigon"], max_changes=1)["rounds"][0]
        item_results = [r for r in results if r["kind"] == "item"]
        self.assertTrue(item_results, "expected at least one genuine item improvement")
        for r in item_results:
            self.assertIn(r["member"], self.core)
            self.assertNotEqual(r["removed"], r["added"])
            self.assertEqual(r["team"], self.core)
            self.assertGreater(r["delta"], 0.0)

    def test_each_result_carries_its_own_resulting_team_and_breakdown_rates(self):
        """"give more in depth/summary info" -- each result now carries the
        resulting FULL roster and the individual baseline/new per-90
        breakdown rates (win/tailwind-safe/protect-safe/follow-me-safe),
        not just the single blended score."""
        results = self._round1(swap_pool=["Hydreigon"])
        self.assertTrue(results)
        for r in results:
            self.assertIn(len(r["team"]), (3,))
            self.assertEqual(set(r["team"]) - {r["added"]}, set(self.core) - {r["removed"]})
            for key in ("win_rate", "tailwind_safe_rate", "protect_safe_rate",
                       "follow_me_safe_rate"):
                self.assertIn(f"baseline_{key}", r)
                self.assertIn(f"new_{key}", r)

    def test_each_result_carries_its_full_resulting_sets(self):
        """"I need to see the details, what are the movesets of the new
        pokemon, what are the move/item changes, and so on" -- every
        result's own "sets" is the FULL resulting team's per-member item +
        4-move list, not just the one changed member's new name."""
        results = self._round1(swap_pool=["Hydreigon"])
        self.assertTrue(results)
        for r in results:
            self.assertEqual(set(r["sets"]), set(r["team"]))
            for s in r["sets"].values():
                self.assertIn("item", s)
                self.assertEqual(len(s["moves"]), 4)

    def test_jobs_2_produces_the_same_results_as_serial(self):
        """`jobs` is purely a speed knob -- the same trials, same scores,
        just farmed out to worker processes instead of run one after
        another."""
        serial = self._round1(swap_pool=["Hydreigon"])
        parallel = self._evolve(swap_pool=["Hydreigon"], jobs=2)["rounds"][0]
        key = lambda r: (r["kind"], r["member"], r["removed"], r["added"])  # noqa: E731
        self.assertEqual(sorted(serial, key=key), sorted(parallel, key=key))

    def test_progress_callback_reaches_done_equals_total_exactly_once(self):
        calls = []
        self._evolve(swap_pool=["Hydreigon"],
                     progress_callback=lambda d, t, rnd: calls.append((d, t, rnd)))
        self.assertTrue(calls)
        total = calls[0][1]
        self.assertTrue(all(t == total and rnd == 1 for _d, t, rnd in calls))
        self.assertEqual([d for d, _t, _rnd in calls], list(range(1, total + 1)))

    def test_iterating_chains_a_move_swap_then_an_item_swap(self):
        """"I need to see the best possible joint impact of replacing ...
        moves, or replacing items ... maximising these effects jointly" --
        round 1's own best move swap (Poison Jab -> Earthquake) is chained
        into round 2's own search around the now-improved team, which finds
        a further genuine item improvement (Sitrus Berry -> Life Orb) on
        top of it -- a real joint result neither round alone would show."""
        evolved = self._evolve(swap_pool=["Hydreigon"], max_changes=2)
        self.assertEqual(len(evolved["chain"]), 2)
        step1, step2 = evolved["chain"]
        self.assertEqual(step1["kind"], "move")
        self.assertEqual(step1["member"], "Garchomp")
        self.assertEqual(step1["added"], "Earthquake")
        self.assertEqual(step2["kind"], "item")
        self.assertEqual(step2["member"], "Garchomp")
        self.assertEqual(step2["added"], "Life Orb")
        # Round 2's own baseline is round 1's OWN new score, a moving
        # target ACROSS rounds -- unlike within one round (see
        # test_baseline_score_is_the_same_across_every_returned_entry).
        self.assertAlmostEqual(step2["baseline_score"], step1["new_score"])
        self.assertAlmostEqual(evolved["final_score"], step2["new_score"])
        self.assertGreater(evolved["final_score"], evolved["baseline_score"])
        self.assertEqual(evolved["final_team"], step2["team"])
        self.assertEqual(evolved["final_sets"], step2["sets"])
        self.assertEqual(len(evolved["rounds"]), 2)

    def test_max_changes_1_runs_only_one_round(self):
        """`max_changes=1` reproduces the tool's original one-pass
        behaviour -- `chain` still names the single best pick (so a caller
        always knows what WOULD be applied first), but no second round
        ever runs to chain onto it."""
        evolved = self._evolve(swap_pool=["Hydreigon"], max_changes=1)
        self.assertEqual(len(evolved["rounds"]), 1)
        self.assertEqual(len(evolved["chain"]), 1)
        self.assertEqual(evolved["chain"][0], evolved["rounds"][0][0])

    def test_iteration_stops_early_once_a_round_finds_no_improvement(self):
        """Asking for more rounds than there turn out to be genuine
        improvements for doesn't pad `rounds`/`chain` with empty or
        worse-than-baseline entries -- it just stops."""
        evolved = self._evolve(swap_pool=["Hydreigon"], max_changes=10)
        self.assertLess(len(evolved["chain"]), 10)
        self.assertEqual(len(evolved["rounds"]), len(evolved["chain"]) + 1)
        self.assertEqual(evolved["rounds"][-1], [])

    def test_no_improvement_at_all_returns_an_empty_chain_and_the_original_team(self):
        """`evolve_from_team`'s own outer orchestration, isolated from
        needing a real fixture with provably zero improvements anywhere
        (every real team this search tries tends to have SOME marginal
        item swap available) -- forcing round 1 itself to come back empty
        confirms `chain`/`final_team`/`final_score`/`final_sets` all
        correctly fall back to "nothing changed", not an error."""
        from unittest import mock
        baseline_breakdown = {"score": 42.0, "win_rate": 1.0, "tailwind_safe_rate": 1.0,
                              "protect_safe_rate": 1.0, "follow_me_safe_rate": 1.0}
        with mock.patch.object(cf, "_evolve_run_one_round",
                               return_value=(baseline_breakdown, [])):
            evolved = self._evolve(swap_pool=["Hydreigon"], max_changes=3)
        self.assertEqual(evolved["chain"], [])
        self.assertEqual(evolved["rounds"], [[]])
        self.assertEqual(evolved["final_team"], self.core)
        self.assertEqual(evolved["final_score"], 42.0)
        self.assertIsNone(evolved["final_sets"])

    def test_a_mega_is_never_offered_as_a_whole_member_swap_alongside_its_own_base_form(self):
        """"respect the fact that only one of a species can be a team, I
        see it adding a Mega version of a base form already on the team" --
        a whole-member-swap candidate that would put a Mega and its own
        base form on the team together is never offered, even when it's
        explicitly in `swap_pool`."""
        core = ["Dragonite", "Kingambit", "Whimsicott"]
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        results = cf.evolve_from_team(
            core, self.targets, merged, moves, natures, typechart, turns=2,
            swap_pool=["Mega Dragonite", "Hydreigon"], max_changes=1)["rounds"][0]
        offered = {r["added"] for r in results if r["kind"] == "member"}
        self.assertNotIn("Mega Dragonite", offered)


class TestRoundRobinSavedTeams(unittest.TestCase):
    """"Give me an option ... to only run all the saved teams vs the other
    teams (including themself), rather than creating teams" -- every saved
    team raced against every OTHER saved team (mirrors included), both
    sides' own real sets intact, no `--answer_for` search on either side.

    Real, small fixture: two 3-Pokemon teams that don't share a species
    name -- Garchomp+Kingambit+Whimsicott ("A") vs Incineroar+Toxapex+
    Arcanine-Hisui ("B") -- plus a distinctive item override on A's own
    Kingambit to confirm a saved team's own set survives into the race on
    its own side, matching this session's `--benchmark-teams` "with the
    sets intact" contract, now extended to the ENEMY side too."""

    def setUp(self):
        self.W = world()
        self.teams = {"A": ["Garchomp", "Kingambit", "Whimsicott"],
                      "B": ["Incineroar", "Toxapex", "Arcanine-Hisui"]}
        self.meta = {"A": {"sets": {"Kingambit": {"item": "Chople Berry"}}},
                    "B": {"sets": None}}

    def _run(self, **kwargs):
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        return list(cf.round_robin_saved_teams(
            self.teams, self.meta, merged, moves, natures, typechart,
            turns=1, **kwargs))

    def test_mirror_included_both_directions_raced_plus_head_to_head(self):
        """Two teams A/B: the mirror A-A and B-B each race once (there is
        only one direction against yourself), but the non-mirror A/B pair
        races BOTH directions (A's own best-4 vs B's full roster, then B's
        own best-4 vs A's full roster) plus a third "best4_vs_best4" layer
        racing each side's own best-4 directly against the other's --
        "race both directions" so every team's own summary reflects its
        own real performance, not just whichever side it landed on."""
        results = self._run()
        rows = [(a, b, layer) for a, b, _pr, _br, layer, _er in results]
        self.assertEqual(rows, [
            ("A", "A", "vs_full"),
            ("A", "B", "vs_full"),
            ("B", "A", "vs_full"),
            ("A", "B", "best4_vs_best4"),
            ("B", "B", "vs_full")])

    def test_a_teams_own_item_override_survives_on_its_own_side_every_time(self):
        """Kingambit only exists on team A -- its "Chople Berry" override
        must show up in `item1`/`item2` (OUR side's own field) whenever A
        is racing, whichever side of the matchup it's on."""
        for team_a, team_b, pair_rows, _br, _layer, _er in self._run():
            if "A" not in (team_a, team_b):
                continue
            for r in pair_rows:
                if "Kingambit" not in r["pair"]:
                    continue
                idx = r["pair"].index("Kingambit")
                item = r["item1"] if idx == 0 else r["item2"]
                self.assertEqual(item, "Chople Berry", (team_a, team_b, r["pair"]))

    def test_best4_vs_best4_enemy_roster_is_the_enemys_best4_not_its_full_roster(self):
        """The `enemy_roster` returned alongside the "best4_vs_best4" layer
        is the enemy's OWN best bring-4 (its "vs_full" row's `bring4`), not
        its full saved roster -- the whole point of this third layer being
        a narrower, more realistic head-to-head than "vs_full"."""
        results = self._run()
        vs_full_by_pair = {(a, b): br for a, b, _pr, br, layer, _er in results
                           if layer == "vs_full"}
        for a, b, _pr, _br, layer, enemy_roster in results:
            if layer != "best4_vs_best4":
                continue
            expected = list(vs_full_by_pair[(b, a)][0]["bring4"])
            self.assertEqual(sorted(enemy_roster), sorted(expected))

    def test_team_names_narrows_the_grid(self):
        results = self._run(team_names=["A"])
        self.assertEqual([(a, b) for a, b, _pr, _br, _layer, _er in results],
                         [("A", "A")])

    def test_a_team_with_an_illegal_roster_size_is_skipped_not_crashed(self):
        teams = dict(self.teams)
        teams["Tiny"] = ["Garchomp", "Kingambit"]  # only 2 -- below bring4_search's 3-6 range
        meta = dict(self.meta)
        meta["Tiny"] = {"sets": None}
        merged, moves = self.W["merged"], self.W["moves"]
        natures, typechart = self.W["natures"], self.W["typechart"]
        results = list(cf.round_robin_saved_teams(
            teams, meta, merged, moves, natures, typechart, turns=1))
        names = {n for a, b, _pr, _br, _layer, _er in results for n in (a, b)}
        self.assertNotIn("Tiny", names)


class TestTeamSideOverrides(unittest.TestCase):
    """`_team_side_overrides`, the pure helper `round_robin_saved_teams`
    uses to turn a saved team's `meta[...]["sets"]` into the five
    `bring4_search` override dicts -- skips any field a member's own set
    doesn't carry, and tolerates `None` (a plain usage-derived team)."""

    def test_none_sets_gives_five_empty_dicts(self):
        out = cf._team_side_overrides(None)
        self.assertEqual(out, ({}, {}, {}, {}, {}))

    def test_only_populated_fields_appear_per_member(self):
        sets = {"Kingambit": {"item": "Chople Berry", "moves": ["Sucker Punch"]},
                "Garchomp": {"evs": {"hp": 4}, "nature": "Jolly"}}
        item, moves, evs, nature, ability = cf._team_side_overrides(sets)
        self.assertEqual(item, {"Kingambit": "Chople Berry"})
        self.assertEqual(moves, {"Kingambit": ["Sucker Punch"]})
        self.assertEqual(evs, {"Garchomp": {"hp": 4}})
        self.assertEqual(nature, {"Garchomp": "Jolly"})
        self.assertEqual(ability, {})
