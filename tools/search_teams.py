"""Resumable, tiered team search ranked by how punishable a team is.

    python search_teams.py --effort thorough --batch 4 --export
    python search_teams.py --effort thorough --batch 4 --export   # resumes

Designed around the fact that the expensive setting is expensive: work is done
in batches, every batch is written to a cache file before the next one starts,
and a re-run with the same settings skips everything already done. Kill it at
any point and restart -- you lose at most one batch.

The cache key includes the effort tier, so switching tiers does not silently
serve results computed under a cheaper one. That is the failure mode that makes
a cache worse than no cache.

`--export` writes the workbook (src/export_search.py) from whatever the cache
holds, so it can be run against a half-finished overnight run without waiting
for the rest, and re-running the same command with --export after everything is
cached just re-exports without recomputing anything.
"""
import argparse
import os
import sys
import time

from _harness import load_world

sys.path.insert(0, "../src")
from matchup_search import search_robust_composition  # noqa: E402
from search_effort import (TIER_ORDER, ResultCache, batches,  # noqa: E402
                           relative_cost, tier)

DEFAULT_CACHE = "search_cache.json"

# Bumped when the shape of a cached record changes. It is part of the cache key,
# so a run that recorded less detail is never served to a run that expects more
# -- the same reasoning that puts the effort tier in the key.
SCHEMA = 2


def _candidate_row(rec):
    """The JSON-able part of one audited bring, keeping the per-turn detail."""
    return {
        "bring": rec.get("our_bring4"),
        "exploitability": rec.get("exploitability"),
        "severe_turns": rec.get("severe_turns"),
        "rated_turns": rec.get("rated_turns"),
        "worst_margin": rec.get("worst_margin"),
        "solver_wins": rec.get("solver_wins"),
        "solver_total": rec.get("solver_total"),
        "downside": rec.get("downside"),
        "hardest_lead": rec.get("hardest_lead"),
        "worst_turn": rec.get("worst_turn"),
        "audit": rec.get("audit") or [],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", choices=TIER_ORDER, default="standard")
    ap.add_argument("--batch", type=int, default=4,
                    help="pairings per save point")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--turns", type=int, default=10, help="battle turn cap")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any cached results and recompute")
    ap.add_argument("--teams", default="",
                    help="comma-separated team names to search as OUR side. "
                         "Use this to spend the expensive tiers on the few "
                         "candidates that survived a cheaper run.")
    ap.add_argument("--prescreen", type=int, default=None,
                    help="keep only the N best candidates by static threat "
                         "coverage before simulating anything. OFF unless set: "
                         "run tools/measure_prescreen.py first and use the "
                         "narrowest width that still recalls ~100%.")
    ap.add_argument("--vs", default="",
                    help="comma-separated team names to use as OPPONENTS "
                         "(default: all others)")
    ap.add_argument("--export", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="also write an .xlsx report (default: the cache file "
                         "name with an .xlsx extension). Exports whatever is in "
                         "the cache, so it works on an interrupted run too.")
    args = ap.parse_args()

    settings = tier(args.effort)
    world = load_world()
    cache = ResultCache(None if args.fresh else args.cache)

    names = list(world["teams"])

    def _select(spec, label):
        if not spec:
            return names
        wanted = [t.strip() for t in spec.split(",") if t.strip()]
        unknown = [t for t in wanted if t not in names]
        if unknown:
            raise SystemExit(f"unknown {label}: {unknown}\navailable: {names}")
        return wanted

    ours_pool = _select(args.teams, "team")
    theirs_pool = _select(args.vs, "opponent")
    jobs = [(a, b) for a in ours_pool for b in theirs_pool if a != b]
    if not jobs:
        raise SystemExit("no pairings selected")

    print(f"effort   : {settings['label']} (~{relative_cost(args.effort):.0f}x quick)")
    print(f"           {settings['blurb']}")
    print(f"pairings : {len(jobs)}   batch {args.batch}   cache {args.cache}")
    print(f"resuming : {len(cache)} already done\n")

    done = 0
    started = time.time()
    for batch in batches(jobs, args.batch):
        for ours, theirs in batch:
            # The prescreen width is part of the key: a run that filtered
            # candidates must not be served to a run that did not.
            key = ResultCache.key("bring", SCHEMA, ours, theirs, args.effort,
                                  args.turns,
                                  args.prescreen or settings.get("prescreen"))
            if cache.get(key) is not None:
                done += 1
                continue
            results = search_robust_composition(
                list(world["teams"][ours]), list(world["teams"][theirs]),
                world["merged"], world["moves"], world["natures"],
                world["typechart"], max_turns=args.turns,
                verify_top=settings["verify_top"],
                rate_robustness=settings["robustness"],
                robustness_leads=settings["leads"] or 1,
                robustness_turns=settings["turns"] or 1,
                prescreen_top=args.prescreen or settings.get("prescreen"))
            top = results[0] if results else None
            cache.put(key, {
                "ours": ours, "theirs": theirs,
                "bring": top["our_bring4"] if top else None,
                "exploitability": (top or {}).get("exploitability"),
                "severe_turns": (top or {}).get("severe_turns"),
                "solver_wins": (top or {}).get("solver_wins"),
                "solver_total": (top or {}).get("solver_total"),
                "downside": (top or {}).get("downside"),
                "hardest_lead": (top or {}).get("hardest_lead"),
                "worst_turn": (top or {}).get("worst_turn"),
                # Every audited candidate, with its per-turn detail -- not just
                # the winner. An overnight run is too expensive to repeat just
                # to see why the runner-up lost, or which turn cost the winner.
                "candidates": [_candidate_row(r) for r in results],
            })
            done += 1
            rate = (time.time() - started) / max(done, 1)
            print(f"  [{done}/{len(jobs)}] {ours} vs {theirs}"
                  f"   ~{rate * (len(jobs) - done) / 60:.0f} min left", flush=True)
        cache.save()   # save point: killing the run now costs at most this batch

    cache.save()

    if args.export is not None:
        from export_search import build_workbook
        path = args.export or (os.path.splitext(args.cache)[0] + ".xlsx")
        n = build_workbook(cache.data, path)
        print(f"\nWorkbook: {os.path.abspath(path)}  ({n} pairings)")

    rows = [v for v in cache.data.values() if isinstance(v, dict) and v.get("ours")]
    by_team = {}
    for r in rows:
        if r.get("exploitability") is None:
            continue
        by_team.setdefault(r["ours"], []).append(r)

    if not by_team:
        print("\nNo exploitability ratings (quick tier does not compute them).")
        return

    print("\n===== teams ranked by how punishable they are =====")
    print(f"{'team':<20}{'exploitability':>16}{'severe':>10}{'matchups':>10}")
    ranked = sorted(by_team.items(),
                    key=lambda kv: sum(r["exploitability"] for r in kv[1]) / len(kv[1]))
    for name, rs in ranked:
        mean = sum(r["exploitability"] for r in rs) / len(rs)
        severe = sum(r.get("severe_turns") or 0 for r in rs)
        print(f"{name:<20}{mean:>16.1f}{severe:>10}{len(rs):>10}")

    worst_overall = max(rows, key=lambda r: r.get("exploitability") or -1)
    wt = worst_overall.get("worst_turn")
    if wt:
        print(f"\nWorst single matchup: {worst_overall['ours']} vs "
              f"{worst_overall['theirs']}"
              f"  (lead {' / '.join(worst_overall.get('hardest_lead') or [])})")
        print(f"  T{wt['turn']}: they gain {wt['exploitability']:.0f} by answering")
        print(f"    {wt['our_play']}")
        print(f"  with {wt['punished_by']}")
    print("\nLower is better. Re-run with the same --effort to resume.")


if __name__ == "__main__":
    main()
