"""A single-target move may aim at the other Pokemon, if that removes it.

Reported with a turn log:

    "a scizor bullet punch ... would have outsped and KO'd the Mega Floette ...
     and even ignoring the target selection, bug bite after whimsicott has
     already moved when bullet punch would go first makes no sense"

`candidate_actions` pruned every single-target move to ONE target -- whichever
took the most damage -- with the comment "prune to 1". Measured on that
position, Mega Scizor's Bullet Punch does

    206-242 to Mega Floette   (2x, guaranteed OHKO)
    134-158 to Whimsicott     (2x, OHKO on most rolls, 137 HP)

so only the Floette version was ever offered, and "remove the faster attacker
before it moves with a +1 priority move" was not an option the search could
see. That is not a subtle line; it is the first thing a player checks.

A KO is the right second criterion, rather than "the damage is close". What
makes the other target worth hitting is that the move FINISHES it -- a
different kind of value from chip, and precisely the thing raw damage cannot
express. It costs at most one extra action per move and only where a KO is
actually on: measured, 39 joint actions against 33 on this position, and no
change at all on two others.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world, setup_battle  # noqa: E402
from solver import our_candidate_joint_actions  # noqa: E402

_WORLD = None
OURS = ["Mega Scizor", "Ninetales-Alola", "Mega Charizard Y", "Garchomp"]
THEIRS = ["Whimsicott", "Mega Floette", "Garchomp", "Archaludon"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def scizor_actions():
    battle, movesets = setup_battle(list(OURS), list(THEIRS), world())
    joints = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                         movesets, 1)
    return {f"{a.move.name}->{a.targets[0].name}"
            for joint in joints for a in joint
            if a.combatant.name == "Mega Scizor" and a.kind == "move"
            and a.targets}


class TestTheReportedPosition(unittest.TestCase):

    def setUp(self):
        self.actions = scizor_actions()

    def test_the_hardest_hit_is_still_offered(self):
        self.assertIn("Bullet Punch->Mega Floette", self.actions)

    def test_the_other_target_is_offered_when_the_move_kills_it(self):
        """The option that was missing: a +1 priority KO on the faster
        attacker, which raw damage ranked second and so hid entirely."""
        self.assertIn("Bullet Punch->Whimsicott", self.actions)

    def test_a_move_that_kills_neither_is_still_pruned_to_one(self):
        """The prune is not simply removed -- Bug Bite kills nobody here, so it
        keeps exactly one target and the action space does not double."""
        bug = [a for a in self.actions if a.startswith("Bug Bite")]
        self.assertEqual(len(bug), 1, bug)


class TestItDoesNotBlowUpTheActionSpace(unittest.TestCase):

    def test_the_extra_action_only_appears_where_a_ko_is_on(self):
        quiet = setup_battle(
            ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"],
            ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"], world())
        battle, movesets = quiet
        joints = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                             movesets, 1)
        # Measured at 33 before the change and 33 after: nothing on this board
        # is one hit from gone at full HP.
        self.assertLessEqual(len(joints), 40)

    def test_the_reported_position_grows_only_modestly(self):
        battle, movesets = setup_battle(list(OURS), list(THEIRS), world())
        joints = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                             movesets, 1)
        self.assertLessEqual(len(joints), 60, len(joints))


class TestTheSolverNowPrefersThePriorityKO(unittest.TestCase):
    """The end of the report: Bug Bite should not be played here at all."""

    @classmethod
    def setUpClass(cls):
        import solver as _solver
        from turn_game import solve_turn
        battle, movesets = setup_battle(list(OURS), list(THEIRS), world())
        with _solver.evaluation("sacrifice"):
            cls.solution = solve_turn(battle, "p1", movesets, depth=1)

    def played(self):
        out = []
        for action, weight in self.solution.support():
            for a in action:
                if a.combatant.name == "Mega Scizor" and a.kind == "move":
                    out.append((a.move.name, weight))
        return out

    def test_bullet_punch_is_what_it_plays(self):
        names = {name for name, _w in self.played()}
        self.assertIn("Bullet Punch", names)

    def test_bug_bite_carries_no_weight(self):
        bug = sum(w for name, w in self.played() if name == "Bug Bite")
        self.assertLess(bug, 0.01, f"Bug Bite still played {bug:.1%} of the time")


if __name__ == "__main__":
    unittest.main()
