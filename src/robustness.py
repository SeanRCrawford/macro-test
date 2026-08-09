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

# Below this, a "best response" is not a punish at all -- it is the arbitrary
# winner of a tie. Measured on a real line: on one turn 46 of their 62 actions
# scored IDENTICALLY, and the reported punisher was simply the first of them,
# which is why the workbook showed opponents "punishing" by switching into
# positions that gained them nothing. A gap this small is noise from regret
# matching, not a play anyone would make.
NO_PUNISH = 2.0


@dataclass
class TurnRobustness:
    turn: int
    exploitability: float      # what a PERFECTLY READING opponent gains
    regret: float              # how far short of the safest available play
    equilibrium: float         # value of the turn played out properly
    worst_case: float          # value of our play against their best reply
    # What a strong opponent actually takes when it CANNOT see our move: the
    # equilibrium value minus our play's value against their equilibrium
    # mixture. Exploitability is the worst case and assumes they guess right
    # every time; this is the realistic one, because their punish has to be a
    # sound play for them too, chosen without knowing ours. Near zero means the
    # play only loses ground to a read they cannot reliably make.
    expected_loss: float = 0.0
    punisher: object = None     # their best reply, or None when there is none
    # How many of their actions tied at that same worst value. A large number
    # means the position is insensitive to what they do -- typically because we
    # protected -- and the named punisher is a tie-break, not a read.
    tied_replies: int = 1
    our_action: object = None
    # Their EQUILIBRIUM reply -- the action a strong player picks without
    # seeing ours. Distinct from `punisher`, which is the best response to the
    # move we actually made and therefore assumes clairvoyance. Use `punisher`
    # to score a single turn (that is what exploitability means); use this to
    # advance a line, or the opponent reads our mind on every turn of the game.
    equilibrium_reply: object = None

    @property
    def severe(self) -> bool:
        return self.exploitability > SEVERE

    @property
    def has_punish(self) -> bool:
        """Is there actually a play that hurts us here?

        False means every reply is worth the same and the `punisher` field is a
        tie-break. Reporting one anyway invents a read the opponent never had.
        """
        return self.exploitability > NO_PUNISH


@dataclass
class LineReport:
    turns: list = field(default_factory=list)
    outcome: str = "unresolved"   # "win" | "loss" | "draw" | "unresolved"
    final_margin: float = 0.0     # our leaf_value at the end of the line
    length: int = 0               # turns actually played

    @property
    def won(self):
        """Did this line WIN against an opponent that punished every turn?

        `line_report` advances the game against their EQUILIBRIUM reply each
        turn, so this is not "did we beat our own bot" -- it is "did we beat a
        player choosing optimally without seeing our move". Measured, not
        inferred from a points total.

        Deliberately NOT the best response to our revealed move: that opponent
        is clairvoyant, and iterating it for ten turns beat every team tested
        (0 wins in 24 lines the same teams won 180/180 against the standard
        model). Exploitability still scores each TURN against the best
        response, which is what the definition requires.

        "unresolved" (the turn cap hit with both sides alive) is NOT a win.
        """
        return self.outcome == "win"

    @property
    def mean_exploitability(self):
        return (sum(t.exploitability for t in self.turns) / len(self.turns)
                if self.turns else 0.0)

    @property
    def worst_turn(self):
        """The turn a good opponent would punish hardest -- where to look first."""
        return max(self.turns, key=lambda t: t.exploitability) if self.turns else None

    @property
    def mean_expected_loss(self):
        """Ground conceded per turn to an opponent who cannot read us.

        The honest cost of a line. mean_exploitability is its worst case.
        """
        return (sum(t.expected_loss for t in self.turns) / len(self.turns)
                if self.turns else 0.0)

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

    equilibrium, _p, q = solve_matrix(A)
    worst_per_row = [min(r) for r in A]
    maximin = max(worst_per_row)
    row_min = min(A[idx])
    tied = [j for j in range(len(theirs)) if A[idx][j] <= row_min + 1e-9]
    # Among equal-valued replies prefer one that actually commits to something:
    # an attack reads as a punish, a switch or a Protect that scores the same is
    # the tie-break talking. Without this the first-indexed action wins and the
    # report claims the opponent "punished" by repositioning for no gain.
    def _passivity(j):
        acts = theirs[j]
        return (any(a.kind == "switch" for a in acts),
                any(a.kind == "protect" for a in acts), j)

    worst_j = min(tied, key=_passivity)
    # Their equilibrium play, taken as the highest-probability action in the
    # equilibrium mixture. argmax rather than a sample so a line is
    # reproducible -- the golden baseline and the parallel path both depend on
    # the same inputs giving the same walk.
    eq_j = max(range(len(theirs)), key=lambda j: q[j]) if q else worst_j
    # Our play's value against their whole equilibrium mixture, not against
    # the single reply that hurts most.
    if q and len(q) == len(theirs):
        against_mixture = sum(q[j] * A[idx][j] for j in range(len(theirs)))
    else:
        against_mixture = A[idx][eq_j]

    return TurnRobustness(
        turn=battle.turn_num + 1,
        exploitability=equilibrium - worst_per_row[idx],
        expected_loss=equilibrium - against_mixture,
        regret=maximin - worst_per_row[idx],
        equilibrium=equilibrium,
        worst_case=worst_per_row[idx],
        punisher=theirs[worst_j],
        tied_replies=len(tied),
        our_action=our_action,
        equilibrium_reply=theirs[eq_j],
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
        # Advance against their EQUILIBRIUM reply, not against the best
        # response to the move we just made. Those are different opponents:
        # the best response knows our action before choosing, and iterating a
        # clairvoyant opponent for ten turns beats every team ever built --
        # measured, 0 wins in 24 audited lines that the same teams won 180/180
        # against the standard model. Exploitability still scores the turn
        # against the best response, which is what the definition requires;
        # only the trajectory changes.
        nxt = step(battle, our_action, rec.equilibrium_reply) if rec else None
        if nxt is None:
            break
        battle = nxt
        report.length += 1

    # Who actually won the punished line. Recorded here rather than inferred
    # from the points total later: a caller cannot reconstruct it, and the
    # walk above is the only place that knows.
    winner = battle.winner() if battle.is_over() else None
    if winner == "draw":
        report.outcome = "draw"
    elif winner is not None:
        report.outcome = "win" if winner == side_name else "loss"
    else:
        report.outcome = "unresolved"
    # Only meaningful once a turn has actually been played. A line that ended
    # before its first step has no final position to score, and evaluating one
    # anyway is how a caller that legitimately passes an unplayable position
    # gets an exception instead of an empty report.
    if report.length:
        report.final_margin = leaf_value(battle, side_name)
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
