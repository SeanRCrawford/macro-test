"""Does `--brings N` actually reach the bring that plays best?

Asked: "when I add --brings 3, am I really going to get any good lines? I'm
searching 3 out of 90 -- how are those 3 selected?"

`--brings N` sets `verify_top`, so it is literally "audit the N best of our 90
(bring-4, lead-2) configurations". The default is already small: 3 at standard,
6 at thorough, 8 at exhaustive. The N are chosen by the stage-1 screener, which
ranks on the LEAD pair's worst margin and (since the back tie-break) the back
pair's as a secondary key.

MEASURED, one real six against one real six, ground truth = wins over their
three hardest leads under the equilibrium pilot:

    only 1 of 90 configurations beats all three
    --brings  3    best found 2/3    MISSES it
    --brings  6    best found 2/3    MISSES it
    --brings  8    best found 2/3    MISSES it
    --brings 12    best found 2/3    MISSES it
    --brings 20    best found 3/3    finds it
    --brings 90    best found 3/3    finds it

The winner sits at screen rank 18. So at the default settings the answer is no:
the audit never sees the only bring that beats the hard leads.

AND THE SHORTLIST IS NARROWER THAN IT LOOKS. The screener produces ~14 distinct
lead scores over 90 configurations, so the top of the list is one big tie. How
many DIFFERENT leads a run actually audits, consistent across three pairings:

    --brings  3  ->  1 lead        --brings 12  ->  2 leads
    --brings  6  ->  1 lead        --brings 20  ->  4 leads
    --brings  8  ->  2 leads

At the default, every audited configuration shares ONE lead and differs only in
the back pair. For "find a fixed lead per opponent that withstands any of
theirs", that is the wrong shape entirely -- the search never compares leads.

A NEGATIVE RESULT, recorded so it is not retried. The obvious fix is to make the
shortlist lead-diverse: take the best configuration of each distinct lead first,
then the second of each, and so on. Measured, it is WORSE. The winning
configuration's lead scores -214.3 (a bad lead by the screen) and it is not even
the best back within that lead, so round-robin pushes it from rank 18 to rank
78, and it is missed at every N above:

    --brings 20    plain FINDS      lead-diverse misses

Which is evidence the screen's lead ranking is coarse but not worthless. The
working answer is simply to raise `--brings`, and the cost is linear:

    per pairing     brings 3    brings 20    brings 90
    standard          ~40 s       ~2 min       ~8 min
    thorough           ~1 min     ~4 min      ~17 min

    python tools/measure_brings_recall.py
    python tools/measure_brings_recall.py --hard-leads 5
"""
import argparse
import itertools
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from _harness import load_world  # noqa: E402
from fast_eval import fast_pair_score  # noqa: E402
from matchup_search import (EQUILIBRIUM_PILOT, enemy_configs,  # noqa: E402
                            play_out_worst_case)

OUR6 = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf",
        "Pelipper", "Archaludon"]


def our_configurations(pool):
    """All 90: every bring of four, every lead pair within it."""
    out, seen = [], set()
    for bring4 in itertools.combinations(pool, 4):
        for lead2 in itertools.combinations(bring4, 2):
            key = tuple(list(lead2) + [x for x in bring4 if x not in lead2])
            if key not in seen:
                seen.add(key)
                out.append(list(key))
    return out


def screen_order(our_configs, enemy6, world):
    """Stage 1 exactly as search_robust_composition ranks it: lead worst margin
    first, back worst margin as the tie-break."""
    merged, moves = world["merged"], world["moves"]
    natures, typechart = world["natures"], world["typechart"]
    configs = enemy_configs(enemy6)
    movesets, memo = {}, {}

    def margin(ours, theirs):
        if (ours, theirs) not in memo:
            memo[(ours, theirs)] = fast_pair_score(
                ours, theirs, merged, moves, natures, typechart,
                movesets)["margin"]
        return memo[(ours, theirs)]

    scored = []
    for oc in our_configs:
        lead = min(margin(tuple(oc[:2]), tuple(l)) for l, _b in configs)
        back = min(margin(tuple(oc[2:]), tuple(l)) for l, _b in configs)
        scored.append((lead, back, oc))
    scored.sort(key=lambda row: (-row[0], -row[1]))
    return scored


def hardest_leads(enemy6, n):
    """Their leads, hardest first by the screen -- the ones a plan must beat."""
    return list(itertools.combinations(enemy6, 2))[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vs", default="Rain", help="opponent team name")
    ap.add_argument("--hard-leads", type=int, default=3,
                    help="how many of their leads form the ground truth")
    ap.add_argument("--turns", type=int, default=12)
    args = ap.parse_args()

    world = load_world()
    enemy6 = world["teams"][args.vs]
    merged, moves = world["merged"], world["moves"]
    natures, typechart = world["natures"], world["typechart"]

    our_configs = our_configurations(OUR6)
    scored = screen_order(our_configs, enemy6, world)
    distinct = len({round(row[0], 6) for row in scored})
    print(f"{len(scored)} configurations, {distinct} distinct lead scores")

    for n in (3, 6, 8, 12, 20):
        leads = {tuple(row[2][:2]) for row in scored[:n]}
        print(f"  --brings {n:2d}  audits {len(leads)} distinct lead(s)")

    leads = hardest_leads(enemy6, args.hard_leads)
    print(f"\nground truth: wins over {len(leads)} of their leads, "
          f"equilibrium pilot")
    started, truth = time.time(), []
    for i, (_lead_m, _back_m, oc) in enumerate(scored):
        wins = 0
        for lead in leads:
            enemy4 = list(lead) + [x for x in enemy6 if x not in lead][:2]
            if play_out_worst_case(oc, enemy4, merged, moves, natures,
                                   typechart, args.turns,
                                   pilot=EQUILIBRIUM_PILOT)[0] == "p1":
                wins += 1
        truth.append(wins)
        if (i + 1) % 15 == 0:
            print(f"  {i + 1}/{len(scored)}  {time.time() - started:.0f}s",
                  flush=True)

    best = max(truth)
    print(f"\nbest achievable {best}/{len(leads)}, reached by "
          f"{truth.count(best)} of {len(scored)}; winner at screen rank "
          f"{truth.index(best) + 1}")
    for n in (3, 6, 8, 12, 20, 45, 90):
        found = max(truth[:n])
        print(f"  --brings {n:2d}: best found {found}/{len(leads)}   "
              + ("finds it" if found == best else "MISSES it"))


if __name__ == "__main__":
    main()
