"""Swap a rated team's worst member, re-rate, keep the change if it helped.

    python substitute.py --rosters shortlist.json --team gen01 --rounds 2
    python substitute.py --members "Incineroar,Farigiraf,..." --vs "Big 6"

THE GAP THIS CLOSES (WORKFLOW.md §4). Generation stopped at "here is a rated
team". The Plan sheet already names the brings that beat each team, and nothing
ever used that: no stage took a team, removed the member that was not earning
its slot, and asked whether a different Pokemon rates better.

This is that loop, and it is hill climbing on the REAL objective rather than on
another heuristic:

    propose cheaply (the screener)  ->  accept expensively (the full rating)

Each round it picks the members with the least evidence for their slot -- first
"was this member even in the bring we committed to against any opponent", then
how much the team's screened coverage degrades without it -- proposes
replacements aimed at the opponents the team is actually failing, and then rates
every proposal with `roster_rating.rate_team`: the same audit, the same effort
tier and the same cache stage 1 uses. A swap is kept only if the adjusted win
rate improves (exploitability breaks ties), which is exactly the ranking stage 1
sorts by. Nothing is ever accepted on the screener's opinion.

Cost is honest and unavoidable: one accepted step costs `--per-round` full team
ratings, minutes each. Everything is cached under the same key stage 1 uses, so
an interrupted run resumes, and a team either tool has already rated is never
rated twice.

Output is a shortlist-format JSON (`--out`), so the result chains straight into
the deep search:

    search_teams.py --rosters substituted.json --effort thorough --audit-all
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
# MUST come before anything that reaches numpy/pandas -- OpenBLAS sizes its
# thread pool once, at load. Every import below this line is ordered, not sorted.
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import _harness  # noqa: E402,F401  (adds ../src to sys.path)

import roster_rating  # noqa: E402
import substitution  # noqa: E402
from search_effort import TIER_ORDER, ResultCache  # noqa: E402
from species_data import load_preferences  # noqa: E402
from team_search import build_candidate_pool, enemy_pairs_from_teams  # noqa: E402

DEFAULT_CACHE = "generate_cache.json"


def _load_roster(args, world):
    """The six we start from, plus its sets, from --members or --rosters."""
    if args.members:
        team = [n.strip() for n in args.members.split(",") if n.strip()]
        if len(team) != 6:
            raise SystemExit(f"--members needs 6 names, got {len(team)}")
        unknown = [n for n in team if n not in world["merged"]]
        if unknown:
            raise SystemExit(f"not in the dataset: {', '.join(unknown)}")
        return args.team or "custom", team, None

    if not args.rosters:
        raise SystemExit("give either --members or --rosters")
    with open(args.rosters, encoding="utf-8") as fh:
        blob = json.load(fh)
    teams = blob.get("teams", blob)
    sets = blob.get("sets", {})
    name = args.team or next(iter(teams))
    # Stage 1's rank numbers are how the rest of the pipeline refers to a team
    # ("--pick 6"), so accept them here too rather than making the caller know
    # that rank 6 is spelled gen06.
    if name.isdigit():
        name = f"gen{int(name):02d}"
    if name not in teams:
        raise SystemExit(f"{name} is not in {args.rosters} "
                         f"(has {', '.join(list(teams)[:8])}...)")
    return name, list(teams[name]), (sets.get(name) or None)


def _pool_for(args, world, team):
    """The Pokemon eligible to come IN, always including the incumbents.

    Same construction as generation's candidate pool, so the substitution loop
    searches the same space the beam did -- with one addition: the team's own
    members are kept even if a generation restriction or a small --pool-size
    would have excluded them, because dropping a member is a decision for the
    loop to make on evidence, not something the pool should force.
    """
    prefs = load_preferences()
    merged = world["merged"]
    if args.generations:
        from species_data import (build_generation_map, load_showdown_static,
                                  parse_generations)
        allowed = parse_generations(args.generations)
        pokedex, _, _, _ = load_showdown_static()
        gen_map = build_generation_map(merged.keys(), pokedex)
        pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs,
                                    allowed_generations=allowed,
                                    generation_map=gen_map)
    else:
        pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs)
    return pool + [n for n in team if n not in pool]


def _matrix_base(path, pool, enemy_pairs, fresh=False):
    """Reuse generation's pickled pair matrix where it is still valid.

    A row is one of OUR pairs against every enemy pair, so what has to match is
    the ENEMY pair list -- the pool only decides which rows happen to be
    present, and `PairMatrix` computes the missing ones on demand. Restricting
    with --vs therefore just means more of the matrix is computed here.
    """
    if not path or fresh or not os.path.exists(path):
        return None
    try:
        import pickle
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
    except Exception:
        return None
    if blob.get("enemy_pairs") != enemy_pairs:
        return None
    return blob.get("matrix")


def _optimise(team, world, enemies):
    from optimize_sets import optimise_team_unique
    opt = optimise_team_unique(list(team), world["merged"], world["moves"],
                               world["natures"], world["typechart"], enemies)
    return {n: {"item": opt[n]["item"], "moves": opt[n]["moves"]} for n in team}


def _fmt(record):
    adj = record.get("adjusted_win_rate")
    expl = record.get("exploitability")
    return (f"adj {adj:.3f}" if adj is not None else "adj   n/a") + \
           ("  punish " + (f"{expl:6.1f}" if expl is not None else "   n/a")) + \
           f"  {record.get('wins', 0)}/{record.get('total', 0)} won" + \
           (f"  [{record['skipped']}]" if record.get("skipped") else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rosters", default=None,
                    help="a shortlist.json (or any {name: [six]} file) to take "
                         "the starting team from")
    ap.add_argument("--team", default=None,
                    help="which team in --rosters to improve (default: the first)")
    ap.add_argument("--members", default=None,
                    help="six comma-separated names, instead of --rosters")
    ap.add_argument("--rounds", type=int, default=2,
                    help="how many swaps to attempt, one accepted swap per "
                         "round. The loop stops early when a round finds no "
                         "improvement.")
    ap.add_argument("--per-round", type=int, default=4,
                    help="how many candidate rosters get FULLY RATED each "
                         "round. This is the cost dial: each one is a complete "
                         "team rating.")
    ap.add_argument("--drops", type=int, default=2,
                    help="how many of the worst members to consider replacing "
                         "each round")
    ap.add_argument("--swaps", type=int, default=4,
                    help="how many replacements the screener proposes per "
                         "dropped member, before --per-round trims them")
    ap.add_argument("--pool-size", type=int, default=34,
                    help="how many top-Score Pokemon are eligible to come in")
    ap.add_argument("--generations", default=None,
                    help="restrict the incoming pool, e.g. '3' or '1-5'")
    ap.add_argument("--effort", choices=TIER_ORDER, default="standard")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="candidate rosters rated in parallel (0 = one per "
                         "core). Capped by --per-round: that is how many "
                         "candidates a round has to hand out.")
    ap.add_argument("--evaluation", default=None,
                    choices=["sacrifice", "legacy"],
                    help="which evaluation the solver uses; 'legacy' restores "
                         "the pre-sacrifice behaviour exactly. Part of the "
                         "cache key, so the two never mix.")
    ap.add_argument("--vs", default=None,
                    help="rate against ONLY these opponents (comma-separated). "
                         "Applies to the incumbent too, so the comparison stays "
                         "like-for-like; such ratings are cached separately "
                         "from full-library ones.")
    ap.add_argument("--min-winrate", type=float, default=0.0, metavar="F",
                    help="abandon a candidate whose quick-tier win rate is "
                         "below this before auditing it. 0 (default) rates "
                         "every candidate: unlike generation, the loop is "
                         "already only looking at a handful.")
    # --pilot is the name every other tool uses. --record-pilot is kept as an
    # alias because it is what this tool shipped with, and a flag that silently
    # stops being accepted breaks scripts rather than warning them.
    ap.add_argument("--pilot", "--record-pilot", dest="pilot",
                    choices=["greedy", "equilibrium"],
                    default="greedy",
                    help="who plays the games the RECORD is counted from. The "
                         "default greedy pilot is what the workbook has always "
                         "reported; 'equilibrium' plays it with the same pilot "
                         "the punish audit uses, so the two headline numbers "
                         "describe the same players (WORKFLOW.md §4.4). "
                         "Slower, and rated separately in the cache.")
    ap.add_argument("--max-record-loss", type=float, default=0.0, metavar="F",
                    help="how much of the record (Wins / Of) a swap may give "
                         "up, as a fraction. Default 0: a candidate that beats "
                         "fewer of their configurations is rejected however "
                         "good its adjusted win rate looks. Raise it only if "
                         "you mean to trade record for line quality.")
    ap.add_argument("--optimise-sets", action="store_true",
                    help="re-optimise item and four moves for every candidate "
                         "roster. A swapped member changes what the rest of "
                         "the team should be running, so without this the "
                         "candidate is rated on usage defaults while the "
                         "incumbent may not be.")
    ap.add_argument("--cache", default=DEFAULT_CACHE,
                    help="shared with generate_overnight.py: a team either tool "
                         "has rated at these settings is never rated twice")
    ap.add_argument("--matrix-cache", default="matrix_cache.pkl")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--out", default="substituted.json",
                    help="where to write the improved roster, in the format "
                         "search_teams.py --rosters reads")
    ap.add_argument("--append", action="store_true",
                    help="add to --out instead of replacing it, so several "
                         "teams can be hill-climbed into one file and deep-"
                         "searched together")
    args = ap.parse_args()
    args.jobs, warning = blas_limits.workers_advice(args.jobs)
    if warning:
        print(f"WARNING  : {warning}")

    world = roster_rating.load_world()
    name, team, sets = _load_roster(args, world)
    opponents = ([t.strip() for t in args.vs.split(",") if t.strip()]
                 if args.vs else None)
    if opponents:
        unknown = [t for t in opponents if t not in world["teams"]]
        if unknown:
            raise SystemExit(f"unknown opponent(s): {', '.join(unknown)}")
    rated_against = opponents or list(world["teams"])

    pool = _pool_for(args, world, team)
    enemy_pairs = enemy_pairs_from_teams(
        {t: world["teams"][t] for t in rated_against})
    pm = substitution.PairMatrix(
        world["merged"], world["moves"], world["natures"], world["typechart"],
        base=_matrix_base(args.matrix_cache, pool, enemy_pairs, args.fresh))

    print(f"team     : {name} -- {', '.join(team)}")
    print(f"pool     : {len(pool)} eligible   opponents: "
          f"{', '.join(rated_against)}")
    print(f"effort   : {args.effort}   rounds: {args.rounds}   "
          f"rated per round: {args.per_round}")
    print(f"workers  : {args.jobs} of {os.cpu_count()} cores\n")

    enemies = None
    if sets is not None and not args.optimise_sets:
        # The starting team came out of an optimised run. Rating a candidate on
        # usage defaults against an optimised incumbent is not a comparison --
        # it measures the sets, and every swap would look bad.
        print("sets     : the starting team carries optimised sets, so "
              "candidates are optimised too (implies --optimise-sets)")
        args.optimise_sets = True
    if args.optimise_sets:
        from optimize_sets import enemy_individuals
        enemies = enemy_individuals({t: world["teams"][t]
                                     for t in rated_against})
        if sets is None:
            print("sets     : optimising the starting team...")
            sets = _optimise(team, world, enemies)

    cache = ResultCache(None if args.fresh else args.cache)
    print(f"resuming : {len(cache)} team ratings already cached\n")

    pilot = None if args.pilot == "greedy" else args.pilot
    import solver
    solver.apply_eval_profile(args.evaluation)

    def rate(roster, roster_sets):
        key = roster_rating.rating_key(roster, args.effort, args.turns,
                                       roster_sets, opponents=opponents,
                                       pilot=pilot,
                                       eval_profile=args.evaluation)
        record = cache.get(key)
        if record is None:
            record = roster_rating.rate_team(
                roster, world, effort=args.effort, turns=args.turns,
                our_sets=roster_sets, jobs=args.jobs,
                min_winrate=args.min_winrate, opponents=opponents,
                pilot=pilot, eval_profile=args.evaluation)
            cache.put(key, record)
            cache.save()
        return record

    started = time.time()
    print("rating the starting team...", flush=True)
    current = rate(team, sets)
    print(f"  {name}: {_fmt(current)}\n", flush=True)
    if current.get("adjusted_win_rate") is None:
        print("The starting team has no rating (skipped by a screen), so there "
              "is nothing to improve on.\nRe-run with --min-winrate 0.")
        return

    history = [{"round": 0, "team": list(team), "record": current,
                "accepted": True, "change": None}]

    for round_no in range(1, args.rounds + 1):
        weights = substitution.key_weights(
            enemy_pairs, substitution.opponent_weights(current, rated_against))
        ranking = substitution.drop_ranking(team, enemy_pairs, pm,
                                            record=current, weights=weights)
        print(f"===== round {round_no} " + "=" * 60)
        print("  member evidence (worst slot first):")
        for entry in ranking:
            brought = ("never brought" if entry["brought_by"] == 0 else
                       f"brought vs {entry['brought_by']}/"
                       f"{entry['rated_opponents']}"
                       if entry["brought_by"] is not None else "not yet rated")
            print(f"    {entry['name']:<22} {brought:<20} "
                  f"marginal {entry['marginal']:8.1f}   "
                  f"only answer to {entry['keystone_for']} lead pair(s)")

        proposals = substitution.propose(
            team, pool, enemy_pairs, pm, record=current, weights=weights,
            drops=args.drops, swaps=args.swaps, limit=args.per_round)
        if not proposals:
            print("  no legal substitution left to try.")
            break
        print(f"  screener proposes {len(proposals)} roster(s); rating all of "
              f"them ({pm.computed} margins simulated so far)\n")

        best, best_record, best_sets = None, current, sets
        benched = []
        # The round's candidates are independent, so rate them all at once
        # rather than one after another. Each worker takes a whole candidate
        # roster, which is the granularity that keeps a big machine busy: the
        # opponent-level split inside a single rating tops out at eight.
        cand_sets_by_i = {i: (_optimise(p["team"], world, enemies)
                              if args.optimise_sets else None)
                          for i, p in enumerate(proposals)}
        pending = [{"key": roster_rating.rating_key(
                        p["team"], args.effort, args.turns, cand_sets_by_i[i],
                        opponents=opponents, pilot=pilot,
                        eval_profile=args.evaluation),
                    "team": list(p["team"]), "effort": args.effort,
                    "turns": args.turns, "our_sets": cand_sets_by_i[i],
                    "jobs": 1, "min_winrate": args.min_winrate,
                    "opponents": opponents, "pilot": pilot,
                    "eval_profile": args.evaluation}
                   for i, p in enumerate(proposals)
                   # MUST match the key three lines above, or the cache never
                   # hits and every round re-rates what it already rated.
                   if cache.get(roster_rating.rating_key(
                       p["team"], args.effort, args.turns, cand_sets_by_i[i],
                       opponents=opponents, pilot=pilot,
                       eval_profile=args.evaluation)) is None]
        if pending:
            print(f"  rating {len(pending)} candidate(s) on "
                  f"{min(args.jobs, len(pending))} worker(s)...", flush=True)
            for key, record in roster_rating.rate_many(pending, args.jobs):
                cache.put(key, record)
                cache.save()

        for i, proposal in enumerate(proposals, start=1):
            cand_sets = cand_sets_by_i[i - 1]
            record = rate(proposal["team"], cand_sets)
            # THE RECORD COMES FIRST. Adjusted wins and the win count are
            # produced by different pilots (WORKFLOW.md §4.4), and they can
            # disagree hard: the first real run of this loop accepted a swap
            # that raised adjusted wins 0.426 -> 0.467 while the record fell
            # 75/90 -> 56/90. Trading nineteen of their configurations for a
            # discount on the lines we still win is not an improvement, so a
            # candidate that gives up record is rejected before its rating is
            # even consulted.
            lost = substitution.lost_record(current, record)
            if lost is not None and lost > args.max_record_loss:
                verdict = f"   REJECTED: record -{lost:.0%}"
                better = False
            else:
                better = (roster_rating.rank_key(record)
                          < roster_rating.rank_key(best_record))
                verdict = "   <-- best so far" if better else ""
            # Did the audit actually BRING the new member? When it does not,
            # the rated lines are the same four Pokemon as before and the
            # candidate scores identically to the incumbent -- which looks like
            # "the swap did not help" when it really means "the swap was never
            # tried". Worth saying out loud, because it is the usual reason a
            # round finds nothing.
            came_in = substitution.brought_count(record, proposal["candidate"])
            if came_in == 0:
                benched.append(proposal["candidate"])
                verdict += "   (bench in every bring)"
            print(f"  [{i}/{len(proposals)}] -{proposal['drop']} "
                  f"+{proposal['candidate']}  (screen {proposal['gain']:+.1f})"
                  f"  {_fmt(record)}{verdict}", flush=True)
            if better:
                best, best_record, best_sets = proposal, record, cand_sets

        if best is None:
            print(f"\n  round {round_no}: nothing beat the incumbent. Stopping "
                  f"-- a further round would propose the same rosters.")
            if len(benched) == len(proposals):
                print(f"  Every candidate stayed on the bench in its own "
                      f"committed brings, so none of them changed a single "
                      f"audited line.\n  The slots being replaced are not the "
                      f"ones deciding these matchups: widen the search with "
                      f"--drops, --pool-size or\n  more opponents, or take it "
                      f"as evidence the six is settled here.")
            break
        team, current, sets = list(best["team"]), best_record, best_sets
        history.append({"round": round_no, "team": list(team),
                        "record": current, "accepted": True,
                        "change": {"out": best["drop"],
                                   "in": best["candidate"],
                                   "screen_gain": best["gain"]}})
        print(f"\n  ACCEPTED: -{best['drop']} +{best['candidate']}   "
              f"{_fmt(current)}\n", flush=True)

    print("=" * 72)
    start_record = history[0]["record"]
    print(f"start : {_fmt(start_record)}   {', '.join(history[0]['team'])}")
    print(f"end   : {_fmt(current)}   {', '.join(team)}")
    changes = [h["change"] for h in history if h["change"]]
    if changes:
        for change in changes:
            print(f"        -{change['out']}  +{change['in']}")
        adj0 = start_record.get("adjusted_win_rate") or 0.0
        adj1 = current.get("adjusted_win_rate") or 0.0
        print(f"        adjusted wins {adj0:.3f} -> {adj1:.3f} "
              f"({adj1 - adj0:+.3f}) in {time.time() - started:.0f}s")
    else:
        print("        no swap improved on the starting team.")

    # Keep the stage 1 name when nothing changed, so a team that is already at
    # a local optimum still reaches stage 2 under the number the user knows it
    # by; only a team that actually moved gets a new name.
    out_name = f"{name}_sub" if changes else name
    payload = {"teams": {}, "sets": {}, "substitution": {}}
    if args.append and os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as fh:
            existing = json.load(fh)
        payload["teams"] = existing.get("teams", {})
        payload["sets"] = existing.get("sets", {})
        payload["substitution"] = existing.get("substitution", {})
        if not isinstance(payload["substitution"], dict) or \
                "changes" in payload["substitution"]:
            # An --out written by a single-team run (before --append) keeps one
            # flat record. Nest it under its team so appending never silently
            # discards the provenance of what is already there.
            payload["substitution"] = {"(earlier run)": payload["substitution"]}
    payload["teams"][out_name] = list(team)
    payload["sets"][out_name] = (sets or {})
    payload["substitution"][out_name] = {
        "started_from": history[0]["team"],
        "opponents": rated_against,
        "effort": args.effort,
        "changes": changes,
        "adjusted_win_rate": current.get("adjusted_win_rate"),
        "exploitability": current.get("exploitability"),
        "wins": current.get("wins"), "of": current.get("total")}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    from team_sheet_export import write_team_sheets
    sheets_path = os.path.splitext(args.out)[0] + "_sheets.json"
    write_team_sheets(payload["teams"], world["merged"], sheets_path,
                      optimised=args.optimise_sets, sets=payload["sets"])
    print(f"\nWrote {os.path.abspath(args.out)} and the team sheets beside it.")
    print("Deep-search it with:")
    print(f"  search_teams.py --rosters {args.out} --effort thorough "
          f"--audit-all --sheets {os.path.basename(sheets_path)} --export")


if __name__ == "__main__":
    main()
