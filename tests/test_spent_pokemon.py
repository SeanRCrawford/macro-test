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
# Read at import, before any test can touch them.
SHIPPED_DEFAULT = solver.FRAGILE_HP
SHIPPED_MEGA_PREMIUM = solver.UNSPENT_MEGA_PREMIUM

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
    """The discount ships ON now, at this threshold. Pinned locally anyway so
    the file keeps testing the mechanism at a known setting rather than
    silently re-tuning itself if the default is ever changed."""
    global _patch
    _patch = unittest.mock.patch.object(solver, "FRAGILE_HP", FRAGILE_HP)
    _patch.start()


def tearDownModule():
    _patch.stop()


class TestTheDefault(unittest.TestCase):

    def test_it_ships_on(self):
        """Changing it means bumping the two cache schemas and re-recording the
        golden baseline, so the default must not drift by accident."""
        self.assertEqual(SHIPPED_DEFAULT, FRAGILE_HP)

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

    def test_with_the_shipped_settings_the_solver_sacrifices(self):
        """THE REPORTED LOSS, as the decision it now makes."""
        sacrifice, rescue = self._costs()
        self.assertLess(sacrifice, rescue,
                        "the spent mon is still worth rescuing at a healthy "
                        "Pokemon's expense")

    def test_it_takes_BOTH_terms_to_flip_it(self):
        """Honest about what one term does on its own. The discount alone takes
        the cost of sacrificing from 192 to 61, against a rescue costing 50 -- a
        big move, but not enough on its own. The flip needs the speed term too,
        because a slow spent Pokemon is a positional liability as well as a
        spent one."""
        with unittest.mock.patch.object(solver, "SPEED_CONTROL_WEIGHT", 0.0):
            alone_sac, rescue = self._costs()
        self.assertGreater(alone_sac, rescue)
        both_sac, both_rescue = self._costs()      # shipped: both on
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


class TestUnspentMega(unittest.TestCase):
    """The second half of the report: the Pokemon that came in to cover the
    spent one "was a Mega which had not mega'd", so it took the hits in its
    frailer base form and fainted -- costing the team the Mega slot as well as
    the Pokemon.

    The premium that prices that is PARKED AT 1.0 (see the comment above the
    constant: inert at the clamp for real attackers, and it does not move the
    reported decision because being chipped is not a KO). These tests turn it
    on to pin the mechanism for whoever picks it up next.
    """

    def setUp(self):
        self._p = unittest.mock.patch.object(solver, "UNSPENT_MEGA_PREMIUM", 1.25)
        self._p.start()
        self.addCleanup(self._p.stop)

    def test_it_ships_off(self):
        self.assertEqual(SHIPPED_MEGA_PREMIUM, 1.0)

    def test_an_untransformed_mega_is_worth_more_than_its_base_stats(self):
        b, _ms = fresh()
        mega = next(c for side in (b.p1, b.p2) for c in side.roster
                    if c.is_mega_pick)
        # Middling offence, so the 1.35 clamp is not already binding -- which is
        # also the case that matters: the Mega brought in to EAT hits is the
        # bulky one, and a maxed attacker sits at the clamp either way.
        mega.stats["atk"], mega.stats["spa"] = 100, 100
        mega.mega_evolved = False
        unspent = solver._ko_threat_value(mega)
        mega.mega_evolved = True
        spent = solver._ko_threat_value(mega)
        self.assertGreater(unspent, spent)
        self.assertAlmostEqual(unspent, spent * solver.UNSPENT_MEGA_PREMIUM)

    def test_a_pokemon_that_is_not_a_mega_pick_is_unaffected(self):
        b, _ms = fresh()
        plain = next(c for c in b.p1.roster if not c.is_mega_pick)
        before = solver._ko_threat_value(plain)
        with unittest.mock.patch.object(solver, "UNSPENT_MEGA_PREMIUM", 2.0):
            self.assertEqual(solver._ko_threat_value(plain), before)

    def test_the_premium_cannot_escape_the_clamp(self):
        """_ko_threat_value is bounded so no single Pokemon outweighs the
        board. A multiplier that broke that would make the KO term unstable."""
        b, _ms = fresh()
        mega = next(c for side in (b.p1, b.p2) for c in side.roster
                    if c.is_mega_pick)
        mega.mega_evolved = False
        with unittest.mock.patch.object(solver, "UNSPENT_MEGA_PREMIUM", 10.0):
            self.assertLessEqual(solver._ko_threat_value(mega), 1.35)

    def test_turning_it_off_restores_the_old_value(self):
        b, _ms = fresh()
        mega = next(c for side in (b.p1, b.p2) for c in side.roster
                    if c.is_mega_pick)
        mega.mega_evolved = False
        with unittest.mock.patch.object(solver, "UNSPENT_MEGA_PREMIUM", 1.0):
            off = solver._ko_threat_value(mega)
        mega.mega_evolved = True
        self.assertEqual(off, solver._ko_threat_value(mega))


class TestStillAntisymmetric(unittest.TestCase):

    def test_both_rosters_are_discounted_the_same_way(self):
        b, _ms = fresh()
        for c in (b.p1.active[0], b.p2.active[0]):
            c.apply_damage(c.max_hp() * 0.9)
        total = solver.heuristic_eval(b, "p1") + solver.heuristic_eval(b, "p2")
        self.assertLess(abs(total), 1e-6)


if __name__ == "__main__":
    unittest.main()
