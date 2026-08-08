"""How much does it cost to be wrong about the opponent's SET?

This sizes Phase E (belief state / ISMCTS) before building it, the way section
2b sized Phase B.

Section 5 splits hidden information in two: which four of six they brought, and
what their sets are. The first is handled structurally -- the sweep enumerates
their brings and Phase D weights them by plausibility. The second is not
handled at all, and worse, it is currently INVISIBLE:

    both sides' movesets are built with top_k=TOP_K_MOVES by usage, so the
    solver plans against the four most-used moves AND the simulated opponent
    then plays exactly those four.

The assumption is self-fulfilling. That is structurally the same error as
section 1's "we compute a best response to a policy we authored", one level
down -- an assumed SET rather than an assumed POLICY -- and it is invisible for
the same reason: nothing in the harness ever contradicts it.

So: let the opponent actually run a different plausible set while our solver
keeps planning against the usage-standard one, and measure what that costs. The
gap is the ceiling on what any belief-state modelling could recover, and
therefore the honest input to "is Phase E worth building".

IMPORTANT -- read the absolute win rates with care. The opponent here is
`greedy_opponent_joint_action`, the raw damage-maximising heuristic, NOT another
`solve_best_action` as in measure_headtohead. The expectimax solver
best-responds to exactly that function by construction, so against this opponent
its model is perfectly correct and it plays near-optimally, while the
equilibrium solver pays the usual price of assuming a competent adversary. That
makes greedy look far stronger here than it is in a fair head-to-head.

Only the WITHIN-solver comparison (matched vs surprised for one configuration)
is meaningful; the between-solver absolute rates are not comparable, and the
difference in DROP between the two is the number this tool exists to produce.
"""
import argparse
import itertools
import sys

from _harness import enemy_bring, load_world, setup_battle, step

sys.path.insert(0, "../src")
import solver  # noqa: E402
from solver import build_moveset, greedy_opponent_joint_action  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 14

# Which usage ranks the opponent's "surprise" set actually runs. Keeping ranks
# 0 and 1 is deliberate: a real off-meta set keeps the Pokemon's signature moves
# and varies the coverage slots, rather than being four unheard-of moves.
SURPRISE_RANKS = (0, 1, 4, 5)


def movesets_for(names, world, ranks=None, pool=10):
    """Movesets by usage rank. `ranks=None` gives the standard top-4."""
    out = {}
    for name in names:
        if ranks is None:
            out[name] = build_moveset(world["merged"][name], world["moves"])
        else:
            wide = build_moveset(world["merged"][name], world["moves"], top_k=pool)
            picked = [wide[i] for i in ranks if i < len(wide)]
            out[name] = picked or wide[:4]
    return out


def play(our4, enemy4, world, assumed, actual):
    """We plan against `assumed`; they actually play from `actual`.

    Returns +1 if we win, -1 if they do, 0 otherwise.
    """
    battle, _ = setup_battle(our4, enemy4, world)
    battle.movesets = assumed
    for _ in range(MAX_TURNS):
        if battle.is_over():
            break
        try:
            ours, _score, _sim = solver.solve_best_action(battle, "p1", assumed)
        except Exception:
            break
        theirs = greedy_opponent_joint_action(battle, battle.p2, battle.p1,
                                              actual, battle.turn_num + 1)
        if not ours or not theirs:
            break
        nxt = step(battle, ours, theirs)
        if nxt is None:
            break
        battle = nxt

    if battle.p2.has_lost() and not battle.p1.has_lost():
        return 1
    if battle.p1.has_lost() and not battle.p2.has_lost():
        return -1
    ours_hp = sum(c.current_hp_frac for c in battle.p1.roster if not c.fainted)
    theirs_hp = sum(c.current_hp_frac for c in battle.p2.roster if not c.fainted)
    return 1 if ours_hp - theirs_hp > 0.05 else (-1 if ours_hp - theirs_hp < -0.05 else 0)


def our_brings(world, our_variants):
    """Fours for OUR side, so the sample can exceed the 15-combination cap."""
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
    """(team, enemy four) pairs -- several fours per team, for sample size."""
    out = []
    for team_name in world["teams"]:
        roster = list(world["teams"][team_name])
        default = tuple(enemy_bring(team_name, world))
        chosen, seen = [default], {tuple(sorted(default))}
        for four in itertools.combinations(roster, 4):
            if len(chosen) >= bring_variants:
                break
            sig = tuple(sorted(four))
            if sig in seen:
                continue
            seen.add(sig)
            chosen.append(four)
        out.extend((team_name, list(f)) for f in chosen)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nash", action="store_true",
                    help="use the equilibrium solver rather than greedy")
    ap.add_argument("--brings", type=int, default=8,
                    help="enemy bring variants per team (sample size)")
    ap.add_argument("--ours", type=int, default=1,
                    help="variants for OUR four. brings alone caps at 15 (the "
                         "distinct four-of-six combinations), so this is the "
                         "only way past ~240 games.")
    args = ap.parse_args()

    world = load_world()
    tally = {"matched": [0, 0, 0], "surprised": [0, 0, 0]}

    with solver.solver_mode(nash=args.nash, depth=1):
        jobs = [(t, e, o) for (t, e) in matchups(world, args.brings)
                for o in our_brings(world, args.ours)]
        for team_name, enemy4, our4 in jobs:
            names = list(our4) + list(enemy4)
            standard = movesets_for(names, world)
            surprise = dict(standard)
            surprise.update(movesets_for(enemy4, world, ranks=SURPRISE_RANKS))

            # Same position twice: once where our assumption is right, once
            # where they run something else. Only their set differs.
            for label, actual in (("matched", standard), ("surprised", surprise)):
                r = play(our4, enemy4, world, standard, actual)
                tally[label][0 if r == 1 else (1 if r == -1 else 2)] += 1
            print(f"  done {team_name} {'/'.join(x[:8] for x in enemy4)}", flush=True)

    print("\n========== cost of being wrong about their set ==========")
    print(f"{'':<12}{'W':>5}{'L':>5}{'D':>5}{'win rate':>12}")
    rates = {}
    for label in ("matched", "surprised"):
        w, l, d = tally[label]
        rate = w / (w + l) if (w + l) else 0.0
        rates[label] = rate
        print(f"{label:<12}{w:>5}{l:>5}{d:>5}{100 * rate:>11.0f}%")
    drop = rates["matched"] - rates["surprised"]
    # Difference of two independent proportions; the interval is what decides
    # whether this is a finding or a coincidence.
    n_m = tally["matched"][0] + tally["matched"][1]
    n_s = tally["surprised"][0] + tally["surprised"][1]
    se = ((rates["matched"] * (1 - rates["matched"]) / max(n_m, 1))
          + (rates["surprised"] * (1 - rates["surprised"]) / max(n_s, 1))) ** 0.5
    lo, hi = drop - 1.96 * se, drop + 1.96 * se
    print(f"\nwin rate lost to set uncertainty : {100 * drop:+.0f} points"
          f"   95% CI [{100 * lo:+.0f}, {100 * hi:+.0f}]")
    print("significant" if lo > 0 else "NOT significant at this sample size")
    print("\nThis is the CEILING on what a belief state could recover: no amount")
    print("of modelling their set beats already knowing it. A small number means")
    print("Phase E is not worth building on this pool.")


if __name__ == "__main__":
    main()
