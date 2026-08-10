"""Swap a rated team's worst member and re-rate it (design gap: §4.5).

THE GAP THIS CLOSES. Generation ends the moment a team is rated. Nothing ever
went back and asked the obvious follow-up question -- *which member is the
problem, and is there a better one* -- even though the Plan sheet already names
the brings that beat each team. This module is the machinery for that loop, and
`tools/substitute.py` is the driver.

It is HILL CLIMBING on the real objective, not another heuristic:

    propose cheaply (screener)  ->  accept expensively (full rating)

The proposals come from `fast_pair_score`, the same greedy screen the beam uses,
so a proposal is a guess. Nothing is ever accepted on that guess: every
candidate roster is rated by `roster_rating.rate_team` -- the audit that
produces exploitability and the adjusted win rate -- and a swap is kept only if
that number improves. So the search is finally steered by the quantity the
system judges teams on, which is the thing §4.1 says generation does not do.

TWO SIGNALS PICK THE MEMBER TO DROP, and they are not equally trustworthy:

  1. WAS IT EVEN BROUGHT. The rating commits to one bring-4 per opponent. A
     member that appears in none of them is, by the engine's own choice, doing
     nothing -- it is bench for every matchup we rated. This is real-engine
     evidence and it is weighted first.
  2. MARGINAL COVERAGE. How much the team's best answer to each enemy lead pair
     degrades if that member is unavailable, from the screener matrix. Cheap,
     and it is the only signal available for a member the audit has no opinion
     on (e.g. before any rating exists). A member whose removal costs nothing is
     redundant; one whose removal turns a winning matchup into a losing one is a
     keystone and is reported as such.

Replacements are aimed, not enumerated blindly: each opponent is weighted by how
badly the incumbent does against it (`opponent_weights`), so a candidate is
scored on the matchups the team is actually failing rather than on the ones it
already wins comfortably.

WHAT THIS CANNOT DO. It is a local search: one member at a time, accepting only
improvements, so it finds a local optimum near the team it started from and will
not discover a team two swaps away through a worse intermediate. That is a
deliberate trade -- each accepted step costs a full re-rating, minutes per team
-- and it is the reason the loop reports the path it took, so a plateau is
visible rather than looking like a finished search.
"""
import itertools

from species_data import base_form_name

# How much of an opponent's weight survives when we already beat it soundly.
# Not zero: a swap that fixes our worst matchup by wrecking a matchup we
# currently win 90/90 is not an improvement, and a floor of zero would make the
# proposal blind to that. The full rating would still catch it -- this only
# decides what gets PROPOSED -- but proposing teams that cannot be accepted
# wastes the expensive half of the loop.
MIN_OPPONENT_WEIGHT = 0.15


# ------------------------------------------------------------------ screening

class PairMatrix:
    """Screener margins for our lead pairs, computed on demand and memoised.

    Wraps the dict `team_search.build_pair_matrix` produces so a generation run
    can hand over what it already computed, while a team loaded from a file
    (whose members may not be in any pool) still works -- the missing pairs are
    filled in as they are asked for. Pair order is normalised, because the
    matrix is built over combinations and callers legitimately hold either
    order.
    """

    def __init__(self, merged, moves_db, natures, typechart, base=None):
        self.merged = merged
        self.moves_db = moves_db
        self.natures = natures
        self.typechart = typechart
        self.rows = dict(base or {})
        self._movesets = {}
        self.computed = 0        # how many margins this instance had to simulate

    def _row(self, our_pair):
        if our_pair in self.rows:
            return self.rows[our_pair], our_pair
        flipped = our_pair[::-1]
        if flipped in self.rows:
            return self.rows[flipped], flipped
        self.rows[our_pair] = {}
        return self.rows[our_pair], our_pair

    def margin(self, our_pair, enemy_key):
        """Our screened margin for `our_pair` against (team_name, enemy_pair)."""
        row, key = self._row(tuple(our_pair))
        if enemy_key not in row:
            from fast_eval import fast_pair_score
            r = fast_pair_score(key, tuple(enemy_key[1]), self.merged,
                                self.moves_db, self.natures, self.typechart,
                                self._movesets)
            row[enemy_key] = r["margin"]
            self.computed += 1
        return row[enemy_key]

    def best_margin(self, team, enemy_key, exclude=()):
        """The best any pair of `team` manages against one enemy lead pair.

        Models team preview the same way `team_search.team_coverage` does: we
        pick our lead after seeing their six, so what matters is whether SOME
        pair answers each threat.
        """
        members = [n for n in team if n not in exclude]
        pairs = list(itertools.combinations(members, 2))
        if not pairs:
            return None
        return max(self.margin(p, enemy_key) for p in pairs)


def coverage(team, enemy_keys, pm, weights=None):
    """Weighted mean of the team's best answer to each enemy lead pair."""
    total_w, acc = 0.0, 0.0
    for key in enemy_keys:
        w = 1.0 if weights is None else weights.get(key, 1.0)
        if w <= 0:
            continue
        best = pm.best_margin(team, key)
        if best is None:
            continue
        acc += w * best
        total_w += w
    return acc / total_w if total_w else 0.0


# ------------------------------------------------------------- who to drop

def bring_appearances(record):
    """{member: how many opponents' committed bring-4s contain it}.

    Reads `per_opponent[...]["our_bring4"]`, which `roster_rating.summarise`
    records. Returns {} when the record predates that field -- callers must
    treat "no opinion" and "never brought" as different things, since an empty
    dict would otherwise read as every member being dead weight.
    """
    per = (record or {}).get("per_opponent") or {}
    counts, seen_any = {}, False
    for rec in per.values():
        bring = (rec or {}).get("our_bring4")
        if not bring:
            continue
        seen_any = True
        for name in bring:
            counts[name] = counts.get(name, 0) + 1
    return counts if seen_any else {}


def brought_count(record, name):
    """How many committed bring-4s contain `name`, or None if unknown.

    The three-valued answer is the point, and it is easy to get wrong: a member
    that was never brought is ABSENT from `bring_appearances`, not present with
    a zero, so a plain `.get(name)` returns None for both "never brought" and
    "this record has no bring information at all". Those mean opposite things --
    damning evidence versus no evidence.
    """
    appearances = bring_appearances(record)
    return appearances.get(name, 0) if appearances else None


def rated_opponent_count(record):
    """How many opponents contributed a committed bring-4 to `record`."""
    per = (record or {}).get("per_opponent") or {}
    return sum(1 for rec in per.values() if (rec or {}).get("our_bring4"))


def drop_ranking(team, enemy_keys, pm, record=None, weights=None):
    """Rank members worst-first for removal, with the evidence for each.

    Sort key, in order:
      1. how many committed bring-4s contain the member (fewer is worse);
      2. how many enemy lead pairs it is the ONLY answer to (fewer is worse) --
         a keystone is never dropped ahead of a redundant member;
      3. its marginal coverage (less is worse).

    `record` is optional: without a rating, signal 1 is simply absent and the
    ranking falls back to the screener. That is what makes the loop usable on a
    team that has never been rated.
    """
    rated_opponents = rated_opponent_count(record) or None

    base = {key: pm.best_margin(team, key) for key in enemy_keys}
    out = []
    for name in team:
        marginal, keystone, support, total_w = 0.0, 0, 0.0, 0.0
        for key in enemy_keys:
            w = 1.0 if weights is None else weights.get(key, 1.0)
            without = pm.best_margin(team, key, exclude=(name,))
            if without is None or base[key] is None:
                continue
            marginal += w * max(0.0, base[key] - without)
            # The case that matters most: with it we beat this lead pair, and
            # without it we do not.
            if base[key] > 0 >= without:
                keystone += 1
            # How well the member does in the pairs it is actually PART of.
            # Marginal value alone leaves whole groups of members tied at zero
            # -- anyone the team's best pair does not need scores nothing --
            # and the tie then falls to list order, which is not a reason to
            # drop one Pokemon rather than another.
            mine = [p for p in itertools.combinations(team, 2) if name in p]
            support += w * max(pm.margin(p, key) for p in mine)
            total_w += w
        out.append({
            "name": name,
            "brought_by": brought_count(record, name),
            "rated_opponents": rated_opponents,
            "marginal": marginal,
            "keystone_for": keystone,
            "support": support / total_w if total_w else 0.0,
        })
    out.sort(key=lambda d: (d["brought_by"] if d["brought_by"] is not None
                            else 0, d["keystone_for"], d["marginal"],
                            d["support"]))
    return out


# --------------------------------------------------------- what to bring in

def record_rate(record):
    """Wins as a share of their configurations -- `Wins / Of` on the Plan sheet.

    None when nothing was played (a candidate a screen threw out), which callers
    must treat as "unknown" rather than as zero.
    """
    total = (record or {}).get("total") or 0
    return ((record or {}).get("wins") or 0) / total if total else None


def lost_record(incumbent, candidate):
    """How much of the incumbent's record a candidate gives up, or None.

    Positive means the candidate wins a SMALLER share of their configurations.
    The loop rejects on this before it looks at anything else: the win count and
    the adjusted win rate come from different pilots (WORKFLOW.md §4.4) and can
    move in opposite directions, and the record is the number the Plan sheet is
    read on.
    """
    before, after = record_rate(incumbent), record_rate(candidate)
    if before is None or after is None:
        return None
    return before - after


def opponent_weights(record, opponents):
    """How much each opponent should steer the next swap.

    `1 - adjusted win rate`, floored, so the search aims at the matchups the
    team is actually failing. Adjusted wins rather than the raw count because
    that is what the loop accepts on: aiming at a different number than the one
    that decides acceptance proposes teams that cannot be accepted.

    Uniform when the record has no per-opponent detail.
    """
    per = (record or {}).get("per_opponent") or {}
    weights = {}
    for name in opponents:
        rec = per.get(name)
        if not rec:
            weights[name] = 1.0
            continue
        adj = rec.get("adjusted_win_rate")
        if adj is None:
            wins, total = rec.get("wins"), rec.get("total")
            adj = (wins / total) if (wins is not None and total) else 0.0
        weights[name] = max(MIN_OPPONENT_WEIGHT, 1.0 - float(adj))
    return weights


def key_weights(enemy_keys, per_opponent_weight):
    """Spread the per-opponent weights over their enemy lead pairs."""
    return {key: per_opponent_weight.get(key[0], 1.0) for key in enemy_keys}


def legal_swap(team, drop, candidate, max_megas=2):
    """Is `team - drop + candidate` a team you could actually bring?

    Three rules, and the third is the one the beam search does not enforce:
    a Mega pick and its base form are the SAME SPECIES, so a team cannot hold
    both. The beam has never proposed one because it builds from a pool of
    distinct names, but a substitution is exactly the operation that would.
    """
    if candidate in team or candidate == drop:
        return False
    new = [n for n in team if n != drop] + [candidate]
    if sum(1 for n in new if n.startswith("Mega ")) > max_megas:
        return False
    species = [base_form_name(n) or n for n in new]
    return len(set(species)) == len(species)


def replacement_ranking(team, drop, pool, enemy_keys, pm, weights=None,
                        top_k=5, max_megas=2):
    """Rank pool members as the replacement for `drop`, best-first.

    Scored by the weighted coverage of the resulting team, so the comparison is
    against the incumbent on exactly the same enemy pairs and weights. The
    `gain` reported is relative to the team as it stands -- positive means the
    screener expects an improvement, and the point of the loop is that the
    screener does not get the last word.
    """
    incumbent = coverage(team, enemy_keys, pm, weights)
    scored = []
    for cand in pool:
        if not legal_swap(team, drop, cand, max_megas=max_megas):
            continue
        new_team = [n for n in team if n != drop] + [cand]
        score = coverage(new_team, enemy_keys, pm, weights)
        scored.append({"candidate": cand, "team": new_team,
                       "coverage": score, "gain": score - incumbent})
    scored.sort(key=lambda d: -d["gain"])
    return scored[:top_k]


def propose(team, pool, enemy_keys, pm, record=None, weights=None,
            drops=2, swaps=4, limit=6, max_megas=2):
    """The round's candidate rosters, best predicted gain first.

    Considers the `drops` worst members, the `swaps` best replacements for each,
    and returns at most `limit` distinct rosters. Deduplicated by roster
    CONTENT: two different (drop, candidate) pairs can produce the same six, and
    rating it twice is minutes wasted.
    """
    ranking = drop_ranking(team, enemy_keys, pm, record=record, weights=weights)
    proposals, seen = [], {frozenset(team)}
    for entry in ranking[:drops]:
        for rep in replacement_ranking(team, entry["name"], pool, enemy_keys,
                                       pm, weights=weights, top_k=swaps,
                                       max_megas=max_megas):
            fingerprint = frozenset(rep["team"])
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            proposals.append({**rep, "drop": entry["name"],
                              "drop_evidence": entry})
    proposals.sort(key=lambda d: -d["gain"])
    return proposals[:limit]
