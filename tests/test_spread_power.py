"""The damageslop table: spread output x how long you get to repeat it.

    "Spread damage appears to be highly valuable, as this game is what I like to
     call a 'damageslop' format. Spread damage, given it attacks two enemies at
     0.75x, is a way to maximise damage output, and chip enemies into kills by
     partner. If your spread attacker is bulky and not threatened by
     supereffective damage, it can stay on the field and trade favourably vs the
     enemy (they cannot match its output). It also is simple because there is no
     target selection, so is harder to punish and harder to switch in on."

    "rank spread attackers (spread moves >70bp) by damage output (including self
     generated weather, abilities like sheer force, pixilate, liquid voice) and
     then by neutrally effective damage output as a % (vs pokemon with base
     100hp/100def/100spdef) * hits taken to KO (hits taken to KO =
     100%/(avg of incoming neutral damage from 100bp physical move and 100bp
     special move from pokemon with 125atk/spatk stat.)"

Most of what follows guards the two bugs the first working version had, because
both produced a table that looked plausible and ranked the wrong Pokemon first.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import spread_power as sp  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def rate(name, **kw):
    W = world()
    return sp.rate(name, W["merged"], W["natures"], W["moves"], W["typechart"],
                   **kw)


class TestTheBenchmark(unittest.TestCase):

    def test_the_defender_is_typeless(self):
        """The first version typed it Normal, and a Normal benchmark is fine --
        but the INCOMING probe was Normal too, which does 0 damage to Ghosts. All
        thirteen Ghost-types came out with infinite survivability and swept the
        top of the table. Typeless makes the multiplier 1.0 by construction."""
        self.assertEqual(sp.benchmark_defender().types, [])

    def test_the_defender_is_base_100_across_the_board(self):
        d = sp.benchmark_defender()
        self.assertEqual(d.stats["def"], d.stats["spd"])
        self.assertEqual(d.current_hp, d.stats["hp"])

    def test_the_incoming_probe_finds_a_neutral_type(self):
        W = world()
        tc = W["typechart"]
        from combatants import make_combatant
        for name in ("Gholdengo", "Gengar", "Garchomp", "Sylveon"):
            c = make_combatant(name, W["merged"], W["natures"])
            _t, mult = sp.neutral_probe_type(c, tc)
            self.assertEqual(mult, 1.0, f"no neutral probe type for {name}")

    def test_nobody_survives_forever(self):
        """The symptom of the Normal-probe bug, asserted directly."""
        W = world()
        rows = sp.table(list(W["merged"]), W["merged"], W["natures"],
                        W["moves"], W["typechart"])
        for r in rows:
            self.assertLess(r["hits_to_ko"], 50.0,
                            f"{r['name']} takes no damage from the probe")
            self.assertGreater(r["incoming"], 0.0)


class TestWhatCountsAsSpreadOutput(unittest.TestCase):

    def test_suicide_moves_are_excluded(self):
        """The other bug worth a test. Metagross Explosion came out #1 and
        Snorlax Self-Destruct #2 -- 250 and 200 BP multiplied by four to six
        turns of survivability, for a move that faints the user on use. The
        score's premise is REPEATABLE output and `selfdestruct` is its exact
        negation."""
        for name in ("Metagross", "Snorlax"):
            row = rate(name)
            if row is not None:
                self.assertNotIn(row["move"],
                                 ("Explosion", "Self-Destruct",
                                  "Misty Explosion"))

    def test_the_power_floor_keeps_speed_control_out(self):
        """Snarl and Icy Wind at 55 BP are speed control, not damage."""
        W = world()
        for name in list(W["merged"]):
            for move, _raw in sp.spread_moves(W["merged"][name], W["moves"]):
                self.assertGreaterEqual(move.power, sp.MIN_SPREAD_POWER)

    def test_only_spread_moves_qualify(self):
        from damage import is_spread_move
        W = world()
        for move, _raw in sp.spread_moves(W["merged"]["Mega Charizard Y"],
                                          W["moves"]):
            self.assertTrue(is_spread_move(move.target))

    def test_foes_only_drops_the_ally_hitting_moves(self):
        """Earthquake is real damageslop but it constrains the slot beside it,
        which the score does not price -- so it must be possible to ask the
        question without it."""
        both = rate("Garchomp")
        foes = rate("Garchomp", foes_only=True)
        self.assertTrue(both["hits_ally"])
        if foes is not None:
            self.assertFalse(foes["hits_ally"])

    def test_hp_scaled_moves_are_flagged(self):
        """Eruption is 150 BP at full health and decays -- the opposite of the
        repeatability the score rewards, so the row says so."""
        self.assertTrue(rate("Torkoal")["hp_scaled"])
        self.assertFalse(rate("Mega Charizard Y")["hp_scaled"])


class TestTheMultipliersThatMatter(unittest.TestCase):
    """The named examples. Each is only "ridiculous" because of an ability or an
    item, so each is a check that the multiplier is actually reaching the sum.
    """

    def test_charizard_y_brings_its_own_sun(self):
        """"drought (sun boosted) Charizard Y heatwave". Drought is on the MEGA,
        which is the whole point -- rating base Charizard's 109 SpA in no
        weather is a different Pokemon."""
        row = rate("Mega Charizard Y")
        self.assertEqual(row["move"], "Heat Wave")
        self.assertEqual(row["weather"], "sun")
        self.assertEqual(row["ability"], "Drought")

    def test_sun_is_worth_a_lot_of_it(self):
        W = world()
        from combatants import make_combatant
        from damage import move_from_showdown
        c = make_combatant("Mega Charizard Y", W["merged"], W["natures"])
        view = c
        if c.mega_stats:
            from copy import deepcopy
            view = deepcopy(c)
            view.stats = dict(c.mega_stats)
            view.ability = c.mega_ability
        hw = move_from_showdown(W["moves"]["heatwave"])
        d = sp.benchmark_defender()
        dry = sp._neutral_damage(view, d, hw, W["typechart"], None, 2)
        sunny = sp._neutral_damage(view, d, hw, W["typechart"], "sun", 2)
        self.assertGreater(sunny, dry * 1.4)

    def test_sylveon_gets_pixilate(self):
        """"Sylveon fairy feather pixilate hyper voice (1.2x pixilate, 1.2x
        fairy feather)". Pixilate turns a Normal move Fairy, which also gains it
        STAB -- so the ability is worth much more than its own 1.2x."""
        row = rate("Sylveon")
        self.assertEqual(row["move"], "Hyper Voice")
        self.assertEqual(row["ability"], "Pixilate")
        W = world()
        from combatants import make_combatant
        from damage import move_from_showdown
        hv = move_from_showdown(W["moves"]["hypervoice"])
        d = sp.benchmark_defender()
        with_ate = sp._neutral_damage(
            make_combatant("Sylveon", W["merged"], W["natures"],
                           ability="Pixilate"), d, hv, W["typechart"], None, 2)
        without = sp._neutral_damage(
            make_combatant("Sylveon", W["merged"], W["natures"],
                           ability="Cute Charm"), d, hv, W["typechart"], None, 2)
        self.assertGreater(with_ate, without * 1.3)

    def test_ninetales_alola_brings_its_own_snow(self):
        row = rate("Ninetales-Alola")
        self.assertEqual(row["weather"], "snow")
        self.assertEqual(row["move"], "Blizzard")


class TestTheScore(unittest.TestCase):

    def test_two_targets_is_one_and_a_half_single_hits(self):
        """0.75x each, twice. This is the arithmetic the whole idea rests on."""
        W = world()
        from combatants import make_combatant
        from damage import move_from_showdown
        c = make_combatant("Sylveon", W["merged"], W["natures"])
        hv = move_from_showdown(W["moves"]["hypervoice"])
        d = sp.benchmark_defender()
        single = sp._neutral_damage(c, d, hv, W["typechart"], None, 1)
        spread = sp._neutral_damage(c, d, hv, W["typechart"], None, 2)
        self.assertAlmostEqual(spread / single, 0.75, places=1)
        self.assertAlmostEqual((spread * 2) / single, 1.5, places=1)

    def test_score_is_two_target_output_times_hits_survived(self):
        row = rate("Mega Charizard Y")
        self.assertAlmostEqual(row["score"],
                               row["two_target"] * row["hits_to_ko"])
        self.assertAlmostEqual(row["two_target"], row["per_target"] * 2)
        self.assertAlmostEqual(row["hits_to_ko"], 1.0 / row["incoming"])

    def test_bulk_is_rewarded_at_equal_output(self):
        """The claim being tested is that survivability belongs in the score at
        all: two Pokemon with the same move, and the bulkier one must rank
        higher."""
        a, b = rate("Mega Abomasnow"), rate("Froslass")
        self.assertEqual(a["move"], b["move"])
        self.assertLess(a["incoming"], b["incoming"])
        self.assertGreater(a["hits_to_ko"], b["hits_to_ko"])

    def test_the_table_is_sorted_and_named_examples_rank_well(self):
        W = world()
        rows = sp.table(list(W["merged"]), W["merged"], W["natures"],
                        W["moves"], W["typechart"], foes_only=True)
        self.assertEqual([r["score"] for r in rows],
                         sorted((r["score"] for r in rows), reverse=True))
        rank = {r["name"]: i for i, r in enumerate(rows, start=1)}
        # Not a pinned rank -- the data moves -- but the example that was called
        # "ridiculous spread damage" has to come out near the top of 160.
        self.assertLessEqual(rank["Mega Charizard Y"], 10)
        self.assertLessEqual(rank["Sylveon"], 60)

    def test_a_pokemon_with_no_spread_move_is_skipped(self):
        self.assertIsNone(rate("Scizor"))


if __name__ == "__main__":
    unittest.main()
