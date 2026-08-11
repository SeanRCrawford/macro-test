"""Is this team obviously hopeless? One turn, one matrix, seconds per team.

WHY THIS EXISTS. The full rating plays every line to a finish and solves a
payoff matrix on every turn -- minutes per team, hours for a generation run.
That is the right tool for choosing between good teams and far too slow for
throwing out bad ones, so a generation run spends most of its night auditing
teams a competent player would dismantle immediately.

This is the cheap end of the same question. It looks at the OPENING position
only -- their plausible lead against ours, before anything has happened -- and
solves that one turn as a matrix game, which is the same machinery the audit
uses on every turn of every line.

WHAT IT MEASURES, AND THE TRAP IT AVOIDS. The obvious thing to screen on is the
punish -- what a best-responding opponent gains -- and that is exactly wrong.
Measured, screening three teams on turn-1 exploitability:

    NAIC (real team)   49.5
    Big 6 (real team)  66.6
    junk team           3.5   <- "best"

A team with no threats gives a best-responding opponent nothing to gain, so the
worst team scores first. It is §1 of WORKFLOW.md in miniature: punish alone is a
trap, because a lost position has nothing left to punish.

So the screen is the MAXIMIN VALUE of the opening instead -- the value we can
guarantee against their best reply, which is `equilibrium - exploitability` and
is what `robustness.turn_robustness` calls `worst_case`. It prices threat and
punishability together, and on the same three teams it orders them the way a
player would:

    Big 6              -80.1
    NAIC              -208.3
    junk team         -306.5

WHAT IT CANNOT SAY. Nothing about a team that opens cleanly and falls apart on
turn five -- only the full audit sees that. This is a SCREEN: it rejects the
hopeless so the night is spent on the plausible, and it must never be used to
RANK teams. Both sides choose the way they really would: they pick the lead that
hurts us most, we pick the best answer we have.
"""
import itertools

from combatants import make_team
from battle import Battle
from matchup_search import _attach_movesets
from robustness import turn_robustness
from solver import TOP_K_MOVES, build_moveset, solver_mode
import solver as _solver

# Guaranteed opening value below which a team is not worth auditing. Calibrated,
# loosely, on the three teams above: it sits below both real teams and above the
# junk one. Deliberately generous -- rejecting a team costs its whole audit, so
# this should only catch what is plainly hopeless. Raise it to screen harder,
# and check what it starts throwing away before trusting a tighter setting.
DEFAULT_FLOOR = -250.0


def opening_turn(our4, enemy4, merged, moves_db, natures, typechart,
                 our_sets=None, enemy_sets=None, depth=1, seed=20260808):
    """Solve turn 1 of this position. Returns a robustness.TurnRobustness.

    Piloted with the equilibrium solver, like the audit: screening a team while
    playing it badly measures the pilot. Their action space is the WIDE one
    (`_attach_movesets`), because an opening that only survives the four moves
    we assumed is not safe.
    """
    ours = make_team(list(our4), merged, natures, sets=our_sets)
    theirs = make_team(list(enemy4), merged, natures, sets=enemy_sets)
    movesets = {}
    for c in ours + theirs:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
        movesets[c.name] = build_moveset(merged[c.name], moves_db,
                                         top_k=TOP_K_MOVES,
                                         only_moves=spec.get("moves"))
    battle = Battle(ours, theirs, typechart, moves_db)
    _attach_movesets(battle, movesets, theirs, merged, moves_db, enemy_sets)

    with solver_mode(nash=True, depth=depth):
        _solver.seed_nash_sampling(seed)
        action, _score, _sim = _solver.solve_best_action(battle, "p1", movesets)
    if not action:
        return None
    return turn_robustness(battle, movesets, action, "p1")


def screen_team(team, opponents, merged, moves_db, natures, typechart,
                our_sets=None, enemy_sets=None, leads_per_opponent=2,
                our_brings=2, depth=1):
    """The worst opening any of `opponents` can force on this team.

    Both sides choose, and the asymmetry is the point: THEY pick the lead that
    hurts us most, because at preview they see our six; WE pick the best answer
    we have, because being punishable with one bring is not being punishable if
    another bring is fine. So: max over their leads, min over ours.

    `leads_per_opponent` and `our_brings` bound that search -- the default 2x2
    is a screen, not a proof.

    Returns {"guaranteed": float | None, "worst_vs": (opponent, their lead,
             our bring), "punish": float, "per_opponent": {name: guaranteed}}.
    """
    per_opponent, guaranteed, worst_vs, punish = {}, None, None, 0.0
    our_leads = list(itertools.combinations(team, 2))[:max(1, our_brings)]

    for name, roster in opponents.items():
        their_leads = list(itertools.combinations(roster, 2))[
            :max(1, leads_per_opponent)]
        opp_guaranteed, opp_vs = None, None
        for their_lead in their_leads:
            enemy4 = list(their_lead) + [x for x in roster
                                         if x not in their_lead][:2]
            best_answer, best_bring, best_punish = None, None, 0.0
            for our_lead in our_leads:
                our4 = list(our_lead) + [x for x in team
                                         if x not in our_lead][:2]
                rec = opening_turn(our4, enemy4, merged, moves_db, natures,
                                   typechart, our_sets=our_sets,
                                   enemy_sets=enemy_sets, depth=depth)
                if rec is None:
                    continue
                if best_answer is None or rec.worst_case > best_answer:
                    best_answer = rec.worst_case
                    best_bring, best_punish = our4, rec.exploitability
            if best_answer is None:
                continue
            # Their best lead is the one our best answer handles WORST.
            if opp_guaranteed is None or best_answer < opp_guaranteed:
                opp_guaranteed = best_answer
                opp_vs = (name, list(their_lead), best_bring)
                if best_punish > punish:
                    punish = best_punish
        if opp_guaranteed is None:
            continue
        per_opponent[name] = opp_guaranteed
        if guaranteed is None or opp_guaranteed < guaranteed:
            guaranteed, worst_vs = opp_guaranteed, opp_vs

    return {"guaranteed": guaranteed, "worst_vs": worst_vs,
            "punish": punish, "per_opponent": per_opponent}


def is_hopeless(result, floor=DEFAULT_FLOOR):
    """True when the best opening this team has is already badly losing."""
    return result.get("guaranteed") is not None and result["guaranteed"] < floor
