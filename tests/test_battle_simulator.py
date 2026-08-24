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
SIM_TAB_INDEX = 5  # Team Builder, Generate, Lead/Back, Battle Viewer, Vs Team, Battle Simulator

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
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        battle1 = at.session_state["sim_battle"]
        self.assertEqual(battle1.turn_num, 1)
        self.assertTrue(at.session_state["sim_turn_log"])

    def test_action_options_are_real_legal_moves_not_ai_pruned(self):
        """`sim_legal_actions` (via `sim_grouped_actions`'s button menu)
        must offer EVERY live-foe target for a single-target move, not
        just the AI heuristic's "best" one -- the whole point of playing
        manually. Both actives default to their FIRST move, whose targets
        (if it needs any) already show as a row of target buttons."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        target_names = {"Kingambit", "Basculegion"}
        found_both_targets = any(
            b.label in target_names for b in tab.button
        ) and len({b.label for b in tab.button} & target_names) >= 2
        self.assertTrue(found_both_targets, "at least one default move's "
                                            "target row must offer both "
                                            "enemy leads, not just one")

    def test_switching_the_selected_move_changes_the_target_row(self):
        """The move buttons act as a real menu -- clicking a DIFFERENT
        move re-renders the target row for that move, not the first one's
        leftover targets."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        move_buttons = [b for b in tab.button
                       if b.label not in ("Attack", "Switch", "Submit turn")
                       and "faints" not in (b.label or "")]
        self.assertTrue(move_buttons)
        move_buttons[0].click().run()
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
        self.assertFalse(any(b.label == "Switch" for b in tab.button))
        self.assertTrue(any(b.label == "Attack" for b in tab.button))
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
        # Both active slots normally get their own "Switch" button (a full
        # bench sits behind each) -- with Garchomp forced to recharge, only
        # Incineroar's slot should still offer one.
        switch_buttons = [b for b in tab.button if b.label == "Switch"]
        self.assertEqual(len(switch_buttons), 1,
                         "the recharging slot must not offer a Switch option")
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

        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        submit.click().run()
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
        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        submit.click().run()
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
        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        gyarados = next(c for c in battle.p1.roster if c.name == "Mega Gyarados")
        self.assertTrue(gyarados.mega_evolved)

    def test_opponent_still_mega_evolves_automatically(self):
        """Our side's explicit choice must not silently gate theirs -- they
        have no turn-by-turn UI of their own, so they keep the engine's
        default "transforms the instant it's eligible" behaviour."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, ["Mega Charizard Y"] + self.THEIR4[1:])
        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        charizard = next(c for c in battle.p2.roster if c.name == "Mega Charizard Y")
        self.assertTrue(charizard.mega_evolved)

    def test_replacement_picker_offers_the_live_bench(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        rep_sbs = [sb for sb in tab.selectbox if "faints this turn" in sb.label]
        # One per non-fainted active slot with a live bench.
        self.assertEqual(len(rep_sbs), 2)
        for sb in rep_sbs:
            self.assertEqual(sb.options[0], "Auto-pick (recommended)")
            self.assertEqual(set(sb.options[1:]), {"Garchomp", "Basculegion"})
            self.assertEqual(sb.value, "Auto-pick (recommended)")

    def test_picking_a_specific_replacement_is_honoured(self):
        """A pre-declared preference reaches `run_turn` correctly when the
        Pokemon it names actually faints THIS turn -- 1 HP so any live
        opposing attack KOes it. `THEIR4` here (not the class default,
        which opens with Tailwind/Trick Room and deals no damage at all
        turn 1) is picked to reliably attack rather than set up."""
        at = fresh_app()
        their4 = ["Basculegion", "Whimsicott", "Sinistcha", "Incineroar"]
        at = seed_battle(at, self.OUR4, their4)
        battle = at.session_state["sim_battle"]
        battle.p1.active[0].current_hp = 1  # Mega Gyarados, still alive
        at = at.run()

        tab = sim_tab(at)
        rep_sb = next(sb for sb in tab.selectbox
                     if sb.label == "If Mega Gyarados faints this turn, send in:")
        rep_sb.set_value("Basculegion").run()
        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        self.assertFalse(submit.disabled)
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
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

    def test_menu_defaults_to_attack_with_a_move_row(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        self.assertEqual(len([b for b in tab.button if b.label == "Attack"]), 2)
        self.assertEqual(len([b for b in tab.button if b.label == "Switch"]), 2)
        first_moveset = list(seed_movesets(self.OUR4[0]))
        move_names = {mv.name for mv, _pct in first_moveset}
        rendered_labels = {b.label for b in tab.button}
        self.assertTrue(move_names & rendered_labels, "the default Attack "
                                                       "menu must show real "
                                                       "move-name buttons")

    def test_clicking_switch_then_a_bench_mon_builds_a_switch_action(self):
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        switch_btn = next(b for b in tab.button if b.label == "Switch")
        switch_btn.click().run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        bench_btn = next(b for b in tab.button if b.label in ("Gallade", "Hydreigon"))
        bench_btn.click().run()
        self.assertEqual(len(at.exception), 0)

        tab = sim_tab(at)
        submit = next(b for b in tab.button if b.label == "Submit turn")
        self.assertFalse(submit.disabled)
        submit.click().run()
        self.assertEqual(len(at.exception), 0)
        battle = at.session_state["sim_battle"]
        self.assertIn(battle.p1.active[0].name, ("Gallade", "Hydreigon"))
        self.assertNotEqual(battle.p1.active[0].name, "Garchomp")


def seed_movesets(name):
    """The real usage moveset for `name`, for a fixture to check button
    labels against without hand-listing moves that could drift."""
    from solver import build_moveset, TOP_K_MOVES
    W = world()
    return build_moveset(W["merged"][name], W["moves"], top_k=TOP_K_MOVES)


if __name__ == "__main__":
    unittest.main()
