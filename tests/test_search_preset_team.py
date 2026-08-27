""""I would also like one test option in the streamlit app; let me run a
given preset team(s) as the player the lead/back section. A team should
probably always perform better than 50/50 vs itself because it can pick the
optimal lead."

Before this, the Lead / Back Search tab's "our" side could only ever be
whatever is currently loaded in Team Builder (`get_state_team()`) -- testing
a library team meant loading it there first, and there was no direct way to
run a team against a copy of itself (the mirror-match sanity check the
request itself names: a team should beat itself more than 50% of the time,
since the lead is picked AFTER seeing the opponent's roster).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestSearchTabOffersAPresetTeam(unittest.TestCase):

    APP = os.path.join(ROOT, "src", "app.py")

    @classmethod
    def setUpClass(cls):
        from species_data import load_teams
        cls.preset_names = list(load_teams())

    def _run(self, team=None):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(self.APP, default_timeout=90)
        at.session_state["team"] = team or []
        at.session_state["sets"] = {}
        return at.run()

    def test_it_renders(self):
        at = self._run(["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
                        "Incineroar", "Farigiraf"])
        self.assertFalse(at.exception, list(at.exception))

    def test_the_selector_defaults_to_the_current_team_builder_team(self):
        at = self._run(["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
                        "Incineroar", "Farigiraf"])
        sb = [s for s in at.selectbox if s.key == "search_our_source"]
        self.assertEqual(len(sb), 1)
        self.assertEqual(sb[0].value, "(current Team Builder team)")

    def test_every_preset_team_is_offered(self):
        at = self._run(["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
                        "Incineroar", "Farigiraf"])
        sb = [s for s in at.selectbox if s.key == "search_our_source"][0]
        for name in self.preset_names:
            self.assertIn(name, sb.options)

    def test_picking_a_preset_team_works_without_a_team_builder_team(self):
        """The whole point: no team loaded in Team Builder must no longer
        block the tab once a preset is chosen as "our" side. Matched on the
        distinguishing suffix -- "Pick 6 Pokemon..." alone also appears in
        an unrelated tab's own independent Team-Builder check."""
        marker = "or choose a preset team above"
        at = self._run(team=[])  # nothing loaded in Team Builder
        warnings_before = " ".join(w.value for w in at.warning)
        self.assertIn(marker, warnings_before)

        sb = [s for s in at.selectbox if s.key == "search_our_source"][0]
        sb.set_value(self.preset_names[0])
        at = at.run()
        self.assertFalse(at.exception, list(at.exception))
        warnings_after = " ".join(w.value for w in at.warning)
        self.assertNotIn(marker, warnings_after)

    def test_the_opponents_list_still_includes_every_preset_for_a_mirror_test(self):
        """Selecting the SAME name as both "Test as" and an Opponent is how
        the mirror-match sanity check ("a team should beat itself more than
        50% of the time") is actually run -- so the preset must still be
        selectable as an opponent after being chosen as "our" side."""
        at = self._run(team=[])
        sb = [s for s in at.selectbox if s.key == "search_our_source"][0]
        sb.set_value(self.preset_names[0])
        at = at.run()
        self.assertFalse(at.exception, list(at.exception))
        opp = [m for m in at.multiselect if m.label == "Opponents"]
        self.assertEqual(len(opp), 1)
        self.assertIn(self.preset_names[0], opp[0].options)


if __name__ == "__main__":
    unittest.main()
