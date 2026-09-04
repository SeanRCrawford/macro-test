"""`tools/counter_table.py`'s search, in the Streamlit app.

    "Now, add the counter_table.py functionality to the streamlit app."

A new "Counter Table" tab wraps `counter_finder.py`'s bring4_search/
multi_bring4_coverage+exhaustive/beam/joint_pair_search directly -- no new
search logic, the same functions the CLI calls, so a result here can never
disagree with the CLI's own answer for the same inputs.
"""
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
    at = AppTest.from_file(APP, default_timeout=250)
    at.session_state["team"] = list(team if team is not None else TEAM)
    at.session_state["sets"] = dict(sets) if sets is not None else {}
    return at.run()


class TestCounterTableTabExists(unittest.TestCase):

    def test_it_renders(self):
        at = app()
        self.assertFalse(at.exception, list(at.exception))

    def test_the_three_modes_are_offered(self):
        at = app()
        radios = [r for r in at.radio if r.key == "ct_mode"]
        self.assertEqual(len(radios), 1)
        self.assertEqual(set(radios[0].options),
                         {"Bring-4 (one enemy roster)",
                          "Multi-bring4 (several enemy rosters)",
                          "Joint pair search"})

    def test_switching_to_multi_bring4_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Multi-bring4 (several enemy rosters)").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_mb4_pool" for s in at.slider))
        self.assertTrue(any(m.key == "ct_mb4_vs" for m in at.multiselect))

    def test_switching_to_joint_pair_mode_renders_its_controls(self):
        at = app()
        [r for r in at.radio if r.key == "ct_mode"][0].set_value(
            "Joint pair search").run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any(s.key == "ct_jp_partner" for s in at.selectbox))

    def test_choice_scarf_is_excluded_by_default(self):
        at = app()
        cb = [c for c in at.checkbox if c.key == "ct_allow_scarf"][0]
        self.assertFalse(cb.value)


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

    def test_pasting_our_6_reveals_a_text_area_and_parses(self):
        at = app(team=[])
        sb = [s for s in at.selectbox if s.key == "ct_b4_our"][0]
        at = sb.set_value("\U0001f4cb Paste a pokepaste").run()
        self.assertFalse(at.exception, list(at.exception))
        ta = [t for t in at.text_area if t.key == "ct_b4_our_paste"][0]
        at = ta.set_value(self.RAIN_PASTE).run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Archaludon" in s.value for s in at.success))


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
            self.assertEqual(mega_captions,
                            [f"Mega Evolution: {megas_here[0]} evolves."])
        else:
            used = dive["mega_used"]
            other = next(n for n in megas_here if n != used)
            self.assertEqual(mega_captions, [
                f"Mega Evolution: {used} evolves this whole dive -- "
                f"{other} stays in base form (VGC: only one Mega per side)."])

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


if __name__ == "__main__":
    unittest.main()
