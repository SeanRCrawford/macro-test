"""`species_data.team_to_showdown_export`/`resolve_export_fields` -- the
inverse of `custom_team_from_export`, added for "I need a way to export any
of the teamsheets generated in the counter_table.py .xlsx to the streamlit
app, maybe with a base64 encoded pokepaste in an excel cell." A pokepaste is
only useful if it comes back out the same way it went in, so these tests are
all round-trips through the existing parser.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))


def world():
    from _harness import load_world
    return load_world()


class TestTeamToShowdownExportRoundTrips(unittest.TestCase):
    """Every case round-trips through `custom_team_from_export` -- the
    species/item/ability/nature/EVs/moves that come back out must be
    exactly what would have been simulated for that member."""

    @classmethod
    def setUpClass(cls):
        cls.W = world()
        cls.merged = cls.W["merged"]

    def test_a_plain_mon_with_no_overrides_round_trips(self):
        from species_data import custom_team_from_export, team_to_showdown_export
        text = team_to_showdown_export(["Incineroar"], {}, self.merged)
        self.assertIn("Incineroar @", text.splitlines()[0])
        roster, sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, ["Incineroar"])
        rec = self.merged["Incineroar"]
        self.assertEqual(sets["Incineroar"]["nature"], rec["nature"])
        self.assertEqual(sets["Incineroar"]["evs"], rec["evs"])

    def test_explicit_item_ability_nature_evs_moves_round_trip_exactly(self):
        from species_data import custom_team_from_export, team_to_showdown_export
        spec = {"Gallade": {
            "item": "Life Orb", "ability": "Sharpness", "nature": "Jolly",
            "evs": {"hp": 0, "atk": 32, "def": 0, "spa": 0, "spd": 0, "spe": 34},
            "moves": ["Psycho Cut", "Sacred Sword", "Close Combat", "Ice Punch"]}}
        text = team_to_showdown_export(["Gallade"], spec, self.merged)
        roster, sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, ["Gallade"])
        self.assertEqual(sets["Gallade"], spec["Gallade"])

    def test_a_stone_holding_mega_round_trips_to_the_same_mega_name(self):
        from species_data import (custom_team_from_export, find_mega_stone,
                                  team_to_showdown_export)
        mega_name = next(
            n for n in self.merged
            if n.startswith("Mega ") and n != "Mega Floette"
            and find_mega_stone(n, self.merged))
        text = team_to_showdown_export([mega_name], {}, self.merged)
        base = mega_name[len("Mega "):]
        # Base species (not the "Mega X" roster name) holding the stone --
        # what Showdown/pokepast.es itself expects.
        self.assertTrue(text.split(" @ ", 1)[0].endswith(base.split()[0])
                        or text.startswith(mega_name[5:].split()[0]))
        roster, _sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, [mega_name])

    def test_a_mega_with_no_recorded_stone_round_trips_verbatim(self):
        """Mega Floette's usage data shows Choice Scarf/Life Orb, not a
        stone -- nothing to invert, so it's written and read back as that
        literal name (`find_mega_stone`'s own documented behaviour)."""
        from species_data import custom_team_from_export, team_to_showdown_export
        if "Mega Floette" not in self.merged:
            self.skipTest("Mega Floette not in this dataset")
        text = team_to_showdown_export(["Mega Floette"], {}, self.merged)
        self.assertTrue(text.startswith("Mega Floette") or text.startswith("Floette"))
        roster, _sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, ["Mega Floette"])

    def test_floette_eternal_is_recognised_as_mega_floette(self):
        """"unknown Pokemon: Floette-Eternal" -- a real pokepaste (e.g. one
        exported straight from Showdown, which has its own "Floette-Eternal"
        dex entry) names this Pokemon differently than this roster's usage
        data does ("Mega Floette", per the no-recorded-stone case above).
        A paste using either name must resolve to the roster's own name."""
        from species_data import custom_team_from_export
        if "Mega Floette" not in self.merged:
            self.skipTest("Mega Floette not in this dataset")
        text = ("Floette-Eternal @ Choice Scarf\n"
               "- Moonblast\n- Aromatherapy\n- Wish\n- Protect")
        roster, sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, ["Mega Floette"])
        self.assertEqual(sets["Mega Floette"]["item"], "Choice Scarf")
        self.assertEqual(sets["Mega Floette"]["moves"],
                         ["Moonblast", "Aromatherapy", "Wish", "Protect"])

    def test_floette_eternal_with_no_item_still_resolves(self):
        from species_data import custom_team_from_export
        if "Mega Floette" not in self.merged:
            self.skipTest("Mega Floette not in this dataset")
        text = "Floette-Eternal\n- Moonblast"
        roster, _sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, ["Mega Floette"])

    def test_a_whole_team_round_trips_in_order(self):
        from species_data import custom_team_from_export, team_to_showdown_export
        names = ["Incineroar", "Gallade", "Whimsicott"]
        text = team_to_showdown_export(names, {}, self.merged)
        self.assertEqual(len(text.split("\n\n")), 3)
        roster, _sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, names)

    def test_ev_zero_stats_are_omitted_from_the_evs_line(self):
        from species_data import team_to_showdown_export
        spec = {"Gallade": {"evs": {"hp": 0, "atk": 32, "def": 0, "spa": 0,
                                    "spd": 0, "spe": 34}}}
        text = team_to_showdown_export(["Gallade"], spec, self.merged)
        evs_line = next(l for l in text.splitlines() if l.startswith("EVs:"))
        self.assertNotIn("0 HP", evs_line)
        self.assertNotIn("0 Def", evs_line)
        self.assertIn("32 Atk", evs_line)
        self.assertIn("34 Spe", evs_line)


class TestResolveExportFields(unittest.TestCase):
    """The per-member field resolver `team_to_showdown_export` itself uses
    -- fills anything a `spec` doesn't say the same way
    `combatants._build_combatant` would, so a caller (an xlsx 'Sets'
    column, say) reading it directly gets values consistent with what was
    actually simulated."""

    @classmethod
    def setUpClass(cls):
        cls.W = world()
        cls.merged = cls.W["merged"]

    def test_an_explicit_item_and_moves_override_usage_defaults(self):
        from species_data import resolve_export_fields
        f = resolve_export_fields(
            "Gallade", {"item": "Life Orb", "moves": ["Psycho Cut"]}, self.merged)
        self.assertEqual(f["species"], "Gallade")
        self.assertEqual(f["item"], "Life Orb")
        self.assertEqual(f["moves"], ["Psycho Cut"])
        # Nature/EVs/ability weren't overridden -- usage defaults.
        self.assertEqual(f["nature"], self.merged["Gallade"]["nature"])
        self.assertEqual(f["evs"], self.merged["Gallade"]["evs"])

    def test_no_spec_at_all_falls_back_to_every_usage_default(self):
        from species_data import resolve_export_fields
        f = resolve_export_fields("Incineroar", None, self.merged)
        rec = self.merged["Incineroar"]
        self.assertEqual(f["nature"], rec["nature"])
        self.assertEqual(f["evs"], rec["evs"])
        self.assertEqual(f["moves"], [m for m, _ in rec["moves_usage"][:4]])


class TestRegulationMCAdditions(unittest.TestCase):
    """"There are new mons coming to the new regulation M-C ... with sample
    moves" -- Rillaboom, Baxcalibur, and the three Mega-Z formes (Mega
    Absol Z, Mega Garchomp Z, Mega Lucario Z). The three Mega-Z formes were
    already fully statted in the bundled pokedex; what was actually missing
    was two real bugs that silently broke them (`base_form_name` only
    stripped a trailing "X"/"Y", not "Z"; mega-stone detection didn't
    recognize the "...ite Z" naming Regulation M-C had to use since
    "Garchompite"/"Lucarionite" were already claimed by the non-Z megas) --
    these tests pin both fixes plus that the pool actually reaches them.
    """

    @classmethod
    def setUpClass(cls):
        cls.W = world()
        cls.merged = cls.W["merged"]

    def test_base_form_name_strips_a_trailing_z(self):
        from species_data import base_form_name
        self.assertEqual(base_form_name("Mega Garchomp Z"), "Garchomp")
        self.assertEqual(base_form_name("Mega Absol Z"), "Absol")
        self.assertEqual(base_form_name("Mega Lucario Z"), "Lucario")

    def test_base_form_name_still_strips_x_and_y(self):
        """The fix widened the trailing-token check, not replaced it."""
        from species_data import base_form_name
        self.assertEqual(base_form_name("Mega Charizard Y"), "Charizard")
        self.assertEqual(base_form_name("Mega Charizard X"), "Charizard")

    def test_base_form_name_a_plain_mega_is_unaffected(self):
        from species_data import base_form_name
        self.assertEqual(base_form_name("Mega Skarmory"), "Skarmory")

    def test_ite_z_is_recognized_as_a_mega_stone(self):
        from species_data import is_mega_stone_name
        for stone in ("Garchompite Z", "Absolite Z", "Lucarionite Z"):
            self.assertTrue(is_mega_stone_name(stone), stone)

    def test_plain_ite_stones_are_still_recognized(self):
        """The fix widened the shared check, not replaced it -- the three
        previously-duplicated call sites (`find_mega_stone`,
        `load_mbsmogon`'s dedup, `damage.py`'s Knock Off exemption) all now
        share this one function."""
        from species_data import is_mega_stone_name
        for stone in ("Garchompite", "Lucarionite", "Charizardite X",
                     "Charizardite Y"):
            self.assertTrue(is_mega_stone_name(stone), stone)

    def test_an_ordinary_item_is_not_a_mega_stone(self):
        from species_data import is_mega_stone_name
        self.assertFalse(is_mega_stone_name("Life Orb"))

    def test_mega_garchomp_z_round_trips_through_a_showdown_export(self):
        from species_data import custom_team_from_export, team_to_showdown_export
        text = team_to_showdown_export(["Mega Garchomp Z"], {}, self.merged)
        self.assertTrue(text.startswith("Garchomp @ Garchompite Z")
                        or text.startswith("Mega Garchomp Z"))
        roster, _sets = custom_team_from_export(text, self.merged)
        self.assertEqual(roster, ["Mega Garchomp Z"])

    def test_all_five_new_mons_resolve_with_the_given_stats(self):
        """The user's own stat table, cross-checked against what
        `build_merged_dataset` actually resolves each name to."""
        expected = {
            "Mega Absol Z": (dict(hp=65, atk=154, defe=60, spa=75, spd=60, spe=151),
                             ["Dark", "Ghost"]),
            "Mega Garchomp Z": (dict(hp=108, atk=130, defe=85, spa=141, spd=85, spe=151),
                               ["Dragon"]),
            "Mega Lucario Z": (dict(hp=70, atk=100, defe=70, spa=164, spd=70, spe=151),
                              ["Fighting", "Steel"]),
            "Rillaboom": (dict(hp=100, atk=125, defe=90, spa=60, spd=70, spe=85),
                         ["Grass"]),
            "Baxcalibur": (dict(hp=115, atk=145, defe=92, spa=75, spd=86, spe=87),
                          ["Dragon", "Ice"]),
        }
        for name, (stats, types) in expected.items():
            self.assertIn(name, self.merged, name)
            rec = self.merged[name]
            base = rec["base_stats"]
            self.assertEqual(base["hp"], stats["hp"], name)
            self.assertEqual(base["atk"], stats["atk"], name)
            self.assertEqual(base["def"], stats["defe"], name)
            self.assertEqual(base["spa"], stats["spa"], name)
            self.assertEqual(base["spd"], stats["spd"], name)
            self.assertEqual(base["spe"], stats["spe"], name)
            self.assertEqual(rec["types"], types, name)

    def test_all_five_carry_their_given_ability(self):
        expected = {
            "Mega Absol Z": "Sharpness",
            "Mega Garchomp Z": "Levitate",
            "Mega Lucario Z": "Aura Break",
            "Rillaboom": "Grassy Surge",
            "Baxcalibur": "Thermal Exchange",
        }
        for name, ability in expected.items():
            abilities = dict(self.merged[name]["abilities_usage"])
            self.assertIn(ability, abilities, name)

    def test_all_five_have_a_score_and_are_not_silently_excluded(self):
        """Without a `roster.csv` row, `score` is `None` and
        `team_search.build_candidate_pool` silently drops the mon from
        every pool-based search -- this is the actual "added to the pool"
        requirement, not just "resolves with the right stats"."""
        from team_search import build_candidate_pool
        names = ["Mega Absol Z", "Mega Garchomp Z", "Mega Lucario Z",
                 "Rillaboom", "Baxcalibur"]
        for name in names:
            self.assertIsNotNone(self.merged[name].get("score"), name)
        pool = build_candidate_pool(self.merged, top_n=1000)
        for name in names:
            self.assertIn(name, pool, name)


if __name__ == "__main__":
    unittest.main()
