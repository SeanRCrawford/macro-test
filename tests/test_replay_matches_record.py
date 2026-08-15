"""A replay has to reproduce the record, or the screen contradicts itself.

Reported: in the Lead/Back "Vs Team" section, battles headed "LOSS — vs lead
..." show "Avg-roll result: WIN" inside. Two numbers about one game, on one
screen, disagreeing with nothing to explain it.

Neither was random. The header comes from the stored search; the metric comes
from replaying the config live. `play_scripted_worst_case` took no `pilot`
argument, so every replay ran the GREEDY pilot -- while a Thorough+ record had
been made by the EQUILIBRIUM one, which is a genuinely stronger opponent. The
replay won games the record lost.

Which one was wrong is worth stating, because the report assumed the header
was: the header was RIGHT. The replay was playing an easier opponent. Same
defect as WORKFLOW 0d ("the line and the win rate used to play different
opponents"), one layer up.

The second half of the same bug: the search's own scripted branch never passed
`pilot` either, so picking Thorough+ against a scripted opponent produced a
greedy record that the record then labelled `"pilot": "equilibrium"`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from matchup_search import (EQUILIBRIUM_PILOT, GREEDY_PILOT,  # noqa: E402
                            play_out_worst_case, play_scripted_worst_case)

_WORLD = None
OUR4 = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"]
# The exact config the disagreement was reproduced on.
EB4 = ["Pelipper", "Archaludon", "Mega Swampert", "Garchomp"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def record(pilot, our4=OUR4, eb4=EB4):
    """What the search stores: the unscripted branch of search_robust_composition."""
    w = world()
    winner, _t, _b = play_out_worst_case(
        list(our4), list(eb4), w["merged"], w["moves"], w["natures"],
        w["typechart"], 12, pilot=pilot)
    return winner


def replay(pilot, our4=OUR4, eb4=EB4, script_team=None):
    """What the app shows in the expander: app.replay_config."""
    w = world()
    winner, _t, _b, _v = play_scripted_worst_case(
        list(our4), list(eb4), w["merged"], w["moves"], w["natures"],
        w["typechart"], script_team, 12, pilot=pilot)
    return winner


class TestTheReplayReproducesTheRecord(unittest.TestCase):

    def test_they_agree_under_the_greedy_pilot(self):
        self.assertEqual(record(GREEDY_PILOT), replay(GREEDY_PILOT))

    def test_they_agree_under_the_equilibrium_pilot(self):
        self.assertEqual(record(EQUILIBRIUM_PILOT), replay(EQUILIBRIUM_PILOT))

    def test_this_config_is_one_where_the_pilots_genuinely_differ(self):
        """Without this the two tests above could pass on a position where every
        pilot wins, and would prove nothing at all."""
        self.assertNotEqual(record(GREEDY_PILOT), record(EQUILIBRIUM_PILOT))

    def test_the_equilibrium_pilot_is_the_one_that_loses_here(self):
        """Naming the direction: the header said LOSS and was right; the replay
        said WIN because it faced a weaker opponent."""
        self.assertEqual(record(EQUILIBRIUM_PILOT), "p2")
        self.assertEqual(record(GREEDY_PILOT), "p1")


class TestTheScriptedBranchTakesAPilotToo(unittest.TestCase):

    def test_play_scripted_worst_case_accepts_a_pilot(self):
        import inspect
        self.assertIn("pilot",
                      inspect.signature(play_scripted_worst_case).parameters)

    def test_it_defaults_to_greedy_so_old_callers_are_unchanged(self):
        import inspect
        param = inspect.signature(play_scripted_worst_case).parameters["pilot"]
        self.assertEqual(param.default, GREEDY_PILOT)

    def test_the_pilot_actually_changes_the_result(self):
        """Guards against the argument being accepted and then dropped on the
        floor -- which is exactly what the bug looked like from outside."""
        self.assertNotEqual(replay(GREEDY_PILOT), replay(EQUILIBRIUM_PILOT))

    def test_the_search_passes_its_pilot_to_the_scripted_branch(self):
        """Read from the source, because reaching this branch needs a scripted
        opponent and a full verify pass."""
        import re
        path = os.path.join(os.path.dirname(__file__), "..", "src",
                            "matchup_search.py")
        body = open(path).read()
        call = re.search(r"play_scripted_worst_case\(\s*\n\s*cand\[.our_bring4.\]"
                         r".*?\)", body, re.S)
        self.assertIsNotNone(call, "verify-pass call site not found")
        self.assertIn("pilot=pilot", call.group(0))


class TestTheAppReplayHelper(unittest.TestCase):

    def helper(self):
        """app.replay_config without importing streamlit's page body."""
        import inspect
        path = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
        body = open(path).read()
        self.assertIn("def replay_config(", body)
        start = body.index("def replay_config(")
        return body[start:start + 2000]

    def test_it_reads_the_pilot_from_the_record(self):
        """Not from the sidebar: the sidebar can have been changed since, and a
        replay reproduces a past result, not the current setting."""
        src = self.helper()
        self.assertIn('.get("pilot")', src)

    def test_it_falls_back_to_greedy_for_records_that_predate_the_field(self):
        self.assertIn("or GREEDY_PILOT", self.helper())

    def test_both_expanders_use_it(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
        body = open(path).read()
        self.assertEqual(body.count("replay_config("), 3)   # def + two call sites

    def test_a_disagreement_is_reported_rather_than_shown_silently(self):
        path = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
        body = open(path).read()
        self.assertIn("def replay_mismatch_note(", body)
        self.assertEqual(body.count("replay_mismatch_note("), 3)


if __name__ == "__main__":
    unittest.main()
