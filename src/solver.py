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
import itertools

from damage import Combatant, MoveInfo, is_spread_move, effective_stat, damage_roll, CHARGE_WEATHER_SKIP
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
        m = moves_db[key]
        out.append((MoveInfo(m["name"], m["basePower"], m["type"], m["category"], m["target"],
                              priority=m.get("priority", 0), secondary=m.get("secondary"),
                              self_effect=m.get("self"), boosts=m.get("boosts"),
                              recoil=m.get("recoil"), drain=m.get("drain"),
                              has_crash=bool(m.get("hasCrashDamage")),
                              volatile_status=m.get("volatileStatus"), flags=m.get("flags"),
                              self_switch=m.get("selfSwitch"), accuracy=m.get("accuracy", True)), pct))
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
    def_stat = effective_stat(target.stats[def_key], target.stages[def_key])
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
    return max(0.35, min(1.35, off / 130.0))  # ~130 is a typical Lv50 attacking stat


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
    my_hp = sum(c.current_hp_frac for c in my.roster if not c.fainted)
    opp_hp = sum(c.current_hp_frac for c in opp.roster if not c.fainted)

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
    my_value = sum(_ko_threat_value(c) for c in my.roster if not c.fainted)
    opp_value = sum(_ko_threat_value(c) for c in opp.roster if not c.fainted)
    score = (my_value - opp_value) * KO_WEIGHT
    score += (my_hp - opp_hp) * 100

    # Positional terms. Without these the evaluation is pure HP differential, so a
    # switch -- which costs a turn and usually takes a hit -- can never look good at
    # depth 1, no matter how bad the current board is. These give credit for the
    # things a pivot actually buys.
    score += _positional_score(my, opp, battle.typechart)
    score -= _positional_score(opp, my, battle.typechart)
    return score


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
        cands = candidate_actions(c, side.name, side.active, opp_side.active,
                                   movesets[c.name], battle.typechart, battle.field, turn_num,
                                   bench=[b for b in side.bench if not b.fainted])
        per_mon_options.append(cands)
    if not per_mon_options:
        return [[]]
    combos = [list(combo) for combo in itertools.product(*per_mon_options)]
    # Filter out combos that try to send the same bench mon into two empty slots at once.
    def valid(combo):
        incoming = [id(a.targets[0]) for a in combo if a.kind == "switch"]
        return len(incoming) == len(set(incoming))
    return [c for c in combos if valid(c)]


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
