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


def _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll,
        enemy_sets=None):
    oc = make_team(our_names, merged, natures, sets=our_sets)
    ec = make_team(enemy_names, merged, natures, sets=enemy_sets)
    b = Battle(oc, ec, typechart, moves_db)
    b.force_roll = roll          # 'min' | 'avg' | 'max'
    return b


def _movesets(battle, merged, moves_db, our_sets, enemy_sets=None):
    ms = {}
    for c in battle.p1.roster + battle.p2.roster:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
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
    b._movesets = ms  # scripted_openings.py's generic alternatives need real moves
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


def _quick_turn1_outcome(desc, our_bring4, enemy_names, merged, moves_db, natures, typechart,
                          script, our_sets, roll):
    """Cheap stand-in for play_committed: resolve ONLY turn 1 (no multi-turn
    solver loop), and return a rough score for ranking candidate turn-1
    actions -- our total remaining HP minus theirs, with an outright turn-1
    KO of either side given a dominant score. This is what makes the
    screening pass in find_plan_unknown_backs cheap: the expensive part of
    play_committed is the up-to-13-turn solve_best_action loop after turn 1,
    which this skips entirely.
    """
    b = _mk(our_bring4, enemy_names, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(b, merged, moves_db, our_sets)
    b._movesets = ms  # scripted_openings.py's generic alternatives need real moves
    fixed = rebind(desc, b)
    if fixed is None:
        return -1e9
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
        return -1e9
    if b.is_over():
        w = b.winner()
        if w == "p1":
            return 1e6
        if w == "p2":
            return -1e6
    return sum(c.current_hp for c in b.p1.roster) - sum(c.current_hp for c in b.p2.roster)


def enemy_brings(enemy_roster, enemy_lead=None, assumed_back=None):
    """The bring-4s consistent with what we can SEE at turn 1.

    Each side brings four. Handing a script their whole six is not a harder
    version of the matchup, it is a different game -- reported as "the scripted
    variants are WAY too tough, they have 6 pokemon always".

    A roster of four is already a bring. A roster of six means we have seen the
    lead and nothing else, so every C(4,2) back pair is still live; naming
    `assumed_back` narrows that to the one you are betting on.
    """
    roster = list(enemy_roster)
    if len(roster) <= 4:
        return [roster]
    lead = list(enemy_lead or roster[:2])
    rest = [x for x in roster if x not in lead]
    if assumed_back:
        return [lead + list(assumed_back)]
    return [lead + list(bp) for bp in itertools.combinations(rest, 2)]


def rank_openings(our_bring4, brings, merged, moves_db, natures, typechart,
                   team_name=None, our_sets=None, roll="min", aggregate="worst"):
    """Every legal turn-1 action of ours, ranked at the information set.

    THE information set is: our four, our lead, their lead. Not their backs
    (they have not come out) and not their turn-1 action (the turn is
    simultaneous). So each candidate is scored across every bring in `brings`
    crossed with every opening variant they might run, and the score is the
    aggregate -- `worst` for "must withstand anything", `sum` for "do best on
    average across them".

    Turn-1-only outcomes, which is what makes this affordable: the expensive
    part is playing the remaining turns, and a candidate that is already losing
    the opening exchange is not the answer to the lead.

    Returns [(score, desc)] best first.
    """
    variants = (all_scripts(team_name) if team_name else []) + [(None, None)]
    probe = _mk(our_bring4, brings[0], merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(probe, merged, moves_db, our_sets)
    probe._movesets = ms
    combine = min if aggregate == "worst" else sum

    ranked = []
    for opt in our_candidate_joint_actions(probe, probe.p1, probe.p2, ms, 1):
        desc = describe(opt)
        outcomes = [_quick_turn1_outcome(desc, our_bring4, enemy, merged, moves_db,
                                          natures, typechart, script, our_sets, roll)
                    for enemy in brings for _idx, script in variants]
        ranked.append((combine(outcomes), desc))
    ranked.sort(key=lambda x: -x[0])
    return ranked


def choose_opening(our_bring4, enemy_roster, merged, moves_db, natures, typechart,
                    team_name=None, enemy_lead=None, assumed_back=None,
                    our_sets=None, roll="min"):
    """The ONE turn-1 action to commit to, given only what we can see.

    `assumed_back` is the judgement call: of the six back pairs their lead could
    be hiding, bet on this one. The action is CHOSEN against it and should still
    be REPORTED against all six, so the cost of the bet is visible rather than
    assumed away.
    """
    brings = enemy_brings(enemy_roster, enemy_lead, assumed_back)
    ranked = rank_openings(our_bring4, brings, merged, moves_db, natures, typechart,
                            team_name=team_name, our_sets=our_sets, roll=roll)
    return ranked[0][1] if ranked else None


def find_plan_unknown_backs(our_bring4, enemy_roster, enemy_lead, merged, moves_db, natures,
                             typechart, team_name=None, max_turns=14, our_sets=None,
                             roll="min", screen_top=6):
    """Find a turn-1 action that works WITHOUT knowing the opponent's backs.

    At turn 1 you have seen their lead and nothing else. A plan chosen against one
    specific bring-4 can quietly depend on which two Pokemon sit behind it -- so
    the same lead could get a different "guaranteed" answer per back pair, which is
    not something you could execute.

    This requires ONE turn-1 action to beat every back pair the lead could be
    hiding (all C(4,2) of them) crossed with every scripted opening variant. Only
    then is it a plan you can commit to on sight of the lead alone.

    Cost control: the full check (play_committed's up-to-max_turns solve per
    back-pair x variant) is too expensive to run for every candidate turn-1
    action -- README previously flagged this as untested/likely to time out.
    Two-stage, same screen-then-verify pattern used elsewhere (e.g.
    search_robust_composition): first rank every candidate turn-1 action by a
    CHEAP turn-1-only outcome summed across all back-pairs x variants, then
    only run the expensive full verification on the top `screen_top`
    candidates. A real turn-1 answer to a fixed lead is usually also strong
    on the immediate exchange, so this rarely discards the true winner while
    cutting the expensive pass by roughly options/screen_top.

    Returns (desc, results, ok) where results maps (back_pair, variant) -> outcome.
    """
    rest = [x for x in enemy_roster if x not in enemy_lead]
    back_pairs = list(itertools.combinations(rest, 2))
    variants = (all_scripts(team_name) if team_name else []) + [(None, None)]

    probe_enemy = list(enemy_lead) + list(back_pairs[0])
    probe = _mk(our_bring4, probe_enemy, merged, moves_db, natures, typechart, our_sets, roll)
    ms = _movesets(probe, merged, moves_db, our_sets)
    options = our_candidate_joint_actions(probe, probe.p1, probe.p2, ms, 1)

    screened = []
    for opt in options:
        desc = describe(opt)
        total = 0.0
        for bp in back_pairs:
            enemy = list(enemy_lead) + list(bp)
            for idx, script in variants:
                total += _quick_turn1_outcome(desc, our_bring4, enemy, merged, moves_db, natures,
                                               typechart, script, our_sets, roll)
        screened.append((total, desc))
    screened.sort(key=lambda x: -x[0])

    best = None
    for _, desc in screened[:screen_top]:
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


def turn1_breakdown(our_names, enemy_roster, merged, moves_db, natures, typechart, team_name,
                     desc=None, our_sets=None, enemy_sets=None, roll="avg",
                     include_generic=True, max_turns=14,
                     enemy_lead=None, assumed_back=None):
    """Play ONE committed opening against everything it might meet.

    Three things this has to respect, all of which it previously broke:

    1. EACH SIDE BRINGS FOUR. `enemy_roster` may be their whole six -- that is
       what team preview shows you -- but the game is four against four, so the
       six is expanded into the bring-4s their lead could be fronting rather
       than being sent in as a six-Pokemon side.

    2. OUR TURN 1 CANNOT DEPEND ON THEIR TURN 1. The turn is simultaneous. This
       used to solve our response separately per variant, WITH that variant's
       script handed to the solver, so Hard Trick Room got one answer if its
       Incineroar faked out our left slot and a different answer if it faked out
       our right -- a plan that reads their mind. One action is chosen and then
       pinned everywhere.

    3. OUR TURN 1 CANNOT DEPEND ON THEIR BACKS. You do not know what is behind
       the lead until it comes out, so the same action is played against every
       back pair.

    Turns after the first DO use the solver, and that is correct: by then their
    line is observed.

    desc: pin this exact action instead of choosing one (e.g. from find_plan).
    assumed_back: the judgement call -- choose the action against this back pair
    only, while still reporting it against all of them, so the cost of the bet
    is visible.

    Returns {"opening", "assumed_back", "brings", "variants": {key: {...}}}.
    Each variant entry is its WORST bring-4 (the one that hurts us most), with
    every bring under "per_back".
    """
    # The bet narrows what the action is CHOSEN against; it must not narrow what
    # the action is REPORTED against, or backing a hunch would make the other
    # five back pairs disappear instead of showing what the hunch costs.
    brings = enemy_brings(enemy_roster, enemy_lead)
    if desc is None:
        desc = choose_opening(our_names, enemy_roster, merged, moves_db, natures,
                              typechart, team_name=team_name, enemy_lead=enemy_lead,
                              assumed_back=assumed_back, our_sets=our_sets, roll=roll)
    variants = all_scripts(team_name)
    if include_generic:
        variants = variants + [("greedy", None)]
    if not variants:
        variants = [("greedy", None)]

    out = {}
    for idx, script in variants:
        per_back = {}
        for enemy in brings:
            played = _play_one(desc, our_names, enemy, merged, moves_db, natures, typechart,
                               script, our_sets, enemy_sets, roll, max_turns)
            if played is not None:
                per_back[tuple(enemy)] = played
        if not per_back:
            continue
        worst_key = max(per_back,
                        key=lambda k: _loss_rank(per_back[k]["winner"], per_back[k]["turns"]))
        out[idx] = {**per_back[worst_key], "worst_bring": list(worst_key),
                    "per_back": per_back}
    return {"opening": desc, "assumed_back": list(assumed_back) if assumed_back else None,
            "brings": [list(b) for b in brings], "variants": out}


def _loss_rank(winner, turns):
    """Order outcomes worst-for-us first, so `max` picks the bring that hurts."""
    return (2, -turns) if winner == "p2" else ((0, turns) if winner == "p1" else (1, 0))


def _play_one(desc, our_names, enemy_names, merged, moves_db, natures, typechart,
               script, our_sets, enemy_sets, roll, max_turns):
    """One committed opening against one bring-4 and one opening variant,
    played to completion. Returns None if turn 1 was never legal."""
    b = _mk(our_names, enemy_names, merged, moves_db, natures, typechart, our_sets, roll,
            enemy_sets=enemy_sets)
    ms = _movesets(b, merged, moves_db, our_sets, enemy_sets=enemy_sets)
    b._movesets = ms  # scripted_openings.py's generic alternatives need real moves
    our_t1_action = enemy_t1_action = None
    for t in range(1, max_turns + 1):
        if b.is_over():
            break
        if t == 1:
            fixed = rebind(desc, b)
            if fixed is None:
                return None
        else:
            # From turn 2 the solver may use the script: their line is observed
            # by then, so this is knowledge, not clairvoyance.
            fixed, _, _ = solve_best_action(b, "p1", ms, depth=1, enemy_script=script)
            if not fixed:
                break
        opp = None
        if script is not None:
            try:
                opp = script(b, b.p2, b.p1, t)
            except Exception:
                opp = None
        if not opp:
            opp = greedy_opponent_joint_action(b, b.p2, b.p1, ms, t)
        if t == 1:
            our_t1_action = describe(fixed)
            enemy_t1_action = [(a.combatant.name, a.kind, a.move.name if a.move else "-")
                               for a in opp]
        try:
            b.run_turn(list(fixed), opp)
        except ValueError:
            break
    if our_t1_action is None:
        return None
    return {
        "winner": b.winner() or "timeout",
        "turns": b.turn_num,
        "our_t1_action": our_t1_action,
        "enemy_t1_action": enemy_t1_action,
        "hp_after": {c.name: (c.current_hp, c.max_hp()) for c in b.p1.roster + b.p2.roster},
        "log": b.log.dump(),
    }
