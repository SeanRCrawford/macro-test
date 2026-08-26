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


class TestLowKickWeightBasedPower(unittest.TestCase):
    """"Low kick's base power varies depending on weight." Showdown's raw
    data gives Low Kick/Grass Knot `basePower: 0` (a `basePowerCallback` JS
    function computes the real number from the TARGET's weight at battle
    time), which this simulator did not handle -- every Low Kick and Grass
    Knot dealt exactly zero damage, silently, for the whole of this branch's
    history before this fix."""

    def test_the_raw_move_data_is_zero_power(self):
        """The bug's precondition -- if Showdown ever ships a real basePower
        for this move, `weight_based_power` becomes dead code and this whole
        fix should be revisited."""
        from damage import move_from_showdown
        lk = move_from_showdown(world()["moves"]["lowkick"])
        self.assertEqual(lk.power, 0)

    def test_breakpoints_match_the_current_gen_table(self):
        from damage import weight_based_power
        self.assertEqual(weight_based_power(9.9), 20)
        self.assertEqual(weight_based_power(10.0), 40)
        self.assertEqual(weight_based_power(24.9), 40)
        self.assertEqual(weight_based_power(25.0), 60)
        self.assertEqual(weight_based_power(49.9), 60)
        self.assertEqual(weight_based_power(50.0), 80)
        self.assertEqual(weight_based_power(99.9), 80)
        self.assertEqual(weight_based_power(100.0), 100)
        self.assertEqual(weight_based_power(199.9), 100)
        self.assertEqual(weight_based_power(200.0), 120)
        self.assertEqual(weight_based_power(999), 120)

    def test_unknown_weight_returns_none_rather_than_guessing(self):
        from damage import weight_based_power
        self.assertIsNone(weight_based_power(None))

    def test_species_carry_their_real_weight(self):
        """Populated all the way from the pokedex through make_combatant --
        not just the standalone table above."""
        from combatants import make_combatant
        w = world()
        whimsicott = make_combatant("Whimsicott", w["merged"], w["natures"])
        kingambit = make_combatant("Kingambit", w["merged"], w["natures"])
        self.assertAlmostEqual(whimsicott.weight_kg, 6.6)
        self.assertAlmostEqual(kingambit.weight_kg, 120.0)

    def test_a_mega_pick_carries_the_mega_forms_own_weight(self):
        """Mega Gallade (56.4kg) is heavier than base Gallade (52kg) -- and a
        Mega pick starts a real battle in base form, evolving on send-out
        (see `TestMegaForm` in test_counter_finder.py for the same principle
        applied to stats/ability/typing)."""
        from combatants import make_combatant
        from engine import mega_evolve
        w = world()
        c = make_combatant("Mega Gallade", w["merged"], w["natures"])
        c.is_mega_pick = True
        self.assertAlmostEqual(c.weight_kg, 52.0)
        mega_evolve(c)
        self.assertAlmostEqual(c.weight_kg, 56.4)

    def test_low_kick_deals_more_damage_to_a_heavier_target(self):
        """Direct damage_roll check: Kingambit (120kg -> 100 power) must take
        a harder Low Kick than Whimsicott (6.6kg -> 20 power) from the same
        attacker, and neither may be zero (the bug)."""
        from combatants import make_combatant
        from damage import damage_roll, move_from_showdown
        w = world()
        lk = move_from_showdown(w["moves"]["lowkick"])
        gallade = make_combatant("Gallade", w["merged"], w["natures"])
        light = make_combatant("Whimsicott", w["merged"], w["natures"])
        heavy = make_combatant("Kingambit", w["merged"], w["natures"])
        _lo, _hi, avg_light, _eff = damage_roll(
            50, lk.power, gallade.stats["atk"], light.stats["def"], gallade,
            light, lk, w["typechart"])
        _lo, _hi, avg_heavy, _eff = damage_roll(
            50, lk.power, gallade.stats["atk"], heavy.stats["def"], gallade,
            heavy, lk, w["typechart"])
        self.assertGreater(avg_light, 0.0, "Low Kick dealt zero -- the bug")
        self.assertGreater(avg_heavy, 0.0, "Low Kick dealt zero -- the bug")
        self.assertGreater(avg_heavy, avg_light)

    def test_low_kick_deals_real_damage_in_a_played_turn(self):
        """End to end through `Battle.run_turn`, not just the arithmetic --
        the exact path a real game (and this simulator's engine) takes."""
        b = battle(["Gallade", "Farigiraf"], ["Kingambit", "Garchomp"])
        target = b.p2.active[0]
        before_hp = target.current_hp
        # Only the PARTNER protects -- the target itself must not, or the hit
        # (rightly) never lands and the assertion would pass for the wrong
        # reason.
        b.run_turn([Action(b.p1.active[0], "p1", "move", b.make_move("lowkick"),
                           [target]),
                    Action(b.p1.active[1], "p1", "protect",
                           b.make_move("protect"), [b.p1.active[1]])],
                   [Action(target, "p2", "move", b.make_move("tailwind"), [target]),
                    Action(b.p2.active[1], "p2", "protect",
                           b.make_move("protect"), [b.p2.active[1]])])
        self.assertLess(target.current_hp, before_hp)

    def test_technician_reads_the_resolved_power_not_the_raw_zero(self):
        """`move.power <= 60` (Technician's own threshold) is trivially true
        for Low Kick's raw data (always 0), which would boost EVERY Low Kick
        by 1.5x regardless of the real (weight-resolved) power -- wrong for
        a heavy target where the resolved power is well above 60. Checked by
        comparing a Technician attacker's ratio of actual-to-no-ability
        damage: it must be ~1.5x against a LIGHT target (resolved power 20,
        genuinely eligible) and ~1.0x (no boost) against a HEAVY one
        (resolved power 100, not eligible)."""
        from combatants import make_combatant
        from damage import damage_roll, move_from_showdown, weight_based_power
        w = world()
        lk = move_from_showdown(w["moves"]["lowkick"])
        light = make_combatant("Whimsicott", w["merged"], w["natures"])
        heavy = make_combatant("Kingambit", w["merged"], w["natures"])
        self.assertLessEqual(weight_based_power(light.weight_kg), 60)
        self.assertGreater(weight_based_power(heavy.weight_kg), 60)

        attacker = make_combatant("Gallade", w["merged"], w["natures"])
        technician = make_combatant("Gallade", w["merged"], w["natures"])
        technician.ability = "Technician"

        for defender, expect_boost in ((light, True), (heavy, False)):
            _lo, _hi, base, _eff = damage_roll(
                50, lk.power, attacker.stats["atk"], defender.stats["def"],
                attacker, defender, lk, w["typechart"])
            _lo, _hi, boosted, _eff = damage_roll(
                50, lk.power, technician.stats["atk"], defender.stats["def"],
                technician, defender, lk, w["typechart"])
            ratio = boosted / base
            if expect_boost:
                self.assertAlmostEqual(ratio, 1.5, places=2)
            else:
                self.assertAlmostEqual(ratio, 1.0, places=2)


class TestFakeOutOnlyLegalTurnOne(unittest.TestCase):
    """"Massive error in lead_sweep.py; Sneasler uses fake out turn 2. This
    move can only be used on turn 1. ... The whole point of lead_sweep is it
    must be robust to enemy switch, protect, or attack -> I don't think it
    is." Fake Out (and First Impression) are legal only the exact turn a
    Pokemon is sent out (`active_turn_count == 0`); `solver.py`'s real
    action-generator already enforced this, but `lead_sim.py`'s OWN separate
    `candidate_joints` (used by the cheap sweep's re-verification and the
    workbook's turn-by-turn lines) and `threat.py`'s static matrix did not."""

    def _position(self):
        import lead_sim as sim
        w = world()
        battle, movesets, _sets = sim.build_position(
            ["Sneasler", "Garchomp"], ["Kingambit", "Gyarados"], w,
            optimise=False)
        return battle, movesets

    def test_fake_out_is_offered_the_turn_it_is_sent_out(self):
        import lead_sim as sim
        battle, movesets = self._position()
        joints = sim.candidate_joints(battle, "p1", movesets)
        moves_seen = {a.move.name for j in joints for a in j if a.move}
        self.assertIn("Fake Out", moves_seen)

    def test_fake_out_is_not_offered_once_active_turn_count_advances(self):
        import lead_sim as sim
        battle, movesets = self._position()
        sneasler = next(c for c in battle.p1.roster if c.name == "Sneasler")
        sneasler.active_turn_count = 1
        joints = sim.candidate_joints(battle, "p1", movesets)
        moves_seen = {a.move.name for j in joints for a in j if a.move}
        self.assertNotIn("Fake Out", moves_seen)

    def test_their_strategies_never_scripts_fake_out_past_turn_zero(self):
        """The enemy-side scripted-plan generator, used by `race()`'s
        exhaustive strategy search -- must not hand back a plan asking a
        Pokemon to Fake Out on turn index 1."""
        import lead_sim as sim
        battle, movesets = self._position()
        for seq in sim.their_strategies(battle, movesets, turns=2, breadth="full"):
            for turn_idx, (_plan, forced) in enumerate(seq):
                if forced and forced[0] == "Fake Out":
                    self.assertEqual(turn_idx, 0)

    def test_their_strategies_never_scripts_fake_out_for_an_already_active_mon(self):
        import lead_sim as sim
        battle, movesets = self._position()
        kingambit = next(c for c in battle.p2.roster if c.name == "Kingambit")
        kingambit.active_turn_count = 1
        for seq in sim.their_strategies(battle, movesets, turns=2, breadth="full"):
            for _plan, forced in seq:
                if forced:
                    self.assertFalse(forced[0] == "Fake Out" and forced[1] == "Kingambit")

    def test_best_attack_never_credits_fake_out_in_the_threat_matrix(self):
        """The cheap arithmetic layer (`scan_bring`/`race_bring`/`pin.py`)
        reads a single static best-move-per-pair value with no notion of
        which turn is being asked about, so Fake Out must never be it --
        crediting it would silently assume it lands every turn it's asked
        about, including turns it cannot possibly fire on."""
        from threat import _best_attack
        w = world()
        from combatants import make_combatant
        sneasler = make_combatant("Sneasler", w["merged"], w["natures"])
        target = make_combatant("Whimsicott", w["merged"], w["natures"])
        from solver import build_moveset
        movesets = {"Sneasler": build_moveset(w["merged"]["Sneasler"], w["moves"])}
        self.assertIn("Fake Out", [m.name for m, _pct in movesets["Sneasler"]])
        best = _best_attack(sneasler, target, movesets, w["typechart"],
                            context=(None, (), False, False, False))
        self.assertIsNotNone(best)
        move, _lo, _hi, _avg = best
        self.assertNotEqual(move.name, "Fake Out")

    def test_sneasler_never_repeats_fake_out_across_a_real_two_turn_race(self):
        """End to end: play the actual turn-by-turn race and confirm the log
        never shows Fake Out firing on turn 2 -- the exact symptom reported."""
        import lead_sim as sim
        w = world()
        our4 = ["Sneasler", "Garchomp", "Whimsicott", "Hydreigon"]
        enemy4 = ["Kingambit", "Gyarados", "Mega Floette", "Basculegion"]
        battle, movesets, _sets = sim.build_position(our4, enemy4, w, optimise=False)
        _verdict, _od, _td, _margin, _desc, log = sim.race(
            battle, movesets, turns=2, breadth="full", want_log=True)
        turn2_start = next((i for i, line in enumerate(log)
                            if "Turn 2" in line), None)
        self.assertIsNotNone(turn2_start, log)
        turn2_lines = "\n".join(log[turn2_start:])
        self.assertNotIn("Sneasler uses Fake Out", turn2_lines, log)


class TestSuckerPunchFailsIfTheTargetAlreadyMoved(unittest.TestCase):
    """"Sucker punch fails if the target outspeeds and use[s] a priority
    move." The existing check only asked "is the target ALSO using a
    damaging move THIS turn" against `ordered` -- the turn's full,
    unmutated action list -- so a target whose OWN damaging move had
    ALREADY resolved (higher priority, or a tied priority tier won on
    speed) still counted as "the target was attacking", and Sucker Punch
    landed anyway. It cannot retroactively read a move that already
    happened -- the real rule is "the target has not yet moved AND that
    still-pending move is damaging", checked against `remaining` (what's
    still queued once the Sucker Punch user's own action was popped off),
    not the original `ordered` list.
    """

    def test_extreme_speed_beats_sucker_punch_and_it_fails(self):
        """Extreme Speed is priority +2, always ahead of Sucker Punch's +1
        regardless of raw speed -- Dragonite has already moved by the time
        Kingambit's Sucker Punch would resolve."""
        b = battle(["Kingambit", "Whimsicott"], ["Dragonite", "Sylveon"])
        kingambit, dragonite = b.p1.active[0], b.p2.active[0]
        sucker_punch = b.make_move("suckerpunch")
        extreme_speed = b.make_move("extremespeed")
        protect = b.make_move("protect")
        b.run_turn(
            [Action(kingambit, "p1", "move", sucker_punch, [dragonite]),
             Action(b.p1.active[1], "p1", "protect", protect, [b.p1.active[1]])],
            [Action(dragonite, "p2", "move", extreme_speed, [kingambit]),
             Action(b.p2.active[1], "p2", "protect", protect, [b.p2.active[1]])])
        log = b.log.dump()
        self.assertIn("Sucker Punch failed", log, log)
        self.assertIn("Extreme Speed", log, log)

    def test_a_target_that_has_not_moved_yet_is_still_hit(self):
        """Contrast: a target using an ordinary (priority 0) damaging move
        has NOT yet acted when Sucker Punch (priority +1) resolves --
        Sucker Punch must still land normally, same as before this fix."""
        b = battle(["Kingambit", "Whimsicott"], ["Corviknight", "Sylveon"])
        kingambit, corviknight = b.p1.active[0], b.p2.active[0]
        sucker_punch = b.make_move("suckerpunch")
        body_press = b.make_move("bodypress")
        protect = b.make_move("protect")
        b.run_turn(
            [Action(kingambit, "p1", "move", sucker_punch, [corviknight]),
             Action(b.p1.active[1], "p1", "protect", protect, [b.p1.active[1]])],
            [Action(corviknight, "p2", "move", body_press, [kingambit]),
             Action(b.p2.active[1], "p2", "protect", protect, [b.p2.active[1]])])
        log = b.log.dump()
        self.assertNotIn("Sucker Punch failed", log, log)
        self.assertIn("Kingambit uses Sucker Punch", log, log)

    def test_upper_hand_has_the_same_still_pending_rule(self):
        """Upper Hand shares the exact same `ordered`-vs-`remaining` bug --
        confirmed by source inspection rather than a second live battle,
        since both checks were fixed together for the same reason."""
        src = open(os.path.join(os.path.dirname(__file__), "..", "src",
                                "battle.py"), encoding="utf-8").read()
        block = src[src.index('a.move.name == "Upper Hand"'):][:900]
        self.assertIn("for o in remaining", block)
        self.assertNotIn("for o in ordered", block)


if __name__ == "__main__":
    unittest.main()
