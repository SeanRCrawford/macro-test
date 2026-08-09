"""Both slots protecting passes the turn -- except when stalling speed control.

Double Protect gains no damage, no position and no progress, and hands the
opponent a free turn to switch or set up. It survives a depth-1 maximin search
anyway, because Protect's worst case is the best worst case when the evaluation
cannot see that nothing was gained.

It is also what made the punish reporting meaningless: when we protect with
both slots, every attack the opponent makes leads to the same state, so their
replies tie en masse and the "best answer" is whichever tied first. Measured on
one line before and after this filter, ties at the minimum went

    [1, 4, 2, 30, 1, 46]  ->  [1, 1, 1, 1, 1, 19]

The exceptions are the reason this is a targeted filter and not a ban. Double
Protect is a real option when the opponent's Tailwind or a Trick Room is
running (stalling the turns out is what Protect is for) and on a turn they
could Fake Out.

In those positions the combo stays a CANDIDATE and nothing more. That
distinction is the point: against Fake Out it is often still wrong, because
Fake Out does almost no damage and eating it while switching or attacking can
beat handing them a free turn to set up or improve their board. The filter
keeps the option available; the search prices it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import solver  # noqa: E402
from engine import Action, FieldState  # noqa: E402


class _Mon:
    def __init__(self, name, active_turn_count=1):
        self.name = name
        self.fainted = False
        self.active_turn_count = active_turn_count   # 0 => Fake Out is legal


class _Side:
    def __init__(self, name, active=()):
        self.name = name
        self.active = list(active)


class _Move:
    def __init__(self, name):
        self.name = name


class _Battle:
    def __init__(self, p1_active=(), p2_active=(), movesets=None, **field):
        self.field = FieldState(**field)
        self.p1 = _Side("p1", p1_active)
        self.p2 = _Side("p2", p2_active)
        self.movesets = movesets or {}


def _protect(mon, side):
    return Action(mon, side, "protect", None, [mon])


def _move(mon, side):
    return Action(mon, side, "move", None, [])


def _switch(mon, side, target):
    return Action(mon, side, "switch", None, [target])


A, B, C = _Mon("A"), _Mon("B"), _Mon("C")
P1 = _Side("p1")


class TestDoubleProtectFilter(unittest.TestCase):

    def test_both_slots_protecting_is_filtered(self):
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertTrue(
            solver._is_pointless_double_protect(combo, _Battle(), P1))

    def test_one_protect_plus_an_attack_is_a_real_play(self):
        combo = [_protect(A, "p1"), _move(B, "p1")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, _Battle(), P1))

    def test_protect_alongside_a_switch_is_a_real_play(self):
        """Protect one slot, pivot the other -- a standard doubles line."""
        combo = [_protect(A, "p1"), _switch(B, "p1", C)]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, _Battle(), P1))

    def test_allowed_while_their_tailwind_is_ticking(self):
        battle = _Battle(tailwind_p2=3)
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_our_own_tailwind_is_not_a_reason_to_stall(self):
        """Stalling out our OWN speed control wastes the advantage."""
        battle = _Battle(tailwind_p1=3)
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertTrue(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_tailwind_attribution_is_side_relative(self):
        """p2 stalls p1's Tailwind, and vice versa."""
        battle = _Battle(tailwind_p1=3)
        combo = [_protect(A, "p2"), _protect(B, "p2")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, battle, _Side("p2")))

    def test_allowed_under_trick_room(self):
        battle = _Battle(trick_room=True, trick_room_turns_left=3)
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_allowed_on_a_turn_they_could_fake_out(self):
        """Fake Out is legal only on the turn a Pokemon is sent out."""
        incoming = _Mon("Incineroar", active_turn_count=0)
        battle = _Battle(p2_active=[incoming],
                         movesets={"Incineroar": [_Move("Fake Out")]})
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_not_allowed_once_their_fake_out_window_has_passed(self):
        settled = _Mon("Incineroar", active_turn_count=2)
        battle = _Battle(p2_active=[settled],
                         movesets={"Incineroar": [_Move("Fake Out")]})
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertTrue(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_a_freshly_switched_mon_without_fake_out_is_no_reason(self):
        incoming = _Mon("Garchomp", active_turn_count=0)
        battle = _Battle(p2_active=[incoming],
                         movesets={"Garchomp": [_Move("Earthquake")]})
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertTrue(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_the_wider_move_space_is_what_is_checked(self):
        """A Fake Out we did not plan for is exactly the case that matters."""
        incoming = _Mon("Incineroar", active_turn_count=0)
        battle = _Battle(p2_active=[incoming],
                         movesets={"Incineroar": [_Move("Flare Blitz")]})
        battle.wide_movesets = {"Incineroar": [_Move("Flare Blitz"),
                                               _Move("Fake Out")]}
        combo = [_protect(A, "p1"), _protect(B, "p1")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, battle, P1))

    def test_the_exception_only_permits_it_never_forces_it(self):
        """Fake Out does almost no damage, so eating it can beat protecting.

        The filter's job is to keep the option available, not to pick it -- the
        search prices it against switching and attacking like anything else.
        """
        incoming = _Mon("Incineroar", active_turn_count=0)
        battle = _Battle(p2_active=[incoming],
                         movesets={"Incineroar": [_Move("Fake Out")]})
        for combo in ([_protect(A, "p1"), _move(B, "p1")],
                      [_switch(A, "p1", C), _move(B, "p1")],
                      [_move(A, "p1"), _move(B, "p1")]):
            self.assertFalse(
                solver._is_pointless_double_protect(combo, battle, P1))

    def test_a_single_active_slot_is_never_a_double_protect(self):
        combo = [_protect(A, "p1")]
        self.assertFalse(
            solver._is_pointless_double_protect(combo, _Battle(), P1))

    def test_the_filter_can_be_switched_off_for_measurement(self):
        """A behaviour change this broad has to stay measurable."""
        self.assertFalse(solver.ALLOW_DOUBLE_PROTECT,
                         "the filter should be ON by default")
        original = solver.ALLOW_DOUBLE_PROTECT
        solver.ALLOW_DOUBLE_PROTECT = True
        try:
            self.assertTrue(solver.ALLOW_DOUBLE_PROTECT)
        finally:
            solver.ALLOW_DOUBLE_PROTECT = original


if __name__ == "__main__":
    unittest.main()
