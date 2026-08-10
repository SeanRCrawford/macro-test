"""
Streamlit UI -- interactive front-end for the whole toolkit.

Launch with:  ./run.sh app        (or: streamlit run src/app.py)

Four tabs:
  Team Builder  -- pick your 6 by hand (or load a generated team.json),
                    optimise items/movesets, save the sheet
  Generate      -- run the full team generation search with live controls
  Lead / Back   -- run the lead/back search for a chosen team and opponent
  Battle Viewer -- step through a single matchup turn by turn
"""
import sys
import itertools
import json
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC))
ROOT = SRC.parent

import streamlit as st
import pandas as pd

import species_data
from species_data import build_merged_dataset, load_preferences, find_mega_stone
from matchup_search import enemy_configs, play_out_worst_case
from matchup_search import (search_robust_composition, search_best_composition,
                             play_out_worst_case, enemy_configs)

st.set_page_config(page_title="VGC Champions M-B Solver", layout="wide")


def _data_fingerprint():
    """Modification times of the source data files. Used as a cache key so that
    editing mbsmogon.xlsx (EV spreads, items, moves) is picked up on the next
    rerun -- previously the parsed sheet was cached for the life of the process,
    so hand-edited EVs appeared to have no effect at all."""
    files = ["mbsmogon.xlsx", "roster.csv", "teams.csv", "preferences.csv"]
    out = []
    for f in files:
        p = species_data.DATA_DIR / f
        out.append((f, p.stat().st_mtime if p.exists() else 0))
    return tuple(out)


@st.cache_resource
def load_all(fingerprint):
    # Combatants are memoised by name inside combatants.py; that cache must be
    # dropped too or freshly-parsed EVs would still build stale Pokemon.
    import combatants
    combatants._TEMPLATE_CACHE.clear()
    merged, unresolved, moves, natures, typechart = build_merged_dataset()
    dups = getattr(build_merged_dataset, "last_duplicates", {}) or {}
    return merged, unresolved, moves, natures, typechart, dups


@st.cache_data
def load_teams_csv(fingerprint=None, _merged=None):
    from species_data import load_teams as _lt
    t, meta = _lt(with_meta=True, merged=_merged)
    return t, meta


merged, unresolved, moves, natures, typechart, dups = load_all(_data_fingerprint())
teams, team_meta = load_teams_csv(_data_fingerprint(), _merged=merged)
prefs = load_preferences()
all_names = sorted(merged.keys())


@st.cache_data
def load_generation_map(fingerprint=None):
    from species_data import build_generation_map, load_showdown_static
    pokedex, _, _, _ = load_showdown_static()
    return build_generation_map(merged.keys(), pokedex)


generation_map = load_generation_map(_data_fingerprint())


def _build_version_string():
    """Git commit + commit time, so it's obvious in the deployed app whether
    you're looking at the latest code -- Streamlit Cloud (and any host) can
    silently keep serving a stale build if it hasn't redeployed yet."""
    import subprocess, os
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True,
            stderr=subprocess.DEVNULL).strip()
        env = {**os.environ, "TZ": "UTC"}
        when = subprocess.check_output(
            ["git", "log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M UTC"],
            cwd=ROOT, text=True, stderr=subprocess.DEVNULL, env=env).strip()
        return f"`{sha}` — {when}"
    except Exception:
        return "unknown (not a git checkout)"


hdr1, hdr2 = st.columns([4, 1])
hdr1.title("VGC Champions M-B Solver")
hdr1.caption(f"Build: {_build_version_string()}")
if hdr2.button("Reload data", help="Re-read mbsmogon.xlsx / roster.csv / teams.csv from disk. "
                                     "Data is normally reloaded automatically when a file changes."):
    st.cache_resource.clear(); st.cache_data.clear(); st.rerun()
if dups:
    st.caption(f"Note: {', '.join(dups)} appear on multiple rows in mbsmogon.xlsx; "
               f"the Mega-Stone row was used for the Mega and the other filed as its base form.")

tab_build, tab_gen, tab_search, tab_battle, tab_vs = st.tabs(
    ["Team Builder", "Generate Team", "Lead / Back Search", "Battle Viewer", "Vs Team"])


# ------------------------------------------------------------------ helpers
def solver_controls(key_prefix: str):
    """Solver picker shared by the battle pages. Returns (nash, depth).

    Measured, so the cost/benefit can be stated rather than guessed at:
    the equilibrium solver beats the greedy one 60% of games (95% CI
    [55%, 65%], n=384) and costs about 19x more per decision.
    """
    nash = st.checkbox(
        "Use the Nash equilibrium solver (slower, much stronger)",
        value=False, key=f"{key_prefix}_engine",
        help="Off = greedy expectimax: fast, but it plays the best response to a "
             "fixed opponent model this codebase wrote itself. That overstates "
             "its own position by ~1 Pokemon per turn, and it cannot express a "
             "mixed strategy -- the correct answer on 83% of turns.\n\n"
             "On = solve the turn as a simultaneous-move matrix game. Wins 60% "
             "head-to-head (n=384) and, more importantly, picks plays that are "
             "far harder for a good opponent to punish. Costs roughly 19x per "
             "decision, so it is deliberately opt-in rather than the default.")
    depth = 1
    if nash:
        depth = st.select_slider(
            "Search depth", options=[1, 2], value=1, key=f"{key_prefix}_depth",
            help="1 = this turn only (~0.26 s per decision). "
                 "2 = this turn plus the state at the end of next turn "
                 "(~5.5 s per decision, ~21x). Depth 2 is what sees two-turn "
                 "punishes -- attack into a switch, then into a Protect -- and "
                 "field-effect clocks like stalling Trick Room. Expect a long "
                 "wait on a full simulation.")
        if depth == 2:
            st.caption("Depth 2 is roughly 150 s for a 14-turn simulation. "
                       "Best used on a short turn cap, or on the turn analysis below.")
    return nash, depth


def team_sheet_df(team, sets=None):
    sets = sets or {}
    rows = []
    for n in team:
        p = merged[n]
        spec = sets.get(n) or {}
        item = spec.get("item") or (p["items_usage"][0][0] if p["items_usage"] else "-")
        mvs = spec.get("moves") or [m for m, _ in p["moves_usage"][:4]]
        # The OVERRIDE if there is one, exactly as item and moves are handled.
        # Reading the dataset spread unconditionally is what made an applied
        # stat-point edit invisible here: the simulations used the edited
        # spread, the table above them showed the dataset's.
        ev = spec.get("evs") or p["evs"]
        edited = bool(spec.get("evs")) and dict(spec["evs"]) != dict(p["evs"])
        from combatants import _default_ability
        rows.append({
            "Pokemon": n, "Types": "/".join(p["types"]), "Item": item,
            "Ability": _default_ability(p["abilities_usage"]) if p["abilities_usage"] else "-",
            "Nature": p["nature"],
            "EVs": "/".join(str(ev.get(k, 0))
                            for k in ["hp", "atk", "def", "spa", "spd", "spe"])
                   + (" (edited)" if edited else ""),
            "Moves": ", ".join(mvs), "Score": round(p["score"], 1) if p["score"] else None,
        })
    return pd.DataFrame(rows)


def weakness_table(team):
    from species_data import TYPES
    rows = []
    for t in TYPES:
        weak = [n for n in team if merged[n].get("defensive_chart")
                and merged[n]["defensive_chart"][t] > 1.0]
        resist = [n for n in team if merged[n].get("defensive_chart")
                  and merged[n]["defensive_chart"][t] < 1.0]
        rows.append({"Type": t, "Weak (n)": len(weak), "Weak": ", ".join(weak),
                     "Resist (n)": len(resist), "Over limit": "YES" if len(weak) > 2 else ""})
    return pd.DataFrame(rows)


# The house-rule stat-point budget. Dataset spreads total 32-66 points with 66
# by far the most common, so it is the fallback when a species has no spread.
STAT_POINT_BUDGET = 66


def our_side_pool(key_prefix, teams, all_names):
    """Where OUR six come from, offered the same way everywhere.

    Every view that puts our team against someone else's needs this, and each
    one used to answer it differently: the Battle Viewer silently fell back to
    all 271 species when the Team Builder was empty, which is unusable, and the
    preview panels only ever offered the loaded team. One control, three
    sources: the team in the Team Builder tab, any saved team from the library,
    or the whole dataset for a one-off.

    Returns the list of names to pick a bring from (possibly empty).
    """
    loaded = get_state_team()
    options = ["My loaded team", "A saved team", "Any Pokemon"]
    default = 0 if loaded else 1
    source = st.radio("Our side", options, index=default, horizontal=True,
                      key=f"{key_prefix}_side_source",
                      help="'My loaded team' is whatever the Team Builder tab "
                           "currently holds. 'A saved team' is anything in "
                           "data/teams. 'Any Pokemon' opens the whole dataset "
                           "for a one-off matchup.")
    if source == "My loaded team":
        if not loaded:
            st.warning("No team loaded — pick six in the Team Builder tab, "
                       "or choose another source above.")
        return list(loaded)
    if source == "A saved team":
        if not teams:
            st.warning("No saved teams in data/teams.")
            return []
        pick = st.selectbox("Saved team", list(teams), key=f"{key_prefix}_saved")
        return list(teams[pick])
    return list(all_names)


def their_side_pool(key_prefix, teams, all_names):
    """Where THEIR six come from. Mirrors our_side_pool.

    The Battle Viewer could only face a saved team, so a matchup against six
    Pokemon you had just seen -- the actual team-preview situation -- could not
    be set up there at all.
    """
    source = st.radio("Their side", ["A saved team", "Pick 6"], index=0,
                      horizontal=True, key=f"{key_prefix}_foe_source")
    if source == "A saved team":
        if not teams:
            st.warning("No saved teams in data/teams.")
            return []
        pick = st.selectbox("Opponent team", list(teams), key=f"{key_prefix}_foe_saved")
        return list(teams[pick])
    return st.multiselect("Their six", all_names, max_selections=6,
                          key=f"{key_prefix}_foe_manual")


def enemy_side_input(key_prefix, teams, team_meta, all_names, merged,
                     allow_paste=True, label="Their six"):
    """Where THEIR six come from, offered the SAME way everywhere in Vs Team.

    Every panel in that tab used to ask differently: team preview and deep dive
    made you type six (or four) names by hand, and only the bottom section
    could take a pokepaste -- so answering "what do I bring against Big 6" meant
    retyping Big 6 in each panel, from memory, with no way to be sure the three
    answers were about the same team.

    A preset also brings its RECORDED SETS and its fixed lead along with it,
    which hand-picking cannot: a scripted team typed out by name is simulated
    without the script's sets, and quietly answers a slightly different
    question.

    Returns (roster, sets, meta) where meta carries {"name", "lead"} for a
    preset and empty values otherwise.
    """
    options = ["A preset team", "Pick 6"] + (["Paste a pokepaste"] if allow_paste else [])
    source = st.radio("Their side", options, index=0, horizontal=True,
                      key=f"{key_prefix}_foe_source")

    if source == "A preset team":
        if not teams:
            st.warning("No teams in data/teams.csv.")
            return [], {}, {}
        pick = st.selectbox("Opponent team", list(teams),
                            key=f"{key_prefix}_foe_preset")
        info = team_meta.get(pick) or {}
        roster = list(teams[pick])
        st.caption(" / ".join(roster)
                   + (f"  ·  FIXED LEAD {' + '.join(info['lead'])}"
                      if info.get("lead") else ""))
        return roster, dict(info.get("sets") or {}), {"name": pick,
                                                      "lead": info.get("lead")}

    if source == "Pick 6":
        roster = st.multiselect(label, all_names, max_selections=6,
                                key=f"{key_prefix}_foe_manual")
        return list(roster), {}, {}

    paste = st.text_area(
        "Paste Showdown export text here (blank line between each Pokemon)",
        height=200, key=f"{key_prefix}_foe_paste")
    if not paste.strip():
        return [], {}, {}
    from species_data import custom_team_from_export
    roster, sets = custom_team_from_export(paste, merged)
    unknown = [n for n in roster if n not in merged]
    if unknown:
        st.error(f"Unrecognised species (check spelling against "
                 f"mbsmogon.xlsx): {unknown}")
        return [], {}, {}
    if len(roster) != 6:
        st.error(f"Parsed {len(roster)} Pokemon, need exactly 6.")
        return [], {}, {}
    st.success(f"Parsed: {', '.join(roster)}")
    with st.expander("Parsed sets (item/ability/nature/EVs/moves)"):
        st.json(sets)
    return list(roster), sets, {}


def enemy_lead_and_backs(key_prefix, roster, fixed_lead=None):
    """Their lead pair, and which back pairs to test behind it.

    THE POINT: at the moment you have to answer this you know their lead --
    you just watched them send it out -- and you do NOT know their back two.
    Making the caller name a bring-4 forces them to invent the half they
    cannot see, and then reports an answer that is only right if the guess was.

    Returns (lead, [back pairs]). "All 6" is the honest default: it is every
    back they could still be holding, and six lines is affordable for a single
    position.
    """
    import itertools
    # Sanitise before the widget renders. Streamlit requires the current
    # selection to be a subset of the options, so switching opponent while a
    # lead from the previous one is still selected CRASHES the multiselect
    # rather than quietly re-defaulting (the same trap _lead_back_picker
    # documents).
    state_key = f"{key_prefix}_lead"
    if state_key in st.session_state:
        kept = [n for n in st.session_state[state_key] if n in roster]
        if kept != list(st.session_state[state_key]):
            st.session_state[state_key] = kept
    default = [n for n in (fixed_lead or []) if n in roster][:2]
    lead = st.multiselect(
        "Their lead (exactly 2 -- what you can actually see)", roster,
        default=default, max_selections=2, key=state_key)
    if len(lead) != 2:
        return list(lead), []
    rest = [n for n in roster if n not in lead]
    all_backs = [list(p) for p in itertools.combinations(rest, 2)]
    labels = ["All %d possible backs" % len(all_backs)] + \
             [" + ".join(p) for p in all_backs]
    choice = st.selectbox(
        "Their back two", labels, key=f"{key_prefix}_back",
        help="You do not see their back at the moment you decide, so the "
             "default tests every one they could still be holding. Pick a "
             "specific pair only if you have actually scouted it.")
    if choice == labels[0]:
        return list(lead), all_backs
    return list(lead), [all_backs[labels.index(choice) - 1]]


def report_as_line(report):
    """A deep_dive LineReport in the dict shape render_line_result reads.

    The audited-line renderer was written against the JSON the overnight cache
    stores. A live report holds the same information as objects, so converting
    is what lets one renderer serve both -- rather than a second, subtly
    different battle log for the live path.
    """
    from robustness import describe_action
    return {
        "outcome": report.outcome,
        "line_turns": report.length,
        "turns": [{
            "turn": t.turn,
            "our_play": describe_action(t.our_action) if t.our_action else None,
            "punished_by": (describe_action(t.punisher)
                            if (t.punisher and t.has_punish) else None),
            "events": list(t.events),
            "kos": list(t.kos),
            "hp_after": [f"{n} {hp}/{mx}" for _s, n, hp, mx in t.hp_after
                         if hp > 0],
        } for t in report.turns],
    }


def advanced_model_controls(key_prefix, default="standard"):
    """The one place the advanced model's strength is chosen.

    Returns (effort_name, audit_all). Used by every panel that runs the new
    engine live, so the answer to "where is the advanced model" is the same
    control everywhere rather than a different widget per tab.
    """
    from search_effort import TIER_ORDER, relative_cost, tier as _t
    effort = st.select_slider(
        "Advanced model strength", options=TIER_ORDER, value=default,
        format_func=lambda k: _t(k)["label"], key=f"{key_prefix}_adv_effort",
        help="How hard the equilibrium engine works.\n\n"
             "QUICK - no punish analysis at all: it screens brings and counts "
             "wins against the standard opponent model. Seconds.\n\n"
             "STANDARD - audits the 3 best brings against their 2 likeliest "
             "leads, playing each line out against an opponent choosing "
             "optimally, and measures how much a good player gains per turn.\n\n"
             "THOROUGH - 6 brings, 4 of their leads, longer lines. The setting "
             "to trust before committing to a team.\n\n"
             "EXHAUSTIVE - 8 brings against ALL 90 of their bring-4s, leads "
             "and backs. This is the total-pathing answer and it is slow.")
    audit_all = st.checkbox(
        "Test against ALL 90 of their brings (leads and backs)",
        value=_t(effort).get("all_configs", False), key=f"{key_prefix}_adv_all",
        help="Off, only their most plausible LEADS are tested and their backs "
             "are assumed -- so a plan that beats their lead and loses to "
             "their back still counts as a win. On, the record is a true "
             "X of 90. Costs about 90/leads times as much: this is the "
             "difference between a fast read and a number you can commit to.")
    st.caption(f"{_t(effort)['label']}"
               f"{' + all 90 brings' if audit_all else ''} "
               f"— roughly {relative_cost(effort) * (22 if audit_all and not _t(effort).get('all_configs') else 1):.0f}x "
               f"the quick setting.")
    return effort, audit_all


def render_line_result(lead, key):
    """A whole audited line as one battle, the way the Lead/Back tab shows one.

    render_audited_turn gives the analysis per turn -- punish, their answer,
    the tie count. This gives the other half a player wants: the result, and
    the battle log end to end in one block you can read or copy, assembled from
    the per-turn engine logs the audit already recorded.
    """
    outcome = (lead.get("outcome") or "?").lower()
    turns = lead.get("turns") or []
    label = {"win": "WIN", "loss": "LOSS",
             "draw": "DRAW"}.get(outcome, "UNRESOLVED")
    c1, c2, c3 = st.columns(3)
    c1.metric("Result", label)
    c2.metric("Turns", lead.get("line_turns") or len(turns))
    kos = [k for t in turns for k in (t.get("kos") or [])]
    c3.metric("Knocked out", len(kos))
    if outcome != "win":
        st.warning("This line does not win against an opponent playing its "
                   "equilibrium every turn.")

    log = []
    for t in turns:
        log.append(f"--- Turn {t.get('turn')} ---")
        log.append(f"  we play: {t.get('our_play')}")
        answer = t.get("punished_by")
        log.append(f"  their best answer: {answer}" if answer
                   else "  their best answer: none worth making")
        # The engine's log already opens each turn with its own "--- Turn N ---"
        # banner, so keeping both would double every header.
        log.extend("  " + e for e in (t.get("events") or [])
                   if e.strip() and not e.strip().startswith("--- Turn"))
        if t.get("kos"):
            log.append("  KO: " + ", ".join(t["kos"]))
        if t.get("hp_after"):
            log.append("  after: " + ", ".join(t["hp_after"]))
    if log:
        st.code("\n".join(log))
    else:
        st.caption("No per-turn log recorded for this line.")


def render_audited_turn(turn, key=None):
    """One audited turn: our play, their answer, the damage, the KOs.

    Shared by the cached overnight browser and the live deep dive so the two
    can never drift into showing the same turn differently. `turn` is either
    the dict stored in the cache or a robustness.TurnRobustness.
    """
    from robustness import describe_action

    def _g(name, default=None):
        if isinstance(turn, dict):
            return turn.get(name, default)
        return getattr(turn, name, default)

    punish = _g("exploitability") or 0.0
    flag = " \u26a0\ufe0f" if punish > 60 else ""
    play = _g("our_play") or (describe_action(_g("our_action"))
                              if _g("our_action") is not None else "?")
    answer = _g("punished_by")
    if answer is None and _g("punisher") is not None and _g("has_punish", True):
        answer = describe_action(_g("punisher"))
    with st.expander(f"Turn {_g('turn')} \u2014 punish {punish:.0f}{flag}"
                     f"   |   {play}", expanded=False):
        st.markdown(f"**We play:** {play}")
        st.markdown(f"**Their best answer:** {answer}" if answer
                    else "**Their best answer:** none \u2014 nothing they can "
                         "do gains meaningful ground here.")
        tied = _g("tied_replies", 1) or 1
        if tied > 5:
            st.caption(f"{tied} of their replies score identically, so the "
                       f"named answer is a tie-break, not a read.")
        events = _g("events") or []
        if events:
            st.code("\n".join(events), language=None)
        kos = _g("kos") or []
        if kos:
            st.error("Knocked out: " + ", ".join(kos))
        hp = _g("hp_after") or []
        if hp and not isinstance(hp[0], str):
            hp = [f"{n} {v}/{mx}" for _s, n, v, mx in hp if v > 0]
        if hp:
            st.caption("After: " + ", ".join(hp))


def get_state_team():
    return st.session_state.get("team", [])


def render_path_explorer(key_prefix, matchup, our_sets):
    """Shared 'does this hold up against the worst rolls AND losing every speed
    tie' panel -- used by both Battle Viewer and Vs Team. `matchup` is
    (our_bring4, enemy_bring4, max_turns) or None."""
    st.markdown("**Path explorer** — does this hold up against the worst rolls AND losing "
               "every speed tie, or only on average?")
    st.caption("Crosses {we win every tie, they win every tie} x {min roll, 5/16 roll, max "
               "roll, average} and reports the outcome of each. 'Robust win' means still a "
               "win in the single worst cell (lose every tie, roll minimum damage) -- the bar "
               "for a plan you can actually rely on, not just one that wins on average.")
    if st.button("Explore paths", disabled=not matchup, key=f"{key_prefix}_paths_go"):
        from matchup_search import robust_win_paths
        mo4, mt4, mturns = matchup
        with st.spinner("Playing out every tie x roll path..."):
            paths = robust_win_paths(mo4, mt4, merged, moves, natures, typechart, mturns,
                                      our_sets=our_sets)
        st.metric("Robust win?", "YES" if paths["robust_win"] else "no",
                  help=f"Worst cell checked: {paths['worst_cell'][0]} / {paths['worst_cell'][1]}")
        prows = [{"Ties": tie, "Roll": roll, "Result": {"p1": "WIN", "p2": "LOSS"}.get(w, w.upper()),
                  "Turns": t} for (tie, roll), (w, t) in paths["paths"].items()]
        st.dataframe(pd.DataFrame(prows), width='stretch', hide_index=True)
    elif not matchup:
        st.caption("Run a search/simulation above first.")


def render_salvage(key_prefix, matchup, our_sets):
    """Shared 'try single changes and see if any flips a loss' panel with live
    apply/save -- used by both Battle Viewer and Vs Team."""
    res_key, matchup_key = f"{key_prefix}_salvage_result", f"{key_prefix}_salvage_matchup"
    st.markdown("**Salvage a losing matchup** — try single changes (EV spread, item/resist "
               "berry, one support or setup move) and see if any flips it.")
    st.caption("Each trial is scored with the real battle engine (does it actually win more), "
               "not the offense-only coverage heuristic used elsewhere in this tool.")
    if st.button("Try to salvage", disabled=not matchup, key=f"{key_prefix}_salvage_go"):
        from salvage import salvage_losing_matchup
        mo4, mt4, mturns = matchup
        with st.spinner("Trying EV/item/move swaps..."):
            res = salvage_losing_matchup(mo4, mt4, merged, moves, natures, typechart, mturns,
                                          our_sets=our_sets)
        st.session_state[res_key] = res
        st.session_state[matchup_key] = matchup
    elif not matchup:
        st.caption("Run a search/simulation above first.")

    sres = st.session_state.get(res_key)
    if sres and st.session_state.get(matchup_key) == matchup:
        bw, bt = sres["baseline"]
        bw_label = {"p1": "WIN", "p2": "LOSS"}.get(bw, bw.upper())
        st.caption(f"Baseline: {bw_label} in {bt} turns")
        if not sres["fixes"]:
            st.info("No single EV/item/move swap tried here flipped the result.")
        else:
            FIELD_FOR_KIND = {"evs": "evs", "item": "item", "support": "moves", "setup": "moves"}

            def _apply_fixes(fix_list):
                cur = dict(st.session_state.get("sets", {}))
                for f in fix_list:
                    field = FIELD_FOR_KIND[f["kind"]]
                    mon_spec = dict(cur.get(f["mon"]) or {})
                    mon_spec[field] = f["new_spec"][field]
                    cur[f["mon"]] = mon_spec
                st.session_state["sets"] = cur

            st.caption("Apply a fix to try it live -- it updates the sets used everywhere else "
                       "in the app (Team Builder, searches, ...) for this session. 'Save' on top "
                       "of that writes it to a team file, so it survives a restart.")
            for i, f in enumerate(sres["fixes"]):
                fc1, fc2 = st.columns([5, 1])
                result_label = {"p1": "WIN", "p2": "LOSS"}.get(f["winner"], f["winner"].upper())
                fc1.write(f"**{f['mon']}** ({f['kind']}): {f['change']} → "
                          f"{result_label} (turn {f['turns']})")
                if fc2.button("Apply", key=f"{key_prefix}_sv_apply_{i}", width='stretch'):
                    _apply_fixes([f])
                    st.rerun()
            if st.button("Apply ALL fixes", key=f"{key_prefix}_sv_apply_all"):
                _apply_fixes(sres["fixes"])
                st.rerun()

            cur_sets = st.session_state.get("sets", {})
            mo4, mt4, mturns = matchup
            from matchup_search import play_out_worst_case as _pow
            w2, t2, btl2 = _pow(mo4, mt4, merged, moves, natures, typechart, mturns,
                                 our_sets=cur_sets)
            w2_label = {"p1": "WIN", "p2": "LOSS"}.get(w2, w2.upper())
            st.metric("This matchup with your CURRENT session sets", f"{w2_label} (turn {t2})",
                      help="Reflects whatever's currently applied, including any fixes clicked "
                           "above -- re-check this after each Apply.")

            st.markdown("**Save current sets permanently (overwrites a team file)**")
            sv_fname = st.text_input("Save as", value=st.session_state.get("save_name", "team.json"),
                                      key=f"{key_prefix}_salvage_save_name")
            if st.button("Save", key=f"{key_prefix}_salvage_save_btn"):
                from team_sheet import save_team
                fn = sv_fname if sv_fname.endswith(".json") else sv_fname + ".json"
                save_team(ROOT / fn, get_state_team(), cur_sets)
                st.success(f"Saved to {ROOT / fn}")


def send_to_battle_viewer(our4, enemy4, turns, label=""):
    """Hand a matchup to the Battle Viewer tab.

    `bv_last_matchup` is the state the Battle Viewer's punish check, path
    explorer and salvage panel all read, so setting it here is what connects
    the two tabs: find a loss in the Lead/Back search, then take THAT battle
    apart in the viewer instead of rebuilding it by hand from two dropdowns.
    """
    st.session_state["bv_last_matchup"] = (list(our4), list(enemy4), turns)
    st.session_state["bv_handoff"] = label or f"{'/'.join(our4)} vs {'/'.join(enemy4)}"


def render_joint_salvage(key_prefix, our4, losing_brings, turns, our_sets):
    """ONE change that fixes ALL the losses -- see salvage.salvage_all_losses.

    Salvaging losses one at a time answers the wrong question: six fixes for
    six losses is six different teams, and the fix for one matchup routinely
    breaks another. This asks whether a single change turns the whole set
    around, and ranks the partial answers when nothing does.
    """
    res_key = f"{key_prefix}_joint_salvage"
    n = len(losing_brings)
    st.markdown(f"**Salvage all {n} losses together** — one change, scored "
                f"against every one of them.")
    st.caption("Each candidate is replayed against every losing bring with the "
               "real engine. A change that fixes four and breaks two is not a "
               "fix, so regressions are counted and shown.")
    c1, c2 = st.columns([1, 2])
    require_all = c2.checkbox("Only show changes that fix ALL of them",
                              value=False, key=f"{key_prefix}_js_all",
                              help="Much faster: a candidate is abandoned as "
                                   "soon as it fails one matchup.")
    if c1.button(f"Salvage all {n}", key=f"{key_prefix}_js_go", disabled=not n):
        from salvage import salvage_all_losses
        bar = st.progress(0.0, text="Replaying candidate changes...")
        try:
            res = salvage_all_losses(
                list(our4), losing_brings, merged, moves, natures, typechart,
                turns, our_sets=our_sets, require_all=require_all,
                progress=lambda i, total: bar.progress(
                    i / total, text=f"Candidate {i} of {total}"))
        finally:
            bar.empty()
        st.session_state[res_key] = res

    res = st.session_state.get(res_key)
    if not res:
        return
    st.caption(f"{res['trials']} single changes tried against {n} losing brings.")
    if not res["fixes"]:
        st.info("No single change fixed any of them. The losses do not share "
                "one cause -- try the per-matchup salvage, or a substitution "
                "(tools/substitute.py) which changes a team MEMBER rather than "
                "a set.")
        return
    complete = [f for f in res["fixes"] if f["fixed"] == f["of"]]
    if complete:
        st.success(f"{len(complete)} single change(s) fix all {n}.")
    else:
        st.warning(f"Nothing fixes all {n}. The best single change fixes "
                   f"{res['fixes'][0]['fixed']}.")
    FIELD_FOR_KIND = {"evs": "evs", "item": "item", "support": "moves",
                      "setup": "moves"}
    for i, f in enumerate(res["fixes"][:10]):
        cols = st.columns([5, 1])
        warn = (f"  ·  breaks {f['regressed']}" if f["regressed"] else "")
        cols[0].write(f"**{f['mon']}** ({f['kind']}): {f['change']} → fixes "
                      f"**{f['fixed']} of {f['of']}**{warn}")
        if cols[1].button("Apply", key=f"{key_prefix}_js_apply_{i}",
                          width='stretch'):
            cur = dict(st.session_state.get("sets", {}))
            spec = dict(cur.get(f["mon"]) or {})
            field = FIELD_FOR_KIND[f["kind"]]
            spec[field] = f["new_spec"][field]
            cur[f["mon"]] = spec
            st.session_state["sets"] = cur
            st.rerun()
        with cols[0].expander("per matchup"):
            for m in f["per_matchup"]:
                mark = ("fixed" if m["improved"] else
                        "BROKE" if m["regressed"] else "no change")
                st.write(f"{mark} — vs {'/'.join(m['enemy'])}: "
                         f"{ {'p1': 'WIN', 'p2': 'LOSS'}.get(m['winner'], m['winner'])}"
                         f" (turn {m['turns']})")


def render_punish_audit(res):
    """Shared renderer for matchup_search.full_game_punish_audit's result --
    used by both Battle Viewer and Vs Team, so the two stay in sync."""
    m1, m2 = st.columns(2)
    m1.metric("Result", {"p1": "WIN", "p2": "LOSS"}.get(res["winner"], res["winner"].upper()))
    m2.metric("Turns", res["turns"])
    n_punish = sum(1 for e in res["audit"] if e["punished"])
    if n_punish == 0:
        st.success(f"No punishable turn across all {len(res['audit'])} turns checked -- every "
                   "one of our plays holds up against every legal response tried.")
    else:
        st.warning(f"{n_punish} of {len(res['audit'])} turns had a legal response of theirs "
                   "that punishes our planned play.")
    for e in res["audit"]:
        tag = "PUNISHABLE" if e["punished"] else "safe"
        with st.expander(f"Turn {e['turn']}: {tag} — {e['our_action']}"):
            if e["punished"]:
                kind_label = ("KO'd this turn" if e["kind"] == "immediate" else
                               "survives this turn, but even our best follow-up loses a "
                               "Pokemon on their very next move regardless")
                st.error(f"Punishable: {kind_label}.")
                st.write(f"**Their punishing response:** {e['their_punishing_action']}")
            else:
                st.success("No legal response tried punishes this play.")
    with st.expander("Full battle log"):
        st.code(res["log"])


# ------------------------------------------------------------------ builder
with tab_build:
    st.subheader("Pick your 6")
    c1, c2 = st.columns([3, 1])
    with c2:
        st.markdown("**Load / save**")
        from team_sheet import load_team, save_team, load_analysis
        saved = sorted(p.name for p in ROOT.glob("*.json"))
        pick = st.selectbox("Saved teams", saved or ["(none)"], key="load_pick")
        if st.button("Load", width='stretch', disabled=not saved):
            pool, sets = load_team(ROOT / pick)
            st.session_state["team"] = pool
            st.session_state["sets"] = sets
            st.session_state["team_analysis"] = load_analysis(ROOT / pick)
            st.success(f"Loaded {len(pool)} Pokemon from {pick}")
        up = st.file_uploader("...or upload a team .json", type="json", key="up_team")
        if up is not None:
            import json as _json
            data = _json.loads(up.read())
            st.session_state["team"] = data.get("pool", data if isinstance(data, list) else [])
            st.session_state["sets"] = data.get("sets", {}) if isinstance(data, dict) else {}
            st.session_state["team_analysis"] = (data.get("analysis")
                                                  if isinstance(data, dict) else None)
            st.success(f"Loaded {len(st.session_state['team'])} Pokemon from upload")
        if st.button("Use default 6", width='stretch'):
            st.session_state["team"] = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon",
                                         "Mega Skarmory", "Gholdengo"]
            st.session_state["sets"] = {}
            st.session_state["team_analysis"] = None
        only_top = st.checkbox("Only show top-100 by Score", value=False,
                                help="Off by default -- this is the hand-picking tab, so every "
                                     "Pokemon should be selectable. Turn on to shorten the list "
                                     "to the top 100 by roster.csv Score if it's unwieldy.")
        gen_filter = st.multiselect(
            "Restrict to generation(s)", list(range(1, 10)), default=[], key="tb_gen_filter",
            help="A form (Mega, regional, ...) counts as its BASE species' generation -- "
                 "Mega Lucario is gen 4, Arcanine-Hisui is gen 1. Empty = no restriction.")

    pool_options = all_names
    if only_top:
        ranked = sorted([n for n in all_names if merged[n].get("score")],
                        key=lambda n: -merged[n]["score"])[:100]
        pool_options = sorted(set(ranked) | set(get_state_team()))
    if gen_filter:
        allowed = set(gen_filter)
        pool_options = sorted(set(n for n in pool_options if generation_map.get(n) in allowed)
                               | set(get_state_team()))

    with c1:
        team = st.multiselect("Your team (choose 6)", pool_options,
                               default=get_state_team(), max_selections=6)
        st.session_state["team"] = team

    if len(team) == 6:
        sets = st.session_state.get("sets", {})
        megas = [n for n in team if find_mega_stone(n, merged)]
        if len(megas) > 1:
            st.info(f"{len(megas)} Mega-capable picks ({', '.join(megas)}). Only one can "
                    f"Mega Evolve per battle -- which one is chosen per matchup by the search.")
        st.dataframe(team_sheet_df(team, sets), width='stretch', hide_index=True)

        # --- Item editor -------------------------------------------------
        # Items were only settable by running the optimiser, which decides all
        # six at once. Picking one by hand is a different job -- you know this
        # Pokemon wants a Choice Scarf and want the rest left alone -- and it
        # has to write to the same `sets` override every simulation reads, or
        # the choice would be cosmetic.
        with st.expander("Edit items", expanded=False):
            st.caption("Used by every simulation the app runs. A Mega-capable "
                       "pick is locked to its stone -- that is what makes it a "
                       "Mega. The list is what this Pokemon is actually "
                       "recorded holding; 'Any item...' below it opens the "
                       "full catalogue for something the usage data has never "
                       "seen.")
            from optimize_sets import TYPE_RESIST_BERRY, legal_items
            item_sets = dict(st.session_state.get("sets") or {})
            OTHER = "Any item..."
            # Every item anyone in the dataset is recorded holding, plus the
            # resist berries (which nobody logs usage for, so they would
            # otherwise be unreachable by hand). Mega Stones are excluded: a
            # Mega is a SPECIES choice here ("Mega Metagross" in the team
            # picker), and handing a stone to a base form would produce a
            # Pokemon the rest of the engine does not treat as a Mega -- an
            # item that silently does nothing.
            def _is_stone(item):
                low = item.lower().replace(" x", "").replace(" y", "")
                return low.endswith("ite")

            # Safety Goggles is in the catalogue despite nobody in the dataset
            # being recorded holding one, because it is now a real decision:
            # it turns off sandstorm chip damage (Battle._sand_immune), and an
            # item you cannot select is not a decision.
            all_items = sorted(
                {i for rec in merged.values()
                 for i, _pct in (rec.get("items_usage") or [])
                 if i and i.lower() != "other" and not _is_stone(i)}
                | set(TYPE_RESIST_BERRY.values()) | {"Safety Goggles"})
            item_changed = False
            for mon in team:
                spec = dict(item_sets.get(mon) or {})
                usage = merged[mon].get("items_usage") or []
                default_item = usage[0][0] if usage else "(none)"
                options = legal_items(mon, merged)
                locked = bool(find_mega_stone(mon, merged))
                current = spec.get("item") or default_item
                if current not in options:
                    options = options + [current]
                ic1, ic2 = st.columns([2, 3])
                ic1.markdown(f"**{mon}**")
                if locked:
                    ic2.text_input("Item", value=options[0], disabled=True,
                                   key=f"item_{mon}_locked",
                                   label_visibility="collapsed")
                    continue
                choice = ic2.selectbox(
                    "Item", options + [OTHER], index=options.index(current),
                    key=f"item_{mon}", label_visibility="collapsed",
                    help=f"usage default: {default_item}")
                if choice == OTHER:
                    choice = ic2.selectbox(
                        "Any item", all_items,
                        index=all_items.index(current) if current in all_items else 0,
                        key=f"item_any_{mon}", label_visibility="collapsed")
                if choice != current:
                    spec["item"] = choice
                    item_sets[mon] = spec
                    item_changed = True
            b1, b2 = st.columns(2)
            if b1.button("Apply items", width='stretch', key="item_apply"):
                st.session_state["sets"] = item_sets
                st.success("Applied — every simulation now uses them.")
                st.rerun()
            if b2.button("Reset to usage defaults", width='stretch',
                         key="item_reset"):
                for mon in team:
                    if mon in item_sets:
                        item_sets[mon].pop("item", None)
                st.session_state["sets"] = item_sets
                st.rerun()
            if item_changed:
                st.info("Edited but not applied — press Apply items.")

        # --- Stat point editor -----------------------------------------
        # These are NOT standard EVs. stats.py is explicit:
        #     final_stat = normal_stat + ev_points   (flat, no /4)
        # One point is one stat point, added after the level-50 formula --
        # a Champions M-B house rule. The dataset's spreads total 32 to 66
        # points, 66 being by far the most common, so 66 is the budget and the
        # per-stat cap. Treating these as real EVs (step 4, cap 252, 508 total)
        # would let a 1512-point Pokemon be built and reported as legal.
        with st.expander("Edit stat points", expanded=False):
            st.caption("Flat stat points, not EVs: each point is +1 to that "
                       "stat after the level-50 formula. No division by four. "
                       "Used by every simulation the app runs, because they "
                       "travel with the same overrides as items and moves.")
            cur_sets = dict(st.session_state.get("sets") or {})
            STATS = ("hp", "atk", "def", "spa", "spd", "spe")
            changed = False
            for mon in team:
                base = dict((merged.get(mon) or {}).get("evs") or {})
                budget = sum(base.values()) or STAT_POINT_BUDGET
                spec = dict(cur_sets.get(mon) or {})
                have = dict(spec.get("evs") or base)
                st.markdown(f"**{mon}**")
                cols = st.columns(6)
                new_spread = {}
                for col, stat in zip(cols, STATS):
                    new_spread[stat] = col.number_input(
                        stat.upper(), min_value=0, max_value=budget, step=1,
                        value=int(have.get(stat, 0)), key=f"ev_{mon}_{stat}")
                total = sum(new_spread.values())
                if total > budget:
                    st.error(f"{total} points — {total - budget} over this "
                             f"Pokemon's {budget}-point budget.")
                else:
                    st.caption(f"{total} / {budget} points"
                               + (f" — {budget - total} unspent"
                                  if total < budget else ""))
                if new_spread != have:
                    spec["evs"] = new_spread
                    cur_sets[mon] = spec
                    changed = True
            e1, e2 = st.columns(2)
            if e1.button("Apply stat points", width='stretch', key="ev_apply"):
                st.session_state["sets"] = cur_sets
                st.success("Applied — every simulation now uses them.")
                st.rerun()
            if e2.button("Reset to dataset spreads", width='stretch',
                         key="ev_reset"):
                for mon in team:
                    if mon in cur_sets:
                        cur_sets[mon].pop("evs", None)
                st.session_state["sets"] = cur_sets
                st.rerun()
            if changed:
                st.info("Edited but not applied — press Apply stat points.")

        cc1, cc2 = st.columns(2)
        with cc1:
            if st.button("Optimise items + movesets vs teams.csv", type="primary",
                          width='stretch'):
                from optimize_sets import optimise_team_unique as optimise_team, enemy_individuals
                with st.spinner("Optimising..."):
                    en = enemy_individuals(teams)
                    opt = optimise_team(team, merged, moves, natures, typechart, en)
                    st.session_state["sets"] = {n: {"item": opt[n]["item"], "moves": opt[n]["moves"]}
                                                 for n in team}
                st.rerun()
        with cc2:
            fname = st.text_input("Save as", value="team.json", key="save_name")
            if st.button("Save", width='stretch'):
                from team_sheet import save_team
                from team_search import compute_team_analysis
                if not fname.endswith(".json"):
                    fname += ".json"
                with st.spinner("Computing team-detail analysis..."):
                    analysis = compute_team_analysis(team, merged, moves, natures, typechart, teams)
                st.session_state["team_analysis"] = analysis
                save_team(ROOT / fname, team, st.session_state.get("sets", {}), analysis=analysis)
                st.success(f"Saved to {ROOT/fname} (with team-detail analysis)")
            import json as _json
            st.download_button(
                "Download team .json",
                _json.dumps({"pool": team, "sets": st.session_state.get("sets", {}),
                             **({"analysis": st.session_state["team_analysis"]}
                                if st.session_state.get("team_analysis") else {})}, indent=2),
                file_name=(fname if fname.endswith(".json") else fname + ".json"),
                mime="application/json", width='stretch')

        with st.expander("Move contributions — which moves are earning their slot"):
            st.caption("Computed live from the current sets, so it refreshes after loading "
                       "a .json or re-optimising. A move that is best against nobody and "
                       "does little damage is dead weight -- that slot may be better spent "
                       "on setup or utility that flips a losing matchup.")
            if st.button("Analyse moves", key="mv_an"):
                from optimize_sets import (move_contributions, suggest_replacements,
                                            enemy_individuals)
                en = enemy_individuals(teams)
                for n in team:
                    spec = st.session_state.get("sets", {}).get(n) or {}
                    itm = spec.get("item") or (merged[n]["items_usage"][0][0]
                                                if merged[n]["items_usage"] else "")
                    mvs = spec.get("moves") or [x for x, _ in merged[n]["moves_usage"][:4]]
                    c = move_contributions(n, itm, mvs, merged, moves, natures, typechart, en)
                    rows = [{"Move": k, "Best against": f"{v['best_against']} mons",
                              "Max damage share": v["max_value"],
                              "Verdict": "dead weight" if v["dead_weight"] else ""}
                            for k, v in c.items()]
                    st.markdown(f"**{n}** @ {itm}")
                    st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
                    sug = suggest_replacements(n, itm, mvs, merged, moves, natures, typechart, en)
                    if sug:
                        for slot, alts in sug.items():
                            st.warning(f"{n}: '{slot}' contributes nothing — consider "
                                        f"{', '.join(alts)}")

        with st.expander("Team detail — member/threat contributions",
                          expanded=bool(st.session_state.get("team_analysis"))):
            st.caption("Which enemy lead pairs each member is the team's best answer to, and "
                       "which of those NO other pair on the team can beat (irreplaceable). "
                       "Persisted into team.json on Save, so it's available right after loading "
                       "one -- not only right after a live Generate Team run.")
            ta = st.session_state.get("team_analysis")
            refresh = st.button("Compute / refresh analysis", key="ta_refresh")
            if refresh:
                from team_search import compute_team_analysis
                with st.spinner("Computing..."):
                    ta = compute_team_analysis(team, merged, moves, natures, typechart, teams)
                st.session_state["team_analysis"] = ta
            if ta:
                st.caption("Tested against: " + ", ".join(ta.get("tested_against", [])))
                rows = []
                for n in team:
                    m = ta.get("members", {}).get(n)
                    if not m:
                        continue
                    top = m["top_threats"][:3]
                    irr = m["irreplaceable"][:3]
                    rows.append({
                        "Pokemon": n,
                        "Best answer to (n)": m["wins"],
                        "Top threats it answers": "; ".join(
                            f"{t['team']} {t['enemy_pair'][0]}/{t['enemy_pair'][1]}" for t in top) or "-",
                        "Irreplaceable for (n)": len(m["irreplaceable"]),
                        "Example irreplaceable": "; ".join(
                            f"{t['team']} {t['enemy_pair'][0]}/{t['enemy_pair'][1]}" for t in irr) or "-",
                    })
                st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
            else:
                st.caption("No analysis yet — click above, or Save the team to compute and "
                           "persist it into the .json.")

        st.markdown("**Defensive coverage** (rule: no more than 2 members weak to any one type)")
        wt = weakness_table(team)
        over = wt[wt["Over limit"] == "YES"]
        if len(over):
            st.error(f"Types over the limit: {', '.join(over['Type'])}")
        else:
            st.success("No type exceeds 2 weaknesses")
        st.dataframe(wt, width='stretch', hide_index=True, height=250)
    else:
        st.warning(f"Select 6 Pokemon ({len(team)} chosen)")


# ------------------------------------------------------------------ generate
with tab_gen:
    st.subheader("Generate an optimal team")
    g1, g2, g3, g4 = st.columns(4)
    max_pool = len([n for n in merged if merged[n].get("score") is not None])
    pool_size = g1.slider(
        "Candidate pool size", 12, max_pool, min(26, max_pool),
        help=f"How many Pokemon the search may choose from, taken as the top-N by "
             f"roster.csv Score (max {max_pool}, the whole dataset). A wider pool can "
             f"find better teams but the screening cost grows roughly quadratically: "
             f"~25 takes seconds, ~60 tens of seconds, the full pool several minutes.")
    beam = g2.slider(
        "Beam width", 10, 200, 30,
        help="How many partial teams are kept alive at each step while growing 2 -> 6 "
             "members. The search is not exhaustive: at every step it scores all the "
             "ways to add one Pokemon and keeps only the best `beam width` of them. "
             "Narrow (10-20) is fast but can discard a team whose value only appears "
             "once the 6th member is added; wide (80+) explores more of those and is "
             "slower. Raise it if results look like they're missing an obvious core.")
    top_n = g3.slider("Teams to report", 1, 25, 3,
                       help="How many beam-search finalists to show. Cheap -- they are "
                            "already computed; only the optional set optimisation per "
                            "team costs extra time.")
    do_opt = g4.checkbox("Optimise sets", value=True,
                          help="Pick each member's item and 4 moves against the specific "
                               "Pokemon in teams.csv, with no item used twice on the team.")
    w1, w2 = st.columns(2)
    max_weak = w1.slider("Max members weak to any one type", 1, 6, 2,
                          help="A team is penalised for every member beyond this that "
                               "shares a weakness to the same type.")
    use_net = w2.checkbox("Also cap NET weakness", value=False,
                           help="Net = (members weak to a type) - (members that resist it). "
                                "Often the better rule: three Fire-weak members matter far "
                                "less if four others resist Fire.")
    max_net = w2.slider("Max net weakness", -6, 6, 1, disabled=not use_net) if True else None
    if not use_net:
        max_net = None

    with st.expander("Must include / exclude / prefer (overrides preferences.csv for this run)"):
        st.caption("A quick per-run knob instead of editing preferences.csv: 'must include' and "
                   "'must exclude' are hard constraints (an include is always kept in the "
                   "candidate pool and every generated team; an exclude never appears at all), "
                   "'prefer' is a soft tiebreaker. Narrowing the pool this way also cuts search "
                   "time -- fewer legal combinations to screen.")
        ic1, ic2, ic3 = st.columns(3)
        include_sel = ic1.multiselect("Must include", all_names,
                                       default=[n for n in prefs["include"] if n in all_names])
        exclude_sel = ic2.multiselect("Must exclude", all_names,
                                       default=[n for n in prefs["exclude"] if n in all_names])
        prefer_sel = ic3.multiselect("Prefer (soft)", all_names,
                                      default=[n for n in prefs["prefer"] if n in all_names])
        run_prefs = {"include": include_sel, "exclude": exclude_sel, "prefer": prefer_sel}

    gen_options = list(range(1, 10))
    allowed_gens = st.multiselect(
        "Restrict to generation(s)", gen_options, default=[],
        help="e.g. pick just 3 for gen-3-only, or 1-5 for gen 1 through 5. Empty = no "
             "restriction (all generations). A form (Mega, regional, ...) counts as its "
             "BASE species' generation -- Mega Lucario is gen 4, Arcanine-Hisui is gen 1.")

    vc1, vc2 = st.columns([2, 1])
    verify_n = vc1.slider("Verify top N with the real solver (slower, trustworthy)", 0, 25, 1,
                           help="Each verified team is re-run through the full engine against "
                                "every opponent. With 'All enemy brings' on that is roughly 20s "
                                "per team per opponent, so this is the main cost of a run.")
    deep = vc2.checkbox("All enemy brings", value=True,
                         help="Verify against every enemy bring-4 -- all C(6,2) leads x "
                              "C(4,2) backs = 90 configurations per opponent -- instead of "
                              "one sampled back pair. Slower but the only trustworthy check.")
    script_screen = st.checkbox(
        "Early-disqualify finalists that always lose a scripted opening", value=False,
        help="Before ranking/reporting, cheaply check EVERY beam-search finalist (not just "
             "the ones you'd otherwise verify) against scripted opponents (King / Hard Trick "
             "Room / Perish Trap) with the real solver -- one sampled enemy config each, so "
             "it's much cheaper than 'All enemy brings' verification. A team with genuinely "
             "no plan against a script (loses ALL of them, not just one) is dropped before it "
             "can take a report slot.")

    with st.expander("Pair matrix cache (skip the expensive rebuild)"):
        st.caption("Building the pair matrix (every candidate lead pair of ours x every "
                   "enemy lead pair) is the slow step, especially with a big pool size. "
                   "Once built it's kept for this session and reused automatically as long "
                   "as pool size / generation filter / preferences.csv haven't changed since. "
                   "Download it to reuse across separate app sessions -- upload it back here "
                   "before clicking 'Run generation'.")
        cached_matrix = st.session_state.get("gen_matrix")
        if cached_matrix is not None:
            st.caption(f"In-session cache: {len(cached_matrix)} of our pairs x "
                       f"{len(st.session_state.get('gen_eps') or [])} enemy pairs.")
            import pickle as _pickle
            st.download_button(
                "Download matrix cache", _pickle.dumps({
                    "pool": st.session_state.get("gen_pool"),
                    "enemy_pairs": st.session_state.get("gen_eps"),
                    "matrix": cached_matrix,
                }), file_name="matrix_cache.pkl", mime="application/octet-stream")
        up_matrix = st.file_uploader("...or upload a saved matrix cache (.pkl)", type="pkl",
                                      key="up_matrix_cache")
        if up_matrix is not None:
            import pickle as _pickle
            loaded = _pickle.loads(up_matrix.read())
            st.session_state["gen_matrix"] = loaded["matrix"]
            st.session_state["gen_pool"] = loaded["pool"]
            st.session_state["gen_eps"] = loaded["enemy_pairs"]
            st.success(f"Loaded matrix cache: {len(loaded['matrix'])} of our pairs.")

    if st.button("Run generation", type="primary"):
        from team_search import (build_candidate_pool, enemy_pairs_from_teams, build_pair_matrix,
                                  beam_search_teams)
        if allowed_gens:
            pool = build_candidate_pool(merged, top_n=pool_size, prefs=run_prefs,
                                         allowed_generations=set(allowed_gens),
                                         generation_map=generation_map)
        else:
            pool = build_candidate_pool(merged, top_n=pool_size, prefs=run_prefs)
        eps = enemy_pairs_from_teams(teams)

        matrix = None
        if (st.session_state.get("gen_pool") == pool and st.session_state.get("gen_eps") == eps
                and st.session_state.get("gen_matrix") is not None):
            matrix = st.session_state["gen_matrix"]
            st.info(f"Reusing cached pair matrix ({len(matrix)} of our pairs) -- pool and "
                    f"enemy teams match the cache.")
        else:
            prog = st.progress(0.0, "Screening pair matchups...")
            t0 = time.time()
            matrix = build_pair_matrix(pool, eps, merged, moves, natures, typechart,
                                        progress=lambda i, t: prog.progress(i / t, f"{i}/{t} pairs"))
            prog.progress(1.0, f"Matrix built in {time.time()-t0:.0f}s -- searching teams...")
            prog.empty()

        finals = beam_search_teams(pool, matrix, eps, merged, beam_width=beam,
                                    must_include=run_prefs["include"], prefer=run_prefs["prefer"])

        if script_screen and finals:
            from generate_team import quick_script_screen
            with st.spinner("Screening finalists against scripted openings..."):
                screened = quick_script_screen(finals, teams, matrix, merged, moves, natures,
                                                typechart, max_turns=12)
            survivors = [(sc, t) for sc, t, dq, _d in screened if not dq]
            dropped = [(t, d) for _sc, t, dq, d in screened if dq]
            if dropped:
                st.warning(f"Dropped {len(dropped)}/{len(finals)} finalists that lose EVERY "
                           f"scripted opening tested (King / Hard Trick Room / Perish Trap, "
                           f"whichever are in teams.csv).")
            if survivors:
                finals = survivors
            elif dropped:
                st.warning("Every finalist always loses a scripted opening -- keeping the "
                           "original list rather than showing nothing.")

        if not finals:
            st.error("No valid teams found -- try a larger pool size.")
        else:
            st.session_state["gen_finals"] = [(sc, t) for sc, t in finals[:top_n]]
            st.session_state["gen_matrix"] = matrix
            st.session_state["gen_pool"] = pool
            st.session_state["gen_eps"] = eps
            for rank, (sc, t) in enumerate(finals[:top_n], 1):
                with st.expander(f"Team #{rank} — beats {sc['pairs_won']}/{sc['pairs_total']} "
                                  f"enemy lead pairs", expanded=(rank == 1)):
                    sets = {}
                    if do_opt:
                        from optimize_sets import optimise_team_unique as optimise_team, enemy_individuals
                        opt = optimise_team(t, merged, moves, natures, typechart,
                                             enemy_individuals(teams))
                        sets = {n: {"item": opt[n]["item"], "moves": opt[n]["moves"]} for n in t}
                    st.dataframe(team_sheet_df(t, sets), width='stretch', hide_index=True)
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Matchups won", f"{sc['pairs_won']}/{sc['pairs_total']}")
                    m2.metric("Type cores", f"{sc['core_bonus']:.2f}")
                    m3.metric("Weakness violations", sc["weakness_violations"])
                    if sc["matched_cores"]:
                        st.caption("Cores: " + ", ".join(sc["matched_cores"]))
                    cg1, cg2 = st.columns([1, 2])
                    with cg1:
                        import json as _j
                        st.download_button(
                            f"Download #{rank} .json",
                            _j.dumps({"pool": list(t), "sets": sets}, indent=2),
                            file_name=f"generated_{rank}.json", mime="application/json",
                            key=f"dl{rank}", width='stretch',
                            help="Exactly the format the Team Builder loads -- feed it back "
                                 "via 'upload a team .json' or the saved-team dropdown.")
                        if st.button(f"Use team #{rank}", key=f"use{rank}", width='stretch'):
                            st.session_state["team"] = list(t)
                            st.session_state["sets"] = sets
                            st.success("Loaded into Team Builder")
                    with cg2:
                        gname = st.text_input("Save as", value=f"generated_{rank}.json",
                                               key=f"gname{rank}")
                        gc1, gc2 = st.columns(2)
                        with gc1:
                            if st.button("Save", key=f"gsave{rank}", width='stretch'):
                                from team_sheet import save_team
                                fn = gname if gname.endswith(".json") else gname + ".json"
                                save_team(ROOT / fn, list(t), sets)
                                st.success(f"Saved {fn}")
                        with gc2:
                            import json as _json
                            st.download_button(
                                "Download", _json.dumps({"pool": list(t), "sets": sets}, indent=2),
                                file_name=(gname if gname.endswith(".json") else gname + ".json"),
                                mime="application/json", key=f"gdl{rank}", width='stretch')

                    if verify_n and rank <= verify_n:
                        from generate_team import verify_with_solver
                        with st.spinner("Verifying with the real solver..."):
                            v = verify_with_solver(t, teams, merged, moves, natures, typechart,
                                                    matrix, eps, 12, all_backs=deep,
                                                    our_sets=sets)
                        vr = []
                        for k, r in v.items():
                            if not r:
                                continue
                            if r.get("mode") == "all_backs":
                                b4 = r.get("our_bring4") or []
                                worst = r["losses"][0] if r["losses"] else None
                                vr.append({
                                    "Opponent": k,
                                    "Our lead": "/".join(b4[:2]) if b4 else "-",
                                    "Our back": "/".join(b4[2:]) if b4 else "-",
                                    "Enemy brings beaten": f"{r['wins']}/{r['total']}",
                                    "Clean": "yes" if not r["losses"] else "no",
                                    "Example loss": (f"{worst[0][0]}/{worst[0][1]} + "
                                                      f"{worst[1][0]}/{worst[1][1]}") if worst else "-",
                                })
                            else:
                                vr.append({
                                    "Opponent": k,
                                    "Our lead": "/".join(r["our_bring4"][:2]),
                                    "Our back": "/".join(r["our_bring4"][2:]),
                                    "Enemy brings beaten": "1 sampled",
                                    "Clean": "yes" if r["winner"] == "p1" else "no",
                                    "Example loss": (f"lead {r['enemy_lead'][0]}/{r['enemy_lead'][1]}"
                                                      if r["winner"] != "p1" else "-"),
                                })
                        tot_w = sum(r["wins"] for r in v.values() if r and r.get("mode") == "all_backs")
                        tot_n = sum(r["total"] for r in v.values() if r and r.get("mode") == "all_backs")
                        st.session_state.setdefault("verified_teams", {})
                        st.session_state["verified_teams"][tuple(t)] = {
                            "team": list(t), "sets": sets, "wins": tot_w, "total": tot_n,
                            "per_opponent": {k: (r["wins"], r["total"]) for k, r in v.items()
                                              if r and r.get("mode") == "all_backs"},
                            "clean": tot_n > 0 and tot_w == tot_n,
                        }
                        st.markdown("**Solver verification** (trustworthy, unlike the screener)")
                        st.dataframe(pd.DataFrame(vr), width='stretch', hide_index=True)



    # ---- Verified leaderboard -------------------------------------------------
    vt = st.session_state.get("verified_teams") or {}
    if vt:
        st.divider()
        st.subheader("Verified teams — ranked")
        st.caption("Every team solver-verified this session, ranked by enemy bring-4s "
                   "beaten across ALL opponents, including scripted ones (fixed-lead teams "
                   "like Hard Trick Room / Perish Trap play their rehearsed script -- and the "
                   "generic Protect/attack alternatives -- against every one of your bring-4s, "
                   "with your side adapting each turn via the real solver). 'Perfect' = beats "
                   "every one of the 90 bring-4s for every opponent.")
        only_perfect = st.checkbox("Perfect only", value=False, key="lb_only_perfect")
        ranked_all = sorted(vt.values(), key=lambda d: (-d["wins"], -int(d["clean"])))
        ranked = [d for d in ranked_all if not only_perfect or d["clean"]]
        for i, d in enumerate(ranked, 1):
            d["rank"] = i

        rows = []
        for d in ranked:
            row = {"#": d["rank"], "Perfect": "YES" if d["clean"] else "",
                    "Total": f"{d['wins']}/{d['total']}",
                    "Team": ", ".join(d["team"])}
            for opp, (w, n) in d["per_opponent"].items():
                row[opp] = f"{w}/{n}"
            rows.append(row)
        st.dataframe(pd.DataFrame(rows), width='stretch', hide_index=True)
        if not ranked and only_perfect:
            st.info("No verified team matches the current filters.")

        # --- bulk export -------------------------------------------------------
        st.markdown("**Export**")
        labels = {f"#{d['rank']} — {', '.join(d['team'])}"
                  f"{'  (PERFECT)' if d['clean'] else ''}":
                  d for d in ranked}
        picked = st.multiselect("Teams to export", list(labels),
                                 default=[k for k in labels if "(PERFECT)" in k])
        e1, e2 = st.columns(2)
        if picked:
            chosen = [labels[k] for k in picked]
            # One ZIP of individual team .json files. Using download_button (not a
            # normal button) means no rerun is triggered, so the page no longer
            # resets after exporting.
            import io, zipfile, json as _json
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for d in chosen:
                    payload = {"pool": d["team"], "sets": d["sets"]}
                    zf.writestr(f"team_{d['rank']:02d}.json", _json.dumps(payload, indent=2))
            e1.download_button(f"Download {len(chosen)} team(s) as ZIP", buf.getvalue(),
                                file_name="teams.zip", mime="application/zip",
                                width='stretch')
            combined = _json.dumps([{"rank": d["rank"], "pool": d["team"], "sets": d["sets"],
                                      "wins": d["wins"], "total": d["total"]} for d in chosen],
                                    indent=2)
            e2.download_button("Download as one combined .json", combined,
                                file_name="teams_combined.json", mime="application/json",
                                width='stretch')
            if e1.button("Save selected to disk", width='stretch'):
                from team_sheet import save_team as _save
                for d in chosen:
                    _save(ROOT / f"team_{d['rank']:02d}.json", d["team"], d["sets"])
                st.success(f"Wrote {len(chosen)} file(s) to {ROOT}")

        # --- per-team detail ---------------------------------------------------
        st.markdown("**Team detail**")
        pick = st.selectbox("Inspect a team", list(labels), key="lb_inspect")
        if pick:
            d = labels[pick]
            st.dataframe(team_sheet_df(d["team"], d["sets"]), width='stretch', hide_index=True)
            # Prefer the cheap in-session pool matrix (from this generation run) when it's
            # available -- reuses work already done. Otherwise fall back to recomputing just
            # the 15 in-team pairs (compute_team_analysis), which is cheap enough to always
            # work, not just right after a live generation.
            mtx = st.session_state.get("gen_matrix")
            eps_saved = st.session_state.get("gen_eps")
            if mtx and eps_saved:
                from team_search import member_contributions
                contrib = member_contributions(d["team"], mtx, eps_saved, merged)
                crows = []
                for n in d["team"]:
                    c = contrib[n]
                    ex = c["irreplaceable"][:2]
                    crows.append({
                        "Pokemon": n,
                        "Best answer to": f"{c['wins']} threats",
                        "Only answer to": len(c["irreplaceable"]),
                        "Example threat only it beats": "; ".join(
                            f"{t} {e[0]}/{e[1]}" for t, e in ex) or "-",
                    })
                st.dataframe(pd.DataFrame(crows), width='stretch', hide_index=True)
            elif st.button("Compute contribution analysis", key="lb_ta_compute"):
                from team_search import compute_team_analysis
                with st.spinner("Computing..."):
                    ta = compute_team_analysis(d["team"], merged, moves, natures, typechart, teams)
                crows = [{
                    "Pokemon": n, "Best answer to": f"{m['wins']} threats",
                    "Only answer to": len(m["irreplaceable"]),
                    "Example threat only it beats": "; ".join(
                        f"{t['team']} {t['enemy_pair'][0]}/{t['enemy_pair'][1]}"
                        for t in m["irreplaceable"][:2]) or "-",
                } for n, m in ta.get("members", {}).items()]
                st.dataframe(pd.DataFrame(crows), width='stretch', hide_index=True)
            else:
                st.caption("Click above to compute (cheap -- only this team's 15 pairs), "
                           "or it's reused instantly from this session's generation run.")
            st.caption("'Best answer to' counts enemy lead pairs where a pair "
                       "containing this Pokemon is the team's strongest response. "
                       "'Only answer to' counts threats that NO pair without it "
                       "beats -- that is the concrete case for its slot.")
            if st.button("Load into Team Builder", key="lb_load"):
                st.session_state["team"] = d["team"]
                st.session_state["sets"] = d["sets"]
                st.success("Loaded")
        if st.button("Clear leaderboard"):
            st.session_state["verified_teams"] = {}
            st.rerun()


# ------------------------------------------------------------------ search
with tab_search:
    st.subheader("Lead / back search")
    team = get_state_team()
    if len(team) != 6:
        st.warning("Pick 6 Pokemon in the Team Builder tab first.")
    else:
        s1, s2, s3 = st.columns(3)
        chosen = s1.multiselect("Opponents", list(teams), default=list(teams))
        for _t in chosen:
            _m = team_meta.get(_t, {})
            if _m.get("note"):
                _fl = _m.get("lead")
                st.info(f"**{_t}**" + (f" — fixed lead {_fl[0]}/{_fl[1]}" if _fl else "")
                        + f"\n\n{_m['note']}")
        mode = s2.radio(
            "Enemy brings to test", ["all backs (90)", "sampled back (fast)"], index=0,
            help="all backs = every C(6,2) lead x C(4,2) back = 90 configurations per "
                 "opponent. The opponent picks their own best bring, so you only 'beat' "
                 "them if you beat all 90. Sampling one back pair is far faster but lets "
                 "compositions look clean while losing to backs never tried.")
        max_turns = s3.slider("Turn cap", 6, 24, 12)
        n_roll = st.slider(
            "Rolled games per matchup (0 = deterministic only)", 0, 200, 0, step=20,
            help="Re-runs each recommended matchup with real damage rolls, secondary "
                 "effects like Rock Slide flinches, and coin-flip speed ties, reporting a "
                 "win probability. A deterministic WIN can still be an 80% matchup.")

        with st.expander("Enemy set overrides (optional)"):
            st.caption("Pin a specific enemy Pokemon's exact item/moves instead of its "
                       "usage-derived default -- e.g. data/enemy_overrides.json ships a "
                       "Kingambit set (Kowtow Cleave/Sucker Punch/Iron Head/Low Kick). "
                       "Same JSON shape as a team.json's 'sets' block, or a bare "
                       "{name: {item, moves}} dict.")
            default_ov = ROOT / "data" / "enemy_overrides.json"
            use_default = st.checkbox(f"Use {default_ov.name}", value=default_ov.exists(),
                                       disabled=not default_ov.exists(), key="use_default_esets")
            up_es = st.file_uploader("...or upload enemy-set overrides .json", type="json",
                                      key="up_esets")
            enemy_sets_override = {}
            if up_es is not None:
                import json as _json
                enemy_sets_override = _json.loads(up_es.read())
                if "sets" in enemy_sets_override:
                    enemy_sets_override = enemy_sets_override["sets"]
            elif use_default and default_ov.exists():
                from team_sheet import load_sets_override
                enemy_sets_override = load_sets_override(default_ov)
            if enemy_sets_override:
                st.caption(f"Active overrides: {list(enemy_sets_override.keys())}")
            st.session_state["enemy_sets_override"] = enemy_sets_override

        if st.button("Run search", type="primary") and chosen:
            from matchup_search import (search_robust_composition, search_best_composition,
                                         play_out_worst_case, enemy_configs)
            from run_search import find_toughest_lead
            sets = st.session_state.get("sets", {})
            esets = st.session_state.get("enemy_sets_override") or {}
            all_backs = mode.startswith("all")
            results = {}
            prog = st.progress(0.0)
            for i, tname in enumerate(chosen):
                roster = teams[tname]
                team_esets = {**esets, **(team_meta.get(tname, {}).get("sets") or {})}
                if all_backs:
                    import scripted_openings as _so
                    _fl = team_meta.get(tname, {}).get('lead')
                    rob = search_robust_composition(team, roster, merged, moves, natures,
                                                     typechart, max_turns, our_sets=sets,
                                                     verify_top=1,
                                                     fixed_lead=_fl,
                                                     enemy_script=_so.script_for(tname),
                                                     script_team=tname if tname in _so.SCRIPTS
                                                     else None, enemy_sets=team_esets)
                    results[tname] = {"mode": "all", "robust": rob[0] if rob else None,
                                       "roster": roster}
                else:
                    lead = find_toughest_lead(team, roster, merged, moves, natures, typechart,
                                               max_turns, our_sets=sets)
                    rem = [x for x in roster if x not in lead]
                    eb4 = list(lead) + rem[:2]
                    combos = search_best_composition(team, eb4, merged, moves, natures,
                                                      typechart, max_turns, our_sets=sets,
                                                      enemy_sets=team_esets)
                    results[tname] = {"mode": "one", "lead": lead, "eb4": eb4,
                                       "combos": combos, "roster": roster}
                prog.progress((i + 1) / len(chosen), f"{tname} done")
            prog.empty()
            st.session_state["search_results"] = results
            st.session_state["search_maxturns"] = max_turns
            st.session_state["search_nroll"] = n_roll

        res = st.session_state.get("search_results")
        if res:
            sets = st.session_state.get("sets", {})
            rows = []
            for tname, d in res.items():
                if d["mode"] == "all":
                    r = d["robust"]
                    if not r:
                        continue
                    b = r["our_bring4"]
                    row = {"Opponent": tname, "Our lead": f"{b[0]}/{b[1]}",
                           "Our back": f"{b[2]}/{b[3]}",
                           "Enemy brings beaten": f"{r['solver_wins']}/{r['solver_total']}",
                           "Clean": "yes" if not r["solver_losses"] else "no"}
                    if "script_wins" in r:
                        row["Vs script"] = f"{r['script_wins']}/{r['script_total']}"
                        row["Vs conventional"] = f"{r['conventional_wins']}/{r['conventional_total']}"
                    rows.append(row)
                else:
                    b = d["combos"][0] if d["combos"] else None
                    if not b:
                        continue
                    rows.append({"Opponent": tname, "Our lead": "/".join(b[0][:2]),
                                  "Our back": "/".join(b[0][2:]),
                                  "Enemy brings beaten": "1 sampled",
                                  "Clean": "yes" if b[1] == "p1" else "no"})
            if rows:
                df_rows = pd.DataFrame(rows)
                nr = st.session_state.get("search_nroll", 0)
                if nr:
                    from matchup_search import evaluate_risk
                    fair_col, adv_col = [], []
                    with st.spinner(f"Rolling {nr} games per matchup..."):
                        for tname, d in res.items():
                            if d["mode"] == "all" and d.get("robust"):
                                b4 = d["robust"]["our_bring4"]
                                we = d["robust"].get("worst_enemy")
                                eb4 = (list(we[0]) + list(we[1])) if we else None
                            else:
                                b4 = d["combos"][0][0] if d.get("combos") else None
                                eb4 = d.get("eb4")
                            if not b4 or not eb4:
                                fair_col.append("-"); adv_col.append("-"); continue
                            mt = st.session_state.get("search_maxturns", 12)
                            f = evaluate_risk(b4, eb4, merged, moves, natures, typechart, mt,
                                               our_sets=sets, n_random=nr, tie_bias=None)
                            a_ = evaluate_risk(b4, eb4, merged, moves, natures, typechart, mt,
                                                our_sets=sets, n_random=nr, tie_bias="p2")
                            fair_col.append(f"{100*f['win_rate']:.0f}%")
                            adv_col.append(f"{100*a_['win_rate']:.0f}%")
                    df_rows["Win % (fair ties)"] = fair_col
                    df_rows["Win % (they win ties)"] = adv_col
                    st.caption("Percentages are against each opponent's hardest bring, with real "
                                "damage rolls, flinches and speed ties. A deterministic win can "
                                "still be a coin toss.")
                st.dataframe(df_rows, width='stretch', hide_index=True)
                st.download_button("Download summary CSV", pd.DataFrame(rows).to_csv(index=False),
                                    "search_summary.csv", "text/csv")

            # --- Usage stats: move usage, damage %, KOs, aggregated across every ---
            # enemy bring-4 for the recommended composition against one opponent.
            all_backs_teams = [tn for tn, d in res.items() if d["mode"] == "all" and d.get("robust")]
            if all_backs_teams:
                st.markdown("### Usage stats")
                st.caption("Move-usage counts, total damage dealt (as %% of the target's max HP, "
                           "summed over every hit landed), and KOs secured, aggregated across "
                           "EVERY enemy bring-4 configuration for the recommended composition -- "
                           "both sides. Re-plays that opponent's games, so it costs about as much "
                           "as the search itself for that opponent.")
                us_tname = st.selectbox("Opponent", all_backs_teams, key="us_tname")
                if st.button("Compute usage stats", key="us_go"):
                    from matchup_search import aggregate_battle_stats
                    import scripted_openings as _so3
                    b4 = res[us_tname]["robust"]["our_bring4"]
                    fl = team_meta.get(us_tname, {}).get("lead")
                    us_esets = {**(st.session_state.get("enemy_sets_override") or {}),
                                **(team_meta.get(us_tname, {}).get("sets") or {})}
                    with st.spinner("Replaying every enemy bring-4..."):
                        stats = aggregate_battle_stats(
                            b4, teams[us_tname], merged, moves, natures, typechart,
                            st.session_state.get("search_maxturns", 12), our_sets=sets,
                            enemy_sets=us_esets, fixed_lead=fl,
                            script_team=us_tname if _so3.all_scripts(us_tname) else None)
                    srows = []
                    for (side, name), s in stats.items():
                        top_moves = sorted(s["moves"].items(), key=lambda x: -x[1])
                        srows.append({
                            "Side": "Us" if side == "p1" else "Them", "Pokemon": name,
                            "KOs": s["kos"], "Damage %": round(s["damage_pct"], 0),
                            "Moves used": ", ".join(f"{m} x{c}" for m, c in top_moves),
                        })
                    srows.sort(key=lambda r: (r["Side"], -r["KOs"]))
                    st.dataframe(pd.DataFrame(srows), width='stretch', hide_index=True)
                    import json as _json
                    json_stats = {f"{side}:{name}": s for (side, name), s in stats.items()}
                    st.download_button("Download stats .json", _json.dumps(json_stats, indent=2),
                                        file_name=f"usage_stats_{us_tname}.json",
                                        mime="application/json", key="us_dl")

            # Per-opening-variant turn-1 breakdown, for fixed-lead/scripted opponents.
            import scripted_openings as _so2
            scripted_chosen = [tn for tn in res if tn in _so2.SCRIPTS and res[tn].get("robust")]
            if scripted_chosen:
                st.markdown("### Full match breakdown by opening variant")
                st.caption("Fixed-lead opponents run a scripted opening with several variants "
                           "(which of your slots it targets), plus two practical T1 lines "
                           "they could take INSTEAD of the script: their best unscripted "
                           "attack+attack ('greedy'), and either of their two Pokemon "
                           "Protecting while the other attacks ('protect'). Each variant is "
                           "played to completion -- the opponent keeps running its script on "
                           "every turn it still has one for (e.g. Perish Trap's turns 2-4), "
                           "falling back to greedy once it's spent -- so you see the whole "
                           "match, not just the opening, for every option side by side.")
                t1_tname = st.selectbox("Opponent", scripted_chosen, key="t1bd_tname")
                if st.button("Show breakdown", key="t1bd_go"):
                    from committed_plan import turn1_breakdown
                    b4 = res[t1_tname]["robust"]["our_bring4"]
                    er = teams[t1_tname]
                    t1_esets = {**(st.session_state.get("enemy_sets_override") or {}),
                                **(team_meta.get(t1_tname, {}).get("sets") or {})}
                    with st.spinner("Playing out every opening variant to completion..."):
                        bd = turn1_breakdown(b4, er, merged, moves, natures, typechart, t1_tname,
                                              our_sets=sets, enemy_sets=t1_esets,
                                              max_turns=st.session_state.get("search_maxturns", 12))
                    labels = {"greedy": "Unscripted: their best attack+attack",
                              "protect0": "Unscripted: one Protects, the other attacks (choice A)",
                              "protect1": "Unscripted: one Protects, the other attacks (choice B)"}
                    for key, d in bd.items():
                        title = labels.get(key, f"Scripted variant {key}")
                        result = {"p1": "WIN", "p2": "LOSS"}.get(d["winner"], d["winner"].upper())
                        with st.expander(f"{title} — {result} (turn {d['turns']})"):
                            st.write("Our turn-1 action:",
                                     [(a[0], a[2], list(a[3])) for a in d["our_t1_action"]])
                            st.write("Their turn-1 action:", d["enemy_t1_action"])
                            st.caption("HP shown is the FINAL state at the end of the match.")
                            hprows = [{"Pokemon": n, "HP": f"{hp}/{mx}"}
                                      for n, (hp, mx) in d["hp_after"].items()]
                            st.dataframe(pd.DataFrame(hprows), width='stretch', hide_index=True)
                            st.code(d["log"])

            # Branching detail: opponent -> enemy bring -> turn-by-turn gameplan.
            st.markdown("### Individual matchups")
            st.caption("There are up to 90 enemy brings per opponent, so they are grouped: "
                       "expand an opponent, then a specific enemy bring, to see that battle.")
            for tname, d in res.items():
                if d["mode"] != "all" or not d["robust"]:
                    continue
                r = d["robust"]
                b4 = r["our_bring4"]
                losses = {(tuple(l), tuple(bk)): (w, t) for l, bk, w, t in r["solver_losses"]}
                with st.expander(f"{tname} — our lead {b4[0]}/{b4[1]}, back {b4[2]}/{b4[3]} "
                                  f"({r['solver_wins']}/{r['solver_total']} beaten)"):
                    if "script_wins" in r:
                        sc1, sc2 = st.columns(2)
                        sc1.metric("Vs script", f"{r['script_wins']}/{r['script_total']}")
                        sc2.metric("Vs conventional (normal 6/6)",
                                   f"{r['conventional_wins']}/{r['conventional_total']}")
                        st.caption("'Vs script' is every game where the opponent ran its scripted "
                                   "opening (or a generic near-script deviation) at least once; "
                                   "'vs conventional' is the plain unscripted greedy 2v2 -- the "
                                   "overall count above blends both together.")
                    cfgs = enemy_configs(d["roster"],
                                          fixed_lead=team_meta.get(tname, {}).get('lead'))
                    # The losses, solved TOGETHER. One fix per loss is one team
                    # per loss; what a player needs is the single change that
                    # turns the whole set around, if there is one.
                    if losses:
                        st.divider()
                        render_joint_salvage(
                            f"lb_{tname}", b4,
                            [list(l) + list(bk) for l, bk in losses],
                            st.session_state.get("search_maxturns", 12), sets)
                        st.divider()
                    fc1, fc2 = st.columns([1, 2])
                    only_losses = fc1.checkbox("Show only losses", value=bool(losses),
                                                key=f"ol_{tname}")
                    page_size = fc2.select_slider(
                        "Battles shown per page", options=[10, 30, 45, 90], value=30,
                        key=f"ps_{tname}",
                        help="All 90 enemy brings are available -- this only controls how "
                             "many are rendered at once, since each is a full battle log.")
                    ff1, ff2 = st.columns(2)
                    filt_lead = ff1.multiselect("Filter to EXACTLY this enemy lead (2)", d["roster"],
                                                 max_selections=2, key=f"fl_{tname}",
                                                 help="Must consist of only these two Pokemon, "
                                                      "not just contain one of them.")
                    filt_back = ff2.multiselect("...and EXACTLY this back (2, optional)", d["roster"],
                                                 max_selections=2, key=f"fb_{tname}",
                                                 help="Must consist of only these two Pokemon, "
                                                      "not just contain one of them.")
                    visible = [(l, bk) for l, bk in cfgs
                               if not (only_losses and (tuple(l), tuple(bk)) not in losses)
                               and (len(filt_lead) != 2 or set(l) == set(filt_lead))
                               and (len(filt_back) != 2 or set(bk) == set(filt_back))]
                    n_pages = max(1, (len(visible) + page_size - 1) // page_size)
                    page = 1
                    if n_pages > 1:
                        page = st.number_input(f"Page (1-{n_pages}, {len(visible)} battles)",
                                                1, n_pages, 1, key=f"pg_{tname}")
                    lo, hi = (page - 1) * page_size, page * page_size
                    shown = 0
                    for lead, back in visible[lo:hi]:
                        key = (tuple(lead), tuple(back))
                        lost = key in losses
                        shown += 1
                        label = (f"{'LOSS' if lost else 'win '} — vs lead "
                                 f"{lead[0]}/{lead[1]} + back {back[0]}/{back[1]}")
                        with st.expander(label):
                            _repl_esets = {**(st.session_state.get("enemy_sets_override") or {}),
                                            **(team_meta.get(tname, {}).get("sets") or {})}
                            # play_scripted_worst_case tries every opening variant this
                            # opponent has (its rehearsed script(s), the generic Protect/
                            # attack alternative, and the plain greedy 2v2 -- see
                            # scripted_openings.all_scripts) and returns whichever was
                            # worst for us, so a fixed-lead team's replay actually shows
                            # its real scripted line playing out turn by turn, not an
                            # unscripted approximation. Degrades to plain greedy for any
                            # opponent with no script.
                            from matchup_search import play_scripted_worst_case
                            w, t, btl, variant_idx = play_scripted_worst_case(
                                b4, list(lead) + list(back), merged, moves, natures, typechart,
                                tname, st.session_state.get("search_maxturns", 12),
                                our_sets=sets, enemy_sets=_repl_esets)
                            om = next((c.name for c in btl.p1.roster
                                       if c.is_mega_pick and c.mega_evolved), None)
                            tm = next((c.name for c in btl.p2.roster
                                       if c.is_mega_pick and c.mega_evolved), None)
                            c1, c2, c3, c4 = st.columns(4)
                            c1.metric("Avg-roll result",
                                      {"p1": "WIN", "p2": "LOSS"}.get(w, w.upper()))
                            c2.metric("Turns", t)
                            c3.metric("Megas", f"{om or '-'} / {tm or '-'}")
                            if variant_idx is not None:
                                vlabel = {"protect0": "generic Protect+attack (A)",
                                          "protect1": "generic Protect+attack (B)"}.get(
                                              variant_idx, f"scripted opening #{variant_idx}")
                                st.caption(f"Worst-case opening: {vlabel}")
                            if btl.speed_ties:
                                c4.metric("Speed ties", len(btl.speed_ties),
                                          help=", ".join(f"{a} vs {bb} (T{tt})"
                                                          for tt, a, bb in btl.speed_ties))
                            else:
                                c4.metric("Speed ties", 0)

                            if st.checkbox("Estimate win probability (slower)",
                                            key=f"pr_{tname}_{lead}_{back}"):
                                from matchup_search import evaluate_risk
                                fair = evaluate_risk(b4, list(lead) + list(back), merged, moves,
                                                      natures, typechart,
                                                      st.session_state.get("search_maxturns", 12),
                                                      our_sets=sets, n_random=40, tie_bias=None)
                                adv = evaluate_risk(b4, list(lead) + list(back), merged, moves,
                                                     natures, typechart,
                                                     st.session_state.get("search_maxturns", 12),
                                                     our_sets=sets, n_random=40, tie_bias="p2")
                                p1c, p2c = st.columns(2)
                                p1c.metric("Win % (fair coin ties)", f"{100*fair['win_rate']:.0f}%",
                                           help=f"{fair['wins']}W-{fair['losses']}L over 40 games "
                                                f"with randomised damage rolls, flinches and ties")
                                p2c.metric("Win % (they win every tie)",
                                           f"{100*adv['win_rate']:.0f}%",
                                           help=f"{adv['wins']}W-{adv['losses']}L -- worst case")
                                if adv['win_rate'] < 0.9:
                                    st.warning("This line is not safe: it depends on rolls or "
                                                "on winning speed ties.")
                            st.caption("The log below uses the average damage roll and no "
                                        "secondary procs -- it is the planned line, not a "
                                        "guaranteed one.")
                            st.code(btl.log.dump())
                            # Take THIS battle apart in the Battle Viewer --
                            # punish check, path explorer, per-matchup salvage
                            # -- instead of rebuilding it there from dropdowns.
                            if st.button("Open in Battle Viewer",
                                          key=f"bv_{tname}_{lead}_{back}",
                                          width='stretch'):
                                send_to_battle_viewer(
                                    b4, list(lead) + list(back),
                                    st.session_state.get("search_maxturns", 12),
                                    label=f"{tname}: {lead[0]}/{lead[1]} + "
                                          f"{back[0]}/{back[1]}")
                                st.success("Sent to the Battle Viewer tab — "
                                           "its punish check, path explorer and "
                                           "salvage now act on this matchup.")
                    if shown == 0:
                        st.success("No losses against any of the 90 enemy brings.")


# ------------------------------------------------------------------ battle
with tab_battle:
    st.subheader("Single battle viewer")
    # A matchup handed over from the Lead/Back search. The panels below all read
    # `bv_last_matchup`, so the hand-off is already live -- this just says so,
    # since otherwise the tab looks unchanged and the button that sent it here
    # looks like it did nothing.
    if st.session_state.get("bv_handoff"):
        hc1, hc2 = st.columns([4, 1])
        hc1.info(f"From the Lead/Back search: **{st.session_state['bv_handoff']}**. "
                 f"The punish check, path explorer and salvage panels below act "
                 f"on it; the pickers are for starting a different battle.")
        if hc2.button("Clear", key="bv_clear_handoff", width='stretch'):
            st.session_state.pop("bv_handoff", None)
            st.rerun()
    team = get_state_team()
    def _lead_back_picker(label, pool, lead_key, back_key):
        """Two linked multiselects (lead/back, 2 each) instead of one flat 4-pick
        list, so which two are the LEAD is explicit and directly controllable --
        not dependent on click order. Session state is sanitised before each
        widget renders: once lead changes, any back selection that's no longer
        valid (now in the lead, or the pool shrank) would otherwise crash the
        multiselect, since Streamlit requires the current selection to be a
        subset of the current options."""
        st.caption(label)
        if lead_key in st.session_state:
            st.session_state[lead_key] = [n for n in st.session_state[lead_key] if n in pool]
        lead = st.multiselect("Lead (2)", pool,
                               default=pool[:2] if len(pool) >= 2 else [],
                               max_selections=2, key=lead_key)
        back_opts = [n for n in pool if n not in lead]
        if back_key in st.session_state:
            st.session_state[back_key] = [n for n in st.session_state[back_key] if n in back_opts]
        back = st.multiselect("Back (2)", back_opts,
                               default=back_opts[:2] if len(back_opts) >= 2 else [],
                               max_selections=2, key=back_key)
        return lead + back

    b1, b2 = st.columns(2)
    with b1:
        our_pool = our_side_pool("bv", teams, all_names) or list(all_names)
        our4 = _lead_back_picker("Our bring-4", our_pool, "bv_our_lead", "bv_our_back")
    with b2:
        their_pool = their_side_pool("bv", teams, all_names)
        # Feeds the scripted-opponent lookup, so it must be a REAL team name
        # or None. A hand-picked six has no script, and inventing a label for
        # it would send a name into all_scripts that means nothing.
        opp = (st.session_state.get("bv_foe_saved")
               if st.session_state.get("bv_foe_source") == "A saved team"
               else None)
        if len(their_pool) < 2:
            st.info("Pick at least two of theirs.")
        their4 = _lead_back_picker(
            "Their bring-4 -- pick a specific enemy lead, and optionally a specific back",
            their_pool or list(all_names), "bv_their_lead", "bv_their_back")
    turns = st.slider("Turn cap", 4, 30, 12, key="bv_turns")
    bv_nash, bv_depth = solver_controls("bv")

    n_roll = st.slider("Rolled games for probability", 0, 200, 60, step=20,
                        help="Re-runs the matchup with real damage rolls (0.85-1.00), "
                             "secondary effects like Rock Slide flinches, and coin-flip "
                             "speed ties. The single deterministic result uses average "
                             "rolls and can hide a matchup that is actually a coin toss.")

    if st.button("Simulate", type="primary") and len(our4) == 4 and len(their4) == 4:
        # play_scripted_worst_case tries every opening variant the opponent team has
        # (rehearsed script(s), generic Protect/attack alternative, plain greedy) and
        # keeps whichever is worst for us -- degrades to plain greedy for a team with
        # no script, so this is safe to call unconditionally.
        from matchup_search import play_scripted_worst_case
        from solver import solver_mode
        # Scoped rather than set globally: Streamlit keeps module state alive
        # across reruns, so assigning the solver globals directly would make the
        # last-used mode silently sticky for every other tab.
        with solver_mode(nash=bv_nash, depth=bv_depth):
            w, t, btl, variant_idx = play_scripted_worst_case(
                our4, their4, merged, moves, natures, typechart, opp, turns,
                our_sets=st.session_state.get("sets", {}))
        ourm = next((c.name for c in btl.p1.roster if c.is_mega_pick and c.mega_evolved), None)
        theirm = next((c.name for c in btl.p2.roster if c.is_mega_pick and c.mega_evolved), None)
        r1, r2, r3 = st.columns(3)
        r1.metric("Result", {"p1": "WIN", "p2": "LOSS"}.get(w, w.upper()))
        r2.metric("Turns", t)
        r3.metric("Mega choice", f"{ourm or '-'} vs {theirm or '-'}")
        if variant_idx is not None:
            vlabel = {"protect0": "generic Protect+attack (A)",
                      "protect1": "generic Protect+attack (B)"}.get(
                          variant_idx, f"scripted opening #{variant_idx}")
            st.caption(f"Worst-case opening: {vlabel}")

        if n_roll:
            from matchup_search import evaluate_risk, evaluate_tie_branches
            # Same solver as the deterministic run above, or the rolled
            # probability would describe a different player.
            with st.spinner(f"Rolling {n_roll} games..."), \
                    solver_mode(nash=bv_nash, depth=bv_depth):
                fair = evaluate_risk(our4, their4, merged, moves, natures, typechart, turns,
                                      our_sets=st.session_state.get("sets", {}),
                                      n_random=n_roll, tie_bias=None)
                adv = evaluate_risk(our4, their4, merged, moves, natures, typechart, turns,
                                     our_sets=st.session_state.get("sets", {}),
                                     n_random=n_roll, tie_bias="p2")
                tb = evaluate_tie_branches(our4, their4, merged, moves, natures, typechart,
                                            turns, our_sets=st.session_state.get("sets", {}),
                                            n_random=0)
            p1, p2, p3 = st.columns(3)
            p1.metric("Win % (fair coin ties)", f"{100*fair['win_rate']:.0f}%",
                       help=f"{fair['wins']}W-{fair['losses']}L-{fair['other']} over {n_roll} games "
                            f"with real damage rolls, flinches and coin-flip ties.")
            p2.metric("Win % (they win all ties)", f"{100*adv['win_rate']:.0f}%",
                       help="Worst case: every speed tie goes against you.")
            flips = tb['we_win_ties']['winner'] != tb['they_win_ties']['winner']
            p3.metric("Depends on a coin flip?", "YES" if flips else "no",
                       help="Whether the deterministic result changes when speed ties are "
                            "forced the other way.")
            ties = tb['we_win_ties'].get('ties') or []
            if ties:
                st.caption("Speed ties this matchup: "
                           + ", ".join(f"{a} vs {b} (turn {t})" for t, a, b in ties))
            if fair['losses']:
                st.warning(f"Loses {fair['losses']} of {n_roll} rolled games -- the single "
                            f"deterministic result above does not tell the whole story.")

        st.code(btl.log.dump())

        st.session_state["bv_last_matchup"] = (our4, their4, turns)

    # --- Line robustness: which turn gets punished? -----------------------
    st.divider()
    st.markdown("**How punishable is this line?** — audit every turn against an "
                "opponent who is actually trying.")
    st.caption("A play that beats the simulated opponent can still be one a good "
               "player punishes on sight. This replays the plan against a "
               "BEST-RESPONDING opponent, drawing their moves from a wider set "
               "than we assume they run, and reports what they gain on each turn. "
               "Low is good; it is the difference between a line that works and a "
               "line that happens to have worked.")
    if st.button("Audit the line", key="bv_audit",
                 disabled=not (len(our4) == 4 and len(their4) == 4)):
        from combatants import make_team
        from battle import Battle
        from solver import build_moveset, build_wide_movesets, solver_mode
        from robustness import describe_action, line_report
        oc = make_team(our4, merged, natures, sets=st.session_state.get("sets", {}))
        ec = make_team(their4, merged, natures)
        ms = {c.name: build_moveset(merged[c.name], moves) for c in oc + ec}
        btl3 = Battle(oc, ec, typechart, moves)
        btl3.movesets = ms
        btl3.wide_movesets = {**ms, **build_wide_movesets(
            [c.name for c in ec], merged, moves)}

        def choose(bt):
            with solver_mode(nash=bv_nash, depth=bv_depth):
                act, _s, _x = solve_best_action(bt, "p1", ms)
            return act

        with st.spinner("Auditing..."):
            rep = line_report(btl3, ms, choose, max_turns=min(turns, 8))

        if not rep.turns:
            st.warning("Could not audit this line.")
        else:
            a1, a2 = st.columns(2)
            a1.metric("Mean exploitability", f"{rep.mean_exploitability:.0f}",
                      help="Average points a best-responding opponent gains per "
                           "turn. ~180 is one Pokemon. Under ~30 is a solid line.")
            a2.metric("Severely punishable turns",
                      f"{rep.severe_count}/{len(rep.turns)}",
                      help="Turns where a good opponent gains more than a third "
                           "of a Pokemon.")
            st.dataframe(pd.DataFrame([
                {"Turn": t.turn,
                 "Our play": describe_action(t.our_action),
                 "They gain": f"{t.exploitability:.0f}",
                 "Punished by": describe_action(t.punisher) if t.punisher else "-"}
                for t in rep.turns
            ]), hide_index=True, width='stretch')
            worst = rep.worst_turn
            if worst and worst.severe:
                st.warning(
                    f"**Turn {worst.turn} is the weak link** — a good opponent "
                    f"gains {worst.exploitability:.0f} points by answering "
                    f"`{describe_action(worst.our_action)}` with "
                    f"`{describe_action(worst.punisher)}`.")
            elif worst:
                st.success(f"No turn is severely punishable. The worst is turn "
                           f"{worst.turn} at {worst.exploitability:.0f} points.")

    # --- Turn-1 equilibrium analysis -------------------------------------
    st.divider()
    st.markdown("**Turn 1 equilibrium** — solve the opening turn as a "
                "simultaneous-move matrix game and show what it actually says.")
    st.caption("The greedy solver can only ever name one move. Solving the turn "
               "gives the whole picture: how often each play should be made, how "
               "much a best-responding opponent gains if you commit to one, and "
               "how hard a decision they are facing.")
    if st.button("Solve turn 1", key="bv_solve_turn",
                 disabled=not (len(our4) == 4 and len(their4) == 4)):
        from combatants import make_team
        from battle import Battle
        from solver import build_moveset
        from turn_game import solve_turn
        our_sets = st.session_state.get("sets", {})
        oc = make_team(our4, merged, natures, sets=our_sets)
        ec = make_team(their4, merged, natures)
        ms = {c.name: build_moveset(merged[c.name], moves) for c in oc + ec}
        btl2 = Battle(oc, ec, typechart, moves)
        btl2.movesets = ms
        with st.spinner(f"Solving (depth {bv_depth})..."):
            sol = solve_turn(btl2, "p1", ms, depth=bv_depth)

        m1, m2, m3 = st.columns(3)
        m1.metric("Equilibrium value", f"{sol.value:+.0f}",
                  help="In heuristic_eval points; ~180 is one Pokemon. Positive "
                       "favours you.")
        m2.metric("Mixed strategy?", "YES" if sol.is_mixed else "no",
                  help=f"Mixing is worth {sol.mixing_gain:+.0f} points over the "
                       f"best single play. A pure strategy is exploitable "
                       f"wherever this says YES.")
        m3.metric("Their decision difficulty", f"{sol.their_decision_difficulty:.0f}",
                  help="Gap between the opponent's best and second-best replies. "
                       "Small means they are guessing; large means they have an "
                       "obvious out. 'Pokemon is in many ways a game about making "
                       "your opponent make difficult decisions.'")

        st.markdown("**How often to make each play**")
        rows = []
        for joint, prob in sol.support():
            desc = " + ".join(
                f"{a.combatant.name} {a.move.name if a.move else a.kind}"
                + (f" -> {a.targets[0].name}"
                   if a.targets and a.targets[0] is not a.combatant else "")
                for a in joint)
            rows.append({"Play": desc, "Frequency": f"{100 * prob:.0f}%"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

        exploit = sol.exploitability_of(sol.maximin_index)
        if exploit is not None:
            st.caption(
                f"Safest single play: **{' + '.join(a.combatant.name for a in sol.maximin_action)}** "
                f"— guarantees {sol.maximin_value:+.0f} whatever they do. "
                f"Committing to it costs {exploit:+.0f} against a best-responding "
                f"opponent versus playing the mix.")
        st.caption(f"Simulated {sol.n_sims} turns to solve this"
                   + ("" if sol.converged else " (did not fully converge)"))

    # --- Punish check: is EVERY turn of our plan safe against EVERY legal response? ---
    st.divider()
    st.markdown("**Punish check** — across the WHOLE match, is there a legal response "
                "(including a switch) that really punishes our planned play, turn by turn?")
    st.caption("E.g. we target their Garchomp with a Dragon move assuming it stays in, but "
               "they switch to a Fairy-type immune to Dragon and we eat a hit instead; or we "
               "split our attacks assuming they Protect one slot, but they Protect the other. "
               "At every turn, tries EVERY legal response of theirs (not just their greedy pick) "
               "and reports the worst one -- does it KO one of ours outright that turn, or does "
               "even our best follow-up still lose one of ours the turn after.")
    last_pc = st.session_state.get("bv_last_matchup")
    if st.button("Check for a punish", disabled=not last_pc, key="punish_go"):
        from matchup_search import full_game_punish_audit
        lo4, lt4, lturns = last_pc
        with st.spinner("Playing the match, checking every turn against every legal response..."):
            pc_res = full_game_punish_audit(lo4, lt4, merged, moves, natures, typechart, lturns,
                                             our_sets=st.session_state.get("sets", {}))
        render_punish_audit(pc_res)

    # --- Path explorer: tie x roll scenario matrix -----------------------------
    st.divider()
    last = st.session_state.get("bv_last_matchup")
    render_path_explorer("bv", last, st.session_state.get("sets", {}))

    # --- Salvage a losing matchup -----------------------------------------------
    st.divider()
    render_salvage("bv", last, st.session_state.get("sets", {}))


# ------------------------------------------------------------------ vs team
with tab_vs:
    st.subheader("Vs a specific team")

    # --- Overnight results browser -------------------------------------
    # The batch tools already computed and cached every audited line. Reading
    # that cache here is far better than recomputing it in the app: it is the
    # SAME numbers the workbook reports, and it is instant.
    with st.expander("Load an overnight run (shortlist / results cache)",
                     expanded=False):
        st.caption("Point this at the files overnight.bat wrote in tools\\. "
                   "The shortlist makes generated teams selectable as YOUR "
                   "team; the results cache lets you read the best line "
                   "against each opponent, turn by turn, with damage.")
        c1, c2 = st.columns(2)
        shortlist_path = c1.text_input(
            "Shortlist JSON", value="../tools/shortlist.json",
            key="vs_shortlist_path",
            help="Written by generate_overnight / overnight.bat. Carries the "
                 "generated rosters and their optimised sets.")
        cache_path = c2.text_input(
            "Results cache JSON", value="../tools/overnight_thorough.json",
            key="vs_cache_path",
            help="The search cache. Same data the .xlsx is built from.")

        st.caption("Or upload them, if the app is not running next to the "
                   "files (a different machine, or a copy someone sent you).")
        u1, u2 = st.columns(2)
        up_shortlist = u1.file_uploader("Shortlist JSON", type="json",
                                        key="vs_up_shortlist")
        up_cache = u2.file_uploader("Results cache JSON", type="json",
                                    key="vs_up_cache")

        if st.button("Load", key="vs_load_overnight"):
            import json as _json
            loaded = {}
            # An upload wins over the path: if you went to the trouble of
            # picking a file, that is the one you meant.
            for label, upload, path in (("shortlist", up_shortlist, shortlist_path),
                                        ("cache", up_cache, cache_path)):
                try:
                    if upload is not None:
                        loaded[label] = _json.loads(upload.getvalue().decode("utf-8"))
                    elif path:
                        with open(path, encoding="utf-8") as fh:
                            loaded[label] = _json.load(fh)
                except (OSError, ValueError, UnicodeDecodeError) as exc:
                    st.warning(f"{label}: {exc}")
            st.session_state["vs_overnight"] = loaded
            if loaded:
                st.success(f"Loaded: {', '.join(loaded)}")

        blob = st.session_state.get("vs_overnight") or {}
        sl = blob.get("shortlist") or {}
        gen_teams = sl.get("teams", sl) if isinstance(sl, dict) else {}
        gen_sets = sl.get("sets", {}) if isinstance(sl, dict) else {}
        if gen_teams:
            pick = st.selectbox("Generated team", ["(none)"] + list(gen_teams),
                                key="vs_gen_pick")
            if pick != "(none)":
                members = gen_teams[pick]
                st.write(" / ".join(members))
                sheet = gen_sets.get(pick) or {}
                if sheet:
                    st.dataframe(
                        [{"Pokemon": n, "Item": spec.get("item"),
                          "Moves": ", ".join(spec.get("moves") or [])}
                         for n, spec in sheet.items()],
                        use_container_width=True, hide_index=True)
                    st.caption("Optimised sets, as simulated.")
                else:
                    st.caption("No optimised sets recorded -- this team was "
                               "rated on usage defaults.")
                if st.button(f"Use {pick} as my team", key="vs_use_gen"):
                    st.session_state["team"] = list(members)
                    st.session_state["team_analysis"] = None
                    st.success(f"{pick} loaded into the Team Builder tab.")
                    st.rerun()

        cache = blob.get("cache") or {}
        rows = [v for v in cache.values()
                if isinstance(v, dict) and v.get("ours")] if cache else []
        if rows:
            st.markdown("**Best line against a specific opponent**")
            teams_in = sorted({r["ours"] for r in rows})
            ours_pick = st.selectbox("Our team", teams_in, key="vs_line_ours")
            opps = sorted({r["theirs"] for r in rows if r["ours"] == ours_pick})
            theirs_pick = st.selectbox("Opponent", opps, key="vs_line_theirs")
            row = next((r for r in rows if r["ours"] == ours_pick
                        and r["theirs"] == theirs_pick), None)

            # Every audited line for this pairing, so a specific LEAD of theirs
            # can be inspected rather than only the recommended one.
            lines = [(cand, lead) for cand in (row.get("candidates") or [])
                     for lead in (cand.get("audit") or [])] if row else []
            if not lines:
                st.info("No audited lines for this pairing "
                        "(the quick tier does not audit lines).")
            else:
                def _label(pair):
                    cand, lead = pair
                    bring = " / ".join(cand.get("bring") or [])
                    theirs = " / ".join(lead.get("enemy_bring")
                                        or lead.get("lead") or [])
                    return (f"[{(lead.get('outcome') or '?').upper()}] "
                            f"vs {theirs}   (we bring {bring})")

                wins_first = sorted(
                    lines,
                    key=lambda pr: (pr[1].get("outcome") != "win",
                                    pr[1].get("mean_exploitability") or 0))
                chosen = st.selectbox("Their bring (winning lines first)",
                                      wins_first, format_func=_label,
                                      key="vs_line_pick")
                cand, lead = chosen
                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Result", (lead.get("outcome") or "?").upper())
                m2.metric("Mean punish",
                          f"{lead.get('mean_exploitability') or 0:.0f}")
                m3.metric("Worst punish",
                          f"{lead.get('worst_exploitability') or 0:.0f}")
                m4.metric("Line value",
                          f"{lead.get('adjusted_value') or 0:.2f}")

                tab_log, tab_turns = st.tabs(["Battle result + log",
                                              "Turn-by-turn analysis"])
                with tab_log:
                    render_line_result(lead, "vs_line")
                with tab_turns:
                    for t in lead.get("turns") or []:
                        render_audited_turn(t)

    st.caption("Check the currently loaded team (Team Builder tab) against any enemy team you "
               "specify right here -- hand-pick 6 Pokemon, or paste a Showdown export "
               "(pokepast.es 'Paste'/'Export' view) -- without saving it to data/teams/ first. "
               "See data/teams/ if you want this enemy team to stick around permanently instead.")
    # --- Team preview: their six are known, their four are not ----------
    # The decision the whole pipeline exists to support, at the one moment you
    # actually make it. Runs the same analysis the overnight search runs, for
    # this one pairing, live.
    with st.expander("Team preview: what do I bring against this team?",
                     expanded=False):
        st.caption("You can see their six. Pick your four and your lead pair "
                   "BEFORE they reveal theirs. This runs the advanced model on "
                   "that decision and reports the one committed plan, its "
                   "record across their brings, and what beats it.")
        pv_pool = our_side_pool("pv", teams, all_names)
        pv_ours = st.multiselect(
            "My six", pv_pool or all_names, max_selections=6, key="pv_ours",
            default=list(pv_pool)[:6] if len(pv_pool) == 6 else [],
            help="Filled in from the source chosen above.")
        pv_theirs, pv_esets, _pv_meta = enemy_side_input(
            "pv", teams, team_meta, all_names, merged,
            label="Their six (from team preview)")
        pv_effort, pv_all = advanced_model_controls("pv")

        if st.button("Find my bring and lead", key="pv_run"):
            if len(pv_ours) != 6 or len(pv_theirs) != 6:
                st.warning("Both sides need six Pokemon.")
            else:
                from preview_plan import plan_against
                with st.spinner("Running the advanced model on this preview..."):
                    st.session_state["pv_plan"] = plan_against(
                        pv_ours, pv_theirs, merged, moves, natures, typechart,
                        effort=pv_effort, audit_all=pv_all,
                        our_sets=st.session_state.get("sets", {}),
                        enemy_sets=pv_esets)

        plan = st.session_state.get("pv_plan")
        if plan:
            st.success("BRING: " + " / ".join(plan["bring"] or []))
            st.caption("Lead with the first two.")
            if not plan.get("audited"):
                st.warning(
                    "Quick strength does no punish analysis -- this is the "
                    "screen's pick, verified only by win count "
                    f"({plan.get('solver_wins')}/{plan.get('solver_total')}). "
                    "Raise the slider for a plan you can trust.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Record", f"{plan['wins']} / {plan['of']}",
                          help="Commit to this bring and lead, and win this "
                               "many of their configurations.")
                c2.metric("Mean punish", f"{plan['punish']:.0f}",
                          help="Points a good player gains per turn. A KO is "
                               "about 180. Lower is better.")
                c3.metric("Severe turns", plan.get("severe_turns") or 0)
                if plan["losing_to"]:
                    st.error("Loses to: " + "; ".join(plan["losing_to"][:8])
                             + ("  ..." if len(plan["losing_to"]) > 8 else ""))
                else:
                    st.success(f"Beats all {plan['of']} of their brings tested.")
                if plan.get("of") and plan["of"] < 90:
                    st.caption(f"Only {plan['of']} of their 90 possible brings "
                               f"were tested. Tick the box above for the full "
                               f"X-of-90 answer.")

                # Every line behind that record, with its battle log. The plan
                # is one number until you can see the games it came from --
                # and "loses to X" is only actionable once you can read HOW.
                lines = plan.get("lines") or []
                if lines:
                    st.markdown("#### The games behind that record")
                    st.caption("One line per enemy bring audited. Losing lines "
                               "first, since those are the ones to look at.")
                    ordered = sorted(
                        lines,
                        key=lambda ln: ((ln.get("outcome") or "").lower() == "win",
                                        -(ln.get("mean_exploitability") or 0)))
                    for i, ln in enumerate(ordered):
                        theirs = " / ".join(ln.get("enemy_bring")
                                            or ln.get("lead") or [])
                        head = (f"[{(ln.get('outcome') or '?').upper()}] "
                                f"vs {theirs}   punish "
                                f"{ln.get('mean_exploitability') or 0:.0f}")
                        with st.expander(head):
                            lt1, lt2 = st.tabs(["Battle result + log",
                                                "Turn-by-turn analysis"])
                            with lt1:
                                render_line_result(ln, f"pv_line_{i}")
                            with lt2:
                                for t in ln.get("turns") or []:
                                    render_audited_turn(t, key=f"pv_{i}_{t.get('turn')}")

    # --- Deep dive: one position, at full strength ----------------------
    # The exhaustive tier is expensive because of BREADTH -- 8 brings against
    # all 90 of their bring-4s. Once you have actually led, that breadth is
    # gone: one bring, one lead, one position. So the same analysis runs here
    # in seconds, and can afford a depth the batch run cannot.
    with st.expander("Deep dive: my answer to a specific lead (after they lead)",
                     expanded=False):
        st.caption("The same advanced model, one notch deeper, once their four "
                   "and their lead are known. "
                   "You have led. They have led. This runs the exhaustive "
                   "tier's analysis on that single position -- full payoff "
                   "matrix every turn, their wider move space, the "
                   "equilibrium solver piloting -- and shows the match.")
        _loaded = our_side_pool("dd", teams, all_names)
        our_bring = st.multiselect(
            "My bring-4 (LEAD FIRST, order matters)", _loaded or all_names,
            max_selections=4, key="dd_ours",
            help="Your six from the Team Builder tab. The first two you pick "
                 "are the pair you actually led with.")
        dd_theirs, dd_esets, dd_meta = enemy_side_input(
            "dd", teams, team_meta, all_names, merged)
        dd_lead, dd_backs = ([], [])
        if len(dd_theirs) == 6:
            dd_lead, dd_backs = enemy_lead_and_backs(
                "dd", dd_theirs, fixed_lead=dd_meta.get("lead"))

        dd3, dd4 = st.columns(2)
        dd_depth = dd3.select_slider(
            "Solver depth", options=[1, 2], value=1, key="dd_depth",
            help="Depth 2 sees one turn further -- the setup or switch-in that "
                 "depth 1 prices as free. Measured ~48x the cost, which is "
                 "affordable for ONE line and is not affordable for a batch. "
                 "Expect seconds at depth 1, minutes at depth 2.")
        dd_turns = dd4.slider("Turn cap", 4, 24, 16, key="dd_turns")

        if dd_depth == 2 and len(dd_backs) > 1:
            st.warning(f"Depth 2 costs ~48x per line, and this is "
                       f"{len(dd_backs)} lines. Pick one back pair above, or "
                       f"expect minutes.")

        if st.button("Analyse this position", key="dd_run"):
            if len(our_bring) != 4:
                st.warning("Pick exactly 4 Pokemon on your side, lead first.")
            elif len(dd_lead) != 2 or not dd_backs:
                st.warning("Pick their six and the two they led with.")
            else:
                from deep_dive import audit_position
                bar = st.progress(0.0, text="Solving every turn...")
                runs = []
                try:
                    for i, back in enumerate(dd_backs):
                        enemy4 = list(dd_lead) + list(back)
                        bar.progress(i / len(dd_backs),
                                     text=f"Line {i + 1} of {len(dd_backs)}: "
                                          f"back {' + '.join(back)}")
                        runs.append((enemy4, audit_position(
                            our_bring, enemy4, merged, moves, natures,
                            typechart, max_turns=dd_turns, depth=dd_depth,
                            our_sets=st.session_state.get("sets", {}),
                            enemy_sets=dd_esets)))
                finally:
                    bar.empty()
                st.session_state["dd_runs"] = runs

        runs = st.session_state.get("dd_runs")
        if runs:
            from deep_dive import summarise
            rows = [(enemy4, summarise(rep), rep) for enemy4, rep in runs]

            # Their back is the half you cannot see, so the summary leads with
            # how the answer VARIES across it: one winning line is not a plan
            # if the other five lose.
            if len(rows) > 1:
                won = sum(1 for _e, info, _r in rows if info["outcome"] == "win")
                s1, s2, s3 = st.columns(3)
                s1.metric("Lines won", f"{won} / {len(rows)}",
                          help="Across every back two they could still be "
                               "holding behind that lead.")
                s2.metric("Mean punish",
                          f"{sum(i['mean_exploitability'] for _e, i, _r in rows) / len(rows):.0f}")
                worst = max(rows, key=lambda t: t[1]["worst_exploitability"])
                s3.metric("Worst single turn",
                          f"{worst[1]['worst_exploitability']:.0f}",
                          help="vs " + " / ".join(worst[0][2:]))
                if won == len(rows):
                    st.success("Beats every back they could have behind that "
                               "lead.")
                elif won == 0:
                    st.error("Loses to every back they could have. The lead "
                             "itself is the problem, not the guess about "
                             "their back.")
                else:
                    st.warning("Depends on their back: "
                               + "; ".join(" + ".join(e[2:]) for e, i, _r
                                           in rows if i["outcome"] != "win")
                               + " beat this.")

            for i, (enemy4, info, rep) in enumerate(
                    sorted(rows, key=lambda t: (t[1]["outcome"] == "win",
                                                -t[1]["mean_exploitability"]))):
                head = (f"[{info['outcome'].upper()}] their back "
                        f"{' + '.join(enemy4[2:])}   punish "
                        f"{info['mean_exploitability']:.0f}")
                # A single line opens expanded -- there is nothing to choose
                # between, so making the user click would just hide the answer.
                with st.expander(head, expanded=len(rows) == 1):
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("Result", info["outcome"].upper())
                    k2.metric("Mean punish", f"{info['mean_exploitability']:.0f}")
                    k3.metric("Worst punish", f"{info['worst_exploitability']:.0f}",
                              f"T{info['worst_turn']}" if info["worst_turn"] else None)
                    k4.metric("Severe turns", info["severe_turns"])
                    if info["outcome"] != "win":
                        st.warning(
                            "This line does not win against an opponent playing "
                            "its equilibrium every turn. The turns below show "
                            "where it goes wrong -- the highest punish is the "
                            "one to change.")
                    dt1, dt2 = st.tabs(["Battle result + log",
                                        "Turn-by-turn analysis"])
                    with dt1:
                        render_line_result(report_as_line(rep), f"dd_line_{i}")
                    with dt2:
                        for t in rep.turns:
                            render_audited_turn(t, key=f"dd_{i}_{t.turn}")

    vs_team = get_state_team()
    if len(vs_team) != 6:
        st.warning("Pick 6 Pokemon in the Team Builder tab first.")
    else:
        enemy_roster, enemy_sets_vs, vs_foe_meta = enemy_side_input(
            "vs", teams, team_meta, all_names, merged,
            label="Enemy team (choose 6)")

        if len(enemy_roster) == 6:
            # Changing opponent must re-derive the widgets that describe THAT
            # opponent. Streamlit keys win over a computed default once they
            # hold a value, so without this the fixed lead and the script stay
            # on whatever the previous preset set them to -- and a stale lead
            # is not even a legal selection for the new roster.
            if st.session_state.get("vs_foe_current") != tuple(enemy_roster):
                st.session_state["vs_foe_current"] = tuple(enemy_roster)
                st.session_state.pop("vs_fixed_lead", None)
                st.session_state.pop("vs_script_team", None)
                st.session_state.pop("vs_filt_lead", None)
                st.session_state.pop("vs_filt_back", None)
            vs_search_mode = st.radio(
                "Search mode",
                ["Quick (sampled toughest lead)", "All enemy brings (90, slower, trustworthy)",
                 "Assume a guaranteed enemy lead (6 configs)"],
                key="vs_search_mode",
                help="'Guaranteed lead' solves the easier problem of finding your best bring "
                     "GIVEN you already know their lead -- e.g. from a scouted team or a real "
                     "read -- testing only the 6 back-pair configs behind that lead instead of "
                     "all 90.")
            fixed_lead_vs = None
            if vs_search_mode == "Assume a guaranteed enemy lead (6 configs)":
                # A preset that declares a fixed lead fills this in: retyping
                # Hard Trick Room's known Incineroar+Farigiraf by hand is a
                # chance to get it wrong for no benefit.
                fixed_lead_vs = st.multiselect(
                    "Guaranteed enemy lead (exactly 2)", enemy_roster,
                    default=[n for n in (vs_foe_meta.get("lead") or [])
                             if n in enemy_roster][:2],
                    max_selections=2, key="vs_fixed_lead")
            max_turns_vs = st.slider("Turn cap", 6, 24, 12, key="vs_turns")
            import scripted_openings as _so_vs
            # Likewise the script: if the preset IS one of the scripted
            # archetypes, it is selected by default rather than left off, which
            # is what silently rated King without its opening.
            _script_options = ["None"] + list(_so_vs.SCRIPTS)
            _script_default = (_script_options.index(vs_foe_meta["name"])
                               if vs_foe_meta.get("name") in _so_vs.SCRIPTS else 0)
            script_team_vs = st.selectbox(
                "This enemy team runs a known scripted opening", _script_options,
                index=_script_default, key="vs_script_team",
                help="Only pick this if the enemy roster you entered above IS that fixed-lead "
                     "archetype (same key Pokemon/names) -- lets results be split into 'vs "
                     "script' and 'vs conventional (normal 6/6)' below, instead of one blended "
                     "number. Filled in automatically when you choose a preset that has one.")
            script_team_vs = None if script_team_vs == "None" else script_team_vs

            from search_effort import TIER_ORDER, relative_cost, tier as _tier
            effort_vs = st.select_slider(
                "Search effort", options=TIER_ORDER, value="standard",
                format_func=lambda k: _tier(k)["label"], key="vs_effort",
                help="How much of the expensive analysis to run. The cheap end "
                     "is the original behaviour; the dear end audits far more "
                     "candidates against far more of their plausible leads.")
            _st = _tier(effort_vs)
            st.caption(f"{_st['blurb']}  (~{relative_cost(effort_vs):.0f}x the "
                       f"quick setting)")

            vs_nash, vs_depth = solver_controls("vs")
            if vs_nash:
                st.warning(
                    "This page sweeps many brings, and the equilibrium solver costs "
                    "~19x per decision at depth 1 (~500x at depth 2). Expect a long "
                    "search — prefer the greedy solver here and use Nash in the "
                    "Battle Viewer to verify a specific matchup.")
            if st.button("Find best response", type="primary", key="vs_go"):
                from matchup_search import search_robust_composition, search_best_composition
                from run_search import find_toughest_lead
                vs_sets = st.session_state.get("sets", {})
                if (vs_search_mode == "Assume a guaranteed enemy lead (6 configs)"
                        and len(fixed_lead_vs or []) != 2):
                    st.error("Pick exactly 2 Pokemon for the guaranteed enemy lead.")
                else:
                    from solver import solver_mode
                    with st.spinner("Searching..."), \
                            solver_mode(nash=vs_nash, depth=vs_depth):
                        if vs_search_mode == "Quick (sampled toughest lead)":
                            lead = find_toughest_lead(vs_team, enemy_roster, merged, moves, natures,
                                                       typechart, max_turns_vs, our_sets=vs_sets)
                            rem = [x for x in enemy_roster if x not in lead]
                            eb4_tested = list(lead) + rem[:2]
                            combos = search_best_composition(
                                vs_team, eb4_tested, merged, moves, natures, typechart, max_turns_vs,
                                our_sets=vs_sets, enemy_sets=enemy_sets_vs)
                            r = None
                            if combos:
                                b4, w, t = combos[0]
                                r = {"our_bring4": b4, "solver_wins": int(w == "p1"), "solver_total": 1,
                                     "solver_losses": [] if w == "p1"
                                     else [(tuple(lead), tuple(rem[:2]), w, t)]}
                        else:
                            fl = (fixed_lead_vs if vs_search_mode ==
                                  "Assume a guaranteed enemy lead (6 configs)" else None)
                            rob = search_robust_composition(
                                vs_team, enemy_roster, merged, moves, natures, typechart, max_turns_vs,
                                our_sets=vs_sets,
                                verify_top=max(1, _st["verify_top"] // 3),
                                rate_robustness=_st["robustness"],
                                robustness_leads=_st["leads"] or 1,
                                robustness_turns=_st["turns"] or 1,
                                enemy_sets=enemy_sets_vs,
                                fixed_lead=fl,
                                enemy_script=_so_vs.script_for(script_team_vs) if script_team_vs
                                else None,
                                script_team=script_team_vs)
                            r = rob[0] if rob else None
                            eb4_tested = None
                    if r is None:
                        st.error("No legal composition found (check your team and the enemy roster).")
                    else:
                        st.session_state["vs_result"] = {
                            "r": r, "enemy_roster": enemy_roster, "enemy_sets": enemy_sets_vs,
                            "mode": vs_search_mode, "max_turns": max_turns_vs,
                            "eb4_tested": eb4_tested if vs_search_mode == "Quick (sampled toughest lead)"
                            else None,
                            "fixed_lead": fixed_lead_vs
                            if vs_search_mode == "Assume a guaranteed enemy lead (6 configs)" else None,
                        }

        vres = st.session_state.get("vs_result")
        vs_matchup = None
        if vres:
            r = vres["r"]
            b4 = r["our_bring4"]
            is_quick = vres["mode"] == "Quick (sampled toughest lead)"
            n_cfg = 6 if vres.get("fixed_lead") else 90
            m1, m2 = st.columns(2)
            m1.metric("Best response", f"{b4[0]}/{b4[1]} + {b4[2]}/{b4[3]}")
            if is_quick:
                m2.metric("Vs their toughest lead", "WIN" if r["solver_wins"] else "LOSS")
            else:
                m2.metric(f"Enemy brings beaten ({n_cfg} configs)",
                          f"{r['solver_wins']}/{r['solver_total']}")
            if "script_wins" in r:
                sc1_vs, sc2_vs = st.columns(2)
                sc1_vs.metric("Vs script", f"{r['script_wins']}/{r['script_total']}")
                sc2_vs.metric("Vs conventional (normal 6/6)",
                              f"{r['conventional_wins']}/{r['conventional_total']}")
                st.caption("'Vs script' is every game where the opponent ran its scripted "
                           "opening (or a generic near-script deviation) at least once; "
                           "'vs conventional' is the plain unscripted greedy 2v2 -- the "
                           "overall count above blends both together.")
            for lead, back, w, t in r.get("solver_losses", [])[:5]:
                st.write(f"LOSES to lead {lead[0]}/{lead[1]} + back {back[0]}/{back[1]} "
                         f"({w} T{t})")

            # Design doc 6a/6b: score against the brings they would PLAUSIBLY
            # pick, rather than uniformly over all 90 (which lets a bring no
            # rational opponent makes drag the number down) or by a win count
            # (which says nothing about how badly the losses go).
            if r.get("exploitability") is not None:
                e1, e2 = st.columns(2)
                e1.metric("Exploitability", f"{r['exploitability']:.0f}",
                          help="What a best-responding opponent gains per turn "
                               "against this bring's best line, across the leads "
                               "they would plausibly bring. Lower is better; "
                               "~180 is one Pokemon. THIS is the robustness "
                               "number -- the win count above is against a fixed "
                               "opponent model.")
                e2.metric("Severely punishable turns",
                          f"{r.get('severe_turns', 0)}/{r.get('rated_turns', 0)}")
                wt = r.get("worst_turn")
                if wt and r.get("hardest_lead"):
                    st.warning(
                        f"**Hardest plausible lead: {' / '.join(r['hardest_lead'])}** "
                        f"— on turn {wt['turn']} they gain {wt['exploitability']:.0f} "
                        f"by answering `{wt['our_play']}` with `{wt['punished_by']}`.")

            if r.get("plausible_brings"):
                with st.expander("Which of their leads are actually credible?"):
                    st.caption(
                        "Good players vary their lead, but they do not pick their "
                        "worst one -- so neither 'worst of all 90' nor a flat win "
                        "count describes what you are up against. Leads are "
                        "weighted by how good they are FOR THEM, and the downside "
                        "score reads the worst third of that weighted "
                        "distribution. An implausible lead keeps a small weight "
                        "rather than being dropped, so a genuine disaster still "
                        "shows up.")
                    d1, d2 = st.columns(2)
                    d1.metric("Downside score", f"{r['downside']:+.0f}",
                              help="Average margin across the worst third of "
                                   "plausible enemy leads. Higher is better; "
                                   "~180 points is one Pokemon.")
                    d2.metric("Worst of all 90 (old measure)",
                              f"{r['worst_margin']:+.0f}",
                              help="Kept for comparison. Dominated by leads a "
                                   "rational opponent would not choose.")
                    st.dataframe(pd.DataFrame([
                        {"Their lead": row["label"],
                         "Likelihood": f"{100 * row['probability']:.1f}%",
                         "Our margin": f"{row['our_margin']:+.0f}"}
                        for row in r["plausible_brings"]
                    ]), hide_index=True, width='stretch')

            if is_quick:
                vs_matchup = (b4, vres["eb4_tested"], vres["max_turns"]) if vres["eb4_tested"] else None
                if st.button("Show full battle log", key="vs_log"):
                    from matchup_search import play_out_worst_case
                    eb4 = vres["eb4_tested"]
                    w, t, btl = play_out_worst_case(
                        b4, eb4, merged, moves, natures, typechart, vres["max_turns"],
                        our_sets=st.session_state.get("sets", {}), enemy_sets=vres["enemy_sets"])
                    st.caption(f"vs enemy {eb4[0]}/{eb4[1]} + {eb4[2]}/{eb4[3]}")
                    st.code(btl.log.dump())
            else:
                # --- Individual matchups: every enemy bring-4 (90, or 6 for a ---
                # guaranteed lead), same format as the Lead/Back Search tab.
                st.markdown("### Individual matchups")
                st.caption(f"All {n_cfg} enemy bring-4s. Expand one to see that battle's full log.")
                losses = {(tuple(l), tuple(bk)): (w, t) for l, bk, w, t in r.get("solver_losses", [])}
                cfgs = enemy_configs(vres["enemy_roster"], fixed_lead=vres.get("fixed_lead"))
                fc1, fc2 = st.columns([1, 2])
                only_losses_vs = fc1.checkbox("Show only losses", value=bool(losses),
                                               key="vs_only_losses")
                page_size_vs = fc2.select_slider(
                    "Battles shown per page", options=[6, 10, 30, 45, 90], value=30, key="vs_page_size",
                    help="This only controls how many are rendered at once, since each is a "
                         "full battle log.")
                ff1_vs, ff2_vs = st.columns(2)
                filt_lead_vs = ff1_vs.multiselect(
                    "Filter to EXACTLY this enemy lead (2)", vres["enemy_roster"],
                    max_selections=2, key="vs_filt_lead",
                    help="Must consist of only these two Pokemon, not just contain one of them.")
                filt_back_vs = ff2_vs.multiselect(
                    "...and EXACTLY this back (2, optional)", vres["enemy_roster"],
                    max_selections=2, key="vs_filt_back",
                    help="Must consist of only these two Pokemon, not just contain one of them.")
                visible_vs = [(l, bk) for l, bk in cfgs
                              if not (only_losses_vs and (tuple(l), tuple(bk)) not in losses)
                              and (len(filt_lead_vs) != 2 or set(l) == set(filt_lead_vs))
                              and (len(filt_back_vs) != 2 or set(bk) == set(filt_back_vs))]
                n_pages_vs = max(1, (len(visible_vs) + page_size_vs - 1) // page_size_vs)
                page_vs = 1
                if n_pages_vs > 1:
                    page_vs = st.number_input(f"Page (1-{n_pages_vs}, {len(visible_vs)} battles)",
                                               1, n_pages_vs, 1, key="vs_page")
                lo_vs, hi_vs = (page_vs - 1) * page_size_vs, page_vs * page_size_vs
                shown_vs = 0
                for lead, back in visible_vs[lo_vs:hi_vs]:
                    key_vs = (tuple(lead), tuple(back))
                    lost_vs = key_vs in losses
                    shown_vs += 1
                    label_vs = (f"{'LOSS' if lost_vs else 'win '} — vs lead "
                                f"{lead[0]}/{lead[1]} + back {back[0]}/{back[1]}")
                    with st.expander(label_vs):
                        from matchup_search import play_out_worst_case
                        w_vs, t_vs, btl_vs = play_out_worst_case(
                            b4, list(lead) + list(back), merged, moves, natures, typechart,
                            vres["max_turns"], our_sets=st.session_state.get("sets", {}),
                            enemy_sets=vres["enemy_sets"])
                        om_vs = next((c.name for c in btl_vs.p1.roster
                                      if c.is_mega_pick and c.mega_evolved), None)
                        tm_vs = next((c.name for c in btl_vs.p2.roster
                                      if c.is_mega_pick and c.mega_evolved), None)
                        vc1_, vc2_, vc3_ = st.columns(3)
                        vc1_.metric("Avg-roll result",
                                    {"p1": "WIN", "p2": "LOSS"}.get(w_vs, w_vs.upper()))
                        vc2_.metric("Turns", t_vs)
                        vc3_.metric("Megas", f"{om_vs or '-'} / {tm_vs or '-'}")
                        st.caption("The log below uses the average damage roll and no secondary "
                                   "procs -- it is the planned line, not a guaranteed one.")
                        st.code(btl_vs.log.dump())
                        if st.button("Use this matchup below (punish/paths/salvage)",
                                     key=f"vs_use_{key_vs}"):
                            st.session_state["vs_last_matchup"] = (b4, list(lead) + list(back),
                                                                    vres["max_turns"])
                            st.success("Set as the active matchup for the sections below.")
                if shown_vs == 0:
                    st.success(f"No losses against any of the {n_cfg} enemy brings.")

            if st.button("Compute usage stats (all their brings)", key="vs_stats"):
                from matchup_search import aggregate_battle_stats
                with st.spinner("Replaying every enemy bring-4..."):
                    vstats = aggregate_battle_stats(
                        b4, vres["enemy_roster"], merged, moves, natures, typechart,
                        vres["max_turns"], our_sets=st.session_state.get("sets", {}),
                        enemy_sets=vres["enemy_sets"], fixed_lead=vres.get("fixed_lead"))
                vsrows = []
                for (side, name), s in vstats.items():
                    top_moves = sorted(s["moves"].items(), key=lambda x: -x[1])
                    vsrows.append({
                        "Side": "Us" if side == "p1" else "Them", "Pokemon": name,
                        "KOs": s["kos"], "Damage %": round(s["damage_pct"], 0),
                        "Moves used": ", ".join(f"{m} x{c}" for m, c in top_moves),
                    })
                vsrows.sort(key=lambda row: (row["Side"], -row["KOs"]))
                st.dataframe(pd.DataFrame(vsrows), width='stretch', hide_index=True)

            # --- Punish check / Path explorer / Salvage, same as Battle Viewer ---
            vs_active_matchup = vs_matchup or st.session_state.get("vs_last_matchup")
            st.divider()
            st.markdown("**Punish check** — across the WHOLE match, is there a legal response "
                       "(including a switch) that really punishes our planned play, turn by turn?")
            st.caption("For the recommended composition's lead/back matchup (or one you picked "
                       "'Use this matchup' on above). Tries EVERY legal response of theirs at "
                       "EVERY turn, not just their greedy pick, and reports the worst one per turn.")
            if st.button("Check for a punish", disabled=not vs_active_matchup, key="vs_punish_go"):
                from matchup_search import full_game_punish_audit
                pmo4, pmt4, pmturns = vs_active_matchup
                with st.spinner("Playing the match, checking every turn against every legal "
                                "response..."):
                    pc_res = full_game_punish_audit(
                        pmo4, pmt4, merged, moves, natures, typechart, pmturns,
                        our_sets=st.session_state.get("sets", {}), enemy_sets=vres["enemy_sets"])
                render_punish_audit(pc_res)
            elif not vs_active_matchup:
                st.caption("Run a search above, or click 'Use this matchup' on a specific "
                           "individual matchup, first.")

            st.divider()
            render_path_explorer("vs", vs_active_matchup, st.session_state.get("sets", {}))

            st.divider()
            render_salvage("vs", vs_active_matchup, st.session_state.get("sets", {}))
