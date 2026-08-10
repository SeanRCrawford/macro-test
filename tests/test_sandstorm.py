"""Sandstorm chip damage.

Nothing implemented it: weather only ever counted down, so a sand team's central
source of pressure was worth exactly zero and any line that stalled under sand
was scored as free.

1/16 of max HP at the end of every turn, to every ACTIVE Pokemon that is not
Ground, Rock or Steel -- plus the standard ability/item exemptions, without
which a sand team's own members are damaged by the weather they set.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Battle  # noqa: E402
from combatants import make_team  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def battle_with(ours, theirs, weather="sand"):
    w = world()
    b = Battle(make_team(list(ours), w["merged"], w["natures"]),
               make_team(list(theirs), w["merged"], w["natures"]),
               w["typechart"], w["moves"])
    b.field.weather = weather
    b.field.weather_turns_left = 5
    return b


def hp_of(b):
    return {c.name: c.current_hp for side in (b.p1, b.p2) for c in side.active}


class TestWhoTakesIt(unittest.TestCase):

    def test_a_neutral_pokemon_loses_a_sixteenth_every_turn(self):
        b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        expected = c.max_hp() - int(round(c.max_hp() / 16))
        b._sandstorm_damage()
        self.assertEqual(c.current_hp, expected)
        # ...and again next turn, not once per battle.
        b._sandstorm_damage()
        self.assertEqual(c.current_hp,
                         expected - int(round(c.max_hp() / 16)))

    def test_ground_rock_and_steel_are_immune(self):
        """Excadrill is Ground/Steel, Mega Tyranitar Rock/Dark, Garchomp
        Dragon/Ground -- one of each of the three immune types."""
        b = battle_with(["Excadrill", "Garchomp"], ["Mega Tyranitar", "Milotic"])
        before = hp_of(b)
        b._sandstorm_damage()
        after = hp_of(b)
        for name in ("Excadrill", "Garchomp", "Mega Tyranitar"):
            self.assertEqual(after[name], before[name], name)
        self.assertLess(after["Milotic"], before["Milotic"])

    def test_both_sides_are_chipped(self):
        """It is weather, not an attack -- it does not care whose it is."""
        b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"])
        before = hp_of(b)
        b._sandstorm_damage()
        after = hp_of(b)
        for name in before:
            self.assertLess(after[name], before[name], name)

    def test_the_bench_is_not_in_the_weather(self):
        b = battle_with(["Hydreigon", "Gallade", "Whimsicott"],
                        ["Milotic", "Sinistcha", "Pelipper"])
        benched = b.p1.bench[0]
        before = benched.current_hp
        b._sandstorm_damage()
        self.assertEqual(benched.current_hp, before)

    def test_no_other_weather_chips(self):
        for weather in ("rain", "sun", "snow", None):
            b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"],
                            weather=weather)
            before = hp_of(b)
            b._sandstorm_damage()
            self.assertEqual(hp_of(b), before, weather)


class TestExemptions(unittest.TestCase):
    """Beyond the three types. Without these a sand team damages itself with
    the weather it exists to set."""

    def _immune_via(self, field, value):
        b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        setattr(c, field, value)
        before = c.current_hp
        b._sandstorm_damage()
        return c.current_hp == before

    def test_sand_synergy_abilities(self):
        for ability in ("Sand Veil", "Sand Rush", "Sand Force"):
            self.assertTrue(self._immune_via("ability", ability), ability)

    def test_general_weather_immunity(self):
        self.assertTrue(self._immune_via("ability", "Overcoat"))
        self.assertTrue(self._immune_via("ability", "Magic Guard"))
        self.assertTrue(self._immune_via("item", "Safety Goggles"))

    def test_an_unrelated_ability_or_item_is_not_immune(self):
        self.assertFalse(self._immune_via("ability", "Levitate"))
        self.assertFalse(self._immune_via("item", "Leftovers"))


class TestItKills(unittest.TestCase):

    def test_a_pokemon_can_faint_to_sand_and_the_log_says_so(self):
        b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        c.apply_damage(c.max_hp() - 1)
        b.log.lines.clear()
        b._sandstorm_damage()
        self.assertTrue(c.fainted)
        self.assertIn("FAINTED", "\n".join(b.log.lines))


class TestEndOfTurnWiring(unittest.TestCase):

    def test_it_runs_as_part_of_the_turn_not_only_when_called(self):
        b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"])
        before = hp_of(b)
        b.run_turn([], [])
        after = hp_of(b)
        self.assertLess(after["Hydreigon"], before["Hydreigon"])
        self.assertLess(after["Milotic"], before["Milotic"])

    def test_it_stops_when_the_weather_runs_out(self):
        b = battle_with(["Hydreigon", "Gallade"], ["Milotic", "Sinistcha"])
        b.field.weather_turns_left = 1
        b.run_turn([], [])          # last turn of sand: still chips
        self.assertIsNone(b.field.weather)
        settled = hp_of(b)
        b.run_turn([], [])
        self.assertEqual(hp_of(b), settled)


if __name__ == "__main__":
    unittest.main()
