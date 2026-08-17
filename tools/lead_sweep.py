"""Sweep lead pairs by the pin arithmetic, ranked on their WORST opponent.

    python lead_sweep.py --vs "Big 6"
    python lead_sweep.py --vs "Big 6,Rain,King" --pool-size 34 --top 25
    python lead_sweep.py --check "Ninetales-Alola,Garchomp,Mega Scizor,Rotom-Wash"
    python lead_sweep.py --vs "Big 6" --xlsx lead_sweep.xlsx

No games are played. Every verdict is a sum -- see src/lead_scan.py for what
that buys and what it cannot see. This is the narrowing step BEFORE the overnight
audit, not a replacement for it.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402
import itertools  # noqa: E402
import time  # noqa: E402

import _harness  # noqa: E402,F401

import lead_scan as ls  # noqa: E402


def _opponents(world, spec):
    if not spec:
        return list(world["teams"])
    return [n.strip() for n in spec.split(",") if n.strip()]


def _score_pair(lead, backs, opponents, world, top_backs=3):
    """Best (score, reports) for this lead over a few plausible back pairs.

    The back two are chosen, not swept: sweeping them multiplies the work by
    hundreds and the lead pair is what the scan is really about. So take the
    highest-scoring back pair found among `top_backs` samples per opponent and
    report the WORST opponent of that choice.
    """
    best = None
    for back in backs:
        our4 = list(lead) + list(back)
        # A bring is four DISTINCT SPECIES, at most one Mega. Without this the
        # sweep's top answer was "Garchomp + Mega Garchomp" -- the same Pokemon
        # twice -- with "Dragonite + Mega Dragonite" behind it. Species clause
        # counts the base form; a Mega is that Pokemon holding a stone.
        if not ls.legal_bring(our4):
            continue
        reports = []
        for opp in opponents:
            try:
                reports.append(ls.race_bring(our4, world["teams"][opp], world,
                                             opponent_name=opp))
            except Exception:                          # noqa: BLE001
                return None
        # Their WORST for us: they choose their four and their lead after seeing
        # ours, so a pair is worth what its worst opponent is worth.
        worst = min(reports, key=lambda r: (r.score, -len(r.losses)))
        key = (worst.score, -len(worst.losses))
        if best is None or key > best[0]:
            best = (key, back, reports, worst)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", default="",
                    help="opponents to cover, comma-separated library names. "
                         "Default: every team in the library. The ranking is by "
                         "the WORST of them, because they choose after seeing "
                         "your four.")
    ap.add_argument("--pool-size", type=int, default=24,
                    help="how many Pokemon are eligible for the lead (default "
                         "24). Pairs grow as N^2/2, so 24 is 276 pairs and 40 "
                         "is 780.")
    ap.add_argument("--top", type=int, default=15, help="rows to print")
    ap.add_argument("--check", default="",
                    help="skip the sweep and report ONE bring, lead first: "
                         "--check \"Ninetales-Alola,Garchomp,Mega Scizor,Rotom-Wash\"")
    ap.add_argument("--xlsx", default="", metavar="PATH",
                    help="write the full sweep as a workbook")
    args = ap.parse_args()

    from _harness import load_world
    world = load_world()
    opponents = _opponents(world, args.vs)
    missing = [o for o in opponents if o not in world["teams"]]
    if missing:
        raise SystemExit(f"unknown team(s): {', '.join(missing)}. "
                         f"Available: {', '.join(sorted(world['teams']))}")

    if args.check:
        our4 = [n.strip() for n in args.check.split(",") if n.strip()]
        if len(our4) != 4:
            raise SystemExit("--check needs exactly 4 Pokemon, lead first")
        print()
        for opp in opponents:
            # Both views: the per-enemy breakdown (the EXPLANATION, in the shape
            # the idea was expressed in) and the race over their 15 openings
            # (the VERDICT, which is what a screen can act on).
            for line in ls.describe(ls.scan_bring(our4, world["teams"][opp],
                                                  world, opponent_name=opp)):
                print(line)
            print()
            race = ls.race_bring(our4, world["teams"][opp], world,
                                 opponent_name=opp)
            for line in ls.describe_race(race, limit=args.top):
                print(line)
            print()
            print(f"  Their HP budget -- what can switch in against our back "
                  f"two, and afford it:")
            for name, afford, verdict in ls.enemy_hp_budget(
                    our4, world["teams"][opp], world):
                print(f"    {name:20s} can afford to lose {afford * 100:+7.1f}%"
                      f"   {verdict}")
            slots = ls.mega_slots(world["teams"][opp], world)
            if len(slots) > 1:
                print(f"\n  NOTE: they can Mega any of {', '.join(slots)} and "
                      f"pick AFTER seeing your four; this reflects one choice.")
            print()
        return

    from generate_team import build_candidate_pool
    pool = list(build_candidate_pool(world["merged"], top_n=args.pool_size))
    pairs = [p for p in itertools.combinations(pool, 2) if ls.legal_bring(p)]
    # Back pairs are sampled from the same pool rather than swept: the lead is
    # the question, and sweeping backs multiplies the work by hundreds.
    backs = list(itertools.combinations(pool[:10], 2))[:12]
    print(f"\nSweeping {len(pairs)} lead pairs from a pool of {len(pool)}, "
          f"against {len(opponents)} opponent(s), {len(backs)} back pairs each.")
    print(f"Ranked on the WORST opponent. No games played.\n")

    t0 = time.time()
    rows = []
    for i, lead in enumerate(pairs, start=1):
        got = _score_pair(lead, backs, opponents, world)
        if got is None:
            continue
        (score, _neg_losses), back, reports, worst = got
        rows.append({"lead": lead, "back": back, "score": score,
                     "covered": worst.wins, "worst": worst, "reports": reports})
        if i % 25 == 0 or i == len(pairs):
            done = time.time() - t0
            print(f"  [{i}/{len(pairs)}]  {done:.0f}s  "
                  f"({done / i:.2f}s/pair, ~{done / i * len(pairs):.0f}s total)",
                  flush=True)
    rows.sort(key=lambda r: (-r["score"], -r["covered"]))

    clean = [r for r in rows if r["score"] > 0]
    print(f"\n{len(clean)} of {len(rows)} pairs lose to NONE of their lead "
          f"pairs, on every opponent.\n")
    print(f"{'#':>3} {'lead pair':40s} {'back two':30s} {'worst opp':12s} "
          f"{'W-L':>7} {'score':>7}")
    print("-" * 104)
    for i, r in enumerate(rows[:args.top], start=1):
        w = r["worst"]
        print(f"{i:>3} {' + '.join(r['lead'])[:40]:40s} "
              f"{' + '.join(r['back'])[:30]:30s} {w.opponent[:12]:12s} "
              f"{w.wins}-{len(w.losses):<5} {r['score']:>+7.2f}")
    if rows:
        print("\nBest pair, against their hardest openings:")
        for line in ls.describe_race(rows[0]["worst"], limit=5):
            print("  " + line)

    if args.xlsx:
        from lead_scan import write_workbook
        write_workbook(rows, args.xlsx, opponents=opponents,
                       pool_size=args.pool_size)
        print(f"\nWorkbook ({len(rows)} rows): {os.path.abspath(args.xlsx)}")


if __name__ == "__main__":
    main()
