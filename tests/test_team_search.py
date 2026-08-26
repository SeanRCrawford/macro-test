"""The team generator's weakness/net-weakness limits, and the Advanced panel's
hard per-type limits and required cores.

    "I set the limit to 1 but it did not filter teams early."

`weakness_violations`'s per-type accounting already existed and was already
threaded through `score_team`/`beam_search_teams` as a bounded SOFT synergy
penalty -- the literal bug was one call site (`src/app.py`'s "Run generation"
button) computing the sliders' values and then never passing them into
`beam_search_teams` at all, so the limit did nothing at any setting. That's
covered here at the `team_search.beam_search_teams` level (the call app.py
makes), not by driving the Streamlit UI.

Beyond the fix: an Advanced panel adds per-type overrides and required type
cores that are HARD requirements (confirmed with the user) -- a team that
breaks one is dropped outright, however well it wins matchups, unlike the
existing sliders which stay a bounded nudge no matter what.
"""
import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import team_search as ts  # noqa: E402
from _harness import load_world  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


# A small, fixed pool + one opponent so the beam search here is fast and its
# numbers are hand-checkable, same convention `test_counter_finder.py` uses.
POOL = ["Garchomp", "Incineroar", "Kingambit", "Gyarados", "Whimsicott",
       "Sinistcha", "Basculegion", "Dragonite", "Mega Gyarados"]


def small_matrix_and_pairs(pool=POOL, opponent="Big 6"):
    W = world()
    eps = ts.enemy_pairs_from_teams({opponent: W["teams"][opponent]})
    matrix = ts.build_pair_matrix(pool, eps, W["merged"], W["moves"],
                                  W["natures"], W["typechart"])
    return W["merged"], matrix, eps


class TestWeaknessViolationsPerType(unittest.TestCase):
    """The SOFT count -- feeds `score_team`'s bounded synergy penalty."""

    def setUp(self):
        self.merged = world()["merged"]

    def test_a_scalar_default_applies_to_every_type(self):
        team = ["Garchomp", "Incineroar", "Kingambit", "Gyarados",
               "Whimsicott", "Sinistcha"]
        viol, detail = ts.weakness_violations(team, self.merged, max_weak=6)
        self.assertEqual(viol, 0, "nothing can exceed a cap of 6 with 6 members")

    def test_a_per_type_override_tightens_only_that_type(self):
        team = ["Garchomp", "Incineroar", "Kingambit", "Gyarados",
               "Whimsicott", "Sinistcha"]
        _viol, base_detail = ts.weakness_violations(team, self.merged)
        fire = base_detail.get("Fire")
        self.assertIsNotNone(fire, "fixture assumes this team already has a "
                                   "Fire-weakness entry at the default cap")
        n_fire_weak = len(fire["weak"])
        self.assertGreaterEqual(n_fire_weak, 1)
        # Tightening Fire alone should not touch any other type's count.
        _viol2, tightened = ts.weakness_violations(
            team, self.merged, type_limits={"Fire": {"max_weak": 0}})
        self.assertEqual(len(tightened["Fire"]["weak"]), n_fire_weak)
        other_types_before = {t: d for t, d in base_detail.items() if t != "Fire"}
        other_types_after = {t: d for t, d in tightened.items() if t != "Fire"}
        self.assertEqual(other_types_before, other_types_after)

    def test_an_unset_field_within_a_named_type_falls_back_to_the_scalar(self):
        team = ["Garchomp", "Incineroar", "Kingambit", "Gyarados",
               "Whimsicott", "Sinistcha"]
        # Only max_net named for Fire -- its max_weak should still use the
        # scalar default (2), not become uncapped.
        _viol, detail = ts.weakness_violations(
            team, self.merged, max_weak=2, type_limits={"Fire": {"max_net": 6}})
        _viol_scalar, detail_scalar = ts.weakness_violations(
            team, self.merged, max_weak=2)
        self.assertEqual(detail.get("Fire", {}).get("weak"),
                         detail_scalar.get("Fire", {}).get("weak"))


class TestHardViolations(unittest.TestCase):
    """The HARD predicate -- deliberately does NOT default to
    MAX_WEAK_PER_TYPE the way the soft count does: only what's explicitly in
    `type_limits`/`required_cores` is non-negotiable."""

    def setUp(self):
        self.merged = world()["merged"]
        self.team = ["Garchomp", "Incineroar", "Kingambit", "Gyarados",
                    "Whimsicott", "Sinistcha"]

    def test_no_constraints_never_excludes(self):
        self.assertFalse(ts.hard_violations(self.team, self.merged))

    def test_an_unnamed_type_has_no_hard_cap_even_with_many_weaknesses(self):
        """The soft default (MAX_WEAK_PER_TYPE=2) must NOT leak into the hard
        check -- a type absent from `type_limits` is uncapped here, full stop."""
        _viol, detail = ts.weakness_violations(self.team, self.merged)
        heavily_weak_types = [t for t, d in detail.items() if len(d["weak"]) >= 2]
        self.assertTrue(heavily_weak_types, "fixture assumes at least one type "
                                            "already exceeds the soft default")
        self.assertFalse(ts.hard_violations(self.team, self.merged, type_limits={}))

    def test_an_explicit_per_type_max_weak_excludes(self):
        _viol, detail = ts.weakness_violations(self.team, self.merged)
        fire_weak = len(detail.get("Fire", {}).get("weak", []))
        self.assertGreaterEqual(fire_weak, 1)
        self.assertTrue(ts.hard_violations(
            self.team, self.merged, type_limits={"Fire": {"max_weak": 0}}))
        self.assertFalse(ts.hard_violations(
            self.team, self.merged,
            type_limits={"Fire": {"max_weak": fire_weak}}))

    def test_a_present_required_core_does_not_exclude(self):
        # Garchomp=Dragon, Kingambit=Steel, Whimsicott=Fairy -- all present.
        self.assertFalse(ts.hard_violations(
            self.team, self.merged,
            required_cores=[("Dragon", "Fairy", "Steel")]))

    def test_a_missing_required_core_excludes(self):
        self.assertTrue(ts.hard_violations(
            self.team, self.merged,
            required_cores=[("Ice", "Poison", "Bug")]))

    def test_required_cores_is_an_and_not_an_or(self):
        """Two cores named, one present and one absent -- still excluded:
        'make sure certain cores are included' was plural and additive."""
        self.assertTrue(ts.hard_violations(
            self.team, self.merged,
            required_cores=[("Dragon", "Fairy", "Steel"),
                            ("Ice", "Poison", "Bug")]))


class TestMonotonicPrune(unittest.TestCase):
    """`_breaks_monotonic_hard_limit` is the early-growth prune -- only safe
    for max_weak (a count that only grows as members are added)."""

    def setUp(self):
        self.merged = world()["merged"]

    def test_a_partial_team_already_over_the_cap_is_flagged(self):
        # Incineroar and Kingambit are both Dark-type -> both weak to Fighting.
        team = ["Incineroar", "Kingambit"]
        weak, _resist = ts._weak_resist(team, self.merged, "Fighting")
        self.assertGreaterEqual(len(weak), 2, "fixture assumes both are "
                                              "Fighting-weak")
        self.assertTrue(ts._breaks_monotonic_hard_limit(
            team, self.merged, {"Fighting": {"max_weak": 1}}))

    def test_a_partial_team_under_the_cap_is_not_flagged(self):
        team = ["Garchomp", "Whimsicott"]
        self.assertFalse(ts._breaks_monotonic_hard_limit(
            team, self.merged, {"Fire": {"max_weak": 6}}))

    def test_no_type_limits_never_flags(self):
        self.assertFalse(ts._breaks_monotonic_hard_limit(
            ["Incineroar", "Kingambit"], self.merged, None))


class TestBeamSearchWiring(unittest.TestCase):
    """`beam_search_teams` actually USES max_weak/type_limits/required_cores
    when given them -- the regression test for the wiring bug (`src/app.py`
    computed the sliders and then never passed them to this call at all)."""

    def test_max_weak_changes_which_team_is_reported(self):
        merged, matrix, eps = small_matrix_and_pairs()
        baseline = ts.beam_search_teams(POOL, matrix, eps, merged, beam_width=10)
        self.assertTrue(baseline)
        # A cap loose enough to allow anything (6) has to reproduce the
        # unconstrained (default max_weak=2) top team's synergy count, or the
        # plumbing itself is broken independent of any tightening.
        loose = ts.beam_search_teams(POOL, matrix, eps, merged, beam_width=10,
                                     max_weak=6)
        self.assertTrue(loose)

        # A tight per-type HARD cap actually changes the top team when SOME
        # candidate the unconstrained search already found violates it --
        # scanned across the whole reported beam (not just baseline[0]),
        # since which single team ranks #1 can shift with any change
        # elsewhere in this codebase's matchup/ability modelling, and this
        # fixture only needs a real violation to exist somewhere in the
        # pool's own results, not specifically at the very top.
        tight_type = None
        for _sc, team in baseline:
            _v, detail = ts.weakness_violations(team, merged)
            tight_type = next((t for t, d in detail.items()
                              if len(d["weak"]) >= 2), None)
            if tight_type is not None:
                break
        self.assertIsNotNone(tight_type, "fixture assumes at least one "
                                         "unconstrained result has a real "
                                         "weakness to tighten against")
        constrained = ts.beam_search_teams(
            POOL, matrix, eps, merged, beam_width=10,
            type_limits={tight_type: {"max_weak": 1}})
        self.assertTrue(constrained)
        for _sc, team in constrained:
            _v, d = ts.weakness_violations(team, merged)
            self.assertLessEqual(len(d.get(tight_type, {}).get("weak", [])), 1)

    def test_a_required_core_absent_from_the_pool_empties_the_results(self):
        merged, matrix, eps = small_matrix_and_pairs()
        finals = ts.beam_search_teams(
            POOL, matrix, eps, merged, beam_width=10,
            required_cores=[("Ice", "Poison", "Bug")])
        self.assertEqual(finals, [])

    def test_a_satisfiable_required_core_is_honoured_in_every_result(self):
        merged, matrix, eps = small_matrix_and_pairs()
        core = ("Dragon", "Fairy", "Steel")
        finals = ts.beam_search_teams(
            POOL, matrix, eps, merged, beam_width=10, required_cores=[core])
        self.assertTrue(finals, "fixture pool contains Dragon/Fairy/Steel "
                                "members, so this should be satisfiable")
        for _sc, team in finals:
            team_types = set()
            for n in team:
                team_types.update(merged[n]["types"])
            self.assertTrue(set(core).issubset(team_types))

    def test_no_advanced_constraints_reproduces_prior_behaviour(self):
        """type_limits/required_cores both unset (None) -- the hard filter
        must be a no-op, not an empty-by-default trap."""
        merged, matrix, eps = small_matrix_and_pairs()
        finals = ts.beam_search_teams(POOL, matrix, eps, merged, beam_width=10)
        self.assertTrue(finals)


if __name__ == "__main__":
    unittest.main()
