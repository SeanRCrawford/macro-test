"""Upload a team as a pokepaste.

    "in the streamlit app I want to upload one of my teams as a pokepaste.
     Either this will be a txt in a 'my_teams' folder in /data, or I will
     paste a pokepaste in the webapp."

Two paths, both covered here: dropping a .txt straight into data/my_teams/
(species_data.load_teams already scans data/teams/*.txt the same way; this
just adds a second, personal folder alongside it), and pasting into the
Streamlit app's "Our side" -> "Paste a pokepaste" control, which can also
save what you pasted back to data/my_teams/ so it shows up as "A saved
team" from then on.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")

PASTE = """Tyranitar @ Tyranitarite
Ability: Sand Stream
Level: 50
EVs: 17 HP / 18 Atk / 31 Spe
Jolly Nature
- Knock Off
- Rock Slide
- High Horsepower
- Protect

Excadrill @ Focus Sash
Ability: Sand Rush
Level: 50
EVs: 2 HP / 32 Atk / 32 Spe
Adamant Nature
- Earthquake
- Iron Head
- Rock Slide
- Protect

Milotic @ Sitrus Berry
Ability: Competitive
Level: 50
EVs: 20 HP / 20 Def / 4 SpA / 8 SpD / 14 Spe
Calm Nature
- Scald
- Icy Wind
- Haze
- Protect

Sinistcha @ Colbur Berry
Ability: Hospitality
Level: 50
EVs: 29 HP / 15 Def / 22 SpD
Bold Nature
- Matcha Gotcha
- Shadow Ball
- Protect
- Rage Powder

Staraptor @ Staraptite
Ability: Intimidate
Level: 50
EVs: 29 HP / 1 Atk / 4 SpD / 32 Spe
Jolly Nature
- Close Combat
- Brave Bird
- Tailwind
- Protect

Gholdengo @ Life Orb
Ability: Good as Gold
Level: 50
EVs: 27 HP / 13 Def / 16 SpD / 10 Spe
Modest Nature
- Make It Rain
- Shadow Ball
- Nasty Plot
- Protect
"""


def world():
    from _harness import load_world
    return load_world()


class TestMyTeamsFolder(unittest.TestCase):
    """A .txt dropped into data/my_teams/ shows up exactly like
    data/teams/*.txt already does, everywhere a saved team is used."""

    def setUp(self):
        from species_data import DATA_DIR
        self.path = DATA_DIR / "my_teams" / "test_upload_delete_me.txt"
        self.addCleanup(lambda: self.path.unlink(missing_ok=True))

    def test_a_dropped_in_pokepaste_is_parsed_into_a_team(self):
        from species_data import load_teams
        W = world()
        self.path.write_text(PASTE)
        teams, meta = load_teams(with_meta=True, merged=W["merged"])
        self.assertIn("Test Upload Delete Me", teams)
        self.assertEqual(
            teams["Test Upload Delete Me"],
            ["Mega Tyranitar", "Excadrill", "Milotic", "Sinistcha",
             "Mega Staraptor", "Gholdengo"])

    def test_the_parsed_sets_are_attached_in_meta(self):
        from species_data import load_teams
        W = world()
        self.path.write_text(PASTE)
        _teams, meta = load_teams(with_meta=True, merged=W["merged"])
        sets = meta["Test Upload Delete Me"]["sets"]
        self.assertEqual(sets["Mega Tyranitar"]["item"], "Tyranitarite")
        self.assertEqual(sets["Mega Tyranitar"]["ability"], "Sand Stream")
        self.assertIn("Knock Off", sets["Mega Tyranitar"]["moves"])

    def test_data_teams_and_data_my_teams_are_both_scanned(self):
        """The existing built-in library must not be dropped by adding the
        new personal folder alongside it."""
        from species_data import load_teams
        W = world()
        self.path.write_text(PASTE)
        teams = load_teams(merged=W["merged"])
        self.assertIn("Sand", teams)                    # data/teams/sand.txt
        self.assertIn("Test Upload Delete Me", teams)    # data/my_teams/...

    def test_a_my_teams_file_wins_a_name_collision_with_teams(self):
        """Documented in load_teams' own docstring: data/my_teams/ is
        processed second, so a same-named personal file overrides the
        built-in one -- on the theory that's deliberate."""
        from species_data import DATA_DIR, load_teams
        W = world()
        collide = DATA_DIR / "my_teams" / "sand.txt"
        collide.write_text(PASTE)  # a different roster than data/teams/sand.txt
        self.addCleanup(lambda: collide.unlink(missing_ok=True))
        teams, meta = load_teams(with_meta=True, merged=W["merged"])
        self.assertEqual(teams["Sand"],
                         ["Mega Tyranitar", "Excadrill", "Milotic",
                          "Sinistcha", "Mega Staraptor", "Gholdengo"])
        self.assertIn("My team", meta["Sand"]["note"])


class TestStreamlitPastePanel(unittest.TestCase):
    """The "Paste a pokepaste" option on `our_side_pool` -- same control the
    opponent side ("Vs Team") already had, now on our own side too."""

    def app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_file(APP, default_timeout=900).run()

    def test_paste_a_pokepaste_is_offered_as_an_our_side_option(self):
        at = self.app()
        r = next(x for x in at.radio if x.key == "bv_side_source")
        self.assertIn("Paste a pokepaste", r.options)

    def test_pasting_a_team_parses_it_and_reports_success(self):
        at = self.app()
        at.radio(key="bv_side_source").set_value("Paste a pokepaste").run()
        at.text_area(key="bv_our_paste").set_value(PASTE).run()
        self.assertFalse(list(at.exception))
        messages = " ".join(s.value for s in at.success)
        for name in ("Mega Tyranitar", "Excadrill", "Milotic", "Sinistcha",
                     "Mega Staraptor", "Gholdengo"):
            self.assertIn(name, messages)

    def test_an_unparseable_paste_reports_an_error_not_a_crash(self):
        at = self.app()
        at.radio(key="bv_side_source").set_value("Paste a pokepaste").run()
        at.text_area(key="bv_our_paste").set_value("Not A Real Pokemon @ Nonsense Item\n"
                                                    "- Fake Move").run()
        self.assertFalse(list(at.exception))
        self.assertTrue(list(at.error))

    def test_save_to_my_teams_writes_the_file_and_reloads_cleanly(self):
        from species_data import DATA_DIR
        saved = DATA_DIR / "my_teams" / "Apptest_Upload_Delete_Me.txt"
        self.addCleanup(lambda: saved.unlink(missing_ok=True))

        at = self.app()
        at.radio(key="bv_side_source").set_value("Paste a pokepaste").run()
        at.text_area(key="bv_our_paste").set_value(PASTE).run()
        at.text_input(key="bv_save_name").set_value("Apptest Upload Delete Me").run()
        at.button(key="bv_save_btn").click().run()

        self.assertFalse(list(at.exception))
        self.assertTrue(saved.exists())
        self.assertIn("Tyranitarite", saved.read_text())


if __name__ == "__main__":
    unittest.main()
