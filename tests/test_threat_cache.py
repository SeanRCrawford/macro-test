"""Tests that the threat-matrix damage cache cannot go silently stale.

A wrong cache key does not crash -- it returns plausible, stale numbers, which
is the worst failure mode available here. Each test below mutates one field the
key claims to depend on and asserts the answer actually moves.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import threat  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402
from threat import build_threat_matrix  # noqa: E402

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


def damage_after(mutate):
    """Best-move damage from our first mon into their first, after `mutate`."""
    battle, movesets = fresh()
    mutate(battle)
    T = build_threat_matrix(battle, movesets)
    return T.threat(T.ours[0], T.theirs[0]).dmg_avg


def assert_sensitive_to(mutate, label):
    base = damage_after(lambda _b: None)
    changed = damage_after(mutate)
    assert base != changed, (
        f"cache is stale: changing {label} did not change the damage "
        f"({base:.4f} both times)")


def test_sensitive_to_attack_stage():
    assert_sensitive_to(lambda b: b.p1.roster[0].stages.update(atk=-2), "attack stage")


def test_sensitive_to_defence_stage():
    assert_sensitive_to(lambda b: b.p2.roster[0].stages.update(**{"def": 4}),
                        "defence stage")


def test_sensitive_to_weather():
    assert_sensitive_to(lambda b: setattr(b.field, "weather", "rain"), "weather")


def test_sensitive_to_burn():
    assert_sensitive_to(lambda b: setattr(b.p1.roster[0], "status", "brn"), "burn")


def test_sensitive_to_attacker_item():
    assert_sensitive_to(lambda b: setattr(b.p1.roster[0], "item", "Life Orb"),
                        "attacker item")


def test_sensitive_to_screens():
    """Regression: threat.py used to ignore screens entirely, so it
    over-estimated damage into a screened side."""
    def put_up_reflect(b):
        b.p2.screens_reflect = 5
        b.p2.screens_lightscreen = 5
    assert_sensitive_to(put_up_reflect, "screens")


def test_sensitive_to_multiscale_full_hp_flag():
    """Multiscale halves damage only at full HP, so damage is NOT independent of
    current HP -- the easiest dependency to leave out of a cache key."""
    battle, movesets = fresh()
    target = battle.p2.roster[0]
    target.ability = "Multiscale"

    T_full = build_threat_matrix(battle, movesets)
    full = T_full.threat(T_full.ours[0], target).dmg_avg

    chipped = copy.deepcopy(battle)
    chip_target = chipped.p2.roster[0]
    chip_target.ability = "Multiscale"
    chip_target.current_hp = chip_target.max_hp() - 1
    T_chip = build_threat_matrix(chipped, movesets)
    hurt = T_chip.threat(T_chip.ours[0], chip_target).dmg_avg

    assert hurt > full, (
        f"Multiscale ignored by the cache: {full:.4f} at full HP vs "
        f"{hurt:.4f} at 1 below")


def test_cache_actually_hits():
    """If it never hits, it is pure overhead."""
    threat.clear_cache()
    battle, movesets = fresh()
    for _ in range(5):
        build_threat_matrix(battle, movesets)
    info = threat.cache_info()
    assert info["hits"] > 0, info
    assert info["hit_rate"] > 0.5, info


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
