"""Who plays the games a record is counted from (WORKFLOW.md §4.4).

The win count has always been played by the greedy pilot and the punish audit by
the equilibrium one, so the two headline numbers describe two different pairs of
players. `pilot` makes that choice explicit. These tests pin the DISPATCH -- a
pilot that is silently ignored is worse than no option at all, because it
produces a number labelled as something it is not -- and leave the size of the
disagreement to tools/measure_pilot_gap.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import generate_team  # noqa: E402
import matchup_search  # noqa: E402


class _Recorder:
    """Stands in for search_robust_composition and records how it was called."""

    def __init__(self):
        self.calls = []

    def __call__(self, our_pool6, enemy_roster, *args, **kwargs):
        self.calls.append(kwargs)
        return [{"our_bring4": list(our_pool6)[:4], "solver_wins": 88,
                 "solver_total": 90, "solver_losses": [],
                 "pilot": kwargs.get("pilot", "greedy")}]


class TestPilotReachesTheSearch(unittest.TestCase):

    def setUp(self):
        self.original = matchup_search.search_robust_composition
        matchup_search.search_robust_composition = _Recorder()
        self.teams = {"Rain": ["E1", "E2", "E3", "E4", "E5", "E6"]}
        self.team = ["A", "B", "C", "D", "E", "F"]

    def tearDown(self):
        matchup_search.search_robust_composition = self.original

    def _verify(self, **kwargs):
        out = generate_team.verify_with_solver(
            self.team, self.teams, {}, {}, {}, {}, {}, [], max_turns=8,
            all_backs=True, effort="quick", **kwargs)
        return matchup_search.search_robust_composition.calls[0], out

    def test_the_default_is_unchanged(self):
        """Every record ever exported was played greedily. Not passing a pilot
        must keep asking for exactly that, rather than newly forcing one."""
        call, _out = self._verify()
        self.assertNotIn("pilot", call)

    def test_an_explicit_pilot_is_passed_down(self):
        call, _out = self._verify(pilot=matchup_search.EQUILIBRIUM_PILOT)
        self.assertEqual(call["pilot"], matchup_search.EQUILIBRIUM_PILOT)

    def test_the_record_says_who_played_it(self):
        _call, out = self._verify(pilot=matchup_search.EQUILIBRIUM_PILOT)
        self.assertEqual(out["Rain"]["pilot"], matchup_search.EQUILIBRIUM_PILOT)


class TestPilotIsPartOfTheCacheKey(unittest.TestCase):

    def test_two_pilots_are_two_different_answers(self):
        import roster_rating
        team = ["A", "B", "C", "D", "E", "F"]
        greedy = roster_rating.rating_key(team, "standard", 10, None)
        equilibrium = roster_rating.rating_key(
            team, "standard", 10, None, pilot=matchup_search.EQUILIBRIUM_PILOT)
        self.assertNotEqual(greedy, equilibrium)
        # ...and asking for the default explicitly must not fork the cache.
        self.assertEqual(greedy, roster_rating.rating_key(team, "standard", 10,
                                                          None, pilot=None))


class TestEquilibriumPilotPlaysBothSides(unittest.TestCase):
    """The point of the equilibrium pilot is that ONE solve produces both
    plays. Taking our action from the equilibrium and leaving theirs to the
    greedy model would be a third pilot that matches neither number."""

    def test_both_actions_come_from_the_same_solve(self):
        calls = []

        class FakeSolution:
            our_actions = ["ours-a", "ours-b"]
            their_actions = ["theirs-a", "theirs-b"]
            # BOTH degenerate, so the assertion below is about WHICH
            # distribution each seat reads rather than about a sampling draw.
            # This used to leave q mixed and assert their MODAL action, which
            # pinned the very asymmetry that made records self-contradictory --
            # our seat sampled its mixture while theirs played a pure strategy.
            # See tests/test_mirror_consistency.py for the measurements.
            p = [0.0, 1.0]
            q = [0.0, 1.0]
            best_action = "ours-b"

        import turn_game
        original = turn_game.solve_turn

        def fake_solve_turn(battle, side, movesets, **kwargs):
            calls.append(side)
            return FakeSolution()

        turn_game.solve_turn = fake_solve_turn
        try:
            ours, theirs = matchup_search._equilibrium_joint_actions(
                object(), {})
        finally:
            turn_game.solve_turn = original

        self.assertEqual(calls, ["p1"])          # one solve, not two
        self.assertEqual(ours, "ours-b")         # our side, from the mixture
        self.assertEqual(theirs, "theirs-b")     # from THEIR mixture, same rule

    def test_a_dead_position_yields_no_actions_rather_than_raising(self):
        class Empty:
            our_actions = []
            their_actions = []
            p = q = []
            best_action = None

        import turn_game
        original = turn_game.solve_turn
        turn_game.solve_turn = lambda *a, **k: Empty()
        try:
            self.assertEqual(
                matchup_search._equilibrium_joint_actions(object(), {}),
                (None, None))
        finally:
            turn_game.solve_turn = original


if __name__ == "__main__":
    unittest.main()
