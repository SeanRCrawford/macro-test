"""Search the Pokemon pool for answers to specific threats.

    python counter_table.py --vs "Kingambit,Basculegion"
    python counter_table.py --vs "Kingambit,Basculegion" --threshold 100
    python counter_table.py --vs "Kingambit" --chip-from "Ninetales-Alola" --chip-move "Blizzard"
    python counter_table.py --vs "Kingambit,Basculegion,Garchomp" --pairs
    python counter_table.py --vs "Kingambit,Basculegion" --pairs --chip-from "Ninetales-Alola" --chip-move "Blizzard"
    python counter_table.py --vs "Kingambit,Basculegion" --pool-size 60 --top 15
    python counter_table.py --vs "Kingambit,Basculegion,Urshifu-Rapid-Strike,Chien-Pao,Landorus-Therian,Rillaboom" --team "Big 6" --item "Gallade=Choice Scarf"

Three modes, pick one (or combine --chip-from/--chip-move with --pairs):

  (default)   OHKO / threshold search: best legal item, WORST-roll % on EACH
              named target (a guarantee), ranked on the worst of them.
  --chip-from / --chip-move
              Who finishes the named targets off (worst roll) after a
              partner's named move has already landed? Spread moves (e.g.
              Blizzard) take the doubles 0.75x multi-target penalty.
  --pairs     Who KOes one of every PAIR drawn from the named targets BEFORE
              the pair KOes it -- one full turn, priority then speed order,
              AVERAGE rolls (a realistic line, not a guarantee). Add
              --chip-from/--chip-move to give it a partner's help; every
              combination of who the candidate and the partner go for is
              tried and the best is kept.

By default every pool member's item AND moveset are searched for the best
legal answer to `--vs` -- "or to just select optimal item" is the default,
not something you have to ask for. To instead PIN a specific item on a named
Pokemon (e.g. "Choice Scarf on Gallade beats Big 6 easily" -- go verify
that claim directly), use --item:

    --item "Gallade=Choice Scarf"
    --item "Gallade=Choice Scarf;Ninetales-Alola=Life Orb"

A pinned item does not freeze the moveset to some hardcoded default -- "For
Choice Scarf, a pokemon can use 4 moves, which could be highly useful": the
4 moves are still genuinely re-optimised FOR that item, same search as
always, just with the item fixed instead of searched. Pin the moves too
(skipping move search entirely) with --moves, semicolon-separated by
Pokemon and comma-separated within:

    --moves "Gallade=Psycho Cut,Sacred Sword,Close Combat,Ice Punch"

--partner-item pins the --chip-from/--pairs partner's item the same way (it
defaults to the partner's own best legal item otherwise). Pins are checked
against Regulation MB's banned list (Assault Vest, Choice Band, Choice
Specs) the same as a searched item would be -- a pin is a decision, not a
loophole around the ban.

Every damage number printed is the FULL roll (worst-average-best%), and a
"Mega X" name always means the mega form, stats and all -- see
src/counter_finder.py for exactly what that means and what this deliberately
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


def _parse_item_overrides(spec):
    """"Gallade=Choice Scarf;Ninetales-Alola=Life Orb" -> {name: item}."""
    if not spec:
        return None
    out = {}
    for entry in spec.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise SystemExit(f"--item entry {entry!r} must be 'Pokemon=Item'")
        name, item = entry.split("=", 1)
        out[name.strip()] = item.strip()
    return out


def _parse_move_overrides(spec):
    """"Gallade=Psycho Cut,Sacred Sword;X=..." -> {name: [move, ...]}."""
    if not spec:
        return None
    out = {}
    for entry in spec.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise SystemExit(f"--moves entry {entry!r} must be 'Pokemon=Move,Move,...'")
        name, moves = entry.split("=", 1)
        out[name.strip()] = [m.strip() for m in moves.split(",") if m.strip()]
    return out


def _pool(args, merged):
    if args.team:
        from _harness import load_world
        W = load_world()
        return list(W["teams"][args.team])
    from generate_team import build_candidate_pool
    return list(build_candidate_pool(merged, top_n=args.pool_size))


def _roll(h):
    """"what the damage roll is": worst-avg-best, as %, one Hit."""
    return f"{h.lo * 100:.0f}-{h.avg * 100:.0f}-{h.hi * 100:.0f}%"


def _print_threshold(rows, targets, threshold, top):
    header = f"{'#':>3} {'Pokemon':20s} {'item':16s}"
    for t in targets:
        header += f" {t[:22]:>22s}"
    header += f" {'worst':>6s} {'all >= thr':>10s}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        line = f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s}"
        for t in targets:
            h = r["per_target"][t]
            line += f" {_roll(h):>22s}"
        line += f" {r['worst_pct'] * 100:>5.0f}% {('YES' if r['meets_all'] else ''):>10s}"
        print(line)
    print()
    print("Each cell is the damage ROLL (worst-average-best %) that Pokemon's")
    print("best move (from its own optimised set, under its best LEGAL item for")
    print("this matchup) does to that target at full HP -- the WORST end is what")
    print("counts for the verdict (a guarantee, not a hope). 'worst' is the")
    print("worst-roll minimum across targets -- ranked on it, since answering")
    print("one target and whiffing the other is not the answer that was asked")
    print("for. A 'Mega X' name is always its mega stats.")
    for i, r in enumerate(rows[:top], start=1):
        bits = [f"{t}: {h.move_name or '-'} {_roll(h)}"
                for t, h in r["per_target"].items()]
        print(f"  {i:>3} {r['name']}: " + "; ".join(bits))


def _print_chip(rows, targets, partner, move, top):
    print(f"Chip from {partner}'s {move}:")
    for t in targets:
        h = rows[0]["chip"][t] if rows else None
        if h:
            spread = " (spread, 0.75x)" if h.num_targets_hit > 1 else ""
            print(f"  {t}: {_roll(h)}{spread}")
    print()
    header = f"{'#':>3} {'Pokemon':20s} {'item':16s} {'KOs':>4s}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        print(f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s} "
              f"{r['n_ko']:>4d}/{len(targets)}")
    print()
    print("The chip is the PARTNER's damage roll, worst end used as the")
    print("guaranteed amount taken off before the finisher swings. Each row")
    print("below is the finisher's roll against what's left. A spread chip")
    print("move (Blizzard, ...) is shown with its doubles 0.75x noted -- it")
    print("assumes a real 2v2 field even if only one target was named.")
    for i, r in enumerate(rows[:top], start=1):
        bits = []
        for t, (ko, h) in r["finishes"].items():
            tag = "KO" if ko else _roll(h)
            bits.append(f"{t}: {tag} ({h.move_name or '-'})")
        print(f"  {i:>3} {r['name']}: " + "; ".join(bits))


_RANK = {"clean": 0, "trade": 1, "no_ko": 2, "pinned": 3}


def _print_pairs(rows, targets, top, partner=None, move=None):
    total = rows[0]["pairs_total"] if rows else 0
    if partner:
        print(f"With help from {partner}'s {move} (average roll):\n")
    header = (f"{'#':>3} {'Pokemon':20s} {'item':16s} {'beaten':>7s} "
              f"{'clean':>6s} {'trade':>6s} {'no KO':>6s} {'pinned':>7s}")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        beaten = r["pairs_clean"] + r["pairs_trade"]
        print(f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s} "
              f"{beaten:>4d}/{total:<2d} {r['pairs_clean']:>3d}/{total:<2d} "
              f"{r['pairs_trade']:>3d}/{total:<2d} {r['pairs_no_ko']:>3d}/{total:<2d} "
              f"{r['pairs_pinned']:>4d}/{total:<2d}")
    print()
    print("One full turn per pair, priority THEN speed order, average rolls --")
    print("does this Pokemon KO one of the pair before the pair KOs it? Tried")
    print("against every (candidate target, partner target) combination; the")
    print("better outcome is kept. A spread move (candidate's, or the")
    print("partner's) hits BOTH pair members at once, 0.75x each. A 'Mega X'")
    print("name is always its mega stats.")
    print("clean   it KOs one of them and survives the rest of the turn.")
    print("trade   it KOs one of them but is also KO'd, later the same turn,")
    print("        by the pair member it wasn't aimed at -- still 'KO'd them")
    print("        before being KO'd', just not for free.")
    print("no KO   it acts (isn't removed before its own move fires) but")
    print("        nothing in the pair goes down.")
    print("pinned  removed before it ever gets to act.")
    print("beaten  clean + trade -- pairs where the stated condition holds.")
    for i, r in enumerate(rows[:top], start=1):
        worst = sorted(r["detail"].items(), key=lambda kv: _RANK[kv[1]["outcome"]])
        print(f"  {i:>3} {r['name']}:")
        for (e1, e2), d in worst[:3]:
            print(f"      {e1} + {e2}: {d['outcome']}"
                 + (f" -> {d['target']}" if d["outcome"] in ("clean", "trade") else ""))
            for role, hits in d["hits"].items():
                for tgt, h in hits.items():
                    spread = " (spread)" if h.num_targets_hit > 1 else ""
                    print(f"          {role} -> {tgt}: {h.move_name or '-'} "
                         f"{_roll(h)}{spread}")


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
    ap.add_argument("--partner-item", default="", metavar="ITEM",
                    help="pin the --chip-from/--pairs partner's item instead "
                         "of searching for its best legal one")
    ap.add_argument("--item", default="", metavar="POKEMON=ITEM[;...]",
                    help="pin a specific legal item on named pool members "
                         "instead of searching for the best one, e.g. "
                         "'Gallade=Choice Scarf'. The moveset is still "
                         "re-optimised under the pinned item")
    ap.add_argument("--moves", default="", metavar="POKEMON=MOVE,MOVE,...[;...]",
                    help="pin the moveset too (skips move search) for named "
                         "pool members, e.g. 'Gallade=Psycho Cut,Sacred "
                         "Sword,Close Combat,Ice Punch'")
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
    if args.partner_item and not args.chip_from:
        raise SystemExit("--partner-item requires --chip-from/--chip-move")

    from _harness import load_world
    from lead_sim import BANNED_ITEMS
    W = load_world()
    merged, moves, natures, typechart = (W["merged"], W["moves"], W["natures"],
                                         W["typechart"])
    targets = [n.strip() for n in args.vs.split(",") if n.strip()]
    missing = [n for n in targets if n not in merged]
    if missing:
        raise SystemExit(f"unknown Pokemon: {', '.join(missing)}")
    pool = _pool(args, merged)
    threshold = args.threshold / 100.0

    item_overrides = _parse_item_overrides(args.item)
    move_overrides = _parse_move_overrides(args.moves)
    banned = [i for i in (item_overrides or {}).values() if i in BANNED_ITEMS]
    if banned:
        raise SystemExit(f"--item: not legal in Regulation MB: {', '.join(banned)}")
    overridden = set(item_overrides or {}) | set(move_overrides or {})
    unknown = [n for n in overridden if n not in merged]
    if unknown:
        raise SystemExit(f"--item/--moves: unknown Pokemon: {', '.join(unknown)}")
    # A pin only means something if the Pokemon is actually searched -- add
    # anything named in --item/--moves that the pool (top-N or --team) didn't
    # already include, so e.g. "Gallade=Choice Scarf" is tested even when
    # Gallade wouldn't otherwise have made a --pool-size 40 cut.
    pool = pool + [n for n in overridden if n not in pool and n not in targets]
    if args.partner_item and args.partner_item in BANNED_ITEMS:
        raise SystemExit(f"--partner-item: {args.partner_item!r} is not legal "
                         "in Regulation MB")

    print(f"Searching {len(pool)} Pokemon vs {', '.join(targets)}\n")

    if args.pairs:
        rows = pair_search(pool, targets, merged, moves, natures, typechart,
                           partner_name=args.chip_from or None,
                           partner_move_name=args.chip_move or None,
                           partner_item=args.partner_item or None,
                           item_overrides=item_overrides,
                           move_overrides=move_overrides)
        _print_pairs(rows, targets, args.top, partner=args.chip_from,
                    move=args.chip_move)
    elif args.chip_from:
        rows = chip_then_ko(pool, targets, args.chip_from, args.chip_move,
                            merged, moves, natures, typechart,
                            partner_item=args.partner_item or None,
                            item_overrides=item_overrides,
                            move_overrides=move_overrides)
        _print_chip(rows, targets, args.chip_from, args.chip_move, args.top)
    else:
        rows = threshold_search(pool, targets, merged, moves, natures, typechart,
                                threshold=threshold,
                                item_overrides=item_overrides,
                                move_overrides=move_overrides)
        _print_threshold(rows, targets, threshold, args.top)

    if args.csv:
        import csv
        flat = []
        for r in rows:
            row = {"name": r["name"], "item": r.get("item")}
            if "per_target" in r:
                for t, h in r["per_target"].items():
                    row[f"{t} move"] = h.move_name
                    row[f"{t} lo%"] = round(h.lo * 100, 1)
                    row[f"{t} avg%"] = round(h.avg * 100, 1)
                    row[f"{t} hi%"] = round(h.hi * 100, 1)
                row["worst %"] = round(r["worst_pct"] * 100, 1)
                row["meets all"] = r["meets_all"]
            elif "finishes" in r:
                for t, (ko, h) in r["finishes"].items():
                    row[f"{t} KO"] = ko
                    row[f"{t} move"] = h.move_name
                    row[f"{t} lo%"] = round(h.lo * 100, 1)
                    row[f"{t} avg%"] = round(h.avg * 100, 1)
                    row[f"{t} hi%"] = round(h.hi * 100, 1)
                row["n_ko"] = r["n_ko"]
            else:
                row["pairs clean"] = r["pairs_clean"]
                row["pairs trade"] = r["pairs_trade"]
                row["pairs no KO"] = r["pairs_no_ko"]
                row["pairs pinned"] = r["pairs_pinned"]
                row["pairs total"] = r["pairs_total"]
            flat.append(row)
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(flat[0]) if flat else [])
            w.writeheader()
            w.writerows(flat)
        print(f"\nFull table ({len(flat)} rows): {os.path.abspath(args.csv)}")


if __name__ == "__main__":
    main()
