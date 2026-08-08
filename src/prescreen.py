"""Rank candidate brings without simulating a single battle.

The expensive stages of a team search -- playing out 90 configurations, then
auditing survivors for exploitability -- are worth spending on good candidates
and wasted on bad ones. The threat matrix (src/threat.py) already answers "does
this team have an answer to each of their threats", costs about 0.7 ms per
matchup, and needs no simulation at all. Using it to cut the candidate set
BEFORE the sweep is where the leverage is at the expensive tiers: the
Exhaustive tier pays ~161x the Quick tier per candidate, so eliminating a
candidate for a millisecond is worth roughly six minutes of avoided work.

The score is the answer-preservation matching of section 4b, computed at full
health for the bring as brought:

    coverage(our four vs their six) = mean over their threats of how well our
                                      best available answer handles it

with the exclusivity that makes matching the right tool rather than a sum: one
Pokemon cannot be the answer to three of their threats at once.

IMPORTANT -- this is a FILTER, not a ranking to trust. It is a static,
full-health, no-simulation proxy: it cannot see speed control coming down, a
Trick Room flip, item effects that only matter mid-game, or anything about how
the line actually plays. Its only job is to be right about which candidates are
*obviously hopeless*, cheaply. `tools/measure_prescreen.py` measures how often
it drops a candidate the full sweep would have ranked top -- the number that
decides how aggressively it can be set.
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
