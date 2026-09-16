"""The Battle Simulator tab -- interactive, turn-by-turn play on a real
`Battle`, not a second engine.

    "I want to add a new tool to the streamlit app - battle simulator. This
     will let me use a loaded team and play a match vs the preset/chosen
     teams, either with them bringing their optimal bring, them going
     through all 15 potential leads (+ optimal backs) in order, or with me
     selecting their bring."

Confirmed with the user: the human plays their OWN side manually every
turn; the opponent always plays its strongest available action once the
match is underway, regardless of which mode chose its bring; "all 15
leads" steps through them one at a time.

These drive the real app headless (`streamlit.testing.v1.AppTest`), the
same convention `test_app_pin.py` already uses for app.py -- it is a script
that runs `st.set_page_config`/`st.tabs()` at import time, so its helpers
can only be exercised through a real (headless) Streamlit run, not a plain
`import app`.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "src", "app.py")
SIM_TAB_INDEX = 6  # Team Builder, Generate, Lead/Back, Counter Table, Battle Viewer, Vs Team, Battle Simulator

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def fresh_app():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(APP, default_timeout=180)
    return at.run()


def sim_tab(at):
    return at.tabs[SIM_TAB_INDEX]


def submit_turn(at):
    """Click "Submit turn" and, if this exact turn happens to faint one of
    our own Pokemon (a real possibility any full-damage fixture can hit,
    not something most tests here are trying to exercise), auto-confirm
    with "Auto-pick" so the turn still fully resolves -- the dedicated
    faint-choice tests drive that pause themselves instead of using this
    helper. Returns the app state once the turn has genuinely gone through.
    """
    tab = sim_tab(at)
    submit = next(b for b in tab.button if b.label == "Submit turn")
    at = submit.click().run()
    if "sim_pending_turn" in at.session_state:
        tab = sim_tab(at)
        confirm = next(b for b in tab.button if b.label == "Confirm and resolve turn")
        at = confirm.click().run()
    return at


def seed_battle(at, our4, their4, our_mega=None, enemy_mega=None):
    """Build a real Battle exactly the way `app.sim_build_battle` does
    (combatants.make_team + solver.build_moveset), and inject it straight
    into session_state -- the same "skip the setup widgets, seed the state
    a search would have produced" convention `test_app_pin.py` already uses
    for its own recommendation dicts, just with a real Battle object
    instead of a plain dict."""
    from combatants import make_team
    from battle import Battle
    from solver import build_moveset, TOP_K_MOVES
    W = world()
    oc = make_team(our4, W["merged"], W["natures"], mega_transforms=our_mega)
    ec = make_team(their4, W["merged"], W["natures"], mega_transforms=enemy_mega)
    battle = Battle(oc, ec, W["typechart"], W["moves"])
    movesets = {c.name: build_moveset(W["merged"][c.name], W["moves"], top_k=TOP_K_MOVES)
               for c in oc + ec}
    at.session_state["sim_battle"] = battle
    at.session_state["sim_movesets"] = movesets
    at.session_state["sim_our4"] = list(our4)
    at.session_state["sim_their4"] = list(their4)
    at.session_state["sim_mode"] = "I choose their bring"
    at.session_state["sim_turn_log"] = []
    return at.run()


class TestBattleSimulatorSetup(unittest.TestCase):
    """The tab exists and its setup widgets render without error."""

    def test_tab_renders_with_setup_widgets(self):
        at = fresh_app()
        self.assertEqual(len(at.exception), 0)
        tab = sim_tab(at)
        labels = {r.label for r in tab.radio}
        self.assertIn("Our side", labels)
        self.assertIn("Their side", labels)
        self.assertIn("Their bring", labels)
        mode_radio = next(r for r in tab.radio if r.label == "Their bring")
        self.assertEqual(set(mode_radio.options),
                         {"Their optimal bring", "Step through all 15 leads",
                          "I choose their bring"})

    def test_two_mega_capable_picks_offer_a_mega_choice(self):
        """"Only one can mega" -- when our bring has two Mega-capable
        picks, the setup UI must ask which one (if either) transforms."""
        at = fresh_app()
        tab = sim_tab(at)
        src_radio = next(r for r in tab.radio if r.label == "Our side")
        src_radio.set_value("Any Pokemon").run()
        tab = sim_tab(at)
        lead_ms = next(m for m in tab.multiselect if m.label == "Our lead (2)")
        lead_ms.set_value(["Mega Gyarados", "Mega Charizard Y"]).run()
        tab = sim_tab(at)
        # The back multiselect's own default can otherwise pick up a THIRD
        # Mega-capable name (whichever sorts first among the rest of the
        # dataset), which would make this fixture ambiguous about what it's
        # actually testing.
        back_ms = next(m for m in tab.multiselect if m.label == "Our back (2)")
        back_ms.set_value(["Garchomp", "Kingambit"]).run()
        tab = sim_tab(at)
        mega_sb = next((sb for sb in tab.selectbox if "Mega Evolves" in sb.label), None)
        self.assertIsNotNone(mega_sb)
        self.assertEqual(set(mega_sb.options),
                         {"Mega Gyarados", "Mega Charizard Y", "Neither"})

    def test_one_mega_capable_pick_offers_a_mega_choice_too(self):
        """Unlike `species_data.mega_variants`'s "no ambiguity" default, a
        SOLE Mega-capable pick still gets asked -- staying base (keeping
        Intimidate/typing) is a real choice here, not a fixed property."""
        at = fresh_app()
        tab = sim_tab(at)
        src_radio = next(r for r in tab.radio if r.label == "Our side")
        src_radio.set_value("Any Pokemon").run()
        tab = sim_tab(at)
        lead_ms = next(m for m in tab.multiselect if m.label == "Our lead (2)")
        lead_ms.set_value(["Mega Gyarados", "Kingambit"]).run()
        tab = sim_tab(at)
        back_ms = next(m for m in tab.multiselect if m.label == "Our back (2)")
        back_ms.set_value(["Garchomp", "Basculegion"]).run()
        tab = sim_tab(at)
        mega_sb = next((sb for sb in tab.selectbox if "Mega Evolves" in sb.label), None)
        self.assertIsNotNone(mega_sb)
        self.assertEqual(set(mega_sb.options), {"Mega Gyarados", "Neither"})


class TestTheirSideAcceptsAPokepaste(unittest.TestCase):
    """"Let me use a pokepaste for the battle simulator as well" -- "Our
    side" already offered "Paste a pokepaste" (`our_side_pool`); "Their
    side" only offered "A saved team"/"Pick 6" (`their_side_pool`,
    shared with the Battle Viewer tab) until now."""

    SIX_MON_PASTE = (
        "Kingambit @ Black Glasses\nAbility: Defiant\nEVs: 4 HP / 252 Atk / 252 Spe\n"
        "Adamant Nature\n- Sucker Punch\n- Kowtow Cleave\n- Protect\n- Iron Head\n\n"
        "Basculegion @ Choice Scarf\nAbility: Adaptability\nEVs: 4 HP / 252 Atk / 252 Spe\n"
        "Jolly Nature\n- Last Respects\n- Aqua Jet\n- Wave Crash\n- Protect\n\n"
        "Whimsicott @ Focus Sash\nAbility: Prankster\nEVs: 4 HP / 252 SpA / 252 Spe\n"
        "Timid Nature\n- Tailwind\n- Moonblast\n- Encore\n- Protect\n\n"
        "Sinistcha @ Kasib Berry\nAbility: Hospitality\nEVs: 252 HP / 4 Def / 252 SpD\n"
        "Bold Nature\n- Matcha Gotcha\n- Rage Powder\n- Trick Room\n- Protect\n\n"
        "Garchomp @ Life Orb\nAbility: Rough Skin\nEVs: 4 HP / 252 Atk / 252 Spe\n"
        "Jolly Nature\n- Dragon Claw\n- Rock Slide\n- Earthquake\n- Protect\n\n"
        "Incineroar @ Sitrus Berry\nAbility: Intimidate\nEVs: 252 HP / 4 Atk / 252 SpD\n"
        "Careful Nature\n- Fake Out\n- Parting Shot\n- Flare Blitz\n- Throat Chop")

    def test_their_side_offers_a_paste_option(self):
        at = fresh_app()
        tab = sim_tab(at)
        src_radio = next(r for r in tab.radio if r.label == "Their side")
        self.assertIn("Paste a pokepaste", src_radio.options)

    def test_pasting_their_six_parses_and_can_start_a_battle(self):
        at = fresh_app()
        tab = sim_tab(at)
        our_radio = next(r for r in tab.radio if r.label == "Our side")
        at = our_radio.set_value("Any Pokemon").run()
        tab = sim_tab(at)
        lead_ms = next(m for m in tab.multiselect if m.label == "Our lead (2)")
        at = lead_ms.set_value(["Garchomp", "Incineroar"]).run()
        tab = sim_tab(at)
        back_ms = next(m for m in tab.multiselect if m.label == "Our back (2)")
        at = back_ms.set_value(["Gallade", "Hydreigon"]).run()
        tab = sim_tab(at)
        their_radio = next(r for r in tab.radio if r.label == "Their side")
        at = their_radio.set_value("Paste a pokepaste").run()
        tab = sim_tab(at)
        ta = next(t for t in tab.text_area
                 if t.key and t.key.endswith("_foe_paste"))
        at = ta.set_value(self.SIX_MON_PASTE).run()
        self.assertFalse(at.exception, list(at.exception))
        tab = sim_tab(at)
        self.assertTrue(any("Kingambit" in s.value for s in tab.success))
        mode_radio = next(r for r in tab.radio if r.label == "Their bring")
        at = mode_radio.set_value("I choose their bring").run()
        tab = sim_tab(at)
        their_lead = next(m for m in tab.multiselect if m.label == "Their lead (2)")
        at = their_lead.set_value(["Kingambit", "Basculegion"]).run()
        tab = sim_tab(at)
        their_back = next(m for m in tab.multiselect if m.label == "Their back (2)")
        at = their_back.set_value(["Whimsicott", "Garchomp"]).run()
        tab = sim_tab(at)
        start = next(b for b in tab.button if b.label == "Start Battle")
        self.assertFalse(start.disabled)
        at = start.click().run()
        self.assertFalse(at.exception, list(at.exception))
        self.assertIsNotNone(at.session_state["sim_battle"])


class TestBattleSimulatorTurnLoop(unittest.TestCase):
    """Once a battle exists, the human's own action picker and Submit
    button drive a real `Battle.run_turn` -- no second engine."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_submitting_a_turn_advances_the_real_battle(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        self.assertEqual(len(at.exception), 0)
        battle0 = at.session_state["sim_battle"]
        self.assertEqual(battle0.turn_num, 0)

        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        self.assertFalse(submit.disabled)
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle1 = at.session_state["sim_battle"]
        self.assertEqual(battle1.turn_num, 1)
        self.assertTrue(at.session_state["sim_turn_log"])

    def test_action_options_are_real_legal_moves_not_ai_pruned(self):
        """`sim_legal_actions` (via `sim_grouped_actions`'s Target dropdown)
        must offer EVERY live-foe target for a single-target move, not
        just the AI heuristic's "best" one -- the whole point of playing
        manually. Both actives default to their FIRST move, whose targets
        (if it needs any) already show as a "Target" dropdown."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        target_names = {"Kingambit", "Basculegion"}
        target_sbs = [sb for sb in tab.selectbox if sb.label == "Target"]
        # Options now carry a "~NN%" damage estimate suffix, so match by prefix.
        found_both_targets = any(
            all(any(opt.startswith(n) for opt in sb.options) for n in target_names)
            for sb in target_sbs)
        self.assertTrue(found_both_targets, "at least one default move's "
                                            "target dropdown must offer both "
                                            "enemy leads, not just one")

    def test_turn_log_shows_percent_damage_alongside_raw_damage(self):
        """"When showing move damage in the log, show % damage as well" --
        the damage line now carries both, e.g. "92 dmg (46%) (1.0x eff)"."""
        import re
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        at = submit_turn(at)
        log_text = "\n".join(at.session_state["sim_turn_log"])
        m = re.search(r"(\d+) dmg \((\d+)%\) \([\d.]+x eff\)", log_text)
        self.assertIsNotNone(m, f"no percent-annotated damage line found in:\n{log_text}")


class TestFieldStatusShowsTerrainDuration(unittest.TestCase):
    """"display the remaining duration of field effects like psychic
    terrain and grassy terrain, along with the trick room/tailwind
    timers" -- Trick Room/Tailwind were already shown; terrain (fully
    tracked by `FieldState.terrain`/`terrain_turns_left` already) was the
    one field-status line never rendered."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_grassy_terrain_shows_its_pretty_name_and_remaining_turns(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        battle.field.terrain = "grassy"
        battle.field.terrain_turns_left = 4
        at.session_state["sim_battle"] = battle
        at = at.run()
        tab = sim_tab(at)
        self.assertTrue(any("Grassy Terrain (4 left)" in c.value
                            for c in tab.caption))

    def test_no_terrain_shows_no_terrain_bit(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        self.assertFalse(any("Terrain" in c.value for c in tab.caption))

    def test_weather_shows_its_pretty_name_not_the_raw_key(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        battle.field.weather = "rain"
        battle.field.weather_turns_left = 3
        at.session_state["sim_battle"] = battle
        at = at.run()
        tab = sim_tab(at)
        self.assertTrue(any("a rainstorm (3 left)" in c.value
                            for c in tab.caption))
        self.assertFalse(any("Weather: rain " in c.value for c in tab.caption))

    def test_switching_the_selected_move_changes_the_target_row(self):
        """The Move dropdown acts as a real menu -- picking a DIFFERENT
        move re-renders the target row for that move, not the first one's
        leftover targets."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        move_sbs = [sb for sb in tab.selectbox if sb.label == "Move"]
        self.assertTrue(move_sbs)
        move_sb = next((sb for sb in move_sbs if len(sb.options) > 1), None)
        self.assertIsNotNone(move_sb, "fixture assumes at least one slot's "
                                      "moveset offers more than one move")
        other = next(o for o in move_sb.options if o != move_sb.value)
        move_sb.set_value(other).run()
        self.assertEqual(len(at.exception), 0)

    def test_no_bench_left_does_not_wrongly_disable_submit(self):
        """Regression: a fainted active with an empty bench needs NO
        action at all (`Battle.run_turn` matches `solver.
        greedy_opponent_joint_action`'s own identical skip) -- requiring
        exactly one action per active slot froze the Submit button forever
        once this came up."""
        from combatants import make_team
        from battle import Battle
        from solver import build_moveset, TOP_K_MOVES
        W = world()
        our4 = ["Garchomp", "Incineroar"]  # bring-2: no bench at all
        their4 = ["Kingambit", "Basculegion"]
        oc = make_team(our4, W["merged"], W["natures"])
        ec = make_team(their4, W["merged"], W["natures"])
        battle = Battle(oc, ec, W["typechart"], W["moves"])
        oc[0].fainted = True
        oc[0].current_hp = 0
        movesets = {c.name: build_moveset(W["merged"][c.name], W["moves"], top_k=TOP_K_MOVES)
                   for c in oc + ec}

        at = fresh_app()
        at.session_state["sim_battle"] = battle
        at.session_state["sim_movesets"] = movesets
        at.session_state["sim_our4"] = our4
        at.session_state["sim_their4"] = their4
        at.session_state["sim_mode"] = "I choose their bring"
        at.session_state["sim_turn_log"] = []
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        # No bench anywhere in this bring-2 -- Incineroar's slot (the only
        # non-fainted one) has no "Switch" option at all, and Garchomp's
        # fainted slot needs no action (nothing left to replace it with).
        menu_sbs = [sb for sb in tab.selectbox if sb.label == "Action type"]
        self.assertTrue(menu_sbs)
        self.assertFalse(any("Switch" in sb.options for sb in menu_sbs))
        submit = next(b for b in tab.button if b.label == "Submit turn")
        self.assertFalse(submit.disabled, "a permanently-empty slot must not "
                                          "block the turn from being submitted")

    def test_battle_over_shows_a_verdict_and_a_reset(self):
        from combatants import make_team
        from battle import Battle
        from solver import build_moveset, TOP_K_MOVES
        W = world()
        our4 = ["Garchomp", "Incineroar"]
        their4 = ["Kingambit", "Basculegion"]
        oc = make_team(our4, W["merged"], W["natures"])
        ec = make_team(their4, W["merged"], W["natures"])
        battle = Battle(oc, ec, W["typechart"], W["moves"])
        for c in ec:  # the opponent has already lost
            c.fainted = True
            c.current_hp = 0
        movesets = {c.name: build_moveset(W["merged"][c.name], W["moves"], top_k=TOP_K_MOVES)
                   for c in oc + ec}

        at = fresh_app()
        at.session_state["sim_battle"] = battle
        at.session_state["sim_movesets"] = movesets
        at.session_state["sim_our4"] = our4
        at.session_state["sim_their4"] = their4
        at.session_state["sim_mode"] = "I choose their bring"
        at.session_state["sim_turn_log"] = []
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        self.assertTrue(any("YOU WIN" in s.value for s in tab.success))
        self.assertTrue(any(b.label == "New battle" for b in tab.button))
        self.assertFalse(any(b.label == "Submit turn" for b in tab.button))


class TestRechargeUI(unittest.TestCase):
    """"Hyper Beam requires a one turn cooldown after using." A recharging
    Pokemon gets no action menu at all in the real games -- the Battle
    Simulator must match that, not offer a move list `Battle.run_turn`
    would just force into a no-op anyway."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_a_recharging_slot_shows_no_action_menu(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        garchomp = battle.p1.active[0]
        garchomp.volatile["must_recharge"] = True
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        self.assertTrue(
            any("must recharge" in (m.value or "") for m in tab.markdown),
            "the recharging slot must be labelled, not shown a normal menu")
        # Both active slots normally get their own "Action type" dropdown (a
        # full bench sits behind each) -- with Garchomp forced to recharge,
        # only Incineroar's slot should still offer one.
        menu_sbs = [sb for sb in tab.selectbox if sb.label == "Action type"]
        self.assertEqual(len(menu_sbs), 1,
                         "the recharging slot must not offer an action-type menu")
        submit = next(b for b in tab.button if b.label == "Submit turn")
        self.assertFalse(submit.disabled)

    def test_submitting_through_a_recharge_turn_advances_the_battle(self):
        """Clicking Submit turn while a slot is recharging must still run a
        real turn -- `sim_legal_actions`'s forced no-op action for that
        slot, not a blocked or crashed submission."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        garchomp = battle.p1.active[0]
        garchomp.volatile["must_recharge"] = True
        at = at.run()

        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle_after = at.session_state["sim_battle"]
        self.assertEqual(battle_after.turn_num, 1)
        self.assertFalse(garchomp.volatile.get("must_recharge"),
                         "the recharge turn must actually be spent")


class TestStepThroughSummary(unittest.TestCase):
    """"When I finish stepping through a team in the Battle Simulator, I
    would like to see a summary of where I lose, to what, and KO/faint and
    damage ratio by my/their mons." Built from `Battle.stats` (already
    tracked per-battle by the engine) plus `Combatant.fainted`, accumulated
    across `sim_leads` -- not a second stats mechanism."""

    def _finished_battle(self, our4, their4, our_loses):
        from combatants import make_team
        from battle import Battle
        from solver import build_moveset, TOP_K_MOVES
        W = world()
        oc = make_team(our4, W["merged"], W["natures"])
        ec = make_team(their4, W["merged"], W["natures"])
        battle = Battle(oc, ec, W["typechart"], W["moves"])
        for c in (oc if our_loses else ec):
            c.fainted = True
            c.current_hp = 0
        movesets = {c.name: build_moveset(W["merged"][c.name], W["moves"], top_k=TOP_K_MOVES)
                   for c in oc + ec}
        return battle, movesets

    def test_summary_accumulates_and_shows_after_the_last_lead(self):
        our4 = ["Garchomp", "Incineroar"]
        their4_a = ["Kingambit", "Basculegion"]
        their4_b = ["Whimsicott", "Sinistcha"]
        battle1, movesets1 = self._finished_battle(our4, their4_a, our_loses=False)

        at = fresh_app()
        at.session_state["sim_battle"] = battle1
        at.session_state["sim_movesets"] = movesets1
        at.session_state["sim_our4"] = our4
        at.session_state["sim_their4"] = their4_a
        at.session_state["sim_mode"] = "Step through all 15 leads"
        at.session_state["sim_leads"] = [(0.0, their4_a, None), (0.0, their4_b, None)]
        at.session_state["sim_lead_idx"] = 0
        at.session_state["sim_turn_log"] = []
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        self.assertEqual(len(at.session_state["sim_leads_summary"]), 1,
                         "the first (won) lead must be recorded once it ends")
        self.assertTrue(any("Next lead" in b.label for b in tab.button))
        # No summary render yet -- there's still a lead left to step through.
        self.assertFalse(any("Step-through summary" in (m.value or "")
                             for m in tab.markdown))

        # Advance to the (losing) second and final lead.
        battle2, movesets2 = self._finished_battle(our4, their4_b, our_loses=True)
        at.session_state["sim_battle"] = battle2
        at.session_state["sim_movesets"] = movesets2
        at.session_state["sim_their4"] = their4_b
        at.session_state["sim_lead_idx"] = 1
        at.session_state["sim_turn_log"] = []
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        self.assertEqual(len(at.session_state["sim_leads_summary"]), 2,
                         "the second (lost) lead must be recorded too, not "
                         "just overwrite the first")
        headers = [m.value for m in tab.markdown
                  if "Step-through summary" in (m.value or "")]
        self.assertTrue(headers)
        self.assertIn("1/2 won", headers[0])
        self.assertTrue(any("Lost to" in (m.value or "") for m in tab.markdown))
        self.assertTrue(any("Whimsicott" in (m.value or "") for m in tab.markdown),
                        "the losing enemy pair's names must appear in the summary")

    def test_new_battle_clears_the_accumulated_summary(self):
        our4 = ["Garchomp", "Incineroar"]
        their4 = ["Kingambit", "Basculegion"]
        battle, movesets = self._finished_battle(our4, their4, our_loses=False)

        at = fresh_app()
        at.session_state["sim_battle"] = battle
        at.session_state["sim_movesets"] = movesets
        at.session_state["sim_our4"] = our4
        at.session_state["sim_their4"] = their4
        at.session_state["sim_mode"] = "Step through all 15 leads"
        at.session_state["sim_leads"] = [(0.0, their4, None)]
        at.session_state["sim_lead_idx"] = 0
        at.session_state["sim_turn_log"] = []
        at = at.run()
        self.assertIn("sim_leads_summary", at.session_state)

        tab = sim_tab(at)
        new_battle_btn = next(b for b in tab.button if b.label == "New battle")
        new_battle_btn.click().run()
        self.assertNotIn("sim_leads_summary", at.session_state)

    def test_replay_button_on_a_lost_lead_restarts_against_it(self):
        """"After losing a match and after running the 15 pair step through
        in battle simulator, give me an option to replay the losing
        match(es)." Each loss row in the final step-through summary gets
        its own "Replay" button that rebuilds the battle against exactly
        that lead's `their4`."""
        our4 = ["Garchomp", "Incineroar"]
        their4 = ["Kingambit", "Basculegion"]
        battle, movesets = self._finished_battle(our4, their4, our_loses=True)

        at = fresh_app()
        at.session_state["sim_battle"] = battle
        at.session_state["sim_movesets"] = movesets
        at.session_state["sim_our4"] = our4
        at.session_state["sim_our_sets"] = {}
        at.session_state["sim_their4"] = their4
        at.session_state["sim_their_sets"] = {}
        at.session_state["sim_our_mega"] = None
        at.session_state["sim_mode"] = "Step through all 15 leads"
        at.session_state["sim_leads"] = [(0.0, their4, None)]
        at.session_state["sim_lead_idx"] = 0
        at.session_state["sim_turn_log"] = []
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        replay_buttons = [b for b in tab.button if b.label == "Replay"]
        self.assertEqual(len(replay_buttons), 1,
                         "exactly one loss row -- exactly one Replay button")
        replay_buttons[0].click().run()

        tab = sim_tab(at)
        self.assertEqual(len(tab.exception), 0)
        new_battle = at.session_state["sim_battle"]
        self.assertEqual(sorted(c.name for c in new_battle.p2.roster), sorted(their4))
        self.assertFalse(new_battle.p1.has_lost(), "a freshly rebuilt battle "
                                                    "must start un-fainted")
        self.assertEqual(at.session_state["sim_their4"], their4)
        self.assertEqual(at.session_state["sim_mode"], "I choose their bring")
        self.assertEqual(at.session_state["sim_turn_log"], [])


class TestBattleSimulatorOnlyOneMegaInLiveBattle(unittest.TestCase):
    """The actual Combatants built for the interactive battle honour "only
    one Mega per side", the same rule this session's counter_finder.py fix
    applies to the cheap pool searches -- here it's the real engine's own
    `combatants.make_team`/`species_data.resolve_team_mega_slot`, reused
    rather than re-derived."""

    def test_forced_base_pick_keeps_its_own_base_ability(self):
        at = fresh_app()
        at = seed_battle(at, ["Mega Gyarados", "Mega Charizard Y", "Garchomp", "Kingambit"],
                         ["Basculegion", "Whimsicott", "Sinistcha", "Incineroar"],
                         our_mega="Mega Gyarados")
        battle = at.session_state["sim_battle"]
        gyarados = next(c for c in battle.p1.roster if c.name == "Mega Gyarados")
        charizard = next(c for c in battle.p1.roster if c.name == "Mega Charizard Y")
        self.assertTrue(gyarados.is_mega_pick)
        self.assertFalse(charizard.is_mega_pick, "the OTHER Mega-named pick "
                                                 "must be forced to base form")
        # Charizard's forced-base ability is its own base ability (Blaze or
        # Solar Power), never Drought (the Mega-exclusive ability) and
        # never Intimidate (Gyarados's, not its own).
        self.assertIn(charizard.ability, ("Blaze", "Solar Power"))


class TestPerTurnMegaChoiceAndReplacementUI(unittest.TestCase):
    """"I need to be able to choose during the battle which of my brings
    mega evolves, not before, and choose at the start of the turn on which
    I wish to mega evolve. I also need to be able to choose who I send in
    after a faint." """

    OUR4 = ["Mega Gyarados", "Kingambit", "Garchomp", "Basculegion"]
    THEIR4 = ["Whimsicott", "Sinistcha", "Incineroar", "Hydreigon"]

    def test_mega_capable_active_gets_a_per_turn_checkbox(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next((c for c in tab.checkbox if "Mega Gyarados" in c.label), None)
        self.assertIsNotNone(cb)
        self.assertFalse(cb.value, "must default to NOT transforming -- a "
                                   "real choice, not a pre-commitment")

    def test_leaving_it_unchecked_keeps_the_pick_in_base_form(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        gyarados = next(c for c in battle.p1.roster if c.name == "Mega Gyarados")
        self.assertFalse(gyarados.mega_evolved)

    def test_checking_it_transforms_on_that_turn(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next(c for c in tab.checkbox if "Mega Gyarados" in c.label)
        cb.set_value(True).run()
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        gyarados = next(c for c in battle.p1.roster if c.name == "Mega Gyarados")
        self.assertTrue(gyarados.mega_evolved)

    def test_opponent_still_mega_evolves_automatically(self):
        """Our side's explicit choice must not silently gate theirs -- they
        have no turn-by-turn UI of their own, so they keep the engine's
        default "transforms the instant it's eligible" behaviour. Mega
        Charizard Y hits hard enough to faint one of ours turn 1, which now
        pauses for a replacement choice -- `submit_turn` auto-confirms it,
        since that pause isn't what this test is about."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, ["Mega Charizard Y"] + self.THEIR4[1:])
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        charizard = next(c for c in battle.p2.roster if c.name == "Mega Charizard Y")
        self.assertTrue(charizard.mega_evolved)

    def test_no_upfront_replacement_picker_and_no_pause_when_nobody_faints(self):
        """"For the 'if faints this turn', make it a choice when the faint
        happens, not a preselection." -- no "X faints this turn" dropdown
        should ever be pre-rendered, and a turn where nobody on our side
        faints resolves immediately, with no pending choice left behind."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        self.assertFalse(any("faints this turn" in (sb.label or "")
                             for sb in tab.selectbox))

        submit = next(b for b in tab.button if b.label == "Submit turn")
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertNotIn("sim_pending_turn", at.session_state)
        self.assertEqual(at.session_state["sim_battle"].turn_num, 1)

    def test_a_faint_pauses_for_a_real_choice_instead_of_a_preselection(self):
        """A Pokemon that actually faints this turn must surface a real,
        post-faint choice -- not resolve immediately on some earlier guess.
        1 HP so any live opposing attack KOes it. `THEIR4` here (not the
        class default, which opens with Tailwind/Trick Room and deals no
        damage at all turn 1) is picked to reliably attack rather than set
        up."""
        at = fresh_app()
        their4 = ["Basculegion", "Whimsicott", "Sinistcha", "Incineroar"]
        at = seed_battle(at, self.OUR4, their4)
        battle = at.session_state["sim_battle"]
        battle.p1.active[0].current_hp = 1  # Mega Gyarados, still alive
        at = at.run()

        submit = next(b for b in sim_tab(at).button if b.label == "Submit turn")
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        # The turn must NOT have resolved yet -- a faint needs a real
        # choice first, not whatever was declared before the turn ran.
        self.assertIn("sim_pending_turn", at.session_state)
        self.assertEqual(at.session_state["sim_battle"].turn_num, 0)

        tab = sim_tab(at)
        rep_sb = next(sb for sb in tab.selectbox
                     if sb.label == "Mega Gyarados fainted -- send in:")
        self.assertEqual(rep_sb.options[0], "Auto-pick (recommended)")
        self.assertEqual(set(rep_sb.options[1:]), {"Garchomp", "Basculegion"})
        rep_sb.set_value("Basculegion").run()

        tab = sim_tab(at)
        confirm = next(b for b in tab.button if b.label == "Confirm and resolve turn")
        confirm.click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertNotIn("sim_pending_turn", at.session_state)
        battle = at.session_state["sim_battle"]
        self.assertEqual(battle.turn_num, 1)
        gyarados = next(c for c in battle.p1.roster if c.name == "Mega Gyarados")
        self.assertTrue(gyarados.fainted, "fixture assumes 1 HP dies to "
                                          "any live opposing attack")
        self.assertEqual(battle.p1.active[0].name, "Basculegion")


class TestBattleMenu(unittest.TestCase):
    """"It would also be good to have a better UI, ... buttons like
    battle->4 moves/switch->select pokemon." Sprites were dropped later --
    "remove links to showdown etc, I don't want to be calling external
    websites" -- `st.image` would fetch them straight from the viewer's
    browser, an external call this app must never make."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_board_shows_no_external_images(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        self.assertEqual(len(tab.image), 0,
                         "no st.image (or any other external fetch) belongs "
                         "in the Battle Simulator board")

    def test_menu_defaults_to_attack_with_a_move_dropdown(self):
        """"Switch back to the dropdown or something fast for the streamlit
        Battle Simulator, the buttons are way too slow." -- Action type,
        Move and Target are all plain `st.selectbox` dropdowns, one rerun
        per pick instead of a whole grid of buttons."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        menu_sbs = [sb for sb in tab.selectbox if sb.label == "Action type"]
        self.assertEqual(len(menu_sbs), 2)
        for sb in menu_sbs:
            self.assertEqual(set(sb.options), {"Attack", "Switch"})
            self.assertEqual(sb.value, "Attack", "must default to Attack")
        move_sbs = [sb for sb in tab.selectbox if sb.label == "Move"]
        self.assertEqual(len(move_sbs), 2)
        first_moveset = list(seed_movesets(self.OUR4[0]))
        move_names = {mv.name for mv, _pct in first_moveset}
        rendered_options = {opt for sb in move_sbs for opt in sb.options}
        self.assertTrue(move_names & rendered_options, "the default Attack "
                                                        "menu must offer real "
                                                        "move names")

    def test_switching_to_switch_then_a_bench_mon_builds_a_switch_action(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        menu_sb = next(sb for sb in tab.selectbox if sb.label == "Action type")
        menu_sb.set_value("Switch").run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        switch_sb = next(sb for sb in tab.selectbox if sb.label == "Switch to")
        self.assertEqual(set(switch_sb.options), {"Gallade", "Hydreigon"})
        switch_sb.set_value("Hydreigon").run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        self.assertFalse(submit.disabled)
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        self.assertEqual(battle.p1.active[0].name, "Hydreigon")
        self.assertNotEqual(battle.p1.active[0].name, "Garchomp")


class TestMoveSelectionShowsDamagePercent(unittest.TestCase):
    """"When showing move damage in the log, show % damage as well. In
    move selection let me see the % damage the potential moves will do."
    The Move dropdown carries a "~NN%" suffix whenever a move only has one
    legal opt (the common case -- one live foe, or a self/status move with
    nothing to estimate), and the Target dropdown carries it per-target
    when a move offers a real choice."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_a_damaging_move_carries_a_percent_estimate(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        move_sbs = [sb for sb in tab.selectbox if sb.label in ("Move", "Target")]
        all_options = [opt for sb in move_sbs for opt in sb.options]
        self.assertTrue(any("(~" in opt and "%)" in opt for opt in all_options),
                        f"expected a ~NN%% estimate somewhere in: {all_options}")

    def test_a_pure_status_move_carries_no_percent_estimate(self):
        """Protect (always legal, always offered) must never get a damage
        estimate -- it can't damage anything."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        move_sbs = [sb for sb in tab.selectbox if sb.label == "Move"]
        protect_opts = [opt for sb in move_sbs for opt in sb.options
                        if opt.startswith("Protect")]
        self.assertTrue(protect_opts, "Protect must always be offered")
        for opt in protect_opts:
            self.assertEqual(opt, "Protect", "Protect must never carry a % estimate")


class TestSuggestedAction(unittest.TestCase):
    """"Also in the battle simulator, provide the suggested move or
    suggested switch after a faint" -- a "Suggested: ..." caption next to
    each of our own mon's action menu, scored by the exact same greedy
    one-ply valuation (`solver._action_value`) that plays the OPPONENT
    side, so it can never drift from what the AI itself would call best.
    Display-only -- the dropdowns below it stay exactly as manual as
    before."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_a_suggested_caption_appears_and_matches_the_scorer(self):
        from app import sim_suggest_action
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        suggestion_captions = [c.value for c in tab.caption
                               if c.value and c.value.startswith("Suggested: ")]
        self.assertEqual(len(suggestion_captions), 2,
                         "one suggestion per one of our own two active mons")

        battle = at.session_state["sim_battle"]
        movesets = at.session_state["sim_movesets"]
        expected = [f"Suggested: {sim_suggest_action(battle, c, battle.p1, battle.p2, movesets, battle.turn_num + 1)}"
                   for c in battle.p1.active]
        self.assertEqual(suggestion_captions, expected)

    def test_a_suggested_replacement_caption_appears_after_a_faint(self):
        """The fainted-replacement dropdown gets its own "Suggested: Switch
        in ..." caption, off `Battle._best_replacement` -- the SAME
        strategic pick the opponent's own auto-replacement already uses,
        not a second heuristic."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        battle.p1.active[0].fainted = True
        battle.p1.active[0].current_hp = 0
        at = at.run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        expected = battle._best_replacement(
            [b for b in battle.p1.bench if not b.fainted], battle.p2.active)
        self.assertTrue(any(c.value == f"Suggested: Switch in {expected.name}"
                            for c in tab.caption))


class TestLiveWinConditionsPanel(unittest.TestCase):
    """"A live tracker in a battle simulator match (while holding the
    enemy backs as unconfirmed until revealed) -- for instance, I can
    afford to risk Metagross this turn and attack if I trade it for the
    enemy Staraptor, because my Scizor beats the rest." Per the user's own
    "live recompute, full knowledge" choice: no fog-of-war/reveal-tracking
    state, just a live 1v1 hit-count matrix scoped to whichever mons are
    CURRENTLY ALIVE, off each one's REAL current HP."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_panel_appears_with_one_matrix_cell_per_alive_pair(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        self.assertTrue(any("Win conditions (live" in e.label for e in tab.expander))
        matrix_dfs = [d.value for d in tab.dataframe if list(d.value.columns[:1]) == ["Ours"]]
        self.assertTrue(matrix_dfs, "expected an Ours/<enemy...> live matrix table")
        self.assertEqual(set(matrix_dfs[0]["Ours"]), set(self.OUR4),
                         "the whole alive roster (bench included), not just actives")
        self.assertEqual(set(matrix_dfs[0].columns[1:]), set(self.THEIR4))

    def test_matrix_shrinks_once_a_mon_faints(self):
        """A fainted mon (on either side) drops out of the live matrix --
        "scoped to whichever mons are CURRENTLY ALIVE"."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        battle.p2.active[0].fainted = True
        battle.p2.active[0].current_hp = 0
        at = at.run()
        self.assertEqual(len(at.exception), 0)
        tab = sim_tab(at)
        matrix_dfs = [d.value for d in tab.dataframe if list(d.value.columns[:1]) == ["Ours"]]
        self.assertTrue(matrix_dfs)
        self.assertNotIn(battle.p2.active[0].name, matrix_dfs[0].columns)

    def test_sim_hit_count_matrix_reflects_real_current_hp_not_full(self):
        """Halving a target's current HP can only shrink (never grow) our
        own hits-to-KO on it -- the whole point of using REAL current HP
        instead of full team-preview HP."""
        from app import sim_hit_count_matrix
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        battle = at.session_state["sim_battle"]
        movesets = at.session_state["sim_movesets"]
        full = sim_hit_count_matrix(battle, movesets)
        target = battle.p2.active[0]
        our_attacker = battle.p1.active[0]
        target.current_hp = target.current_hp // 2
        halved = sim_hit_count_matrix(battle, movesets)
        cell_full = full[(our_attacker.name, target.name)]
        cell_half = halved[(our_attacker.name, target.name)]
        if cell_full["our_hits_to_ko"] is not None:
            self.assertLessEqual(cell_half["our_hits_to_ko"], cell_full["our_hits_to_ko"])


class TestForceRedirectMode(unittest.TestCase):
    """"Let me select a mode in the battle simulator where the enemy always
    uses its redirection moves" -- a checkbox that overrides `greedy_
    opponent_joint_action`'s own (already Follow-Me-aware, but merely
    HEURISTIC) per-mon choice, forcing every alive, redirect-capable enemy
    to click Follow Me/Rage Powder EVERY turn regardless of whether the
    greedy AI judges it worthwhile -- a deliberately pessimistic
    "can this specific play really be punished" testing mode."""

    OUR4 = ["Garchomp", "Hydreigon"]
    THEIR4 = ["Indeedee-F", "Kingambit"]  # Indeedee-F: real Follow Me user

    def test_checkbox_present_and_off_by_default(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next(c for c in tab.checkbox
                  if "always redirects" in c.label)
        self.assertFalse(cb.value)

    def test_enabling_it_makes_the_redirector_click_follow_me(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next(c for c in tab.checkbox if "always redirects" in c.label)
        at = cb.set_value(True).run()
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        indeedee = next(c for c in battle.p2.roster if c.name == "Indeedee-F")
        self.assertIs(battle.p2.follow_me_target, indeedee,
                      "Indeedee-F must have actually clicked Follow Me this turn "
                      "(regardless of whether it then took a KO for it)")

    def test_left_off_the_ai_keeps_its_own_normal_heuristic_choice(self):
        """Regression guard: the toggle must not change anything when OFF --
        `greedy_opponent_joint_action`'s own choice (whatever it is) goes
        through unmodified."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)


class TestManualEnemyMode(unittest.TestCase):
    """"Give me a mode where I can manually select the enemies moves too, to
    see if a specific play can really be punished." -- the SAME Attack/
    Switch dropdown menu built for our own side, rendered a second time for
    `battle.p2.active`, entirely replacing `greedy_opponent_joint_action`
    for the turn."""

    OUR4 = ["Garchomp", "Incineroar", "Gallade", "Hydreigon"]
    THEIR4 = ["Kingambit", "Basculegion", "Whimsicott", "Sinistcha"]

    def test_checkbox_present_and_off_by_default(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next(c for c in tab.checkbox if "pick the enemy" in c.label)
        self.assertFalse(cb.value)

    def test_enabling_it_renders_a_move_menu_for_the_enemy_side(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next(c for c in tab.checkbox if "pick the enemy" in c.label)
        at = cb.set_value(True).run()
        tab = sim_tab(at)
        menu_sbs = [sb for sb in tab.selectbox if sb.label == "Action type"]
        # 2 for our own side + 2 for the enemy side, now that manual mode is on.
        self.assertEqual(len(menu_sbs), 4)

    def test_a_human_chosen_enemy_move_actually_gets_submitted(self):
        """Pick a specific enemy move that the greedy AI would not
        necessarily choose on its own (a non-damaging status move), submit,
        and confirm it's the one that actually landed -- proves the manual
        pick reaches `Battle.run_turn`, not just the AI's own default."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        cb = next(c for c in tab.checkbox if "pick the enemy" in c.label)
        at = cb.set_value(True).run()

        tab = sim_tab(at)
        enemy_move_sbs = [sb for sb in tab.selectbox if sb.label == "Move"
                          and sb.key and sb.key.startswith("sim_enemy_move_")]
        # "Protect" over any damaging option -- the greedy AI's own
        # `action_value` scores Protect at -1 (see `solver.py`'s own
        # comment: "opponent modeled as not bothering to Protect"), so this
        # is a move the AI would never pick on its own, making it a clean
        # signal that the HUMAN pick, not the AI's, actually landed.
        protect_sb = next(sb for sb in enemy_move_sbs if "Protect" in sb.options)
        target_move = "Protect"
        at = protect_sb.set_value(target_move).run()
        self.assertEqual(len(at.exception), 0)

        at = submit_turn(at)
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        log = "\n".join(battle.log.lines)
        self.assertIn("protects itself", log)


def seed_movesets(name):
    """The real usage moveset for `name`, for a fixture to check button
    labels against without hand-listing moves that could drift."""
    from solver import build_moveset, TOP_K_MOVES
    W = world()
    return build_moveset(W["merged"][name], W["moves"], top_k=TOP_K_MOVES)


class TestResetBattle(unittest.TestCase):
    """"an option to just reset a battle in the battle simulator and go
    back to the selection screen" -- a button on the in-progress view that
    clears every `sim_*` piece of battle state, so the SAME `if st.session_
    state.get("sim_battle") is None:` check that shows the setup widgets on
    a fresh load shows them again."""

    def test_reset_button_is_present_once_a_battle_is_running(self):
        at = fresh_app()
        at = seed_battle(at, ["Garchomp", "Hydreigon"], ["Kingambit", "Whimsicott"])
        tab = sim_tab(at)
        labels = {b.label for b in tab.button}
        self.assertIn("🔄 Reset battle (back to team selection)", labels)

    def test_clicking_it_returns_to_the_setup_screen(self):
        at = fresh_app()
        at = seed_battle(at, ["Garchomp", "Hydreigon"], ["Kingambit", "Whimsicott"])
        self.assertIn("sim_battle", at.session_state)
        tab = sim_tab(at)
        reset = next(b for b in tab.button
                    if b.label == "🔄 Reset battle (back to team selection)")
        at = reset.click().run()
        self.assertEqual(len(at.exception), 0)
        self.assertNotIn("sim_battle", at.session_state)
        tab = sim_tab(at)
        labels = {r.label for r in tab.radio}
        self.assertIn("Their bring", labels, "setup widgets are showing again")

    def test_clicking_it_clears_every_battle_state_key(self):
        """Not just `sim_battle` -- a stale `sim_pending_turn`/`sim_leads`/
        etc. left behind would corrupt the NEXT battle started from the
        setup screen."""
        at = fresh_app()
        at = seed_battle(at, ["Garchomp", "Hydreigon"], ["Kingambit", "Whimsicott"])
        at.session_state["sim_pending_turn"] = {"fake": "state"}
        at.session_state["sim_leads"] = [("margin", ["a"], ["b"])]
        at.session_state["sim_lead_idx"] = 3
        at = at.run()
        tab = sim_tab(at)
        reset = next(b for b in tab.button
                    if b.label == "🔄 Reset battle (back to team selection)")
        at = reset.click().run()
        for key in ("sim_battle", "sim_movesets", "sim_our4", "sim_our_sets",
                   "sim_our_mega", "sim_their4", "sim_mode", "sim_turn_log",
                   "sim_leads", "sim_lead_idx", "sim_pending_turn"):
            self.assertNotIn(key, at.session_state, key)

    def test_a_fresh_battle_can_be_started_after_a_reset(self):
        """The reset must not just clear state but leave the setup screen
        actually usable -- not stuck disabled on stale widget defaults."""
        at = fresh_app()
        at = seed_battle(at, ["Garchomp", "Hydreigon"], ["Kingambit", "Whimsicott"])
        tab = sim_tab(at)
        reset = next(b for b in tab.button
                    if b.label == "🔄 Reset battle (back to team selection)")
        at = reset.click().run()
        at = seed_battle(at, ["Whimsicott", "Sinistcha"], ["Milotic", "Kingambit"])
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        self.assertEqual({c.name for c in battle.p1.active}, {"Whimsicott", "Sinistcha"})


if __name__ == "__main__":
    unittest.main()
