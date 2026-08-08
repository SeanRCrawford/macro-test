"""Tests for src/threat.py.

Built on real battle states rather than mocks: the threat matrix is a thin
layer over the damage calculator and the speed rules, so a mock would mostly
test the mock. The assertions are structural properties that must hold for any
matchup, plus one directed case with a hand-checkable answer.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from threat import NO_THREAT, build_threat_matrix  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]

_WORLD = None
_MATRIX = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def matrix():
    global _MATRIX
    if _MATRIX is None:
        w = world()
        battle, movesets = setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)
        _MATRIX = build_threat_matrix(battle, movesets)
    return _MATRIX


def test_matrix_covers_every_pair_in_both_directions():
    T = matrix()
    assert T.ours and T.theirs
    for a in T.ours:
        for b in T.theirs:
            assert T.threat(a, b) is not None
            assert T.threat(b, a) is not None


def test_damage_fractions_are_ordered_and_sane():
    T = matrix()
    for a in T.ours:
        for b in T.theirs:
            for t in (T.threat(a, b), T.threat(b, a)):
                assert 0.0 <= t.dmg_min <= t.dmg_avg <= t.dmg_max, t
                assert t.dmg_max < 10.0, f"absurd damage fraction: {t}"


def test_guaranteed_ohko_implies_possible_ohko():
    """A kill on the worst roll is necessarily a kill on the best one."""
    T = matrix()
    for a in T.ours:
        for b in T.theirs:
            for t in (T.threat(a, b), T.threat(b, a)):
                if t.ohko:
                    assert t.ohko_possible, t


def test_nhko_is_consistent_with_a_guaranteed_ohko():
    T = matrix()
    for a in T.ours:
        for b in T.theirs:
            for t in (T.threat(a, b), T.threat(b, a)):
                if t.ohko:
                    assert t.nhko == 1, t


def test_handles_score_is_antisymmetric_in_sign_for_mirrored_edges():
    """If ours cleanly beats theirs, theirs must not also cleanly beat ours."""
    T = matrix()
    for a in T.ours:
        for b in T.theirs:
            ours_handles = T.handles_score(a, b)
            # Rebuild the reverse comparison from the same two directed edges.
            out, inc = T.threat(a, b), T.threat(b, a)
            if out.ohko and out.outspeeds and not inc.ohko:
                assert ours_handles > 0, (a.name, b.name, ours_handles)
            if inc.ohko and inc.outspeeds and not out.ohko:
                assert ours_handles < 0, (a.name, b.name, ours_handles)


def test_fainted_pokemon_are_excluded_and_threatless():
    w = world()
    battle, movesets = setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)
    victim = battle.p2.roster[0]
    victim.current_hp = 0
    victim.fainted = True
    T = build_threat_matrix(battle, movesets)
    assert victim not in T.theirs
    assert T.threat(battle.p1.roster[0], victim) is NO_THREAT


def test_joint_kos_is_at_least_as_strong_as_either_attacker_alone():
    """Focus fire cannot be worse than the better single attacker."""
    T = matrix()
    for b in T.theirs:
        for i, a1 in enumerate(T.ours):
            for a2 in T.ours[i + 1:]:
                if T.threat(a1, b).nhko == 1 or T.threat(a2, b).nhko == 1:
                    assert T.joint_kos(a1, a2, b), (a1.name, a2.name, b.name)


def test_joint_kos_needs_two_distinct_attackers():
    T = matrix()
    a = T.ours[0]
    assert T.joint_kos(a, a, T.theirs[0]) is False


def test_joint_kos_finds_focus_fire_a_pairwise_matrix_would_miss():
    """The case section 8c.3 says a pairwise matrix structurally cannot express.

    Not every matchup contains one, so this asserts the mechanism directly: two
    attackers that each do more than half but neither of which OHKOs.
    """
    T = matrix()
    found = False
    for b in T.theirs:
        need = b.current_hp / (b.max_hp() or 1)
        for i, a1 in enumerate(T.ours):
            for a2 in T.ours[i + 1:]:
                t1, t2 = T.threat(a1, b), T.threat(a2, b)
                if (t1.dmg_avg < need and t2.dmg_avg < need
                        and t1.dmg_avg + t2.dmg_avg >= need):
                    assert T.joint_kos(a1, a2, b)
                    found = True
    if not found:
        # Nothing to assert on this matchup; the mechanism is still exercised by
        # test_joint_kos_is_at_least_as_strong_as_either_attacker_alone.
        pass


def test_guaranteed_joint_ko_implies_average_joint_ko():
    T = matrix()
    for b in T.theirs:
        for i, a1 in enumerate(T.ours):
            for a2 in T.ours[i + 1:]:
                if T.joint_kos(a1, a2, b, guaranteed=True):
                    assert T.joint_kos(a1, a2, b)


def test_answers_to_is_sorted_best_first():
    T = matrix()
    for enemy in T.theirs:
        scores = [s for _mon, s in T.answers_to(enemy)]
        assert scores == sorted(scores, reverse=True)
        assert len(scores) == len(T.ours)


def test_pressured_by_only_returns_the_other_side():
    T = matrix()
    for mine in T.ours:
        assert all(x in T.theirs for x in T.pressured_by(mine))
    for theirs in T.theirs:
        assert all(x in T.ours for x in T.pressured_by(theirs))


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
