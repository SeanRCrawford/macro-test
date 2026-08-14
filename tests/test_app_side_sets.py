"""The sets have to travel with the team they belong to.

Reported: "On the 'Team preview: what do I bring against this team', I need it
to select my actual loaded team. Right now it just uses my six pokemon but
doesn't use their specific moves etc."

`our_side_pool` returned only NAMES, so every caller reached for
st.session_state["sets"] -- the Team Builder's. Correct for the loaded team,
and simply wrong for the other two sources, where it put one team's items,
moves and abilities onto a different team's Pokemon. It now returns
(names, sets) and each source supplies its own.

Also covers the A/B panel, which is only worth anything if variant B's sets
actually reach the search -- a comparison that silently runs the same team
twice would report a clean "no difference" forever.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
TEAM = ["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
        "Incineroar", "Farigiraf"]
SETS = {"Arcanine-Hisui": {"ability": "Intimidate", "moves": ["Rock Slide"]}}


def app(sets=None, team=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=900)
    at.session_state["team"] = list(team or TEAM)
    at.session_state["sets"] = dict(sets or {})
    return at.run()


def click(at, key):
    return [b for b in at.button if b.key == key][0].click().run()


class TestThePreviewUsesTheTeamItSelected(unittest.TestCase):

    def setUp(self):
        import preview_plan
        self.seen = {}
        self._orig = preview_plan.plan_against

        def spy(our6, their6, *a, **k):
            self.seen["our6"] = list(our6)
            self.seen["our_sets"] = k.get("our_sets")
            return {"bring": list(our6)[:4], "wins": 1, "of": 1, "punish": 0.0,
                    "losing_to": [], "audited": True, "severe_turns": 0,
                    "lines": []}

        preview_plan.plan_against = spy
        self.addCleanup(setattr, preview_plan, "plan_against", self._orig)

    def test_the_loaded_team_brings_its_own_sets(self):
        at = app(SETS)
        click(at, "pv_run")
        self.assertEqual(self.seen["our6"], TEAM)
        self.assertEqual(self.seen["our_sets"], SETS)

    def test_a_saved_team_does_not_inherit_the_builders_sets(self):
        """The actual defect: switching source kept applying the Team Builder's
        overrides to somebody else's Pokemon."""
        at = app(SETS)
        at.radio(key="pv_side_source").set_value("A saved team").run()
        click(at, "pv_run")
        self.assertNotIn("Arcanine-Hisui", self.seen["our6"])
        self.assertEqual(self.seen["our_sets"], {})

    def test_switching_source_refills_the_picker(self):
        """One shared widget key kept the old names, none of which were options
        any more, so the box came back empty and you re-picked six by hand."""
        at = app(SETS)
        at.radio(key="pv_side_source").set_value("A saved team").run()
        picked = at.multiselect(key="pv_ours_A saved team").value
        self.assertEqual(len(picked), 6)
        self.assertNotIn("Arcanine-Hisui", picked)


class TestTheABPanel(unittest.TestCase):

    def setUp(self):
        import matchup_search
        self.calls = []
        self._orig = matchup_search.search_robust_composition

        def spy(our, enemy, *a, **k):
            sets = k.get("our_sets") or {}
            self.calls.append(sets)
            # B is whichever call carries the override, and it is made to look
            # worse so the delta has a sign the test can check.
            worse = bool(sets.get("Gallade", {}).get("moves"))
            return [{"our_bring4": list(our)[:4],
                     "solver_wins": 70 if worse else 84, "solver_total": 90}]

        matchup_search.search_robust_composition = spy
        self.addCleanup(setattr, matchup_search, "search_robust_composition",
                        self._orig)

    def run_ab(self, at):
        at.selectbox(key="ab_mon").select("Gallade").run()
        at.multiselect(key="ab_moves").set_value(["Protect"]).run()
        at.multiselect(key="ab_opps").set_value(["Rain"]).run()
        return click(at, "ab_go")

    def test_it_runs_both_variants_against_the_same_opponent(self):
        at = self.run_ab(app())
        self.assertEqual(len(self.calls), 2)

    def test_only_one_of_the_two_carries_the_change(self):
        """A comparison that runs the same team twice always reports "no
        difference", which is the failure mode worth pinning."""
        self.run_ab(app())
        changed = [c for c in self.calls if c.get("Gallade", {}).get("moves")]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["Gallade"]["moves"], ["Protect"])

    def test_the_rest_of_the_team_is_held_identical(self):
        base = {"Hydreigon": {"item": "Choice Scarf"}}
        self.run_ab(app(base))
        for c in self.calls:
            self.assertEqual(c["Hydreigon"], base["Hydreigon"])

    def test_the_delta_is_reported_with_its_sign(self):
        at = self.run_ab(app())
        rows = at.session_state["ab_rows"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Delta"], 70 - 84)

    def test_an_empty_change_is_refused_rather_than_reported_as_a_tie(self):
        at = app()
        at.selectbox(key="ab_mon").select("Gallade").run()
        at.multiselect(key="ab_opps").set_value(["Rain"]).run()
        click(at, "ab_go")
        self.assertEqual(self.calls, [])
        self.assertTrue(any("identical" in w.value for w in at.warning))


class TestTheRealSearchSeesTheChange(unittest.TestCase):
    """The stubs above pin the wiring; this pins that the wiring reaches the
    engine. Slow, so it is one opponent and one deliberately crippling change."""

    def test_crippling_a_member_costs_real_games(self):
        import matchup_search
        from _harness import load_world
        w = load_world()
        base = matchup_search.search_robust_composition(
            TEAM, w["teams"]["Rain"], w["merged"], w["moves"], w["natures"],
            w["typechart"], 12, our_sets={}, verify_top=1)
        crippled = matchup_search.search_robust_composition(
            TEAM, w["teams"]["Rain"], w["merged"], w["moves"], w["natures"],
            w["typechart"], 12, our_sets={"Gallade": {"moves": ["Protect"]}},
            verify_top=1)
        self.assertLess(crippled[0]["solver_wins"], base[0]["solver_wins"])


if __name__ == "__main__":
    unittest.main()
