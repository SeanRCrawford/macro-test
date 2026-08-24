"""Two additive, opt-in `Battle.run_turn` hooks for a human playing
interactively -- both default to the engine's existing automatic behaviour,
so every OTHER caller in the repo (unaffected -- see the full suite +
golden_baseline.py) never has to know these exist.

    "I need to be able to choose during the battle which of my brings mega
     evolves, not before, and choose at the start of the turn on which I
     wish to mega evolve. I also need to be able to choose who I send in
     after a faint."

1. `mega_decisions`: {id(combatant): bool}. Without it, `Battle.
   _mega_evolve_now` transforms an eligible Mega pick the instant it takes
   its first move/protect action (the pre-existing rule -- "eligible" comes
   from `combatants.make_team`'s `mega_transforms` pre-commitment at
   team-build time). With it, a combatant transforms only if its id maps to
   a truthy value, so the SAME eligible Pokemon can attack normally on turn
   1 and transform on turn 3 instead, at whichever turn the caller says so.

2. `replacement_choices`: {id(the active combatant, BEFORE it faints):
   Combatant to send in for it}. Without it, `Battle._replace_fainted`
   always calls `_best_replacement` (a strategic auto-pick, not something
   either player chooses). With it, the caller's own pick is used instead,
   as long as it's still on the alive bench when the replacement actually
   happens.
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


def _movesets(combatants, W):
    return {c.name: build_moveset(W["merged"][c.name], W["moves"], top_k=TOP_K_MOVES)
           for c in combatants}


class TestMegaDecisions(unittest.TestCase):

    def setUp(self):
        self.W = world()

    def _battle(self):
        our4 = ["Mega Gyarados", "Kingambit"]
        their4 = ["Garchomp", "Basculegion"]
        oc = make_team(our4, self.W["merged"], self.W["natures"])
        ec = make_team(their4, self.W["merged"], self.W["natures"])
        battle = Battle(oc, ec, self.W["typechart"], self.W["moves"])
        ms = _movesets(oc + ec, self.W)
        return battle, ms

    def _tackle_actions(self, battle, ms):
        gyarados = battle.p1.active[0]
        kingambit = battle.p1.active[1]
        garchomp = battle.p2.active[0]
        basculegion = battle.p2.active[1]
        g_move = ms[gyarados.name][0][0]
        k_move = ms[kingambit.name][0][0]
        p1 = [Action(gyarados, "p1", "move", g_move, [garchomp]),
             Action(kingambit, "p1", "move", k_move, [basculegion])]
        gc_move = ms[garchomp.name][0][0]
        b_move = ms[basculegion.name][0][0]
        p2 = [Action(garchomp, "p2", "move", gc_move, [gyarados]),
             Action(basculegion, "p2", "move", b_move, [kingambit])]
        return p1, p2

    def test_none_preserves_the_automatic_default(self):
        """The pre-existing rule, unchanged: an eligible Mega pick
        transforms the instant it takes its first action."""
        battle, ms = self._battle()
        gyarados = battle.p1.active[0]
        p1, p2 = self._tackle_actions(battle, ms)
        battle.run_turn(p1, p2)
        self.assertTrue(gyarados.mega_evolved)
        self.assertEqual(gyarados.ability, "Mold Breaker")

    def test_explicit_false_defers_the_transform(self):
        battle, ms = self._battle()
        gyarados = battle.p1.active[0]
        p1, p2 = self._tackle_actions(battle, ms)
        battle.run_turn(p1, p2, mega_decisions={id(gyarados): False})
        self.assertFalse(gyarados.mega_evolved)
        self.assertEqual(gyarados.ability, "Intimidate")
        self.assertFalse(battle.p1.mega_used, "declining to transform must "
                                              "not spend the side's one Mega")

    def test_a_later_true_transforms_it_on_that_turn(self):
        battle, ms = self._battle()
        gyarados = battle.p1.active[0]
        p1, p2 = self._tackle_actions(battle, ms)
        battle.run_turn(p1, p2, mega_decisions={id(gyarados): False})
        self.assertFalse(gyarados.mega_evolved)
        p1b, p2b = self._tackle_actions(battle, ms)
        battle.run_turn(p1b, p2b, mega_decisions={id(gyarados): True})
        self.assertTrue(gyarados.mega_evolved)
        self.assertEqual(gyarados.ability, "Mold Breaker")

    def test_unspecified_ids_default_to_not_transforming(self):
        """`mega_decisions` given but with no entry for this combatant --
        must NOT fall back to auto-evolve (that would defeat the whole
        point of an explicit per-turn choice)."""
        battle, ms = self._battle()
        gyarados = battle.p1.active[0]
        p1, p2 = self._tackle_actions(battle, ms)
        battle.run_turn(p1, p2, mega_decisions={})
        self.assertFalse(gyarados.mega_evolved)

    def test_other_side_is_unaffected_by_our_side_only_entries(self):
        """A caller only supplying OUR combatant's choice must not silently
        block the opponent's own (separately-supplied) decision."""
        battle, ms = self._battle()
        gyarados = battle.p1.active[0]
        garchomp = battle.p2.active[0]  # not a Mega pick -- sanity control
        p1, p2 = self._tackle_actions(battle, ms)
        battle.run_turn(p1, p2, mega_decisions={id(gyarados): True})
        self.assertTrue(gyarados.mega_evolved)
        self.assertFalse(garchomp.is_mega_pick)


class TestReplacementChoices(unittest.TestCase):

    def setUp(self):
        self.W = world()

    def _fainted_battle(self):
        our4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
        their4 = ["Kingambit", "Basculegion"]
        oc = make_team(our4, self.W["merged"], self.W["natures"])
        ec = make_team(their4, self.W["merged"], self.W["natures"])
        battle = Battle(oc, ec, self.W["typechart"], self.W["moves"])
        garchomp = battle.p1.active[0]
        garchomp.fainted = True
        garchomp.current_hp = 0
        return battle, garchomp

    def test_none_falls_back_to_best_replacement(self):
        battle, garchomp = self._fainted_battle()
        battle._replace_fainted()
        incoming = battle.p1.active[0]
        self.assertIsNot(incoming, garchomp)
        self.assertTrue(incoming.name in ("Gallade", "Hydreigon"))

    def test_an_explicit_choice_is_honoured_over_best_replacement(self):
        battle, garchomp = self._fainted_battle()
        wanted = next(b for b in battle.p1.bench if b.name == "Hydreigon")
        battle._replace_fainted(replacement_choices={id(garchomp): wanted})
        self.assertIs(battle.p1.active[0], wanted)
        self.assertNotIn(wanted, battle.p1.bench)

    def test_a_choice_naming_an_unavailable_pokemon_is_ignored(self):
        """The named Pokemon isn't actually on the bench (already fainted
        earlier, say) -- falls back to `_best_replacement` rather than
        crashing or leaving the slot empty."""
        battle, garchomp = self._fainted_battle()
        not_on_bench = make_team(["Kingambit"], self.W["merged"], self.W["natures"])[0]
        battle._replace_fainted(replacement_choices={id(garchomp): not_on_bench})
        self.assertIsNot(battle.p1.active[0], garchomp)
        self.assertIn(battle.p1.active[0].name, ("Gallade", "Hydreigon"))

    def test_run_turn_threads_replacement_choices_through(self):
        """The same override, reached the way the Battle Simulator actually
        calls it -- through `run_turn`, not `_replace_fainted` directly."""
        battle, garchomp = self._fainted_battle()
        incineroar = battle.p1.active[1]
        wanted = next(b for b in battle.p1.bench if b.name == "Gallade")
        kingambit = battle.p2.active[0]
        basculegion = battle.p2.active[1]
        ms = _movesets(battle.p1.roster + battle.p2.roster, self.W)
        i_move = ms[incineroar.name][0][0]
        k_move = ms[kingambit.name][0][0]
        b_move = ms[basculegion.name][0][0]
        p1 = [Action(incineroar, "p1", "move", i_move, [kingambit])]
        p2 = [Action(kingambit, "p2", "move", k_move, [incineroar]),
             Action(basculegion, "p2", "move", b_move, [incineroar])]
        battle.run_turn(p1, p2, replacement_choices={id(garchomp): wanted})
        self.assertIs(battle.p1.active[0], wanted)


if __name__ == "__main__":
    unittest.main()
