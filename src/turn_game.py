"""Solve one turn as a two-player zero-sum matrix game (design doc section 3).

The current solver computes a best response to `greedy_opponent_joint_action`,
a policy we wrote ourselves. Maximising against one fixed pure strategy is the
definition of an exploitable strategy, and it is measurable: section 2b puts the
overstatement at ~174 points per turn, and finds that on **83% of turns the
equilibrium is MIXED**, so no pure strategy is optimal and the current
architecture cannot represent the right answer at all.

This module builds the real payoff matrix

    A[i][j] = value(our joint action i, their joint action j)

and solves it. What that buys, without extra machinery: the maximin row is the
"safe play that cannot be punished" promoted from post-hoc filter to objective;
mixed strategies become representable; and exploitability becomes a number.

Cost is not free and section 2c measured it rather than guessing:

    depth 1   118 simulated turns, 0.08 s per decision   (24% of the full matrix)
    depth 2   6778 simulated turns, 3.88 s per decision   (47.9x)

so depth 2 belongs in interactive use and never inside a sweep. Two findings
from that measurement are baked in below: the inner game is solved by BRUTE
FORCE, because at 8x8 double oracle evaluates ~57 of 64 cells and buys nothing;
and the recursion is EQUILIBRIUM-VALUED at every level, because scoring the
next turn with a greedy playout instead costs 175.8 points -- about one Pokemon,
the very bias this module exists to remove.
"""
from dataclasses import dataclass, field

from matrix_game import double_oracle, solve_matrix
from solver import (leaf_value, our_candidate_joint_actions,
                    greedy_opponent_joint_action)

# Below this many cells, solve the whole matrix instead of running double
# oracle. DO's best-response step scans a full action set against the other
# side's support, so on small matrices it costs about as much as brute force
# while adding iteration overhead (measured: ~57 of 64 cells at 8x8).
BRUTE_FORCE_CELLS = 80

# Cap on actions per side in a nested (depth > 1) subgame. Depth 2 is already
# ~48x depth 1; without this it is far worse.
INNER_ACTION_CAP = 8

# Regret-matching iterations for a nested subgame. See the note in solve_turn:
# at depth 2 this count, not the simulation count, is the bottleneck.
INNER_SOLVE_ITERS = 400

LOSS = -1e4  # a joint action that fails to simulate


@dataclass
class TurnSolution:
    """The equilibrium of one turn, plus what it says about the position."""
    our_actions: list
    their_actions: list
    p: list                     # our mixed strategy over our_actions
    q: list                     # theirs over their_actions
    value: float                # equilibrium value to us
    maximin_value: float        # best guaranteed value from a single PURE action
    maximin_index: int
    n_sims: int = 0
    converged: bool = True
    _matrix: dict = field(default_factory=dict, repr=False)

    @property
    def best_action(self):
        """Highest-probability action, for callers that need a single choice."""
        if not self.our_actions:
            return None
        return self.our_actions[max(range(len(self.p)), key=lambda i: self.p[i])]

    @property
    def maximin_action(self):
        if not self.our_actions:
            return None
        return self.our_actions[self.maximin_index]

    def support(self, tol=1e-3):
        """The actions actually played, with probabilities -- the mixed advice."""
        out = [(self.our_actions[i], x) for i, x in enumerate(self.p) if x > tol]
        out.sort(key=lambda t: -t[1])
        return out

    @property
    def is_mixed(self):
        return len(self.support()) > 1

    @property
    def mixing_gain(self):
        """What mixing is worth over the best pure play.

        Section 2b found this is 50-86 points on the turns where it matters, and
        it is the argument for solving for the equilibrium rather than simply
        taking the safest row.
        """
        return self.value - self.maximin_value

    @property
    def their_decision_difficulty(self):
        """How flat the opponent's options are -- "making your opponent make
        difficult decisions" (section 8c.4), computed from the matrix we already
        built. Small means their best and second-best replies are close, so they
        are guessing; large means they have an obvious out.
        """
        if not self.their_actions or not self._matrix:
            return 0.0
        best = []
        for j in range(len(self.their_actions)):
            column = [self._matrix.get((i, j)) for i in range(len(self.our_actions))]
            column = [c for c in column if c is not None]
            if column:
                best.append(sum(column) / len(column))
        if len(best) < 2:
            return 0.0
        best.sort()
        return best[1] - best[0]

    def exploitability_of(self, row_index):
        """How much a best-responding opponent gains if we commit to one action.

        exploitability(pi) = value(best response to pi) - value(pi), the doc's
        section 3c metric and the principled replacement for "beats N/90 brings".
        Zero for the equilibrium mix; positive for any exploitable pure choice.
        """
        worst = min((v for (i, j), v in self._matrix.items() if i == row_index),
                    default=None)
        return None if worst is None else self.value - worst


def _mirror(battle, side_name):
    """(our side, their side) for whichever player we are solving for."""
    return ((battle.p1, battle.p2) if side_name == "p1"
            else (battle.p2, battle.p1))


def _enumerate(battle, side_name, movesets, cap=None):
    us, them = _mirror(battle, side_name)
    ours = our_candidate_joint_actions(battle, us, them, movesets,
                                       battle.turn_num + 1)
    theirs = our_candidate_joint_actions(battle, them, us, movesets,
                                         battle.turn_num + 1)
    if cap:
        ours, theirs = ours[:cap], theirs[:cap]
    return ours, theirs


def _seed_indices(battle, side_name, movesets, ours, theirs):
    """Restricted action sets to start double oracle from.

    Seeding matters -- a good seed converges in two iterations rather than five.
    Pressure is the principled source (section 8b.2: "pressure is your guide to
    your opponent's thought process ... which lets you figure out which moves
    they are most likely to make"), and the cheapest usable proxy for it is the
    greedy pick, which is exactly the "what threatens what" question. Protect is
    added because it is the archetypal reactive action and is never greedy-
    optimal, so it would otherwise only enter after an extra iteration.
    """
    us, them = _mirror(battle, side_name)

    def protect_row(actions):
        for idx, joint in enumerate(actions):
            if any(a.kind == "protect" for a in joint):
                return idx
        return None

    seed_rows, seed_cols = [0], [0]
    try:
        greedy_theirs = greedy_opponent_joint_action(battle, them, us, movesets,
                                                     battle.turn_num + 1)
        if greedy_theirs:
            names = {(a.combatant.name, a.kind,
                      a.move.name if a.move else None) for a in greedy_theirs}
            for idx, joint in enumerate(theirs):
                if {(a.combatant.name, a.kind,
                     a.move.name if a.move else None) for a in joint} == names:
                    seed_cols = [idx]
                    break
    except Exception:
        pass

    for pool, seeds in ((ours, seed_rows), (theirs, seed_cols)):
        idx = protect_row(pool)
        if idx is not None and idx not in seeds:
            seeds.append(idx)
    return seed_rows, seed_cols


def _script_column(battle, side_name, movesets, theirs, enemy_script):
    """Index of the scripted joint action among `theirs`, if it is in there."""
    if not enemy_script:
        return None
    us, them = _mirror(battle, side_name)
    try:
        # Same calling convention as solver._script_or_greedy:
        # (battle, scripted side, other side, turn number).
        scripted = enemy_script(battle, them, us, battle.turn_num + 1)
    except Exception:
        return None
    if not scripted:
        return None
    want = {(a.combatant.name, a.kind, a.move.name if a.move else None)
            for a in scripted}
    for idx, joint in enumerate(theirs):
        got = {(a.combatant.name, a.kind, a.move.name if a.move else None)
               for a in joint}
        if got == want:
            return idx
    return None


def solve_turn(battle, side_name, movesets, depth=1, step=None, inner=False,
               enemy_script=None, script_confidence=0.7):
    """Equilibrium of the current turn for `side_name`.

    `step(battle, our_joint, their_joint) -> battle | None` advances a COPY of
    the battle; it is injected so callers can count simulations and so this
    module does not need its own copy semantics.

    `inner` marks a nested (recursive) subgame, which is capped to
    INNER_ACTION_CAP actions per side. That cap is what makes depth 2 tractable
    at all: uncapped, a nested solve runs a full ~23x28 game inside every cell
    of another full game, which measured at 31.6 s per decision (~885 s per
    game) against 1.6 s capped.
    """
    if step is None:
        from turn_step import step as default_step
        step = default_step

    ours, theirs = _enumerate(battle, side_name, movesets,
                              cap=INNER_ACTION_CAP if inner else None)
    if not ours or not theirs:
        return TurnSolution([], [], [], [], leaf_value(battle, side_name),
                            leaf_value(battle, side_name), 0)

    counter = {"n": 0}
    matrix = {}

    def payoff(i, j):
        counter["n"] += 1
        nxt = step(battle, ours[i], theirs[j])
        if nxt is None:
            value = LOSS
        elif depth > 1 and not nxt.is_over():
            # EQUILIBRIUM-valued, not a greedy playout: see the module docstring.
            sub = solve_turn(nxt, side_name, movesets, depth - 1, step=step,
                             inner=True)
            value = sub.value
            counter["n"] += sub.n_sims
        else:
            value = leaf_value(nxt, side_name)
        matrix[(i, j)] = value
        return value

    # Regret-matching iterations. A nested subgame is small (capped at
    # INNER_ACTION_CAP per side) and is solved once per cell of the outer game,
    # so the iteration count -- not the simulation count -- becomes the
    # bottleneck at depth 2: 3000 iterations on an 8x8, run for every outer
    # cell, measured 17.6 s per decision against 1.6 s at 400. Small games
    # converge long before 3000 anyway.
    iters = INNER_SOLVE_ITERS if inner else 3000

    n, m = len(ours), len(theirs)
    if n * m <= BRUTE_FORCE_CELLS:
        A = [[payoff(i, j) for j in range(m)] for i in range(n)]
        value, p, q = solve_matrix(A, iters=iters)
        converged = True
    else:
        seed_rows, seed_cols = _seed_indices(battle, side_name, movesets,
                                             ours, theirs)
        res = double_oracle(n, m, payoff, seed_rows=seed_rows, seed_cols=seed_cols,
                            inner_iters=iters)
        value, p, q, converged = res.value, res.p, res.q, res.converged

    # Maximin over the rows we actually materialised. Rows double oracle never
    # touched are not equilibrium candidates, so this is the meaningful set.
    worst = {}
    for (i, j), v in matrix.items():
        worst[i] = min(worst.get(i, float("inf")), v)
    maximin_index = max(worst, key=lambda i: worst[i]) if worst else 0
    maximin_value = worst.get(maximin_index, value)

    # A scripted opponent is not a different algorithm, it is a PRIOR on their
    # columns (section 3d). Rather than assuming they follow the script -- which
    # is what the old top-K deviation check had to defend against with an
    # arbitrary cap -- shift their equilibrium strategy toward the scripted
    # action and best-respond to the mixture. Deviation safety then falls out:
    # the off-script columns keep their equilibrium weight, so a play that loses
    # badly to a deviation is still priced for it.
    script_index = _script_column(battle, side_name, movesets, theirs, enemy_script)
    if script_index is not None and 0.0 < script_confidence <= 1.0:
        lam = script_confidence
        mixed_q = [(1.0 - lam) * qj for qj in q]
        mixed_q[script_index] += lam
        rows = sorted({i for (i, _j) in matrix})
        best_i, best_v = None, float("-inf")
        for i in rows:
            expected, weight = 0.0, 0.0
            for j, qj in enumerate(mixed_q):
                if qj <= 0:
                    continue
                cell = matrix.get((i, j))
                if cell is None:
                    continue
                expected += qj * cell
                weight += qj
            if weight > 0 and expected / weight > best_v:
                best_i, best_v = i, expected / weight
        if best_i is not None:
            # Put the script-aware response at the front of the mix so
            # best_action returns it, while leaving the equilibrium value and
            # the rest of the distribution intact for reporting.
            p = [0.0] * len(ours)
            p[best_i] = 1.0
            q = mixed_q

    return TurnSolution(our_actions=ours, their_actions=theirs, p=p, q=q,
                        value=value, maximin_value=maximin_value,
                        maximin_index=maximin_index, n_sims=counter["n"],
                        converged=converged, _matrix=matrix)
