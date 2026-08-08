"""Does roll-aware evaluation actually pick safer plays?

Head-to-head could not resolve Phase C: 53% at n=128 decayed to 51% at n=192.
That is a real result about WIN RATE, but it is a downstream proxy, and there is
a specific reason to think it is underpowered here -- roll variance hits both
sides symmetrically, so the roll-aware player still eats the opponent's high
rolls. The effect being looked for (better decisions under variance) sits on
top of a first-order noise source the metric cannot remove.

So measure the thing Phase C is actually designed to do, directly:

    when the roll goes against us, is the roll-averse solver's chosen play
    less badly punished than the risk-neutral one's?

For each decision point, both configurations pick a play. Each play is then
scored at the WORST roll against the opponent's best response -- "if I low-roll
and they answer correctly, where am I". If Phase C works, the roll-averse pick
has the better worst case. If the two configurations pick the same play almost
always, the knob does nothing and that alone explains the flat win rate.

This is a low-noise measurement: it compares two decisions in the same position
rather than accumulating whole games.
"""
import statistics
import sys

from _harness import LOSS, enemy_bring, load_world, setup_battle, step

sys.path.insert(0, "../src")
import solver  # noqa: E402
import turn_game  # noqa: E402
from turn_game import solve_turn  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]
MAX_TURNS = 5
WORST_ROLL = 0          # index 0 is the 0.85x roll

NEUTRAL = 1.0           # plain mean over roll scenarios
AVERSE = 0.3            # reads the worst 30% of the roll distribution


def punished_value(battle, our_joint, their_actions):
    """Value of `our_joint` at the worst roll, against their best reply.

    The opponent is adversarial so this is a min over their columns; the roll is
    pinned to its worst value rather than averaged, which is the "can I be
    really punished for missing a roll" question.
    """
    worst = None
    for their_joint in their_actions:
        nxt = step(battle, our_joint, their_joint, roll_index=WORST_ROLL)
        value = LOSS if nxt is None else solver.leaf_value(nxt, "p1")
        if worst is None or value < worst:
            worst = value
    return worst


def describe(joint):
    return " + ".join(f"{a.combatant.name}:{a.move.name if a.move else a.kind}"
                      for a in joint)


def main():
    world = load_world()
    rows = []
    disagreements = 0

    original = (turn_game.ROLL_SCENARIOS, turn_game.ROLL_RISK)
    turn_game.ROLL_SCENARIOS = True
    try:
        for team_name in world["teams"]:
            battle, movesets = setup_battle(OUR_TEAM, enemy_bring(team_name, world),
                                            world)
            for _ in range(MAX_TURNS):
                if battle.is_over():
                    break

                turn_game.ROLL_RISK = NEUTRAL
                neutral = solve_turn(battle, "p1", movesets)
                turn_game.ROLL_RISK = AVERSE
                averse = solve_turn(battle, "p1", movesets)
                if not neutral.our_actions or not averse.our_actions:
                    break

                same = describe(neutral.best_action) == describe(averse.best_action)
                if not same:
                    disagreements += 1
                rows.append({
                    "team": team_name,
                    "same": same,
                    "neutral": punished_value(battle, neutral.best_action,
                                              neutral.their_actions),
                    "averse": punished_value(battle, averse.best_action,
                                             averse.their_actions),
                })

                nxt = step(battle, neutral.best_action, neutral.their_actions[0])
                if nxt is None:
                    break
                battle = nxt
            print(f"  done {team_name}", flush=True)
    finally:
        turn_game.ROLL_SCENARIOS, turn_game.ROLL_RISK = original

    if not rows:
        print("no decision points measured")
        return

    n = len(rows)
    differing = [r for r in rows if not r["same"]]
    print("\n========== roll safety ==========")
    print(f"decision points                     : {n}")
    print(f"where the two picks DIFFER          : {disagreements}/{n}"
          f"  ({100 * disagreements / n:.0f}%)")
    print(f"\nvalue at the WORST roll vs their best reply (higher = safer):")
    print(f"  risk-neutral (ROLL_RISK={NEUTRAL})      "
          f"{statistics.mean(r['neutral'] for r in rows):9.1f}")
    print(f"  roll-averse  (ROLL_RISK={AVERSE})      "
          f"{statistics.mean(r['averse'] for r in rows):9.1f}")

    if differing:
        gain = statistics.mean(r["averse"] - r["neutral"] for r in differing)
        print(f"\nOn the {len(differing)} points where the picks differ, the "
              f"roll-averse choice is")
        print(f"worth {gain:+.1f} points at the worst roll.")
        better = sum(1 for r in differing if r["averse"] > r["neutral"])
        print(f"It has the better worst case on {better}/{len(differing)}.")
    else:
        print("\nThe two configurations never disagreed -- the knob does nothing "
              "here, which\nwould by itself explain a flat head-to-head result.")
    print("\n(units: heuristic_eval points; KO_WEIGHT=180 => ~180 = one Pokemon)")


main()
