"""The audited line has to play the team on the team sheet.

Reported: "best lines use moves not in the teamsheet; I see Scizor using Bug
Bite when it doesn't have it, and when Bullet Punch would have KOd."

Both halves came from ONE defect. `_rate_and_rerank.build` -- the closure that
stands up the battle every audited line is played on -- did this:

    oc = make_team(our_names, merged, natures, sets=our_sets)     # item, ability, EVs
    ms = {c.name: build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES)}

`make_team` applied the item, ability and EVs from the set, and the second line
dropped the MOVES, falling back to the four most-used. So the audit rated, and
the reported line played, a Pokemon holding the right item with somebody else's
attacks. Every other build site -- play_out_pair, deep_dive, committed_plan,
punish_screen, fast_eval -- already passed `only_moves`; this one did not, so
the RECORD and the LINE disagreed about what the team was.

The second half needs no separate explanation. Scizor's usage four are Bullet
Punch, Protect, Bug Bite and Swords Dance, and against a neutral-ish target Bug
Bite is 60 BP to Bullet Punch's 40 (both Technician-boosted), so preferring it
is correct play -- given an illegal move to prefer. Take Bug Bite out of the set
and the choice cannot arise.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from solver import TOP_K_MOVES, build_moveset  # noqa: E402

_WORLD = None
SET = {"Scizor": {"ability": "Technician", "item": "Life Orb",
                  "moves": ["Bullet Punch", "Close Combat", "Swords Dance",
                            "Protect"]}}


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


class TestThePremise(unittest.TestCase):
    """Without these, a passing fix could mean the move was never available."""

    def test_bug_bite_is_in_scizors_usage_four(self):
        names = [m.name for m, _ in
                 build_moveset(world()["merged"]["Scizor"], world()["moves"])]
        self.assertIn("Bug Bite", names)
        self.assertIn("Bullet Punch", names)

    def test_the_set_under_test_excludes_it(self):
        self.assertNotIn("Bug Bite", SET["Scizor"]["moves"])

    def test_the_set_is_honoured_when_it_is_passed(self):
        names = [m.name for m, _ in
                 build_moveset(world()["merged"]["Scizor"], world()["moves"],
                               only_moves=SET["Scizor"]["moves"])]
        self.assertEqual(names, SET["Scizor"]["moves"])


class TestEveryBuildSitePassesTheSetsMoves(unittest.TestCase):
    """Read from the source, because reaching them all needs a full search.

    The defect was one site out of six getting this wrong, so the useful test is
    the one that checks all of them rather than the one that was reported.
    """

    def build_calls(self, path):
        """Every build_moveset call in a file, as source text."""
        import re
        body = open(path, encoding="utf-8").read()
        return re.findall(r"build_moveset\((?:[^()]|\([^()]*\))*\)", body)

    def site(self, relative):
        return os.path.join(os.path.dirname(__file__), "..", relative)

    def test_the_line_audit_passes_only_moves(self):
        body = open(self.site("src/matchup_search.py"), encoding="utf-8").read()
        block = body[body.index("def build(our_names, enemy_spec):"):][:1400]
        self.assertIn("only_moves=spec.get(\"moves\")", block)

    def test_no_engine_build_site_ignores_the_set(self):
        """`build_wide_moveset` is the one legitimate exception -- it exists to
        widen the OPPONENT's move space past their four on purpose."""
        for relative in ("src/matchup_search.py", "src/deep_dive.py",
                         "src/committed_plan.py", "src/punish_screen.py",
                         "src/fast_eval.py"):
            for call in self.build_calls(self.site(relative)):
                self.assertIn("only_moves", call, f"{relative}: {call}")

    def test_the_app_build_sites_pass_it_too(self):
        for call in self.build_calls(self.site("src/app.py")):
            self.assertIn("only_moves", call, call)


class TestTheAuditedLineNeverPlaysAnExcludedMove(unittest.TestCase):
    """The end-to-end version: run the real audit and read the lines back."""

    # FOUR Pokemon, not six. With a pool of six the screen simply did not bring
    # Scizor, so the test passed with the bug still in place -- it was asserting
    # that a Pokemon which never took the field did not use a move. A pool of
    # four leaves exactly one possible bring, so Scizor has to play.
    POOL = ["Scizor", "Gallade", "Gholdengo", "Incineroar"]

    @classmethod
    def setUpClass(cls):
        import matchup_search
        w = world()
        cls.result = matchup_search.search_robust_composition(
            list(cls.POOL),
            w["teams"]["Rain"], w["merged"], w["moves"], w["natures"],
            w["typechart"], 12, our_sets=SET, verify_top=6,
            rate_robustness=True, robustness_leads=2, robustness_turns=12)

    def our_plays(self):
        plays = []
        for rec in self.result:
            for line in rec.get("audit") or []:
                for turn in line.get("turns") or []:
                    plays.append(str(turn.get("our_play") or ""))
        return plays

    def test_the_audit_actually_ran(self):
        self.assertTrue(self.our_plays(), "no audited turns to inspect")

    def test_scizor_was_actually_brought(self):
        """The guard the first version of this test lacked."""
        brought = any("Scizor" in (r.get("our_bring4") or [])
                      for r in self.result)
        self.assertTrue(brought, "Scizor never took the field")

    def test_scizor_actually_acted(self):
        """And acted, so there was an opportunity to use the wrong move."""
        self.assertTrue(any("Scizor" in p for p in self.our_plays()),
                        self.our_plays()[:6])

    def test_scizor_never_uses_the_move_it_does_not_have(self):
        offending = [p for p in self.our_plays() if "Bug Bite" in p]
        self.assertEqual(offending, [], offending[:5])


if __name__ == "__main__":
    unittest.main()
