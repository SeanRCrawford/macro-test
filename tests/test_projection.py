"""Decide against the field the move fires into, not the one showing now.

The reported symptom was "far too many switches and protects into a winning
match up". The cause was not the pilot being timid. On the turn a Mega
transforms, candidate generation ran against `battle.field` -- the field BEFORE
the transform -- while the payoff matrix ran real turns AFTER it. So the matrix
was correct and the winning row was simply absent from it.

Measured on the position that was reported, Mega Charizard Y + Garchomp into
Pelipper + Mega Swampert:

    before   turn-1 equilibrium value -58.5, Solar Beam not offered at all
    after    turn-1 equilibrium value +55.3, Solar Beam in the mix at 38%

The mechanism is one line in solver.candidate_actions: a two-turn move is
skipped unless `field.weather` is already the skip weather. At decision time
that reads "rain", because Pelipper's Drizzle has landed and Charizard's Drought
has not yet -- it fires between the decision and the move.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world, setup_battle  # noqa: E402
from battle import Action  # noqa: E402
from projection import (mega_view, pending_mega, pending_megas,  # noqa: E402
                        projected_field, projected_weather)

_WORLD = None
OURS = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"]
THEIRS = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh(ours=OURS, theirs=THEIRS):
    return setup_battle(list(ours), list(theirs), world())


class TestTheProjectionItself(unittest.TestCase):

    def test_the_visible_weather_is_the_opponents(self):
        """The premise. Nothing is wrong with the engine here -- Drizzle really
        has fired and Drought really has not."""
        b, _ms = fresh()
        self.assertEqual(b.field.weather, "rain")

    def test_the_projected_weather_is_ours(self):
        b, _ms = fresh()
        self.assertEqual(projected_weather(b), "sun")

    def test_the_projection_does_not_touch_the_battle(self):
        b, _ms = fresh()
        projected_field(b)
        self.assertEqual(b.field.weather, "rain")
        self.assertFalse(b.p1.active[0].mega_evolved)

    def test_it_returns_the_real_field_when_nothing_is_pending(self):
        """No allocation in the common case, so callers can pass it anywhere."""
        b, _ms = fresh(ours=["Garchomp", "Farigiraf", "Pelipper", "Archaludon"])
        self.assertIs(projected_field(b), b.field)

    def test_both_actives_are_pending_megas_here(self):
        b, _ms = fresh()
        names = {c.name for c in pending_megas(b)}
        self.assertEqual(names, {"Mega Charizard Y", "Mega Swampert"})

    def test_a_benched_mega_is_not_pending(self):
        """A mega pick on the bench is a base-form Pokemon in every respect --
        which is exactly why a mega weather setter provides no weather until it
        actually comes in."""
        b, _ms = fresh()
        aero = [c for c in b.p1.roster if c.name == "Mega Aerodactyl"][0]
        self.assertFalse(pending_mega(b, aero))

    def test_only_one_stone_per_side(self):
        b, _ms = fresh(ours=["Mega Charizard Y", "Mega Aerodactyl",
                             "Garchomp", "Farigiraf"])
        ours = [c for c in pending_megas(b) if c in b.p1.active]
        self.assertEqual(len(ours), 1)

    def test_the_slowest_weather_setter_wins(self):
        """run_turn transforms in descending speed order and each setter
        overwrites the last, so the projection has to agree on the order rather
        than just on the set."""
        b, _ms = fresh()
        p = b.make_move("protect")
        b.run_turn([Action(c, "p1", "protect", p, [c]) for c in b.p1.active],
                   [Action(c, "p2", "protect", p, [c]) for c in b.p2.active])
        self.assertEqual(b.field.weather, "sun")


class TestTheMegaView(unittest.TestCase):

    def test_it_carries_the_mega_stats(self):
        b, _ms = fresh()
        zard = b.p1.active[0]
        self.assertGreater(mega_view(b, zard).stats["spa"], zard.stats["spa"])

    def test_it_carries_the_mega_ability(self):
        b, _ms = fresh()
        self.assertEqual(mega_view(b, b.p1.active[0]).ability, "Drought")

    def test_the_real_pokemon_is_untouched(self):
        """The view is for damage estimation only. Every Action must still be
        built from the real object, or the engine mutates a throwaway copy."""
        b, _ms = fresh()
        zard = b.p1.active[0]
        mega_view(b, zard)
        self.assertFalse(zard.mega_evolved)
        self.assertEqual(zard.ability, "Solar Power")

    def test_a_non_mega_is_returned_unchanged(self):
        b, _ms = fresh()
        chomp = b.p1.active[1]
        self.assertIs(mega_view(b, chomp), chomp)


class TestTheWinningRowIsGenerated(unittest.TestCase):
    """The actual defect: not a mispriced action, an absent one."""

    def our_moves(self, battle, movesets):
        from solver import our_candidate_joint_actions
        joints = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                             movesets, battle.turn_num + 1)
        return {a.move.name for joint in joints for a in joint
                if a.kind == "move" and a.move is not None}

    def test_solar_beam_is_offered_on_turn_one(self):
        b, ms = fresh()
        self.assertIn("Solar Beam", self.our_moves(b, ms))

    def test_it_is_in_charizards_moveset_either_way(self):
        """Guards the test above against passing for the wrong reason."""
        _b, ms = fresh()
        self.assertIn("Solar Beam",
                      {m.name for m, _pct in ms["Mega Charizard Y"]})

    def test_a_charge_move_is_still_filtered_without_its_weather(self):
        """The filter has to keep working. Same Charizard, no Drought pending
        because the stone is spent elsewhere -- Solar Beam must go back to being
        a two-turn move and drop out."""
        b, ms = fresh(ours=["Mega Aerodactyl", "Mega Charizard Y",
                            "Garchomp", "Farigiraf"])
        self.assertNotEqual(projected_weather(b), "sun")
        self.assertNotIn("Solar Beam", self.our_moves(b, ms))


class TestTheThreatMatrixAgrees(unittest.TestCase):

    def matrix(self, battle, movesets):
        from threat import build_threat_matrix
        return build_threat_matrix(battle, movesets)

    def test_solar_beam_is_a_guaranteed_ohko_on_swampert(self):
        b, ms = fresh()
        t = self.matrix(b, ms).threat(b.p1.active[0], b.p2.active[1])
        self.assertEqual(t.move, "Solar Beam")
        self.assertTrue(t.ohko)

    def test_charizard_outspeeds_swampert_because_the_rain_is_gone(self):
        """Mega Swampert has Swift Swim. It is worth nothing in its opponent's
        sun, and reading the pre-mega rain here would hand Swampert the turn."""
        b, ms = fresh()
        self.assertTrue(self.matrix(b, ms).threat(b.p1.active[0],
                                                   b.p2.active[1]).outspeeds)


if __name__ == "__main__":
    unittest.main()
