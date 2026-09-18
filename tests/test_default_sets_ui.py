"""The Team Builder tab's "Default sets" section.

    "let me create a 'default set' txt where I paste pokepastes for
     individual pokemon, and for enemies this should be the actual sets
     used by default (if no set or EVs specified)"

Confirmed with the user: data/default_sets.txt (editable via the app,
same Showdown-export-per-Pokemon format as data/teams/*.txt), applying
to BOTH our own side and enemies (baked directly into the shared
`merged` dataset -- see species_data.apply_default_sets). The FILE
itself now applies per-field (a hand-edited entry missing e.g. EVs
still overrides item/ability/nature/moves; only the missing field falls
back to mbsmogon usage) -- but the "Save default set(s)" button in this
tab still requires a WHOLE set (item/ability/nature/EVs/moves all
specified or the paste is rejected), since that's the one path meant to
be a full, deliberate statement of what a Pokemon runs.

These tests WRITE to the real data/default_sets.txt (the same file the
app itself reads/writes -- there is no test-only path to redirect it
to), so every test saves and restores whatever was already there.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")

COMPLETE_SET = (
    "Kingambit @ Chople Berry\nAbility: Supreme Overlord\n"
    "EVs: 4 HP / 252 Atk / 252 Spe\nAdamant Nature\n"
    "- Sucker Punch\n- Kowtow Cleave\n- Iron Head\n- Low Kick")

INCOMPLETE_SET = (  # no EVs line
    "Kingambit @ Chople Berry\nAbility: Supreme Overlord\n"
    "Adamant Nature\n- Sucker Punch\n- Kowtow Cleave\n- Iron Head\n- Low Kick")


def app():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=900)
    return at.run()


class TestDefaultSetsUI(unittest.TestCase):

    def setUp(self):
        import species_data
        self.path = species_data.DATA_DIR / "default_sets.txt"
        self._had_file = self.path.exists()
        self._original = (self.path.read_text(encoding="utf-8")
                          if self._had_file else None)
        if self.path.exists():
            self.path.unlink()

    def tearDown(self):
        if self._had_file:
            self.path.write_text(self._original, encoding="utf-8")
        elif self.path.exists():
            self.path.unlink()

    def test_the_section_is_offered(self):
        at = app()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(t.key == "default_sets_paste" for t in at.text_area))
        self.assertTrue(any(b.key == "default_sets_save" for b in at.button))

    def test_saving_a_complete_set_writes_the_file(self):
        """Mirrors `test_my_teams_upload.py`'s own "Save to My Teams" test
        -- the save handler ends with `st.rerun()` (same as that flow),
        so the transient `st.success(...)` banner belongs to the PRE-rerun
        script pass and is gone from the snapshot `.run()` hands back;
        what actually matters -- and survives -- is the file on disk."""
        at = app()
        ta = [t for t in at.text_area if t.key == "default_sets_paste"][0]
        at = ta.set_value(COMPLETE_SET).run()
        at = [b for b in at.button if b.key == "default_sets_save"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(self.path.exists())
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("Kingambit", text)
        self.assertIn("Chople Berry", text)

    def test_the_default_actually_takes_effect(self):
        at = app()
        ta = [t for t in at.text_area if t.key == "default_sets_paste"][0]
        at = ta.set_value(COMPLETE_SET).run()
        at = [b for b in at.button if b.key == "default_sets_save"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        # Re-derive `merged` fresh (session_state doesn't carry app.py's own
        # module-level `merged`) to confirm the save really changed what the
        # NEXT load actually reads, not just what the UI echoed back.
        from species_data import build_merged_dataset
        fresh, _u, _m, _n, _t = build_merged_dataset()
        self.assertEqual(fresh["Kingambit"]["items_usage"], [("Chople Berry", 100.0)])

    def test_an_incomplete_set_is_rejected_with_an_error(self):
        at = app()
        ta = [t for t in at.text_area if t.key == "default_sets_paste"][0]
        at = ta.set_value(INCOMPLETE_SET).run()
        at = [b for b in at.button if b.key == "default_sets_save"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Kingambit" in e.value for e in at.error))
        self.assertFalse(self.path.exists())

    def test_currently_set_species_are_listed_and_clearable(self):
        self.path.write_text(COMPLETE_SET, encoding="utf-8")
        at = app()
        self.assertTrue(any("Kingambit" in c.value for c in at.caption
                            if "Currently set" in c.value))
        clear_btn = [b for b in at.button if b.key == "default_sets_clear"][0]
        at = clear_btn.click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
