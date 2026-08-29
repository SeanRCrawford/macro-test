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


if __name__ == "__main__":
    unittest.main()
