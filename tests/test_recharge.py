"""Hyper Beam (and the rest of the recharge family -- Giga Impact,
Blast Burn, ...) locks its user out of any action the following turn.

    "Also, Hyper Beam requires a one turn cooldown after using."

`move.flags["recharge"]` already comes straight from the real Showdown move
data (verified: `moves_db["hyperbeam"]["flags"] == {..., "recharge": 1}`),
so the only gap was `Battle.run_turn` never reading it. The fix mirrors the
existing charge-move ("Solar Beam without sun", `move.flags.get("charge")`)
handling almost exactly, just inverted in timing: charge moves spend a turn
BEFORE they hit, recharge moves spend one AFTER.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from combatants import make_team  # noqa: E402
from battle import Battle  # noqa: E402
from engine import Action  # noqa: E402
from solver import build_moveset, TOP_K_MOVES  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


class TestRecharge(unittest.TestCase):
    """Snorlax is forced onto Hyper Beam via `only_moves` -- real learnset
    legality isn't tracked in this dataset, and `build_moveset`'s own
    `only_moves` escape hatch (used elsewhere for hand-built sets) is exactly
    the tool for "make this Pokemon carry a specific move regardless of its
    usage stats"."""

    def setUp(self):
        self.W = world()
        our = ["Snorlax", "Incineroar", "Gallade"]
        their = ["Garchomp", "Kingambit"]
        self.oc = make_team(our, self.W["merged"], self.W["natures"])
        self.ec = make_team(their, self.W["merged"], self.W["natures"])
        self.battle = Battle(self.oc, self.ec, self.W["typechart"], self.W["moves"])
        self.snorlax = self.battle.p1.active[0]
        self.incineroar = self.battle.p1.active[1]
        self.gallade = next(c for c in self.battle.p1.bench if c.name == "Gallade")
        self.garchomp = self.battle.p2.active[0]
        self.kingambit = self.battle.p2.active[1]

        snorlax_ms = build_moveset(self.W["merged"]["Snorlax"], self.W["moves"],
                                   only_moves=["Hyper Beam", "Body Slam"])
        self.hyper_beam = snorlax_ms[0][0]
        self.body_slam = snorlax_ms[1][0]
        self.incineroar_move = build_moveset(
            self.W["merged"]["Incineroar"], self.W["moves"], top_k=TOP_K_MOVES)[0][0]
        self.garchomp_move = build_moveset(
            self.W["merged"]["Garchomp"], self.W["moves"], top_k=TOP_K_MOVES)[0][0]
        self.kingambit_move = build_moveset(
            self.W["merged"]["Kingambit"], self.W["moves"], top_k=TOP_K_MOVES)[0][0]

    def _enemy_actions(self):
        return [Action(self.garchomp, "p2", "move", self.garchomp_move, [self.snorlax]),
               Action(self.kingambit, "p2", "move", self.kingambit_move, [self.incineroar])]

    def _incineroar_action(self):
        return Action(self.incineroar, "p1", "move", self.incineroar_move, [self.kingambit])

    def test_using_hyper_beam_sets_must_recharge(self):
        p1 = [Action(self.snorlax, "p1", "move", self.hyper_beam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())
        self.assertTrue(self.snorlax.volatile.get("must_recharge"),
                        "using a recharge move must set the flag for next turn")

    def test_recharge_turn_forces_no_action_regardless_of_submission(self):
        """Turn 2: submit a DIFFERENT move for Snorlax (Body Slam) -- the
        engine must still force the recharge, dealing no damage from
        Snorlax this turn, exactly like the real games show no action menu
        at all during a forced recharge."""
        p1 = [Action(self.snorlax, "p1", "move", self.hyper_beam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())

        self.battle.log.lines.clear()
        p1 = [Action(self.snorlax, "p1", "move", self.body_slam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())

        # Garchomp's own Life Orb recoil still fires every turn regardless of
        # what Snorlax does, so the log (not a raw HP delta) is what proves
        # Body Slam itself never landed.
        self.assertFalse(
            any("Body Slam" in line for line in self.battle.log.lines),
            "the submitted Body Slam must never actually be used: "
            "\n" + "\n".join(self.battle.log.lines))
        self.assertFalse(self.snorlax.volatile.get("must_recharge"),
                         "the flag must clear once the recharge turn is spent")
        self.assertTrue(any("recharge" in line for line in self.battle.log.lines),
                        "\n".join(self.battle.log.lines))

    def test_free_to_act_normally_two_turns_later(self):
        """Turn 3: Snorlax is free to use Hyper Beam again."""
        p1 = [Action(self.snorlax, "p1", "move", self.hyper_beam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())
        p1 = [Action(self.snorlax, "p1", "move", self.body_slam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())
        hp_before = self.garchomp.current_hp

        p1 = [Action(self.snorlax, "p1", "move", self.hyper_beam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())

        self.assertLess(self.garchomp.current_hp, hp_before,
                        "Snorlax must be free to attack again on the third turn")
        self.assertTrue(self.snorlax.volatile.get("must_recharge"),
                        "using Hyper Beam again must re-set the flag")

    def test_recharge_blocks_switching_out(self):
        """A recharging Pokemon cannot voluntarily switch out either -- the
        real games don't offer the option, so a submitted switch action must
        simply fail to happen."""
        p1 = [Action(self.snorlax, "p1", "move", self.hyper_beam, [self.garchomp]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())

        p1 = [Action(self.snorlax, "p1", "switch", None, [self.gallade]),
             self._incineroar_action()]
        self.battle.run_turn(p1, self._enemy_actions())

        self.assertIn(self.snorlax, self.battle.p1.active,
                      "a recharging Pokemon must not be allowed to switch out")
        self.assertTrue(
            any("cannot switch out" in line for line in self.battle.log.lines),
            "\n".join(self.battle.log.lines))


if __name__ == "__main__":
    unittest.main()
