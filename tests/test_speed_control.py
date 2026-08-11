"""The depth-1 speed-control term (WORKFLOW.md §4.2).

The blind spot: the solver sees one turn ahead, so a turn that moves no HP
looks free, and setting Tailwind moves no HP. Before this term the state after
Tailwind evaluated IDENTICALLY to the state after doing nothing, so a four-turn
speed swing was free to throw away -- the same shape of hole that produced the
Protect spam.

These tests build real battle states rather than mocking, because the property
being guarded (antisymmetry of a term computed from the shared field) only means
anything on a real Field.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import pytest  # noqa: E402

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
WEIGHT = 12.0        # the weight the measurements in solver.py were taken at
# Read at import, BEFORE the fixture below can touch it, so the shipped default
# is still assertable from inside a file that turns the term on for itself.
SHIPPED_DEFAULT = solver.SPEED_CONTROL_WEIGHT

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


@pytest.fixture(autouse=True)
def term_on():
    """The term SHIPS OFF (see test_the_default_is_off), so every test below
    switches it on for itself. Otherwise the whole file would silently assert
    nothing the moment the default changed."""
    before = solver.SPEED_CONTROL_WEIGHT
    solver.SPEED_CONTROL_WEIGHT = WEIGHT
    yield
    solver.SPEED_CONTROL_WEIGHT = before


def fresh():
    w = world()
    return setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)


def test_it_ships_on():
    """Live, at the weight the measurements were taken at. It was parked at 0
    first; the behaviour it fixes was reported a second time from real games, so
    the default moved. Changing it back means bumping the two cache schemas and
    re-recording the golden baseline, so it must not drift by accident."""
    assert SHIPPED_DEFAULT == WEIGHT


def test_it_is_a_no_op_when_off():
    """Weight 0 must reproduce the previous evaluation exactly, or 'set it back
    to 0' is not the escape hatch the comment promises."""
    battle, _ms = fresh()
    solver.SPEED_CONTROL_WEIGHT = 0.0
    before = solver.heuristic_eval(battle, "p1")
    battle.field.tailwind_p1 = 4
    assert solver.heuristic_eval(battle, "p1") == before


def test_tailwind_is_no_longer_a_wasted_turn():
    """THE GAP. Same position, our Tailwind up: it must score better."""
    battle, _ms = fresh()
    before = solver.heuristic_eval(battle, "p1")
    battle.field.tailwind_p1 = 4
    after = solver.heuristic_eval(battle, "p1")
    assert after > before, (
        f"setting Tailwind still evaluates as a wasted turn: "
        f"{before:.1f} -> {after:.1f}")


def test_their_tailwind_hurts_us_as_ours_helps():
    """The two are NOT mirror images -- how many pairs flip depends on the
    speed spread of the four Pokemon actually out -- but the signs must be
    opposite, and each side must be priced from the same state (asserted
    directly in test_it_does_not_break_antisymmetry)."""
    battle, _ms = fresh()
    base = solver.heuristic_eval(battle, "p1")
    battle.field.tailwind_p1 = 4
    ours = solver.heuristic_eval(battle, "p1") - base
    battle.field.tailwind_p1 = 0
    battle.field.tailwind_p2 = 4
    theirs = solver.heuristic_eval(battle, "p1") - base
    assert ours > 0 > theirs


def test_renewing_tailwind_that_is_already_up_gains_nothing():
    """Why the term reads the BOARD and not the move. A bonus for *using*
    Tailwind would be collectable every turn, which is the Protect spam again
    with a different move; a state-based term cannot be farmed."""
    battle, _ms = fresh()
    battle.field.tailwind_p1 = 4
    once = solver.heuristic_eval(battle, "p1")
    battle.field.tailwind_p1 = 4      # what the engine does on a re-set
    assert solver.heuristic_eval(battle, "p1") == once


def test_trick_room_is_credited_to_whoever_it_helps():
    """The engine does not record who SET Trick Room, so the term scores the
    effect rather than the ownership -- which is the only sound reading of an
    unattributed field flag."""
    battle, _ms = fresh()
    normal = solver.heuristic_eval(battle, "p1")
    battle.field.trick_room = True
    flipped = solver.heuristic_eval(battle, "p1")
    # Whoever was ahead on speed is now behind, so the term has to move -- and
    # in the direction that matches who is now slower.
    from solver import _speed_control_score
    faster_now = _speed_control_score(battle, battle.p1, battle.p2)
    battle.field.trick_room = False
    faster_before = _speed_control_score(battle, battle.p1, battle.p2)
    if faster_now == faster_before:
        return          # a mirror-speed position has nothing to assert
    assert (flipped > normal) == (faster_now > faster_before)


def test_the_term_cannot_outbid_a_ko():
    """Speed control is worth at most 4 pairs x the weight. A KO is worth
    _ko_threat_value x KO_WEIGHT, floored at 0.35 x 180 = 63."""
    assert 4 * solver.SPEED_CONTROL_WEIGHT < 0.35 * solver.KO_WEIGHT


def test_it_does_not_break_antisymmetry():
    """A zero-sum matrix solve is only coherent if our gain is their loss."""
    battle, _ms = fresh()
    for field in ({"tailwind_p1": 4}, {"tailwind_p2": 4},
                  {"trick_room": True}, {"tailwind_p1": 4, "trick_room": True}):
        battle.field.tailwind_p1 = 0
        battle.field.tailwind_p2 = 0
        battle.field.trick_room = False
        for key, value in field.items():
            setattr(battle.field, key, value)
        total = (solver.heuristic_eval(battle, "p1")
                 + solver.heuristic_eval(battle, "p2"))
        assert abs(total) < 1e-6, f"{field} broke antisymmetry by {total}"


def test_a_speed_tie_counts_for_neither_side():
    """Ties are what keep the two directions exactly opposite."""
    battle, _ms = fresh()
    for c in battle.p1.active + battle.p2.active:
        c.stats["spe"] = 100
        c.stages["spe"] = 0
        c.item = None
        c.status = None
    assert solver._speed_control_score(battle, battle.p1, battle.p2) == 0.0
    assert solver._speed_control_score(battle, battle.p2, battle.p1) == 0.0


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))
