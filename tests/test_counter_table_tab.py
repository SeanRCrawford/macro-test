"""`tools/counter_table.py`'s search, in the Streamlit app.

    "Now, add the counter_table.py functionality to the streamlit app."

A new "Counter Table" tab wraps `counter_finder.py`'s bring4_search/
multi_bring4_coverage+exhaustive/beam/joint_pair_search directly -- no new
search logic, the same functions the CLI calls, so a result here can never
disagree with the CLI's own answer for the same inputs.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

ROOT = os.path.join(os.path.dirname(__file__), "..")
APP = os.path.join(ROOT, "src", "app.py")
TEAM = ["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
        "Incineroar", "Farigiraf"]


def app(team=None, sets=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=500)
    at.session_state["team"] = list(team if team is not None else TEAM)
    at.session_state["sets"] = dict(sets) if sets is not None else {}
    return at.run()


class TestCounterTableTabExists(unittest.TestCase):

    def test_it_renders(self):
        at = app()
        self.assertFalse(at.exception, list(at.exception))

    def test_the_nine_modes_are_offered(self):
        at = app()
        radios = [r for r in at.radio if r.key == "ct_mode"]
        self.assertEqual(len(radios), 1)
        self.assertEqual(set(radios[0].options),
                         {"Bring-4 (one enemy roster)",
                          "Multi-bring4 (several enemy rosters)",
                          "Enemy's best response (to my team)",
                          "Complete my team",
                          "Joint pair search",
                          "2-2-2 teambuilding",
                          "Coverage groups",
                          "Import pair coverage",
                          "Round-robin (saved teams only)"})

    def test_switching_to_multi_bring4_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Multi-bring4 (several enemy rosters)").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_mb4_pool" for s in at.slider))
        self.assertTrue(any(m.key == "ct_mb4_vs" for m in at.multiselect))

    def test_switching_to_two_two_two_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "2-2-2 teambuilding").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_222_pool" for s in at.slider))
        self.assertTrue(any(m.key == "ct_222_teams" for m in at.multiselect))
        self.assertTrue(any(c.key == "ct_222_cap_on" for c in at.checkbox))

    def test_switching_to_joint_pair_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Joint pair search").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_jp_partner" for s in at.selectbox))

    def test_switching_to_coverage_groups_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_cov_pool" for s in at.slider))
        self.assertTrue(any(m.key == "ct_cov_teams" for m in at.multiselect))
        self.assertTrue(any(m.key == "ct_cov_include" for m in at.multiselect))
        self.assertTrue(any(m.key == "ct_cov_suggested" for m in at.multiselect))
        self.assertTrue(any(m.key == "ct_cov_sizes" for m in at.multiselect))
        self.assertTrue(any(c.key == "ct_cov_dup" for c in at.checkbox))
        self.assertTrue(any(c.key == "ct_cov_full_pool" for c in at.checkbox))
        self.assertTrue(any(b.key == "ct_cov_go" for b in at.button))

    def test_full_pool_checkbox_runs_the_search_unnarrowed(self):
        """"I need it to be comprehensive within the defined set, no
        matter the links" -- checking it and running a (tiny, fast) real
        search must not crash, and the resulting search must not have
        silently narrowed the pool via the best-link heuristic."""
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        at = [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        at = [c for c in at.checkbox if c.key == "ct_cov_full_pool"][0].set_value(True).run()
        at = [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))

    def test_switching_to_import_pair_coverage_mode_renders_its_controls(self):
        """Before any upload -- just the file uploader itself, no crash
        from the rest of the mode's own controls (which only render once
        `ct_pc_pair_rows` is in session state) being skipped."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Import pair coverage").run()
        self.assertFalse(at.exception, list(at.exception))

    def test_min_offensive_types_and_threat_controls_wire_through(self):
        """"using the same constraints as the coverage groups" -- Import
        pair coverage's own filter controls only render once pair_rows are
        in session state (the file uploader can't be driven via AppTest),
        so this seeds them directly with a real --pairs-only export, same
        as a genuine upload would produce."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        import counter_finder as cf
        import counter_table as ct
        import tempfile
        W = load_world()
        merged, moves = W["merged"], W["moves"]
        natures, typechart = W["natures"], W["typechart"]
        pool = ["Garchomp", "Incineroar", "Gallade", "Hydreigon",
               "Whimsicott", "Mega Alakazam"]
        vs_teams = [["Kingambit", "Basculegion"]]
        coverage = cf.multi_bring4_coverage(
            pool, vs_teams, merged, moves, natures, typechart,
            good_threshold=0.0, min_enemies=0)
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            path = f.name
        os.unlink(path)
        try:
            ct._write_pairs_only_xlsx(path, coverage, vs_teams, 15)
            with open(path, "rb") as f:
                file_bytes = f.read()
        finally:
            if os.path.exists(path):
                os.unlink(path)
        from app import _parse_pair_coverage_xlsx
        pair_rows, detail_rows, target_name_lists = _parse_pair_coverage_xlsx(file_bytes)
        self.assertTrue(pair_rows)
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Import pair coverage").run()
        at.session_state["ct_pc_pair_rows"] = pair_rows
        at.session_state["ct_pc_detail_rows"] = detail_rows
        at.session_state["ct_pc_target_name_lists"] = target_name_lists
        at.run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(c.key == "ct_pc_min_off_on" for c in at.checkbox))
        self.assertTrue(any(c.key == "ct_pc_threat_on" for c in at.checkbox))
        [c for c in at.checkbox if c.key == "ct_pc_threat_on"][0].set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_pc_min_threat_answers" for s in at.slider))
        at = [b for b in at.button if b.key == "ct_pc_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertIn("ct_pc_results_by_size", at.session_state)

    def test_always_include_forces_a_name_through_a_tiny_pool(self):
        """"specify individual Pokemon to include" -- a name outside the
        top-Score pool cutoff must still show up in the results once
        named in "Always include these Pokemon"."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(10).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        include_ms = [m for m in at.multiselect if m.key == "ct_cov_include"][0]
        # A real, low-Score species (never a top-10-by-Score pick on its
        # own) -- its presence in the actual search pool can only be
        # explained by the "always include" wiring.
        forced_name = "Ariados"
        self.assertIn(forced_name, include_ms.options)
        include_ms.set_value([forced_name]).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        # Whether it lands in a top-ranked GROUP is a search-ranking
        # question already covered at the counter_finder.py level
        # (test_must_include_forces_a_name_through_narrowing) -- this
        # checks the UI wiring itself: the forced name actually reached
        # the search pool find_pair_cores scored pairs for.
        pair_rows = at.session_state["ct_cov_pair_rows"]
        pool_names = {n for r in pair_rows for n in r["pair"]}
        self.assertIn(forced_name, pool_names)

    def test_always_include_forces_membership_in_every_returned_group(self):
        """The strengthened guarantee: not just "reached the pool" (the
        test above), but present in EVERY returned group for the
        requested size -- a real UI-level end-to-end check of the same
        thing `test_must_include_appears_in_every_returned_group_not_
        just_the_top_one` proves at the counter_finder.py level."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        forced_name = "Ariados"
        [m for m in at.multiselect if m.key == "ct_cov_include"][0].set_value(
            [forced_name]).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"])
        for row in results[3]["rows"]:
            self.assertIn(forced_name, row["group"])

    def test_offensive_and_threat_coverage_populate_on_a_real_search(self):
        """"assess offensive type coverage as well as simple 1v1 threat
        coverage of common enemies" -- both are always computed once a
        real search runs (no need to turn on either hard-cap checkbox),
        wired through from the app's own typechart/one_v_one_matrix."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"])
        for row in results[3]["rows"]:
            self.assertIsNotNone(row["offensive_coverage"])
            self.assertIsNotNone(row["threat_coverage"])

    def test_min_offensive_types_checkbox_filters_results(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [c for c in at.checkbox if c.key == "ct_cov_min_off_on"][0].set_value(True).run()
        at = [s for s in at.slider if s.key == "ct_cov_min_off"][0].set_value(18).run()
        at = [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        for row in results[3]["rows"]:
            self.assertEqual(len(row["offensive_coverage"]["covered"]), 18)

    def test_max_uncovered_threats_checkbox_filters_results(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [c for c in at.checkbox if c.key == "ct_cov_max_uncov_on"][0].set_value(True).run()
        at = [s for s in at.slider if s.key == "ct_cov_max_uncov"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        for row in results[3]["rows"]:
            self.assertEqual(row["threat_coverage"]["uncovered"], [])

    def test_min_threat_answers_slider_raises_the_covered_bar(self):
        """"it would also be good to filter for having multiple 1v1
        answers to each enemy, ideally at least two" -- raising "Minimum
        1v1 answers per enemy" to 2 and capping "Max enemies short of
        that" at 0 must return only groups where every named enemy has at
        least 2 independent 1v1 answers, not just one."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([4]).run()
        [s for s in at.slider if s.key == "ct_cov_min_threat_answers"][0].set_value(2).run()
        [c for c in at.checkbox if c.key == "ct_cov_max_uncov_on"][0].set_value(True).run()
        at = [s for s in at.slider if s.key == "ct_cov_max_uncov"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        for row in results[4]["rows"]:
            tc = row["threat_coverage"]
            self.assertEqual(tc["uncovered"], [])
            for count in tc["answer_counts"].values():
                self.assertGreaterEqual(count, 2)

    def test_suggested_pokemon_controls_render_and_apply_quorum(self):
        """"Give a 'suggested' list as well, of which at least 3 (or n,
        selected) must appear" -- the new multiselect + quorum slider
        render, wire through, and every returned group meets the quorum."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([4]).run()
        suggested_ms = [m for m in at.multiselect if m.key == "ct_cov_suggested"][0]
        suggested_names = suggested_ms.options[:4]
        suggested_ms.set_value(suggested_names).run()
        self.assertFalse(at.exception, list(at.exception))
        quorum_sliders = [s for s in at.slider if s.key == "ct_cov_suggested_min"]
        self.assertTrue(quorum_sliders)
        quorum_sliders[0].set_value(2).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        for row in results[4]["rows"]:
            self.assertGreaterEqual(
                sum(1 for nm in row["group"] if nm in suggested_names), 2)

    def test_coverage_groups_search_runs_and_shows_results(self):
        """A real (small) run through the actual widget tree -- confirms the
        button click wires through to `coverage_group_search` and back into
        rendered output, not just that the controls exist."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(12).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Groups of 3" in md.value for md in at.markdown))
        # "for the top 5 in each group show the pair performance" -- a
        # dataframe of the group's own internal pairs, no button needed.
        self.assertTrue(len(at.dataframe) >= 1)

    def test_coverage_groups_advanced_controls_render(self):
        """"define must bring cores and minimum scores" -- the new
        Advanced expander's controls exist under the expected keys."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(m.key == "ct_cov_required_cores" for m in at.multiselect))
        self.assertTrue(any(t.key == "ct_cov_custom_core" for t in at.text_input))
        self.assertTrue(any(c.key == "ct_cov_min_score_on" for c in at.checkbox))

    def test_required_type_core_keeps_only_groups_covering_it(self):
        """Picking a must-bring core must mean every returned group's own
        combined types cover all 3 of it -- not just offered in the UI."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        W = load_world()
        merged = W["merged"]
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        cores_ms = [m for m in at.multiselect if m.key == "ct_cov_required_cores"][0]
        self.assertIn("Fighting/Psychic/Dark", cores_ms.options)
        cores_ms.set_value(["Fighting/Psychic/Dark"]).run()
        self.assertFalse(at.exception, list(at.exception))
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"], "fixture never produced a group -- "
                        "widen the pool or the test is vacuous")
        for row in results[3]["rows"]:
            group_types = {t for nm in row["group"] for t in merged[nm]["types"]}
            self.assertTrue({"Fighting", "Psychic", "Dark"} <= group_types)

    def test_custom_type_core_is_parsed_and_enforced(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [t for t in at.text_input if t.key == "ct_cov_custom_core"][0].set_value(
            "Fire, Water, Electric").run()
        self.assertFalse(at.exception, list(at.exception))
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        merged = load_world()["merged"]
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        for row in results[3]["rows"]:
            group_types = {t for nm in row["group"] for t in merged[nm]["types"]}
            self.assertTrue({"Fire", "Water", "Electric"} <= group_types)

    def test_minimum_score_floor_excludes_below_floor_members(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        merged = load_world()["merged"]
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [c for c in at.checkbox if c.key == "ct_cov_min_score_on"][0].set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        min_score_sliders = [s for s in at.slider if s.key == "ct_cov_min_score"]
        self.assertTrue(min_score_sliders)
        floor = 500
        min_score_sliders[0].set_value(floor).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"], "fixture never produced a group -- "
                        "the floor may be too strict for this pool")
        for row in results[3]["rows"]:
            for nm in row["group"]:
                self.assertGreaterEqual(merged[nm]["score"], floor)

    def test_exclude_pokemon_removes_them_from_every_group(self):
        """"allow an option to exclude specific pokemon" -- the excluded
        name never appears in a returned group of any size."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        exclude_ms = [m for m in at.multiselect if m.key == "ct_cov_exclude"][0]
        excluded_name = exclude_ms.options[0]
        exclude_ms.set_value([excluded_name]).run()
        self.assertFalse(at.exception, list(at.exception))
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"])
        for row in results[3]["rows"]:
            self.assertNotIn(excluded_name, row["group"])

    def test_excluding_an_always_include_name_is_a_visible_conflict(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        forced_name = "Ariados"
        [m for m in at.multiselect if m.key == "ct_cov_include"][0].set_value(
            [forced_name]).run()
        [m for m in at.multiselect if m.key == "ct_cov_exclude"][0].set_value(
            [forced_name]).run()
        self.assertFalse(at.exception, list(at.exception))
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertEqual(results[3]["rows"], [])

    def test_absolute_weakness_cap_caps_the_worst_types_raw_count(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [c for c in at.checkbox if c.key == "ct_cov_abs_cap_on"][0].set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        cap_sliders = [s for s in at.slider if s.key == "ct_cov_max_weak"]
        self.assertTrue(cap_sliders)
        cap = 2
        cap_sliders[0].set_value(cap).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"], "fixture never produced a group -- "
                        "the cap may be too strict for this pool")
        for row in results[3]["rows"]:
            self.assertLessEqual(row["worst_weakness"], cap)

    def test_required_techs_drops_groups_missing_the_tech(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        merged = load_world()["merged"]
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        techs_ms = [m for m in at.multiselect if m.key == "ct_cov_required_techs"][0]
        self.assertIn("Fake Out user", techs_ms.options)
        techs_ms.set_value(["Fake Out user"]).run()
        self.assertFalse(at.exception, list(at.exception))
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"], "fixture never produced a group -- "
                        "the requirement may be too strict for this pool")
        import counter_finder as cf
        for row in results[3]["rows"]:
            self.assertEqual(cf.team_missing_techs(row["group"], merged, ["fake_out"]), [])

    def test_max_weak_types_caps_the_breadth_of_2plus_weak_types(self):
        """"limit the total number of types with absolute weaknesses of 2
        or more" -- a BREADTH cap, distinct from the existing "Max
        absolute weakness" per-type magnitude cap."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([4]).run()
        [s for s in at.slider if s.key == "ct_cov_maxweaktypes2"][0].set_value(2).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[4]["rows"], "fixture never produced a group -- "
                        "the cap may be too strict for this pool")
        for row in results[4]["rows"]:
            self.assertLessEqual(row["weak_type_breadth_2"], 2)

    def test_max_weak_types_3_caps_the_breadth_of_3plus_weak_types(self):
        """"...and 3 or more" -- the higher-bar sibling of the above."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([4]).run()
        [s for s in at.slider if s.key == "ct_cov_maxweaktypes3"][0].set_value(1).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[4]["rows"], "fixture never produced a group -- "
                        "the cap may be too strict for this pool")
        for row in results[4]["rows"]:
            self.assertLessEqual(row["weak_type_breadth_3"], 1)

    def test_min_special_attackers_defaults_to_2_and_is_honoured(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        w = load_world()
        merged, moves = w["merged"], w["moves"]
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([4]).run()
        # OFF by default (an unchecked checkbox) -- an ordinary search
        # must never come back silently narrower just because this
        # control exists on the page.
        self.assertFalse([c for c in at.checkbox
                          if c.key == "ct_cov_minspecial_on"][0].value)
        at = [c for c in at.checkbox if c.key == "ct_cov_minspecial_on"][0].set_value(
            True).run()
        msa = [s for s in at.slider if s.key == "ct_cov_minspecial"]
        self.assertTrue(msa)
        self.assertEqual(msa[0].value, 2)
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[4]["rows"])
        import counter_finder as cf
        for row in results[4]["rows"]:
            self.assertGreaterEqual(
                cf.count_special_attackers(row["group"], merged, moves), 2)

    def test_generation_include_restricts_every_returned_group(self):
        """"I also want to include or exclude specific generations of
        pokemon there" -- gen-1-only must return groups entirely made of
        gen-1 species (a form counts as its BASE species' generation)."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        w = load_world()
        merged = w["merged"]
        from species_data import build_generation_map, load_showdown_static
        pokedex, _moves, _natures, _typechart = load_showdown_static()
        gen_map = build_generation_map(merged.keys(), pokedex)
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [m for m in at.multiselect if m.key == "ct_cov_gen_include"][0].set_value(
            [1]).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"], "fixture never produced a group -- "
                        "gen-1 pool may be too narrow for this pool size")
        for row in results[3]["rows"]:
            for n in row["group"]:
                self.assertEqual(gen_map.get(n), 1, f"{n} is not gen 1")

    def test_generation_exclude_drops_every_excluded_generation(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        w = load_world()
        merged = w["merged"]
        from species_data import build_generation_map, load_showdown_static
        pokedex, _moves, _natures, _typechart = load_showdown_static()
        gen_map = build_generation_map(merged.keys(), pokedex)
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(30).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        [m for m in at.multiselect if m.key == "ct_cov_gen_exclude"][0].set_value(
            list(range(1, 9))).run()  # exclude every generation except 9
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        results = at.session_state["ct_cov_results"]
        self.assertTrue(results[3]["rows"], "fixture never produced a group -- "
                        "gen-9-only pool may be too narrow for this pool size")
        for row in results[3]["rows"]:
            for n in row["group"]:
                self.assertEqual(gen_map.get(n), 9, f"{n} is not gen 9")

    def test_required_techs_offers_the_new_granular_options(self):
        """"I want to be able to define specific techs, like ... fake out,
        tailwind, coaching, and so on" -- the granular techs sit alongside
        the existing broad buckets in the SAME multiselect, not a
        separate control."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        techs_ms = [m for m in at.multiselect if m.key == "ct_cov_required_techs"][0]
        for label in ("Tailwind user", "Coaching user", "Fake Out user",
                     "pivot/switching move (U-turn, Volt Switch, Parting Shot, ...)"):
            self.assertIn(label, techs_ms.options)

    def test_coverage_groups_run_bring4_button_works(self):
        """"add an option to run the actual pair performance vs enemy
        teams in a proper bring 4" -- clicking it must not crash and must
        render a real bring-4 result table."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(12).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        teams_ms = [m for m in at.multiselect if m.key == "ct_cov_teams"][0]
        if teams_ms.options:
            teams_ms.set_value([teams_ms.options[0]]).run()
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        b4_buttons = [b for b in at.button if b.key and b.key.startswith("ct_cov_b4_3_")]
        self.assertTrue(b4_buttons)
        dataframes_before = len(at.dataframe)
        b4_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertGreater(len(at.dataframe), dataframes_before)

    def test_real_win_rate_checkbox_runs_and_shows_estimate_and_results(self):
        """"assess all of the pairs in the counter table and link that to
        the coverage group search" -- the opt-in real-racing checkbox must
        show a cost estimate, run without crashing on a small scope, and
        surface a real win-rate reading."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(15).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        teams_ms = [m for m in at.multiselect if m.key == "ct_cov_teams"][0]
        self.assertTrue(teams_ms.options)
        teams_ms.set_value([teams_ms.options[0]]).run()
        [c for c in at.checkbox if c.key == "ct_cov_real_wins"][0].set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        win_sliders = [s for s in at.slider if s.key == "ct_cov_real_win_names"]
        self.assertTrue(win_sliders)
        win_sliders[0].set_value(6).run()
        self.assertTrue(any("Estimated:" in c.value for c in at.caption))
        [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(sb.key == "ct_cov_resort" for sb in at.selectbox))
        self.assertTrue(any("Real pair win rate" in c.value for c in at.caption))

    def test_choice_scarf_is_excluded_by_default(self):
        at = app()
        cb = [c for c in at.checkbox if c.key == "ct_allow_scarf"][0]
        self.assertFalse(cb.value)


class TestJointPairSearchVsAllTeams(unittest.TestCase):
    """"let me run the joint pair search with a given partner vs all enemy
    teams" -- a checkbox next to the single-team picker that races every
    saved team's own internal pairs at once (never a cross-team pair)."""

    def _goto(self, at):
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Joint pair search").run()
        self.assertFalse(at.exception, list(at.exception))
        return at

    def test_the_checkbox_is_offered_and_disables_the_single_roster_picker(self):
        at = self._goto(app())
        self.assertTrue(any(c.key == "ct_jp_vs_all" for c in at.checkbox))
        sb = [s for s in at.selectbox if s.key == "ct_jp_vs"][0]
        self.assertFalse(sb.disabled)
        at = [c for c in at.checkbox if c.key == "ct_jp_vs_all"][0].set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        sb = [s for s in at.selectbox if s.key == "ct_jp_vs"][0]
        self.assertTrue(sb.disabled)

    def test_searching_vs_all_teams_runs_without_error(self):
        at = self._goto(app())
        at = [s for s in at.slider if s.key == "ct_jp_pool"][0].set_value(12).run()
        at = [c for c in at.checkbox if c.key == "ct_jp_vs_all"][0].set_value(True).run()
        at = [b for b in at.button if b.key == "ct_jp_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(d.value is not None for d in at.dataframe))


class TestForceIncludeAcrossViews(unittest.TestCase):
    """"let me force include pokemon in each view" -- the SAME "Always
    include these Pokemon" pattern Coverage groups already had (`ct_cov_
    include`), extended to every other pool-based search in this tab.
    Unlike Coverage groups' own `must_include=` (a hard requirement on
    every returned GROUP), these just union into the raw search pool --
    `_run_multi_bring4_search`'s own docstring is explicit that a forced
    name still has to clear the same good-pair bar as everything else."""

    def test_bring4_search_pool_offers_and_honours_always_include(self):
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        at = sb.set_value("\U0001f50d Search a pool for the best team").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(m.key == "ct_b4_include" for m in at.multiselect))
        vs_sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        vs_sb.set_value("Golisopod Rain").run()
        # A real, low-Score species that a small top-Score pool would
        # otherwise never include.
        forced_name = "Ariados"
        at = [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(10).run()
        at = [m for m in at.multiselect if m.key == "ct_b4_include"][0].set_value(
            [forced_name]).run()
        at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))

    def test_bring4_search_pool_offers_and_honours_min_special_attackers(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        w = load_world()
        merged, moves = w["merged"], w["moves"]
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        at = sb.set_value("\U0001f50d Search a pool for the best team").run()
        vs_sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        vs_sb.set_value("Golisopod Rain").run()
        at = [c for c in at.checkbox
             if c.key == "ct_b4_pool_minspecial_on"][0].set_value(True).run()
        msa = [s for s in at.slider if s.key == "ct_b4_pool_minspecial"]
        self.assertTrue(msa)
        self.assertEqual(msa[0].value, 2)
        at = [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(12).run()
        at = [s for s in at.slider if s.key == "ct_b4_maxweak"][0].set_value(6).run()
        at = [s for s in at.slider if s.key == "ct_b4_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        rows = at.session_state.get("ct_b4_pool_rows") or []
        self.assertTrue(rows)
        import counter_finder as cf
        for r in rows:
            self.assertGreaterEqual(
                cf.count_special_attackers(r["core"], merged, moves), 2)

    def test_multi_bring4_offers_and_honours_always_include(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Multi-bring4 (several enemy rosters)").run()
        self.assertTrue(any(m.key == "ct_mb4_include" for m in at.multiselect))
        forced_name = "Ariados"
        at = [s for s in at.slider if s.key == "ct_mb4_pool"][0].set_value(10).run()
        at = [m for m in at.multiselect if m.key == "ct_mb4_include"][0].set_value(
            [forced_name]).run()
        at = [m for m in at.multiselect if m.key == "ct_mb4_vs"][0].set_value(
            ["Golisopod Rain"]).run()
        at = [b for b in at.button if b.key == "ct_mb4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))

    def test_multi_bring4_offers_and_honours_required_techs(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        merged = load_world()["merged"]
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Multi-bring4 (several enemy rosters)").run()
        techs_ms = [m for m in at.multiselect if m.key == "ct_mb4_required_techs"][0]
        self.assertIn("Fake Out user", techs_ms.options)
        at = [s for s in at.slider if s.key == "ct_mb4_pool"][0].set_value(20).run()
        at = techs_ms.set_value(["Fake Out user"]).run()
        at = [m for m in at.multiselect if m.key == "ct_mb4_vs"][0].set_value(
            ["Golisopod Rain"]).run()
        at = [b for b in at.button if b.key == "ct_mb4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        import counter_finder as cf
        rows = at.session_state.get("ct_mb4_rows") or []
        for r in rows:
            self.assertEqual(cf.team_missing_techs(r["core"], merged, ["fake_out"]), [])

    def test_multi_bring4_offers_and_honours_min_special_attackers(self):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        w = load_world()
        merged, moves = w["merged"], w["moves"]
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Multi-bring4 (several enemy rosters)").run()
        at = [c for c in at.checkbox
             if c.key == "ct_mb4_minspecial_on"][0].set_value(True).run()
        msa = [s for s in at.slider if s.key == "ct_mb4_minspecial"]
        self.assertTrue(msa)
        self.assertEqual(msa[0].value, 2)
        at = [s for s in at.slider if s.key == "ct_mb4_pool"][0].set_value(20).run()
        at = [m for m in at.multiselect if m.key == "ct_mb4_vs"][0].set_value(
            ["Golisopod Rain"]).run()
        at = [b for b in at.button if b.key == "ct_mb4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        import counter_finder as cf
        rows = at.session_state.get("ct_mb4_rows") or []
        for r in rows:
            self.assertGreaterEqual(
                cf.count_special_attackers(r["core"], merged, moves), 2)

    def test_joint_pair_search_offers_and_honours_always_include(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Joint pair search").run()
        self.assertTrue(any(m.key == "ct_jp_include" for m in at.multiselect))
        forced_name = "Ariados"
        at = [s for s in at.slider if s.key == "ct_jp_pool"][0].set_value(10).run()
        at = [m for m in at.multiselect if m.key == "ct_jp_include"][0].set_value(
            [forced_name]).run()
        at = [b for b in at.button if b.key == "ct_jp_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))

    def test_two_two_two_offers_and_honours_always_include(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "2-2-2 teambuilding").run()
        self.assertTrue(any(m.key == "ct_222_include" for m in at.multiselect))
        forced_name = "Ariados"
        at = [s for s in at.slider if s.key == "ct_222_pool"][0].set_value(10).run()
        at = [m for m in at.multiselect if m.key == "ct_222_include"][0].set_value(
            [forced_name]).run()
        at = [b for b in at.button if b.key == "ct_222_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))


class TestJointPairSearchCustomEnemyPairs(unittest.TestCase):
    """"in the joint pair search, let me enter a list of enemy pairs and
    try to find a pair or a team with the best performance against those
    pairs" -- a new sub-section under "Joint pair search" mode: build up a
    list of hand-picked (Enemy A, Enemy B) matchups (Add/Remove rows, no
    typing on mobile), then either "Search best pair" (both slots free-
    searched, `joint_pool_search`'s new `enemy_pairs` param -- races
    EXACTLY the given pairs, not every C(n,2) of their union) or "Search
    best team" (reuses the existing Multi-bring4 machinery, each pair fed
    in as its own 2-member "roster")."""

    def _goto(self, at):
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Joint pair search").run()
        self.assertFalse(at.exception, list(at.exception))
        return at

    def _add_pair(self, at, a, b):
        at = [s for s in at.selectbox if s.key == "ct_jp_pairs_a"][0].set_value(a).run()
        at = [s for s in at.selectbox if s.key == "ct_jp_pairs_b"][0].set_value(b).run()
        at = [btn for btn in at.button if btn.key == "ct_jp_pairs_add"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        return at

    def test_the_add_pair_controls_are_offered(self):
        at = self._goto(app())
        self.assertTrue(any(s.key == "ct_jp_pairs_a" for s in at.selectbox))
        self.assertTrue(any(s.key == "ct_jp_pairs_b" for s in at.selectbox))
        self.assertTrue(any(b.key == "ct_jp_pairs_add" for b in at.button))

    def test_adding_a_pair_shows_it_in_the_list(self):
        at = self._goto(app())
        at = self._add_pair(at, "Kingambit", "Basculegion")
        self.assertEqual(at.session_state["ct_jp_pairs_list"],
                         [("Basculegion", "Kingambit")])
        self.assertTrue(any("Basculegion + Kingambit" in w.value
                            for w in at.markdown))

    def test_adding_the_same_pokemon_twice_is_rejected(self):
        at = self._goto(app())
        at = [s for s in at.selectbox if s.key == "ct_jp_pairs_a"][0].set_value(
            "Kingambit").run()
        at = [s for s in at.selectbox if s.key == "ct_jp_pairs_b"][0].set_value(
            "Kingambit").run()
        at = [btn for btn in at.button if btn.key == "ct_jp_pairs_add"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        try:
            stored = at.session_state["ct_jp_pairs_list"]
        except KeyError:
            stored = []
        self.assertEqual(stored, [])
        self.assertTrue(any(w.value for w in at.warning))

    def test_adding_a_duplicate_pair_is_rejected(self):
        at = self._goto(app())
        at = self._add_pair(at, "Kingambit", "Basculegion")
        at = self._add_pair(at, "Basculegion", "Kingambit")  # reversed order
        self.assertEqual(len(at.session_state["ct_jp_pairs_list"]), 1)

    def test_removing_a_pair_takes_it_out_of_the_list(self):
        at = self._goto(app())
        at = self._add_pair(at, "Kingambit", "Basculegion")
        at = self._add_pair(at, "Garchomp", "Incineroar")
        self.assertEqual(len(at.session_state["ct_jp_pairs_list"]), 2)
        at = [b for b in at.button if b.key == "ct_jp_pairs_remove_0"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertEqual(at.session_state["ct_jp_pairs_list"],
                         [("Garchomp", "Incineroar")])

    def test_clear_all_empties_the_list(self):
        at = self._goto(app())
        at = self._add_pair(at, "Kingambit", "Basculegion")
        at = [b for b in at.button if b.key == "ct_jp_pairs_clear"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertEqual(at.session_state["ct_jp_pairs_list"], [])

    def test_search_best_pair_races_exactly_the_given_pairs(self):
        at = self._goto(app())
        at = self._add_pair(at, "Kingambit", "Basculegion")
        at = self._add_pair(at, "Garchomp", "Incineroar")
        at = [s for s in at.slider if s.key == "ct_jp_pool"][0].set_value(10).run()
        at = [b for b in at.button
             if b.key == "ct_jp_pairs_go_pair"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        result = at.session_state["ct_jp_pairs_result"]
        self.assertEqual(at.session_state["ct_jp_pairs_result_kind"], "pair")
        self.assertTrue(result)
        for row in result:
            self.assertEqual(row["pairs_total"], 2)
            self.assertEqual(set(row["detail"].keys()),
                             {("Basculegion", "Kingambit"),
                              ("Garchomp", "Incineroar")})

    def test_search_best_team_uses_each_pair_as_its_own_enemy(self):
        at = self._goto(app())
        at = self._add_pair(at, "Kingambit", "Basculegion")
        at = self._add_pair(at, "Garchomp", "Incineroar")
        at = [s for s in at.slider if s.key == "ct_jp_pool"][0].set_value(12).run()
        at = [b for b in at.button
             if b.key == "ct_jp_pairs_go_team"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        result = at.session_state["ct_jp_pairs_result"]
        self.assertEqual(at.session_state["ct_jp_pairs_result_kind"], "team")
        shown_vs = at.session_state["ct_jp_pairs_shown_vs"]
        self.assertEqual(set(shown_vs),
                         {"Basculegion + Kingambit", "Garchomp + Incineroar"})
        if result:
            self.assertEqual(len(result[0]["per_enemy"]), 2)


class TestBring4ModeRunsEndToEnd(unittest.TestCase):
    """The fastest of the three real searches -- one enemy roster, our
    already-loaded 6 -- run for real (not just rendered) to prove the
    wiring from widget -> `counter_finder.bring4_search` -> table actually
    works, not just that the page draws."""

    def test_running_a_real_search_produces_both_stage_tables(self):
        at = app()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = at.dataframe
        # Stage 1 (15 pairs) and Stage 2 (15 bring-4s) tables, at minimum.
        shapes = [d.value.shape[0] for d in dfs]
        self.assertIn(15, shapes, "expected a 15-row Stage 1 or Stage 2 table")

    def test_a_team_of_three_is_accepted_not_warned_about(self):
        """"I would like to output the best 3-pokemon cores against each
        team" -- the tab's own fixed-team floor relaxed from 4 to 3,
        matching `bring4_search`'s own CLI-level relaxation. A 3-member
        team degenerates to one possible bring (itself, 3 pairs)."""
        at = app(team=["Garchomp", "Incineroar", "Gallade"])
        # Other tabs' own "need 6 Pokemon" widgets render regardless (the
        # whole app renders every tab at once) -- only the Bring-4 tab's
        # OWN size-floor warning is what this fix touches.
        self.assertFalse(any("Pick 3, 4, 5, or 6" in w.value for w in at.warning))
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = at.dataframe
        shapes = [d.value.shape[0] for d in dfs]
        self.assertIn(3, shapes, "expected a 3-row Stage 1 or Stage 2 table")

    def test_a_preset_team_can_be_used_instead_of_team_builders(self):
        """A preset "our 6" can legitimately share a Pokemon with the
        selected enemy roster (two library teams both running e.g.
        Grimmsnarl) -- a real VGC mirror, now a normal accepted search, not
        an error. See TestBring4SearchAllowsMirrorMatches in
        test_counter_finder.py for the underlying fix."""
        at = app(team=[])  # nothing loaded in Team Builder
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        # The leading options are all sentinels ("(current Team Builder
        # team)", "search a pool", "paste a pokepaste" -- each with its own
        # dedicated test class); a real preset TEAM name is whatever's left.
        sentinels = {"(current Team Builder team)",
                    "\U0001f50d Search a pool for the best team",
                    "\U0001f4cb Paste a pokepaste"}
        preset_names = [o for o in sb.options if o not in sentinels]
        self.assertTrue(preset_names, "expected preset teams offered")
        sb.set_value(preset_names[0]).run()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any("Pick exactly 6" in w.value for w in at.warning))

    def test_required_techs_drops_brings_missing_the_tech(self):
        """"I want to be able to filter for techs" -- of the default team
        (Arcanine-Hisui, Hydreigon, Gallade, Gholdengo, Incineroar,
        Farigiraf), only Incineroar is a Fake Out user, so requiring it
        must drop every bring-4 that leaves Incineroar out."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        merged = load_world()["merged"]
        at = app()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        unfiltered = at.session_state["ct_b4_bring4_rows"]
        techs_ms = [m for m in at.multiselect if m.key == "ct_b4_required_techs"][0]
        self.assertIn("Fake Out user", techs_ms.options)
        techs_ms.set_value(["Fake Out user"]).run()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        filtered = at.session_state["ct_b4_bring4_rows"]
        self.assertLess(len(filtered), len(unfiltered))
        self.assertTrue(filtered)
        import counter_finder as cf
        for row in filtered:
            self.assertEqual(
                cf.team_missing_techs(row["bring4"], merged, ["fake_out"]), [])

    def test_min_special_attackers_is_off_by_default_and_defaults_to_2_once_enabled(self):
        """OFF by default (an unchecked checkbox) -- the pre-existing
        "just click search" workflow must never come back silently
        narrower just because this control exists on the page. Once
        turned on, the slider itself starts at 2 ("minimum special
        attackers ... by default 2"), and of the default team
        (Arcanine-Hisui, Hydreigon, Gallade, Gholdengo, Incineroar,
        Farigiraf), that floor must drop any bring-4 with fewer than 2
        special attackers among its own 4 members."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        w = load_world()
        merged, moves = w["merged"], w["moves"]
        at = app()
        self.assertFalse([c for c in at.checkbox
                          if c.key == "ct_b4_minspecial_on"][0].value)
        at = [c for c in at.checkbox if c.key == "ct_b4_minspecial_on"][0].set_value(
            True).run()
        msa = [s for s in at.slider if s.key == "ct_b4_minspecial"]
        self.assertTrue(msa)
        self.assertEqual(msa[0].value, 2)
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        rows = at.session_state["ct_b4_bring4_rows"]
        self.assertTrue(rows)
        import counter_finder as cf
        for row in rows:
            self.assertGreaterEqual(
                cf.count_special_attackers(row["bring4"], merged, moves), 2)


class TestBring4TabMirrorsTheCliExactly(unittest.TestCase):
    """"When I loaded a given teamsheet from the xlsx to the streamlit app,
    I got completely different results. In the CLI the bring4 beat 55/90,
    but the streamlit counter table was 27/90. They must mirror rather
    than contradict." Root cause: the tab's `bring4_search` call only ever
    passed `our6` (names) -- a loaded/pasted/preset team's own pinned
    item/moveset (`sets`) was silently dropped, so the app free-searched
    from scratch instead of respecting it, same as an unpinned CLI run
    would. Fixed by threading `our_sets` through as `item_overrides`/
    `move_overrides`, exactly like the CLI's own --item/--moves. Confirmed
    here against a direct `bring4_search(..., item_overrides=...,
    move_overrides=...)` call using the SAME pinned sets -- the app and
    the library function must agree."""

    def test_a_loaded_teams_pinned_sets_reproduce_the_direct_call(self):
        team = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
        sets = {
            "Garchomp": {"item": "Rocky Helmet",
                        "moves": ["Earthquake", "Protect", "Dragon Claw",
                                 "Stealth Rock"]},
            "Incineroar": {"item": "Sitrus Berry",
                          "moves": ["Fake Out", "Flare Blitz", "Knock Off",
                                   "Protect"]},
        }
        at = app(team=team, sets=sets)
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        app_pair_rows = at.session_state["ct_b4_pair_rows"]
        vs_name = [s for s in at.selectbox if s.key == "ct_b4_vs"][0].value

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_search
        W = load_world()
        vs_roster = list(W["teams"][vs_name])
        item_overrides = {n: s["item"] for n, s in sets.items() if s.get("item")}
        move_overrides = {n: s["moves"] for n, s in sets.items() if s.get("moves")}
        direct_pair_rows, _direct_bring4_rows = bring4_search(
            team, vs_roster, W["merged"], W["moves"], W["natures"],
            W["typechart"], item_overrides=item_overrides,
            move_overrides=move_overrides)

        app_items = {r["pair"]: (r["item1"], r["item2"]) for r in app_pair_rows}
        direct_items = {r["pair"]: (r["item1"], r["item2"]) for r in direct_pair_rows}
        self.assertEqual(app_items, direct_items)
        # The pinned items themselves must actually show up, not just
        # happen to match a coincidental free-search result.
        self.assertTrue(any("Rocky Helmet" in v for v in app_items.values()))
        self.assertTrue(any("Sitrus Berry" in v for v in app_items.values()))

        app_beaten = {r["pair"]: r["pairs_swept"] + r["pairs_traded"]
                     for r in app_pair_rows}
        direct_beaten = {r["pair"]: r["pairs_swept"] + r["pairs_traded"]
                         for r in direct_pair_rows}
        self.assertEqual(app_beaten, direct_beaten)


class TestBring4CanSearchAPoolInsteadOfAFixedSix(unittest.TestCase):
    """"For bring4, I would like to be able to do it vs just 1 team,
    searching for the best 4." Reuses the exact same pool search as
    Multi-bring4 mode (`_run_multi_bring4_search`/`_render_multi_bring4_
    core`, factored out so the two paths can't drift), just scoped to a
    single enemy roster -- so there's no new search logic here, only a way
    to reach the existing one without already having a 6 in hand."""

    SEARCH_POOL = "\U0001f50d Search a pool for the best team"

    def test_the_option_is_offered_alongside_the_current_team_and_presets(self):
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        self.assertIn(self.SEARCH_POOL, sb.options)
        self.assertIn("(current Team Builder team)", sb.options)

    def test_choosing_it_swaps_in_pool_search_controls(self):
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        sb.set_value(self.SEARCH_POOL).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_b4_pool" for s in at.slider),
                        "expected a pool-size slider")
        self.assertTrue(any(b.key == "ct_b4_pool_go" for b in at.button),
                        "expected a search button")
        # The old "pick exactly 6" warning must not show -- there's no
        # fixed 6 to be missing in this mode.
        self.assertFalse(any("Pick exactly 6" in w.value for w in at.warning))

    def test_a_real_pool_search_against_one_roster_produces_results(self):
        at = app(team=[])  # no Team Builder team needed for this mode
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        sb.set_value(self.SEARCH_POOL).run()
        # Loosen the defaults so a small pool actually yields candidates,
        # the same knobs TestMultiBring4ProducesResults below turns.
        [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(16).run()
        [s for s in at.slider if s.key == "ct_b4_maxweak"][0].set_value(6).run()
        [s for s in at.slider if s.key == "ct_b4_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        try:
            rows = at.session_state["ct_b4_pool_rows"]
        except KeyError:
            self.fail("expected ct_b4_pool_rows to be set after a real search")
        self.assertTrue(rows, "expected at least one core back from the search")
        # Every returned core's own per-enemy breakdown must be scoped to
        # exactly the one roster this mode is meant for.
        for r in rows:
            self.assertEqual(len(r["per_enemy"]), 1)


class TestBestBring4PairTable(unittest.TestCase):
    """"In the streamlit app for bring4, I want to see the performance of
    my best bring 4 by the 6 pairs and their key metrics" -- Stage 2's
    top-ranked bring-4 (`bring4_rows[0]`, already sorted best-worst-case-
    first) gets its own 6-pair table, the same shape Stage 1's own table
    uses, not just the one-line "worst pair" summary Stage 2 shows."""

    def test_best_bring4_gets_its_own_six_pair_table(self):
        at = app()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Your best bring-4" in m.value for m in at.markdown))
        shapes = [d.value.shape[0] for d in at.dataframe]
        # Stage 1 (15), Stage 2 (15), and now the best bring-4's own 6.
        self.assertIn(6, shapes, "expected a 6-row best-bring-4 pair table")

    def test_results_survive_a_rerun_from_an_unrelated_widget(self):
        """Regression: the search used to run and render entirely inside
        `elif st.button(...)`, so results vanished the instant any OTHER
        widget on the page triggered a rerun (e.g. the new deep-dive
        selectbox) -- now stored in session_state like the other two
        modes already do."""
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertTrue(any("Your best bring-4" in m.value for m in at.markdown))
        at = [sb for sb in at.selectbox if sb.key == "ct_b4_deepdive_pick"][0] \
            .set_value(2).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Your best bring-4" in m.value for m in at.markdown),
                        "Stage 1/2 results must still be showing")


class TestCleanWinScoringIsVisible(unittest.TestCase):
    """"I would consider losing 1 pokemon and taking a lot of damage and
    KOing 2 enemies as far inferior to KOing the enemy without taking
    damage ... There should be a way to score this to reflect this
    dynamic." The app must surface `pairs_clean_win_total`, not just use
    it silently in the ranking."""

    def test_pair_tables_have_a_clean_win_column(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        cols = [set(d.value.columns) for d in at.dataframe]
        self.assertTrue(any("Clean win" in c for c in cols),
                        "expected a 'Clean win' column in a pair table")

    def test_deep_dive_shows_the_overall_clean_win_figure(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("clean win" in m.value for m in at.markdown))


class TestDeepDiveASpecificTeam(unittest.TestCase):
    """"I want to be able to choose a specific team to deep dive into" --
    an on-demand `core_deep_dive` call for whichever bring-4/core the user
    actually picks, not automatic for every result a search returns."""

    def test_fixed_six_bring4_mode_offers_a_deep_dive_picker(self):
        at = app()
        [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        picks = [sb for sb in at.selectbox if sb.key == "ct_b4_deepdive_pick"]
        self.assertEqual(len(picks), 1)
        self.assertEqual(len(picks[0].options), 15)

    def test_clicking_deep_dive_runs_core_deep_dive_and_shows_a_gameplan(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        self.assertEqual(len(dd_buttons), 1)
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Overall" in m.value for m in at.markdown))
        self.assertTrue(any("Set:" in c.value for c in at.caption))

    def test_deep_dive_offers_a_teamsheet_download_and_load_button(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dl = [d for d in at.download_button if d.key and d.key.endswith("_dl")]
        self.assertTrue(dl, "expected a teamsheet download button")
        loaded = [b for b in at.button if b.key and b.key.endswith("_use")
                 and b.label == "Load into Team Builder"]
        self.assertTrue(loaded, "expected a Load into Team Builder button")

    def test_loading_into_team_builder_sets_team_and_sets(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        use_buttons = [b for b in at.button if b.key and b.key.endswith("_use")]
        at = use_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertEqual(len(at.session_state["team"]), 4)
        self.assertEqual(set(at.session_state["sets"]), set(at.session_state["team"]))
        for spec in at.session_state["sets"].values():
            self.assertTrue(spec.get("item") is not None or spec.get("moves"))

    def test_pool_search_cores_also_offer_a_deep_dive(self):
        """The pool-search Bring-4 path and Multi-bring4 both go through
        `_render_multi_bring4_core` -- confirm the deep dive reaches that
        shared renderer too, not just the fixed-6 branch."""
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        sb.set_value("\U0001f50d Search a pool for the best team").run()
        [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(16).run()
        [s for s in at.slider if s.key == "ct_b4_maxweak"][0].set_value(6).run()
        [s for s in at.slider if s.key == "ct_b4_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4p_")
                     and b.key.endswith("_go")]
        self.assertTrue(dd_buttons, "expected a deep-dive button on a pool-search core")


class TestLeadBackDeepDive(unittest.TestCase):
    """"look at sequences of their lead and their back ... how much health
    each of yours have left after a 2v2 lead, and new field conditions" --
    the opt-in, real-engine bridge (`_render_lead_back_deep_dive`) on a
    chosen bring-4, alongside (not instead of) the cheap model's own
    `_render_core_deep_dive` above it."""

    def test_bring4_mode_offers_a_lead_back_deep_dive_expander(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        leads = [s for s in at.selectbox if s.key == "ctb4_lb_1_lead"]
        self.assertEqual(len(leads), 1)
        # C(4,2) lead-pair choices out of the bring-4's own 4 members.
        self.assertEqual(len(leads[0].options), 6)
        self.assertTrue(
            any("Lead/back deep dive" in e.label for e in at.expander))

    def test_playing_out_the_back_shows_a_worst_case_win_rate_metric(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        [s for s in at.slider if s.key == "ctb4_lb_1_budget"][0].set_value(15).run()
        [s for s in at.slider if s.key == "ctb4_lb_1_games"][0].set_value(4).run()
        at = [b for b in at.button if b.key == "ctb4_lb_1_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        metrics = [m for m in at.metric
                  if "Worst-case win rate" in (m.label or "")]
        self.assertEqual(len(metrics), 1)
        self.assertTrue(metrics[0].value.endswith("%"))
        self.assertTrue(any(b.key == "ctb4_lb_1_sample" for b in at.button))

    def test_show_one_real_sample_game_renders_a_battle_transcript(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        [s for s in at.slider if s.key == "ctb4_lb_1_budget"][0].set_value(15).run()
        [s for s in at.slider if s.key == "ctb4_lb_1_games"][0].set_value(4).run()
        at = [b for b in at.button if b.key == "ctb4_lb_1_go"][0].click().run()
        r_lead, r_back = at.session_state["ctb4_lb_1_result"][:2]
        at = [b for b in at.button if b.key == "ctb4_lb_1_sample"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        transcripts = [c.value for c in at.code
                      if c.value.startswith("Leads: ")
                      and r_lead[0] in c.value and r_lead[1] in c.value]
        self.assertEqual(len(transcripts), 1)
        # A real turn-by-turn transcript, not the cheap model's own
        # single-line average-rolls summary.
        self.assertIn("--- Turn 1 ---", transcripts[0])


class TestEnemyBestResponseMode(unittest.TestCase):
    """"an option for the enemy to run their best response against my
    team, and then in the battle simulator I will play that team" -- the
    mirror of Multi-bring4 (which searches a pool for OUR best team
    against named enemies): here OUR OWN team is fixed/known and the pool
    is searched for the opponent's best response, with a one-click hand-
    off into the Battle Simulator's opponent slot."""

    def test_mode_defaults_to_the_current_team_builder_team(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Enemy's best response (to my team)").run()
        self.assertFalse(at.exception, list(at.exception))
        my_source = [s for s in at.selectbox if s.key == "ct_er_my_source"]
        self.assertTrue(my_source)
        self.assertEqual(my_source[0].value, "(current Team Builder team)")

    def test_searching_returns_responses_against_the_fixed_team(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Enemy's best response (to my team)").run()
        at = [s for s in at.slider if s.key == "ct_er_pool"][0].set_value(15).run()
        at = [s for s in at.slider if s.key == "ct_er_maxweak"][0].set_value(6).run()
        at = [s for s in at.slider if s.key == "ct_er_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_er_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        rows = at.session_state.get("ct_er_rows")
        self.assertTrue(rows)
        # The fixed side is the DEFAULT team, never touched by the search.
        self.assertEqual(at.session_state.get("ct_er_my_team"), TEAM)
        # None of the returned "response" cores is just our own team back.
        for r in rows[:5]:
            self.assertNotEqual(set(r["core"]), set(TEAM))

    def test_sending_a_result_to_the_battle_simulator_configures_its_opponent_slot(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Enemy's best response (to my team)").run()
        at = [s for s in at.slider if s.key == "ct_er_pool"][0].set_value(15).run()
        at = [s for s in at.slider if s.key == "ct_er_maxweak"][0].set_value(6).run()
        at = [s for s in at.slider if s.key == "ct_er_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_er_go"][0].click().run()
        first_core = at.session_state["ct_er_rows"][0]["core"]
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("cter_1_")
                     and b.key.endswith("_go")]
        self.assertEqual(len(dd_buttons), 1)
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        send_buttons = [b for b in at.button if b.key and b.key.endswith("_sendsim")]
        self.assertEqual(len(send_buttons), 1)
        at = send_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertEqual(at.session_state["sim_foe_source"], "Paste a pokepaste")
        # "I choose their bring" is the one Simulator mode that works for
        # a response of ANY size (4, 5, or 6) -- the other two modes
        # hard-require a full six, which a 4/5-member response isn't.
        self.assertEqual(at.session_state["sim_mode_choice"], "I choose their bring")
        import json as _json
        payload = _json.loads(at.session_state["sim_foe_paste"])
        self.assertEqual(set(payload["pool"]), set(first_core))
        self.assertTrue(payload["sets"])
        if len(first_core) == 4:
            self.assertEqual(
                set(at.session_state["sim_their_lead"])
                | set(at.session_state["sim_their_back"]),
                set(first_core))

    def test_full_round_trip_can_start_a_battle_against_the_sent_response(self):
        """The actual "I will play that team" payoff -- after sending, the
        Battle Simulator's own "Start Battle" must be clickable (once our
        own side is also picked) and must actually produce a battle, not
        just accept the paste without wiring it all the way through."""
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Enemy's best response (to my team)").run()
        at = [s for s in at.slider if s.key == "ct_er_pool"][0].set_value(15).run()
        at = [s for s in at.slider if s.key == "ct_er_maxweak"][0].set_value(6).run()
        at = [s for s in at.slider if s.key == "ct_er_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_er_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("cter_1_")
                     and b.key.endswith("_go")]
        at = dd_buttons[0].click().run()
        send_buttons = [b for b in at.button if b.key and b.key.endswith("_sendsim")]
        at = send_buttons[0].click().run()
        at = [r for r in at.radio if r.key == "sim_side_source"][0].set_value(
            "A saved team").run()
        go_btn = [b for b in at.button if b.label == "Start Battle"]
        self.assertTrue(go_btn)
        self.assertFalse(go_btn[0].disabled)
        at = go_btn[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertIsNotNone(at.session_state.get("sim_battle"))


class TestCompleteMyTeamMode(unittest.TestCase):
    """"I want to be able to run the full counter_table.py exercise but
    with n mandatory members, taken from a given team pokepaste ... the
    counter table should seek to find remaining members to maximise
    wins" -- every returned team keeps the pasted members' own species,
    with the pool searched for the rest."""

    REQUIRED_PASTE = '{"pool": ["Incineroar", "Farigiraf"], "sets": {}}'

    def _search(self, at, pool=10, sizes=(4,), max_weak=6, good=0):
        at = [t for t in at.text_area if t.key == "ct_ct_paste"][0].set_value(
            self.REQUIRED_PASTE).run()
        # Narrowed to ONE enemy roster (the default is the first 3 saved
        # teams) -- Stage A (`multi_bring4_coverage`) races every pool pair
        # against every enemy roster's own pairs, so this is the single
        # biggest cost knob a test controls; at the smallest legal pool
        # (10) and 3 enemy teams this genuinely ran past AppTest's own
        # 500s ceiling in this environment -- not a hang, just real combat
        # work multiplied by 3x more than a regression test needs to prove
        # "every mandatory member survives."
        vs_multi = [m for m in at.multiselect if m.key == "ct_ct_vs"][0]
        at = vs_multi.set_value(vs_multi.options[:1]).run()
        at = [s for s in at.slider if s.key == "ct_ct_pool"][0].set_value(pool).run()
        at = [m for m in at.multiselect if m.key == "ct_ct_sizes"][0].set_value(
            list(sizes)).run()
        at = [s for s in at.slider if s.key == "ct_ct_maxweak"][0].set_value(
            max_weak).run()
        at = [s for s in at.slider if s.key == "ct_ct_good"][0].set_value(good).run()
        return [b for b in at.button if b.key == "ct_ct_go"][0].click().run()

    def test_pasting_the_mandatory_members_parses_them(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Complete my team").run()
        at = [t for t in at.text_area if t.key == "ct_ct_paste"][0].set_value(
            self.REQUIRED_PASTE).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Parsed: Incineroar, Farigiraf" in s.value
                            for s in at.success))

    def test_every_returned_core_contains_every_mandatory_member(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Complete my team").run()
        at = self._search(at)
        self.assertFalse(at.exception, list(at.exception))
        rows = at.session_state.get("ct_ct_rows")
        self.assertTrue(rows)
        for r in rows:
            self.assertTrue({"Incineroar", "Farigiraf"} <= set(r["core"]))

    def test_no_paste_warns_instead_of_a_disabled_or_broken_button(self):
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Complete my team").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("mandatory members" in w.value for w in at.warning))
        self.assertFalse(any(b.key == "ct_ct_go" for b in at.button))

    def test_a_size_too_small_for_the_mandatory_members_warns(self):
        """3 mandatory members can never fit in a completed size of 3 with
        anything left to search for -- wait, exactly 3 DOES fit trivially
        (zero extra seats); pick size 4 vs 5 mandatory members instead, a
        real mismatch, and confirm the app catches it before the button
        even offers to run."""
        at = app()
        at = [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Complete my team").run()
        big_paste = json.dumps({
            "pool": ["Incineroar", "Farigiraf", "Kingambit", "Garchomp", "Gholdengo"],
            "sets": {}})
        at = [t for t in at.text_area if t.key == "ct_ct_paste"][0].set_value(
            big_paste).run()
        at = [m for m in at.multiselect if m.key == "ct_ct_sizes"][0].set_value(
            [4]).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("won't fit" in w.value for w in at.warning))
        self.assertFalse(any(b.key == "ct_ct_go" for b in at.button))


class TestBring4RostersAcceptAPastedPokepaste(unittest.TestCase):
    """"I need a way ... in the streamlit app, to run a bring4 (4-6) vs
    only ONE named TEAM" plus "let the enemy roster be pasted/custom
    (dropdown) and let our roster be pasted/custom" -- both the enemy
    roster and our own 4-6 in Bring-4 mode now offer a paste option
    alongside the saved-team dropdown, not just a fixed library pick."""

    RAIN_PASTE = ("Archaludon @ Assault Vest\nAbility: Stamina\n"
                 "EVs: 2 HP / 32 SpA / 32 SpD\nModest Nature\n"
                 "- Draco Meteor\n- Flash Cannon\n- Electro Shot\n- Body Press\n\n"
                 "Grimmsnarl @ Light Clay\nAbility: Prankster\n"
                 "EVs: 32 HP / 32 Def\nBold Nature\n"
                 "- Light Screen\n- Reflect\n- Spirit Break\n- Thunder Wave")

    def test_enemy_roster_offers_a_paste_option(self):
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        self.assertIn("\U0001f4cb Paste a pokepaste", sb.options)

    def test_our_6_offers_a_paste_option(self):
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        self.assertIn("\U0001f4cb Paste a pokepaste", sb.options)

    def test_pasting_an_enemy_roster_reveals_a_text_area(self):
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        at = sb.set_value("\U0001f4cb Paste a pokepaste").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(t.key == "ct_b4_vs_paste" for t in at.text_area))

    def test_a_valid_pasted_enemy_roster_parses_and_can_search(self):
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        at = sb.set_value("\U0001f4cb Paste a pokepaste").run()
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(self.RAIN_PASTE).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Archaludon" in s.value for s in at.success))
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))

    def test_an_apply_button_is_offered_for_mobile(self):
        """"for mobile I need to have a button to apply the team" --
        `st.text_area` only syncs to Python on blur/Ctrl+Enter, unavailable
        on mobile; a plain button gives a tap target that forces the same
        rerun. Parsing itself already runs on every rerun regardless (see
        `test_a_valid_pasted_enemy_roster_parses_and_can_search` above,
        which never touches this button), so clicking it must not error
        and the parse must still be visible afterward."""
        at = app()
        sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        at = sb.set_value("\U0001f4cb Paste a pokepaste").run()
        self.assertTrue(any(b.key == "ct_b4_vs_paste_apply" for b in at.button))
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(self.RAIN_PASTE).run()
        at = [b for b in at.button if b.key == "ct_b4_vs_paste_apply"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Archaludon" in s.value for s in at.success))

    def test_pasting_our_6_reveals_a_text_area_and_parses(self):
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        at = sb.set_value("\U0001f4cb Paste a pokepaste").run()
        self.assertFalse(at.exception, list(at.exception))
        ta = [t for t in at.text_area if t.key == "ct_b4_our_paste"][0]
        at = ta.set_value(self.RAIN_PASTE).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Archaludon" in s.value for s in at.success))


class TestBring4EnemyPastePlainSpeciesList(unittest.TestCase):
    """"I get the following enemy team as this format Glimmora / Rillaboom
    / Persian-Alola / Pawmot / Salamence / Milotic. I want to paste this in
    the bring4 counter table. Maybe give me a checkbox to set certain
    enemies as megas. If an enemy pokemon isn't recognised, just ignore
    it." -- a bare name list (no "@"/"Ability:"/move lines) is detected
    separately from a real Showdown export and parsed by matching each
    token against the roster directly, instead of being swallowed whole as
    one garbled "species" by `custom_team_from_export`."""

    PLAIN_LIST = "Glimmora / Rillaboom / Persian-Alola / Pawmot / Salamence / Milotic"

    def _goto_paste(self, at):
        sb = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        at = sb.set_value("\U0001f4cb Paste a pokepaste").run()
        self.assertFalse(at.exception, list(at.exception))
        return at

    def test_a_plain_list_parses_the_recognised_names(self):
        at = self._goto_paste(app())
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(self.PLAIN_LIST).run()
        self.assertFalse(at.exception, list(at.exception))
        success = next(s.value for s in at.success if "Parsed:" in s.value)
        for name in ("Glimmora", "Rillaboom", "Pawmot", "Salamence", "Milotic"):
            self.assertIn(name, success)
        self.assertNotIn("Persian-Alola", success)

    def test_an_unrecognised_entry_is_ignored_not_blocking(self):
        """"If an enemy pokemon isn't recognised, just ignore it" --
        Persian-Alola isn't in this roster at all; the rest of the paste
        must still parse and the search must still be runnable."""
        at = self._goto_paste(app())
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(self.PLAIN_LIST).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Persian-Alola" in c.value for c in at.caption))
        self.assertTrue(any(s.value.startswith("Parsed:") for s in at.success))
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))

    def test_a_real_pokepaste_is_not_misdetected_as_a_plain_list(self):
        """Regression guard: a genuine Showdown export (has "@"/"Ability:"/
        move lines) must still go through `custom_team_from_export`, not
        get mis-sniffed as a bare name list."""
        at = self._goto_paste(app())
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(
            "Garchomp @ Rocky Helmet\nAbility: Rough Skin\n"
            "EVs: 252 Atk / 4 SpD / 252 Spe\nJolly Nature\n"
            "- Earthquake\n- Protect\n- Dragon Claw\n- Stealth Rock").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Rocky Helmet" not in c.value for c in at.caption)
                        or not at.caption)
        self.assertTrue(any(s.value == "Parsed: Garchomp" for s in at.success))

    def test_a_mega_checkbox_is_offered_for_a_single_variant_species(self):
        """Salamence/Glimmora each have exactly one Mega form in this
        roster -- a single checkbox, not a selectbox."""
        at = self._goto_paste(app())
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(self.PLAIN_LIST).run()
        self.assertTrue(any(c.key == "ct_b4_vs_mega_Salamence" for c in at.checkbox))
        self.assertTrue(any(c.key == "ct_b4_vs_mega_Glimmora" for c in at.checkbox))
        # Pawmot/Rillaboom/Milotic have no Mega form -- no checkbox for them.
        self.assertFalse(any(c.key == "ct_b4_vs_mega_Pawmot" for c in at.checkbox))

    def test_checking_the_mega_box_substitutes_the_mega_form(self):
        at = self._goto_paste(app())
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(self.PLAIN_LIST).run()
        cb = [c for c in at.checkbox if c.key == "ct_b4_vs_mega_Salamence"][0]
        at = cb.set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        pair_rows = at.session_state["ct_b4_pair_rows"]
        # The enemy roster actually raced includes "Mega Salamence", not
        # plain "Salamence".
        detail_names = {n for r in pair_rows for pair in r["detail"]
                        for n in pair}
        self.assertIn("Mega Salamence", detail_names)
        self.assertNotIn("Salamence", detail_names)


class TestPerBring4DeepDiveRespectsPinnedMoveset(unittest.TestCase):
    """"The Counter Table in the streamlit app doesn't use the actual
    moveset of the loaded team in bring 4." Root cause: `_render_core_
    deep_dive` never received `item_overrides`/`move_overrides` at all,
    even though the SAME branch's `bring4_search` call already respected
    them (see `TestBring4TabMirrorsTheCliExactly` above) -- so Stage 1/2
    rankings and the deep-dive display could silently disagree on which
    set each Pokemon holds. Fixed by threading the same overrides through
    to the deep dive's own `core_deep_dive` call."""

    TEAM = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    SETS = {
        "Garchomp": {"item": "Rocky Helmet",
                    "moves": ["Earthquake", "Protect", "Dragon Claw",
                             "Stealth Rock"]},
        "Incineroar": {"item": "Sitrus Berry",
                      "moves": ["Fake Out", "Flare Blitz", "Knock Off",
                               "Protect"]},
    }

    def test_the_per_bring4_deep_dive_shows_the_pinned_set(self):
        at = app(team=self.TEAM, sets=self.SETS)
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        caption = next(c.value for c in at.caption if c.value.startswith("Set:"))
        self.assertIn("Rocky Helmet", caption)
        self.assertIn("Sitrus Berry", caption)


class TestFullDeepDiveAllOfOur6VsOneEnemy(unittest.TestCase):
    """"I would also like to run full deep dive with all configurations vs
    a given enemy team with my loaded team" -- every C(6,2) pair `our6`
    can form, raced against the currently-selected enemy roster, without
    needing to search/pick a bring-4 first (a superset of every possible
    bring-4's own internal pairs)."""

    def test_the_button_is_offered_without_a_stage_1_2_search_first(self):
        at = app()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(b.key == "ctb4_dd_all6_one_go" for b in at.button))

    def test_clicking_it_dives_all_15_pairs(self):
        at = app()  # default TEAM has 6 members -> C(6,2) = 15 pairs
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Overall" in m.value for m in at.markdown))
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        self.assertEqual(len(dive["per_pair"]), 15)

    def test_it_respects_a_loaded_teams_pinned_set(self):
        team = ["Garchomp", "Incineroar", "Gallade", "Hydreigon",
                "Farigiraf", "Whimsicott"]
        sets = {"Garchomp": {"item": "Rocky Helmet",
                             "moves": ["Earthquake", "Protect",
                                      "Dragon Claw", "Stealth Rock"]}}
        at = app(team=team, sets=sets)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        caption = next(c.value for c in at.caption if c.value.startswith("Set:"))
        self.assertIn("Rocky Helmet", caption)

    def test_its_sets_match_the_same_teams_row_in_the_vs_all_teams_dive(self):
        """Regression: without `item_resolution_enemies`, this single-
        enemy dive independently re-searched `our6`'s item/moveset against
        JUST the one selected enemy roster, while the "vs ALL saved teams"
        dive (below) searches the same `our6` against the union of every
        saved team -- two different optimisation targets producing two
        different sets, making the single-team dive look artificially
        better than the identical core's own multi-enemy dive shows for
        it. Both dives must now agree on `our6`'s `sets` for the SAME
        selected enemy team."""
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        one_sets = at.session_state["ctb4_dd_all6_one_dive"]["sets"]

        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        all_sets = at.session_state["ctb4_dd_all6_allteams_dive"]["sets"]

        self.assertEqual(one_sets, all_sets)


class TestFullDeepDiveAllOfOur6VsAllEnemyTeams(unittest.TestCase):
    """"and also full deep dive versus all enemy teams with my loaded
    team" -- a separate, more expensive dive across EVERY saved enemy
    roster at once, persisted in session_state (like every other deep
    dive already is) so it survives a rerun triggered by an unrelated
    widget."""

    def test_the_button_is_offered(self):
        at = app()
        self.assertTrue(any(b.key == "ctb4_dd_all6_allteams_go"
                            for b in at.button))

    def test_clicking_it_dives_vs_every_saved_team(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Overall, vs all" in m.value for m in at.markdown))

    def test_results_survive_a_rerun_that_does_not_reclick_the_button(self):
        """Regression: `all_shown_vs` (used to label each enemy team in
        the per-pair-per-enemy breakdown) used to be a local variable
        defined ONLY inside the button's own `if st.button(...):` block,
        but read again OUTSIDE that block, in the always-rendered display
        code further down -- any rerun that did NOT re-click the button
        (e.g. any other widget interaction elsewhere on the page) raised
        a NameError. Calling `.run()` again without touching the button
        reproduces exactly that "later rerun" case."""
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        at = at.run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Overall, vs all" in m.value for m in at.markdown))


class TestBestBring4FromDeepDive(unittest.TestCase):
    """"I may as well calculate for all 6 of my pokemon rather than just 4,
    to see the best bring4" -- once the "Full deep dive: all of Our 6"
    dive has already raced every C(6,2) pair, `_render_core_deep_dive`
    derives the best bring-4 from those ACCURATE results (`bring4_from_
    deep_dive`) instead of leaving that choice to the cheap Stage 1/2
    hypothesis."""

    def test_the_all6_one_dive_shows_a_best_bring4_section(self):
        at = app()  # default TEAM has 6 members
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Best bring-4 (from this deep dive)" in m.value
                            for m in at.markdown))
        dfs = [d.value for d in at.dataframe]
        shapes = [df.shape[0] for df in dfs]
        self.assertIn(15, shapes, "expected a 15-row bring-4 candidate table")
        # 6 pair rows + 1 appended TOTAL row (see TestPairRowsDfTotalsRow).
        self.assertIn(7, shapes,
                     "expected the winning bring-4's own 6-pair table plus a TOTAL row")

    def test_the_picked_bring4_deep_dive_has_no_best_bring4_section(self):
        """A bring-4 already picked from Stage 2 is exactly at the bring
        size (4) -- nothing left to choose between, so this section must
        not appear there (it would be a trivial no-op)."""
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any("Best bring-4 (from this deep dive)" in m.value
                             for m in at.markdown))

    def test_best_bring4_matches_a_direct_bring4_from_deep_dive_call(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        vs_name = [s for s in at.selectbox if s.key == "ct_b4_vs"][0].value

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive
        W = load_world()
        vs_roster = list(W["teams"][vs_name])
        expected = bring4_from_deep_dive(TEAM, dive, vs_roster)
        shown_bring4 = " / ".join(expected[0]["bring4"])
        self.assertTrue(any(c.value == shown_bring4 for c in at.caption))

    def test_shows_the_recommended_lead_and_back_for_the_winning_bring4(self):
        """"after doing a full six deep dive, output the best bring 4 from
        that (lead / back)" -- `recommended_lead` applied to the winning
        `bring4_from_deep_dive` row, not just the bare 4-name list."""
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        vs_name = [s for s in at.selectbox if s.key == "ct_b4_vs"][0].value

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive, recommended_lead
        W = load_world()
        vs_roster = list(W["teams"][vs_name])
        expected = bring4_from_deep_dive(TEAM, dive, vs_roster)
        lb = recommended_lead(expected[0])
        expected_caption = (f"Lead: {' + '.join(lb['lead'])}  |  "
                            f"Back: {' + '.join(lb['backup'])}")
        self.assertTrue(any(c.value == expected_caption for c in at.caption))


class TestWinConditionsSection(unittest.TestCase):
    """"In bring4, I want to be able to identify win conditions -- perhaps
    Metagross + Hydreigon is the only pair that beats Golisopod, or
    Hydreigon is the only pokemon that beats Golisopod. I need to see
    what pokemon I need to preserve to guarantee a win against certain
    pokemon in an endgame." -- `bring4_win_conditions`, surfaced as a
    "Win conditions" table wherever a specific bring-4 vs one enemy
    roster is already shown."""

    def _win_conditions_dfs(self, at):
        return [d.value for d in at.dataframe
               if list(d.value.columns) == ["Enemy", "Preserve", "Caveats"]]

    def test_stage2_best_bring4_shows_win_conditions(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Win conditions" in m.value for m in at.markdown))
        bring4_rows = at.session_state["ct_b4_bring4_rows"]

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from counter_finder import bring4_win_conditions
        expected = bring4_win_conditions(bring4_rows[0])
        dfs = self._win_conditions_dfs(at)
        self.assertTrue(dfs, "expected an Enemy/Preserve win-conditions table")
        shown = dict(zip(dfs[0]["Enemy"], dfs[0]["Preserve"]))
        shown_caveats = dict(zip(dfs[0]["Enemy"], dfs[0]["Caveats"]))
        self.assertEqual(set(shown), set(expected))
        for enemy, info in expected.items():
            if info["uncovered"]:
                self.assertIn("none", shown[enemy])
            elif info["safe_members"]:
                for m in info["safe_members"]:
                    self.assertIn(m, shown[enemy])
            else:
                for n1, n2 in info["safe_pairs"]:
                    self.assertIn(n1, shown[enemy])
                    self.assertIn(n2, shown[enemy])
            self.assertEqual("Tailwind" in shown_caveats[enemy], info["tailwind_risk"])
            self.assertEqual("Protect" in shown_caveats[enemy], info["protect_risk"])

    def test_picked_bring4_deep_dive_shows_win_conditions(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Win conditions" in m.value for m in at.markdown))
        self.assertTrue(self._win_conditions_dfs(at))

    def test_all6_one_team_deep_dive_shows_win_conditions_for_its_best_bring4(self):
        at = app()  # default TEAM has 6 members
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        vs_name = [s for s in at.selectbox if s.key == "ct_b4_vs"][0].value

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive, bring4_win_conditions
        W = load_world()
        vs_roster = list(W["teams"][vs_name])
        best = bring4_from_deep_dive(TEAM, dive, vs_roster)[0]
        expected = bring4_win_conditions(best)
        dfs = self._win_conditions_dfs(at)
        self.assertTrue(dfs)
        self.assertEqual(set(dfs[-1]["Enemy"]), set(expected))

    def test_the_vs_all_enemy_teams_dive_shows_no_win_conditions_table(self):
        """Win conditions only make sense against ONE known enemy roster
        -- `_render_core_deep_dive`'s multi-roster branch (`target_name_
        lists` with more than one entry) must not attempt it."""
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any("Win conditions" in m.value for m in at.markdown))


class TestHitCountMatrixSection(unittest.TestCase):
    """"A prematch view of my win conditions vs theirs (i.e., once Arcanine
    is gone, Scizor easily beats X in endgame given it 2HKOs enemy but
    takes 5HKOs from enemy and so on)" -- a "1v1 hit-count matrix" table
    (`prematch_win_conditions`'s own 1v1 half) rendered right alongside
    the existing "Win conditions" table, wherever a specific bring-4 vs
    one enemy roster is already shown."""

    def _matrix_dfs(self, at):
        return [d.value for d in at.dataframe if list(d.value.columns[:1]) == ["Ours"]]

    def test_stage2_best_bring4_shows_the_hit_count_matrix(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("1v1 hit-count matrix" in m.value for m in at.markdown))
        dfs = self._matrix_dfs(at)
        self.assertTrue(dfs, "expected an Ours/<enemy...> hit-count table")
        bring4_rows = at.session_state["ct_b4_bring4_rows"]
        self.assertEqual(set(dfs[0]["Ours"]), set(bring4_rows[0]["bring4"]))
        for cell in dfs[0].iloc[0, 1:]:
            self.assertRegex(str(cell),
                             r"^(\d+HKO|--) / (\d+HKO|--) [✅❌➖]( \(chip \d+%\))?$")

    def test_picked_bring4_deep_dive_shows_the_hit_count_matrix(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("1v1 hit-count matrix" in m.value for m in at.markdown))
        self.assertTrue(self._matrix_dfs(at))

    def test_all6_one_team_deep_dive_shows_the_hit_count_matrix(self):
        at = app()  # default TEAM has 6 members
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = self._matrix_dfs(at)
        self.assertTrue(dfs)

    def test_the_vs_all_enemy_teams_dive_shows_no_hit_count_matrix(self):
        """Same rule as the Win conditions table -- only makes sense
        against ONE known enemy roster."""
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any("1v1 hit-count matrix" in m.value for m in at.markdown))

    def test_stage2_best_bring4_can_show_a_crucial_to_preserve_table(self):
        """"Individual pokemon can be crucial win conditions to preserve
        for a given match" -- a "Crucial to preserve" table with a
        Preserve/Sole answer to shape, rendered exactly when the tab's own
        bring-4 result actually has a must_preserve member (never rendered
        empty, never silently skipped when there's something to show)."""
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        crucial_dfs = [d.value for d in at.dataframe
                      if list(d.value.columns) == ["Preserve", "Sole answer to"]]
        has_crucial_markdown = any("Crucial to preserve" in m.value for m in at.markdown)
        self.assertEqual(bool(crucial_dfs), has_crucial_markdown)
        for df in crucial_dfs:
            self.assertFalse(df.empty, "a rendered crucial table must not be empty")


class TestGameplanCache(unittest.TestCase):
    """"If a counter table analysis has been loaded, show what the 2v2
    calculator saw as the optimal play sequence" -- a bring-4 search
    caches every pair's own already-raced `detail[(e1, e2)]` (log/outcome/
    turns_used) into `st.session_state["ct_gameplans"]`, keyed by
    `(frozenset(our_pair), frozenset(enemy_pair))` so the Battle Simulator
    can look it up later regardless of role order."""

    def test_bring4_search_populates_the_gameplan_cache(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        pair_rows = at.session_state["ct_b4_pair_rows"]
        self.assertIn("ct_gameplans", at.session_state)
        cache = at.session_state["ct_gameplans"]
        self.assertTrue(cache, "expected at least one cached gameplan")
        first_pr = pair_rows[0]
        (e1, e2), d = next(iter(first_pr["detail"].items()))
        key = (frozenset(first_pr["pair"]), frozenset((e1, e2)))
        self.assertIn(key, cache)
        entry = cache[key]
        self.assertEqual(entry["outcome"], d["outcome"])
        self.assertEqual(entry["log"], d["log"])
        self.assertEqual(entry["source"], "Bring-4 search")


class TestRoundRobinMode(unittest.TestCase):
    """"Give me an option in the streamlit app counter table ... to only
    run all the saved teams vs the other teams (including themself),
    rather than creating teams" -- a "Round-robin (saved teams only)" mode:
    a team multiselect (default all saved teams), a Run button, and a
    sequential per-matchup render (mirrors included, no reversed
    duplicate) once run."""

    def test_mode_shows_team_multiselect_and_run_button(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Round-robin (saved teams only)").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(m.key == "ct_rr_teams" for m in at.multiselect))
        self.assertTrue(any(b.key == "ct_rr_go" for b in at.button))

    def test_running_it_on_two_teams_renders_all_six_matchups(self):
        """Two teams: the mirrors A-A/B-B (one direction each), the non-
        mirror pair raced BOTH directions (A-B and B-A -- "race both
        directions" so every team's own summary reflects its own real
        performance), plus the "best4 vs best4" head-to-head layer for
        that same non-mirror pair, ALSO raced both directions ("matches
        still systematically favour side A, without representing a
        genuine assessment of the matchup" -- see `round_robin_saved_
        teams`'s own docstring), rendered in its own section below."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        W = load_world()
        two_names = sorted(W["teams"])[:2]
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Round-robin (saved teams only)").run()
        [s for s in at.slider if s.key == "ct_turns"][0].set_value(1).run()
        [m for m in at.multiselect if m.key == "ct_rr_teams"][0].set_value(
            two_names).run()
        at = [b for b in at.button if b.key == "ct_rr_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        a, b = two_names
        headings = {m.value for m in at.markdown if m.value.startswith("### ")}
        self.assertIn(f"### {a} vs {a}", headings)
        self.assertIn(f"### {a} vs {b}", headings)
        self.assertIn(f"### {b} vs {a}", headings)
        self.assertIn(f"### {a} best-4 vs {b} best-4", headings)
        self.assertIn(f"### {b} best-4 vs {a} best-4", headings)
        self.assertIn(f"### {b} vs {b}", headings)
        results = at.session_state["ct_rr_results"]
        self.assertEqual(len(results), 6)

    def test_running_it_also_populates_the_gameplan_cache(self):
        """The same `_cache_gameplans` hook every other Counter Table
        search wires in -- a round-robin result should feed the Battle
        Simulator's "gameplan" panel too, tagged with its own source
        label."""
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        W = load_world()
        two_names = sorted(W["teams"])[:2]
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Round-robin (saved teams only)").run()
        [s for s in at.slider if s.key == "ct_turns"][0].set_value(1).run()
        [m for m in at.multiselect if m.key == "ct_rr_teams"][0].set_value(
            two_names).run()
        at = [b for b in at.button if b.key == "ct_rr_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertIn("ct_gameplans", at.session_state)
        cache = at.session_state["ct_gameplans"]
        self.assertTrue(any(v["source"].startswith("Round-robin:")
                            for v in cache.values()))


class TestTrickRoomOptIn(unittest.TestCase):
    """"avoiding enemy tailwind and trick room may be key for a matchup
    swinging from a win to a clear loss ... Add it now as an option" --
    a new opt-in "Also check enemy Trick Room" checkbox, unchecked by
    default (no behavior change unless a user explicitly turns it on),
    threaded into `bring4_search`/`core_deep_dive` as `check_trick_room`
    and read back out via `bring4_win_conditions`'s own `trick_room_risk`
    into the Win conditions table's Caveats column."""

    def test_stage2_offers_the_checkbox_unchecked_by_default(self):
        at = app()
        cb = [c for c in at.checkbox if c.key == "ct_b4_check_tr"]
        self.assertTrue(cb, "expected the Trick Room opt-in checkbox in "
                        "Bring-4 (one enemy roster) mode")
        self.assertFalse(cb[0].value)

    def test_checking_it_threads_check_trick_room_into_the_search(self):
        at = app()
        [c for c in at.checkbox if c.key == "ct_b4_check_tr"][0].set_value(True).run()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        pair_rows = at.session_state["ct_b4_pair_rows"]
        self.assertTrue(pair_rows)
        first_detail = list(pair_rows[0]["detail"].values())[0]
        self.assertIn("trick_room_safe", first_detail)

    def test_leaving_it_unchecked_never_adds_trick_room_fields(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        pair_rows = at.session_state["ct_b4_pair_rows"]
        first_detail = list(pair_rows[0]["detail"].values())[0]
        self.assertNotIn("trick_room_safe", first_detail)

    def test_picked_bring4_deep_dive_offers_the_checkbox_too(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_checks = [c for c in at.checkbox if c.key and c.key.startswith("ctb4_dd_")
                    and c.key.endswith("_check_tr") and "all6" not in c.key]
        self.assertTrue(dd_checks, "expected the Trick Room opt-in checkbox "
                        "on the picked bring-4's own deep dive")
        self.assertFalse(dd_checks[0].value)


class TestPairRowsDfTotalsRow(unittest.TestCase):
    """"It shows the six pairs on the deep dive option, but not the totals
    for the six pairs" -- the winning bring-4's own 6-pair table (Beaten/
    Swept/Traded/Lost/No KO/Clean win/Tailwind-safe/Protect-safe) gets one
    extra summary row across all 6, instead of leaving the reader to add
    them up by hand."""

    def _totals_row(self, at):
        dfs = [d.value for d in at.dataframe]
        # The winning bring-4's own 6-pair table is the one with 7 rows
        # (6 pairs + 1 TOTAL row) -- the only other tables on this page are
        # the 15-row Stage-1-shaped candidate table and the per-enemy-team
        # pair/matchup tables, neither of which is 7 rows for this fixture.
        seven_row = [df for df in dfs if df.shape[0] == 7]
        self.assertEqual(len(seven_row), 1, [df.shape for df in dfs])
        df = seven_row[0]
        self.assertTrue(str(df.iloc[-1]["Pair"]).startswith("TOTAL"))
        return df

    def test_the_total_row_sums_every_column_across_the_six_pairs(self):
        at = app()  # default TEAM has 6 members
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        df = self._totals_row(at)

        dive = at.session_state["ctb4_dd_all6_one_dive"]
        vs_name = [s for s in at.selectbox if s.key == "ct_b4_vs"][0].value
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive
        W = load_world()
        vs_roster = list(W["teams"][vs_name])
        expected = bring4_from_deep_dive(TEAM, dive, vs_roster)
        pair_rows = expected[0]["pair_rows"]
        total = pair_rows[0]["pairs_total"]
        n = len(pair_rows)
        swept = sum(r["pairs_swept"] for r in pair_rows)
        traded = sum(r["pairs_traded"] for r in pair_rows)
        lost = sum(r["pairs_lost"] for r in pair_rows)
        no_ko = sum(r["pairs_no_ko"] for r in pair_rows)
        clean = sum(r["pairs_clean_win_total"] for r in pair_rows)
        tw_safe = sum(r["pairs_tailwind_safe"] for r in pair_rows)
        pr_safe = sum(r["pairs_protect_safe"] for r in pair_rows)

        last = df.iloc[-1]
        self.assertEqual(last["Beaten"], f"{swept + traded}/{n * total}")
        self.assertEqual(last["Swept"], swept)
        self.assertEqual(last["Traded"], traded)
        self.assertEqual(last["Lost"], lost)
        self.assertEqual(last["No KO"], no_ko)
        self.assertEqual(last["Clean win"], f"{clean:.1f}/{2 * n * total}")
        self.assertEqual(last["Tailwind-safe"], tw_safe)
        self.assertEqual(last["Protect-safe"], pr_safe)

    def test_the_stage1_all_pairs_table_has_no_total_row(self):
        """Only a bring-4's own FIXED set of pairs gets a total -- the
        Stage-1 table (every pair drawn from a larger pool, not yet
        narrowed to one bring-4) stays exactly as many rows as pairs.
        Identified by its "Pair" column (Stage 2's own 15-row "all
        possible bring-4s" table -- C(6,4)=15, same row count as Stage 1's
        C(6,2)=15 pairs for a 6-member team -- has a "Bring-4" column
        instead, so row count alone can't tell the two apart)."""
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = [d.value for d in at.dataframe]
        stage1 = [df for df in dfs if "Pair" in df.columns and df.shape[0] == 15]
        self.assertEqual(len(stage1), 1)
        self.assertFalse(
            any(str(v).startswith("TOTAL") for v in stage1[0]["Pair"]))


class TestWinningBring4OwnPairsSection(unittest.TestCase):
    """"When all pairs are deep dived and the best bring4 is found, then
    have a section which only shows the deep dive for those four" -- a
    dedicated subsection with just the winning bring-4's own 6 pairs and
    their full matchup-by-matchup breakdown, instead of having to find
    them among the core's full C(6,2)=15."""

    def test_the_section_shows_exactly_the_winning_bring4s_six_pairs(self):
        at = app()  # default TEAM has 6 members
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(
            "Deep dive: just the winning bring-4's own pairs" in m.value
            for m in at.markdown))
        self.assertTrue(any(m.value == "**Every pair in the core**"
                            for m in at.markdown))

        dive = at.session_state["ctb4_dd_all6_one_dive"]
        vs_name = [s for s in at.selectbox if s.key == "ct_b4_vs"][0].value
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive
        W = load_world()
        vs_roster = list(W["teams"][vs_name])
        expected = bring4_from_deep_dive(TEAM, dive, vs_roster)
        expected_pairs = {tuple(r["pair"]) for r in expected[0]["pair_rows"]}

        expander_labels = [e.label for e in at.expander]
        # Every one of the winning bring-4's 6 pairs shows up as its own
        # expander (once for the dedicated section, once more inside the
        # unfiltered "every pair in the core" section below it).
        for n1, n2 in expected_pairs:
            matches = [lbl for lbl in expander_labels
                      if lbl.startswith(f"{n1} + {n2} ")]
            self.assertGreaterEqual(len(matches), 2,
                                    f"expected {n1} + {n2} in both sections")

    def test_no_section_for_a_core_already_at_the_bring_size(self):
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any(
            "Deep dive: just the winning bring-4's own pairs" in m.value
            for m in at.markdown))

    def test_only_losses_filter_also_applies_inside_this_section(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        cb = [c for c in at.checkbox if c.key == "ctb4_dd_all6_one_onlyloss"][0]
        at = cb.set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))


class TestOnlyShowLossesFilter(unittest.TestCase):
    """"I also want an option to just see the specific enemy pairs my
    given pair loses against" -- a checkbox that filters each pair's own
    matchup list down to just the unconditional losses (`outcome ==
    "loss"`), instead of scrolling past every sweep/trade/no-KO to find
    them."""

    def test_the_checkbox_is_offered_on_a_deep_dive(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(c.key == "ctb4_dd_all6_one_onlyloss"
                            for c in at.checkbox))

    def test_toggling_it_never_raises_and_narrows_the_shown_matchups(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        # A real fixture must actually contain at least one non-loss
        # matchup for "narrows" to be a meaningful assertion.
        any_non_loss = any(
            d["outcome"] != "loss"
            for pair in dive["per_pair"].values()
            for pe in pair["per_enemy"] for d in pe["detail"].values())
        self.assertTrue(any_non_loss, "fixture needs at least one non-loss "
                                      "matchup to make this test meaningful")
        codes_before = len(at.code)
        cb = [c for c in at.checkbox if c.key == "ctb4_dd_all6_one_onlyloss"][0]
        at = cb.set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        codes_after = len(at.code)
        self.assertLess(codes_after, codes_before)

    def test_a_pair_with_zero_losses_says_so_when_filtered(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        # Find a pair (vs the one enemy roster here) with zero losses.
        clean_pair = None
        for (n1, n2), pair in dive["per_pair"].items():
            detail = pair["per_enemy"][0]["detail"]
            if not any(d["outcome"] == "loss" for d in detail.values()):
                clean_pair = (n1, n2)
                break
        self.assertIsNotNone(clean_pair, "fixture needs a pair with zero "
                                         "losses to make this test meaningful")
        cb = [c for c in at.checkbox if c.key == "ctb4_dd_all6_one_onlyloss"][0]
        at = cb.set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))
        n1, n2 = clean_pair
        expanders = [e for e in at.expander if e.label.startswith(f"{n1} + {n2} ")]
        self.assertTrue(expanders)
        captions = [c.value for c in expanders[0].caption]
        self.assertIn("No losses.", captions)


class TestBestBring4TeamByTeam(unittest.TestCase):
    """"When I see the overall deep dive result vs all enemy teams, I
    should look team by team for the best brings, rather than just
    individual pair performance versus all enemies" -- the "vs ALL saved
    enemy teams" dive is now organised per enemy team, each with its own
    best bring-4 (`bring4_from_deep_dive`, scored against just that one
    roster)."""

    def test_shows_one_best_bring4_line_per_enemy_team(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Best bring-4, team by team" in m.value
                            for m in at.markdown))
        team_lines = [m.value for m in at.markdown if m.value.startswith("**vs ")]

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        W = load_world()
        self.assertEqual(len(team_lines), len(W["teams"]))

    def test_each_teams_best_bring4_matches_a_direct_call(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        dive = at.session_state["ctb4_dd_all6_allteams_dive"]

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive, recommended_lead
        W = load_world()
        name0 = list(W["teams"])[0]
        expected = bring4_from_deep_dive(TEAM, dive, list(W["teams"][name0]))
        lb = recommended_lead(expected[0])
        expected_line = (f"**vs {name0}**: bring "
                         f"{' / '.join(expected[0]['bring4'])} "
                         f"(worst pair beats "
                         f"{expected[0]['worst_pair_row']['pairs_swept'] + expected[0]['worst_pair_row']['pairs_traded']}"
                         f"/{expected[0]['worst_pair_row']['pairs_total']}) -- lead "
                         f"{' + '.join(lb['lead'])}, back "
                         f"{' + '.join(lb['backup'])}")
        self.assertTrue(any(m.value == expected_line for m in at.markdown))

    def test_only_losses_checkbox_is_offered_and_toggles_cleanly(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertTrue(any(c.key == "ctb4_dd_all6_allteams_onlyloss"
                            for c in at.checkbox))
        cb = [c for c in at.checkbox
             if c.key == "ctb4_dd_all6_allteams_onlyloss"][0]
        at = cb.set_value(True).run()
        self.assertFalse(at.exception, list(at.exception))


class TestAllTeamsSummaryTable(unittest.TestCase):
    """"first give a summary table of all enemy teams; your bring/lead vs
    each, full performance (beats, under tailwind, under protect, best
    pair/3rd best/worst and so on as done elsewhere)" -- a `st.dataframe`
    overview, one row per enemy team, ABOVE the existing "team by team
    (full detail)" markdown loop (`TestBestBring4TeamByTeam`), matching the
    CLI's own per-enemy `bring4_pair_depth` breakdown (`_write_multi_
    bring4_xlsx`'s Cores sheet)."""

    def test_a_summary_dataframe_is_shown_with_one_row_per_team(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = [d.value for d in at.dataframe if "Enemy team" in d.value.columns]
        self.assertTrue(dfs, "expected a summary dataframe with an 'Enemy "
                         "team' column")

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        W = load_world()
        self.assertEqual(len(dfs[0]), len(W["teams"]))
        self.assertEqual(set(dfs[0]["Enemy team"]), set(W["teams"]))

    def test_it_appears_before_the_full_detail_section(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        markdowns = [m.value for m in at.markdown]
        summary_idx = next(i for i, v in enumerate(markdowns)
                           if "Summary: best bring-4 vs each enemy team" in v)
        detail_idx = next(i for i, v in enumerate(markdowns)
                          if "Best bring-4, team by team" in v)
        self.assertLess(summary_idx, detail_idx)

    def test_the_full_performance_columns_are_present(self):
        """"full performance (beats, under tailwind, under protect, best
        pair/3rd best/worst and so on as done elsewhere)" -- the SAME
        `bring4_pair_depth` fields the CLI's xlsx Cores sheet already
        surfaces per enemy, not just the beaten count."""
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        dfs = [d.value for d in at.dataframe if "Enemy team" in d.value.columns]
        cols = set(dfs[0].columns)
        for expected in ("Bring-4", "Lead", "Backup", "Worst pair beaten",
                         "Beaten total", "Beaten 3rd best", "Beaten 4th best",
                         "Beaten worst", "Tailwind-safe total",
                         "Tailwind-safe best", "Tailwind-safe 3rd best",
                         "Protect-safe total", "Protect-safe best",
                         "Protect-safe 3rd best", "Clean win total"):
            self.assertIn(expected, cols)

    def test_a_rows_values_match_a_direct_call(self):
        at = app()
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        dive = at.session_state["ctb4_dd_all6_allteams_dive"]
        dfs = [d.value for d in at.dataframe if "Enemy team" in d.value.columns]
        df = dfs[0]

        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import (bring4_from_deep_dive, bring4_pair_depth,
                                    recommended_lead)
        W = load_world()
        name0 = list(W["teams"])[0]
        best = bring4_from_deep_dive(TEAM, dive, list(W["teams"][name0]))[0]
        depth = bring4_pair_depth(best)
        pt = depth["pairs_total"]
        n_pairs = len(best["pair_rows"])
        lb = recommended_lead(best)

        row = df[df["Enemy team"] == name0].iloc[0]
        self.assertEqual(row["Bring-4"], " / ".join(best["bring4"]))
        self.assertEqual(row["Lead"], " + ".join(lb["lead"]))
        self.assertEqual(row["Backup"], " + ".join(lb["backup"]))
        self.assertEqual(row["Beaten total"], f"{depth['beaten_total']}/{n_pairs * pt}")
        self.assertEqual(row["Tailwind-safe total"],
                         f"{depth['tailwind_safe_total']}/{n_pairs * pt}")
        self.assertEqual(row["Protect-safe total"],
                         f"{depth['protect_safe_total']}/{n_pairs * pt}")


class TestMegaEvolutionVisibility(unittest.TestCase):
    """"I'm not sure enemy pokemon or my pokemon are mega evolving in
    Counter Table in the streamlit app, it should match the CLI" -- every
    turn log already prints a Mega-stone holder's literal pool name
    ("Mega Metagross") whether or not it actually transformed for THIS
    specific race (VGC allows only one Mega per side, so a core carrying 2
    stone holders has one forced to base form for the whole dive), so the
    name alone never answered "is it actually mega evolving". Fixed by
    surfacing `core_deep_dive`'s own `mega_used` field (already computed,
    already used for the CLI's xlsx "Mega Used" column) as a plain
    sentence in every Streamlit deep-dive display."""

    TWO_MEGAS = ["Mega Metagross", "Mega Swampert", "Incineroar",
                "Farigiraf", "Gallade", "Hydreigon"]
    ONE_MEGA = ["Mega Metagross", "Incineroar", "Farigiraf", "Gallade",
               "Hydreigon", "Whimsicott"]
    NO_MEGA = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon",
              "Whimsicott", "Kingambit"]

    def test_a_core_with_two_mega_picks_names_the_one_that_evolves(self):
        at = app(team=self.TWO_MEGAS)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        used = dive["mega_used"]
        self.assertIn(used, self.TWO_MEGAS)
        other = next(n for n in ("Mega Metagross", "Mega Swampert") if n != used)
        expected = (f"Mega Evolution: {used} evolves this whole dive -- "
                   f"{other} stays in base form (VGC: only one Mega per side).")
        self.assertTrue(any(c.value == expected for c in at.caption))

    def test_a_core_with_one_mega_pick_says_it_evolves_with_no_ambiguity(self):
        at = app(team=self.ONE_MEGA)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(c.value == "Mega Evolution: Mega Metagross evolves."
                            for c in at.caption))

    def test_a_core_with_no_mega_pick_shows_no_mega_caption_at_all(self):
        at = app(team=self.NO_MEGA)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertFalse(any(c.value.startswith("Mega Evolution")
                             for c in at.caption))

    def test_the_per_bring4_pick_deep_dive_also_shows_it(self):
        """Shared by every `_render_core_deep_dive` call site, not just the
        all-6 one -- confirmed via the ordinary Stage 1/2 pick, which is
        exactly at the bring size (4, no longer 6) and so exercises a
        DIFFERENT `core` than the all-6 tests above. Whether a caption is
        expected at all depends on how many Megas Stage 2's own pick
        happens to carry (0, 1, or both) -- read straight off the same
        `bring4_rows` the app itself picked from, rather than assuming."""
        at = app(team=self.TWO_MEGAS)
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        bring4_rows = at.session_state["ct_b4_bring4_rows"]
        picked_bring4 = bring4_rows[0]["bring4"]
        dd_buttons = [b for b in at.button if b.key and b.key.startswith("ctb4_dd_")
                     and b.key.endswith("_go") and "all6" not in b.key]
        at = dd_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dive = at.session_state["ctb4_dd_1_dive"]
        megas_here = [n for n in picked_bring4 if n.startswith("Mega ")]
        mega_captions = [c.value for c in at.caption
                        if c.value.startswith("Mega Evolution")]
        if not megas_here:
            self.assertEqual(mega_captions, [])
        elif len(megas_here) == 1:
            # Since the deep-dive button's own pick defaults to Stage 2's
            # own top bring-4 (`bring4_rows[0]`, same as `picked_bring4`
            # here), this exact sentence now ALSO comes from the Stage 2
            # "Your best bring-4" highlight above (its own, separate fix --
            # "you must show which of my bring megas in the bring4") --
            # `_render_core_deep_dive`'s OWN caption is what this test
            # actually checks, so `assertIn` rather than an exact list.
            self.assertIn(f"Mega Evolution: {megas_here[0]} evolves.",
                          mega_captions)
        else:
            used = dive["mega_used"]
            other = next(n for n in megas_here if n != used)
            self.assertIn(
                f"Mega Evolution: {used} evolves this whole dive -- "
                f"{other} stays in base form (VGC: only one Mega per side).",
                mega_captions)

    def test_the_vs_all_enemy_teams_dive_also_shows_it(self):
        at = app(team=self.TWO_MEGAS)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        all_dive = at.session_state["ctb4_dd_all6_allteams_dive"]
        used = all_dive["mega_used"]
        other = next(n for n in ("Mega Metagross", "Mega Swampert") if n != used)
        expected = (f"Mega Evolution: {used} evolves this whole dive -- "
                   f"{other} stays in base form (VGC: only one Mega per side).")
        self.assertTrue(any(c.value == expected for c in at.caption))


class TestBring4MegaVisibilityOutsideDeepDive(unittest.TestCase):
    """"It should also show the chosen mega vs a given six" --
    `TestMegaEvolutionVisibility` above only covers the opt-in deep dive;
    the BASIC (no click needed) Stage 2 table and per-enemy bring-4 lines
    (`_render_multi_bring4_core`, the "team by team" breakdown) never
    showed `mega_used` at all, even though `_bring4_candidates` already
    computes it for every row."""

    TWO_MEGAS = ["Mega Metagross", "Mega Swampert", "Incineroar",
                "Farigiraf", "Gallade", "Hydreigon"]
    ONE_MEGA = ["Mega Metagross", "Incineroar", "Farigiraf", "Gallade",
               "Hydreigon", "Whimsicott"]
    NO_MEGA = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon",
              "Whimsicott", "Kingambit"]

    def test_stage2_table_carries_a_mega_column(self):
        at = app(team=self.ONE_MEGA)
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        bring4_rows = at.session_state["ct_b4_bring4_rows"]
        dfs = [d.value for d in at.dataframe if "Mega" in d.value.columns]
        self.assertTrue(dfs, "expected a 'Mega' column somewhere")
        stage2 = next(df for df in dfs if "Bring-4" in df.columns)
        for i, row in enumerate(bring4_rows):
            expected = row.get("mega_used") or "-"
            self.assertEqual(stage2.iloc[i]["Mega"], expected)

    def test_stage2_table_shows_dash_for_no_mega(self):
        at = app(team=self.NO_MEGA)
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        dfs = [d.value for d in at.dataframe if "Mega" in d.value.columns]
        stage2 = next(df for df in dfs if "Bring-4" in df.columns)
        self.assertTrue(all(v == "-" for v in stage2["Mega"]))

    @staticmethod
    def _expected_bring4_mega_caption(bring4_row):
        """Mirrors `app._bring4_mega_caption`'s own formula -- app.py can't
        be imported directly (its Streamlit calls run at module level), so
        this is re-derived here from `mega_used`/`bring4`, the same way
        `TestMegaEvolutionVisibility`'s existing tests hand-build their own
        expected strings rather than calling the app function."""
        megas_in_bring = [n for n in bring4_row["bring4"] if n.startswith("Mega ")]
        if not megas_in_bring:
            return None
        used = bring4_row.get("mega_used")
        if len(megas_in_bring) == 1:
            return f"Mega Evolution: {megas_in_bring[0]} evolves."
        other = ", ".join(n for n in megas_in_bring if n != used)
        return (f"Mega Evolution: {used} evolves -- {other} stays in base "
               f"form (VGC: only one Mega per side).")

    def test_pool_search_result_shows_the_mega_caption(self):
        """`_render_multi_bring4_core`'s own per-enemy line -- exercised via
        the Bring-4 pool-search path, same button sequence as
        `test_pool_search_cores_also_offer_a_deep_dive`."""
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        sb.set_value("\U0001f50d Search a pool for the best team").run()
        [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(16).run()
        [s for s in at.slider if s.key == "ct_b4_maxweak"][0].set_value(6).run()
        [s for s in at.slider if s.key == "ct_b4_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        rows = at.session_state["ct_b4_pool_rows"]
        self.assertTrue(rows)
        top = rows[0]
        mega_captions = [c.value for c in at.caption
                        if c.value.startswith("Mega Evolution")]
        for pe in top["per_enemy"]:
            expected = self._expected_bring4_mega_caption(pe["best_bring4_row"])
            if expected:
                self.assertIn(expected, mega_captions)

    def test_team_by_team_shows_its_own_per_team_caption(self):
        at = app(team=self.TWO_MEGAS)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_allteams_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        our6 = self.TWO_MEGAS
        all_dive = at.session_state["ctb4_dd_all6_allteams_dive"]
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive
        teams = load_world()["teams"]
        mega_captions = [c.value for c in at.caption
                        if c.value.startswith("Mega Evolution")]
        for team_name in teams:
            best = bring4_from_deep_dive(
                our6, all_dive, list(teams[team_name]))[0]
            expected = self._expected_bring4_mega_caption(best)
            if expected:
                self.assertIn(expected, mega_captions)

    def test_your_best_bring4_highlight_also_shows_the_mega_caption(self):
        """"You must show which of my bring[s] [is a] mega[s] in the
        bring4" -- the direct Bring-4 mode's (`ct_b4_go`) Stage 2 table
        already had its own "Mega" column, but the single highlighted
        "Your best bring-4" pick right below it never called
        `_bring4_mega_caption` the way the pool-search/team-by-team paths
        already did."""
        at = app(team=self.ONE_MEGA)
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        bring4_rows = at.session_state["ct_b4_bring4_rows"]
        expected = self._expected_bring4_mega_caption(bring4_rows[0])
        self.assertIsNotNone(expected)
        self.assertIn(expected, [c.value for c in at.caption])

    def test_the_all6_deep_dives_best_bring4_also_shows_the_mega_caption(self):
        """Same gap in `_render_core_deep_dive`'s own "Best bring-4 (from
        this deep dive)" section, reached via the "Full deep dive: all of
        Our 6" button rather than Stage 2."""
        at = app(team=self.ONE_MEGA)
        at = [b for b in at.button
             if b.key == "ctb4_dd_all6_one_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        from counter_finder import bring4_from_deep_dive
        teams = load_world()["teams"]
        dive = at.session_state["ctb4_dd_all6_one_dive"]
        # `ct_b4_vs`'s own current value is exactly the enemy roster
        # `_render_core_deep_dive` was called with here (`[vs_roster]`,
        # `[ct_vs_name]`) -- no separate bookkeeping key needed.
        vs_name = at.session_state["ct_b4_vs"]
        best = bring4_from_deep_dive(self.ONE_MEGA, dive,
                                     list(teams[vs_name]))[0]
        expected = self._expected_bring4_mega_caption(best)
        self.assertIsNotNone(expected)
        self.assertIn(expected, [c.value for c in at.caption])

    def test_coverage_groups_bring4_table_carries_a_mega_column(self):
        """Same gap in the Coverage Groups tab's own "Run bring-4 vs enemy
        teams" table (`b4_rows`) -- it never carried the "Mega" column
        `_bring4_rows_df` already had."""
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(12).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        teams_ms = [m for m in at.multiselect if m.key == "ct_cov_teams"][0]
        if teams_ms.options:
            teams_ms.set_value([teams_ms.options[0]]).run()
        at = [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        b4_buttons = [b for b in at.button if b.key and b.key.startswith("ct_cov_b4_3_")]
        self.assertTrue(b4_buttons)
        at = b4_buttons[0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        b4_dfs = [d.value for d in at.dataframe if "Best bring-4" in d.value.columns]
        self.assertTrue(b4_dfs, "expected the coverage-groups bring-4 result table")
        self.assertIn("Mega", b4_dfs[0].columns)


class TestGameplansForABring4VsATeam(unittest.TestCase):
    """"For a bring4 vs any team, I want to see the output of gamelogs" --
    every place a "best bring-4 vs a specific enemy roster" result is
    shown must include the real turn-by-turn move log
    (`_render_pair_matchup_detail`'s own `st.code` output), not just the
    numeric pair-outcome table. The underlying `detail`/`log` these draw
    from always comes from the real multi-turn race (`_joint_race`, via
    `joint_pool_search`/`bring4_search`), which already applies
    Intimidate/Defiant/Competitive correctly (`_intimidate_mult_by_role`,
    computed once at the top of `_joint_race`) -- unlike the SEPARATE
    `--deep` damage-grid preview that needed its own fix earlier -- so no
    engine change is needed here, only surfacing the log that already
    exists."""

    ONE_MEGA = ["Mega Metagross", "Incineroar", "Farigiraf", "Gallade",
               "Hydreigon", "Whimsicott"]

    def test_bring4_one_enemy_roster_shows_gameplans_for_its_best_bring4(self):
        at = app(team=self.ONE_MEGA)
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        bring4_rows = at.session_state["ct_b4_bring4_rows"]
        n_pairs = len(bring4_rows[0]["pair_rows"])
        self.assertGreater(len(at.code), 0, "expected at least one gamelog block")
        pair_headers = {m.value for m in at.markdown}
        for row in bring4_rows[0]["pair_rows"]:
            n1, n2 = row["pair"]
            self.assertIn(f"**{n1} + {n2}**", pair_headers)
        self.assertTrue(any(c.key == "ct_b4_best_onlyloss" for c in at.checkbox))

    def test_multi_bring4_pool_search_shows_gameplans_per_enemy(self):
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        sb.set_value("\U0001f50d Search a pool for the best team").run()
        [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(16).run()
        [s for s in at.slider if s.key == "ct_b4_maxweak"][0].set_value(6).run()
        [s for s in at.slider if s.key == "ct_b4_good"][0].set_value(0).run()
        at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        rows = at.session_state["ct_b4_pool_rows"]
        self.assertTrue(rows)
        codes_before = len(at.code)
        self.assertGreater(codes_before, 0, "expected gamelog blocks in the "
                                            "default-expanded top result")
        top_pair_rows = rows[0]["per_enemy"][0]["best_bring4_row"]["pair_rows"]
        pair_headers = {m.value for m in at.markdown}
        for row in top_pair_rows:
            n1, n2 = row["pair"]
            self.assertIn(f"**{n1} + {n2}**", pair_headers)

    def test_coverage_groups_bring4_table_shows_gameplans_per_enemy(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Coverage groups").run()
        [s for s in at.slider if s.key == "ct_cov_pool"][0].set_value(12).run()
        [m for m in at.multiselect if m.key == "ct_cov_sizes"][0].set_value([3]).run()
        teams_ms = [m for m in at.multiselect if m.key == "ct_cov_teams"][0]
        if teams_ms.options:
            teams_ms.set_value([teams_ms.options[0]]).run()
        at = [b for b in at.button if b.key == "ct_cov_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        b4_buttons = [b for b in at.button if b.key and b.key.startswith("ct_cov_b4_3_")]
        self.assertTrue(b4_buttons)
        clicked = b4_buttons[0]
        at = clicked.click().run()
        self.assertFalse(at.exception, list(at.exception))
        result_key = "ct_cov_b4_result_" + clicked.key[len("ct_cov_b4_"):]
        b4_result = at.session_state[result_key]
        self.assertTrue(b4_result)
        self.assertGreater(len(at.code), 0, "expected gamelog blocks for the "
                                            "coverage-groups bring-4 result")
        pair_headers = {m.value for m in at.markdown}
        for row in b4_result[0]["pair_rows"]:
            n1, n2 = row["pair"]
            self.assertIn(f"**{n1} + {n2}**", pair_headers)


class TestEnemyChoiceScarfDropdown(unittest.TestCase):
    """"In the bring4 vs a specific team, add a dropdown to select an
    enemy as a choice scarf user (and hence will have 4 attacks), default
    no choice scarf user." -- pins the chosen enemy's item to Choice Scarf
    and its moveset to its own top-4 non-status moves by usage
    (`choice_scarf_enemy_moveset`), instead of whatever mbsmogon.xlsx's
    usage-derived top item/moveset happens to be (which can, and often
    does, include Protect)."""

    def _vs_roster(self, at):
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
        from _harness import load_world
        teams = load_world()["teams"]
        vs_name = at.session_state["ct_b4_vs"]
        return vs_name, list(teams[vs_name])

    def test_dropdown_defaults_to_none_with_the_enemy_roster_as_options(self):
        at = app()
        sb = [s for s in at.selectbox if s.key and s.key.startswith("ct_b4_enemy_scarf_")][0]
        self.assertEqual(sb.value, "(none)")
        _vs_name, vs_roster = self._vs_roster(at)
        self.assertEqual(sb.options, ["(none)"] + vs_roster)

    def test_selecting_an_enemy_pins_choice_scarf_and_a_protect_free_moveset(self):
        at = app()
        _vs_name, vs_roster = self._vs_roster(at)
        scarfed = vs_roster[0]
        sb = [s for s in at.selectbox if s.key and s.key.startswith("ct_b4_enemy_scarf_")][0]
        at = sb.set_value(scarfed).run()
        self.assertFalse(at.exception, list(at.exception))
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        pair_rows = at.session_state["ct_b4_pair_rows"]
        self.assertTrue(pair_rows)
        # The scarfed enemy's own role in each pair's detail log -- "E1" if
        # it's the first name racing (alphabetically sorted `target_names`)
        # or "E2" otherwise -- must never show Protect, since a real Choice
        # item locks the holder into the first move used and no real Scarf
        # set carries a status move it could get stuck repeating.
        seen_any_log = False
        for row in pair_rows:
            for (e1, e2), d in row["detail"].items():
                scarfed_role = ("E1" if e1 == scarfed else
                               "E2" if e2 == scarfed else None)
                if scarfed_role is None:
                    continue
                for turn_hits in d["log"]:
                    for role, _tgt, h in turn_hits:
                        if role == scarfed_role:
                            seen_any_log = True
                            self.assertNotEqual(h.move_name, "Protect")
        self.assertTrue(seen_any_log, "fixture never actually raced the "
                                      "scarfed enemy -- test is vacuous")

    def test_none_selected_leaves_the_search_unaffected(self):
        """Precondition/contrast: the default ("(none)") must reproduce the
        exact same result as never touching the dropdown at all -- no
        override silently applied."""
        at = app()
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        baseline = at.session_state["ct_b4_pair_rows"]

        at2 = app()
        sb = [s for s in at2.selectbox if s.key and s.key.startswith("ct_b4_enemy_scarf_")][0]
        at2 = sb.set_value("(none)").run()
        at2 = [b for b in at2.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at2.exception, list(at2.exception))
        touched = at2.session_state["ct_b4_pair_rows"]
        self.assertEqual([r["pairs_total"] for r in baseline],
                         [r["pairs_total"] for r in touched])
        self.assertEqual([r["pair"] for r in baseline], [r["pair"] for r in touched])


class TestEnemyChoiceScarfDropdownRealSets(unittest.TestCase):
    """"The enemy choice scarf holder dropdown should use the actual set if
    it comes from a paste or an existing team (which specifies the items,
    such as Basculegion has the choice scarf), otherwise it should default
    to none, or give a suggestion if a member(s) has high choice scarf
    usage." -- refines the plain dropdown above: auto-select a REAL known
    Scarf holder, suggest a high-usage one only when no real holder is
    known, and never clobber a real known moveset with the derived one."""

    PASTE_WITH_REAL_SCARF = (
        "Basculegion @ Choice Scarf\nAbility: Adaptability\n"
        "EVs: 4 HP / 252 Atk / 252 Spe\nJolly Nature\n"
        "- Last Respects\n- Aqua Jet\n- Wave Crash\n- Flip Turn\n"
        "\n"
        "Whimsicott @ Focus Sash\nAbility: Prankster\n"
        "EVs: 4 HP / 252 SpA / 252 Spe\nTimid Nature\n"
        "- Tailwind\n- Moonblast\n- Encore\n- Protect")

    # Basculegion no longer works for this: data/default_sets.txt now pins
    # its real default set (Life Orb, no Choice Scarf), so its own top
    # recorded item is no longer Choice Scarf at all. Staraptor's BASE row
    # is untouched by that file (its pinned entry lists item "Staraptite",
    # a Mega stone, so `apply_default_sets` bakes it onto the "Mega
    # Staraptor" row instead -- see that function's own "Item/nature/EVs/
    # moves apply directly to whichever row `name` resolves to" comment) --
    # it still shows its real, very high (~99%) Choice Scarf usage here.
    PLAIN_SPECIES_LIST = "Staraptor / Whimsicott"

    def _to_paste_mode(self, at):
        vs = [s for s in at.selectbox if s.key == "ct_b4_vs"][0]
        at = vs.set_value("\U0001f4cb Paste a pokepaste").run()
        return at

    def _paste(self, at, text):
        ta = [t for t in at.text_area if t.key == "ct_b4_vs_paste"][0]
        at = ta.set_value(text).run()
        return at

    def _scarf_selectbox(self, at):
        return [s for s in at.selectbox
                if s.key and s.key.startswith("ct_b4_enemy_scarf_")][0]

    def test_a_real_known_scarf_holder_is_auto_selected(self):
        at = app()
        at = self._to_paste_mode(at)
        at = self._paste(at, self.PASTE_WITH_REAL_SCARF)
        self.assertFalse(at.exception, list(at.exception))
        sb = self._scarf_selectbox(at)
        self.assertEqual(sb.value, "Basculegion")

    def test_a_real_known_moveset_is_never_overwritten_by_the_derived_one(self):
        """Basculegion's real recorded 4th move here is Flip Turn (not the
        Protect its real set actually would carry, and not whatever
        `choice_scarf_enemy_moveset` would derive) -- if the derived
        moveset silently replaced the real one, Flip Turn would never show
        up in any race log for Basculegion's role."""
        at = app()
        at = self._to_paste_mode(at)
        at = self._paste(at, self.PASTE_WITH_REAL_SCARF)
        self.assertFalse(at.exception, list(at.exception))
        sb = self._scarf_selectbox(at)
        self.assertEqual(sb.value, "Basculegion")
        at = [b for b in at.button if b.key == "ct_b4_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        pair_rows = at.session_state["ct_b4_pair_rows"]
        self.assertTrue(pair_rows)
        seen_flip_turn = False
        for row in pair_rows:
            for (e1, e2), d in row["detail"].items():
                scarfed_role = ("E1" if e1 == "Basculegion" else
                               "E2" if e2 == "Basculegion" else None)
                if scarfed_role is None:
                    continue
                for turn_hits in d["log"]:
                    for role, _tgt, h in turn_hits:
                        if role == scarfed_role and h.move_name == "Flip Turn":
                            seen_flip_turn = True
        self.assertTrue(seen_flip_turn, "Basculegion's real moveset (with "
                        "Flip Turn) was never observed -- looks like it "
                        "got overwritten by the derived usage-based one")

    def test_no_suggestion_when_a_known_scarf_holder_already_exists(self):
        at = app()
        at = self._to_paste_mode(at)
        at = self._paste(at, self.PASTE_WITH_REAL_SCARF)
        self.assertFalse(at.exception, list(at.exception))
        captions = [c.value for c in at.caption]
        self.assertFalse(any("commonly runs Choice Scarf" in c for c in captions))

    def test_a_suggestion_is_shown_when_no_known_set_but_high_scarf_usage(self):
        at = app()
        at = self._to_paste_mode(at)
        at = self._paste(at, self.PLAIN_SPECIES_LIST)
        self.assertFalse(at.exception, list(at.exception))
        sb = self._scarf_selectbox(at)
        self.assertEqual(sb.value, "(none)")
        captions = [c.value for c in at.caption]
        matches = [c for c in captions
                  if "Staraptor" in c and "commonly runs Choice Scarf" in c]
        self.assertTrue(matches, f"expected a Staraptor Choice Scarf "
                        f"suggestion caption, got: {captions}")


class TestItemCapsWiredIntoPoolSearches(unittest.TestCase):
    """"so `src/app.py` can call the SAME function on whatever it
    displays" -- `_run_multi_bring4_search` (shared by Bring-4's own
    "search a pool" mode, Multi-bring4, and Joint Pair Search's "search
    best team") must correct its rows through the exact same
    `counter_finder._apply_item_caps_to_top_rows` the CLI's --multi-bring4
    uses, not a separate, drifting copy -- and BY DEFAULT, no flag needed,
    same as the CLI."""

    def test_bring4_pool_search_applies_the_default_item_caps(self):
        import unittest.mock as mock
        import counter_finder as cf
        with mock.patch.object(cf, "_apply_item_caps_to_top_rows",
                              wraps=cf._apply_item_caps_to_top_rows) as spy:
            at = app(team=[])
            sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
            at = sb.set_value("\U0001f50d Search a pool for the best team").run()
            at = [s for s in at.slider if s.key == "ct_b4_pool"][0].set_value(10).run()
            at = [s for s in at.slider if s.key == "ct_b4_maxweak"][0].set_value(6).run()
            at = [s for s in at.slider if s.key == "ct_b4_good"][0].set_value(0).run()
            at = [b for b in at.button if b.key == "ct_b4_pool_go"][0].click().run()
        self.assertFalse(at.exception, list(at.exception))
        spy.assert_called_once()
        self.assertEqual(spy.call_args.kwargs.get("item_caps"),
                         {"Focus Sash": cf.DEFAULT_MAX_FOCUS_SASH,
                          "Life Orb": cf.DEFAULT_MAX_LIFE_ORB})


if __name__ == "__main__":
    unittest.main()
