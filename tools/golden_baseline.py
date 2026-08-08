"""Step 0c: pin current behaviour so Phase A's changes are attributable.

Phase A rewrites `heuristic_eval`, which every number in every tab depends on,
and the repo has no test suite. Without a baseline, a change that silently
re-ranks half the matchups looks exactly like a change that fixes them.

  python golden_baseline.py --write    record current behaviour to golden.json
  python golden_baseline.py            compare current behaviour to the record

This is deliberately a CHANGE DETECTOR, not a correctness test: Phase A is
*supposed* to move these numbers. The point is that every move shows up in a
diff someone has to look at and explain, rather than passing unnoticed.
"""
import argparse
import json
import os
import sys

from _harness import enemy_bring, evaluate, load_world, setup_battle, step

sys.path.insert(0, "../src")
from solver import (our_candidate_joint_actions,  # noqa: E402
                    greedy_opponent_joint_action, heuristic_eval, solve_best_action)

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 6
GOLDEN = os.path.join(os.path.dirname(__file__), "golden.json")


def describe(actions):
    """Stable text for a joint action, so diffs are readable."""
    parts = []
    for a in actions:
        target = a.targets[0].name if a.targets else "-"
        move = a.move.name if a.move else a.kind
        parts.append(f"{a.combatant.name}:{a.kind}:{move}->{target}")
    return " | ".join(sorted(parts))


def collect(world):
    out = {}
    for team_name in list(world["teams"])[:6]:
        enemy4 = enemy_bring(team_name, world)
        battle, movesets = setup_battle(OUR_TEAM, enemy4, world)
        turns = []
        for _ in range(MAX_TURNS):
            if battle.is_over():
                break
            ours = our_candidate_joint_actions(battle, battle.p1, battle.p2,
                                               movesets, battle.turn_num + 1)
            greedy = greedy_opponent_joint_action(battle, battle.p2, battle.p1,
                                                  movesets, battle.turn_num + 1)
            if not ours or not greedy:
                break
            try:
                chosen, score = solve_best_action(battle, "p1", movesets)[:2]
            except Exception as exc:  # noqa: BLE001 - recorded, not raised
                turns.append({"turn": battle.turn_num + 1, "error": repr(exc)})
                break
            turns.append({
                "turn": battle.turn_num + 1,
                "n_our_actions": len(ours),
                "eval": round(heuristic_eval(battle, "p1"), 4),
                "eval_p2": round(heuristic_eval(battle, "p2"), 4),
                "chosen": describe(chosen) if chosen else None,
                "score": round(score, 4) if isinstance(score, (int, float)) else None,
            })
            nxt = step(battle, ours[0], greedy)
            if nxt is None:
                break
            battle = nxt
        out[team_name] = {"enemy": enemy4, "turns": turns}
    return out


def diff(old, new):
    """Human-readable differences between two collections."""
    problems = []
    for team in sorted(set(old) | set(new)):
        if team not in old:
            problems.append(f"{team}: NEW team in baseline")
            continue
        if team not in new:
            problems.append(f"{team}: MISSING from current run")
            continue
        o_turns, n_turns = old[team]["turns"], new[team]["turns"]
        if len(o_turns) != len(n_turns):
            problems.append(f"{team}: turn count {len(o_turns)} -> {len(n_turns)}")
        for ot, nt in zip(o_turns, n_turns):
            for field in ("eval", "eval_p2", "chosen", "score", "n_our_actions"):
                if ot.get(field) != nt.get(field):
                    problems.append(
                        f"{team} T{ot.get('turn')}: {field}: "
                        f"{ot.get(field)!r} -> {nt.get(field)!r}")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true",
                    help="record current behaviour as the new baseline")
    args = ap.parse_args()

    world = load_world()
    current = collect(world)

    if args.write:
        with open(GOLDEN, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True)
        n = sum(len(v["turns"]) for v in current.values())
        print(f"wrote {GOLDEN}: {len(current)} matchups, {n} turns")
        return 0

    if not os.path.exists(GOLDEN):
        print(f"no baseline at {GOLDEN} -- run with --write first")
        return 2

    with open(GOLDEN, encoding="utf-8") as fh:
        baseline = json.load(fh)
    problems = diff(baseline, current)
    if not problems:
        n = sum(len(v["turns"]) for v in current.values())
        print(f"unchanged: {len(current)} matchups, {n} turns match the baseline")
        return 0
    print(f"{len(problems)} difference(s) from the baseline:")
    for p in problems:
        print(f"  {p}")
    return 1


sys.exit(main())
