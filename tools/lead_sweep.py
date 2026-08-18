"""Sweep lead pairs by the pin arithmetic, ranked on their WORST opponent.

HOW TO GET THE WORKBOOK. `--xlsx` takes a PATH and is off unless you pass one:

    cd tools
    python lead_sweep.py --vs "Big 6,Rain" --pool-size 16 --detail-top 6 ^
        --xlsx lead_book.xlsx

Two stages, and they cost very different amounts. The SWEEP scores every legal
lead pair from the pool -- cheap, and `--pool-size` drives it, pairs growing as
N^2/2. Then `--detail-top N` re-runs only the best N brings with the full
treatment: every play (stay / switch / switch+Protect / sacrifice), the mop-up,
the turn-by-turn lines with damage, the switch-in table, and the item search.
That second stage is minutes per bring, so 6-8 is a sensible number and 40 is a
night.

    python lead_sweep.py --vs "Big 6"                    console only, no file
    python lead_sweep.py --xlsx book.xlsx                every library team
    python lead_sweep.py --vs "Big 6" --pool-size 30 --detail-top 12 ^
        --xlsx book.xlsx                                 a serious run
    python lead_sweep.py --check "Ninetales-Alola,Garchomp,Mega Scizor,Rotom-Wash"
                                                         one bring, console
    python lead_sweep.py --check "Gallade,Garchomp,Mega Floette,Whimsicott" ^
        --vs "Big 6" --items "Gallade=Choice Scarf"     pin an item, console
    python lead_sweep.py --check "Gallade,Garchomp,Mega Floette,Whimsicott" ^
        --vs "Big 6" --optimise                         genuinely optimise
                                                         every item AND
                                                         moveset, console

The path is relative to wherever you run it, so `--xlsx book.xlsx` from `tools`
lands in `tools\book.xlsx`. Generated workbooks are gitignored.

ITEMS. Every Pokemon holds its MOST POPULAR item by usage already -- that is what
`make_team` does and it needs no flag. The Loadout sheet shows that baseline and
names the one swap, if any, that strictly increases the number of openings held.

`--check` prints against the usage-default item and moveset by default -- the
same cheap baseline the sweep itself ranks on. Two ways to change what it
tests, usable together:

  --optimise      genuinely search for the best legal item AND moveset for
                  each of the four against `--vs`, the same exhaustive search
                  `--xlsx`'s detail stage already runs (`_optimised_sets`),
                  instead of the usage-default set. Slower per opponent, still
                  console-only and no games played.
  --items "Gallade=Choice Scarf"
                  pin a specific LEGAL item on a named Pokemon in the bring
                  instead of searching for (or defaulting to) one -- e.g. to
                  test a specific claim directly, "Choice Scarf on Gallade
                  beats Big 6 easily". The moveset is still genuinely
                  re-optimised UNDER the pinned item (a Choice item locks you
                  into your first move used, not into one hard-coded move),
                  and the pin overrides whatever `--optimise` would otherwise
                  have searched for on that one Pokemon. Semicolon-separated
                  for more than one: "Gallade=Choice Scarf;Whimsicott=Focus
                  Sash". Checked against Regulation MB's banned list.

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


def read_preferences():
    """(exclude, prefer) name sets from data/preferences.csv.

    The real columns are `Include, Exclude, Prefer` -- one name per cell, blanks
    everywhere else. My first reader looked for a `pokemon`/`name` column and a
    "no"-ish flag, found neither, and silently excluded nothing, which is the
    quiet kind of wrong: the run looked like it had honoured the file.

    EXCLUDE is honoured by default -- an entry there is a standing judgement that
    a Pokemon is not wanted, which is exactly the "obviously terrible pairs" the
    sweep should not be spending time on. PREFER is returned so the pool can be
    ordered by it rather than filtered, since preferring something is not a claim
    that everything else is unusable.
    """
    import csv
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "data", "preferences.csv")
    exclude, prefer = set(), set()
    if not os.path.exists(path):
        return exclude, prefer
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            for key, bucket in (("Exclude", exclude), ("Prefer", prefer)):
                name = (row.get(key) or "").strip()
                if name:
                    bucket.add(name)
    return exclude, prefer


def _parse_item_overrides(spec):
    """"Gallade=Choice Scarf;Whimsicott=Focus Sash" -> {name: item}."""
    if not spec:
        return None
    out = {}
    for entry in spec.split(";"):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise SystemExit(f"--items entry {entry!r} must be 'Pokemon=Item'")
        name, item = entry.split("=", 1)
        out[name.strip()] = item.strip()
    return out


def _opponents(world, spec):
    if not spec:
        return list(world["teams"])
    return [n.strip() for n in spec.split(",") if n.strip()]


def _score_pair(lead, backs, opponents, world, top_backs=3, max_losses=0):
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
        # ONE ENGINE. The sweep used to score on the threat-matrix race while the
        # workbook rendered committed-move lines, so the console said "12 wins, 0
        # unheld" and the workbook said "loss" for the same openings. A workbook
        # that contradicts the run that produced it is worse than no workbook.
        reports = []
        for opp in opponents:
            try:
                for mega_name, variant in ls.mega_variants(world["teams"][opp],
                                                           world):
                    rep, _ms, _b = ls.full_report(
                        our4, variant, world, opponent_name=opp,
                        want_logs=False, mega_name=mega_name, plays=False,
                        max_losses=max_losses)
                    reports.append(rep)
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
    ap.add_argument("--optimise", action="store_true",
                    help="--check only: genuinely search for the best legal "
                         "item AND moveset for each of the four, instead of "
                         "the usage-default set (same search --xlsx's detail "
                         "stage already runs)")
    ap.add_argument("--items", default="", metavar="POKEMON=ITEM[;...]",
                    help="--check only: pin a specific legal item on a named "
                         "Pokemon in the bring instead of searching for one, "
                         "e.g. 'Gallade=Choice Scarf'. The moveset is still "
                         "re-optimised under the pinned item")
    ap.add_argument("--xlsx", default="", metavar="PATH",
                    help="write the workbook: brings, teams of 6, openings, "
                         "lines with damage, switch-ins and item fixes")
    ap.add_argument("--max-losses", type=int, default=0, metavar="N",
                    help="abandon a bring as soon as it has lost N of their "
                         "openings, instead of scoring all fifteen. The score is "
                         "zeroed by a single unheld opening anyway, so a bring "
                         "with five is not a candidate -- N=5 is the usual "
                         "setting and it is much the cheapest speed-up "
                         "available. 0 (default) scores everything.")
    ap.add_argument("--ignore-preferences", action="store_true",
                    help="do NOT exclude the names listed in "
                         "data/preferences.csv. By default they are dropped from "
                         "the pool, since a preferences entry is a standing "
                         "judgement that a Pokemon is not wanted.")
    ap.add_argument("--detail-top", type=int, default=8, metavar="N",
                    help="how many of the best brings get the full treatment in "
                         "the workbook (default 8). Lines and item search are "
                         "much dearer than the screen, so this is deliberately "
                         "a handful rather than all of them.")
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
        item_overrides = _parse_item_overrides(args.items)
        unknown = [n for n in (item_overrides or {}) if n not in our4]
        if unknown:
            raise SystemExit(f"--items names not in --check's bring: "
                             f"{', '.join(unknown)}")
        print()
        for opp in opponents:
            # `sets` is built PER OPPONENT: --optimise searches against this
            # one enemy roster, same as --xlsx's detail stage does per record
            # (`_optimised_sets`) -- a real team is built once, but "the best
            # item against this specific six" is still a legitimate thing to
            # ask, one opponent at a time.
            try:
                sets = ls._sets_for(our4, world, enemy_roster=world["teams"][opp],
                                    optimise=args.optimise,
                                    item_overrides=item_overrides)
            except ValueError as e:
                raise SystemExit(f"--items: {e}")
            # Both views: the per-enemy breakdown (the EXPLANATION, in the shape
            # the idea was expressed in) and the race over their 15 openings
            # (the VERDICT, which is what a screen can act on).
            for line in ls.describe(ls.scan_bring(our4, world["teams"][opp],
                                                  world, opponent_name=opp,
                                                  sets=sets)):
                print(line)
            print()
            race = ls.race_bring(our4, world["teams"][opp], world,
                                 opponent_name=opp, sets=sets)
            for line in ls.describe_race(race, limit=args.top):
                print(line)
            print()
            print(f"  Their HP budget -- what can switch in against our back "
                  f"two, and afford it:")
            for name, afford, verdict in ls.enemy_hp_budget(
                    our4, world["teams"][opp], world, sets=sets):
                print(f"    {name:20s} can afford to lose {afford * 100:+7.1f}%"
                      f"   {verdict}")
            slots = ls.mega_slots(world["teams"][opp], world)
            if len(slots) > 1:
                print(f"\n  NOTE: they can Mega any of {', '.join(slots)} and "
                      f"pick AFTER seeing your four; this reflects one choice.")
            print()
        # --check used to `return` here, so passing --xlsx alongside it silently
        # produced no file. The single-bring case is exactly where the lines are
        # most worth having, so it writes the same workbook the sweep does.
        if args.xlsx:
            records = _records_for(our4, opponents, world,
                                   item_overrides=item_overrides)
            import lead_book
            n = lead_book.build(records, args.xlsx)
            print(f"Workbook ({n} records): {os.path.abspath(args.xlsx)}")
        return

    from generate_team import build_candidate_pool
    pool = list(build_candidate_pool(world["merged"], top_n=args.pool_size))
    exclude, prefer = ((set(), set()) if args.ignore_preferences
                       else read_preferences())
    if exclude:
        before = len(pool)
        pool = [n for n in pool
                if n not in exclude and ls.species_of(n) not in exclude]
        print(f"\npreferences.csv Exclude drops {sorted(exclude)}: pool "
              f"{before} -> {len(pool)}.")
    if prefer:
        # Preferred names sort FIRST, so a truncated pool keeps them. Not a
        # filter: preferring something says nothing about the rest.
        pool.sort(key=lambda n: (0 if (n in prefer
                                      or ls.species_of(n) in prefer) else 1))
        print(f"preferences.csv Prefer puts {sorted(prefer)} first.")
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
        got = _score_pair(lead, backs, opponents, world,
                          max_losses=args.max_losses)
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
        _build_book(rows, opponents, args, world)



def _records_for(bring, opponents, world, want_items=True, item_overrides=None):
    """Full records for one bring: every opponent x every Mega they could pick.

    A bring does NOT have to beat every opponent -- "a given 4 doesn't need to
    beat every enemy team" -- so this returns one record per (opponent, Mega) and
    lets the workbook be the catalogue.

    `item_overrides` (from `--check`'s `--items`): pins a specific legal item
    on a named Pokemon in `bring` for every record, instead of letting
    `full_report`'s own exhaustive search pick one -- same `--items` pin
    `--check`'s console output already honours, carried into the workbook too
    when `--check` and `--xlsx` are combined.
    """
    out = []
    for opp in opponents:
        roster = world["teams"][opp]
        for mega_name, variant in ls.mega_variants(roster, world):
            sets = (ls._sets_for(bring, world, enemy_roster=variant, optimise=True,
                                 item_overrides=item_overrides)
                   if item_overrides else None)
            report, ms, b = ls.full_report(bring, variant, world,
                                           opponent_name=opp, mega_name=mega_name,
                                           sets=sets)
            ours = [c for c in b.p1.roster if not c.fainted]
            their_bench = [c for c in b.p2.roster if not c.fainted][2:]
            switch_ins = ls.switch_in_table(ms, b.typechart, b.field, b,
                                            ours[:2], their_bench)
            fixes = []
            if want_items:
                # EVERY losing opening, not just the worst. The first version
                # broke after one, which meant a bring with three different holes
                # was only ever offered a fix for one of them.
                for x in report.results:
                    if x.verdict == ls.LOSS:
                        for mon, item, play in ls.item_fixes(
                                bring, variant, world, x.enemy_lead):
                            fixes.append((x.enemy_lead, mon, item, play))
            out.append({"bring": tuple(bring), "opponent": opp,
                        "mega": mega_name, "score": report.score,
                        "report": report, "switch_ins": switch_ins,
                        "item_fixes": fixes,
                        "loadout": [(m, ls.default_item(m, world))
                                    for m in bring],
                        "item_swaps": ls.recommended_items(
                            bring, variant, world, mega_name=mega_name)})
    return out


def _build_book(rows, opponents, args, world):
    """Re-run the top brings at full detail and write the workbook."""
    import lead_book
    keep = [r for r in rows[:args.detail_top]]
    print(f"\nBuilding the workbook: re-running the top {len(keep)} brings at "
          f"full detail (plays, mop-up, lines, switch-ins, items)...", flush=True)
    records = []
    for i, r in enumerate(keep, start=1):
        bring = list(r["lead"]) + list(r["back"])
        records.extend(_records_for(bring, opponents, world))
        print(f"  [{i}/{len(keep)}] {' / '.join(bring)}", flush=True)
    n = lead_book.build(records, args.xlsx)
    print(f"\nWorkbook ({n} records): {os.path.abspath(args.xlsx)}")

if __name__ == "__main__":
    main()
