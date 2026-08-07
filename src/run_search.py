"""
VGC Champions M-B team/lead/back search -- local CLI entry point.

USAGE:
    python run_search.py                          # quick mode (default)
    python run_search.py --mode comprehensive       # test every enemy lead pair
    python run_search.py --pool "Incineroar,Farigiraf,Gallade,Hydreigon,Mega Skarmory,Gholdengo"
    python run_search.py --max-turns 15
    python run_search.py --show-top 10              # print top 10 alternatives per team, not just #1

MEGA RULE: at most ONE Pokemon in --pool may actually Mega Evolve (name
starting with "Mega "). You can still bring a *different* mega-capable
species in its base form alongside it (e.g. "Tyranitar", not "Mega
Tyranitar") -- that's legal, it just never transforms. Mega picks start
battle in their BASE form (base stats/ability/typing) and only transform
to the Mega form the turn they're actually sent out -- this matters for
weather: Mega-exclusive weather abilities (Charizard Y's Drought,
Abomasnow's Snow Warning) don't exist until that mega is on the field,
unlike base-ability weather setters (Tyranitar's Sand Stream, Pelipper's
Drizzle) which work from the instant they switch in.

OUTPUT: prints a summary to the console AND writes two files next to this
script: results.xlsx (full transparency -- every combo tested, not just
the winner, plus the turn-by-turn gameplan) and results.html (the same
gameplan data as a readable, clickable local report -- no server needed,
just open it in a browser).
"""
import argparse
import itertools
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import species_data
from species_data import build_merged_dataset, resolve_team_mega_slot
from matchup_search import play_out_pair, play_out_worst_case, search_best_composition, MAX_TURNS
from scripted_openings import script_for, all_scripts
from export_excel import build_workbook
from export_html import build_html

DEFAULT_POOL = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon", "Mega Skarmory", "Gholdengo"]
OUTPUT_DIR = Path(__file__).resolve().parent.parent


def load_teams(merged=None):
    from species_data import load_teams as _lt
    teams, meta = _lt(with_meta=True, merged=merged)
    load_teams.meta = meta
    return teams


def find_toughest_lead(our_pool6, enemy_roster, merged, moves_db, natures, typechart, max_turns,
                        our_sets=None):
    """Find the enemy lead pair that is hardest for us.

    "Hardest" is measured against our BEST available answer, not an arbitrary
    default bring-4. Measuring against a fixed default made unrealistic leads
    (e.g. an opponent leading BOTH of their Mega-capable Pokemon, which wastes
    one of them since only a single Mega can transform) look artificially
    threatening -- they beat one specific weak default, not our best response.

    Uses the fast greedy screener across every one of our candidate lead pairs,
    so this stays cheap.
    """
    from fast_eval import fast_pair_score
    movesets_cache = {}
    worst_lead, worst_best_margin = None, None

    for enemy_lead in itertools.combinations(enemy_roster, 2):
        best_margin = None
        for our_pair in itertools.combinations(our_pool6, 2):
            r = fast_pair_score(our_pair, enemy_lead, merged, moves_db, natures, typechart,
                                 movesets_cache)
            if best_margin is None or r["margin"] > best_margin:
                best_margin = r["margin"]
        # Toughest = the enemy lead where even our best answer does worst.
        if worst_best_margin is None or best_margin < worst_best_margin:
            worst_best_margin, worst_lead = best_margin, enemy_lead
    return worst_lead


def run_quick(team_name, roster, our_pool6, merged, moves_db, natures, typechart, max_turns,
               show_top, our_sets=None, enemy_sets=None):
    print("  Finding their toughest lead pair (vs our best available answer)...")
    toughest = find_toughest_lead(our_pool6, roster, merged, moves_db, natures, typechart, max_turns,
                                    our_sets=our_sets)
    remaining = [m for m in roster if m not in toughest]
    enemy_bring4 = list(toughest) + remaining[:2]
    print(f"  Toughest lead identified: {toughest} (their assumed back: {remaining[:2]})")

    results = search_best_composition(our_pool6, enemy_bring4, merged, moves_db, natures, typechart,
                                       max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
    _print_transparency(results, show_top)

    best = results[0]
    _, _, battle = play_out_worst_case(best[0], enemy_bring4, merged, moves_db, natures, typechart,
                                        max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
    return {
        "toughest_lead": toughest, "enemy_back": remaining[:2],
        "all_combos": results, "gameplan_events": battle.events, "gameplan_log": battle.log.dump(),
    }


def run_comprehensive(team_name, roster, our_pool6, merged, moves_db, natures, typechart, max_turns,
                       show_top, our_sets=None, enemy_sets=None):
    print("  Testing all 15 possible enemy lead pairs (this takes a while)...")
    all_results_by_lead = {}
    for enemy_lead in itertools.combinations(roster, 2):
        remaining = [m for m in roster if m not in enemy_lead]
        enemy_bring4 = list(enemy_lead) + remaining[:2]
        results = search_best_composition(our_pool6, enemy_bring4, merged, moves_db, natures,
                                           typechart, max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
        all_results_by_lead[enemy_lead] = results
        wins = sum(1 for _, w, _ in results if w == "p1")
        best = results[0]
        status = "WIN " if best[1] == "p1" else ("LOSS" if best[1] == "p2" else "DRAW")
        print(f"    vs {enemy_lead}: best = lead {best[0][:2]}, back {best[0][2:]} "
              f"-> {status} (turn {best[2]}) [{wins}/{len(results)} combos win]")

    # Pick the single overall-best combo across ALL enemy leads (most total wins) as "the"
    # recommendation for the Excel/HTML report, and identify their overall toughest lead
    # (the one where our best combo did worst) for the gameplan illustration.
    combo_win_counts = {}
    for enemy_lead, results in all_results_by_lead.items():
        for bring4, winner, turns in results:
            key = tuple(bring4)
            combo_win_counts.setdefault(key, 0)
            if winner == "p1":
                combo_win_counts[key] += 1
    best_overall = max(combo_win_counts, key=combo_win_counts.get)

    def overall_combo_result(enemy_lead):
        for bring4, winner, turns in all_results_by_lead[enemy_lead]:
            if tuple(bring4) == best_overall:
                return winner, turns
        return "timeout", max_turns

    toughest = min(all_results_by_lead.keys(),
                    key=lambda el: (0, overall_combo_result(el)[1]) if overall_combo_result(el)[0] == "p2"
                    else (1, 0))
    remaining = [m for m in roster if m not in toughest]
    enemy_bring4 = list(toughest) + remaining[:2]

    flat_results = sorted(
        [r for results in all_results_by_lead.values() for r in results],
        key=lambda r: (0, r[2]) if r[1] == "p1" else ((1, -r[2]) if r[1] != "p2" else (2, -r[2]))
    )
    print(f"  Overall best combo across all 15 enemy leads: lead {list(best_overall)[:2]}, "
          f"back {list(best_overall)[2:]} (wins {combo_win_counts[best_overall]}/15 enemy leads)")

    _, _, battle = play_out_worst_case(list(best_overall), enemy_bring4, merged, moves_db, natures,
                                  typechart, max_turns, our_sets=our_sets, enemy_sets=enemy_sets)

    # Per-enemy-lead detail, so comprehensive mode can export a full breakdown
    # (every enemy lead, its best answer, and its own turn-by-turn gameplan)
    # rather than only the single toughest one.
    per_lead = {}
    for enemy_lead, results in all_results_by_lead.items():
        if not results:
            continue
        rem = [m for m in roster if m not in enemy_lead]
        eb4 = list(enemy_lead) + rem[:2]
        best_here = results[0]
        _, _, b_here = play_out_worst_case(best_here[0], eb4, merged, moves_db, natures,
                                            typechart, max_turns, our_sets=our_sets,
                                            enemy_sets=enemy_sets)
        per_lead[enemy_lead] = {
            "enemy_bring4": eb4,
            "all_combos": results,
            "best": best_here,
            "wins": sum(1 for _, w, _ in results if w == "p1"),
            "gameplan_events": b_here.events,
            "gameplan_log": b_here.log.dump(),
        }

    return {
        "toughest_lead": toughest, "enemy_back": remaining[:2],
        "all_combos": flat_results, "gameplan_events": battle.events,
        "gameplan_log": battle.log.dump(),
        "per_lead": per_lead,
        "overall_best": list(best_overall),
        "overall_best_wins": combo_win_counts[best_overall],
    }


def _print_transparency(results, show_top):
    print(f"  --- Top {show_top} alternatives (of {len(results)} combos tested) ---")
    for i, (bring4, winner, turns) in enumerate(results[:show_top]):
        status = "WIN " if winner == "p1" else ("LOSS" if winner == "p2" else "DRAW")
        marker = " <== RECOMMENDED" if i == 0 else ""
        print(f"    {i+1}. lead {bring4[:2]}, back {bring4[2:]} -> {status} (turn {turns}){marker}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool", type=str, default=",".join(DEFAULT_POOL),
                     help="Comma-separated list of your 6 Pokemon names (must match mbsmogon.xlsx names exactly)")
    ap.add_argument("--team-file", type=str, default=None,
                     help="Load the team (names + items + movesets) from a team.json written by "
                          "generate_team.py. Overrides --pool and uses the optimised sets.")
    ap.add_argument("--enemy-sets", type=str, default=None,
                     help="Path to a JSON file of enemy set overrides (same {'sets': {name: "
                          "{'item','moves'}}} shape as a team.json's 'sets' block, or a bare "
                          "{name: {...}} dict), e.g. pin a specific enemy Kingambit's exact "
                          "moveset instead of its usage-derived default. Defaults to "
                          "data/enemy_overrides.json if that file exists, else none.")
    ap.add_argument("--all-backs", action="store_true",
                     help="Exhaustive: test every enemy bring-4 (all C(6,2) leads x C(4,2) backs "
                          "= 90 configurations per team), not just one arbitrary back pair. "
                          "Reports the composition with the best WORST case.")
    ap.add_argument("--mode", choices=["quick", "comprehensive"], default="quick",
                     help="quick = test only each team's single toughest lead pair (fast). "
                          "comprehensive = test all 15 possible enemy lead pairs per team (slow, thorough).")
    ap.add_argument("--thorough", action="store_true", help="Alias for --mode comprehensive")
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS + 4,
                     help="Turn cap per simulated game before declaring a timeout/draw")
    ap.add_argument("--team", type=str, default=None,
                     help="Only run against this one team name from teams.csv (default: all teams)")
    ap.add_argument("--show-top", type=int, default=5,
                     help="How many alternative combos to print per team in the console (full list always goes to Excel)")
    ap.add_argument("--out-dir", type=str, default=None,
                     help="Where to write results.xlsx / results.html (default: package root)")
    args = ap.parse_args()

    mode = "comprehensive" if args.thorough else args.mode

    our_sets = {}
    if args.team_file:
        from team_sheet import load_team, describe_sets
        tf = Path(args.team_file)
        if not tf.is_absolute():
            cand = Path(__file__).resolve().parent.parent / tf
            tf = cand if cand.exists() else tf
        if not tf.exists():
            print(f"ERROR: team file not found: {tf}")
            sys.exit(1)
        our_pool6, our_sets = load_team(tf)
        print(f"Loaded team from {tf}:")
        for line in describe_sets(our_pool6, our_sets):
            print(line)
        print()
    else:
        our_pool6 = [x.strip() for x in args.pool.split(",")]

    from team_sheet import load_sets_override
    default_enemy_sets_path = OUTPUT_DIR / "data" / "enemy_overrides.json"
    enemy_sets_path = args.enemy_sets or (str(default_enemy_sets_path)
                                           if default_enemy_sets_path.exists() else None)
    enemy_sets = {}
    if enemy_sets_path:
        enemy_sets = load_sets_override(enemy_sets_path)
        print(f"Loaded enemy set overrides from {enemy_sets_path}: {list(enemy_sets.keys())}\n")

    if len(our_pool6) != 6:
        print(f"ERROR: --pool must have exactly 6 names, got {len(our_pool6)}: {our_pool6}")
        sys.exit(1)
    megas_on_team = [n for n in our_pool6 if n.startswith("Mega ")]
    if len(megas_on_team) > 1:
        print(f"Mega note: this team brings {len(megas_on_team)} Mega-capable Pokemon "
              f"({', '.join(megas_on_team)}).")
        print("  Only one can Mega Evolve per battle, and WHICH one is chosen per matchup --")
        print("  the search tries each and keeps whichever wins that specific matchup, with the")
        print("  other playing in base form. You do not need to reorder anything.")
    elif megas_on_team:
        print(f"Mega note: '{megas_on_team[0]}' will Mega Evolve when sent out.")

    print("Loading data (mbsmogon.xlsx + roster.csv + Showdown static data)...")
    merged, unresolved, moves, natures, typechart = build_merged_dataset()
    if unresolved:
        print(f"WARNING: could not resolve base stats for: {unresolved}")
    dups = getattr(build_merged_dataset, "last_duplicates", {}) or {}
    if dups:
        print("  Note: these names appear on multiple rows in mbsmogon.xlsx; the row holding a")
        print("  Mega Stone was used for the Mega, and the other row was filed as its base form:")
        for k in dups:
            print(f"    {k}")
    for name in our_pool6:
        if name not in merged:
            print(f"ERROR: '{name}' not found in mbsmogon.xlsx. Check spelling (case-sensitive).")
            sys.exit(1)

    teams = load_teams(merged=merged)
    if args.team:
        if args.team not in teams:
            print(f"ERROR: team '{args.team}' not found. Available: {list(teams.keys())}")
            sys.exit(1)
        teams = {args.team: teams[args.team]}

    print(f"Pool: {our_pool6}")
    print(f"Mode: {mode.upper()}")
    print(f"Teams: {list(teams.keys())}\n")

    if args.all_backs:
        from matchup_search import search_robust_composition
        for team_name, roster in teams.items():
            meta = (getattr(load_teams, "meta", {}) or {}).get(team_name, {})
            fl = meta.get("lead")
            n_cfg = 6 if fl else 90
            print(f"=== {team_name} -- {n_cfg} enemy bring-4 configurations"
                  + (f" (FIXED LEAD {fl[0]}/{fl[1]})" if fl else "") + " ===")
            if meta.get("note"):
                print(f"    note: {meta['note']}")
            team_enemy_sets = {**enemy_sets, **(meta.get("sets") or {})}
            t0 = time.time()
            res = search_robust_composition(our_pool6, roster, merged, moves, natures, typechart,
                                             args.max_turns, our_sets=our_sets, verify_top=3,
                                             fixed_lead=fl,
                                             enemy_script=script_for(team_name),
                                             script_team=team_name, enemy_sets=team_enemy_sets)
            if all_scripts(team_name):
                print(f"    (scripted opponent: {len(all_scripts(team_name))} opening "
                      f"variants tested, worst case reported)")
            for r in res:
                b = r["our_bring4"]
                print(f"  lead {b[0]}/{b[1]}, back {b[2]}/{b[3]}: "
                      f"{r['solver_wins']}/{r['solver_total']} enemy brings beaten")
                for lead, back, w, t in r["solver_losses"][:5]:
                    print(f"      LOSES to lead {lead[0]}/{lead[1]} + back {back[0]}/{back[1]} ({w} T{t})")
            print(f"  [{time.time()-t0:.0f}s]\n")
        return

    team_reports = {}
    overall_start = time.time()
    for team_name, roster in teams.items():
        t0 = time.time()
        print(f"=== {team_name} ===")
        try:
            team_meta_here = (getattr(load_teams, "meta", {}) or {}).get(team_name, {})
            team_enemy_sets = {**enemy_sets, **(team_meta_here.get("sets") or {})}
            if mode == "comprehensive":
                rep = run_comprehensive(team_name, roster, our_pool6, merged, moves, natures,
                                         typechart, args.max_turns, args.show_top, our_sets,
                                         team_enemy_sets)
            else:
                rep = run_quick(team_name, roster, our_pool6, merged, moves, natures, typechart,
                                 args.max_turns, args.show_top, our_sets, team_enemy_sets)
            team_reports[team_name] = rep
        except Exception as e:
            print(f"  ERROR while processing {team_name}: {e}")
        print(f"  [{time.time()-t0:.0f}s]\n")

    print(f"Total time: {time.time()-overall_start:.0f}s\n")

    out_dir = Path(args.out_dir) if args.out_dir else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    xlsx_path = out_dir / "results.xlsx"
    html_path = out_dir / "results.html"

    if team_reports:
        build_workbook(team_reports, str(xlsx_path))
        build_html(team_reports, our_pool6, str(html_path), mode)
        print(f"Full transparency report (every combo tested): {xlsx_path}")
        print(f"Visual turn-by-turn gameplan viewer (open in a browser): {html_path}")
    else:
        print("No results generated -- check errors above.")


if __name__ == "__main__":
    main()
