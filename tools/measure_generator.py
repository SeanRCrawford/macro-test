"""Is the generator producing GOOD teams? Measured against a real control.

"Good" has to mean something comparable. The control here is the eight teams in
teams.csv -- real, human-built, tournament teams -- rated through EXACTLY the
rating path a generated team goes through, against the same opponents, at the
same effort. If the generator's output cannot reach the numbers the hand-built
teams post, it is not producing good teams, whatever its internal score says.

Every team is rated against the seven OTHER library teams: rating Big 6 against
a library containing Big 6 lets it play itself, which is not a matchup either
arm should be scored on.

    python3 tools/measure_generator.py --effort standard --candidates 8
"""
import argparse
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

import roster_rating  # noqa: E402


def rate(team, world, opponents, args, label):
    t0 = time.time()
    rec = roster_rating.rate_team(
        list(team), world, effort=args.effort, turns=args.turns,
        jobs=args.jobs, opponents=list(opponents),
        pilot=args.pilot, eval_profile=args.evaluation)
    rec["label"] = label
    rec["seconds"] = time.time() - t0
    return rec


def line(rec):
    total = rec.get("total") or 0
    share = (rec.get("wins") or 0) / total if total else 0.0
    adj = rec.get("adjusted_win_rate")
    return (f"{rec['label'][:26]:26} {rec.get('wins') or 0:5}/{total:<5} "
            f"{share:6.1%}  adj {f'{adj:.2f}' if adj is not None else ' n/a'}  "
            f"punish {rec.get('exploitability') or float('nan'):6.1f}  "
            f"worst {str(rec.get('worst_opponent'))[:16]:16} "
            f"{rec['seconds']:5.0f}s")


def summarise(name, recs):
    shares = [(r.get("wins") or 0) / r["total"] for r in recs if r.get("total")]
    if not shares:
        return f"{name}: nothing rated"
    return (f"{name}: n={len(shares)}  best {max(shares):.1%}  "
            f"median {statistics.median(shares):.1%}  worst {min(shares):.1%}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--effort", default="standard")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--candidates", type=int, default=8,
                    help="how many beam finalists to rate")
    ap.add_argument("--pool-size", type=int, default=40)
    ap.add_argument("--beam-width", type=int, default=12)
    ap.add_argument("--control-only", action="store_true")
    ap.add_argument("--pilot", default=None,
                    choices=["greedy", "equilibrium"])
    ap.add_argument("--evaluation", default=None,
                    choices=["sacrifice", "legacy"])
    ap.add_argument("--only", default=None,
                    help="comma-separated control teams, for a quick sweep")
    ap.add_argument("--optimise-sets", action="store_true")
    args = ap.parse_args()

    world = roster_rating.load_world()
    names = list(world["teams"])
    picked = ([n.strip() for n in args.only.split(",")] if args.only
              else names)
    print(f"effort {args.effort}, {args.turns} turns, {args.jobs} jobs, "
          f"pilot={args.pilot or 'tier default'}, "
          f"eval={args.evaluation or 'default'}, "
          f"{len(names)} library teams\n")

    print("CONTROL -- the hand-built teams in teams.csv, each vs the other 7")
    print("-" * 100)
    control = []
    for n in picked:
        rec = rate(world["teams"][n], world,
                   [m for m in names if m != n], args, n)
        control.append(rec)
        print(line(rec), flush=True)
    print("\n" + summarise("control", control))
    if args.control_only:
        return

    # --- the generator's own output, rated identically --------------------
    from species_data import load_preferences
    from team_search import (build_candidate_pool, beam_search_teams,
                             build_pair_matrix, enemy_pairs_from_teams)
    prefs = dict(load_preferences())
    merged = world["merged"]
    pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs)
    pairs = enemy_pairs_from_teams(world["teams"])
    print(f"\nGENERATED -- pool {len(pool)}, {len(pairs)} enemy lead pairs")
    print("-" * 100, flush=True)
    t0 = time.time()
    matrix = build_pair_matrix(pool, pairs, merged, world["moves"],
                               world["natures"], world["typechart"])
    print(f"screener matrix built in {time.time() - t0:.0f}s", flush=True)
    finals = beam_search_teams(pool, matrix, pairs, merged,
                               beam_width=args.beam_width,
                               must_include=prefs.get("include"),
                               prefer=prefs.get("prefer"))
    finals = finals[:args.candidates]

    generated = []
    for i, (score, team) in enumerate(finals, 1):
        sets = None
        if args.optimise_sets:
            from optimize_sets import (optimise_team_unique as optimise_team,
                                       enemy_individuals)
            opt = optimise_team(list(team), merged, world["moves"],
                                world["natures"], world["typechart"],
                                enemy_individuals(world["teams"]))
            sets = {n: {"item": opt[n]["item"], "moves": opt[n]["moves"]}
                    for n in team}
        t1 = time.time()
        rec = roster_rating.rate_team(
            list(team), world, effort=args.effort, turns=args.turns,
            jobs=args.jobs, our_sets=sets, pilot=args.pilot,
            eval_profile=args.evaluation)
        rec["label"] = f"gen{i:02d}"
        rec["seconds"] = time.time() - t1
        generated.append(rec)
        print(line(rec) + "  " + ", ".join(team), flush=True)

    print()
    print(summarise("control  ", control))
    print(summarise("generated", generated))

    # The comparison that answers the question.
    c = sorted(((r.get("wins") or 0) / r["total"]) for r in control if r.get("total"))
    g = sorted(((r.get("wins") or 0) / r["total"]) for r in generated if r.get("total"))
    if c and g:
        beaten = sum(1 for x in g if x > statistics.median(c))
        print(f"\ngenerated teams above the CONTROL MEDIAN ({statistics.median(c):.1%}): "
              f"{beaten}/{len(g)}")
        print(f"best generated {max(g):.1%} vs best control {max(c):.1%}")


if __name__ == "__main__":
    main()
