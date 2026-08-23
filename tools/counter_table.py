"""Search the Pokemon pool for answers to specific threats.

    python counter_table.py --vs "Kingambit,Basculegion"
    python counter_table.py --vs "Kingambit,Basculegion" --threshold 100
    python counter_table.py --vs "Kingambit" --chip-from "Ninetales-Alola" --chip-move "Blizzard"
    python counter_table.py --vs "Kingambit,Basculegion,Garchomp" --pairs
    python counter_table.py --vs "Kingambit,Basculegion" --pairs --chip-from "Ninetales-Alola" --chip-move "Blizzard"
    python counter_table.py --vs "Kingambit,Basculegion" --top 15
    python counter_table.py --vs "Kingambit,Basculegion,Urshifu-Rapid-Strike,Chien-Pao,Landorus-Therian,Rillaboom" --team "Big 6" --item "Gallade=Choice Scarf"
    python counter_table.py --vs "Kingambit,Basculegion,Mega Floette" --speed
    python counter_table.py --vs "Kingambit,Basculegion" --threshold 100 --max-taken 50 --outspeed natural
    python counter_table.py --vs "Kingambit,Basculegion" --threshold 50 --max-taken 33 --outspeed scarf
    python counter_table.py --vs "Kingambit,Basculegion,Garchomp" --joint --partner "Whimsicott"
    python counter_table.py --vs "Kingambit,Basculegion,Garchomp" --joint --partner "Whimsicott" --turns 3

Four modes, pick one (or combine --chip-from/--chip-move with --pairs):

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
  --speed     Instead of a damage search, a SPEED-TIER chart: `--vs`'s
              targets plus the pool, in real turn order (priority bracket
              first, then effective speed under each one's own best legal
              item -- Choice Scarf and a weather-boosting ability both
              apply). "to have an option to make sure my guys (accounting
              for priority like bullet punch) outspeed their enemies" --
              read down the list; anyone above a target in the SAME
              priority bracket outspeeds it, and a higher bracket outspeeds
              it regardless of the speed numbers.
  --joint     A JOINT pair table: paired with --partner (both attacking with
              their own real set, not one fixed move), does the candidate +
              partner beat every PAIR drawn from the named targets, over
              --turns turns (default 2), in real priority-then-speed order?
              Classified as a clean SWEEP (both enemies dead before either
              of them ever acted), an OUT-TRADE win (both enemies dead
              within the window, ours took hits but didn't faint), a LOSS,
              or NO-KO (window elapsed, nobody finished). Also replays the
              same race with the enemy side's speed doubled (a Tailwind
              hypothesis, same spirit as --outspeed scarf) and reports
              whether the pair is still safe.

By default the WHOLE ~270-Pokemon dataset is searched, not a pre-narrowed
"generically good" subset -- see `_pool` below for why (short version: "why
does Mega Scizor not show up" used to have an answer, and it was a bug in
which Pokemon ever got asked, not in the damage/item/move search itself).

DEFAULT MODE ONLY: --max-taken and --outspeed add hard requirements on top
of --threshold's KO bar, all three composed by AND -- a row failing any of
them is dropped, not just flagged.

    --max-taken 50           every named target's best roll against this
                              Pokemon must do under 50%
    --outspeed natural       must out-speed every named target under its
                              own chosen item
    --outspeed scarf         ... or would, if it held Choice Scarf instead
                              (a hypothesis; pin --item yourself if you want
                              that reflected in the actual damage numbers)

"my attackers in counter_table must either be faster than the enemy, able
to be faster with choice scarf, and/or take max X damage from the enemy's
best attack (e.g., OHKO all, take less than 50%, outspeed. or 2HKO all,
take less than 33%, outspeed.)" -- those two examples are exactly the last
two commands above (2HKO all == --threshold 50, the same worst-roll->=50%
guarantee --threshold already means).

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

from counter_finder import (chip_then_ko, joint_pair_search, pair_search,  # noqa: E402
                            speed_tiers, threshold_search)


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
    """Who gets searched. Default: EVERY Pokemon in the dataset.

    "Why does Mega Scizor not show up" -- the earlier default reused
    `generate_team.build_candidate_pool`, which ranks by roster.csv's generic
    Score for TEAM GENERATION (a balanced-team-of-6 concern) and truncates to
    the top N of that ranking. Mega Scizor's generic Score put it outside
    that top 40 even though it is a genuinely strong, correctly-calculated
    answer to a SPECIFIC trio (Close Combat/Knock Off/Bullet Punch clears
    all three) -- the search itself was never the problem, the pool it never
    got to run on was. A single-Pokemon threshold/chip/pairs search over the
    full ~270-entry dataset takes low single-digit seconds, so there is no
    real cost to searching everything by default; `--pool-size` still exists
    to explicitly narrow it (e.g. to that same generic-Score top N) if
    something ever needs to be faster.
    """
    if args.team:
        from _harness import load_world
        W = load_world()
        return list(W["teams"][args.team])
    if args.pool_size:
        from generate_team import build_candidate_pool
        return list(build_candidate_pool(merged, top_n=args.pool_size))
    return sorted(merged)


def _roll(h):
    """"what the damage roll is": worst-avg-best, as %, one Hit."""
    return f"{h.lo * 100:.0f}-{h.avg * 100:.0f}-{h.hi * 100:.0f}%"


def _print_threshold(rows, targets, threshold, top, max_taken=None, outspeed=None):
    screened = max_taken is not None or outspeed is not None
    header = f"{'#':>3} {'Pokemon':20s} {'item':16s}"
    for t in targets:
        header += f" {t[:22]:>22s}"
    header += f" {'worst':>6s} {'all >= thr':>10s}"
    if screened:
        header += f" {'max taken':>10s} {'outspeeds all':>14s}"
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        line = f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s}"
        for t in targets:
            h = r["per_target"][t]
            line += f" {_roll(h):>22s}"
        line += f" {r['worst_pct'] * 100:>5.0f}% {('YES' if r['meets_all'] else ''):>10s}"
        if screened:
            worst_taken = max((h.hi for h in r["incoming"].values()), default=0.0)
            outsped = (r["outspeeds_scarf"] if outspeed == "scarf"
                      else r["outspeeds"])
            line += f" {worst_taken * 100:>9.0f}% {('YES' if all(outsped.values()) else ''):>14s}"
        print(line)
    print()
    print("Each cell is the damage ROLL (worst-average-best %) that Pokemon's")
    print("best move (from its own optimised set, under its best LEGAL item for")
    print("this matchup) does to that target at full HP -- the WORST end is what")
    print("counts for the verdict (a guarantee, not a hope). 'worst' is the")
    print("worst-roll minimum across targets -- ranked on it, since answering")
    print("one target and whiffing the other is not the answer that was asked")
    print("for. A 'Mega X' name is always its mega stats.")
    if screened:
        print()
        print("Rows that failed --max-taken/--outspeed are not shown at all --")
        print("'max taken' is the worst of EACH named target's best roll against")
        print("this Pokemon (the most they could do, guaranteed-survival reading);")
        print("'outspeeds all' is against every named target " +
             ("naturally OR with Choice Scarf (a hypothesis)."
              if outspeed == "scarf" else "under its own chosen item."))
    for i, r in enumerate(rows[:top], start=1):
        bits = [f"{t}: {h.move_name or '-'} {_roll(h)}"
                for t, h in r["per_target"].items()]
        print(f"  {i:>3} {r['name']}: " + "; ".join(bits))
        if screened:
            in_bits = [f"{t}: {h.move_name or '-'} {_roll(h)}"
                      for t, h in r["incoming"].items()]
            print(f"      incoming: " + "; ".join(in_bits))


def _print_speed(rows, targets, top):
    print("Speed tiers -- real turn order: PRIORITY bracket first, then")
    print("effective speed (Choice Scarf / a weather-boosting ability already")
    print("applied) within the same bracket. No field: no Tailwind/Trick Room.")
    print("'role' marks the named --vs targets so you can read straight down")
    print("for anyone in your pool sitting above one in the same bracket.\n")
    header = f"{'#':>3} {'Pokemon':20s} {'item':16s} {'pri':>4s} {'move':16s} {'speed':>6s}  role"
    print(header)
    print("-" * len(header))
    shown = 0
    for r in rows:
        is_target = r["name"] in targets
        if not is_target:
            if shown >= top:
                continue
            shown += 1
        pri = f"+{r['priority']}" if r["priority"] > 0 else "-"
        num = "" if is_target else str(shown)
        print(f"{num:>3} {r['name'][:20]:20s} "
              f"{(r['item'] or '-')[:16]:16s} {pri:>4s} "
              f"{(r['priority_move'] or '-')[:16]:16s} {r['speed']:>6.1f}  "
              f"{'TARGET' if is_target else ''}")


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


_JOINT_RANK = {"sweep": 0, "out_trade": 1, "no_ko": 2, "loss": 3}


def _print_joint(rows, targets, top, partner, turns):
    total = rows[0]["pairs_total"] if rows else 0
    print(f"Paired with {partner} (both attacking with their own real set, "
          f"{turns} turns, average rolls):\n")
    header = (f"{'#':>3} {'Pokemon':20s} {'item':16s} {'beaten':>7s} "
              f"{'swept':>6s} {'traded':>7s} {'lost':>5s} {'no KO':>6s} "
              f"{'tw-safe':>8s}")
    print(header)
    print("-" * len(header))
    for i, r in enumerate(rows[:top], start=1):
        beaten = r["pairs_swept"] + r["pairs_traded"]
        print(f"{i:>3} {r['name'][:20]:20s} {(r['item'] or '-')[:16]:16s} "
              f"{beaten:>4d}/{total:<2d} {r['pairs_swept']:>3d}/{total:<2d} "
              f"{r['pairs_traded']:>4d}/{total:<2d} {r['pairs_lost']:>2d}/{total:<2d} "
              f"{r['pairs_no_ko']:>3d}/{total:<2d} "
              f"{r['pairs_tailwind_safe']:>5d}/{total:<2d}")
    print()
    print("Up to `turns` turns, priority THEN speed order, average rolls -- the")
    print("candidate and the fixed partner both attack with their own real")
    print("optimised set (not one fixed move). A spread move (Eruption, Heat")
    print("Wave, ...) hits both of the enemy pair at once, 0.75x each. A")
    print("'Mega X' name is always its mega stats.")
    print("swept      both of the pair are dead before EITHER of them ever")
    print("           got to act -- outsped and KO'd before they could move.")
    print("out-trade  both of the pair die within the turn window, but ours")
    print("           took a hit along the way (still won the race).")
    print("lost       one of ours fainted before both of the pair did.")
    print("no KO      the turn window elapsed with neither side finished.")
    print("beaten     swept + traded -- pairs where the joint fight is won.")
    print("tw-safe    the SAME race, replayed with the enemy pair's speed")
    print("           doubled (a Tailwind hypothesis) -- still swept or")
    print("           traded, not lost or no-KO'd, once they move first.")
    for i, r in enumerate(rows[:top], start=1):
        worst = sorted(r["detail"].items(),
                       key=lambda kv: _JOINT_RANK[kv[1]["outcome"]])
        print(f"  {i:>3} {r['name']}:")
        for (e1, e2), d in worst[:3]:
            tw = "" if d["tailwind_safe"] else f"  [tailwind: {d['tailwind_outcome']}]"
            print(f"      {e1} + {e2}: {d['outcome']} "
                 f"(turn {d['turns_used']}){tw}")


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
    ap.add_argument("--speed", action="store_true",
                    help="print a speed-tier chart (priority bracket, then "
                         "effective speed) for --vs's targets plus the pool, "
                         "instead of a damage search")
    ap.add_argument("--joint", action="store_true",
                    help="a JOINT pair table: search the pool paired with "
                         "--partner (both attacking with their own real "
                         "set) against every pair drawn from --vs, over "
                         "--turns turns, real priority-then-speed order, "
                         "plus a Tailwind-robustness replay")
    ap.add_argument("--partner", default="", metavar="POKEMON",
                    help="--joint only: the fixed second half of the pair "
                         "(required with --joint)")
    ap.add_argument("--turns", type=int, default=2, metavar="N",
                    help="--joint only: how many turns to race (default 2)")
    ap.add_argument("--max-taken", type=float, default=None, metavar="PCT",
                    help="default mode only: drop any row where SOME named "
                         "target's best attack could do PCT%% or more to it "
                         "(their best roll, the guaranteed-survival "
                         "direction). E.g. --max-taken 50 for 'take less "
                         "than 50%%'")
    ap.add_argument("--outspeed", choices=("natural", "scarf"), default=None,
                    help="default mode only: drop any row that doesn't "
                         "out-speed EVERY named target. 'natural': under "
                         "its own chosen item. 'scarf': naturally, OR if it "
                         "held Choice Scarf instead (a hypothesis -- pin "
                         "--item yourself for that to show in the damage "
                         "numbers too)")
    ap.add_argument("--pool-size", type=int, default=0, metavar="N",
                    help="search only the top N of the generic team-"
                         "generation Score ranking, instead of the whole "
                         "~270-Pokemon dataset (the default, 0). Narrowing "
                         "is rarely useful -- a full search is a couple of "
                         "seconds -- and can HIDE a real answer whose "
                         "generic Score is unremarkable (see 'Why does Mega "
                         "Scizor not show up' at the top of this file)")
    ap.add_argument("--team", default="",
                    help="search this library team's members instead of the "
                         "full dataset")
    ap.add_argument("--top", type=int, default=20, help="rows to print")
    ap.add_argument("--csv", default="", help="also write the whole table here")
    args = ap.parse_args()

    if bool(args.chip_from) != bool(args.chip_move):
        raise SystemExit("--chip-from and --chip-move must be given together")
    if args.partner_item and not (args.chip_from or args.joint):
        raise SystemExit("--partner-item requires --chip-from/--chip-move or --joint")
    if (args.max_taken is not None or args.outspeed) and (
            args.pairs or args.chip_from or args.speed or args.joint):
        raise SystemExit("--max-taken/--outspeed only apply to the default "
                         "(threshold) mode")
    if args.joint and (args.pairs or args.chip_from or args.speed):
        raise SystemExit("--joint cannot be combined with --pairs/--chip-from/--speed")
    if args.joint and not args.partner:
        raise SystemExit("--joint requires --partner")
    if args.partner and not args.joint:
        raise SystemExit("--partner requires --joint")
    if args.turns != 2 and not args.joint:
        raise SystemExit("--turns only applies to --joint")

    from _harness import load_world
    from lead_sim import BANNED_ITEMS
    W = load_world()
    merged, moves, natures, typechart = (W["merged"], W["moves"], W["natures"],
                                         W["typechart"])
    targets = [n.strip() for n in args.vs.split(",") if n.strip()]
    missing = [n for n in targets if n not in merged]
    if missing:
        raise SystemExit(f"unknown Pokemon: {', '.join(missing)}")
    if args.partner and args.partner not in merged:
        raise SystemExit(f"unknown Pokemon: {args.partner}")
    if args.partner and args.partner in targets:
        raise SystemExit("--partner can't also be a --vs target")
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

    if args.speed:
        names = targets + [n for n in pool if n not in targets]
        rows = speed_tiers(names, targets, merged, moves, natures, typechart,
                           item_overrides=item_overrides)
        _print_speed(rows, targets, args.top)
    elif args.joint:
        rows = joint_pair_search(pool, targets, args.partner, merged, moves,
                                 natures, typechart, turns=args.turns,
                                 partner_item=args.partner_item or None,
                                 item_overrides=item_overrides,
                                 move_overrides=move_overrides)
        _print_joint(rows, targets, args.top, args.partner, args.turns)
    elif args.pairs:
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
        max_taken = args.max_taken / 100.0 if args.max_taken is not None else None
        rows = threshold_search(pool, targets, merged, moves, natures, typechart,
                                threshold=threshold,
                                item_overrides=item_overrides,
                                move_overrides=move_overrides,
                                max_taken=max_taken, outspeed=args.outspeed)
        _print_threshold(rows, targets, threshold, args.top,
                         max_taken=max_taken, outspeed=args.outspeed)

    if args.csv:
        import csv
        flat = []
        for r in rows:
            row = {"name": r["name"], "item": r.get("item")}
            if "priority" in r:
                row["priority"] = r["priority"]
                row["priority move"] = r["priority_move"]
                row["speed"] = round(r["speed"], 1)
                row["target"] = r["name"] in targets
            elif "per_target" in r:
                for t, h in r["per_target"].items():
                    row[f"{t} move"] = h.move_name
                    row[f"{t} lo%"] = round(h.lo * 100, 1)
                    row[f"{t} avg%"] = round(h.avg * 100, 1)
                    row[f"{t} hi%"] = round(h.hi * 100, 1)
                row["worst %"] = round(r["worst_pct"] * 100, 1)
                row["meets all"] = r["meets_all"]
                if "incoming" in r:
                    for t, h in r["incoming"].items():
                        row[f"{t} incoming move"] = h.move_name
                        row[f"{t} incoming hi%"] = round(h.hi * 100, 1)
                    row["outspeeds all (natural)"] = all(r["outspeeds"].values())
                    row["outspeeds all (scarf)"] = all(r["outspeeds_scarf"].values())
            elif "finishes" in r:
                for t, (ko, h) in r["finishes"].items():
                    row[f"{t} KO"] = ko
                    row[f"{t} move"] = h.move_name
                    row[f"{t} lo%"] = round(h.lo * 100, 1)
                    row[f"{t} avg%"] = round(h.avg * 100, 1)
                    row[f"{t} hi%"] = round(h.hi * 100, 1)
                row["n_ko"] = r["n_ko"]
            elif "pairs_swept" in r:
                row["pairs swept"] = r["pairs_swept"]
                row["pairs traded"] = r["pairs_traded"]
                row["pairs lost"] = r["pairs_lost"]
                row["pairs no KO"] = r["pairs_no_ko"]
                row["pairs tailwind-safe"] = r["pairs_tailwind_safe"]
                row["pairs total"] = r["pairs_total"]
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
