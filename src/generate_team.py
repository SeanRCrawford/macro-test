"""
Generate an optimal 6-Pokemon team from the full pool (rather than
arranging a pool you hand-picked).

USAGE:
    python generate_team.py                       # default settings
    python generate_team.py --pool-size 40         # consider more candidates (slower)
    python generate_team.py --beam-width 60        # wider search (slower, better)
    python generate_team.py --top 5                # report the best 5 teams
    python generate_team.py --verify 3             # re-check the top 3 with the real solver
    python generate_team.py --no-verify            # skip solver verification (fastest)

HOW IT WORKS -- and what the numbers mean:
  Stage 1  filter the ~270-mon dataset to the top `--pool-size` by roster.csv
           Score, minus preferences.csv exclusions, plus any include/prefer
           entries (which are always kept).
  Stage 2  build a pair matrix: every candidate lead pair of ours vs every
           possible enemy lead pair (all C(6,2) from each teams.csv team),
           scored by a FAST greedy playout. This is a screen, not a verdict.
  Stage 3  beam search 2 -> 6 members, scoring teams by simulated coverage
           plus synergy (type cores, the <=2-weaknesses-per-type rule,
           average Score). Coverage models team preview: what matters is
           whether SOME pair of yours handles each threat.
  Stage 4  switch-rescue analysis: for threats your best lead still loses to,
           check whether bringing in a specific bench member flips it, and
           report the HP you have left afterwards so a "rescue" that limps in
           at 10% isn't sold as a fix.
  Stage 5  verification: re-run the top teams through the REAL solver
           (the same engine run_search.py uses) so the headline result isn't
           a screening number.

IMPORTANT: Stage 2/3/4 numbers come from the fast screener, which plays both
sides greedily with no lookahead. Stage 5 is the trustworthy one. If a team
looks great in stage 3 but poor in stage 5, believe stage 5.
"""
import argparse
import itertools
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd
import species_data
from species_data import build_merged_dataset, load_preferences
from team_search import (build_candidate_pool, enemy_pairs_from_teams, build_pair_matrix,
                          beam_search_teams, score_team, find_switch_rescues, team_items,
                          TYPE_CORES, MAX_WEAK_PER_TYPE)


def load_teams():
    from species_data import load_teams as _lt
    teams, meta = _lt(with_meta=True)
    load_teams.meta = meta
    return teams


def print_team_sheet(team, merged):
    rows = team_items(team, merged)
    print("  " + "-" * 96)
    print(f"  {'Pokemon':<20}{'Types':<18}{'Item':<18}{'Ability':<16}{'Nature':<10}{'Score':>7}")
    print("  " + "-" * 96)
    for r in rows:
        print(f"  {r['name']:<20}{r['types']:<18}{r['item'][:17]:<18}"
              f"{r['ability'][:15]:<16}{r['nature']:<10}{r['score']:>7.1f}")
    print("  " + "-" * 96)
    for r in rows:
        ev = r["evs"]
        ev_str = "/".join(str(ev[k]) for k in ["hp", "atk", "def", "spa", "spd", "spe"])
        print(f"  {r['name']:<20} EVs {ev_str:<22} moves: {', '.join(r['moves'])}")
    print()


def verify_with_solver(team, teams, merged, moves, natures, typechart, matrix, enemy_pairs,
                        max_turns=12, all_backs=True, our_sets=None):
    """Re-run this team through the REAL solver.

    all_backs=True (default): test EVERY enemy bring-4 -- all C(6,2) lead pairs
    x C(4,2) back pairs = 90 configurations per opponent. The opponent gets to
    choose their own configuration, so a team only counts as beating an
    opponent if it beats all 90. Sampling one back pair (the old default) let
    compositions look clean while losing to backs never tried.
    """
    from matchup_search import search_best_composition, enemy_configs
    results = {}
    for team_name, roster in teams.items():
        if all_backs:
            # Two-stage: fast-screen every (our config x their config) pairing, then
            # solver-verify the survivor against all 90 enemy bring-4s. Looping the
            # full composition search over all 90 enemy configs instead is 8,100
            # solver battles per opponent -- roughly 15 minutes each.
            from matchup_search import search_robust_composition
            meta_all = getattr(load_teams, "meta", {}) or {}
            fl = (meta_all.get(team_name) or {}).get("lead")
            from scripted_openings import SCRIPTS, script_for
            # Two fixes for a real discrepancy: generation used to verify WITHOUT the
            # opponent's script and WITHOUT its fixed lead, so e.g. King scored 90/90
            # here but 72/90 in the lead/back tab, which does apply the script. A
            # fixed-lead team is also only tested on the lead it actually uses --
            # its other 84 brings are ones it never makes.
            robust = search_robust_composition(team, roster, merged, moves, natures, typechart,
                                                max_turns, our_sets=our_sets, verify_top=1,
                                                fixed_lead=fl,
                                                enemy_script=script_for(team_name)
                                                if team_name in SCRIPTS else None,
                                                script_team=team_name if team_name in SCRIPTS
                                                else None)
            if not robust:
                results[team_name] = None
                continue
            r = robust[0]
            rec = {
                "mode": "all_backs", "total": r["solver_total"], "wins": r["solver_wins"],
                "losses": r["solver_losses"], "our_bring4": r["our_bring4"],
            }
            # Scripted teams (fixed lead + rehearsed opening) additionally require a
            # COMMITTED plan: one turn-1 action that beats every opening variant at
            # worst-case rolls. Beating their brings with a solver that adapts to each
            # variant separately is not the same thing -- you cannot pick your move
            # after seeing theirs.
            from scripted_openings import SCRIPTS
            if team_name in SCRIPTS:
                meta_all = getattr(load_teams, "meta", {}) or {}
                fl = (meta_all.get(team_name) or {}).get("lead")
                if fl:
                    from committed_plan import find_plan
                    eb4 = list(fl) + [x for x in roster if x not in fl][:2]
                    # Search a committed plan for EVERY bring-4/lead-2 of ours, not just
                    # the screener's pick -- passing the full 6 silently tested whatever
                    # happened to be listed first.
                    import itertools as _it
                    desc = per = None
                    ok = False
                    seen = set()
                    for b4 in _it.combinations(team, 4):
                        for l2 in _it.combinations(b4, 2):
                            bk = tuple(x for x in b4 if x not in l2)
                            cand = list(l2) + list(bk)
                            key = tuple(cand)
                            if key in seen:
                                continue
                            seen.add(key)
                            d, pr, good = find_plan(cand, eb4, merged, moves, natures,
                                                     typechart, team_name, max_turns,
                                                     our_sets=our_sets, roll="min")
                            if good:
                                desc, per, ok = d, pr, True
                                rec["plan_bring4"] = cand
                                break
                        if ok:
                            break
                    rec.update({"committed_plan": desc, "plan_ok": ok, "plan_detail": per})
            results[team_name] = rec
        else:
            our_pairs = [p for p in itertools.combinations(team, 2) if p in matrix]
            threats = [(tn, ep) for tn, ep in enemy_pairs if tn == team_name]
            if not threats or not our_pairs:
                results[team_name] = None
                continue
            toughest = min(threats, key=lambda k: max(matrix[p][k] for p in our_pairs))[1]
            rem = [x for x in roster if x not in toughest]
            combos = search_best_composition(team, list(toughest) + rem[:2], merged, moves,
                                              natures, typechart, max_turns, our_sets=our_sets)
            b = combos[0] if combos else None
            results[team_name] = None if not b else {
                "mode": "sampled", "enemy_lead": toughest, "our_bring4": b[0],
                "winner": b[1], "turns": b[2]}
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pool-size", type=int, default=34,
                     help="How many of the top-Score Pokemon to consider (default 34). "
                          "Cost grows ~quadratically: 34 -> ~35s matrix, 50 -> ~80s.")
    ap.add_argument("--beam-width", type=int, default=30, help="Beam search width (default 30)")
    ap.add_argument("--top", type=int, default=3, help="How many final teams to report")
    ap.add_argument("--verify", type=int, default=1,
                     help="How many of the top teams to re-check with the real solver (0 = none)")
    ap.add_argument("--no-verify", action="store_true", help="Skip solver verification entirely")
    ap.add_argument("--sample-backs", action="store_true",
                     help="Verify against ONE sampled enemy back pair instead of all 90 "
                          "bring-4 configurations. Much faster, much less trustworthy.")
    ap.add_argument("--save-team", type=str, default="team.json",
                     help="Where to write the winning team sheet (names + items + moves) so "
                          "run_search can use it via --team-file. Default: team.json")
    ap.add_argument("--optimise-sets", action="store_true",
                     help="Optimise each team member's item and 4 moves against the specific "
                          "Pokemon in teams.csv, instead of using raw usage defaults")
    ap.add_argument("--max-turns", type=int, default=12, help="Turn cap in verification battles")
    ap.add_argument("--deep", action="store_true",
                     help="Verify finalists against EVERY enemy lead with full bring-4 "
                          "(leads AND backs), not just each opponent's toughest lead.")
    args = ap.parse_args()

    print("Loading data...")
    merged, unresolved, moves, natures, typechart = build_merged_dataset()
    prefs = load_preferences()
    teams = load_teams()

    dups = getattr(build_merged_dataset, "last_duplicates", {}) or {}
    if dups:
        print("  Note: these names appear on multiple rows in mbsmogon.xlsx; the row holding a")
        print("  Mega Stone was used for the Mega, and the other row was filed as its base form:")
        for k in dups:
            print(f"    {k}")
    if prefs["exclude"]:
        print(f"  Excluding (preferences.csv): {prefs['exclude']}")
    if prefs["include"]:
        print(f"  Force-including (preferences.csv): {prefs['include']}")
    if prefs["prefer"]:
        print(f"  Preferred (preferences.csv, always kept in candidate pool): {prefs['prefer']}")

    pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs)
    enemy_pairs = enemy_pairs_from_teams(teams)
    our_pair_count = len(list(itertools.combinations(pool, 2)))
    print(f"\nCandidate pool: {len(pool)} Pokemon -> {our_pair_count} of our lead pairs")
    print(f"Enemy threats: {len(enemy_pairs)} possible lead pairs across {len(teams)} teams")
    print(f"Screening {our_pair_count * len(enemy_pairs):,} pair matchups...")

    t0 = time.time()
    matrix = build_pair_matrix(
        pool, enemy_pairs, merged, moves, natures, typechart,
        progress=lambda i, t: print(f"  {i}/{t} pairs ({time.time()-t0:.0f}s)", flush=True))
    print(f"Pair matrix built in {time.time()-t0:.0f}s\n")

    t0 = time.time()
    finals = beam_search_teams(pool, matrix, enemy_pairs, merged, beam_width=args.beam_width,
                                must_include=prefs["include"], prefer=prefs["prefer"])
    print(f"Beam search complete in {time.time()-t0:.1f}s\n")

    if not finals:
        print("ERROR: beam search produced no valid teams.")
        print(f"  Pool had {sum(1 for n in pool if n.startswith('Mega '))} Megas of {len(pool)}.")
        print("  A legal team can field at most 2 Mega picks, so if the candidate pool is")
        print("  almost all Megas there is no valid 6-mon combination. Try a larger")
        print("  --pool-size, or lower min_non_mega_frac in team_search.build_candidate_pool.")
        sys.exit(1)

    # Includes are enforced inside the beam search now. Verify rather than silently
    # falling back -- an unsatisfiable constraint should be reported, not ignored.
    forced = set(prefs["include"])
    if forced:
        ok = [(sc, t) for sc, t in finals if forced.issubset(set(t))]
        if not ok:
            print(f"WARNING: no team could be built containing all of {sorted(forced)}. "
                  f"Check the names match mbsmogon.xlsx exactly, and that they are not "
                  f"also excluded.")
        else:
            finals = ok

    best_sets = {}
    for rank, (sc, team) in enumerate(finals[:args.top], start=1):
        print("=" * 94)
        print(f"TEAM #{rank}   (wins {sc['pairs_won']}/{sc['pairs_total']} enemy lead pairs, "
              f"synergy tiebreak {sc['synergy']:+.1f})")
        print("=" * 94)
        print_team_sheet(team, merged)

        if args.optimise_sets:
            from optimize_sets import optimise_team_unique as optimise_team, enemy_individuals
            enemies = enemy_individuals(teams)
            print(f"  Optimised sets vs the {len(enemies)} distinct Pokemon in teams.csv:")
            opt = optimise_team(team, merged, moves, natures, typechart, enemies)
            print(f"  {'Pokemon':<20}{'Item':<18}Moves")
            for n in team:
                o = opt[n]
                print(f"  {n:<20}{str(o['item'])[:17]:<18}{', '.join(o['moves'])}")
            print("  (item/move optimisation uses 1v1 damage coverage, not full battles --")
            print("   it knows what hits hard, not what survives long enough to use it)")
            if rank == 1:
                best_sets = {n: {"item": opt[n]["item"], "moves": opt[n]["moves"]} for n in team}
            print()

        print(f"  Synergy breakdown (tiebreaker only -- matchups won always rank first):")
        print(f"    Enemy lead pairs beaten: {sc['pairs_won']}/{sc['pairs_total']}   "
              f"(mean margin {sc['coverage']:.1f})")
        print(f"    Type cores matched ({sc['core_bonus']:.2f} weighted): "
              f"{', '.join(sc['matched_cores']) if sc['matched_cores'] else 'none'}")
        if sc["weakness_detail"]:
            print(f"    Types weak >{MAX_WEAK_PER_TYPE}x (rule violations: {sc['weakness_violations']}):")
            for t, mons in sc["weakness_detail"].items():
                print(f"      {t}: {mons}")
        else:
            print(f"    Weakness rule: OK -- no type hit by more than {MAX_WEAK_PER_TYPE} members")
        print(f"    Average effective-stat Score: {sc['avg_score']:.1f}")
        print()

        losing = [(k, v) for k, v in sc["per_enemy"].items() if v <= 0]
        print(f"  Threat check: {len(sc['per_enemy']) - len(losing)}/{len(sc['per_enemy'])} "
              f"enemy lead pairs handled by at least one of our pairs (screening)")
        if losing:
            print(f"  Screening losses ({len(losing)}), running switch-rescue analysis...")
            rescues = find_switch_rescues(team, matrix, enemy_pairs, merged, moves, natures,
                                           typechart)
            shown = 0
            for r in rescues:
                if shown >= 6:
                    print(f"    ... and {len(rescues)-shown} more (see full analysis in code)")
                    break
                tag = f"{r['team']} {r['enemy_pair'][0]}/{r['enemy_pair'][1]}"
                if r["rescues"]:
                    best = r["rescues"][0]
                    print(f"    {tag}: lead {best['lead'][0]}/{best['lead'][1]} + switch to "
                          f"{best['rescue_mon']} -> {best['winner']} "
                          f"(HP left {best['our_hp_left']:.2f} of 4.0)")
                else:
                    print(f"    {tag}: NO switch rescue found (best lead "
                          f"{r['best_lead'][0]}/{r['best_lead'][1]}, margin {r['best_margin']:.0f})")
                shown += 1
        print()

    if finals:
        from team_sheet import save_team
        top_team = finals[0][1]
        out_path = Path(args.save_team)
        if not out_path.is_absolute():
            out_path = Path(__file__).resolve().parent.parent / out_path
        save_team(out_path, top_team, best_sets)
        print(f"Team sheet written to: {out_path}")
        print(f"  Use it with:  ./run.sh --team-file {out_path.name}")
        print()

    if not args.no_verify and args.verify > 0:
        print("=" * 94)
        print(f"SOLVER VERIFICATION (real engine, not the fast screener) -- top {args.verify} team(s)")
        print("=" * 94)
        for rank, (sc, team) in enumerate(finals[:args.verify], start=1):
            print(f"\nTeam #{rank}: {team}")
            t0 = time.time()
            v = verify_with_solver(team, teams, merged, moves, natures, typechart,
                                    matrix, enemy_pairs, args.max_turns,
                                    all_backs=not args.sample_backs)
            for team_name, rec in v.items():
                if rec is None:
                    print(f"  {team_name:<10} -- no result")
                    continue
                if rec.get("mode") == "all_backs":
                    tag = "CLEAN" if not rec["losses"] else f"{len(rec['losses'])} losses"
                    plan = ""
                    if "plan_ok" in rec:
                        plan = ("   PLAN OK" if rec["plan_ok"]
                                else "   NO COMMITTED PLAN -- not viable vs this script")
                    print(f"  {team_name:<10} {rec['wins']}/{rec['total']} enemy bring-4s beaten   {tag}{plan}")
                    if rec.get("plan_ok") and rec.get("committed_plan"):
                        pb = rec.get("plan_bring4")
                        if pb:
                            print(f"       plan lead {pb[0]}/{pb[1]}, back {pb[2]}/{pb[3]}")
                        for d in rec["committed_plan"]:
                            print(f"       T1: {d[0]} -> {d[2]} {list(d[3])}")
                    for lead, back, w, t in rec["losses"][:4]:
                        print(f"       loses to lead {lead[0]}/{lead[1]} + back {back[0]}/{back[1]} ({w} T{t})")
                else:
                    status = "WIN " if rec["winner"] == "p1" else ("LOSS" if rec["winner"] == "p2" else "DRAW")
                    print(f"  {team_name:<10} sampled lead {rec['enemy_lead'][0]}/{rec['enemy_lead'][1]:<16} -> {status} T{rec['turns']}")
            print(f"  [{time.time()-t0:.0f}s]")

    print("\nReminder: coverage/margin numbers above the verification block come from the fast")
    print("screener (greedy, no lookahead). The verification block is the trustworthy one.")


if __name__ == "__main__":
    main()
