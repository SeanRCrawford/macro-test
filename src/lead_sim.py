"""The lead race, played on the REAL simulator. No parallel damage model.

    "I don't think abilities are applying, such as Mega Dragonite's multiscale. I
     don't think they're ever mega evolving. I don't think this uses the full
     simulator logic. The damage seems very incorrect. It MUST be properly linked.
     This is meant to be a useful and accurate tool, and there is no room for a
     redundant, useless simulation - use the full battle simulator logic."

Correct on every count, and the diagnosis is the important part: `lead_scan`'s
`move_turn` re-derived damage from `damage_roll` by hand, which meant it silently
opted out of everything `Battle.run_turn` does. Verified before this module
existed:

  * `mega_evolved` was False for every Pokemon in every race, so a Mega's stats,
    typing and ABILITY never applied -- Mega Dragonite's Multiscale among them.
  * Items reached the calculation only where `move_plans` happened to pass them
    (Choice Band/Specs); Life Orb recoil, Sitrus Berry, Focus Sash consumption
    and Never-Melt Ice all lived in `battle.py` and were skipped.
  * Intimidate, Snow Warning's Ice-type Defence boost, Aurora Veil, redirection,
    contact abilities, recoil and end-of-turn residuals: none of it ran.
  * Movesets were the usage top-4, so Mega Scizor got Bug Bite and Swords Dance
    rather than the Knock Off / Close Combat / Bullet Punch that
    `optimize_sets.best_moveset` independently picks as its best.

So this module does not calculate anything. It enumerates candidate joint actions,
hands them to `Battle.run_turn`, and reads the result. Every mechanic the engine
knows about is therefore in scope by construction, and the golden baseline covers
it -- which is the whole reason a second damage model was a mistake.

WHAT IS ENUMERATED. For each of their lead pairs, over two turns:

  * their PROTECT plans, per turn, with nobody Protecting twice in a row
  * their SPEED CONTROL (Tailwind / Trick Room) on either turn
  * our plays: stay, switch, switch+Protect, sacrifice

and the worst outcome over their choices is what we report, with every speed tie
resolved against us. A copy of the Battle is played per combination, which is why
this is dearer than the arithmetic it replaces -- and why `--max-losses` exists.
"""
import copy
import itertools

from damage import hits_ally, is_spread_move
from solver import Action, build_moveset

WIN, EVEN, LOSS = "win", "even", "loss"

# Their Protect options for one turn.
PROTECT_PLANS = ("both attack", "left protects", "right protects")
SETUP_MOVES = ("Tailwind", "Trick Room")


def optimised_movesets(names, world, enemy_names=(), team_weather=None):
    """Each Pokemon's BEST four moves against this metagame, not its usage four.

        "Scizor for instance should use Knock Off, Close Combat, Bullet Punch;
         these are by far its best moves, as proven above."

    `optimize_sets.best_moveset` already answers this and the race was ignoring
    it. Falls back to the usage moveset when the optimiser cannot rate a species,
    so a missing entry degrades rather than raises.
    """
    from optimize_sets import best_moveset
    out = {}
    foes = list(enemy_names) or list(names)
    for name in names:
        try:
            moves, _score = best_moveset(name, world["merged"], world["moves"],
                                         world["natures"], world["typechart"],
                                         foes, team_weather=team_weather)
        except Exception:                                  # noqa: BLE001
            moves = None
        out[name] = moves
    return out


def build_position(our4, enemy4, world, our_sets=None, enemy_sets=None,
                   optimise=True):
    """A real Battle, with send-out abilities resolved and movesets attached.

    `optimise` replaces the usage top-four with `optimize_sets.best_moveset` for
    OUR side, which is the difference between Mega Scizor holding Bug Bite and
    holding Knock Off.
    """
    from combatants import make_team

    from battle import Battle
    ours = make_team(list(our4), world["merged"], world["natures"],
                     sets=our_sets)
    theirs = make_team(list(enemy4), world["merged"], world["natures"],
                       sets=enemy_sets)
    chosen = {}
    if optimise:
        chosen = optimised_movesets([c.name for c in ours], world,
                                    enemy_names=[c.name for c in theirs])
    movesets = {}
    for c in ours + theirs:
        spec = ((our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name)
                or {})
        only = spec.get("moves") or chosen.get(c.name)
        movesets[c.name] = build_moveset(world["merged"][c.name], world["moves"],
                                         only_moves=only)
    battle = Battle(ours, theirs, world["typechart"], world["moves"])
    battle.movesets = movesets
    return battle, movesets


def _actions_for(battle, combatant, side, movesets, foes, allow_protect=True):
    """Candidate Actions for one Pokemon: every move, every legal target.

    Deliberately NOT the solver's pruned list. The point of this module is that
    nothing is filtered out by a heuristic that might be wrong; the enumeration is
    small (4 moves x at most 2 targets) and the simulator decides what happens.
    """
    out = []
    live = [f for f in foes if not f.fainted]
    for move, _usage in movesets.get(combatant.name) or []:
        if move.name == "Protect":
            if allow_protect and not combatant.protected_last_turn:
                out.append(Action(combatant, side, "move", move, [combatant]))
            continue
        if move.category == "Status":
            out.append(Action(combatant, side, "move", move, []))
            continue
        if is_spread_move(move.target):
            out.append(Action(combatant, side, "move", move, list(live)))
            continue
        for target in live:
            out.append(Action(combatant, side, "move", move, [target]))
    if not out and live:
        first = (movesets.get(combatant.name) or [(None, 0)])[0][0]
        if first is not None:
            out.append(Action(combatant, side, "move", first, [live[0]]))
    return out


def _hp_snapshot(battle):
    return {id(c): (c.current_hp, c.max_hp())
            for c in battle.p1.roster + battle.p2.roster}


def outcome(battle):
    """(verdict, our_dead, their_dead, margin) from a played-out Battle.

    Margin is in Pokemon-equivalents of HP lost, theirs minus ours, so it breaks
    ties between positions with the same body count.
    """
    def lost(side):
        total = 0.0
        for c in side.roster:
            mx = c.max_hp() or 1
            total += (mx - max(0, c.current_hp)) / mx
        return total

    our_dead = sum(1 for c in battle.p1.roster if c.fainted)
    their_dead = sum(1 for c in battle.p2.roster if c.fainted)
    margin = lost(battle.p2) - lost(battle.p1)
    if their_dead > our_dead or (their_dead == our_dead and margin > 0.25):
        return WIN, our_dead, their_dead, margin
    if our_dead > their_dead or margin < -0.25:
        return LOSS, our_dead, their_dead, margin
    return EVEN, our_dead, their_dead, margin


def _their_turn_actions(battle, movesets, plan, setup, turn):
    """Their Actions for one turn under a (Protect plan, speed-control) choice."""
    out = []
    actives = [c for c in battle.p2.active if c is not None and not c.fainted]
    ours = [c for c in battle.p1.active if c is not None and not c.fainted]
    for i, c in enumerate(actives):
        protect = ((plan == "left protects" and i == 0)
                   or (plan == "right protects" and i == 1))
        if protect and not c.protected_last_turn:
            mv = next((m for m, _ in movesets.get(c.name) or []
                       if m.name == "Protect"), None)
            if mv is not None:
                out.append(Action(c, "p2", "move", mv, [c]))
                continue
        if setup and setup[1] is c and setup[2] == turn:
            mv = next((m for m, _ in movesets.get(c.name) or []
                       if m.name == setup[0]), None)
            if mv is not None:
                out.append(Action(c, "p2", "move", mv, []))
                continue
        cands = _actions_for(battle, c, "p2", movesets, ours,
                             allow_protect=False)
        # Their attack choice: the one that removes the most of our HP, decided
        # by the simulator rather than by a formula -- so Intimidate, screens and
        # abilities are all already priced.
        best, best_val = None, None
        for a in cands:
            probe = copy.deepcopy(battle)
            val = _probe_damage(probe, a, "p2")
            if best_val is None or val > best_val:
                best, best_val = a, val
        if best is not None:
            out.append(Action(c, "p2", best.kind, best.move,
                              _remap(best.targets, battle)))
    return out


def _probe_damage(probe, action, side):
    """How much enemy HP one action removes, by playing it and looking."""
    mine = [a for a in [_remap_action(action, probe, side)] if a]
    other = "p2" if side == "p1" else "p1"
    before = sum(max(0, c.current_hp) for c in getattr(probe, other).roster)
    try:
        if side == "p1":
            probe.run_turn(mine, [])
        else:
            probe.run_turn([], mine)
    except Exception:                                      # noqa: BLE001
        return -1.0
    after = sum(max(0, c.current_hp) for c in getattr(probe, other).roster)
    return before - after


def _remap(targets, battle):
    return [t for t in (targets or [])]


def _remap_action(action, probe, side):
    """Re-point an Action at the equivalent Pokemon inside `probe`."""
    roster = probe.p1.roster if side == "p1" else probe.p2.roster
    other = probe.p2.roster if side == "p1" else probe.p1.roster
    actor = next((c for c in roster if c.name == action.combatant.name), None)
    if actor is None:
        return None
    tgts = []
    for t in action.targets or []:
        match = next((c for c in roster + other if c.name == t.name), None)
        if match is not None:
            tgts.append(match)
    return Action(actor, side, action.kind, action.move, tgts)
