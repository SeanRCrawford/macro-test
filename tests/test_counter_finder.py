"""Search the pool for answers to specific threats.

    "using the move selection logic, I want to search for specific pokemon
     that beat others. For instance, I want a pokemon that can OHKO or do 90%
     Kingambit, Basculegion, assuming most favourable item, or maybe a Pokemon
     which can KO a number of selected pokemon after chip from a specified
     partner using a specified move, or vs any pair of the selected Pokemon
     while taking damage according to speed order (sequential game)"
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import counter_finder as cf  # noqa: E402
from _harness import load_world  # noqa: E402
from lead_sim import BANNED_ITEMS  # noqa: E402

_WORLD = None

# A small, fixed pool so tests are fast and their numbers are hand-checkable.
POOL = ["Gallade", "Garchomp", "Ninetales-Alola", "Hydreigon", "Gyarados",
       "Arcanine-Hisui", "Mega Scizor", "Basculegion"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


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
                             min(p for p, _m in r["per_target"].values()))

    def test_gallade_sacred_sword_clears_both(self):
        """Sacred Sword ignores Defence stage changes and is Fighting into
        Kingambit's Steel/Dark and Basculegion's plain typing -- a real,
        checkable OHKO-class matchup."""
        row = self.by_name["Gallade"]
        self.assertTrue(row["meets_all"])
        for pct, _mv in row["per_target"].values():
            self.assertGreaterEqual(pct, 0.9)

    def test_no_row_ever_carries_a_banned_item(self):
        """"most favourable item" means most favourable LEGAL item."""
        for r in self.rows:
            self.assertNotIn(r["item"], BANNED_ITEMS,
                             f"{r['name']} got a banned item: {r['item']}")

    def test_a_status_move_is_never_reported_as_the_best_hit(self):
        for r in self.rows:
            for _pct, mv_name in r["per_target"].values():
                if mv_name is not None:
                    self.assertNotIn(mv_name, ("Protect", "Detect", "Taunt"))


class TestChipThenKO(unittest.TestCase):
    """Finishing a target off after a named partner's named move has landed."""

    def setUp(self):
        W = world()
        self.rows = cf.chip_then_ko(
            POOL, ["Kingambit"], "Ninetales-Alola", "Blizzard",
            W["merged"], W["moves"], W["natures"], W["typechart"])
        self.by_name = {r["name"]: r for r in self.rows}

    def test_the_partner_and_targets_are_excluded_from_the_pool(self):
        self.assertNotIn("Ninetales-Alola", self.by_name)

    def test_chip_is_the_same_worst_roll_number_for_every_row(self):
        """The partner's hit doesn't depend on who finishes the job."""
        chips = {r["chip"]["Kingambit"] for r in self.rows}
        self.assertEqual(len(chips), 1)

    def test_a_ko_actually_needs_the_chip(self):
        """Basculegion's Wave Crash alone should not one-shot Kingambit --
        this row exists to show the chip mattering, not being redundant."""
        row = self.by_name.get("Basculegion")
        self.assertIsNotNone(row)
        ko, pct, _mv = row["finishes"]["Kingambit"]
        chip = row["chip"]["Kingambit"]
        self.assertGreater(chip, 0.0)
        if ko:
            self.assertLess(pct, 1.0, "a KO credited without chip mattering "
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

    def test_pinned_no_ko_and_clean_partition_every_pair(self):
        for r in self.rows:
            clean = r["pairs_total"] - r["pairs_pinned"] - r["pairs_no_ko"]
            self.assertGreaterEqual(clean, 0)
            for d in r["detail"].values():
                if d["pinned"]:
                    self.assertEqual(d["ko"], [],
                                     "a pinned Pokemon cannot also KO -- it "
                                     "never got to act")

    def test_pinned_requires_the_summed_worst_roll_to_reach_the_whole_hp(self):
        for r in self.rows:
            for d in r["detail"].values():
                self.assertEqual(d["pinned"], d["pre_damage"] >= 1.0)

    def test_ranked_fewest_pins_then_fewest_no_kos(self):
        keys = [(r["pairs_pinned"], r["pairs_no_ko"]) for r in self.rows]
        self.assertEqual(keys, sorted(keys))

    def test_a_ko_is_only_credited_against_a_pair_member_actually_in_it(self):
        for r in self.rows:
            for pair, d in r["detail"].items():
                for name, _mv in d["ko"]:
                    self.assertIn(name, pair)
