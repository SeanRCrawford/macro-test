"""Resumable, tiered team search ranked by how punishable a team is.

    python search_teams.py --effort thorough --batch 4
    python search_teams.py --effort exhaustive --batch 4      # resumes

Designed around the fact that the expensive setting is expensive: work is done
in batches, every batch is written to a cache file before the next one starts,
and a re-run with the same settings skips everything already done. Kill it at
any point and restart -- you lose at most one batch.

The cache key includes the effort tier, so switching tiers does not silently
serve results computed under a cheaper one. That is the failure mode that makes
a cache worse than no cache.
"""
import argparse
import sys
import time

from _harness import load_world

sys.path.insert(0, "../src")
from matchup_search import search_robust_composition  # noqa: E402
from search_effort import (TIER_ORDER, ResultCache, batches,  # noqa: E402
                           relative_cost, tier)

DEFAULT_CACHE = "search_cache.json"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", choices=TIER_ORDER, default="standard")
    ap.add_argument("--batch", type=int, default=4,
                    help="pairings per save point")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--turns", type=int, default=10, help="battle turn cap")
    ap.add_argument("--fresh", action="store_true",
                    help="ignore any cached results and recompute")
    args = ap.parse_args()

    settings = tier(args.effort)
    world = load_world()
    cache = ResultCache(None if args.fresh else args.cache)

    names = list(world["teams"])
    jobs = [(ours, theirs) for ours in names for theirs in names if ours != theirs]

    print(f"effort   : {settings['label']} (~{relative_cost(args.effort):.0f}x quick)")
    print(f"           {settings['blurb']}")
    print(f"pairings : {len(jobs)}   batch {args.batch}   cache {args.cache}")
    print(f"resuming : {len(cache)} already done\n")

    done = 0
    started = time.time()
    for batch in batches(jobs, args.batch):
        for ours, theirs in batch:
            key = ResultCache.key("bring", ours, theirs, args.effort, args.turns)
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
                robustness_turns=settings["turns"] or 1)
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
            })
            done += 1
            rate = (time.time() - started) / max(done, 1)
            print(f"  [{done}/{len(jobs)}] {ours} vs {theirs}"
                  f"   ~{rate * (len(jobs) - done) / 60:.0f} min left", flush=True)
        cache.save()   # save point: killing the run now costs at most this batch

    cache.save()
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
