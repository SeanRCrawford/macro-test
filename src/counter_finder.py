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

Seven questions, seven functions, and none of them re-derives damage or move
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
  deep_dive         -- the full report for ONE named, already-chosen pair:
                        every enemy pair (pass a whole 6-name roster for
                        "all 15 leads"), the 2x2 damage grid both directions,
                        which enemy hits are outright OHKO risks on their
                        best roll, and the turn-by-turn line each one
                        collapses into.
  switch_in_search  -- for a pair that LOSES a specific enemy pair, which
                        bench candidate switching in for which of ours turns
                        it around -- ranked by least damage taken on the
                        switch-in turn, among the candidates that actually
                        fix the loss.

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
"Mega Floette" as a candidate or a target (ALONE, not as half of a pair --
see ONLY ONE MEGA PER SIDE below) is asking about the mega form by
construction. `_mega_project` swaps in the mega stats/ability/typing for
such a Combatant, unconditionally. It does NOT reach into
`optimize_sets.best_item`/`best_moveset`, which build their OWN internal
combatants for move/item scoring and are left alone here -- a wider, pre-
existing property of that module, out of scope for this fix. That can
occasionally make the OPTIMISER's move/item choice sub-optimal for a Mega
pick (scored partly off base stats); the DAMAGE NUMBERS this module reports
for whatever set comes back are always the mega ones (or the base ones, on
whichever side of a pair a search decided should stay base).

ONLY ONE MEGA PER SIDE. A real side may bring two Mega-capable picks, but
only one may actually Mega Evolve per game -- the other plays the whole
match in base form. The 2v2-board functions (the same five WEATHER lists
below) never evaluate a pair with BOTH members mega'd at once:
`_mega_choices`/`_build_forms`/`_resolve_forms` enumerate every legal
assignment (each Mega name transforming, or nobody transforming) and
`_pair_vs_targets`/`pair_search`/`switch_in_search` search it exactly like
target assignment already was -- OURS for whichever gives us the best
result (we know their team at preview), the ENEMY PAIR's for whichever is
WORST for us, mirroring `matchup_search.play_out_worst_case`'s minimax.
Unlike `species_data.mega_variants` (which treats a SOLE Mega-capable pick
as having no real choice and always evolves it), this module always offers
"stays base" too, even for a lone Mega pick: a forced-base Pokemon keeps
its OWN base ability and typing (Gyarados's Intimidate and Water/Flying,
not Mold Breaker and Water/Dark) for every damage/speed calculation that
follows, which is exactly the per-matchup trade-off "should this one
actually transform" is asking about. Actual Intimidate STAT-DROP
simulation stays out of scope -- this module tracks no stat stages for
anyone, Mega-related or not -- only which ability/typing is active is
guaranteed correct. `threshold_search`/`chip_then_ko`/`speed_tiers`
evaluate every name alone (no shared pair to search over), so they keep
the old unconditional-mega behaviour.

SPREAD MOVES. A move that hits multiple Pokemon at once (Blizzard, Earthquake,
...) takes the standard doubles 0.75x penalty whenever it actually lands on
more than one target -- `damage_roll`'s own `num_targets_hit` parameter.
`chip_then_ko` assumes a real doubles field (a second enemy is always present
even if unnamed) and applies it to any spread move unconditionally;
`pair_search` applies it only when BOTH pair members are alive and a spread
move is actually used, since there only a Pokemon's own two moves are ever in
play.

WHAT THIS DOES NOT MODEL. No screens, no Follow Me/Rage Powder redirection,
no Life Orb recoil accumulating. `pair_search` resolves a single turn, not
the two-turn horizon the lead-race tools use, and an enemy's spread move is
never modelled as also hitting the assisting partner (the partner is
assumed not to be a target this turn at all). These are hypotheses to point
the detailed lead-race tools at, in the same division of labour
`lead_scan.py` documents for its own cheap stage.

Focus Sash / Sturdy survival at 1 HP from full HP IS modelled, but only in
the multi-turn joint-race functions (`_resolve_turn`, hence
`joint_pair_search`/`joint_pool_search`/`deep_dive`/`switch_in_search`) --
`pair_search`'s own single-turn resolution predates that and still assumes
no items played out.

WEATHER. The real board has exactly ONE field weather, set by whichever
weather ability actually resolves (either side's), and it applies to
EVERYONE's damage and speed, not just its setter's own side. The 2v2-board
functions -- `pair_search` (via `_sequential_pair_outcome`), `joint_pair_search`,
`joint_pool_search`, `deep_dive`, `switch_in_search` -- model this correctly:
`_field_weather(combatants)` looks at every combatant actually on the board
(both named enemies, the candidate, and the partner when there is one) and
picks whichever weather-setting ability is present, so e.g. a Mega
Charizard Y opponent's sun applies uncontested -- to both sides' damage AND
turn order (Swift Swim/Chlorophyll/Sand Rush/Slush Rush included) -- even
when neither of ours sets anything. `threshold_search`/`chip_then_ko`/
`speed_tiers` are NOT board functions in this sense (each checks a
candidate against named targets one at a time, never assuming a specific
enemy pairing), so they stay scoped to the candidate's/named-enemy's own
weather-setting usage rather than a shared 2v2 field -- there is no single
"the board" to derive one from.
"""
import copy
import itertools
from dataclasses import dataclass

from combatants import make_combatant
from damage import (WEIGHT_BASED_POWER, MoveInfo, damage_roll, defensive_stat, effective_stat,
                    hit_count_for, is_spread_move, move_from_showdown)
from engine import FieldState, WEATHER_SETTERS, effective_speed
from optimize_sets import best_item, best_moveset, legal_items, team_weather_for
from solver import build_moveset
from species_data import NO_MEGA, resolve_team_mega_slot


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
    site.

    Only correct for a `name` evaluated ALONE (`threshold_search`/
    `chip_then_ko`/`speed_tiers`, and enemies named one at a time outside a
    shared 2v2 board) -- a PAIR (ours or the enemy's) can legally have at
    most one Mega Evolution between its two members, so a board function
    must go through `_build_forms`/`_resolve_forms` instead. See those for
    why."""
    return _build_form(name, merged, natures, item=item, stay_base=False)


def _build_form(name, merged, natures, item=None, stay_base=False):
    """One Combatant in a SPECIFIC form. `stay_base=True` mirrors
    `combatants.make_team`'s `force_base_form` -- base stats/types/ability
    (e.g. Gyarados keeps Intimidate instead of gaining Mold Breaker), still
    holding whatever item it would otherwise (a Mega Stone pick still holds
    its stone even when forced to stay base -- "brought it, didn't use it").
    `stay_base=False` projects it into mega form immediately (`_mega_project`
    is a no-op for a `force_base_form=True` combatant already, since
    `is_mega_pick` comes back False for one -- there is nothing to
    conditionally skip).
    """
    c = _mega_project(make_combatant(name, merged, natures, item=item,
                                     force_base_form=stay_base))
    c.current_hp = c.max_hp()
    return c


def _mega_choices(names_pair):
    """Every legal `mega_transforms` value for this pair -- who (if anyone)
    actually Mega Evolves, subject to VGC's real "at most one Mega Evolution
    per side per game" rule.

        "Only one can mega. Both in a pair can be a potential Mega, but vs
         each enemy pair only one can choose to become the Mega, the other
         will stay as base form -- this can be favourable, such as Gyarados
         keeping Water/Flying type rather than choosing to switch to
         Water/Dark. Account for factors like intimidate too. This is also
         true for opponents."

    Unlike `species_data.mega_variants` (which treats a SOLE Mega-capable
    pick as having no real choice to search, and always evolves it -- the
    right default for the rest of the codebase's minimax, where searching a
    non-choice would be pure waste), this module always offers "nobody
    Mega Evolves" too, even for a lone Mega-capable member: staying base
    keeps that Pokemon's OWN base ability and typing (Gyarados's Intimidate,
    Water/Flying) instead of the Mega's (Mold Breaker, Water/Dark), and
    whether that is actually better is exactly the per-matchup question
    being asked here, not a fixed property of the team. Actual Intimidate
    stat-drop simulation is still out of scope for this module -- it tracks
    no stat stages for anyone, Mega-related or not -- but which ABILITY is
    active (for typing and for Mold Breaker's ignore-the-target's-ability
    effect) is correct either way once the right form is built.

    Returns `[None]` when neither name is Mega-capable (nothing to search),
    else each Mega name present plus `species_data.NO_MEGA` (nobody
    transforms) -- every value `species_data.resolve_team_mega_slot` accepts.
    """
    megas = [n for n in names_pair if n.startswith("Mega ")]
    if not megas:
        return [None]
    return megas + [NO_MEGA]


def _build_forms(names, merged, natures, moves_db, items=None):
    """{name: {"mega": Combatant, "base": Combatant, "moves": [MoveInfo,...]}}
    for every name in `names`, built ONCE regardless of how many pairs drawn
    from `names` end up using it (`make_combatant`'s own template cache
    already makes a second build of the same (name, form) pair cheap, so
    there is no separate cache to maintain here). "mega" and "base" are
    the SAME Combatant's stats for a non-Mega-capable name (nothing to
    choose), so `_resolve_forms` never needs a special case for "this name
    can't Mega Evolve anyway".

    Shared by BOTH our own pair and the enemy pair -- a pool search's
    per-pair mega-choice resolution never has two different code paths for
    "ours" vs "theirs".
    """
    items = items or {}
    out = {}
    for name in names:
        mvs = [mi for mi, _pct in build_moveset(merged[name], moves_db)]
        out[name] = {
            "mega": _build_form(name, merged, natures, item=items.get(name),
                               stay_base=False),
            "base": _build_form(name, merged, natures, item=items.get(name),
                               stay_base=True),
            "moves": mvs,
        }
    return out


def _resolve_forms(names, built):
    """For `names` (one candidate alone, or a real pair) drawn from `built`
    (a `_build_forms` dict), every legal way to honour "only one Mega per
    side" -- yields (mega_transforms, [combatant_for_each_name]) for each of
    `_mega_choices(names)`, in the same order as `names`. A single-name list
    still works (`_mega_choices` only ever looks for "Mega "-prefixed
    entries, regardless of how many names it's given) -- `pair_search` uses
    that for a candidate with no partner.
    """
    for mt in _mega_choices(names):
        _evolves, forced_base = resolve_team_mega_slot(list(names),
                                                        mega_transforms=mt)
        cs = [built[n]["base" if n in forced_base else "mega"] for n in names]
        yield mt, cs


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


def _field_weather(combatants):
    """The ONE shared field weather for a 2v2 board -- checked against
    EVERY combatant's ability (both sides), not just "ours".

        "make sure weather is accounted for, such as Mega Charizard Y's sun
         applying uncontested if neither of your brings set weather"

    Before this, `pair_search`/the joint searches only ever asked "what does
    OUR candidate's own `_answer_for` call say" (a single Pokemon's usage-
    weather guess, via `optimize_sets.team_weather_for`), and applied that
    number to OUR attacks only -- an enemy Drought/Drizzle/Sand Stream/Snow
    Warning never came up at all, for either side's damage, and a weather-
    speed-boost ability (Swift Swim, Chlorophyll, Sand Rush, Slush Rush)
    never applied to anyone's turn order either, since the speed-order code
    built its own bare, weatherless `FieldState()` on every call.

    `combatants`: an ordered mapping ("C" then "P" then "E1" then "E2", same
    convention every joint function already builds) -- resolved by applying
    each entry's weather-setting ability IN THAT ORDER, later ones
    overwriting earlier ones exactly like `battle.py`'s own opening
    switch-in resolution does (`Battle.__init__` calls `on_switch_in` for
    p1's two actives then p2's two, in that fixed order, each new distinct
    weather overwriting the last) -- this module has no real send-out event
    to order by speed, so it mirrors the real engine's own actual
    tie-break rather than inventing a different one. `None` (no setter
    anywhere) if nothing in `combatants` has one.
    """
    weather = None
    for c in combatants.values():
        if c is not None and c.ability in WEATHER_SETTERS:
            weather = WEATHER_SETTERS[c.ability]
    return weather


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

# `_joint_race`'s own outcome vocabulary -- lower is better for us, same
# convention as `_OUTCOME_RANK` above. Used to pick our own best mega
# choice (minimise) and the enemy's worst-for-us one (maximise) in
# `_pair_vs_targets`/`pair_search`/`switch_in_search`.
_JOINT_OUTCOME_RANK = {"sweep": 0, "out_trade": 1, "no_ko": 2, "loss": 3}


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
                             e2_name, e2, e2_moves, typechart,
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

    Field weather is resolved from EVERY combatant actually on this board
    (`_field_weather`) -- candidate, partner (if any), and both named
    enemies -- not just the candidate's own usage guess, so an enemy
    Drought/Drizzle/Sand Stream/Snow Warning applies (to BOTH sides'
    damage and turn order) even when neither of ours sets anything.

    Returns {"outcome": one of "clean"/"trade"/"no_ko"/"pinned", "target",
    "partner_target", "hp_left": {...}, "hits": {role: {tgt_role: Hit}}}.
    "clean" and "trade" both satisfy "KO them before being KO'd" -- the target
    dies either way, from `attacker` alone or with `partner`'s help; "trade"
    only means `attacker` itself also went down later the same turn, to the
    pair member it wasn't aimed at.
    """
    combatants = {"C": attacker, "E1": e1, "E2": e2}
    if partner is not None:
        combatants["P"] = partner
    weather = _field_weather(combatants)
    field = FieldState(weather=weather)
    hp = {k: 1.0 for k in combatants}
    defenders = {"E1": e1, "E2": e2}
    target_role = "E1" if e1_name == candidate_target else "E2"

    plan = {}
    got, mv = _choose_move(attacker, atk_moves, combatants[target_role], typechart,
                           weather=weather)
    plan["C"] = (_hit_or_spread(attacker, mv, target_role, defenders, typechart,
                                weather=weather), mv)
    for role, e, e_moves in (("E1", e1, e1_moves), ("E2", e2, e2_moves)):
        got, mv = _choose_move(e, e_moves, attacker, typechart, weather=weather)
        plan[role] = (_hit_or_spread(e, mv, "C", {"C": attacker}, typechart,
                                     weather=weather), mv)
    if partner is not None:
        p_target_role = "E1" if (partner_target or candidate_target) == e1_name else "E2"
        plan["P"] = (_hit_or_spread(partner, partner_move, p_target_role, defenders,
                                    typechart, weather=weather), partner_move)

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

    ONLY ONE MEGA PER SIDE. When the candidate AND its partner are both
    Mega-capable (or even just one of them, since staying base is always a
    legal choice too -- see `_mega_choices`), every legal assignment of
    "who, if anyone, actually Mega Evolves" is tried and whichever gives US
    the best outcome is kept -- we know their team at preview, so we choose
    accordingly. The SAME is true of the named enemy pair: every legal
    assignment on their side is tried too, and whichever is WORST for us is
    assumed (mirroring `matchup_search.play_out_worst_case`'s minimax: we
    pick our best response, they pick their best against us). A forced-base
    pick keeps its own base ability and typing (e.g. Gyarados's Intimidate
    and Water/Flying, instead of Mold Breaker and Water/Dark) for every one
    of these combinations, exactly as `_build_forms`/`_resolve_forms`
    resolve it.

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

    enemy_forms = _build_forms(target_names, merged, natures, moves_db)

    rows = []
    for name in pool:
        if name in target_names or name == partner_name:
            continue
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        moves = _move_infos(name, merged, moves_db, move_names)

        our_names = [name]
        our_items = {name: item}
        if partner_name is not None:
            if partner_item is None:
                p_item, _mv, _w = best_answer(partner_name, merged, moves_db, natures,
                                              typechart, target_names)
            else:
                p_item = partner_item
            our_names.append(partner_name)
            our_items[partner_name] = p_item
        our_forms = _build_forms(our_names, merged, natures, moves_db, items=our_items)

        detail = {}
        for e1_name, e2_name in itertools.combinations(target_names, 2):
            e1_moves = enemy_forms[e1_name]["moves"]
            e2_moves = enemy_forms[e2_name]["moves"]

            partner_target_options = ((None,) if (partner_name is None or partner_is_spread)
                                      else (e1_name, e2_name))
            # Minimax, outer to inner: WE pick our mega choice for the best
            # result (outer); THEY pick their mega choice for the worst
            # result against us (middle); WE pick our target assignment for
            # the best result (inner) -- both target and mega choice are
            # OUR calls, so both minimise, only the enemy's mega choice
            # maximises.
            best = None
            for _our_mt, our_cs in _resolve_forms(our_names, our_forms):
                attacker = our_cs[0]
                partner = our_cs[1] if partner_name is not None else None
                worst = None
                for _enemy_mt, (e1, e2) in _resolve_forms((e1_name, e2_name), enemy_forms):
                    best_targets = None
                    for c_target in (e1_name, e2_name):
                        for p_target in partner_target_options:
                            got = _sequential_pair_outcome(
                                attacker, moves, e1_name, e1, e1_moves, e2_name, e2, e2_moves,
                                typechart, c_target,
                                partner=partner, partner_move=partner_move,
                                partner_target=p_target)
                            if (best_targets is None or
                                    _OUTCOME_RANK[got["outcome"]] < _OUTCOME_RANK[best_targets["outcome"]]):
                                best_targets = got
                    if worst is None or _OUTCOME_RANK[best_targets["outcome"]] > _OUTCOME_RANK[worst["outcome"]]:
                        worst = best_targets
                if best is None or _OUTCOME_RANK[worst["outcome"]] < _OUTCOME_RANK[best["outcome"]]:
                    best = worst
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


# A no-op stand-in for "this role used Protect this turn" -- `_resolve_turn`
# substitutes this for a protected enemy role instead of calling
# `_choose_action`, so it still counts as an action (priority 4, real speed
# order effects) but lands no hit. Not a real move lookup (Protect isn't in
# any moveset here) -- just enough of a `MoveInfo` for `speed_key`/the
# enemy-acted bookkeeping to treat it like any other move.
_PROTECT_MOVE = MoveInfo("Protect", 0, "Normal", "Status", "self", priority=4)


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
                  enemy_speed_mult=1.0, protected_roles=frozenset()):
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

    `protected_roles`: enemy roles ("E1"/"E2") that use Protect THIS turn
    instead of whatever `_choose_action` would have picked -- `_PROTECT_MOVE`
    is substituted directly (still a real action, at priority 4, so it still
    goes first in `speed_key` and still counts toward `enemy_acted`) and any
    hit aimed at that role this turn is dropped rather than applied, exactly
    like a real Protect block. This is what lets `_best_turn`'s existing
    exhaustive hint search learn -- with no change to ITS own logic -- not to
    waste an attack on a protected enemy: a hint combo that targets a
    protected role simply scores no KO and no damage for that hit, so a combo
    that targets the other, unprotected enemy naturally ranks higher.

    `weather` is the ONE shared field value (`_field_weather`'s own return)
    -- applied to BOTH sides' damage (a Fire move is sun-boosted no matter
    which side casts it) and, via a real `FieldState(weather=weather)`
    rather than a bare one, to BOTH sides' turn order too (Swift Swim/
    Chlorophyll/Sand Rush/Slush Rush all key off the field, not off who
    happens to be attacking).
    """
    hp = dict(hp)
    ours_live = {r: combatants[r] for r in ("C", "P") if hp[r] > 0}
    theirs_live = {r: combatants[r] for r in ("E1", "E2") if hp[r] > 0}
    field = FieldState(weather=weather)

    plan = {}
    for role, c in ours_live.items():
        plan[role] = _choose_action(c, moves_by_role[role], theirs_live,
                                    typechart, weather=weather,
                                    hinted_target=our_hints.get(role))
    for role, c in theirs_live.items():
        if role in protected_roles:
            plan[role] = ({}, _PROTECT_MOVE)
        else:
            plan[role] = _choose_action(c, moves_by_role[role], ours_live,
                                        typechart, weather=weather)

    def speed_key(role):
        mv = plan[role][1]
        prio = mv.priority if mv is not None else 0
        side = "p1" if role in ("C", "P") else "p2"
        spd = effective_speed(combatants[role], field, side)
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
            if tgt_role in protected_roles:
                continue  # Protect blocks this hit entirely
            if hp.get(tgt_role, 0.0) <= 0:
                continue
            target_c = combatants[tgt_role]
            new_hp = hp[tgt_role] - got.frac
            # Focus Sash / Sturdy: survive a would-be KO at 1 HP, but only
            # from FULL HP -- mirrors `battle.py`'s own rule exactly
            # (`target.current_hp == target.max_hp()`). Checked against
            # `hp[tgt_role]` (still at 1.0, untouched) rather than a
            # separate "already used" flag, and never written back onto the
            # shared `Combatant` (`target_c.item` stays whatever it was) --
            # these objects are reused across every replay `_pair_vs_targets`
            # runs (normal, tailwind, Protect x2), so mutating the real item
            # here would silently consume it in hypotheses that never
            # actually happened. Once knocked down to that sliver, `hp[
            # tgt_role]` is no longer 1.0, so a second lethal hit this same
            # race correctly finishes it off instead of re-triggering.
            if (new_hp <= 0 and hp[tgt_role] >= 1.0 and target_c.max_hp()
                    and (target_c.item == "Focus Sash" or target_c.ability == "Sturdy")):
                new_hp = 1.0 / target_c.max_hp()
            hp[tgt_role] = max(0.0, new_hp)
            log.append((role, tgt_role, got))
        if wiped is None:
            if hp["E1"] <= 0 and hp["E2"] <= 0:
                wiped = "theirs"
            elif hp["C"] <= 0 and hp["P"] <= 0:
                wiped = "ours"
    return hp, log, enemy_acted, wiped


def _best_turn(combatants, moves_by_role, hp, typechart, weather,
              enemy_speed_mult=1.0, protected_roles=frozenset()):
    """Try every combination of OUR target hints for this turn -- the same
    "exhaustive over permutations, the better outcome is kept" `pair_search`
    already promises, generalised from one candidate (plus an optional
    partner locked to ONE named move) to two full attackers each choosing
    their own move. At most 2x2=4 combinations (fewer once one side is down
    to one live attacker or the other side to one live target), so this stays
    cheap per turn.

    `protected_roles` is passed straight through to `_resolve_turn` -- see
    its own docstring for how a protected enemy role naturally falls out of
    OUR side's ranking here with no change to the ranking itself.

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
            enemy_speed_mult=enemy_speed_mult, protected_roles=protected_roles)
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
                enemy_speed_mult=1.0, first_turn_moves_override=None,
                first_turn_protected_role=None):
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

    `first_turn_moves_override`: optional `{role: [MoveInfo, ...]}` applied
    ONLY to the first turn, then dropped -- `switch_in_search` uses this to
    hand the incoming Pokemon an EMPTY move list for turn 1, modeling a real
    doubles switch: the ally staying in, and both enemies, still act that
    same turn (via `_best_turn`'s existing target search, unmodified -- an
    enemy is free to aim at the switch-in or the stayer, same "no
    coordination, each picks its own best" rule this module already has),
    only the Pokemon that just switched in has nothing to do yet.

    `first_turn_protected_role`: optional single enemy role ("E1" or "E2")
    that Protects on turn 1 ONLY, then is dropped -- `_pair_vs_targets` uses
    this (once for each enemy role) to check whether a scouting Protect on
    the very first turn -- the canonical "50/50" moment in real doubles --
    can still turn our sweep/out-trade into something worse. See
    `_resolve_turn`'s own docstring for the mechanics.
    """
    hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
    any_enemy_acted = False
    wiped_side, turns_used = None, 0
    full_log = []
    for turn_i in range(max(1, turns)):
        if (hp["E1"] <= 0 and hp["E2"] <= 0) or (hp["C"] <= 0 and hp["P"] <= 0):
            break
        turn_moves = moves_by_role
        if turn_i == 0 and first_turn_moves_override:
            turn_moves = {**moves_by_role, **first_turn_moves_override}
        protected = ({first_turn_protected_role}
                    if turn_i == 0 and first_turn_protected_role else frozenset())
        hp, turn_log, enemy_acted, wiped = _best_turn(
            combatants, turn_moves, hp, typechart, weather,
            enemy_speed_mult=enemy_speed_mult, protected_roles=protected)
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


def _grid_hit(attacker, moves, target, other_live, typechart, weather=None):
    """The best `Hit` `attacker`'s own moveset can land on `target`
    SPECIFICALLY -- one cell of the 2x2 damage grid, not the move a
    target-choosing AI would actually pick (that's `_choose_action`).

    A spread move still takes the doubles 0.75x penalty whenever
    `other_live` (the OTHER Pokemon on the target's side) is not None --
    hitting `target` at all means hitting `other_live` too, regardless of
    which single cell is being asked about, same rule `_choose_action` and
    `_hit_or_spread` already apply. Average roll, matching every other
    "realistic exchange" reading in this module (`pair_search`'s own).
    """
    best_key, best_hit = None, NO_HIT
    for mv in moves:
        if mv.category == "Status":
            continue
        if not mv.power and mv.name not in WEIGHT_BASED_POWER:
            continue
        n = 2 if (is_spread_move(mv.target) and other_live is not None) else 1
        got = _raw_hit(attacker, mv, target, typechart, weather=weather,
                       roll="avg", num_targets_hit=n)
        key = (got.frac >= 1.0, mv.priority, got.frac)
        if best_key is None or key > best_key:
            best_key, best_hit = key, got
    return best_hit


def _damage_grid(c1, c2, e1c, e2c, m1, m2, e1m, e2m, typechart, weather):
    """Every one of the 8 attacker-vs-specific-defender `Hit`s on this board
    -- "see if and how I out-trade (2x2 damage)" asks for the actual numbers,
    not just which line the race happened to choose. Returns {"ours": {("C",
    "E1"): Hit, ("C","E2"): Hit, ("P","E1"): Hit, ("P","E2"): Hit}, "theirs":
    {("E1","C"): Hit, ("E1","P"): Hit, ("E2","C"): Hit, ("E2","P"): Hit}}.
    """
    ours = {
        ("C", "E1"): _grid_hit(c1, m1, e1c, e2c, typechart, weather),
        ("C", "E2"): _grid_hit(c1, m1, e2c, e1c, typechart, weather),
        ("P", "E1"): _grid_hit(c2, m2, e1c, e2c, typechart, weather),
        ("P", "E2"): _grid_hit(c2, m2, e2c, e1c, typechart, weather),
    }
    theirs = {
        ("E1", "C"): _grid_hit(e1c, e1m, c1, c2, typechart, weather),
        ("E1", "P"): _grid_hit(e1c, e1m, c2, c1, typechart, weather),
        ("E2", "C"): _grid_hit(e2c, e2m, c1, c2, typechart, weather),
        ("E2", "P"): _grid_hit(e2c, e2m, c2, c1, typechart, weather),
    }
    return {"ours": ours, "theirs": theirs}


def _ohko_risk(grid):
    """Which of THEIRS' grid cells could OHKO one of ours outright, on their
    BEST roll -- "Scizor is always OHKO'd by Mega Charizard Y" is a
    structural fact about the matchup, true regardless of who happens to
    move first in any one played-out line, so this reads `Hit.hi` (their
    best-case roll, the same guaranteed-worst-case-FOR-US direction
    `threshold_search`'s own `incoming` check uses) rather than the race's
    actual (speed-order-dependent) outcome. Returns [{"attacker", "target",
    "move", "hi"}, ...], only the cells that clear 100%.
    """
    return [{"attacker": atk, "target": tgt, "move": h.move_name, "hi": h.hi}
           for (atk, tgt), h in grid["theirs"].items() if h.hi >= 1.0]


def _pair_vs_targets(n1, n2, our_built, target_names, enemy_built, typechart,
                     turns, want_grid=False):
    """(detail, summary) for OUR pair (`n1`, `n2`, drawn from `our_built`, a
    `_build_forms` dict) against every pair drawn from `target_names` -- the
    one place a joint pair is actually raced, so `joint_pair_search`
    (partner fixed) and `joint_pool_search` (both slots searched) can never
    drift apart on what "beats" means.

    ONLY ONE MEGA PER SIDE, the same minimax `pair_search` uses: every legal
    mega assignment on OUR side (`_resolve_forms`/`_mega_choices`) is tried
    and whichever gives US the best outcome is kept; every legal assignment
    on the ENEMY pair is tried too and whichever is WORST for us is assumed
    -- mirroring `matchup_search.play_out_worst_case`'s minimax (we pick our
    best response, they pick their best against us).

    Field weather is resolved FRESH per (our mega choice, enemy pair, enemy
    mega choice) combination, from all four combatants actually on the
    board that time (`_field_weather`) -- it can genuinely differ between
    two mega assignments of the SAME pair (a forced-base Ninetales-Alola
    loses Snow Warning's usual guarantee), not just between two of
    `target_names`' C(n,2) pairings.

    `detail`: {(e1, e2): {outcome, turns_used, tailwind_outcome,
    tailwind_safe, protect_outcomes, protect_safe, log}}. `log` is
    `_joint_race`'s own per-turn hit list -- "what the damage roll is", not
    just the win/loss classification -- for the NORMAL-speed race (the one
    that decided `outcome`); the Tailwind and Protect replays' logs aren't
    kept, only whether they still won.

    PROTECT ROBUSTNESS: the same normal-speed race is also replayed twice
    more, once with E1 Protecting turn 1 and once with E2 Protecting turn 1
    (mirroring the Tailwind replay's "same plan, one hypothesis changed"
    pattern) -- the real-doubles 50/50 the user described (Metagross+
    Hydreigon vs Mega Charizard Y+Sylveon: if Sylveon protects turn 1, Mega
    Charizard Y KOs Metagross, then Sylveon beats Hydreigon next turn) is
    exactly "does a turn-1 scouting Protect from either enemy still leave us
    sweeping or out-trading". `protect_outcomes`: {"E1": outcome_if_E1_
    protects, "E2": outcome_if_E2_protects}. `protect_safe` is True only when
    BOTH replays are still "sweep" or "out_trade" -- either one flipping to
    "loss"/"no_ko" means the enemy has a Protect-timed 50/50 against this
    pair, which is exactly what the user wants surfaced rather than hidden
    behind a `outcome` that only reflects the no-Protect line of play.

    `want_grid`: also compute `_damage_grid`/`_ohko_risk` per enemy pair, on
    whichever mega assignment was actually kept -- 8 extra `_raw_hit` calls
    each, cheap for the single fixed pair `--deep` checks but wasted (and
    never displayed) for a pool-wide search, so it defaults OFF and only
    the `--deep` CLI path turns it on.
    """
    m1, m2 = our_built[n1]["moves"], our_built[n2]["moves"]
    detail = {}
    for e1_name, e2_name in itertools.combinations(target_names, 2):
        e1m, e2m = enemy_built[e1_name]["moves"], enemy_built[e2_name]["moves"]

        best = None
        for _our_mt, (c1, c2) in _resolve_forms((n1, n2), our_built):
            worst = None
            for _enemy_mt, (e1c, e2c) in _resolve_forms((e1_name, e2_name), enemy_built):
                combatants = {"C": c1, "P": c2, "E1": e1c, "E2": e2c}
                moves_by_role = {"C": m1, "P": m2, "E1": e1m, "E2": e2m}
                weather = _field_weather(combatants)
                outcome, turns_used, _hp, log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns)
                tw_outcome, _tw_t, _tw_hp, _tw_log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns,
                    enemy_speed_mult=2.0)
                pr_e1_outcome, _pr1_t, _pr1_hp, _pr1_log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns,
                    first_turn_protected_role="E1")
                pr_e2_outcome, _pr2_t, _pr2_hp, _pr2_log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns,
                    first_turn_protected_role="E2")
                protect_outcomes = {"E1": pr_e1_outcome, "E2": pr_e2_outcome}
                entry = {
                    "outcome": outcome, "turns_used": turns_used,
                    "tailwind_outcome": tw_outcome,
                    "tailwind_safe": tw_outcome in ("sweep", "out_trade"),
                    "protect_outcomes": protect_outcomes,
                    "protect_safe": all(o in ("sweep", "out_trade")
                                        for o in protect_outcomes.values()),
                    "log": log, "_c1": c1, "_c2": c2, "_e1c": e1c, "_e2c": e2c,
                }
                if (worst is None or _JOINT_OUTCOME_RANK[entry["outcome"]]
                        > _JOINT_OUTCOME_RANK[worst["outcome"]]):
                    worst = entry
            if (best is None or _JOINT_OUTCOME_RANK[worst["outcome"]]
                    < _JOINT_OUTCOME_RANK[best["outcome"]]):
                best = worst

        if want_grid:
            weather = _field_weather({"C": best["_c1"], "P": best["_c2"],
                                      "E1": best["_e1c"], "E2": best["_e2c"]})
            grid = _damage_grid(best["_c1"], best["_c2"], best["_e1c"], best["_e2c"],
                               m1, m2, e1m, e2m, typechart, weather)
            best["grid"] = grid
            best["ohko_risk"] = _ohko_risk(grid)
        for k in ("_c1", "_c2", "_e1c", "_e2c"):
            best.pop(k, None)
        detail[(e1_name, e2_name)] = best
    summary = {
        "pairs_swept": sum(1 for d in detail.values() if d["outcome"] == "sweep"),
        "pairs_traded": sum(1 for d in detail.values() if d["outcome"] == "out_trade"),
        "pairs_lost": sum(1 for d in detail.values() if d["outcome"] == "loss"),
        "pairs_no_ko": sum(1 for d in detail.values() if d["outcome"] == "no_ko"),
        "pairs_tailwind_safe": sum(1 for d in detail.values() if d["tailwind_safe"]),
        "pairs_protect_safe": sum(1 for d in detail.values() if d["protect_safe"]),
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

    PROTECT ROBUSTNESS: every enemy pair is also raced twice more, once with
    each enemy Protecting turn 1 -- see `_pair_vs_targets`'s own docstring.
    `protect_safe` is True only when BOTH replays are still `sweep` or
    `out_trade`, i.e. neither enemy has a turn-1 scouting Protect that turns
    this pair's win into a loss or stall.

    Returns rows: {name, item, pairs_swept, pairs_traded, pairs_lost,
    pairs_no_ko, pairs_tailwind_safe, pairs_protect_safe, pairs_total,
    detail} -- `detail` is `_pair_vs_targets`'s own shape, damage log
    included. Ranked by (swept + traded) first, then protect_safe count,
    then tailwind_safe count -- mirrors `pair_search`'s existing
    `(clean+trade, -pinned)` sort, with protect_safe weighted ahead of
    tailwind_safe since a live Protect 50/50 is a more concrete risk than a
    hypothetical Tailwind.
    """
    partner_item, partner_move_names, _partner_weather = best_answer(
        partner_name, merged, moves_db, natures, typechart, target_names,
        item=partner_item)
    partner_moves = _move_infos(partner_name, merged, moves_db, partner_move_names)
    enemy_built = _build_forms(target_names, merged, natures, moves_db)

    rows = []
    for name in pool:
        if name in target_names or name == partner_name:
            continue
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        moves = _move_infos(name, merged, moves_db, move_names)

        our_built = _build_forms([name, partner_name], merged, natures, moves_db,
                                 items={name: item, partner_name: partner_item})
        # The optimised set (not `_build_forms`' own usage-derived default)
        # is what this function actually attacks with -- same convention
        # `_answer_for`'s callers everywhere else in this module follow.
        our_built[name]["moves"] = moves
        our_built[partner_name]["moves"] = partner_moves

        detail, summary = _pair_vs_targets(
            name, partner_name, our_built, target_names, enemy_built,
            typechart, turns)
        rows.append({"name": name, "item": item, "detail": detail, **summary})
    rows.sort(key=lambda r: (-(r["pairs_swept"] + r["pairs_traded"]),
                             -r["pairs_protect_safe"], -r["pairs_tailwind_safe"]))
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
    pairs_traded, pairs_lost, pairs_no_ko, pairs_tailwind_safe,
    pairs_protect_safe, pairs_total, detail} -- `detail` is
    `_pair_vs_targets`'s own shape (damage log included), ranked the same
    way `joint_pair_search` ranks.
    """
    built = {}
    for name in pool:
        if name in target_names:
            continue
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not move_names:
            continue
        entry = _build_forms([name], merged, natures, moves_db,
                             items={name: item})[name]
        entry["moves"] = _move_infos(name, merged, moves_db, move_names)
        entry["item"] = item
        built[name] = entry
    enemy_built = _build_forms(target_names, merged, natures, moves_db)

    rows = []
    for n1, n2 in itertools.combinations(built, 2):
        detail, summary = _pair_vs_targets(n1, n2, built, target_names,
                                           enemy_built, typechart, turns)
        rows.append({"pair": (n1, n2), "item1": built[n1]["item"],
                    "item2": built[n2]["item"], "detail": detail, **summary})
    rows.sort(key=lambda r: (-(r["pairs_swept"] + r["pairs_traded"]),
                             -r["pairs_protect_safe"], -r["pairs_tailwind_safe"]))
    return rows


def deep_dive(name1, name2, target_names, merged, moves_db, natures,
             typechart, turns=2, item_overrides=None, move_overrides=None):
    """The full report for ONE SPECIFIC, already-chosen pair (not a pool
    search) against every pair drawn from `target_names`.

        "a deep dive on a selected given pair; see all the possible enemy
         pairs, see if I'm at risk of being KO'd in one turn, to see if and
         how I outtrade (2x2 damage), see how it collapses into a win. For
         instance, Scizor is always OHKOd by Mega Charizard Y, so would not
         be a good bring as it auto loses."

    Pass the WHOLE 6-Pokemon roster as `target_names` for "the 15 possible
    enemy pair leads of 6 individuals" reading -- `itertools.combinations`
    inside `_pair_vs_targets` turns that into all C(6,2)=15 pairs on its own.

    Same machinery as `joint_pair_search`/`joint_pool_search` (`_build`,
    `_answer_for`, `_pair_vs_targets`), just for a NAMED pair instead of a
    pool search -- with `want_grid=True`, since the 2x2 damage grid and the
    OHKO-risk read (`_damage_grid`/`_ohko_risk`) are the whole point here and
    a single pair against a handful of enemy pairs is cheap regardless.

    Returns (item1, item2, detail, summary) -- `detail`/`summary` are
    `_pair_vs_targets`'s own shape, `grid`/`ohko_risk` included on every
    entry.
    """
    item1, moves1, _w1 = _answer_for(
        name1, merged, moves_db, natures, typechart, target_names,
        item_overrides=item_overrides, move_overrides=move_overrides)
    item2, moves2, _w2 = _answer_for(
        name2, merged, moves_db, natures, typechart, target_names,
        item_overrides=item_overrides, move_overrides=move_overrides)
    our_built = _build_forms([name1, name2], merged, natures, moves_db,
                             items={name1: item1, name2: item2})
    our_built[name1]["moves"] = _move_infos(name1, merged, moves_db, moves1)
    our_built[name2]["moves"] = _move_infos(name2, merged, moves_db, moves2)
    enemy_built = _build_forms(target_names, merged, natures, moves_db)
    detail, summary = _pair_vs_targets(name1, name2, our_built, target_names,
                                       enemy_built, typechart, turns,
                                       want_grid=True)
    return item1, item2, detail, summary


def switch_in_search(name1, name2, enemy_pair, bench, merged, moves_db,
                     natures, typechart, turns=2, item_overrides=None,
                     move_overrides=None):
    """For the LOSING pair (`name1`, `name2`) against `enemy_pair`, try
    swapping each of ours out for each `bench` candidate: does the new pair
    turn this specific enemy pair around?

        "for losing enemy leads, see if there are easy and optimal switch
         ins (i.e., they take little damage from the enemy on the switch
         in, and then the board state becomes a clearly winning one again)"

    ONE TURN IS SPENT ON THE SWITCH: the incoming Pokemon has no move on
    turn 1 -- the ally staying in, and both enemies, still act that same
    turn, exactly like a real doubles turn (`_joint_race`'s own
    `first_turn_moves_override`). Both enemies pick their own target
    independently and greedily (this module's usual "no coordination" rule),
    so a candidate that draws their fire is priced exactly as harshly as one
    that doesn't -- no separate "worst case, assume both focus it" layered
    on top; the SAME race that decides the outcome is what "how much did the
    switch-in take" is read from.

    `item_overrides`/`move_overrides`: optional {name: item} / {name:
    [move, ...]} pins -- apply to the STAYING Pokemon and every candidate,
    same as everywhere else in this module (`_answer_for`); the two enemies
    are unaffected (their sets came from wherever `enemy_pair` was decided).

    Returns rows, best (least damage taken switching in) first, FILTERED to
    candidates that actually fix the loss (`outcome` in "sweep"/"out_trade")
    -- "easy AND optimal", not a ranking of every candidate tried:
    [{"leaving", "arriving", "switch_in_taken", "outcome", "turns_used",
    "tailwind_outcome", "tailwind_safe"}]. Also returns `tried`, the total
    candidate count, so a caller can say "0 found among N tried" rather than
    silence standing in for "nothing tried".
    """
    e1_name, e2_name = enemy_pair
    enemy_built = _build_forms([e1_name, e2_name], merged, natures, moves_db)
    set_targets = [e1_name, e2_name]

    rows, tried = [], 0
    for leaving_role, leaving_name, staying_name in (
            ("C", name1, name2), ("P", name2, name1)):
        stay_item, stay_moves, _stay_w = _answer_for(
            staying_name, merged, moves_db, natures, typechart, set_targets,
            item_overrides=item_overrides, move_overrides=move_overrides)
        if not stay_moves:
            continue
        stay_m = _move_infos(staying_name, merged, moves_db, stay_moves)
        # Built ONCE per role, outside the bench loop -- `staying_name`
        # doesn't change per candidate, and an explicit `item=` bypasses
        # `make_combatant`'s own template cache, so leaving this inside the
        # loop would rebuild it (both forms) on every one of potentially
        # ~270 bench candidates for nothing.
        stay_built = _build_forms([staying_name], merged, natures, moves_db,
                                  items={staying_name: stay_item})
        stay_built[staying_name]["moves"] = stay_m

        for cand in bench:
            if cand in (name1, name2, e1_name, e2_name):
                continue
            item, mvs, _w = _answer_for(
                cand, merged, moves_db, natures, typechart, set_targets,
                item_overrides=item_overrides, move_overrides=move_overrides)
            if not mvs:
                continue
            tried += 1
            cand_m = _move_infos(cand, merged, moves_db, mvs)

            cand_built = _build_forms([cand], merged, natures, moves_db,
                                      items={cand: item})
            cand_built[cand]["moves"] = cand_m
            our_built = {**stay_built, **cand_built}
            # our_names[0] takes role "C", our_names[1] takes role "P" --
            # `leaving_role` says which slot the incoming candidate fills.
            our_names = (cand, staying_name) if leaving_role == "C" else (staying_name, cand)
            override = {leaving_role: []}

            # ONLY ONE MEGA PER SIDE, same minimax `_pair_vs_targets` uses:
            # we pick our own best mega assignment, the enemy pair (fixed by
            # the caller) is assumed to pick whichever of ITS legal mega
            # assignments is worst for us.
            best = None
            for _our_mt, (c_c, c_p) in _resolve_forms(our_names, our_built):
                worst = None
                for _enemy_mt, (e1c, e2c) in _resolve_forms((e1_name, e2_name), enemy_built):
                    combatants = {"C": c_c, "P": c_p, "E1": e1c, "E2": e2c}
                    moves_by_role = {"C": our_built[our_names[0]]["moves"],
                                     "P": our_built[our_names[1]]["moves"],
                                     "E1": enemy_built[e1_name]["moves"],
                                     "E2": enemy_built[e2_name]["moves"]}
                    # The switch-in's OWN weather-setting ability (a
                    # Drought/Snow Warning candidate) applies on entry
                    # regardless of whether it gets to ACT this turn --
                    # `_field_weather` already covers this, since
                    # `combatants` has it in its normal slot.
                    race_weather = _field_weather(combatants)
                    outcome, turns_used, _hp, log = _joint_race(
                        combatants, moves_by_role, typechart, race_weather, turns,
                        first_turn_moves_override=override)
                    entry = {"outcome": outcome, "turns_used": turns_used, "log": log,
                             "_combatants": combatants, "_moves_by_role": moves_by_role,
                             "_weather": race_weather}
                    if (worst is None or _JOINT_OUTCOME_RANK[entry["outcome"]]
                            > _JOINT_OUTCOME_RANK[worst["outcome"]]):
                        worst = entry
                if (best is None or _JOINT_OUTCOME_RANK[worst["outcome"]]
                        < _JOINT_OUTCOME_RANK[best["outcome"]]):
                    best = worst

            if best["outcome"] not in ("sweep", "out_trade"):
                continue
            tw_outcome, _tw_t, _tw_hp, _tw_log = _joint_race(
                best["_combatants"], best["_moves_by_role"], typechart,
                best["_weather"], turns, enemy_speed_mult=2.0,
                first_turn_moves_override=override)
            log = best["log"]
            switch_in_taken = (sum(h.avg for role, _tgt, h in log[0]
                                   if role in ("E1", "E2")
                                   and _tgt == leaving_role) if log else 0.0)
            rows.append({
                "leaving": leaving_name, "arriving": cand,
                "switch_in_taken": switch_in_taken, "outcome": best["outcome"],
                "turns_used": best["turns_used"], "tailwind_outcome": tw_outcome,
                "tailwind_safe": tw_outcome in ("sweep", "out_trade"),
            })
    rows.sort(key=lambda r: (r["switch_in_taken"], not r["tailwind_safe"]))
    return rows, tried
