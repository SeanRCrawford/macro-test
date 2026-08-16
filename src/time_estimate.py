"""How long will this take? Measured, not guessed.

Asked for: "make sure sections have an estimated time to complete the
calculation." The app had prose -- "minutes to hours", "expect seconds at depth
1" -- and one number, `search_effort.relative_cost`, which is not a wall-clock
predictor and is wrong by about an order of magnitude when read as one:

    tier         relative_cost    MEASURED per pairing    real ratio
    quick                  1.0                  25.0 s          1.0x
    standard              17.0                  39.8 s          1.6x
    thorough              73.0                  94.8 s          3.8x

`relative_cost` models the AUDIT and treats the screen as free. The screen is
not free -- it plays 90 of our configurations against 90 of theirs -- and at the
cheaper tiers it is most of the wall clock. Told "17x", you budget a night for
something that takes forty minutes, or the reverse.

THE MODEL. Two terms, two constants, fitted to the measurements above:

    seconds = SCREEN + PER_AUDIT_UNIT * (verify_top * configs * turns) * pilot

where `configs` is 90 when all the enemy's brings are audited and the tier's
`leads` otherwise, and `pilot` is 13 for the equilibrium tiers (a full payoff
matrix per turn for both sides). Fitted on quick and standard, it predicts
thorough to within 3.4% -- which is the only reason to trust it at all, since a
two-point fit through two points is not evidence of anything.

WHAT IT IS NOT. An estimate, not a guarantee. It does not know about your
machine, the enemy roster's size, or a cache that makes the run instant. Every
number it produces should be shown as "about", and the app records what actually
happened (`record_actual`) and rescales, so the second estimate in a session is
better than the first.
"""

# Seconds for the screen stage of one (our team x one opponent) pairing:
# every one of our 90 bring/lead configurations against every one of their 90.
# Tier-independent -- every tier pays it, which is exactly what relative_cost
# misses.
SCREEN_SECONDS = 25.0

# Seconds per audit unit, where one unit is one (bring x enemy config x turn).
PER_AUDIT_UNIT = 0.1542

# Both sides solved as a payoff matrix every turn, rather than our solver
# against a fixed policy. The side-bias sweep put this at ~13x; measured HERE,
# on a thorough+ pairing, it is 11.7 -- 804.8 s against the 891 s that 13x
# predicts. The measured value is used, so thorough+ is now an anchor rather
# than an extrapolation.
EQUILIBRIUM_MULTIPLIER = 11.7

# Panels that are not tier-driven, measured directly. Seconds.
FIXED = {
    # punish_screen.screen_team over the library, per team.
    "punish_screen_per_team": 21.0,
    # preview_lead.rank_leads: one opening solve, both sides, one position.
    "opening_solve": 0.75,
    # One full line played out against one enemy lead, before win-probability
    # sampling. Measured: 3.1s at 0 games, 6.6s at 4, 10.0s at 12.
    #
    # SUB-LINEAR, and the reason matters: `win_samples` is a CAP, not a count.
    # win_rate.matchup_win_prob_adaptive stops as soon as the Wilson interval is
    # tight enough, so asking for 12 games rarely plays 12. A linear model fitted
    # to the endpoints predicts 5.4s at 4 games against a measured 6.6; sqrt
    # fits all three within 0.9s.
    "line_base": 3.1,
    "line_per_root_game": 1.75,
    # deep_dive.audit_position at depth 1 / depth 2, one position.
    "deep_dive_depth1": 20.0,
    "deep_dive_depth2": 240.0,
}


def line_seconds(win_games):
    """One reported line against one enemy lead, at `win_games` samples (a cap,
    not a count -- see FIXED)."""
    import math
    return (FIXED["line_base"]
            + FIXED["line_per_root_game"] * math.sqrt(max(0, win_games)))


def leads_in_budget(budget, win_games):
    """How many of their 15 leads a budget actually reaches. The number worth
    printing: a 45 s budget at 8 games covers 6 of them, not all 15, and the
    panel is otherwise silent about which question it stopped answering."""
    return max(1, int(budget / line_seconds(win_games)))


def tier_seconds(effort, pairings=1, audit_all=False, jobs=1):
    """Estimated wall clock for `pairings` (our team x one opponent) pairings.

    `jobs` divides, but never below one pairing's worth: eight workers cannot
    make a single pairing finish in an eighth of the time.
    """
    from search_effort import tier as _tier
    t = _tier(effort)
    per = SCREEN_SECONDS
    if t["robustness"]:
        configs = 90 if (audit_all or t.get("all_configs")) else max(1, t["leads"])
        units = t["verify_top"] * configs * max(1, t["turns"])
        per += PER_AUDIT_UNIT * units * (
            EQUILIBRIUM_MULTIPLIER if t.get("pilot") == "equilibrium" else 1.0)
    total = per * max(1, pairings)
    if jobs and jobs > 1:
        total = max(per, total / jobs)
    return total


def humanise(seconds):
    """"about 40 s", "about 7 min", "about 2.5 h".

    Deliberately coarse. A number like "412 s" reads as a precision this does
    not have, and invites someone to notice it took 500 and conclude the
    estimate is broken rather than approximate.
    """
    if seconds is None:
        return "unknown"
    if seconds < 90:
        return f"about {int(round(seconds / 5.0)) * 5} s"
    if seconds < 5400:
        return f"about {seconds / 60:.0f} min"
    return f"about {seconds / 3600:.1f} h"


def describe(effort, pairings=1, audit_all=False, jobs=1):
    """One sentence for a caption, with the shape of the cost spelled out."""
    from search_effort import tier as _tier
    t = _tier(effort)
    total = tier_seconds(effort, pairings, audit_all, jobs)
    bits = [f"**{humanise(total)}**"]
    if pairings > 1:
        bits.append(f"{pairings} pairings")
    if jobs and jobs > 1:
        bits.append(f"{jobs} workers")
    if audit_all or t.get("all_configs"):
        bits.append("all 90 of their brings")
    if t.get("pilot") == "equilibrium":
        bits.append("equilibrium pilot (~13x)")
    return " — ".join(bits)


# --- calibration -----------------------------------------------------------
# The constants above were measured on one machine. Rather than ask anyone to
# re-measure, the app reports what a finished run actually took and the factor
# is corrected from there. Bounded, because one anomalous run (a warm cache, a
# laptop that throttled) should nudge the estimate rather than replace it.

_MIN_FACTOR, _MAX_FACTOR = 0.2, 5.0


def calibrate(factor, predicted, actual, weight=0.4):
    """Blend a new observation into the running factor. Pure, so the caller
    owns where it is stored (session state, a file, nothing at all)."""
    if not predicted or predicted <= 0 or not actual or actual <= 0:
        return factor
    observed = actual / predicted
    blended = factor * (1 - weight) + observed * weight
    return max(_MIN_FACTOR, min(_MAX_FACTOR, blended))
