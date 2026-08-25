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


class TestTheEditorsExist(unittest.TestCase):

    def test_a_pokemon_with_two_abilities_gets_a_picker(self):
        at = app()
        keys = {s.key for s in at.selectbox}
        self.assertIn("abil_Arcanine-Hisui", keys)

    def test_both_of_its_real_abilities_are_offered(self):
        at = app()
        opts = at.selectbox(key="abil_Arcanine-Hisui").options
        self.assertIn("Rock Head", opts)
        self.assertIn("Intimidate", opts)

    def test_every_pokemon_gets_a_move_picker(self):
        at = app()
        keys = {m.key for m in at.multiselect}
        for mon in TEAM:
            self.assertIn(f"mv_{mon}_usage", keys, mon)


class TestAnEditReachesTheOverrides(unittest.TestCase):

    def test_applying_an_ability_writes_it(self):
        at = app()
        at.selectbox(key="abil_Arcanine-Hisui").select("Intimidate").run()
        click(at, "abil_apply")
        self.assertEqual(at.session_state["sets"]["Arcanine-Hisui"]["ability"],
                         "Intimidate")

    def test_applying_moves_writes_them(self):
        at = app()
        at.multiselect(key="mv_Arcanine-Hisui_usage").select("Rock Slide").run()
        click(at, "mv_apply")
        self.assertEqual(at.session_state["sets"]["Arcanine-Hisui"]["moves"],
                         ["Rock Slide"])

    def test_applying_one_editor_does_not_wipe_a_pending_edit_in_another(self):
        """st.rerun() from an Apply button stops the script there, so every
        editor BELOW it never renders -- and Streamlit drops widget state for
        widgets that did not render. Applying abilities silently discarded a
        pending move edit."""
        at = app()
        at.multiselect(key="mv_Arcanine-Hisui_usage").select("Rock Slide").run()
        at.selectbox(key="abil_Arcanine-Hisui").select("Intimidate").run()
        click(at, "abil_apply")
        self.assertEqual(at.multiselect(key="mv_Arcanine-Hisui_usage").value,
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
        at.selectbox(key="abil_Arcanine-Hisui").select("Intimidate").run()
        at = click(at, "abil_apply")
        self.assertTrue(at.success, "no confirmation reached the user at all")
        self.assertTrue(any("Abilities applied" in s.value for s in at.success))

    def test_applying_moves_shows_a_success_message(self):
        at = app()
        at.multiselect(key="mv_Arcanine-Hisui_usage").select("Rock Slide").run()
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
        at.selectbox(key="abil_Arcanine-Hisui").select("Intimidate").run()
        at = click(at, "abil_apply")
        at.multiselect(key="mv_Arcanine-Hisui_usage").select("Rock Slide").run()
        at = click(at, "mv_apply")
        messages = [s.value for s in at.success]
        self.assertTrue(any("Moves applied" in m for m in messages))
        self.assertFalse(any("Abilities applied" in m for m in messages),
                         "the earlier Apply's confirmation must not still be "
                         "showing once a different edit has been applied")


if __name__ == "__main__":
    unittest.main()
