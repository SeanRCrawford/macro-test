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
        """`sim_legal_actions` must offer EVERY live-foe target for a
        single-target move, not just the AI heuristic's "best" one --
        the whole point of playing manually."""
        at = fresh_app()
        at = seed_battle(at, self.OUR4, self.THEIR4)
        tab = sim_tab(at)
        action_sbs = [sb for sb in tab.selectbox if sb.label == "Action"]
        self.assertEqual(len(action_sbs), 2)
        # At least one slot must offer BOTH enemy leads as targets for some
        # single-target move (not pruned to a single "best" one).
        found_both_targets = False
        for sb in action_sbs:
            targets = {opt.split(" -> ")[1] for opt in sb.options if " -> " in opt}
            if len(targets) >= 2:
                found_both_targets = True
        self.assertTrue(found_both_targets)

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
        # Only ONE action selectbox should render -- the fainted, bench-less
        # slot needs none.
        action_sbs = [sb for sb in tab.selectbox if sb.label == "Action"]
        self.assertEqual(len(action_sbs), 1)
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


if __name__ == "__main__":
    unittest.main()
