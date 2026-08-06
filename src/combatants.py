"""
Canonical Combatant factory, used by everything that actually runs
searches (matchup_search.py / run_search.py). Handles the Mega Evolution
mechanic properly: a "Mega X" pick starts the battle in its BASE form
(base stats/ability/typing) and only transforms to the mega form when
it's actually sent out (see engine.mega_evolve, called from on_switch_in).

Same Nature/EVs are used for both forms -- that's mechanically correct,
training doesn't change on Mega Evolution, only the base stat table,
ability, and (sometimes) typing do.
"""
import copy

from damage import Combatant
from stats import compute_stats
from species_data import base_form_name, resolve_species, resolve_team_mega_slot

_TEMPLATE_CACHE = {}


def make_combatant(name: str, merged: dict, natures: dict, pokedex: dict | None = None,
                    ability: str | None = None, item: str | None = None,
                    force_base_form: bool = False, evs: dict | None = None,
                    nature: str | None = None):
    """Returns a fresh Combatant. Internally caches an immutable template per
    (name, force_base_form) and deep-copies it -- building one from scratch
    involves stat math and (for Megas) a pokedex lookup, and the team search
    constructs these tens of thousands of times.

    evs/nature: optional per-instance overrides of mbsmogon.xlsx's usage-default
    spread (e.g. a bulk EV spread being compared against the default for the
    same species -- see optimize_sets.bulk_spread_for). Bypasses the template
    cache, same as a custom ability/item does, since the result is no longer
    the shared default template.
    """
    if ability is None and item is None and evs is None and nature is None:
        key = (name, force_base_form)
        tpl = _TEMPLATE_CACHE.get(key)
        if tpl is None:
            tpl = _build_combatant(name, merged, natures, pokedex, ability, item, force_base_form)
            _TEMPLATE_CACHE[key] = tpl
        return copy.deepcopy(tpl)
    return _build_combatant(name, merged, natures, pokedex, ability, item, force_base_form,
                             evs, nature)


def _build_combatant(name: str, merged: dict, natures: dict, pokedex: dict | None = None,
                      ability: str | None = None, item: str | None = None,
                      force_base_form: bool = False, evs: dict | None = None,
                      nature: str | None = None):
    p = merged[name]
    nat = natures[(nature or p["nature"]).lower()]
    ev_points = evs if evs is not None else p["evs"]
    ab = ability or (p["abilities_usage"][0][0] if p["abilities_usage"] else "")
    it = item or (p["items_usage"][0][0] if p["items_usage"] else "")

    base_name = base_form_name(name)
    if base_name is None:
        # Regular (non-Mega) Pokemon -- single form for the whole battle.
        stats = compute_stats(p["base_stats"], nat, ev_points)
        return Combatant(name=name, stats=stats, types=p["types"], ability=ab, item=it)

    # Mega pick: compute BOTH forms up front. Base form is what it starts
    # battle as; mega form is applied by engine.mega_evolve() on switch-in
    # -- UNLESS force_base_form is set (this team already has a different
    # Mega actually transforming, so this pick runs in base form all game).
    mega_stats = compute_stats(p["base_stats"], nat, ev_points)
    mega_types = p["types"]
    mega_ability = ab  # the mega-exclusive ability (e.g. Drought), already correct from mbsmogon.xlsx

    if base_name in merged:
        base_rec = merged[base_name]
        base_stats_table = base_rec["base_stats"]
        base_types = base_rec["types"]
        base_ability = base_rec["abilities_usage"][0][0] if base_rec["abilities_usage"] else mega_ability
    else:
        # Base species isn't in mbsmogon.xlsx (not played un-mega'd) -- fall back
        # to Showdown's pokedex for its base stats/types/ability directly.
        if pokedex is None:
            from species_data import load_showdown_static
            pokedex, _, _, _ = load_showdown_static()
        _, sdata = resolve_species(base_name, pokedex)
        if sdata is None:
            print(f"WARNING: could not resolve base form '{base_name}' for '{name}' -- "
                  f"treating as already Mega-evolved from turn 1 (inaccurate).")
            base_stats_table, base_types, base_ability = p["base_stats"], p["types"], mega_ability
        else:
            base_stats_table = sdata["baseStats"]
            base_types = sdata["types"]
            base_ability = list(sdata.get("abilities", {}).values())[0] if sdata.get("abilities") else mega_ability

    base_stats = compute_stats(base_stats_table, nat, ev_points)

    # ITEM RULE: a Mega that actually has a stone in the usage data must hold it and
    # cannot hold anything else, so item optimisation can never hand it a Life Orb.
    # If no stone is present for this row (see species_data.find_mega_stone), normal
    # item rules apply instead of forcing a nonexistent stone.
    from species_data import find_mega_stone
    stone = find_mega_stone(name, merged)
    if stone:
        it = stone

    if force_base_form:
        # Forced to stay base all game (a teammate is the team's actual Mega).
        # Keep the "Mega X" display name for team-sheet clarity, but is_mega_pick=False
        # means it will never transform. It still holds its stone (that's why it was
        # brought), it simply doesn't get to use it this battle.
        return Combatant(name=name, stats=base_stats, types=base_types, ability=base_ability, item=it,
                          is_mega_pick=False, mega_evolved=False)

    return Combatant(
        name=name, stats=base_stats, types=base_types, ability=base_ability, item=it,
        is_mega_pick=True, mega_evolved=False,
        mega_stats=mega_stats, mega_ability=mega_ability, mega_types=mega_types,
    )


def make_team(names: list[str], merged: dict, natures: dict, pokedex: dict | None = None,
              mega_transforms: str | None = None, sets: dict | None = None):
    """Build a full brought team, correctly resolving the multi-Mega-picks
    rule. By default the FIRST Mega-named entry in `names` actually
    transforms; pass `mega_transforms=<name>` to force a specific one
    instead (used to search over which Mega to actually evolve without
    disturbing lead/back position order)."""
    mega_that_evolves, forced_base = resolve_team_mega_slot(names, mega_transforms=mega_transforms)
    sets = sets or {}
    out = []
    for n in names:
        spec = sets.get(n) or {}
        out.append(make_combatant(n, merged, natures, pokedex=pokedex,
                                   item=spec.get("item"),
                                   force_base_form=(n in forced_base),
                                   evs=spec.get("evs"), nature=spec.get("nature")))
    return out
