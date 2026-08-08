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
import json
import os
import sys
import time

import _harness  # noqa: F401  (adds ../src to sys.path)

sys.path.insert(0, "../src")
from matchup_search import search_robust_composition  # noqa: E402
from search_effort import (TIER_ORDER, ResultCache, batches,  # noqa: E402
                           relative_cost, tier)

DEFAULT_CACHE = "search_cache.json"

# Bumped when the shape of a cached record changes. It is part of the cache key,
# so a run that recorded less detail is never served to a run that expects more
# -- the same reasoning that puts the effort tier in the key.
SCHEMA = 3   # adjusted wins + line outcomes


_WORLD = None       # per-process dataset, loaded once (13-14s) and reused
_EXTRA_ROSTERS = {}  # generated teams, merged into the library per process


def _worker_init(extra=None):
    """Load the dataset once per worker process rather than once per pairing.

    `extra` carries generated rosters, which do not exist in teams.csv. They
    are merged into the team library so a generated team is searchable by name
    exactly like a real one -- that is what lets generation feed this tool.
    """
    global _WORLD, _EXTRA_ROSTERS
    from _harness import load_world
    _WORLD = load_world()
    _EXTRA_ROSTERS = dict(extra or {})
    _WORLD["teams"].update(_EXTRA_ROSTERS)


def load_rosters(path):
    """Read generated teams from JSON: {"name": ["Mon", ...], ...}.

    Also accepts the list-of-records shape generate_overnight writes, so the
    two tools chain without a conversion step.
    """
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    if isinstance(blob, list):
        return {r["name"]: list(r["team"]) for r in blob if r.get("team")}
    if isinstance(blob, dict) and "teams" in blob:
        blob = blob["teams"]
    return {name: list(members) for name, members in blob.items()}


def _run_pairing(job):
    """One pairing, start to finish. Must be top-level and return only plain
    types: on Windows the pool uses spawn, so both the function and its result
    cross a pickle boundary.
    """
    ours, theirs, effort, turns, prescreen = job
    global _WORLD
    if _WORLD is None:                       # serial path, or a pool without init
        _worker_init()
    settings = tier(effort)
    results = search_robust_composition(
        list(_WORLD["teams"][ours]), list(_WORLD["teams"][theirs]),
        _WORLD["merged"], _WORLD["moves"], _WORLD["natures"],
        _WORLD["typechart"], max_turns=turns,
        verify_top=settings["verify_top"],
        rate_robustness=settings["robustness"],
        robustness_leads=settings["leads"] or 1,
        robustness_turns=settings["turns"] or 1,
        prescreen_top=prescreen)
    top = results[0] if results else None
    return {
        "ours": ours, "theirs": theirs,
        "bring": top["our_bring4"] if top else None,
        "exploitability": (top or {}).get("exploitability"),
        "adjusted_win_rate": (top or {}).get("adjusted_win_rate"),
        "robust_win_rate": (top or {}).get("robust_win_rate"),
        "reliable_wins": (top or {}).get("reliable_wins"),
        "outcomes": (top or {}).get("outcomes"),
        "severe_turns": (top or {}).get("severe_turns"),
        "solver_wins": (top or {}).get("solver_wins"),
        "solver_total": (top or {}).get("solver_total"),
        "downside": (top or {}).get("downside"),
        "hardest_lead": (top or {}).get("hardest_lead"),
        "worst_turn": (top or {}).get("worst_turn"),
        "candidates": [_candidate_row(r) for r in results],
    }


def _candidate_row(rec):
    """The JSON-able part of one audited bring, keeping the per-turn detail."""
    return {
        "bring": rec.get("our_bring4"),
        "exploitability": rec.get("exploitability"),
        "adjusted_win_rate": rec.get("adjusted_win_rate"),
        "robust_win_rate": rec.get("robust_win_rate"),
        "reliable_wins": rec.get("reliable_wins"),
        "outcomes": rec.get("outcomes"),
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
                         # %% because argparse formats help through the %
                         # operator, and Python 3.14 rejects a lone % eagerly
                         # at add_argument time rather than on --help.
                         "narrowest width that still recalls ~100%%.")
    ap.add_argument("--vs", default="",
                    help="comma-separated team names to use as OPPONENTS "
                         "(default: all others)")
    ap.add_argument("--rosters", default="", metavar="PATH",
                    help="JSON of generated teams to add to the library, so "
                         "teams that are not in teams.csv can be searched by "
                         "name. This is how generate_overnight.py feeds this "
                         "tool. Roster contents are part of the cache key.")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="run N pairings in parallel (0 = one per CPU core). "
                         "Each worker loads the dataset once (~14s) and then "
                         "reuses it, so this is close to a linear speedup on a "
                         "long run. Memory is the limit, not CPU: budget "
                         "roughly 1GB per worker.")
    ap.add_argument("--export", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="also write an .xlsx report (default: the cache file "
                         "name with an .xlsx extension). Exports whatever is in "
                         "the cache, so it works on an interrupted run too.")
    args = ap.parse_args()

    settings = tier(args.effort)
    if args.jobs == 0:
        args.jobs = os.cpu_count() or 1
    # The parent needs the team list; workers load their own copy. Reusing this
    # one in the serial path avoids loading the dataset twice.
    extra = load_rosters(args.rosters) if args.rosters else {}
    _worker_init(extra)
    world = _WORLD

    # Validate roster members NOW. An unknown species otherwise surfaces as a
    # KeyError three layers down in the damage engine, after the dataset load
    # and partway into a pairing -- which in an overnight run means discovering
    # the typo in the morning instead of at second five.
    unknown = {name: [m for m in members if m not in world["merged"]]
               for name, members in extra.items()}
    unknown = {k: v for k, v in unknown.items() if v}
    if unknown:
        lines = [f"  {name}: {', '.join(bad)}" for name, bad in unknown.items()]
        raise SystemExit("unknown Pokemon in --rosters "
                         f"{args.rosters}:\n" + "\n".join(lines) +
                         "\nNames must match the dataset exactly "
                         "(case-sensitive, e.g. 'Mega Charizard Y').")
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
    print(f"workers  : {args.jobs}"
          f"{' (serial)' if args.jobs <= 1 else ''}   of {os.cpu_count()} cores")
    print(f"resuming : {len(cache)} already done\n")

    # The prescreen width is part of the key: a run that filtered candidates
    # must not be served to a run that did not.
    prescreen = args.prescreen or settings.get("prescreen")
    # A generated roster is part of what determines the answer, so it belongs
    # in the key: two teams sharing a name but not a roster must not collide.
    keyed = [(ResultCache.key("bring", SCHEMA, a, b, args.effort, args.turns,
                              prescreen, extra.get(a), extra.get(b)), a, b)
             for a, b in jobs]
    todo = [(k, a, b) for k, a, b in keyed if cache.get(k) is None]
    skipped = len(keyed) - len(todo)
    done, computed = skipped, 0
    started = time.time()

    def _progress(ours, theirs):
        nonlocal computed
        computed += 1
        rate = (time.time() - started) / computed
        left = (len(keyed) - done) * rate / 60
        print(f"  [{done}/{len(keyed)}] {ours} vs {theirs}"
              f"   ~{left:.0f} min left", flush=True)

    if args.jobs > 1 and todo:
        # Pairings are independent, so the whole pairing is the parallel unit --
        # coarse enough that the 14s dataset load per worker is amortised away
        # and no shared state has to cross a process boundary.
        import concurrent.futures as cf
        for batch in batches(todo, max(args.batch, args.jobs)):
            with cf.ProcessPoolExecutor(max_workers=args.jobs,
                                        initializer=_worker_init,
                                        initargs=(extra,)) as pool:
                futures = {pool.submit(_run_pairing,
                                       (a, b, args.effort, args.turns, prescreen)): (k, a, b)
                           for k, a, b in batch}
                for future in cf.as_completed(futures):
                    k, a, b = futures[future]
                    cache.put(k, future.result())
                    done += 1
                    _progress(a, b)
            cache.save()   # save point: killing now costs at most this batch
    else:
        for batch in batches(todo, args.batch):
            for k, ours, theirs in batch:
                cache.put(k, _run_pairing((ours, theirs, args.effort,
                                           args.turns, prescreen)))
                done += 1
                _progress(ours, theirs)
            cache.save()

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

    print("\n===== teams ranked by WINS THAT HOLD UP =====")
    print(f"{'team':<20}{'adjusted':>10}{'vs punisher':>13}"
          f"{'exploit':>10}{'severe':>8}{'won':>12}{'matchups':>10}")

    def _adj(rs):
        vals = [r.get("adjusted_win_rate") for r in rs
                if r.get("adjusted_win_rate") is not None]
        return sum(vals) / len(vals) if vals else 0.0

    ranked = sorted(by_team.items(),
                    key=lambda kv: (-_adj(kv[1]),
                                    sum(r["exploitability"] for r in kv[1]) / len(kv[1])))
    flagged = []
    for name, rs in ranked:
        mean = sum(r["exploitability"] for r in rs) / len(rs)
        severe = sum(r.get("severe_turns") or 0 for r in rs)
        wins = sum(r.get("solver_wins") or 0 for r in rs)
        total = sum(r.get("solver_total") or 0 for r in rs)
        share = f"{wins}/{total}" if total else "-"
        rob = [r.get("robust_win_rate") for r in rs
               if r.get("robust_win_rate") is not None]
        rob = sum(rob) / len(rob) if rob else 0.0
        print(f"{name:<20}{_adj(rs):>10.2f}{rob:>13.2f}"
              f"{mean:>10.1f}{severe:>8}{share:>12}{len(rs):>10}")
        # A LOST position has nothing left to punish, so it scores near zero.
        # Printing the win count beside the rating is what stops a team that
        # loses everything from topping a table titled "least punishable".
        if total and wins / total < 0.5:
            flagged.append(name)
    if flagged:
        print(f"\nCareful with: {', '.join(flagged)} -- these lose more than half "
              f"their games.\nExploitability is measured against each turn's "
              f"equilibrium, so a position that is\nsimply lost rates as "
              f"unpunishable. Low rating + low win count means the line was\n"
              f"played well, not that the team is good. Read the two columns "
              f"together.")

    worst_overall = max(rows, key=lambda r: r.get("exploitability") or -1)
    wt = worst_overall.get("worst_turn")
    if wt:
        print(f"\nWorst single matchup: {worst_overall['ours']} vs "
              f"{worst_overall['theirs']}"
              f"  (lead {' / '.join(worst_overall.get('hardest_lead') or [])})")
        print(f"  T{wt['turn']}: they gain {wt['exploitability']:.0f} by answering")
        print(f"    {wt['our_play']}")
        print(f"  with {wt['punished_by']}")
    print("\nadjusted    = wins against an opponent punishing every turn, each "
          "discounted by\n              how punishable the winning line was. "
          "HIGHER is better; it is the ranking.")
    print("vs punisher = the same wins without the discount.")
    print("exploit     = points a good player gains per turn. LOWER is better.")
    print("won         = games against our own bot. Context only; it is the "
          "biased measure.")
    print("\nRe-run with the same --effort to resume.")


if __name__ == "__main__":
    main()
