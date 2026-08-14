"""What does each stage of the funnel cost, and what does it let through?

Asked: "I want to screen a large number of teams (say 1000) and then refine
them down, what is the best way to do that?"

That is a budgeting question, and the budget is per-stage cost x fan-out. This
measures both ends of it:

  * how long the beam takes to emit N finalists, and -- the part that decides
    whether 1000 is worth anything -- HOW DIFFERENT those N teams actually are.
    A beam keeps the top N by score, and the top N by score are usually N
    rearrangements of one core. 1000 near-duplicates cost 1000x and search one
    neighbourhood.
  * seconds per team for each screen, so the funnel can be sized against a
    night rather than guessed at.

    python3 tools/measure_funnel.py --pool-size 50 --widths 40,200,1000
"""
import argparse
import collections
import itertools
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import roster_rating  # noqa: E402


def diversity(teams):
    """How much of the search space `teams` actually covers.

    Three numbers, because one hides the problem: distinct Pokemon used at all,
    the mean pairwise overlap (out of 6), and how many teams share the single
    most common 4-member core. High overlap means the beam is offering the same
    team with the last slot permuted.
    """
    used = {n for t in teams for n in t}
    sets = [frozenset(t) for t in teams]
    sample = sets[:200]        # pairwise is quadratic; 200 is plenty for a mean
    overlaps = [len(a & b) for a, b in itertools.combinations(sample, 2)]
    cores = collections.Counter()
    for s in sets:
        for core in itertools.combinations(sorted(s), 4):
            cores[core] += 1
    top_core, top_n = cores.most_common(1)[0] if cores else ((), 0)
    return {
        "distinct_pokemon": len(used),
        "mean_overlap": statistics.mean(overlaps) if overlaps else 0.0,
        "largest_shared_core": top_n,
        "core": top_core,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-size", type=int, default=50)
    ap.add_argument("--widths", default="40,200,1000")
    ap.add_argument("--screen-sample", type=int, default=3,
                    help="teams to time each screen on")
    args = ap.parse_args()

    from species_data import load_preferences
    from team_search import (beam_search_teams, build_candidate_pool,
                             build_pair_matrix, enemy_pairs_from_teams)

    world = roster_rating.load_world()
    merged = world["merged"]
    prefs = dict(load_preferences())
    pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs)
    pairs = enemy_pairs_from_teams(world["teams"])
    print(f"pool {len(pool)}   enemy lead pairs {len(pairs)}\n")

    t0 = time.time()
    matrix = build_pair_matrix(pool, pairs, merged, world["moves"],
                               world["natures"], world["typechart"])
    print(f"screener matrix: {time.time() - t0:5.0f}s  (ONCE, then reused)\n")

    print("BEAM -- cost of asking for N finalists, and how different they are")
    print("-" * 78)
    print(f"{'width':>6} {'seconds':>8} {'distinct':>9} {'mean overlap':>13} "
          f"{'share one 4-core':>17}")
    biggest = None
    for width in [int(w) for w in args.widths.split(",")]:
        t0 = time.time()
        finals = beam_search_teams(pool, matrix, pairs, merged,
                                   beam_width=width,
                                   must_include=prefs.get("include"),
                                   prefer=prefs.get("prefer"))
        elapsed = time.time() - t0
        teams = [t for _s, t in finals]
        d = diversity(teams)
        print(f"{width:>6} {elapsed:8.0f} {d['distinct_pokemon']:>9} "
              f"{d['mean_overlap']:>13.2f} "
              f"{d['largest_shared_core']:>8}/{len(teams):<8}")
        biggest = (teams, d)
    if biggest and biggest[1]["core"]:
        print(f"\nmost common 4-core: {', '.join(biggest[1]['core'])}")

    # --- per-team screen costs -------------------------------------------
    teams = biggest[0][:args.screen_sample] if biggest else []
    if not teams:
        return
    print(f"\nSCREENS -- seconds per team, mean of {len(teams)}")
    print("-" * 78)

    from punish_screen import screen_team
    t0 = time.time()
    for t in teams:
        screen_team(list(t), world["teams"], merged, world["moves"],
                    world["natures"], world["typechart"])
    ps = (time.time() - t0) / len(teams)
    print(f"  punish screen (opening only)      {ps:8.1f}s / team")

    import generate_team as gt
    t0 = time.time()
    for t in teams:
        gt.verify_with_solver(list(t), world["teams"], merged, world["moves"],
                              world["natures"], world["typechart"], {}, [],
                              max_turns=10, all_backs=True, effort="quick",
                              jobs=1)
    qv = (time.time() - t0) / len(teams)
    print(f"  quick verify (win count, greedy)  {qv:8.1f}s / team")

    print(f"\n{'stage':36} {'s/team':>8} {'1000 teams, 8 workers':>24}")
    print("-" * 78)
    for label, cost in (("punish screen", ps), ("quick verify", qv),
                        ("standard rating, greedy", 150.0),
                        ("standard rating, equilibrium", 600.0)):
        print(f"{label:36} {cost:8.1f} {cost * 1000 / 8 / 3600:>21.1f} h")


if __name__ == "__main__":
    main()
