"""Sacred Sword ignores the target's Defence stage changes.

Reported: "Sacred Sword move ignores enemy defence changes". It did not -- the
raw move data carries `ignoreDefensive`, but nothing read it, so a +2 Iron
Defence blunted Sacred Sword exactly as it would blunt Close Combat.

Showdown ignores the stage in BOTH directions (it hits as though the stage were
0), so this also pins the less obvious half: a target at -2 Defence does not
take extra damage from Sacred Sword.

Four separate places computed the defence stat from the stage, so the flag is
checked at each of them -- getting it right in the simulator but not in the
solver that CHOOSES the move would just move the bug.
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
IGNORERS = ("Sacred Sword", "Chip Away", "Darkest Lariat")


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh(ours=("Gallade", "Hydreigon"), theirs=("Milotic", "Sinistcha")):
    w = world()
    return Battle(make_team(list(ours), w["merged"], w["natures"]),
                  make_team(list(theirs), w["merged"], w["natures"]),
                  w["typechart"], w["moves"])


def key(name):
    return name.lower().replace(" ", "").replace("-", "").replace("'", "")


def hit(move_name, def_stage):
    """Damage `move_name` deals to a target sitting at `def_stage`."""
    from damage import defensive_stat
    b = fresh()
    attacker, target = b.p1.active[0], b.p2.active[0]
    target.stages["def"] = def_stage
    move = b.make_move(key(move_name))
    return defensive_stat(target, "def", move)


class TestTheFlagIsRead(unittest.TestCase):

    def test_the_ignoring_moves_carry_it(self):
        b = fresh()
        for name in IGNORERS:
            self.assertTrue(b.make_move(key(name)).ignore_defensive, name)

    def test_an_ordinary_move_does_not(self):
        b = fresh()
        for name in ("Close Combat", "Sacred Fire", "Body Press"):
            self.assertFalse(b.make_move(key(name)).ignore_defensive, name)


class TestTheDefenceItHits(unittest.TestCase):

    def test_a_defence_boost_does_not_blunt_it(self):
        self.assertEqual(hit("Sacred Sword", +2), hit("Sacred Sword", 0))

    def test_a_defence_DROP_does_not_help_it_either(self):
        """The half that is easy to get wrong: Showdown zeroes the stage, it
        does not take the better of the two."""
        self.assertEqual(hit("Sacred Sword", -2), hit("Sacred Sword", 0))

    def test_an_ordinary_move_is_still_blunted(self):
        """The control. Without this the test would pass on a helper that
        ignored every move's stages."""
        self.assertGreater(hit("Close Combat", +2), hit("Close Combat", 0))

    def test_all_three_ignorers_behave_the_same(self):
        for name in IGNORERS:
            self.assertEqual(hit(name, +2), hit(name, 0), name)


class TestItReachesRealDamage(unittest.TestCase):
    """Verify by running: the helper agreeing with itself proves nothing if the
    battle never calls it."""

    @staticmethod
    def damage_after_boost(move_name, def_stage):
        w = world()
        b = fresh()
        attacker, target = b.p1.active[0], b.p2.active[0]
        target.stages["def"] = def_stage
        b.force_roll_index = 0            # pin the roll: this is a comparison
        before = target.current_hp
        from battle import Action
        b.run_turn([Action(attacker, "p1", "move",
                           b.make_move(key(move_name)), [target])], [])
        return before - target.current_hp

    def test_sacred_sword_deals_the_same_damage_through_iron_defence(self):
        plain = self.damage_after_boost("Sacred Sword", 0)
        boosted = self.damage_after_boost("Sacred Sword", +2)
        self.assertGreater(plain, 0)
        self.assertEqual(plain, boosted)

    def test_a_normal_fighting_move_is_reduced_by_the_same_boost(self):
        plain = self.damage_after_boost("Close Combat", 0)
        boosted = self.damage_after_boost("Close Combat", +2)
        self.assertGreater(plain, boosted)


class TestTheSolverSeesIt(unittest.TestCase):
    """The move-choice path builds its own MoveInfo. If the flag stops here the
    simulator is right and the pilot still picks the wrong move."""

    def test_build_moveset_carries_the_flag(self):
        from solver import build_moveset
        w = world()
        moves = build_moveset(w["merged"]["Gallade"], w["moves"], top_k=30)
        by_name = {m.name: m for m, _ in moves}
        self.assertIn("Sacred Sword", by_name)
        self.assertTrue(by_name["Sacred Sword"].ignore_defensive)
        # ...and the rest of Gallade's real moveset is untouched.
        others = [m for n, m in by_name.items() if n not in IGNORERS]
        self.assertTrue(others)
        self.assertFalse(any(m.ignore_defensive for m in others),
                         [m.name for m in others if m.ignore_defensive])


if __name__ == "__main__":
    unittest.main()
