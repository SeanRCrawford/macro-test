"""How punishable is a line? (design doc sections 3c and 2n)

The project's aim is realistic, high-level play -- lines that hold up against a
competent opponent -- and win rate against a fixed opponent is a biased proxy
for that. This module makes the unbiased quantity reusable rather than leaving
it in a measurement script:

    exploitability(play) = equilibrium value of the turn
                         - worst case of the play actually chosen

Zero means unpunishable. Large means the play only looked good because the
opponent we imagined was not trying.

Two things make this an honest measure rather than a restatement of the
solver's own opinion:

  1. The opponent's action space comes from `battle.wide_movesets` when the
     caller has attached it -- their SIX most-used moves, not the four we plan
     with. A play that only survives the moves we assumed is not safe, and
     pretending otherwise is the self-fulfilling assumption that measured at 10
     points of win rate (solver.build_wide_moveset).
  2. The value compared against is the equilibrium of the turn, not the
     solver's own estimate of its play. A solver cannot mark its own homework.

`line_report` walks a whole game and returns per-turn exploitability, so the
question a player actually asks -- "which turn of this plan is the one that
gets punished?" -- has an answer.
"""
from dataclasses import dataclass, field

from matrix_game import solve_matrix
from solver import leaf_value, our_candidate_joint_actions
from turn_step import step

LOSS = -1e4

# A best response gaining more than this is a play worth reconsidering: about a
# third of a Pokemon at KO_WEIGHT = 180.
SEVERE = 60.0


@dataclass
class TurnRobustness:
    turn: int
    exploitability: float      # what a best-responding opponent gains
    regret: float              # how far short of the safest available play
    equilibrium: float         # value of the turn played out properly
    worst_case: float          # value of our play against their best reply
    punisher: object = None    # their joint action that does it
    our_action: object = None

    @property
    def severe(self) -> bool:
        return self.exploitability > SEVERE


@dataclass
class LineReport:
    turns: list = field(default_factory=list)

    @property
    def mean_exploitability(self):
        return (sum(t.exploitability for t in self.turns) / len(self.turns)
                if self.turns else 0.0)

    @property
    def worst_turn(self):
        """The turn a good opponent would punish hardest -- where to look first."""
        return max(self.turns, key=lambda t: t.exploitability) if self.turns else None

    @property
    def severe_count(self):
        return sum(1 for t in self.turns if t.severe)


def _signature(joint):
    return frozenset((a.combatant.name, a.kind, a.move.name if a.move else None)
                     for a in joint)


def turn_robustness(battle, movesets, our_action, side_name="p1"):
    """Exploitability of `our_action` at this position, or None if unmeasurable.

    Builds the real payoff matrix rather than trusting the solver's own score.
    """
    us = battle.p1 if side_name == "p1" else battle.p2
    them = battle.p2 if side_name == "p1" else battle.p1
    ours = our_candidate_joint_actions(battle, us, them, movesets,
                                       battle.turn_num + 1)
    # Their SIX most-used moves where the caller supplied them: a play that is
    # only safe against the four we assumed is not safe.
    their_movesets = getattr(battle, "wide_movesets", None) or movesets
    theirs = our_candidate_joint_actions(battle, them, us, their_movesets,
                                         battle.turn_num + 1)
    if not ours or not theirs:
        return None

    want = _signature(our_action)
    idx = next((i for i, oa in enumerate(ours) if _signature(oa) == want), None)
    if idx is None:
        return None

    A = []
    for oa in ours:
        row = []
        for ta in theirs:
            nxt = step(battle, oa, ta)
            row.append(LOSS if nxt is None else leaf_value(nxt, side_name))
        A.append(row)

    equilibrium, _p, _q = solve_matrix(A)
    worst_per_row = [min(r) for r in A]
    maximin = max(worst_per_row)
    worst_j = min(range(len(theirs)), key=lambda j: A[idx][j])

    return TurnRobustness(
        turn=battle.turn_num + 1,
        exploitability=equilibrium - worst_per_row[idx],
        regret=maximin - worst_per_row[idx],
        equilibrium=equilibrium,
        worst_case=worst_per_row[idx],
        punisher=theirs[worst_j],
        our_action=our_action,
    )


def line_report(battle, movesets, choose, max_turns=8, side_name="p1"):
    """Walk a game, recording how punishable each chosen play was.

    `choose(battle) -> joint action` is the policy being audited, so the same
    function serves the greedy solver, the equilibrium solver, or a hand-written
    line a player wants checked.
    """
    report = LineReport()
    for _ in range(max_turns):
        if battle.is_over():
            break
        our_action = choose(battle)
        if not our_action:
            break
        rec = turn_robustness(battle, movesets, our_action, side_name)
        if rec is not None:
            report.turns.append(rec)
        # Advance against their best reply -- auditing a line means asking how it
        # holds up when the opponent is actually trying, not against a script.
        nxt = step(battle, our_action, rec.punisher) if rec else None
        if nxt is None:
            break
        battle = nxt
    return report


def describe_action(joint):
    """Readable one-liner for display."""
    parts = []
    for a in joint:
        move = a.move.name if a.move else a.kind
        target = (f" -> {a.targets[0].name}"
                  if a.targets and a.targets[0] is not a.combatant else "")
        parts.append(f"{a.combatant.name} {move}{target}")
    return " + ".join(parts)
