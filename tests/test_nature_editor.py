"""Nature has to be editable, and a genuine speed tie has to stay a tie.

    "I need to be able to change nature. If two mons of the same speed have
     different natures, the one with the speed boosting nature may
     automatically win."

`combatants.make_combatant`/`make_team` have always honoured a per-Pokemon
nature override (`spec.get("nature")`, the same `sets` field the item/
ability/move editors already write into) -- there was simply no UI for it.
Mirrors the item/ability editors' own test convention (`test_app_side_sets.
py`): drive the real app headless and read the rendered/session state.

The second half of the report is a worry, not (as it turns out) a bug:
`engine.find_speed_ties`/`turn_order` compare the final computed speed stat
only, which is where nature already lives once a Combatant is built -- a
genuine tie (equal final stat) still breaks arbitrarily, never toward
whoever's nature happens to favour Speed. Covered here directly against
`engine.py`, not just asserted.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
TEAM = ["Garchomp", "Incineroar", "Gallade", "Hydreigon", "Whimsicott", "Kingambit"]

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def app(sets=None, team=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180)
    at.session_state["team"] = list(team or TEAM)
    at.session_state["sets"] = dict(sets or {})
    return at.run()


class TestNatureEditorUI(unittest.TestCase):

    def test_expander_offers_every_nature_for_every_team_member(self):
        at = app()
        self.assertEqual(len(at.exception), 0)
        exp = next(e for e in at.expander if e.label == "Edit natures")
        sbs = [sb for sb in exp.selectbox if sb.label == "Nature"]
        self.assertEqual(len(sbs), len(TEAM))
        self.assertEqual(set(sbs[0].options), set(n.capitalize() for n in world()["natures"]))

    def test_default_shown_is_the_usage_nature(self):
        at = app()
        exp = next(e for e in at.expander if e.label == "Edit natures")
        sb = next(sb for sb in exp.selectbox if sb.label == "Nature" and sb.key == "nature_Garchomp")
        self.assertEqual(sb.value, world()["merged"]["Garchomp"]["nature"])

    def test_a_pinned_nature_is_shown_over_the_usage_default(self):
        at = app(sets={"Garchomp": {"nature": "Timid"}})
        exp = next(e for e in at.expander if e.label == "Edit natures")
        sb = next(sb for sb in exp.selectbox if sb.label == "Nature" and sb.key == "nature_Garchomp")
        self.assertEqual(sb.value, "Timid")

    def test_apply_writes_the_choice_into_session_state_sets(self):
        at = app()
        exp = next(e for e in at.expander if e.label == "Edit natures")
        sb = next(sb for sb in exp.selectbox if sb.label == "Nature" and sb.key == "nature_Garchomp")
        sb.set_value("Timid").run()
        exp = next(e for e in at.expander if e.label == "Edit natures")
        apply_btn = next(b for b in exp.button if b.label == "Apply natures")
        apply_btn.click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertEqual(at.session_state["sets"]["Garchomp"]["nature"], "Timid")

    def test_reset_clears_a_pinned_nature(self):
        at = app(sets={"Garchomp": {"nature": "Timid", "item": "Choice Scarf"}})
        exp = next(e for e in at.expander if e.label == "Edit natures")
        reset_btn = next(b for b in exp.button if b.label == "Reset to usage defaults")
        reset_btn.click().run()
        self.assertEqual(len(at.exception), 0)
        # The OTHER pinned field on the same mon must survive -- reset only
        # touches "nature", not the whole spec.
        self.assertNotIn("nature", at.session_state["sets"].get("Garchomp", {}))
        self.assertEqual(at.session_state["sets"]["Garchomp"]["item"], "Choice Scarf")


class TestGenuineSpeedTiesAreNatureAgnostic(unittest.TestCase):
    """The worry behind the report, checked directly against the engine
    that actually resolves turn order."""

    def test_equal_final_speed_is_a_real_tie_not_a_nature_win(self):
        from combatants import make_combatant
        from engine import FieldState, effective_speed, find_speed_ties
        from damage import MoveInfo
        from engine import Action
        W = world()
        # Same species, same EVs -- only the nature differs (one Speed-
        # positive, one neutral) -- so if nature leaked into the comparison
        # itself, this would show as unequal even when we force the RAW
        # stat to match by construction below.
        a = make_combatant("Garchomp", W["merged"], W["natures"], nature="Jolly")
        b = make_combatant("Garchomp", W["merged"], W["natures"], nature="Hardy")
        # Force a genuine tie the way the real game can produce one (e.g.
        # a slower nature offset by a few more Speed EVs) -- what matters
        # for the test is that the FINAL stat is equal, regardless of how.
        b.stats["spe"] = a.stats["spe"]
        field = FieldState()
        self.assertEqual(effective_speed(a, field, "p1"), effective_speed(b, field, "p2"))

        mv = MoveInfo("Tackle", 40, "Normal", "Physical", "normal")
        act_a = Action(a, "p1", "move", mv, [b])
        act_b = Action(b, "p2", "move", mv, [a])
        ties = find_speed_ties([act_a, act_b], field)
        self.assertEqual(len(ties), 1, "an equal final Speed stat must be "
                                       "reported as a genuine tie")


if __name__ == "__main__":
    unittest.main()
