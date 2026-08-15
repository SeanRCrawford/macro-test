"""The Pin, and the safe play.

Both concepts came from the user, and both are pinned here in their own words.

    "in a 2v2, there is speed order 1234 (Garchomp, Zard, Swampert, Pelipper)
     which in this case is reduced to 124 because Swampert must switch or
     protect if Zard 'Pins' with solar beam"

    "if someone outspeeds and OHKOs one of your guys but its partner isn't
     threatened to be fainted this turn, is slower but OHKOs the enemy, it is a
     safe play to protect the threatened guy + attack with other"

The second is the one worth being careful about. A payoff matrix cannot tell
the two kinds of Protect apart: protecting because you guessed they would go
for that slot is a read, and protecting the Pokemon they are GUARANTEED to kill
while the unthreatened partner attacks is not. Only the second is safe, and the
distinction is the point of `safe_plays` -- it deliberately refuses to fire when
both of ours are under a guaranteed KO, because then choosing which to save is
a read again.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import pin  # noqa: E402
from _harness import load_world, setup_battle  # noqa: E402
from threat import build_threat_matrix  # noqa: E402

_WORLD = None
OURS = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"]
THEIRS = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def position(ours=OURS, theirs=THEIRS):
    b, ms = setup_battle(list(ours), list(theirs), world())
    return b, ms, build_threat_matrix(b, ms)


def named(battle, name):
    for c in battle.p1.roster + battle.p2.roster:
        if c.name == name:
            return c
    raise KeyError(name)


class TestThePinTheUserDescribed(unittest.TestCase):

    def setUp(self):
        self.b, self.ms, self.tm = position()
        self.zard = named(self.b, "Mega Charizard Y")
        self.chomp = named(self.b, "Garchomp")
        self.swampert = named(self.b, "Mega Swampert")
        self.pelipper = named(self.b, "Pelipper")

    def test_charizard_pins_swampert(self):
        p = pin.pin_between(self.tm, self.zard, self.swampert)
        self.assertIsNotNone(p)
        self.assertTrue(p.real)

    def test_it_is_solar_beam_that_does_it(self):
        self.assertEqual(pin.pin_between(self.tm, self.zard, self.swampert).move,
                         "Solar Beam")

    def test_swampert_is_pinned_and_charizard_is_not(self):
        self.assertTrue(pin.is_pinned(self.tm, self.swampert))
        self.assertFalse(pin.is_pinned(self.tm, self.zard))

    def test_swampert_still_threatens_garchomp(self):
        """The pin is not "Swampert is harmless". Its Ice Punch is a guaranteed
        KO on Garchomp -- it simply never gets to fire it."""
        t = self.tm.threat(self.swampert, self.chomp)
        self.assertTrue(t.ohko)

    def test_but_it_does_not_pin_garchomp_because_it_is_slower(self):
        """Speed is what turns a guaranteed KO into a pin."""
        self.assertFalse(self.tm.threat(self.swampert, self.chomp).outspeeds)
        self.assertFalse(pin.is_pinned(self.tm, self.chomp))

    def test_1234_becomes_124(self):
        """The user's sentence, as an assertion."""
        order, dropped = pin.acting_order(self.b, self.tm)
        self.assertEqual([c.name for c in order],
                         ["Garchomp", "Mega Charizard Y", "Pelipper"])
        self.assertEqual([c.name for c in dropped], ["Mega Swampert"])

    def test_all_four_are_in_one_list_or_the_other(self):
        order, dropped = pin.acting_order(self.b, self.tm)
        self.assertEqual(len(order) + len(dropped), 4)

    def test_the_description_says_switch_or_protect(self):
        text = " ".join(pin.describe(self.tm, self.b, "p1"))
        self.assertIn("Mega Charizard Y pins Mega Swampert", text)
        self.assertIn("switch or Protect", text)


class TestWhatIsNotAPin(unittest.TestCase):

    def test_a_ko_that_needs_a_roll_is_not_a_pin(self):
        """"A safe play does not rely on rolls." A defender is entitled to stay
        in and take a 1-in-16, so only a worst-roll KO removes the option."""
        b, ms, tm = position()
        zard, swampert = named(b, "Mega Charizard Y"), named(b, "Mega Swampert")
        t = tm.threat(zard, swampert)
        self.assertTrue(t.ohko)
        # Keep the possibility, drop the guarantee -- the pin must go with it.
        object.__setattr__(t, "ohko", False)
        object.__setattr__(t, "ohko_possible", True)
        self.assertIsNone(pin.pin_between(tm, zard, swampert))

    def test_a_fainted_pokemon_pins_nothing(self):
        b, ms, tm = position()
        zard, swampert = named(b, "Mega Charizard Y"), named(b, "Mega Swampert")
        zard.current_hp = 0
        zard.fainted = True
        self.assertIsNone(pin.pin_between(tm, zard, swampert))

    def test_nobody_is_pinned_in_a_quiet_position(self):
        b, _ms, tm = position(ours=["Farigiraf", "Garchomp", "Pelipper", "Archaludon"],
                              theirs=["Grimmsnarl", "Archaludon", "Pelipper", "Farigiraf"])
        _order, dropped = pin.acting_order(b, tm)
        self.assertEqual(dropped, [])


class TestTheSafePlay(unittest.TestCase):
    """Their Swampert guarantees a KO on our Garchomp; nothing guarantees one on
    our Charizard. Protect Garchomp, attack with Charizard -- value on every
    column, no read involved.

    Set up by hand rather than taken from turn 1, because on turn 1 Swampert is
    itself pinned and so threatens nothing.
    """

    def setUp(self):
        self.b, self.ms, _ = position()
        # Take Charizard's pin away so the position is the one being described:
        # they hold the guaranteed KO, we do not.
        self.zard = named(self.b, "Mega Charizard Y")
        self.zard.current_hp = self.zard.max_hp()
        self.swampert = named(self.b, "Mega Swampert")
        self.chomp = named(self.b, "Garchomp")

    def plays(self, matrix):
        return pin.safe_plays(matrix, self.b, "p1")

    def test_it_protects_the_pinned_one_and_attacks_with_the_other(self):
        tm = build_threat_matrix(self.b, self.ms)
        # Force the shape the user described: they pin Garchomp, not Charizard.
        for attacker, defender, ohko, outspeeds in (
                (self.swampert, self.chomp, True, True),
                (self.swampert, self.zard, False, True),
                (self.zard, self.swampert, False, True)):
            t = tm.threat(attacker, defender)
            object.__setattr__(t, "ohko", ohko)
            object.__setattr__(t, "outspeeds", outspeeds)
        plays = self.plays(tm)
        self.assertTrue(plays)
        self.assertEqual(plays[0].protect.name, "Garchomp")
        self.assertEqual(plays[0].attacker.name, "Mega Charizard Y")

    def test_there_is_no_safe_play_when_both_are_pinned(self):
        """The refusal is the interesting half. With both under a guaranteed KO,
        deciding which one to save is a read, and calling it "safe" would be a
        lie in exactly the spot where it costs a Pokemon."""
        tm = build_threat_matrix(self.b, self.ms)
        for defender in (self.chomp, self.zard):
            t = tm.threat(self.swampert, defender)
            object.__setattr__(t, "ohko", True)
            object.__setattr__(t, "outspeeds", True)
        object.__setattr__(tm.threat(self.zard, self.swampert), "ohko", False)
        self.assertEqual(self.plays(tm), [])

    def test_no_safe_play_when_nothing_is_pinned(self):
        b, _ms, tm = position(ours=["Farigiraf", "Garchomp", "Pelipper", "Archaludon"],
                              theirs=["Grimmsnarl", "Archaludon", "Pelipper", "Farigiraf"])
        self.assertEqual(pin.safe_plays(tm, b, "p1"), [])


class TestOrderingActionsByPressure(unittest.TestCase):
    """The pin's job inside the search: get the decisive row in front of the
    prefix cut, without ever deleting a row."""

    def setUp(self):
        self.b, self.ms, self.tm = position()
        from solver import our_candidate_joint_actions
        self.joints = our_candidate_joint_actions(
            self.b, self.b.p1, self.b.p2, self.ms, self.b.turn_num + 1)

    def test_ordering_keeps_every_action(self):
        """Sorting, never pruning. "Stay in and attack while pinned" is not
        dominated -- it is excellent on the columns where they hit the other
        slot -- so removing it would change the equilibrium."""
        ranked = pin.rank_joint_actions(self.tm, self.b, self.joints)
        self.assertEqual(len(ranked), len(self.joints))
        self.assertEqual({id(a) for j in ranked for a in j},
                         {id(a) for j in self.joints for a in j})

    def test_firing_the_pin_sorts_to_the_front(self):
        ranked = pin.rank_joint_actions(self.tm, self.b, self.joints)
        top = ranked[0]
        fires = [a for a in top if a.kind == "move" and a.move is not None
                 and a.move.name == "Solar Beam"]
        self.assertTrue(fires, [f"{a.combatant.name}:{a.kind}" for a in top])

    def test_it_beats_the_arbitrary_generator_order(self):
        """The prefix cut is what makes this matter, so measure it AS a cut.

        As generated, the pin row is at index 11 and the first five rows are all
        "Charizard Protects" -- itertools.product walks Protect first because it
        is first in the moveset. At INNER_ACTION_CAP=8 the move that wins the
        game is cut and the passive rows are kept, which is the reported symptom
        in miniature.
        """
        from turn_game import INNER_ACTION_CAP as cap

        def has_solar_beam(joints):
            return any(a.kind == "move" and a.move is not None
                       and a.move.name == "Solar Beam"
                       for j in joints for a in j)

        self.assertFalse(has_solar_beam(self.joints[:cap]))
        ranked = pin.rank_joint_actions(self.tm, self.b, self.joints)
        self.assertTrue(has_solar_beam(ranked[:cap]))

    def test_a_pinned_pokemon_attacking_scores_below_it_protecting(self):
        b, ms, tm = position()
        swampert, zard = named(b, "Mega Swampert"), named(b, "Mega Charizard Y")
        from solver import our_candidate_joint_actions
        joints = our_candidate_joint_actions(b, b.p2, b.p1, ms, b.turn_num + 1)
        protects, attacks = [], []
        for j in joints:
            for a in j:
                if a.combatant is not swampert:
                    continue
                if a.kind == "protect":
                    protects.append(pin.joint_action_score(tm, b, j))
                elif a.kind == "move" and a.move is not None and a.move.power:
                    attacks.append(pin.joint_action_score(tm, b, j))
        self.assertTrue(protects and attacks)
        self.assertGreater(max(protects), max(attacks))


class TestItReachesTheReportedLine(unittest.TestCase):
    """The pins have to appear where the line is read, or they are a library
    nobody calls."""

    def test_the_opening_pins_are_attached_to_a_line(self):
        from preview_lead import opening_pins
        text = " ".join(opening_pins(OURS, THEIRS, world()))
        self.assertIn("pins Mega Swampert", text)

    def test_opening_pins_never_raises(self):
        """A line that cannot be explained is still a line worth reporting."""
        from preview_lead import opening_pins
        self.assertEqual(opening_pins(["Nonexistent Pokemon"], THEIRS, world()), [])

    def test_describe_line_shows_them(self):
        from preview_lead import describe_line
        text = describe_line({
            "their_lead": ["Pelipper", "Mega Swampert"],
            "outcome": "win", "length": 10, "win_prob": None,
            "pins": ["Mega Charizard Y pins Mega Swampert with Solar Beam"],
            "turns": [],
        })
        self.assertIn("pins Mega Swampert", text)

    def test_the_recorded_kinds_distinguish_a_switch_from_an_attack(self):
        """Counting passivity off the prose does not work -- an attack and a
        switch both render with an arrow -- so the kinds are recorded."""
        from preview_lead import _kinds, _say

        class FakeMon:
            name = "Garchomp"

        class FakeAction:
            def __init__(self, kind, move=None, targets=()):
                self.kind, self.move, self.targets = kind, move, list(targets)
                self.combatant = FakeMon()

        class FakeMove:
            name = "Earthquake"

        class Target:
            name = "Farigiraf"

        actions = [FakeAction("switch", targets=[Target()]),
                   FakeAction("move", FakeMove(), [Target()])]
        self.assertEqual(_kinds(actions), ["switch", "attack"])
        self.assertEqual(_say(actions).count("->"), 2)   # why prose fails


if __name__ == "__main__":
    unittest.main()
