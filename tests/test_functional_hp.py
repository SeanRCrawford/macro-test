"""Tests for the functional-value HP shaping (design doc 4d).

The properties that must hold at EVERY floor setting, not just the default:
antisymmetry, no reveal incentive, monotonicity in HP, and a strict preference
for being alive.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from solver import _functional_hp  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
FLOORS = (0.0, 0.25, 0.5, 0.75, 1.0)

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh():
    w = world()
    return setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)


class floor:
    """Context manager: run a block at a given FUNCTIONAL_FLOOR."""

    def __init__(self, value):
        self.value = value

    def __enter__(self):
        self.original = solver.FUNCTIONAL_FLOOR
        solver.FUNCTIONAL_FLOOR = self.value

    def __exit__(self, *exc):
        solver.FUNCTIONAL_FLOOR = self.original


def make(hp_frac):
    battle, _ = fresh()
    c = battle.p1.roster[0]
    c.current_hp = max(1, int(round(c.max_hp() * hp_frac)))
    return c


def test_floor_zero_reproduces_the_linear_term():
    with floor(0.0):
        for frac in (0.1, 0.5, 1.0):
            c = make(frac)
            assert abs(_functional_hp(c) - c.current_hp_frac) < 1e-9


def test_floor_one_ignores_hp_entirely():
    with floor(1.0):
        for frac in (0.02, 0.5, 1.0):
            assert abs(_functional_hp(make(frac)) - 1.0) < 1e-9


def test_one_hp_is_worth_at_least_the_floor():
    """The article's actual claim, in the form the evaluation can express."""
    for f in FLOORS:
        with floor(f):
            barely = make(0.01)
            barely.current_hp = 1
            assert _functional_hp(barely) >= f - 1e-9


def test_alive_always_beats_fainted():
    for f in FLOORS:
        with floor(f):
            alive = make(0.01)
            alive.current_hp = 1
            dead = copy.copy(alive)
            dead.current_hp, dead.fainted = 0, True
            assert _functional_hp(alive) > _functional_hp(dead)


def test_monotonic_in_hp_so_chip_damage_still_costs():
    """A pure step would make chip damage free, which is clearly wrong."""
    for f in FLOORS[:-1]:  # 1.0 is deliberately flat
        with floor(f):
            values = [_functional_hp(make(x)) for x in (0.1, 0.4, 0.7, 1.0)]
            assert values == sorted(values), (f, values)
            assert values[0] < values[-1], (f, values)


def test_evaluation_stays_antisymmetric_at_every_floor():
    battle, _ = fresh()
    for f in FLOORS:
        with floor(f):
            total = (solver.heuristic_eval(battle, "p1")
                     + solver.heuristic_eval(battle, "p2"))
            assert abs(total) < 1e-6, (f, total)


def test_no_reveal_incentive_at_any_floor():
    """A hidden Pokemon is at full HP, so it must score 1.0 at every floor and
    revealing it must not move the evaluation."""
    battle, _ = fresh()
    hidden = [c for c in battle.p1.roster
              if c not in battle.p1.active and not getattr(c, "revealed", False)]
    assert hidden, "expected benched, unrevealed Pokemon"
    for f in FLOORS:
        with floor(f):
            before = solver.heuristic_eval(battle, "p1")
            hidden[0].revealed = True
            after = solver.heuristic_eval(battle, "p1")
            hidden[0].revealed = False
            assert abs(after - before) < 1e-9, (f, after - before)


def test_default_is_the_pre_a4_behaviour():
    """Shipping a change to this must be a deliberate, measured decision."""
    assert solver.FUNCTIONAL_FLOOR == 0.0, (
        "FUNCTIONAL_FLOOR changed -- re-run tools/measure_headtohead.py first")


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
