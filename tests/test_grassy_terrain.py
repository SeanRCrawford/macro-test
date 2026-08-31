"""Regulation M-C: Grassy Terrain (Rillaboom's Grassy Surge) and Grassy Glide.

    "Grassy Surge sets grassy terrain, and boosts grass type damage and e.g.,
     reduces earthquake damage, while grassy glide is priority grass."

Zero terrain modeling existed anywhere in this engine before this feature --
these tests pin the whole thing, mirroring the existing weather tests
(`test_mega_weather.py`, `test_sandstorm.py`) point for point since terrain
was built by copying weather's own shape at every touch point:

  - a switch-in setter (`on_switch_in`) that does NOT refresh an already-
    matching terrain, same rule weather uses
  - a per-hit damage-formula multiplier (grounded Grass +30%, the Earthquake
    family -50% vs a grounded defender) threaded into `damage_roll`
  - a recurring end-of-turn effect (here a HEAL, not chip) that only exists
    in the real engine (`battle.py`), never in the cheap model -- same
    "real-engine-only" rule sandstorm's own chip damage follows
  - a duration counted down at end of turn, same shape as weather's

"Grounded" = not (Flying-type or ability == Levitate) -- this engine has no
Air Balloon/Magnet Rise, so that is the whole rule (`damage.is_grounded`).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Action, Battle  # noqa: E402
from combatants import make_team  # noqa: E402
from damage import damage_roll, effective_stat, is_grounded  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh(ours, theirs, sets=None, enemy_sets=None):
    w = world()
    return Battle(make_team(list(ours), w["merged"], w["natures"], sets=sets),
                  make_team(list(theirs), w["merged"], w["natures"], sets=enemy_sets),
                  w["typechart"], w["moves"])


def protect_turn(b):
    """One turn where everyone Protects, so only the switch-in openings
    (and any end-of-turn field effects) matter."""
    p = b.make_move("protect")
    b.run_turn([Action(c, "p1", "protect", p, [c]) for c in b.p1.active],
               [Action(c, "p2", "protect", p, [c]) for c in b.p2.active])
    return b


def hp_of(b):
    return {c.name: c.current_hp for side in (b.p1, b.p2) for c in side.active}


class TestTheDataIsRight(unittest.TestCase):

    def test_rillaboom_has_grassy_surge(self):
        abilities = dict(world()["merged"]["Rillaboom"]["abilities_usage"])
        self.assertIn("Grassy Surge", abilities)


class TestSwitchInSetsTerrain(unittest.TestCase):

    def test_grassy_surge_sets_terrain_from_turn_one(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        self.assertEqual(b.field.terrain, "grassy")
        self.assertEqual(b.field.terrain_turns_left, 5)

    def test_no_setter_no_terrain(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        self.assertIsNone(b.field.terrain)

    def test_the_log_says_so(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        log = b.log.dump()
        self.assertIn("Grassy Surge", log)
        self.assertIn("Grassy Terrain", log)

    def test_terrain_and_weather_are_not_exclusive(self):
        """Rillaboom's Grassy Surge and a partner's Drizzle both fire on the
        same turn 1 -- terrain is a SEPARATE field slot from weather, so
        both stick, unlike two competing weather setters."""
        b = fresh(["Rillaboom", "Pelipper"], ["Milotic", "Sinistcha"])
        self.assertEqual(b.field.terrain, "grassy")
        self.assertEqual(b.field.weather, "rain")


class TestSameTerrainDoesNotRefreshDuration(unittest.TestCase):

    def test_a_second_grassy_surge_switch_in_does_not_reset_the_clock(self):
        b = fresh(["Rillaboom", "Garchomp", "Rillaboom"],
                  ["Milotic", "Sinistcha"])
        b.field.terrain_turns_left = 2  # pretend a few turns already passed
        from engine import on_switch_in
        rilla2 = b.p1.bench[0]
        on_switch_in(rilla2, b.p2.active, b.field, ally=b.p1.active[0], log=b.log)
        self.assertEqual(b.field.terrain_turns_left, 2)


class TestGrassMoveDamageBoost(unittest.TestCase):
    """Grass moves get +30% power for a GROUNDED attacker under Grassy
    Terrain -- checked as a direct `damage_roll` ratio, same style as
    `test_mega_weather.py`'s own sun-vs-rain check."""

    def damage_with(self, terrain, attacker_name="Rillaboom",
                    defender_name="Kingambit", move_key="woodhammer"):
        w = world()
        b = fresh([attacker_name, "Garchomp"], [defender_name, "Sinistcha"])
        atk = b.p1.active[0]
        dfn = b.p2.active[0]
        move = b.make_move(move_key)
        a = effective_stat(atk.stats["atk"], atk.stages["atk"])
        d = effective_stat(dfn.stats["def"], dfn.stages["def"])
        _mn, _mx, avg, _eff = damage_roll(50, move.power, a, d, atk, dfn, move,
                                          w["typechart"], weather=None,
                                          num_targets_hit=1, terrain=terrain)
        return avg

    def test_grass_move_is_boosted_thirty_percent_under_terrain(self):
        no_terrain = self.damage_with(None)
        grassy = self.damage_with("grassy")
        self.assertAlmostEqual(grassy / no_terrain, 1.3, places=3)

    def test_an_airborne_attacker_gets_no_boost(self):
        """Not grounded (Flying-type) -- Grassy Terrain does not touch it."""
        no_terrain = self.damage_with(None, attacker_name="Talonflame",
                                      move_key="seedbomb")
        grassy = self.damage_with("grassy", attacker_name="Talonflame",
                                  move_key="seedbomb")
        self.assertAlmostEqual(grassy, no_terrain, places=3)

    def test_a_non_grass_move_is_unaffected(self):
        no_terrain = self.damage_with(None, move_key="hammerarm")
        grassy = self.damage_with("grassy", move_key="hammerarm")
        self.assertAlmostEqual(grassy, no_terrain, places=3)


class TestEarthquakeFamilyReduction(unittest.TestCase):
    """The Earthquake family gets -50% power against a GROUNDED defender."""

    def damage_with(self, terrain, defender_name="Kingambit", move_key="earthquake"):
        w = world()
        # `protect_turn` forces any Mega pick (e.g. Mega Garchomp Z, below)
        # to actually transform first -- a fresh Battle starts every Mega
        # pick in BASE form (real Mega Evolution timing, see
        # `test_mega_weather.py`), so reading `dfn.ability` before playing a
        # turn would see the base ability, not the Mega's.
        b = protect_turn(fresh(["Baxcalibur", "Garchomp"], [defender_name, "Sinistcha"]))
        atk = b.p1.active[0]
        dfn = b.p2.active[0]
        move = b.make_move(move_key)
        a = effective_stat(atk.stats["atk"], atk.stages["atk"])
        d = effective_stat(dfn.stats["def"], dfn.stages["def"])
        _mn, _mx, avg, _eff = damage_roll(50, move.power, a, d, atk, dfn, move,
                                          w["typechart"], weather=None,
                                          num_targets_hit=1, terrain=terrain)
        return avg

    def test_earthquake_is_halved_against_a_grounded_defender(self):
        no_terrain = self.damage_with(None)
        grassy = self.damage_with("grassy")
        self.assertAlmostEqual(grassy / no_terrain, 0.5, places=3)

    def test_bulldoze_is_in_the_family_too(self):
        no_terrain = self.damage_with(None, move_key="bulldoze")
        grassy = self.damage_with("grassy", move_key="bulldoze")
        self.assertAlmostEqual(grassy / no_terrain, 0.5, places=3)

    def test_magnitude_is_named_in_the_family_constant(self):
        """Magnitude's raw Showdown data carries `basePower: 0` (its real
        power is a random 10-150 roll) with no per-use resolution anywhere
        in this codebase -- same "not modeled" gap Low Kick/Grass Knot would
        have without `WEIGHT_BASED_POWER`, just never plugged for Magnitude
        specifically. `damage_roll` can't be driven to a nonzero number for
        it, so this only pins that it's still named in the family, same as
        the other two."""
        from damage import EARTHQUAKE_FAMILY
        self.assertIn("Magnitude", EARTHQUAKE_FAMILY)

    def test_a_levitate_defender_is_untouched(self):
        """Mega Garchomp Z carries Levitate -- not grounded, so the terrain
        has nothing to shield it from in the first place."""
        no_terrain = self.damage_with(None, defender_name="Mega Garchomp Z")
        grassy = self.damage_with("grassy", defender_name="Mega Garchomp Z")
        self.assertAlmostEqual(grassy, no_terrain, places=3)


class TestIsGrounded(unittest.TestCase):

    def test_a_plain_grounded_pokemon(self):
        b = fresh(["Kingambit", "Garchomp"], ["Sinistcha", "Milotic"])
        self.assertTrue(is_grounded(b.p1.active[0]))

    def test_a_flying_type_is_not_grounded(self):
        b = fresh(["Corviknight", "Garchomp"], ["Sinistcha", "Milotic"])
        self.assertFalse(is_grounded(b.p1.active[0]))

    def test_levitate_is_not_grounded(self):
        b = fresh(["Rotom-Wash", "Garchomp"], ["Sinistcha", "Milotic"])
        self.assertFalse(is_grounded(b.p1.active[0]))


class TestGrassyGlidePriority(unittest.TestCase):
    """"grassy glide is priority grass" -- +1 priority for a grounded user,
    only while Grassy Terrain is up."""

    def test_grassy_glide_is_zero_priority_on_its_own(self):
        move = world()["moves"]
        from damage import move_from_showdown
        mv = move_from_showdown(move["grassyglide"])
        self.assertEqual(mv.priority, 0)

    def test_it_outpaces_a_faster_foe_under_terrain(self):
        """Baxcalibur (87 base Speed) is faster than Rillaboom (85) on raw
        speed -- so without terrain, Rillaboom's Wood Hammer goes second;
        under Grassy Terrain, Grassy Glide's +1 priority sends it first."""
        b = fresh(["Rillaboom", "Garchomp"], ["Baxcalibur", "Sinistcha"])
        self.assertEqual(b.field.terrain, "grassy")
        rilla, bax = b.p1.active[0], b.p2.active[0]
        glide = b.make_move("grassyglide")
        icicle = b.make_move("iciclecrash")
        from engine import turn_order
        order = turn_order([Action(rilla, "p1", "move", glide, [bax]),
                            Action(bax, "p2", "move", icicle, [rilla])],
                           b.field)
        self.assertIs(order[0].combatant, rilla)

    def test_without_terrain_the_faster_mon_goes_first(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Baxcalibur", "Sinistcha"],
                  sets={"Rillaboom": {"ability": "Overgrow"}})
        self.assertIsNone(b.field.terrain)
        rilla, bax = b.p1.active[0], b.p2.active[0]
        glide = b.make_move("grassyglide")
        icicle = b.make_move("iciclecrash")
        from engine import turn_order
        order = turn_order([Action(rilla, "p1", "move", glide, [bax]),
                            Action(bax, "p2", "move", icicle, [rilla])],
                           b.field)
        self.assertIs(order[0].combatant, bax)

    def test_an_airborne_user_gets_no_bump(self):
        """Contrived (nothing airborne actually has Grassy Glide), but pins
        the gate itself rather than just its one real user."""
        from damage import grassy_glide_priority_bonus
        b = fresh(["Corviknight", "Garchomp"], ["Sinistcha", "Milotic"])
        glide = b.make_move("grassyglide")
        self.assertEqual(
            grassy_glide_priority_bonus(b.p1.active[0], glide, "grassy"), 0)


class TestGrassyTerrainCastAsAMove(unittest.TestCase):
    """The move itself (not the Rillaboom-only ability) -- always (re)sets
    the full 5-turn duration, unlike the switch-in ability's same-terrain
    no-refresh rule (there is no "re-using the exact same move" concern)."""

    def test_casting_it_sets_terrain(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        self.assertIsNone(b.field.terrain)
        c = b.p1.active[0]
        move = b.make_move("grassyterrain")
        p = b.make_move("protect")
        b.run_turn([Action(c, "p1", "move", move, [c]),
                   Action(b.p1.active[1], "p1", "protect", p, [b.p1.active[1]])],
                  [Action(x, "p2", "protect", p, [x]) for x in b.p2.active])
        self.assertEqual(b.field.terrain, "grassy")
        # Set to 5 during resolution, same as the switch-in ability -- but
        # the SAME turn's end-of-turn countdown already ran once by the time
        # `run_turn` returns (mirrors weather's own `weather_turns_left`
        # after a turn-1 Drizzle switch-in), so what's left standing is 4.
        self.assertEqual(b.field.terrain_turns_left, 4)

    def test_the_log_says_so(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        move = b.make_move("grassyterrain")
        p = b.make_move("protect")
        b.run_turn([Action(c, "p1", "move", move, [c]),
                   Action(b.p1.active[1], "p1", "protect", p, [b.p1.active[1]])],
                  [Action(x, "p2", "protect", p, [x]) for x in b.p2.active])
        self.assertIn("Grassy Terrain", b.log.dump())

    def test_recasting_it_refreshes_the_clock(self):
        """Without the refresh this would count down from 1 to 0 (terrain
        clears) instead of the fresh 5 (then 4 post-decrement) a real re-cast
        gives -- the gap this test actually pins."""
        b = fresh(["Rillaboom", "Hydreigon"], ["Milotic", "Sinistcha"])
        b.field.terrain_turns_left = 1
        c = b.p1.active[0]
        move = b.make_move("grassyterrain")
        p = b.make_move("protect")
        b.run_turn([Action(c, "p1", "move", move, [c]),
                   Action(b.p1.active[1], "p1", "protect", p, [b.p1.active[1]])],
                  [Action(x, "p2", "protect", p, [x]) for x in b.p2.active])
        self.assertEqual(b.field.terrain, "grassy")
        self.assertEqual(b.field.terrain_turns_left, 4)


class TestEndOfTurnHeal(unittest.TestCase):
    """"the real-game counterpart to sandstorm's chip" -- 1/16 max HP to
    every grounded ACTIVE Pokemon while Grassy Terrain is up."""

    def test_a_grounded_pokemon_heals_a_sixteenth_every_turn(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        c.current_hp = c.max_hp() // 2
        before = c.current_hp
        b._grassy_terrain_heal()
        self.assertEqual(c.current_hp, before + int(round(c.max_hp() / 16)))

    def test_full_hp_pokemon_does_not_overheal(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        self.assertEqual(c.current_hp, c.max_hp())
        b._grassy_terrain_heal()
        self.assertEqual(c.current_hp, c.max_hp())

    def test_a_flying_type_does_not_heal(self):
        b = fresh(["Rillaboom", "Corviknight"], ["Milotic", "Sinistcha"])
        corv = b.p1.active[1]
        corv.current_hp = corv.max_hp() // 2
        before = corv.current_hp
        b._grassy_terrain_heal()
        self.assertEqual(corv.current_hp, before)

    def test_no_heal_without_terrain(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        c.current_hp = c.max_hp() // 2
        before = c.current_hp
        b._grassy_terrain_heal()
        self.assertEqual(c.current_hp, before)

    def test_the_bench_does_not_heal(self):
        b = fresh(["Rillaboom", "Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        benched = b.p1.bench[0]
        benched.current_hp = benched.max_hp() // 2
        before = benched.current_hp
        b._grassy_terrain_heal()
        self.assertEqual(benched.current_hp, before)

    def test_it_runs_as_part_of_the_turn(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        c.current_hp = c.max_hp() // 2
        before = c.current_hp
        b.run_turn([], [])
        self.assertGreater(c.current_hp, before)

    def test_a_fainted_pokemon_is_not_healed(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        c.current_hp = 0
        c.fainted = True
        b._grassy_terrain_heal()
        self.assertEqual(c.current_hp, 0)


class TestDurationCountdown(unittest.TestCase):

    def test_it_stops_when_the_terrain_runs_out(self):
        b = fresh(["Rillaboom", "Garchomp"], ["Milotic", "Sinistcha"])
        b.field.terrain_turns_left = 1
        b.run_turn([], [])          # last turn of terrain: still heals
        self.assertIsNone(b.field.terrain)
        settled = hp_of(b)
        b.run_turn([], [])
        self.assertEqual(hp_of(b), settled)

    def test_the_log_says_it_faded(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        b.field.terrain = "grassy"
        b.field.terrain_turns_left = 1
        b.run_turn([], [])
        self.assertIn("Grassy Terrain", b.log.dump())
        self.assertIn("faded", b.log.dump())


if __name__ == "__main__":
    unittest.main()
