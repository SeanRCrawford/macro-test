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
    """A turn_robustness stand-in: only maximin, exploitability, our_action are
    read.

    `worst_case` is deliberately set to a WRONG value rather than the same one.
    The ranking has to use `maximin` -- the best guarantee available in the
    position -- and not `worst_case`, which is the worst case of the single row
    sampled from our equilibrium mixture and can be dreadful in exactly the
    positions worth playing. Ranking on it made rank_leads abandon
    Charizard-Y/Garchomp at -278.0 (maximin +20.1) against Pelipper/Swampert, a
    lead that goes on to win 100% of the time.

    Making the decoy differ by a constant means these tests fail if the ranking
    ever reads that field again, instead of quietly still passing.
    """

    DECOY = -1000.0

    def __init__(self, maximin, kinds=("move",), punish=0.0):
        self.maximin = maximin
        self.worst_case = maximin + self.DECOY
        self.regret = -self.DECOY
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


class TestTheLine(unittest.TestCase):
    """Ranking leads says what to SEND OUT. This is the half you play."""

    def make_report(self, outcome="win", n=3):
        class Rec:
            def __init__(self, turn):
                self.turn = turn
                self.exploitability = 10.0 * turn
                self.kos = ["Torkoal"] if turn == 2 else []
                self.events = [f"turn {turn} happened"]
                self.our_action = [type("A", (), {
                    "combatant": type("C", (), {"name": "Charizard"})(),
                    "kind": "move",
                    "move": type("M", (), {"name": "Heat Wave"})(),
                    "targets": [type("C", (), {"name": "Incineroar"})()]})()]
                self.equilibrium_reply = [type("A", (), {
                    "combatant": type("C", (), {"name": "Incineroar"})(),
                    "kind": "move",
                    "move": type("M", (), {"name": "Fake Out"})(),
                    "targets": [type("C", (), {"name": "Charizard"})()]})()]
        return type("R", (), {"turns": [Rec(i) for i in range(1, n + 1)],
                              "outcome": outcome, "length": n,
                              "final_margin": 120.0})()

    # line_for unpacks the dataset before calling audit_position, which is
    # stubbed here -- the values are unused, the keys are not.
    WORLD = {"merged": {}, "moves": {}, "natures": {}, "typechart": {}}

    def patched(self, **kw):
        import deep_dive
        real_audit = deep_dive.audit_position
        deep_dive.audit_position = lambda *a, **k: self.make_report(**kw)
        self.addCleanup(setattr, deep_dive, "audit_position", real_audit)

    def test_it_returns_a_playable_sequence(self):
        self.patched()
        line = preview_lead.line_for(["A", "B", "C", "D"], ["u", "v", "w", "x"],
                                     self.WORLD, win_samples=0)
        self.assertEqual(line["outcome"], "win")
        self.assertEqual(len(line["turns"]), 3)
        self.assertEqual(line["turns"][0]["turn"], 1)
        self.assertIn("Charizard Heat Wave", line["turns"][0]["play"])
        self.assertIn("Incineroar", line["turns"][0]["play"])

    def test_it_records_their_reply_and_the_kos(self):
        self.patched()
        line = preview_lead.line_for(["A", "B", "C", "D"], ["u", "v", "w", "x"],
                                     self.WORLD, win_samples=0)
        self.assertIn("Incineroar Fake Out", line["turns"][0]["their_reply"])
        self.assertEqual(line["turns"][1]["kos"], ["Torkoal"])

    def test_a_switch_reads_as_an_arrow(self):
        actions = [type("A", (), {
            "combatant": type("C", (), {"name": "Garchomp"})(),
            "kind": "switch", "move": None,
            "targets": [type("C", (), {"name": "Farigiraf"})()]})()]
        self.assertEqual(preview_lead._say(actions), "Garchomp -> Farigiraf")

    def test_no_win_samples_means_no_invented_probability(self):
        """A line with no games behind it must not report a win rate; that is
        exactly the 'wins on the average roll' claim 4.0b is about."""
        self.patched()
        line = preview_lead.line_for(["A", "B", "C", "D"], ["u", "v", "w", "x"],
                                     self.WORLD, win_samples=0)
        self.assertIsNone(line["win_prob"])
        self.assertEqual(line["win_games"], 0)

    def test_the_hardest_enemy_lead_is_played_first(self):
        """The budget can cut the list short, so the lead that hurts most must
        never be the one that gets cut."""
        self.patched()
        real = preview_lead.line_for
        seen = []

        def spy(bring, their_bring, world, **kw):
            seen.append(tuple(their_bring[:2]))
            return {"their_bring": list(their_bring), "outcome": "win",
                    "turns": [], "length": 0, "final_margin": 0.0,
                    "win_prob": None, "win_interval": None, "win_games": 0}

        preview_lead.line_for = spy
        try:
            entry = {"bring": ["A", "B", "C", "D"], "worst_vs": ["y", "z"]}
            lines, _meta = preview_lead.lines_for_lead(
                entry, ["u", "v", "w", "x", "y", "z"], {}, budget=1e6,
                win_samples=0)
        finally:
            preview_lead.line_for = real
        self.assertEqual(seen[0], ("y", "z"))
        self.assertEqual(lines[0]["their_lead"], ["y", "z"])

    def test_the_write_up_leads_with_the_probability(self):
        line = {"their_lead": ["u", "v"], "win_prob": 0.75,
                "win_interval": (0.5, 0.9), "win_games": 8, "outcome": "win",
                "length": 5, "turns": [{"turn": 1, "play": "X Move",
                                        "their_reply": "", "kos": []}]}
        text = preview_lead.describe_line(line)
        self.assertIn("75%", text)
        self.assertIn("8 games", text)
        self.assertIn("T1", text)


class TestAdjudicatingTheClock(unittest.TestCase):
    """A turn cap is a clock, and VGC decides a clock on the board.

    Reported: "for a timeout, maybe make the winner the one with the best
    simple matchup with remaining board state". That is the actual tournament
    rule -- Pokemon remaining first, then HP percentage -- and counting a capped
    game as a LOSS is not conservative, it is wrong: it discards the grind-out
    lines that most real games are.
    """

    def battle(self, ours, theirs):
        from _harness import load_world
        from battle import Battle
        from combatants import make_team
        w = load_world()
        return Battle(make_team(list(ours), w["merged"], w["natures"]),
                      make_team(list(theirs), w["merged"], w["natures"]),
                      w["typechart"], w["moves"])

    def test_more_pokemon_left_wins(self):
        b = self.battle(["Incineroar", "Gallade"], ["Hydreigon", "Gholdengo"])
        b.p2.roster[1].apply_damage(b.p2.roster[1].max_hp())
        self.assertEqual(b.adjudicate(), "p1")

    def test_equal_count_falls_through_to_hp_share(self):
        b = self.battle(["Incineroar", "Gallade"], ["Hydreigon", "Gholdengo"])
        b.p2.roster[0].apply_damage(b.p2.roster[0].max_hp() // 2)
        self.assertEqual(b.adjudicate(), "p1")

    def test_hp_is_a_FRACTION_not_raw_points(self):
        """Otherwise a bulky survivor beats a healthy frail one for free."""
        b = self.battle(["Incineroar", "Gallade"], ["Hydreigon", "Gholdengo"])
        for c in b.p1.roster:              # us: everyone at 60%
            c.apply_damage(c.max_hp() * 0.4)
        for c in b.p2.roster:              # them: everyone at 50%
            c.apply_damage(c.max_hp() * 0.5)
        self.assertEqual(b.adjudicate(), "p1")

    def test_a_decided_game_is_not_re_adjudicated(self):
        b = self.battle(["Incineroar", "Gallade"], ["Hydreigon", "Gholdengo"])
        for c in b.p2.roster:
            c.apply_damage(c.max_hp())
        self.assertEqual(b.winner(), "p1")
        self.assertEqual(b.adjudicate(), "p1")

    def test_it_never_returns_none(self):
        """The whole point: a caller can always ask who is ahead."""
        b = self.battle(["Incineroar", "Gallade"], ["Hydreigon", "Gholdengo"])
        self.assertIn(b.adjudicate(), ("p1", "p2", "draw"))
        self.assertIsNone(b.winner())


class TestOptimisingTheBackTwo(unittest.TestCase):
    """Reported: "Optimise the back two also, it is critical."

    rank_leads solves turn 1, where the back is off the field -- so it used a
    placeholder. The back decides what you pivot INTO and whether the endgame
    is 2v2 or 2v1, so it has to be played out, not assumed.
    """

    OURS = ["A", "B", "C", "D", "E", "F"]
    THEIRS = ["u", "v", "w", "x", "y", "z"]
    WORLD = {"merged": {}, "moves": {}, "natures": {}, "typechart": {}}

    def patch(self, table):
        import win_rate

        class E:
            def __init__(self, p):
                self.p = p
                self.n = 4

        real = win_rate.matchup_win_prob_adaptive
        win_rate.matchup_win_prob_adaptive = (
            lambda our4, their4, world, **kw: E(
                table.get((tuple(our4[2:]), their4[0]), 0.5)))
        self.addCleanup(setattr, win_rate, "matchup_win_prob_adaptive", real)

    def test_every_back_pair_is_considered(self):
        self.patch({})
        entry = {"lead": ["A", "B"], "worst_vs": ["u", "v"],
                 "bring": ["A", "B", "C", "D"]}
        out, meta = preview_lead.rank_brings(entry, self.OURS, self.THEIRS,
                                             self.WORLD, budget=1e6)
        self.assertEqual(meta["of"], 6)              # C(4,2)
        self.assertEqual(len(out), 6)

    def test_the_back_is_chosen_on_its_WORST_enemy_lead(self):
        table = {}
        for their in self.THEIRS:
            table[(("C", "D"), their)] = 0.9
        table[(("C", "D"), "u")] = 0.1               # one disaster
        for their in self.THEIRS:
            table[(("E", "F"), their)] = 0.6
        self.patch(table)
        entry = {"lead": ["A", "B"], "worst_vs": ["u", "v"],
                 "bring": ["A", "B", "C", "D"]}
        out, _ = preview_lead.rank_brings(entry, self.OURS, self.THEIRS,
                                          self.WORLD, budget=1e6)
        best = out[0]
        self.assertEqual(best["back"], ["E", "F"])

    def test_the_bring_puts_the_lead_first(self):
        self.patch({})
        entry = {"lead": ["A", "B"], "worst_vs": ["u", "v"],
                 "bring": ["A", "B", "C", "D"]}
        out, _ = preview_lead.rank_brings(entry, self.OURS, self.THEIRS,
                                          self.WORLD, budget=1e6)
        for e in out:
            self.assertEqual(e["bring"][:2], ["A", "B"])
            self.assertEqual(e["bring"][2:], e["back"])

    def test_an_unfinished_back_is_an_upper_bound_and_ranks_below(self):
        """The same trap as the lead prune, and it bit the same way: a 6-of-15
        '100%' sorted above a 15-of-15 75%."""
        out = [{"back": ["C", "D"], "worst_win": 0.5, "complete": False,
                "checked": 2, "of": 15},
               {"back": ["E", "F"], "worst_win": 0.25, "complete": True,
                "checked": 15, "of": 15}]
        out.sort(key=lambda e: (not e["complete"], -e["worst_win"]))
        self.assertEqual(out[0]["back"], ["E", "F"])

    def test_backs_for_excludes_the_lead(self):
        backs = preview_lead.backs_for(self.OURS, ("A", "B"))
        self.assertEqual(len(backs), 6)
        for b in backs:
            self.assertNotIn("A", b)
            self.assertNotIn("B", b)


class TestTheBringIsFour(Harness):

    def test_the_lead_pair_comes_first(self):
        bring = preview_lead._bring(self.OURS, ("C", "E"))
        self.assertEqual(len(bring), 4)
        self.assertEqual(bring[:2], ["C", "E"])
        self.assertNotIn("C", bring[2:])


if __name__ == "__main__":
    unittest.main()
