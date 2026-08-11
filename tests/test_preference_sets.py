"""Pinning a set in preferences.csv: "Mamoswine (Life Orb)".

Asked for directly: a way to say not just WHICH Pokemon but which set, so
"Mega Gengar (Substitute, Protect, Shadow Ball, Sludge Bomb)" is a thing the
file can express. One bracket syntax covers items and moves, because each part
is classified by whether the dataset has ever seen it as an item.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from species_data import load_preferences, parse_preference_entry  # noqa: E402


class TestParsing(unittest.TestCase):

    def test_an_item(self):
        self.assertEqual(parse_preference_entry("Mamoswine (Life Orb)"),
                         ("Mamoswine", {"item": "Life Orb"}))

    def test_moves(self):
        name, spec = parse_preference_entry(
            "Mega Gengar (Substitute, Protect, Shadow Ball, Sludge Bomb)")
        self.assertEqual(name, "Mega Gengar")
        self.assertEqual(spec["moves"],
                         ["Substitute", "Protect", "Shadow Ball", "Sludge Bomb"])
        self.assertNotIn("item", spec)

    def test_both_at_once(self):
        _n, spec = parse_preference_entry(
            "Mega Gengar (Life Orb, Substitute, Protect)")
        self.assertEqual(spec["item"], "Life Orb")
        self.assertEqual(spec["moves"], ["Substitute", "Protect"])

    def test_a_plain_name_is_untouched(self):
        self.assertEqual(parse_preference_entry("Gallade"), ("Gallade", {}))
        self.assertEqual(parse_preference_entry("  Hydreigon  "),
                         ("Hydreigon", {}))

    def test_the_name_is_what_the_pool_matches_on(self):
        """The bracket must not leak into the species name, or the entry
        silently matches nothing and the preference is ignored."""
        name, _spec = parse_preference_entry("Mamoswine (Life Orb)")
        from species_data import build_merged_dataset
        merged, *_ = build_merged_dataset()
        self.assertIn(name, merged)


class TestLoading(unittest.TestCase):

    def test_the_shipped_file_still_loads_with_the_new_key(self):
        prefs = load_preferences()
        self.assertEqual(set(prefs),
                         {"include", "exclude", "prefer", "sets"})
        self.assertIsInstance(prefs["sets"], dict)

    def test_names_reach_the_lists_without_their_brackets(self):
        import pandas as pd
        import species_data
        original = pd.read_csv

        def fake(path, *a, **k):
            return pd.DataFrame({"Include": ["Mamoswine (Life Orb)"],
                                 "Exclude": ["Slurpuff"],
                                 "Prefer": ["Mega Gengar (Substitute)"]})

        pd.read_csv = fake
        try:
            prefs = species_data.load_preferences()
        finally:
            pd.read_csv = original
        self.assertEqual(prefs["include"], ["Mamoswine"])
        self.assertEqual(prefs["prefer"], ["Mega Gengar"])
        self.assertEqual(prefs["sets"]["Mamoswine"], {"item": "Life Orb"})
        self.assertEqual(prefs["sets"]["Mega Gengar"], {"moves": ["Substitute"]})


class TestItReachesTheSimulation(unittest.TestCase):

    def test_a_pinned_item_and_moveset_are_what_gets_built(self):
        from _harness import load_world
        from combatants import make_team
        from solver import TOP_K_MOVES, build_moveset
        w = load_world()
        sets = {
            "Mega Gengar": parse_preference_entry(
                "Mega Gengar (Substitute, Protect, Shadow Ball, "
                "Sludge Bomb)")[1],
            "Mamoswine": parse_preference_entry("Mamoswine (Life Orb)")[1],
        }
        team = make_team(["Mega Gengar", "Mamoswine", "Incineroar", "Hydreigon"],
                         w["merged"], w["natures"], sets=sets)
        by_name = {c.name: c for c in team}
        self.assertEqual(by_name["Mamoswine"].item, "Life Orb")
        moves = build_moveset(w["merged"]["Mega Gengar"], w["moves"],
                              top_k=TOP_K_MOVES,
                              only_moves=sets["Mega Gengar"]["moves"])
        self.assertEqual([m.name for m, _ in moves],
                         ["Substitute", "Protect", "Shadow Ball", "Sludge Bomb"])

    def test_a_mega_keeps_its_stone_over_a_pinned_item(self):
        """A Mega without its stone is not a Mega. The stone wins."""
        from _harness import load_world
        from combatants import make_team
        w = load_world()
        team = make_team(["Mega Gengar", "Incineroar"], w["merged"],
                         w["natures"], sets={"Mega Gengar": {"item": "Life Orb"}})
        self.assertTrue(team[0].item.endswith("ite"), team[0].item)


class TestGenerationAppliesThem(unittest.TestCase):

    def test_pinned_sets_are_applied_after_the_optimiser(self):
        """Order matters: the optimiser decides all six, and a pinned set has to
        override its choice rather than be overwritten by it."""
        source = open(os.path.join(os.path.dirname(__file__), "..", "tools",
                                   "generate_overnight.py"),
                      encoding="utf-8").read()
        optimise_at = source.index("teams optimised")
        pin_at = source.index('pinned = load_preferences()')
        self.assertLess(optimise_at, pin_at,
                        "pinned sets are applied before optimisation, so the "
                        "optimiser would overwrite them")


if __name__ == "__main__":
    unittest.main()
