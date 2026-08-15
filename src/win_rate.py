"""What a win rate has to mean, and how to compute one that means it.

Reported: "these winrates are not sound". They are not, and the pilot was only
the surface of it. Three separate things are wrong with `wins / 90`, and each
has a principle that fixes it.

--------------------------------------------------------------------------
P1. A MATCHUP'S VALUE IS A PROBABILITY, NOT A BOOLEAN.

`wins / 90` plays ONE game per enemy bring, on the AVERAGE damage roll, with
speed ties broken deterministically, and scores it 1 or 0. But damage varies
over a 16-step 0.85-1.00 range, speed ties are coin flips, and secondary
effects fire or do not. A line that wins on the average roll and loses 45% of
real games is recorded as a clean win.

So a cell of the matrix is P(we win), estimated by replaying the matchup over
the game's own randomness, and it carries the interval that estimate deserves.

--------------------------------------------------------------------------
P2. THEIR BRING IS CHOSEN, NOT DRAWN FROM A HAT.

This is the deep one. `wins / 90` averages uniformly over their 90 bring-4s --
which is the number you would want if they picked their four by lottery. They
do not. At team preview both players see the other's six and then choose four,
simultaneously and adversarially.

That is a matrix game, and it already has a right answer: the VALUE of the
game. A team that beats 89 of their 90 brings and loses to the one they will
always pick is not a 98.9% team, it is a 0% team, and the uniform mean cannot
say so. Three aggregates of the same matrix, reported together because the gap
between them is the information:

    uniform     the old number. Assumes they choose at random.
    worst_case  they read us perfectly and pick the counter. Pessimistic:
                it also assumes WE cannot mix.
    game_value  both sides play the equilibrium of the bring-selection game.
                This is the answer.

`game_value` sits between the other two by construction, and the test suite
pins that.

--------------------------------------------------------------------------
P3. A NUMBER WITHOUT ITS UNCERTAINTY IS NOT A MEASUREMENT.

Every estimate here reports the sample size and a Wilson interval. Wilson
rather than the normal approximation because these proportions live near 0 and
1, where the normal interval runs past the ends and understates the width.

--------------------------------------------------------------------------
WHAT THIS COSTS. Honestly: a lot. `wins / 90` is 90 games. The sound version is
`samples x our_brings x their_brings`. That is why `n_samples` and both bring
lists are arguments rather than constants -- the caller decides how much
soundness it can afford, and `estimate.n` records what it bought.
"""
import math

from matchup_search import EQUILIBRIUM_PILOT, play_out_pair
from rolls import DEFAULT_SCENARIO_WEIGHTS, DEFAULT_SCENARIOS

# --------------------------------------------------------------- quadrature
# Monte Carlo is the wrong tool for a distribution this small and this known.
# Damage is 16 discrete equiprobable rolls; `rolls.py` already brackets them
# with three -- low, median, high -- carrying the share of the 16 each stands
# in for. Speed ties are a fair coin. Crossing them gives SIX deterministic
# games that cover both sources of chance exactly, against 24 random draws that
# cover them approximately and never the same way twice.
#
# What this trades: sampling error for BIAS. Six scenarios pin every roll in a
# game to the same index, so "all rolls low" is a correlated extreme rather
# than an independent draw. Whether that bias matters is an empirical question
# and tools/measure_estimator.py answers it -- do not assume either way.
ROLL_SCENARIOS = tuple(zip(DEFAULT_SCENARIOS, DEFAULT_SCENARIO_WEIGHTS))
TIE_SCENARIOS = (("p1", 0.5), ("p2", 0.5))

# A timeout is not a win. It is also not evidence of a loss, so it is counted
# against us (the conservative reading, matching search_robust_composition)
# AND reported separately -- a matchup that times out half the time is telling
# you something the win rate alone cannot.
WIN, LOSS = "p1", "p2"


def wilson(successes, n, z=1.96):
    """95% Wilson score interval for a binomial proportion.

    The normal approximation is wrong exactly where these numbers live: at
    18/20 it produces an upper bound above 1, which is not a probability.
    """
    if n <= 0:
        return (0.0, 1.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


class Estimate:
    """One probability, with the evidence behind it."""

    __slots__ = ("wins", "losses", "timeouts", "n")

    def __init__(self, wins=0, losses=0, timeouts=0):
        self.wins, self.losses, self.timeouts = wins, losses, timeouts
        self.n = wins + losses + timeouts

    @property
    def p(self):
        """P(we win). Timeouts count against us -- see the module note."""
        return self.wins / self.n if self.n else 0.0

    @property
    def interval(self):
        return wilson(self.wins, self.n)

    @property
    def timeout_rate(self):
        return self.timeouts / self.n if self.n else 0.0

    def __repr__(self):
        lo, hi = self.interval
        return (f"<{self.p:.0%} [{lo:.0%}-{hi:.0%}] "
                f"n={self.n}{f' timeouts={self.timeouts}' if self.timeouts else ''}>")


class Quadrature:
    """A probability from weighted deterministic scenarios, not from sampling.

    `interval` is not a confidence interval and does not pretend to be: there
    is no sampling error to quantify. What it reports instead is the SPREAD --
    the win rate if every roll went badly, and if every roll went well. A line
    whose spread is (0, 1) wins or loses purely on the dice, which is the thing
    a single averaged number hides and the thing a player most needs told.
    """

    __slots__ = ("outcomes", "p", "n", "timeouts")

    def __init__(self, outcomes):
        # outcomes: [(won: bool, timed_out: bool, weight)]
        self.outcomes = outcomes
        self.n = len(outcomes)
        total = sum(w for _w, _t, w in outcomes) or 1.0
        self.p = sum(w for won, _t, w in outcomes if won) / total
        self.timeouts = sum(1 for _w, t, _ in outcomes if t)

    @property
    def wins(self):
        """For interoperability with Estimate; rounded to whole games."""
        return round(self.p * self.n)

    @property
    def losses(self):
        return self.n - self.wins - self.timeouts

    @property
    def interval(self):
        """(worst roll, best roll) -- the range the dice can move this to."""
        if not self.outcomes:
            return (0.0, 1.0)
        return (0.0 if not all(w for w, _t, _ in self.outcomes) else 1.0,
                1.0 if any(w for w, _t, _ in self.outcomes) else 0.0)

    @property
    def spread(self):
        """1.0 when the scenarios disagree completely: a coin-flip line."""
        lo, hi = self.interval
        return hi - lo

    @property
    def timeout_rate(self):
        return self.timeouts / self.n if self.n else 0.0

    def __repr__(self):
        return f"<{self.p:.0%} quadrature n={self.n} spread={self.spread:.0%}>"


def matchup_win_prob_quadrature(our4, their4, world, turns=18,
                                pilot=EQUILIBRIUM_PILOT, our_sets=None,
                                enemy_sets=None, roll_scenarios=None,
                                tie_scenarios=None):
    """P1 by quadrature: every roll pinned, every tie forced, weights applied.

    Six games instead of twenty-four, fully deterministic, and it reports the
    spread rather than a confidence interval because the uncertainty here is
    bias rather than noise.
    """
    rolls = ROLL_SCENARIOS if roll_scenarios is None else roll_scenarios
    ties = TIE_SCENARIOS if tie_scenarios is None else tie_scenarios
    outcomes = []
    for roll_index, roll_w in rolls:
        for tie, tie_w in ties:
            winner, _t, _b = play_out_pair(
                list(our4), list(their4), world["merged"], world["moves"],
                world["natures"], world["typechart"], max_turns=turns,
                our_sets=our_sets, enemy_sets=enemy_sets,
                roll_index=roll_index, tie_bias=tie, pilot=pilot)
            outcomes.append((winner == WIN, winner not in (WIN, LOSS),
                             roll_w * tie_w))
    return Quadrature(outcomes)


def matchup_win_prob_adaptive(our4, their4, world, precision=0.35, n_min=4,
                              n_max=24, batch=4, turns=18,
                              pilot=EQUILIBRIUM_PILOT, our_sets=None,
                              enemy_sets=None, seed=0, tie_bias=None):
    """P1, with the samples spent where they are needed.

    THE OBSERVATION. Most cells are not close: a matchup that wins 40 out of 40
    is settled after four games, and spending twenty more on it buys nothing.
    The cells that need evidence are the ones near a coin flip, and there are
    few of them. So sample in batches and stop as soon as the Wilson interval
    is narrower than `precision`.

    This is a strictly better trade than quadrature, which bought its speed by
    pinning every roll in a game to one index -- a correlated extreme that is
    not a game anyone plays, and which measured a 0.78 error on a real cell
    (tools/measure_estimator.py). Adaptive sampling changes only HOW MANY
    honest games are played, never what a game is, so it cannot introduce bias
    -- it can only stop too early, and the interval it stopped at says how
    early.
    """
    tally = Estimate()
    while tally.n < n_max:
        for i in range(batch):
            winner, _t, _b = play_out_pair(
                list(our4), list(their4), world["merged"], world["moves"],
                world["natures"], world["typechart"], max_turns=turns,
                our_sets=our_sets, enemy_sets=enemy_sets,
                rng_seed=seed + tally.n + i, tie_bias=tie_bias, pilot=pilot)
            if winner == WIN:
                tally.wins += 1
            elif winner == LOSS:
                tally.losses += 1
            else:
                tally.timeouts += 1
        tally.n = tally.wins + tally.losses + tally.timeouts
        if tally.n >= n_min:
            lo, hi = tally.interval
            if hi - lo <= precision:
                break
    return tally


def matchup_win_prob(our4, their4, world, n_samples=24, turns=18,
                     pilot=EQUILIBRIUM_PILOT, our_sets=None, enemy_sets=None,
                     seed=0, tie_bias=None):
    """P1: replay one matchup over the game's randomness and count.

    `seed` makes the estimate REPRODUCIBLE: the same call gives the same
    number, so two teams compared today compare the same way tomorrow. Each
    sample gets seed + i, so the samples are still independent draws.

    `tie_bias=None` leaves speed ties to the coin they are. Forcing them to
    "p2" gives the pessimistic reading instead; it is not the default here
    because P1 asks for the probability, not for the worst case -- the worst
    case is P2's job and it belongs at the aggregate level, not smuggled into
    every cell.
    """
    tally = Estimate()
    for i in range(n_samples):
        winner, _t, _b = play_out_pair(
            list(our4), list(their4), world["merged"], world["moves"],
            world["natures"], world["typechart"], max_turns=turns,
            our_sets=our_sets, enemy_sets=enemy_sets,
            rng_seed=seed + i, tie_bias=tie_bias, pilot=pilot)
        if winner == WIN:
            tally.wins += 1
        elif winner == LOSS:
            tally.losses += 1
        else:
            tally.timeouts += 1
    tally.n = tally.wins + tally.losses + tally.timeouts
    return tally


def cell_estimator(method):
    """The per-cell estimator, by name. "quadrature" (6 deterministic games) or
    "sample" (n random ones)."""
    if method == "quadrature":
        return matchup_win_prob_quadrature
    if method == "sample":
        return matchup_win_prob
    if method == "adaptive":
        return matchup_win_prob_adaptive
    raise ValueError(f"unknown method {method!r}")


def bring_matrix(our_brings, their_brings, world, on_cell=None,
                 method="quadrature", **kw):
    """The payoff matrix of the bring-selection game.

    `W[i][j]` = P(we win | we bring `our_brings[i]`, they bring
    `their_brings[j]`). Rows are ours because we maximise.
    """
    estimate = cell_estimator(method)
    rows = []
    for i, ours in enumerate(our_brings):
        row = []
        for j, theirs in enumerate(their_brings):
            est = estimate(ours, theirs, world, **kw)
            if on_cell:
                on_cell(i, j, est)
            row.append(est)
        rows.append(row)
    return rows


def evaluate_lazily(our_brings, their_brings, world, on_cell=None,
                    method="quadrature", iters=3000, **kw):
    """The same game value, without simulating every cell.

    THE COST INSIGHT. A full matrix is `our_brings x their_brings` cells, and
    almost all of them are irrelevant: the equilibrium is supported on a handful
    of brings each. `matrix_game.double_oracle` materialises only the cells it
    needs to prove the value, calling `payoff(i, j)` lazily.

    Returns the same shape as `evaluate`, plus `n_cells_evaluated` -- read it
    against `n_cells` to see what the laziness bought. `matrix` is sparse here:
    a dict keyed (i, j), because most cells were never played.
    """
    from matrix_game import double_oracle

    estimate = cell_estimator(method)
    seen = {}

    def payoff(i, j):
        if (i, j) not in seen:
            est = estimate(our_brings[i], their_brings[j], world, **kw)
            seen[(i, j)] = est
            if on_cell:
                on_cell(i, j, est)
        return seen[(i, j)].p

    res = double_oracle(len(our_brings), len(their_brings), payoff,
                        inner_iters=iters)
    played = list(seen.values())
    total_games = sum(c.n for c in played)
    # `uniform` and `worst_case` are over the cells ACTUALLY PLAYED, and say so:
    # computing them honestly would need the whole matrix, which is the cost
    # this function exists to avoid.
    ps = [c.p for c in played]
    return {
        "game_value": res.value,
        "our_mix": list(res.p),
        "their_mix": list(res.q),
        "converged": res.converged,
        "uniform_over_played": (sum(ps) / len(ps)) if ps else 0.0,
        "matrix": seen,
        "n_cells": len(our_brings) * len(their_brings),
        "n_cells_evaluated": len(seen),
        "n_games": total_games,
        "timeout_rate": (sum(c.timeouts for c in played) / total_games
                         if total_games else 0.0),
    }


def aggregate(matrix, iters=3000):
    """P2: three readings of the same matrix, and the strategies behind them.

    `matrix` is Estimates (or floats). Returns a dict:

        uniform      mean over every cell -- they choose at random
        worst_case   max over our rows of the min over their columns -- they
                     counter perfectly and we may not mix
        game_value   the equilibrium value. THE answer, and always between the
                     other two
        our_mix      how often to bring each of ours, at equilibrium
        their_mix    what they should be bringing, which is the actionable half
        best_pure    our single best bring if you must commit to one
    """
    from matrix_game import solve_matrix

    # Duck-typed on `.p`, not isinstance: Estimate and Quadrature are two
    # different estimators of the same quantity and aggregate() must not care
    # which produced a cell. An isinstance check here silently excluded
    # Quadrature and fell through to float(), which raises.
    A = [[c.p if hasattr(c, "p") else float(c) for c in row] for row in matrix]
    if not A or not A[0]:
        return {"uniform": 0.0, "worst_case": 0.0, "game_value": 0.0,
                "our_mix": [], "their_mix": [], "best_pure": None}

    cells = [v for row in A for v in row]
    uniform = sum(cells) / len(cells)
    row_worst = [min(row) for row in A]
    worst_case = max(row_worst)
    value, p, q = solve_matrix(A, iters=iters)
    return {
        "uniform": uniform,
        "worst_case": worst_case,
        "game_value": value,
        "our_mix": list(p),
        "their_mix": list(q),
        "best_pure": max(range(len(A)), key=lambda i: row_worst[i]),
    }


def evaluate(our_brings, their_brings, world, on_cell=None, iters=3000,
             method="quadrature", **kw):
    """The whole thing: matrix, aggregates, and the evidence.

    This is what a caller should use. `n_games` is the total simulated, so the
    cost of the number is reported alongside it rather than hidden.
    """
    matrix = bring_matrix(our_brings, their_brings, world, on_cell=on_cell,
                          method=method, **kw)
    out = aggregate(matrix, iters=iters)
    flat = [c for row in matrix for c in row]
    total_wins = sum(c.wins for c in flat)
    total_n = sum(c.n for c in flat)
    out["matrix"] = matrix
    out["n_games"] = total_n
    out["n_cells"] = len(flat)
    # The interval on the UNIFORM figure only. There is no closed form for the
    # interval on a game value -- it is a nonlinear function of every cell --
    # so quoting one would be inventing precision. Cell intervals are on the
    # cells; read those.
    out["uniform_interval"] = wilson(total_wins, total_n)
    out["timeout_rate"] = (sum(c.timeouts for c in flat) / total_n
                           if total_n else 0.0)
    return out


def describe(result, our_labels=None, their_labels=None, top=3):
    """The result as text, leading with the number that is actually the answer."""
    lines = [
        f"game value   {result['game_value']:6.1%}   <- both sides choose well",
        f"worst case   {result['worst_case']:6.1%}   they counter, we cannot mix",
        f"uniform      {result['uniform']:6.1%}   they choose at random "
        f"(the old 'X / 90')",
        f"             from {result['n_games']} games over "
        f"{result['n_cells']} bring pairings",
    ]
    if result.get("timeout_rate"):
        lines.append(f"             {result['timeout_rate']:.0%} of games hit "
                     f"the turn cap and are counted as losses")
    mix = result.get("their_mix") or []
    if mix and their_labels:
        ranked = sorted(range(len(mix)), key=lambda j: -mix[j])[:top]
        lines.append("what they should bring:")
        for j in ranked:
            if mix[j] > 0.01:
                lines.append(f"    {mix[j]:5.0%}  {their_labels[j]}")
    ours = result.get("our_mix") or []
    if ours and our_labels:
        ranked = sorted(range(len(ours)), key=lambda i: -ours[i])[:top]
        lines.append("what we should bring:")
        for i in ranked:
            if ours[i] > 0.01:
                lines.append(f"    {ours[i]:5.0%}  {our_labels[i]}")
    return "\n".join(lines)
