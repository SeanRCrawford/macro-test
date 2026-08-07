"""
Scripted opponent openings.

Three teams in teams.csv don't play greedily -- they execute a rehearsed
sequence that only makes sense as a whole, and a damage-maximising AI will
never reproduce it. Notably the greedy model would never Protect twice in a
row, never Perish Song (0 damage), and never spend a turn on Shell Smash.

Each script returns the opponent's actions for a given turn number, or None
to hand control back to the greedy model (once the plan is spent or has been
disrupted).

These are the tactics to beat, so a team is only really "safe" into these
opponents if it survives the scripted line -- not the greedy approximation.
Only the OPPONENT is scripted: our own side always plays adaptively (the real
solver, turn by turn), since that's the realistic assumption -- you see their
move before committing to yours on every turn after the first.
"""
from engine import Action

SETUP_MOVE = {"Mega Blastoise": "shellsmash", "Mega Delphox": "nastyplot"}
REDIRECT_MOVE = {"Sinistcha": "ragepowder", "Maushold": "followme", "Clefable": "followme"}


def _find(side, name):
    for c in side.active:
        if c is not None and not c.fainted and c.name == name:
            return c
    return None


def _move(battle, key):
    return battle.make_move(key)


def _target(opp_side, idx):
    """The idx-th live opposing Pokemon, or None. Scripts take a variant index so
    every targeting choice is tested: with a forced lead the opponent's decision
    space is tiny, so it should be searched exhaustively rather than assuming
    they always hit slot 1."""
    live = [f for f in opp_side.active if f is not None and not f.fainted]
    if not live:
        return None
    return live[idx % len(live)]


def _best_damaging_action(battle, attacker, targets):
    """attacker's highest-damage legal move against any of `targets`, using the
    real movesets stashed on the battle by whichever caller is running this
    script (see the `battle._movesets` assignments in matchup_search.py /
    committed_plan.py). Returns (damage, MoveInfo, target) or None."""
    ms = getattr(battle, "_movesets", None) or {}
    from solver import quick_damage_estimate
    best = None
    for move, _pct in ms.get(attacker.name, []):
        if move.category == "Status" or move.power == 0:
            continue
        for tgt in targets:
            dmg = quick_damage_estimate(attacker, tgt, move, battle.typechart, battle.field)
            if best is None or dmg > best[0]:
                best = (dmg, move, tgt)
    return best


def _protect_and_attack_script(protector_idx):
    """Generic turn-1-only alternative for ANY fixed-lead team: one active
    Protects, the other attacks with its best real move. This is a plausible
    thing a scripted team could do INSTEAD of its rehearsed opening, and
    unlike the greedy opponent model (which never Protects -- see
    solver.greedy_opponent_joint_action's action_value, which scores 'protect'
    as -1) it isn't covered by the plain 2v2 fallback either. So it needs to
    be its own explicit variant to actually be tested for pass/fail, not just
    shown for information (see committed_plan.turn1_breakdown, which used to
    carry a private copy of this same idea purely for display)."""
    def fn(battle, side, opp_side, turn):
        if turn != 1:
            return None
        actives = [c for c in side.active if c is not None and not c.fainted]
        if len(actives) != 2:
            return None
        protector = actives[protector_idx % 2]
        attacker = actives[(protector_idx + 1) % 2]
        opp_live = [c for c in opp_side.active if c is not None and not c.fainted]
        if not opp_live:
            return None
        protect_move = _move(battle, "protect")
        acts = [Action(protector, side.name, "protect", protect_move, [protector])]
        best = _best_damaging_action(battle, attacker, opp_live)
        if best:
            _, move, tgt = best
            acts.append(Action(attacker, side.name, "move", move, [tgt]))
        else:
            acts.append(Action(attacker, side.name, "protect", protect_move, [attacker]))
        return acts
    return fn


def king_script(battle, side, opp_side, turn, variant=0):
    """King: buy a turn with Fake Out / Rage Powder, set up Shell Smash on Mega
    Blastoise or Nasty Plot on Mega Delphox, then sweep."""
    if turn > 2:
        return None
    acts = []
    setup_done = False
    for c in side.active:
        if c is None or c.fainted:
            continue
        key = SETUP_MOVE.get(c.name)
        if key and not setup_done:
            acts.append(Action(c, side.name, "move", _move(battle, key), [c]))
            setup_done = True
        elif c.name in REDIRECT_MOVE:
            acts.append(Action(c, side.name, "move", _move(battle, REDIRECT_MOVE[c.name]), [c]))
        elif turn == 1 and c.name == "Incineroar":
            tgt = _target(opp_side, variant)
            if tgt is not None:
                acts.append(Action(c, side.name, "move", _move(battle, "fakeout"), [tgt]))
    return acts or None


def hard_trick_room_script(battle, side, opp_side, turn, variant=0):
    """Hard TR: T1 Fake Out + Trick Room, T2 Parting Shot to bring in a
    sweeper (Farigiraf keeps attacking). No further turns are scripted --
    from T3 the team plays out normally under a Trick Room it has already
    set, sweeping with whatever came in on T2.

    The real defensive aim for US here: KO Farigiraf T1 if at all possible
    (it's the ONLY Trick Room setter on this team -- kill it and the entire
    plan never gets off the ground). If that's not achievable, the fallback
    is to survive under Trick Room with careful Protects/switches until it
    expires (5 turns) rather than trying to out-damage a team that out-speeds
    everything of ours for that whole window.
    """
    acts = []
    inc = _find(side, "Incineroar")
    far = _find(side, "Farigiraf")
    tgt = _target(opp_side, variant)
    if turn == 1:
        if inc and tgt is not None:
            acts.append(Action(inc, side.name, "move", _move(battle, "fakeout"), [tgt]))
        if far:
            acts.append(Action(far, side.name, "move", _move(battle, "trickroom"), [far]))
    elif turn == 2:
        if inc:
            acts.append(Action(inc, side.name, "move", _move(battle, "partingshot"), [inc]))
        if far and tgt is not None:
            acts.append(Action(far, side.name, "move", _move(battle, "psychic"), [tgt]))
    else:
        return None
    return acts or None


def perish_trap_script(battle, side, opp_side, turn, variant=0):
    """Perish Trap: T1 Fake Out + Perish Song. T2 double Protect (the counter
    keeps ticking regardless -- Protect only avoids damage). T3 Mega Gengar
    switches out to freeze its own Perish count, and Incineroar's Parting
    Shot brings it straight back in (via the engine's self-switch mechanic,
    forced to pick Gengar specifically -- see battle._forced_switch_in) so
    Shadow Tag never actually leaves the field and the trap holds. Whichever
    of our two Pokemon are still trapped and still carrying the T1 Perish
    count faint at the end of T3. T4: both their actives (now whatever filled
    Incineroar's slot, plus Gengar) just Protect -- by this point their plan
    has already succeeded or failed. No further turns are scripted.

    The real defensive aim for US: KO Mega Gengar T1 if at all possible (kills
    Shadow Tag along with it, so nothing traps us in the first place -- Fake
    Out first, then a real attack, or a priority move). If that fails, the
    fallback is to escape Shadow Tag before T3: any Ghost-type of ours is
    immune to the trap outright, and a self-switch move (U-turn, Volt Switch,
    Parting Shot, ...) still works even while trapped -- Shadow Tag only
    blocks a manual switch command, not a switching move (see
    Battle.is_trapped).
    """
    acts = []
    inc = _find(side, "Incineroar")
    gen = _find(side, "Mega Gengar")
    tgt = _target(opp_side, variant)
    if turn == 1:
        if inc and tgt is not None:
            acts.append(Action(inc, side.name, "move", _move(battle, "fakeout"), [tgt]))
        if gen:
            acts.append(Action(gen, side.name, "move", _move(battle, "perishsong"), [gen]))
    elif turn == 2:
        for c in (inc, gen):
            if c is not None and not c.fainted:
                acts.append(Action(c, side.name, "protect", _move(battle, "protect"), [c]))
    elif turn == 3:
        if gen is not None and not gen.fainted:
            alive_bench = [b for b in side.bench if not b.fainted]
            filler = battle._best_replacement(alive_bench, opp_side.active) if alive_bench else None
            if filler is not None:
                acts.append(Action(gen, side.name, "switch", None, [filler]))
                if inc is not None and not inc.fainted:
                    battle._forced_switch_in = {**getattr(battle, "_forced_switch_in", {}),
                                                 id(inc): gen}
        if inc is not None and not inc.fainted:
            if tgt is not None:
                acts.append(Action(inc, side.name, "move", _move(battle, "partingshot"), [tgt]))
            else:
                acts.append(Action(inc, side.name, "protect", _move(battle, "protect"), [inc]))
    elif turn == 4:
        for c in side.active:
            if c is not None and not c.fainted:
                acts.append(Action(c, side.name, "protect", _move(battle, "protect"), [c]))
    else:
        return None
    return acts or None


SCRIPTS = {
    "King": king_script,
    "Hard Trick Room": hard_trick_room_script,
    "Perish Trap": perish_trap_script,
}


# How many distinct openings each scripted team has. Currently this is the
# choice of which of our two slots to Fake Out; King additionally varies which
# Pokemon it sets up with.
VARIANTS = {"King": 4, "Hard Trick Room": 2, "Perish Trap": 2}


def script_for(team_name, variant=0):
    """A script bound to one specific variant, ready to pass as `enemy_script`."""
    fn = SCRIPTS.get(team_name)
    if fn is None:
        return None

    def bound(battle, side, opp_side, turn):
        return fn(battle, side, opp_side, turn, variant=variant)
    return bound


def all_scripts(team_name):
    """Every opening variant that must actually be survived for this
    fixed-lead team: the rehearsed script (varied over VARIANTS targeting
    choices) plus the generic "one Protects, the other attacks" alternative
    (both choices of which slot protects) -- a real line a fixed-lead team
    could take instead of its rehearsed plan, not covered by the plain greedy
    2v2 fallback callers already add separately (script=None). Together with
    that fallback, this is the full set a composition needs to beat."""
    if team_name not in SCRIPTS:
        return []
    out = [(i, script_for(team_name, i)) for i in range(VARIANTS.get(team_name, 1))]
    out.append(("protect0", _protect_and_attack_script(0)))
    out.append(("protect1", _protect_and_attack_script(1)))
    return out
