"""
Turn-solver policy: given a battle state, finds the best joint action for
"our" side.

SCOPING DECISION (flagging explicitly): true doubles turn selection is a
simultaneous-move game -- both sides commit actions blind, then they
resolve in speed order. Solving that exactly is a matrix-game / Nash
equilibrium problem, which is a much bigger undertaking than fits here.

Instead this solver does depth-limited EXPECTIMAX against a GREEDY
opponent model: the opponent is assumed to have its active Pokemon each
use whichever of its known top-usage moves does the most immediate damage
(or sets up its known signature tech move on turn 1, e.g. Trick Room /
Tailwind, since that's the standard "obvious" doubles opening for teams
built around it). This is not a game-theoretic guarantee against a
perfect opponent, but it gives concrete, checkable lines against how these
archetypes actually pilot in practice -- which is what "know exactly what
moves to do" needs in practice. The opponent model is swappable/extendable
later (e.g. add a second, more defensive opponent profile and take the
worst case over both).

Our side searches over a pruned action set: each active mon's top-K
known moves (by usage %) + Protect, each damaging move evaluated against
whichever legal target maximizes immediate damage, combined across our
two active mons, then extended `depth` turns using the same opponent
model, scored by a simple HP-differential heuristic at the leaf.
"""
import copy
import random
from contextlib import contextmanager
import itertools

from damage import (Combatant, MoveInfo, is_spread_move, effective_stat, damage_roll,
                    defensive_stat, move_from_showdown, CHARGE_WEATHER_SKIP)
from engine import FieldState, Action, on_switch_in, effective_speed
from battle import Battle, Side, PROTECT_MOVES, CHOICE_ITEMS

TOP_K_MOVES = 4   # real sets run 4 moves; 3 systematically under-armed the AI
FIRST_TURN_ONLY_MOVES = {"Fake Out", "First Impression"}


def build_moveset(pokemon_record: dict, moves_db: dict, top_k: int = TOP_K_MOVES,
                   only_moves: list | None = None):
    """Turn mbsmogon usage-% move list into a list of (MoveInfo, usage_pct),
    skipping the 'Other' bucket and non-resolvable move names."""
    out = []
    for mv_name, pct in pokemon_record["moves_usage"]:
        if mv_name == "Other":
            continue
        key = mv_name.lower().replace(" ", "").replace("-", "").replace("'", "")
        if key not in moves_db:
            continue
        out.append((move_from_showdown(moves_db[key]), pct))
    if only_moves:
        # Explicit set supplied (e.g. an optimised team sheet) -- use exactly these,
        # preserving the given order, ignoring usage ranking and top_k.
        wanted = {m.lower() for m in only_moves}
        chosen = [(mi, pct) for mi, pct in out if mi.name.lower() in wanted]
        if chosen:
            order = {m.lower(): i for i, m in enumerate(only_moves)}
            chosen.sort(key=lambda x: order.get(x[0].name.lower(), 99))
            return chosen
    out.sort(key=lambda x: -x[1])
    return out[:top_k]


def build_wide_moveset(pokemon_record: dict, moves_db: dict,
                        pool: int = 6, base_k: int = TOP_K_MOVES):
    """The moves a Pokemon PLAUSIBLY has, not just the usage-standard four.

    The solver plans against `top_k=TOP_K_MOVES` by usage, and the simulated
    opponent then plays exactly those four, so the assumption is self-fulfilling
    and nothing ever contradicts it. Measured cost of that being wrong: **10
    points of win rate** even for the equilibrium solver, 95% CI [+3, +17] over
    720 games (tools/measure_set_uncertainty.py).

    A second measurement says the same thing from the other side: a play chosen
    against the narrow set is exploitable by moves outside it, which is why
    `nash-maximin` scores worse than greedy on exploitability -- it is maximin
    with respect to an opponent action space that is too small
    (tools/measure_robustness.py).

    Widening the pool the OPPONENT is assumed to be able to play is the cheap
    part of section 5's belief state: no inference, no sampling, no ISMCTS --
    just stop pretending they cannot have their fifth and sixth most common
    moves. Usage percentages ride along on each entry, so a later refinement can
    weight the columns by likelihood rather than treating them as equally
    available.
    """
    return build_moveset(pokemon_record, moves_db, top_k=max(pool, base_k))


def build_wide_movesets(names, merged: dict, moves_db: dict, pool: int = 6):
    """`build_wide_moveset` over a list of species names."""
    return {n: build_wide_moveset(merged[n], moves_db, pool=pool)
            for n in names if n in merged}


def quick_damage_estimate(attacker: Combatant, target: Combatant, move: MoveInfo,
                           typechart: dict, field: FieldState, num_hit: int = 1,
                           battle=None) -> float:
    if move.power == 0:
        return 0.0
    power = move.power
    # Last Respects' true power depends on the attacker's fainted-ally count, which
    # needs the battle/side context this function doesn't otherwise take -- see the
    # matching computation in battle.py's _resolve_move. Only estimated when a
    # caller passes `battle` (move-choice comparisons that matter, e.g.
    # action_value); target-selection pruning doesn't need it since a uniform power
    # scale doesn't change which target looks best.
    if move.name == "Last Respects" and battle is not None:
        side = battle.side_of(attacker)
        fainted_allies = sum(1 for c in side.roster if c is not attacker and c.fainted)
        power = min(200, 50 + 50 * fainted_allies)
    atk_key = "atk" if move.category == "Physical" else "spa"
    def_key = "def" if move.category == "Physical" else "spd"
    atk_stat = effective_stat(attacker.stats[atk_key], attacker.stages[atk_key])
    if attacker.item == "Choice Band" and atk_key == "atk":
        atk_stat *= 1.5
    if attacker.item == "Choice Specs" and atk_key == "spa":
        atk_stat *= 1.5
    def_stat = defensive_stat(target, def_key, move)
    _, _, avg, _ = damage_roll(50, power, atk_stat, def_stat, attacker, target, move,
                                typechart, weather=field.weather, num_targets_hit=num_hit)
    return avg


def candidate_actions(combatant: Combatant, side_key: str, allies: list, foes: list,
                       moveset, typechart: dict, field: FieldState, turn_num: int,
                       bench: list | None = None):
    """Returns a pruned list of Action objects worth considering for this mon."""
    actions = []
    live_foes = [f for f in foes if not f.fainted]
    # NOTE: do NOT early-return here even if live_foes is empty. That happens
    # legitimately when both opposing actives just fainted this turn and are
    # about to be replaced -- this mon still needs *some* legal action (status
    # moves, Protect) or the whole turn's joint-action search silently breaks
    # (this was a real bug: it looked like a "timeout" but was actually the
    # solver finding zero valid actions and giving up early).

    # Respect an existing Choice item lock: only the already-locked move is legal.
    if combatant.item in CHOICE_ITEMS and combatant.choice_locked_move:
        locked = [m for m in moveset if m[0].name == combatant.choice_locked_move]
        if locked:
            moveset = locked

    # Fake Out / First Impression only work the exact turn the mon was sent out.
    if combatant.active_turn_count > 0:
        moveset = [m for m in moveset if m[0].name not in FIRST_TURN_ONLY_MOVES]

    for move, pct in moveset:
        # Two-turn (charge) moves (Solar Beam/Blade, Electro Shot) spend this whole
        # turn dealing NO damage unless the right weather is already up to skip the
        # charge -- quick_damage_estimate has no notion of that, so without this
        # filter the move looks like a guaranteed huge hit RIGHT NOW and gets chosen
        # as if it were one-turn. Once already charging, the real turn resolution
        # (battle.py) force-overrides whatever action is submitted anyway, so this
        # filter only affects the voluntary choice to START charging.
        if move.flags and move.flags.get("charge"):
            skip_weather = CHARGE_WEATHER_SKIP.get(move.name)
            weather_ready = bool(skip_weather) and field.weather == skip_weather
            already_charging = combatant.volatile.get("charging_move") == move.name
            if not weather_ready and not already_charging:
                continue
        if move.name in PROTECT_MOVES:
            # Don't offer Protect if it would auto-fail (protected last turn) -- otherwise
            # the search wastes a branch on a guaranteed no-op and can look like it's
            # "choosing" to stall.
            if not combatant.protected_last_turn:
                actions.append(Action(combatant, side_key, "protect", move, [combatant]))
            continue
        if move.category == "Status":
            # Status/setup moves (Trick Room, Tailwind, etc.) -- always offer as a candidate,
            # targeting handled generically (self/allySide/field moves don't need foe targets).
            if move.target in ("self", "allySide", "all"):
                actions.append(Action(combatant, side_key, "move", move, [combatant]))
            continue
        if not live_foes:
            continue  # damaging move but nothing legal to target it at right now
        if is_spread_move(move.target):
            from damage import spread_targets
            tgts = spread_targets(move.target, live_foes, allies, combatant)
            actions.append(Action(combatant, side_key, "move", move, tgts))
        else:
            # single-target: only branch on the target that does more damage (prune to 1)
            best_target = max(
                live_foes,
                key=lambda f: quick_damage_estimate(combatant, f, move, typechart, field)
            )
            actions.append(Action(combatant, side_key, "move", move, [best_target]))

    if not actions:
        # No damaging move had a legal target (e.g. both opposing actives just fainted
        # this turn) and nothing else applied -- fall back to Protect so this mon still
        # has SOME legal action rather than leaving the whole turn's search with nothing.
        actions.append(Action(combatant, side_key, "protect",
                               MoveInfo("Protect", 0, "Normal", "Status", "self", priority=4),
                               [combatant]))

    # Switch candidates: bringing in each available bench mon instead of acting this turn.
    # (Bench Pokemon still take Intimidate/hazard-style switch-in effects via on_switch_in.)
    if bench:
        for b in bench:
            if not b.fainted:
                actions.append(Action(combatant, side_key, "switch", None, [b]))

    return actions


def greedy_opponent_joint_action(battle: Battle, side: Side, opp_side: Side, movesets: dict,
                                  turn_num: int):
    joint = []
    used_incoming = set()
    for c in side.active:
        if c.fainted:
            if side.bench:
                remaining = [b for b in side.bench if id(b) not in used_incoming]
                if remaining:
                    incoming = max(remaining, key=lambda b: b.current_hp_frac)
                    used_incoming.add(id(incoming))
                    joint.append(Action(c, side.name, "switch", None, [incoming]))
            continue
        cands = candidate_actions(c, side.name, side.active, opp_side.active,
                                   movesets[c.name], battle.typechart, battle.field, turn_num)
        # Greedy: prefer a status/setup move on turn 1 only if it's their known signature
        # (Trick Room / Tailwind), else take the highest total estimated damage option.
        def action_value(a: Action):
            if a.kind == "protect":
                return -1  # opponent modeled as not bothering to Protect (simplification)
            if a.move.category == "Status":
                # Speed control (Tailwind / Trick Room) is a genuine board-state swing --
                # a Whimsicott or Talonflame will take it whenever it isn't already up,
                # not just on turn 1, and Prankster/Gale Wings makes it better still.
                if a.move.name in ("Tailwind", "Trick Room"):
                    already = (battle.field.trick_room if a.move.name == "Trick Room"
                               else (battle.field.tailwind_p1 if a.side == "p1"
                                     else battle.field.tailwind_p2) > 0)
                    if already:
                        return -20
                    return 90 if c.ability in ("Prankster", "Gale Wings") else 75
                return 5 if turn_num == 1 else -5
            # Damage must be NORMALISED to the same 0-100 scale the status values use.
            # Previously this returned raw HP damage, so any attack (~150) always beat
            # any status move (<=90) -- which meant Prankster Tailwind, Trick Room,
            # redirection and Protect were effectively never chosen. Value = percentage
            # of the target's remaining HP removed, plus a bonus for actually securing
            # the KO.
            total = 0.0
            own_side = battle.side_of(c)
            total_dealt = 0.0
            for t in a.targets:
                dmg = quick_damage_estimate(c, t, a.move, battle.typechart, battle.field,
                                             num_hit=len(a.targets) if is_spread_move(a.move.target) else 1,
                                             battle=battle)
                pct = 100.0 * min(dmg, t.current_hp) / t.max_hp() if t.max_hp() else 0.0
                # An allAdjacent move (Earthquake, Surf, Discharge) also hits our own
                # partner -- that damage counts AGAINST the move, not for it.
                if battle.side_of(t) is own_side:
                    total -= pct * 1.2
                    if dmg >= t.current_hp:
                        total -= 50.0
                else:
                    total += pct
                    total_dealt += min(dmg, t.current_hp)
                    if dmg >= t.current_hp:
                        total += 40.0
                        # A KO secured with priority cannot be pre-empted, so it is
                        # strictly better than the same KO in the normal bracket.
                        if a.move.priority > 0:
                            total += 15.0 * a.move.priority
            # Recoil moves (Flare Blitz, Head Smash, Light of Ruin, ...) cost the user
            # HP for the same damage dealt -- a non-recoil move doing the same job
            # (e.g. Light of Ruin vs Moonblast on an equally-lethal hit) should win the
            # tie. Rock Head / Magic Guard negate real recoil, so they're exempt here too.
            if a.move.recoil and c.ability not in ("Rock Head", "Magic Guard") and c.max_hp():
                num, den = a.move.recoil
                recoil_dmg = total_dealt * num / den
                total -= 100.0 * recoil_dmg / c.max_hp()
            # Tiny tie-break, far below any real damage/KO difference: when two moves
            # are otherwise equally good (e.g. two ways to secure the same KO), prefer
            # the more reliable one. No accuracy roll is modeled in battle resolution
            # (moves always hit here), so this only ever matters as a tie-break, never
            # as a real expected-value discount.
            acc = 100.0 if a.move.accuracy is True else a.move.accuracy
            total += acc * 0.001
            return total
        best = max(cands, key=action_value) if cands else None
        if best:
            joint.append(best)
    return joint


KO_WEIGHT = 180.0  # see heuristic_eval: a secured KO must dominate the HP-differential


# ---------------------------------------------------------------------------
# Functional value (design doc 4d, "1 HP is infinitely more than 0 HP")
# ---------------------------------------------------------------------------
# Wolfe Glick's article is written directly against a linear HP term: "All of
# your Pokemon will function in EXACTLY the same way no matter how much health
# they have left -- a Pokemon at low HP won't do less damage than if it was
# fully healthy."
#
# Worth being precise about what this evaluation already does, because the
# obvious reading ("add a step at zero") is a change it has largely made
# already. Per ALIVE Pokemon the two terms contribute:
#
#     KO term   _ko_threat_value in [0.35, 1.35] * KO_WEIGHT  ->   63..243 points
#     HP term   current_hp_frac                  * 100        ->     0..100 points
#
# so dying already costs far more than being chipped to 1 HP. The real open
# question is not whether there is a step but whether the BALANCE between the
# two is right -- and that is a single number, which can be swept and measured
# rather than argued about.
#
# FUNCTIONAL_FLOOR is that number: the share of a Pokemon's HP-term value it
# keeps merely by being alive.
#
#     0.0  purely linear in HP            (the behaviour before Phase A4)
#     0.5  half of the HP term is "alive", half is remaining bulk
#     1.0  HP is irrelevant; only the alive count matters
#
# Chip damage must keep costing something -- it moves a Pokemon toward dying,
# and a pure step would make it free -- so the useful range is interior.
FUNCTIONAL_FLOOR = 0.0


def _functional_hp(c: Combatant) -> float:
    """HP-term contribution of one living Pokemon.

    Kept antisymmetric-safe: identical for both rosters, and a hidden Pokemon
    (necessarily at full HP) evaluates to 1.0 at any floor, so revealing one
    still cannot manufacture value. See the reveal-incentive note on
    leaf_value.
    """
    if c.fainted:
        return 0.0
    return FUNCTIONAL_FLOOR + (1.0 - FUNCTIONAL_FLOOR) * c.current_hp_frac
                    # noise a forced enemy replacement introduces


def _ko_threat_value(c: Combatant) -> float:
    """How much removing this Pokemon from play is worth, KO-credit-wise --
    scaled by its own offensive potential. A KO on a low-output support/wall
    piece ('does little damage against your pair, achieves nothing') is
    worth noticeably less than a KO on a genuine attacking threat -- it
    removes less danger, so it's less worth handing the opponent a free,
    unpunished replacement for (see the adverse-selection note below).
    Floored well above zero: removing ANY Pokemon is still real progress,
    just not always worth forcing.
    """
    off = max(c.stats.get("atk", 0), c.stats.get("spa", 0))
    value = max(0.35, min(1.35, off / 130.0))  # ~130 is a typical Lv50 attacking stat
    # A Mega pick that has not transformed yet is worth more than the base-form
    # stats above say. Losing it costs the Pokemon AND the Mega slot it never
    # got to spend -- and reported from real games, that is exactly what
    # happened: a Pokemon that could no longer do anything was preserved by
    # switching in "a Mega which had not mega'd", which was less bulky than its
    # Mega form, ate the hits meant for the spent mon, and fainted.
    #
    # Pricing it at what it is about to become makes feeding it hits look as
    # expensive as it is, in both directions: it discourages bringing it in as
    # a wall, and it makes KOing THEIR unevolved Mega attractive.
    if UNSPENT_MEGA_PREMIUM and c.is_mega_pick and not c.mega_evolved:
        value = min(1.35, value * UNSPENT_MEGA_PREMIUM)
    return value


# How much more an un-transformed Mega pick is worth than its base form.
#
# PARKED AT 1.0 (no premium), because measuring it showed it does not do the job
# it was written for:
#
#   * It is INERT for the Megas people actually bring. The value above divides a
#     level-50 attacking stat by 130 and clamps at 1.35, so anything with an
#     attacking stat of 175+ is already at the ceiling -- which a level-50 Mega
#     Metagross or Charizard Y is, before transforming. Measured: the cost of
#     losing an un-transformed Mega Metagross is 319.0 points either way.
#   * It does not change the reported decision. On the exact position from the
#     report -- spent mon slow and at 5% HP, an un-transformed Mega alongside it
#     -- the cost of feeding the Mega half its HP is 49.7 with the premium and
#     49.7 without, because the premium lives in the KO term and being chipped
#     is not a KO. At depth 1 the solver never sees the faint it leads to.
#   * On the 9-pairing audit it made things slightly worse rather than better
#     (mean adjusted wins 0.444 -> 0.423, record 74% -> 72%).
#
# The observation behind it is still right -- losing an un-transformed Mega
# costs the Pokemon AND the Mega slot -- but pricing it needs the Mega's actual
# post-transform stats rather than a multiplier on a clamped base value, and
# probably needs depth 2 to connect "it takes this hit" to "it faints". Left
# implemented, off, and tested so the next attempt starts from something.
UNSPENT_MEGA_PREMIUM = 1.0


# ---------------------------------------------------------------------------
# Preserving a Pokemon that can no longer do anything
# ---------------------------------------------------------------------------
# Reported from real games: "several losses induced by not sacrificing a Pokemon
# that could no longer do much -- it was slower than threats and on low health,
# but it switched out, causing another mon to take the hits and needlessly
# faint."
#
# The cause is in the KO term above, not in the search. `_ko_threat_value` pays
# a Pokemon its FULL threat value for merely not being fainted, so a mon at 5%
# HP that is outsped and dies to the next hit is priced at 63-243 points --
# exactly the same as at full health. Losing it therefore looks like a
# catastrophe worth paying a healthy Pokemon's HP to avoid, and the switch that
# "saves" it wins the comparison. It is the HP term (0-100 points, linear) that
# carries the damage, and it is much the smaller of the two.
#
# What the player's rule actually says: a low-HP Pokemon is worth preserving
# only if it can DO something in a future gamestate. So discount the threat
# credit by how much action it has left:
#
#   healthy, or benched      full value -- nothing is threatening it right now
#   fragile but faster       it still gets one more turn, and can act first
#   fragile and outsped      it likely never acts again; it is already spent
#
# Deliberately cheap and state-based: HP fraction and the speed comparison the
# speed-control term already makes. Asking "does their best move actually KO it"
# would be the exact question, and it costs a damage calculation per foe per
# leaf -- the measured 25x that keeps COVERAGE_WEIGHT switched off.
#
# Priority moves are the reason FRAGILE_HP is not tuned tighter: being faster is
# not safety when Extreme Speed or Sucker Punch is on the field, so "fragile but
# faster" keeps a real discount rather than full value.
#
# OFF BY DEFAULT, on the same evidence as SPEED_CONTROL_WEIGHT above, measured
# the same three ways.
#
# It does move the reported decision, and by a lot. On that position -- our mon
# at 5% HP and outsped, against feeding a healthy one half its HP, which costs
# 50.0 points:
#
#     nothing on (shipped)    sacrificing costs 191.9   -> solver rescues
#     this discount           sacrificing costs  61.0   -> solver rescues
#     + SPEED_CONTROL_WEIGHT  sacrificing costs  37.0   -> solver SACRIFICES
#
# So the discount is most of the correction (192 -> 61: from "worth more than a
# whole Pokemon" to "worth a third of one") but on its own it does not flip this
# particular choice; the flip needs the speed term too, because a slow spent mon
# is a positional liability as well as a dead weight.
#
# Head to head it is neutral (53% over 96 games, CI [43, 63]) and per-decision
# exploitability improves further (nash-mixed 31.5 -> 21.9, regret 9.8 -> 0.4).
# The whole-team audit -- the number teams are ranked on -- comes out slightly
# worse, and that is the gate. See the block above for the table and for what
# turning either term on requires.
#
# ON, AND WHY THE DEFAULT MOVED. This shipped at 0 first time round, on the
# reasoning below: the whole-team audit could not show a gain, so the term had
# not earned a change that moves every number in the tool.
#
# It was then reported a second time, from real games, with the mechanism spelt
# out -- the spent Pokemon switched out, and the Pokemon that came in to cover
# it was a Mega that had not transformed, so it took the hits in its frailer
# base form and fainted for nothing. Two independent reports of the same losing
# pattern outweigh a nine-pairing sweep that could not separate the
# configurations, so the terms are on. The sweep's cost is stated in the block
# above rather than buried: adjusted wins up, record down, high variance.
#
# Set FRAGILE_HP to 0 and the discount is skipped entirely, which is the
# previous evaluation exactly.
FRAGILE_HP = 0.25          # at or below this, one hit ends it (0 disables)
FRAGILE_FASTER_KEEP = 0.6  # threat credit kept when it still acts first
FRAGILE_SLOWER_KEEP = 0.3  # ...and when it does not


def _spent_discount(battle: Battle, c: Combatant, side, foe_side) -> float:
    """How much of `c`'s threat credit survives its current position."""
    if FRAGILE_HP <= 0 or c not in side.active:
        return 1.0                      # benched: nothing is threatening it
    if c.current_hp_frac > FRAGILE_HP:
        return 1.0
    live_foes = [f for f in foe_side.active if f is not None and not f.fainted]
    if not live_foes:
        return 1.0
    faster = all(_moves_first(c, f, battle.field, side.name, foe_side.name)
                 for f in live_foes)
    return FRAGILE_FASTER_KEEP if faster else FRAGILE_SLOWER_KEEP


def heuristic_eval(battle: Battle, my_side_name: str) -> float:
    my = battle.p1 if my_side_name == "p1" else battle.p2
    opp = battle.p2 if my_side_name == "p1" else battle.p1
    if my.has_lost():
        return -10_000
    if opp.has_lost():
        return 10_000
    # COMMON KNOWLEDGE ONLY. Solving a turn as a two-player zero-sum matrix game
    # requires both sides to agree on the value of a state -- our gain must be
    # exactly their loss. That holds only if the evaluation is a function of what
    # BOTH players know, so every term below is computed identically for the two
    # rosters. `revealed` is set for any Pokemon that enters the field
    # (engine.py), so it is genuinely symmetric information.
    #
    # Previously the `seen` filter was applied to the opponent only: `my_hp`
    # summed the full roster while `opp_hp` summed revealed mons alone. That made
    # eval(s,"p1") + eval(s,"p2") == 100 * (hidden HP on both sides), measured at
    # 277.7 points mean -- larger than the mean |eval| itself, and varying per
    # cell, so it did not cancel out of an argmax (it moved the maximin pick on
    # 65% of turns). See tools/measure_antisymmetry.py.
    def is_common_knowledge(c, side):
        return c in side.active or c.fainted or getattr(c, "revealed", False)

    # HP: a mon that has never been on the field is necessarily at FULL HP, and
    # the number of live Pokemon each side has left is public. So counting hidden
    # mons at their (always 1.0) HP fraction leaks nothing -- the old filter was
    # guarding against a disclosure that cannot happen for this term, at the cost
    # of the asymmetry above.
    my_hp = sum(_functional_hp(c) for c in my.roster if not c.fainted)
    opp_hp = sum(_functional_hp(c) for c in opp.roster if not c.fainted)

    # KOing a Pokemon forces a replacement, which is immediately "seen" and enters
    # at full HP -- so opp_hp can go UP the instant we secure a kill (we removed 0.x
    # HP of a fainted mon but revealed a fresh 1.0). Pure HP differential then scores
    # taking a free KO as neutral-or-negative, which is backwards: permanently
    # removing a Pokemon is real, irreversible progress toward winning regardless of
    # how healthy its replacement is. An explicit KO-credit term, weighted well
    # above a turn's normal HP swing, makes sure a clean KO is never worth passing up
    # just to avoid revealing what replaces it.
    #
    # That credit is THREAT-SCALED (_ko_threat_value), not a flat count per KO:
    # killing something that barely threatens our side isn't worth much, and
    # forcing the opponent's free, unpunished replacement in exchange for it can
    # be a bad trade -- adverse selection, they get to bring in their best answer
    # at no cost.
    #
    # This term reads Attack/Sp.Atk, so the old code placeholdered hidden
    # OPPONENT mons at a flat 1.0 while scoring our own bench at its true value.
    # Both halves of that were wrong for a zero-sum game:
    #
    #   - Side-dependent scoring breaks antisymmetry outright: the two players
    #     price the same state differently, so "their loss is our gain" is false.
    #   - Placeholdering by REVEAL STATE creates a reveal incentive. Flipping
    #     `revealed` moved the score by |1.0 - _ko_threat_value| * KO_WEIGHT, up
    #     to 63 points, so simply switching a strong mon in manufactured value
    #     out of an information update rather than out of any real progress.
    #     (Exactly the failure the HP fix above avoids, one term over. Both were
    #     caught by tests/test_leaf_value.py and tools/golden_baseline.py.)
    #
    # So both rosters now use the real value, always. That is a deliberate
    # relaxation of the old no-leak guard, and it is sound HERE because the
    # evaluation is always conditioned on a HYPOTHESISED enemy bring -- callers
    # like search_robust_composition sweep over the brings rather than peeking at
    # one. Within a hypothesis the species are known, and _ko_threat_value is a
    # coarse clamp on attacking stats that discloses nothing about the things
    # that actually stay hidden: moves, items and EV spreads.
    # Scaled by how much action each Pokemon has left (_spent_discount): a mon
    # that is one hit from gone and moves second is not worth a healthy
    # Pokemon's HP to rescue. Applied identically to both rosters, so the term
    # stays antisymmetric.
    my_value = sum(_ko_threat_value(c) * _spent_discount(battle, c, my, opp)
                   for c in my.roster if not c.fainted)
    opp_value = sum(_ko_threat_value(c) * _spent_discount(battle, c, opp, my)
                    for c in opp.roster if not c.fainted)
    score = (my_value - opp_value) * KO_WEIGHT
    score += (my_hp - opp_hp) * 100

    # Positional terms. Without these the evaluation is pure HP differential, so a
    # switch -- which costs a turn and usually takes a hit -- can never look good at
    # depth 1, no matter how bad the current board is. These give credit for the
    # things a pivot actually buys.
    score += _positional_score(my, opp, battle.typechart)
    score -= _positional_score(opp, my, battle.typechart)

    # Who moves first, given the field as it stands (WORKFLOW.md §4.2). Like
    # the positional terms above, computed for both rosters and subtracted, so
    # it cannot break antisymmetry.
    if SPEED_CONTROL_WEIGHT:
        score += _speed_control_score(battle, my, opp)
        score -= _speed_control_score(battle, opp, my)

    # Answer preservation (design doc 4b). Off unless the caller has attached
    # movesets to the battle, because the threat matrix needs to know what each
    # Pokemon can actually do and Combatant does not carry its moves. Callers
    # that want the term set `battle.movesets`; everything else is unchanged.
    if COVERAGE_WEIGHT:
        score += COVERAGE_WEIGHT * _coverage_term(battle, my_side_name)
    return score


# ---------------------------------------------------------------------------
# Speed control (WORKFLOW.md §4.2: the depth-1 horizon)
# ---------------------------------------------------------------------------
# The solver sees one turn ahead, so a turn that changes no HP looks free -- and
# setting Tailwind changes no HP. At depth 1 the state after Tailwind evaluates
# identically to the state after doing nothing, so the search reads a genuine
# four-turn speed advantage as a wasted turn. Same blind spot as the Protect
# spam: not a bug in the search, a term missing from the evaluation.
#
# What is valued is the BOARD, not the move: how many of the opposing actives
# each of ours moves before, under the field as it currently stands. That
# framing does three things a "credit for using Tailwind" bonus would not:
#
#   * it is state-based, so re-setting Tailwind while it is already up gains
#     nothing -- the spam this would otherwise cause is impossible by
#     construction (compare ALLOW_DOUBLE_PROTECT, which needed a filter);
#   * it handles Trick Room, which the engine does NOT attribute to a side. The
#     effect is scored, not its ownership, so a Trick Room that helps the slower
#     side is credited to that side whoever set it;
#   * it picks up everything else that moves the speed order for free --
#     paralysis, Choice Scarf, Swift Swim in rain, a Speed drop.
#
# effective_speed already knows about Tailwind, weather abilities, Scarf and
# paralysis, so this is the same comparison turn_order makes.
#
# WEIGHT, and the evidence for it. 2v2 gives four ordered pairs, so a weight of
# w moves the differential by at most 4w -- at 12 that is 48 points, comfortably
# below _ko_threat_value's 63-243, so speed control can never outbid a KO.
#
# MEASURED TWO WAYS, because they disagree and only one of them is the right
# gate. Win rate is the wrong gate here and this project has said so since the
# redesign: it rewards beating a bot we wrote ourselves. Reported anyway, since
# a term that helped punishability while losing games would need explaining:
#
#   head to head vs weight 0 (tools/measure_headtohead.py --setting
#   SPEED_CONTROL_WEIGHT)
#     w=6   53% over 96 games   95% CI [43, 63]
#     w=12  46% over 64 games   95% CI [34, 58]
#     w=25  50% over 96 games   95% CI [40, 60]
#     -- indistinguishable from baseline at every weight tried: no harm.
#
#   exploitability (tools/measure_robustness.py --brings 6, 192 decision
#   points, --set SPEED_CONTROL_WEIGHT=12 against 0)
#     nash-mixed    44.8 -> 31.5   regret 28.0 -> 9.8   severe 21% -> 14%
#     nash-d1       78.9 -> 67.5   regret 62.1 -> 45.8  severe 35% -> 30%
#     nash-maximin  51.1 -> 50.2   regret 34.2 -> 28.6  severe 22% -> 22%
#     -- a 30% improvement on the configuration the audit pilots with, and no
#        row got worse. On this evidence the term looked like a clear win.
#
# IT IS OFF ANYWAY, and the third measurement is why. Per-decision
# exploitability is not the number teams are ranked on: `adjusted_win_rate` is
# (see team_rating), and a line that concedes nothing because it never
# threatens anything scores well on the first and zero on the second -- the
# trap WORKFLOW.md §1 warns about. So the terms were also run through the real
# audit, 9 pairings per configuration (3 of our teams x Rain / Sand / King,
# standard tier), which is what a team is actually ranked by:
#
#     configuration     mean adjusted wins    record        audited lines won
#     both off             0.435             660/810 (81%)        9/18
#     both on              0.487             605/810 (75%)       11/18
#     speed only           0.285             645/810 (80%)        6/18
#     fragile only         0.332             626/810 (77%)        7/18
#
# Read that carefully, because it does not say what either side wants. Both
# terms together score BEST on adjusted wins and win two more audited lines --
# and cost six points of record, which WORKFLOW.md §1 says to read first. Each
# term ALONE scores worse than having neither, which is the tell: if these were
# real effects of the size they look, they would not be non-monotone. The
# per-pairing adjusted wins are bimodal (mostly 0.0 or 0.8+, since a pairing
# audits two lines), so the mean of nine of them is noisy, and 11 of 18 lines
# against 9 of 18 is not a difference this sample can resolve.
#
# The honest summary of that sweep: no measurable gain on the ranking metric, a
# consistent loss on the record -- and an earlier partial run of the SAME sweep
# pointed the other way at 6 pairings, which is its own warning about reading a
# measurement before it finishes.
#
# What it does fix, demonstrably, is the blind spot it was written for. With it
# on, the golden baseline changes on 11 of 33 pinned turns, and 5 of those are
# `Farigiraf: Protect` becoming `Farigiraf: Trick Room` -- the wasted turn
# becoming a real play. Three more are a chip attack becoming Trick Room.
#
# IT IS ON, and that is a judgement call worth stating plainly. It shipped at 0
# first, on the grounds that the audit could not show a gain. The behaviour it
# fixes was then reported a SECOND time from real games, with the mechanism
# spelt out (see FRAGILE_HP above). A sweep that cannot resolve a difference is
# not evidence that the reported losses are not happening, and between "the
# measurement is neutral" and "this keeps costing me games", the games win.
#
# What that costs, so it is not a surprise later: the record -- `Wins / Of` --
# came out about six points lower across the sweep, while adjusted wins came out
# higher. Watch the record on your own runs. Setting this back to 0.0 (with
# FRAGILE_HP) restores the previous evaluation exactly; changing either means
# bumping roster_rating.RATING_SCHEMA and search_teams.SCHEMA, since every
# cached rating was produced by the evaluation in force when it ran.
SPEED_CONTROL_WEIGHT = 12.0


def _moves_first(mine, theirs, field, my_side, their_side) -> bool:
    """Does `mine` act before `theirs` on speed alone, under this field?

    Priority brackets are deliberately excluded: they belong to a MOVE, and this
    term scores a position, not a choice. Ties count for neither side, which is
    what keeps the two directions exactly antisymmetric.
    """
    from engine import effective_speed
    mine_spe = effective_speed(mine, field, my_side)
    theirs_spe = effective_speed(theirs, field, their_side)
    if field.trick_room:
        return mine_spe < theirs_spe
    return mine_spe > theirs_spe


def _speed_control_score(battle, side, foe_side) -> float:
    """SPEED_CONTROL_WEIGHT per opposing active that `side` moves before."""
    ahead = 0
    for c in side.active:
        if c is None or c.fainted:
            continue
        for f in foe_side.active:
            if f is None or f.fainted:
                continue
            if _moves_first(c, f, battle.field, side.name, foe_side.name):
                ahead += 1
    return SPEED_CONTROL_WEIGHT * ahead


# Scale for the answer-preservation term (design doc 4b).
#
# OFF BY DEFAULT: measured, and it does not currently earn its cost.
#
#   cost    heuristic_eval 0.028 ms -> 0.702 ms (25x), so a matrix CELL goes
#           0.201 ms -> 0.876 ms (4.35x). The term rebuilds the whole threat
#           matrix at every leaf.
#   benefit head to head against the same solver with the term off, at weights
#           0.10 and 0.25: 3 W / 5 L both times. n=8 is far too small to claim
#           it is WORSE, but there is no evidence it is better, and 4.35x is a
#           lot to pay on a hope -- especially after section 2c found the
#           matrix-game solver already ~20x more expensive than assumed.
#
# The threat matrix itself (src/threat.py) is NOT in question and is used for
# the answer map, focus-fire detection, and later for action pruning and
# double-oracle seeding. It is only this evaluation term that is parked.
#
# To revisit, in order: (1) make the matrix incremental or cached so the cost
# is not paid per leaf; (2) re-run tools/measure_headtohead.py over many more
# games; (3) look at coverage_differential itself, whose second half moves when
# our roster size changes and which therefore ranks keystone losses less
# cleanly than our coverage alone (see tests/test_coverage_term.py).
COVERAGE_WEIGHT = 0.0


def _coverage_term(battle: Battle, my_side_name: str) -> float:
    movesets = getattr(battle, "movesets", None)
    if not movesets:
        return 0.0
    from threat import build_threat_matrix, coverage_differential
    diff = coverage_differential(build_threat_matrix(battle, movesets))
    return diff if my_side_name == "p1" else -diff


# STAB threat proxy: how hard the opposing actives hit what we have out.
def _positional_score(side, foe_side, typechart) -> float:
    from damage import type_multiplier
    total = 0.0
    for c in side.active:
        if c is None or c.fainted:
            continue
        # Accumulated stat drops are a real liability -- an Atk/SpA-dropped attacker
        # (e.g. after Draco Meteor, or two Intimidates) is often worth pivoting out.
        total += sum(v for v in c.stages.values() if v < 0) * 7.0
        total += sum(v for v in c.stages.values() if v > 0) * 4.0

        # A Choice lock is a liability once the locked move is resisted or immune.
        if c.item in CHOICE_ITEMS and c.choice_locked_move:
            total -= 6.0

        # Defensive matchup vs what is actually facing it, using the foes' STAB
        # types as a threat proxy.
        for f in foe_side.active:
            if f is None or f.fainted:
                continue
            worst = max((type_multiplier(t, c.types, typechart) for t in f.types), default=1.0)
            if worst >= 4.0:
                total -= 22.0
            elif worst >= 2.0:
                total -= 11.0
            elif worst <= 0.25:
                total += 9.0
            elif worst <= 0.5:
                total += 5.0
    return total


# ---------------------------------------------------------------------------
# Leaf value
# ---------------------------------------------------------------------------
# Everything that scores a position should go through leaf_value() rather than
# calling heuristic_eval() directly, so that the leaf value can change (points ->
# calibrated win probability is expected next) in one edit rather than a hunt.
#
# On symmetry: heuristic_eval is now antisymmetric AT SOURCE -- every term is
# computed from common knowledge, identically for both rosters -- so
# symmetric_eval below is an exact no-op and is off by default.
#
# It is kept for two reasons: it is the cheap guarantee if a future term is
# added that is accidentally one-sided, and flipping the flag makes the cost of
# such a mistake measurable rather than invisible. `tests/test_leaf_value.py`
# asserts the at-source property directly, so a regression fails the suite
# instead of silently being papered over by this wrapper.
#
# Note that averaging is NOT a substitute for fixing the terms. The first
# attempt at this used symmetric_eval over the old one-sided heuristic, and it
# converted a side asymmetry into a REVEAL INCENTIVE: hidden mons ended up
# weighted 0.5x and revealed ones 1.0x, so switching a healthy mon in
# manufactured exactly +50 points (0.5 x 100) from nothing, and the solver
# started preferring switches everywhere. Caught by tools/golden_baseline.py.

SYMMETRIC_EVAL = False   # True forces the averaging wrapper (see above)


# ---------------------------------------------------------------------------
# Phase B: turn-level matrix game
# ---------------------------------------------------------------------------
# When True, solve_best_action solves the turn as a two-player zero-sum matrix
# game (src/turn_game.py) rather than best-responding to
# greedy_opponent_joint_action -- a policy this codebase wrote itself, which is
# the definition of an exploitable strategy.
#
# NASH_DEPTH must be chosen per call site rather than globally. Measured in
# section 2c: depth 1 costs 0.08 s per decision, depth 2 costs 3.88 s (47.9x,
# about 47 s for a twelve-turn game). Depth 2 is for interactive single
# decisions -- Battle Viewer, punish analysis -- and is unaffordable inside any
# sweep.
NASH_SOLVER = False
NASH_DEPTH = 1

# Play the equilibrium MIXTURE rather than its most-likely action.
#
# This matters more than it looks. A mixed strategy's support consists of
# actions that are INDIVIDUALLY exploitable -- mixing them is the entire reason
# the strategy is safe. Collapsing to the modal action therefore discards the
# property that motivated solving the matrix at all. Measured
# (tools/measure_robustness.py, exploitability in heuristic_eval points, lower
# is better):
#
#     greedy                      65.1
#     Nash, argmax of the mixture  75.5   <- worse than greedy
#     Nash, the mixture itself     58.7   <- the actual prize
#
# So the deployed solver was landing on the wrong side of the greedy baseline on
# the metric that matters, while winning 60% of games -- which is exactly why
# win rate is the wrong gate for this project.
#
# Sampling makes the solver non-deterministic, so it is off by default and the
# RNG is seedable: reproducibility is worth keeping for the sweeps and for the
# golden baseline, and an opponent cannot exploit a distribution it cannot
# observe across a single game anyway. Turn it on for real play.
NASH_SAMPLE = True
_NASH_RNG = random.Random(20260808)


def seed_nash_sampling(seed):
    """Reseed mixture sampling, so a run can be made reproducible."""
    global _NASH_RNG
    _NASH_RNG = random.Random(seed)


def _pick_from_mixture(actions, probabilities):
    """Draw an action from the equilibrium distribution."""
    total = sum(probabilities)
    if total <= 0:
        return actions[0] if actions else None
    threshold = _NASH_RNG.random() * total
    cumulative = 0.0
    for action, weight in zip(actions, probabilities):
        cumulative += weight
        if cumulative >= threshold:
            return action
    return actions[-1]


@contextmanager
def solver_mode(nash: bool | None = None, depth: int | None = None):
    """Temporarily select the solver, restoring the previous setting after.

    The UI needs to run one simulation under a user-chosen mode without leaking
    that choice into everything else in the process -- Streamlit keeps module
    state alive across reruns, so setting the globals directly would make the
    last-used mode silently sticky for every other tab.

        with solver_mode(nash=True, depth=2):
            ...
    """
    global NASH_SOLVER, NASH_DEPTH
    previous = (NASH_SOLVER, NASH_DEPTH)
    if nash is not None:
        NASH_SOLVER = nash
    if depth is not None:
        NASH_DEPTH = depth
    try:
        yield
    finally:
        NASH_SOLVER, NASH_DEPTH = previous


def symmetric_eval(battle: Battle, my_side_name: str) -> float:
    """Force antisymmetry by averaging our view against the negation of theirs.

    Exact no-op while heuristic_eval is antisymmetric at source. Only meaningful
    if a one-sided term is reintroduced -- and see the warning above about what
    it does and does not fix.
    """
    other = "p2" if my_side_name == "p1" else "p1"
    return (heuristic_eval(battle, my_side_name) - heuristic_eval(battle, other)) / 2.0


def leaf_value(battle: Battle, my_side_name: str) -> float:
    """The single scoring entry point for search. See the note above."""
    if SYMMETRIC_EVAL:
        return symmetric_eval(battle, my_side_name)
    return heuristic_eval(battle, my_side_name)


# Both slots protecting on the same turn passes the whole turn: no damage, no
# position, no progress -- and it hands the opponent a free turn to switch or
# set up. It survives a depth-1 maximin search anyway, because Protect's worst
# case is the best worst case when the evaluation cannot see that nothing was
# gained. Measured on one line: our side protected on 3 of 6 turns, and it is
# also what produced the enormous ties that made "punishes" meaningless (every
# attack they make leads to the same protected state -- 46 of 62 replies
# identical on one turn).
#
# The exceptions are real and are why this is a targeted filter rather than a
# blanket ban. Double Protect is a genuine option when the opponent's speed
# control is ticking (stalling it out is what Protect is for) and on a turn
# they could Fake Out. In those positions the combo stays a CANDIDATE -- the
# search then decides, which matters because it is often still wrong: Fake Out
# does almost no damage, so eating it and switching can beat handing them a
# free turn to set up.
ALLOW_DOUBLE_PROTECT = False


def _opposing_speed_control(battle: Battle, side: Side) -> bool:
    """Is there a speed-control effect worth stalling out?

    Tailwind is attributed, so "theirs" is exact. Trick Room is not -- the
    engine does not record who set it -- so any active Trick Room permits the
    stall. That is deliberately permissive: this filter only REMOVES an option,
    and wrongly leaving it available costs a search branch, while wrongly
    removing it would hide the correct play against a Trick Room team.
    """
    f = battle.field
    theirs = f.tailwind_p2 if side.name == "p1" else f.tailwind_p1
    return bool(theirs > 0 or f.trick_room)


def _fake_out_pressure(battle: Battle, side: Side) -> bool:
    """Could an opposing active Fake Out THIS turn?

    Fake Out is legal only on the turn a Pokemon is sent out
    (`active_turn_count == 0`), so this is a one-turn window, not a property of
    the matchup. Their move space is read from `wide_movesets` where the caller
    attached it, because a Fake Out we did not plan for is exactly the case
    that matters.
    """
    opp = battle.p2 if side.name == "p1" else battle.p1
    known = (getattr(battle, "wide_movesets", None)
             or getattr(battle, "movesets", None) or {})
    for c in opp.active:
        if c is None or c.fainted or getattr(c, "active_turn_count", 1) != 0:
            continue
        for m in known.get(c.name) or []:
            if getattr(m, "name", m) == "Fake Out":
                return True
    return False


def _double_protect_has_a_purpose(battle: Battle, side: Side) -> bool:
    """The cases where passing the turn with both slots is a real option.

    Note what this does NOT claim: that double Protect is right in these
    positions. It only keeps the option in the candidate set so the search can
    price it. Against Fake Out in particular it is often wrong -- Fake Out does
    almost no damage, so eating it and switching or attacking can be far better
    than handing them a free turn to set up or improve their board. Removing
    the option would decide that for the search; leaving it in lets the
    position decide.
    """
    return _opposing_speed_control(battle, side) or _fake_out_pressure(battle, side)


def _is_pointless_double_protect(combo, battle: Battle, side: Side) -> bool:
    """Every active slot protecting, with no speed control to stall."""
    acting = [a for a in combo if a.kind != "switch"]
    if len(acting) < 2 or len(acting) != len(combo):
        return False           # a switch alongside a Protect is a real play
    if not all(a.kind == "protect" for a in acting):
        return False
    return not _double_protect_has_a_purpose(battle, side)


def our_candidate_joint_actions(battle: Battle, side: Side, opp_side: Side, movesets: dict,
                                 turn_num: int):
    per_mon_options = []
    for c in side.active:
        if c.fainted:
            if side.bench:
                # Forced replacement: must send in a bench member for this empty slot.
                per_mon_options.append([Action(c, side.name, "switch", None, [b]) for b in side.bench])
            continue  # no bench left -> this slot contributes no action
        # Pass the bench so VOLUNTARY switches are candidate actions, not just forced
        # replacements after a faint. Pivoting is a core doubles resource: it resets a
        # Choice lock, escapes accumulated stat drops, and lets a resist absorb a hit
        # that would otherwise lose the board.
        # ...unless it is TRAPPED. Shadow Tag (and the rest of Battle.is_trapped)
        # was enforced only when the turn was executed, so the search offered
        # switches that would be refused at run_turn -- and worse, offered them
        # to the OPPONENT, so every payoff matrix against a Shadow Tag user
        # contained columns they cannot legally play.
        #
        # That is not a small error: trapping is the whole point of Mega Gengar,
        # and pricing their escape as available both overstates their options
        # and hides the value of taking it away. It also cost us turns directly
        # -- the solver could choose a switch that simply failed.
        trapped = battle.is_trapped(c)
        cands = candidate_actions(c, side.name, side.active, opp_side.active,
                                   movesets[c.name], battle.typechart, battle.field, turn_num,
                                   bench=None if trapped else
                                   [b for b in side.bench if not b.fainted])
        per_mon_options.append(cands)
    if not per_mon_options:
        return [[]]
    combos = [list(combo) for combo in itertools.product(*per_mon_options)]
    # Filter out combos that try to send the same bench mon into two empty slots at once.
    def valid(combo):
        incoming = [id(a.targets[0]) for a in combo if a.kind == "switch"]
        return len(incoming) == len(set(incoming))
    combos = [c for c in combos if valid(c)]
    if not ALLOW_DOUBLE_PROTECT:
        combos = [c for c in combos if not _is_pointless_double_protect(c, battle, side)]
    return combos or [c for c in itertools.product(*per_mon_options)][:1]


def solve_best_action(battle: Battle, my_side_name: str, movesets: dict, depth: int = 1,
                       enemy_script=None):
    """
    Returns (best_joint_action, expected_score, sim_log) for `my_side_name`
    this turn, searching `depth` turns ahead (depth=1 means "just this turn,
    then heuristic-evaluate the result").

    enemy_script: when set (a scripted opponent -- see scripted_openings.py),
    planning ASSUMES THE OPPONENT ACTUALLY RUNS ITS SCRIPT this turn, instead
    of the generic greedy model. We have real, documented knowledge of what a
    team like Hard Trick Room or Perish Trap does on a given turn (e.g. that
    its Mega Gengar Perish Songs rather than attacks) -- planning as if it
    might attack instead throws that knowledge away and produces nonsense like
    Sucker Punching a Pokemon that was never going to attack.

    Committing blindly to "beat the script" is dangerous if the opponent
    doesn't actually run it, though, so every candidate is ALSO checked
    against the plain greedy (unscripted) response as a stand-in for "they
    deviated". A candidate that would cost us a Pokemon fainting THIS TURN
    against that deviation is excluded, unless every candidate shares that
    flaw (then the search has no safe option and falls back to ranking on the
    assume-script score alone). Among what's left, the one that scores best
    assuming the script wins -- the foremost goal is to beat the script,
    without being blown up the instant it doesn't happen.
    """
    # Phase B: solve the turn as a matrix game instead of best-responding to a
    # policy we wrote ourselves. Off by default until measured (see
    # tools/measure_headtohead.py). Imported lazily because turn_game imports
    # from this module.
    if NASH_SOLVER:
        from turn_game import solve_turn
        # enemy_script is passed through rather than ignored: a script is a
        # PRIOR on their columns, not a separate algorithm (section 3d). This is
        # what lets the matrix solve subsume the CHECK_TOP_K deviation check --
        # off-script columns keep their equilibrium weight, so a play that loses
        # to a deviation is priced for it without an arbitrary top-K cap.
        solution = solve_turn(battle, my_side_name, movesets, depth=NASH_DEPTH,
                              enemy_script=enemy_script)
        if solution.our_actions:
            # Callers use the third value as a liveness signal, not just as a
            # convenience: committed_plan and matchup_search both treat
            # `sim is None` as "the solver found nothing, stop here". Returning
            # None unconditionally made every one of those paths bail on turn 1
            # the moment the Nash solver was switched on -- silently, since
            # bailing looks exactly like a legitimately dead position.
            #
            # So play the chosen action against their most likely reply. One
            # extra run_turn, against a ~240-simulation solve.
            # Play the MIXTURE, not its mode -- see NASH_SAMPLE above. The
            # actions in an equilibrium's support are individually exploitable;
            # mixing them is what makes the strategy safe, so collapsing to the
            # argmax throws away the whole point.
            if NASH_SAMPLE:
                chosen = _pick_from_mixture(solution.our_actions, solution.p)
            else:
                chosen = solution.best_action

            sim = None
            if solution.their_actions:
                from turn_step import step as _step
                likely = max(range(len(solution.q)), key=lambda j: solution.q[j])
                sim = _step(battle, chosen, solution.their_actions[likely])
            return chosen, solution.value, sim

    my_side = battle.p1 if my_side_name == "p1" else battle.p2
    opp_side = battle.p2 if my_side_name == "p1" else battle.p1

    joint_options = our_candidate_joint_actions(battle, my_side, opp_side, movesets, battle.turn_num + 1)
    my_alive_before = sum(1 for c in my_side.roster if not c.fainted)

    def _play_branch(my_joint, opp_action_fn):
        sim = copy.deepcopy(battle)
        # Plan DETERMINISTICALLY. If the search explores branches using the same
        # RNG stream the real battle is scored on, it just picks whichever branch
        # the dice happened to favour -- reporting coin flips and 30% flinches as
        # certainties. Planning uses average rolls and no secondary procs; the
        # chosen action is then executed on the real battle, where RNG applies.
        sim.rng = None
        sim.tie_bias = battle.tie_bias
        sim_my_side = sim.p1 if my_side_name == "p1" else sim.p2
        sim_opp_side = sim.p2 if my_side_name == "p1" else sim.p1

        # Re-point the actions' combatant/target refs at the deep-copied objects. Map the
        # FULL roster (not just currently-active mons) so switch-in targets drawn from the
        # bench also resolve to their correct deep-copied counterpart, not a stale pre-copy
        # object -- otherwise a switched-in mon silently desyncs from the roster used for
        # win-condition checks (a real bug this caught: fainted-in-log but alive-in-roster).
        my_map = {id(o): n for o, n in zip(my_side.roster, sim_my_side.roster)}
        opp_map = {id(o): n for o, n in zip(opp_side.roster, sim_opp_side.roster)}
        full_map = {**my_map, **opp_map}

        def remap(a: Action) -> Action:
            return Action(full_map.get(id(a.combatant), a.combatant), a.side, a.kind, a.move,
                          [full_map.get(id(t), t) for t in a.targets])

        my_joint_r = [remap(a) for a in my_joint]
        opp_joint = opp_action_fn(sim, sim_opp_side, sim_my_side)

        p1_actions = my_joint_r if my_side_name == "p1" else opp_joint
        p2_actions = opp_joint if my_side_name == "p1" else my_joint_r
        sim.run_turn(p1_actions, p2_actions)
        return sim, sim_my_side

    script_used_flags = []  # observed True/False per candidate: did the script actually
                              # fire this turn, or has it run out (e.g. King past turn 2)?

    def _script_or_greedy(sim, sim_opp_side, sim_my_side):
        opp = None
        if enemy_script is not None:
            try:
                opp = enemy_script(sim, sim_opp_side, sim_my_side, sim.turn_num + 1)
            except Exception:
                opp = None
        script_used_flags.append(bool(opp))
        if not opp:
            opp = greedy_opponent_joint_action(sim, sim_opp_side, sim_my_side, movesets,
                                                sim.turn_num + 1)
        return opp

    def _greedy(sim, sim_opp_side, sim_my_side):
        return greedy_opponent_joint_action(sim, sim_opp_side, sim_my_side, movesets,
                                             sim.turn_num + 1)

    scored = []  # (my_joint, score, sim)
    for my_joint in joint_options:
        sim, sim_my_side = _play_branch(my_joint, _script_or_greedy)

        if depth > 1 and not sim.is_over():
            _, future_score, _ = solve_best_action(sim, my_side_name, movesets, depth - 1,
                                                     enemy_script=enemy_script)
            score = future_score
        else:
            score = leaf_value(sim, my_side_name)

        scored.append((my_joint, score, sim))

    if not scored:
        return None, -float("inf"), None

    # Once the script has nothing left to say this turn (e.g. King's script only
    # covers turns 1-2 -- every candidate already fell back to greedy above), the
    # assume-script and assume-greedy branches are IDENTICAL, so a deviation
    # check would find nothing new. Skipping it here matters a lot in practice:
    # most of a scripted team's game is actually unscripted play after the
    # opening, and this keeps that majority at the same cost as any other match.
    if enemy_script is None or not any(script_used_flags):
        best = max(scored, key=lambda c: c[1])
        return best[0], best[1], best[2]

    # Deviation-safety check: only for scripted opponents, and only against the
    # top candidates by assume-script score, not the whole candidate space --
    # doubling the cost of every single candidate compounds badly with a large
    # per-mon moveset (this genuinely timed out King's 90-config search before
    # this cap was added). Check in descending script-score order and take the
    # first one that isn't punished; if every one of those is, fall back to the
    # single best script score (no safe option was found in the checked pool).
    #
    # SUPERSEDED, not yet deleted. This whole block exists because planning
    # ASSUMES the script and then has to defend against the assumption being
    # wrong -- and the cap of 6 is arbitrary, chosen for runtime rather than for
    # any property of the game. The matrix solver replaces it properly: a script
    # is a PRIOR on the opponent's columns (turn_game.solve_turn's
    # `enemy_script`/`script_confidence`), so off-script replies keep their
    # equilibrium weight and a play that loses to a deviation is priced for it
    # directly. No assumption to defend, and no cap.
    #
    # It is kept only because NASH_SOLVER is still off by default, so this is
    # the live path. Delete it together with that default flip; until then
    # removing it would change current behaviour for no measured benefit.
    CHECK_TOP_K = 6
    scored.sort(key=lambda c: -c[1])
    for my_joint, score, sim in scored[:CHECK_TOP_K]:
        sim_dev, sim_dev_my_side = _play_branch(my_joint, _greedy)
        my_alive_after_dev = sum(1 for c in sim_dev_my_side.roster if not c.fainted)
        if my_alive_after_dev >= my_alive_before:
            return my_joint, score, sim

    best = scored[0]
    return best[0], best[1], best[2]


def punish_check(battle: Battle, my_side_name: str, our_action: list, movesets: dict, cap: int = 40):
    """Given a candidate joint action for `my_side_name` THIS turn, is there a
    legal response of the opponent's that really punishes it -- an immediate KO
    of one of ours this turn, or (if not KO'd immediately) a KO on their very
    next follow-up that our best defensive play couldn't prevent?

    This is the concrete worry behind "assumed they'd do X, they did Y instead":
    e.g. we target their Garchomp with a Dragon move assuming it stays in, but
    they switch it for a Fairy-type immune to Dragon and we eat a hit instead;
    or we split our attacks assuming they Protect one specific slot, but they
    Protect the other and the assumption costs us a Pokemon. Every one of their
    LEGAL joint actions is tried (including switches), not just their greedy
    pick or scripted line -- worst response wins.

    Returns {"punished": bool, "kind": "immediate" | "next_turn" | None,
             "their_action": [(name, kind, move_name, [targets]), ...] or None}.
    "immediate": one of our currently-active Pokemon faints resolving THIS turn.
    "next_turn": survives this turn, but even our own best response (solve_best_action)
    loses one of ours on the very next turn -- i.e. this turn's choice didn't
    actually buy safety, just delayed the loss by one turn.
    """
    my_side = battle.p1 if my_side_name == "p1" else battle.p2
    opp_side = battle.p2 if my_side_name == "p1" else battle.p1
    my_alive_before = sum(1 for c in my_side.roster if not c.fainted)

    their_options = our_candidate_joint_actions(battle, opp_side, my_side, movesets,
                                                  battle.turn_num + 1)[:cap]

    worst = None  # (rank, kind, their_action_desc)
    for their_joint in their_options:
        sim = copy.deepcopy(battle)
        sim.rng = None
        sim.tie_bias = battle.tie_bias
        sim_my_side = sim.p1 if my_side_name == "p1" else sim.p2
        sim_opp_side = sim.p2 if my_side_name == "p1" else sim.p1

        my_map = {id(o): n for o, n in zip(my_side.roster, sim_my_side.roster)}
        opp_map = {id(o): n for o, n in zip(opp_side.roster, sim_opp_side.roster)}
        full_map = {**my_map, **opp_map}

        def remap(a: Action) -> Action:
            return Action(full_map.get(id(a.combatant), a.combatant), a.side, a.kind, a.move,
                          [full_map.get(id(t), t) for t in a.targets])

        my_joint_r = [remap(a) for a in our_action]
        their_joint_r = [remap(a) for a in their_joint]
        p1_actions = my_joint_r if my_side_name == "p1" else their_joint_r
        p2_actions = their_joint_r if my_side_name == "p1" else my_joint_r
        try:
            sim.run_turn(p1_actions, p2_actions)
        except ValueError:
            continue

        my_alive_after = sum(1 for c in sim_my_side.roster if not c.fainted)
        kind = None
        if my_alive_after < my_alive_before:
            kind = "immediate"
        elif not sim.is_over():
            _, _, sim2 = solve_best_action(sim, my_side_name, movesets, depth=1)
            if sim2 is not None:
                sim2_my_side = sim2.p1 if my_side_name == "p1" else sim2.p2
                my_alive_next = sum(1 for c in sim2_my_side.roster if not c.fainted)
                if my_alive_next < my_alive_after:
                    kind = "next_turn"

        if kind is not None:
            rank = 2 if kind == "immediate" else 1
            if worst is None or rank > worst[0]:
                desc = [(a.combatant.name, a.kind, a.move.name if a.move else "-",
                        [t.name for t in a.targets]) for a in their_joint]
                worst = (rank, kind, desc)

    if worst is None:
        return {"punished": False, "kind": None, "their_action": None}
    return {"punished": True, "kind": worst[1], "their_action": worst[2]}


if __name__ == "__main__":
    from species_data import build_merged_dataset
    from stats import compute_stats

    merged, _, moves, natures, typechart = build_merged_dataset()

    def make_combatant(name):
        p = merged[name]
        nat = natures[p["nature"].lower()]
        stats = compute_stats(p["base_stats"], nat, p["evs"])
        ab = p["abilities_usage"][0][0] if p["abilities_usage"] else ""
        it = p["items_usage"][0][0] if p["items_usage"] else ""
        return Combatant(name=name, stats=stats, types=p["types"], ability=ab, item=it)

    # Our lead: Incineroar + Farigiraf. Enemy lead (from teams.csv "Rain"): Archaludon + Pelipper.
    incineroar = make_combatant("Incineroar")
    farigiraf = make_combatant("Farigiraf")
    archaludon = make_combatant("Archaludon")
    pelipper = make_combatant("Pelipper")

    battle = Battle([incineroar, farigiraf], [archaludon, pelipper], typechart, moves)

    movesets = {}
    for name in ["Incineroar", "Farigiraf", "Archaludon", "Pelipper"]:
        movesets[name] = build_moveset(merged[name], moves)
        print(name, "moveset:", [(m.name, round(pct, 1)) for m, pct in movesets[name]])

    best_action, score, sim = solve_best_action(battle, "p1", movesets, depth=1)
    print("\nBest turn-1 joint action for us:")
    for a in best_action:
        print(f"  {a.combatant.name} -> {a.kind} {a.move.name if a.move else ''} "
              f"targeting {[t.name for t in a.targets]}")
    print(f"Resulting heuristic score: {score:.1f}")
    print("\nSimulated log:")
    print(sim.log.dump())
