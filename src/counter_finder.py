"""Search the Pokemon pool for answers to specific threats.

    "using the move selection logic, I want to search for specific pokemon
     that beat others. For instance, I want a pokemon that can OHKO or do 90%
     Kingambit, Basculegion, assuming most favourable item, or maybe a Pokemon
     which can KO a number of selected pokemon after chip from a specified
     partner using a specified move, or vs any pair of the selected Pokemon
     while taking damage according to speed order (sequential game)"

    "You must extend to priority moves. Use average rolls. For a pair to
     work, it must KOd them before it can be KOd by the enemy pair, using
     help from the partner move if specified"

    "make sure to use the mega form, e.g. mega floette has mega floette
     stats" / "I also want to know what the damage roll is, and what the pair
     chip is for each pokemon. For pairs, make sure it's exhaustive over
     permutations, and can include chip from ally. For a spread move such as
     Blizzard, make sure the chip is adjusted correctly (0.75x)"

Five questions, five functions, and none of them re-derives damage or move
choice from scratch:

  threshold_search  -- who OHKOs / clears X% on each of these targets, with
                        its best LEGAL item for the matchup?
  chip_then_ko      -- who finishes these targets off after a named partner's
                        named move has already landed?
  pair_search       -- who KOes one of every PAIR drawn from these targets
                        BEFORE the pair KOes it, in one priority-then-speed
                        ordered turn, with an optional partner assist?
  joint_pair_search -- paired with a NAMED partner (both attacking with their
                        own real set, not one fixed move), who joint-beats
                        every pair drawn from these targets over several
                        turns -- a clean sweep, an out-trade, or robust to
                        the enemy having Tailwind up? See its own docstring;
                        it's `pair_search` generalised from one fixed move to
                        a real second attacker and from one turn to several.
  joint_pool_search -- the same joint fight, but GENERATING both halves of
                        the pair from the pool instead of fixing one --
                        every legal pair, ranked the same way. Shares its
                        detail shape (including the turn-by-turn damage log)
                        with `joint_pair_search` via `_pair_vs_targets`.

Move and item selection is `optimize_sets.best_item`/`best_moveset` -- the
same search `spread_table.py` and the lead screen use, so a Pokemon found here
carries the set it would actually be built with, not a hand-picked one. Damage
is `damage.damage_roll`, read the same way `optimize_sets.move_value_table`
reads it -- not the KO/priority/speed-control-boosted VALUE that function
scores movesets with, which is a ranking score rather than an actual
percentage. Every `Hit` this module returns carries the full lo/avg/hi roll,
not just whichever one a given function searches on, so a caller (or the CLI)
can always show "what the damage roll actually is".

Two different rolls, on purpose. `threshold_search` and `chip_then_ko` answer
"does this GUARANTEE an OHKO/threshold", so they SEARCH on the WORST roll --
the same worst-roll-is-the-guarantee convention `pin.py` uses throughout.
`pair_search` plays out one realistic exchange, not a claim about what's
guaranteed, so it searches on the AVERAGE roll, and orders every actor by
move PRIORITY before speed -- a Sucker Punch or Extreme Speed user acts on
its priority bracket regardless of who is faster, exactly like a real turn.

MEGA FORM. `combatants.make_combatant` builds a "Mega X" pick in its BASE
form -- correct for the full engine, where mega evolution is a send-out event
(`engine.mega_evolve`, applied by `Battle` at the start of the relevant
turn) -- but this module has no Battle to send anything out INTO, and naming
"Mega Floette" as a candidate or a target is asking about the mega form by
construction. `_mega_project` swaps in the mega stats/ability/typing on every
Combatant this module builds itself, unconditionally. It does NOT reach into
`optimize_sets.best_item`/`best_moveset`, which build their OWN internal
combatants for move/item scoring and are left alone here -- a wider, pre-
existing property of that module, out of scope for this fix. That can
occasionally make the OPTIMISER's move/item choice sub-optimal for a Mega
pick (scored partly off base stats); the DAMAGE NUMBERS this module reports
for whatever set comes back are always the mega ones.

SPREAD MOVES. A move that hits multiple Pokemon at once (Blizzard, Earthquake,
...) takes the standard doubles 0.75x penalty whenever it actually lands on
more than one target -- `damage_roll`'s own `num_targets_hit` parameter.
`chip_then_ko` assumes a real doubles field (a second enemy is always present
even if unnamed) and applies it to any spread move unconditionally;
`pair_search` applies it only when BOTH pair members are alive and a spread
move is actually used, since there only a Pokemon's own two moves are ever in
play.

WHAT THIS DOES NOT MODEL. No field: no weather except a combatant's own
instant-set (Drought/Drizzle/Sand Stream/Snow Warning), no Tailwind, no
screens, no Follow Me/Rage Powder redirection, no items played out over a
whole game (Focus Sash survival, Life Orb recoil accumulating). `pair_search`
resolves a single turn, not the two-turn horizon the lead-race tools use, and
an enemy's spread move is never modelled as also hitting the assisting
partner (the partner is assumed not to be a target this turn at all). These
are hypotheses to point the detailed lead-race tools at, in the same division
of labour `lead_scan.py` documents for its own cheap stage.
"""
import copy
import itertools
from dataclasses import dataclass

from combatants import make_combatant
from damage import (WEIGHT_BASED_POWER, damage_roll, defensive_stat, effective_stat,
                    hit_count_for, is_spread_move, move_from_showdown)
from engine import FieldState, effective_speed
from optimize_sets import best_item, best_moveset, legal_items, team_weather_for
from solver import build_moveset


def _mega_project(c):
    """`c` AS IT ACTUALLY APPEARS once mega-evolved, if it is a Mega pick.

    `make_combatant` builds every "Mega X" name in BASE form -- correct for
    the real engine, where mega evolution happens on send-out, but this
    module has no send-out event to wait for, and asking about "Mega Floette"
    at all means asking about the mega form. Applied unconditionally, unlike
    `projection.mega_view` (which checks whether a combatant is currently
    ACTIVE in a real `Battle` -- there is no such battle here).
    """
    if not getattr(c, "is_mega_pick", False) or not c.mega_stats:
        return c
    view = copy.copy(c)
    view.stats = dict(c.mega_stats)
    view.types = list(c.mega_types) if c.mega_types else c.types
    if c.mega_ability:
        view.ability = c.mega_ability
    if c.mega_weight_kg is not None:
        view.weight_kg = c.mega_weight_kg
    view.mega_evolved = True
    return view


def _build(name, merged, natures, item=None):
    """A full-HP Combatant, mega-projected. The one place every Combatant in
    this module is built, so the projection can never be forgotten at a call
    site."""
    c = _mega_project(make_combatant(name, merged, natures, item=item))
    c.current_hp = c.max_hp()
    return c


def best_answer(name, merged, moves_db, natures, typechart, target_names,
                item=None, move_names=None):
    """(item, move_names, weather): `name`'s best LEGAL item and moveset
    against `target_names`, via `optimize_sets.best_item`/`best_moveset` --
    or, "or to just select optimal item" is not the only option, an explicit
    override.

    `item`: pin a specific item instead of searching for the best legal one
    (e.g. Choice Scarf on a Pokemon the search would not have picked it for).
    Checked against Regulation MB's banned list the same as a searched item
    would be -- an override is a decision, not a loophole, so it still has to
    be legal. The moveset is still genuinely re-optimised UNDER whichever
    item ends up in play, pinned or found -- "For Choice Scarf, a pokemon can
    use 4 moves, which could be highly useful": a Choice item locks you into
    the first move you use, not into a single hard-coded one, so its set is
    chosen the same way any other item's is.

    `move_names`: pin the moveset outright too (skips move optimisation
    entirely). Only meaningful together with `item`, since a pinned moveset
    with a searched-for item would be re-deriving the item against moves that
    might not even be the ones the search would have chosen.

    `best_item`'s own candidate list (`legal_items`) is not filtered against
    Regulation MB's banned items (Assault Vest, Choice Band, Choice Specs --
    NOT Choice Scarf, which is legal here) -- they are logged in
    `items_usage` same as everything else, across every format. Filtered
    here the same way `lead_sim.optimised_items` filters it, so a search
    "for the most favourable item" cannot hand back an illegal one.
    """
    from lead_sim import BANNED_ITEMS
    weather = team_weather_for([name], merged)
    if item is not None and item in BANNED_ITEMS:
        raise ValueError(f"{item!r} is not legal in Regulation MB")
    if item is not None and move_names is not None:
        return item, move_names, weather
    if item is not None:
        move_names, _score = best_moveset(name, merged, moves_db, natures, typechart,
                                          target_names, item=item, team_weather=weather)
        return item, move_names, weather

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


@dataclass
class Hit:
    """One move against one defender, with the FULL roll -- not just
    whichever end a search reads. "I also want to know what the damage roll
    is" -- `lo`/`avg`/`hi` are always all three, as fractions of the
    defender's CURRENT hp (so a post-chip defender is scored against what it
    actually has left, not its max); `frac` is whichever of them the caller
    asked `_raw_hit` to search on.
    """
    move_name: str
    frac: float
    lo: float
    avg: float
    hi: float
    eff: float
    num_targets_hit: int = 1


NO_HIT = Hit(move_name=None, frac=0.0, lo=0.0, avg=0.0, hi=0.0, eff=1.0)


def _raw_hit(attacker, move, defender, typechart, weather=None, roll="lo",
            num_targets_hit=1):
    """The full `Hit` `move` (already on `attacker`, item applied by the
    caller via `_build`) does to `defender`.

    `roll`: "lo" (the default) is the WORST roll -- what `threshold_search`
    and `chip_then_ko` search on, because those answer "does this GUARANTEE
    an OHKO/threshold", the same worst-roll-is-the-guarantee convention
    `pin.py` uses throughout. "avg" is the average roll -- what `pair_search`
    searches on, because a sequential exchange is a single realistic line,
    not a claim about what's guaranteed. `Hit.lo`/`.avg`/`.hi` are always all
    three regardless.

    `num_targets_hit`: the doubles multi-target penalty (0.75x whenever this
    is 2, `damage_roll`'s own rule) -- the caller's job to set correctly for
    a spread move actually hitting more than one Pokemon this turn.
    """
    if move.category == "Status":
        return NO_HIT
    if not move.power and move.name not in WEIGHT_BASED_POWER:
        return NO_HIT
    physical = move.category == "Physical"
    ak, dk = ("atk", "def") if physical else ("spa", "spd")
    atk = effective_stat(attacker.stats[ak], attacker.stages[ak])
    if attacker.item == "Choice Band" and physical:
        atk *= 1.5
    if attacker.item == "Choice Specs" and not physical:
        atk *= 1.5
    dfn = defensive_stat(defender, dk, move, weather=weather)
    lo, hi, avg, eff = damage_roll(50, move.power, atk, dfn, attacker, defender,
                                   move, typechart, weather=weather,
                                   num_targets_hit=num_targets_hit)
    hits = hit_count_for(move.name, attacker)
    cur = defender.current_hp or 1
    lo_f, avg_f, hi_f = (lo * hits) / cur, (avg * hits) / cur, (hi * hits) / cur
    frac = avg_f if roll == "avg" else lo_f
    return Hit(move_name=move.name, frac=frac, lo=lo_f, avg=avg_f, hi=hi_f,
              eff=eff, num_targets_hit=num_targets_hit)


def _best_hit(attacker, moves, defender, typechart, weather=None):
    """The best `Hit` among `moves` against `defender`, searched worst-roll
    (see `_raw_hit`). Single-target only -- for a spread-aware search see
    `_choose_move` (average-roll, used by `pair_search`)."""
    best = NO_HIT
    for mv in moves:
        got = _raw_hit(attacker, mv, defender, typechart, weather=weather)
        if got.frac > best.frac:
            best = got
    return best


def _choose_move(attacker, moves, defender, typechart, weather=None):
    """The move `attacker` would actually use against `defender`, searched on
    the AVERAGE roll: prefer one that KOes, and among those prefer higher
    PRIORITY (more likely to land before the target can reply or flee), then
    more damage. Returns a `Hit` (`NO_HIT` if nothing does damage) plus the
    chosen `MoveInfo` (needed by the caller to check `is_spread_move` and to
    read `.priority` for turn ordering).

    This is the move-choice half of `pair_search`'s speed-order resolution --
    a move's priority decides the ACTION order, not just how hard it hits, so
    picking "the biggest number" the way `_best_hit` does would silently
    prefer a slow 100% chunk over a Sucker Punch that secures the same KO
    first, or over-credit a priority option that doesn't actually knock the
    target out.
    """
    best_key, best_hit, best_move = None, NO_HIT, None
    for mv in moves:
        if mv.category == "Status":
            continue
        if not mv.power and mv.name not in WEIGHT_BASED_POWER:
            continue
        got = _raw_hit(attacker, mv, defender, typechart, weather=weather, roll="avg")
        key = (got.frac >= 1.0, mv.priority, got.frac)
        if best_key is None or key > best_key:
            best_key, best_hit, best_move = key, got, mv
    return best_hit, best_move


def _answer_for(name, merged, moves_db, natures, typechart, target_names,
                item_overrides=None, move_overrides=None):
    """`best_answer`, applying this specific `name`'s entry (if any) in
    `item_overrides`/`move_overrides` -- "I also want the option to define
    item ... such as Choice Scarf, or to just select optimal item". Both
    dicts default to "search for the best", per-name, unaffected by an
    override on some OTHER name in the same pool.
    """
    item = (item_overrides or {}).get(name)
    moves = (move_overrides or {}).get(name)
    return best_answer(name, merged, moves_db, natures, typechart, target_names,
                       item=item, move_names=moves)


def _scarf_speed(combatant):
    """`combatant`'s effective speed AS IF it held Choice Scarf -- a
    hypothesis for `threshold_search`'s `outspeed="scarf"` filter,
    independent of whatever item its best-answer search actually chose for
    damage purposes. Pin `--item "Name=Choice Scarf"` yourself if you want a
    row's real displayed set (and damage numbers) to reflect Scarf too --
    this is only "would it be fast enough if it did".
    """
    view = copy.copy(combatant)
    view.item = "Choice Scarf"
    return effective_speed(view, FieldState(), "p1")


def threshold_search(pool, target_names, merged, moves_db, natures, typechart,
                     threshold=0.9, item_overrides=None, move_overrides=None,
                     max_taken=None, outspeed=None):
    """Each pool member's best legal item/moveset against `target_names`, and
    the worst-roll `Hit` its best move lands on EACH target individually.

    Ranked by the WORST (minimum) of those percentages, best first -- the same
    "ranked on the worst" the lead screen uses throughout: a Pokemon that
    OHKOes one of the named targets and whiffs the other has not answered the
    question "beats Kingambit AND Basculegion", it has answered a smaller one.

    `item_overrides`/`move_overrides`: optional {name: item} / {name:
    [move, ...]} pins for specific pool members -- see `_answer_for`.

    `max_taken`/`outspeed`: optional extra requirements, composed with
    `threshold` and each other by AND --

        "my attackers in counter_table must either be faster than the
         enemy, able to be faster with choice scarf, and/or take max X
         damage from the enemy's best attack (e.g., OHKO all, take less
         than 50%, outspeed. or 2HKO all, take less than 33%, outspeed.)"

    `max_taken`: a fraction (0.5 for "under 50%") -- a row only survives if
    EVERY named target's best attack against it, read on THEIR best roll
    (the most they could possibly do, the guaranteed-survival direction --
    the mirror of `threshold` reading OUR worst roll), is under this.
    `outspeed`: `None` (no filter) / `"natural"` (must out-speed every named
    target under its own chosen item) / `"scarf"` (out-speeds naturally OR
    would if it held Choice Scarf instead -- see `_scarf_speed`). A speed
    TIE does not count as outspeeding, matching this module's "ties resolve
    against us" convention elsewhere (`pair_search`'s turn order).

    Both are `None` by default, in which case none of this runs at all --
    same cost and same rows as before either existed. When either is given,
    a row that fails is DROPPED, not merely flagged: "must" was asked for.

    Returns rows: {name, item, moves, per_target: {target: Hit}, worst_pct,
    meets_all}, plus, only when `max_taken`/`outspeed` are given: {incoming:
    {target: Hit}, outspeeds: {target: bool}, outspeeds_scarf: {target:
    bool}}. `incoming[t].frac`/`.hi` are the same number (their best roll);
    `.lo`/`.avg` are their worst/average roll on you, still worth showing.
    """
    targets = set(target_names)
    need_screen = max_taken is not None or outspeed is not None
    # Each named target's own set is searched ONCE (against the whole pool,
    # the same "what does this threat generally run" question `speed_tiers`
    # asks), not once per candidate -- a real Pokemon carries one set, not a
    # different one for every possible opponent it might face.
    target_sets = {}
    if need_screen:
        for t in target_names:
            target_sets[t] = _answer_for(t, merged, moves_db, natures,
                                         typechart, pool)

    rows = []
    for name in pool:
        if name in targets:
            continue
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        attacker = _build(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)
        per_target = {t: _best_hit(attacker, moves, _build(t, merged, natures),
                                   typechart, weather=weather)
                     for t in target_names}
        worst_pct = min((h.frac for h in per_target.values()), default=0.0)
        row = {"name": name, "item": item, "moves": move_names,
              "per_target": per_target, "worst_pct": worst_pct,
              "meets_all": all(h.frac >= threshold
                              for h in per_target.values())}

        if need_screen:
            own_speed = effective_speed(attacker, FieldState(), "p1")
            own_scarf_speed = _scarf_speed(attacker)
            incoming, outspeeds, outspeeds_scarf = {}, {}, {}
            for t in target_names:
                t_item, t_moves, t_weather = target_sets[t]
                if not t_moves:
                    # This named target could not itself be built/optimised
                    # (a data gap, not a real matchup fact) -- do not let it
                    # silently fail every row's filter.
                    incoming[t], outspeeds[t], outspeeds_scarf[t] = NO_HIT, True, True
                    continue
                t_combatant = _build(t, merged, natures, item=t_item)
                t_move_infos = _move_infos(t, merged, moves_db, t_moves)
                got = _best_hit(t_combatant, t_move_infos, attacker, typechart,
                                weather=t_weather)
                incoming[t] = Hit(move_name=got.move_name, frac=got.hi, lo=got.lo,
                                  avg=got.avg, hi=got.hi, eff=got.eff,
                                  num_targets_hit=got.num_targets_hit)
                t_speed = effective_speed(t_combatant, FieldState(), "p1")
                outspeeds[t] = own_speed > t_speed
                outspeeds_scarf[t] = outspeeds[t] or own_scarf_speed > t_speed
            row["incoming"] = incoming
            row["outspeeds"] = outspeeds
            row["outspeeds_scarf"] = outspeeds_scarf

            taken_ok = max_taken is None or all(h.hi <= max_taken
                                                for h in incoming.values())
            if outspeed == "natural":
                speed_ok = all(outspeeds.values())
            elif outspeed == "scarf":
                speed_ok = all(outspeeds_scarf.values())
            else:
                speed_ok = True
            # `threshold`/`meets_all` is informational everywhere else in
            # this function (a row that whiffs one target is still ranked
            # and shown, since "how close" is useful on its own) -- but
            # once a screen is actually requested, "OHKO all, take less
            # than 50%, outspeed" reads as three MUSTS together, not two
            # hard ones next to a soft one.
            if not (taken_ok and speed_ok and row["meets_all"]):
                continue
        rows.append(row)
    rows.sort(key=lambda r: -r["worst_pct"])
    return rows


def speed_tiers(names, target_names, merged, moves_db, natures, typechart,
                item_overrides=None):
    """Real turn order for `names` -- priority bracket first, then effective
    speed under each one's own best legal item (or a pinned one).

        "I also need to see speed tiers, for instance to have an option to
         make sure my guys (accounting for priority like bullet punch)
         outspeed their enemies."

    Priority is a property of a MOVE, not a fixed Pokemon stat, so this does
    not claim a Pokemon always leads with its priority option -- it reports
    the HIGHEST priority among its own chosen ATTACKING moves (Status moves
    excluded: Protect's priority bracket does not describe "outspeeding to
    hit something") as a note, and ranks on it, the same way a real turn
    resolves: everyone in a higher bracket goes before everyone in a lower
    one, and only within the SAME bracket does raw speed decide it.

    Each Pokemon's item/moveset is `_answer_for`'s usual search against
    `target_names` (or an `item_overrides` pin) -- the SAME set the rest of
    this module searches against, so the chart shows the set a Pokemon would
    actually be running for this matchup, not some other hypothetical one.
    Scoring against `names` itself (everyone else in the chart, dozens to
    hundreds of entries for a whole-pool `--speed` run) would be both the
    wrong question ("what beats this specific cast" isn't what's asked) and
    far more expensive -- `optimize_sets`' move/item search cost scales with
    the enemy-name-list length.

    No field: no Tailwind/screens, the same simplification the rest of this
    module makes (see the module docstring) -- a real Tailwind or Trick Room
    would reorder brackets this does not know about.

    Returns rows: {name, item, moves, priority, priority_move, speed},
    sorted by (-priority, -speed).
    """
    rows = []
    for name in names:
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides)
        if not move_names:
            continue
        combatant = _build(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)
        atk_moves = [m for m in moves if m.category != "Status"]
        priority = max((m.priority for m in atk_moves), default=0)
        priority_move = next((m.name for m in atk_moves if m.priority == priority),
                             None)
        speed = effective_speed(combatant, FieldState(), "p1")
        rows.append({"name": name, "item": item, "moves": move_names,
                     "priority": priority, "priority_move": priority_move,
                     "speed": speed})
    rows.sort(key=lambda r: (-r["priority"], -r["speed"]))
    return rows


def chip_then_ko(pool, target_names, partner_name, partner_move_name, merged,
                 moves_db, natures, typechart, partner_item=None,
                 item_overrides=None, move_overrides=None):
    """Who finishes each target off after `partner_name`'s `partner_move_name`
    has already landed (worst roll -- the guaranteed chip)?

    `partner_item` defaults to the partner's own best legal item for
    `target_names`, same selection as everything else here; pass one to pin a
    specific set instead (e.g. a Life Orb Ninetales-Alola you already know
    you're bringing). If the partner's move is a SPREAD move (Blizzard and
    friends), the chip is computed at the doubles 0.75x multi-target penalty
    -- a real field always has a second enemy for it to also be hitting, even
    one not among the named targets.

    `item_overrides`/`move_overrides`: optional {name: item} / {name:
    [move, ...]} pins for specific pool members (the finishers) -- see
    `_answer_for`. Does not affect the partner; use `partner_item` for that.

    Returns rows: {name, item, chip: {target: Hit}, finishes:
    {target: (ko: bool, Hit)}, n_ko}.
    """
    if partner_item is None:
        partner_item, _mv, partner_weather = best_answer(
            partner_name, merged, moves_db, natures, typechart, target_names)
    else:
        partner_weather = team_weather_for([partner_name], merged)
    partner = _build(partner_name, merged, natures, item=partner_item)
    p_move = _lookup_move(partner_move_name, moves_db)
    if p_move is None:
        raise ValueError(f"{partner_move_name!r} is not in the move database")
    n_hit = 2 if is_spread_move(p_move.target) else 1

    chip = {}
    for t in target_names:
        defender = _build(t, merged, natures)
        got = _raw_hit(partner, p_move, defender, typechart, weather=partner_weather,
                       num_targets_hit=n_hit)
        chip[t] = Hit(move_name=got.move_name, frac=min(1.0, got.frac),
                     lo=min(1.0, got.lo), avg=min(1.0, got.avg),
                     hi=min(1.0, got.hi), eff=got.eff, num_targets_hit=n_hit)

    rows = []
    for name in pool:
        if name in target_names or name == partner_name:
            continue
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        attacker = _build(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)
        finishes = {}
        for t in target_names:
            if chip[t].frac >= 1.0:
                already = Hit(move_name=f"{partner_name}'s {partner_move_name} alone",
                              frac=1.0, lo=chip[t].lo, avg=chip[t].avg, hi=chip[t].hi,
                              eff=chip[t].eff)
                finishes[t] = (True, already)
                continue
            defender = _build(t, merged, natures)
            defender.current_hp = max(1, round(defender.max_hp() * (1.0 - chip[t].frac)))
            got = _best_hit(attacker, moves, defender, typechart, weather=weather)
            finishes[t] = (got.frac >= 1.0, got)
        rows.append({"name": name, "item": item, "chip": dict(chip),
                     "finishes": finishes,
                     "n_ko": sum(1 for ko, _h in finishes.values() if ko)})
    rows.sort(key=lambda r: -r["n_ko"])
    return rows


_OUTCOME_RANK = {"clean": 0, "trade": 1, "no_ko": 2, "pinned": 3}


def _hit_or_spread(attacker, mv, intended_role, defenders, typechart,
                   weather=None, roll="avg"):
    """{role: Hit} for whatever `mv` actually damages: every role in
    `defenders` if it's a spread move landing on more than one of them (the
    doubles 0.75x penalty applied via `num_targets_hit`), else just
    `intended_role`. `mv=None` (nothing to hit with) returns {}.
    """
    if mv is None:
        return {}
    if is_spread_move(mv.target) and len(defenders) > 1:
        n = len(defenders)
        return {role: _raw_hit(attacker, mv, d, typechart, weather=weather,
                               roll=roll, num_targets_hit=n)
               for role, d in defenders.items()}
    got = _raw_hit(attacker, mv, defenders[intended_role], typechart,
                   weather=weather, roll=roll)
    return {intended_role: got}


def _sequential_pair_outcome(attacker, atk_moves, e1_name, e1, e1_moves,
                             e2_name, e2, e2_moves, typechart, weather,
                             candidate_target, partner=None, partner_move=None,
                             partner_target=None):
    """One full turn, PRIORITY THEN SPEED order, everyone at AVERAGE rolls,
    `attacker` going for `candidate_target` (with `partner`'s fixed move
    helping, aimed at `partner_target` -- defaults to `candidate_target` --
    unless the move is a spread move, which always hits both regardless of
    "target"). Every actor's move is chosen ONCE against the FULL-health
    version of its target(s) (`_choose_move`), and the resulting %-of-max-HP
    is then subtracted from a running HP fraction as the turn plays out -- the
    same fixed-fraction-per-hit convention `lead_scan`'s own arithmetic race
    used, so a later hit is never priced against a HEALTHIER target than the
    turn has actually left it.

    Ties (equal priority AND equal speed) resolve against the attacker's
    side, matching the rest of this codebase: a plan that needs to win a
    coin flip is a plan with a hole.

    Returns {"outcome": one of "clean"/"trade"/"no_ko"/"pinned", "target",
    "partner_target", "hp_left": {...}, "hits": {role: {tgt_role: Hit}}}.
    "clean" and "trade" both satisfy "KO them before being KO'd" -- the target
    dies either way, from `attacker` alone or with `partner`'s help; "trade"
    only means `attacker` itself also went down later the same turn, to the
    pair member it wasn't aimed at.
    """
    field = FieldState()
    combatants = {"C": attacker, "E1": e1, "E2": e2}
    if partner is not None:
        combatants["P"] = partner
    hp = {k: 1.0 for k in combatants}
    defenders = {"E1": e1, "E2": e2}
    target_role = "E1" if e1_name == candidate_target else "E2"

    plan = {}
    got, mv = _choose_move(attacker, atk_moves, combatants[target_role], typechart,
                           weather=weather)
    plan["C"] = (_hit_or_spread(attacker, mv, target_role, defenders, typechart,
                                weather=weather), mv)
    for role, e, e_moves in (("E1", e1, e1_moves), ("E2", e2, e2_moves)):
        got, mv = _choose_move(e, e_moves, attacker, typechart)
        plan[role] = (_hit_or_spread(e, mv, "C", {"C": attacker}, typechart), mv)
    if partner is not None:
        p_target_role = "E1" if (partner_target or candidate_target) == e1_name else "E2"
        plan["P"] = (_hit_or_spread(partner, partner_move, p_target_role, defenders,
                                    typechart), partner_move)

    def speed_key(role):
        mv = plan[role][1]
        prio = mv.priority if mv is not None else 0
        side = "p1" if role in ("C", "P") else "p2"
        spd = effective_speed(combatants[role], field, side)
        theirs_first = 0 if role in ("E1", "E2") else 1  # ties resolve against us
        return (-prio, -spd, theirs_first)

    order = sorted(plan.keys(), key=speed_key)

    pinned = False
    hits_done = {}
    for role in order:
        if hp[role] <= 0:
            if role == "C":
                pinned = True
            continue
        hits, mv = plan[role]
        if mv is None:
            continue
        hits_done[role] = hits
        for tgt_role, got in hits.items():
            if hp.get(tgt_role, 1.0) <= 0:
                continue
            hp[tgt_role] = max(0.0, hp[tgt_role] - got.frac)

    if hp[target_role] <= 0:
        outcome = "clean" if hp["C"] > 0 else "trade"
    else:
        outcome = "pinned" if pinned else "no_ko"
    return {"outcome": outcome, "target": candidate_target,
           "partner_target": (partner_target or candidate_target) if partner else None,
           "hp_left": {k: round(v, 3) for k, v in hp.items()}, "hits": hits_done}


def pair_search(pool, target_names, merged, moves_db, natures, typechart,
                partner_name=None, partner_move_name=None, partner_item=None,
                item_overrides=None, move_overrides=None):
    """For each pool member, against EVERY pair drawn from `target_names`: a
    full ONE-TURN exchange, in real priority-then-speed order, AVERAGE rolls
    -- does it KO one of the pair before the pair KOes it?

        "You must extend to priority moves. Use average rolls. For a pair to
         work, it must KOd them before it can be KOd by the enemy pair, using
         help from the partner move if specified"

    `partner_name`/`partner_move_name` (both required together): a partner
    that fires the named move to help finish the pair, in its own place in
    the SAME speed/priority order -- not a guaranteed head start. The partner
    is assumed not to be a target itself this turn (the enemies always aim at
    the candidate); this is the same simplification `chip_then_ko` makes
    about its partner's hit landing. If the partner's move is a SPREAD move,
    it hits BOTH pair members at once (doubles 0.75x penalty), same as it
    would for real.

    EXHAUSTIVE OVER PERMUTATIONS: each pair is tried against every
    combination of (which pair member the candidate goes for) x (which pair
    member the partner's move is aimed at, when that choice is even
    meaningful -- a spread partner move always hits both), and the BEST
    outcome is kept. A candidate that can only beat the pair with the right
    target assignment has still beaten the pair.

    `partner_item` pins the partner's item instead of searching for its best
    legal one (same idea as `chip_then_ko`'s). `item_overrides`/
    `move_overrides`: optional {name: item} / {name: [move, ...]} pins for
    specific pool members (the candidates) -- see `_answer_for`.

    Returns rows: {name, item, pairs_clean, pairs_trade, pairs_no_ko,
    pairs_pinned, pairs_total, detail} where detail is {(e1, e2): {outcome,
    target, partner_target, hp_left, hits}} for the choice that was kept.
    Ranked by most (clean + trade) -- pairs actually beaten -- then fewest
    pinned.
    """
    if bool(partner_name) != bool(partner_move_name):
        raise ValueError("partner_name and partner_move_name must be given together")
    partner_move = _lookup_move(partner_move_name, moves_db) if partner_move_name else None
    if partner_move_name and partner_move is None:
        raise ValueError(f"{partner_move_name!r} is not in the move database")
    partner_is_spread = partner_move is not None and is_spread_move(partner_move.target)

    rows = []
    for name in pool:
        if name in target_names or name == partner_name:
            continue
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        attacker = _build(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)

        partner = None
        if partner_name is not None:
            if partner_item is None:
                p_item, _mv, _w = best_answer(partner_name, merged, moves_db, natures,
                                              typechart, target_names)
            else:
                p_item = partner_item
            partner = _build(partner_name, merged, natures, item=p_item)

        detail = {}
        for e1_name, e2_name in itertools.combinations(target_names, 2):
            e1 = _build(e1_name, merged, natures)
            e2 = _build(e2_name, merged, natures)
            e1_moves = [mi for mi, _pct in build_moveset(merged[e1_name], moves_db)]
            e2_moves = [mi for mi, _pct in build_moveset(merged[e2_name], moves_db)]

            partner_target_options = ((None,) if (partner is None or partner_is_spread)
                                      else (e1_name, e2_name))
            best = None
            for c_target in (e1_name, e2_name):
                for p_target in partner_target_options:
                    got = _sequential_pair_outcome(
                        attacker, moves, e1_name, e1, e1_moves, e2_name, e2, e2_moves,
                        typechart, weather, c_target,
                        partner=partner, partner_move=partner_move,
                        partner_target=p_target)
                    if best is None or _OUTCOME_RANK[got["outcome"]] < _OUTCOME_RANK[best["outcome"]]:
                        best = got
            detail[(e1_name, e2_name)] = best

        rows.append({
            "name": name, "item": item,
            "pairs_clean": sum(1 for d in detail.values() if d["outcome"] == "clean"),
            "pairs_trade": sum(1 for d in detail.values() if d["outcome"] == "trade"),
            "pairs_no_ko": sum(1 for d in detail.values() if d["outcome"] == "no_ko"),
            "pairs_pinned": sum(1 for d in detail.values() if d["outcome"] == "pinned"),
            "pairs_total": len(detail), "detail": detail,
        })
    rows.sort(key=lambda r: (-(r["pairs_clean"] + r["pairs_trade"]), r["pairs_pinned"]))
    return rows


# ------------------------------------------------------------------ joint pair


def _choose_action(attacker, moves, live_targets, typechart, weather=None,
                   hinted_target=None):
    """Best (hits: {role: Hit}, MoveInfo) for `attacker` against whichever of
    `live_targets` ({role: Combatant}) it ends up hitting.

    A spread move that's actually live (more than one target still standing)
    always hits EVERY entry in `live_targets` at once (doubles 0.75x penalty,
    via `num_targets_hit`), same rule `_hit_or_spread` applies -- target choice
    doesn't mean anything for it. A single-target move is aimed at
    `hinted_target` if it's one of `live_targets`' keys, else at whichever
    live target the same KO-then-priority-then-damage key `_choose_move` uses
    prefers (used for the enemy side here, which is not given a hint -- see
    `joint_pair_search`'s module note on why only OUR side gets the
    exhaustive-permutation treatment).

    `live_targets` empty (everyone on the other side already fainted) returns
    ({}, None) -- nothing left to hit.
    """
    if not live_targets:
        return {}, None
    best_key, best_hits, best_move = None, {}, None
    n_live = len(live_targets)
    for mv in moves:
        if mv.category == "Status":
            continue
        if not mv.power and mv.name not in WEIGHT_BASED_POWER:
            continue
        if is_spread_move(mv.target) and n_live > 1:
            hits = {role: _raw_hit(attacker, mv, d, typechart, weather=weather,
                                   roll="avg", num_targets_hit=n_live)
                   for role, d in live_targets.items()}
            key = (all(h.frac >= 1.0 for h in hits.values()), mv.priority,
                  sum(h.frac for h in hits.values()))
            if best_key is None or key > best_key:
                best_key, best_hits, best_move = key, hits, mv
            continue
        candidates = ([hinted_target] if hinted_target in live_targets
                      else list(live_targets))
        for role in candidates:
            got = _raw_hit(attacker, mv, live_targets[role], typechart,
                           weather=weather, roll="avg")
            key = (got.frac >= 1.0, mv.priority, got.frac)
            if best_key is None or key > best_key:
                best_key, best_hits, best_move = key, {role: got}, mv
    return best_hits, best_move


def _resolve_turn(combatants, moves_by_role, hp, typechart, weather, our_hints,
                  enemy_speed_mult=1.0):
    """One turn, given OUR target hints ({role: enemy_role_or_None}) -- the
    enemy side chooses independently and greedily (`_choose_action` with no
    hint), same "no coordination" behaviour `_sequential_pair_outcome`
    already gives E1/E2. Does NOT mutate `hp` -- returns a fresh dict, log,
    whether an enemy actually got to act, and which side (if either) was
    fully fainted DURING this turn's resolution (checked after every actor's
    hits land, so a true same-turn mutual wipe is still attributed to
    whichever side went down first in the actual initiative order, not left
    ambiguous).

    `enemy_speed_mult`: `>1.0` for the Tailwind-robustness replay (mirrors
    `engine.effective_speed`'s real `tailwind_p2 *= 2.0` rule) -- applied only
    to the turn-ORDER comparison, the same thing a real Tailwind changes.
    """
    hp = dict(hp)
    ours_live = {r: combatants[r] for r in ("C", "P") if hp[r] > 0}
    theirs_live = {r: combatants[r] for r in ("E1", "E2") if hp[r] > 0}

    plan = {}
    for role, c in ours_live.items():
        plan[role] = _choose_action(c, moves_by_role[role], theirs_live,
                                    typechart, weather=weather,
                                    hinted_target=our_hints.get(role))
    for role, c in theirs_live.items():
        plan[role] = _choose_action(c, moves_by_role[role], ours_live,
                                    typechart, weather=None)

    def speed_key(role):
        mv = plan[role][1]
        prio = mv.priority if mv is not None else 0
        side = "p1" if role in ("C", "P") else "p2"
        spd = effective_speed(combatants[role], FieldState(), side)
        if role in ("E1", "E2"):
            spd *= enemy_speed_mult
        theirs_first = 0 if role in ("E1", "E2") else 1  # ties resolve against us
        return (-prio, -spd, theirs_first)

    order = sorted(plan.keys(), key=speed_key)
    log, enemy_acted, wiped = [], False, None
    for role in order:
        if hp[role] <= 0:
            continue
        hits, mv = plan[role]
        if role in ("E1", "E2") and mv is not None:
            enemy_acted = True
        if mv is None:
            continue
        for tgt_role, got in hits.items():
            if hp.get(tgt_role, 0.0) <= 0:
                continue
            hp[tgt_role] = max(0.0, hp[tgt_role] - got.frac)
            log.append((role, tgt_role, got))
        if wiped is None:
            if hp["E1"] <= 0 and hp["E2"] <= 0:
                wiped = "theirs"
            elif hp["C"] <= 0 and hp["P"] <= 0:
                wiped = "ours"
    return hp, log, enemy_acted, wiped


def _best_turn(combatants, moves_by_role, hp, typechart, weather,
              enemy_speed_mult=1.0):
    """Try every combination of OUR target hints for this turn -- the same
    "exhaustive over permutations, the better outcome is kept" `pair_search`
    already promises, generalised from one candidate (plus an optional
    partner locked to ONE named move) to two full attackers each choosing
    their own move. At most 2x2=4 combinations (fewer once one side is down
    to one live attacker or the other side to one live target), so this stays
    cheap per turn.

    Ranked by (enemies KO'd this turn, -ours KO'd this turn, net fractional
    damage this turn) -- "best FOR US", matching every other joint search in
    this module ranking on the attacker's own outcome, not the enemy's.
    """
    ours_live = [r for r in ("C", "P") if hp[r] > 0]
    theirs_live_roles = [r for r in ("E1", "E2") if hp[r] > 0]
    hint_options = theirs_live_roles or [None]
    best = None
    for combo in itertools.product(hint_options, repeat=max(1, len(ours_live))):
        hints = dict(zip(ours_live, combo))
        new_hp, log, enemy_acted, wiped = _resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, hints,
            enemy_speed_mult=enemy_speed_mult)
        enemies_ko = sum(1 for r in ("E1", "E2") if hp[r] > 0 and new_hp[r] <= 0)
        ours_ko = sum(1 for r in ("C", "P") if hp[r] > 0 and new_hp[r] <= 0)
        dmg_dealt = sum(hp[r] - new_hp[r] for r in ("E1", "E2"))
        dmg_taken = sum(hp[r] - new_hp[r] for r in ("C", "P"))
        key = (enemies_ko, -ours_ko, dmg_dealt - dmg_taken)
        if best is None or key > best[0]:
            best = (key, new_hp, log, enemy_acted, wiped)
        if not ours_live:
            break  # nothing of ours can act -- one combo (the empty one) is all there is
    return best[1], best[2], best[3], best[4]


def _joint_race(combatants, moves_by_role, typechart, weather, turns,
                enemy_speed_mult=1.0):
    """`turns` turns (or fewer, once a side is fully fainted), returns
    (outcome, turns_used, hp, log) -- outcome is "sweep" (both enemies
    fainted before either of them ever got to act), "out_trade" (both
    enemies fainted within the window, ours took hits but didn't faint),
    "loss" (ours fully fainted first), or "no_ko" (window elapsed, neither
    side finished the other).

    `log` is `[turn_hits, ...]`, one entry per turn actually played, each a
    list of `(role, target_role, Hit)` in the order they resolved -- "what
    the damage roll is" for every hit either side landed, not just the win/
    loss classification. Always the NORMAL-speed race's log, even when this
    call is the Tailwind-robustness replay (`enemy_speed_mult=2.0`) -- a
    caller that wants both shows the outcome difference, not two logs to
    reconcile.
    """
    hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
    any_enemy_acted = False
    wiped_side, turns_used = None, 0
    full_log = []
    for turn_i in range(max(1, turns)):
        if (hp["E1"] <= 0 and hp["E2"] <= 0) or (hp["C"] <= 0 and hp["P"] <= 0):
            break
        hp, turn_log, enemy_acted, wiped = _best_turn(
            combatants, moves_by_role, hp, typechart, weather,
            enemy_speed_mult=enemy_speed_mult)
        full_log.append(turn_log)
        any_enemy_acted = any_enemy_acted or enemy_acted
        turns_used = turn_i + 1
        if wiped is not None:
            wiped_side = wiped
            break
    theirs_alive = hp["E1"] > 0 or hp["E2"] > 0
    ours_alive = hp["C"] > 0 or hp["P"] > 0
    if wiped_side == "theirs" or (not theirs_alive and ours_alive):
        outcome = "sweep" if not any_enemy_acted else "out_trade"
    elif wiped_side == "ours" or not ours_alive:
        outcome = "loss"
    else:
        outcome = "no_ko"
    return outcome, turns_used, hp, full_log


def _build_enemy_pairs(target_names, merged, natures, moves_db):
    """{name: (Combatant, [MoveInfo, ...])}, built ONCE -- every candidate
    (or candidate pair) evaluated against `target_names` reuses these instead
    of rebuilding the same enemy Combatants on every row, which is what both
    joint search functions below used to do."""
    return {name: (_build(name, merged, natures),
                   [mi for mi, _pct in build_moveset(merged[name], moves_db)])
           for name in target_names}


def _pair_vs_targets(c1, m1, c2, m2, target_names, enemy_built, typechart,
                     weather, turns):
    """(detail, summary) for OUR pair ((c1,m1), (c2,m2)) against every pair
    drawn from `target_names` -- the one place a joint pair is actually
    raced, so `joint_pair_search` (partner fixed) and `joint_pool_search`
    (both slots searched) can never drift apart on what "beats" means.

    `detail`: {(e1, e2): {outcome, turns_used, tailwind_outcome,
    tailwind_safe, log}}. `log` is `_joint_race`'s own per-turn hit list --
    "what the damage roll is", not just the win/loss classification -- for
    the NORMAL-speed race (the one that decided `outcome`); the Tailwind
    replay's log isn't kept, only whether it still won.
    """
    detail = {}
    for e1_name, e2_name in itertools.combinations(target_names, 2):
        e1c, e1m = enemy_built[e1_name]
        e2c, e2m = enemy_built[e2_name]
        combatants = {"C": c1, "P": c2, "E1": e1c, "E2": e2c}
        moves_by_role = {"C": m1, "P": m2, "E1": e1m, "E2": e2m}
        outcome, turns_used, _hp, log = _joint_race(
            combatants, moves_by_role, typechart, weather, turns)
        tw_outcome, _tw_turns, _tw_hp, _tw_log = _joint_race(
            combatants, moves_by_role, typechart, weather, turns,
            enemy_speed_mult=2.0)
        tailwind_safe = tw_outcome in ("sweep", "out_trade")
        detail[(e1_name, e2_name)] = {
            "outcome": outcome, "turns_used": turns_used,
            "tailwind_outcome": tw_outcome, "tailwind_safe": tailwind_safe,
            "log": log,
        }
    summary = {
        "pairs_swept": sum(1 for d in detail.values() if d["outcome"] == "sweep"),
        "pairs_traded": sum(1 for d in detail.values() if d["outcome"] == "out_trade"),
        "pairs_lost": sum(1 for d in detail.values() if d["outcome"] == "loss"),
        "pairs_no_ko": sum(1 for d in detail.values() if d["outcome"] == "no_ko"),
        "pairs_tailwind_safe": sum(1 for d in detail.values() if d["tailwind_safe"]),
        "pairs_total": len(detail),
    }
    return detail, summary


def joint_pair_search(pool, target_names, partner_name, merged, moves_db,
                      natures, typechart, turns=2, partner_item=None,
                      item_overrides=None, move_overrides=None):
    """Paired with `partner_name` (a fixed second attacker, both using their
    own real optimised set -- not one fixed move), for each pool member:
    against every pair drawn from `target_names`, does the joint pair beat
    it?

        "against a given enemy pair, my pair either out trade all possible
         enemy pairs to a win (including spread damage ...), outspeed and ko
         before either of mine fail, or ... do not get OHKOd by any under
         enemy tailwind"

    `pair_search` already answers a narrower version of this (one candidate,
    an optional partner locked to ONE named move, one turn); this is the same
    machinery -- `_build`, `_raw_hit`, mega projection, the doubles 0.75x rule
    -- generalised to a real second attacker and to a multi-turn race
    (`_joint_race`), still on `counter_finder.py`'s own cheap arithmetic
    model rather than `Battle.run_turn`: a full pool search needs to stay
    cheap across up to ~270 candidates, the same reason `pair_search` never
    called into the real engine either.

    Both the candidate's and the partner's item/moveset are optimised ONCE
    against the whole `target_names` list (same convention `pair_search`/
    `chip_then_ko` use -- a Pokemon carries one set, not a different one per
    specific enemy pair). `partner_item` pins the partner's item instead of
    searching for its best legal one. `item_overrides`/`move_overrides`:
    optional {name: item} / {name: [move, ...]} pins for pool members (the
    candidates) -- see `_answer_for`.

    TAILWIND ROBUSTNESS: every enemy pair is also raced with the enemy side's
    effective speed doubled throughout (mirroring `engine.effective_speed`'s
    real `tailwind_p2 *= 2.0` rule) -- a hypothesis in the same spirit as
    `threshold_search`'s existing `outspeed="scarf"`, not contingent on
    either named enemy actually knowing Tailwind. `tailwind_safe` is True
    when that re-run's outcome is STILL `sweep` or `out_trade`.

    Returns rows: {name, item, pairs_swept, pairs_traded, pairs_lost,
    pairs_no_ko, pairs_tailwind_safe, pairs_total, detail} -- `detail` is
    `_pair_vs_targets`'s own shape, damage log included. Ranked by
    (swept + traded) first, then tailwind_safe count -- mirrors
    `pair_search`'s existing `(clean+trade, -pinned)` sort.
    """
    partner_item, partner_move_names, partner_weather = best_answer(
        partner_name, merged, moves_db, natures, typechart, target_names,
        item=partner_item)
    partner = _build(partner_name, merged, natures, item=partner_item)
    partner_moves = _move_infos(partner_name, merged, moves_db, partner_move_names)
    enemy_built = _build_enemy_pairs(target_names, merged, natures, moves_db)

    rows = []
    for name in pool:
        if name in target_names or name == partner_name:
            continue
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        attacker = _build(name, merged, natures, item=item)
        moves = _move_infos(name, merged, moves_db, move_names)
        # Both sides' weather is whichever of ours is doing the setting, same
        # simplification `pair_search` makes -- the enemy's own instant-set
        # ability is not modelled here either (see the module docstring).
        race_weather = weather or partner_weather

        detail, summary = _pair_vs_targets(
            attacker, moves, partner, partner_moves, target_names,
            enemy_built, typechart, race_weather, turns)
        rows.append({"name": name, "item": item, "detail": detail, **summary})
    rows.sort(key=lambda r: (-(r["pairs_swept"] + r["pairs_traded"]),
                             -r["pairs_tailwind_safe"]))
    return rows


def joint_pool_search(pool, target_names, merged, moves_db, natures,
                      typechart, turns=2, item_overrides=None,
                      move_overrides=None):
    """GENERATE the pair, not just search a second member for a named
    partner: every legal pair drawn from `pool`, both members' item/moveset
    genuinely searched (not one fixed), against every pair drawn from
    `target_names`.

        "I want it to generate my pair, i.e., mine and partner"

    The expensive part -- `optimize_sets.best_item`/`best_moveset` -- is
    still paid ONCE per pool member (`_answer_for`), same as everywhere else
    in this module; what scales with the pool is the cheap arithmetic race
    itself, C(pool, 2) of them. MEASURED: ~6ms per our-pair per 3 named
    targets (3 enemy pairs, 2 races each, tailwind included) -- a
    `--pool-size 80` run (3160 our-pairs) is under 20s, but the FULL
    ~270-Pokemon default is minutes, not seconds, unlike every other search
    in this module. Narrow with `--pool-size` for anything but a quick check.

    Returns rows: {pair: (name1, name2), item1, item2, pairs_swept,
    pairs_traded, pairs_lost, pairs_no_ko, pairs_tailwind_safe, pairs_total,
    detail} -- `detail` is `_pair_vs_targets`'s own shape (damage log
    included), ranked the same way `joint_pair_search` ranks.
    """
    built = {}
    for name in pool:
        if name in target_names:
            continue
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        built[name] = (_build(name, merged, natures, item=item),
                       _move_infos(name, merged, moves_db, move_names),
                       weather, item)
    enemy_built = _build_enemy_pairs(target_names, merged, natures, moves_db)

    rows = []
    for n1, n2 in itertools.combinations(built, 2):
        c1, m1, w1, item1 = built[n1]
        c2, m2, w2, item2 = built[n2]
        race_weather = w1 or w2
        detail, summary = _pair_vs_targets(c1, m1, c2, m2, target_names,
                                           enemy_built, typechart,
                                           race_weather, turns)
        rows.append({"pair": (n1, n2), "item1": item1, "item2": item2,
                    "detail": detail, **summary})
    rows.sort(key=lambda r: (-(r["pairs_swept"] + r["pairs_traded"]),
                             -r["pairs_tailwind_safe"]))
    return rows
