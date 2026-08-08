"""Rank teams by how punishable they are against a strong opponent.

This answers "which team should I take against high-level players" with the
metric that matches the question, rather than with a win count against our own
bot. For each team in the library:

  * take its bring (lead pair + back), and each opposing team in turn
  * pick the enemy leads they would PLAUSIBLY bring (usage-weighted, section 6a)
  * play our best line and measure what a BEST-RESPONDING opponent gains on
    each turn (section 3c), with their moves drawn from the WIDER set they
    might actually be running

Lower is better. A team near the top is one a strong player struggles to punish;
a team near the bottom wins games against a passive opponent and hands a good
one free value.

Deliberately audited with the equilibrium solver, mixture sampling and the wide
opponent move space -- the configuration measured least exploitable (section
2o). Rating a team while piloting it badly measures the pilot, not the team.
"""
import argparse
import sys

from _harness import load_world, setup_battle

sys.path.insert(0, "../src")
import solver  # noqa: E402
from fast_eval import fast_pair_score  # noqa: E402
from robustness import describe_action  # noqa: E402
from team_rating import plausible_leads, rank_bringings, rate_bring  # noqa: E402


def enemy_lead_options(our_lead, enemy_roster, world, cache):
    """Their candidate leads, with our margin against each (for plausibility)."""
    import itertools
    labels, margins, leads = [], [], []
    for pair in itertools.combinations(enemy_roster, 2):
        r = fast_pair_score(tuple(our_lead), tuple(pair), world["merged"],
                            world["moves"], world["natures"], world["typechart"],
                            cache)
        leads.append(pair)
        margins.append(r["margin"])
        labels.append(" / ".join(pair))
    return leads, margins, labels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leads", type=int, default=3,
                    help="how many of their most plausible leads to audit")
    ap.add_argument("--turns", type=int, default=5)
    ap.add_argument("--greedy", action="store_true",
                    help="audit with the greedy solver instead (for comparison)")
    args = ap.parse_args()

    world = load_world()
    cache = {}
    use_nash = not args.greedy

    def build_battle(our4, enemy_lead):
        enemy_team = pairing["enemy_roster"]
        rest = [x for x in enemy_team if x not in enemy_lead]
        return setup_battle(list(our4), list(enemy_lead) + rest[:2], world)

    def choose(battle):
        with solver.solver_mode(nash=use_nash, depth=1):
            solver.seed_nash_sampling(20260808)
            action, _score, _sim = solver.solve_best_action(
                battle, "p1", battle.movesets)
        return action

    results = []
    names = list(world["teams"])
    for our_name in names:
        our_roster = list(world["teams"][our_name])
        our4 = our_roster[:4]
        ratings = []
        for enemy_name in names:
            if enemy_name == our_name:
                continue
            pairing = {"enemy_roster": list(world["teams"][enemy_name])}
            leads, margins, labels = enemy_lead_options(
                our4[:2], pairing["enemy_roster"], world, cache)
            ranked = plausible_leads(margins, labels, top_k=args.leads)
            chosen = [(leads[row["index"]], row["probability"]) for row in ranked]
            ratings.append(rate_bring(our4, chosen, build_battle, choose,
                                      max_turns=args.turns))
        merged_rating = ratings[0]
        for r in ratings[1:]:
            merged_rating.per_lead.extend(r.per_lead)
        results.append((our_name, merged_rating))
        print(f"  rated {our_name}", flush=True)

    print(f"\n===== teams ranked by how punishable they are "
          f"({'equilibrium' if use_nash else 'greedy'} solver) =====")
    print(f"{'team':<20}{'exploitability':>16}{'severe turns':>15}")
    ordered = sorted(results, key=lambda t: t[1].weighted_exploitability)
    for name, rating in ordered:
        print(f"{name:<20}{rating.weighted_exploitability:>16.1f}"
              f"{rating.severe_turns:>10}/{rating.total_turns:<4}")

    best, best_rating = ordered[0]
    worst, worst_rating = ordered[-1]
    print(f"\nMost robust : {best} ({best_rating.weighted_exploitability:.1f})")
    print(f"Least robust: {worst} ({worst_rating.weighted_exploitability:.1f})")

    hard = worst_rating.worst_lead
    if hard:
        lead, _p, rep = hard
        turn = rep.worst_turn
        print(f"\n{worst}'s hardest plausible lead: {' / '.join(lead)}")
        if turn:
            print(f"  worst turn T{turn.turn}: they gain {turn.exploitability:.0f} "
                  f"by answering")
            print(f"    {describe_action(turn.our_action)}")
            print(f"  with {describe_action(turn.punisher)}")
    print("\n(units: heuristic_eval points; KO_WEIGHT=180 => ~180 = one Pokemon)")
    print("Lower is better. This is what a good player gets for free.")


if __name__ == "__main__":
    main()
