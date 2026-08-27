"""Intimidate priced as a resource, and their backs made a bring instead of six.

Two halves of one report:

    "Intimidate is not scored as a resource, nor is an enemy back bringing its
     own; the 'blank backs then calibrate' ordering isn't built."

The engine has always RESOLVED Intimidate correctly (see
tests/test_intimidate_timing.py for when it fires and on whom). What was missing
was a number for it, and a game in which their Intimidate user could be one of
the two Pokemon they left at home rather than one of four guaranteed
replacements.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import intimidate as I  # noqa: E402
import lead_scan as ls  # noqa: E402
import lead_sim as sim  # noqa: E402
from species_data import NO_MEGA, resolve_team_mega_slot  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None

OURS = ["Gyarados", "Arcanine-Hisui", "Mega Scizor", "Hydreigon"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def position(enemy4, our_sets=None, enemy_mega=None):
    return sim.build_position(OURS, enemy4, world(), our_sets=our_sets,
                              optimise=False, enemy_mega=enemy_mega)


class TestTheCounterfactualAbility(unittest.TestCase):
    """`INERT` has to be genuinely inert or every number here is a difference of
    two abilities rather than the presence of one."""

    def test_the_engine_has_no_rule_for_it(self):
        import damage
        self.assertNotIn(I.INERT, damage.TYPE_IMMUNITY_ABILITIES)
        self.assertNotIn(I.INERT, damage.DRAW_ABILITIES)
        self.assertNotIn(I.INERT, I.BACKFIRES)
        self.assertNotIn(I.INERT, I.BLOCKS)

    def test_it_does_not_block_the_attack_drop(self):
        """The one rule that would silently zero every measurement."""
        from combatants import make_combatant
        W = world()
        c = make_combatant("Garchomp", W["merged"], W["natures"],
                           ability=I.INERT)
        before = c.stages["atk"]
        from damage import apply_intimidate
        apply_intimidate(c)
        self.assertEqual(c.stages["atk"], before - 1)

    def test_the_override_reaches_the_combatant(self):
        battle, _ms, _s = position(["Garchomp", "Kingambit"],
                                   our_sets={"Gyarados": {"ability": I.INERT}})
        gyara = next(c for c in battle.p1.roster if c.name == "Gyarados")
        self.assertEqual(gyara.ability, I.INERT)


class TestHolders(unittest.TestCase):
    """Who Intimidates is read off the BUILT board, not guessed from the sheet."""

    def test_it_finds_both_sides(self):
        battle, _ms, _s = position(["Incineroar", "Kingambit"])
        found = {(h.side, h.name) for h in I.holders(battle)}
        self.assertIn(("ours", "Gyarados"), found)
        self.assertIn(("theirs", "Incineroar"), found)

    def test_it_honours_the_ability_a_set_actually_chose(self):
        """Arcanine-Hisui is 68% Rock Head, so it is NOT a holder by default --
        "Arcanine-Hisui with Intimidate may be much better than Rock Head" is a
        set decision, and the report has to follow the set."""
        battle, _ms, _s = position(["Garchomp", "Kingambit"])
        self.assertNotIn("Arcanine-Hisui",
                         [h.name for h in I.holders(battle)])
        battle, _ms, _s = position(
            ["Garchomp", "Kingambit"],
            our_sets={"Arcanine-Hisui": {"ability": "Intimidate"}})
        self.assertIn("Arcanine-Hisui", [h.name for h in I.holders(battle)])

    def test_a_lead_is_marked_as_one_and_a_bench_mon_is_not(self):
        battle, _ms, _s = position(["Mega Blastoise", "Mega Delphox",
                                    "Maushold", "Incineroar"],
                                   enemy_mega="Mega Blastoise")
        incin = next(h for h in I.holders(battle) if h.name == "Incineroar")
        self.assertFalse(incin.lead)
        self.assertEqual(incin.where, "back")


class TestWhatItIsWorth(unittest.TestCase):

    def test_their_leading_intimidate_is_priced_against_us(self):
        """Perish Trap leads Incineroar. Turning it off has to move the margin
        our way, or the measurement is not measuring anything."""
        _base, values = I.worth(OURS, ["Incineroar", "Mega Gengar",
                                       "Sinistcha", "Kommo-o"], world(),
                                enemy_mega="Mega Gengar")
        theirs = next(v for v in values if v.holder.name == "Incineroar")
        self.assertTrue(theirs.brought)
        self.assertGreater(theirs.worth, 0.0)
        self.assertGreater(theirs.margin_without, theirs.margin_with)

    def test_a_back_that_never_arrives_is_reported_unpaid_for(self):
        """The gap this module exists for: their Intimidate user is usually not
        their lead, and a two-turn window never makes you pay for it."""
        _base, values = I.worth(OURS, ["Mega Blastoise", "Mega Delphox",
                                       "Maushold", "Incineroar"], world(),
                                enemy_mega="Mega Blastoise", turns=2)
        incin = next(v for v in values if v.holder.name == "Incineroar")
        self.assertFalse(incin.brought)
        self.assertEqual(incin.worth, 0.0)

    def test_the_counterfactual_changes_the_ability_and_nothing_else(self):
        """Items and moves are held fixed, or the number folds in a re-optimised
        item and is reported as the worth of an ability."""
        enemy4 = ["Incineroar", "Mega Gengar", "Sinistcha", "Kommo-o"]
        base, _ms, resolved = position(enemy4, enemy_mega="Mega Gengar")
        alt, _ms2, _r2 = sim.build_position(
            OURS, enemy4, world(), our_sets=resolved,
            enemy_sets={"Incineroar": {"ability": I.INERT}},
            optimise=False, enemy_mega="Mega Gengar")
        for b, a in zip(base.p1.roster + base.p2.roster,
                        alt.p1.roster + alt.p2.roster):
            self.assertEqual(b.name, a.name)
            self.assertEqual(b.item, a.item)
            self.assertEqual(b.stats, a.stats)
            if b.name != "Incineroar":
                self.assertEqual(b.ability, a.ability)
        incin_before = next(c for c in base.p2.roster if c.name == "Incineroar")
        incin_after = next(c for c in alt.p2.roster if c.name == "Incineroar")
        self.assertEqual(incin_before.ability, I.INTIMIDATE)
        self.assertEqual(incin_after.ability, I.INERT)

    def test_two_holders_get_a_joint_row_as_well_as_their_marginals(self):
        """Turning one of two off leaves the other still dropping Attack, so the
        marginals can both read zero while the pair is worth something. The
        joint row is the number for "how much of this is Intimidate"."""
        sets = {"Arcanine-Hisui": {"ability": "Intimidate"}}
        _base, values = I.worth(OURS, ["Mega Aerodactyl", "Kingambit",
                                       "Garchomp", "Sylveon"], world(),
                                our_sets=sets)
        ours = [v for v in values if v.holder.side == "ours"]
        self.assertEqual(sum(1 for v in ours if v.joint), 1)
        joint = next(v for v in ours if v.joint)
        self.assertEqual(set(joint.holder.names),
                         {"Gyarados", "Arcanine-Hisui"})

    def test_one_holder_gets_no_joint_row(self):
        _base, values = I.worth(OURS, ["Garchomp", "Kingambit"], world())
        self.assertEqual([v for v in values if v.joint], [])

    def test_defiant_on_the_board_is_named(self):
        """Not branched on -- the simulator already applied the boost and the
        measurement already priced it -- but a reader needs to be told why an
        Intimidate came out small."""
        _base, values = I.worth(OURS, ["Mega Aerodactyl", "Kingambit"], world())
        ours = next(v for v in values if v.holder.side == "ours")
        self.assertIn("Kingambit", ours.note)
        self.assertIn("Defiant", ours.note)

    def test_describe_says_which_window_it_used(self):
        """The sign of a worth can change with the horizon (measured: -0.25 at
        two turns, +0.07 at three), so a block that does not state its window is
        not a number anyone can act on."""
        _base, values = I.worth(OURS, ["Incineroar", "Mega Gengar"], world())
        lines = I.describe(values, turns=2)
        self.assertIn("over 2 turns", lines[0])


class TestBlankBacksThenCalibrate(unittest.TestCase):
    """They bring FOUR. Handing them six is a different game, not a harder one."""

    ROSTER = ["Basculegion", "Mega Charizard Y", "Mega Floette", "Garchomp",
              "Kingambit", "Whimsicott"]
    LEAD = ("Basculegion", "Mega Charizard Y")

    def positions(self, backs):
        return ls.enemy_positions(self.ROSTER, self.LEAD, "Mega Charizard Y",
                                  backs=backs)

    def test_worst_enumerates_every_bring_four_behind_that_lead(self):
        got = self.positions(ls.WORST)
        self.assertEqual(len(got), 6)          # C(4, 2) back pairs
        for names, _mega in got:
            self.assertEqual(len(names), 4)
            for n in self.LEAD:
                self.assertIn(n, names)

    def test_blank_is_the_lead_pair_alone_and_roster_is_all_six(self):
        (blank, _m), = self.positions(ls.BLANK)
        self.assertEqual(list(blank), list(self.LEAD))
        (whole, _m2), = self.positions(ls.ROSTER)
        self.assertEqual(len(whole), 6)

    def test_a_slice_without_their_stone_evolves_nobody(self):
        """`None` would evolve whichever Mega name sorts first, handing them a
        second stone; NO_MEGA is the third answer."""
        names, mega = ls._as_bring(["Mega Floette", "Garchomp"],
                                   "Mega Charizard Y")
        self.assertEqual(names, ["Mega Floette", "Garchomp"])
        self.assertEqual(mega, NO_MEGA)
        chosen, forced = resolve_team_mega_slot(names, mega_transforms=mega)
        self.assertIsNone(chosen)
        self.assertEqual(forced, ["Mega Floette"])

    def test_a_slice_holding_their_stone_still_pins_it(self):
        names, mega = ls._as_bring(["Mega Floette", "Mega Charizard Y"],
                                   "Mega Charizard Y")
        self.assertEqual(mega, "Mega Charizard Y")

    def test_a_mega_whose_base_species_is_unplayed_builds(self):
        """The regression the rename caused: "Mega Delphox" was rewritten to
        "Delphox", which is not in mbsmogon.xlsx at all -- KeyError('Delphox')
        the moment a bring-4 contained it."""
        names, mega = ls._as_bring(["Mega Blastoise", "Mega Delphox",
                                    "Maushold", "Sinistcha"], "Mega Blastoise")
        battle, _ms, _s = position(names, enemy_mega=mega)
        delphox = next(c for c in battle.p2.roster if c.name == "Mega Delphox")
        self.assertFalse(delphox.is_mega_pick)   # forced to base, keeps its name

    def test_every_opening_records_its_worst_bring_and_its_blank_reference(self):
        W = world()
        report, _ms, _b = ls.full_report(
            OURS, ["Incineroar", "Mega Gengar", "Sinistcha", "Kommo-o"], W,
            opponent_name="Perish Trap", mega_name="Mega Gengar",
            want_logs=False)
        self.assertTrue(report.results)
        for r in report.results:
            self.assertEqual(len(r.bring), 4)
            for n in r.enemy_lead:
                self.assertIn(n, r.bring)
            self.assertIsNotNone(r.blank)

    def test_sweep_mode_still_faces_the_lead_pair_alone(self):
        W = world()
        report, _ms, _b = ls.full_report(
            OURS, ["Incineroar", "Mega Gengar", "Sinistcha", "Kommo-o"], W,
            opponent_name="Perish Trap", mega_name="Mega Gengar", plays=False)
        self.assertTrue(report.results)
        self.assertTrue(all(r.bring is None for r in report.results))


if __name__ == "__main__":
    unittest.main()
