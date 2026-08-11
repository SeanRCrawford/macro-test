"""
Team GENERATION -- builds an optimal 6-Pokemon team from the full pool,
rather than arranging a pool you hand-picked.

PIPELINE (staged for speed -- a naive search is tens of thousands of full
battles, which is hours):

  1. CANDIDATE FILTER  -- drop preferences.csv exclusions, keep the top-N
     of the pool by roster.csv Score, always keep preferences.csv includes
     and prefers.
  2. PAIR MATRIX       -- for every candidate lead pair of ours x every
     possible enemy lead pair (all C(6,2) from each teams.csv team),
     run a fast greedy playout (fast_eval.py) and record a margin.
     This is the expensive step, but it's computed ONCE and then every
     candidate team is scored by cheap table lookups.
  3. BEAM SEARCH       -- grow teams from 2 to 6 members, at each step
     keeping the best `beam_width` partial teams by combined
     coverage + synergy score.
  4. SWITCH RESCUE     -- for the final team, find enemy pairs that beat
     our best lead, and check whether bringing in a specific bench member
     flips it, accounting for the damage taken on the switch-in turn.
  5. VERIFY            -- re-run the finalists through the real solver
     (matchup_search) so the headline numbers aren't screening numbers.

SCORING a team T against the set of enemy pairs E:
  coverage  = mean over e in E of ( best margin any pair within T achieves vs e )
  This models team preview: you see their team and choose your lead, so
  what matters is whether SOME pair of yours handles each threat -- not
  whether every pair does.
Plus synergy terms (type cores, the <=2-weakness-per-type rule, and average
Score), each weighted and reported separately so you can see the breakdown
rather than getting one opaque number.
"""
import itertools
from collections import defaultdict

from species_data import TYPES, load_preferences, mega_variants
from fast_eval import fast_pair_score

# The user-supplied complementary type cores (best-first).
TYPE_CORES = [
    ("Dragon", "Fairy", "Steel"), ("Fire", "Water", "Grass"), ("Fighting", "Psychic", "Dark"),
    ("Electric", "Ground", "Ice"), ("Fighting", "Dark", "Ghost"), ("Rock", "Flying", "Grass"),
    ("Poison", "Dark", "Flying"), ("Fire", "Water", "Electric"), ("Fire", "Fairy", "Ground"),
    ("Fighting", "Flying", "Ground"), ("Fire", "Psychic", "Bug"), ("Poison", "Water", "Ground"),
    ("Steel", "Fighting", "Electric"), ("Dragon", "Flying", "Water"), ("Poison", "Dark", "Fighting"),
    ("Psychic", "Steel", "Dark"), ("Electric", "Flying", "Grass"), ("Dragon", "Fire", "Grass"),
]

MAX_WEAK_PER_TYPE = 2
MATCHUP_WEIGHT = 1000.0
W_PREFER = 8.0         # per 'prefer' entry present -- a tiebreaker, never decisive
# MARGIN_WEIGHT scales `cov` (the mean fast_pair_score margin -- how convincingly the
# team's best-available pair handles each enemy lead pair, not just win/loss) into the
# main tiebreaker BETWEEN teams that already tie on pairs_won. This matters a lot in
# practice: many candidate teams beat every enemy pair at least narrowly, and without
# this term the ranking fell through to synergy (type cores / weakness rules), which
# says nothing about how those teams actually perform in the simulated matchups. cov
# typically spans roughly -240..+240 across real teams; weighting it this heavily makes
# a real performance gap of even a few points outrank the whole synergy block below.
MARGIN_WEIGHT = 3.0
W_CORE = 12.0          # bonus per rank-weighted type core matched
W_WEAKNESS = 25.0      # penalty per type exceeding the max-weaknesses rule
W_SCORE = 0.05         # small nudge toward higher effective-stat mons


# ---------------------------------------------------------------- candidates

def build_candidate_pool(merged, top_n=40, prefs=None, min_non_mega_frac=0.6,
                          allowed_generations=None, generation_map=None):
    """Filter the ~270-mon dataset down to a workable candidate list.

    Takes the top `top_n` by Score, but guarantees at least
    `min_non_mega_frac` of the pool is non-Mega. Without that quota the
    highest-Score entries are almost entirely Megas, and since a legal team
    can field at most 2 Mega picks, the team search would find NO valid
    6-mon combination at all (this actually happened -- the beam emptied
    silently at small pool sizes).

    allowed_generations: optional set of generation numbers (1-9) to restrict
    the pool to -- e.g. {3} for gen-3-only, {1,2,3,4,5} for gen 1-5. A form
    (Mega, regional, ...) counts as its BASE species' generation (see
    species_data.species_generation), matching how a player would actually
    mean "gen 3 only." `generation_map` is the precomputed {name: gen} dict
    (species_data.build_generation_map) -- required if allowed_generations is
    set. Include/prefer entries outside the allowed generations are dropped,
    same as an explicit exclude -- a generation restriction is a hard filter,
    not a suggestion that a stray prefer overrides.
    """
    prefs = prefs or {"include": [], "exclude": [], "prefer": []}
    # Excluding "Garchomp" should also exclude "Mega Garchomp" (and vice versa) --
    # they are the same Pokemon and banning one but not the other is almost never
    # what is meant.
    excluded = set(prefs["exclude"])
    for e in list(excluded):
        if e.startswith("Mega "):
            excluded.add(e[5:])
        else:
            excluded.update({f"Mega {e}", f"Mega {e} X", f"Mega {e} Y"})
    if allowed_generations:
        gen_map = generation_map or {}
        excluded |= {n for n in merged if gen_map.get(n) not in allowed_generations}
    forced = [n for n in (prefs["include"] + prefs["prefer"]) if n in merged and n not in excluded]

    scored = [(n, r["score"]) for n, r in merged.items()
              if n not in excluded and r.get("score") is not None]
    scored.sort(key=lambda x: -x[1])

    non_mega = [n for n, _ in scored if not n.startswith("Mega ")]
    megas = [n for n, _ in scored if n.startswith("Mega ")]

    want_non_mega = max(1, int(round(top_n * min_non_mega_frac)))
    pool = non_mega[:want_non_mega]
    pool += megas[:max(0, top_n - len(pool))]

    for n in forced:  # includes/prefers always make the cut
        if n not in pool:
            pool.append(n)
    return pool


def enemy_pairs_from_teams(teams: dict):
    """All C(6,2) lead pairs each enemy team could open with, tagged by team."""
    out = []
    for team_name, roster in teams.items():
        for pair in itertools.combinations(roster, 2):
            out.append((team_name, pair))
    return out


# ---------------------------------------------------------------- pair matrix

def build_pair_matrix(pool, enemy_pairs, merged, moves_db, natures, typechart,
                       progress=None):
    """{(our_a, our_b): {(team, enemy_pair): margin}} via the fast screener."""
    matrix = {}
    movesets = {}
    our_pairs = list(itertools.combinations(pool, 2))
    total = len(our_pairs)
    for i, op in enumerate(our_pairs):
        row = {}
        for team_name, ep in enemy_pairs:
            r = fast_pair_score(op, ep, merged, moves_db, natures, typechart, movesets)
            row[(team_name, ep)] = r["margin"]
        matrix[op] = row
        if progress and (i + 1) % max(1, total // 20) == 0:
            progress(i + 1, total)
    return matrix


# ---------------------------------------------------------------- synergy

def weakness_violations(team, merged, max_weak=None, max_net=None):
    """Count how badly a team breaks its defensive rules.

    max_weak: most members allowed to be weak to any one type (default
        MAX_WEAK_PER_TYPE).
    max_net:  most allowed NET weakness to any one type, i.e.
        (members weak) - (members resistant/immune). None disables the check.
        Net is often the better rule: three Fire-weak members matter much less
        if four others resist it.
    """
    max_weak = MAX_WEAK_PER_TYPE if max_weak is None else max_weak
    bad = 0
    detail = {}
    for t in TYPES:
        weak, resist = [], []
        for n in team:
            dc = merged[n].get("defensive_chart")
            if not dc:
                continue
            if dc[t] > 1.0:
                weak.append(n)
            elif dc[t] < 1.0:
                resist.append(n)
        over = max(0, len(weak) - max_weak)
        net = len(weak) - len(resist)
        over_net = max(0, net - max_net) if max_net is not None else 0
        if over or over_net:
            bad += over + over_net
            detail[t] = {"weak": weak, "resist": resist, "net": net}
    return bad, detail


def core_bonus(team, merged):
    """Rank-weighted bonus for containing any of the listed type cores.
    Earlier cores in the list are weighted slightly higher (user's ordering)."""
    team_types = set()
    for n in team:
        team_types.update(merged[n]["types"])
    total, matched = 0.0, []
    for rank, core in enumerate(TYPE_CORES):
        if set(core).issubset(team_types):
            w = 1.0 - (rank / (2 * len(TYPE_CORES)))  # 1.0 down to ~0.5
            total += w
            matched.append("/".join(core))
    return total, matched


# Weight enemy leads by how plausible they are FOR THEM, instead of treating all
# C(6,2) as equally likely. A good player does not lead with their worst pair, so
# averaging over all fifteen lets openings nobody would choose drag the score
# around -- the same argument section 6a makes for the audit, applied to the
# screen. `preview.plausibility_weights` already does the weighting.
#
# OFF: measured, and it changed nothing. Same harness as the shaped objective
# (tools/measure_objective.py, pool 34, top 4 each, played against the whole
# preset library with the real engine):
#
#     old (uniform)                  1927/2208  87.3%   best team 92%
#     plausibility-weighted          1928/2208  87.3%   best team 92%
#     plausibility + shaped          1928/2208  87.3%   best team 92%
#
# One game in two thousand, and it picks almost the same teams. So the more
# principled formulation is not wrong -- it is simply not what is limiting the
# search, which is the same conclusion the shaped objective reached from the
# other direction: both point at the screener margin underneath.
PLAUSIBILITY_WEIGHTED = False


def team_coverage(team, matrix, enemy_pairs):
    """Mean over enemy pairs of the best margin any of our pairs achieves.
    Models team preview: we choose our lead after seeing their team."""
    our_pairs = [p for p in itertools.combinations(team, 2) if p in matrix or p[::-1] in matrix]
    if not our_pairs:
        return -1e6, {}
    per_enemy = {}
    for team_name, ep in enemy_pairs:
        key = (team_name, ep)
        best = max((matrix[p][key] if p in matrix else matrix[p[::-1]][key]) for p in our_pairs)
        per_enemy[key] = best
    if not PLAUSIBILITY_WEIGHTED:
        return sum(per_enemy.values()) / len(per_enemy), per_enemy
    # Weighted WITHIN each opponent: plausibility is a choice they make between
    # their own leads, so normalising across the whole library would let a team
    # with more plausible leads count for more than another team.
    from preview import plausibility_weights
    by_team = {}
    for (team_name, ep), margin in per_enemy.items():
        by_team.setdefault(team_name, []).append(margin)
    total = 0.0
    for margins in by_team.values():
        weights = plausibility_weights(margins)
        total += sum(w * m for w, m in zip(weights, margins))
    return total / len(by_team), per_enemy


# --------------------------------------------------------------- the objective
#
# WHAT WAS WRONG WITH THE OLD ONE. It ranked on `pairs_won` (threats where SOME
# pair of ours scores a margin above zero) and then on the MEAN of those best
# margins. Three separate ways that picks teams that lose:
#
#   1. MEAN hides the threats that matter. A team that crushes twelve of their
#      leads and is crushed by three scores better than one that handles all
#      fifteen solidly. The three are the ones you actually meet, because at
#      preview THEY choose the lead -- so the mean rewards exactly the
#      lopsidedness that loses games.
#   2. A MARGIN OF +1 COUNTED AS AN ANSWER. `pairs_won` is a strict `> 0`
#      threshold on a greedy 2v2 playout, so a coin-flip counts the same as a
#      clean win, and a team could be credited with fifteen "answers" it would
#      lose half of.
#   3. NO CREDIT FOR A SECOND ANSWER. Coverage is a max over our pairs, so a
#      team whose only answer to a threat is one specific pair scores exactly
#      like a team with three answers -- until that pair is unavailable, which
#      is most of the game.
#
# The replacement keeps the same cheap inputs (the screener margin matrix, no
# extra simulation) and asks better questions of them:
#
#   answered    threats where some pair beats them by more than SAFE_MARGIN
#   downside    the mean of the WORST THIRD of our best-answer margins, so the
#               threats we handle badly are what the score is made of
#   depth       the share of threats with a SECOND independent answering pair
#
# Ranked lexicographically the same way as before -- answers first, then how
# convincing they are -- with synergy still a tiebreak that can never buy back
# a matchup.
# MEASURED, AND IT DID NOT WORK -- so all three ship neutral, and the terms are
# left here with the numbers rather than deleted.
#
# The test is the only one that counts: take the top 4 teams each objective
# proposes from the same pool and the same screener matrix, and play them
# against the whole preset library with the real engine
# (tools/measure_objective.py). Pool 34, quick tier, 8 opponents x 90 configs
# each:
#
#     objective   record across its 4 teams   best single team
#     old         1927/2208  (87.3%)                92%
#     new         1885/2208  (85.4%)                89%
#
# So the reasoning above is sound about what the old objective IGNORES, and
# fixing it still picked slightly worse teams. Worth being clear about why that
# is possible: the screener margin is a greedy 2v2 playout and the record is a
# greedy 4v4 count, so "average margin" tracks "average wins" quite well, while
# the tail and the redundancy this scores are real properties that the record
# does not reward. Optimising a better-shaped objective against the same crude
# margin did not survive contact.
#
# What would actually move this, in the order worth trying: a screener margin
# that is not a greedy 2v2 (the objective can only be as good as its input);
# scoring against the leads they would PLAUSIBLY bring rather than all C(6,2)
# uniformly (preview.plausibility_weights already exists); and validating any
# candidate objective the way above, since intuition lost here.
#
# Set these to the commented values to switch the new objective back on.
SAFE_MARGIN = 0.0      # 20.0: below this a screener "win" is a coin flip
W_DOWNSIDE = 0.0       # 6.0: weight on the worst third of our best answers
W_DEPTH = 0.0          # 120.0: weight on having a SECOND independent answer


def _worst_third(values):
    """Mean of the worst third. The whole objective in one line: a team is as
    good as the threats it handles WORST, not as good as its average."""
    if not values:
        return 0.0
    ordered = sorted(values)
    cut = max(1, len(ordered) // 3)
    return sum(ordered[:cut]) / cut


def answer_depth(team, matrix, enemy_pairs):
    """(answers with margin > SAFE_MARGIN, share of threats with two or more).

    Two answers matter because one of them will not be available: your best
    pair against their Rain lead is no use once half of it has fainted, or once
    you have committed it elsewhere in the bring. A team with a single answer to
    a threat is one KO away from having none.
    """
    our_pairs = [p for p in itertools.combinations(team, 2)
                 if p in matrix or p[::-1] in matrix]
    if not our_pairs:
        return 0, 0.0

    def val(p, key):
        return matrix[p][key] if p in matrix else matrix[p[::-1]][key]

    answered = deep = 0
    for team_name, ep in enemy_pairs:
        key = (team_name, ep)
        good = [p for p in our_pairs if val(p, key) > SAFE_MARGIN]
        if good:
            answered += 1
        # "Independent" means not sharing a Pokemon: two answers that both need
        # the same Mega are one answer.
        if len(good) >= 2 and any(not set(a) & set(b)
                                  for a in good for b in good if a != b):
            deep += 1
    return answered, (deep / len(enemy_pairs) if enemy_pairs else 0.0)


def score_team(team, matrix, enemy_pairs, merged, max_weak=None, max_net=None):
    """Scoring is LEXICOGRAPHIC in three tiers:
      1. `pairs_won` (how many enemy lead pairs at least one of our pairs beats)
         -- multiplied by a large constant so no amount of margin or synergy can
         ever buy back a lost matchup.
      2. `cov` (mean simulated margin -- see team_coverage) -- how CONVINCINGLY
         the team's best-available pair handles each threat, not just whether it
         nominally wins. This is the real prescreen signal: a great many
         candidate teams tie on pairs_won (often winning every enemy pair at
         least narrowly), and margin is what actually distinguishes "beats them
         comfortably" from "barely scrapes by" among those ties.
      3. `synergy` (type cores / weakness rule / avg Score) -- a small nudge
         that only matters once pairs_won AND margin are both tied.
    """
    cov, per_enemy = team_coverage(team, matrix, enemy_pairs)
    cb, matched_cores = core_bonus(team, merged)
    viol, viol_detail = weakness_violations(team, merged, max_weak=max_weak, max_net=max_net)
    avg_score = sum(merged[n]["score"] for n in team) / len(team)

    pairs_won = sum(1 for v in per_enemy.values() if v > 0)
    answered, depth = answer_depth(team, matrix, enemy_pairs)
    downside = _worst_third(list(per_enemy.values()))

    # Bounded well below MATCHUP_WEIGHT so it can never outrank a single extra
    # matchup won, but well above the synergy block so it dominates that tier.
    synergy = (W_CORE * cb) - (W_WEAKNESS * viol) + (W_SCORE * avg_score)
    synergy = max(-MATCHUP_WEIGHT * 0.2, min(MATCHUP_WEIGHT * 0.2, synergy))

    # With the parked weights at 0 and SAFE_MARGIN at 0, `answered` IS
    # pairs_won and this is exactly the original score -- asserted by
    # tests/test_objective.py, so the parked terms cannot drift into changing
    # the default behaviour.
    total = (answered * MATCHUP_WEIGHT
             + MARGIN_WEIGHT * cov
             + W_DOWNSIDE * downside
             + W_DEPTH * depth
             + synergy)
    return {
        "total": total, "pairs_won": pairs_won, "pairs_total": len(per_enemy),
        "answered": answered, "downside": downside, "answer_depth": depth,
        "coverage": cov, "synergy": synergy,
        "core_bonus": cb, "matched_cores": matched_cores,
        "weakness_violations": viol, "weakness_detail": viol_detail, "avg_score": avg_score,
        "per_enemy": per_enemy,
    }


# ---------------------------------------------------------------- beam search

def beam_search_teams(pool, matrix, enemy_pairs, merged, team_size=6, beam_width=40,
                       max_megas=2, must_include=None, prefer=None,
                       max_weak=None, max_net=None):
    """Grow teams from size 2 up to `team_size`, keeping the best partials.

    must_include: names that EVERY candidate team must contain. These are seeded
    into the beam from the start rather than filtered out at the end -- filtering
    afterwards fails whenever the search never happened to build a team with them,
    which silently dropped the constraint.
    prefer: names given a small scoring bonus, enough to break ties but not enough
    to override matchup results.
    """
    must_include = [n for n in (must_include or []) if n in pool]
    prefer = set(prefer or [])

    def mega_count(t):
        return sum(1 for n in t if n.startswith("Mega "))

    # Seed with the best pairs by raw coverage.
    if len(must_include) >= team_size:
        return [(score_team(must_include[:team_size], matrix, enemy_pairs, merged,
                             max_weak=max_weak, max_net=max_net),
                 must_include[:team_size])]

    if len(must_include) >= 2:
        # Everything required is already in: start from exactly that set.
        beam = [list(must_include)]
    else:
        seeds = []
        for p in itertools.combinations(pool, 2):
            if p not in matrix or mega_count(p) > max_megas:
                continue
            if must_include and must_include[0] not in p:
                continue      # every seed must carry the required Pokemon
            cov, _ = team_coverage(list(p), matrix, enemy_pairs)
            seeds.append((cov, list(p)))
        seeds.sort(key=lambda x: -x[0])
        beam = [t for _, t in seeds[:beam_width]]

    start_size = len(beam[0]) if beam else 2
    for _ in range(team_size - start_size):
        cand = {}
        for team in beam:
            for n in pool:
                if n in team:
                    continue
                nt = sorted(team + [n])
                if mega_count(nt) > max_megas:
                    continue
                key = tuple(nt)
                if key in cand:
                    continue
                sc = score_team(nt, matrix, enemy_pairs, merged,
                                 max_weak=max_weak, max_net=max_net)["total"]
                # Small nudge for preferences.csv "prefer" entries -- enough to break
                # ties between otherwise equal teams, never enough to outweigh a
                # matchup (MATCHUP_WEIGHT is 1000 per enemy pair beaten).
                sc += W_PREFER * len(prefer.intersection(nt))
                cand[key] = sc
        ranked = sorted(cand.items(), key=lambda kv: -kv[1])[:beam_width]
        beam = [list(k) for k, _ in ranked]
        if not beam:
            break

    final = [(score_team(t, matrix, enemy_pairs, merged, max_weak=max_weak,
                          max_net=max_net), t) for t in beam]
    final.sort(key=lambda x: -x[0]["total"])
    return final


# ---------------------------------------------------------------- switch rescue

def find_switch_rescues(team, matrix, enemy_pairs, merged, moves_db, natures, typechart,
                         margin_floor=0.0):
    """For each enemy pair our best lead still LOSES to, test whether bringing
    in a bench member on turn 1 flips it -- and report the HP cost of the
    switch, so a 'rescue' that arrives at 10% HP isn't sold as a solution.

    Returns a list of dicts describing losing matchups and any working answer.
    """
    from fast_eval import fast_playout
    out = []
    our_pairs = [p for p in itertools.combinations(team, 2) if p in matrix]

    for team_name, ep in enemy_pairs:
        key = (team_name, ep)
        best_margin, best_pair = max(
            ((matrix[p][key], p) for p in our_pairs), key=lambda x: x[0]
        )
        if best_margin > margin_floor:
            continue  # already fine, nothing to rescue

        rescues = []
        for lead in our_pairs:
            bench = [n for n in team if n not in lead]
            for b in bench:
                for slot in (0, 1):
                    # Bring-4 with `b` benched behind the lead; the sim will switch it in
                    # when the slot mon faints. To test a *deliberate* early pivot we put
                    # the rescue mon directly opposite the threat as the replacement.
                    bring4 = [lead[0], lead[1], b, [x for x in bench if x != b][0]] \
                        if len(bench) > 1 else [lead[0], lead[1], b]
                    r = fast_playout(bring4, list(ep) + [], merged, moves_db, natures, typechart)
                    if r["margin"] > margin_floor and r["margin"] > best_margin:
                        rescues.append({
                            "lead": lead, "rescue_mon": b, "margin": r["margin"],
                            "our_hp_left": round(r["our_hp"], 2), "winner": r["winner"],
                        })
        rescues.sort(key=lambda x: -x["margin"])
        out.append({
            "team": team_name, "enemy_pair": ep, "best_lead": best_pair,
            "best_margin": best_margin, "rescues": rescues[:3],
        })
    return out


# ---------------------------------------------------------------- reporting

def team_items(team, merged):
    """Most-used item and ability per team member, for the team sheet."""
    rows = []
    for n in team:
        p = merged[n]
        item = p["items_usage"][0][0] if p["items_usage"] else "(none)"
        item_pct = p["items_usage"][0][1] if p["items_usage"] else 0.0
        from combatants import _default_ability
        ability = _default_ability(p["abilities_usage"]) if p["abilities_usage"] else "(none)"
        ability_pct = p["abilities_usage"][0][1] if p["abilities_usage"] else 0.0
        moves = [m for m, _ in p["moves_usage"][:4]]
        rows.append({
            "name": n, "types": "/".join(p["types"]), "item": item, "item_pct": item_pct,
            "ability": ability, "ability_pct": ability_pct, "nature": p["nature"],
            "score": p["score"], "moves": moves,
            "evs": p["evs"],
        })
    return rows


def member_contributions(team, matrix, enemy_pairs, merged):
    """Explain why each member earns its slot.

    For every enemy lead pair, find which of our pairs answers it best. A member
    is credited for the threats where a pair containing it is the team's best
    answer, and is marked IRREPLACEABLE for threats that no pair without it can
    beat -- that is the concrete case for keeping it.
    """
    our_pairs = [p for p in itertools.combinations(team, 2)
                 if p in matrix or p[::-1] in matrix]

    def val(p, key):
        return matrix[p][key] if p in matrix else matrix[p[::-1]][key]

    out = {n: {"best_answer": [], "irreplaceable": [], "wins": 0} for n in team}
    for team_name, ep in enemy_pairs:
        key = (team_name, ep)
        best_pair = max(our_pairs, key=lambda p: val(p, key))
        best_val = val(best_pair, key)
        if best_val <= 0:
            continue          # nothing on the team beats this threat
        for n in best_pair:
            out[n]["best_answer"].append((team_name, ep, best_val))
            out[n]["wins"] += 1
        # Would the team still beat this threat without each member?
        for n in team:
            without = [p for p in our_pairs if n not in p]
            if without and max(val(p, key) for p in without) <= 0:
                out[n]["irreplaceable"].append((team_name, ep))
    return out


def compute_team_analysis(team, merged, moves_db, natures, typechart, teams_csv):
    """Build the 'team detail' snapshot -- which threats/pairs each member
    contributes to most, and what the team was tested against -- so it can
    be persisted into team.json and shown after a plain load, not only right
    after a live generation run (see team_sheet.save_team's analysis param
    and app.py's Team Builder tab).

    Cheap to compute for any single 6-mon team: build_pair_matrix only needs
    the 15 in-team pairs here (pool=team), not the full candidate-pool
    matrix a generation run builds. Uses the same fast screener as
    generation (fast_pair_score/usage-default sets, not our_sets overrides)
    -- an approximation, same caveat as every other screener-based number
    in this tool, not the real-engine verified result.
    """
    enemy_pairs = enemy_pairs_from_teams(teams_csv)
    matrix = build_pair_matrix(list(team), enemy_pairs, merged, moves_db, natures, typechart)
    contrib = member_contributions(list(team), matrix, enemy_pairs, merged)

    members = {}
    for n in team:
        c = contrib[n]
        top_threats = sorted(c["best_answer"], key=lambda x: -x[2])[:5]
        members[n] = {
            "wins": c["wins"],
            "top_threats": [{"team": tn, "enemy_pair": list(ep), "margin": round(v, 2)}
                             for tn, ep, v in top_threats],
            "irreplaceable": [{"team": tn, "enemy_pair": list(ep)}
                               for tn, ep in c["irreplaceable"][:5]],
        }
    return {"tested_against": list(teams_csv.keys()), "members": members}
