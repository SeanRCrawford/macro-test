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
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
# MUST come before anything that reaches numpy/pandas -- see the module
# docstring. Every import below this line is deliberately ordered, not sorted.
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import _harness  # noqa: E402,F401  (adds ../src to sys.path)

sys.path.insert(0, "../src")
from matchup_search import search_robust_composition  # noqa: E402
from search_effort import (TIER_ORDER, ResultCache, batches,  # noqa: E402
                           relative_cost, tier)

DEFAULT_CACHE = "search_cache.json"

# Seconds per audited TURN, measured on this engine at depth 1 (tools timing:
# a 6-turn line took 5.3s => ~0.9s/turn). Only used for the up-front estimate
# before the first pairing finishes -- after that the real rate takes over.
SECONDS_PER_AUDITED_TURN = 0.9
# Lines rarely run to the cap: a game usually resolves first. Measured lines
# averaged 6-10 turns against caps of 16-20, so the estimate uses this rather
# than the cap, which would overstate the wait by 2-3x.
TYPICAL_LINE_TURNS = 8

# Bumped when the shape of a cached record changes. It is part of the cache key,
# so a run that recorded less detail is never served to a run that expects more
# -- the same reasoning that puts the effort tier in the key.
SCHEMA = 7   # --brings is part of what was computed


_WORLD = None       # per-process dataset, loaded once (13-14s) and reused
_EXTRA_ROSTERS = {}  # generated teams, merged into the library per process
_EXTRA_SETS = {}     # their optimised item/moves, so we simulate what was rated


def _worker_init(extra=None, sets=None):
    """Load the dataset once per worker process rather than once per pairing.

    `extra` carries generated rosters, which do not exist in teams.csv. They
    are merged into the team library so a generated team is searchable by name
    exactly like a real one -- that is what lets generation feed this tool.
    """
    global _WORLD, _EXTRA_ROSTERS, _EXTRA_SETS
    from _harness import load_world
    _WORLD = load_world()
    _EXTRA_ROSTERS = dict(extra or {})
    _EXTRA_SETS = dict(sets or {})
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


def load_roster_sets(path):
    """{team: {pokemon: {"item":..., "moves":[...]}}} from the same file.

    Empty when generation did not optimise. Simulating a generated team on
    usage defaults after it was RATED on optimised sets would silently measure
    a different team, so these travel with the roster.
    """
    with open(path, encoding="utf-8") as fh:
        blob = json.load(fh)
    if isinstance(blob, dict) and "sets" in blob:
        return {k: v for k, v in (blob["sets"] or {}).items() if v}
    return {}


def _run_pairing(job):
    """One pairing, start to finish. Must be top-level and return only plain
    types: on Windows the pool uses spawn, so both the function and its result
    cross a pickle boundary.
    """
    ours, theirs, effort, turns, prescreen, audit_all, brings = job
    global _WORLD
    if _WORLD is None:                       # serial path, or a pool without init
        _worker_init()
    settings = tier(effort)
    results = search_robust_composition(
        list(_WORLD["teams"][ours]), list(_WORLD["teams"][theirs]),
        _WORLD["merged"], _WORLD["moves"], _WORLD["natures"],
        _WORLD["typechart"], max_turns=turns,
        our_sets=_EXTRA_SETS.get(ours) or None,
        enemy_sets=_EXTRA_SETS.get(theirs) or None,
        verify_top=brings or settings["verify_top"],
        rate_robustness=settings["robustness"],
        robustness_leads=settings["leads"] or 1,
        robustness_turns=settings["turns"] or 1,
        prescreen_top=prescreen,
        audit_all_configs=audit_all or settings.get("all_configs", False))
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
    ap.add_argument("--brings", type=int, default=0, metavar="N",
                    help="audit only the N best of OUR brings instead of the "
                         "tier's default. The cheap screen has already ranked "
                         "them by worst-case margin, so this keeps the most "
                         "promising few and skips auditing the rest. Cost is "
                         "linear in N: --brings 2 at thorough is a third of "
                         "the work. The runner-up you did not audit will not "
                         "appear in the workbook, which is the trade.")
    ap.add_argument("--pick", default="",
                    help="which of the roster file's teams to search, by RANK "
                         "NUMBER: --pick \"4,10,12\". The numbering is stable "
                         "across runs, and results accumulate in the cache, so "
                         "you can search #6 tonight and come back for #4 and "
                         "#10 tomorrow without redoing either.")
    ap.add_argument("--audit-all", action="store_true",
                    help="audit the line against ALL 90 of their bring-4s "
                         "(leads AND backs) instead of a sample of their most "
                         "plausible leads. Already on at --effort exhaustive; "
                         "this turns it on at any tier. Intensive: it "
                         "multiplies the audit by 90/leads.")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="run N pairings in parallel (0 = one per CPU core). "
                         "Each worker loads the dataset once (~14s) and then "
                         "reuses it, so this is close to a linear speedup on a "
                         "long run. Memory is the limit, not CPU: budget "
                         "roughly 1GB per worker.")
    ap.add_argument("--sheets", default="", metavar="PATH",
                    help="team-sheet JSON written by generate_overnight "
                         "(<shortlist>_sheets.json). Rendered as a Team sheets "
                         "tab so the workbook says what each generated team "
                         "actually is -- members, item, ability, EVs, moves.")
    ap.add_argument("--export", nargs="?", const="", default=None,
                    metavar="PATH",
                    help="also write an .xlsx report (default: the cache file "
                         "name with an .xlsx extension). Exports whatever is in "
                         "the cache, so it works on an interrupted run too.")
    args = ap.parse_args()

    settings = tier(args.effort)
    args.jobs, _warning = blas_limits.workers_advice(args.jobs)
    # The parent needs the team list; workers load their own copy. Reusing this
    # one in the serial path avoids loading the dataset twice.
    extra = load_rosters(args.rosters) if args.rosters else {}
    extra_sets = load_roster_sets(args.rosters) if args.rosters else {}
    _worker_init(extra, extra_sets)
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

    def _select(spec, label, default=None):
        if not spec:
            return default if default is not None else names
        wanted = [t.strip() for t in spec.split(",") if t.strip()]
        unknown = [t for t in wanted if t not in names]
        if unknown:
            raise SystemExit(f"unknown {label}: {unknown}\navailable: {names}")
        return wanted

    # With a roster file and no explicit --teams, OUR side is the generated
    # teams and theirs is the library. That is what the pipeline always wants,
    # and it removes the need for the caller to parse the shortlist and pass
    # the names back in -- which is exactly what broke when the shortlist grew
    # a wrapper for optimised sets.
    library = [n for n in names if n not in extra]

    picked = None
    if args.pick:
        order = list(extra) if extra else names
        wanted = []
        for token in args.pick.replace(" ", "").split(","):
            if not token:
                continue
            if not token.lstrip("-").isdigit():
                raise SystemExit(f"--pick takes rank NUMBERS, got {token!r}")
            idx = int(token)
            if not 1 <= idx <= len(order):
                raise SystemExit(f"--pick {idx} is out of range: the roster "
                                 f"file has {len(order)} teams (1-{len(order)})")
            wanted.append(order[idx - 1])
        picked = wanted or None
        if picked:
            print(f"picked   : {', '.join(picked)}")

    ours_pool = _select(args.teams, "team",
                        picked or (list(extra) if extra else None))
    theirs_pool = _select(args.vs, "opponent", library if extra else None)
    jobs = [(a, b) for a in ours_pool for b in theirs_pool if a != b]
    if not jobs:
        raise SystemExit("no pairings selected")

    _audit_all = args.audit_all or settings.get("all_configs", False)
    print(f"effort   : {settings['label']} (~{relative_cost(args.effort):.0f}x quick)")
    # The tier blurb names a lead sample, which --audit-all overrides. Printing
    # both unedited would contradict the audit line two rows below.
    if not _audit_all:
        print(f"           {settings['blurb']}")
    print(f"pairings : {len(jobs)}   batch {args.batch}   cache {args.cache}")
    print(f"workers  : {args.jobs}"
          f"{' (serial)' if args.jobs <= 1 else ''}   of {os.cpu_count()} cores")
    if _warning:
        print(f"WARNING  : {_warning}")
    print(f"resuming : {len(cache)} already done")

    # What --audit-all actually means, in the units that cost time, plus an
    # up-front estimate. Without this the first feedback of a multi-hour stage
    # arrives only when pairing 1 completes, which can itself be half an hour.
    audit_all = _audit_all
    if settings["robustness"]:
        their_configs = 90 if audit_all else settings["leads"]
        our_brings = args.brings or settings["verify_top"]
        lines_each = our_brings * their_configs
        print(f"audit    : {our_brings} of our brings x "
              f"{their_configs} of their bring-4s "
              f"{'(ALL of them: leads AND backs)' if audit_all else '(sampled leads)'}"
              f" = {lines_each} lines per pairing")
        seconds = (len(jobs) * lines_each * TYPICAL_LINE_TURNS
                   * SECONDS_PER_AUDITED_TURN / max(args.jobs, 1))
        print(f"estimate : ~{seconds / 3600:.1f} h for {len(jobs)} pairings on "
              f"{args.jobs} worker(s)")
        print(f"           (rough -- a real ETA replaces it once the first "
              f"pairing finishes)")
        if seconds > 4 * 3600:
            # Naming the levers beside the number, because "83 hours" with no
            # suggestion is where a tool gets abandoned.
            print("           TOO LONG? the three levers, in order of effect:")
            print(f"             --vs \"Big 6\"   one opponent instead of "
                  f"{len(theirs_pool)}  ->  {len(theirs_pool)}x less")
            print(f"             --brings 2      instead of {our_brings}"
                  f"  ->  {our_brings / 2:.0f}x less")
            print("             --sample-leads  their likely leads instead of "
                  "all 90  ->  ~22x less")
    print()

    # The prescreen width is part of the key: a run that filtered candidates
    # must not be served to a run that did not.
    prescreen = args.prescreen or settings.get("prescreen")
    # A generated roster is part of what determines the answer, so it belongs
    # in the key: two teams sharing a name but not a roster must not collide.
    keyed = [(ResultCache.key("bring", SCHEMA, a, b, args.effort, args.turns,
                              prescreen, args.audit_all, args.brings,
                              extra.get(a), extra.get(b),
                              extra_sets.get(a), extra_sets.get(b)), a, b)
             for a, b in jobs]
    todo = [(k, a, b) for k, a, b in keyed if cache.get(k) is None]
    skipped = len(keyed) - len(todo)
    done, computed = skipped, 0
    started = time.time()

    def _progress(ours, theirs):
        nonlocal computed
        computed += 1
        elapsed = time.time() - started
        rate = elapsed / computed                  # seconds per pairing, real
        left = (len(keyed) - done) * rate
        eta = time.strftime("%H:%M", time.localtime(time.time() + left))
        print(f"  [{done}/{len(keyed)}] {ours} vs {theirs}"
              f"   {rate / 60:.0f} min/pairing"
              f"   elapsed {elapsed / 60:.0f} min"
              f"   left ~{left / 3600:.1f} h (done ~{eta})", flush=True)

    if args.jobs > 1 and todo:
        # Pairings are independent, so the whole pairing is the parallel unit --
        # coarse enough that the 14s dataset load per worker is amortised away
        # and no shared state has to cross a process boundary.
        import concurrent.futures as cf
        for batch in batches(todo, max(args.batch, args.jobs)):
            with cf.ProcessPoolExecutor(max_workers=args.jobs,
                                        initializer=_worker_init,
                                        initargs=(extra, extra_sets)) as pool:
                futures = {pool.submit(_run_pairing,
                                       (a, b, args.effort, args.turns, prescreen,
                                        args.audit_all, args.brings)): (k, a, b)
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
                                           args.turns, prescreen,
                                           args.audit_all, args.brings)))
                done += 1
                _progress(ours, theirs)
            cache.save()

    cache.save()

    if args.export is not None:
        from export_search import build_workbook
        path = args.export or (os.path.splitext(args.cache)[0] + ".xlsx")
        sheets = None
        # Default to the sheets sitting beside the roster file, so the chained
        # pipeline picks them up without a third flag to remember.
        sheets_path = args.sheets or (
            os.path.splitext(args.rosters)[0] + "_sheets.json"
            if args.rosters else "")
        if sheets_path and os.path.exists(sheets_path):
            with open(sheets_path, encoding="utf-8") as fh:
                sheets = json.load(fh)
        n = build_workbook(cache.data, path, team_sheets=sheets)
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
