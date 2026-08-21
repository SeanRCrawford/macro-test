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

    def test_the_description_says_what_the_pinned_side_can_do(self):
        """It used to say "must switch or Protect". Both halves of that are
        wrong once the escape check exists: a REAL pin is precisely one no
        switch answers, and Protect defers it rather than solving it -- the
        board is unchanged and the same pin applies next turn."""
        text = " ".join(pin.describe(self.tm, self.b, "p1"))
        self.assertIn("Mega Charizard Y pins Mega Swampert", text)
        self.assertIn("no switch that answers it", text)
        self.assertIn("Protect only defers", text)


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


class TestThisTurnPlusNextTurn(unittest.TestCase):
    """A pin is a claim about the whole SIDE, over TWO turns.

    Two corrections built this, and the second overturned the first.

    What breaks a pin -- a switch, not a stall:

        "Favourable repositioning requires them to switch. A double protect is
         fine as long as the enemy can't favourably reposition or set up ...
         If there is no-one in the back who can resist the incoming hit, or
         survive this turn+next turn ... that's a pin. But if they take the hit
         well, e.g. resisted, then can KO me next turn, either by outspeeding or
         by being slower and not getting KOd by either of mine, then that is not
         a pin and is a risk and a good play for them."

    And then the sum that matters:

        "Really, this turn + next turn (very quick and simple arithmetic) is all
         that matters. ... if Charizard switches in, it will take bullet punch +
         blizzard, then next turn another blizzard (outsped) + bullet punch
         (priority) which might kill, so could be a genuine full pin."

    The reference position is the one that was reported. Scizor's Bullet Punch
    guarantee-OHKOs both of their actives and moves first. Mega Charizard Y is
    Fire/Flying, resists Bullet Punch at 0.25x, and KOs Scizor back with Heat
    Wave -- so against the PINNER alone it looks like a clean escape, which is
    what the first version of `escape_from` concluded.

    It is not. Measured on this position:

        Bullet Punch alone           21.2% of Charizard's max
        Bullet Punch + Blizzard      78.6%  guaranteed, on the switch-in turn
        the same again on turn 2     78.6%  (Blizzard outspeeds, BP has priority)
        two-turn total              157.1%  -- dead without ever acting

    So it is a COMPLETE pin: their best answer to it dies on the way to firing.
    """

    OURS = ["Scizor", "Ninetales-Alola", "Garchomp", "Kingambit"]
    THEIRS = ["Mega Floette", "Whimsicott", "Mega Charizard Y", "Basculegion"]

    def setUp(self):
        self.b, self.ms, self.tm = position(self.OURS, self.THEIRS)
        self.scizor = named(self.b, "Scizor")
        self.ninetales = named(self.b, "Ninetales-Alola")
        self.floette = named(self.b, "Mega Floette")
        self.zard = named(self.b, "Mega Charizard Y")

    # --- the arithmetic itself -------------------------------------------

    def test_the_pinner_alone_does_not_threaten_the_switch_in(self):
        """The premise of the wrong answer, kept so the fix cannot silently
        regress into being right for the wrong reason."""
        t = self.tm.threat(self.scizor, self.zard)
        self.assertFalse(t.ohko_possible)
        self.assertLess(t.dmg_min, 0.30)

    def test_the_switch_in_turn_counts_both_of_our_attacks(self):
        """It switched, so everything we aim at that slot lands."""
        ours = [self.scizor, self.ninetales]
        alone = pin._incoming(self.tm, [self.scizor], self.zard)
        both = pin._incoming(self.tm, ours, self.zard)
        self.assertGreater(both, alone * 2)
        self.assertGreater(both, 0.70)
        self.assertLess(both, 1.0, "it must SURVIVE the switch-in turn, or "
                                   "this position tests nothing about turn 2")

    def test_turn_two_counts_only_what_lands_before_it_acts(self):
        """Damage after it has fired does not stop it firing. Here both of ours
        are ahead of it -- Blizzard outspeeds, Bullet Punch has priority -- so
        the whole round counts."""
        ours = [self.scizor, self.ninetales]
        before = pin._incoming(self.tm, ours, self.zard, only_before_it_acts=True)
        self.assertAlmostEqual(before, pin._incoming(self.tm, ours, self.zard))

    def test_two_turns_kill_it(self):
        ours = [self.scizor, self.ninetales]
        two = (pin._incoming(self.tm, ours, self.zard)
               + pin._incoming(self.tm, ours, self.zard, only_before_it_acts=True))
        self.assertGreaterEqual(two, 1.0)

    # --- and therefore ---------------------------------------------------

    def test_the_pin_holds_through_the_switch(self):
        p = pin.pin_between(self.tm, self.scizor, self.floette, self.b)
        self.assertIsNone(p.escape, "Charizard Y survives Bullet Punch but not "
                                    "Bullet Punch + Blizzard, twice")
        self.assertTrue(p.real)
        self.assertTrue(pin.is_pinned(self.tm, self.floette, self.b))

    def test_it_is_reported_as_complete_and_names_who_tried(self):
        """`complete` is a stronger claim than `real`: their answer was played
        out and lost, rather than never existing."""
        p = pin.pin_between(self.tm, self.scizor, self.floette, self.b)
        self.assertTrue(p.complete)
        self.assertIn("Mega Charizard Y", p.through)
        text = " ".join(pin.describe(self.tm, self.b, "p1"))
        self.assertIn("Complete pin", text)
        self.assertIn("Mega Charizard Y", text)

    def test_a_lone_pinner_cannot_claim_a_complete_pin(self):
        """Take the partner off the field and the arithmetic changes sides:
        Bullet Punch alone is 21% a turn, so Charizard lives, KOs back, and the
        pin becomes a risk. Same board, same bench -- the difference is entirely
        the second attacker, which is what the two-turn sum is for."""
        self.ninetales.fainted = True
        self.ninetales.current_hp = 0
        b, ms, tm = self.b, self.ms, build_threat_matrix(self.b, self.ms)
        p = pin.pin_between(tm, self.scizor, self.floette, b)
        self.assertIs(p.escape, self.zard)
        self.assertFalse(p.real)
        self.assertFalse(p.complete)
        text = " ".join(pin.describe(tm, b, "p1"))
        self.assertIn("risk, not a threat", text)

    # --- what is not an escape -------------------------------------------

    def test_protect_is_never_an_escape(self):
        """A null turn leaves the board as it was, so the pin must survive it.

        "next turn I can do the same thing". No amount of Protecting turns a
        threat into a risk or back.
        """
        before = pin.pin_between(self.tm, self.scizor, self.floette, self.b)
        self.floette.protecting = True
        self.floette.protected_last_turn = True
        after = pin.pin_between(self.tm, self.scizor, self.floette, self.b)
        self.assertEqual(before.real, after.real)
        self.assertEqual(before.through, after.through)

    def test_a_pin_with_no_answer_in_the_back_stays_real_but_not_complete(self):
        """The control. Charizard's Solar Beam on Swampert: nothing behind it
        threatens Charizard back, so the pin is real and UNCONTESTED -- there is
        no `through` list because nobody tried."""
        b, _ms, tm = position()
        p = pin.pin_between(tm, named(b, "Mega Charizard Y"),
                            named(b, "Mega Swampert"), b)
        self.assertIsNone(p.escape)
        self.assertTrue(p.real)
        self.assertEqual(p.through, ())
        self.assertFalse(p.complete)

    def test_battle_is_optional(self):
        """Callers without a battle keep working; the two-turn check is skipped
        rather than crashing, and the pin is reported on the board alone."""
        p = pin.pin_between(self.tm, self.scizor, self.floette)
        self.assertIsNotNone(p)
        self.assertTrue(p.real)
        self.assertEqual(p.through, ())
        self.assertTrue(pin.is_pinned(self.tm, self.floette))


class TestASwitchInThatMegaEvolves(unittest.TestCase):
    """The switch-in is measured as what it BECOMES, not what it is on the bench.

        "I would note Mega Charizard Y could mega evolve the next turn which
         would make it bulkier."

    Correct, and the two-turn sum was blind to it -- the ThreatMatrix is built
    against base forms. Measured on the reference position with Charizard Y as
    their only Mega: 157.1% over two turns against base bulk, 122.2% against
    Mega bulk. Still a pin, but 35 points of that margin was phantom, and on a
    closer call it flips the verdict.
    """

    # Charizard Y is their ONLY Mega here, so it can still evolve. In the
    # earlier fixture Mega Floette holds the slot, `is_mega_pick` is False on
    # Charizard, and the factor correctly does nothing.
    OURS = ["Scizor", "Ninetales-Alola", "Garchomp", "Kingambit"]
    THEIRS = ["Whimsicott", "Basculegion", "Mega Charizard Y", "Garchomp"]

    def setUp(self):
        self.b, self.ms, self.tm = position(self.OURS, self.THEIRS)
        self.zard = named(self.b, "Mega Charizard Y")

    def test_the_bench_mega_is_still_in_base_form(self):
        """The premise: this is why the correction is needed at all."""
        self.assertTrue(self.zard.is_mega_pick)
        self.assertFalse(self.zard.mega_evolved)
        self.assertIsNotNone(self.zard.mega_stats)

    def test_the_factor_discounts_our_damage(self):
        f = pin.mega_bulk_factor(self.zard)
        self.assertLess(f, 1.0)
        self.assertGreater(f, 0.5)

    def test_it_takes_the_ratio_most_generous_to_them(self):
        """Which defence applies depends on the move's category, which a Threat
        does not keep. Understating a pin is the safe error."""
        base, mega = self.zard.stats, self.zard.mega_stats
        ratios = [(base["hp"] * base[k]) / (mega["hp"] * mega[k])
                  for k in ("def", "spd")]
        self.assertAlmostEqual(pin.mega_bulk_factor(self.zard), min(ratios))

    def test_incoming_is_scaled_by_it(self):
        ours = [named(self.b, "Scizor"), named(self.b, "Ninetales-Alola")]
        scaled = pin._incoming(self.tm, ours, self.zard)
        raw = sum(self.tm.threat(a, self.zard).dmg_min for a in ours)
        self.assertAlmostEqual(scaled,
                               raw * pin.mega_bulk_factor(self.zard))
        self.assertLess(scaled, raw)

    def test_a_pokemon_that_cannot_mega_is_untouched(self):
        for name in ("Whimsicott", "Basculegion", "Garchomp"):
            self.assertEqual(pin.mega_bulk_factor(named(self.b, name)), 1.0)

    def test_an_already_evolved_mega_is_untouched(self):
        """It is already the bulkier thing; discounting again would double-count."""
        self.zard.mega_evolved = True
        self.assertEqual(pin.mega_bulk_factor(self.zard), 1.0)

    def test_the_slot_is_taken_when_another_mega_is_on_the_team(self):
        """One Mega per team. With Floette holding the slot, Charizard cannot
        evolve and the factor must not pretend it can."""
        b, _ms, _tm = position(
            self.OURS, ["Mega Floette", "Whimsicott", "Mega Charizard Y",
                        "Basculegion"])
        self.assertFalse(named(b, "Mega Charizard Y").is_mega_pick)
        self.assertEqual(pin.mega_bulk_factor(named(b, "Mega Charizard Y")), 1.0)
