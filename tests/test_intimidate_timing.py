"""When Intimidate resolves after a KO, and who it hits.

The rule, as the user stated it: replacements for fainted Pokemon are sent in
together, and Intimidate takes effect AFTER all of them are on the field. So in
a double KO the fainted enemy is not intimidated -- it is gone -- but its
replacement is.

The engine used to send in and trigger one side at a time, which got both
halves of that backwards: p1's Intimidate landed on p2's corpse (an effect on
nothing) and p2's replacement walked in untouched.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from battle import Battle  # noqa: E402
from combatants import make_team  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def battle_with(ours, theirs):
    w = world()
    p1 = make_team(list(ours), w["merged"], w["natures"])
    p2 = make_team(list(theirs), w["merged"], w["natures"])
    return Battle(p1, p2, w["typechart"], w["moves"])


class TestIntimidateOnReplacement(unittest.TestCase):
    """Incineroar has Intimidate; the bench mon behind each lead does not, so
    every Attack drop below is attributable."""

    def setUp(self):
        # Our bench holds the Intimidate user, so it arrives as a REPLACEMENT
        # rather than as a lead.
        # THREE Pokemon a side, so each bench holds exactly one and the
        # replacement is not a choice `_best_replacement` gets to make -- these
        # tests are about timing, not about who comes in.
        #
        # No Defiant or Competitive on their side either: those turn an
        # Intimidate into an Attack BOOST, which is correct behaviour and the
        # wrong thing to measure a timing fix with.
        self.b = battle_with(["Gallade", "Hydreigon", "Incineroar"],
                             ["Garchomp", "Pelipper", "Archaludon"])

    def _faint(self, side, slot):
        """KO it the way the engine does -- `fainted` is a flag apply_damage
        sets, not a property of current_hp, so zeroing HP alone leaves a
        Pokemon that is at 0 and still standing."""
        c = side.active[slot]
        c.apply_damage(c.max_hp())
        assert c.fainted
        return c

    def test_a_fainted_pokemon_is_not_intimidated_but_its_replacement_is(self):
        """The double KO. Both sides lose their slot-0 Pokemon in the same turn;
        our replacement has Intimidate."""
        dead_ours = self._faint(self.b.p1, 0)
        dead_theirs = self._faint(self.b.p2, 0)
        survivor = self.b.p2.active[1]

        self.b._replace_fainted()

        self.assertTrue(dead_ours.fainted and dead_theirs.fainted)
        self.assertEqual(self.b.p1.active[0].name, "Incineroar")
        their_replacement = self.b.p2.active[0]
        self.assertNotIn(their_replacement.name, ("Garchomp",))

        # The corpse is untouched -- there is nothing there to intimidate.
        self.assertEqual(dead_theirs.stages["atk"], 0)
        # Its replacement, and the surviving partner, both took the drop.
        self.assertEqual(their_replacement.stages["atk"], -1)
        self.assertEqual(survivor.stages["atk"], -1)

    def test_it_still_hits_the_survivor_when_only_we_lost_a_pokemon(self):
        self._faint(self.b.p1, 0)
        before = [c.stages["atk"] for c in self.b.p2.active]
        self.b._replace_fainted()
        self.assertEqual(self.b.p1.active[0].name, "Incineroar")
        for c, was in zip(self.b.p2.active, before):
            self.assertEqual(c.stages["atk"], was - 1)

    def test_both_replacements_are_on_the_field_before_any_ability_fires(self):
        """The property the two-phase split exists for. Whatever a switch-in
        ability looks at, it must see the finished board."""
        seen = []
        import battle as battle_module
        original = battle_module.on_switch_in

        def spy(entering, opposing_active, field_state, **kwargs):
            seen.append((entering.name,
                         [c.name for c in opposing_active if c is not None],
                         [c.fainted for c in opposing_active if c is not None]))
            return original(entering, opposing_active, field_state, **kwargs)

        battle_module.on_switch_in = spy
        try:
            self._faint(self.b.p1, 0)
            self._faint(self.b.p2, 0)
            self.b._replace_fainted()
        finally:
            battle_module.on_switch_in = original

        self.assertEqual(len(seen), 2)
        for _who, _foes, fainted_flags in seen:
            self.assertFalse(any(fainted_flags),
                             "a switch-in ability resolved against a corpse")

    def test_switch_in_abilities_resolve_in_speed_order(self):
        """Simultaneous switch-ins are ordered by Speed -- it decides which of
        two weather setters ends up owning the weather."""
        order = []
        import battle as battle_module
        original = battle_module.on_switch_in

        def spy(entering, *args, **kwargs):
            order.append(entering.name)
            return original(entering, *args, **kwargs)

        battle_module.on_switch_in = spy
        try:
            self._faint(self.b.p1, 0)
            self._faint(self.b.p2, 0)
            self.b._replace_fainted()
        finally:
            battle_module.on_switch_in = original

        from engine import effective_speed
        speeds = []
        for name in order:
            for side in (self.b.p1, self.b.p2):
                for c in side.active:
                    if c is not None and c.name == name:
                        speeds.append(effective_speed(c, self.b.field, side.name))
        self.assertEqual(speeds, sorted(speeds, reverse=True))

    def test_a_replacement_does_not_act_on_the_turn_it_arrives(self):
        """Unchanged behaviour, pinned because the two-phase rewrite touches the
        code that decides it: replacements land at the end of the turn and get
        their first action on the next one."""
        self._faint(self.b.p1, 0)
        self.b._replace_fainted()
        self.assertEqual(self.b.p1.active[0].active_turn_count, 0)


if __name__ == "__main__":
    unittest.main()
