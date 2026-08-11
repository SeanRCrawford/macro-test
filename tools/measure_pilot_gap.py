"""How far apart are the two pilots? (WORKFLOW.md §4.4)

Two headline numbers in this project are played by different players:

    Wins / Of   the record. `matchup_search.play_out_pair` pilots OUR side with
                the greedy solver and plays THEIRS with
                `greedy_opponent_joint_action` -- a policy this codebase wrote
                itself.
    punish      the audit. `_rate_and_rerank` pilots our side with the
                equilibrium solver and advances against their EQUILIBRIUM reply.

So "this team beats 75 of their 90 brings, and concedes 56 points a turn" is two
sentences about two different pairs of players, and nothing has ever measured
how much that matters. This does: it replays the SAME configurations under both
pilots and reports where they disagree.

    python measure_pilot_gap.py                       # 1 team, 1 opponent
    python measure_pilot_gap.py --configs 30 --vs "Rain,Big 6"

Reported per pairing:

    greedy       wins under the pilot the record has always used
    equilibrium  wins under the pilot the audit uses
    flips        configurations whose WINNER changes, split by direction

The flip count is the number that matters. Two pilots winning 60 of 90 each is
not agreement if they win a different 60 -- the record would then be the right
count of the wrong games, and any plan committed to on the strength of it is
resting on which bot played.

WHAT THE GAP IS MADE OF, stated plainly because the number is easy to
over-read. Switching pilot changes two things at once:

  1. the POLICY on both sides -- equilibrium mixture and equilibrium reply,
     rather than our best response to their greedy move;
  2. the opponent's ACTION SPACE. `turn_game._enumerate` builds their columns
     from `battle.wide_movesets` -- their six most-used moves, not the four we
     plan against -- so the equilibrium pilot is also playing a strictly
     better-armed opponent.

So this measures "the record under the audit's opponent" against "the record
under the record's opponent", which is the comparison that matters for reading
the workbook, but it is not a clean ablation of the policy alone.

The turn cap matters too, and more than it looks: equilibrium lines are longer,
so a cap tuned to the greedy pilot turns wins into unresolved lines, and
unresolved is not a win. Run at the cap the audit uses (16-18) before
concluding anything about the win counts.

Cost: the equilibrium pilot solves a full payoff matrix per turn, so it is
dearer than the greedy one. That is why it is not the default anywhere -- this
script exists to say what the default costs you, not to propose changing it.
"""
import argparse
import itertools
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
import blas_limits  # noqa: E402,F401

import _harness  # noqa: E402,F401  (adds ../src to sys.path)

from matchup_search import (EQUILIBRIUM_PILOT, GREEDY_PILOT,  # noqa: E402
                            enemy_configs, play_out_worst_case)
from roster_rating import load_world  # noqa: E402

OUR_BRING = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bring", default=",".join(OUR_BRING),
                    help="our committed bring-4")
    ap.add_argument("--vs", default=None,
                    help="opponents to measure against (default: the first)")
    ap.add_argument("--configs", type=int, default=15,
                    help="how many of their 90 bring-4s to replay under both "
                         "pilots. The full 90 under the equilibrium pilot is "
                         "an hour-scale job.")
    ap.add_argument("--turns", type=int, default=10)
    args = ap.parse_args()

    world = load_world()
    our4 = [n.strip() for n in args.bring.split(",") if n.strip()]
    opponents = ([t.strip() for t in args.vs.split(",")] if args.vs
                 else [next(iter(world["teams"]))])

    print(f"our bring : {', '.join(our4)}")
    print(f"turn cap  : {args.turns}\n")
    totals = {"n": 0, "greedy": 0, "equilibrium": 0, "to_loss": 0, "to_win": 0}

    for name in opponents:
        roster = world["teams"][name]
        configs = enemy_configs(roster)
        # Evenly spread rather than the first N: their configs are generated in
        # a fixed order (lead pairs outermost), so the first 15 are all the same
        # two leads and would answer a much narrower question than intended.
        stride = max(1, len(configs) // args.configs)
        sample = configs[::stride][:args.configs]

        rows, started = [], time.time()
        for lead, back in sample:
            eb4 = list(lead) + list(back)
            outcomes = {}
            for pilot in (GREEDY_PILOT, EQUILIBRIUM_PILOT):
                winner, turns, _b = play_out_worst_case(
                    our4, eb4, world["merged"], world["moves"],
                    world["natures"], world["typechart"], args.turns,
                    pilot=pilot)
                outcomes[pilot] = (winner, turns)
            rows.append((eb4, outcomes))

        g_wins = sum(1 for _c, o in rows if o[GREEDY_PILOT][0] == "p1")
        e_wins = sum(1 for _c, o in rows if o[EQUILIBRIUM_PILOT][0] == "p1")
        to_loss = [(c, o) for c, o in rows
                   if o[GREEDY_PILOT][0] == "p1" != o[EQUILIBRIUM_PILOT][0]]
        to_win = [(c, o) for c, o in rows
                  if o[EQUILIBRIUM_PILOT][0] == "p1" != o[GREEDY_PILOT][0]]

        print(f"vs {name}  ({len(rows)} of {len(configs)} configurations, "
              f"{time.time() - started:.0f}s)")
        print(f"  greedy pilot      {g_wins:>3} / {len(rows)} won")
        print(f"  equilibrium pilot {e_wins:>3} / {len(rows)} won")
        print(f"  flips             {len(to_loss) + len(to_win)} "
              f"({len(to_loss)} greedy-only wins, {len(to_win)} "
              f"equilibrium-only wins)")
        for cfg, o in (to_loss + to_win)[:5]:
            print(f"    {' / '.join(cfg):<52} greedy {o[GREEDY_PILOT][0]:<8}"
                  f"equilibrium {o[EQUILIBRIUM_PILOT][0]}")
        print()

        totals["n"] += len(rows)
        totals["greedy"] += g_wins
        totals["equilibrium"] += e_wins
        totals["to_loss"] += len(to_loss)
        totals["to_win"] += len(to_win)

    if totals["n"]:
        flips = totals["to_loss"] + totals["to_win"]
        print("=" * 72)
        print(f"over {totals['n']} configurations: greedy {totals['greedy']}, "
              f"equilibrium {totals['equilibrium']} "
              f"({totals['equilibrium'] - totals['greedy']:+d})")
        print(f"the winner changed on {flips} of them "
              f"({flips / totals['n']:.0%}) -- that, not the difference in the "
              f"two counts,\nis how much the record depends on which pilot "
              f"played it.")


if __name__ == "__main__":
    main()
