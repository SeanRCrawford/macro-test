"""Play the SAME matchup from both sides. Do the two answers agree?

Every team in teams.csv scores 83-99% against the rest of the library, which
cannot be true: Rain and Big 6 both count their head-to-head as a win. A result
is only meaningful if it survives being mirrored -- if A beats B, then B must
lose to A, in the same position, with the same sets.

The suspect is the pilot. `play_out_pair`'s own docstring says our side
best-responds to `greedy_opponent_joint_action`, "a policy this codebase wrote
itself" -- so whoever is passed as p1 is played by the real solver and whoever
is passed as p2 is played by a weaker model. If that is the cause, mirroring a
matchup flips the winner rather than preserving it, and EVERY win count in the
system is inflated by however often that happens.

`--pilot equilibrium` runs both sides through the matrix solver instead, which
is the candidate fix; the point of this tool is to say how much it buys.

    python3 tools/measure_side_bias.py
    python3 tools/measure_side_bias.py --pilot equilibrium
"""
import argparse
import itertools
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import matchup_search  # noqa: E402
import roster_rating  # noqa: E402


def outcome(a4, b4, world, turns, pilot):
    """Winner of a4-as-p1 against b4-as-p2, named by TEAM rather than by side."""
    w, t, _b = matchup_search.play_out_pair(
        list(a4), list(b4), world["merged"], world["moves"], world["natures"],
        world["typechart"], max_turns=turns, pilot=pilot)
    return {"p1": "A", "p2": "B"}.get(w, w), t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=12)
    ap.add_argument("--pilot", default=matchup_search.GREEDY_PILOT,
                    choices=[matchup_search.GREEDY_PILOT,
                             matchup_search.EQUILIBRIUM_PILOT])
    ap.add_argument("--symmetric-info", action="store_true",
                    help="strip the deliberately WIDER opponent move space "
                         "(matchup_search._attach_movesets). The model gives "
                         "whoever sits in p2 six plausible moves per Pokemon "
                         "against our known four, which is correct modelling "
                         "and rides with the SEAT -- so it shows up here as a "
                         "p2 bias that is not a bug. This isolates the rest.")
    ap.add_argument("--brings", type=int, default=1,
                    help="how many bring-4s per team to mirror (1 = roster[:4])")
    args = ap.parse_args()

    if args.symmetric_info:
        matchup_search.build_wide_movesets = lambda *a, **k: {}
    world = roster_rating.load_world()
    names = list(world["teams"])
    print(f"pilot={args.pilot}  turns={args.turns}\n")
    print(f"{'matchup':38} {'A as p1':>10} {'B as p1':>10}   verdict")
    print("-" * 78)

    agree = contradict = undecided = 0
    direction = {}
    t0 = time.time()
    for a, b in itertools.combinations(names, 2):
        for k in range(args.brings):
            a4 = list(world["teams"][a])[k:k + 4]
            b4 = list(world["teams"][b])[k:k + 4]
            if len(a4) < 4 or len(b4) < 4:
                continue
            fwd, _ = outcome(a4, b4, world, args.turns, args.pilot)
            # Mirrored: same two brings, sides swapped. Relabelled back to A/B
            # so the two columns are directly comparable.
            rev_raw, _ = outcome(b4, a4, world, args.turns, args.pilot)
            rev = {"A": "B", "B": "A"}.get(rev_raw, rev_raw)
            if fwd in ("A", "B") and rev in ("A", "B"):
                if fwd == rev:
                    verdict, agree = "consistent", agree + 1
                else:
                    # WHICH WAY it contradicts is the diagnosis. "p1 wins both
                    # times" is a systematic side advantage -- one seat is
                    # played better than the other. Contradictions pointing
                    # both ways are near-ties and chaos, which is a different
                    # and much less alarming problem.
                    side = "p1 wins both" if fwd == "A" else "p2 wins both"
                    direction[side] = direction.get(side, 0) + 1
                    verdict, contradict = f"CONTRADICTION ({side})", contradict + 1
            else:
                verdict, undecided = "undecided (timeout)", undecided + 1
            print(f"{a[:18]:18} vs {b[:16]:16} {fwd:>10} {rev:>10}   {verdict}",
                  flush=True)

    decided = agree + contradict
    print("-" * 78)
    print(f"consistent      {agree}")
    print(f"CONTRADICTIONS  {contradict}"
          + (f"   ({contradict / decided:.0%} of decided matchups)" if decided else ""))
    for k, v in sorted(direction.items()):
        print(f"   of which {k:14} {v}")
    print(f"undecided       {undecided}")
    print(f"{time.time() - t0:.0f}s")
    if contradict:
        print("\nA contradiction means BOTH teams are recorded as beating the "
              "other in the same position. Every win count built on this is "
              "inflated by whatever share of games land here.")


if __name__ == "__main__":
    main()
