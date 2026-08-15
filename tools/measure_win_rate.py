"""How far wrong is `wins / 90`? Measured, on real teams.

Two corrections, and they are not the same size:

  P1  a cell is a PROBABILITY over the game's randomness, not one average-roll
      playout scored 0/1
  P2  their bring is CHOSEN, so the aggregate is the VALUE of the
      bring-selection game, not the uniform mean over their 90

This runs both against the number the pipeline reports today, on one real
pairing, and prints the gap. Use it to decide how much of the pipeline is worth
rebuilding on win_rate.py -- if P1 barely moves the cells, the deterministic
playout is a fine estimator and only the aggregation needs replacing.

    python3 tools/measure_win_rate.py --ours Rain --theirs "Big 6" --samples 8
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


def brings(roster, limit):
    """Bring-4s in the pipeline's own order: lead pair then back pair."""
    out = []
    for lead in itertools.combinations(roster, 2):
        rest = [x for x in roster if x not in lead]
        for back in itertools.combinations(rest, 2):
            out.append(list(lead) + list(back))
            if len(out) >= limit:
                return out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ours", default="Rain")
    ap.add_argument("--theirs", default="Big 6")
    ap.add_argument("--samples", type=int, default=8,
                    help="playouts per cell (P1). 1 reproduces the old "
                         "single-playout behaviour.")
    ap.add_argument("--our-brings", type=int, default=4)
    ap.add_argument("--their-brings", type=int, default=12)
    ap.add_argument("--turns", type=int, default=18)
    ap.add_argument("--pilot", default=ms.EQUILIBRIUM_PILOT,
                    choices=[ms.GREEDY_PILOT, ms.EQUILIBRIUM_PILOT])
    args = ap.parse_args()

    world = roster_rating.load_world()
    ours = brings(world["teams"][args.ours], args.our_brings)
    theirs = brings(world["teams"][args.theirs], args.their_brings)
    print(f"{args.ours} vs {args.theirs}   pilot={args.pilot}   "
          f"{len(ours)} of our brings x {len(theirs)} of theirs x "
          f"{args.samples} samples = {len(ours)*len(theirs)*args.samples} games\n")

    t0 = time.time()
    done = [0]

    def tick(i, j, est):
        done[0] += 1
        if done[0] % 10 == 0:
            print(f"  {done[0]}/{len(ours)*len(theirs)} cells "
                  f"({time.time()-t0:.0f}s)", flush=True)

    res = win_rate.evaluate(ours, theirs, world, on_cell=tick,
                            n_samples=args.samples, turns=args.turns,
                            pilot=args.pilot)
    print(f"\n{time.time() - t0:.0f}s\n")
    print(win_rate.describe(
        res,
        our_labels=[" / ".join(b) for b in ours],
        their_labels=[" / ".join(b) for b in theirs]))

    # --- how much of the gap is P1, and how much is P2? -------------------
    M = res["matrix"]
    ps = [c.p for row in M for c in row]
    decisive = sum(1 for p in ps if p in (0.0, 1.0))
    print(f"\nP1 -- are the cells actually probabilities?")
    print(f"  {decisive}/{len(ps)} cells came out 0% or 100% "
          f"({decisive/len(ps):.0%}); the rest are genuinely uncertain")
    if decisive < len(ps):
        mid = [p for p in ps if 0.0 < p < 1.0]
        print(f"  the uncertain ones average {statistics.mean(mid):.0%} "
              f"and would each have been scored 0 or 1")
    print(f"\nP2 -- does it matter that they CHOOSE?")
    print(f"  uniform {res['uniform']:.1%} -> game value "
          f"{res['game_value']:.1%}   "
          f"({res['game_value'] - res['uniform']:+.1%})")
    print(f"  worst case {res['worst_case']:.1%}, so mixing is worth "
          f"{res['game_value'] - res['worst_case']:+.1%}")


if __name__ == "__main__":
    main()
