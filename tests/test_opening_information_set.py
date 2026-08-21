"""What you are allowed to know when you choose turn 1.

Three things were reported, and they are one thing: the opening was being
chosen with information the player does not have.

  "the scripted variants are WAY too tough - they have 6 pokemon always! Each
   side should only ever bring 4"

  "in Hard Trick Room, your response is different depending on which pokemon
   the opposing Incineroar fakes out, but it's a simultaneous game so you can't
   know ahead of time, and the whole point of script is you have to do the same
   no matter what"

  "I notice the openings change vs a given lead depending on what's in the back
   - YOU DONT KNOW WHAT'S IN THE BACK UNTIL IT COMES OUT"

So the information set at turn 1 is exactly: our four, our lead, their lead.
Not their backs, not their turn-1 action. One action comes out of it, and the
same action is played against everything.

The escape hatch is deliberate and also tested: you may BET on one back pair,
which changes what the action is chosen against and must not change what it is
reported against.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import committed_plan  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None
OURS = ["Gallade", "Hydreigon", "Gholdengo", "Mega Skarmory"]
TEAM = "Hard Trick Room"
LEAD = ["Incineroar", "Farigiraf"]


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def breakdown(**kw):
    w = world()
    return committed_plan.turn1_breakdown(
        OURS, w["teams"][TEAM], w["merged"], w["moves"], w["natures"], w["typechart"],
        TEAM, enemy_lead=LEAD, max_turns=6, **kw)


class TestEachSideBringsFour(unittest.TestCase):

    def test_their_six_becomes_the_bring_fours_their_lead_could_front(self):
        w = world()
        brings = committed_plan.enemy_brings(w["teams"][TEAM], LEAD)
        self.assertEqual(len(brings), 6)          # C(4,2)
        for b in brings:
            self.assertEqual(len(b), 4, b)
            self.assertEqual(b[:2], LEAD)

    def test_a_roster_of_four_is_already_a_bring(self):
        self.assertEqual(committed_plan.enemy_brings(["A", "B", "C", "D"]),
                         [["A", "B", "C", "D"]])

    def test_naming_a_back_pair_gives_exactly_that_bring(self):
        w = world()
        rest = [x for x in w["teams"][TEAM] if x not in LEAD]
        brings = committed_plan.enemy_brings(w["teams"][TEAM], LEAD,
                                             assumed_back=rest[:2])
        self.assertEqual(brings, [LEAD + rest[:2]])

    def test_the_breakdown_never_fields_more_than_four(self):
        """The reported bug, at the level it was actually reported: the panel
        handed the script all six."""
        bd = breakdown()
        self.assertTrue(bd["brings"])
        for b in bd["brings"]:
            self.assertEqual(len(b), 4, b)


class TestOneActionForEverything(unittest.TestCase):

    def setUp(self):
        self.bd = breakdown()

    def test_every_variant_plays_the_same_turn_one(self):
        """Before the fix this produced three different answers to the same
        information set: one for the script, one for their Protect line, one
        for their plain attack."""
        seen = {tuple(d["our_t1_action"]) for d in self.bd["variants"].values()}
        self.assertEqual(len(seen), 1, seen)

    def test_every_back_pair_plays_the_same_turn_one(self):
        seen = {tuple(r["our_t1_action"])
                for d in self.bd["variants"].values()
                for r in d["per_back"].values()}
        self.assertEqual(len(seen), 1, seen)

    def test_that_action_is_the_one_reported_as_the_opening(self):
        for d in self.bd["variants"].values():
            self.assertEqual(tuple(d["our_t1_action"]), tuple(self.bd["opening"]))

    def test_more_than_one_variant_and_back_pair_was_actually_tried(self):
        """Guards the tests above: they all pass trivially on a single game."""
        self.assertGreater(len(self.bd["variants"]), 1)
        for d in self.bd["variants"].values():
            self.assertEqual(len(d["per_back"]), 6)

    def test_the_worst_back_pair_is_the_headline(self):
        for d in self.bd["variants"].values():
            ranks = [committed_plan._loss_rank(r["winner"], r["turns"])
                     for r in d["per_back"].values()]
            self.assertEqual(committed_plan._loss_rank(d["winner"], d["turns"]),
                             max(ranks))


class TestTheBetIsAllowedButPriced(unittest.TestCase):

    def test_betting_on_a_back_pair_still_reports_all_of_them(self):
        """The bet is a judgement call about what they brought, not a licence to
        stop looking at the other five."""
        w = world()
        rest = [x for x in w["teams"][TEAM] if x not in LEAD]
        bet = breakdown(assumed_back=tuple(rest[:2]))
        self.assertEqual(len(bet["brings"]), 6)
        for d in bet["variants"].values():
            self.assertEqual(len(d["per_back"]), 6)
        self.assertEqual(bet["assumed_back"], list(rest[:2]))

    def test_the_bet_reaches_the_chooser(self):
        """It must actually narrow what the action is SCORED against -- a bet
        that changes nothing anywhere is a control that does nothing.

        Checked across every back pair rather than one: a pair that happens to
        be the worst case for every candidate scores identically to the wide
        ranking, which is correct behaviour and not evidence of a dead control.
        """
        w = world()
        common = (w["merged"], w["moves"], w["natures"], w["typechart"])
        rest = [x for x in w["teams"][TEAM] if x not in LEAD]
        wide = committed_plan.rank_openings(
            OURS, committed_plan.enemy_brings(w["teams"][TEAM], LEAD), *common,
            team_name=TEAM, roll="min")
        differed = 0
        for bp in itertools.combinations(rest, 2):
            narrow = committed_plan.rank_openings(
                OURS, committed_plan.enemy_brings(w["teams"][TEAM], LEAD,
                                                  assumed_back=bp),
                *common, team_name=TEAM, roll="min")
            self.assertEqual(len(wide), len(narrow))       # same option space
            if [s for s, _ in wide] != [s for s, _ in narrow]:
                differed += 1
        self.assertGreater(differed, 0,
                           "no back pair changed the scoring -- the bet is inert")


class TestTheRankingIsAtTheInformationSet(unittest.TestCase):

    def test_worst_case_scoring_is_never_kinder_than_the_mean(self):
        """`worst` is the default because "withstands any back" is the claim
        being made. It must genuinely be the pessimistic reading."""
        w = world()
        brings = committed_plan.enemy_brings(w["teams"][TEAM], LEAD)
        common = (w["merged"], w["moves"], w["natures"], w["typechart"])
        n_games = len(brings) * (len(committed_plan.all_scripts(TEAM)) + 1)
        worst = committed_plan.rank_openings(OURS, brings, *common,
                                             team_name=TEAM, roll="min",
                                             aggregate="worst")
        total = committed_plan.rank_openings(OURS, brings, *common,
                                             team_name=TEAM, roll="min",
                                             aggregate="sum")
        means = {d: s / n_games for s, d in total}
        for score, desc in worst:
            self.assertLessEqual(score, means[desc] + 1e-6)

    def test_choose_opening_returns_the_top_of_the_ranking(self):
        w = world()
        common = (w["merged"], w["moves"], w["natures"], w["typechart"])
        chosen = committed_plan.choose_opening(OURS, w["teams"][TEAM], *common,
                                               team_name=TEAM, enemy_lead=LEAD,
                                               roll="min")
        ranked = committed_plan.rank_openings(
            OURS, committed_plan.enemy_brings(w["teams"][TEAM], LEAD), *common,
            team_name=TEAM, roll="min")
        self.assertEqual(chosen, ranked[0][1])


if __name__ == "__main__":
    unittest.main()
