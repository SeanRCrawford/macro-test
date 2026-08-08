"""Generate many teams, rate them by exploitability, write a shortlist.

    python generate_overnight.py --candidates 40 --effort standard --jobs 0

THE PROBLEM THIS SOLVES. Beam search scores teams on coverage and synergy
heuristics; those are the only thing steering generation, and they have no
knowledge of exploitability at all (`team_search.py` mentions it zero times).
So the objective generation optimises and the number we finally judge a team by
are two different quantities. Asking for the beam's top 3 and rating those is
therefore not a search for a low-punish team -- it is a search for a
high-coverage team that we then measure.

Nothing here changes the beam's objective, which would be a much larger piece
of work. What it does is widen the funnel: take MANY beam finalists rather than
three, rate every one of them by exploitability, and let that ranking -- not the
beam's -- decide the shortlist. That is the honest version of "generate great
teams" available today, and the residual bias is stated rather than hidden.

Resumable in the same way as search_teams: each team's rating is cached under a
key covering everything that would change it, so an interrupted overnight run
picks up where it stopped.

Output is a shortlist JSON that search_teams.py reads via --rosters, so the two
stages chain:

    generate_overnight.py --candidates 40 --effort standard --out shortlist.json
    search_teams.py --rosters shortlist.json --teams "gen01,gen02" --effort thorough
"""
import argparse
import json
import os
import sys
import time

import _harness  # noqa: F401  (adds ../src to sys.path)

sys.path.insert(0, "../src")
import generate_team as gt  # noqa: E402
from search_effort import TIER_ORDER, ResultCache, relative_cost, tier  # noqa: E402
from species_data import build_merged_dataset, load_preferences  # noqa: E402
from team_search import (beam_search_teams, build_candidate_pool,  # noqa: E402
                         build_pair_matrix, enemy_pairs_from_teams)

DEFAULT_CACHE = "generate_cache.json"
SCHEMA = 1


def _beam_finalists(args, world):
    """Stage 1-3 of generate_team: pool -> pair matrix -> beam search.

    The pair matrix is the expensive part and depends only on the pool and the
    enemy teams, so it is pickled: re-running with a different --candidates or
    --effort reuses it instead of rebuilding.
    """
    import pickle

    prefs = load_preferences()
    pool = build_candidate_pool(world["merged"], args.pool_size, prefs)
    enemy_pairs = enemy_pairs_from_teams(world["teams"])
    print(f"pool     : {len(pool)} Pokemon   enemy lead pairs: {len(enemy_pairs)}")

    matrix = None
    path = args.matrix_cache
    if path and os.path.exists(path) and not args.fresh:
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        if blob.get("pool") == pool and blob.get("enemy_pairs") == enemy_pairs:
            matrix = blob["matrix"]
            print(f"matrix   : loaded from {path}")
    if matrix is None:
        t0 = time.time()
        matrix = build_pair_matrix(pool, enemy_pairs, world["merged"], world["moves"],
                                   world["natures"], world["typechart"])
        print(f"matrix   : built in {time.time() - t0:.0f}s")
        if path:
            with open(path, "wb") as fh:
                pickle.dump({"pool": pool, "enemy_pairs": enemy_pairs,
                             "matrix": matrix}, fh)

    finals = beam_search_teams(pool, matrix, enemy_pairs, world["merged"],
                               beam_width=max(args.beam_width, args.candidates))
    finals = [(score["total"], team) for score, team in finals][:args.candidates]
    print(f"beam     : {len(finals)} candidate teams to rate\n")
    return finals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=20,
                    help="how many beam finalists to RATE. The whole point of "
                         "this tool: rate many, not three.")
    ap.add_argument("--pool-size", type=int, default=34)
    ap.add_argument("--beam-width", type=int, default=30)
    ap.add_argument("--effort", choices=TIER_ORDER, default="standard")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="opponents verified in parallel (0 = one per core)")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--matrix-cache", default="matrix_cache.pkl",
                    help="pickled pair matrix, reused across runs with the "
                         "same pool and opponents")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--out", default="shortlist.json",
                    help="where to write the shortlist search_teams.py reads")
    ap.add_argument("--keep", type=int, default=10,
                    help="how many of the rated teams to write to --out")
    args = ap.parse_args()
    if args.jobs == 0:
        args.jobs = os.cpu_count() or 1

    merged, _usage, moves, natures, typechart = build_merged_dataset()
    teams = gt.load_teams(merged=merged)
    world = dict(merged=merged, moves=moves, natures=natures,
                 typechart=typechart, teams=teams)

    settings = tier(args.effort)
    print(f"effort   : {settings['label']} (~{relative_cost(args.effort):.0f}x quick)")
    print(f"workers  : {args.jobs} of {os.cpu_count()} cores")

    finals = _beam_finalists(args, world)
    cache = ResultCache(None if args.fresh else args.cache)
    print(f"resuming : {len(cache)} already rated\n")

    rated, started = [], time.time()
    for i, (beam_score, team) in enumerate(finals, start=1):
        key = ResultCache.key("team", SCHEMA, sorted(team), args.effort, args.turns)
        record = cache.get(key)
        if record is None:
            verdict = gt.verify_with_solver(
                team, teams, merged, moves, natures, typechart, {}, [],
                max_turns=args.turns, all_backs=True, effort=args.effort,
                jobs=args.jobs)
            scores = [r["exploitability"] for r in verdict.values()
                      if r and r.get("exploitability") is not None]
            wins = sum((r.get("wins") or 0) for r in verdict.values() if r)
            total = sum((r.get("total") or 0) for r in verdict.values() if r)
            worst = max((r for r in verdict.values()
                         if r and r.get("exploitability") is not None),
                        key=lambda r: r["exploitability"], default=None)
            record = {
                "team": list(team),
                "beam_score": beam_score,
                "exploitability": (sum(scores) / len(scores)) if scores else None,
                "severe_turns": sum((r.get("severe_turns") or 0)
                                    for r in verdict.values() if r),
                "wins": wins, "total": total,
                "worst_opponent": next((n for n, r in verdict.items()
                                        if r is worst), None),
                "worst_value": worst["exploitability"] if worst else None,
                "worst_turn": worst.get("worst_turn") if worst else None,
                "per_opponent": {n: {"wins": r.get("wins"), "total": r.get("total"),
                                     "exploitability": r.get("exploitability"),
                                     "severe_turns": r.get("severe_turns")}
                                 for n, r in verdict.items() if r},
            }
            cache.put(key, record)
            cache.save()          # every team is a save point: teams are slow
        rated.append(record)
        elapsed = time.time() - started
        print(f"  [{i}/{len(finals)}] "
              f"exploitability {record['exploitability'] or float('nan'):7.1f}   "
              f"{record['wins']}/{record['total']} won   "
              f"~{elapsed / i * (len(finals) - i) / 60:.0f} min left", flush=True)

    ranked = sorted([r for r in rated if r.get("exploitability") is not None],
                    key=lambda r: r["exploitability"])
    if not ranked:
        print("\nNo ratings produced (quick tier does not compute them).")
        return

    print("\n" + "=" * 96)
    print("GENERATED TEAMS RANKED BY HOW PUNISHABLE THEY ARE (lower is better)")
    print("=" * 96)
    for i, r in enumerate(ranked, start=1):
        share = f"{r['wins']}/{r['total']}"
        flag = ("   <-- loses most games: a lost position rates as unpunishable"
                if r["total"] and r["wins"] / r["total"] < 0.5 else "")
        print(f"{i:2}. {r['exploitability']:7.1f}  {r['severe_turns']:>3} severe  "
              f"{share:>9} won{flag}")
        print(f"    {', '.join(r['team'])}")
        if r.get("worst_opponent"):
            print(f"    worst vs {r['worst_opponent']} ({r['worst_value']:.0f})")

    shortlist = {f"gen{i:02d}": r["team"] for i, r in enumerate(ranked[:args.keep], 1)}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(shortlist, fh, indent=2)
    print(f"\nShortlist: {os.path.abspath(args.out)}  ({len(shortlist)} teams)")
    print("Feed it to the deep search with:")
    print(f'  search_teams.py --rosters {args.out} '
          f'--teams "{",".join(list(shortlist)[:3])}" --effort thorough --jobs 0 --export')
    print("\nNOTE: beam search still ranks on coverage/synergy, not "
          "exploitability.\nThis widens the funnel (rating many finalists) but "
          "does not steer generation itself.")


if __name__ == "__main__":
    main()
