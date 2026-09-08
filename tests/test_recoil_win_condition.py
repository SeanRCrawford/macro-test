"""A hit that KOs the opponent's last Pokemon wins the battle, even if the
move that did it (a recoil move, Life Orb, ...) then faints the ATTACKER
too, the same turn, via its own self-damage.

    "I have Scizor here, so it should have been a loss for me (these are
     the final two) given I was KOd before the enemy fainted to recoil."

Real rule, not an invention: the game ends the instant a side's last
Pokemon faints -- self-inflicted recoil/Life-Orb damage that happens to
also faint the attacker a moment later cannot undo a win already earned.
`Battle.winner()` used to be a pure end-state snapshot (`has_lost()` on
both sides), so a same-turn double-faint like this always fell through to
"draw" regardless of which side actually ran out of Pokemon first.
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
from solver import build_moveset  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


class TestRecoilCanDecideTheWinner(unittest.TestCase):
    """Mega Scizor (p1) vs Incineroar (p2), both down to their last Pokemon.
    Incineroar's Flare Blitz KOs Scizor outright (p1 has none left), THEN
    its own 33% recoil also drops Incineroar to 0 -- p2 must still be
    credited the win, not a draw."""

    def setUp(self):
        self.W = world()
        merged, moves, natures, typechart = (
            self.W["merged"], self.W["moves"], self.W["natures"], self.W["typechart"])
        oc = make_team(["Mega Scizor", "Kingambit"], merged, natures)
        ec = make_team(["Incineroar", "Basculegion"], merged, natures)
        self.battle = Battle(oc, ec, typechart, moves)
        self.battle.force_roll_index = 15  # deterministic max roll

        self.scizor, kingambit = self.battle.p1.active
        self.incineroar, basculegion = self.battle.p2.active
        kingambit.fainted = True
        kingambit.current_hp = 0
        basculegion.fainted = True
        basculegion.current_hp = 0
        self.incineroar.item = ""  # no berry to complicate the exact HP math

        self.bullet_punch = build_moveset(
            merged["Mega Scizor"], moves, only_moves=["Bullet Punch"])[0][0]
        self.flare_blitz = build_moveset(
            merged["Incineroar"], moves, only_moves=["Flare Blitz"])[0][0]

    def _run(self, scizor_hp, incineroar_hp):
        self.scizor.current_hp = scizor_hp
        self.incineroar.current_hp = incineroar_hp
        p1 = [Action(self.scizor, "p1", "move", self.bullet_punch, [self.incineroar])]
        p2 = [Action(self.incineroar, "p2", "move", self.flare_blitz, [self.scizor])]
        self.battle.run_turn(p1, p2)

    def test_attackers_own_recoil_faint_does_not_turn_a_win_into_a_draw(self):
        # Scizor at 10 HP: Flare Blitz (279 dmg, 4x) KOs it outright. Incineroar
        # at 27 HP: survives Bullet Punch's 26 dmg (down to 1), then its OWN
        # Flare Blitz recoil (3 dmg) also drops it to 0 -- a genuine same-turn
        # double faint.
        self._run(scizor_hp=10, incineroar_hp=27)
        self.assertTrue(self.scizor.fainted)
        self.assertTrue(self.incineroar.fainted)
        self.assertEqual(self.battle.winner(), "p2",
                          "Incineroar's side KO'd the last of ours before its "
                          "own recoil finished it -- that is a loss for p1, "
                          "not a draw.")
        self.assertTrue(self.battle.is_over())

    def test_a_genuine_simultaneous_double_faint_still_reports_a_draw(self):
        """If BOTH sides are already fully spent with no locked-in decision
        (nothing to say which side ran out first), `winner()` still falls
        back to the original snapshot-based draw -- exactly as before this
        fix, `_maybe_lock_winner` just never having had a chance to fire."""
        self.scizor.fainted = True
        self.scizor.current_hp = 0
        self.incineroar.fainted = True
        self.incineroar.current_hp = 0
        self.assertEqual(self.battle.winner(), "draw")

    def test_does_not_fire_when_the_attacker_survives_its_own_recoil(self):
        """Sanity check on the fixture itself: with more cushion, Incineroar
        survives its own recoil and the normal winner-by-attrition path is
        unaffected."""
        self._run(scizor_hp=10, incineroar_hp=200)
        self.assertTrue(self.scizor.fainted)
        self.assertFalse(self.incineroar.fainted)
        self.assertEqual(self.battle.winner(), "p2")

    def test_decided_winner_survives_a_deepcopy(self):
        """`Battle.__deepcopy__` is hand-written and must know about
        `_decided_winner` explicitly -- the solver deep-copies a Battle for
        every candidate action it explores, and losing this attribute on
        copy would silently let a copy's own later self-damage re-open a
        battle that already ended."""
        import copy
        self._run(scizor_hp=10, incineroar_hp=27)
        self.assertEqual(self.battle.winner(), "p2")
        clone = copy.deepcopy(self.battle)
        self.assertEqual(clone.winner(), "p2")


if __name__ == "__main__":
    unittest.main()
