"""Play two evaluation configurations against each other and count wins.

This fills a real gap. The other harnesses cannot settle whether an evaluation
change is an IMPROVEMENT:

  - measure_exploitability measures how exploitable the greedy solver is. Change
    the evaluation and that number moves, but it moves for two very different
    reasons -- the evaluation got better, or it just got noisier across columns
    -- and the metric cannot tell them apart.
  - golden_baseline detects change, deliberately, and says nothing about
    direction.

Winning games is the thing we actually care about, so measure that: side p1
picks with configuration A, side p2 with configuration B, same starting
position, and see who is standing at the end. Sides are swapped for half the
matchups so a first-player advantage cannot masquerade as an evaluation edge.
"""
import argparse
import sys

from _harness import enemy_bring, load_world, setup_battle, step

sys.path.insert(0, "../src")
import solver  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 14


class Config:
    """One evaluation setup, applied by mutating the solver's module globals."""

    def __init__(self, name, coverage_weight):
        self.name = name
        self.coverage_weight = coverage_weight

    def apply(self):
        solver.COVERAGE_WEIGHT = self.coverage_weight


def pick(battle, side, movesets, config):
    config.apply()
    try:
        chosen, _score, _sim = solver.solve_best_action(battle, side, movesets)
    except Exception:
        return None
    return chosen


def material(battle, side_name):
    side = battle.p1 if side_name == "p1" else battle.p2
    return sum(c.current_hp_frac for c in side.roster if not c.fainted)


def play(our4, enemy4, world, config_p1, config_p2):
    """Returns +1 if p1's configuration wins, -1 if p2's, 0 for a draw."""
    battle, movesets = setup_battle(our4, enemy4, world)
    for _ in range(MAX_TURNS):
        if battle.is_over():
            break
        a1 = pick(battle, "p1", movesets, config_p1)
        a2 = pick(battle, "p2", movesets, config_p2)
        if not a1 or not a2:
            break
        nxt = step(battle, a1, a2)
        if nxt is None:
            break
        battle = nxt

    if battle.p2.has_lost() and not battle.p1.has_lost():
        return 1
    if battle.p1.has_lost() and not battle.p2.has_lost():
        return -1
    # Timed out: fall back to material, which is what "ahead" means here.
    diff = material(battle, "p1") - material(battle, "p2")
    return 1 if diff > 0.05 else (-1 if diff < -0.05 else 0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weight", type=float, default=0.25,
                    help="COVERAGE_WEIGHT for configuration B (A is always 0)")
    args = ap.parse_args()

    world = load_world()
    baseline = Config("coverage-off", 0.0)
    candidate = Config(f"coverage-{args.weight}", args.weight)

    wins = losses = draws = 0
    original = solver.COVERAGE_WEIGHT
    try:
        for i, team_name in enumerate(world["teams"]):
            enemy4 = enemy_bring(team_name, world)
            # Swap sides on alternate matchups so any first-player edge cancels.
            if i % 2 == 0:
                result = play(OUR_TEAM, enemy4, world, candidate, baseline)
            else:
                result = -play(OUR_TEAM, enemy4, world, baseline, candidate)
            label = {1: "WIN ", -1: "LOSS", 0: "DRAW"}[result]
            print(f"  {label}  {team_name}  (candidate as "
                  f"{'p1' if i % 2 == 0 else 'p2'})", flush=True)
            wins += result == 1
            losses += result == -1
            draws += result == 0
    finally:
        solver.COVERAGE_WEIGHT = original

    total = wins + losses + draws
    print(f"\n========== {candidate.name} vs {baseline.name} ==========")
    print(f"games      : {total}")
    print(f"candidate  : {wins} W / {losses} L / {draws} D")
    if wins + losses:
        print(f"win rate (excluding draws): {100 * wins / (wins + losses):.0f}%")
    print("\nA candidate that is not clearly above 50% has not earned its cost.")


if __name__ == "__main__":
    main()
