"""Search the Pokemon pool for answers to specific threats.

    python counter_table.py --vs "Kingambit,Basculegion"
    python counter_table.py --vs "Kingambit,Basculegion" --threshold 100
    python counter_table.py --vs "Kingambit" --chip-from "Ninetales-Alola" --chip-move "Blizzard"
    python counter_table.py --vs "Kingambit,Basculegion,Garchomp" --pairs
    python counter_table.py --vs "Kingambit,Basculegion" --pool-size 60 --top 15

Three modes, pick one:

  (default)   OHKO / threshold search: best legal item, worst-roll % on EACH
              named target, ranked on the worst of them.
  --chip-from / --chip-move
              Who finishes the named targets off after a partner's named move
              has already landed?
  --pairs     Who is never pinned and always KOes something, against EVERY
              pair drawn from the named targets, resolved in speed order?

See src/counter_finder.py for what each number means and what it deliberately
does not model (no field, no Tailwind/screens/redirection, no whole-game item
effects -- a hypothesis for the detailed lead-race tools, not a verdict).
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402

import _harness  # noqa: E402,F401

from counter_finder import chip_then_ko, pair_search, threshold_search  # noqa: E402


def _pool(args, merged):
    if args.team:
        from _harness import load_world
        W = load_world()
        return list(W["teams"][args.team])
    from generate_team import build_candidate_pool
    return list(build_candidate_pool(merged, top_n=args.pool_size))


def _print_threshold(rows, targets, threshold, top):
    width = max(10, *(len(t) for t in targets))
    header = f"{'#':>3} {'Pokemon':20s} {'item':16s}"
    for t in targets:
        header += f" {t[:width]:>{width}s}"
    header += f" {'worst':>7s} {'all >= thr':>10s}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        line = f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s}"
        for t in targets:
            pct, mv = r["per_target"][t]
            line += f" {pct * 100:>{width - 1}.0f}%"
        line += f" {r['worst_pct'] * 100:>6.0f}% {('YES' if r['meets_all'] else ''):>10s}"
        print(line)
    print()
    print("Each %% is the WORST-ROLL damage that Pokemon's best move (from its")
    print("own optimised set, under its best LEGAL item for this matchup) does")
    print("to that target at full HP. 'worst' is the minimum across targets --")
    print("ranked on it, since answering one target and whiffing the other is")
    print("not the answer that was asked for.")
    for i, r in enumerate(rows[:top], start=1):
        bits = [f"{t}: {mv or '-'} ({pct * 100:.0f}%)"
                for t, (pct, mv) in r["per_target"].items()]
        print(f"  {i:>3} {r['name']}: " + "; ".join(bits))


def _print_chip(rows, targets, partner, move, top):
    print(f"Chip from {partner}'s {move} (worst roll):")
    for t in targets:
        chip = rows[0]["chip"][t] if rows else 0.0
        print(f"  {t}: {chip * 100:.0f}%")
    print()
    header = f"{'#':>3} {'Pokemon':20s} {'item':16s} {'KOs':>4s}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        print(f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s} "
              f"{r['n_ko']:>4d}/{len(targets)}")
    print()
    for i, r in enumerate(rows[:top], start=1):
        bits = [f"{t}: {'KO' if ko else f'{pct * 100:.0f}%'} ({mv or '-'})"
                for t, (ko, pct, mv) in r["finishes"].items()]
        print(f"  {i:>3} {r['name']}: " + "; ".join(bits))


def _print_pairs(rows, targets, top):
    total = rows[0]["pairs_total"] if rows else 0
    header = (f"{'#':>3} {'Pokemon':20s} {'item':16s} {'pinned':>8s} "
              f"{'no KO':>7s} {'clean':>7s}")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        clean = total - r["pairs_pinned"] - r["pairs_no_ko"]
        print(f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s} "
              f"{r['pairs_pinned']:>5d}/{total:<2d} {r['pairs_no_ko']:>4d}/{total:<2d} "
              f"{clean:>4d}/{total:<2d}")
    print()
    print("pinned  of the pair's TWO members outsped this Pokemon, together")
    print("        their worst-roll damage reaches 100%% before it can act.")
    print("no KO   it acts, but nothing in the pair drops to its worst-roll hit.")
    print("clean   it acts AND KOes at least one of the pair -- the good rows.")
    for i, r in enumerate(rows[:top], start=1):
        worst = sorted(r["detail"].items(),
                       key=lambda kv: (not kv[1]["pinned"], bool(kv[1]["ko"])))
        bits = []
        for (e1, e2), d in worst[:3]:
            if d["pinned"]:
                bits.append(f"{e1}+{e2}: PINNED ({d['pre_damage'] * 100:.0f}%)")
            elif d["ko"]:
                bits.append(f"{e1}+{e2}: KOs " + ", ".join(n for n, _m in d["ko"]))
            else:
                bits.append(f"{e1}+{e2}: no KO")
        print(f"  {i:>3} {r['name']}: " + "; ".join(bits))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", required=True,
                    help="comma-separated target Pokemon, by library name")
    ap.add_argument("--threshold", type=float, default=90.0,
                    help="%% threshold for the default mode's 'meets all' "
                         "column (default 90)")
    ap.add_argument("--chip-from", default="", metavar="POKEMON",
                    help="partner Pokemon whose move chips the targets first")
    ap.add_argument("--chip-move", default="", metavar="MOVE",
                    help="the partner's move (requires --chip-from)")
    ap.add_argument("--pairs", action="store_true",
                    help="speed-order pair search instead of the single-hit "
                         "threshold search")
    ap.add_argument("--pool-size", type=int, default=40,
                    help="how many Pokemon from the generation pool to "
                         "search (default 40)")
    ap.add_argument("--team", default="",
                    help="search this library team's members instead of the "
                         "generation pool")
    ap.add_argument("--top", type=int, default=20, help="rows to print")
    ap.add_argument("--csv", default="", help="also write the whole table here")
    args = ap.parse_args()

    if bool(args.chip_from) != bool(args.chip_move):
        raise SystemExit("--chip-from and --chip-move must be given together")
    if args.pairs and args.chip_from:
        raise SystemExit("--pairs and --chip-from are different modes; pick one")

    from _harness import load_world
    W = load_world()
    merged, moves, natures, typechart = (W["merged"], W["moves"], W["natures"],
                                         W["typechart"])
    targets = [n.strip() for n in args.vs.split(",") if n.strip()]
    missing = [n for n in targets if n not in merged]
    if missing:
        raise SystemExit(f"unknown Pokemon: {', '.join(missing)}")
    pool = _pool(args, merged)
    threshold = args.threshold / 100.0

    print(f"Searching {len(pool)} Pokemon vs {', '.join(targets)}\n")

    if args.chip_from:
        rows = chip_then_ko(pool, targets, args.chip_from, args.chip_move,
                            merged, moves, natures, typechart)
        _print_chip(rows, targets, args.chip_from, args.chip_move, args.top)
    elif args.pairs:
        rows = pair_search(pool, targets, merged, moves, natures, typechart)
        _print_pairs(rows, targets, args.top)
    else:
        rows = threshold_search(pool, targets, merged, moves, natures, typechart,
                                threshold=threshold)
        _print_threshold(rows, targets, threshold, args.top)

    if args.csv:
        import csv
        flat = []
        for r in rows:
            row = {"name": r["name"], "item": r.get("item")}
            if "per_target" in r:
                for t, (pct, mv) in r["per_target"].items():
                    row[f"{t} %"] = round(pct * 100, 1)
                    row[f"{t} move"] = mv
                row["worst %"] = round(r["worst_pct"] * 100, 1)
                row["meets all"] = r["meets_all"]
            elif "finishes" in r:
                for t, (ko, pct, mv) in r["finishes"].items():
                    row[f"{t} KO"] = ko
                    row[f"{t} %"] = round(pct * 100, 1)
                    row[f"{t} move"] = mv
                row["n_ko"] = r["n_ko"]
            else:
                row["pairs pinned"] = r["pairs_pinned"]
                row["pairs no KO"] = r["pairs_no_ko"]
                row["pairs total"] = r["pairs_total"]
            flat.append(row)
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(flat[0]) if flat else [])
            w.writeheader()
            w.writerows(flat)
        print(f"\nFull table ({len(flat)} rows): {os.path.abspath(args.csv)}")


if __name__ == "__main__":
    main()
