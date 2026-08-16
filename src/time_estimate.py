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

THE MODEL. Three stages, because the first version of this module had two and
was wrong in a way that only showed up when it was tested outside the range it
was fitted on. It treated everything before the audit as a constant "screen".
Measured at verify_top=1, a whole pairing came in at 16.5 s -- BELOW that
supposedly constant 25 s. The pre-audit cost is not constant: VERIFICATION
plays `verify_top x their configs` full games, so it scales with `--brings`
exactly like the audit does.

    seconds = MATRIX
            + PER_VERIFY_GAME * (verify_top * enemy_configs)
            + PER_AUDIT_UNIT  * (verify_top * audited * turns) * pilot

`audited` is 90 when all their brings are audited and the tier's `leads`
otherwise. Least-squares over five measurements spanning verify_top 1-6 and
audited 0-90; the two largest observations land within 3%, and the worst error
is +32% on the smallest (21.8 s predicted against 16.5 s measured, so 5 s in
absolute terms). Good enough to plan a night by, not good enough to bill for.

The equilibrium multiplier is measured on a thorough+ pairing rather than
carried over from the side-bias sweep: 11.0x, against the 13x that sweep
suggested.

WHAT IT IS NOT. An estimate, not a guarantee. It does not know about your
machine, the enemy roster's size, or a cache that makes the run instant. Every
number it produces should be shown as "about", and the app records what actually
happened (`record_actual`) and rescales, so the second estimate in a session is
better than the first.
"""

# Stage 1, the pair matrix: our 90 configurations against their 90, memoised to
# 225 distinct playouts. Genuinely constant -- it is what relative_cost misses.
MATRIX_SECONDS = 14.4

# Verification: one full game per (kept bring x enemy config). This is the term
# the first version of this module did not have, and it is why `--brings 90`
# costs far more than "the audit is linear in brings" suggests -- brings pays
# here TWICE, once for verification and once for the audit.
PER_VERIFY_GAME = 0.0236

# Seconds per audit unit, where one unit is one (bring x audited config x turn).
PER_AUDIT_UNIT = 0.1635

# Both sides solved as a payoff matrix every turn, rather than our solver
# against a fixed policy. Measured on a thorough+ pairing (804.8 s), not carried
# over from the side-bias sweep, which suggested 13x.
EQUILIBRIUM_MULTIPLIER = 11.0

# Their bring-4s. 90 for a free lead; 6 when their lead is fixed.
ENEMY_CONFIGS = 90

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


def tier_seconds(effort, pairings=1, audit_all=False, jobs=1, brings=None,
                 enemy_configs=ENEMY_CONFIGS):
    """Estimated wall clock for `pairings` (our team x one opponent) pairings.

    `brings` overrides the tier's `verify_top` -- the `--brings N` flag. It
    appears in BOTH the verification and audit terms, which is why raising it is
    dearer than the audit term alone suggests.

    `jobs` divides, but never below one pairing's worth: eight workers cannot
    make a single pairing finish in an eighth of the time.
    """
    from search_effort import tier as _tier
    t = _tier(effort)
    verify_top = brings or t["verify_top"]
    per = MATRIX_SECONDS + PER_VERIFY_GAME * verify_top * enemy_configs
    if t["robustness"]:
        audited = (enemy_configs if (audit_all or t.get("all_configs"))
                   else max(1, t["leads"]))
        units = verify_top * audited * max(1, t["turns"])
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
