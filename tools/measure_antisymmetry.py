"""Step 0b: how far is `heuristic_eval` from being zero-sum?

Everything downstream treats the turn as a two-player ZERO-SUM matrix game:
A[i][j] is our value, and the opponent is assumed to minimise it. That is only
coherent if our gain is exactly their loss, i.e.

    heuristic_eval(s, "p1") + heuristic_eval(s, "p2") == 0

for every state s. It is currently NOT, because the `seen` filter treats the
two sides differently -- our roster is fully known, theirs is partly hidden and
unrevealed mons get a flat placeholder. Open question 5 in the design doc asks
how much that matters. This measures it.

Reported per state:
  asym = eval(s, "p1") + eval(s, "p2")          0 if perfectly antisymmetric
  and the same after symmetrising:
  sym_eval(s, "p1") = (eval(s,"p1") - eval(s,"p2")) / 2

The symmetrised form is antisymmetric by construction, so the question is not
whether it fixes the asymmetry (it does, trivially) but whether it CHANGES THE
CHOSEN PLAY -- that is what the last block reports.
"""
import statistics
import sys

from _harness import LOSS, enemy_bring, evaluate, load_world, setup_battle, step

sys.path.insert(0, "../src")
from solver import (our_candidate_joint_actions,  # noqa: E402
                    greedy_opponent_joint_action)

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 6
N_TEAMS = 4


def symmetrised(battle):
    return (evaluate(battle, "p1") - evaluate(battle, "p2")) / 2.0


def main():
    world = load_world()
    asymmetries = []
    scales = []
    flipped_rows = 0
    total_rows = 0

    for team_name in list(world["teams"])[:N_TEAMS]:
        enemy4 = enemy_bring(team_name, world)
        battle, movesets = setup_battle(OUR_TEAM, enemy4, world)

        for _ in range(MAX_TURNS):
            if battle.is_over():
                break
            ours = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                               movesets, battle.turn_num + 1)
            theirs = our_candidate_joint_actions(battle, battle.p2, battle.p1,
                                                 movesets, battle.turn_num + 1)
            if not ours or not theirs:
                break

            # Build both matrices from the SAME simulated states, so any
            # difference is the evaluation function and nothing else.
            raw = []
            sym = []
            for oa in ours:
                raw_row, sym_row = [], []
                for ta in theirs:
                    nxt = step(battle, oa, ta)
                    if nxt is None:
                        raw_row.append(LOSS)
                        sym_row.append(LOSS)
                        continue
                    p1 = evaluate(nxt, "p1")
                    p2 = evaluate(nxt, "p2")
                    asymmetries.append(p1 + p2)
                    scales.append(abs(p1))
                    raw_row.append(p1)
                    sym_row.append((p1 - p2) / 2.0)
                raw.append(raw_row)
                sym.append(sym_row)

            # Does the asymmetry change the safest play?
            raw_pick = max(range(len(raw)), key=lambda i: min(raw[i]))
            sym_pick = max(range(len(sym)), key=lambda i: min(sym[i]))
            total_rows += 1
            if raw_pick != sym_pick:
                flipped_rows += 1

            greedy = greedy_opponent_joint_action(battle, battle.p2, battle.p1,
                                                  movesets, battle.turn_num + 1)
            nxt = step(battle, ours[raw_pick], greedy)
            if nxt is None:
                break
            battle = nxt

    if not asymmetries:
        print("no states measured")
        return

    absasym = [abs(x) for x in asymmetries]
    print("========== 0b: heuristic_eval antisymmetry ==========")
    print(f"states measured                  : {len(asymmetries)}")
    print(f"mean   |eval(p1) + eval(p2)|     : {statistics.mean(absasym):10.1f}")
    print(f"median |eval(p1) + eval(p2)|     : {statistics.median(absasym):10.1f}")
    print(f"max    |eval(p1) + eval(p2)|     : {max(absasym):10.1f}")
    print(f"mean   |eval(p1)| (for scale)    : {statistics.mean(scales):10.1f}")
    print(f"perfectly antisymmetric states   : "
          f"{sum(1 for x in absasym if x < 1e-9)}/{len(absasym)}")
    print()
    print(f"decision points                  : {total_rows}")
    print(f"maximin pick CHANGES if symmetrised: {flipped_rows}/{total_rows}"
          f"  ({100 * flipped_rows / total_rows:.0f}%)" if total_rows else "")
    print("(units: heuristic_eval points; KO_WEIGHT=180 => ~180 = one Pokemon)")


main()
