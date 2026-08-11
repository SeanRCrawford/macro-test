"""Rate a whole 6-Pokemon roster, once, the way stage 1 rates one.

This is the block that used to live inside `tools/generate_overnight.py`'s main
loop. It moved here for one reason: the substitution loop
(`tools/substitute.py`) has to rate teams the SAME way stage 1 does, or the two
cannot be compared -- and a copy of the code would have drifted the moment
either side changed. Sharing it also means the two tools share a cache: a swap
candidate that generation already rated is never re-rated, and a team the
substitution loop discovers is already rated when stage 1 next sees it.

What "rated" means here, in order:

  1. (optional) SCRIPT SCREEN  -- drop a team with no plan against ANY of the
     rehearsed openings (King / Hard Trick Room / Perish Trap).
  2. (optional) WIN SCREEN     -- drop a team whose quick-tier win rate is
     below `min_winrate`. A lost position has nothing left to punish, so it
     rates as unpunishable; screening first stops the audit flattering a team
     that cannot win.
  3. AUDIT                     -- `verify_with_solver` at the requested effort
     tier, which is what produces exploitability and the adjusted win rate.

`rating_key` is the cache key, and it covers everything that would change the
answer: the roster, the effort tier, the turn cap and the sets. It is
deliberately unchanged from the key generate_overnight used before this module
existed, so caches from earlier runs still hit.
"""
from search_effort import ResultCache

# Bump only when a change makes an OLD cached record wrong to reuse. Adding a
# field to the record is not such a change -- readers must tolerate its absence,
# because the cache is the whole reason an overnight run can be interrupted.
# Changing the EVALUATION is such a change: every rating is a number the
# evaluation produced, so a cache written before it moved cannot be compared
# with one written after. Touching solver.SPEED_CONTROL_WEIGHT, FRAGILE_HP or
# UNSPENT_MEGA_PREMIUM means bumping this, and tools/search_teams.py's SCHEMA
# with it.
#   1 -> 2  speed control, the spent-Pokemon discount and the unspent-Mega
#           premium all switched on (WORKFLOW.md §4.2)
RATING_SCHEMA = 2


def rating_key(team, effort, turns, our_sets, opponents=None, pilot=None):
    """The resumable-cache key for one team's rating.

    `opponents` and `pilot` are appended only when they are set, so the default
    full-library greedy-pilot rating keys exactly as it did before either
    argument existed. Both change the answer, so neither may be served in place
    of the other: a rating restricted to one opponent must not stand in for one
    against eight, and a record played by the equilibrium pilot is a different
    number from the same record played greedily.
    """
    parts = ["team", RATING_SCHEMA, sorted(team), effort, turns, our_sets]
    if opponents is not None:
        parts.append(sorted(opponents))
    if pilot:
        parts.append(pilot)
    return ResultCache.key(*parts)


def load_world():
    """The dataset every rating needs, in one dict.

    Calls `generate_team.load_teams`, which also stashes the per-team metadata
    (fixed leads, known sets) that `verify_with_solver` reads back off the
    function object. Building the world any other way loses that and silently
    rates scripted opponents without their script.
    """
    import generate_team as gt
    from species_data import build_merged_dataset
    merged, _usage, moves, natures, typechart = build_merged_dataset()
    teams = gt.load_teams(merged=merged)
    return dict(merged=merged, moves=moves, natures=natures,
                typechart=typechart, teams=teams)


def _skipped(team, our_sets, beam_score, reason, wins=0, total=0):
    return {"team": list(team), "beam_score": beam_score, "sets": our_sets,
            "exploitability": None, "adjusted_win_rate": None,
            "robust_win_rate": None, "severe_turns": 0,
            "wins": wins, "total": total, "skipped": reason}


def rate_team(team, world, effort="standard", turns=10, our_sets=None, jobs=1,
              min_winrate=0.0, script_screen=False, beam_score=None,
              opponents=None, pilot=None):
    """Rate one roster. Returns the record stage 1 writes to its cache.

    `opponents` restricts which of the library's teams it is rated against --
    used by the substitution loop, which spends its budget on the opponents a
    team is actually failing rather than on the whole library.

    `pilot` chooses who plays the games the win count comes from; None keeps
    the greedy default. See WORKFLOW.md §4.4 -- the record and the punish
    numbers are otherwise played by different players.
    """
    import generate_team as gt
    merged, moves = world["merged"], world["moves"]
    natures, typechart = world["natures"], world["typechart"]
    teams = ({n: world["teams"][n] for n in opponents} if opponents
             else world["teams"])

    if script_screen:
        # One sampled config per scripted opponent, so this is cheap next to
        # the audit it protects.
        screened = gt.quick_script_screen(
            [(beam_score, list(team))], teams, {}, merged, moves, natures,
            typechart, max_turns=turns, our_sets=our_sets)
        if screened and screened[0][2]:
            return _skipped(team, our_sets, beam_score,
                            "loses every scripted opening")

    if min_winrate > 0:
        screen = gt.verify_with_solver(
            team, teams, merged, moves, natures, typechart, {}, [],
            max_turns=turns, all_backs=True, effort="quick", jobs=jobs,
            our_sets=our_sets, pilot=pilot)
        s_wins = sum((r.get("wins") or 0) for r in screen.values() if r)
        s_total = sum((r.get("total") or 0) for r in screen.values() if r)
        if s_total and s_wins / s_total < min_winrate:
            return _skipped(team, our_sets, beam_score, "below --min-winrate",
                            wins=s_wins, total=s_total)

    verdict = gt.verify_with_solver(
        team, teams, merged, moves, natures, typechart, {}, [],
        max_turns=turns, all_backs=True, effort=effort, jobs=jobs,
        our_sets=our_sets, pilot=pilot)
    return summarise(team, verdict, our_sets=our_sets, beam_score=beam_score)


def summarise(team, verdict, our_sets=None, beam_score=None):
    """Roll `verify_with_solver`'s per-opponent results into one record."""
    scores = [r["exploitability"] for r in verdict.values()
              if r and r.get("exploitability") is not None]
    adj = [r["adjusted_win_rate"] for r in verdict.values()
           if r and r.get("adjusted_win_rate") is not None]
    rob = [r["robust_win_rate"] for r in verdict.values()
           if r and r.get("robust_win_rate") is not None]
    wins = sum((r.get("wins") or 0) for r in verdict.values() if r)
    total = sum((r.get("total") or 0) for r in verdict.values() if r)
    worst = max((r for r in verdict.values()
                 if r and r.get("exploitability") is not None),
                key=lambda r: r["exploitability"], default=None)
    return {
        "team": list(team),
        "sets": our_sets,
        "beam_score": beam_score,
        "exploitability": (sum(scores) / len(scores)) if scores else None,
        "adjusted_win_rate": (sum(adj) / len(adj)) if adj else None,
        "robust_win_rate": (sum(rob) / len(rob)) if rob else None,
        "severe_turns": sum((r.get("severe_turns") or 0)
                            for r in verdict.values() if r),
        "wins": wins, "total": total,
        "worst_opponent": next((n for n, r in verdict.items() if r is worst),
                               None),
        "worst_value": worst["exploitability"] if worst else None,
        "worst_turn": worst.get("worst_turn") if worst else None,
        "per_opponent": {n: {"wins": r.get("wins"), "total": r.get("total"),
                             "exploitability": r.get("exploitability"),
                             "adjusted_win_rate": r.get("adjusted_win_rate"),
                             "robust_win_rate": r.get("robust_win_rate"),
                             "severe_turns": r.get("severe_turns"),
                             # WHICH FOUR we committed to against this
                             # opponent. The substitution loop reads it: a
                             # member no committed bring ever contains is doing
                             # nothing, and that is real-engine evidence rather
                             # than a screener opinion.
                             "our_bring4": r.get("our_bring4")}
                         for n, r in verdict.items() if r},
    }


# --------------------------------------------------------------- in parallel
#
# WHY TEAMS ARE THE PARALLEL UNIT HERE. `verify_with_solver` parallelises across
# OPPONENTS, which caps concurrency at the size of the library -- eight. Beyond
# eight cores, `--jobs` stopped buying anything, and even below it the split is
# poor: the opponents are wildly unequal (a scripted team like King measured 3-6x
# a plain one), so the makespan is the slowest opponent no matter how many
# workers are free. Measured on 4 cores: 8 opponents at --jobs 4 took 81s against
# 126s serial -- 1.56x, not 3.5x.
#
# Stage 1 rates dozens of teams, so the team is the better unit: many more tasks
# than workers, each self-contained, so the pool stays full and one slow pairing
# cannot stall the run. Each worker loads the dataset once and then keeps it.
_WORKER_WORLD = None


def worker_init():
    """Build this worker's dataset once, not once per task."""
    global _WORKER_WORLD
    _WORKER_WORLD = load_world()


def rate_team_job(job):
    """Rate one roster inside a pool worker. Top-level and plain-typed: on
    Windows the pool uses spawn and both ends cross a pickle boundary."""
    global _WORKER_WORLD
    if _WORKER_WORLD is None:
        worker_init()
    job = dict(job)
    key, team = job.pop("key"), job.pop("team")
    return key, rate_team(team, _WORKER_WORLD, **job)


def rate_many(jobs_list, workers, on_done=None):
    """Rate many rosters, `workers` at a time. Yields (key, record) as they
    finish -- out of order, which is what keeps every worker busy.

    Falls back to running in this process when there is nothing to gain, so a
    one-team run does not pay for a process pool.
    """
    if workers <= 1 or len(jobs_list) <= 1:
        for job in jobs_list:
            key, record = rate_team_job(job)
            if on_done:
                on_done(key, record)
            yield key, record
        return

    import concurrent.futures as cf
    with cf.ProcessPoolExecutor(max_workers=min(workers, len(jobs_list)),
                                initializer=worker_init) as pool:
        futures = [pool.submit(rate_team_job, job) for job in jobs_list]
        for future in cf.as_completed(futures):
            key, record = future.result()
            if on_done:
                on_done(key, record)
            yield key, record


def rank_key(record):
    """Stage 1's ordering, as one function: adjusted wins first, punish breaks
    ties. Ranking on punishability alone puts a team that loses everything
    first, because a lost position has nothing left to punish."""
    return (-(record.get("adjusted_win_rate") or 0.0),
            record.get("exploitability")
            if record.get("exploitability") is not None else float("inf"))
