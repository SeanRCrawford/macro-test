"""Mega Evolution sets its weather on turn 1, and overrides what is already up.

Challenged, and the challenge was right about the mechanic and wrong about the
conclusion:

    "if Charizard Y mega evolves it gets drought, which overrides rain and
     makes it a win"

The first half is exactly how the engine behaves and these tests pin it. I had
told the user this matchup lost because "rain is up and Charizard's Fire is
halved", which was simply wrong -- sun is up from turn 1 and Fire is BOOSTED.

The order that makes it work, and the reason it is easy to break:

  1. leads are sent out; Pelipper's Drizzle fires -> rain
  2. turn 1 begins; Mega Evolution resolves BEFORE any move, in speed order,
     for Pokemon that are acting
  3. Charizard Y's Drought fires on transform -> sun, replacing the rain
  4. only then does turn order get computed, so Swift Swim is already dead

Get step 2 wrong -- resolve megas on send-out instead, or skip Pokemon that
"just switched in" -- and a Mega lead spends the whole game in base form with
base stats, base typing and base ability.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Action, Battle  # noqa: E402
from combatants import make_team  # noqa: E402

_WORLD = None
OURS = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"]
THEIRS = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh(ours=OURS, theirs=THEIRS):
    w = world()
    return Battle(make_team(list(ours), w["merged"], w["natures"]),
                  make_team(list(theirs), w["merged"], w["natures"]),
                  w["typechart"], w["moves"])


def protect_turn(b):
    """One turn where everyone Protects, so only the openings matter."""
    p = b.make_move("protect")
    b.run_turn([Action(c, "p1", "protect", p, [c]) for c in b.p1.active],
               [Action(c, "p2", "protect", p, [c]) for c in b.p2.active])
    return b


class TestTheDataIsRight(unittest.TestCase):

    def test_mega_charizard_y_has_drought(self):
        abilities = dict(world()["merged"]["Mega Charizard Y"]["abilities_usage"])
        self.assertIn("Drought", abilities)

    def test_the_base_form_does_not(self):
        """The whole point of the mega: the ability only exists after it
        transforms, so a benched Charizard sets no sun."""
        abilities = dict(world()["merged"]["Charizard"]["abilities_usage"])
        self.assertNotIn("Drought", abilities)


class TestTheLeadTransformsOnTurnOne(unittest.TestCase):

    def test_it_starts_in_base_form(self):
        """Correct, and the thing that makes the rest easy to misread: at
        send-out it really is a base Charizard."""
        b = fresh()
        zard = b.p1.active[0]
        self.assertTrue(zard.is_mega_pick)
        self.assertFalse(zard.mega_evolved)
        self.assertEqual(zard.ability, "Solar Power")
        self.assertEqual(b.field.weather, "rain")   # Drizzle got there first

    def test_it_has_transformed_by_the_end_of_turn_one(self):
        b = protect_turn(fresh())
        zard = b.p1.active[0]
        self.assertTrue(zard.mega_evolved)
        self.assertEqual(zard.ability, "Drought")

    def test_drought_overrides_the_rain_that_was_already_up(self):
        """THE reported mechanic. Sun, not rain, from turn 1."""
        b = protect_turn(fresh())
        self.assertEqual(b.field.weather, "sun")

    def test_the_log_says_so(self):
        b = protect_turn(fresh())
        log = b.log.dump()
        self.assertIn("Drizzle", log)
        self.assertIn("Mega Evolved", log)
        self.assertIn("Weather -> sun", log)

    def test_the_mega_stats_are_what_is_on_the_field(self):
        b = protect_turn(fresh())
        zard = b.p1.active[0]
        base = world()["merged"]["Charizard"]["base_stats"]
        self.assertNotEqual(zard.stats["spa"], base["spa"])


class TestOnlyOneMegaPerSide(unittest.TestCase):

    def test_the_second_mega_pick_stays_in_base_form(self):
        """Two stones, one transformation -- so which one leads is a real
        decision, not a cosmetic one."""
        b = protect_turn(fresh(ours=["Mega Charizard Y", "Mega Aerodactyl",
                                     "Garchomp", "Farigiraf"]))
        zard, aero = b.p1.active
        self.assertTrue(zard.mega_evolved)
        self.assertFalse(aero.mega_evolved)

    def test_leading_the_other_one_transforms_the_other_one(self):
        b = protect_turn(fresh(ours=["Mega Aerodactyl", "Mega Charizard Y",
                                     "Garchomp", "Farigiraf"]))
        aero, zard = b.p1.active
        self.assertTrue(aero.mega_evolved)
        self.assertFalse(zard.mega_evolved)
        # ...and with no Drought, the rain stands.
        self.assertEqual(b.field.weather, "rain")


class TestFireIsBoostedNotHalved(unittest.TestCase):
    """The correction, as a number: I told the user this matchup lost because
    Fire was halved by rain. It is boosted by the sun Charizard itself sets."""

    def damage_with(self, weather):
        from damage import damage_roll, effective_stat
        w = world()
        b = protect_turn(fresh())
        zard = b.p1.active[0]
        target = b.p2.active[0]
        b.field.weather = weather
        move = b.make_move("heatwave")
        atk = effective_stat(zard.stats["spa"], zard.stages["spa"])
        dfn = effective_stat(target.stats["spd"], target.stages["spd"])
        _mn, _mx, avg, _eff = damage_roll(50, move.power, atk, dfn, zard, target,
                                          move, w["typechart"],
                                          weather=weather, num_targets_hit=1)
        return avg

    def test_sun_beats_rain_for_a_fire_move(self):
        self.assertGreater(self.damage_with("sun"), self.damage_with("rain"))

    def test_and_the_battle_is_in_sun_not_rain(self):
        self.assertEqual(protect_turn(fresh()).field.weather, "sun")


if __name__ == "__main__":
    unittest.main()
