"""Search the Pokemon pool for answers to specific threats.

    "using the move selection logic, I want to search for specific pokemon
     that beat others. For instance, I want a pokemon that can OHKO or do 90%
     Kingambit, Basculegion, assuming most favourable item, or maybe a Pokemon
     which can KO a number of selected pokemon after chip from a specified
     partner using a specified move, or vs any pair of the selected Pokemon
     while taking damage according to speed order (sequential game)"

Three questions, three functions, and none of them re-derives damage or move
choice from scratch:

  threshold_search  -- who OHKOs / clears X% on each of these targets, with
                        its best LEGAL item for the matchup?
  chip_then_ko      -- who finishes these targets off after a named partner's
                        named move has already landed?
  pair_search       -- who gets to act at all (isn't pinned) and KOes,
                        against every PAIR drawn from these targets, resolved
                        in real speed order?

Move and item selection is `optimize_sets.best_item`/`best_moveset` -- the
same search `spread_table.py` and the lead screen use, so a Pokemon found here
carries the set it would actually be built with, not a hand-picked one. Damage
is `damage.damage_roll`, read the same way `optimize_sets.move_value_table`
reads it (worst roll, as a fraction of the target's CURRENT hp) -- not the
KO/priority/speed-control-boosted VALUE that function scores movesets with,
which is a ranking score rather than an actual percentage.

WHAT THIS DOES NOT MODEL. No field: no weather except a combatant's own
instant-set (Drought/Drizzle/Sand Stream/Snow Warning), no Tailwind, no
screens, no Follow Me/Rage Powder redirection, no items played out over a
whole game (Focus Sash survival, Life Orb recoil accumulating). It is a single
worst-roll hit, or a short sequence of them -- a hypothesis to point the
detailed lead-race tools at, in the same division of labour `lead_scan.py`
documents for its own cheap stage.

`pair_search`'s speed order is by raw effective speed only (item, ability,
self-set weather) -- it does NOT credit a candidate's own priority move for
letting it act before a nominally faster pair. A Bullet Punch user that would
outrun its move priority alone is reported as pinned by anything faster than
its base speed. Flag this to a caller if it matters for their answer; fixing
it means resolving speed PER CANDIDATE MOVE rather than once per candidate,
which this intentionally keeps simple.
"""
import itertools

from combatants import make_combatant
from damage import damage_roll, defensive_stat, effective_stat, hit_count_for, move_from_showdown
from engine import FieldState, effective_speed
from optimize_sets import best_item, best_moveset, legal_items, team_weather_for
from solver import build_moveset


def best_answer(name, merged, moves_db, natures, typechart, target_names):
    """(item, move_names, weather): `name`'s best LEGAL item and moveset
    against `target_names`, via `optimize_sets.best_item`/`best_moveset`.

    `best_item`'s own candidate list (`legal_items`) is not filtered against
    Regulation MB's banned items (Assault Vest, the three Choice items) --
    they are logged in `items_usage` same as everything else, across every
    format. Filtered here the same way `lead_sim.optimised_items` filters it,
    so a search "for the most favourable item" cannot hand back an illegal one.
    """
    from lead_sim import BANNED_ITEMS
    weather = team_weather_for([name], merged)
    item, move_names, _score = best_item(name, merged, moves_db, natures, typechart,
                                         target_names, team_weather=weather)
    if item in BANNED_ITEMS:
        item, move_names = None, None
    if item is None:
        legal = [i for i in (legal_items(name, merged) or []) if i not in BANNED_ITEMS]
        item = legal[0] if legal else None
        move_names, _score = best_moveset(name, merged, moves_db, natures, typechart,
                                          target_names, item=item, team_weather=weather)
    return item, move_names, weather


def _move_infos(name, merged, moves_db, move_names):
    """[MoveInfo, ...] for a chosen move-name list, in the same resolver
    `build_position`/`setup_battle` use (`solver.build_moveset`)."""
    return [mi for mi, _pct in build_moveset(merged[name], moves_db, only_moves=move_names)]


def _lookup_move(name, moves_db):
    """A single MoveInfo by name, even one not in anyone's usage table (e.g. a
    partner's chip move the user names by hand)."""
    key = name.lower().replace(" ", "").replace("-", "").replace("'", "")
    raw = moves_db.get(key)
    return move_from_showdown(raw) if raw else None


def _raw_hit(attacker, move, defender, typechart, weather=None):
    """Worst-roll damage `move` (already on `attacker`, item applied by the
    caller via `make_combatant`) does to `defender`, as a fraction of
    `defender.current_hp` -- so a post-chip defender is scored against what it
    actually has left, not its max. Returns (fraction, type_eff_mult).
    """
    if move.category == "Status" or not move.power:
        return 0.0, 1.0
    physical = move.category == "Physical"
    ak, dk = ("atk", "def") if physical else ("spa", "spd")
    atk = effective_stat(attacker.stats[ak], attacker.stages[ak])
    if attacker.item == "Choice Band" and physical:
        atk *= 1.5
    if attacker.item == "Choice Specs" and not physical:
        atk *= 1.5
    dfn = defensive_stat(defender, dk, move, weather=weather)
    lo, _hi, _avg, eff = damage_roll(50, move.power, atk, dfn, attacker, defender,
                                     move, typechart, weather=weather)
    hits = hit_count_for(move.name, attacker)
    frac = (lo * hits) / (defender.current_hp or 1)
    return frac, eff


def _best_hit(attacker, moves, defender, typechart, weather=None):
    """The best (fraction, move_name) among `moves` against `defender`."""
    best_pct, best_name = 0.0, None
    for mv in moves:
        pct, _eff = _raw_hit(attacker, mv, defender, typechart, weather=weather)
        if pct > best_pct:
            best_pct, best_name = pct, mv.name
    return best_pct, best_name


def threshold_search(pool, target_names, merged, moves_db, natures, typechart,
                     threshold=0.9):
    """Each pool member's best legal item/moveset against `target_names`, and
    the worst-roll % its best move does to EACH target individually.

    Ranked by the WORST (minimum) of those percentages, best first -- the same
    "ranked on the worst" the lead screen uses throughout: a Pokemon that
    OHKOes one of the named targets and whiffs the other has not answered the
    question "beats Kingambit AND Basculegion", it has answered a smaller one.

    Returns rows: {name, item, moves, per_target: {target: (pct, move_name)},
    worst_pct, meets_all}.
    """
    targets = set(target_names)
    rows = []
    for name in pool:
        if name in targets:
            continue
        item, move_names, weather = best_answer(name, merged, moves_db, natures,
                                                 typechart, target_names)
        if not move_names:
            continue
        attacker = make_combatant(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)
        per_target = {}
        for t in target_names:
            defender = make_combatant(t, merged, natures)
            defender.current_hp = defender.max_hp()
            per_target[t] = _best_hit(attacker, moves, defender, typechart,
                                      weather=weather)
        worst_pct = min((p for p, _m in per_target.values()), default=0.0)
        rows.append({"name": name, "item": item, "moves": move_names,
                     "per_target": per_target, "worst_pct": worst_pct,
                     "meets_all": all(p >= threshold for p, _m in per_target.values())})
    rows.sort(key=lambda r: -r["worst_pct"])
    return rows


def chip_then_ko(pool, target_names, partner_name, partner_move_name, merged,
                 moves_db, natures, typechart, partner_item=None):
    """Who finishes each target off after `partner_name`'s `partner_move_name`
    has already landed (worst roll -- the guaranteed chip)?

    `partner_item` defaults to the partner's own best legal item for
    `target_names`, same selection as everything else here; pass one to pin a
    specific set instead (e.g. a Life Orb Ninetales-Alola you already know
    you're bringing).

    Returns rows: {name, item, chip: {target: pct}, finishes:
    {target: (ko: bool, pct, move_name)}, n_ko}.
    """
    if partner_item is None:
        partner_item, _mv, partner_weather = best_answer(
            partner_name, merged, moves_db, natures, typechart, target_names)
    else:
        partner_weather = team_weather_for([partner_name], merged)
    partner = make_combatant(partner_name, merged, natures, item=partner_item)
    p_move = _lookup_move(partner_move_name, moves_db)
    if p_move is None:
        raise ValueError(f"{partner_move_name!r} is not in the move database")

    chip = {}
    for t in target_names:
        defender = make_combatant(t, merged, natures)
        defender.current_hp = defender.max_hp()
        pct, _eff = _raw_hit(partner, p_move, defender, typechart, weather=partner_weather)
        chip[t] = min(1.0, pct)

    rows = []
    for name in pool:
        if name in target_names or name == partner_name:
            continue
        item, move_names, weather = best_answer(name, merged, moves_db, natures,
                                                 typechart, target_names)
        if not move_names:
            continue
        attacker = make_combatant(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)
        finishes = {}
        for t in target_names:
            if chip[t] >= 1.0:
                finishes[t] = (True, 1.0, f"{partner_name}'s {partner_move_name} alone")
                continue
            defender = make_combatant(t, merged, natures)
            defender.current_hp = max(1, round(defender.max_hp() * (1.0 - chip[t])))
            pct, mv_name = _best_hit(attacker, moves, defender, typechart, weather=weather)
            finishes[t] = (pct >= 1.0, pct, mv_name)
        rows.append({"name": name, "item": item, "chip": dict(chip),
                     "finishes": finishes,
                     "n_ko": sum(1 for ko, _p, _m in finishes.values() if ko)})
    rows.sort(key=lambda r: -r["n_ko"])
    return rows


def pair_search(pool, target_names, merged, moves_db, natures, typechart):
    """For each pool member, against EVERY pair drawn from `target_names`:
    does it get to act at all (isn't removed by a faster pair member before
    its own move fires, TAKING DAMAGE IN SPEED ORDER rather than
    simultaneously), and if it acts, which of the pair does its best move KO?

    A candidate is PINNED for a pair when the SUMMED worst-roll damage of
    every pair member faster than it reaches 100% of its HP -- covering both
    a single faster mon that OHKOes it outright and two slower-than-each-other
    attackers that only remove it together.

    Returns rows: {name, item, pairs_pinned, pairs_no_ko, pairs_total, detail}
    where detail is {(e1, e2): {pinned, pre_damage, ko: [(name, move), ...]}}.
    Ranked by fewest pairs pinned, then fewest pairs with no KO -- an answer
    that is never pinned and always KOes something is the top row.
    """
    field = FieldState()
    rows = []
    for name in pool:
        if name in target_names:
            continue
        item, move_names, weather = best_answer(name, merged, moves_db, natures,
                                                 typechart, target_names)
        if not move_names:
            continue
        attacker = make_combatant(name, merged, natures, item=item)
        attacker.current_hp = attacker.max_hp()
        my_speed = effective_speed(attacker, field, "p1")
        moves = _move_infos(name, merged, moves_db, move_names)

        detail = {}
        for e1, e2 in itertools.combinations(target_names, 2):
            enemies = []
            for en in (e1, e2):
                ec = make_combatant(en, merged, natures)
                ec.current_hp = ec.max_hp()
                enemies.append(ec)
            faster = [e for e in enemies if effective_speed(e, field, "p2") > my_speed]

            pre_damage = 0.0
            for e in faster:
                e_moves = [mi for mi, _pct in build_moveset(merged[e.name], moves_db)]
                best_pct, _mv = _best_hit(e, e_moves, attacker, typechart)
                pre_damage += best_pct
            pinned = pre_damage >= 1.0

            ko = []
            if not pinned:
                for en, ec in zip((e1, e2), enemies):
                    pct, mv_name = _best_hit(attacker, moves, ec, typechart, weather=weather)
                    if pct >= 1.0:
                        ko.append((en, mv_name))
            detail[(e1, e2)] = {"pinned": pinned, "pre_damage": pre_damage, "ko": ko}

        rows.append({
            "name": name, "item": item,
            "pairs_pinned": sum(1 for d in detail.values() if d["pinned"]),
            "pairs_no_ko": sum(1 for d in detail.values()
                               if not d["pinned"] and not d["ko"]),
            "pairs_total": len(detail), "detail": detail,
        })
    rows.sort(key=lambda r: (r["pairs_pinned"], r["pairs_no_ko"]))
    return rows
