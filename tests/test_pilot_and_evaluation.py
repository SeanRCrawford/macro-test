"""Who plays the games, and which evaluation plays them — as settings.

Two things were reported, and both were constants buried in a module:

  "fix the pilot, make the equilibrium selectable as a flag and in the
   streamlit app"
  "say the word and I'll revert it, or make it a flag" -> "make it a flag and
   an option in the streamlit app"

The pilot is the one that matters: the greedy default hands whichever side is
passed as p1 a systematic advantage, measured at 78% of mirrored matchups
flipping (tools/measure_side_bias.py). The evaluation is the `sacrifice` /
`legacy` trade, whose cost is measured at record 80% -> 74%.

Both change the answer, so the tests that matter most here are the cache-key
ones: a record produced under one setting must never be served to a run asking
for the other. That is the failure mode that makes a cache worse than useless,
and it is invisible until the numbers are already wrong.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import roster_rating  # noqa: E402
import solver  # noqa: E402
from search_effort import TIER_ORDER, relative_cost, tier  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


class TestTheEvaluationProfile(unittest.TestCase):

    def test_the_two_profiles_differ(self):
        self.assertNotEqual(solver.EVAL_PROFILES["sacrifice"],
                            solver.EVAL_PROFILES["legacy"])

    def test_legacy_is_the_previous_evaluation_exactly(self):
        """Both terms off is what "restores the previous evaluation" means; if
        either drifts off zero the promise is quietly broken."""
        self.assertEqual(solver.EVAL_PROFILES["legacy"],
                         {"SPEED_CONTROL_WEIGHT": 0.0, "FRAGILE_HP": 0.0})

    def test_the_context_manager_restores_what_it_found(self):
        before = (solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP)
        with solver.evaluation("legacy"):
            self.assertEqual(solver.SPEED_CONTROL_WEIGHT, 0.0)
            self.assertEqual(solver.FRAGILE_HP, 0.0)
        self.assertEqual((solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP),
                         before)

    def test_it_restores_even_when_the_block_raises(self):
        """Streamlit keeps module state alive across reruns, so a leaked
        profile is sticky for every other tab."""
        before = (solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP)
        with self.assertRaises(RuntimeError):
            with solver.evaluation("legacy"):
                raise RuntimeError("boom")
        self.assertEqual((solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP),
                         before)

    def test_none_leaves_it_alone(self):
        before = (solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP)
        with solver.evaluation(None):
            self.assertEqual((solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP),
                             before)

    def test_an_unknown_name_falls_back_to_the_default(self):
        with solver.evaluation("nonsense"):
            self.assertEqual(
                solver.SPEED_CONTROL_WEIGHT,
                solver.EVAL_PROFILES[solver.DEFAULT_EVAL_PROFILE]
                ["SPEED_CONTROL_WEIGHT"])

    def test_apply_is_permanent_for_pool_workers(self):
        """A worker never sees the parent's context manager, so it needs a
        setter that just sets."""
        before = (solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP)
        try:
            solver.apply_eval_profile("legacy")
            self.assertEqual(solver.SPEED_CONTROL_WEIGHT, 0.0)
        finally:
            solver.SPEED_CONTROL_WEIGHT, solver.FRAGILE_HP = before


class TestTheCacheCannotServeTheWrongAnswer(unittest.TestCase):

    def base(self, **kw):
        return roster_rating.rating_key(["A", "B"], "standard", 10, None, **kw)

    def test_the_pilot_changes_the_key(self):
        self.assertNotEqual(self.base(), self.base(pilot="equilibrium"))

    def test_the_evaluation_changes_the_key(self):
        self.assertNotEqual(self.base(), self.base(eval_profile="legacy"))

    def test_the_two_are_not_interchangeable(self):
        self.assertNotEqual(self.base(pilot="equilibrium"),
                            self.base(eval_profile="legacy"))

    def test_the_old_default_key_is_unchanged(self):
        """Caches from before either argument existed must still hit, or every
        interrupted overnight run starts over."""
        self.assertEqual(self.base(),
                         self.base(pilot=None, eval_profile=None))


class TestTheTier(unittest.TestCase):

    def test_every_tier_names_a_pilot(self):
        for name in TIER_ORDER:
            self.assertIn(tier(name).get("pilot"), ("greedy", "equilibrium"),
                          name)

    def test_thorough_plus_is_the_equilibrium_one(self):
        self.assertEqual(tier("thorough+")["pilot"], "equilibrium")

    def test_the_existing_tiers_are_untouched(self):
        """Changing what `thorough` means would silently reprice every run
        anybody already has cached."""
        for name in ("quick", "standard", "thorough", "exhaustive"):
            self.assertEqual(tier(name)["pilot"], "greedy", name)

    def test_thorough_plus_is_priced_as_dearer_than_thorough(self):
        """It differs from thorough ONLY in the pilot, so if the cost estimate
        ignores the pilot the two look identical and nobody is warned."""
        self.assertGreater(relative_cost("thorough+"),
                           relative_cost("thorough") * 5)


class TestRateTeamAcceptsBoth(unittest.TestCase):

    def test_the_signature_takes_them(self):
        import inspect
        params = inspect.signature(roster_rating.rate_team).parameters
        self.assertIn("pilot", params)
        self.assertIn("eval_profile", params)

    def test_the_profile_is_applied_around_the_work(self):
        """Applied inside rate_team rather than by the caller, because a pool
        worker only ever receives the job dict."""
        seen = {}
        real = roster_rating._rate_team

        def spy(*a, **k):
            seen["weight"] = solver.SPEED_CONTROL_WEIGHT
            return {"team": [], "wins": 0, "total": 0}

        roster_rating._rate_team = spy
        try:
            roster_rating.rate_team(["A"], {}, eval_profile="legacy")
        finally:
            roster_rating._rate_team = real
        self.assertEqual(seen["weight"], 0.0)
        self.assertNotEqual(solver.SPEED_CONTROL_WEIGHT, 0.0)  # and restored


class TestTheFlagsExist(unittest.TestCase):

    @staticmethod
    def source(*parts):
        return open(os.path.join(ROOT, *parts), encoding="utf-8").read()

    def test_the_generator_takes_them(self):
        import generate_overnight as go
        opts = {a.dest for a in go.build_parser()._actions}
        self.assertIn("pilot", opts)
        self.assertIn("evaluation", opts)

    def test_they_are_part_of_the_stage1_fingerprint(self):
        """Both change WHICH teams come out, so a run that varies them must
        renumber loudly rather than silently reusing --pick numbers."""
        import argparse

        import generate_overnight as go
        base = vars(go.build_parser().parse_args([]))
        base["out"] = "x.json"
        fp = go._stage1_settings(argparse.Namespace(**base))
        self.assertIn("pilot", fp)
        self.assertIn("evaluation", fp)

    def test_stage_2_takes_them_too(self):
        src = self.source("tools", "search_teams.py")
        self.assertIn('"--pilot"', src)
        self.assertIn('"--evaluation"', src)
        self.assertIn("args.pilot, args.evaluation", src)

    def test_the_batch_file_passes_them_to_both_stages(self):
        src = self.source("overnight.bat")
        self.assertIn('"--pilot"', src)
        self.assertIn('"--evaluation"', src)
        stage1 = src[src.index("generate_overnight.py"):]
        stage2 = src[src.index("search_teams.py"):]
        self.assertIn("%PILOT%", stage1[:800])
        self.assertIn("%PILOT%", stage2[:800])


class TestGenerateTabPassesThePilot(unittest.TestCase):
    """Reported: "when I turn equilibrium mode on and use the Generate Team
    tab, I get teams with 89/90, but then I test them in lead/back search
    and they get 30/90." Root cause: the Generate tab's own two
    `verify_with_solver` calls never passed `pilot=` at all, so they always
    ran the greedy pilot regardless of the sidebar's "Who plays the games"
    toggle -- unlike Lead/Back Search, which correctly passes `pilot=PILOT`.
    A team that only "wins" against the greedy pilot's fixed, non-adaptive
    opponent (measured 78% side bias, `search_effort.py`) looks nothing
    like the same team audited honestly."""

    APP = os.path.join(ROOT, "src", "app.py")

    def test_every_generate_tab_verify_call_passes_the_pilot(self):
        import ast
        with open(self.APP, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "verify_with_solver"]
        # Both of the Generate tab's own calls are identifiable by
        # `all_backs=deep` -- the "verify against every enemy bring" flag
        # only that tab threads through this way.
        generate_tab_calls = [c for c in calls
                              if any(kw.arg == "all_backs" for kw in c.keywords)]
        self.assertGreaterEqual(len(generate_tab_calls), 2,
                                "expected both of the Generate tab's "
                                "verify_with_solver calls to still exist")
        for c in generate_tab_calls:
            kw_names = {kw.arg for kw in c.keywords}
            self.assertIn("pilot", kw_names,
                          "a Generate tab verify_with_solver call is "
                          "missing pilot= -- the sidebar's equilibrium "
                          "toggle would silently do nothing here")


class TestTheAppExposesThem(unittest.TestCase):
    """Asked for explicitly: "add all of the features in the table/flags as
    options in the streamlit app"."""

    APP = os.path.join(ROOT, "src", "app.py")

    @classmethod
    def setUpClass(cls):
        from streamlit.testing.v1 import AppTest
        at = AppTest.from_file(cls.APP, default_timeout=900)
        at.session_state["team"] = ["Arcanine-Hisui", "Hydreigon", "Gallade",
                                    "Gholdengo", "Incineroar", "Farigiraf"]
        at.session_state["sets"] = {}
        cls.at = at.run()
        cls.keys = {w.key for w in (list(cls.at.checkbox) + list(cls.at.slider)
                                    + list(cls.at.selectbox)
                                    + list(cls.at.multiselect)
                                    + list(cls.at.radio)) if w.key}

    def test_it_renders(self):
        self.assertFalse(self.at.exception, list(self.at.exception))

    def test_the_pilot_is_selectable(self):
        self.assertIn("model_pilot", self.keys)

    def test_the_evaluation_is_selectable(self):
        self.assertIn("model_eval", self.keys)

    def test_the_punish_screen_is_there(self):
        self.assertIn("gen_punish_screen", self.keys)

    def test_the_substitution_loop_is_there(self):
        for k in ("sub_vs", "sub_effort", "sub_n", "sub_pool"):
            self.assertIn(k, self.keys, k)

    def test_thorough_plus_is_offered_by_the_strength_slider(self):
        sliders = [s for s in self.at.select_slider
                   if s.key and s.key.endswith("_adv_effort")]
        self.assertTrue(sliders)
        # The slider shows tier LABELS, not keys.
        self.assertTrue(any("Thorough+" in o for o in sliders[0].options),
                        list(sliders[0].options))

    def test_the_greedy_default_is_flagged_as_inflated(self):
        """The warning is the point: a number nobody knows is inflated is worse
        than no number."""
        text = " ".join(w.value for w in self.at.warning)
        self.assertIn("78%", text)


if __name__ == "__main__":
    unittest.main()
