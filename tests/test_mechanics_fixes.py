"""Three reported mechanics, each of which the engine got wrong.

    "lightning rod absorbs all single target incoming electric moves to ally
     side, does no damage, and raises spatk by 1"
    "freeze dry is super effective against water (so 4x against water/ground)"
    "switch moves do not cause a switch if they do not hit (e.g. by protect)"

The third is the one that mattered most for how the solver played. A U-turn that
pivots whether or not it connected is free repositioning, and Protect cannot
deny it -- so the engine priced pivoting as safe when the whole point of
clicking a switching move is that it is a bet on them not protecting.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Action, Battle  # noqa: E402
from combatants import make_team  # noqa: E402
from damage import DRAW_ABILITIES, type_multiplier  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def battle(ours, theirs):
    w = world()
    return Battle(make_team(list(ours), w["merged"], w["natures"]),
                  make_team(list(theirs), w["merged"], w["natures"]),
                  w["typechart"], w["moves"])


class TestFreezeDry(unittest.TestCase):
    """An Ice move that is SUPER effective on Water, not resisted by it."""

    def chart(self):
        return world()["typechart"]

    def test_ice_is_normally_resisted_by_water(self):
        self.assertEqual(type_multiplier("Ice", ["Water"], self.chart()), 0.5)

    def test_freeze_dry_is_super_effective_on_water(self):
        self.assertEqual(
            type_multiplier("Ice", ["Water"], self.chart(), "Freeze-Dry"), 2.0)

    def test_four_times_on_water_ground(self):
        """The case that was named: 2x for Water, 2x for Ground."""
        self.assertEqual(
            type_multiplier("Ice", ["Water", "Ground"], self.chart(),
                            "Freeze-Dry"), 4.0)

    def test_it_is_still_ice_against_everything_else(self):
        for types, expected in ((["Fire"], 0.5), ("Dragon", None),
                                (["Flying"], 2.0), (["Steel"], 0.5)):
            if expected is None:
                continue
            self.assertEqual(
                type_multiplier("Ice", types, self.chart(), "Freeze-Dry"),
                expected, types)

    def test_other_ice_moves_are_unaffected(self):
        self.assertEqual(
            type_multiplier("Ice", ["Water"], self.chart(), "Ice Beam"), 0.5)

    def test_water_ground_still_resists_a_normal_ice_move(self):
        """1/2 x 2 = 1, against Freeze-Dry's 4 -- an eightfold difference on the
        exact matchup that was reported."""
        self.assertEqual(
            type_multiplier("Ice", ["Water", "Ground"], self.chart(), "Ice Beam"),
            1.0)


class TestLightningRod(unittest.TestCase):

    def test_it_is_registered_as_a_drawing_ability(self):
        self.assertEqual(DRAW_ABILITIES["Lightning Rod"], ("Electric", "spa"))

    def test_storm_drain_is_the_same_mechanic(self):
        self.assertEqual(DRAW_ABILITIES["Storm Drain"], ("Water", "spa"))

    def test_absorbing_pokemon_are_not_the_ones_that_merely_resist(self):
        """Motor Drive, Volt Absorb and Sap Sipper are immunities that do NOT
        redirect. Keeping them out is deliberate."""
        for ability in ("Motor Drive", "Volt Absorb", "Sap Sipper",
                        "Water Absorb"):
            self.assertNotIn(ability, DRAW_ABILITIES)

    def test_the_redirect_runs_after_follow_me(self):
        """Follow Me is a choice made this turn and outranks a passive
        ability."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                "battle.py"), encoding="utf-8").read()
        self.assertLess(src.index("follow_me_target is not None"),
                        src.index("DRAW_ABILITIES.get(drawer.ability)"))

    def test_it_only_draws_single_target_moves(self):
        """A spread move hits everyone; there is nothing to redirect."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                "battle.py"), encoding="utf-8").read()
        block = src[src.index("Lightning Rod / Storm Drain: draw"):][:600]
        self.assertIn("single_target", block)

    def test_the_boost_is_applied_before_damage_not_after(self):
        src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                "battle.py"), encoding="utf-8").read()
        block = src[src.index("for target in hit_targets:"):][:3000]
        self.assertIn("DRAW_ABILITIES.get(target.ability)", block)
        # Before the attack stat is even read, so the move being nullified can
        # never be the one the boost applies to.
        self.assertLess(block.index("DRAW_ABILITIES"), block.index("atk_key ="))
        self.assertLess(block.index("DRAW_ABILITIES"), block.index("damage_roll("))


class TestASwitchMoveThatDoesNotHitDoesNotSwitch(unittest.TestCase):

    def pivot(self, move_key, protect):
        """Play one turn: p1 uses a switching move into a p2 that may Protect.

        When `protect` is False they must actually do something ELSE -- an
        earlier version of this helper passed the Protect move with kind "move",
        so both branches protected and the connecting case could never pass.
        """
        b = battle(["Incineroar", "Farigiraf", "Gallade", "Gholdengo"],
                   ["Pelipper", "Archaludon", "Grimmsnarl", "Garchomp"])
        user = b.p1.active[0]
        if protect:
            p2_actions = [Action(c, "p2", "protect", b.make_move("protect"), [c])
                          for c in b.p2.active]
        else:
            p2_actions = [Action(c, "p2", "move", b.make_move("tailwind"), [c])
                          for c in b.p2.active]
        b.run_turn([Action(user, "p1", "move", b.make_move(move_key),
                           [b.p2.active[0]]),
                    Action(b.p1.active[1], "p1", "protect",
                           b.make_move("protect"), [b.p1.active[1]])],
                   p2_actions)
        return b.p1.active[0] is user, b.log.dump()

    def test_parting_shot_pivots_when_it_connects(self):
        """Guard against the test passing because the move never works."""
        stayed, log = self.pivot("partingshot", protect=False)
        self.assertFalse(stayed, log[-1500:])

    def test_parting_shot_does_not_pivot_through_protect(self):
        stayed, log = self.pivot("partingshot", protect=True)
        self.assertTrue(stayed, log[-1500:])

    def test_a_blocked_parting_shot_says_so(self):
        _stayed, log = self.pivot("partingshot", protect=True)
        self.assertIn("Parting Shot was blocked", log)

    def test_parting_shot_lowers_both_attack_stats(self):
        """Measured as a DELTA: Incineroar's own Intimidate has already taken a
        stage of Attack off both foes before the move resolves, so an absolute
        check reads -2 and looks like a bug that is not there."""
        b = battle(["Incineroar", "Farigiraf", "Gallade", "Gholdengo"],
                   ["Pelipper", "Archaludon", "Grimmsnarl", "Garchomp"])
        target = b.p2.active[0]
        before = dict(target.stages)
        b.run_turn([Action(b.p1.active[0], "p1", "move",
                           b.make_move("partingshot"), [target]),
                    Action(b.p1.active[1], "p1", "protect",
                           b.make_move("protect"), [b.p1.active[1]])],
                   [Action(c, "p2", "move", b.make_move("tailwind"), [c])
                    for c in b.p2.active[1:]])
        self.assertEqual(target.stages["atk"] - before["atk"], -1)
        self.assertEqual(target.stages["spa"] - before["spa"], -1)

    def test_it_is_single_target_not_a_spread_debuff(self):
        """It used to reuse the Intimidate helper on BOTH foes."""
        b = battle(["Incineroar", "Farigiraf", "Gallade", "Gholdengo"],
                   ["Pelipper", "Archaludon", "Grimmsnarl", "Garchomp"])
        first, second = b.p2.active
        before = dict(second.stages)
        b.run_turn([Action(b.p1.active[0], "p1", "move",
                           b.make_move("partingshot"), [first]),
                    Action(b.p1.active[1], "p1", "protect",
                           b.make_move("protect"), [b.p1.active[1]])],
                   [Action(second, "p2", "move", b.make_move("tailwind"),
                           [second])])
        self.assertEqual(second.stages["atk"], before["atk"])
        self.assertEqual(second.stages["spa"], before["spa"])


if __name__ == "__main__":
    unittest.main()
