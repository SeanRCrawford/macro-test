"""Hand-editing a set in the Team Builder, and having it stick.

Reported: "I want to be able to edit abilities/optimise in the team. For
instance, Arcanine-Hisui with intimidate may be much better than rock head. I
also need to be able to select moves, often the moves used aren't that good."

The engine already honoured both -- combatants.make_team reads
sets[name]['ability'] and solver.build_moveset reads sets[name]['moves'] -- so
this is about the edit reaching that dict, surviving the next Apply, and being
visible in the table above it.

The app is driven headless rather than inspected: three of the four defects
here (the wiped pending edit, the stale Ability column, the optimiser
discarding hand-set abilities) look completely fine in the source.

Every per-Pokemon editor widget's key is suffixed with a generation number
(`f"abil_{mon}_{gen}"`, not `f"abil_{mon}"`) -- see `_bump_builder_gen` in
app.py for why: reported AGAIN later ("the Team Builder app still seems very
glitchy; it never applies changes"), traced to a second, deeper instance of
the same class of bug -- Reset/Load/Optimise correctly updated
`session_state["sets"]`, but the input widgets themselves kept showing
whatever the user had last typed, because a Streamlit widget's own `key=`
state (once set) overrides its `value=`/`index=` on every later render
regardless of what the underlying data says. Bumping a generation counter and
folding it into every such widget's key forces a fresh widget -- with no
prior state to cling to -- on exactly the renders where that matters. Tests
here look widgets up by KEY PREFIX (`_find_one`) rather than an exact key,
since the generation suffix changes across reruns.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
TEAM = ["Arcanine-Hisui", "Hydreigon", "Gallade", "Gholdengo",
        "Incineroar", "Farigiraf"]


def app(sets=None):
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=900)
    at.session_state["team"] = list(TEAM)
    at.session_state["sets"] = dict(sets or {})
    return at.run()


def click(at, key):
    return [b for b in at.button if b.key == key][0].click().run()


def _find_one(widgets, prefix):
    """The single widget (from an AppTest widget sequence, e.g. `at.selectbox`)
    whose key is `prefix` followed by `_<generation number>` -- the shape
    every per-Pokemon editor widget's key has now (see module docstring).
    Fails loudly, with every candidate key, if that's not exactly one."""
    cands = [w for w in widgets if w.key and w.key.startswith(prefix + "_")]
    assert len(cands) == 1, (prefix, [w.key for w in widgets if w.key])
    return cands[0]


def abil_selectbox(at, mon):
    return _find_one(at.selectbox, f"abil_{mon}")


def mv_multiselect(at, mon, any_move=False):
    return _find_one(at.multiselect, f"mv_{mon}_{'any' if any_move else 'usage'}")


class TestTheEditorsExist(unittest.TestCase):

    def test_a_pokemon_with_two_abilities_gets_a_picker(self):
        at = app()
        keys = [s.key for s in at.selectbox if s.key]
        self.assertTrue(any(k.startswith("abil_Arcanine-Hisui_") for k in keys),
                        keys)

    def test_both_of_its_real_abilities_are_offered(self):
        at = app()
        opts = abil_selectbox(at, "Arcanine-Hisui").options
        self.assertIn("Rock Head", opts)
        self.assertIn("Intimidate", opts)

    def test_every_pokemon_gets_a_move_picker(self):
        at = app()
        keys = [m.key for m in at.multiselect if m.key]
        for mon in TEAM:
            self.assertTrue(any(k.startswith(f"mv_{mon}_usage_") for k in keys),
                            mon)


class TestAnEditReachesTheOverrides(unittest.TestCase):

    def test_applying_an_ability_writes_it(self):
        at = app()
        abil_selectbox(at, "Arcanine-Hisui").select("Intimidate").run()
        click(at, "abil_apply")
        self.assertEqual(at.session_state["sets"]["Arcanine-Hisui"]["ability"],
                         "Intimidate")

    def test_applying_moves_writes_them(self):
        at = app()
        mv_multiselect(at, "Arcanine-Hisui").select("Rock Slide").run()
        click(at, "mv_apply")
        self.assertEqual(at.session_state["sets"]["Arcanine-Hisui"]["moves"],
                         ["Rock Slide"])

    def test_applying_one_editor_does_not_wipe_a_pending_edit_in_another(self):
        """st.rerun() from an Apply button stops the script there, so every
        editor BELOW it never renders -- and Streamlit drops widget state for
        widgets that did not render. Applying abilities silently discarded a
        pending move edit."""
        at = app()
        mv_multiselect(at, "Arcanine-Hisui").select("Rock Slide").run()
        abil_selectbox(at, "Arcanine-Hisui").select("Intimidate").run()
        at = click(at, "abil_apply")
        self.assertEqual(mv_multiselect(at, "Arcanine-Hisui").value,
                         ["Rock Slide"])
        click(at, "mv_apply")
        spec = at.session_state["sets"]["Arcanine-Hisui"]
        self.assertEqual(spec["ability"], "Intimidate")
        self.assertEqual(spec["moves"], ["Rock Slide"])

    def test_reset_clears_only_its_own_key(self):
        at = app({"Arcanine-Hisui": {"ability": "Intimidate",
                                     "moves": ["Rock Slide"]}})
        click(at, "abil_reset")
        spec = at.session_state["sets"]["Arcanine-Hisui"]
        self.assertNotIn("ability", spec)
        self.assertEqual(spec["moves"], ["Rock Slide"])


class TestResetActuallyRefreshesTheWidget(unittest.TestCase):
    """"the Team Builder app still seems very glitchy; it never applies
    changes" -- the SECOND, deeper bug. Resetting DID correctly clear
    `session_state["sets"]` (see `TestAnEditReachesTheOverrides` above), but
    the widget itself silently kept showing the user's last hand-picked
    value, because its `key=` state overrides `value=`/`index=` once set.
    That's what "doesn't seem to apply" looks like from the browser even
    though the data is right underneath."""

    def test_resetting_an_ability_visibly_reverts_the_dropdown(self):
        at = app()
        abil_selectbox(at, "Arcanine-Hisui").select("Intimidate").run()
        at = click(at, "abil_apply")
        self.assertEqual(abil_selectbox(at, "Arcanine-Hisui").value, "Intimidate")
        at = click(at, "abil_reset")
        self.assertEqual(abil_selectbox(at, "Arcanine-Hisui").value, "Rock Head",
                         "the dropdown must show the usage default again, not "
                         "the value the user picked before resetting")

    def test_resetting_moves_visibly_clears_the_multiselect(self):
        at = app()
        mv_multiselect(at, "Arcanine-Hisui").select("Rock Slide").run()
        at = click(at, "mv_apply")
        self.assertEqual(mv_multiselect(at, "Arcanine-Hisui").value, ["Rock Slide"])
        at = click(at, "mv_reset")
        self.assertEqual(mv_multiselect(at, "Arcanine-Hisui").value, [],
                         "the multiselect must go back to empty (usage-standard "
                         "set), not keep showing the picked move")

    def test_the_load_upload_and_default_paths_also_refresh_widgets(self):
        """Load/Upload/"Use default 6" all change `sets`/`team` directly
        rather than through one widget's own interaction -- exactly like
        Reset -- so a team.json reusing a species with a DIFFERENT override
        would otherwise leave that species' widgets showing whatever an
        EARLIER team happened to leave in their key. Checked at the source:
        driving a real file upload/on-disk saved team through AppTest is
        its own separate concern already covered by `test_my_teams_upload.py`
        and the Load/Save round-trip tests; this only needs to confirm the
        refresh call is actually there at each of the three call sites."""
        source = open(APP, encoding="utf-8").read()
        tab_start = source.index("with tab_build:")
        tab_end = source.index("# ------------------------------------------------------------------ generate")
        section = source[tab_start:tab_end]
        for marker in ('st.button("Load"', 'up is not None:', 'st.button("Use default 6"'):
            idx = section.index(marker)
            nearby = section[idx:idx + 700]
            self.assertIn("_bump_builder_gen()", nearby, marker)


class TestTheTableShowsWhatIsSimulated(unittest.TestCase):

    def test_the_ability_column_reads_the_override(self):
        """The same defect that was reported for stat points, one column over:
        the simulations used the override, the table showed the usage default."""
        at = app({"Arcanine-Hisui": {"ability": "Intimidate"}})
        row = at.dataframe[0].value.set_index("Pokemon").loc["Arcanine-Hisui"]
        self.assertIn("Intimidate", row["Ability"])

    def test_an_unedited_pokemon_still_shows_its_usage_default(self):
        at = app()
        row = at.dataframe[0].value.set_index("Pokemon").loc["Arcanine-Hisui"]
        self.assertIn("Rock Head", row["Ability"])
        self.assertNotIn("(set)", row["Ability"])

    def test_the_table_shows_computed_stats_from_the_evs(self):
        """"let me see actual stats for my members based on their EVs" --
        the team overview table, not just the raw EV spread."""
        at = app()
        row = at.dataframe[0].value.set_index("Pokemon").loc["Arcanine-Hisui"]
        col = "Stats (HP/Atk/Def/SpA/SpD/Spe)"
        self.assertIn(col, row.index)
        parts = row[col].split("/")
        self.assertEqual(len(parts), 6)
        self.assertTrue(all(p.isdigit() for p in parts), row[col])

    def test_a_stat_point_edit_changes_the_reported_stats(self):
        from _harness import load_world
        from stats import compute_stats
        w = load_world()
        p = w["merged"]["Arcanine-Hisui"]
        nat = w["natures"][p["nature"].lower()]
        base_hp = compute_stats(p["base_stats"], nat, p["evs"])["hp"]

        edited_evs = dict(p["evs"])
        edited_evs["hp"] = edited_evs.get("hp", 0) + 20
        at = app({"Arcanine-Hisui": {"evs": edited_evs}})
        row = at.dataframe[0].value.set_index("Pokemon").loc["Arcanine-Hisui"]
        col = "Stats (HP/Atk/Def/SpA/SpD/Spe)"
        edited_hp = int(row[col].split("/")[0])
        self.assertEqual(edited_hp, base_hp + 20)


class TestItReachesTheSimulation(unittest.TestCase):

    def test_the_ability_override_is_what_the_battle_uses(self):
        from _harness import load_world
        from combatants import make_team
        w = load_world()
        for ability, expected in ((None, "Rock Head"), ("Intimidate", "Intimidate")):
            sets = {"Arcanine-Hisui": {"ability": ability}} if ability else {}
            team = make_team(["Arcanine-Hisui", "Hydreigon"], w["merged"],
                             w["natures"], sets=sets)
            self.assertEqual(team[0].ability, expected)

    def test_a_move_the_usage_data_never_recorded_is_still_built(self):
        """The "any move in the game" escape hatch. Dropping an unrecorded move
        silently would make the hand-built set differ from the simulated one
        with nothing to show for it."""
        from _harness import load_world
        from solver import build_moveset, TOP_K_MOVES
        w = load_world()
        pinned = ["Flare Blitz", "Extreme Speed", "Protect", "Snarl"]
        ms = build_moveset(w["merged"]["Arcanine-Hisui"], w["moves"],
                           top_k=TOP_K_MOVES, only_moves=pinned)
        self.assertEqual([m.name for m, _ in ms], pinned)
        usage = {m for m, _ in w["merged"]["Arcanine-Hisui"]["moves_usage"]}
        self.assertNotIn("Snarl", usage)     # the point of the test
        self.assertEqual(dict((m.name, p) for m, p in ms)["Snarl"], 0.0)


class TestTheOptimiserDoesNotEatHandEdits(unittest.TestCase):

    def test_optimising_keeps_an_ability_and_stat_points(self):
        """It decides items and moves; replacing the whole dict threw away the
        two things it has no opinion about."""
        source = open(APP, encoding="utf-8").read()
        call = source[source.index("optimise_team(team, merged"):][:600]
        self.assertIn("prev.get(n)", call,
                      "optimiser output replaces the sets dict instead of "
                      "merging into it")


class TestApplyGivesAVisibleConfirmation(unittest.TestCase):
    """"the team editing in Team Builder is incredibly buggy and never seems
    to apply." Every edit was already landing in `st.session_state["sets"]`
    correctly (see `TestAnEditReachesTheOverrides` above, unaffected by this
    bug) -- what never happened is the user SEEING it: `st.success(...)`
    called right before `_defer_rerun()` rendered for exactly the one frame
    `st.rerun()` immediately discards, so the confirmation never reached a
    real browser. `_defer_rerun(flash=...)` stashes the message and shows it
    on the render that actually follows the rerun instead.
    """

    def test_applying_an_ability_shows_a_success_message(self):
        at = app()
        abil_selectbox(at, "Arcanine-Hisui").select("Intimidate").run()
        at = click(at, "abil_apply")
        self.assertTrue(at.success, "no confirmation reached the user at all")
        self.assertTrue(any("Abilities applied" in s.value for s in at.success))

    def test_applying_moves_shows_a_success_message(self):
        at = app()
        mv_multiselect(at, "Arcanine-Hisui").select("Rock Slide").run()
        at = click(at, "mv_apply")
        self.assertTrue(any("Moves applied" in s.value for s in at.success))

    def test_resetting_also_confirms(self):
        """Reset buttons never showed any message at all, doomed or not --
        the same silent-looking-broken symptom applies to them too."""
        at = app({"Arcanine-Hisui": {"ability": "Intimidate"}})
        at = click(at, "abil_reset")
        self.assertTrue(any("reset" in s.value.lower() for s in at.success))

    def test_a_second_apply_overwrites_the_first_confirmation_not_both(self):
        """Only the LATEST action's message should show -- a stale flash
        from an earlier click must not linger and be mistaken for feedback
        on the one just clicked."""
        at = app()
        abil_selectbox(at, "Arcanine-Hisui").select("Intimidate").run()
        at = click(at, "abil_apply")
        mv_multiselect(at, "Arcanine-Hisui").select("Rock Slide").run()
        at = click(at, "mv_apply")
        messages = [s.value for s in at.success]
        self.assertTrue(any("Moves applied" in m for m in messages))
        self.assertFalse(any("Abilities applied" in m for m in messages),
                         "the earlier Apply's confirmation must not still be "
                         "showing once a different edit has been applied")


class TestPasteTeamsheetJson(unittest.TestCase):
    """"I am primarily using the CLI for counter_table.py and then checking
    results in the streamlit app, so the export in the CLI needs to be able
    to export to the streamlit app, whether through a paste or otherwise" --
    `counter_table.py --teamsheet-json -` prints exactly this JSON shape
    (`{"pool": [...], "sets": {...}}`) for copy-pasting here, alongside the
    existing file-uploader (which `AppTest` itself cannot drive -- there is
    no existing test coverage for it either, for the same reason)."""

    PAYLOAD = ('{"pool": ["Kingambit", "Dragapult"], '
              '"sets": {"Kingambit": {"item": "Black Glasses", '
              '"moves": ["Sucker Punch", "Kowtow Cleave"]}}}')

    def test_pasting_valid_json_loads_team_and_sets(self):
        at = app()
        ta = [t for t in at.text_area if t.key == "paste_team_json"][0]
        ta.set_value(self.PAYLOAD).run()
        at = click(at, "paste_team_json_go")
        self.assertFalse(at.exception, list(at.exception))
        self.assertEqual(at.session_state["team"], ["Kingambit", "Dragapult"])
        self.assertEqual(at.session_state["sets"]["Kingambit"]["item"],
                         "Black Glasses")
        self.assertTrue(any("Loaded 2 Pokemon from pasted JSON" in s.value
                            for s in at.success))

    def test_invalid_json_shows_an_error_not_a_crash(self):
        at = app()
        ta = [t for t in at.text_area if t.key == "paste_team_json"][0]
        ta.set_value("not valid json{{{").run()
        at = click(at, "paste_team_json_go")
        self.assertFalse(at.exception, list(at.exception))
        self.assertTrue(any("Not valid JSON" in e.value for e in at.error))
        # The previously-loaded team must survive an invalid paste.
        self.assertEqual(at.session_state["team"], TEAM)


if __name__ == "__main__":
    unittest.main()
