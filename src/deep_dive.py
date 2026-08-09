"""Audit ONE position at full strength: our bring, their lead, turn by turn.

The exhaustive tier is expensive because it audits 8 brings against all 90 of
their bring-4s. Almost all of that cost is breadth, not depth -- and once you
have actually led, the breadth is gone: you know what you brought, you know
what they led, and there is exactly one position left to analyse.

So this runs the SAME analysis the exhaustive tier runs, on one position:

  * the equilibrium solver pilots our side, at whatever depth is asked for;
  * their move space is the wide one (six most-used, not the four we planned
    for), because a line that only survives the moves we assumed is not safe;
  * every turn gets a full payoff matrix, so exploitability, the punish and
    the tie count are real rather than sampled;
  * the line advances against their equilibrium reply, not against a best
    response to the move we just made -- see robustness.line_report.

One line at depth 1 is seconds. That is what makes "having already led X"
worth asking as a separate question rather than a slice of the batch run.
"""
from combatants import make_team
from battle import Battle
from matchup_search import _attach_movesets
from robustness import line_report
from solver import TOP_K_MOVES, build_moveset, seed_nash_sampling, solver_mode
import solver as _solver

DEFAULT_SEED = 20260808


def build_position(our4, enemy4, merged, moves_db, natures, typechart,
                   our_sets=None, enemy_sets=None):
    """A Battle at turn 0 with `our4[:2]` and `enemy4[:2]` already leading.

    Lead ORDER is meaningful: make_team treats the first two as the openers,
    which is exactly the "having already led X" the caller is asking about.
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
    return battle, movesets


def audit_position(our4, enemy4, merged, moves_db, natures, typechart,
                   our_sets=None, enemy_sets=None, max_turns=16, depth=1,
                   seed=DEFAULT_SEED, side_name="p1"):
    """Play and audit this one position. Returns a robustness.LineReport.

    `depth` is the equilibrium solver's lookahead. Depth 2 sees a turn further
    -- the setup or the switch-in that depth 1 prices as free -- and measured
    roughly 48x the cost, which is affordable for a single line and is not
    affordable for a batch.
    """
    battle, movesets = build_position(our4, enemy4, merged, moves_db, natures,
                                      typechart, our_sets, enemy_sets)

    def choose(state):
        with solver_mode(nash=True, depth=depth):
            seed_nash_sampling(seed)
            action, _score, _sim = _solver.solve_best_action(
                state, side_name, state.movesets)
        return action

    return line_report(battle, movesets, choose, max_turns=max_turns,
                       side_name=side_name)


def summarise(report):
    """The headline numbers for one line, as plain types for a UI."""
    worst = report.worst_turn
    return {
        "outcome": report.outcome,
        "turns": len(report.turns),
        "mean_exploitability": report.mean_exploitability,
        "mean_expected_loss": report.mean_expected_loss,
        "worst_exploitability": worst.exploitability if worst else 0.0,
        "worst_turn": worst.turn if worst else None,
        "severe_turns": report.severe_count,
        "final_margin": report.final_margin,
    }
