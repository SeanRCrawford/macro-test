"""Preserving a Pokemon that can no longer do anything.

Reported from real games: a mon on low health, slower than the threats, gets
switched out rather than sacrificed, and the healthy Pokemon that comes in eats
the hits and faints instead. A low-HP Pokemon is only worth preserving if it can
DO something in a future gamestate.

The cause was in the evaluation, not the search: `_ko_threat_value` paid a full
threat credit for merely being unfainted, so a spent Pokemon was priced like a
healthy one and rescuing it always looked worth a healthy mon's HP.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import unittest.mock  # noqa: E402

import solver  # noqa: E402
from _harness import enemy_bring, load_world, setup_battle  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
FRAGILE_HP = 0.25    # the threshold the measurements in solver.py were taken at
# Read at import, before any test can touch it.
SHIPPED_DEFAULT = solver.FRAGILE_HP

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh():
    w = world()
    return setup_battle(OUR_TEAM, enemy_bring(list(w["teams"])[0], w), w)


def setUpModule():
    """The discount SHIPS OFF -- it fixes the reported behaviour but costs more
    on the whole-team audit than it gains (see the measurement block in
    solver.py). Every test here turns it on for itself, so the file keeps
    testing the mechanism rather than silently passing on a disabled one."""
    global _patch
    _patch = unittest.mock.patch.object(solver, "FRAGILE_HP", FRAGILE_HP)
    _patch.start()


def tearDownModule():
    _patch.stop()


class TestTheDefault(unittest.TestCase):

    def test_it_ships_off(self):
        """Turning it on means bumping the two cache schemas and re-recording
        the golden baseline, so the default must not drift by accident."""
        self.assertEqual(SHIPPED_DEFAULT, 0.0)

    def test_zero_is_a_true_no_op(self):
        b, _ms = fresh()
        c = b.p1.active[0]
        c.apply_damage(c.max_hp() * 0.95)
        make_slower(c, b, b.p2.active)
        with unittest.mock.patch.object(solver, "FRAGILE_HP", 0.0):
            self.assertEqual(solver._spent_discount(b, c, b.p1, b.p2), 1.0)


def make_slower(c, battle, foes):
    c.stats["spe"] = 1
    for f in foes:
        f.stats["spe"] = 300


class TestSpentDiscount(unittest.TestCase):

    def test_a_healthy_active_keeps_its_full_threat_credit(self):
        b, _ms = fresh()
        c = b.p1.active[0]
        self.assertEqual(solver._spent_discount(b, c, b.p1, b.p2), 1.0)

    def test_a_benched_pokemon_is_never_discounted(self):
        """Nothing is threatening it, however hurt it is -- it comes back on a
        turn of our choosing, which is exactly the 'can do something in a future
        gamestate' the rule asks for."""
        b, _ms = fresh()
        benched = b.p1.bench[0]
        benched.apply_damage(benched.max_hp() * 0.95)
        self.assertFalse(benched.fainted)
        self.assertEqual(solver._spent_discount(b, benched, b.p1, b.p2), 1.0)

    def test_fragile_and_outsped_is_discounted_hardest(self):
        b, _ms = fresh()
        c = b.p1.active[0]
        c.apply_damage(c.max_hp() * 0.95)
        make_slower(c, b, b.p2.active)
        self.assertEqual(solver._spent_discount(b, c, b.p1, b.p2),
                         solver.FRAGILE_SLOWER_KEEP)

    def test_fragile_but_faster_keeps_more(self):
        """It still gets one more action. Not full credit though -- priority
        moves mean being faster is not safety."""
        b, _ms = fresh()
        c = b.p1.active[0]
        c.apply_damage(c.max_hp() * 0.95)
        c.stats["spe"] = 300
        for f in b.p2.active:
            f.stats["spe"] = 1
        self.assertEqual(solver._spent_discount(b, c, b.p1, b.p2),
                         solver.FRAGILE_FASTER_KEEP)
        self.assertGreater(solver.FRAGILE_FASTER_KEEP,
                           solver.FRAGILE_SLOWER_KEEP)
        self.assertLess(solver.FRAGILE_FASTER_KEEP, 1.0)


class TestTheDecisionItChanges(unittest.TestCase):

    def _costs(self):
        """(cost of sacrificing the spent mon, cost of rescuing it).

        The reported loss, as two positions. Left: our spent Pokemon dies.
        Right: it is saved and a healthy team member takes a heavy hit instead.
        """
        b, _ms = fresh()
        spent, healthy = b.p1.active[0], b.p1.active[1]
        spent.apply_damage(spent.max_hp() * 0.95)
        make_slower(spent, b, b.p2.active)
        base = solver.heuristic_eval(b, "p1")
        return (base - solver.heuristic_eval(_copy_with(b, kill=spent), "p1"),
                base - solver.heuristic_eval(
                    _copy_with(b, damage=(healthy, 0.5)), "p1"))

    def test_it_stops_pricing_a_spent_mon_like_a_healthy_one(self):
        """On the reported position the cost of losing the spent Pokemon falls
        from ~192 points to ~61 -- from 'more than a Pokemon' to a third of
        one, which is what it is actually worth once it can no longer act."""
        with_discount, _ = self._costs()
        with unittest.mock.patch.object(solver, "FRAGILE_HP", 0.0):
            without, _ = self._costs()
        self.assertLess(with_discount, without / 2)
        self.assertLess(with_discount, solver.KO_WEIGHT * 0.35,
                        "still priced above the cheapest possible KO")

    def test_the_switch_only_flips_with_the_speed_term_as_well(self):
        """Honest about what one term does on its own. The discount alone takes
        the sacrifice from 192 to 61 against a rescue costing 50 -- a big move,
        but the solver still rescues. It is with SPEED_CONTROL_WEIGHT on too
        (a slow spent mon is also a positional liability) that the cost drops
        to 37 and the decision actually changes. Both are parked; this records
        what turning them on would buy."""
        alone_sac, rescue = self._costs()
        self.assertGreater(alone_sac, rescue)
        with unittest.mock.patch.object(solver, "SPEED_CONTROL_WEIGHT", 12.0):
            both_sac, both_rescue = self._costs()
        self.assertLess(both_sac, both_rescue)

    def test_a_HEALTHY_pokemon_is_still_worth_saving(self):
        """The discount must not turn into 'never protect anything'."""
        b, _ms = fresh()
        ours, other = b.p1.active[0], b.p1.active[1]
        base = solver.heuristic_eval(b, "p1")
        lost = solver.heuristic_eval(_copy_with(b, kill=ours), "p1")
        chipped = solver.heuristic_eval(_copy_with(b, damage=(other, 0.5)), "p1")
        self.assertGreater(base - lost, base - chipped)


def _copy_with(battle, kill=None, damage=None):
    """A deep copy with one Pokemon KO'd, or one damaged by a fraction."""
    import copy
    nxt = copy.deepcopy(battle)
    names = {id(c): c for side in (nxt.p1, nxt.p2) for c in side.roster}

    def twin(target):
        for c in names.values():
            if c.name == target.name:
                return c
        raise AssertionError(target.name)

    if kill is not None:
        twin(kill).apply_damage(kill.max_hp())
    if damage is not None:
        target, frac = damage
        twin(target).apply_damage(target.max_hp() * frac)
    return nxt


class TestStillAntisymmetric(unittest.TestCase):

    def test_both_rosters_are_discounted_the_same_way(self):
        b, _ms = fresh()
        for c in (b.p1.active[0], b.p2.active[0]):
            c.apply_damage(c.max_hp() * 0.9)
        total = solver.heuristic_eval(b, "p1") + solver.heuristic_eval(b, "p2")
        self.assertLess(abs(total), 1e-6)


if __name__ == "__main__":
    unittest.main()
