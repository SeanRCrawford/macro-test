"""Sweep COVERAGE_WEIGHT: what does the answer-preservation term buy, and cost?

The term is meant to break ties between materially similar positions, not to
overrule material. Too small and it does nothing; too large and it starts
trading Pokemon away to protect a matching. This measures both ends.

Cost matters more than usual here. The term rebuilds the threat matrix on every
leaf, and section 2c already found the matrix-game solver is ~20x more expensive
than believed -- so a term that doubles the cost of a leaf is not obviously
worth it and should be seen clearly before being switched on.
"""
import sys
import time

from _harness import enemy_bring, load_world, setup_battle, step

sys.path.insert(0, "../src")
import solver  # noqa: E402
from measure_exploitability import OUR_TEAM, run, summarise  # noqa: E402
from solver import our_candidate_joint_actions  # noqa: E402

WEIGHTS = (0.0, 0.10, 0.25, 0.50, 1.00)


def eval_cost(world, repeats=400):
    """Seconds per heuristic_eval call, with the term on and off."""
    team = list(world["teams"])[0]
    battle, _ = setup_battle(OUR_TEAM, enemy_bring(team, world), world)
    out = {}
    for label, weight in (("off", 0.0), ("on", 0.25)):
        original = solver.COVERAGE_WEIGHT
        solver.COVERAGE_WEIGHT = weight
        try:
            t0 = time.perf_counter()
            for _ in range(repeats):
                solver.heuristic_eval(battle, "p1")
            out[label] = (time.perf_counter() - t0) / repeats
        finally:
            solver.COVERAGE_WEIGHT = original
    return out


def turn_cost(world, repeats=60):
    """Seconds per simulated turn, for scale -- a leaf costs run_turn + eval."""
    team = list(world["teams"])[0]
    battle, movesets = setup_battle(OUR_TEAM, enemy_bring(team, world), world)
    ours = our_candidate_joint_actions(battle, battle.p1, battle.p2, movesets, 1)
    theirs = our_candidate_joint_actions(battle, battle.p2, battle.p1, movesets, 1)
    t0 = time.perf_counter()
    for i in range(repeats):
        step(battle, ours[i % len(ours)], theirs[i % len(theirs)])
    return (time.perf_counter() - t0) / repeats


def main():
    world = load_world()

    costs = eval_cost(world)
    per_turn = turn_cost(world)
    print("========== cost ==========")
    print(f"heuristic_eval, term OFF : {costs['off'] * 1000:7.3f} ms")
    print(f"heuristic_eval, term ON  : {costs['on'] * 1000:7.3f} ms"
          f"   ({costs['on'] / costs['off']:.1f}x)")
    print(f"run_turn (for scale)     : {per_turn * 1000:7.3f} ms")
    leaf_off = per_turn + costs["off"]
    leaf_on = per_turn + costs["on"]
    print(f"=> cost of one matrix CELL: {leaf_off * 1000:7.3f} ms -> "
          f"{leaf_on * 1000:.3f} ms  ({leaf_on / leaf_off:.2f}x)")

    print("\n========== quality sweep ==========")
    print(f"{'weight':>8}{'delusion':>12}{'regret':>10}{'diff-play':>12}{'mixed':>10}")
    original = solver.COVERAGE_WEIGHT
    try:
        for weight in WEIGHTS:
            solver.COVERAGE_WEIGHT = weight
            s = summarise(run(world, verbose=False))
            print(f"{weight:>8.2f}{s['delusion']:>12.1f}{s['regret']:>10.1f}"
                  f"{s['different']:>8}/{s['n']:<3}{s['mixed']:>7}/{s['n']:<3}")
    finally:
        solver.COVERAGE_WEIGHT = original

    print("\nweight 0.00 is the Phase A1 baseline (no coverage term).")
    print("(units: heuristic_eval points; KO_WEIGHT=180 => ~180 = one Pokemon)")


main()
