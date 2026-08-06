"""
Committed-plan verification.

The question this answers: is there a SINGLE turn-1 action that wins against
every way the opponent might open?

Why a separate module: the normal solver picks our move after computing the
opponent's action for that turn, so evaluating opponent variants one at a time
lets us quietly use a different answer to each. That is not a plan you can
execute -- at preview you commit to a lead, and on turn 1 to a move, without
knowing whether they run their script, Fake Out the other slot, or just attack.

This module pins turn 1 explicitly, then plays to completion, and can require
the win to hold on WORST-CASE damage rolls rather than average ones.
"""
import itertools

from battle import Battle
from engine import Action
from combatants import make_team
from solver import (build_moveset, our_candidate_joint_actions,
                     greedy_opponent_joint_action, solve_best_action, TOP_K_MOVES)
from scripted_openings import all_scripts


def _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll):
    oc = make_team(our_names, merged, natures, sets=our_sets)
    ec = make_team(enemy_names, merged, natures)
    b = Battle(oc, ec, typechart, moves_db)
    b.force_roll = roll          # 'min' | 'avg' | 'max'
    return b


def _movesets(battle, merged, moves_db, our_sets):
    ms = {}
    for c in battle.p1.roster + battle.p2.roster:
        spec = (our_sets or {}).get(c.name) or {}
        ms[c.name] = build_moveset(merged[c.name], moves_db, top_k=TOP_K_MOVES,
                                    only_moves=spec.get("moves"))
    return ms


def describe(actions):
    """A portable description of a turn-1 joint action."""
    out = []
    for a in actions:
        if a.kind == "switch":
            out.append((a.combatant.name, "switch", a.targets[0].name, ()))
        else:
            out.append((a.combatant.name, a.kind,
                        a.move.name if a.move else "-",
                        tuple(t.name for t in a.targets)))
    return tuple(out)


def rebind(desc, battle):
    """Rebuild a described joint action against a fresh battle, or None."""
    out = []
    for name, kind, label, tgt_names in desc:
        actor = next((c for c in battle.p1.active if c is not None and c.name == name), None)
        if actor is None:
            return None
        if kind == "switch":
            inc = next((c for c in battle.p1.bench if c.name == label and not c.fainted), None)
            if inc is None:
                return None
            out.append(Action(actor, "p1", "switch", None, [inc]))
            continue
        key = label.lower().replace(" ", "").replace("-", "").replace("'", "")
        if key not in battle.moves_db:
            return None
        mv = battle.make_move(key)
        tgts = [c for c in list(battle.p1.active) + list(battle.p2.active)
                if c is not None and c.name in tgt_names]
        out.append(Action(actor, "p1", kind, mv, tgts or [actor]))
    return out


def play_committed(desc, our_names, enemy_names, merged, moves_db, natures, typechart,
                    script, max_turns=14, our_sets=None, roll="avg"):
    """Pin turn 1 to `desc`, then let the solver finish. Returns (winner, turns, battle)."""
    b = _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(b, merged, moves_db, our_sets)
    fixed = rebind(desc, b)
    if fixed is None:
        return "illegal", 0, b
    opp = None
    if script is not None:
        try:
            opp = script(b, b.p2, b.p1, 1)
        except Exception:
            opp = None
    if not opp:
        opp = greedy_opponent_joint_action(b, b.p2, b.p1, ms, 1)
    try:
        b.run_turn(fixed, opp)
    except ValueError:
        return "illegal", 0, b
    for _ in range(max_turns - 1):
        if b.is_over():
            break
        _, _, sim = solve_best_action(b, "p1", ms, depth=1)
        if sim is None:
            break
        sim.force_roll = roll
        b = sim
    return (b.winner() or "timeout"), b.turn_num, b


def find_plan(our_names, enemy_names, merged, moves_db, natures, typechart, team_name,
               max_turns=14, our_sets=None, roll="min", require_all=True):
    """Search every legal turn-1 joint action for one that beats ALL opponent
    variants (each scripted opening plus the plain greedy 2v2).

    roll='min' demands the win hold on worst-case damage rolls, i.e. a
    guaranteed answer rather than one that depends on a favourable roll.

    Returns (desc, {variant: (winner, turns)}, wins_all) -- best found if none
    wins everything.
    """
    variants = all_scripts(team_name) + [(None, None)]
    probe = _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(probe, merged, moves_db, our_sets)
    options = our_candidate_joint_actions(probe, probe.p1, probe.p2, ms, 1)

    best = None
    for opt in options:
        desc = describe(opt)
        res, ok = {}, True
        for idx, script in variants:
            w, t, _ = play_committed(desc, our_names, enemy_names, merged, moves_db, natures,
                                      typechart, script, max_turns, our_sets, roll)
            res[idx] = (w, t)
            if w != "p1":
                ok = False
                if require_all:
                    break
        if ok and len(res) == len(variants):
            return desc, res, True
        score = sum(1 for v in res.values() if v[0] == "p1")
        if best is None or score > best[2]:
            best = (desc, res, score)
    return (best[0], best[1], False) if best else (None, {}, False)


def worst_response(desc, our_names, enemy_names, merged, moves_db, natures, typechart,
                    max_turns=14, our_sets=None, roll="min", cap=40):
    """Minimax turn 1: play our committed action against EVERY legal opponent
    response, not just their greedy pick or their script.

    The point is punishment. If we assume they make the obvious play (spread
    damage, hit the threatening slot) we may Protect the slot they were going to
    hit and eat a KO on the other. A play is only safe if NO response of theirs
    blows it up -- so this enumerates their joint actions and returns the worst
    outcome for us.

    `cap` limits how many of their responses are tried (they are generated in the
    solver's own preference order, so the dangerous ones come first); raise it
    for a more exhaustive check at proportional cost.

    Returns (worst_winner, worst_turns, worst_response_description, n_checked).
    """
    probe = _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(probe, merged, moves_db, our_sets)
    # Their legal joint actions, from their point of view.
    their_options = our_candidate_joint_actions(probe, probe.p2, probe.p1, ms, 1)[:cap]

    worst = None
    for topt in their_options:
        b = _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll)
        ms2 = _movesets(b, merged, moves_db, our_sets)
        fixed = rebind(desc, b)
        if fixed is None:
            continue
        # Rebind their action against this battle by description.
        their = []
        ok = True
        for a in topt:
            actor = next((c for c in b.p2.active if c is not None and c.name == a.combatant.name),
                          None)
            if actor is None:
                ok = False
                break
            if a.kind == "switch":
                inc = next((c for c in b.p2.bench if c.name == a.targets[0].name), None)
                if inc is None:
                    ok = False
                    break
                their.append(Action(actor, "p2", "switch", None, [inc]))
            else:
                key = a.move.name.lower().replace(" ", "").replace("-", "").replace("'", "")
                if key not in b.moves_db:
                    ok = False
                    break
                tn = {t.name for t in a.targets}
                tg = [c for c in list(b.p1.active) + list(b.p2.active)
                      if c is not None and c.name in tn]
                their.append(Action(actor, "p2", a.kind, b.make_move(key), tg or [actor]))
        if not ok:
            continue
        try:
            b.run_turn(fixed, their)
        except ValueError:
            continue
        for _ in range(max_turns - 1):
            if b.is_over():
                break
            _, _, sim = solve_best_action(b, "p1", ms2, depth=1)
            if sim is None:
                break
            sim.force_roll = roll
            b = sim
        w, t = (b.winner() or "timeout"), b.turn_num
        rank = (2, -t) if w == "p2" else ((0, t) if w == "p1" else (1, 0))
        if worst is None or rank > worst[0]:
            worst = (rank, w, t, describe(topt))
    if worst is None:
        return "unknown", 0, None, 0
    return worst[1], worst[2], worst[3], len(their_options)


def find_unpunishable_plan(our_names, enemy_names, merged, moves_db, natures, typechart,
                            team_name=None, max_turns=14, our_sets=None, roll="min", cap=25):
    """A turn-1 action that wins against every scripted opening AND cannot be
    punished by any of the opponent's legal responses. The strongest guarantee
    this toolkit offers, and correspondingly the slowest check."""
    variants = (all_scripts(team_name) if team_name else []) + [(None, None)]
    probe = _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(probe, merged, moves_db, our_sets)
    options = our_candidate_joint_actions(probe, probe.p1, probe.p2, ms, 1)

    best = None
    for opt in options:
        desc = describe(opt)
        res, ok = {}, True
        for idx, script in variants:
            w, t, _ = play_committed(desc, our_names, enemy_names, merged, moves_db, natures,
                                      typechart, script, max_turns, our_sets, roll)
            res[idx] = (w, t)
            if w != "p1":
                ok = False
                break
        if not ok:
            continue
        w, t, punish, n = worst_response(desc, our_names, enemy_names, merged, moves_db,
                                          natures, typechart, max_turns, our_sets, roll, cap)
        res["worst_response"] = (w, t, punish, n)
        if w == "p1":
            return desc, res, True
        if best is None:
            best = (desc, res, False)
    return best if best else (None, {}, False)


def find_plan_unknown_backs(our_bring4, enemy_roster, enemy_lead, merged, moves_db, natures,
                             typechart, team_name=None, max_turns=14, our_sets=None,
                             roll="min"):
    """Find a turn-1 action that works WITHOUT knowing the opponent's backs.

    At turn 1 you have seen their lead and nothing else. A plan chosen against one
    specific bring-4 can quietly depend on which two Pokemon sit behind it -- so
    the same lead could get a different "guaranteed" answer per back pair, which is
    not something you could execute.

    This requires ONE turn-1 action to beat every back pair the lead could be
    hiding (all C(4,2) of them) crossed with every scripted opening variant. Only
    then is it a plan you can commit to on sight of the lead alone.

    Returns (desc, results, ok) where results maps (back_pair, variant) -> outcome.
    """
    rest = [x for x in enemy_roster if x not in enemy_lead]
    back_pairs = list(itertools.combinations(rest, 2))
    variants = (all_scripts(team_name) if team_name else []) + [(None, None)]

    probe_enemy = list(enemy_lead) + list(back_pairs[0])
    probe = _mk(our_bring4, probe_enemy, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(probe, merged, moves_db, our_sets)
    options = our_candidate_joint_actions(probe, probe.p1, probe.p2, ms, 1)

    best = None
    for opt in options:
        desc = describe(opt)
        res, ok = {}, True
        for bp in back_pairs:
            enemy = list(enemy_lead) + list(bp)
            for idx, script in variants:
                w, t, _ = play_committed(desc, our_bring4, enemy, merged, moves_db, natures,
                                          typechart, script, max_turns, our_sets, roll)
                res[(bp, idx)] = (w, t)
                if w != "p1":
                    ok = False
                    break
            if not ok:
                break
        if ok and res:
            return desc, res, True
        score = sum(1 for v in res.values() if v[0] == "p1")
        if best is None or score > best[2]:
            best = (desc, res, score)
    return (best[0], best[1], False) if best else (None, {}, False)
