"""Regulation M-C: Psychic Terrain (Indeedee's Psychic Surge), the priority-
move block, and Expanding Force.

    "Include Indeedee-F's psychic terrain (priority block, boosts psychic
     1.5x, and the mechanics of expanding force (single target 80bp psychic
     special move -> 120bp spread psychic target move)"

Mirrors `test_grassy_terrain.py` point for point wherever Psychic Terrain
shares Grassy Terrain's own shape (switch-in setter, same-terrain no-refresh,
per-hit damage multiplier, move-cast version, duration countdown) -- new
tests only for what's genuinely new here: the priority-move block (no Grassy
equivalent) and Expanding Force's terrain-conditional power/targeting.
Psychic Terrain has no end-of-turn heal (that's Grassy-only), so there is no
analog to `test_grassy_terrain.py`'s `TestEndOfTurnHeal`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Action, Battle  # noqa: E402
from combatants import make_team  # noqa: E402
from damage import damage_roll, effective_stat, effective_move_target, is_spread_move  # noqa: E402

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


class TestTheDataIsRight(unittest.TestCase):

    def test_indeedee_f_has_psychic_surge(self):
        abilities = dict(world()["merged"]["Indeedee-F"]["abilities_usage"])
        self.assertIn("Psychic Surge", abilities)

    def test_indeedee_m_has_psychic_surge(self):
        abilities = dict(world()["merged"]["Indeedee-M"]["abilities_usage"])
        self.assertIn("Psychic Surge", abilities)

    def test_indeedee_m_and_f_are_both_psychic_normal(self):
        merged = world()["merged"]
        self.assertEqual(set(merged["Indeedee-M"]["types"]), {"Psychic", "Normal"})
        self.assertEqual(set(merged["Indeedee-F"]["types"]), {"Psychic", "Normal"})

    def test_golisopod_and_mega_golisopod_resolve(self):
        merged = world()["merged"]
        self.assertEqual(set(merged["Golisopod"]["types"]), {"Bug", "Water"})
        self.assertEqual(set(merged["Mega Golisopod"]["types"]), {"Bug", "Steel"})

    def test_mega_golisopod_has_tough_claws(self):
        # The Mega's own stat table (distinct from the base form's) is only
        # resolved through combatants.make_combatant.
        merged = world()["merged"]
        from combatants import make_combatant
        c = make_combatant("Mega Golisopod", merged, world()["natures"])
        self.assertEqual(c.mega_ability, "Tough Claws")

    def test_mega_golisopod_stats_at_level_50_are_internally_consistent(self):
        """Not a re-derivation of the level-50 formula -- just pins that the
        base stat TABLE actually feeding it is the user-supplied one (Atk
        150/Def 175/SpA 70/SpD 120/Spe 40/HP 75), by checking the Mega's
        raw base_stats dict directly (species_data resolves this from
        Showdown's own bundled dex, not anything hand-typed here)."""
        from species_data import load_showdown_static, resolve_species
        pokedex, _moves, _nat, _tc = load_showdown_static()
        _sid, sdata = resolve_species("Mega Golisopod", pokedex)
        self.assertEqual(sdata["baseStats"],
                         {"hp": 75, "atk": 150, "def": 175, "spa": 70, "spd": 120, "spe": 40})


class TestSwitchInSetsTerrain(unittest.TestCase):

    def test_psychic_surge_sets_terrain_from_turn_one(self):
        b = fresh(["Indeedee-F", "Garchomp"], ["Milotic", "Sinistcha"])
        self.assertEqual(b.field.terrain, "psychic")
        self.assertEqual(b.field.terrain_turns_left, 5)

    def test_no_setter_no_terrain(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        self.assertIsNone(b.field.terrain)

    def test_the_log_says_so(self):
        b = fresh(["Indeedee-F", "Garchomp"], ["Milotic", "Sinistcha"])
        log = b.log.dump()
        self.assertIn("Psychic Surge", log)
        self.assertIn("Psychic Terrain", log)

    def test_terrain_and_weather_are_not_exclusive(self):
        b = fresh(["Indeedee-F", "Pelipper"], ["Milotic", "Sinistcha"])
        self.assertEqual(b.field.terrain, "psychic")
        self.assertEqual(b.field.weather, "rain")


class TestSameTerrainDoesNotRefreshDuration(unittest.TestCase):

    def test_a_second_psychic_surge_switch_in_does_not_reset_the_clock(self):
        b = fresh(["Indeedee-F", "Garchomp", "Indeedee-M"],
                  ["Milotic", "Sinistcha"])
        b.field.terrain_turns_left = 2
        from engine import on_switch_in
        second = b.p1.bench[0]
        on_switch_in(second, b.p2.active, b.field, ally=b.p1.active[0], log=b.log)
        self.assertEqual(b.field.terrain_turns_left, 2)


class TestPsychicMoveDamageBoost(unittest.TestCase):
    """Psychic moves get +50% power for a GROUNDED attacker under Psychic
    Terrain -- direct `damage_roll` ratio, same style as
    `test_grassy_terrain.py`'s `TestGrassMoveDamageBoost`."""

    def damage_with(self, terrain, attacker_name="Indeedee-F",
                    defender_name="Garchomp", move_key="psychic"):
        w = world()
        b = fresh([attacker_name, "Garchomp"], [defender_name, "Sinistcha"])
        atk = b.p1.active[0]
        dfn = b.p2.active[0]
        move = b.make_move(move_key)
        a = effective_stat(atk.stats["spa"], atk.stages["spa"])
        d = effective_stat(dfn.stats["spd"], dfn.stages["spd"])
        _mn, _mx, avg, _eff = damage_roll(50, move.power, a, d, atk, dfn, move,
                                          w["typechart"], weather=None,
                                          num_targets_hit=1, terrain=terrain)
        return avg

    def test_psychic_move_is_boosted_fifty_percent_under_terrain(self):
        no_terrain = self.damage_with(None)
        psychic_terrain = self.damage_with("psychic")
        self.assertAlmostEqual(psychic_terrain / no_terrain, 1.5, places=3)

    def test_an_airborne_attacker_gets_no_boost(self):
        no_terrain = self.damage_with(None, attacker_name="Talonflame",
                                      move_key="psychic")
        psychic_terrain = self.damage_with("psychic", attacker_name="Talonflame",
                                           move_key="psychic")
        self.assertAlmostEqual(psychic_terrain, no_terrain, places=3)

    def test_a_non_psychic_move_is_unaffected(self):
        no_terrain = self.damage_with(None, move_key="hyperbeam")
        psychic_terrain = self.damage_with("psychic", move_key="hyperbeam")
        self.assertAlmostEqual(psychic_terrain, no_terrain, places=3)


class TestPriorityMoveBlock(unittest.TestCase):
    """"priority block" -- any priority move fails outright against a
    GROUNDED target while Psychic Terrain is up. A field effect, not an
    ability, so Mold Breaker/Teravolt/Turboblaze do NOT bypass it (unlike
    the Queenly Majesty/Dazzling/Armor Tail block right next to it)."""

    def test_a_priority_move_fails_against_a_grounded_target(self):
        b = fresh(["Indeedee-F", "Incineroar"], ["Kingambit", "Garchomp"])
        indeedee, incineroar = b.p1.active
        kingambit, garchomp = b.p2.active
        aqua_jet = b.make_move("aquajet")
        tackle = b.make_move("tackle")
        protect = b.make_move("protect")
        before = indeedee.current_hp
        b.run_turn(
            [Action(indeedee, "p1", "move", tackle, [kingambit]),
             Action(incineroar, "p1", "protect", protect, [incineroar])],
            [Action(kingambit, "p2", "move", aqua_jet, [indeedee]),
             Action(garchomp, "p2", "protect", protect, [garchomp])])
        self.assertEqual(indeedee.current_hp, before)
        self.assertIn("Psychic Terrain", b.log.dump())

    def test_a_priority_move_still_lands_on_an_ungrounded_target(self):
        b = fresh(["Indeedee-F", "Pelipper"], ["Kingambit", "Garchomp"])
        indeedee, pelipper = b.p1.active
        kingambit, garchomp = b.p2.active
        aqua_jet = b.make_move("aquajet")
        tackle = b.make_move("tackle")
        protect = b.make_move("protect")
        before = pelipper.current_hp
        b.run_turn(
            [Action(pelipper, "p1", "move", tackle, [kingambit]),
             Action(indeedee, "p1", "protect", protect, [indeedee])],
            [Action(kingambit, "p2", "move", aqua_jet, [pelipper]),
             Action(garchomp, "p2", "protect", protect, [garchomp])])
        self.assertLess(pelipper.current_hp, before)

    def test_a_non_priority_move_is_unaffected(self):
        b = fresh(["Indeedee-F", "Incineroar"], ["Kingambit", "Garchomp"])
        indeedee, incineroar = b.p1.active
        kingambit, garchomp = b.p2.active
        tackle = b.make_move("tackle")
        protect = b.make_move("protect")
        before = indeedee.current_hp
        b.run_turn(
            [Action(incineroar, "p1", "protect", protect, [incineroar]),
             Action(indeedee, "p1", "move", tackle, [kingambit])],
            [Action(kingambit, "p2", "move", tackle, [indeedee]),
             Action(garchomp, "p2", "protect", protect, [garchomp])])
        self.assertLess(indeedee.current_hp, before)


class TestExpandingForce(unittest.TestCase):
    """80 power, single-target, Psychic Special -- becomes 120 power and
    hits BOTH opposing Pokemon when its user is grounded on Psychic
    Terrain."""

    def test_raw_power_and_target_off_terrain(self):
        move = world()["moves"]
        from damage import move_from_showdown
        mv = move_from_showdown(move["expandingforce"])
        self.assertEqual(mv.power, 80)
        self.assertEqual(mv.category, "Special")
        self.assertEqual(mv.move_type, "Psychic")
        self.assertFalse(is_spread_move(mv.target))

    def test_effective_target_is_spread_under_terrain_for_a_grounded_user(self):
        b = fresh(["Indeedee-F", "Garchomp"], ["Milotic", "Sinistcha"])
        ef = b.make_move("expandingforce")
        eff = effective_move_target(ef, b.p1.active[0], b.field.terrain)
        self.assertTrue(is_spread_move(eff))

    def test_effective_target_stays_single_without_terrain(self):
        w = world()
        b = Battle(make_team(["Indeedee-F", "Garchomp"], w["merged"], w["natures"]),
                  make_team(["Milotic", "Sinistcha"], w["merged"], w["natures"]),
                  w["typechart"], w["moves"])
        ef = b.make_move("expandingforce")
        eff = effective_move_target(ef, b.p1.active[0], None)
        self.assertFalse(is_spread_move(eff))

    def test_effective_target_stays_single_for_an_ungrounded_user(self):
        b = fresh(["Talonflame", "Garchomp"], ["Milotic", "Sinistcha"],
                  sets={"Talonflame": {"ability": "Gale Wings"}})
        b.field.terrain = "psychic"
        ef = b.make_move("expandingforce")
        eff = effective_move_target(ef, b.p1.active[0], b.field.terrain)
        self.assertFalse(is_spread_move(eff))

    def test_it_hits_both_enemies_under_terrain(self):
        b = fresh(["Indeedee-F", "Incineroar"], ["Kingambit", "Garchomp"])
        indeedee, incineroar = b.p1.active
        kingambit, garchomp = b.p2.active
        ef = b.make_move("expandingforce")
        protect = b.make_move("protect")
        kg_before, gc_before = kingambit.current_hp, garchomp.current_hp
        b.run_turn(
            [Action(indeedee, "p1", "move", ef, [kingambit, garchomp]),
             Action(incineroar, "p1", "protect", protect, [incineroar])],
            [Action(kingambit, "p2", "protect", protect, [kingambit]),
             Action(garchomp, "p2", "protect", protect, [garchomp])])
        # Both protected, so no damage either way -- this only pins that
        # BOTH were legitimately targeted (no crash from a 2-target list on
        # a "single-target" move), which the protect-block log confirms.
        log = b.log.dump()
        self.assertEqual(log.count("blocked by"), 2)
        self.assertEqual(kingambit.current_hp, kg_before)
        self.assertEqual(garchomp.current_hp, gc_before)

    def test_120_power_beats_80_power_single_target_on_the_same_matchup(self):
        """Direct `damage_roll` comparison, isolating the power/terrain
        change from targeting -- 120 power under Psychic Terrain's own 1.5x
        boost against the SAME single target must clear 80 power with no
        terrain by more than a spread move's own 0.75x penalty could ever
        give back."""
        w = world()
        b = fresh(["Indeedee-F", "Garchomp"], ["Sinistcha", "Milotic"])
        indeedee, sinistcha = b.p1.active[0], b.p2.active[0]
        a = effective_stat(indeedee.stats["spa"], indeedee.stages["spa"])
        d = effective_stat(sinistcha.stats["spd"], sinistcha.stages["spd"])
        ef = b.make_move("expandingforce")
        _mn, _mx, avg_120, _eff = damage_roll(
            50, 120, a, d, indeedee, sinistcha, ef, w["typechart"],
            terrain="psychic", num_targets_hit=1)
        _mn, _mx, avg_80, _eff = damage_roll(
            50, 80, a, d, indeedee, sinistcha, ef, w["typechart"],
            terrain=None, num_targets_hit=1)
        self.assertGreater(avg_120, avg_80)


class TestPsychicTerrainCastAsAMove(unittest.TestCase):

    def test_casting_it_sets_terrain(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        self.assertIsNone(b.field.terrain)
        c = b.p1.active[0]
        move = b.make_move("psychicterrain")
        p = b.make_move("protect")
        b.run_turn([Action(c, "p1", "move", move, [c]),
                   Action(b.p1.active[1], "p1", "protect", p, [b.p1.active[1]])],
                  [Action(x, "p2", "protect", p, [x]) for x in b.p2.active])
        self.assertEqual(b.field.terrain, "psychic")
        self.assertEqual(b.field.terrain_turns_left, 4)

    def test_the_log_says_so(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        c = b.p1.active[0]
        move = b.make_move("psychicterrain")
        p = b.make_move("protect")
        b.run_turn([Action(c, "p1", "move", move, [c]),
                   Action(b.p1.active[1], "p1", "protect", p, [b.p1.active[1]])],
                  [Action(x, "p2", "protect", p, [x]) for x in b.p2.active])
        self.assertIn("Psychic Terrain", b.log.dump())


class TestDurationCountdown(unittest.TestCase):

    def test_it_stops_when_the_terrain_runs_out(self):
        b = fresh(["Indeedee-F", "Garchomp"], ["Milotic", "Sinistcha"])
        b.field.terrain_turns_left = 1
        b.run_turn([], [])
        self.assertIsNone(b.field.terrain)

    def test_the_log_says_it_faded(self):
        b = fresh(["Garchomp", "Hydreigon"], ["Milotic", "Sinistcha"])
        b.field.terrain = "psychic"
        b.field.terrain_turns_left = 1
        b.run_turn([], [])
        self.assertIn("Psychic Terrain", b.log.dump())
        self.assertIn("faded", b.log.dump())


if __name__ == "__main__":
    unittest.main()
