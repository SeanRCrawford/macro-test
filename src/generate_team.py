"""
Generate an optimal 6-Pokemon team from the full pool (rather than
arranging a pool you hand-picked).

USAGE:
    python generate_team.py                       # default settings
    python generate_team.py --pool-size 40         # consider more candidates (slower)
    python generate_team.py --beam-width 60        # wider search (slower, better)
    python generate_team.py --top 5                # report the best 5 teams
    python generate_team.py --verify 3             # re-check the top 3 with the real solver
    python generate_team.py --verify 3 --effort standard   # ...and rank them by exploitability
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
  Stage 6  (--effort standard or above) rate each finalist by EXPLOITABILITY --
           how much a best-responding opponent gains against its best line --
           and re-rank by it. Stages 1-5 all measure a team against opponents
           this codebase wrote, which rewards beating our own bot; stage 6 is
           the one that asks whether a good player can punish the team.

IMPORTANT: Stage 2/3/4 numbers come from the fast screener, which plays both
sides greedily with no lookahead. Stage 5 is the trustworthy one. If a team
looks great in stage 3 but poor in stage 5, believe stage 5.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# MUST come before pandas/numpy: OpenBLAS sizes its thread pool once, at load,
# and a pool of workers each starting a full-width pool exhausts memory.
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402
import itertools  # noqa: E402
import time  # noqa: E402

import pandas as pd  # noqa: E402
import species_data
from species_data import build_merged_dataset, load_preferences
from team_search import (build_candidate_pool, enemy_pairs_from_teams, build_pair_matrix,
                          beam_search_teams, score_team, find_switch_rescues, team_items,
                          TYPE_CORES, MAX_WEAK_PER_TYPE)


def load_teams(merged=None):
    from species_data import load_teams as _lt
    teams, meta = _lt(with_meta=True, merged=merged)
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


def quick_script_screen(finals, teams, matrix, merged, moves, natures, typechart,
                         max_turns=10, our_sets=None):
    """Cheap early-disqualify pass against scripted openings (King / Hard Trick
    Room / Perish Trap), meant to run on EVERY beam-search finalist before the
    expensive full verify stage (Stage 5, which checks all 90 enemy bring-4s
    per opponent and is far too slow to run on more than a handful of teams).

    fast_eval's greedy-vs-greedy screen has no idea a scripted opponent's Mega
    Gengar Perish Songs instead of attacking on a given turn -- it can only
    tell you a team "loses to Kingambit's damage output", not "has no plan for
    the actual script" -- so this uses the real solver via
    play_scripted_worst_case, but keeps the cost down by trying only ONE
    sampled enemy bring-4 per scripted opponent (that opponent's fixed lead +
    its first two back members) instead of the full 90, and picks our bring-4
    cheaply from the already-computed pair matrix (free lookup, no extra
    simulation) rather than searching our own C(6,4) bring choices.

    A team that loses EVERY scripted opponent this way (not just one -- a
    single unlucky sampled config isn't proof) is flagged disqualified.

    Returns [(score, team, disqualified, detail), ...], same order as
    `finals`. detail is {team_name: beat_the_script(bool)} for opponents
    tested (empty dict if no scripted opponent is in `teams`).
    """
    from scripted_openings import SCRIPTS
    from matchup_search import play_scripted_worst_case
    meta_all = getattr(load_teams, "meta", {}) or {}
    scripted = [(tn, teams[tn]) for tn in teams if tn in SCRIPTS]

    out = []
    for sc, team in finals:
        detail = {}
        for tn, roster in scripted:
            meta = meta_all.get(tn, {})
            fl = meta.get("lead") or list(roster[:2])
            remaining = [x for x in roster if x not in fl]
            enemy_bring4 = list(fl) + remaining[:2]

            fl_key = next((ep for ep in itertools.combinations(roster, 2)
                           if set(ep) == set(fl)), None)
            our_pairs = list(itertools.combinations(team, 2))
            scored_pairs = [(matrix[p][(tn, fl_key)], p) for p in our_pairs
                             if fl_key is not None and p in matrix and (tn, fl_key) in matrix[p]]
            best_pair = max(scored_pairs, key=lambda x: x[0])[1] if scored_pairs else tuple(team[:2])
            rest = [x for x in team if x not in best_pair]
            our_bring4 = list(best_pair) + rest[:2]

            enemy_sets = meta.get("sets") or None
            w, _t, _b, _idx = play_scripted_worst_case(
                our_bring4, enemy_bring4, merged, moves, natures, typechart, tn,
                max_turns=max_turns, our_sets=our_sets, enemy_sets=enemy_sets)
            detail[tn] = (w == "p1")
        disqualified = bool(detail) and not any(detail.values())
        out.append((sc, team, disqualified, detail))
    return out


_WORLD = None      # per-process dataset for the parallel verification path


def _verify_worker_init():
    global _WORLD
    from species_data import build_merged_dataset, load_teams as _lt
    merged, _usage, moves, natures, typechart = build_merged_dataset()
    teams, meta = _lt(with_meta=True, merged=merged)
    _WORLD = dict(merged=merged, moves=moves, natures=natures,
                  typechart=typechart, teams=teams, meta=meta)


def _verify_one_opponent(job):
    """One (team, opponent) verification. Top-level and plain-typed, because on
    Windows the pool uses spawn and both ends cross a pickle boundary.
    """
    team, team_name, max_turns, our_sets, effort = job
    global _WORLD
    if _WORLD is None:
        _verify_worker_init()
    load_teams.meta = _WORLD["meta"]        # verify_with_solver reads this
    out = verify_with_solver(team, {team_name: _WORLD["teams"][team_name]},
                             _WORLD["merged"], _WORLD["moves"], _WORLD["natures"],
                             _WORLD["typechart"], {}, [], max_turns=max_turns,
                             all_backs=True, our_sets=our_sets, effort=effort)
    return team_name, out.get(team_name)


def verify_with_solver(team, teams, merged, moves, natures, typechart, matrix, enemy_pairs,
                        max_turns=12, all_backs=True, our_sets=None, effort=None,
                        jobs=1):
    """Re-run this team through the REAL solver.

    all_backs=True (default): test EVERY enemy bring-4 -- all C(6,2) lead pairs
    x C(4,2) back pairs = 90 configurations per opponent. The opponent gets to
    choose their own configuration, so a team only counts as beating an
    opponent if it beats all 90. Sampling one back pair (the old default) let
    compositions look clean while losing to backs never tried.

    `effort` is a search_effort tier name. Anything above "quick" additionally
    rates each opponent by EXPLOITABILITY -- how much a best-responding player
    gains against our best line. Without it this function reports only wins
    against our own scripted opponent, which is the measure the redesign
    established as biased: a generated team can beat 90/90 configurations and
    still be one a good player dismantles, because the simulated opponent never
    tries the punish.
    """
    from search_effort import tier
    settings = tier(effort) if effort else None
    rate = bool(settings and settings["robustness"])

    # Opponents are independent, so the (team x opponent) verification is the
    # parallel unit. Guarded on all_backs because the sampled path is already
    # cheap and shares the caller's pair matrix, which does not cross processes.
    if jobs > 1 and all_backs and len(teams) > 1:
        import concurrent.futures as cf
        results = {}
        with cf.ProcessPoolExecutor(max_workers=jobs,
                                    initializer=_verify_worker_init) as pool:
            futures = [pool.submit(_verify_one_opponent,
                                   (list(team), name, max_turns, our_sets, effort))
                       for name in teams]
            for future in cf.as_completed(futures):
                name, rec = future.result()
                results[name] = rec
        return results
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
            team_enemy_sets = (meta_all.get(team_name) or {}).get("sets") or None
            from scripted_openings import SCRIPTS, script_for
            # Two fixes for a real discrepancy: generation used to verify WITHOUT the
            # opponent's script and WITHOUT its fixed lead, so e.g. King scored 90/90
            # here but 72/90 in the lead/back tab, which does apply the script. A
            # fixed-lead team is also only tested on the lead it actually uses --
            # its other 84 brings are ones it never makes.
            robust = search_robust_composition(team, roster, merged, moves, natures, typechart,
                                                max_turns, our_sets=our_sets,
                                                verify_top=settings["verify_top"] if rate else 1,
                                                fixed_lead=fl,
                                                enemy_script=script_for(team_name)
                                                if team_name in SCRIPTS else None,
                                                script_team=team_name if team_name in SCRIPTS
                                                else None, enemy_sets=team_enemy_sets,
                                                rate_robustness=rate,
                                                robustness_leads=(settings["leads"] or 1)
                                                if rate else 3,
                                                robustness_turns=(settings["turns"] or 1)
                                                if rate else 5)
            if not robust:
                results[team_name] = None
                continue
            r = robust[0]
            rec = {
                "mode": "all_backs", "total": r["solver_total"], "wins": r["solver_wins"],
                "losses": r["solver_losses"], "our_bring4": r["our_bring4"],
                "exploitability": r.get("exploitability"),
                # The win-quality numbers, not just punishability. Omitting
                # them here made generation report "adj 0.00" for every team
                # while the search reported real values for the same rating --
                # the fields simply never crossed this boundary.
                "adjusted_win_rate": r.get("adjusted_win_rate"),
                "robust_win_rate": r.get("robust_win_rate"),
                "reliable_wins": r.get("reliable_wins"),
                "outcomes": r.get("outcomes"),
                "severe_turns": r.get("severe_turns"),
                "hardest_lead": r.get("hardest_lead"),
                "worst_turn": r.get("worst_turn"),
            }
            # Scripted teams (fixed lead + rehearsed opening) are checked the SAME way as
            # any other opponent: search_robust_composition above already plays their
            # script (and the generic Protect/attack alternatives -- see
            # scripted_openings.all_scripts) against every one of our bring-4/lead-2
            # combos, letting OUR side adapt each turn via the real solver. Only the
            # opponent is scripted -- you genuinely do see their move before choosing
            # yours on every turn after the first, so there's no need for a separate,
            # stricter "one turn-1 action must work blind" requirement on top: solver_wins
            # == solver_total already means the team survives every scripted line as well
            # as the conventional 90.
            results[team_name] = rec
        else:
            our_pairs = [p for p in itertools.combinations(team, 2) if p in matrix]
            threats = [(tn, ep) for tn, ep in enemy_pairs if tn == team_name]
            if not threats or not our_pairs:
                results[team_name] = None
                continue
            toughest = min(threats, key=lambda k: max(matrix[p][k] for p in our_pairs))[1]
            rem = [x for x in roster if x not in toughest]
            meta_all = getattr(load_teams, "meta", {}) or {}
            team_enemy_sets = (meta_all.get(team_name) or {}).get("sets") or None
            combos = search_best_composition(team, list(toughest) + rem[:2], merged, moves,
                                              natures, typechart, max_turns, our_sets=our_sets,
                                              enemy_sets=team_enemy_sets)
            b = combos[0] if combos else None
            results[team_name] = None if not b else {
                "mode": "sampled", "enemy_lead": toughest, "our_bring4": b[0],
                "winner": b[1], "turns": b[2]}
    return results


def leaderboard_summary(results):
    """Roll verify_with_solver's per-opponent-team results up into the column
    the user filters/sorts a team leaderboard by: "perfect vs every opponent"
    -- beats every one of the 90 bring-4s for every opponent team
    (rec['wins'] == rec['total']), scripted opponents included on equal
    footing. A scripted opponent's own 'wins'/'total' already came from
    playing its rehearsed script -- plus the generic Protect/attack
    alternatives, see scripted_openings.all_scripts -- against every one of
    our bring-4/lead-2 combos, with our side adapting each turn via the real
    solver (only the opponent is scripted; you do see their move before
    choosing yours on every turn after the first), so there's no separate
    "specials" check needed on top.

    Only meaningful for results produced with all_backs=True (mode=='all_backs'
    recs) -- a 'sampled' mode result has no wins/total to roll up and is
    skipped, same as a None (no result) entry.

    Returns {"conventional_wins": int, "conventional_total": int,
             "perfect_conventional": bool}.
    """
    conv_wins = conv_total = 0
    for rec in results.values():
        if not rec or rec.get("mode") != "all_backs":
            continue
        conv_wins += rec["wins"]
        conv_total += rec["total"]
    return {
        "conventional_wins": conv_wins, "conventional_total": conv_total,
        "perfect_conventional": conv_total > 0 and conv_wins == conv_total,
    }


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
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                     help="verify N opponents in parallel (0 = one per CPU core). "
                          "Each worker loads the dataset once (~14s) and reuses "
                          "it. Budget roughly 1GB of RAM per worker.")
    ap.add_argument("--effort", default="quick",
                     help="Verification depth: quick / standard / thorough / exhaustive. "
                          "Anything above 'quick' also rates each generated team by "
                          "EXPLOITABILITY -- how much a best-responding opponent gains "
                          "against its best line -- and re-ranks the finalists by it. "
                          "Win counts alone are a biased measure of team strength, so "
                          "'quick' answers 'does it beat our bot' and the others answer "
                          "'does it hold up against a good player'. Slower.")
    ap.add_argument("--deep", action="store_true",
                     help="Verify finalists against EVERY enemy lead with full bring-4 "
                          "(leads AND backs), not just each opponent's toughest lead.")
    ap.add_argument("--script-screen", action="store_true",
                     help="Before the expensive verify stage, cheaply check EVERY beam-search "
                          "finalist against scripted opponents (King / Hard Trick Room / "
                          "Perish Trap) with the real solver, one sampled enemy config each. "
                          "Finalists that lose ALL of them are dropped, so a team with no plan "
                          "against a scripted opening doesn't waste a --verify slot or make "
                          "the final report.")
    ap.add_argument("--generations", type=str, default=None,
                     help="Restrict the candidate pool to these generations, e.g. '3' or "
                          "'1-5' or '1,3,5'. A form (Mega, regional, ...) counts as its base "
                          "species' generation -- Mega Lucario is gen 4, Arcanine-Hisui is "
                          "gen 1. Default: no restriction (all generations).")
    ap.add_argument("--matrix-cache", type=str, default=None,
                     help="Path to save/load the pair matrix (e.g. data/matrix_cache.pkl). "
                          "Building it is the expensive step -- if the file exists and "
                          "matches this run's candidate pool and enemy teams, it's loaded "
                          "instead of rebuilt, so you can re-run beam search (different "
                          "--beam-width, --top, preferences.csv edits, ...) against a large "
                          "pool many times without re-paying that cost. Rebuilt and "
                          "overwritten automatically the moment the pool or enemy teams "
                          "change.")
    args = ap.parse_args()
    args.jobs, _jobs_warning = blas_limits.workers_advice(args.jobs)
    if _jobs_warning:
        print(f"WARNING: {_jobs_warning}")

    print("Loading data...")
    merged, unresolved, moves, natures, typechart = build_merged_dataset()
    prefs = load_preferences()
    teams = load_teams(merged=merged)

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

    allowed_gens = None
    if args.generations:
        from species_data import parse_generations, build_generation_map, load_showdown_static
        allowed_gens = parse_generations(args.generations)
        pokedex, _, _, _ = load_showdown_static()
        gen_map = build_generation_map(merged.keys(), pokedex)
        print(f"  Restricting to generation(s): {sorted(allowed_gens)}")
        pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs,
                                     allowed_generations=allowed_gens, generation_map=gen_map)
    else:
        pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs)
    enemy_pairs = enemy_pairs_from_teams(teams)
    our_pair_count = len(list(itertools.combinations(pool, 2)))
    print(f"\nCandidate pool: {len(pool)} Pokemon -> {our_pair_count} of our lead pairs")
    print(f"Enemy threats: {len(enemy_pairs)} possible lead pairs across {len(teams)} teams")
    print(f"Screening {our_pair_count * len(enemy_pairs):,} pair matchups...")

    matrix = None
    cache_path = Path(args.matrix_cache) if args.matrix_cache else None
    if cache_path and cache_path.exists():
        import pickle
        with open(cache_path, "rb") as f:
            cached = pickle.load(f)
        if cached.get("pool") == pool and cached.get("enemy_pairs") == enemy_pairs:
            matrix = cached["matrix"]
            print(f"Loaded cached pair matrix from {cache_path} ({len(matrix)} of our pairs)\n")
        else:
            print(f"Cached matrix at {cache_path} doesn't match this pool/enemy-team set "
                  f"-- rebuilding.")

    if matrix is None:
        t0 = time.time()
        matrix = build_pair_matrix(
            pool, enemy_pairs, merged, moves, natures, typechart,
            progress=lambda i, t: print(f"  {i}/{t} pairs ({time.time()-t0:.0f}s)", flush=True))
        print(f"Pair matrix built in {time.time()-t0:.0f}s\n")
        if cache_path:
            import pickle
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_path, "wb") as f:
                pickle.dump({"pool": pool, "enemy_pairs": enemy_pairs, "matrix": matrix}, f)
            print(f"Saved pair matrix to {cache_path}\n")

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

    if args.script_screen:
        t0 = time.time()
        screened = quick_script_screen(finals, teams, matrix, merged, moves, natures, typechart,
                                        max_turns=args.max_turns, our_sets=None)
        survivors = [(sc, t) for sc, t, dq, _d in screened if not dq]
        dropped = [(t, d) for _sc, t, dq, d in screened if dq]
        print(f"Script screen: {len(dropped)}/{len(finals)} finalists always lose a scripted "
              f"opening, dropped [{time.time()-t0:.0f}s]")
        for t, d in dropped[:10]:
            print(f"  DROPPED {list(t)}: loses {', '.join(k for k, v in d.items() if not v)}")
        if survivors:
            finals = survivors
        else:
            print("WARNING: every finalist always loses a scripted opening -- keeping the "
                  "original list rather than reporting nothing.")

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
        from search_effort import relative_cost, tier
        settings = tier(args.effort)
        rating_on = settings["robustness"]
        print("=" * 94)
        print(f"SOLVER VERIFICATION (real engine, not the fast screener) -- top {args.verify} team(s)")
        print(f"effort: {settings['label']} (~{relative_cost(args.effort):.0f}x quick) -- "
              f"{'ranked by exploitability' if rating_on else 'win counts only'}")
        print("=" * 94)
        rated_teams = []
        for rank, (sc, team) in enumerate(finals[:args.verify], start=1):
            print(f"\nTeam #{rank}: {team}")
            t0 = time.time()
            v = verify_with_solver(team, teams, merged, moves, natures, typechart,
                                    matrix, enemy_pairs, args.max_turns,
                                    all_backs=not args.sample_backs,
                                    effort=args.effort, jobs=args.jobs)
            scores = [rec["exploitability"] for rec in v.values()
                      if rec and rec.get("exploitability") is not None]
            if scores:
                rated_teams.append((sum(scores) / len(scores), team, v))
            for team_name, rec in v.items():
                if rec is None:
                    print(f"  {team_name:<10} -- no result")
                    continue
                if rec.get("mode") == "all_backs":
                    tag = "CLEAN" if not rec["losses"] else f"{len(rec['losses'])} losses"
                    exp = rec.get("exploitability")
                    exp_tag = (f"   exploitability {exp:.0f}"
                               f" ({rec.get('severe_turns') or 0} severe)") if exp is not None else ""
                    print(f"  {team_name:<10} {rec['wins']}/{rec['total']} enemy bring-4s beaten   {tag}{exp_tag}")
                    for lead, back, w, t in rec["losses"][:4]:
                        print(f"       loses to lead {lead[0]}/{lead[1]} + back {back[0]}/{back[1]} ({w} T{t})")
                    wt = rec.get("worst_turn")
                    if wt:
                        print(f"       worst turn T{wt['turn']}: they gain "
                              f"{wt['exploitability']:.0f} -- {wt['our_play']}")
                        print(f"         answered by {wt['punished_by']}")
                else:
                    status = "WIN " if rec["winner"] == "p1" else ("LOSS" if rec["winner"] == "p2" else "DRAW")
                    print(f"  {team_name:<10} sampled lead {rec['enemy_lead'][0]}/{rec['enemy_lead'][1]:<16} -> {status} T{rec['turns']}")
            print(f"  [{time.time()-t0:.0f}s]")

        # Re-rank the finalists by punishability. The beam search that produced
        # them ranks on coverage and synergy heuristics, and the block above
        # ranks on wins against our own opponent model -- neither answers "how
        # much does a good player get for free against this team".
        if rated_teams:
            rated_teams.sort(key=lambda t: t[0])
            print("\n" + "=" * 94)
            print("GENERATED TEAMS RANKED BY HOW PUNISHABLE THEY ARE (lower is better)")
            print("=" * 94)
            for i, (mean, team, v) in enumerate(rated_teams, start=1):
                severe = sum((rec.get("severe_turns") or 0) for rec in v.values() if rec)
                worst = max((rec for rec in v.values()
                             if rec and rec.get("exploitability") is not None),
                            key=lambda r: r["exploitability"], default=None)
                worst_name = next((n for n, rec in v.items() if rec is worst), None)
                wins = sum((rec.get("wins") or 0) for rec in v.values() if rec)
                total = sum((rec.get("total") or 0) for rec in v.values() if rec)
                print(f"{i}. {mean:7.1f}   {severe:>2} severe   "
                      f"{wins}/{total} won   {', '.join(team)}")
                if worst_name:
                    print(f"          worst matchup: {worst_name} "
                          f"({worst['exploitability']:.0f})")
                # Exploitability is measured against the equilibrium of each
                # turn, so a LOST position scores near zero: there is nothing
                # left for the opponent to gain. Low exploitability plus a poor
                # win count means "played a bad position well", not "good team",
                # and reading it as the latter would pick exactly the wrong
                # team. Shown per team rather than as a footnote.
                lost = [n for n, rec in v.items()
                        if rec and rec.get("total") and not rec.get("wins")]
                if lost:
                    print(f"          NOTE: wins nothing vs {', '.join(lost)} -- "
                          f"a lost position rates as unpunishable, so read the "
                          f"rating for these alongside the win count")

    print("\nReminder: coverage/margin numbers above the verification block come from the fast")
    print("screener (greedy, no lookahead). The verification block is the trustworthy one.")


if __name__ == "__main__":
    main()
