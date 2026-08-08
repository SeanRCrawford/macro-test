"""A scripted opponent is a PRIOR on their columns, not a separate algorithm.

Design doc section 3d. This is what lets the matrix solve subsume the old
CHECK_TOP_K deviation check: off-script columns keep their equilibrium weight,
so a play that loses badly to a deviation is still priced for it, without an
arbitrary top-K cap.

The failure mode being guarded against is silent. `_script_column` swallows
exceptions and returns None, so a script called with the wrong signature simply
disables the prior and everything still "works" -- just with the script
knowledge quietly discarded.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from turn_game import _script_column, solve_turn  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh():
    w = world()
    return setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)


def script_returning(index):
    """A script that always plays `their_actions[index]`."""
    holder = {}

    def script(battle, side, opp_side, turn):
        return holder.get("actions")

    script.holder = holder
    script.index = index
    return script


def test_script_column_is_found_with_the_real_calling_convention():
    """Regression: the convention is (battle, side, opp_side, turn). Calling it
    with just (battle) raises, is swallowed, and silently disables the prior."""
    battle, movesets = fresh()
    base = solve_turn(battle, "p1", movesets)
    target = 2 % len(base.their_actions)

    script = script_returning(target)
    script.holder["actions"] = base.their_actions[target]

    found = _script_column(battle, "p1", movesets, base.their_actions, script)
    assert found == target, (
        f"script column not located (got {found!r}); the prior would be "
        "silently disabled")


def test_a_script_shifts_our_choice_toward_beating_it():
    battle, movesets = fresh()
    base = solve_turn(battle, "p1", movesets)
    assert base.their_actions

    target = 2 % len(base.their_actions)
    script = script_returning(target)
    script.holder["actions"] = base.their_actions[target]

    scripted = solve_turn(battle, "p1", movesets, enemy_script=script,
                          script_confidence=1.0)
    # With full confidence their strategy must be the scripted column.
    assert scripted.q[target] > 0.99, scripted.q[target]
    # And we must commit to a single response to it.
    assert len(scripted.support()) == 1


def test_off_script_columns_keep_weight_at_partial_confidence():
    """Deviation safety: the whole point of a prior rather than an assumption."""
    battle, movesets = fresh()
    base = solve_turn(battle, "p1", movesets)
    target = 1 % len(base.their_actions)
    script = script_returning(target)
    script.holder["actions"] = base.their_actions[target]

    sol = solve_turn(battle, "p1", movesets, enemy_script=script,
                     script_confidence=0.7)
    off_script = sum(x for j, x in enumerate(sol.q) if j != target)
    assert off_script > 0.05, (
        f"off-script columns carry only {off_script:.3f} weight -- the solver is "
        "assuming the script rather than treating it as a prior")


def test_no_script_leaves_the_equilibrium_untouched():
    battle, movesets = fresh()
    plain = solve_turn(battle, "p1", movesets)
    with_none = solve_turn(battle, "p1", movesets, enemy_script=None)
    assert abs(plain.value - with_none.value) < 1e-9


def test_a_broken_script_degrades_to_the_plain_equilibrium():
    def broken(battle, side, opp_side, turn):
        raise RuntimeError("boom")

    battle, movesets = fresh()
    plain = solve_turn(battle, "p1", movesets)
    sol = solve_turn(battle, "p1", movesets, enemy_script=broken)
    assert abs(sol.value - plain.value) < 1e-9


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
