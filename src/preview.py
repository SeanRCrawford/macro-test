"""Team preview: how likely is each enemy bring, and how bad is our downside?

Design doc section 6, as revised in 6a/6b. Two changes to how the bring sweep is
scored, neither of which needs an equilibrium solve.

**Plausibility, not equilibrium support.** The earlier proposal solved the bring
matrix for its Nash equilibrium and treated the support as "the credible
brings". That is a hard cutoff -- a bring is either in the support or has
probability zero -- and it is exactly what a real opponent violates: they bring
somewhat unexpected leads without bringing their worst. A soft weighting

    P(their bring j)  proportional to  exp(value_to_them(j) / tau)

spans both extremes with one tunable number, and both current proposals are its
endpoints:

    tau -> infinity   uniform over all 90        the existing sweep
    tau  moderate     good brings likelier       what we actually want
    tau -> 0          all mass on their best     Nash support

**A tail objective, not a count or a worst case.** "How many of the 90 do we
beat" is a count and "the single worst of the 90" is dominated by brings no
rational opponent picks. The real goal is not to lose badly to a bring they
would plausibly make, which is a downside measure over the weighted
distribution:

    score(our bring) = CVaR_alpha = mean outcome across the worst alpha fraction

alpha = 1 recovers the mean; small alpha with large tau recovers today's
worst-case-over-90.

Keeping the weighting soft matters for more than realism: an absurd bring gets
a small weight rather than being deleted, so it can still surface in the tail if
it is CATASTROPHIC for us -- the one case where we do want to hear about it.
Hard support-pruning would silently drop exactly that case.
"""
import math

# tau in the same units as the margins (heuristic_eval points, KO_WEIGHT = 180).
# At 120 a bring one Pokemon worse for them than their best carries roughly
# exp(-1.5) ~= 22% of its weight: clearly less likely, not impossible.
DEFAULT_TAU = 120.0

# Fraction of the (weighted) distribution the score looks at. 0.3 reads the
# worst third of plausible brings -- bad match-ups without being a single
# worst-case outlier.
DEFAULT_ALPHA = 0.3


def plausibility_weights(our_margins, tau=DEFAULT_TAU):
    """Probability of each enemy bring, given how good it is FOR THEM.

    `our_margins[j]` is our margin against bring j, so their value is its
    negation. Returns weights summing to 1, in the same order.
    """
    n = len(our_margins)
    if n == 0:
        return []
    if tau is None or tau == float("inf"):
        return [1.0 / n] * n          # uniform: the existing behaviour
    if tau <= 0:
        best = min(range(n), key=lambda j: our_margins[j])   # worst for us
        return [1.0 if j == best else 0.0 for j in range(n)]

    their_value = [-m for m in our_margins]
    ceiling = max(their_value)        # subtract the max for numerical stability
    raw = [math.exp((v - ceiling) / tau) for v in their_value]
    total = sum(raw)
    if total <= 0:
        return [1.0 / n] * n
    return [x / total for x in raw]


def cvar(values, weights, alpha=DEFAULT_ALPHA):
    """Mean of the worst `alpha` fraction of a weighted distribution.

    alpha = 1 is the plain weighted mean; as alpha shrinks this approaches the
    single worst outcome carrying any weight.
    """
    if not values:
        return 0.0
    alpha = min(max(alpha, 1e-9), 1.0)
    order = sorted(range(len(values)), key=lambda j: values[j])

    taken = 0.0
    total = 0.0
    for j in order:
        w = weights[j]
        if w <= 0:
            continue
        use = min(w, alpha - taken)
        if use <= 0:
            break
        total += use * values[j]
        taken += use
        if taken >= alpha:
            break
    if taken <= 0:
        # Every weight was zero up to alpha; fall back to the plain worst.
        return min(values)
    return total / taken


def downside_score(our_margins, tau=DEFAULT_TAU, alpha=DEFAULT_ALPHA):
    """Our score against the brings they would plausibly pick.

    Higher is better. This replaces both "beats N of 90" and "worst of 90".
    """
    if not our_margins:
        return 0.0
    weights = plausibility_weights(our_margins, tau)
    return cvar(our_margins, weights, alpha)


def ranked_brings(our_margins, labels=None, tau=DEFAULT_TAU):
    """Enemy brings ordered by how likely they are, most plausible first.

    The user-facing replacement for the equilibrium-support "credible bring
    list": a ranking rather than a set, which is both more useful to show and
    more honest about what is actually known.
    """
    weights = plausibility_weights(our_margins, tau)
    rows = [
        {"index": j,
         "label": labels[j] if labels else j,
         "probability": weights[j],
         "our_margin": our_margins[j]}
        for j in range(len(our_margins))
    ]
    rows.sort(key=lambda r: -r["probability"])
    return rows
