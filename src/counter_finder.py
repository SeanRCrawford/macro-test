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
from damage import (AURA_TYPES, CHARGE_WEATHER_SKIP, WEIGHT_BASED_POWER, MoveInfo,
                    damage_roll, defensive_stat, effective_stat, hit_count_for,
                    hits_ally, is_spread_move, move_from_showdown,
                    grassy_glide_priority_bonus)
from engine import FieldState, WEATHER_SETTERS, TERRAIN_SETTERS, effective_speed
from optimize_sets import best_item, best_moveset, legal_items, team_weather_for
from solver import FIRST_TURN_ONLY_MOVES, build_moveset
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


def _base_species_name(name):
    """The base species name for a "Mega X" (or "Mega X Y", the Charizard/
    Mewtwo-style split) roster entry -- `None` if `name` isn't a Mega pick
    at all. This dataset lists a Mega and its base form as two INDEPENDENT
    roster entries (their own usage stats, EVs, ability) -- there is no
    existing "same species" lookup anywhere else in this module, since
    nothing needed one before now.

        "You cannot have both a mega and its non-mega form."

    Only strips the ONE trailing " X"/" Y" suffix pattern this dataset
    actually uses (Mega Charizard X/Y, Mega Mewtwo X/Y) -- a base species
    name genuinely ending in " X" or " Y" does not otherwise occur.
    """
    if not name.startswith("Mega "):
        return None
    base = name[len("Mega "):]
    for suffix in (" X", " Y"):
        if base.endswith(suffix):
            return base[:-len(suffix)]
    return base


def _mega_base_overlap(names):
    """Every name in `names` that is EITHER a Mega pick whose own base
    species is also present, OR a base species whose Mega form is also
    present -- "you cannot have both a mega and its non-mega form" as a
    reusable membership check, for both `--bring4`'s validation of a
    user-named --our and `--multi-bring4`'s candidate-pool filtering.
    """
    names = set(names)
    bad = set()
    for n in names:
        base = _base_species_name(n)
        if base is not None and base in names:
            bad.add(n)
            bad.add(base)
    return bad


def member_weakness_summary(core, merged):
    """Per-MEMBER type-weakness counts for `core` -- how many of the
    game's types each member is weak to (0, 1, or 2+) -- feeding the
    multi-bring4 CSV/CLI synergy summary:

        "I want the multi-bring4 export to summarise type synergy too,
         such as total weaknesses to 2 types, total weaknesses to 1 type
         and overall"

    ALSO the per-TYPE reading: "for total weaknesses, it should be by
    type, i.e., does the team have 3 weaknesses to fire" -- a flat sum
    across every type answers a different, less useful question ("how
    many weaknesses in total", with no way to tell 3-to-one-type from
    1-each-to-three), so `per_type` ({type: how many members are weak to
    it}) is what the CLI/CSV actually display now. Computed via
    `team_search._weak_resist`, the SAME per-type split `--max-weak`/
    `--type-limit`'s own hard filter already reads (`_core_passes_hard_
    filters` -> `team_search.hard_violations`) -- both must agree on what
    "weak to Fire" means, so this is reused, never reimplemented.

    Returns {"per_member": {name: weak_type_count}, "per_type": {type:
    weak_member_count}, "weak_to_2plus": int, "weak_to_1": int,
    "weak_to_0": int, "total_weakness_instances": int}.
    """
    from species_data import TYPES
    from team_search import _weak_resist
    per_member = {}
    for n in core:
        dc = (merged.get(n) or {}).get("defensive_chart") or {}
        per_member[n] = sum(1 for t in TYPES if dc.get(t, 1.0) > 1.0)
    counts = list(per_member.values())
    per_type = {t: len(_weak_resist(list(core), merged, t)[0]) for t in TYPES}
    return {
        "per_member": per_member,
        "per_type": per_type,
        "weak_to_2plus": sum(1 for c in counts if c >= 2),
        "weak_to_1": sum(1 for c in counts if c == 1),
        "weak_to_0": sum(1 for c in counts if c == 0),
        "total_weakness_instances": sum(counts),
    }


def weak_type_breadth(core, merged, threshold=2):
    """How many DIFFERENT types have at least `threshold` of `core`'s own
    members weak to them -- "I would like to be able to select a cap for
    the number of types that have 2 weaknesses, such as no more than 3
    types that have 2 members weak to it." A complementary question to
    `member_weakness_summary`'s existing per-type COUNT ("does the team
    have 3 weaknesses to Fire"): this is about BREADTH of exposure across
    types, not depth on any one -- a team where Fire, Water, and Grass each
    have exactly 2 weak members is broadly fragile even if no single type
    ever exceeds an existing `--max-weak` cap.

    Reuses `member_weakness_summary`'s own `per_type` counts (the same
    `team_search._weak_resist` split `--max-weak`/`--type-limit` already
    read), never a second, competing notion of "weak to a type".

    MONOTONIC under core growth, same as `max_weak`: a member's own
    weaknesses never change, so adding one to a partial core can only ever
    ADD to a type's weak-member count, never remove from it -- once a type
    crosses `threshold` it stays crossed. Safe to prune ON during
    `multi_bring4_beam`'s incremental growth, not just as a final filter.
    """
    per_type = member_weakness_summary(core, merged)["per_type"]
    return sum(1 for c in per_type.values() if c >= threshold)


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


def _resolve_forms(names, built, forced_base_names=frozenset()):
    """For `names` (one candidate alone, or a real pair) drawn from `built`
    (a `_build_forms` dict), every legal way to honour "only one Mega per
    side" -- yields (mega_transforms, [combatant_for_each_name]) for each of
    `_mega_choices(names)`, in the same order as `names`. A single-name list
    still works (`_mega_choices` only ever looks for "Mega "-prefixed
    entries, regardless of how many names it's given) -- `pair_search` uses
    that for a candidate with no partner.

    `forced_base_names`: names that must NEVER be the one who transforms,
    even though `_mega_choices` would otherwise offer it -- e.g. a bring-4
    that carries BOTH of a core's two allowed Mega-stone holders needs a
    SINGLE one of them designated "the team's mega" for the whole bring
    (VGC's real "only one Mega Evolution per team per game" rule), with the
    other locked to base form in every one of that bring's own pairs, not
    just within whichever single pair it happens to share with the other
    stone-holder. Default empty set changes nothing -- every existing
    caller/test is unaffected.
    """
    for mt in _mega_choices(names):
        if mt in forced_base_names:
            continue
        _evolves, forced_base = resolve_team_mega_slot(list(names),
                                                        mega_transforms=mt)
        cs = [built[n]["base" if n in forced_base else "mega"] for n in names]
        yield mt, cs


# "By default I do not want to allow choice scarf; it is too easy to
# punish, but should be an option." -- Choice Scarf is legal in Regulation
# MB (unlike Assault Vest/Choice Band/Choice Specs, `lead_sim.BANNED_ITEMS`),
# so this is a SEPARATE, counter_table.py-specific preference on top of that
# legality check, not a rule change: `--allow-scarf` passes `excluded_items=
# frozenset()` through every search function below to opt back in.
DEFAULT_EXCLUDED_ITEMS = frozenset({"Choice Scarf"})

# Regulation M-C additions to the pool (Rillaboom, Baxcalibur, Mega Absol Z,
# Mega Garchomp Z, Mega Lucario Z) are documented alongside `lead_sim.
# BANNED_ITEMS`, not here -- see that comment. Nothing here needs to change
# for them.


def best_answer(name, merged, moves_db, natures, typechart, target_names,
                item=None, move_names=None, excluded_items=DEFAULT_EXCLUDED_ITEMS):
    """(item, move_names, weather): `name`'s best LEGAL item and moveset
    against `target_names`, via `optimize_sets.best_item`/`best_moveset` --
    or, "or to just select optimal item" is not the only option, an explicit
    override.

    `item`: pin a specific item instead of searching for the best legal one
    (e.g. Choice Scarf on a Pokemon the search would not have picked it for).
    Checked against Regulation MB's banned list the same as a searched item
    would be -- an override is a decision, not a loophole, so it still has to
    be legal. `excluded_items` does NOT apply to an explicit pin -- naming
    Choice Scarf yourself via `--item` still works even with the default
    exclusion on, same "a decision, not a loophole" reasoning. The moveset
    is still genuinely re-optimised UNDER whichever item ends up in play,
    pinned or found -- "For Choice Scarf, a pokemon can use 4 moves, which
    could be highly useful": a Choice item locks you into the first move you
    use, not into a single hard-coded one, so its set is chosen the same way
    any other item's is.

    `move_names`: pin the moveset outright too (skips move optimisation
    entirely). Only meaningful together with `item`, since a pinned moveset
    with a searched-for item would be re-deriving the item against moves that
    might not even be the ones the search would have chosen.

    `excluded_items`: items dropped from the SEARCH's own candidate list --
    defaults to `DEFAULT_EXCLUDED_ITEMS` (Choice Scarf); pass `frozenset()`
    for "search every legal item, Scarf included". Applied the SAME way
    `BANNED_ITEMS` already is (a post-hoc filter on `best_item`'s pick,
    falling back to the most-used remaining legal item, not a full
    re-optimisation excluding it -- `optimize_sets.best_item` itself is
    shared with the rest of the app and is not touched).

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
    if item in BANNED_ITEMS or item in excluded_items:
        item, move_names = None, None
    if item is None:
        legal = [i for i in (legal_items(name, merged) or [])
                if i not in BANNED_ITEMS and i not in excluded_items]
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
            num_targets_hit=1, attacker_hp_frac=None, defender_hp_frac=None,
            auras=None, terrain=None):
    """The full `Hit` `move` (already on `attacker`, item applied by the
    caller via `_build`) does to `defender`.

    `auras`: the board's active Fairy Aura/Dark Aura/Aura Break set (see
    `_active_auras`) -- passed straight through to `damage_roll`, which
    already knows how to apply it (`aura_multiplier`). `None` (every caller
    outside the 2v2 board functions, which have no board to gather auras
    from at all) means no aura boost/dampening, same as before this existed.

    `terrain`: the board's active terrain (see `_field_terrain`, mirrors
    `weather`) -- passed straight through to `damage_roll`'s own Grassy
    Terrain handling. `None` (default, same as `weather`) means no terrain
    boost/reduction.

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

    TWO-TURN (CHARGE) MOVES -- Solar Beam/Blade, Electro Shot, Fly, Dig, Sky
    Attack, ... (`move.flags.get("charge")`) -- deal NO damage THIS turn
    unless the right weather is already up to skip the charge
    (`CHARGE_WEATHER_SKIP`: Solar Beam/Blade need sun, Electro Shot needs
    rain; most charge moves have no weather skip at all and are NEVER a
    one-turn hit here). Same rule and same reasoning as
    `solver.candidate_actions`/`fast_eval._pick_greedy_action`: without it, a
    move that actually spends this whole turn charging looks like a
    guaranteed huge hit RIGHT NOW and gets credited/chosen as if it were a
    normal one-turn move. This module has no per-role "already charging"
    volatile state the way `battle.py`'s real engine does (nothing here
    persists a commitment across turns the way recharge below does), so the
    simplification is the same one `solver.py`/`fast_eval.py` already make
    for their own non-real-engine heuristics: never a live candidate without
    the matching weather, full stop.

    `attacker_hp_frac`/`defender_hp_frac`: optional current-HP fractions
    (0.0-1.0) for callers that track HP across a running multi-turn
    exchange (`_choose_action`, fed by `_resolve_turn`'s own turn-start `hp`
    dict) rather than a fresh full-HP snapshot. `attacker`/`defender` here
    are always built at full HP (`_build`/`_build_form`) and never mutated
    by this module's joint-race machinery (`_apply_plan` tracks HP
    separately, in a `{role: fraction}` dict, precisely so the same shared
    Combatant can be replayed across the normal/Tailwind/Protect-x2 races
    `_pair_vs_targets` runs without one contaminating another) -- so without
    this, `attacker.current_hp`/`defender.current_hp` always read as full,
    even mid-race. That silently broke two REAL mechanics that key off
    current HP: Eruption/Water Spout's own power scaling for any use after
    the first in a race, and Multiscale, which stayed active turn after
    turn "no matter how much damage it takes" instead of only at full HP.
    Applied via a throwaway `copy.copy` (never mutating the caller's shared
    Combatant) ONLY around the `damage_roll` call these checks live inside;
    `cur` below still divides by the UNMODIFIED `defender.current_hp` (this
    function's own pre-existing "fraction of what `defender` shows as
    current, not max" convention -- see `chip_then_ko`, which deliberately
    mutates a THROWAWAY defender's `current_hp` itself before calling this,
    for the same reason) so a caller passing neither fraction (every
    existing caller) is completely unaffected.
    """
    if move.category == "Status":
        return NO_HIT
    if not move.power and move.name not in WEIGHT_BASED_POWER:
        return NO_HIT
    if move.flags and move.flags.get("charge"):
        skip_weather = CHARGE_WEATHER_SKIP.get(move.name)
        if not (skip_weather and weather == skip_weather):
            return NO_HIT
    physical = move.category == "Physical"
    ak, dk = ("atk", "def") if physical else ("spa", "spd")
    atk = effective_stat(attacker.stats[ak], attacker.stages[ak])
    if attacker.item == "Choice Band" and physical:
        atk *= 1.5
    if attacker.item == "Choice Specs" and not physical:
        atk *= 1.5
    dfn = defensive_stat(defender, dk, move, weather=weather)
    dmg_attacker, dmg_defender = attacker, defender
    if attacker_hp_frac is not None:
        dmg_attacker = copy.copy(attacker)
        dmg_attacker.current_hp = max(1, round(attacker_hp_frac * attacker.max_hp()))
    if defender_hp_frac is not None:
        dmg_defender = copy.copy(defender)
        dmg_defender.current_hp = max(1, round(defender_hp_frac * defender.max_hp()))
    lo, hi, avg, eff = damage_roll(50, move.power, atk, dfn, dmg_attacker, dmg_defender,
                                   move, typechart, weather=weather, auras=auras,
                                   num_targets_hit=num_targets_hit, terrain=terrain)
    hits = hit_count_for(move.name, attacker)
    cur = defender.current_hp or 1
    lo_f, avg_f, hi_f = (lo * hits) / cur, (avg * hits) / cur, (hi * hits) / cur
    frac = avg_f if roll == "avg" else lo_f
    return Hit(move_name=move.name, frac=frac, lo=lo_f, avg=avg_f, hi=hi_f,
              eff=eff, num_targets_hit=num_targets_hit)


def _best_hit(attacker, moves, defender, typechart, weather=None, terrain=None):
    """The best `Hit` among `moves` against `defender`, searched worst-roll
    (see `_raw_hit`). Single-target only -- for a spread-aware search see
    `_choose_move` (average-roll, used by `pair_search`)."""
    best = NO_HIT
    for mv in moves:
        got = _raw_hit(attacker, mv, defender, typechart, weather=weather, terrain=terrain)
        if got.frac > best.frac:
            best = got
    return best


PRIORITY_BLOCK_ABILITIES = frozenset({"Queenly Majesty", "Dazzling", "Armor Tail"})
_PRIORITY_BLOCK_IGNORING_ABILITIES = frozenset(
    {"Mold Breaker", "Teravolt", "Turboblaze"})

# The real -2 SpA "nuke" family (verified against each move's raw `self.
# boosts` data: exactly these five carry `{"spa": -2}`). A LOW-COST stand-in
# for general stat-stage tracking (which this module deliberately has none
# of, see its own module docstring) -- rather than tracking the actual -2
# SpA stage, a role that has used one of these earlier in the SAME race has
# ALL its own outgoing damage halved from then on (a flat approximation, not
# gated to Special moves only). Close Combat/Superpower (-1/-1) are
# deliberately excluded -- too mild a drop for this approximation to fit.
SELF_HALVING_MOVES = frozenset({"Draco Meteor", "Overheat", "Leaf Storm",
                                "Psycho Boost", "Fleur Cannon"})

# Abilities that block Intimidate outright (Clear Body/White Smoke/Full
# Metal Body block ANY opponent-inflicted stat drop; Hyper Cutter blocks
# Attack drops specifically; Inner Focus blocks Intimidate specifically).
INTIMIDATE_BLOCKED = frozenset({"Clear Body", "White Smoke", "Full Metal Body",
                                "Hyper Cutter", "Inner Focus"})


def _priority_blocked(attacker, mv, defending_side):
    """True if `mv` would fail outright against `defending_side` (an
    iterable of the Combatants on the target's side -- `None` entries for an
    absent/fainted slot are fine). Mirrors `battle.py`'s
    `Battle._blocked_by_guard` priority-block rule exactly: Queenly Majesty /
    Dazzling / Armor Tail, held by ANY still-living member of the defending
    side, blocks an incoming priority move aimed at that side entirely --
    "Make sure anti-priority like Farigiraf's armor tail ability is taken
    into account" -- unless the attacker itself has Mold Breaker / Teravolt /
    Turboblaze.
    """
    if mv is None or mv.priority <= 0:
        return False
    if attacker.ability in _PRIORITY_BLOCK_IGNORING_ABILITIES:
        return False
    return any(c is not None and c.ability in PRIORITY_BLOCK_ABILITIES
              for c in defending_side)


def _choose_move(attacker, moves, defender, typechart, weather=None,
                 defending_side=None, auras=None, terrain=None):
    """The move `attacker` would actually use against `defender`, searched on
    the AVERAGE roll: prefer one that KOes outright; failing that, prefer one
    that sets up a KO within a FOLLOW-UP turn (below); failing THAT, prefer
    more damage. Returns a `Hit` (`NO_HIT` if nothing does damage) plus the
    chosen `MoveInfo` (needed by the caller to check `is_spread_move` and to
    read `.priority` for turn ordering).

    PRIORITY IS NOT A TIE-BREAK HERE. It used to be (right after the KO
    checks) -- "prefer a slow 100% chunk over a Sucker Punch that secures
    the same KO first" was the original reasoning -- but that let a WEAK
    priority move beat a far STRONGER slow one whenever both merely cleared
    the same "not a guaranteed kill" bar (Aqua Jet, 34%, over Wave Crash,
    95% and nearly an outright OHKO, because both technically reach a
    2-turn kill once ONE-TURN LOOKAHEAD below exists -- priority is not the
    thing that should decide between "barely" and "almost entirely" done).
    A move's priority is still fully honoured in the REAL turn-order
    computation once chosen (nothing here changes that), and two of the
    cases where priority genuinely needs to drive the CHOICE -- surviving
    to act at all, or a Sucker Punch pick that would otherwise fail
    outright -- are handled by a dedicated correction afterward
    (`_reconsider_for_survival`), not baked into this base ranking.

    ONE NARROW EXCEPTION: when two or more candidates ALREADY guarantee an
    outright KO this turn (`kos_now`), priority breaks the tie before raw
    damage does -- "Kingambit picks Kowtow Cleave (more damage) over Sucker
    Punch when Sucker Punch should guarantee the KO" against a much faster
    Dragapult: both moves one-shot it, so there is no damage tradeoff left
    to weigh, and going first (via Sucker Punch) is strictly better than
    going second with a bigger number on a target that was dying either
    way -- it can turn what would only ever be an `out_trade` (Dragapult
    gets to act first and do something before it faints) into a real
    `sweep`. This does NOT reintroduce the Aqua-Jet-over-Wave-Crash bug
    above: that bucket is `kos_now=False` for both candidates (neither
    one-shots), where this exception never applies -- `priority_if_kos_now`
    is 0 for every such candidate, so the ranking there is exactly as
    described above, priority-blind. Only among moves that already clear
    the same "kills it this turn, for certain" bar does a free "and I go
    first" become worth preferring.

    ONE-TURN LOOKAHEAD: "it's important to at least have a one turn
    lookahead -- if wave crash into aqua jet kills ... on the second turn."
    A move that doesn't KO by itself is still credited with a KO if its
    damage PLUS this attacker's own best available follow-up (any move here,
    not necessarily the same one) would clear 100% -- so a real 2-turn kill
    line beats a move that never threatens a kill at all. This is
    deliberately optimistic (it assumes the attacker survives to act again
    and nothing heals or removes the target in between) -- a forecast, not
    a guarantee, the same kind of hypothesis this module already makes
    elsewhere (Tailwind, `outspeed="scarf"`).

    `defending_side`: the full list of Combatants on `defender`'s side
    (defaults to just `defender` alone) -- checked via `_priority_blocked`
    so a priority move that would actually fail against an Armor Tail /
    Dazzling / Queenly Majesty holder is never credited with its damage;
    a rational attacker picks a move that actually lands.

    `auras`: the board's active Fairy Aura/Dark Aura/Aura Break set
    (`_active_auras`), passed straight through to `_raw_hit`.
    """
    defending_side = defending_side if defending_side is not None else (defender,)
    candidates = []
    for mv in moves:
        if mv.category == "Status":
            continue
        if not mv.power and mv.name not in WEIGHT_BASED_POWER:
            continue
        got = _raw_hit(attacker, mv, defender, typechart, weather=weather, roll="avg",
                       auras=auras, terrain=terrain)
        if _priority_blocked(attacker, mv, defending_side):
            got = NO_HIT
        candidates.append((mv, got))
    if not candidates:
        return NO_HIT, None
    best_follow_up = max(got.frac for _mv, got in candidates)
    best_key, best_hit, best_move = None, NO_HIT, None
    for mv, got in candidates:
        kos_now = got.frac >= 1.0
        kos_in_two = kos_now or (got.frac + best_follow_up) >= 1.0
        priority_if_kos_now = mv.priority if kos_now else 0
        key = (kos_now, kos_in_two, priority_if_kos_now, got.frac)
        if best_key is None or key > best_key:
            best_key, best_hit, best_move = key, got, mv
    return best_hit, best_move


def _answer_for(name, merged, moves_db, natures, typechart, target_names,
                item_overrides=None, move_overrides=None,
                excluded_items=DEFAULT_EXCLUDED_ITEMS):
    """`best_answer`, applying this specific `name`'s entry (if any) in
    `item_overrides`/`move_overrides` -- "I also want the option to define
    item ... such as Choice Scarf, or to just select optimal item". Both
    dicts default to "search for the best", per-name, unaffected by an
    override on some OTHER name in the same pool. `excluded_items`: see
    `best_answer` -- an explicit `item_overrides` pin still bypasses it.
    """
    item = (item_overrides or {}).get(name)
    moves = (move_overrides or {}).get(name)
    return best_answer(name, merged, moves_db, natures, typechart, target_names,
                       item=item, move_names=moves, excluded_items=excluded_items)


def _resolve_unique_items(names, merged, moves_db, natures, typechart,
                          target_names, item_overrides=None, move_overrides=None,
                          excluded_items=DEFAULT_EXCLUDED_ITEMS):
    """VGC's real Item Clause: no two of `names` may hold the same item.
    OPT-IN -- callers only reach for this when asked to (`bring4_search`/
    `core_deep_dive`'s own `enforce_item_clause` flag); it is never applied
    silently.

    Each name's best legal item is searched independently, in the given
    order -- a name whose independently-best item was already claimed by an
    earlier name in this same list is re-searched with every already-claimed
    item ALSO excluded (`_answer_for`'s existing `excluded_items` mechanism),
    falling back to its own next-best legal item. Build-order dependent by
    design (per product decision): whichever name resolves first keeps its
    top choice. A name already pinned via `item_overrides` is respected
    verbatim, never re-resolved -- an explicit pin always wins.

    `_answer_for`'s own fallback already handles "every legal item excluded"
    gracefully (returns `item=None`, moveset still searched under no item)
    -- no special-casing needed here for that, or for Mega Stones (each
    holds a uniquely-named stone, so two different Megas' stones never
    collide as strings in the first place).

    Returns a NEW item_overrides dict (a superset of the input) with every
    name in `names` pinned to its (possibly reassigned) item -- pass it
    straight into a downstream `_answer_for`/`joint_pool_search` call so
    items are computed exactly once, not searched twice.
    """
    resolved = dict(item_overrides or {})
    taken = set()
    for name in names:
        if name not in resolved:
            item, _mv, _w = _answer_for(
                name, merged, moves_db, natures, typechart, target_names,
                item_overrides=item_overrides, move_overrides=move_overrides,
                excluded_items=excluded_items)
            if item and item in taken:
                item, _mv, _w = _answer_for(
                    name, merged, moves_db, natures, typechart, target_names,
                    item_overrides=item_overrides, move_overrides=move_overrides,
                    excluded_items=excluded_items | taken)
            resolved[name] = item
        if resolved[name]:
            taken.add(resolved[name])
    return resolved


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


def _field_terrain(combatants):
    """The ONE shared field terrain for a 2v2 board -- Regulation M-C's
    Grassy Surge, mirrors `_field_weather` exactly (same "checked against
    every combatant, last setter in `combatants`' own order wins" rule,
    same no-fainted-filter: a terrain-setter that later faints doesn't
    retroactively un-set the terrain it already put up). Terrain is NOT
    exclusive with weather -- both can be active on the same board -- so
    this is a fully separate lookup, not folded into `_field_weather`."""
    terrain = None
    for c in combatants.values():
        if c is not None and c.ability in TERRAIN_SETTERS:
            terrain = TERRAIN_SETTERS[c.ability]
    return terrain


_SIDE_OF = {"C": ("C", "P"), "P": ("C", "P"), "E1": ("E1", "E2"), "E2": ("E1", "E2")}
_OPPOSING_OF = {"C": ("E1", "E2"), "P": ("E1", "E2"), "E1": ("C", "P"), "E2": ("C", "P")}


def _intimidate_mult_by_role(combatants):
    """{role: {"physical": mult}} or {role: {"special": mult}} for every
    role whose OPPOSING side has a live Intimidate holder -- computed ONCE
    from the INITIAL `combatants` (the real -1 Atk stage Intimidate applies
    on switch-in PERSISTS even if the holder later faints mid-race, so this
    is correct to compute once at race start, not re-checked every turn).

    A -1 Atk stage is x(2/3) on the stat -- and, since the damage formula
    multiplies by the attack stat directly, x(2/3) on damage output is
    mathematically EXACT for this, not an approximation. `INTIMIDATE_
    BLOCKED` abilities are unaffected; Defiant/Competitive invert it into a
    real +2 stage (x2.0) SELF-boost on the matching damage category instead
    of a drop -- also exact, not approximated. Contrary reverses the SAME
    -1 magnitude into a +1 (x1.5), not a +2 -- it flips direction, not size
    (mirrors `damage.apply_intimidate`'s own Contrary branch).

    A role with no entry here means "unaffected" (multiplier 1.0), matching
    every other optional per-role map in this module's convention.
    """
    out = {}
    for role, c in combatants.items():
        if c is None:
            continue
        if not any(combatants[r] is not None and combatants[r].ability == "Intimidate"
                   for r in _OPPOSING_OF[role]):
            continue
        if c.ability in INTIMIDATE_BLOCKED:
            continue
        if c.ability == "Defiant":
            out[role] = {"physical": 2.0}
        elif c.ability == "Competitive":
            out[role] = {"special": 2.0}
        elif c.ability == "Contrary":
            out[role] = {"physical": 1.5}
        else:
            out[role] = {"physical": 2 / 3}
    return out


_AURA_ABILITIES = frozenset(AURA_TYPES) | {"Aura Break"}


def _active_auras(combatants, hp=None):
    """The board's active Fairy Aura / Dark Aura / Aura Break set -- mirrors
    `battle.py`'s own `Battle._active_auras` (gathered from EVERY live
    combatant, both sides, since these are field-wide effects, not personal
    boosts). `hp` (a `{role: fraction}` dict, `_apply_plan`'s own
    convention): an aura holder that has since fainted no longer
    contributes, matching the real engine's `not c.fainted` check --
    `hp=None` (the default) skips that filter, for a static, everyone-
    presumed-alive snapshot (`_damage_grid`'s own use, the same "right now"
    reading `_grid_hit` already gives Multiscale et al.).
    """
    return {c.ability for role, c in combatants.items()
           if c is not None and (hp is None or hp.get(role, 0) > 0)
           and c.ability in _AURA_ABILITIES}


def threshold_search(pool, target_names, merged, moves_db, natures, typechart,
                     threshold=0.9, item_overrides=None, move_overrides=None,
                     max_taken=None, outspeed=None,
                     excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
    need_screen = max_taken is not None or outspeed is not None
    # Each named target's own set is searched ONCE (against the whole pool,
    # the same "what does this threat generally run" question `speed_tiers`
    # asks), not once per candidate -- a real Pokemon carries one set, not a
    # different one for every possible opponent it might face.
    target_sets = {}
    if need_screen:
        for t in target_names:
            # The named target's OWN set -- a real fact about the matchup,
            # not something being recommended to the user, so it always
            # searches the full item pool regardless of `excluded_items`
            # (the enemy could easily be running Choice Scarf themselves).
            target_sets[t] = _answer_for(t, merged, moves_db, natures,
                                         typechart, pool,
                                         excluded_items=frozenset())

    rows = []
    for name in pool:
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
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
                item_overrides=None, excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
        # `names` here is a MIXED list -- the named targets plus the pool,
        # in the same chart (`--speed` puts everyone on one turn-order
        # ladder). The exclusion is a preference about OUR OWN candidates
        # only; a named target's own set always searches the full item
        # pool, same reasoning as `threshold_search`'s `target_sets`.
        ex = frozenset() if name in target_names else excluded_items
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, excluded_items=ex)
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
                 item_overrides=None, move_overrides=None,
                 excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
            partner_name, merged, moves_db, natures, typechart, target_names,
            excluded_items=excluded_items)
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
        if name == partner_name:
            continue
        item, move_names, weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
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
                   weather=None, roll="avg", defending_side=None, auras=None,
                   terrain=None):
    """{role: Hit} for whatever `mv` actually damages: every role in
    `defenders` if it's a spread move landing on more than one of them (the
    doubles 0.75x penalty applied via `num_targets_hit`), else just
    `intended_role`. `mv=None` (nothing to hit with) returns {}.

    `defending_side`: the full list of Combatants on the defending side, for
    the Armor Tail / Dazzling / Queenly Majesty priority-block check
    (`_priority_blocked`) -- defaults to `defenders.values()`, but pass it
    explicitly whenever the real defending side has a member NOT in
    `defenders` (e.g. a partner the enemy isn't currently aiming at --
    blocking is a whole-side effect, not scoped to just who's being hit). A
    blocked move returns {} entirely, same as `mv=None`.

    `auras`: the board's active Fairy Aura/Dark Aura/Aura Break set
    (`_active_auras`), passed straight through to `_raw_hit`.

    `terrain`: the board's active terrain (`_field_terrain`), passed straight
    through to `_raw_hit`.
    """
    if mv is None:
        return {}
    defending_side = (defending_side if defending_side is not None
                      else list(defenders.values()))
    if _priority_blocked(attacker, mv, defending_side):
        return {}
    if is_spread_move(mv.target) and len(defenders) > 1:
        n = len(defenders)
        return {role: _raw_hit(attacker, mv, d, typechart, weather=weather,
                               roll=roll, num_targets_hit=n, auras=auras,
                               terrain=terrain)
               for role, d in defenders.items()}
    got = _raw_hit(attacker, mv, defenders[intended_role], typechart,
                   weather=weather, roll=roll, auras=auras, terrain=terrain)
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

    SURVIVAL-AWARE RECONSIDERATION: `C`/`E1`/`E2` (not `partner` -- its
    move is a caller-fixed input) each get one chance to swap to a
    strictly-faster move from their own moveset if their independently-
    chosen pick would never actually fire (something faster kills them
    first, the same turn, before their own turn comes up), and one chance
    to swap away from Sucker Punch to a different, unconditional move if
    Sucker Punch itself turned out to fail (the target had already moved)
    -- see `_resolve_turn`'s `_reconsider_for_survival` for the full
    reasoning; this is the same idea, scoped to this function's simpler
    single-turn, full-HP-start board.

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
    terrain = _field_terrain(combatants)
    auras = _active_auras(combatants)
    field = FieldState(weather=weather, terrain=terrain)
    hp = {k: 1.0 for k in combatants}
    defenders = {"E1": e1, "E2": e2}
    target_role = "E1" if e1_name == candidate_target else "E2"

    our_side = [combatants.get("C"), combatants.get("P")]

    plan = {}
    got, mv = _choose_move(attacker, atk_moves, combatants[target_role], typechart,
                           weather=weather, defending_side=[e1, e2], auras=auras,
                           terrain=terrain)
    plan["C"] = (_hit_or_spread(attacker, mv, target_role, defenders, typechart,
                                weather=weather, auras=auras, terrain=terrain), mv)
    for role, e, e_moves in (("E1", e1, e1_moves), ("E2", e2, e2_moves)):
        got, mv = _choose_move(e, e_moves, attacker, typechart, weather=weather,
                               defending_side=our_side, auras=auras, terrain=terrain)
        plan[role] = (_hit_or_spread(e, mv, "C", {"C": attacker}, typechart,
                                     weather=weather, defending_side=our_side,
                                     auras=auras, terrain=terrain), mv)
    if partner is not None:
        p_target_role = "E1" if (partner_target or candidate_target) == e1_name else "E2"
        plan["P"] = (_hit_or_spread(partner, partner_move, p_target_role, defenders,
                                    typechart, weather=weather, auras=auras,
                                    terrain=terrain), partner_move)

    def speed_key(role):
        mv = plan[role][1]
        prio = mv.priority if mv is not None else 0
        prio += grassy_glide_priority_bonus(combatants[role], mv, field.terrain)
        side = "p1" if role in ("C", "P") else "p2"
        spd = effective_speed(combatants[role], field, side)
        theirs_first = 0 if role in ("E1", "E2") else 1  # ties resolve against us
        return (-prio, -spd, theirs_first)

    def doomed_roles(pl):
        """(doomed, sucker_punch_wasted) for `pl` -- `doomed`: roles whose
        hp would already be <=0, from an earlier-acting role's hit, by the
        time their own position in `pl`'s order comes up (a move that never
        actually fires). `sucker_punch_wasted`: roles that chose Sucker
        Punch, stayed alive, had a real target queued, but every one of
        those hits got dropped by the "target already moved" check. Same
        idea as `_apply_plan`'s own `doomed`/`sucker_punch_wasted`
        (`_resolve_turn`'s survival-aware reconsideration), scoped to this
        function's simpler single-turn, full-HP-start board (no Focus Sash/
        Sturdy or Protect here to also account for).

        Sorts by `pl`'s OWN move priorities, not the outer `plan` -- a
        trial plan with one role's move swapped must be ordered by ITS
        move, or a reconsideration can never actually change anything.
        """
        def pl_speed_key(role):
            mv = pl[role][1]
            prio = mv.priority if mv is not None else 0
            prio += grassy_glide_priority_bonus(combatants[role], mv, field.terrain)
            side = "p1" if role in ("C", "P") else "p2"
            spd = effective_speed(combatants[role], field, side)
            theirs_first = 0 if role in ("E1", "E2") else 1
            return (-prio, -spd, theirs_first)
        local_hp = dict(hp)
        found, sp_wasted = set(), set()
        pl_resolved = set()
        for role in sorted(pl.keys(), key=pl_speed_key):
            pl_resolved.add(role)
            hits, mv = pl[role]
            if local_hp.get(role, 0.0) <= 0:
                if mv is not None:
                    found.add(role)
                continue
            if mv is None:
                continue
            if mv.name == "Sucker Punch":
                # Same "must still be pending" rule the real application
                # loop uses -- a reconsideration must not credit Sucker
                # Punch with saving a role it wouldn't actually save.
                filtered = {tgt_role: got for tgt_role, got in hits.items()
                           if tgt_role not in pl_resolved
                           and pl.get(tgt_role, (None, None))[1] is not None
                           and pl[tgt_role][1].category != "Status"}
                if hits and not filtered:
                    sp_wasted.add(role)
                hits = filtered
            for tgt_role, got in hits.items():
                if local_hp.get(tgt_role, 1.0) <= 0:
                    continue
                local_hp[tgt_role] = max(0.0, local_hp[tgt_role] - got.frac)
        return found, sp_wasted

    # SURVIVAL-AWARE RECONSIDERATION -- see `_reconsider_for_survival`'s own
    # docstring for the full reasoning ("the enemy can sucker punch
    # Lycanroc ... if Lycanroc-Dusk targets Kingambit"; "Kingambit has no
    # reason to target Arcanine [with Sucker Punch]"). `partner`'s move is
    # a caller-fixed input (nothing to reconsider there); C/E1/E2 each get
    # one chance to swap to a strictly-faster move if a doomed pick would
    # never actually fire, or to any non-Sucker-Punch move if Sucker Punch
    # itself turned out to fail (the target had already moved).
    def build_hits(role, e, new_mv):
        if role == "C":
            return _hit_or_spread(attacker, new_mv, target_role, defenders,
                                  typechart, weather=weather, auras=auras,
                                  terrain=terrain)
        return _hit_or_spread(e, new_mv, "C", {"C": attacker}, typechart,
                              weather=weather, defending_side=our_side,
                              auras=auras, terrain=terrain)

    move_source = {"C": atk_moves, "E1": e1_moves, "E2": e2_moves}
    doomed, sp_wasted = doomed_roles(plan)
    for role in doomed:
        if role not in move_source:
            continue
        cur_mv = plan[role][1]
        if cur_mv is None:
            continue
        faster = [m for m in move_source[role]
                 if m.category != "Status"
                 and (m.power or m.name in WEIGHT_BASED_POWER)
                 and m.priority > cur_mv.priority]
        if not faster:
            continue
        e = combatants[role]
        target_for = combatants[target_role] if role == "C" else attacker
        defending_side_for = [e1, e2] if role == "C" else our_side
        _got2, new_mv = _choose_move(e, faster, target_for, typechart,
                                     weather=weather,
                                     defending_side=defending_side_for,
                                     auras=auras, terrain=terrain)
        if new_mv is None:
            continue
        trial_plan = dict(plan)
        trial_plan[role] = (build_hits(role, e, new_mv), new_mv)
        if role not in doomed_roles(trial_plan)[0]:
            plan[role] = trial_plan[role]
    for role in sp_wasted:
        if role not in move_source:
            continue
        alternatives = [m for m in move_source[role]
                       if m.name != "Sucker Punch" and m.category != "Status"
                       and (m.power or m.name in WEIGHT_BASED_POWER)]
        if not alternatives:
            continue
        e = combatants[role]
        target_for = combatants[target_role] if role == "C" else attacker
        defending_side_for = [e1, e2] if role == "C" else our_side
        _got2, new_mv = _choose_move(e, alternatives, target_for, typechart,
                                     weather=weather,
                                     defending_side=defending_side_for,
                                     auras=auras, terrain=terrain)
        if new_mv is None:
            continue
        plan[role] = (build_hits(role, e, new_mv), new_mv)

    order = sorted(plan.keys(), key=speed_key)

    pinned = False
    hits_done = {}
    turn_resolved = set()
    for role in order:
        turn_resolved.add(role)
        if hp[role] <= 0:
            if role == "C":
                pinned = True
            continue
        hits, mv = plan[role]
        if mv is None:
            continue
        if mv.name == "Sucker Punch":
            # "Sucker punch fails if the target outspeeds and use[s] a
            # priority move" -- same rule as `_apply_plan`'s: it only lands
            # against a target that has NOT YET acted this turn and whose
            # own queued move is damaging (not Status/Protect).
            hits = {tgt_role: got for tgt_role, got in hits.items()
                   if tgt_role not in turn_resolved
                   and plan.get(tgt_role, (None, None))[1] is not None
                   and plan[tgt_role][1].category != "Status"}
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
                item_overrides=None, move_overrides=None,
                excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
        if name == partner_name:
            continue
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
        if not move_names:
            continue
        moves = _move_infos(name, merged, moves_db, move_names)

        our_names = [name]
        our_items = {name: item}
        if partner_name is not None:
            if partner_item is None:
                p_item, _mv, _w = best_answer(partner_name, merged, moves_db, natures,
                                              typechart, target_names,
                                              excluded_items=excluded_items)
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


def _tailwind_move_for(combatant):
    """A no-op stand-in for "this role used Tailwind this turn" -- same
    role `_PROTECT_MOVE` plays for a forced Protect, substituted directly
    instead of calling `_choose_action` so casting it still counts as a
    real action (speed order, `enemy_acted`) but lands no hit. Priority
    depends on the CASTER (Tailwind is priority 0 normally, +1 under
    Prankster), unlike Protect's always-fixed +4, so this is built fresh
    per combatant rather than a single module-level constant like
    `_PROTECT_MOVE`.
    """
    priority = 1 if combatant.ability == "Prankster" else 0
    return MoveInfo("Tailwind", 0, "Normal", "Status", "allySide", priority=priority)


def _choose_action(attacker, moves, live_targets, typechart, weather=None,
                   hinted_target=None, attacker_hp_frac=None,
                   target_hp_fracs=None, auras=None, terrain=None,
                   attacker_role=None, dmg_mult_by_role=None,
                   half_damage_roles=frozenset()):
    """Best (hits: {role: Hit}, MoveInfo) for `attacker` against whichever of
    `live_targets` ({role: Combatant}) it ends up hitting.

    `attacker_role`/`dmg_mult_by_role`/`half_damage_roles`: the attacker's
    OWN role string, plus two low-cost stand-ins for general stat-stage
    tracking (which this module deliberately has none of -- see the module
    docstring) -- `dmg_mult_by_role` is `_intimidate_mult_by_role`'s static,
    computed-once-per-race map (Intimidate's exact -1 Atk x(2/3), or its
    Defiant/Competitive x2.0 inversion); `half_damage_roles` is `_joint_
    race`'s own turn-to-turn set of roles that have already used a
    `SELF_HALVING_MOVES` move earlier in this race. Both are applied to a
    Hit's damage fields the moment it's computed, BEFORE ranking -- so a
    halved/Intimidate-suppressed attacker is correctly deprioritized in its
    own KO-lookahead too, not just in the number it reports afterward.
    `attacker_role=None` (every caller outside `_resolve_turn`'s real
    joint-race engine) means neither applies at all, matching every other
    optional per-role map in this module.

    `attacker_hp_frac`/`target_hp_fracs` ({role: fraction}): the attacker's
    and each live target's REAL current HP as of the start of this turn --
    passed straight through to every `_raw_hit` call below (see its own
    docstring) so Multiscale and Eruption/Water Spout's HP-scaled power are
    correct for a role that already took damage earlier in a running
    multi-turn race, not just its first turn. `None` (the default, and
    every caller outside the joint race) keeps the old full-HP assumption.

    `auras`: the board's active Fairy Aura/Dark Aura/Aura Break set
    (`_active_auras`) -- passed straight through to `_raw_hit`.

    `terrain`: the board's active terrain (`_field_terrain`) -- passed
    straight through to `_raw_hit`.

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

    A priority move that would be blocked outright by Armor Tail / Dazzling /
    Queenly Majesty on the defending side (`_priority_blocked`, checked
    against the WHOLE of `live_targets` -- held by either enemy blocks it)
    is scored as if it does no damage and carries no priority, same as
    `_choose_move`.

    ONE-TURN LOOKAHEAD, same rule and reasoning as `_choose_move`: a move
    that doesn't KO (every hit target, for a spread move) by itself is still
    credited with a KO if its damage on a given target PLUS this attacker's
    own best available follow-up ON THAT SAME TARGET (any move against it,
    computed across every candidate here first) would clear 100%.

    PRIORITY IS NOT A GENERAL TIE-BREAK HERE, same reasoning and same one
    narrow exception as `_choose_move`: it let a weak priority move beat a
    far stronger slow one whenever both merely cleared the same "reaches a
    2-turn kill" bar, so raw damage is the tie-break there; a chosen move's
    real priority is still fully honoured in the turn-order computation
    once picked, and `_reconsider_for_survival` is the dedicated place
    priority-driven reconsideration otherwise belongs (surviving to act, or
    a Sucker Punch pick that would otherwise fail). But among candidates
    that ALREADY guarantee killing everything they hit THIS turn
    (`kos_now_count` tied at its own max), priority breaks the tie before
    raw damage does -- there is no damage tradeoff left to weigh between two
    moves that both finish the job, and going first is strictly better than
    going second on a target that dies either way.

    SPREAD MOVES RANK BY HOW MANY TARGETS THEY GUARANTEE KILLING RIGHT NOW,
    not an all-or-nothing "does it KO every target" flag: a Heat Wave that
    outright kills one of two live enemies (and merely chips the other)
    must still outrank a single-target Weather Ball that kills neither --
    even though neither move clears every target, and even though Weather
    Ball's OWN 2-turn-kill lookahead on its one target can look satisfied
    (fed by Heat Wave's own big hit on that same target as the "best
    follow-up") while Heat Wave's spread-wide `kos_in_two` fails on its
    weaker leg. `kos_now_count`/`kos_in_two_count` (0, 1, or more for a
    spread move; 0 or 1 for a single-target one) are compared before raw
    damage for exactly this reason -- an outright kill on even one target
    is worth more than any amount of un-lethal chip.
    """
    if not live_targets:
        return {}, None
    defending_side = list(live_targets.values())
    n_live = len(live_targets)

    def _scaled(got, mv):
        """Apply the Intimidate/Defiant/Competitive static multiplier and
        the Draco-Meteor-family halving to a freshly-computed Hit, before
        it's used for ranking. A no-op (returns `got` unchanged) whenever
        `attacker_role` wasn't given, or nothing applies to this attacker."""
        if attacker_role is None or got is NO_HIT:
            return got
        mult = 1.0
        if dmg_mult_by_role:
            cat = "physical" if mv.category == "Physical" else "special"
            mult *= dmg_mult_by_role.get(attacker_role, {}).get(cat, 1.0)
        if attacker_role in half_damage_roles:
            mult *= 0.5
        if mult == 1.0:
            return got
        return Hit(move_name=got.move_name, frac=got.frac * mult,
                  lo=got.lo * mult, avg=got.avg * mult, hi=got.hi * mult,
                  eff=got.eff, num_targets_hit=got.num_targets_hit)

    # Two passes: gather every candidate action's raw hits FIRST (tracking
    # the best single-hit damage this attacker can put on each target,
    # across every move here), then rank -- the lookahead below needs to
    # see every move's damage on a target before any of them can be scored.
    single_candidates = []   # (mv, role, Hit)
    spread_candidates = []   # (mv, {role: Hit})
    best_frac_by_role = {role: 0.0 for role in live_targets}
    for mv in moves:
        if mv.category == "Status":
            continue
        if not mv.power and mv.name not in WEIGHT_BASED_POWER:
            continue
        blocked = _priority_blocked(attacker, mv, defending_side)
        if is_spread_move(mv.target) and n_live > 1:
            hits = {role: _scaled(NO_HIT if blocked else
                          _raw_hit(attacker, mv, d, typechart, weather=weather,
                                   roll="avg", num_targets_hit=n_live,
                                   attacker_hp_frac=attacker_hp_frac,
                                   defender_hp_frac=(target_hp_fracs or {}).get(role),
                                   auras=auras, terrain=terrain), mv)
                   for role, d in live_targets.items()}
            spread_candidates.append((mv, hits))
            for role, h in hits.items():
                best_frac_by_role[role] = max(best_frac_by_role[role], h.frac)
            continue
        candidates = ([hinted_target] if hinted_target in live_targets
                      else list(live_targets))
        for role in candidates:
            got = _scaled(NO_HIT if blocked else _raw_hit(
                attacker, mv, live_targets[role], typechart, weather=weather,
                roll="avg", attacker_hp_frac=attacker_hp_frac,
                defender_hp_frac=(target_hp_fracs or {}).get(role), auras=auras,
                terrain=terrain), mv)
            single_candidates.append((mv, role, got))
            best_frac_by_role[role] = max(best_frac_by_role[role], got.frac)

    def remaining(role):
        """The target's ACTUAL current-HP fraction (of max HP) -- 1.0 (full
        health) unless `target_hp_fracs` says otherwise. This is what a
        guaranteed-kill check must clear, not a hardcoded 1.0: a target
        already sitting at 1 HP after surviving on Focus Sash needs only a
        sliver of `h.frac` (itself always read against MAX hp, `_raw_hit`'s
        own convention) to be a sure kill, and comparing it against a full
        100% instead systematically under-credits finishing it off --
        exactly the bug behind "why doesn't Kingambit finish the 1-HP
        Focus-Sash Lycanroc-Dusk instead of hitting the full-HP Metagross":
        both looked equally "not a guaranteed kill" under the old
        always-100% bar, so the tie-break fell to raw damage, which does
        not know one target is already on its last HP.
        """
        return (target_hp_fracs or {}).get(role, 1.0)

    best_key, best_hits, best_move = None, {}, None
    for mv, role, got in single_candidates:
        bar = remaining(role)
        kos_now_count = 1 if got.frac >= bar else 0
        kos_in_two_count = 1 if (kos_now_count or
                                 (got.frac + best_frac_by_role[role]) >= bar) else 0
        priority_if_kos_now = mv.priority if kos_now_count else 0
        key = (kos_now_count, kos_in_two_count, priority_if_kos_now, got.frac)
        if best_key is None or key > best_key:
            best_key, best_hits, best_move = key, {role: got}, mv
    for mv, hits in spread_candidates:
        kos_now_count = sum(1 for role, h in hits.items() if h.frac >= remaining(role))
        kos_in_two_count = sum(
            1 for role, h in hits.items()
            if h.frac >= remaining(role)
            or (h.frac + best_frac_by_role[role]) >= remaining(role))
        priority_if_kos_now = mv.priority if kos_now_count else 0
        key = (kos_now_count, kos_in_two_count, priority_if_kos_now,
              sum(h.frac for h in hits.values()))
        if best_key is None or key > best_key:
            best_key, best_hits, best_move = key, hits, mv
    return best_hits, best_move


def _apply_plan(plan, combatants, hp, protected_roles, enemy_speed_mult, field):
    """Resolve `plan` ({role: (hits, MoveInfo)}) in priority-then-speed
    order and apply every hit (Focus Sash/Sturdy honoured, a hit aimed at a
    protected role dropped) -- the one place `_resolve_turn`'s real
    application and its survival-reconsideration dry run
    (`_reconsider_for_survival`) share the EXACT same rules, so "would this
    role survive to act" can never quietly diverge from what actually
    happens when the plan is applied for real.

    Returns (hp, log, enemy_acted, wiped, doomed, sucker_punch_wasted) --
    `doomed`: roles whose hp was already <=0 by the time their own position
    in the order came up, despite having a real (non-`None`) move queued:
    chosen a move, in other words, that never actually got to fire.

    "Sucker punch fails if the target outspeeds and use[s] a priority
    move": it cannot retroactively read a move that's already happened, so
    it only lands against a target that has NOT YET acted this turn (still
    ahead of it in `order`) and whose own queued move is damaging (not
    Status/Protect) -- checked against `resolved` (every role already
    reached in this same walk of `order`), the same "still-pending, not the
    unmutated full list" rule `battle.py`'s real engine uses.
    `sucker_punch_wasted`: roles that chose Sucker Punch, were alive and had
    a real (non-empty) hit queued, but every one of those hits got dropped
    by the check above -- distinct from `doomed` (still alive, still had
    something worth doing) and worth a reconsideration pass same as it
    ("Kingambit has no reason to target Arcanine [with Sucker Punch]" --
    a whole turn spent on a move that was never going to land, when a
    normal attack sitting in the same moveset would have).

    RECOIL / LIFE ORB / ROUGH SKIN-IRON BARBS: applied to the ATTACKER's own
    `hp[role]` right after its hits this move land, mirroring `battle.py`'s
    own post-hit "attacker-side consequences" block -- see the inline
    comments below for the exact rules. All three can self-KO (real
    mechanic), and are applied BEFORE the `wiped` check below so a
    recoil/Rough-Skin KO correctly registers as a wipe.
    """
    def speed_key(role):
        mv = plan[role][1]
        prio = mv.priority if mv is not None else 0
        if mv is not None:
            prio += grassy_glide_priority_bonus(combatants[role], mv, field.terrain)
        side = "p1" if role in ("C", "P") else "p2"
        spd = effective_speed(combatants[role], field, side)
        if role in ("E1", "E2"):
            spd *= enemy_speed_mult
        theirs_first = 0 if role in ("E1", "E2") else 1  # ties resolve against us
        return (-prio, -spd, theirs_first)

    order = sorted(plan.keys(), key=speed_key)
    hp = dict(hp)
    log, enemy_acted, wiped = [], False, None
    doomed, resolved, sucker_punch_wasted = set(), set(), set()
    for role in order:
        resolved.add(role)
        hits, mv = plan[role]
        if hp[role] <= 0:
            if mv is not None:
                doomed.add(role)
            continue
        if role in ("E1", "E2") and mv is not None:
            enemy_acted = True
        if mv is None:
            continue
        if mv.name == "Sucker Punch":
            filtered = {tgt_role: got for tgt_role, got in hits.items()
                       if tgt_role not in resolved
                       and plan.get(tgt_role, (None, None))[1] is not None
                       and plan[tgt_role][1].category != "Status"}
            if hits and not filtered:
                sucker_punch_wasted.add(role)
            hits = filtered
        attacker_c = combatants[role]
        contact = bool((mv.flags or {}).get("contact"))
        raw_dmg_dealt = 0.0  # for recoil, mirrors battle.py's total_damage_dealt
        rough_skin_loss = 0.0
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
            # `got.frac` is a fraction of the DEFENDER's max HP (`_raw_hit`'s
            # own convention); recoil/Life Orb need raw HP relative to the
            # ATTACKER's own max HP, so convert through the target's max HP
            # first, same two-step `battle.py` does implicitly via real HP.
            if got.frac > 0:
                raw_dmg_dealt += got.frac * target_c.max_hp()
                # Rough Skin / Iron Barbs: punishes the ATTACKER for making
                # contact, even if this same hit faints the holder (real
                # mechanic -- the ability reacts to the contact itself, not
                # to the holder surviving it). Cheap-model only -- not
                # threaded into `battle.py` (not what was reported).
                if (contact and target_c.ability in ("Rough Skin", "Iron Barbs")
                        and attacker_c.ability != "Magic Guard"):
                    rough_skin_loss += 1 / 8
        if raw_dmg_dealt > 0:
            # Recoil (Flare Blitz/Wave Crash 33%, Head Smash 50%, ...) as a
            # fraction of TOTAL damage dealt this move -- Rock Head/Magic
            # Guard negate it. Mirrors `battle.py:980-989` exactly.
            if mv.recoil and attacker_c.ability not in ("Rock Head", "Magic Guard"):
                num, den = mv.recoil
                recoil_raw = raw_dmg_dealt * num / den
                hp[role] = max(0.0, hp[role] - recoil_raw / attacker_c.max_hp())
            # Life Orb: flat 10% max HP recoil on any damaging move that
            # connected -- Magic Guard blocks it, Sheer Force cancels it
            # when the boosted move has a secondary effect (its signature
            # interaction). Mirrors `battle.py:1001-1017`.
            sheer_force_cancels_lo = (attacker_c.ability == "Sheer Force"
                                      and bool(mv.secondary))
            if (attacker_c.item == "Life Orb" and attacker_c.ability != "Magic Guard"
                    and not sheer_force_cancels_lo):
                hp[role] = max(0.0, hp[role] - 0.10)
        if rough_skin_loss:
            hp[role] = max(0.0, hp[role] - rough_skin_loss)
        if wiped is None:
            if hp["E1"] <= 0 and hp["E2"] <= 0:
                wiped = "theirs"
            elif hp["C"] <= 0 and hp["P"] <= 0:
                wiped = "ours"
    return hp, log, enemy_acted, wiped, doomed, sucker_punch_wasted


def _reconsider_for_survival(plan, doomed, sucker_punch_wasted, combatants,
                             moves_by_role, hp, typechart, weather, field,
                             live_targets_by_role, hint_by_role,
                             enemy_speed_mult, protected_roles, auras=None,
                             dmg_mult_by_role=None, half_damage_roles=frozenset()):
    """"It is not a clean win if the enemy protects one then uses a
    priority move on Lycanroc-Dusk" -- a provisional `plan` chooses every
    actor's move independently, unaware of the others, so an actor can end
    up "choosing" a move that never actually fires because something FASTER
    kills it first, in the very same turn, before its own turn ever comes
    up (`_apply_plan`'s own `doomed` set -- e.g. Kingambit picking Iron
    Head, a guaranteed KO, over Sucker Punch, when Lycanroc-Dusk's own
    Close Combat is about to kill Kingambit first). A move that never fires
    contributes nothing, so any doomed role that has a move in its own
    moveset with STRICTLY HIGHER priority than its current pick (the only
    lever that can move it earlier in the order -- speed is a property of
    the combatant, not the move) is retried: `_choose_action` picks the
    best of those faster options the normal way, and it's adopted only if
    a trial `_apply_plan` confirms this role is no longer doomed under it.
    A role with no such option, or where even the fastest alternative still
    doesn't arrive in time, keeps its original pick -- it really will die
    before acting, exactly as if this reconsideration didn't exist.

    `sucker_punch_wasted` is the OTHER way a chosen move can turn out to
    contribute nothing: alive, with a real target, but Sucker Punch itself
    failed (the target had already moved) -- unlike a doomed role, priority
    isn't the fix here (Sucker Punch is already the highest-priority option
    most movesets carry); the fix is simply "use something else that
    doesn't have a fail condition attached". Every non-Sucker-Punch, non-
    Status move in the role's own moveset is retried via `_choose_action`
    (already lookahead-aware) and adopted directly -- no trial-replay guard
    needed here, since an ordinary move has no analogous condition to fail
    against ("Kingambit has no reason to target Arcanine [with Sucker
    Punch]" -- Iron Head/Kowtow Cleave/Low Kick sitting in the same
    moveset would have landed for real).

    ONE PASS, not a fixed point, and only the SINGLE best-ranked
    alternative per role is tried in either case: reassigning one role can
    in principle change another's own doomed/wasted status -- but with
    only 4 actors on a real board this already covers the cases that
    motivated it, and the module stays a cheap arithmetic screen, not an
    exhaustive search of every ordering.
    """
    new_plan = dict(plan)
    for role in doomed:
        if role in protected_roles or role not in plan:
            continue
        cur_mv = plan[role][1]
        if cur_mv is None:
            continue
        faster = [mv for mv in (moves_by_role.get(role) or [])
                 if mv.category != "Status"
                 and (mv.power or mv.name in WEIGHT_BASED_POWER)
                 and mv.priority > cur_mv.priority]
        if not faster:
            continue
        hits, mv = _choose_action(combatants[role], faster,
                                  live_targets_by_role[role], typechart,
                                  weather=weather,
                                  hinted_target=(hint_by_role or {}).get(role),
                                  attacker_hp_frac=hp[role], target_hp_fracs=hp,
                                  auras=auras, terrain=field.terrain,
                                  attacker_role=role, dmg_mult_by_role=dmg_mult_by_role,
                                  half_damage_roles=half_damage_roles)
        if mv is None:
            continue
        trial_plan = dict(new_plan)
        trial_plan[role] = (hits, mv)
        _hp2, _log2, _ea2, _w2, doomed2, _sp2 = _apply_plan(
            trial_plan, combatants, hp, protected_roles, enemy_speed_mult, field)
        if role not in doomed2:
            new_plan[role] = (hits, mv)
    for role in sucker_punch_wasted:
        if role in protected_roles or role not in plan:
            continue
        alternatives = [mv for mv in (moves_by_role.get(role) or [])
                       if mv.name != "Sucker Punch" and mv.category != "Status"
                       and (mv.power or mv.name in WEIGHT_BASED_POWER)]
        if not alternatives:
            continue
        hits, mv = _choose_action(combatants[role], alternatives,
                                  live_targets_by_role[role], typechart,
                                  weather=weather,
                                  hinted_target=(hint_by_role or {}).get(role),
                                  attacker_hp_frac=hp[role], target_hp_fracs=hp,
                                  auras=auras, terrain=field.terrain,
                                  attacker_role=role, dmg_mult_by_role=dmg_mult_by_role,
                                  half_damage_roles=half_damage_roles)
        if mv is None:
            continue
        new_plan[role] = (hits, mv)
    return new_plan


_ALLY_OF = {"C": "P", "P": "C", "E1": "E2", "E2": "E1"}


def _with_ally_splash(plan, combatants, hp, typechart, weather, terrain, auras):
    """`allAdjacent` moves (Earthquake, Surf, Discharge, Bulldoze, Explosion
    -- `damage.hits_ally`) also hit the user's own live partner. `_choose_
    action` itself still picks the best move purely against the OPPOSING
    side (hitting your own ally is a real cost applied AFTER the choice is
    made, never a reason its own ranking would pick a different move) -- so
    this runs as a small post-process on `plan`, right before `_apply_plan`,
    rather than inside `_choose_action`'s scoring. That also sidesteps the
    one real hazard here: a spread-hit ranking that didn't know "enemy" from
    "own ally" could credit a self-KO as a win. Called on the FINAL `plan`
    (after `_reconsider_for_survival` too, if it ran), so it's always the
    role's actually-chosen move being checked.

    DOCUMENTED SIMPLIFICATION: when exactly one enemy is alive, the existing
    enemy-hit in `hits` was already computed by `_choose_action` at
    `num_targets_hit=1` (no 0.75x, since `is_spread_move(...) and n_live > 1`
    didn't fire there). This does not retroactively recompute that hit once
    adding the ally makes it a real 2-target spread -- only the ally's OWN
    new hit gets the correct `num_targets_hit`. Both-enemies-alive (the
    common case) was already correct and is untouched.
    """
    new_plan = dict(plan)
    for role, (hits, mv) in plan.items():
        if mv is None or not hits_ally(mv.target):
            continue
        ally_role = _ALLY_OF[role]
        if hp.get(ally_role, 0.0) <= 0:
            continue
        n = max(2, len(hits) + 1)  # already-hit enemies + the ally
        got = _raw_hit(combatants[role], mv, combatants[ally_role], typechart,
                       weather=weather, roll="avg", num_targets_hit=n,
                       defender_hp_frac=hp.get(ally_role), auras=auras,
                       terrain=terrain)
        new_plan[role] = ({**hits, ally_role: got}, mv)
    return new_plan


def _resolve_turn(combatants, moves_by_role, hp, typechart, weather, our_hints,
                  enemy_speed_mult=1.0, protected_roles=frozenset(),
                  recharging_roles=frozenset(), tailwind_setter_role=None,
                  terrain=None, dmg_mult_by_role=None,
                  half_damage_roles=frozenset()):
    """One turn, given OUR target hints ({role: enemy_role_or_None}) -- the
    enemy side chooses independently and greedily (`_choose_action` with no
    hint), same "no coordination" behaviour `_sequential_pair_outcome`
    already gives E1/E2. Does NOT mutate `hp` -- returns a fresh dict, log,
    whether an enemy actually got to act, which side (if either) was fully
    fainted DURING this turn's resolution (checked after every actor's hits
    land, so a true same-turn mutual wipe is still attributed to whichever
    side went down first in the actual initiative order, not left
    ambiguous), and which roles must recharge NEXT turn (below).

    RECHARGE (Hyper Beam, Giga Impact, ...): `recharging_roles` are roles
    that used a recharge move (`move.flags.get("recharge")`) LAST turn and
    so are forced to do nothing this turn -- substituted directly with
    `({}, None)` instead of calling `_choose_action`, mirroring
    `battle.py`'s real `must_recharge` lockout (a recharging Pokemon cannot
    even Protect). The 5th return value is the NEW set of roles who must
    recharge NEXT turn -- every role that used a recharge move THIS turn and
    actually got to act (not doomed): `_joint_race`'s own turn loop carries
    this forward as `recharging_roles` on its following `_best_turn` call,
    the same "persists across turns" pattern `_apply_plan`'s `doomed`/
    `sucker_punch_wasted` do NOT need (those are resolved within one turn).
    Unlike `protected_roles` (only ever set for turn 1, by
    `_pair_vs_targets`'s Protect-robustness replay), recharge is real
    ongoing state the turn loop must actually carry.

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

    `tailwind_setter_role`: one enemy role ("E1"/"E2") that casts Tailwind
    THIS turn instead of attacking -- `_tailwind_move_for` substituted
    directly, same "still a real action, lands no hit" pattern
    `protected_roles` uses. `_joint_race`'s own turn loop is what actually
    makes this mean anything speed-wise (only passing this on turn 1, and
    only applying the real `enemy_speed_mult` from turn 2 onward) -- see
    its own docstring for the full "test the setter actually choosing to
    set it, not an instant free boost" reasoning.

    `weather` is the ONE shared field value (`_field_weather`'s own return)
    -- applied to BOTH sides' damage (a Fire move is sun-boosted no matter
    which side casts it) and, via a real `FieldState(weather=weather)`
    rather than a bare one, to BOTH sides' turn order too (Swift Swim/
    Chlorophyll/Sand Rush/Slush Rush all key off the field, not off who
    happens to be attacking).

    `terrain` is the same idea for the ONE shared field terrain
    (`_field_terrain`'s own return) -- applied to both sides' damage (the
    Grass boost/Earthquake reduction) and, via the same `FieldState`, to
    turn order (Grassy Glide's priority bump for a grounded user).

    SURVIVAL-AWARE RECONSIDERATION: after the provisional plan is built, any
    role about to die before its own turn comes up gets one chance to
    reconsider toward a faster move that would actually land, and any role
    whose chosen Sucker Punch turned out to fail (the target had already
    moved) gets one chance to reconsider toward a different, unconditional
    move instead -- see `_reconsider_for_survival`. Only runs (a second,
    cheap `_apply_plan` pass) when the provisional plan actually leaves
    someone doomed or Sucker-Punch-wasted, so an ordinary turn costs
    exactly what it always did.

    An `allAdjacent` move (Earthquake and friends) also hits the user's own
    live partner -- see `_with_ally_splash`, applied to `plan` right before
    each `_apply_plan` call so it always reflects the FINAL chosen move.

    `dmg_mult_by_role`/`half_damage_roles`: Intimidate/Defiant/Competitive's
    static per-role multiplier and the Draco-Meteor-family's turn-to-turn
    halving set -- both `_joint_race`'s own board-level state, threaded
    straight through to every `_choose_action` call (both sides) so ranking
    itself already reflects them (see `_choose_action`'s own docstring).
    """
    hp = dict(hp)
    ours_live = {r: combatants[r] for r in ("C", "P") if hp[r] > 0}
    theirs_live = {r: combatants[r] for r in ("E1", "E2") if hp[r] > 0}
    field = FieldState(weather=weather, terrain=terrain)
    auras = _active_auras(combatants, hp)

    plan = {}
    for role, c in ours_live.items():
        if role in recharging_roles:
            plan[role] = ({}, None)
        else:
            plan[role] = _choose_action(c, moves_by_role[role], theirs_live,
                                        typechart, weather=weather,
                                        hinted_target=our_hints.get(role),
                                        attacker_hp_frac=hp[role],
                                        target_hp_fracs=hp, auras=auras,
                                        terrain=terrain, attacker_role=role,
                                        dmg_mult_by_role=dmg_mult_by_role,
                                        half_damage_roles=half_damage_roles)
    for role, c in theirs_live.items():
        if role in recharging_roles:
            plan[role] = ({}, None)
        elif role == tailwind_setter_role:
            plan[role] = ({}, _tailwind_move_for(c))
        elif role in protected_roles:
            plan[role] = ({}, _PROTECT_MOVE)
        else:
            plan[role] = _choose_action(c, moves_by_role[role], ours_live,
                                        typechart, weather=weather,
                                        attacker_hp_frac=hp[role],
                                        target_hp_fracs=hp, auras=auras,
                                        terrain=terrain, attacker_role=role,
                                        dmg_mult_by_role=dmg_mult_by_role,
                                        half_damage_roles=half_damage_roles)

    plan = _with_ally_splash(plan, combatants, hp, typechart, weather, terrain, auras)
    hp2, log, enemy_acted, wiped, doomed, sp_wasted = _apply_plan(
        plan, combatants, hp, protected_roles, enemy_speed_mult, field)
    final_doomed = doomed
    if (doomed - protected_roles) or (sp_wasted - protected_roles):
        live_targets_by_role = {r: theirs_live for r in ours_live}
        live_targets_by_role.update({r: ours_live for r in theirs_live})
        plan = _reconsider_for_survival(
            plan, doomed, sp_wasted, combatants, moves_by_role, hp, typechart,
            weather, field, live_targets_by_role, our_hints, enemy_speed_mult,
            protected_roles, auras, dmg_mult_by_role=dmg_mult_by_role,
            half_damage_roles=half_damage_roles)
        plan = _with_ally_splash(plan, combatants, hp, typechart, weather, terrain, auras)
        hp2, log, enemy_acted, wiped, final_doomed, _sp2 = _apply_plan(
            plan, combatants, hp, protected_roles, enemy_speed_mult, field)
    recharging_next = {role for role, (_hits, mv) in plan.items()
                       if role not in final_doomed and mv is not None
                       and mv.flags and mv.flags.get("recharge")}
    return hp2, log, enemy_acted, wiped, recharging_next


def _best_turn(combatants, moves_by_role, hp, typechart, weather,
              enemy_speed_mult=1.0, protected_roles=frozenset(),
              recharging_roles=frozenset(), tailwind_setter_role=None,
              terrain=None, dmg_mult_by_role=None,
              half_damage_roles=frozenset()):
    """Try every combination of OUR target hints for this turn -- the same
    "exhaustive over permutations, the better outcome is kept" `pair_search`
    already promises, generalised from one candidate (plus an optional
    partner locked to ONE named move) to two full attackers each choosing
    their own move. At most 2x2=4 combinations (fewer once one side is down
    to one live attacker or the other side to one live target), so this stays
    cheap per turn.

    `protected_roles`/`recharging_roles`/`tailwind_setter_role` are passed
    straight through to `_resolve_turn` -- see its own docstring for how a
    protected, recharging, or tailwind-casting role naturally falls out of
    OUR side's ranking here with no change to the ranking itself.

    Ranked by (enemies KO'd this turn, -ours KO'd this turn, net fractional
    damage this turn) -- "best FOR US", matching every other joint search in
    this module ranking on the attacker's own outcome, not the enemy's.

    Returns (`new_hp`, `log`, `enemy_acted`, `wiped`, `recharging_next`) --
    the winning hint combo's own `_resolve_turn` results, `recharging_next`
    included so `_joint_race`'s turn loop can carry it into the FOLLOWING
    turn's call.
    """
    ours_live = [r for r in ("C", "P") if hp[r] > 0]
    theirs_live_roles = [r for r in ("E1", "E2") if hp[r] > 0]
    hint_options = theirs_live_roles or [None]
    best = None
    for combo in itertools.product(hint_options, repeat=max(1, len(ours_live))):
        hints = dict(zip(ours_live, combo))
        new_hp, log, enemy_acted, wiped, recharging_next = _resolve_turn(
            combatants, moves_by_role, hp, typechart, weather, hints,
            enemy_speed_mult=enemy_speed_mult, protected_roles=protected_roles,
            recharging_roles=recharging_roles,
            tailwind_setter_role=tailwind_setter_role, terrain=terrain,
            dmg_mult_by_role=dmg_mult_by_role, half_damage_roles=half_damage_roles)
        enemies_ko = sum(1 for r in ("E1", "E2") if hp[r] > 0 and new_hp[r] <= 0)
        ours_ko = sum(1 for r in ("C", "P") if hp[r] > 0 and new_hp[r] <= 0)
        dmg_dealt = sum(hp[r] - new_hp[r] for r in ("E1", "E2"))
        dmg_taken = sum(hp[r] - new_hp[r] for r in ("C", "P"))
        key = (enemies_ko, -ours_ko, dmg_dealt - dmg_taken)
        if best is None or key > best[0]:
            best = (key, new_hp, log, enemy_acted, wiped, recharging_next)
        if not ours_live:
            break  # nothing of ours can act -- one combo (the empty one) is all there is
    return best[1], best[2], best[3], best[4], best[5]


def _joint_race(combatants, moves_by_role, typechart, weather, turns,
                enemy_speed_mult=1.0, first_turn_moves_override=None,
                first_turn_protected_role=None, first_turn_tailwind_role=None,
                terrain=None):
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

    FAKE OUT / FIRST IMPRESSION (`FIRST_TURN_ONLY_MOVES`) are legal ONLY the
    turn a role is genuinely fresh (see `still_fresh` below) -- this is the
    one piece of real per-role state this loop enforces on `moves_by_role`
    itself (not `_resolve_turn`'s recharge/reconsideration machinery), since
    it has to be applied before a role's moveset is ever offered to
    `_choose_action`. Previously unenforced here at all (`_sequential_pair_
    outcome`, `pair_search`'s single-turn hypothesis, needs no such gate --
    it only ever plays exactly one turn, so Fake Out is always legal there
    by construction).

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

    `first_turn_tailwind_role`: optional single enemy role that spends turn 1
    CASTING Tailwind (a no-op action, same shape as `first_turn_protected_role`)
    instead of attacking -- modeling the real cost of actually setting it,
    rather than assuming it is simply already up. `enemy_speed_mult` (the
    caller-supplied Tailwind multiplier) is only applied from turn 2 onward;
    turn 1 always races at normal speed, since the boost doesn't exist until
    the setter's own action resolves. This module has no intra-turn re-sort
    (`battle.py`'s real engine does re-sort so a same-turn Tailwind can still
    help a not-yet-acted teammate that turn -- see its comment near the
    Tailwind field-effect application), so this is a deliberate, documented
    simplification: the boost is available strictly starting the turn after
    it's cast.

    RECHARGE (Hyper Beam, Giga Impact, ...): `_best_turn`'s own
    `recharging_next` return is carried forward as the NEXT call's
    `recharging_roles` -- the one piece of real cross-turn state this loop
    threads through, since `_resolve_turn`'s own doomed/Sucker-Punch-wasted
    reconsideration is entirely resolved within a single turn and needs
    nothing carried.

    INTIMIDATE / DEFIANT / COMPETITIVE: `_intimidate_mult_by_role`'s static
    per-role multiplier, computed ONCE here from the INITIAL `combatants` --
    correct to compute once, not re-checked every turn, since the real -1
    Atk stage Intimidate applies on switch-in persists even after the
    holder itself later faints mid-race.

    DRACO METEOR FAMILY (`SELF_HALVING_MOVES`): `half_damage`, a role set
    that starts empty and is threaded turn-to-turn exactly like
    `recharging` above -- after each turn, any role whose move this turn
    was in `SELF_HALVING_MOVES` (read straight off `turn_log`, no extra
    `_best_turn`/`_resolve_turn` return value needed) is added for every
    turn AFTER this one (first use still deals full damage, matching the
    real self-effect's timing).
    """
    hp = {"C": 1.0, "P": 1.0, "E1": 1.0, "E2": 1.0}
    any_enemy_acted = False
    wiped_side, turns_used = None, 0
    full_log = []
    recharging = frozenset()
    dmg_mult_by_role = _intimidate_mult_by_role(combatants)
    half_damage = frozenset()
    # Fake Out / First Impression are only legal the turn a Pokemon is sent
    # out. Every role starts "fresh"; a role loses freshness the first turn
    # it's actually OFFERED a real (non-empty) moveset -- not simply
    # `turn_i == 0`, since `switch_in_search`'s `first_turn_moves_override`
    # can hand a role an EMPTY list on turn 0 (it hasn't switched in with
    # anything to choose yet), meaning ITS real first active turn -- and so
    # its own Fake-Out-legal turn -- is turn_i == 1, not turn_i == 0.
    still_fresh = set(moves_by_role.keys())
    for turn_i in range(max(1, turns)):
        if (hp["E1"] <= 0 and hp["E2"] <= 0) or (hp["C"] <= 0 and hp["P"] <= 0):
            break
        turn_moves = moves_by_role
        if turn_i == 0 and first_turn_moves_override:
            turn_moves = {**moves_by_role, **first_turn_moves_override}
        turn_moves = {
            role: ([m for m in mvs if m.name not in FIRST_TURN_ONLY_MOVES]
                  if role not in still_fresh else mvs)
            for role, mvs in turn_moves.items()
        }
        for role, mvs in turn_moves.items():
            if mvs:
                still_fresh.discard(role)
        protected = ({first_turn_protected_role}
                    if turn_i == 0 and first_turn_protected_role else frozenset())
        tailwind_role_this_turn = (first_turn_tailwind_role
                                   if turn_i == 0 and first_turn_tailwind_role else None)
        mult_this_turn = (1.0 if (first_turn_tailwind_role and turn_i == 0)
                          else enemy_speed_mult)
        hp, turn_log, enemy_acted, wiped, recharging = _best_turn(
            combatants, turn_moves, hp, typechart, weather,
            enemy_speed_mult=mult_this_turn, protected_roles=protected,
            recharging_roles=recharging, tailwind_setter_role=tailwind_role_this_turn,
            terrain=terrain, dmg_mult_by_role=dmg_mult_by_role,
            half_damage_roles=half_damage)
        full_log.append(turn_log)
        any_enemy_acted = any_enemy_acted or enemy_acted
        turns_used = turn_i + 1
        half_damage |= {role for role, _tgt, h in turn_log
                        if h.move_name in SELF_HALVING_MOVES}
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


def _grid_hit(attacker, moves, target, other_live, typechart, weather=None,
              auras=None, terrain=None):
    """The best `Hit` `attacker`'s own moveset can land on `target`
    SPECIFICALLY -- one cell of the 2x2 damage grid, not the move a
    target-choosing AI would actually pick (that's `_choose_action`).

    `auras`: the board's active Fairy Aura/Dark Aura/Aura Break set
    (`_active_auras`), same field-wide reading `_choose_action` gets.

    A spread move still takes the doubles 0.75x penalty whenever
    `other_live` (the OTHER Pokemon on the target's side) is not None --
    hitting `target` at all means hitting `other_live` too, regardless of
    which single cell is being asked about, same rule `_choose_action` and
    `_hit_or_spread` already apply. Average roll, matching every other
    "realistic exchange" reading in this module (`pair_search`'s own).

    A priority move blocked outright by Armor Tail / Dazzling / Queenly
    Majesty on `target`'s side (`_priority_blocked`, checked against
    `target` + `other_live`) is scored as doing no damage, same as
    `_choose_move`/`_choose_action` -- this "best cell" should never show a
    number a real attack could not actually land.

    ONE-TURN LOOKAHEAD, same rule as `_choose_move`: a move that doesn't KO
    by itself is still credited with a KO if its damage plus this
    attacker's own best available follow-up would clear 100% within 2 turns.

    PRIORITY IS NOT A TIE-BREAK HERE, same reasoning as `_choose_move`/
    `_choose_action` -- a weak priority move (Sucker Punch, Aqua Jet, ...)
    no longer beats a far stronger slow one just for being priority; raw
    damage decides once KO-status ties. This also happens to be the honest
    answer for Sucker Punch specifically: this "one cell" has no idea
    whether the target has already moved (`_apply_plan`'s Sucker-Punch-fail
    rule needs the target's SPECIFIC chosen action, information a
    context-free cell doesn't have), so it was never safe to credit its
    priority here regardless -- exactly what produced "Kingambit has no
    reason to target Arcanine with Sucker Punch" before this fix.
    """
    defending_side = [target, other_live]
    candidates = []
    for mv in moves:
        if mv.category == "Status":
            continue
        if not mv.power and mv.name not in WEIGHT_BASED_POWER:
            continue
        if _priority_blocked(attacker, mv, defending_side):
            got = NO_HIT
        else:
            n = 2 if (is_spread_move(mv.target) and other_live is not None) else 1
            got = _raw_hit(attacker, mv, target, typechart, weather=weather,
                           roll="avg", num_targets_hit=n, auras=auras,
                           terrain=terrain)
        candidates.append((mv, got))
    if not candidates:
        return NO_HIT
    best_follow_up = max(got.frac for _mv, got in candidates)
    best_key, best_hit = None, NO_HIT
    for mv, got in candidates:
        kos_now = got.frac >= 1.0
        kos_in_two = kos_now or (got.frac + best_follow_up) >= 1.0
        key = (kos_now, kos_in_two, got.frac)
        if best_key is None or key > best_key:
            best_key, best_hit = key, got
    return best_hit


def _damage_grid(c1, c2, e1c, e2c, m1, m2, e1m, e2m, typechart, weather,
                 terrain=None):
    """Every one of the 8 attacker-vs-specific-defender `Hit`s on this board
    -- "see if and how I out-trade (2x2 damage)" asks for the actual numbers,
    not just which line the race happened to choose. Returns {"ours": {("C",
    "E1"): Hit, ("C","E2"): Hit, ("P","E1"): Hit, ("P","E2"): Hit}, "theirs":
    {("E1","C"): Hit, ("E1","P"): Hit, ("E2","C"): Hit, ("E2","P"): Hit}}.
    """
    auras = _active_auras({"C": c1, "P": c2, "E1": e1c, "E2": e2c})
    ours = {
        ("C", "E1"): _grid_hit(c1, m1, e1c, e2c, typechart, weather, auras=auras, terrain=terrain),
        ("C", "E2"): _grid_hit(c1, m1, e2c, e1c, typechart, weather, auras=auras, terrain=terrain),
        ("P", "E1"): _grid_hit(c2, m2, e1c, e2c, typechart, weather, auras=auras, terrain=terrain),
        ("P", "E2"): _grid_hit(c2, m2, e2c, e1c, typechart, weather, auras=auras, terrain=terrain),
    }
    theirs = {
        ("E1", "C"): _grid_hit(e1c, e1m, c1, c2, typechart, weather, auras=auras, terrain=terrain),
        ("E1", "P"): _grid_hit(e1c, e1m, c2, c1, typechart, weather, auras=auras, terrain=terrain),
        ("E2", "C"): _grid_hit(e2c, e2m, c1, c2, typechart, weather, auras=auras, terrain=terrain),
        ("E2", "P"): _grid_hit(e2c, e2m, c2, c1, typechart, weather, auras=auras, terrain=terrain),
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


def _pruned_entry():
    """A placeholder `detail[(e1,e2)]` entry for an enemy pair `_pair_vs_
    targets` never actually raced -- see its `prune_below` parameter.
    Always "loss": the WORST case, same direction every other minimax in
    this module already biases toward (mega assignment, enemy Protect/
    Tailwind choice) -- never makes a pruned pair look better than it can
    possibly be, only (knowingly) worse than it might actually be. Built
    fresh each call since `detail`'s own consumers (`_pair_vs_targets`
    itself, want_grid's cleanup) pop keys off individual entries; a single
    shared dict reused across every pruned key would let one caller's pop
    silently affect all the others.
    """
    return {
        "outcome": "loss", "outcome_without_tailwind": "loss",
        "tailwind_is_real_threat": False, "tailwind_forced": False,
        "turns_used": 0, "tailwind_outcome": "loss", "tailwind_safe": False,
        "protect_outcomes": {"E1": "loss", "E2": "loss"}, "protect_safe": False,
        "log": [], "_pruned": True,
        "our_hp": {"C": 0.0, "P": 0.0}, "clean_win_value": 0.0,
    }


def _pair_vs_targets(n1, n2, our_built, target_names, enemy_built, typechart,
                     turns, want_grid=False, merged=None, prune_below=None,
                     forced_base_names=frozenset()):
    """(detail, summary) for OUR pair (`n1`, `n2`, drawn from `our_built`, a
    `_build_forms` dict) against every pair drawn from `target_names` -- the
    one place a joint pair is actually raced, so `joint_pair_search`
    (partner fixed) and `joint_pool_search` (both slots searched) can never
    drift apart on what "beats" means.

    `forced_base_names`: passed straight through to `_resolve_forms` for OUR
    side only (the enemy's own mega choice stays unconstrained) -- a name in
    here is locked to base form for this whole race, never offered as the
    transformer. Default empty set changes nothing.

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

    TAILWIND AS AN ASSUMED THREAT, not just a hypothesis footnote: "If the
    enemy has a tailwind setter and tailwind is a loss, assume they set
    tailwind and see if it is a loss." -- but "assume they set tailwind"
    means actually spending turn 1 casting it (`first_turn_tailwind_role`),
    not treating the boost as already up before either enemy has acted: a
    same-turn Tailwind cast doesn't speed up the caster's OWN attack that
    turn, and this module doesn't re-sort mid-turn the way `battle.py`'s
    real engine does. When either `e1_name` or `e2_name` has REAL usage-data
    access to Tailwind (`merged[name]["moves_usage"]`, the same real-
    movepool source `_field_weather`/ability lookups already trust), the
    race is replayed once per real setter role with THAT role casting
    Tailwind turn 1 (worst-for-us kept, when both enemies can set it); if
    that comes out WORSE than the normal-speed one (`_JOINT_OUTCOME_RANK`),
    the tailwind race's own outcome/turns_used/log REPLACE the normal ones
    as `outcome`/`turns_used`/`log` -- feeding the summary counts and the
    worst-enemy-mega minimax below exactly the way a real threat should,
    rather than being reported as a win with a buried caveat. `merged=None`
    (no usage data available) or neither enemy actually knowing Tailwind
    leaves promotion a no-op: `outcome` stays the normal-speed result, same
    as before this was added, and `tailwind_outcome`/`tailwind_safe` fall
    back to the plain instant-speed-doubling hypothesis (no specific real
    setter to cast it realistically for) -- the same reading
    `switch_in_search`'s own separate, purely-informational tailwind check
    still uses everywhere. The pre-override result survives either way as
    `outcome_without_tailwind`; `tailwind_forced` says whether the swap
    happened.

    `prune_below`: optional fraction (e.g. `good_threshold`) -- once it is
    MATHEMATICALLY CERTAIN this pair cannot reach that share of
    `target_names`'s enemy pairs beaten (even if every remaining, not-yet-
    raced enemy pair were a win), stop racing and fill the rest of `detail`
    with `_pruned_entry()` (a "loss" placeholder) instead. This is a SOUND
    bound, not a heuristic guess -- unlike `prescreen.py`'s own measured,
    abandoned attempt at this (see its module docstring: a cheap proxy
    score deleted the eventual winner 85% of the time), it can never
    discard a pair that could actually still qualify, because it only ever
    fires once qualifying is already provably impossible. It CAN understate
    a pruned pair's true performance against the enemy pairs it never
    raced (their real outcome might have been a win) -- always in the
    worst-case direction, same bias every other minimax in this module
    already carries, never the other way. `None` (the default) disables
    this entirely; every enemy pair is always raced. Left off by
    `bring4_search`'s own Stage 1 (needs each of a fixed 6's exact C(6,2)
    pair performances, not an early "this one's definitely bad" verdict --
    "a check for a promising bring-4" needs the real numbers); turned on by
    `multi_bring4_coverage`'s own pool-wide Stage A, where the pool can be
    large (up to 300) and most candidates are mediocre.

    WIN QUALITY, not just win/loss: "losing 1 pokemon and taking a lot of
    damage and KOing 2 enemies [is] far inferior to KOing the enemy without
    taking damage" -- `sweep` and `out_trade` are both "beaten" for
    `pairs_swept`/`pairs_traded`/ranking-by-raw-count purposes, but a messy
    `out_trade` (one of ours fainted, the other badly chipped) and a clean
    `sweep` (neither of ours ever took a hit) used to score identically.
    Each `detail` entry also carries `our_hp` ({"C": frac, "P": frac}, read
    from the CHOSEN race's own final `hp` -- the Tailwind race's if
    `tailwind_forced`, the normal one otherwise) and `clean_win_value`
    (`our_hp["C"] + our_hp["P"]`, 0.0-2.0): exactly 2.0 for every `sweep`
    (a sweep is BY DEFINITION zero damage taken), lower for a chippy
    `out_trade`, at or near 0 for one that cost a whole Pokemon. Always 0.0
    for `loss`/`no_ko` -- whatever HP happened to survive a real loss isn't
    a quality signal worth ranking on, the outcome bucket itself already
    says "bad". `pairs_clean_win_total` (the summary's own sum of this
    across every enemy pair) is what `_pair_sort_key` uses to tell two
    equally-"beaten" pairs apart -- see its own docstring for where in the
    ranking this sits.
    """
    m1, m2 = our_built[n1]["moves"], our_built[n2]["moves"]
    all_enemy_pairs = list(itertools.combinations(target_names, 2))
    total_pairs = len(all_enemy_pairs)
    detail = {}
    for pair_idx, (e1_name, e2_name) in enumerate(all_enemy_pairs):
        if prune_below is not None and detail:
            beaten_so_far = sum(1 for d in detail.values()
                                if d["outcome"] in ("sweep", "out_trade"))
            remaining = total_pairs - len(detail)
            best_possible = (beaten_so_far + remaining) / total_pairs
            if best_possible < prune_below:
                for e1_r, e2_r in all_enemy_pairs[pair_idx:]:
                    detail[(e1_r, e2_r)] = _pruned_entry()
                break
        e1m, e2m = enemy_built[e1_name]["moves"], enemy_built[e2_name]["moves"]
        def _has_tailwind(name):
            return merged is not None and any(
                mv_name == "Tailwind"
                for mv_name, _pct in merged.get(name, {}).get("moves_usage", []))
        tailwind_setter_roles = [role for role, name in (("E1", e1_name), ("E2", e2_name))
                                 if _has_tailwind(name)]
        real_tailwind_threat = bool(tailwind_setter_roles)

        best = None
        for _our_mt, (c1, c2) in _resolve_forms((n1, n2), our_built,
                                                forced_base_names=forced_base_names):
            worst = None
            for _enemy_mt, (e1c, e2c) in _resolve_forms((e1_name, e2_name), enemy_built):
                combatants = {"C": c1, "P": c2, "E1": e1c, "E2": e2c}
                moves_by_role = {"C": m1, "P": m2, "E1": e1m, "E2": e2m}
                weather = _field_weather(combatants)
                terrain = _field_terrain(combatants)
                outcome, turns_used, hp, log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns,
                    terrain=terrain)
                if tailwind_setter_roles:
                    tw_outcome, tw_turns_used, tw_hp, tw_log = max(
                        (_joint_race(combatants, moves_by_role, typechart, weather,
                                    turns, first_turn_tailwind_role=role,
                                    terrain=terrain)
                         for role in tailwind_setter_roles),
                        key=lambda r: _JOINT_OUTCOME_RANK[r[0]])
                else:
                    tw_outcome, tw_turns_used, tw_hp, tw_log = _joint_race(
                        combatants, moves_by_role, typechart, weather, turns,
                        enemy_speed_mult=2.0, terrain=terrain)
                pr_e1_outcome, _pr1_t, _pr1_hp, _pr1_log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns,
                    first_turn_protected_role="E1", terrain=terrain)
                pr_e2_outcome, _pr2_t, _pr2_hp, _pr2_log = _joint_race(
                    combatants, moves_by_role, typechart, weather, turns,
                    first_turn_protected_role="E2", terrain=terrain)
                protect_outcomes = {"E1": pr_e1_outcome, "E2": pr_e2_outcome}
                tailwind_forced = (real_tailwind_threat and
                                   _JOINT_OUTCOME_RANK[tw_outcome] >
                                   _JOINT_OUTCOME_RANK[outcome])
                chosen_outcome = tw_outcome if tailwind_forced else outcome
                chosen_hp = tw_hp if tailwind_forced else hp
                # Retained HP is only a meaningful QUALITY signal for an
                # actual win -- for loss/no_ko the outcome bucket alone
                # already says "bad", and `hp` can go slightly negative on
                # an overkill hit (damage isn't clamped at 0 when applied,
                # only the "fainted" check reads it as <=0), so a real loss
                # must not leak a garbage-but-technically-positive value in.
                if chosen_outcome in ("sweep", "out_trade"):
                    our_hp = {"C": max(0.0, chosen_hp["C"]),
                             "P": max(0.0, chosen_hp["P"])}
                else:
                    our_hp = {"C": 0.0, "P": 0.0}
                entry = {
                    "outcome": chosen_outcome,
                    "outcome_without_tailwind": outcome,
                    "tailwind_is_real_threat": real_tailwind_threat,
                    "tailwind_forced": tailwind_forced,
                    "turns_used": tw_turns_used if tailwind_forced else turns_used,
                    "tailwind_outcome": tw_outcome,
                    "tailwind_safe": tw_outcome in ("sweep", "out_trade"),
                    "protect_outcomes": protect_outcomes,
                    "protect_safe": all(o in ("sweep", "out_trade")
                                        for o in protect_outcomes.values()),
                    "log": tw_log if tailwind_forced else log,
                    "our_hp": our_hp,
                    "clean_win_value": our_hp["C"] + our_hp["P"],
                    "_c1": c1, "_c2": c2, "_e1c": e1c, "_e2c": e2c,
                }
                if (worst is None or _JOINT_OUTCOME_RANK[entry["outcome"]]
                        > _JOINT_OUTCOME_RANK[worst["outcome"]]):
                    worst = entry
            if (best is None or _JOINT_OUTCOME_RANK[worst["outcome"]]
                    < _JOINT_OUTCOME_RANK[best["outcome"]]):
                best = worst

        if want_grid:
            grid_combatants = {"C": best["_c1"], "P": best["_c2"],
                               "E1": best["_e1c"], "E2": best["_e2c"]}
            weather = _field_weather(grid_combatants)
            terrain = _field_terrain(grid_combatants)
            grid = _damage_grid(best["_c1"], best["_c2"], best["_e1c"], best["_e2c"],
                               m1, m2, e1m, e2m, typechart, weather, terrain=terrain)
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
        "pairs_clean_win_total": sum(d["clean_win_value"] for d in detail.values()),
        "pairs_total": len(detail),
    }
    return detail, summary


def joint_pair_search(pool, target_names, partner_name, merged, moves_db,
                      natures, typechart, turns=2, partner_item=None,
                      item_overrides=None, move_overrides=None,
                      excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
        item=partner_item, excluded_items=excluded_items)
    partner_moves = _move_infos(partner_name, merged, moves_db, partner_move_names)
    enemy_built = _build_forms(target_names, merged, natures, moves_db)

    rows = []
    for name in pool:
        if name == partner_name:
            continue
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
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
            typechart, turns, merged=merged)
        rows.append({"name": name, "item": item, "detail": detail, **summary})
    rows.sort(key=_pair_sort_key)
    return rows


def joint_pool_search(pool, target_names, merged, moves_db, natures,
                      typechart, turns=2, item_overrides=None,
                      move_overrides=None, excluded_items=DEFAULT_EXCLUDED_ITEMS,
                      prune_below=None, extra_forced_base=frozenset()):
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

    `prune_below`: passed straight through to `_pair_vs_targets` -- see its
    own docstring. `None` (the default) races every enemy pair for every
    our-pair, exactly as before this existed.

    `extra_forced_base`: for each name in it, EVERY pair containing that name
    is raced a SECOND time with `forced_base_names={that name}` (locking it
    to base form), and the extra row appended with `"forced_base": <name>`
    (every other row gets `"forced_base": None`) -- `bring4_search` uses this
    to get a per-name "what if this stone-holder never transforms" pair
    table, for the bring-4s that carry it alongside the core's OTHER allowed
    stone-holder (VGC's real "only one Mega Evolution per team per game"
    rule needs one of the two consistently picked, across ALL of that
    bring's own pairs, not just within whichever pair happens to contain
    both). Default empty set costs nothing extra and changes no returned row
    -- every existing caller is unaffected.

    Returns rows: {pair: (name1, name2), item1, item2, pairs_swept,
    pairs_traded, pairs_lost, pairs_no_ko, pairs_tailwind_safe,
    pairs_protect_safe, pairs_total, detail, forced_base} -- `detail` is
    `_pair_vs_targets`'s own shape (damage log included), ranked the same
    way `joint_pair_search` ranks. `forced_base` is `None` for every row
    except the `extra_forced_base` extras described above -- a caller that
    doesn't pass `extra_forced_base` only ever sees `None` here, unchanged
    from before this field existed.
    """
    built = {}
    for name in pool:
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
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
                                           enemy_built, typechart, turns,
                                           merged=merged, prune_below=prune_below)
        rows.append({"pair": (n1, n2), "item1": built[n1]["item"],
                    "item2": built[n2]["item"], "detail": detail,
                    "forced_base": None, **summary})
    for forced_name in extra_forced_base:
        if forced_name not in built:
            continue
        for other in built:
            if other == forced_name:
                continue
            n1, n2 = (forced_name, other) if forced_name < other else (other, forced_name)
            detail, summary = _pair_vs_targets(
                n1, n2, built, target_names, enemy_built, typechart, turns,
                merged=merged, prune_below=prune_below,
                forced_base_names=frozenset({forced_name}))
            rows.append({"pair": (n1, n2), "item1": built[n1]["item"],
                        "item2": built[n2]["item"], "detail": detail,
                        "forced_base": forced_name, **summary})
    rows.sort(key=_pair_sort_key)
    return rows


# --------------------------------------------------------------- bring-4/6


def _pair_beaten_frac(row):
    """(swept + traded) / total for one `joint_pool_search`/`joint_pair_search`
    row -- the fraction of `target_names`'s enemy pairs this OUR pair
    actually beats. `pairs_total` is 0 only when `target_names` itself has
    fewer than 2 names (nothing to race against), which every caller here
    already guards against."""
    return ((row["pairs_swept"] + row["pairs_traded"]) / row["pairs_total"]
           if row["pairs_total"] else 0.0)


def _pair_sort_key(row):
    """The exact ranking `joint_pair_search`/`joint_pool_search` already sort
    their own rows by, factored out so `bring4_search` and the multi-enemy
    search can rank INDIVIDUAL pairs the same way those functions do --
    ascending on this key is best-pair-first, so `max(..., key=_pair_sort_key)`
    over a handful of pairs finds the WORST one.

    RANKED BY PROTECT-SAFE WINS FIRST, not raw beaten count: a "win" that
    only holds on the no-Protect line of play is exactly the fragile result
    `protect_outcomes`/`protect_safe` exists to catch (the real doubles
    50/50 the user described), so it leads the ranking rather than being a
    tie-break buried behind it. Raw beaten count (swept+traded) is the
    second criterion -- winning more matchups always outranks winning fewer
    ones more cleanly, the same "uncovered enemy pairs dominate" priority
    `_bring4_candidates` gives an unconditional loss over a bring-4's
    average quality.

    THIRD: `pairs_clean_win_total` (`_pair_vs_targets`'s own "WIN QUALITY"
    field) -- "losing 1 pokemon and taking a lot of damage and KOing 2
    enemies [is] far inferior to KOing the enemy without taking damage".
    Two pairs that win the exact same NUMBER of matchups are told apart by
    how cleanly they won them (2.0 per enemy pair for a true sweep, less
    for a chippy out-trade, near 0 for one that cost a whole Pokemon) --
    strictly after raw count (a pair with an unconditional loss to some
    enemy composition is still worse than one that wins everywhere, however
    messily), strictly before tailwind-safe count (a real Tailwind-boosted
    loss is a more concrete, binary risk than an average-case quality
    difference). Tailwind-safe count is the fourth and final criterion.
    """
    return (-row["pairs_protect_safe"], -(row["pairs_swept"] + row["pairs_traded"]),
           -row["pairs_clean_win_total"], -row["pairs_tailwind_safe"])


def bring4_search(our6, target_names, merged, moves_db, natures, typechart,
                  turns=2, good_threshold=1.0, item_overrides=None,
                  move_overrides=None, excluded_items=DEFAULT_EXCLUDED_ITEMS,
                  enforce_item_clause=False):
    """For an ALREADY-DECIDED team (3, 4, 5, or 6 Pokemon, from team preview)
    against one specific enemy roster, which 4 should you actually bring?

        "given that I will bring 4 vs a specific enemy, that is 6 pairs I
         will bring. I want the 6 possible pairs of my brings to perform
         very well, or at least to have several perform very well, such
         that I always have options no matter what position I am in."

    Sizes other than 6 aren't a special case: a team of exactly 4 has only
    ONE possible bring-4 (itself), so Stage 2 below degenerates to a single
    row summarising its own C(4,2)=6 internal pairs -- "in any case, the
    output should summarise the pair results for the 6 brought pairs in
    the bring-4" holds whether `our6` was already narrowed to 4 or is a
    full 6 Stage 2 still has to choose from. A team of 5 sits in between
    (C(5,4)=5 candidate bring-4s). A team of exactly 3 is the same
    degenerate case one size further down -- there's nothing to bring BUT
    the whole 3 ("output the best 3-Pokemon cores... 3 total pairs"), so
    Stage 2 yields one row summarising its own C(3,2)=3 internal pairs. Same
    "no wasted members" convention `multi_bring4_exhaustive`/
    `multi_bring4_beam` already use.

    STAGE 1 -- every one of `our6`'s C(len(our6),2) pairs, each against
    every enemy pair drawn from `target_names`: literally
    `joint_pool_search(our6, target_names, ...)`, no new racing. This
    alone answers "searching the top pairs to see how many are in."

    STAGE 2 -- every one of `our6`'s C(len(our6),min(4,len(our6))) possible
    BRING subsets: look up its own internal pairs from Stage 1 (pure lookup,
    still no new racing, EXCEPT for a bring that carries BOTH of a 2-Mega
    core's stone-holders -- see below). Ranked PRIMARILY by how many of
    `target_names`'s enemy pairs NONE of those internal pairs can beat
    (`uncovered_enemy_pairs` -- "having a pair that every pair of yours
    loses against is terrible, and this is an important factor": two
    bring-4s can have the identical raw beaten fraction while one of them
    has every one of its 6 pairs losing to the SAME enemy pair -- a real,
    unconditional loss whichever of the 4 you're forced to send out -- and
    the other's losses are spread out so some pair of yours always has an
    answer). THEN by the WORST pair's own rank (`_pair_sort_key`, the same
    ranking `joint_pool_search` already uses) -- "searching the given top
    teams and searching for how bad their worst pair performs," maximin:
    the bring-4 whose worst case is LEAST bad wins. THEN by how many of its
    pairs are "good" (beat at least `good_threshold` of `target_names`'s
    enemy pairs, default 100% -- "always have options").

    BRING-4-CONSISTENT MEGA CHOICE: when `our6` carries exactly 2 Mega-stone
    holders (the existing `--max-megas` composition cap, unaffected by any
    of this), VGC's real "only one Mega Evolution per team per game" rule
    means a SPECIFIC bring-4 subset that happens to carry BOTH of them can't
    freely let each transform independently across its own 6 pairs (today's
    per-pair minimax already correctly restricts "at most one of THESE TWO"
    within any single pair, but that's not the same as "the SAME one, for
    this bring's whole 6 pairs"). For exactly those bring-4 subsets (0 or 1
    of the core's own stone-holders needs no such consistency -- there's
    nothing to be inconsistent WITH, and forcing one anyway would make that
    member look artificially worse than reality), `_bring4_candidates`
    builds the row under BOTH "which one gets to be this bring's mega"
    hypotheses and keeps whichever ranks better -- see its own docstring.

    `enforce_item_clause`: off by default (real search cost concern, not a
    correctness one) -- when `True`, pre-resolves `our6`'s items with VGC's
    real Item Clause enforced (`_resolve_unique_items`) before any racing,
    so no two of `our6` end up holding the same item. Useful for verifying
    an already-decided team; leave off for anything performance-sensitive.

    Returns (pair_rows, bring4_rows):
      pair_rows -- `joint_pool_search`'s own row-per-pair output (its
        `forced_base` bookkeeping field stripped back out -- this stays
        exactly the C(len(our6),2) unique pairs it always was).
      bring4_rows -- [{bring4: (names), pairs: [(n1,n2), ...],
                       pair_rows: [<pair_rows entry>, ...],
                       worst_pair: (n1,n2), worst_pair_row: <pair_rows entry>,
                       pairs_good: int, pairs_total: int,
                       uncovered_enemy_pairs: [(e1,e2), ...]}], best-worst-
      case first -- a single entry when `our6` is already at the bring size
      (3 or 4).
    """
    our6 = list(dict.fromkeys(our6))
    if not (3 <= len(our6) <= 6):
        raise ValueError(f"bring4_search needs 3, 4, 5, or 6 distinct "
                         f"Pokemon, got {len(our6)}: {our6}")
    overlap = _mega_base_overlap(our6)
    if overlap:
        raise ValueError(f"can't bring both a Mega and its own base form: "
                         f"{', '.join(sorted(overlap))}")
    if enforce_item_clause:
        item_overrides = _resolve_unique_items(
            our6, merged, moves_db, natures, typechart, target_names,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
    megas = [n for n in our6 if n.startswith("Mega ")]
    extra_forced_base = frozenset(megas) if len(megas) == 2 else frozenset()
    rows = joint_pool_search(our6, target_names, merged, moves_db, natures,
                             typechart, turns=turns,
                             item_overrides=item_overrides,
                             move_overrides=move_overrides,
                             excluded_items=excluded_items,
                             extra_forced_base=extra_forced_base)
    pair_lookup_forced_base = None
    if extra_forced_base:
        # Built BEFORE popping "forced_base" below -- that pop mutates the
        # same dict objects `rows` still references, so this must read the
        # field first.
        pair_lookup_forced_base = {
            name: {frozenset(r["pair"]): r for r in rows
                  if r["forced_base"] == name}
            for name in megas}
    pair_rows = [r for r in rows if r["forced_base"] is None]
    for r in pair_rows:
        r.pop("forced_base", None)
    pair_by_key = {frozenset(r["pair"]): r for r in pair_rows}
    bring4_rows = _bring4_candidates(
        our6, pair_by_key, target_names, good_threshold,
        megas=megas if extra_forced_base else None,
        pair_lookup_forced_base=pair_lookup_forced_base)
    return pair_rows, bring4_rows


def _uncovered_enemy_pairs(pairs, target_names):
    """Enemy pairs (every C(len(target_names),2) drawn from `target_names`)
    that NONE of `pairs`'s own `detail` rows beat (`outcome` "sweep" or
    "out_trade"). "Having a pair that every pair of yours loses against is
    terrible, and this is an important factor": two bring-4s can look
    identical by raw beaten fraction (say 12/15 each) while one of them has
    every one of its 6 internal pairs losing to the exact SAME enemy
    composition (a real, unconditional loss whichever of the 4 you're
    forced to send out) and the other's losses are spread across different
    enemy pairs (so some pair of ours always has an answer) -- this is what
    tells the two apart."""
    enemy_pairs = list(itertools.combinations(target_names, 2))
    return [ep for ep in enemy_pairs
           if not any(r["detail"].get(ep, {}).get("outcome") in ("sweep", "out_trade")
                      for r in pairs)]


def _bring4_candidates(six, pair_lookup, target_names, good_threshold=1.0,
                       megas=None, pair_lookup_forced_base=None):
    """Every one of the C(len(six),min(4,len(six))) possible bring subsets of
    `six` (`six` is exactly 6 for `bring4_search`'s own Stage 2, but this
    also runs for a 3-, 4-, or 5-member PARTIAL team during
    `multi_bring4_beam`'s growth or a `--core-sizes 3` core, where it
    degenerates to 1, 1, or 5 subsets respectively -- the combinatorics
    don't care), using an ALREADY-COMPUTED `pair_lookup` ({frozenset(pair):
    row}) rather than racing anything. Shared by `bring4_search`'s own
    Stage 2 and the multi-enemy search, which builds the same shape of
    lookup per enemy from one shared pool-wide pair table. `target_names` is
    the exact enemy list `pair_lookup`'s own rows were raced against --
    needed here only to enumerate its enemy pairs for `_uncovered_enemy_pairs`.

    RANKED PRIMARILY BY UNCOVERED ENEMY PAIRS -- see `_uncovered_enemy_pairs`
    -- THEN by the WORST of the bring's own internal pairs' own rank
    (`_pair_sort_key`, maximin: the bring whose worst case is LEAST bad
    wins), THEN by how many of them clear the good-pair bar.

    `megas`/`pair_lookup_forced_base`: BRING-4-CONSISTENT MEGA CHOICE, opt-in
    (both `None` by default -- ordinary lookup, exactly as before this
    existed). `megas`: the core's own (at most 2) Mega-stone-holder names.
    `pair_lookup_forced_base`: {name: {frozenset(pair): row}} for each name
    in `megas` -- the SAME pairs as `pair_lookup`, but raced with that name
    locked to base form (`joint_pool_search`'s `extra_forced_base` rows).
    For a bring subset containing BOTH `megas`, the row is built TWICE --
    once with megas[1] locked to base (so megas[0] is free, "the team's
    mega" for this bring) and once the mirror image -- and the
    better-ranked of the two, by the exact same key used to rank every
    other bring, is kept. A bring with 0 or 1 of `megas` needs no such
    consistency (nothing to be inconsistent WITH) and uses the ordinary
    unconstrained `pair_lookup`, unchanged.

    Returns bring4_rows in `bring4_search`'s own shape, best-worst-case
    first -- `[0]` is always "the best bring available from `six`."
    """
    bring_size = min(4, len(six))

    def _row_for(bring4, lookup):
        pairs = [lookup(p) for p in itertools.combinations(bring4, 2)]
        worst = max(pairs, key=_pair_sort_key)
        pairs_good = sum(1 for r in pairs if _pair_beaten_frac(r) >= good_threshold)
        uncovered = _uncovered_enemy_pairs(pairs, target_names)
        return {
            "bring4": bring4, "pairs": [r["pair"] for r in pairs],
            "pair_rows": pairs, "worst_pair": worst["pair"], "worst_pair_row": worst,
            "pairs_good": pairs_good, "pairs_total": len(pairs),
            "uncovered_enemy_pairs": uncovered,
        }

    def _rank_key(b):
        return (len(b["uncovered_enemy_pairs"]), _pair_sort_key(b["worst_pair_row"]),
                -b["pairs_good"])

    bring4_rows = []
    for bring4 in itertools.combinations(six, bring_size):
        if megas and len(set(bring4) & set(megas)) == 2:
            m1, m2 = megas
            row_m1_is_mega = _row_for(
                bring4, lambda p, forced=m2: pair_lookup_forced_base[forced].get(
                    frozenset(p), pair_lookup[frozenset(p)]))
            row_m2_is_mega = _row_for(
                bring4, lambda p, forced=m1: pair_lookup_forced_base[forced].get(
                    frozenset(p), pair_lookup[frozenset(p)]))
            bring4_rows.append(min(row_m1_is_mega, row_m2_is_mega, key=_rank_key))
        else:
            bring4_rows.append(_row_for(bring4, lambda p: pair_lookup[frozenset(p)]))
    bring4_rows.sort(key=_rank_key)
    return bring4_rows


def bring4_pair_depth(bring4_row):
    """Summarise a bring-4's own 6 internal pairs (`bring4_row["pair_rows"]`,
    already computed -- no new racing) beyond the single worst pair
    `_bring4_candidates` already ranks on: "I would like ... the basic
    details of the 6 pairs for each bring4 (total, 3rd best, 4th best, and
    worst wins, wins under Tailwind ..., under protect safe)" -- a bring-4
    can look fine on JUST its worst pair while its middle-of-the-pack pairs
    (3rd/4th best of 6) are actually mediocre, which the existing ranking
    never surfaces.

    Pairs are ordered by `_pair_sort_key` (ascending = best first, the same
    ranking `joint_pool_search`'s own rows already sort by) -- "3rd best"/
    "4th best" read off that order, not off raw beaten count alone, so they
    agree with the SAME notion of "better" the rest of this module uses
    (protect-safe wins first, then beaten count, then tailwind-safe count).

    Returns {"beaten_total": int, "beaten_3rd": int|None, "beaten_4th":
    int|None, "beaten_worst": int|None, "pairs_total": int (the shared
    per-pair enemy-pairs-total, e.g. 1 for a single named enemy pair),
    "tailwind_safe_total": int, "protect_safe_total": int,
    "clean_win_total": float} -- the four "beaten_*" fields and the three
    "*_total" fields are all summed/read across the SAME `n_pairs` =
    `len(bring4_row["pair_rows"])` pairs (6 for a bring of 4, the common
    case, but only 3 for a 3-Pokemon core -- "I would like to output the
    best 3-pokemon cores against each team", which has just one possible
    bring, itself), so e.g. `beaten_total` compares directly against
    `n_pairs * pairs_total`, and `clean_win_total` against `n_pairs *
    pairs_total * 2.0` (see `_pair_vs_targets`'s own "WIN QUALITY"
    docstring paragraph for what 2.0 per pair means: a true sweep, zero
    damage taken). `beaten_3rd`/`beaten_4th` are `None` only if
    `bring4_row` has fewer than 3/4 pairs (true for a 3-Pokemon core's own
    3 pairs, not just a hand-built partial row -- this stays honest rather
    than raising an IndexError).
    """
    pairs = sorted(bring4_row["pair_rows"], key=_pair_sort_key)
    pairs_total = pairs[0]["pairs_total"] if pairs else 0
    beaten = [r["pairs_swept"] + r["pairs_traded"] for r in pairs]
    return {
        "beaten_total": sum(beaten),
        "beaten_3rd": beaten[2] if len(beaten) > 2 else None,
        "beaten_4th": beaten[3] if len(beaten) > 3 else None,
        "beaten_worst": beaten[-1] if beaten else None,
        "pairs_total": pairs_total,
        "tailwind_safe_total": sum(r["pairs_tailwind_safe"] for r in pairs),
        "protect_safe_total": sum(r["pairs_protect_safe"] for r in pairs),
        "clean_win_total": sum(r["pairs_clean_win_total"] for r in pairs),
    }


def enemy_has_real_tailwind(target_names, merged):
    """True if any named enemy actually runs Tailwind in real usage data
    (`merged[name]["moves_usage"]`) -- the same check `_pair_vs_targets`'s
    own `_has_tailwind` makes per race, factored out here so a CALLER (the
    CLI's CSV/xlsx export) can flag "this roster has a real Tailwind
    threat" once for a whole bring-4 table, instead of re-deriving it from
    `tailwind_is_real_threat`/`tailwind_forced` on every individual pair
    row -- "wins under Tailwind, ESPECIALLY IF they have a tailwind user
    in the 2v2" is exactly this: a flag that tells the reader whether the
    tailwind-safe column is worth a second look at all, not just its raw
    number."""
    return any(
        any(mv == "Tailwind" for mv, _pct in merged.get(name, {}).get("moves_usage", []))
        for name in target_names)


def _core_row(core, pair_by_key_list, target_name_lists, good_threshold=1.0):
    """For a candidate CORE (4, 5, or 6 Pokemon -- see `multi_bring4_
    exhaustive`'s own note on why fewer than 6 is a real, often BETTER
    answer, not a fallback) against SEVERAL enemy rosters: the BEST bring-4
    available from `core` against EACH enemy (`_bring4_candidates`, reused
    -- a bring-4 may differ per opponent, matching real VGC's "you see
    their team at Team Preview before choosing your bring-4"), then the
    WORST of those per-enemy best scores -- the enemy this core is weakest
    against, even playing its best available bring-4. Ranking candidate
    cores on THIS (ascending -- lower `worst_enemy_score_key` is better) is
    the multi-enemy generalisation of `bring4_search`'s own maximin.

    `worst_enemy_score_key` is `(uncovered_enemy_pairs_count, *_pair_sort_key(...))`
    -- the same "uncovered enemy pairs dominate the ranking" rule
    `_bring4_candidates` already applies to pick each enemy's own best
    bring-4, carried up here so it also decides which enemy ROSTER counts
    as this core's bottleneck, and which CORE (in `multi_bring4_exhaustive`/
    `multi_bring4_beam`, which sort on this same field) ranks above
    another: a core with an unconditional loss against one enemy composition
    must rank below one that has an answer everywhere, even if the first
    core's raw worst-pair fraction otherwise looks better.

    Also reports `unused`: any core member that never appears in ANY
    enemy's best bring-4 -- dead weight ("There is no point including a
    6th member on a team if only the other 5 join the battles").
    `multi_bring4_exhaustive`/`multi_bring4_beam` both drop any row where
    this is non-empty, since the identical-scoring smaller core is already
    enumerated on its own.
    """
    core = tuple(sorted(core))
    per_enemy = []
    worst_key, worst_idx = None, None
    used = set()
    for i, (pair_lookup, target_names) in enumerate(zip(pair_by_key_list, target_name_lists)):
        candidates = _bring4_candidates(core, pair_lookup, target_names, good_threshold)
        best = candidates[0]
        key = (len(best["uncovered_enemy_pairs"]),) + _pair_sort_key(best["worst_pair_row"])
        per_enemy.append({"target_names": list(target_names),
                          "best_bring4": best["bring4"], "best_bring4_row": best})
        used.update(best["bring4"])
        if worst_key is None or key > worst_key:
            worst_key, worst_idx = key, i
    return {"core": core, "core_size": len(core), "per_enemy": per_enemy,
           "worst_enemy_idx": worst_idx, "worst_enemy_score_key": worst_key,
           "unused": tuple(sorted(set(core) - used))}


# `multi_bring4_coverage`'s per-enemy `joint_pool_search` calls are fully
# independent (same `pool`/`fixed_items`/`fixed_moves` read, nothing written
# back and forth) -- a `--jobs` process pool, same shape as
# `roster_rating.rate_many`'s own worker_init/_WORKER_WORLD pair. Each worker
# builds its OWN copy of the dataset once (~14s) and reuses it for every
# enemy roster handed to it, rather than paying that cost per task or
# pickling the (large) merged/moves/natures/typechart objects through
# ProcessPoolExecutor on every submit.
_WORKER_WORLD = None


def _multi_bring4_worker_init():
    """Build this worker's dataset once, not once per enemy roster."""
    global _WORKER_WORLD
    from species_data import build_merged_dataset
    merged, _usage, moves, natures, typechart = build_merged_dataset()
    _WORKER_WORLD = {"merged": merged, "moves": moves, "natures": natures,
                     "typechart": typechart}


def _multi_bring4_coverage_job(job):
    """One enemy roster's pool-wide pair search, for `multi_bring4_coverage`'s
    `jobs` > 1 path. Top-level and plain-typed: the pool may use spawn, so
    both ends of this call cross a pickle boundary."""
    (pool, target_names, turns, fixed_items, fixed_moves, excluded_items,
     good_threshold) = job
    global _WORKER_WORLD
    if _WORKER_WORLD is None:
        _multi_bring4_worker_init()
    w = _WORKER_WORLD
    return joint_pool_search(pool, target_names, w["merged"], w["moves"],
                             w["natures"], w["typechart"], turns=turns,
                             item_overrides=fixed_items, move_overrides=fixed_moves,
                             excluded_items=excluded_items, prune_below=good_threshold)


def multi_bring4_coverage(pool, target_name_lists, merged, moves_db, natures,
                          typechart, turns=2, good_threshold=1.0,
                          min_enemies=2, item_overrides=None, move_overrides=None,
                          excluded_items=DEFAULT_EXCLUDED_ITEMS, jobs=1):
    """Stage A, shared by `multi_bring4_exhaustive` and `multi_bring4_beam`:
    run the existing pool-wide pair search once per enemy roster.

        "I want to look at several 'vs' teams, for instance 3 different
         sets of enemy 6. It will run the best pairs against each separate
         team in the same way, but then it will find the best possible
         group of 6, comprised of brings of possible 4 that perform well
         in the 6-pair test I described above."

    `joint_pool_search(pool, target_i, ...)` -- no new racing machinery --
    once per entry in `target_name_lists`. Then narrows `pool` down to a
    CANDIDATE POOL: only members that appear in a "good" pair (beats
    `good_threshold` or more of that one enemy's own pairs) for at least
    `min_enemies` of the named enemy rosters -- clamped to
    `len(target_name_lists)` if given larger, since a member can never be
    "good against N enemies" when fewer than N were even named (the
    default of 2 would otherwise silently empty `candidate_pool` for
    every single-roster search).

    Each per-enemy `joint_pool_search` call below passes `prune_below=
    good_threshold` -- once a pair's remaining, not-yet-raced enemy pairs
    could not possibly push it up to `good_threshold` even if every one of
    them were a win, racing that pair against the rest of THIS roster stops
    (see `_pair_vs_targets`'s docstring for why this bound is sound: it
    never turns a pair that could still qualify into one that doesn't).
    This is what keeps a 300-candidate pool's Stage A affordable -- a pair
    that's already hopeless against one enemy roster doesn't get raced
    against the rest of that roster's pairs too.

        "A good bring of 4 should probably have at least 3-4 good pairs
         out of 6, and there shouldn't actually be many added pairs to
         search anyway given the small number of top candidates, given
         the caveat that they should appear in multiple top pairs."

    This is what keeps `multi_bring4_exhaustive`'s team-of-6 sweep
    tractable BY CONSTRUCTION rather than an arbitrary top-N cutoff --
    someone who is only ever good against ONE of the N enemies is
    presumably not the answer to "an option no matter which of these I
    face."

    Returns {"per_enemy": [pair_rows, ...], "pair_by_key": [{frozenset:
    row}, ...], "target_name_lists": [...], "candidate_pool": [...],
    "fixed_items": {name: item}, "fixed_moves": {name: [move, ...]}} --
    `per_enemy`/`pair_by_key` cover the WHOLE (enemy-free) `pool` (so
    `multi_bring4_beam` can grow a team over the full pool too),
    `candidate_pool` is the narrowed subset for the exhaustive sweep.
    `fixed_items`/`fixed_moves` are the ONE set each pool member actually
    raced with above (see "A REAL TEAM'S SET IS FIXED..." below) -- any
    caller that needs to display a member's set (a teamsheet) should read
    it from here rather than re-deriving one, so the display can never show
    a different set than what the numbers next to it were actually computed
    from.

    A pool member named as an enemy on ANY of `target_name_lists` is
    dropped from `pool` up front, for every enemy, not just the one it's
    named on -- `joint_pool_search` already excludes a name from ITS OWN
    race when that name is also in the enemy `target_names` it was just
    given, but a species who's an enemy on team 2 and a candidate everyone
    else's race considers would otherwise be excluded from ONLY team 2's
    pair table, leaving `_bring4_candidates` unable to find its pairs the
    moment a team-of-6 containing it is checked against team 2.

    A REAL TEAM'S SET IS FIXED FOR THE WHOLE EVENT: "the moves must stay
    the same, i.e., they can't be adjusted battle to battle." Calling
    `joint_pool_search(pool, target_names, ...)` once per enemy roster, the
    naive way, would let `_answer_for` independently re-search each pool
    member's item/moveset against JUST that one enemy team every time --
    the same Pokemon could legally come back with a different set for
    enemy 2 than it got for enemy 1, which isn't a real, biddable team.
    Every pool member's set is instead searched EXACTLY ONCE here, against
    the UNION of every named enemy across every `target_name_lists` entry
    (so it's still built to handle everything it might face), and passed
    into EVERY per-enemy `joint_pool_search` call below as an override --
    one fixed set, evaluated against each enemy in turn, exactly like a
    real tournament team. An explicit caller-supplied `item_overrides`/
    `move_overrides` pin for a name still wins over this computed default,
    same "an explicit pin always wins" rule every other override in this
    module already follows.

    `jobs`: run N enemy rosters' `joint_pool_search` calls in parallel
    worker processes instead of one after another (1, the default: serial,
    no process pool spun up at all). The per-enemy searches below are
    independent -- same `pool`/`fixed_items`/`fixed_moves` read by every
    one of them, nothing written back and forth -- so this is a plain
    process-pool map, same shape as `roster_rating.rate_many`. Falls back to
    serial when there is only one enemy roster to search (nothing to gain
    from a pool for one task) regardless of what `jobs` asks for.
    """
    target_name_lists = [list(t) for t in target_name_lists]
    min_enemies = min(min_enemies, len(target_name_lists))
    any_enemy = {n for t in target_name_lists for n in t}
    all_enemies = sorted(any_enemy)
    fixed_items, fixed_moves = {}, {}
    for name in pool:
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, all_enemies,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
        if move_names:
            fixed_items[name] = item
            fixed_moves[name] = move_names
    # A name `_answer_for` couldn't find any usable moveset for even
    # against the FULL enemy union (a data gap, not a real answer) is
    # dropped from the pool entirely here -- letting it fall through to a
    # per-enemy re-search below would silently reintroduce the exact
    # per-battle-adjusted-set problem this whole block exists to prevent,
    # just for the one name that happened to have no global answer.
    pool = [n for n in pool if n in fixed_moves]
    if jobs > 1 and len(target_name_lists) > 1:
        import concurrent.futures as cf
        jobs_list = [(pool, target_names, turns, fixed_items, fixed_moves,
                     excluded_items, good_threshold)
                    for target_names in target_name_lists]
        with cf.ProcessPoolExecutor(
                max_workers=min(jobs, len(jobs_list)),
                initializer=_multi_bring4_worker_init) as ex:
            # map, not as_completed: per_enemy must stay in target_name_lists
            # order -- every downstream reader (candidate_pool below,
            # multi_bring4_exhaustive/beam, the xlsx writer) zips it against
            # target_name_lists positionally.
            per_enemy = list(ex.map(_multi_bring4_coverage_job, jobs_list))
    else:
        per_enemy = [joint_pool_search(pool, target_names, merged, moves_db,
                                       natures, typechart, turns=turns,
                                       item_overrides=fixed_items,
                                       move_overrides=fixed_moves,
                                       excluded_items=excluded_items,
                                       prune_below=good_threshold)
                    for target_names in target_name_lists]
    pair_by_key = []
    appears_good_in = {}
    for rows in per_enemy:
        pair_by_key.append({frozenset(r["pair"]): r for r in rows})
        good_names = set()
        for r in rows:
            if _pair_beaten_frac(r) >= good_threshold:
                good_names.update(r["pair"])
        for n in good_names:
            appears_good_in[n] = appears_good_in.get(n, 0) + 1
    candidate_pool = sorted(n for n, c in appears_good_in.items() if c >= min_enemies)
    return {"per_enemy": per_enemy, "pair_by_key": pair_by_key,
           "target_name_lists": target_name_lists, "candidate_pool": candidate_pool,
           "merged": merged, "fixed_items": fixed_items, "fixed_moves": fixed_moves}


def _effective_type_limits(max_weak=None, type_limits=None):
    """Merge a global `max_weak` (applied to every type not already
    overridden) into `type_limits`'s own per-type {"max_weak", "max_net"}
    shape -- computed ONCE per search, not per candidate, since
    `hard_violations` is called for every core a multi-bring4 search
    considers. `{}` (falsy) when neither is given, so callers can skip the
    hard-violations check entirely rather than pay for a no-op one."""
    if max_weak is None and not type_limits:
        return {}
    from species_data import TYPES
    limits = {t: dict(v) for t, v in (type_limits or {}).items()}
    if max_weak is not None:
        for t in TYPES:
            limits.setdefault(t, {}).setdefault("max_weak", max_weak)
    return limits


def _core_passes_hard_filters(core, merged, effective_limits, max_megas=2,
                              max_weak_types=None):
    """True if `core` (any size) may be proposed as a multi-bring4
    candidate at all: "you cannot have both a mega and its non-mega form"
    (always enforced, `_mega_base_overlap`), "a full team can only have two
    mega stone users" (`max_megas`, same default -- and same reasoning --
    `team_search.beam_search_teams`/`substitution.legal_swap` already use
    for the Generate tab; in an actual battle either one may choose to
    transform depending on the specific pair matchup, which is exactly what
    `_resolve_forms`'s own per-pair minimax already searches -- this is a
    TEAM COMPOSITION cap, not a per-battle one), plus, when
    `effective_limits` is non-empty, the Advanced weakness limits
    `team_search.hard_violations` already implements -- reused directly,
    never reimplemented -- plus, when `max_weak_types` is given, the
    `weak_type_breadth` cap ("no more than N types may have 2+ weak
    members"). `max_megas` and `max_weak_types` are both checked
    MONOTONICALLY safe to prune on during partial-core growth too
    (`multi_bring4_beam`'s own use of this function): a partial core
    already over either cap can never fix that by adding more members.
    """
    if _mega_base_overlap(core):
        return False
    if sum(1 for n in core if n.startswith("Mega ")) > max_megas:
        return False
    if effective_limits:
        from team_search import hard_violations
        if hard_violations(list(core), merged, type_limits=effective_limits):
            return False
    if max_weak_types is not None and weak_type_breadth(core, merged) > max_weak_types:
        return False
    return True


def _monotonic_limits(effective_limits):
    """The `max_weak`-only portion of `effective_limits` -- safe to prune
    on DURING beam growth, before a candidate reaches a real core size,
    because it is monotonic: a partial team that already has too many
    members weak to a type can never fix that by adding more (mirrors
    `team_search._breaks_monotonic_hard_limit`'s own reasoning). `max_net`
    is NOT monotonic -- a later addition can add a resist and bring net
    back under the cap -- so it is deliberately dropped here and only
    checked once a candidate reaches `_CORE_SIZES` (`multi_bring4_beam`'s
    own `found`-capture step, via the full `effective_limits`)."""
    return {t: {"max_weak": v["max_weak"]} for t, v in effective_limits.items()
           if v.get("max_weak") is not None}


# C(30,6) is ~593k core candidates at size 6 alone (sizes 4/5 add a further
# ~170k), each scored by cheap dict lookups (no racing) -- a couple of
# seconds total. C(40,6) is ~3.8M -- still survivable but no longer
# "instant", and the whole point of `candidate_pool`'s multi-enemy
# narrowing is that it should rarely get anywhere near this.
_EXHAUSTIVE_POOL_CEILING = 30

# VGC team preview caps a roster at 6; a bring-4 needs at least 4 members to
# exist at all. Every size in between is a REAL, often more efficient
# answer ("a full team of only 4-5 members is not a problem, in some ways
# it is actually better and more efficient") -- see `_core_row`'s own
# `unused` field, which is what keeps a padded 6 from crowding out an
# equally-good, genuinely-smaller 4 or 5 that a caller would rather see.
_CORE_SIZES = (4, 5, 6)


def multi_bring4_exhaustive(coverage, good_threshold=1.0,
                            max_candidates=_EXHAUSTIVE_POOL_CEILING,
                            max_weak=None, type_limits=None, max_megas=2,
                            max_weak_types=None, core_sizes=_CORE_SIZES):
    """Every possible CORE (4, 5, or 6 Pokemon by default -- `core_sizes`
    can widen this down to 3, "I would like to output the best 3-pokemon
    cores against each team" -- not just exactly 6) drawn
    from `coverage["candidate_pool"]` (`multi_bring4_coverage`), scored and
    ranked by `_core_row`'s worst-case-across-enemies metric -- exhaustive,
    so (within that candidate pool) provably optimal, not a heuristic
    best-effort.

        "I will note that a full team of only 4-5 members is not a
         problem, in some ways it is actually better and more efficient.
         I would still like to see them."

    A core with ANY unused member (`_core_row`'s own `unused` field -- a
    slot that never gets brought against any of the named enemies) is
    dropped: the identical-scoring core without that member is already
    enumerated on its own, so keeping the padded version would only ever
    duplicate a real answer with dead weight attached. Also drops any core
    with both a Mega and its own base form, one that breaks
    `max_weak`/`type_limits` (`team_search.hard_violations`, reused), and
    (when `max_weak_types` is given) one where more than `max_weak_types`
    DIFFERENT types have 2+ weak members (`weak_type_breadth`) -- "no more
    than 3 types that have 2 members weak to it", a breadth cap distinct
    from `max_weak`'s own per-type ceiling.

    Raises if the candidate pool is bigger than `max_candidates` -- narrow
    with a higher `--min-enemies`/`--good-threshold`, or use
    `multi_bring4_beam` instead.

    Returns rows (`_core_row`'s own shape), best-worst-case first.
    """
    pool = coverage["candidate_pool"]
    n = len(pool)
    min_size = min(core_sizes)
    if n < min_size:
        raise ValueError(f"only {n} candidate(s) appear in a good pair for "
                         f"enough enemies to form even a {min_size}-member "
                         f"core -- widen the pool, or lower "
                         f"--good-threshold/--min-enemies")
    if n > max_candidates:
        raise ValueError(f"{n} candidates is too many for an exhaustive "
                         f"sweep (C({n},6) is enormous) -- narrow with a "
                         f"higher --min-enemies/--good-threshold, or pass "
                         f"--beam instead")
    effective_limits = _effective_type_limits(max_weak, type_limits)
    merged = coverage["merged"]
    rows = []
    for size in core_sizes:
        if size > n:
            continue
        for core in itertools.combinations(pool, size):
            if not _core_passes_hard_filters(core, merged, effective_limits,
                                             max_megas=max_megas,
                                             max_weak_types=max_weak_types):
                continue
            row = _core_row(core, coverage["pair_by_key"],
                            coverage["target_name_lists"], good_threshold)
            if row["unused"]:
                continue
            rows.append(row)
    rows.sort(key=lambda r: r["worst_enemy_score_key"])
    return rows


def multi_bring4_beam(coverage, good_threshold=1.0, beam_width=40,
                      max_weak=None, type_limits=None, max_megas=2,
                      max_weak_types=None, core_sizes=_CORE_SIZES):
    """Beam-search a CORE (4, 5, or 6 Pokemon by default -- `core_sizes`
    can widen this down to 3) over the WHOLE pool
    `multi_bring4_coverage` already has pair data for (the raw pool, NOT
    narrowed to `candidate_pool`) -- for when that candidate pool is too
    large for `multi_bring4_exhaustive`, or a broader search is wanted
    regardless.

    Mirrors `team_search.beam_search_teams`'s own incremental-growth
    structure closely (seed with the best PAIRS by raw coverage, then grow
    one member at a time, keeping the best `beam_width` partials at every
    size) -- a new sibling here rather than a modification of that
    function, since it is scored by a completely different metric and
    `team_search.py`'s existing callers must not change behaviour. A
    growth step never adds a member that would create a Mega/base overlap
    or break a given weakness limit -- an invalid core is never grown INTO,
    not filtered out after the fact.

    Sizes 4, 5 AND 6 are all captured as the beam passes through them (not
    just the final size-6 beam) and returned together, ranked the same
    way -- "a full team of only 4-5 members ... I would still like to see
    them" applies here too. A core with an unused member is dropped, same
    as `multi_bring4_exhaustive`.

    Returns rows (`_core_row`'s own shape), best-worst-case first, for
    whichever cores the beam actually reached (not exhaustive, so not
    guaranteed globally optimal).
    """
    pair_by_key_list = coverage["pair_by_key"]
    target_name_lists = coverage["target_name_lists"]
    merged = coverage["merged"]
    effective_limits = _effective_type_limits(max_weak, type_limits)
    # Only `max_weak` is monotonic (safe to prune ON during growth, before a
    # candidate reaches a real core size) -- `max_net` can only be checked
    # once a candidate is a genuine 4-6 core, at the `found`-capture step
    # below, using the FULL `effective_limits`. See `_monotonic_limits`.
    # `max_weak_types` (a count of TYPES, not a per-type cap) is ALSO
    # monotonic the same way `max_weak`/`max_megas` are -- see
    # `weak_type_breadth`'s own docstring -- so it's passed to every
    # `_core_passes_hard_filters` call below, growth-time and final alike.
    growth_limits = _monotonic_limits(effective_limits)
    pool = sorted({n for pbk in pair_by_key_list for fs in pbk for n in fs})

    def score(team):
        """Ascending = better, matching `_pair_sort_key`'s own convention.
        Fewer than 4 members: no bring-4 exists yet, so this falls back to
        NEGATIVE summed pair-beaten-fraction across every enemy (more raw
        coverage now is better -- a lower number in this convention), the
        same idea `beam_search_teams` seeds its own beam with. 4 or more:
        the real worst-case-across-enemies bring-4 score."""
        if len(team) < 4:
            total = sum(_pair_beaten_frac(pbk[frozenset(p)])
                       for pbk in pair_by_key_list
                       for p in itertools.combinations(team, 2)
                       if frozenset(p) in pbk)
            return (-total,)
        return _core_row(team, pair_by_key_list, target_name_lists,
                         good_threshold)["worst_enemy_score_key"]

    seeds = [(score(list(p)), list(p)) for p in itertools.combinations(pool, 2)
             if _core_passes_hard_filters(p, merged, growth_limits,
                                          max_megas=max_megas,
                                          max_weak_types=max_weak_types)]
    seeds.sort(key=lambda x: x[0])
    beam = [t for _, t in seeds[:beam_width]]

    found = {}  # sorted-tuple -> row, across every size 4/5/6 the beam passes through
    for _ in range(4):  # grow 2 -> 3 -> 4 -> 5 -> 6
        cand = {}
        for team in beam:
            for n in pool:
                if n in team:
                    continue
                key = tuple(sorted(team + [n]))
                if key in cand:
                    continue
                if not _core_passes_hard_filters(key, merged, growth_limits,
                                                 max_megas=max_megas,
                                                 max_weak_types=max_weak_types):
                    continue
                cand[key] = score(list(key))
        ranked = sorted(cand.items(), key=lambda kv: kv[1])[:beam_width]
        beam = [list(k) for k, _ in ranked]
        if not beam:
            break
        if len(beam[0]) in core_sizes:
            for team in beam:
                key = tuple(sorted(team))
                if key in found:
                    continue
                if not _core_passes_hard_filters(key, merged, effective_limits,
                                                 max_megas=max_megas,
                                                 max_weak_types=max_weak_types):
                    continue  # catches a max_net violation growth couldn't see
                row = _core_row(team, pair_by_key_list, target_name_lists, good_threshold)
                if not row["unused"]:
                    found[key] = row

    rows = list(found.values())
    rows.sort(key=lambda r: r["worst_enemy_score_key"])
    return rows


def deep_dive(name1, name2, target_names, merged, moves_db, natures,
             typechart, turns=2, item_overrides=None, move_overrides=None,
             excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
        item_overrides=item_overrides, move_overrides=move_overrides,
        excluded_items=excluded_items)
    item2, moves2, _w2 = _answer_for(
        name2, merged, moves_db, natures, typechart, target_names,
        item_overrides=item_overrides, move_overrides=move_overrides,
        excluded_items=excluded_items)
    our_built = _build_forms([name1, name2], merged, natures, moves_db,
                             items={name1: item1, name2: item2})
    our_built[name1]["moves"] = _move_infos(name1, merged, moves_db, moves1)
    our_built[name2]["moves"] = _move_infos(name2, merged, moves_db, moves2)
    enemy_built = _build_forms(target_names, merged, natures, moves_db)
    detail, summary = _pair_vs_targets(name1, name2, our_built, target_names,
                                       enemy_built, typechart, turns,
                                       want_grid=True, merged=merged)
    return item1, item2, detail, summary


_ROW_TOTAL_FIELDS = ("pairs_swept", "pairs_traded", "pairs_lost", "pairs_no_ko",
                     "pairs_tailwind_safe", "pairs_protect_safe",
                     "pairs_clean_win_total", "pairs_total")


def _sum_rows(rows):
    """Elementwise sum of `_ROW_TOTAL_FIELDS` across `rows` (any iterable of
    dicts carrying them, e.g. `_pair_vs_targets`'s own `summary`) -- the
    "across 6 possible pairs beaten is 85/90" aggregate `core_deep_dive`
    reports at both the per-pair and whole-core level."""
    return {f: sum(r[f] for r in rows) for f in _ROW_TOTAL_FIELDS}


def _core_deep_dive_race(core, target_name_lists, our_built, enemy_built_by_team,
                         typechart, turns, merged, sets, forced_base_names):
    """`core_deep_dive`'s own racing body, factored out so it can be run
    TWICE (once per mega hypothesis) when the core carries 2 stone-holders
    -- see `core_deep_dive`'s own docstring."""
    per_pair = {}
    all_summaries = []
    for n1, n2 in itertools.combinations(core, 2):
        per_enemy = []
        for target_names, enemy_built in zip(target_name_lists, enemy_built_by_team):
            detail, summary = _pair_vs_targets(
                n1, n2, our_built, target_names, enemy_built, typechart,
                turns, merged=merged, forced_base_names=forced_base_names)
            per_enemy.append({"target_names": target_names, "detail": detail,
                             "summary": summary})
        pair_total = _sum_rows([pe["summary"] for pe in per_enemy])
        per_pair[(n1, n2)] = {"per_enemy": per_enemy, "total": pair_total}
        all_summaries.append(pair_total)
    overall = _sum_rows(all_summaries)
    return {"core": tuple(core), "sets": sets, "per_pair": per_pair,
           "overall": overall}


def core_deep_dive(core, target_name_lists, merged, moves_db, natures, typechart,
                   turns=2, item_overrides=None, move_overrides=None,
                   excluded_items=DEFAULT_EXCLUDED_ITEMS,
                   enforce_item_clause=False):
    """The full report for an ALREADY-CHOSEN core (the `--multi-bring4`
    result the user actually wants to inspect, not a fresh search): every
    one of its C(size,2) pairs, raced against every enemy pair drawn from
    EVERY named enemy team, with the turn-by-turn log kept for each --
    "I also want to see the gameplans for each pair included in a team vs
    enemies", and "across 6 possible pairs beaten is 85/90" -- an
    aggregate total, both per-pair (summed across every enemy team) and
    for the whole core (summed across every pair AND every enemy team).

    "Given the size, maybe make this deep dive an option after the search
    has run" -- this is deliberately NOT part of `multi_bring4_exhaustive`/
    `multi_bring4_beam`'s own per-core output (which only needs the
    already-computed `_core_row` summary numbers to rank cores); it is a
    SEPARATE, opt-in follow-up call for the one core the user wants to
    actually inspect, paid for once instead of for every core the main
    search considers.

    EVERY MEMBER'S SET IS FIXED FOR THE WHOLE CORE, same "a real team's
    set is fixed for the whole event" rule `multi_bring4_coverage` uses --
    searched ONCE per member, against the union of every named enemy
    across `target_name_lists`, then reused for every enemy team's races
    below (never a different item/moveset per enemy).

    `enforce_item_clause`: off by default -- when `True`, resolves `core`'s
    items with VGC's real Item Clause enforced (`_resolve_unique_items`)
    before any racing, so `sets` never shows two members holding the same
    item. See `bring4_search`'s own docstring for why this is opt-in.

    BRING-4-CONSISTENT MEGA CHOICE: when `core` carries exactly 2 Mega-stone
    holders, this function (unlike `bring4_search`) doesn't do bring-4
    subsetting -- it reports every one of the core's own C(size,2) pairs
    directly, so there is no "which subset" question, only "which of the
    (at most 2) team-wide hypotheses is better for this core as a whole."
    The whole racing pass runs TWICE, once per hypothesis (`_pair_
    sort_key`'s existing "lower is better" ranking on each `overall` decides
    the winner), and only that winning hypothesis is returned. A core with
    0 or 1 stone-holders needs no such choice and races once, unconstrained,
    exactly as before this existed.

    Returns {"core": tuple(core), "sets": {name: {"item", "moves"}},
    "per_pair": {(n1, n2): {"per_enemy": [{"target_names", "detail",
    "summary"}, ... one entry per `target_name_lists` member ...],
    "total": <summed pairs_swept/traded/lost/no_ko/tailwind_safe/
    protect_safe/total across every enemy team, for this ONE pair>}, ...
    one entry per pair in `core`...}, "overall": <the same summed shape,
    across EVERY pair and EVERY enemy team>}.
    """
    core = list(dict.fromkeys(core))
    target_name_lists = [list(t) for t in target_name_lists]
    all_enemies = sorted({n for t in target_name_lists for n in t})
    if enforce_item_clause:
        item_overrides = _resolve_unique_items(
            core, merged, moves_db, natures, typechart, all_enemies,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
    sets = {}
    for name in core:
        item, move_names, _weather = _answer_for(
            name, merged, moves_db, natures, typechart, all_enemies,
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
        if not move_names:
            raise ValueError(f"{name!r} has no usable moveset against "
                             f"{all_enemies}")
        sets[name] = {"item": item, "moves": move_names}
    our_built = _build_forms(core, merged, natures, moves_db,
                             items={n: s["item"] for n, s in sets.items()})
    for n, s in sets.items():
        our_built[n]["moves"] = _move_infos(n, merged, moves_db, s["moves"])
    enemy_built_by_team = [_build_forms(t, merged, natures, moves_db)
                           for t in target_name_lists]

    megas = [n for n in core if n.startswith("Mega ")]
    if len(megas) == 2:
        dive_a = _core_deep_dive_race(
            core, target_name_lists, our_built, enemy_built_by_team, typechart,
            turns, merged, sets, forced_base_names=frozenset({megas[1]}))
        dive_b = _core_deep_dive_race(
            core, target_name_lists, our_built, enemy_built_by_team, typechart,
            turns, merged, sets, forced_base_names=frozenset({megas[0]}))
        return dive_a if (_pair_sort_key(dive_a["overall"])
                          <= _pair_sort_key(dive_b["overall"])) else dive_b
    return _core_deep_dive_race(core, target_name_lists, our_built,
                                enemy_built_by_team, typechart, turns, merged,
                                sets, forced_base_names=frozenset())


def switch_in_search(name1, name2, enemy_pair, bench, merged, moves_db,
                     natures, typechart, turns=2, item_overrides=None,
                     move_overrides=None, excluded_items=DEFAULT_EXCLUDED_ITEMS):
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
            item_overrides=item_overrides, move_overrides=move_overrides,
            excluded_items=excluded_items)
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
                item_overrides=item_overrides, move_overrides=move_overrides,
                excluded_items=excluded_items)
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
                    # `combatants` has it in its normal slot. Same for a
                    # Grassy Surge candidate and `_field_terrain`.
                    race_weather = _field_weather(combatants)
                    race_terrain = _field_terrain(combatants)
                    outcome, turns_used, _hp, log = _joint_race(
                        combatants, moves_by_role, typechart, race_weather, turns,
                        first_turn_moves_override=override, terrain=race_terrain)
                    entry = {"outcome": outcome, "turns_used": turns_used, "log": log,
                             "_combatants": combatants, "_moves_by_role": moves_by_role,
                             "_weather": race_weather, "_terrain": race_terrain}
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
                first_turn_moves_override=override, terrain=best["_terrain"])
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
