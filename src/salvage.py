"""
Salvage search: given a 2v2 lead matchup that currently loses (or wins
fragilely), try single changes -- an EV-spread swap, an item swap (including
a type-resist berry for an identified weakness), or swapping in one support
or setup move -- and see whether any of them flips the result.

Every candidate is re-scored with the REAL battle engine
(matchup_search.play_out_worst_case), not optimize_sets.py's offense-only
coverage heuristic -- "does it actually win more," not "does it hit harder
on paper." That heuristic is still used to pick a move to displace (the
current weakest-scoring move in the set), just not to judge the outcome.

Candidate support/setup moves are only tried if the species' own usage data
(merged[name]["moves_usage"]) actually lists them -- consistent with how the
rest of optimize_sets.py treats "not in the usage list" as "not legal to
suggest," rather than assuming every move is learnable by every Pokemon.
"""
from damage import type_multiplier
from species_data import TYPES
from matchup_search import play_out_worst_case
from optimize_sets import (bulk_spread_for, legal_items, TYPE_RESIST_BERRY,
                            move_value_table, candidate_moves)

SUPPORT_MOVES = ["Helping Hand", "Tailwind", "Icy Wind", "Electroweb", "Trick Room"]
SETUP_MOVES = ["Swords Dance", "Nasty Plot", "Dragon Dance", "Shell Smash", "Coaching"]


def worst_weakness_type(name, merged, typechart):
    """The attacking type this Pokemon's own typing is weakest to (for
    suggesting a matching resist berry). None if it has no >1x weakness."""
    types = merged[name]["types"]
    best_type, best_mult = None, 1.0
    for atype in TYPES:
        mult = type_multiplier(atype, types, typechart)
        if mult > best_mult:
            best_mult, best_type = mult, atype
    return best_type


def _learns(name, merged, move_name):
    return any(mv == move_name for mv, _ in merged[name]["moves_usage"])


def _rank(winner, turns):
    """Higher is better for us. Mirrors play_out_worst_case's own ranking."""
    if winner == "p1":
        return (2, -turns)
    if winner == "p2":
        return (0, turns)
    return (1, 0)


def _build_trials(our_names, enemy_names, merged, moves_db, natures, typechart,
                   our_sets):
    """Every single change worth trying, as (mon, kind, label, sets).

    Split out of salvage_losing_matchup so the joint search can build one trial
    list and replay it across several matchups.
    """
    trials = []
    for mon in our_names:
        spec = dict(our_sets.get(mon) or {})

        # 1. Bulk EV spread.
        bulk = bulk_spread_for(mon, merged)
        if bulk != merged[mon]["evs"]:
            new_spec = {**spec, "evs": bulk}
            trials.append((mon, "evs", f"bulk EV spread {bulk}", {**our_sets, mon: new_spec}))

        # 2. Type-resist berry for this mon's worst typing weakness.
        weak = worst_weakness_type(mon, merged, typechart)
        if weak and TYPE_RESIST_BERRY.get(weak):
            berry = TYPE_RESIST_BERRY[weak]
            if spec.get("item") != berry:
                new_spec = {**spec, "item": berry}
                trials.append((mon, "item", f"{berry} (resists {weak})", {**our_sets, mon: new_spec}))

        # 3. Swap in one support or setup move, replacing the current
        # weakest-scoring move in the set (by the coverage heuristic), only
        # if this species' own usage data actually lists the candidate move.
        current = spec.get("moves") or [m.name for m, _ in candidate_moves(
            mon, merged, moves_db, item=spec.get("item"))][:4]
        if current:
            table = move_value_table(mon, merged, moves_db, natures, typechart, enemy_names,
                                      item=spec.get("item"))
            scored_current = sorted(
                current, key=lambda m: sum(table.get(m, {}).values()) if m in table else -1)
            weakest = scored_current[0] if scored_current else None
            for cand_move in SUPPORT_MOVES + SETUP_MOVES:
                if cand_move in current or not _learns(mon, merged, cand_move):
                    continue
                if weakest is None:
                    continue
                new_moves = [cand_move if m == weakest else m for m in current]
                new_spec = {**spec, "moves": new_moves}
                kind = "support" if cand_move in SUPPORT_MOVES else "setup"
                trials.append((mon, kind, f"{cand_move} in for {weakest}",
                               {**our_sets, mon: new_spec}))
    return trials


def salvage_all_losses(our_names, losing_enemy_brings, merged, moves_db, natures,
                        typechart, max_turns=8, our_sets=None, require_all=False,
                        progress=None):
    """ONE change that fixes ALL the losses, not one change per loss.

    The Lead/Back search hands back a list of enemy brings this team loses to.
    Salvaging them one at a time answers the wrong question: six fixes for six
    losses is six different teams, and the fix for one matchup routinely breaks
    another. What a player actually needs is the single change -- if there is
    one -- that turns the whole set around.

    So each candidate change is replayed against EVERY losing bring and scored
    by how many it flips. A fix is only interesting if it improves on the
    baseline for a matchup; a change that wins four and loses the two we were
    already winning is not a salvage, which is why matchups we already beat can
    be passed in too and are checked for REGRESSION rather than for fixes.

    `require_all` abandons a candidate the moment it fails to fix one of them.
    That is much faster and is the right setting when only a complete answer is
    of interest; without it the partial fixes are ranked, which is what to look
    at when nothing fixes everything.

    Returns {"baseline": [...], "fixes": [{mon, kind, change, fixed, of,
    regressed, new_spec, per_matchup}], "trials": n}, best-first.
    """
    our_sets = dict(our_sets or {})
    brings = [list(b) for b in losing_enemy_brings]
    if not brings:
        return {"baseline": [], "fixes": [], "trials": 0}

    def play(sets, enemy4):
        w, t, _ = play_out_worst_case(our_names, enemy4, merged, moves_db, natures,
                                       typechart, max_turns, our_sets=sets)
        return w, t

    baseline = [play(our_sets, eb) for eb in brings]
    base_ranks = [_rank(w, t) for w, t in baseline]

    # Trials are built against the FIRST losing bring -- the enemy list only
    # decides which of our moves counts as the weakest one to displace, and
    # building a separate list per matchup would propose the same swap several
    # times over. Deduplicated by the set it produces, for the same reason.
    trials, seen = [], set()
    for eb in brings:
        for mon, kind, label, sets in _build_trials(our_names, eb, merged, moves_db,
                                                     natures, typechart, our_sets):
            fingerprint = (mon, kind, label)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            trials.append((mon, kind, label, sets))

    fixes = []
    for i, (mon, kind, label, sets) in enumerate(trials):
        per_matchup, fixed, regressed = [], 0, 0
        for eb, base_rank in zip(brings, base_ranks):
            w, t = play(sets, eb)
            rank = _rank(w, t)
            per_matchup.append({"enemy": list(eb), "winner": w, "turns": t,
                                "improved": rank > base_rank,
                                "regressed": rank < base_rank})
            if rank > base_rank:
                fixed += 1
            elif rank < base_rank:
                regressed += 1
            elif require_all:
                break        # cannot fix them all; stop paying for it
        if progress:
            progress(i + 1, len(trials))
        if require_all and fixed < len(brings):
            continue
        if fixed:
            fixes.append({"mon": mon, "kind": kind, "change": label,
                          "fixed": fixed, "of": len(brings),
                          "regressed": regressed, "new_spec": sets[mon],
                          "per_matchup": per_matchup})

    # Most matchups fixed first; among equals, the one that breaks least.
    fixes.sort(key=lambda f: (-f["fixed"], f["regressed"]))
    return {"baseline": baseline, "fixes": fixes, "trials": len(trials)}


def salvage_losing_matchup(our_names, enemy_names, merged, moves_db, natures, typechart,
                            max_turns=8, our_sets=None, roll="avg"):
    """our_names/enemy_names: a 2v2 lead matchup. Tries one change at a time,
    per our Pokemon, and reports every trial that improves the outcome
    (flips a loss to a win, or a draw/timeout to a win), best first.

    Returns {"baseline": (winner, turns), "fixes": [ {mon, kind, change,
    winner, turns} , ... ]} -- `fixes` is empty if nothing tried helped.
    """
    our_sets = dict(our_sets or {})
    base_w, base_t, _ = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures,
                                             typechart, max_turns, our_sets=our_sets)
    base_rank = _rank(base_w, base_t)

    trials = _build_trials(our_names, enemy_names, merged, moves_db, natures,
                            typechart, our_sets)

    fixes = []
    for mon, kind, change, new_sets in trials:
        w, t, _ = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures,
                                       typechart, max_turns, our_sets=new_sets)
        if _rank(w, t) > base_rank:
            # new_spec: just this mon's updated set fragment (one field changed --
            # evs/item/moves depending on `kind`), so a caller can apply the fix by
            # merging that one field onto the mon's current spec, rather than
            # needing to re-parse the human-readable `change` string.
            fixes.append({"mon": mon, "kind": kind, "change": change, "winner": w, "turns": t,
                          "new_spec": new_sets[mon]})

    fixes.sort(key=lambda f: -_rank(f["winner"], f["turns"])[0])
    return {"baseline": (base_w, base_t), "fixes": fixes}
