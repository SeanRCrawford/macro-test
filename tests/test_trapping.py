"""A trapped Pokemon cannot switch, and the SEARCH has to know that.

Reported, about Mega Gengar: "its Shadow Tag ability substantially reduces the
range of enemy moves (they can't switch out) so makes a line far easier to
compute". It did not, because `Battle.is_trapped` was only consulted when a turn
was EXECUTED -- the switch was refused there with a log line. The search still
offered switches as candidate actions, so every payoff matrix against a Shadow
Tag user contained columns the opponent cannot legally play.

Measured on Mega Gengar vs a four-Pokemon side, turn 1:

    base Gengar (Cursed Body)   34 joint actions, 20 of them switches
    Mega Gengar (Shadow Tag)    16 joint actions,  0 switches

So trapping was worth nothing to the evaluation, and cost us turns directly --
our own side could pick a switch that simply failed.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Battle  # noqa: E402
from combatants import make_team  # noqa: E402
from solver import (TOP_K_MOVES, build_moveset,  # noqa: E402
                    our_candidate_joint_actions)

_WORLD = None
OURS = ["Mega Gengar", "Incineroar", "Gallade", "Hydreigon"]
THEIRS = ["Garchomp", "Pelipper", "Archaludon", "Sinistcha"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh():
    w = world()
    ours = make_team(OURS, w["merged"], w["natures"])
    them = make_team(THEIRS, w["merged"], w["natures"])
    ms = {c.name: build_moveset(w["merged"][c.name], w["moves"],
                                top_k=TOP_K_MOVES) for c in ours + them}
    b = Battle(ours, them, w["typechart"], w["moves"])
    b.movesets = ms
    return b, ms


def switches(b, ms, side="them"):
    us, them = (b.p2, b.p1) if side == "them" else (b.p1, b.p2)
    joints = our_candidate_joint_actions(b, us, them, ms, 1)
    return len(joints), sum(1 for j in joints for a in j if a.kind == "switch")


class TestShadowTag(unittest.TestCase):

    def test_it_removes_their_switches_from_the_search(self):
        b, ms = fresh()
        before_joints, before_switches = switches(b, ms)
        self.assertGreater(before_switches, 0, "nothing to remove")
        b.p1.active[0].ability = "Shadow Tag"   # what Mega Evolution grants
        after_joints, after_switches = switches(b, ms)
        self.assertEqual(after_switches, 0)
        self.assertLess(after_joints, before_joints)

    def test_the_matrix_it_shrinks_is_the_point(self):
        """Half the columns is half the payoff matrix -- trapping makes the line
        cheaper to solve as well as better for us."""
        b, ms = fresh()
        before, _ = switches(b, ms)
        b.p1.active[0].ability = "Shadow Tag"
        after, _ = switches(b, ms)
        self.assertLessEqual(after, before / 1.5)

    def test_a_ghost_still_escapes(self):
        """Shadow Tag does not hold Ghost types."""
        b, ms = fresh()
        b.p1.active[0].ability = "Shadow Tag"
        ghost = next(c for c in b.p2.active if "Ghost" in c.types) \
            if any("Ghost" in c.types for c in b.p2.active) else None
        if ghost is None:                       # this pairing has none; make one
            ghost = b.p2.active[0]
            ghost.types = ["Ghost"]
        self.assertFalse(b.is_trapped(ghost))

    def test_shed_shell_still_escapes(self):
        b, ms = fresh()
        b.p1.active[0].ability = "Shadow Tag"
        runner = b.p2.active[0]
        runner.item = "Shed Shell"
        self.assertFalse(b.is_trapped(runner))

    def test_our_own_side_is_trapped_too_and_per_pokemon(self):
        """The bug cost us turns directly: a switch the search chose and the
        engine then refused. Note it is PER POKEMON -- our Gengar is a Ghost and
        walks out of Shadow Tag while its partner cannot, so this must not be a
        blanket ban on the side."""
        b, ms = fresh()
        b.p2.active[0].ability = "Shadow Tag"
        trapped = [c for c in b.p1.active if b.is_trapped(c)]
        free = [c for c in b.p1.active if not b.is_trapped(c)]
        self.assertTrue(trapped and free, "wanted one of each to compare")
        joints = our_candidate_joint_actions(b, b.p1, b.p2, ms, 1)
        movers = {a.combatant.name for j in joints for a in j
                  if a.kind == "switch"}
        for c in trapped:
            self.assertNotIn(c.name, movers)
        self.assertTrue(movers & {c.name for c in free})

    def test_nothing_changes_without_a_trapper(self):
        b, ms = fresh()
        _joints, n = switches(b, ms)
        self.assertGreater(n, 0)

    def test_a_forced_replacement_is_still_possible(self):
        """A fainted slot must still be fillable -- trapping stops a VOLUNTARY
        switch, not a replacement, and confusing the two would deadlock."""
        b, ms = fresh()
        b.p1.active[0].ability = "Shadow Tag"
        dead = b.p2.active[0]
        dead.apply_damage(dead.max_hp())
        joints = our_candidate_joint_actions(b, b.p2, b.p1, ms, 1)
        self.assertTrue(any(a.kind == "switch" for j in joints for a in j),
                        "a fainted slot has no way to be refilled")


if __name__ == "__main__":
    unittest.main()
