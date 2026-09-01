"""
Fast 2v2 pair-vs-pair evaluator, for large-scale screening.

WHY THIS EXISTS (and how it differs from solver.py):
solver.py does a real expectimax search -- it deep-copies the battle state
and tries every joint action combination each turn. That's accurate but far
too slow to evaluate the tens of thousands of pair matchups needed to
*generate* a team from a ~270-Pokemon pool.

This module instead runs a plain greedy-vs-greedy playout: both sides simply
pick their highest-expected-damage action each turn (with a few cheap
heuristics -- lead-turn Fake Out, opening Trick Room/Tailwind, and Protect
when it's about to be KO'd and Protect is available and legal). No search,
no deep copies. Roughly 50-100x faster than the solver.

It is a SCREEN, not a verdict. The intended pipeline is:
    1. fast_eval  -> rank thousands of candidate teams cheaply
    2. solver.py  -> verify the handful of finalists properly
Never present a fast_eval number as a final answer on its own.
"""
from damage import (Combatant, MoveInfo, is_spread_move, damage_roll, apply_boosts,
                     effective_stat, CHARGE_WEATHER_SKIP)
from engine import FieldState, Action, on_switch_in, turn_order
from battle import Battle, PROTECT_MOVES, priority_blocked_by_side
from solver import build_moveset, quick_damage_estimate, FIRST_TURN_ONLY_MOVES, CHOICE_ITEMS

FAST_MAX_TURNS = 8
SETUP_MOVES = {"Trick Room", "Tailwind"}


def _speed_control_active(battle, side_key, move_name):
    """True if this speed-control effect is already up for that side."""
    if move_name == "Trick Room":
        return battle.field.trick_room
    if move_name == "Tailwind":
        return (battle.field.tailwind_p1 if side_key == "p1" else battle.field.tailwind_p2) > 0
    return False


def _pick_greedy_action(battle: Battle, c: Combatant, side_key: str, foes: list, moveset,
                          allies: list | None = None):
    """One Pokemon's greedy choice: best immediate damage, with cheap tactical
    heuristics for Fake Out, opening setup moves, and emergency Protect."""
    live_foes = [f for f in foes if not f.fainted]
    if not live_foes:
        return None

    usable = moveset
    if c.item in CHOICE_ITEMS and c.choice_locked_move:
        locked = [m for m in usable if m[0].name == c.choice_locked_move]
        if locked:
            usable = locked
    if c.active_turn_count > 0:
        usable = [m for m in usable if m[0].name not in FIRST_TURN_ONLY_MOVES]

    best_action, best_val = None, -1e9
    for move, pct in usable:
        # Two-turn (charge) moves deal no damage this turn unless the right weather
        # is already up to skip the charge -- see solver.candidate_actions for the
        # matching filter and full rationale. Already-charging is forced to complete
        # by battle.py's real turn resolution regardless of what's chosen here.
        if move.flags and move.flags.get("charge"):
            skip_weather = CHARGE_WEATHER_SKIP.get(move.name)
            weather_ready = bool(skip_weather) and battle.field.weather == skip_weather
            already_charging = c.volatile.get("charging_move") == move.name
            if not weather_ready and not already_charging:
                continue
        if move.name in PROTECT_MOVES:
            # Only worth it if legal (didn't protect last turn). Small fixed value so it
            # loses to any real damaging option but beats doing nothing.
            if not c.protected_last_turn:
                val = 12.0
                if val > best_val:
                    best_val, best_action = val, Action(c, side_key, "protect", move, [c])
            continue

        if move.category == "Status":
            # Speed control (Tailwind / Trick Room) is a board-state swing, not filler:
            # it should be used as early as possible, and is only wasted if the same
            # effect is already up on our side. Prankster/Gale Wings users get it off
            # before attacks, which makes it even better.
            if move.name in SETUP_MOVES and not _speed_control_active(battle, side_key, move.name):
                val = 85.0 if c.ability in ("Prankster", "Gale Wings") else 70.0
            elif move.name in SETUP_MOVES:
                val = -5.0  # already active, don't waste the turn re-setting it
            elif move.boosts and move.target in ("self", "allySide"):
                val = 25.0
            else:
                val = 5.0
            if move.target in ("self", "allySide", "all") and val > best_val:
                best_val, best_action = val, Action(c, side_key, "move", move, [c])
            continue

        # Damage is normalised to "percent of the target's remaining HP" so it is on
        # the same scale as the status-move values above. Using raw HP damage here made
        # every attack outrank every status move regardless of board state.
        def _pct(target, nhit):
            # Queenly Majesty / Dazzling / Armor Tail on the TARGET's own side
            # block this outright if it's priority -- checked against whichever
            # side `target` actually belongs to, since a spread move can hit
            # both an ally and a foe in the same turn ("the enemy trying to
            # click priority moves anyway" against one of these).
            side_roster = allies if (allies and target in allies) else live_foes
            if priority_blocked_by_side(c.ability, move, side_roster):
                return 0.0, 0.0
            dmg = quick_damage_estimate(c, target, move, battle.typechart, battle.field,
                                         num_hit=nhit, battle=battle)
            pct = 100.0 * min(dmg, target.current_hp) / target.max_hp() if target.max_hp() else 0.0
            return pct, dmg

        total_dealt = 0.0
        if is_spread_move(move.target):
            from damage import spread_targets
            targets = spread_targets(move.target, live_foes, allies or [], c)
            val = 0.0
            for f in targets:
                pct, dmg = _pct(f, len(targets))
                dealt = min(dmg, f.current_hp)
                total_dealt += dealt
                # allAdjacent moves hit our own partner too -- that is a cost.
                if f in (allies or []) and f is not c:
                    val -= pct * 1.2 + (50.0 if dmg >= f.current_hp else 0.0)
                else:
                    val += pct + (40.0 if dmg >= f.current_hp else 0.0)
        else:
            scored = [(_pct(f, 1), f) for f in live_foes]
            (best_pct, best_dmg), tgt = max(scored, key=lambda x: x[0][0])
            val = best_pct + (40.0 if best_dmg >= tgt.current_hp else 0.0)
            targets = [tgt]
            total_dealt = min(best_dmg, tgt.current_hp)

        # Recoil moves (Flare Blitz, Head Smash, Light of Ruin, ...) cost the user HP
        # for the same damage dealt -- a non-recoil move doing the same job (e.g. an
        # equally-lethal hit) should win the tie. Rock Head / Magic Guard negate it.
        if move.recoil and c.ability not in ("Rock Head", "Magic Guard") and c.max_hp():
            num, den = move.recoil
            val -= 100.0 * (total_dealt * num / den) / c.max_hp()

        # Tiny tie-break, far below any real damage/KO difference: when two moves are
        # otherwise equally good, prefer the more reliable one (see solver.py's
        # action_value for the matching comment -- no miss chance is modeled here,
        # this only ever decides genuine ties).
        acc = 100.0 if move.accuracy is True else move.accuracy
        val += acc * 0.001

        if val > best_val:
            best_val, best_action = val, Action(c, side_key, "move", move, targets)

    return best_action


def _side_actions(battle: Battle, side, foe_side, movesets):
    acts = []
    used = set()
    for c in side.active:
        if c.fainted:
            remaining = [b for b in side.bench if not b.fainted and id(b) not in used]
            if remaining:
                incoming = max(remaining, key=lambda b: b.current_hp)
                used.add(id(incoming))
                acts.append(Action(c, side.name, "switch", None, [incoming]))
            continue
        a = _pick_greedy_action(battle, c, side.name, foe_side.active, movesets[c.name],
                                 allies=[x for x in side.active if x is not None])
        if a:
            acts.append(a)
    return acts


def fast_playout(our_names, enemy_names, merged, moves_db, natures, typechart,
                  max_turns=FAST_MAX_TURNS, our_mega=None, enemy_mega=None, movesets=None,
                  our_sets=None, enemy_sets=None):
    """Greedy-vs-greedy playout. Returns dict with winner, turns, and the
    end-state HP fractions (which give a much finer-grained signal than
    win/loss alone when screening).

    `our_sets` / `enemy_sets` are the edited sets -- ability, item, EVs, and the
    four moves actually chosen. They used not to be accepted here at all, so the
    screener ranked every candidate on DEFAULT USAGE SETS while the verification
    stage played the real ones. An Arcanine-Hisui switched to Intimidate, or a
    Pokemon whose four moves were hand-picked, changed nothing about which
    candidates were shortlisted -- only about how the shortlist then scored.
    """
    from combatants import make_team
    oc = make_team(our_names, merged, natures, mega_transforms=our_mega, sets=our_sets)
    ec = make_team(enemy_names, merged, natures, mega_transforms=enemy_mega, sets=enemy_sets)

    if movesets is None:
        movesets = {}
    for c in oc + ec:
        spec = (our_sets or {}).get(c.name) or (enemy_sets or {}).get(c.name) or {}
        only = tuple(spec.get("moves") or ())
        # Keyed by the CHOSEN MOVES as well as the name: a bare name key returns
        # the default four for a Pokemon whose moves were edited, and would also
        # hand one side's edited set to the other side's mon of the same name in
        # a mirror.
        key = (c.name, only)
        if key not in movesets:
            movesets[key] = build_moveset(merged[c.name], moves_db, top_k=4,
                                          only_moves=spec.get("moves"))
        movesets[c.name] = movesets[key]

    battle = Battle(oc, ec, typechart, moves_db)
    for _ in range(max_turns):
        if battle.is_over():
            break
        p1 = _side_actions(battle, battle.p1, battle.p2, movesets)
        p2 = _side_actions(battle, battle.p2, battle.p1, movesets)
        if not p1 and not p2:
            break
        try:
            battle.run_turn(p1, p2)
        except ValueError:
            break

    our_hp = sum(c.current_hp / c.max_hp() for c in battle.p1.roster if not c.fainted)
    opp_hp = sum(c.current_hp / c.max_hp() for c in battle.p2.roster if not c.fainted)
    our_alive = sum(1 for c in battle.p1.roster if not c.fainted)
    opp_alive = sum(1 for c in battle.p2.roster if not c.fainted)

    winner = battle.winner() or "timeout"
    # Margin: positive = good for us. Combines KO count (dominant term) and
    # leftover HP (tiebreaker), so "won with both alive at full" beats
    # "won with one survivor at 5%".
    margin = (our_alive - opp_alive) * 100 + (our_hp - opp_hp) * 20
    return {"winner": winner, "turns": battle.turn_num, "margin": margin,
            "our_hp": our_hp, "opp_hp": opp_hp, "battle": battle}


def fast_pair_score(our_pair, enemy_pair, merged, moves_db, natures, typechart, movesets=None,
                     our_sets=None, enemy_sets=None):
    """Screening score for one of our lead pairs vs one enemy lead pair.

    Minimax over Mega choices: WE pick whichever of our Megas is best for us,
    THEY pick whichever of theirs is worst for us (both sides play their own
    mega slot to their advantage). Usually only one variant exists per side,
    in which case this is a single playout.
    """
    from species_data import mega_variants
    our_variants = mega_variants(list(our_pair))
    enemy_variants = mega_variants(list(enemy_pair))

    best_for_us = None
    for ov in our_variants:
        worst_vs_this = None
        for ev in enemy_variants:
            r = fast_playout(list(our_pair), list(enemy_pair), merged, moves_db, natures, typechart,
                              our_mega=ov, enemy_mega=ev, movesets=movesets,
                              our_sets=our_sets, enemy_sets=enemy_sets)
            if worst_vs_this is None or r["margin"] < worst_vs_this["margin"]:
                worst_vs_this = r
        if best_for_us is None or worst_vs_this["margin"] > best_for_us["margin"]:
            best_for_us = worst_vs_this
    return best_for_us
