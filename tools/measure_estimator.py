"""Is the fast estimator as good as the slow one? Measured per cell.

Asked: "is there a way to speed this up without losing predictive value. For
instance, with 16 damage rolls, could instead use 3 - worst, lower third, upper
third".

That is quadrature instead of Monte Carlo, and `rolls.py` already brackets the
16 rolls with three carrying the share of the distribution each stands in for.
Crossed with the two speed-tie branches that is SIX deterministic games per
cell against twenty-four random ones.

The trade is sampling error for BIAS, and MEASURED, THE BIAS LOSES.
NAIC vs Hard Trick Room, 10 real cells, ground truth 40 random games each:

    estimator            cost   mean |err|   worst cell   ranking agreement
    old (1 game)         0.7s      0.25         0.78            96%
    quadrature (6)       4.5s      0.17         0.78            91%
    sample-8 (8)         6.1s      0.13         0.35             -
    adaptive (15.2 avg) 11.4s      0.05         0.17             -
    truth (40)          31.4s        -            -              -

ADAPTIVE WINS: a fifth of the old method's error, a third of sample-8's, at
2.7x cheaper than the ground truth. It spent 8 games on the cells that were
already settled and 24 on the ones near a coin flip, which is the whole idea.

Quadrature halves the mean error but keeps the same catastrophic worst cell --
one where the truth is 78% and quadrature says 0% -- and ranks slightly WORSE
than the single average-roll playout it was meant to replace. Plain random
sampling at the same cost beats it on every axis.

The reason is structural. Pinning every roll in a game to one index is a
CORRELATED extreme; real games roll independently, so "all rolls low" is a game
nobody plays. The mid scenario is not the median GAME, it is the
all-median-rolls game, and a line that needs one high roll somewhere in twelve
turns gets it almost always in reality and never under quadrature.

So the speed-up is not fewer roll VALUES, it is fewer GAMES where the answer is
already clear: `matchup_win_prob_adaptive` samples in batches and stops once
the Wilson interval is narrow enough. That changes only how many honest games
are played, so it cannot introduce bias -- only stop early, and the interval
says how early.

    python3 tools/measure_estimator.py --cells 12 --truth 48
"""
import argparse
import itertools
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import matchup_search as ms  # noqa: E402
import roster_rating  # noqa: E402
import win_rate  # noqa: E402


def cells(world, ours_name, theirs_name, n):
    """A spread of real matchups: our brings crossed with theirs, interleaved
    so the sample is not all one lead."""
    ours = list(world["teams"][ours_name])
    theirs = list(world["teams"][theirs_name])
    out = []
    for lead_o, lead_t in zip(itertools.cycle(itertools.combinations(ours, 2)),
                              itertools.combinations(theirs, 2)):
        rest_o = [x for x in ours if x not in lead_o]
        rest_t = [x for x in theirs if x not in lead_t]
        out.append((list(lead_o) + rest_o[:2], list(lead_t) + rest_t[:2]))
        if len(out) >= n:
            break
    return out


def old_style(our4, their4, world, turns, pilot):
    """The pipeline's number: one average-roll game, scored 0 or 1."""
    winner, _t, _b = ms.play_out_pair(
        list(our4), list(their4), world["merged"], world["moves"],
        world["natures"], world["typechart"], max_turns=turns, pilot=pilot)
    return 1.0 if winner == "p1" else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", default="NAIC")
    ap.add_argument("--theirs", default="Hard Trick Room")
    ap.add_argument("--cells", type=int, default=12)
    ap.add_argument("--truth", type=int, default=48)
    ap.add_argument("--turns", type=int, default=18)
    ap.add_argument("--pilot", default=ms.EQUILIBRIUM_PILOT)
    args = ap.parse_args()

    world = roster_rating.load_world()
    pairs = cells(world, args.ours, args.theirs, args.cells)
    print(f"{args.ours} vs {args.theirs}   {len(pairs)} cells   "
          f"ground truth = {args.truth} random games each\n")
    print(f"{'cell':>4} {'truth':>7} {'old':>7} {'quad':>7} {'samp8':>7} "
          f"{'adapt':>7} {'n':>3} "
          f"{'|old-t|':>8} {'|quad-t|':>9} {'|s8-t|':>8} {'|ad-t|':>8}")
    print("-" * 92)

    err = {"old": [], "quad": [], "s8": [], "adapt": []}
    cost = {"truth": 0.0, "old": 0.0, "quad": 0.0, "s8": 0.0, "adapt": 0.0}
    adapt_n = []
    common = dict(turns=args.turns, pilot=args.pilot)

    for k, (o4, t4) in enumerate(pairs):
        t0 = time.time()
        truth = win_rate.matchup_win_prob(o4, t4, world, n_samples=args.truth,
                                          seed=1000, **common).p
        cost["truth"] += time.time() - t0

        t0 = time.time()
        old = old_style(o4, t4, world, args.turns, args.pilot)
        cost["old"] += time.time() - t0

        t0 = time.time()
        quad = win_rate.matchup_win_prob_quadrature(o4, t4, world, **common).p
        cost["quad"] += time.time() - t0

        t0 = time.time()
        s8 = win_rate.matchup_win_prob(o4, t4, world, n_samples=8, seed=0,
                                       **common).p
        cost["s8"] += time.time() - t0

        t0 = time.time()
        ad = win_rate.matchup_win_prob_adaptive(o4, t4, world, seed=0, **common)
        cost["adapt"] += time.time() - t0
        adapt_n.append(ad.n)

        err["adapt"].append(abs(ad.p - truth))
        err["old"].append(abs(old - truth))
        err["quad"].append(abs(quad - truth))
        err["s8"].append(abs(s8 - truth))
        print(f"{k:>4} {truth:>7.2f} {old:>7.2f} {quad:>7.2f} {s8:>7.2f} "
              f"{ad.p:>7.2f} {ad.n:>3} "
              f"{err['old'][-1]:>8.2f} {err['quad'][-1]:>9.2f} "
              f"{err['s8'][-1]:>8.2f} {err['adapt'][-1]:>8.2f}", flush=True)

    print("-" * 92)
    pad = f"{'':>4} {'':>7} {'':>7} {'':>7} {'':>7} {'':>7} {'':>3} "
    print(pad + f"{statistics.mean(err['old']):>8.2f} "
          f"{statistics.mean(err['quad']):>9.2f} "
          f"{statistics.mean(err['s8']):>8.2f} "
          f"{statistics.mean(err['adapt']):>8.2f}   <- mean abs error")
    print(pad + f"{max(err['old']):>8.2f} {max(err['quad']):>9.2f} "
          f"{max(err['s8']):>8.2f} {max(err['adapt']):>8.2f}"
          f"   <- worst cell")
    print(f"\nadaptive spent {statistics.mean(adapt_n):.1f} games/cell on "
          f"average (min {min(adapt_n)}, max {max(adapt_n)})")
    print()
    for name, label in (("truth", f"truth ({args.truth} games)"),
                        ("old", "old (1 game)"),
                        ("quad", "quadrature (6 games)"),
                        ("s8", "sample-8 (8 games)"),
                        ("adapt", "adaptive (stops when sure)")):
        per = cost[name] / len(pairs)
        print(f"  {label:24} {per:6.1f} s/cell   "
              f"{cost['truth'] / cost[name]:5.1f}x cheaper than truth")

    print("\nRANKING is what the pipeline needs, not the absolute number.")
    # Spearman would need scipy; the pairwise-agreement rate is the same idea
    # and is what "does it order teams the same way" actually means.
    def agreement(a, b):
        pairs_ = [(i, j) for i in range(len(a)) for j in range(i + 1, len(a))]
        if not pairs_:
            return 1.0
        same = sum(1 for i, j in pairs_
                   if (a[i] - a[j]) * (b[i] - b[j]) >= 0)
        return same / len(pairs_)

    truths = [win_rate.matchup_win_prob(o, t, world, n_samples=args.truth,
                                        seed=1000, **common).p
              for o, t in pairs]
    olds = [old_style(o, t, world, args.turns, args.pilot) for o, t in pairs]
    quads = [win_rate.matchup_win_prob_quadrature(o, t, world, **common).p
             for o, t in pairs]
    print(f"  old vs truth        {agreement(olds, truths):.0%} of cell pairs "
          f"ordered the same")
    print(f"  quadrature vs truth {agreement(quads, truths):.0%}")


if __name__ == "__main__":
    main()
