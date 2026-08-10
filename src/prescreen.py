"""Rank candidate brings without simulating a battle. MEASURED: DOES NOT WORK.

    keep    recall    saved      (tools/measure_prescreen.py, 56 pairings,
       3       4%       97%       90 candidates each, recall = share of the
       5       9%       94%       full sweep's top-3 brings that survive)
       8      11%       91%
      12      15%       87%

At its widest setting this filter deletes the eventual winner 85% of the time.
It must not be enabled, and it is off on every tier. The module is kept as the
record of a negative result, not as a feature.

WHY IT FAILS, which is the useful part. The score is a set-based matching over
the four Pokemon brought, so it is blind to LEAD ORDER -- every one of the six
lead arrangements of a bring scores identically (verified: 75.89 for all three
tested orderings). The sweep it was meant to filter for ranks by
`fast_pair_score`, which reads ONLY the lead pair. The two rank on orthogonal
axes, so agreement was never likely.

AND THE PREMISE WAS WRONG. This was built on the claim that "cutting the
candidate set before the sweep is where the leverage is". It is not: the sweep's
screen is `fast_pair_score` over 90 configurations, cached by lead pair, and
costs almost nothing. The expensive stages are verification (verify_top x 90
full games) and the exploitability rating (verify_top x leads x turns payoff
matrices), both governed by `verify_top`. Prescreening candidates optimises a
stage that was never the bottleneck.

The lever that would actually work is the mirror image: prune the ENEMY
configurations rather than ours. Verification currently plays every survivor
against all 90 of their configurations, while section 6a establishes that most
are implausible and provides the weighting to say which. That has evidence
behind it, where this did not -- and it should still be measured before being
switched on.
"""
from combatants import make_team
from battle import Battle
from solver import build_moveset, build_wide_movesets, TOP_K_MOVES
from threat import build_threat_matrix, coverage_for


def coverage_score(our_names, enemy_names, merged, moves_db, natures, typechart,
                   our_sets=None, enemy_sets=None):
    """Static coverage of `our_names` against `enemy_names`. No battle is played.

    Higher is better. Comparable across candidate brings facing the SAME enemy
    roster; not meaningful across different opponents, since the threats differ.
    """
    ours = make_team(list(our_names), merged, natures, sets=our_sets)
    theirs = make_team(list(enemy_names), merged, natures, sets=enemy_sets)
    movesets = {}
    for c in ours + theirs:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
        movesets[c.name] = build_moveset(merged[c.name], moves_db,
                                         top_k=TOP_K_MOVES,
                                         only_moves=spec.get("moves"))
    battle = Battle(ours, theirs, typechart, moves_db)
    battle.movesets = movesets
    battle.wide_movesets = {
        **movesets,
        **build_wide_movesets([c.name for c in theirs], merged, moves_db),
    }
    matrix = build_threat_matrix(battle, movesets)
    return coverage_for(matrix, matrix.theirs, matrix.ours)


def rank_candidates(candidates, enemy_names, merged, moves_db, natures, typechart,
                    our_sets=None, enemy_sets=None):
    """(candidate, score) best-first. `candidates` are lists of Pokemon names."""
    scored = []
    for candidate in candidates:
        try:
            score = coverage_score(candidate, enemy_names, merged, moves_db,
                                   natures, typechart, our_sets, enemy_sets)
        except Exception:
            # A candidate that cannot even be built should not kill the search;
            # rank it last rather than raising.
            score = float("-inf")
        scored.append((candidate, score))
    scored.sort(key=lambda t: -t[1])
    return scored


def keep_top(candidates, enemy_names, merged, moves_db, natures, typechart,
             keep, our_sets=None, enemy_sets=None):
    """The `keep` best candidates by static coverage.

    `keep` of 0 or None disables the filter entirely, which is the honest
    default: a prescreen that silently discards candidates is a behaviour change
    and should be opted into.
    """
    if not keep or keep >= len(candidates):
        return list(candidates)
    ranked = rank_candidates(candidates, enemy_names, merged, moves_db, natures,
                             typechart, our_sets, enemy_sets)
    return [candidate for candidate, _score in ranked[:keep]]
