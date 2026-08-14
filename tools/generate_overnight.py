"""Generate many teams, rate them by exploitability, write a shortlist.

    python generate_overnight.py --candidates 40 --effort standard --jobs 0

THE PROBLEM THIS SOLVES. Beam search scores teams on coverage and synergy
heuristics; those are the only thing steering generation, and they have no
knowledge of exploitability at all (`team_search.py` mentions it zero times).
So the objective generation optimises and the number we finally judge a team by
are two different quantities. Asking for the beam's top 3 and rating those is
therefore not a search for a low-punish team -- it is a search for a
high-coverage team that we then measure.

Nothing here changes the beam's objective, which would be a much larger piece
of work. What it does is widen the funnel: take MANY beam finalists rather than
three, rate every one of them by exploitability, and let that ranking -- not the
beam's -- decide the shortlist. That is the honest version of "generate great
teams" available today, and the residual bias is stated rather than hidden.

Resumable in the same way as search_teams: each team's rating is cached under a
key covering everything that would change it, so an interrupted overnight run
picks up where it stopped.

Output is a shortlist JSON that search_teams.py reads via --rosters, so the two
stages chain:

    generate_overnight.py --candidates 40 --effort standard --out shortlist.json
    search_teams.py --rosters shortlist.json --teams "gen01,gen02" --effort thorough
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))
# MUST come before anything that reaches numpy/pandas -- see the module
# docstring. Every import below this line is deliberately ordered, not sorted.
import blas_limits  # noqa: E402,F401

import argparse  # noqa: E402
import json  # noqa: E402
import time  # noqa: E402

import _harness  # noqa: E402,F401  (adds ../src to sys.path)

sys.path.insert(0, "../src")
import generate_team as gt  # noqa: E402
import punish_screen  # noqa: E402
import roster_rating  # noqa: E402
from search_effort import TIER_ORDER, ResultCache, relative_cost, tier  # noqa: E402
from species_data import load_preferences  # noqa: E402
from team_search import (beam_search_teams, build_candidate_pool,  # noqa: E402
                         build_pair_matrix, enemy_pairs_from_teams)

DEFAULT_CACHE = "generate_cache.json"


def _stage1_settings(args):
    """The arguments that decide WHICH teams stage 1 produces, and in what
    order. Not the ones that only decide how long it takes."""
    return {"pool_size": args.pool_size, "candidates": args.candidates,
            "generations": args.generations, "beam_width": args.beam_width,
            "effort": args.effort, "turns": args.turns,
            "optimise_sets": bool(args.optimise_sets),
            "min_winrate": args.min_winrate,
            "script_screen": bool(args.script_screen),
            "punish_screen": args.punish_screen,
            "worst_matchup": args.worst_matchup,
            # Both change WHICH teams come out and in what order, so a run that
            # varies them is a different stage 1 and must renumber loudly.
            "pilot": args.pilot, "evaluation": args.evaluation}


def _warn_if_renumbering(args):
    """Shout if this run will rewrite an existing shortlist under different
    settings.

    THE TRAP THIS CATCHES, reported from real use: you generate 60 teams from a
    pool of 50, look at the ranking, and come back with

        overnight.bat --deep-effort thorough --pick "5" --vs "Big 6"

    The stage 1 flags are gone from that command, so it silently falls back to
    the defaults (34 / 40), generates a DIFFERENT set of teams -- hours of
    re-rating, since none of them are in the cache -- and rewrites shortlist
    .json. `--pick "5"` then means a team you have never seen. The numbering is
    only stable across runs that generate the same teams, and nothing said so.
    """
    path = args.out
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, encoding="utf-8") as fh:
            previous = (json.load(fh) or {}).get("settings")
    except (OSError, ValueError):
        return
    if not previous:
        return
    now = _stage1_settings(args)
    differing = {k: (previous.get(k), now[k]) for k in now
                 if previous.get(k) != now[k]}
    if not differing:
        return
    print("=" * 78)
    print("WARNING: this run does NOT match the one that wrote "
          f"{os.path.basename(path)}.")
    for key, (was, now_v) in sorted(differing.items()):
        print(f"    {key:<14} was {was!r}, now {now_v!r}")
    print("It will therefore generate a different set of teams and RENUMBER "
          "the shortlist,\nso a --pick number will not mean what it meant "
          "before -- and none of the new\nteams are in the cache, so nothing "
          "resumes.")
    print("\nIf you meant to deep-search what you already have, stop this and "
          "run:")
    print('    overnight.bat --stage2-only --pick "N" [--vs "Big 6"]')
    print("If you meant to generate again, repeat the original stage 1 flags.")
    print("=" * 78 + "\n", flush=True)


def _beam_finalists(args, world):
    """Stage 1-3 of generate_team: pool -> pair matrix -> beam search.

    The pair matrix is the expensive part and depends only on the pool and the
    enemy teams, so it is pickled: re-running with a different --candidates or
    --effort reuses it instead of rebuilding.
    """
    import pickle

    prefs = load_preferences()
    merged = world["merged"]
    if args.generations:
        # Same mechanism generate_team.py uses, so the two agree on what a
        # generation means (a form counts as its base species').
        from species_data import (build_generation_map, load_showdown_static,
                                  parse_generations)
        allowed = parse_generations(args.generations)
        pokedex, _, _, _ = load_showdown_static()
        gen_map = build_generation_map(merged.keys(), pokedex)
        print(f"gens     : restricted to {sorted(allowed)}")
        pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs,
                                    allowed_generations=allowed,
                                    generation_map=gen_map)
    else:
        pool = build_candidate_pool(merged, top_n=args.pool_size, prefs=prefs)
    enemy_pairs = enemy_pairs_from_teams(world["teams"])
    print(f"pool     : {len(pool)} Pokemon   enemy lead pairs: {len(enemy_pairs)}")

    matrix = None
    path = args.matrix_cache
    if path and os.path.exists(path) and not args.fresh:
        with open(path, "rb") as fh:
            blob = pickle.load(fh)
        if blob.get("pool") == pool and blob.get("enemy_pairs") == enemy_pairs:
            matrix = blob["matrix"]
            print(f"matrix   : loaded from {path}")
    if matrix is None:
        t0 = time.time()
        matrix = build_pair_matrix(pool, enemy_pairs, world["merged"], world["moves"],
                                   world["natures"], world["typechart"])
        print(f"matrix   : built in {time.time() - t0:.0f}s")
        if path:
            with open(path, "wb") as fh:
                pickle.dump({"pool": pool, "enemy_pairs": enemy_pairs,
                             "matrix": matrix}, fh)

    # preferences.csv, in full. The pool above honours EXCLUDE (an excluded
    # Pokemon is never eligible), but include/prefer live in the beam, and this
    # pipeline was not passing them: an "Include" entry was merely allowed into
    # the pool rather than required in every team, and "Prefer" did nothing at
    # all. generate_team.py has always passed both, so the two entry points
    # disagreed about what the file means.
    include = [n for n in prefs["include"] if n in pool]
    prefer = [n for n in prefs["prefer"] if n in pool]
    dropped = sorted(set(prefs["include"]) - set(include))
    if include or prefer or prefs["exclude"]:
        print(f"prefs    : exclude {prefs['exclude'] or '-'} | "
              f"include {include or '-'} (in EVERY team) | "
              f"prefer {prefer or '-'} (tiebreak)")
    if dropped:
        print(f"WARNING  : include {dropped} not in the pool -- excluded, or "
              f"outside --generations. They will NOT be in the teams.")
    finals = beam_search_teams(pool, matrix, enemy_pairs, world["merged"],
                               beam_width=max(args.beam_width, args.candidates),
                               must_include=include, prefer=prefer)
    finals = [(score["total"], team) for score, team in finals][:args.candidates]
    print(f"beam     : {len(finals)} candidate teams to rate\n")
    return finals


def build_parser():
    """The parser, separate from main().

    So a test can ask what the real defaults are instead of keeping its own
    copy of them. `_stage1_settings` reads attributes off the Namespace, and a
    hand-maintained stand-in has now fallen out of step three times -- once per
    new stage 1 flag -- failing the test rather than catching a bug.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidates", type=int, default=20,
                    help="how many beam finalists to RATE. The whole point of "
                         "this tool: rate many, not three.")
    ap.add_argument("--pool-size", type=int, default=34,
                    help="how many of the top-Score Pokemon are eligible for "
                         "the beam search. THIS is the size of the search "
                         "space; --candidates only says how many of the beam's "
                         "finalists get rated. Cost grows ~quadratically.")
    ap.add_argument("--generations", default=None,
                    help="restrict the pool to these generations, e.g. '3' or "
                         "'1-5' or '1,3,5'. A form counts as its base species' "
                         "generation (Mega Lucario is gen 4).")
    ap.add_argument("--beam-width", type=int, default=30)
    ap.add_argument("--effort", choices=TIER_ORDER, default="standard")
    ap.add_argument("--turns", type=int, default=10)
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="TEAMS rated in parallel (0 = one per core). Each "
                         "worker rates a whole team against every opponent, so "
                         "this scales past the eight the old opponent-level "
                         "split was capped at -- but it can never exceed the "
                         "number of teams left to rate, which is what "
                         "--candidates controls.")
    ap.add_argument("--cache", default=DEFAULT_CACHE)
    ap.add_argument("--matrix-cache", default="matrix_cache.pkl",
                    help="pickled pair matrix, reused across runs with the "
                         "same pool and opponents")
    ap.add_argument("--fresh", action="store_true")
    ap.add_argument("--out", default="shortlist.json",
                    help="where to write the shortlist search_teams.py reads")
    ap.add_argument("--keep", type=int, default=10,
                    help="how many of the rated teams to write to --out")
    ap.add_argument("--optimise-sets", action="store_true",
                    help="optimise each member's item and four moves against "
                         "the Pokemon actually in teams.csv, instead of using "
                         "usage defaults. Cheap (1v1 damage coverage, no full "
                         "battles) and it changes what is simulated, so the "
                         "sets are part of the cache key and appear in the "
                         "Team sheets tab.")
    ap.add_argument("--worst-matchup", type=float, default=0.0, metavar="F",
                    help="reject a team whose WORST single matchup wins less "
                         "than this share of that opponent's brings, e.g. 0.89 "
                         "for 80/90. The aggregate --min-winrate cannot say "
                         "this: 90/90 seven times and 20/90 once averages 88%% "
                         "and is still a team with a hole.")
    ap.add_argument("--punish-screen", nargs="?", type=float, const=True,
                    default=None, metavar="FLOOR",
                    help="throw out a team whose OPENING is already lost, "
                         "before spending the audit on it. Solves turn 1 as a "
                         "matrix game against the leads they would pick -- "
                         "seconds per team against minutes for the audit -- and "
                         "rejects the team if the best it can guarantee is "
                         "below FLOOR (default "
                         f"{punish_screen.DEFAULT_FLOOR:.0f}). Screens on the "
                         "guaranteed value, not the punish: a team with no "
                         "threats has nothing to punish and would otherwise "
                         "screen best of all.")
    ap.add_argument("--script-screen", action="store_true",
                    help="drop a team that loses to EVERY scripted opening "
                         "(King / Hard Trick Room / Perish Trap) before the "
                         "audit. A team with no plan against a rehearsed line "
                         "is not worth auditing.")
    ap.add_argument("--pilot", default=None,
                    choices=["greedy", "equilibrium"],
                    help="WHO PLAYS THE GAMES the record comes from. Default "
                         "follows the effort tier (greedy everywhere except "
                         "--effort thorough+). 'greedy' plays our side with "
                         "the real solver and theirs with a fixed policy, "
                         "which hands whichever side is p1 a systematic "
                         "advantage -- 78%% of mirrored matchups flip "
                         "(tools/measure_side_bias.py). 'equilibrium' plays "
                         "both sides as a matrix game: a safe play is worth "
                         "what it is worth and a read is charged for being a "
                         "read. ~13x slower per game.")
    ap.add_argument("--evaluation", default=None,
                    choices=["sacrifice", "legacy"],
                    help="Which EVALUATION the solver uses. 'sacrifice' "
                         "(default) scores speed control and discounts a "
                         "Pokemon that is one hit from gone, so the solver "
                         "sacrifices instead of preserving something it cannot "
                         "use -- measured cost, record 80%% -> 74%%. 'legacy' "
                         "restores the previous evaluation exactly.")
    ap.add_argument("--min-winrate", type=float, default=0.80, metavar="F",
                    help="abandon a team as soon as its running win rate "
                         "against the opponents checked so far falls below "
                         "this (default 0.80). A team that cannot win is not "
                         "worth auditing for punishability, and the audit is "
                         "the expensive stage. Set 0 to rate everything.")
    return ap


def main():
    args = build_parser().parse_args()
    args.jobs, _warning = blas_limits.workers_advice(args.jobs)
    if _warning:
        print(f"WARNING  : {_warning}")

    world = roster_rating.load_world()
    merged, moves = world["merged"], world["moves"]
    natures, typechart, teams = (world["natures"], world["typechart"],
                                 world["teams"])

    settings = tier(args.effort)
    print(f"effort   : {settings['label']} (~{relative_cost(args.effort):.0f}x quick)")
    print(f"workers  : {args.jobs} of {os.cpu_count()} cores")

    # `--punish-screen` with no number means "use the default floor".
    # The tier carries a default pilot; --pilot overrides it. Resolved once,
    # here, so the cache key, the workers and the printed banner cannot
    # disagree about who played the games.
    from search_effort import tier as _tier
    from solver import DEFAULT_EVAL_PROFILE
    args.pilot = args.pilot or _tier(args.effort).get("pilot", "greedy")
    args.evaluation = args.evaluation or DEFAULT_EVAL_PROFILE
    print(f"pilot    : {args.pilot}   evaluation: {args.evaluation}"
          + ("   <-- record NOT inflated by the side bias"
             if args.pilot == "equilibrium" else
             "   <-- greedy: whoever is p1 has a systematic advantage "
             "(see WORKFLOW.md section 4.0)"))
    punish_floor = (punish_screen.DEFAULT_FLOOR if args.punish_screen is True
                    else args.punish_screen)
    if punish_floor is not None:
        print(f"screen   : rejecting teams whose opening cannot guarantee "
              f"{punish_floor:.0f}")

    # Before anything expensive: is this run about to renumber a shortlist the
    # user is already referring to by number?
    _warn_if_renumbering(args)

    finals = _beam_finalists(args, world)

    # Optimise item + four moves per team BEFORE anything is simulated, so the
    # rating measures the team you would actually field rather than its usage
    # defaults. This is the piece the old generate_team.py had and this tool
    # did not, which meant the pipeline rated teams running the wrong moves.
    sets_by_team = {}
    if args.optimise_sets:
        from optimize_sets import enemy_individuals, optimise_team_unique
        enemies = enemy_individuals(teams)
        print(f"sets     : optimising vs {len(enemies)} distinct Pokemon...")
        for _score, team in finals:
            opt = optimise_team_unique(team, merged, moves, natures, typechart,
                                       enemies)
            sets_by_team[tuple(sorted(team))] = {
                n: {"item": opt[n]["item"], "moves": opt[n]["moves"]}
                for n in team}
        print(f"sets     : {len(sets_by_team)} teams optimised\n")

    # A set pinned in preferences.csv -- "Mamoswine (Life Orb)", "Mega Gengar
    # (Substitute, Protect, Shadow Ball, Sludge Bomb)" -- WINS over the
    # optimiser. It is a statement about what you are bringing, so applying it
    # after optimisation is the only order that respects it, and it applies
    # whether or not --optimise-sets ran at all.
    pinned = load_preferences().get("sets") or {}
    if pinned:
        print(f"pinned   : {', '.join(f'{n} {v}' for n, v in pinned.items())}")
        for _score, team in finals:
            key = tuple(sorted(team))
            spec = dict(sets_by_team.get(key) or {})
            for name in team:
                if name in pinned:
                    spec[name] = {**(spec.get(name) or {}), **pinned[name]}
            if spec:
                sets_by_team[key] = spec
        print()

    cache = ResultCache(None if args.fresh else args.cache)
    print(f"resuming : {len(cache)} already rated\n")

    # ONE TEAM PER WORKER, many teams at once. The old loop rated teams
    # sequentially and split each one across the eight opponents inside
    # verify_with_solver, so --jobs above 8 bought nothing at all and even below
    # it the split was poor (measured: 1.56x on 4 cores, because the opponents
    # are unequal and the makespan is the slowest one). Teams are the unit with
    # enough tasks to keep any number of cores busy.
    keys, todo = [], []
    for beam_score, team in finals:
        our_sets = sets_by_team.get(tuple(sorted(team)))
        # The sets change what is simulated, so they belong in the key: an
        # optimised run must never be served a usage-default result.
        key = roster_rating.rating_key(team, args.effort, args.turns, our_sets,
                                       pilot=args.pilot,
                                       eval_profile=args.evaluation)
        keys.append(key)
        if cache.get(key) is None and not any(j["key"] == key for j in todo):
            # SCREEN FIRST, then audit -- see roster_rating.rate_team. A team
            # losing its games is not a team whose lines are worth measuring: a
            # lost position rates as unpunishable, so auditing it produces a
            # flattering number for a bad team.
            todo.append({"key": key, "team": list(team), "effort": args.effort,
                         "turns": args.turns, "our_sets": our_sets,
                         "jobs": 1, "min_winrate": args.min_winrate,
                         "script_screen": args.script_screen,
                         "punish_floor": punish_floor,
                         "worst_matchup_floor": args.worst_matchup,
                         "pilot": args.pilot,
                         "eval_profile": args.evaluation,
                         "beam_score": beam_score})

    started = time.time()
    if todo:
        workers = min(args.jobs, len(todo))
        print(f"rating   : {len(todo)} teams on {workers} worker(s)"
              + (f"  ({len(finals) - len(todo)} already cached)"
                 if len(todo) < len(finals) else ""))
        if args.jobs > len(todo):
            print(f"           --jobs {args.jobs} exceeds the {len(todo)} teams "
                  f"left to rate, so {args.jobs - len(todo)} core(s) idle. "
                  f"Raise --candidates to use them.")
        print(flush=True)

        done = 0
        for key, record in roster_rating.rate_many(todo, workers):
            cache.put(key, record)
            cache.save()      # every team is a save point: teams are slow
            done += 1
            elapsed = time.time() - started
            left = elapsed / done * (len(todo) - done) / 60
            # The screen's number is printed for teams it ACCEPTED too, and for
            # teams a LATER screen rejected. Seeing it only where it fires
            # tells you nothing about where to put the floor; seeing the whole
            # distribution does.
            guar = record.get("opening_guaranteed")
            if record.get("skipped"):
                # ...except where the reason already quotes it.
                shown = ("" if record["skipped"].startswith("opening already")
                         or guar is None else f"open {guar:.0f}   ")
                print(f"  [{done}/{len(todo)}] skipped: {record['skipped']} "
                      f"({record['wins']}/{record['total']} won)   "
                      f"{shown}~{left:.0f} min left", flush=True)
                continue
            adj = record.get("adjusted_win_rate")
            print(f"  [{done}/{len(todo)}] "
                  f"adj {f'{adj:.2f}' if adj is not None else ' n/a'}   "
                  f"punish {record['exploitability'] or float('nan'):6.1f}   "
                  + (f"open {guar:6.0f}   " if guar is not None else "")
                  + f"{record['wins']}/{record['total']} won   "
                  f"~{left:.0f} min left", flush=True)

    # Back into beam order. Results arrive out of order -- that is what keeps
    # the pool full -- so the ranking is rebuilt from the cache, not from the
    # order they finished in.
    rated = [cache.get(k) for k in keys if cache.get(k) is not None]

    # Rank by wins that hold up. Ranking on exploitability alone puts a team
    # that loses everything first, because a lost position has nothing left to
    # punish -- observed live before this was changed.
    ranked = sorted([r for r in rated if r.get("exploitability") is not None],
                    key=roster_rating.rank_key)

    def report_openings():
        """WHERE TO PUT THE FLOOR NEXT TIME. Every team the screen looked at,
        accepted or rejected, so the choice is made against a distribution
        rather than against the one number a rejection happened to print. It
        runs on the empty ranking too -- a run where nothing survived is
        exactly the run where you need to know whether the floor was the
        reason."""
        openings = sorted(r["opening_guaranteed"] for r in rated
                          if r.get("opening_guaranteed") is not None)
        if not openings:
            return
        kept = [r["opening_guaranteed"] for r in ranked
                if r.get("opening_guaranteed") is not None]
        mid = openings[len(openings) // 2]
        print(f"\nopening guaranteed across {len(openings)} teams: "
              f"worst {openings[0]:.0f}, median {mid:.0f}, "
              f"best {openings[-1]:.0f}"
              + (f"; worst KEPT team {min(kept):.0f}" if kept else "")
              + f"   (--punish-screen floor was {punish_floor:.0f})")

    if not ranked:
        print("\nNo ratings produced (quick tier does not compute them).")
        report_openings()
        return

    print("\n" + "=" * 96)
    print("GENERATED TEAMS RANKED BY HOW PUNISHABLE THEY ARE (lower is better)")
    print("=" * 96)
    for i, r in enumerate(ranked, start=1):
        share = f"{r['wins']}/{r['total']}"
        flag = ("   <-- loses most games: a lost position rates as unpunishable"
                if r["total"] and r["wins"] / r["total"] < 0.5 else "")
        radj = r.get("adjusted_win_rate")
        print(f"{i:2}. adj {f'{radj:.2f}' if radj is not None else ' n/a'}  "
              f"punish {r['exploitability']:6.1f}  {r['severe_turns']:>3} severe  "
              f"{share:>9} won{flag}")
        print(f"    {', '.join(r['team'])}")
        if r.get("worst_opponent"):
            print(f"    worst vs {r['worst_opponent']} ({r['worst_value']:.0f})")

    report_openings()

    # EVERY rated team, not just the top --keep. The numbering is the ranking,
    # so gen06 means the same team tomorrow as it does today -- which is what
    # makes "run #6 now, come back for #4 and #10 later" work at all.
    shortlist = {f"gen{i:02d}": r["team"] for i, r in enumerate(ranked, 1)}
    # Carry the SETS as well as the roster. Without them the deep search would
    # re-simulate these teams on usage defaults -- rating a different team than
    # the one generation ranked, silently.
    payload = {"teams": shortlist,
               "sets": {f"gen{i:02d}": (r.get("sets") or {})
                        for i, r in enumerate(ranked, 1)},
               # What produced this numbering. Read back by _warn_if_renumbering
               # on the next run, so re-running with different stage 1 flags
               # cannot quietly change what "gen05" refers to.
               "settings": _stage1_settings(args)}
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    # The full sheets alongside the bare rosters. Without these the workbook
    # names "gen03" and the user has no way to see WHAT gen03 is -- which
    # Pokemon, which item, which four moves -- short of re-running generation.
    from team_sheet_export import write_team_sheets
    sheets_path = os.path.splitext(args.out)[0] + "_sheets.json"
    write_team_sheets(shortlist, merged, sheets_path,
                      optimised=args.optimise_sets,
                      sets={f"gen{i:02d}": (r.get("sets") or {})
                            for i, r in enumerate(ranked, 1)})
    print(f"Team sheets: {os.path.abspath(sheets_path)}")
    print(f"\nShortlist: {os.path.abspath(args.out)}  ({len(shortlist)} teams, "
          f"numbered by rank)")
    print("The deep search is the expensive half, so pick what is worth it:")
    print(f'  overnight.bat --pick "{",".join(str(i) for i in range(1, min(args.keep, len(shortlist)) + 1))}"')
    print("  ...then come back later and run --pick with other numbers; the "
          "results accumulate\n  in the same cache and the workbook is "
          "rebuilt to include everything done so far.")
    print("\nNOTE: beam search still ranks on coverage/synergy, not "
          "exploitability.\nThis widens the funnel (rating many finalists) but "
          "does not steer generation itself.")


if __name__ == "__main__":
    main()
