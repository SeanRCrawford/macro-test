"""The one-minute team preview: which lead is not already lost?

Asked for directly:

    "I need to know within around 1 minute what my best, robust line against an
     enemy 6 is... I need a lead that doesn't automatically lose, or at least if
     I face certain bad enemy leads I can switch out to create a winning
     position (e.g., intimidate)"

The full audit is hours, so it cannot serve this. One turn solved as a matrix
game is half a second, and "is this opening already lost" is the question you
actually have at preview.

Three properties make the answer trustworthy, and each has a test:

  * MAXIMIN, not average. They choose their lead knowing our six, so a lead of
    ours is worth its worst case over theirs.
  * THE PRUNE IS SOUND. Abandoning a lead once it is behind a finished one can
    never discard the winner, because a minimum only falls.
  * A PRUNED LEAD IS NEVER REPORTED AS GOOD. Its number is an upper bound and
    the output says so -- measured on a real preview, a pruned lead showed 81
    against a proven 73, which read backwards without the marking.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import preview_lead  # noqa: E402


class FakeRec:
    """A turn_robustness stand-in: only worst_case, exploitability, our_action
    are read."""

    def __init__(self, worst_case, kinds=("move",), punish=0.0):
        self.worst_case = worst_case
        self.exploitability = punish
        self.our_action = [type("A", (), {"kind": k})() for k in kinds]


class Harness(unittest.TestCase):
    """Replaces the engine, so these pin the SEARCH rather than the battles."""

    OURS = ["A", "B", "C", "D", "E", "F"]
    THEIRS = ["u", "v", "w", "x", "y", "z"]

    def setUp(self):
        self.values = {}
        self.calls = []
        self._real = preview_lead.opening_turn

        def fake(our4, enemy4, *a, **k):
            key = (our4[0], our4[1], enemy4[0], enemy4[1])
            self.calls.append(key)
            return FakeRec(self.values.get(key, 0.0))

        preview_lead.opening_turn = fake
        self.addCleanup(setattr, preview_lead, "opening_turn", self._real)

    # rank_leads unpacks the dataset up front and hands it to opening_turn,
    # which is stubbed here -- so the values are never read, but the keys must
    # exist.
    WORLD = {"merged": {}, "moves": {}, "natures": {}, "typechart": {}}

    def run_it(self, **kw):
        return preview_lead.rank_leads(self.OURS, self.THEIRS, self.WORLD, **kw)


class TestItIsAMaximin(Harness):

    def test_a_lead_is_worth_its_WORST_enemy_lead(self):
        """Not its average. One catastrophic enemy lead makes a lead bad even
        if it beats the other fourteen."""
        for their in itertools.combinations(self.THEIRS, 2):
            self.values[("A", "B", their[0], their[1])] = 100.0
        self.values[("A", "B", "u", "v")] = -500.0
        ranked, _ = self.run_it(budget=1e6)
        entry = next(e for e in ranked if e["lead"] == ["A", "B"])
        self.assertEqual(entry["guaranteed"], -500.0)
        self.assertEqual(entry["worst_vs"], ["u", "v"])

    def test_the_best_lead_is_the_one_with_the_best_worst_case(self):
        for our in itertools.combinations(self.OURS, 2):
            for their in itertools.combinations(self.THEIRS, 2):
                self.values[(our[0], our[1], their[0], their[1])] = -100.0
        # C/D is merely mediocre everywhere; A/B is great except once.
        for their in itertools.combinations(self.THEIRS, 2):
            self.values[("A", "B", their[0], their[1])] = 300.0
            self.values[("C", "D", their[0], their[1])] = -20.0
        self.values[("A", "B", "y", "z")] = -400.0
        ranked, _ = self.run_it(budget=1e6)
        self.assertEqual(ranked[0]["lead"], ["C", "D"])


class TestThePrune(Harness):

    def test_it_saves_real_work(self):
        """The whole reason this fits in a minute: 15x15 is 225 solves."""
        for our in itertools.combinations(self.OURS, 2):
            for their in itertools.combinations(self.THEIRS, 2):
                self.values[(our[0], our[1], their[0], their[1])] = -100.0
        for their in itertools.combinations(self.THEIRS, 2):
            self.values[("A", "B", their[0], their[1])] = 50.0
        _ranked, meta = self.run_it(budget=1e6)
        self.assertLess(meta["solves"], 225)

    def test_it_never_discards_the_winner(self):
        """The soundness of the prune, on many random tables: whatever it
        returns first must be the true maximin."""
        import random
        rng = random.Random(11)
        for trial in range(12):
            self.values = {}
            for our in itertools.combinations(self.OURS, 2):
                for their in itertools.combinations(self.THEIRS, 2):
                    self.values[(our[0], our[1], their[0], their[1])] = (
                        rng.uniform(-300, 300))
            ranked, _ = self.run_it(budget=1e6)
            truth = {}
            for our in itertools.combinations(self.OURS, 2):
                truth[our] = min(
                    self.values[(our[0], our[1], t[0], t[1])]
                    for t in itertools.combinations(self.THEIRS, 2))
            best = max(truth, key=lambda k: truth[k])
            proven = [e for e in ranked if e["complete"]]
            self.assertTrue(proven, trial)
            self.assertEqual(proven[0]["lead"], list(best), trial)
            self.assertAlmostEqual(proven[0]["guaranteed"], truth[best],
                                   places=6)

    def test_a_pruned_lead_is_marked_incomplete_and_ranked_below(self):
        for our in itertools.combinations(self.OURS, 2):
            for their in itertools.combinations(self.THEIRS, 2):
                self.values[(our[0], our[1], their[0], their[1])] = -100.0
        for their in itertools.combinations(self.THEIRS, 2):
            self.values[("A", "B", their[0], their[1])] = 50.0
        ranked, _ = self.run_it(budget=1e6)
        complete = [e["complete"] for e in ranked]
        # Proven ones first, and at least one was pruned.
        self.assertTrue(complete[0])
        self.assertIn(False, complete)
        self.assertEqual(complete, sorted(complete, reverse=True))


class TestItRespectsTheBudget(Harness):

    def test_a_tiny_budget_still_returns_something_and_says_so(self):
        for our in itertools.combinations(self.OURS, 2):
            for their in itertools.combinations(self.THEIRS, 2):
                self.values[(our[0], our[1], their[0], their[1])] = 10.0
        ranked, meta = self.run_it(budget=0.0)
        self.assertLessEqual(meta["complete"], len(ranked))
        self.assertEqual(meta["our_leads"], 15)
        self.assertEqual(meta["their_leads"], 15)


class TestItNamesThePivot(Harness):
    """The half the user asked for by name: if the answer to their worst lead
    is to switch, that must be visible without reading a log."""

    def test_a_switch_answer_is_reported_as_a_pivot(self):
        real = preview_lead.opening_turn
        preview_lead.opening_turn = lambda our4, enemy4, *a, **k: FakeRec(
            5.0, kinds=("switch", "move"))
        try:
            ranked, _ = self.run_it(budget=1e6)
        finally:
            preview_lead.opening_turn = real
        self.assertEqual(ranked[0]["answer"], "pivot")

    def test_protect_and_attack_are_distinguished(self):
        self.assertEqual(preview_lead._action_kind(FakeRec(0, ("protect",))),
                         "protect")
        self.assertEqual(preview_lead._action_kind(FakeRec(0, ("move",))),
                         "attack")


class TestTheWriteUp(Harness):

    def test_a_pruned_number_is_shown_as_an_upper_bound(self):
        """Measured on a real preview: a pruned lead showed 81 next to a proven
        73, which reads backwards. It must be marked as a bound."""
        for our in itertools.combinations(self.OURS, 2):
            for their in itertools.combinations(self.THEIRS, 2):
                self.values[(our[0], our[1], their[0], their[1])] = -100.0
        for their in itertools.combinations(self.THEIRS, 2):
            self.values[("A", "B", their[0], their[1])] = 50.0
        ranked, meta = self.run_it(budget=1e6)
        text = preview_lead.describe(ranked, meta, top=15)
        self.assertIn("<=", text)
        self.assertIn("provably worse", text)

    def test_it_leads_with_the_bring_and_the_lead(self):
        for our in itertools.combinations(self.OURS, 2):
            for their in itertools.combinations(self.THEIRS, 2):
                self.values[(our[0], our[1], their[0], their[1])] = 10.0
        ranked, meta = self.run_it(budget=1e6)
        text = preview_lead.describe(ranked, meta)
        self.assertIn("BRING", text)
        self.assertIn("LEAD", text)


class TestTheBringIsFour(Harness):

    def test_the_lead_pair_comes_first(self):
        bring = preview_lead._bring(self.OURS, ("C", "E"))
        self.assertEqual(len(bring), 4)
        self.assertEqual(bring[:2], ["C", "E"])
        self.assertNotIn("C", bring[2:])


if __name__ == "__main__":
    unittest.main()
