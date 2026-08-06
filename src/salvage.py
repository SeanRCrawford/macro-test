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

    trials = []  # (mon, kind, change_label, new_sets)
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

    fixes = []
    for mon, kind, change, new_sets in trials:
        w, t, _ = play_out_worst_case(our_names, enemy_names, merged, moves_db, natures,
                                       typechart, max_turns, our_sets=new_sets)
        if _rank(w, t) > base_rank:
            fixes.append({"mon": mon, "kind": kind, "change": change, "winner": w, "turns": t})

    fixes.sort(key=lambda f: -_rank(f["winner"], f["turns"])[0])
    return {"baseline": (base_w, base_t), "fixes": fixes}
