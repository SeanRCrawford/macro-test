"""The pins have to reach the screen, not just the library.

`preview_lead.line_for` attaching a "pins" key is worth nothing if the Team
Preview expander never prints it, and "I added the markdown" is not evidence --
the panel is gated behind two session_state results and a `complete` entry, so
it is entirely possible to add rendering code that no state ever reaches.

These drive the real app headless and read the rendered output.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")

OURS = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf",
        "Pelipper", "Archaludon"]
PIN_TEXT = ("Mega Charizard Y pins Mega Swampert with Solar Beam "
            "(guaranteed OHKO, moves first) -- Mega Swampert must switch or Protect")

LINE = {
    "their_lead": ["Pelipper", "Mega Swampert"],
    "their_bring": ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"],
    "outcome": "win", "length": 10,
    "win_prob": 1.0, "win_interval": (0.68, 1.0), "win_games": 8,
    "final_margin": 4.0,
    "pins": [PIN_TEXT,
             "Speed order Garchomp > Mega Charizard Y > Pelipper "
             "(pinned, so not counted: Mega Swampert)"],
    "turns": [{"turn": 1, "play": "Mega Charizard Y Solar Beam -> Mega Swampert",
               "their_reply": "Mega Swampert Protect",
               "kinds": ["attack", "protect"], "their_kinds": ["protect", "protect"],
               "punish": 0.0, "kos": [], "events": []}],
}

ENTRY = {"lead": ["Mega Charizard Y", "Garchomp"], "bring": OURS[:4],
         "guaranteed": 55.0, "worst_vs": ["Pelipper", "Mega Swampert"],
         "answer": "attack", "punish": 12.0, "complete": True}


def app_showing_a_line():
    """The app, driven to the state where a played line is on screen."""
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=900)
    at.session_state["team"] = list(OURS)
    at.session_state["sets"] = {}
    at.session_state["pv_fast_result"] = ([ENTRY], {
        "solves": 12, "seconds": 20.0, "complete": 1,
        "our_leads": 15, "their_leads": 15})
    at.session_state["pv_lines_result"] = ([LINE], {
        "seconds": 30.0, "played": 1, "of": 15})
    return at.run()


def rendered(at):
    """Everything the app printed, as one string."""
    out = []
    for block in (at.markdown, at.caption, at.success, at.info, at.warning):
        out.extend(el.value for el in block)
    return "\n".join(out)


class TestThePinsAreOnScreen(unittest.TestCase):

    def setUp(self):
        self.at = app_showing_a_line()

    def test_the_app_runs_at_all(self):
        self.assertFalse(self.at.exception, self.at.exception)

    def test_the_pin_sentence_is_rendered(self):
        self.assertIn("pins Mega Swampert", rendered(self.at))

    def test_it_says_what_the_pinned_pokemon_must_do(self):
        self.assertIn("must switch or Protect", rendered(self.at))

    def test_the_reduced_speed_order_is_rendered(self):
        text = rendered(self.at)
        self.assertIn("Speed order", text)
        self.assertIn("pinned, so not counted", text)

    def test_the_line_itself_is_still_there(self):
        """Guards against the pins block swallowing the turns below it."""
        self.assertIn("Solar Beam", rendered(self.at))


class TestALineWithoutPinsStillRenders(unittest.TestCase):
    """Old cached results, and quiet positions, have no "pins" key at all."""

    def test_no_pins_key_is_not_an_error(self):
        from streamlit.testing.v1 import AppTest
        line = {k: v for k, v in LINE.items() if k != "pins"}
        at = AppTest.from_file(APP, default_timeout=900)
        at.session_state["team"] = list(OURS)
        at.session_state["sets"] = {}
        at.session_state["pv_fast_result"] = ([ENTRY], {
            "solves": 12, "seconds": 20.0, "complete": 1,
            "our_leads": 15, "their_leads": 15})
        at.session_state["pv_lines_result"] = ([line], {
            "seconds": 30.0, "played": 1, "of": 15})
        at.run()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("Solar Beam", rendered(at))


class TestTheFullModelPanelShowsThemToo(unittest.TestCase):
    """The advanced model builds its lines through a different path, so its
    dicts carry no "pins" key -- the panel computes them from the two brings."""

    def app_with_a_plan(self):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(APP, default_timeout=900)
        at.session_state["team"] = list(OURS)
        at.session_state["sets"] = {}
        at.session_state["pv_plan"] = {
            "bring": ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl",
                      "Farigiraf"],
            "wins": 1, "of": 1, "punish": 10.0, "losing_to": [],
            "audited": True, "severe_turns": 0,
            "lines": [{
                "outcome": "win", "line_turns": 10,
                "mean_exploitability": 5.0,
                "enemy_bring": ["Pelipper", "Mega Swampert", "Archaludon",
                                "Grimmsnarl"],
                "turns": [],
            }],
        }
        return at.run()

    def test_the_pin_is_computed_and_shown(self):
        at = self.app_with_a_plan()
        self.assertFalse(at.exception, at.exception)
        self.assertIn("pins Mega Swampert", rendered(at))

    def test_it_explains_what_a_pin_is(self):
        """The vocabulary is new, so the panel says what the word means rather
        than assuming it."""
        self.assertIn("cannot profitably stay in and attack",
                      rendered(self.app_with_a_plan()))


class TestTheFixReachesEveryPanel(unittest.TestCase):
    """The projection lives in candidate generation, so it applies to every
    panel that solves a turn -- not only the one that prints pins. This checks
    the shared entry point rather than each panel."""

    def test_candidate_generation_offers_the_charge_move(self):
        from _harness import load_world, setup_battle
        from solver import our_candidate_joint_actions
        world = load_world()
        battle, movesets = setup_battle(
            ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"],
            ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"], world)
        joints = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                             movesets, battle.turn_num + 1)
        moves = {a.move.name for j in joints for a in j
                 if a.kind == "move" and a.move is not None}
        self.assertIn("Solar Beam", moves)


if __name__ == "__main__":
    unittest.main()
