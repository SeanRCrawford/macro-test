"""The lead race, played on the REAL simulator. One damage model, not two.

    "finish lead_sim so the race is Battle.run_turn end to end. Avoid having two
     damage models. For instance, this can't be correct. Please, it is imperative
     that you use the full simulator."

The accusation was right on every count. `lead_scan.move_turn` re-derived damage
from `damage_roll` by hand, which meant it silently opted out of everything
`Battle.run_turn` does. Verified before this module replaced it:

  * `mega_evolved` was False for every Pokemon in every race, so a Mega's stats,
    typing and ABILITY never applied -- Mega Dragonite's Multiscale among them.
  * Items reached the calculation only where `move_plans` happened to pass them
    (Choice Band/Specs). Life Orb recoil, Sitrus Berry, Focus Sash consumption and
    Never-Melt Ice all live in `battle.py` and were skipped.
  * Intimidate, Snow Warning's Ice-type Defence boost, Aurora Veil, redirection,
    Fake Out, contact abilities, recoil and end-of-turn residuals: none of it ran.
  * Movesets were the usage top-four, so Mega Scizor got Bug Bite and Swords Dance
    rather than the Knock Off / Close Combat / Bullet Punch that
    `optimize_sets.best_moveset` independently picks as its best.

So nothing here calculates damage. It enumerates candidate joint actions, hands
them to `Battle.run_turn`, and reads the result off the Combatants afterwards.
Every mechanic the engine knows about is in scope by construction, and the golden
baseline covers it -- which is exactly why a second damage model was a mistake.

THE COST, STATED PLAINLY. A real turn is orders of magnitude dearer than an
addition, so the breadth is a parameter rather than a constant: `strategies`
controls how much of their strategy space is enumerated. The same function serves
the cheap sweep and the detailed report, at different breadths -- one code path,
one damage model, one set of numbers.
"""
import copy
import itertools

from damage import is_spread_move
from solver import Action, build_moveset, FIRST_TURN_ONLY_MOVES

WIN, EVEN, LOSS = "win", "even", "loss"

# Their Protect options for a single turn. Enumerated per turn, with nobody
# Protecting twice in a row, because the board is unchanged after a stall and the
# same pin re-applies.
PROTECT_PLANS = ("both attack", "left protects", "right protects")

# Moves that change the SPEED ORDER rather than the HP totals, which a damage
# comparison can never see. Whimsicott's Tailwind is what lets Mega Floette stop
# tying Garchomp and simply outspeed it.
SETUP_MOVES = ("Tailwind", "Trick Room", "Fake Out")

# Abilities that make a Pokemon immune to Fake Out's flinch and to Intimidate.
#
#     "A pokemon with the move inner focus cannot be faked out or intimidated so
#      could be useful, such as life orb Dragonite."
#
# Listed so the report can NAME the reason a lead is safe from a script, rather
# than only showing that it happens to be.
FLINCH_PROOF = ("Inner Focus", "Own Tempo", "Oblivious", "Scrappy")
INTIMIDATE_PROOF = ("Inner Focus", "Own Tempo", "Oblivious", "Scrappy",
                    "Hyper Cutter", "Clear Body", "White Smoke", "Full Metal Body")


def optimised_movesets(names, world, enemy_names=(), team_weather=None):
    """Each Pokemon's BEST four moves against this metagame, not its usage four.

        "Scizor for instance should use Knock Off, Close Combat, Bullet Punch;
         these are by far its best moves, as proven above."

    `optimize_sets.best_moveset` already answers this and the race was ignoring
    it. Falls back to the usage moveset when the optimiser cannot rate a species.
    """
    from optimize_sets import best_moveset
    foes = list(enemy_names) or list(names)
    out = {}
    for name in names:
        try:
            moves, _score = best_moveset(name, world["merged"], world["moves"],
                                         world["natures"], world["typechart"],
                                         foes, team_weather=team_weather)
        except Exception:                                  # noqa: BLE001
            moves = None
        out[name] = moves
    return out


def optimised_items(names, world, enemy_names=()):
    """Each Pokemon's best legal item, from what it is recorded using.

        "The movesets and items needs to be genuinely optimised. There are cases
         where a focus sash would turn an opening into a guaranteed win, but this
         is not in the model."

    `optimize_sets.best_item` rates the recorded items; Focus Sash is added as a
    candidate for everything, since surviving a would-be OHKO is a matchup effect
    that no usage table prices. Regulation MB bans Assault Vest and two of the
    three Choice items, so `legal_items` is filtered against them here rather
    than trusted.
    """
    from optimize_sets import best_item, legal_items
    foes = list(enemy_names) or list(names)
    out = {}
    for name in names:
        item = None
        try:
            # THREE values, not two: (item, moves, score). My first call unpacked
            # two, raised ValueError, and a bare `except` hid it -- so every item
            # came back None and "genuinely optimised items" was silently a no-op.
            # The moveset it returns is the one re-optimised UNDER that item, which
            # is what makes the pairing coherent.
            item, moves, _score = best_item(name, world["merged"], world["moves"],
                                            world["natures"], world["typechart"],
                                            foes)
        except Exception:                                  # noqa: BLE001
            item, moves = None, None
        if item in BANNED_ITEMS:
            item = None
        if item is None:
            # Fall back to the best LEGAL recorded item.
            legal = [i for i in (legal_items(name, world["merged"]) or [])
                     if i not in BANNED_ITEMS]
            item = legal[0] if legal else None
            moves = None
        out[name] = (item, moves)
    return out


# Not legal in Regulation MB. Filtered rather than merely omitted, because they
# appear in the usage tables and so would otherwise come back in through
# `legal_items`. An item search that proposes an illegal item is worse than none.
#
# Choice Scarf is NOT on this list -- it is a legal item in this format, and it
# was wrongly banned here for a stretch of this branch's history (a mistake
# made while implementing the other two Choice items' ban, not something the
# user ever asked for; the original request named exactly Assault Vest, Choice
# Band and Choice Specs). Confirmed and corrected directly: *"Choice Scarf
# (which is a valid item, other choice items are not)"*.
BANNED_ITEMS = frozenset({"Assault Vest", "Choice Band", "Choice Specs"})

# Regulation M-C additions to the searchable pool: Rillaboom, Baxcalibur,
# and the three Mega-Z formes (Mega Absol Z, Mega Garchomp Z, Mega Lucario
# Z -- resolved via the existing "Mega X Y" 3-token branch in
# `species_data.resolve_species`, now also matching a trailing "Z" the same
# way it already matches "X"/"Y"). No banned item above needed to change for
# them -- their recorded sets (`data/mbsmogon.xlsx`) don't carry any of
# Assault Vest/Choice Band/Choice Specs. There is no other regulation-tag
# schema in this codebase (see `lead_scan.py`'s own "Regulation MB" item-
# legality comments for the only other enforcement point) -- this is a plain
# documentation note, not something any code reads.


def build_position(our4, enemy4, world, our_sets=None, enemy_sets=None,
                   optimise=True, enemy_mega=None):
    """A real `Battle`, with send-out abilities resolved and movesets attached.

    This is the ONLY place a position is constructed, so weather, Intimidate on
    send-out and the Mega slot are all resolved by the engine exactly as they are
    in a game.

    `enemy_mega` pins WHICH of the enemy roster's Mega-capable names actually
    evolves, independent of `enemy4`'s order -- needed because the lead race
    tests the same six-Pokemon roster under many different lead-pair orderings,
    and the Mega choice must not silently follow whichever one happens to sort
    first (`make_team`'s default).
    """
    from combatants import make_team

    from battle import Battle
    sets = dict(our_sets or {})
    if optimise:
        moves = optimised_movesets(list(our4), world, enemy_names=list(enemy4))
        items = optimised_items(list(our4), world, enemy_names=list(enemy4))
        for name in our4:
            spec = dict(sets.get(name) or {})
            item, item_moves = items.get(name) or (None, None)
            # The moveset re-optimised UNDER the chosen item wins, since an item
            # and a moveset are chosen together or not at all.
            spec.setdefault("moves", item_moves or moves.get(name))
            if item:
                spec.setdefault("item", item)
            sets[name] = {k: v for k, v in spec.items() if v}

    ours = make_team(list(our4), world["merged"], world["natures"], sets=sets)
    theirs = make_team(list(enemy4), world["merged"], world["natures"],
                       sets=enemy_sets, mega_transforms=enemy_mega)
    movesets = {}
    for c in ours + theirs:
        spec = (sets.get(c.name) or (enemy_sets or {}).get(c.name) or {})
        movesets[c.name] = build_moveset(world["merged"][c.name], world["moves"],
                                         only_moves=spec.get("moves"))
    battle = Battle(ours, theirs, world["typechart"], world["moves"])
    battle.movesets = movesets
    return battle, movesets, sets


def _same(combatant, battle, side):
    """The equivalent Combatant inside `battle`, matched by name."""
    roster = (battle.p1 if side == "p1" else battle.p2).roster
    return next((c for c in roster if c.name == combatant.name), None)


def candidate_joints(battle, side, movesets, allow_protect=True,
                     switch_to=None):
    """Every joint action this side could submit this turn.

    Deliberately NOT the solver's pruned list: the point of this module is that
    nothing is removed by a heuristic that might be wrong. Small by construction
    -- four moves times at most two targets, per slot.

    ONE exception, and it is not a heuristic: Fake Out / First Impression
    (`FIRST_TURN_ONLY_MOVES`) are dropped once `c.active_turn_count > 0`, same
    rule `solver.py`'s real action-generator already enforces. This is a hard
    legality rule, not a judgement call -- without it a Sneasler that is still
    on the field two turns in remains free to "use" Fake Out again, which
    cannot happen in a real game, and a bring that only wins because of it is
    not actually robust to the enemy's real options.
    """
    me = battle.p1 if side == "p1" else battle.p2
    foe = battle.p2 if side == "p1" else battle.p1
    actives = [c for c in me.active if c is not None and not c.fainted]
    live_foes = [c for c in foe.active if c is not None and not c.fainted]
    per_slot = []
    for c in actives:
        opts = []
        if switch_to is not None and c.name == switch_to[0]:
            bench = next((x for x in me.roster
                          if x.name == switch_to[1] and not x.fainted), None)
            if bench is not None:
                per_slot.append([Action(c, side, "switch", None, [bench])])
                continue
        moveset = movesets.get(c.name) or []
        if c.active_turn_count > 0:
            moveset = [m for m in moveset if m[0].name not in FIRST_TURN_ONLY_MOVES]
        for move, _usage in moveset:
            if move.name == "Protect":
                if allow_protect and not c.protected_last_turn:
                    opts.append(Action(c, side, "move", move, [c]))
                continue
            if move.category == "Status":
                opts.append(Action(c, side, "move", move, []))
            elif is_spread_move(move.target):
                opts.append(Action(c, side, "move", move, list(live_foes)))
            else:
                for t in live_foes:
                    opts.append(Action(c, side, "move", move, [t]))
        if not opts:
            first = (moveset or [(None, 0)])[0][0]
            if first is not None and live_foes:
                opts.append(Action(c, side, "move", first, [live_foes[0]]))
        per_slot.append(opts or [None])
    return [[a for a in combo if a is not None]
            for combo in itertools.product(*per_slot)] or [[]]


def outcome(battle):
    """(verdict, our_dead, their_dead, margin), read off a played-out Battle.

    Margin is HP lost in Pokemon-equivalents, theirs minus ours, so it separates
    positions with the same body count. Read from the Combatants, not tracked --
    the simulator is the source of truth.
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


def _score_for(battle, side):
    """How good this position is for `side`, in the same units as `outcome`."""
    _v, od, td, margin = outcome(battle)
    return (td - od) * 10.0 + margin if side == "p1" else (od - td) * 10.0 - margin


def _their_joint(battle, movesets, plan, forced=None):
    """Their submission for one turn under a Protect/setup plan.

    Their attacking choice is made by PLAYING each candidate and keeping the one
    that leaves us worst off -- so Intimidate, screens, abilities and items are
    all already priced, because the simulator applied them.
    """
    actives = [c for c in battle.p2.active if c is not None and not c.fainted]
    fixed = {}
    for i, c in enumerate(actives):
        want = None
        if plan == "left protects" and i == 0:
            want = "Protect"
        elif plan == "right protects" and i == 1:
            want = "Protect"
        elif forced and forced[0] is not None and c.name == forced[1]:
            want = forced[0]
        if want == "Protect" and c.protected_last_turn:
            want = None
        if want:
            mv = next((m for m, _ in movesets.get(c.name) or []
                       if m.name == want), None)
            if mv is not None:
                fixed[c.name] = Action(c, "p2", "move", mv,
                                       [c] if want == "Protect" else [])
    best, best_val = None, None
    for joint in candidate_joints(battle, "p2", movesets):
        submission = [fixed.get(a.combatant.name, a) for a in joint]
        for name, act in fixed.items():
            if not any(a.combatant.name == name for a in submission):
                submission.append(act)
        probe = copy.deepcopy(battle)
        mapped = _map_joint(submission, probe, "p2")
        try:
            probe.run_turn([], mapped)
        except Exception:                                  # noqa: BLE001
            continue
        val = _score_for(probe, "p2")
        if best_val is None or val > best_val:
            best, best_val = submission, val
    return best or []


def _map_joint(joint, battle, side):
    out = []
    for a in joint:
        actor = _same(a.combatant, battle, side)
        if actor is None:
            continue
        tgts = []
        for t in a.targets or []:
            m = _same(t, battle, "p1") or _same(t, battle, "p2")
            if m is not None:
                tgts.append(m)
        out.append(Action(actor, side, a.kind, a.move, tgts))
    return out


def play(battle, movesets, turns=2, their_plans=None, our_switch=None,
         want_log=True):
    """Play `turns` turns on a COPY of `battle`. Returns (verdict, ..., log).

    Our submission each turn is chosen by playing every candidate joint action and
    keeping the best resulting position -- the simulator decides, not a formula.
    `our_switch` is (leaving, arriving) for a switch play, applied on turn 1 only.
    """
    b = copy.deepcopy(battle)
    log = [] if want_log else None
    plans = list(their_plans or [("both attack", None)])
    for turn in range(max(1, turns)):
        plan, forced = plans[min(turn, len(plans) - 1)]
        if log is not None:
            bits = [f"  Turn {turn + 1}", f"their plan: {plan}"]
            if forced and forced[0]:
                bits.append(f"{forced[1]} uses {forced[0]}")
            log.append("  ".join(bits))
        theirs = _their_joint(b, movesets, plan, forced=forced)
        best, best_val, best_state = None, None, None
        for joint in candidate_joints(b, "p1", movesets,
                                      switch_to=our_switch if turn == 0 else None):
            probe = copy.deepcopy(b)
            try:
                probe.run_turn(_map_joint(joint, probe, "p1"),
                               _map_joint(theirs, probe, "p2"))
            except Exception:                              # noqa: BLE001
                continue
            val = _score_for(probe, "p1")
            if best_val is None or val > best_val:
                best, best_val, best_state = joint, val, probe
        if best_state is None:
            break
        if log is not None:
            start = len(b.log.lines) if hasattr(b, "log") else 0
            for line in best_state.log.lines[start:]:
                log.append(f"    {line}")
        b = best_state
    verdict, od, td, margin = outcome(b)
    return verdict, od, td, margin, log or [], b


def their_strategies(battle, movesets, turns=2, breadth="full"):
    """Their plan per turn: Protect splits, and the setup moves they really have.

    `breadth` trades cost for coverage. "cheap" is the both-attack column plus one
    Protect split; "full" is every legal combination with nobody Protecting twice
    in a row, plus Tailwind / Trick Room on either turn, and Fake Out on turn 0
    ONLY -- and only if `c` was actually just sent out (`active_turn_count == 0`
    at the start of this window). Scripting Fake Out on a later turn, or on a
    Pokemon that was already on the field before this window began, would be
    scripting a move that fails to even execute in a real game.
    """
    actives = [c for c in battle.p2.active if c is not None and not c.fainted]
    setups = [None]
    for c in actives:
        for move, _u in movesets.get(c.name) or []:
            if move.name in SETUP_MOVES:
                if move.name == "Fake Out":
                    if c.active_turn_count > 0:
                        continue
                    turns_to_try = (0,)
                else:
                    turns_to_try = range(max(1, turns))
                for turn in turns_to_try:
                    setups.append((move.name, c.name, turn))
    plans = PROTECT_PLANS if breadth == "full" else ("both attack",
                                                     "left protects")
    out = []
    for a, bb in itertools.product(plans, plans):
        if a != "both attack" and bb != "both attack" and a == bb:
            continue                    # nobody Protects twice in a row
        for setup in setups:
            seq = []
            for turn in range(max(1, turns)):
                plan = a if turn == 0 else bb
                forced = ((setup[0], setup[1]) if setup and setup[2] == turn
                          else None)
                seq.append((plan, forced))
            out.append(seq)
    return out


def race(battle, movesets, turns=2, our_switch=None, breadth="full",
         want_log=True):
    """Their best strategy against ours. Returns (verdict, od, td, margin, desc, log).

    Every speed tie resolves against us because `Battle` breaks them by its own
    rule and we take THEIR best outcome over the whole enumeration -- so a plan
    that only wins the flip is never credited.
    """
    worst = None
    for seq in their_strategies(battle, movesets, turns=turns, breadth=breadth):
        verdict, od, td, margin, log, _end = play(
            battle, movesets, turns=turns, their_plans=seq,
            our_switch=our_switch, want_log=want_log)
        if worst is None or margin < worst[3]:
            desc = " then ".join(
                p + (f" (+{f[1]} {f[0]})" if f and f[0] else "")
                for p, f in seq)
            worst = (verdict, od, td, margin, desc, log)
    return worst
