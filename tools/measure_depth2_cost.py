"""Step 0a: what does a depth-2 equilibrium solve actually cost?

The design doc requires Phase B to evaluate "this turn plus the state at the
end of next turn", and estimates the cost by extrapolating from depth-1
numbers (~10x, or ~5x with selective deepening). Those are extrapolations. This
measures four solvers at real decision points and counts the only unit that
matters -- simulated turns (`run_turn` calls) -- alongside wall time.

  brute1     full n x m matrix, solved exactly            (reference value)
  do1        double oracle, depth 1                       (the pruning claim)
  do2        double oracle, depth 2, equilibrium-valued   (what Phase B needs)
  do2_sel    depth 1, then re-solve only the top-k rows at depth 2

"Equilibrium-valued" is the part that is easy to get wrong: each cell of the
turn-t matrix must hold the SOLVED value of the turn-(t+1) game, not a greedy
playout of turn t+1. A greedy inner playout reintroduces exactly the bias the
whole redesign exists to remove, one level down, so do2 here does a real nested
solve and `do2_greedy_inner` is measured alongside to show the difference.
"""
import sys
import time

from _harness import LOSS, enemy_bring, evaluate, load_world, setup_battle, step

sys.path.insert(0, "../src")
from matrix_game import double_oracle, solve_matrix  # noqa: E402
from solver import (our_candidate_joint_actions,  # noqa: E402
                    greedy_opponent_joint_action)

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
N_TEAMS = 4
DEPTH2_TURNS = 2      # depth-2 is expensive; measure only the first N turns
TOP_K = 4             # selective deepening width
INNER_CAP = 8         # max actions per side in the inner (t+1) game


class Counter:
    """Counts simulated turns, which is the real cost unit."""

    def __init__(self):
        self.n = 0

    def step(self, battle, ours, theirs):
        self.n += 1
        return step(battle, ours, theirs)


def actions_for(battle, movesets):
    ours = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                       movesets, battle.turn_num + 1)
    theirs = our_candidate_joint_actions(battle, battle.p2, battle.p1,
                                         movesets, battle.turn_num + 1)
    return ours, theirs


def leaf_value(counter, battle, ours, theirs, oa, ta):
    nxt = counter.step(battle, ours[oa], theirs[ta])
    return LOSS if nxt is None else evaluate(nxt, "p1")


def subgame_value(counter, battle, movesets, greedy_inner=False):
    """Value of the game at `battle` -- the inner solve for depth 2."""
    if battle.is_over():
        return evaluate(battle, "p1")
    ours, theirs = actions_for(battle, movesets)
    if not ours or not theirs:
        return evaluate(battle, "p1")
    ours, theirs = ours[:INNER_CAP], theirs[:INNER_CAP]

    if greedy_inner:
        # The WRONG way, measured for comparison: assume they play the scripted
        # greedy action and take our best response to it.
        greedy = greedy_opponent_joint_action(battle, battle.p2, battle.p1,
                                              movesets, battle.turn_num + 1)
        best = LOSS
        for i in range(len(ours)):
            nxt = counter.step(battle, ours[i], greedy)
            v = LOSS if nxt is None else evaluate(nxt, "p1")
            best = max(best, v)
        return best

    res = double_oracle(len(ours), len(theirs),
                        lambda i, j: leaf_value(counter, battle, ours, theirs, i, j),
                        inner_iters=600)
    return res.value


def measure_point(battle, movesets, do_depth2):
    """Run each solver on one decision point. Returns a dict of results."""
    ours, theirs = actions_for(battle, movesets)
    if not ours or not theirs:
        return None
    n, m = len(ours), len(theirs)
    out = {"n_us": n, "n_them": m}

    # --- brute force, depth 1 (reference) ---
    c = Counter()
    t0 = time.perf_counter()
    A = [[leaf_value(c, battle, ours, theirs, i, j) for j in range(m)]
         for i in range(n)]
    value, _, _ = solve_matrix(A)
    out["brute1"] = (c.n, time.perf_counter() - t0, value)

    # --- double oracle, depth 1 ---
    c = Counter()
    t0 = time.perf_counter()
    res = double_oracle(n, m, lambda i, j: leaf_value(c, battle, ours, theirs, i, j))
    out["do1"] = (c.n, time.perf_counter() - t0, res.value)
    out["do1_converged"] = res.converged
    do1_rows = sorted(range(n), key=lambda i: -res.p[i])[:TOP_K]

    if not do_depth2:
        return out

    def depth2_cell(counter, i, j, greedy_inner=False):
        nxt = counter.step(battle, ours[i], theirs[j])
        if nxt is None:
            return LOSS
        return subgame_value(counter, nxt, movesets, greedy_inner=greedy_inner)

    # --- double oracle, depth 2, equilibrium-valued ---
    c = Counter()
    t0 = time.perf_counter()
    res2 = double_oracle(n, m, lambda i, j: depth2_cell(c, i, j))
    out["do2"] = (c.n, time.perf_counter() - t0, res2.value)
    out["do2_converged"] = res2.converged

    # --- selective deepening: depth 2 over the top-k depth-1 rows only ---
    c = Counter()
    t0 = time.perf_counter()
    res3 = double_oracle(len(do1_rows), m,
                         lambda i, j: depth2_cell(c, do1_rows[i], j))
    out["do2_sel"] = (c.n, time.perf_counter() - t0, res3.value)

    # --- depth 2 with a GREEDY inner playout (the tempting mistake) ---
    c = Counter()
    t0 = time.perf_counter()
    res4 = double_oracle(n, m, lambda i, j: depth2_cell(c, i, j, greedy_inner=True))
    out["do2_greedy"] = (c.n, time.perf_counter() - t0, res4.value)

    return out


def main():
    world = load_world()
    rows = []
    for team_name in list(world["teams"])[:N_TEAMS]:
        enemy4 = enemy_bring(team_name, world)
        battle, movesets = setup_battle(OUR_TEAM, enemy4, world)
        print(f"--- {team_name} vs {enemy4}", flush=True)
        for turn in range(DEPTH2_TURNS):
            if battle.is_over():
                break
            r = measure_point(battle, movesets, do_depth2=True)
            if r is None:
                break
            r["team"] = team_name
            r["turn"] = battle.turn_num + 1
            rows.append(r)
            print(f"  T{r['turn']} {r['n_us']}x{r['n_them']}"
                  f"  brute1={r['brute1'][0]:>5} sims {r['brute1'][1]:6.2f}s"
                  f"  do1={r['do1'][0]:>5} sims {r['do1'][1]:6.2f}s"
                  f"  do2={r['do2'][0]:>7} sims {r['do2'][1]:7.2f}s"
                  f"  do2_sel={r['do2_sel'][0]:>6} sims {r['do2_sel'][1]:6.2f}s",
                  flush=True)
            ours, theirs = actions_for(battle, movesets)
            greedy = greedy_opponent_joint_action(battle, battle.p2, battle.p1,
                                                  movesets, battle.turn_num + 1)
            nxt = step(battle, ours[0], greedy)
            if nxt is None:
                break
            battle = nxt

    if not rows:
        print("no decision points measured")
        return

    def agg(key, idx):
        vals = [r[key][idx] for r in rows if key in r]
        return sum(vals) / len(vals) if vals else float("nan")

    print("\n================ 0a: DEPTH-2 COST ================")
    print(f"decision points                    : {len(rows)}")
    print(f"mean matrix size                   : "
          f"{agg('n_us', 0) if False else sum(r['n_us'] for r in rows)/len(rows):.1f}"
          f" x {sum(r['n_them'] for r in rows)/len(rows):.1f}")
    print()
    print(f"{'solver':<12}{'sims/decision':>15}{'sec/decision':>15}{'vs do1':>10}")
    base_sims = agg("do1", 0)
    base_time = agg("do1", 1)
    for key in ("brute1", "do1", "do2", "do2_sel", "do2_greedy"):
        if not any(key in r for r in rows):
            continue
        s, t = agg(key, 0), agg(key, 1)
        print(f"{key:<12}{s:>15.0f}{t:>15.2f}{t / base_time:>9.1f}x")
    print()
    print(f"do1 pruning vs brute force         : "
          f"{100 * base_sims / agg('brute1', 0):.0f}% of the full matrix simulated")
    conv1 = sum(1 for r in rows if r.get("do1_converged"))
    conv2 = sum(1 for r in rows if r.get("do2_converged"))
    print(f"do1 converged                      : {conv1}/{len(rows)}")
    print(f"do2 converged                      : {conv2}/{len(rows)}")
    print()
    print("value agreement (heuristic_eval points):")
    d1 = [abs(r["do1"][2] - r["brute1"][2]) for r in rows]
    print(f"  |do1 - brute1|      mean {sum(d1)/len(d1):8.2f}   max {max(d1):8.2f}")
    dg = [abs(r["do2"][2] - r["do2_greedy"][2]) for r in rows if "do2_greedy" in r]
    if dg:
        print(f"  |do2 - do2_greedy|  mean {sum(dg)/len(dg):8.2f}   max {max(dg):8.2f}"
              "   <- cost of a greedy inner playout")
    ds = [abs(r["do2"][2] - r["do2_sel"][2]) for r in rows if "do2_sel" in r]
    if ds:
        print(f"  |do2 - do2_sel|     mean {sum(ds)/len(ds):8.2f}   max {max(ds):8.2f}"
              "   <- cost of selective deepening")
    print("\n(one 'sim' = one run_turn; KO_WEIGHT=180 => ~180 points = one Pokemon)")


main()
