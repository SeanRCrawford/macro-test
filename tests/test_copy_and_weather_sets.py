"""Two fixes: the set optimiser's dead escape hatch, and the copy hot path.

BLIZZARD. Reported: "on the teamsheet ninetales-alola doesn't even have
blizzard which is crazy as it is its best move by far". It is a 95.7%-usage
move, and `_accuracy_ok` drops anything under 80% accuracy unless something
guarantees it. The guarantee it should have found -- "perfectly accurate in a
weather the team itself sets" -- read a `team_weather` argument that NO CALLER
EVER PASSED. Dead code: every call took the default None, so the branch could
not fire, and the one Pokemon in the game that guarantees Blizzard for itself
had it removed.

Fixed twice over, deliberately. The user's OWN ability is now checked directly,
which needs no plumbing and cannot be forgotten by a caller; and `team_weather`
is threaded from the team entry points so a partner's weather counts too
(Hurricane under a Pelipper).

THE COPY. Profiling put `copy.deepcopy` at 24% cumulative and 11.4 M calls.
Battle and Combatant already had hand-written copies; `Side` and `FieldState`
did not, so they were still walked attribute by attribute. Measured 1.13x by
alternating custom and generic in one process.

The subtlety in Side is IDENTITY: `active` and `bench` hold the same objects as
`roster`, and `follow_me_target` points at one of them. Everything goes through
the shared memo, so the copy keeps one object per Pokemon -- get that wrong and
a Pokemon is damaged in one list and healthy in another.
"""
import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import optimize_sets as opt  # noqa: E402
from _harness import load_world, setup_battle  # noqa: E402

_WORLD = None
OURS = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"]
THEIRS = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


class TestBlizzardSurvivesTheAccuracyFilter(unittest.TestCase):

    def blizzard(self):
        key = "blizzard"
        return world()["moves"][key]

    def test_the_premise_blizzard_is_shaky_on_paper(self):
        """If it were already 80%+ the filter would never have touched it and
        this whole fix would be about nothing."""
        self.assertLess(self.blizzard().get("accuracy"), 80)

    def test_it_is_ninetales_alolas_most_used_move(self):
        moves = dict(world()["merged"]["Ninetales-Alola"]["moves_usage"])
        self.assertGreater(moves["Blizzard"], 90)

    def test_its_own_snow_warning_is_enough(self):
        """No plumbing required -- this is the half that cannot be forgotten."""
        self.assertTrue(opt._accuracy_ok(self.blizzard(), "Ninetales-Alola",
                                         world()["merged"]))

    def test_a_pokemon_without_the_weather_still_loses_it(self):
        """The filter still does its job: Blizzard on something that sets no
        snow is a 70% move and stays dropped."""
        self.assertFalse(opt._accuracy_ok(self.blizzard(), "Garchomp",
                                          world()["merged"]))

    def test_a_partner_setting_the_weather_also_counts(self):
        self.assertTrue(opt._accuracy_ok(self.blizzard(), "Garchomp",
                                         world()["merged"], team_weather="snow"))

    def test_team_weather_reads_the_teams_abilities(self):
        self.assertEqual(
            opt.team_weather_for(["Garchomp", "Ninetales-Alola"],
                                 world()["merged"]), "snow")
        self.assertEqual(
            opt.team_weather_for(["Garchomp", "Pelipper"],
                                 world()["merged"]), "rain")
        self.assertIsNone(
            opt.team_weather_for(["Garchomp", "Gallade"], world()["merged"]))

    def test_the_optimised_set_now_contains_it(self):
        w = world()
        enemies = sorted({n for t in w["teams"].values() for n in t})
        best = opt.optimise_team(
            ["Ninetales-Alola", "Mega Scizor", "Mega Charizard Y", "Garchomp",
             "Farigiraf", "Gallade"],
            w["merged"], w["moves"], w["natures"], w["typechart"], enemies)
        self.assertIn("Blizzard", best["Ninetales-Alola"]["moves"])


class TestTheCopyKeepsOneObjectPerPokemon(unittest.TestCase):

    def setUp(self):
        self.battle, _ms = setup_battle(list(OURS), list(THEIRS), world())
        self.battle.p1.follow_me_target = self.battle.p1.active[0]
        self.copy = copy.deepcopy(self.battle)

    def test_active_holds_the_roster_objects(self):
        for side in (self.copy.p1, self.copy.p2):
            for c in side.active:
                if c is not None:
                    self.assertTrue(any(c is r for r in side.roster), c.name)

    def test_bench_holds_the_roster_objects(self):
        for side in (self.copy.p1, self.copy.p2):
            for c in side.bench:
                self.assertTrue(any(c is r for r in side.roster), c.name)

    def test_follow_me_target_survives_as_the_same_object(self):
        target = self.copy.p1.follow_me_target
        self.assertIsNotNone(target)
        self.assertTrue(any(target is r for r in self.copy.p1.roster))

    def test_it_shares_nothing_with_the_original(self):
        for original, copied in ((self.battle.p1, self.copy.p1),
                                 (self.battle.p2, self.copy.p2)):
            for a in original.roster:
                self.assertFalse(any(a is b for b in copied.roster), a.name)

    def test_damaging_the_copy_leaves_the_original_alone(self):
        before = self.battle.p1.active[0].current_hp
        self.copy.p1.active[0].current_hp = 1
        self.assertEqual(self.battle.p1.active[0].current_hp, before)

    def test_the_field_is_copied_not_shared(self):
        self.copy.field.weather = "sand"
        self.assertNotEqual(self.battle.field.weather, "sand")

    def test_the_field_screens_dicts_are_copied(self):
        self.copy.field.screens_p1["reflect"] = 5
        self.assertEqual(self.battle.field.screens_p1["reflect"], 0)


class TestBothHotPathsAreHandWritten(unittest.TestCase):
    """The point of the change: nothing in the copy path falls back to the
    reflective walk."""

    def test_side_has_its_own_deepcopy(self):
        from battle import Side
        self.assertIn("__deepcopy__", Side.__dict__)

    def test_field_state_has_its_own_deepcopy(self):
        from engine import FieldState
        self.assertIn("__deepcopy__", FieldState.__dict__)

    def test_battle_and_combatant_already_did(self):
        from battle import Battle
        from damage import Combatant
        self.assertIn("__deepcopy__", Battle.__dict__)
        self.assertIn("__deepcopy__", Combatant.__dict__)


if __name__ == "__main__":
    unittest.main()
