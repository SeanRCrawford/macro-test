"""Damage rolls: outcome probabilities, and roll-robust evaluation.

Design doc section 4c, reframed. The original framing was "make the leaf value
an EXPECTATION over roll buckets". That is the wrong summary, because an
expectation averages away precisely the thing that decides whether a play is
sound:

    A safe play does not rely on rolls. If a low roll leaves you badly
    punished -- their survivor now threatens a Sucker Punch, or outspeeds and
    KOs in the endgame -- the play was not safe, however good its average
    looked. A low roll whose consequences you have covered is fine.

So rolls belong in the same place as the opponent's choice: inside the SAFETY
analysis, not inside an average. Section 3 already makes "succeeds regardless
of what your opponent goes for" the objective via maximin; this extends it to
"regardless of how the rolls fall".

The two are not the same kind of uncertainty and are not aggregated the same
way. The opponent is ADVERSARIAL -- take the min over their columns. Rolls are
STOCHASTIC -- take a risk-adjusted aggregate over their distribution, tunable
between the mean (risk-neutral) and the worst case (fully roll-averse). A pure
worst case would be too pessimistic: every attack would be assumed to low-roll,
and nothing would ever look like a KO.

The corpus supports this reading directly (section 8e). Aaron Zheng costs a
real play as `50% speed tie x 90% accuracy x 62.5% OHKO`, where 62.5% = 10/16
is a damage roll fraction; and how-to-analyze asks what a turn "would have
played out [like] if you remove that luck/RNG", which is a request for exactly
the sensitivity report below.
"""

# The engine's 16 discrete rolls are base * modifier * (0.85 + 0.01 * i), so
# they are linear in the index and fully reconstructible from the extremes.
N_ROLLS = 16

# Which roll indices to actually simulate. Simulating all 16 is the
# combinatorial explosion this module exists to avoid; these three bracket the
# distribution -- low, median, high -- at 3x cost rather than 16x.
DEFAULT_SCENARIOS = (0, 8, 15)

# Probability weight of each scenario, i.e. the share of the 16 rolls it stands
# in for. Indices 0-4 are "low" (5/16), 5-11 "mid" (7/16), 12-15 "high" (4/16).
DEFAULT_SCENARIO_WEIGHTS = (5 / 16, 7 / 16, 4 / 16)


def roll_values(lo, hi):
    """All 16 discrete damage values, reconstructed from the extremes.

    Exact rather than approximate: the engine's rolls are linear in the index,
    so lo and hi determine the rest. (Damage is floored to an integer when
    applied, so treat these as pre-floor values -- the difference is at most one
    HP and never changes which bucket a roll lands in except exactly on a
    boundary.)
    """
    if hi <= lo:
        return [lo] * N_ROLLS
    span = hi - lo
    return [lo + span * i / (N_ROLLS - 1) for i in range(N_ROLLS)]


def kill_probability(lo, hi, target_hp):
    """P(this move kills), as a fraction of the 16 rolls.

    The quantity the VGC corpus actually states -- "pick up the OHKO with it
    (62.5%)" is 10/16 -- and the one distinction outcome bucketing has to
    preserve: did it die.
    """
    if target_hp <= 0:
        return 1.0
    values = roll_values(lo, hi)
    return sum(1 for v in values if v >= target_hp) / N_ROLLS


def kill_fraction_label(lo, hi, target_hp):
    """Human-readable '10/16' for the UI, or None when it is not in question."""
    p = kill_probability(lo, hi, target_hp)
    if p in (0.0, 1.0):
        return None
    return f"{round(p * N_ROLLS)}/{N_ROLLS}"


def scenarios(enabled=True):
    """(roll_index, weight) pairs to simulate for one turn."""
    if not enabled:
        return [(None, 1.0)]          # single average-roll simulation
    return list(zip(DEFAULT_SCENARIOS, DEFAULT_SCENARIO_WEIGHTS))


def risk_aggregate(values, weights, risk=1.0):
    """Collapse per-scenario outcomes into one number.

    `risk` is the fraction of the (weighted) distribution to read, worst-first:

        1.0   risk-neutral -- the plain weighted mean
        0.4   reads the worst 40%: a play that collapses on a low roll is
              penalised, one whose low roll is merely less good is not
        ->0   fully roll-averse -- assume the worst roll always happens

    Implemented as the CVaR already used for the preview sweep, so there is one
    definition of "read the bad tail" in the codebase rather than two.
    """
    from preview import cvar
    return cvar(list(values), list(weights), alpha=risk)


def roll_sensitivity(values):
    """Spread between the best and worst roll outcome for one play.

    The reportable form of "does this play rely on a roll?" -- large means the
    line's value depends on the dice, which is what makes it unsafe even when
    its average is good.
    """
    if not values:
        return 0.0
    return max(values) - min(values)
