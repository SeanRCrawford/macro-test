"""How punishable is a solver's chosen play? (design doc section 3c)

Win rate against a fixed opponent is the wrong gate, and this project has the
data to prove it. In tools/measure_set_uncertainty.py the greedy solver scores
65% and the equilibrium solver 31% -- because the greedy solver best-responds to
`greedy_opponent_joint_action` BY CONSTRUCTION, and that is exactly the opponent
it is being scored against. Beating an opponent you model perfectly rewards
exploitation, not soundness. A play that wins against our own bot can still be
one a good player punishes immediately.

What actually matters for "would this hold up against a strong human" is
EXPLOITABILITY:

    exploitability(our play) = equilibrium value - worst case of that play
                             = how much a best-responding opponent gains
                               by knowing we would play it

Zero means unpunishable. Large means the play only looks good because the
opponent we imagined was not trying.

Two sources of "what a good opponent might do" are combined here, because in
real play both are live:

  1. ACTION uncertainty -- they may pick any legal joint action, not the greedy
     one. This is the min over their columns.
  2. SET uncertainty -- they may be running moves we did not assume. Their
     action space is therefore built from a WIDER moveset than the solver plans
     against (usage ranks 0-5 rather than 0-3), so "punishable by a move we did
     not expect" is counted as punishable, which is what it is.

Reported per solver configuration, over real decision points:

    exploitability   mean, and how often it is severe
    regret           how far the chosen play falls short of the safest one
    punishable rate  fraction of decisions where a good opponent gains a lot
"""
import argparse
import statistics
import sys

from _harness import LOSS, enemy_bring, load_world, setup_battle, step

sys.path.insert(0, "../src")
import solver  # noqa: E402
from matrix_game import solve_matrix  # noqa: E402
from measure_set_uncertainty import SURPRISE_RANKS, matchups  # noqa: E402
from solver import build_moveset, our_candidate_joint_actions  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 4

# A loss of this much to a best response is "severely punishable" -- about a
# third of a Pokemon at KO_WEIGHT = 180.
SEVERE = 60.0


def wide_movesets(names, world, standard, pool=10):
    """Their plausible move space: what we assume, plus what we did not.

    Union rather than replacement, because a good opponent might be running the
    standard set OR the off-meta one, and a play that is only safe against one
    of those is not safe.
    """
    out = dict(standard)
    for name in names:
        wide = build_moveset(world["merged"][name], world["moves"], top_k=pool)
        keep = list(standard.get(name) or [])
        seen = {m.name for m, _ in keep}
        for idx in SURPRISE_RANKS:
            if idx < len(wide) and wide[idx][0].name not in seen:
                keep.append(wide[idx])
                seen.add(wide[idx][0].name)
        out[name] = keep
    return out


def signature(joint):
    return frozenset((a.combatant.name, a.kind, a.move.name if a.move else None)
                     for a in joint)


def analyse_point(battle, assumed, wide, configs):
    """Exploitability of each configuration's pick at one decision point."""
    ours = our_candidate_joint_actions(battle, battle.p1, battle.p2, assumed,
                                       battle.turn_num + 1)
    # THEIR action space comes from the wider moveset: a good opponent is not
    # limited to the moves we assumed they have.
    theirs = our_candidate_joint_actions(battle, battle.p2, battle.p1, wide,
                                         battle.turn_num + 1)
    if not ours or not theirs:
        return None

    A = []
    for oa in ours:
        row = []
        for ta in theirs:
            nxt = step(battle, oa, ta)
            row.append(LOSS if nxt is None else solver.leaf_value(nxt, "p1"))
        A.append(row)

    equilibrium, _p, _q = solve_matrix(A)
    worst_per_row = [min(r) for r in A]
    maximin = max(worst_per_row)

    out = {}
    for label, cfg in configs.items():
        with solver.solver_mode(**cfg):
            try:
                chosen, _s, _sim = solver.solve_best_action(battle, "p1", assumed)
            except Exception:
                chosen = None
        if not chosen:
            continue
        want = signature(chosen)
        idx = next((i for i, oa in enumerate(ours) if signature(oa) == want), None)
        if idx is None:
            continue
        out[label] = {
            "exploitability": equilibrium - worst_per_row[idx],
            "regret": maximin - worst_per_row[idx],
            "worst": worst_per_row[idx],
        }

    # The equilibrium solver's robustness lives in its DISTRIBUTION, not in any
    # single action: the actions in a mixed strategy's support are individually
    # exploitable -- that is why they have to be mixed. Scoring only the argmax
    # therefore measures the one property mixing exists to avoid, and makes the
    # equilibrium look worse than the greedy pick. So score the mixture too:
    #
    #     exploitability(sigma) = V - min_j sum_i sigma_i A[i][j]
    #
    # The gap between the two rows is not a measurement artifact to discard --
    # it is exactly what the deployed solver gives up by collapsing the mix to a
    # single action in solve_best_action.
    from turn_game import solve_turn
    sol = solve_turn(battle, "p1", assumed)
    if sol.our_actions:
        sigma = {}
        for i, action in enumerate(sol.our_actions):
            if sol.p[i] <= 1e-9:
                continue
            sig = signature(action)
            j = next((k for k, oa in enumerate(ours) if signature(oa) == sig), None)
            if j is not None:
                sigma[j] = sigma.get(j, 0.0) + sol.p[i]
        total = sum(sigma.values())
        if total > 0:
            worst_mix = min(
                sum(w / total * A[i][j] for i, w in sigma.items())
                for j in range(len(theirs)))
            out["nash-mixed"] = {
                "exploitability": equilibrium - worst_mix,
                "regret": maximin - worst_mix,
                "worst": worst_mix,
            }
        # If a caller must have ONE pure action -- and solve_best_action must --
        # the maximin row is the defensible choice, since it explicitly
        # maximises the worst case. The argmax of the mixture does not.
        mm = sol.maximin_action
        if mm:
            sig = signature(mm)
            k = next((i for i, oa in enumerate(ours) if signature(oa) == sig), None)
            if k is not None:
                out["nash-maximin"] = {
                    "exploitability": equilibrium - worst_per_row[k],
                    "regret": maximin - worst_per_row[k],
                    "worst": worst_per_row[k],
                }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--brings", type=int, default=4)
    args = ap.parse_args()

    world = load_world()
    configs = {"greedy": dict(nash=False),
               "nash-d1": dict(nash=True, depth=1)}
    # Compare the deployed solver with and without the widened opponent move
    # space, which is the whole point of the change.
    if args.narrow:
        for _t, _e in []:
            pass
    rows = {k: [] for k in list(configs) + ["nash-mixed", "nash-maximin"]}

    for team_name, enemy4 in matchups(world, args.brings):
        battle, movesets = setup_battle(OUR_TEAM, enemy4, world)
        wide = wide_movesets(enemy4, world, movesets)
        for _ in range(MAX_TURNS):
            if battle.is_over():
                break
            got = analyse_point(battle, movesets, wide, configs)
            if not got:
                break
            for label, rec in got.items():
                rows[label].append(rec)
            with solver.solver_mode(nash=False):
                chosen, _s, _sim = solver.solve_best_action(battle, "p1", movesets)
            if not chosen:
                break
            theirs = our_candidate_joint_actions(battle, battle.p2, battle.p1,
                                                 wide, battle.turn_num + 1)
            nxt = step(battle, chosen, theirs[0])
            if nxt is None:
                break
            battle = nxt
        print(f"  done {team_name} {'/'.join(x[:8] for x in enemy4)}", flush=True)

    print("\n========== how punishable is each solver's play? ==========")
    print("(their action space includes moves we did NOT assume they have)\n")
    print(f"{'solver':<10}{'points':>8}{'exploitability':>16}{'regret':>10}"
          f"{'severely punishable':>22}")
    for label in rows:
        recs = rows[label]
        if not recs:
            continue
        exploit = [r["exploitability"] for r in recs]
        regret = [r["regret"] for r in recs]
        severe = sum(1 for x in exploit if x > SEVERE)
        print(f"{label:<10}{len(recs):>8}{statistics.mean(exploit):>16.1f}"
              f"{statistics.mean(regret):>10.1f}"
              f"{severe:>15}/{len(recs):<6} ({100 * severe / len(recs):.0f}%)")

    print("\nLower is better on every column. Exploitability is what a good")
    print("opponent gains by knowing our play; regret is how far short of the")
    print("safest available play we fell.")
    print("\n(units: heuristic_eval points; KO_WEIGHT=180 => ~180 = one Pokemon)")


if __name__ == "__main__":
    main()
