"""Rank spread attackers by output x survivability -- the damageslop table.

    python spread_table.py
    python spread_table.py --top 25 --min-power 70
    python spread_table.py --pool-size 40      # only the eligible search pool
    python spread_table.py --team "Big 6"

See src/spread_power.py for what the numbers mean and, more importantly, what
they deliberately do not mean.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402

import _harness  # noqa: E402,F401

from spread_power import MIN_SPREAD_POWER, table  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20,
                    help="how many rows to print (default 20)")
    ap.add_argument("--min-power", type=int, default=MIN_SPREAD_POWER,
                    help=f"ignore spread moves weaker than this (default "
                         f"{MIN_SPREAD_POWER}); Snarl and Icy Wind at 55 are "
                         f"speed control, not damage")
    ap.add_argument("--pool-size", type=int, default=0,
                    help="rate only the top N of the generation pool instead "
                         "of every Pokemon in the dataset")
    ap.add_argument("--team", default="",
                    help="rate only this team's members, by library name")
    ap.add_argument("--foes-only", action="store_true",
                    help="drop allAdjacent moves (Earthquake, Sludge Wave, "
                         "Surf) which hit your own partner too. The examples "
                         "that motivated this table -- Heat Wave, Hyper Voice "
                         "-- are all foes-only, and an ally-hitting move "
                         "constrains the slot beside it in a way the score "
                         "does not price.")
    ap.add_argument("--csv", default="",
                    help="also write the whole table (not just --top) here")
    ap.add_argument("--xlsx", default="spread_table.xlsx", metavar="PATH",
                    help="write the whole table as a workbook (default "
                         "spread_table.xlsx, beside this script). Pass an "
                         "empty string to skip it.")
    args = ap.parse_args()

    from _harness import load_world
    W = load_world()
    merged, moves, natures, typechart = (W["merged"], W["moves"],
                                         W["natures"], W["typechart"])

    if args.team:
        names = list(W["teams"][args.team])
        scope = f"team {args.team!r}"
    elif args.pool_size:
        from generate_team import build_candidate_pool
        names = list(build_candidate_pool(merged, top_n=args.pool_size))
        scope = f"the top {args.pool_size} of the generation pool"
    else:
        names = list(merged)
        scope = f"all {len(merged)} Pokemon in the dataset"

    rows = table(names, merged, natures, moves, typechart,
                 min_power=args.min_power, foes_only=args.foes_only)
    print(f"\nSpread attackers in {scope}: {len(rows)} of {len(names)} run a "
          f"spread move of {args.min_power}+ BP.\n")
    print(f"{'#':>3} {'Pokemon':20s} {'ability':15s} {'spread move':14s} "
          f"{'type':8s} {'BP':>3} {'base':>4} {'off':>4} {'hp':>4} {'def':>4} "
          f"{'spd':>4} {'spe':>4} {'wx':6s} {'2 tgt':>6} {'taken':>6} "
          f"{'hits':>5} {'SCORE':>6}")
    print("-" * 146)
    for i, r in enumerate(rows[:args.top], start=1):
        f = r["final"]
        print(f"{i:>3} {r['name'][:20]:20s} {(r['ability'] or '-')[:15]:15s} "
              f"{r['move'][:14]:14s} {r['type'][:8]:8s} {r['power']:>3} "
              f"{(r['base_off'] or 0):>4} {(f.get(r['off_key']) or 0):>4} "
              f"{(f.get('hp') or 0):>4} {(f.get('def') or 0):>4} "
              f"{(f.get('spd') or 0):>4} {(f.get('spe') or 0):>4} "
              f"{(r['weather'] or '-')[:6]:6s} {r['two_target']*100:>5.1f}% "
              f"{r['incoming']*100:>5.1f}% {r['hits_to_ko']:>5.2f} "
              f"{r['score']:>6.2f}" + ("  hits ally" if r["hits_ally"] else ""))
    print()
    print("1 tgt / 2 tgt  neutral damage as % of a base-100/100/100 target's HP,")
    print("               one target and both (0.75x spread penalty applied).")
    print("taken          mean % taken from a 100 BP neutral hit off a 125 stat.")
    print("hits           1 / taken: how many of those it survives.")
    print("SCORE          2 tgt x hits. Repeatable, untargetable damage.")
    print("hits ally      allAdjacent: it hits your partner too.")
    print("base / off     relevant BASE offensive stat, then the FINAL one -- the")
    print("               gap is investment, nature and a Mega form.")
    print("Eruption and Water Spout are excluded: their power decays with HP.")
    print("Self-destruct moves are excluded: Explosion is not repeatable output.")

    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else [])
            w.writeheader()
            w.writerows(rows)
        print(f"\nFull table ({len(rows)} rows): {os.path.abspath(args.csv)}")

    if args.xlsx:
        from spread_power import write_workbook
        write_workbook(rows, args.xlsx, scope=scope, min_power=args.min_power,
                       foes_only=args.foes_only)
        print(f"\nWorkbook ({len(rows)} rows): {os.path.abspath(args.xlsx)}")


if __name__ == "__main__":
    main()
