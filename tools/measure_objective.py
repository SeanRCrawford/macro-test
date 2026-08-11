"""Does a generator objective actually pick teams that WIN? (WORKFLOW.md §4.1)

    python measure_objective.py [POOL_SIZE] [TOP_N]

The beam ranks teams with a cheap scoring function, and until this existed there
was no way to tell whether a change to that function helped. Intuition is not
enough -- it lost the first time this was run.

The only test that counts: take the top teams each objective proposes, from the
SAME pool and the SAME screener matrix, and play them against the whole preset
library with the real engine. Only the scoring function differs.

First result, pool 34, top 4 each, quick tier (8 opponents x 90 configs):

    old (mean coverage)                  1927/2208  87.3%   best team 92%
    new (worst third + answer depth)     1885/2208  85.4%   best team 89%

...which is why the new terms ship at zero. See the block above SAFE_MARGIN in
src/team_search.py for what that says about where the real limit is.
"""
import os
import sys
import time

sys.path.insert(0, "/home/user/macro-test/src")
sys.path.insert(0, "/home/user/macro-test/tools")
import blas_limits  # noqa
import roster_rating
import team_search
from species_data import load_preferences
from team_search import (beam_search_teams, build_candidate_pool,
                         build_pair_matrix, enemy_pairs_from_teams)

POOL_SIZE = int(sys.argv[1]) if len(sys.argv) > 1 else 34
TOP_N = int(sys.argv[2]) if len(sys.argv) > 2 else 4

world = roster_rating.load_world()
prefs = load_preferences()
pool = build_candidate_pool(world["merged"], top_n=POOL_SIZE, prefs=prefs)
enemy_pairs = enemy_pairs_from_teams(world["teams"])
t0 = time.time()
matrix = build_pair_matrix(pool, enemy_pairs, world["merged"], world["moves"],
                           world["natures"], world["typechart"])
print(f"matrix {len(pool)} mons in {time.time()-t0:.0f}s\n")

NEW = (team_search.MATCHUP_WEIGHT, team_search.W_DOWNSIDE, team_search.W_DEPTH)


def top_teams(objective):
    """Beam finalists under one objective. The OLD one is restored by making
    `answered` behave like pairs_won (any margin counts) and removing the
    downside/depth terms in favour of the old mean-coverage weight."""
    team_search.SAFE_MARGIN, team_search.W_DOWNSIDE, team_search.W_DEPTH = (
        0.0, 0.0, 0.0)
    team_search.PLAUSIBILITY_WEIGHTED = False
    if objective == "shaped":
        team_search.SAFE_MARGIN = 20.0
        team_search.W_DOWNSIDE, team_search.W_DEPTH = NEW[1], NEW[2]
    elif objective == "plausible":
        team_search.PLAUSIBILITY_WEIGHTED = True
    elif objective == "both":
        team_search.SAFE_MARGIN = 20.0
        team_search.W_DOWNSIDE, team_search.W_DEPTH = NEW[1], NEW[2]
        team_search.PLAUSIBILITY_WEIGHTED = True
    original = team_search.score_team

    def scored(team, mtx, eps, merged, **kw):
        out = original(team, mtx, eps, merged, **kw)
        return out

    team_search.score_team = scored
    try:
        finals = beam_search_teams(pool, matrix, enemy_pairs, world["merged"],
                                   beam_width=30,
                                   must_include=[n for n in prefs["include"]
                                                 if n in pool],
                                   prefer=[n for n in prefs["prefer"]
                                           if n in pool])
    finally:
        team_search.score_team = original
    return [t for _s, t in finals[:TOP_N]]


OBJECTIVES = sys.argv[3].split(",") if len(sys.argv) > 3 else ["old", "plausible"]
picks = {name: top_teams(name) for name in OBJECTIVES}
for name, teams in picks.items():
    print(f"{name}: {len(teams)} teams")
    for t in teams:
        print("   ", ", ".join(t))

# Rate them all with the real engine, against the whole preset library.
jobs = []
seen = {}
for name, teams in picks.items():
    for i, team in enumerate(teams):
        key = f"{name}{i}"
        seen[key] = (name, team)
        jobs.append({"key": key, "team": list(team), "effort": "quick",
                     "turns": 10, "jobs": 1, "min_winrate": 0.0})

print(f"\nrating {len(jobs)} teams against {len(world['teams'])} preset teams "
      f"(quick tier, {os.cpu_count()} workers)...\n", flush=True)
results = {}
for key, record in roster_rating.rate_many(jobs, os.cpu_count()):
    results[key] = record
    name, team = seen[key]
    print(f"  {name} {record['wins']:4}/{record['total']}  "
          f"({100*record['wins']/record['total']:.0f}%)  "
          f"{', '.join(team)}", flush=True)

print()
for name in OBJECTIVES:
    rows = [r for k, r in results.items() if k.startswith(name)]
    wins = sum(r["wins"] for r in rows)
    of = sum(r["total"] for r in rows)
    best = max(rows, key=lambda r: r["wins"] / r["total"])
    print(f"{name}: {wins}/{of} ({100*wins/of:.1f}%) across {len(rows)} teams | "
          f"best team {100*best['wins']/best['total']:.0f}%")
