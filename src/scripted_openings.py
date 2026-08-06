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
    """Hard TR: T1 Fake Out + Trick Room, T2 Parting Shot to bring in a sweeper."""
    acts = []
    inc = _find(side, "Incineroar")
    far = _find(side, "Farigiraf")
    live = [f for f in opp_side.active if f is not None and not f.fainted]
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
    """Perish Trap: T1 Fake Out + Perish Song, T2 double Protect, T3 stall again --
    Shadow Tag keeps you in while the counter runs out."""
    acts = []
    inc = _find(side, "Incineroar")
    gen = _find(side, "Mega Gengar")
    live = [f for f in opp_side.active if f is not None and not f.fainted]
    tgt = _target(opp_side, variant)
    if turn == 1:
        if inc and tgt is not None:
            acts.append(Action(inc, side.name, "move", _move(battle, "fakeout"), [tgt]))
        if gen:
            acts.append(Action(gen, side.name, "move", _move(battle, "perishsong"), [gen]))
    elif turn in (2, 3):
        for c in (inc, gen):
            if c is not None and not c.protected_last_turn:
                acts.append(Action(c, side.name, "protect", _move(battle, "protect"), [c]))
            elif c is not None and live:
                acts.append(Action(c, side.name, "move", _move(battle, "partingshot"), [c]))
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
    """Every opening variant for this team, as (variant_index, script)."""
    if team_name not in SCRIPTS:
        return []
    return [(i, script_for(team_name, i)) for i in range(VARIANTS.get(team_name, 1))]
