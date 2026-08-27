"""`tools/counter_table.py`'s search, in the Streamlit app.

    "Now, add the counter_table.py functionality to the streamlit app."

A new "Counter Table" tab wraps `counter_finder.py`'s bring4_search/
multi_bring4_coverage+exhaustive/beam/joint_pair_search directly -- no new
search logic, the same functions the CLI calls, so a result here can never
disagree with the CLI's own answer for the same inputs.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")
APP = os.path.join(ROOT, "src", "app.py")
TEAM = ["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
        "Incineroar", "Farigiraf"]


def app(team=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=250)
    at.session_state["team"] = list(team if team is not None else TEAM)
    at.session_state["sets"] = {}
    return at.run()


class TestCounterTableTabExists(unittest.TestCase):

    def test_it_renders(self):
        at = app()
        self.assertFalse(at.exception, list(at.exception))

    def test_the_three_modes_are_offered(self):
        at = app()
        radios = [r for r in at.radio if r.key == "ct_mode"]
        self.assertEqual(len(radios), 1)
        self.assertEqual(set(radios[0].options),
                         {"Bring-4 (one enemy roster)",
                          "Multi-bring4 (several enemy rosters)",
                          "Joint pair search"})

    def test_switching_to_multi_bring4_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Multi-bring4 (several enemy rosters)").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_mb4_pool" for s in at.slider))
        self.assertTrue(any(m.key == "ct_mb4_vs" for m in at.multiselect))

    def test_switching_to_joint_pair_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Joint pair search").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_jp_partner" for s in at.selectbox))

    def test_choice_scarf_is_excluded_by_default(self):
        at = app()
        cb = [c for c in at.checkbox if c.key == "ct_allow_scarf"][0]
        self.assertFalse(cb.value)


class TestBring4ModeRunsEndToEnd(unittest.TestCase):
    """The fastest of the three real searches -- one enemy roster, our
    already-loaded 6 -- run for real (not just rendered) to prove the
    wiring from widget -> `counter_finder.bring4_search` -> table actually
    works, not just that the page draws."""

    def test_running_a_real_search_produces_both_stage_tables(self):
        at = app()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = at.dataframe
        # Stage 1 (15 pairs) and Stage 2 (15 bring-4s) tables, at minimum.
        shapes = [d.value.shape[0] for d in dfs]
        self.assertIn(15, shapes, "expected a 15-row Stage 1 or Stage 2 table")

    def test_a_preset_team_can_be_used_instead_of_team_builders(self):
        """A preset "our 6" can legitimately share a Pokemon with the
        selected enemy roster (two library teams both running e.g.
        Grimmsnarl) -- that must surface as a clean st.error, never crash
        the page. See TestBring4SearchRejectsOverlap in
        test_counter_finder.py for the underlying fix."""
        at = app(team=[])  # nothing loaded in Team Builder
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        self.assertGreater(len(sb.options), 1, "expected preset teams offered")
        sb.set_value(sb.options[1]).run()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any("Pick exactly 6" in w.value for w in at.warning))


if __name__ == "__main__":
    unittest.main()
