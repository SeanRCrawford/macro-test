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
position, and see who is standing at the end.

EVERY MATCHUP IS PLAYED TWICE, once with the candidate on each side, and both
results are counted. That is not a refinement -- it is what makes the harness
mean anything. An earlier version swapped sides on ALTERNATE matchups instead,
which sounds equivalent and is not: the two halves are then different matchups,
so any per-matchup skew survives the swap. Running the null case through it
(candidate configured identically to baseline, which must by definition score
50%) returned 44%, enough bias to swamp the effects being measured. With paired
play the two games of a pair cancel exactly when the configurations agree, so
the null is 50% by construction -- verified by `--setting X --weight v
--baseline v`.

Always run that null before trusting a result from this tool.
"""
import argparse
import itertools
import sys

from _harness import enemy_bring, load_world, setup_battle, step

sys.path.insert(0, "../src")
import solver  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 14


class Config:
    """One evaluation setup, applied by mutating the solver's module globals.

    Any tunable in solver can be driven from here; the two currently swept are
    COVERAGE_WEIGHT (answer preservation, section 4b) and FUNCTIONAL_FLOOR (the
    HP shaping of section 4d).
    """

    def __init__(self, name, **settings):
        self.name = name
        self.settings = settings

    def apply(self):
        import turn_game
        for key, value in self.settings.items():
            # Settings live in solver or turn_game depending on which layer they
            # belong to; route by where the name actually exists.
            target = turn_game if hasattr(turn_game, key) else solver
            setattr(target, key, value)


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


def our_brings(world, our_variants):
    """Fours for OUR side.

    Everything before this only ever attacked with one fixed team, which caps
    the sample and also risks measuring "helps this particular team" rather than
    "is a better solver". Drawing our four from the library rosters as well
    widens both.
    """
    out = [list(OUR_TEAM)]
    if our_variants <= 1:
        return out
    for team_name in world["teams"]:
        roster = list(world["teams"][team_name])
        if len(roster) >= 4:
            out.append(roster[:4])
        if len(out) >= our_variants:
            break
    return out


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
        chosen = [tuple(default)]
        seen = {tuple(sorted(default))}
        # Genuine four-of-six combinations rather than rotations: a six-Pokemon
        # roster has 15 of them, which is the difference between a sample that
        # can resolve a five-point effect and one that cannot.
        for four in itertools.combinations(roster, 4):
            if len(chosen) >= bring_variants:
                break
            sig = tuple(sorted(four))
            if sig in seen:
                continue
            seen.add(sig)
            chosen.append(four)
        out.extend((team_name, list(four)) for four in chosen)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--setting", default="COVERAGE_WEIGHT",
                    help="solver global to vary (e.g. FUNCTIONAL_FLOOR)")
    ap.add_argument("--weight", type=float, default=0.25,
                    help="value of --setting for the candidate")
    ap.add_argument("--baseline", type=float, default=0.0,
                    help="value of --setting for the baseline")
    ap.add_argument("--brings", type=int, default=4,
                    help="enemy bring variants per team (sample size multiplier)")
    ap.add_argument("--ours", type=int, default=1,
                    help="variants for OUR four (widens the matchup pool)")
    ap.add_argument("--nash", action="store_true",
                    help="run BOTH sides with the equilibrium solver. Required "
                         "for turn_game settings such as ROLL_SCENARIOS, which "
                         "have no effect on the greedy path.")
    args = ap.parse_args()

    world = load_world()
    shared = {"NASH_SOLVER": True} if args.nash else {}
    baseline = Config(f"{args.setting}={args.baseline}",
                      **{args.setting: args.baseline}, **shared)
    candidate = Config(f"{args.setting}={args.weight}",
                       **{args.setting: args.weight}, **shared)
    pairs = matchups(world, args.brings)
    ours_list = our_brings(world, args.ours)
    jobs = [(t, e, o) for (t, e) in pairs for o in ours_list]
    print(f"{2 * len(jobs)} games ({len(pairs)} enemy matchups x "
          f"{len(ours_list)} of our brings, each played both ways)\n")

    wins = losses = draws = 0
    import turn_game
    original = {}
    for k in set(baseline.settings) | set(candidate.settings):
        target = turn_game if hasattr(turn_game, k) else solver
        original[k] = (target, getattr(target, k))
    try:
        for team_name, enemy4, our4 in jobs:
            # Paired play: the SAME matchup with the candidate on each side.
            # When the configurations agree these two cancel exactly, which is
            # what pins the null at 50%.
            results = (play(our4, enemy4, world, candidate, baseline),
                       -play(our4, enemy4, world, baseline, candidate))
            labels = "".join({1: "W", -1: "L", 0: "D"}[r] for r in results)
            print(f"  {labels}  {team_name:<18} vs "
                  f"{','.join(x[:9] for x in enemy4)}", flush=True)
            for r in results:
                wins += r == 1
                losses += r == -1
                draws += r == 0
    finally:
        for k, (target, v) in original.items():
            setattr(target, k, v)

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
