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


def matchups(world, bring_variants):
    """(team_name, enemy four) pairs.

    One bring per team gives only as many games as there are teams, which was
    far too small a sample to conclude anything the first time this ran. Taking
    several different fours from each roster multiplies the sample without
    needing more teams, and varying the bring is realistic anyway -- a term that
    only helps against one particular four is not worth shipping.
    """
    out = []
    for team_name in world["teams"]:
        roster = list(world["teams"][team_name])
        default = enemy_bring(team_name, world)
        out.append((team_name, default))
        seen = {tuple(sorted(default))}
        for start in range(1, len(roster)):
            four = (roster[start:] + roster[:start])[:4]
            if len(four) < 4:
                continue
            sig = tuple(sorted(four))
            if sig in seen:
                continue
            seen.add(sig)
            out.append((team_name, four))
            if len([x for x in out if x[0] == team_name]) >= bring_variants:
                break
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weight", type=float, default=0.25,
                    help="COVERAGE_WEIGHT for configuration B (A is always 0)")
    ap.add_argument("--brings", type=int, default=4,
                    help="enemy bring variants per team (sample size multiplier)")
    args = ap.parse_args()

    world = load_world()
    baseline = Config("coverage-off", 0.0)
    candidate = Config(f"coverage-{args.weight}", args.weight)
    pairs = matchups(world, args.brings)
    print(f"{len(pairs)} games ({len(world['teams'])} teams x up to "
          f"{args.brings} brings)\n")

    wins = losses = draws = 0
    original = solver.COVERAGE_WEIGHT
    try:
        for i, (team_name, enemy4) in enumerate(pairs):
            # Swap sides on alternate matchups so any first-player edge cancels.
            if i % 2 == 0:
                result = play(OUR_TEAM, enemy4, world, candidate, baseline)
            else:
                result = -play(OUR_TEAM, enemy4, world, baseline, candidate)
            label = {1: "WIN ", -1: "LOSS", 0: "DRAW"}[result]
            print(f"  {label}  {team_name:<18} vs {','.join(x[:9] for x in enemy4)}",
                  flush=True)
            wins += result == 1
            losses += result == -1
            draws += result == 0
    finally:
        solver.COVERAGE_WEIGHT = original

    total = wins + losses + draws
    decisive = wins + losses
    print(f"\n========== {candidate.name} vs {baseline.name} ==========")
    print(f"games      : {total}")
    print(f"candidate  : {wins} W / {losses} L / {draws} D")
    if decisive:
        rate = wins / decisive
        # Normal approximation to the binomial: good enough to say whether the
        # result is distinguishable from a coin, which is the only question here.
        se = (0.25 / decisive) ** 0.5
        lo, hi = rate - 1.96 * se, rate + 1.96 * se
        print(f"win rate (excluding draws): {100 * rate:.0f}%"
              f"   95% CI [{100 * max(0, lo):.0f}%, {100 * min(1, hi):.0f}%]")
        if lo > 0.5:
            print("=> significantly BETTER than baseline")
        elif hi < 0.5:
            print("=> significantly WORSE than baseline")
        else:
            print("=> not distinguishable from baseline at this sample size")
    print("\nA candidate that is not clearly above 50% has not earned its cost.")


if __name__ == "__main__":
    main()
