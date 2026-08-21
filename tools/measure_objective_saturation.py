"""Can the number generation RANKS ON tell two candidates apart?

`measure_pilot_gap.py` asks whether the two pilots agree about a given
configuration. This asks the harder question: whether the cheap metric can
ORDER candidates at all. A metric that says 15/15 for every candidate is not a
weak signal, it is no signal.

WHAT THIS NUMBER GOVERNS, precisely -- worth stating, because it is easy to
assume it drives the ranking and it does not. `roster_rating.rank_key` is
`(-adjusted_win_rate, exploitability)`, and both come from the AUDIT, which runs
`solver_mode(nash=True)` whatever `--pilot` says. The greedy win count feeds the
`--min-winrate` and `--worst-matchup` FILTERS and the printed `X / 90`.

So a saturated metric here does not corrupt the ranking. It makes the cheap
filters no-ops -- they pass the whole field, so they neither save audit time nor
exclude anything -- and it inflates the headline record. Saturated-high can only
fail to exclude, never wrongly reject, so this costs time rather than teams.

Measured on eight of our brings against one real six:

    greedy       spread 14-15 out of 15, 2 distinct values
    equilibrium  spread  3-7  out of 15, 4 distinct values

So the greedy record saturates. Every bring "beats almost everything", including
the one that goes on to win 3 of 15 against a real opponent. Any ranking, floor
or beam built on it is sorting a column of near-identical numbers.

This is the measurement to re-run before trusting a generation objective, and
the one to point at when a night of search returns teams that do not feel
better than each other.

    python tools/measure_objective_saturation.py
    python tools/measure_objective_saturation.py --brings 12 --turns 12
"""
import argparse
import itertools
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from _harness import load_world  # noqa: E402
from matchup_search import (EQUILIBRIUM_PILOT, GREEDY_PILOT,  # noqa: E402
                            play_out_worst_case)

OUR6 = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf",
        "Pelipper", "Archaludon"]
ENEMY6 = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl",
          "Farigiraf", "Garchomp"]


def ranks(values):
    """Dense ranks, best first. Ties share a position."""
    order = sorted(range(len(values)), key=lambda i: -values[i])
    out = [0] * len(values)
    for position, i in enumerate(order):
        out[i] = position
    return out


def spearman(a, b):
    """Rank correlation. Meaningless when one series is nearly constant, which
    is precisely the failure this tool exists to surface -- so the caller is
    told the distinct-value counts alongside it."""
    n = len(a)
    if n < 2:
        return 0.0
    ra, rb = ranks(a), ranks(b)
    d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


def wins_over_their_leads(our4, their6, world, pilot, turns):
    """One config per distinct enemy LEAD -- the unit a fixed lead has to
    survive. Their back is resolved in the battle, not at preview."""
    merged, moves = world["merged"], world["moves"]
    natures, typechart = world["natures"], world["typechart"]
    wins = 0
    leads = list(itertools.combinations(their6, 2))
    for lead in leads:
        enemy4 = list(lead) + [x for x in their6 if x not in lead][:2]
        winner, _t, _b = play_out_worst_case(our4, enemy4, merged, moves,
                                             natures, typechart, turns,
                                             pilot=pilot)
        if winner == "p1":
            wins += 1
    return wins, len(leads)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brings", type=int, default=8,
                    help="how many of our lead pairs to compare")
    ap.add_argument("--turns", type=int, default=12)
    args = ap.parse_args()

    world = load_world()
    brings = []
    for lead in itertools.combinations(OUR6, 2):
        rest = [x for x in OUR6 if x not in lead]
        brings.append(list(lead) + rest[:2])
    brings = brings[:args.brings]

    greedy, equilibrium, total = [], [], None
    for our4 in brings:
        g, total = wins_over_their_leads(our4, ENEMY6, world, GREEDY_PILOT,
                                         args.turns)
        e, _ = wins_over_their_leads(our4, ENEMY6, world, EQUILIBRIUM_PILOT,
                                     args.turns)
        greedy.append(g)
        equilibrium.append(e)
        print(f"  {our4[0] + '/' + our4[1]:40s} greedy {g:2d}/{total}"
              f"   equilibrium {e:2d}/{total}", flush=True)

    print(f"\n  greedy       spread {min(greedy)}-{max(greedy)}"
          f"   {len(set(greedy))} distinct values")
    print(f"  equilibrium  spread {min(equilibrium)}-{max(equilibrium)}"
          f"   {len(set(equilibrium))} distinct values")
    print(f"  Spearman     {spearman(greedy, equilibrium):+.2f}"
          + ("   (not meaningful -- the greedy series is nearly constant)"
             if len(set(greedy)) < 3 else ""))

    best_g = max(range(len(brings)), key=lambda i: greedy[i])
    best_e = max(range(len(brings)), key=lambda i: equilibrium[i])
    print(f"\n  greedy would pick      {'/'.join(brings[best_g][:2])}"
          f"   (really {equilibrium[best_g]}/{total})")
    print(f"  equilibrium would pick {'/'.join(brings[best_e][:2])}"
          f"   ({equilibrium[best_e]}/{total})")
    if len(set(greedy)) < 3:
        print("\n  VERDICT: the cheap metric saturates. It cannot order these "
              "candidates,\n  so a beam or a floor built on it is choosing "
              "among ties.")


if __name__ == "__main__":
    main()
