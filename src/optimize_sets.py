"""
Moveset + item optimisation.

IDEA (as requested): instead of blindly taking each Pokemon's top-4 usage
moves, compute ONCE the value of every candidate move against every
individual Pokemon appearing in teams.csv, then choose the 4-move set that
maximises coverage across that specific threat list. Same for the item.

This is what surfaces picks like "Gallade with Leaf Blade" -- Leaf Blade is
a real usage move for it, and against a teams.csv full of Water/Ground
threats (Pelipper, Mega Swampert, Archaludon) a Grass coverage move can be
worth far more than the 4th-most-used option.

Cost model: this uses 1v1 damage calculations, NOT full battles, so
evaluating every (mon x move x enemy) triple is cheap -- a few seconds for
the whole pool. It is a coverage heuristic, not a battle result: it knows
"this move hits that threat hard" but not "I'd be dead before I used it".
Treat it as set selection, then let the real solver judge the team.
"""
import itertools

from damage import MoveInfo, effective_stat, damage_roll, hit_count_for
from combatants import make_combatant

# NOTE: there is deliberately no hardcoded catalogue of "good items" here.
# Items are restricted to those that actually appear in that Pokemon's Items
# column in mbsmogon.xlsx -- inventing plausible-sounding items (a Choice Band
# on something that never runs one) produces sets you cannot actually field.
MIN_ITEM_USAGE = 0.0   # keep every listed item; raise to prune very rare ones

# --- Doubles utility values -------------------------------------------------
# These are on the same scale as the offensive score: "portion of one enemy's
# HP". A pure damage model badly undervalues all of them.
PROTECT_MOVES_OPT = {"Protect", "Detect", "Spiky Shield", "Baneful Bunker", "Silk Trap"}
SIDE_GUARDS = {"Wide Guard", "Quick Guard"}
SPEED_CONTROL = {"Tailwind", "Trick Room", "Thunder Wave", "Glare"}
REDIRECTION = {"Follow Me", "Rage Powder"}
CHOICE_ITEMS_SET = {"Choice Band", "Choice Specs", "Choice Scarf"}
PROTECT_NAMES = ["Protect", "Detect", "Spiky Shield", "Baneful Bunker", "Silk Trap"]

# Damaging moves that ALSO apply speed control. Icy Wind does little damage,
# but dropping both foes' Speed can decide every subsequent turn.
SPEED_DROP_MOVES = {
    "Icy Wind": 0.55, "Electroweb": 0.55, "Bulldoze": 0.40, "Rock Tomb": 0.40,
    "Mud Shot": 0.35, "Low Sweep": 0.30, "Icy Wind ": 0.55,
}

# Known-good hand-specified movesets to compare against the usage-derived pick
# for a specific species -- kept only if they actually score better, same as
# best_item trying multiple items. e.g. enemy Kingambit sets (Kowtow Cleave /
# Sucker Punch / Iron Head / Low Kick) commonly get edged out of the
# usage-derived top-4 by the forced Protect slot even though all four moves
# are individually high-usage; this lets that exact set be checked directly.
FORCED_MOVE_CANDIDATES = {
    "Kingambit": [["Kowtow Cleave", "Sucker Punch", "Iron Head", "Low Kick"]],
}

UTILITY_VALUE = {
    # Fake Out denies a turn outright -- worth far more than its 40 BP suggests.
    "Fake Out": 0.45,
    "Protect": 0.85, "Detect": 0.85, "Spiky Shield": 0.85, "Baneful Bunker": 0.85,
    "Silk Trap": 0.85,
    "Wide Guard": 0.70, "Quick Guard": 0.60,
    "Tailwind": 0.80, "Trick Room": 0.80,
    "Follow Me": 0.75, "Rage Powder": 0.75,
    "Helping Hand": 0.35, "Parting Shot": 0.45, "Will-O-Wisp": 0.40,
    "Thunder Wave": 0.45, "Taunt": 0.35, "Encore": 0.35,
    # Reflect/Light Screen halve one damage category doubles-adjusted (2732/4096,
    # see damage.damage_roll); Aurora Veil does both at once but needs snow.
    # Boosted further below when the holder carries Light Clay (8 turns vs 5) --
    # this is what makes a Grimmsnarl screens-then-Parting-Shot line score
    # correctly instead of reading as "rarely attacks" filler.
    "Reflect": 0.55, "Light Screen": 0.55, "Aurora Veil": 0.75,
}
LIGHT_CLAY_SCREEN_MULT = 1.4  # 8 turns vs 5


# EVs in this dataset (mbsmogon.xlsx) are a flat, small-budget "Champions M-B"
# house rule -- NOT standard EVs (see stats.calc_stat) -- observed as a fixed
# ~66-point total and a 32-point per-stat cap across the sheet.
EV_CAP_PER_STAT = 32


def bulk_spread_for(name, merged):
    """An alternative max-bulk EV spread for `name`: reallocate EVs OUT of
    Def/SpDef and the LESS-used offensive stat (the "dead" attack the mon
    isn't built around) INTO HP, keeping the mon's total EV budget and the
    32-per-stat cap. Speed and the primary attacking stat (whichever of
    Atk/SpA is higher in its base stats) are left untouched, so it keeps
    functioning offensively/speed-wise -- only bulk is being added, not a
    different mon. Returns a fresh evs dict; does not mutate `merged`.
    """
    p = merged[name]
    evs = dict(p["evs"])
    base = p["base_stats"]
    primary = "atk" if base.get("atk", 0) >= base.get("spa", 0) else "spa"
    secondary = "spa" if primary == "atk" else "atk"

    new_evs = dict(evs)
    reclaimed = 0
    for stat in (secondary, "def", "spd"):
        reclaimed += new_evs.get(stat, 0)
        new_evs[stat] = 0

    # A lean glass-cannon spread (e.g. Garchomp's usage-default 2/32/0/0/0/32)
    # has nothing in Def/SpDef/secondary-offense to reclaim -- Speed is the
    # only lever left to actually buy bulk with, so give up HALF of it rather
    # than returning a no-op "bulk" spread identical to the default.
    if reclaimed == 0:
        spe = new_evs.get("spe", 0)
        give_up = spe // 2
        new_evs["spe"] = spe - give_up
        reclaimed += give_up

    hp_room = max(0, EV_CAP_PER_STAT - new_evs.get("hp", 0))
    add_to_hp = min(reclaimed, hp_room)
    new_evs["hp"] = new_evs.get("hp", 0) + add_to_hp
    leftover = reclaimed - add_to_hp
    # HP already capped -- park any leftover back in Def/SpDef/secondary
    # offense (in that preference order) rather than dropping EV points.
    for stat in ("def", "spd", secondary):
        if leftover <= 0:
            break
        room = EV_CAP_PER_STAT - new_evs.get(stat, 0)
        give = min(leftover, room)
        new_evs[stat] = new_evs.get(stat, 0) + give
        leftover -= give
    return new_evs


def best_spread(name, merged, moves_db, natures, typechart, enemy_names, item=None, slots=4):
    """Try the usage-default EV spread against the bulk_spread_for alternative
    for this matchup, keeping whichever scores higher -- same "try it, keep it
    if it wins" pattern as best_item trying multiple items. Useful standalone
    (does a bulkier spread perform better here?) and as a building block for
    salvaging a losing matchup.

    Returns (move_names, score, evs_used, used_bulk: bool).
    """
    default_mv, default_score = best_moveset(name, merged, moves_db, natures, typechart,
                                              enemy_names, item=item, slots=slots)
    bulk_evs = bulk_spread_for(name, merged)
    if bulk_evs == merged[name]["evs"]:
        return default_mv, default_score, merged[name]["evs"], False
    bulk_mv, bulk_score = best_moveset(name, merged, moves_db, natures, typechart, enemy_names,
                                        item=item, slots=slots, evs=bulk_evs)
    if bulk_score > default_score:
        return bulk_mv, bulk_score, bulk_evs, True
    return default_mv, default_score, merged[name]["evs"], False


# Type-resist berries: halve damage from a super-effective hit of that type
# (consumed on use). Not usage-list items (nobody's logged usage stats show
# them, they're situational), so they only enter the search deliberately --
# see legal_items' extra_items and the salvage flow, which adds the specific
# berry for a matchup's identified losing type, not every mon's candidates.
TYPE_RESIST_BERRY = {
    "Normal": "Chilan Berry", "Fire": "Occa Berry", "Water": "Passho Berry",
    "Electric": "Wacan Berry", "Grass": "Rindo Berry", "Ice": "Yache Berry",
    "Fighting": "Chople Berry", "Poison": "Kebia Berry", "Ground": "Shuca Berry",
    "Flying": "Coba Berry", "Psychic": "Payapa Berry", "Bug": "Tanga Berry",
    "Rock": "Charti Berry", "Ghost": "Kasib Berry", "Dragon": "Haban Berry",
    "Dark": "Colbur Berry", "Steel": "Babiri Berry", "Fairy": "Roseli Berry",
}


def legal_items(name, merged, min_usage=MIN_ITEM_USAGE, extra_items=None):
    """Items this Pokemon is actually recorded using, most-used first.

    A Mega with a stone is locked to it. Everything else is limited to its own
    usage list -- no cross-species item catalogue -- PLUS any `extra_items`
    explicitly passed in (e.g. a specific type-resist berry the salvage flow
    wants tried for a matchup with an identified weakness), which are
    otherwise never considered since nobody logs usage stats for them.
    """
    from species_data import find_mega_stone
    stone = find_mega_stone(name, merged)
    if stone:
        return [stone]
    items = [i for i, pct in merged[name]["items_usage"]
             if pct >= min_usage and i.lower() != "other"]
    for extra in (extra_items or []):
        if extra not in items:
            items.append(extra)
    return items or ["Leftovers"]


def _mk_move(m):
    return MoveInfo(m["name"], m["basePower"], m["type"], m["category"], m["target"],
                     priority=m.get("priority", 0), secondary=m.get("secondary"),
                     self_effect=m.get("self"), boosts=m.get("boosts"),
                     recoil=m.get("recoil"), drain=m.get("drain"),
                     volatile_status=m.get("volatileStatus"), flags=m.get("flags"),
                     self_switch=m.get("selfSwitch"))


NEVER_MISS_ABILITIES = {"No Guard"}
WEATHER_PERFECT_ACCURACY = {"Hurricane": "rain", "Thunder": "rain", "Blizzard": "snow"}


def _accuracy_ok(move_raw, name, merged, team_weather=None):
    """Drop shaky moves (<80% accuracy) unless they cannot miss.

    A move is kept if the user has No Guard, or if the move is perfectly accurate
    in a weather the team itself sets (Hurricane/Thunder in rain, Blizzard in
    snow). Planning around a 70% move that whiffs is worse than a weaker sure hit.
    """
    acc = move_raw.get("accuracy", True)
    if acc is True or acc >= 80:
        return True
    from combatants import _default_ability
    ability = _default_ability(merged[name]["abilities_usage"])
    if ability in NEVER_MISS_ABILITIES:
        return True
    need = WEATHER_PERFECT_ACCURACY.get(move_raw.get("name"))
    if need and team_weather == need:
        return True
    return False


def candidate_moves(name, merged, moves_db, max_candidates=10, team_weather=None, item=None):
    """All reasonably-used moves for this Pokemon, as (MoveInfo, usage%)."""
    out = []
    for mv, pct in merged[name]["moves_usage"]:
        if mv == "Other":
            continue
        k = mv.lower().replace(" ", "").replace("-", "").replace("'", "")
        if k not in moves_db:
            continue
        if not _accuracy_ok(moves_db[k], name, merged, team_weather):
            continue
        # Population Bomb's hit count (and therefore its whole value) depends on
        # Wide Lens -- without it, treat it as too unreliable to plan around
        # (use Super Fang or similar instead), same rule as the <80%-accuracy filter.
        if mv == "Population Bomb" and item != "Wide Lens":
            continue
        out.append((_mk_move(moves_db[k]), pct))
    return out[:max_candidates]


def move_value_table(name, merged, moves_db, natures, typechart, enemy_names, item=None,
                      evs=None, nature=None):
    """{move_name: {enemy_name: value}} where value is roughly 'fraction of
    that enemy's HP this move removes', with a bonus for securing a KO.

    Status/utility moves get a flat utility score instead -- they don't do
    damage, but a moveset of four attacks with no Protect/Trick Room is
    usually worse than one with them, so they need a non-zero value.

    evs/nature: optional EV-spread override (see bulk_spread_for) so a
    non-default spread's actual offensive output gets scored, not the
    usage-default spread's.
    """
    attacker = make_combatant(name, merged, natures, item=item, evs=evs, nature=nature)
    table = {}
    for move, pct in candidate_moves(name, merged, moves_db, item=item):
        row = {}
        for en in enemy_names:
            defender = make_combatant(en, merged, natures)
            if move.category == "Status":
                # Utility value if we know this move matters in doubles, else a small
                # usage-weighted default. NOT scaled down by usage for the known ones --
                # Protect being "only" 34% used doesn't make it a bad move.
                val = UTILITY_VALUE.get(move.name, 0.22 * (pct / 100.0))
                if move.name in ("Reflect", "Light Screen", "Aurora Veil") and item == "Light Clay":
                    val *= LIGHT_CLAY_SCREEN_MULT
                row[en] = val
                continue
            atk_key = "atk" if move.category == "Physical" else "spa"
            def_key = "def" if move.category == "Physical" else "spd"
            a = effective_stat(attacker.stats[atk_key], 0)
            if item == "Choice Band" and atk_key == "atk":
                a *= 1.5
            if item == "Choice Specs" and atk_key == "spa":
                a *= 1.5
            d = effective_stat(defender.stats[def_key], 0)
            _, _, avg, eff = damage_roll(50, move.power, a, d, attacker, defender, move, typechart)
            avg *= hit_count_for(move.name, attacker)
            frac = avg / defender.stats["hp"] if defender.stats["hp"] else 0.0
            if frac >= 1.0:
                frac = 1.0 + 0.5  # OHKO bonus
            frac += SPEED_DROP_MOVES.get(move.name, 0.0)
            # Priority moves are worth far more than their base power suggests: they
            # revenge-kill weakened threats, finish through faster sweepers, and ignore
            # the speed tier entirely. A pure damage score sees Accelerock as a feeble
            # 40 BP move and would cut it from the set.
            # Prefer a clean KO over a recoil KO: same result, less self-damage.
            if move.recoil and frac >= 1.0:
                frac -= 0.12
            if move.priority > 0 and move.category != "Status":
                frac += 0.30 * move.priority
                if frac >= 0.45:      # already a meaningful chunk -> genuine revenge KO range
                    frac += 0.20
            if move.name == "Fake Out":
                frac += UTILITY_VALUE["Fake Out"]
            row[en] = frac
        table[move.name] = row
    return table


def best_moveset(name, merged, moves_db, natures, typechart, enemy_names, item=None,
                  slots=4, force_protect=True, forced_candidates=None, evs=None, nature=None):
    """Pick the `slots` moves maximising summed best-value coverage over the
    enemy list: for each enemy, only the single best move against it counts,
    so redundant same-type attacks don't stack.

    forced_candidates: optional list of hand-specified `slots`-length move-name
    lists to score alongside the searched pick, keeping whichever scores
    higher (same "try it, keep it if it wins" pattern as best_item trying
    multiple items). Defaults to FORCED_MOVE_CANDIDATES[name] if not given.

    evs/nature: optional EV-spread override, see move_value_table.
    """
    table = move_value_table(name, merged, moves_db, natures, typechart, enemy_names, item=item,
                              evs=evs, nature=nature)
    names = list(table.keys())
    # A Choice item locks you into the first move used, so Protect/Detect is a dead
    # slot -- drop them entirely rather than letting a zero-value move occupy one of
    # the four. Without this a Choice set wasted a slot and could never compete.
    if item and item.startswith("Choice"):
        names = [n for n in names if n not in PROTECT_MOVES_OPT and n not in SIDE_GUARDS] or names
    if len(names) <= slots:
        best, best_score = names, _score_moveset(names, table, enemy_names)
    else:
        # A non-Choice-locked Pokemon in doubles should essentially always carry a
        # protect-type move: it blocks spread damage, stalls Trick Room / Tailwind
        # turns, and scouts. Reserve a slot for it when one is available and legal.
        # Choice items make Protect a dead slot (you'd be locked into it), so skip.
        forced = []
        if force_protect and item not in CHOICE_ITEMS_SET:
            have = [p for p in PROTECT_NAMES if p in names]
            if have:
                forced = [have[0]]

        remaining_slots = slots - len(forced)
        pickable = [n for n in names if n not in forced]
        best, best_score = None, -1.0
        for combo in itertools.combinations(pickable, remaining_slots):
            full = forced + list(combo)
            sc = _score_moveset(full, table, enemy_names)
            if sc > best_score:
                best, best_score = full, sc

    # Compare against any hand-specified candidate set(s) -- keep whichever
    # scores higher. A candidate is only scorable if every one of its moves
    # is actually in this Pokemon's usage-derived candidate table.
    for cand in (forced_candidates if forced_candidates is not None
                 else FORCED_MOVE_CANDIDATES.get(name, [])):
        if not all(m in table for m in cand):
            continue
        sc = _score_moveset(cand, table, enemy_names)
        if sc > best_score:
            best, best_score = list(cand), sc

    return best, best_score


def _score_moveset(move_names, table, enemy_names):
    total = 0.0
    for en in enemy_names:
        total += max(table[m][en] for m in move_names)
    return total


def incoming_threat(name, merged, moves_db, natures, typechart, enemy_names):
    """For each enemy individual, the fraction of OUR max HP their best move
    removes. Used to give defensive items credit -- without this the optimiser
    just picks Life Orb on everything, because a pure damage-coverage score
    can never see the value of surviving."""
    me = make_combatant(name, merged, natures)
    out = {}
    for en in enemy_names:
        foe = make_combatant(en, merged, natures)
        worst_phys = worst_spec = 0.0
        for move, pct in candidate_moves(en, merged, moves_db):
            if move.category == "Status":
                continue
            atk_key = "atk" if move.category == "Physical" else "spa"
            def_key = "def" if move.category == "Physical" else "spd"
            a = effective_stat(foe.stats[atk_key], 0)
            d = effective_stat(me.stats[def_key], 0)
            _, _, avg, _ = damage_roll(50, move.power, a, d, foe, me, move, typechart)
            frac = avg / me.stats["hp"] if me.stats["hp"] else 0.0
            if move.category == "Physical":
                worst_phys = max(worst_phys, frac)
            else:
                worst_spec = max(worst_spec, frac)
        out[en] = (worst_phys, worst_spec)
    return out


def defensive_bonus(item, name, merged, threat, n_attacks_estimate=3):
    """Rough value of an item's survivability contribution, on the same
    ~0-1-per-enemy scale the offensive coverage score uses."""
    ohko_count = sum(1 for p, s in threat.values() if max(p, s) >= 1.0)
    heavy_spec = sum(1 for p, s in threat.values() if s >= 0.5)
    n = max(1, len(threat))

    if item == "Focus Sash":
        return 1.2 * (ohko_count / n) * len(threat) * 0.25
    if item == "Assault Vest":
        return 0.9 * (heavy_spec / n) * len(threat) * 0.25
    if item in ("Sitrus Berry", "Leftovers"):
        return 0.45 * len(threat) * 0.25
    if item == "Life Orb":
        # 10% max HP per attacking turn is a real cost the damage term ignores.
        return -0.10 * n_attacks_estimate * len(threat) * 0.25
    if item == "White Herb":
        # Only worth a slot if the set actually uses self-lowering moves; the
        # caller adds that context, so keep this neutral-small here.
        return 0.15 * len(threat) * 0.25
    return 0.0


def best_item(name, merged, moves_db, natures, typechart, enemy_names, slots=4):
    """Try each candidate item, re-optimise the moveset under it, keep the best.

    Score = offensive coverage (damage vs each enemy) + defensive value
    (surviving their best hits). Mega picks are skipped entirely -- they must
    hold their Mega Stone and cannot hold anything else.
    """
    from species_data import find_mega_stone
    stone = find_mega_stone(name, merged)
    if stone:
        # Locked to its Mega Stone -- only the moveset is optimisable.
        mv, sc = best_moveset(name, merged, moves_db, natures, typechart, enemy_names, item=stone,
                               slots=slots)
        return stone, mv, sc

    threat = incoming_threat(name, merged, moves_db, natures, typechart, enemy_names)
    tried = legal_items(name, merged)

    best = (None, None, -1e9)
    for it in tried:
        mv, sc = best_moveset(name, merged, moves_db, natures, typechart, enemy_names, item=it,
                               slots=slots)
        sc += defensive_bonus(it, name, merged, threat)

        # Prefer sets that keep Protect/Detect available on anything not Choice-locked.
        if not it.startswith("Choice"):
            if not any(mn in PROTECT_MOVES_OPT for mn in mv):
                has_protect_option = any(m.name in PROTECT_MOVES_OPT
                                          for m, _ in candidate_moves(name, merged, moves_db))
                if has_protect_option:
                    sc -= 0.6 * len(enemy_names) * 0.25

        # White Herb only earns its keep if the chosen set has a self-lowering move.
        if it == "White Herb":
            has_drop = any(
                (m.self_effect or {}).get("boosts")
                for m, _ in candidate_moves(name, merged, moves_db) if m.name in mv
            )
            if not has_drop:
                sc -= 0.15 * len(enemy_names) * 0.25

        if it == "Choice Scarf":
            me = make_combatant(name, merged, natures)
            base_spe = me.stats["spe"]
            gained = sum(1 for en in enemy_names
                          if base_spe < make_combatant(en, merged, natures).stats["spe"] <= base_spe * 1.5)
            sc += 0.6 * gained
        if it.startswith("Choice"):
            # Choice locks you into the first move used. A set containing status /
            # utility moves (Fake Out, Parting Shot, Protect, Trick Room...) simply
            # cannot function locked, so penalise hard rather than by a flat factor.
            status_moves = [m.name for m, _ in candidate_moves(name, merged, moves_db)
                            if m.name in mv and m.category == "Status"]
            fakeout_like = [m for m in mv if m in ("Fake Out", "First Impression")]
            if status_moves or fakeout_like:
                sc *= 0.45
            else:
                sc *= 0.9
        if sc > best[2]:
            best = (it, mv, sc)
    return best


def optimise_team(team, merged, moves_db, natures, typechart, enemy_names, slots=4):
    """Item + moveset optimisation for a whole team, enforcing ONE ITEM PER TEAM.

    Each held item may appear on only one Pokemon, so items cannot be chosen
    independently per mon -- picking every mon's individual favourite happily
    hands Life Orb to three of them, which is an illegal team. This scores
    every (Pokemon, legal item) pair and assigns greedily by score, skipping
    items already taken. Mega Stones are exempt from competition: a Mega must
    hold its own stone, and no two Pokemon can want the same one anyway.
    """
    from species_data import find_mega_stone

    # 1. Lock Megas to their stones first -- they have no choice.
    out, taken = {}, set()
    pending = []
    for n in team:
        stone = find_mega_stone(n, merged)
        if stone:
            mv, sc = best_moveset(n, merged, moves_db, natures, typechart, enemy_names,
                                   item=stone, slots=slots)
            out[n] = {"item": stone, "moves": mv, "score": sc}
            taken.add(stone)
        else:
            pending.append(n)

    # 2. Score every (mon, item) combination that's still available.
    scored = []
    for n in pending:
        threat = incoming_threat(n, merged, moves_db, natures, typechart, enemy_names)
        for it in legal_items(n, merged):
            mv, sc = best_moveset(n, merged, moves_db, natures, typechart, enemy_names,
                                   item=it, slots=slots)
            sc += defensive_bonus(it, n, merged, threat)
            if it.startswith("Choice"):
                status_moves = [x for x in mv if x in PROTECT_MOVES_OPT or x in SPEED_CONTROL]
                sc *= 0.45 if (status_moves or "Fake Out" in mv) else 0.9
            elif not any(x in PROTECT_MOVES_OPT for x in mv):
                if any(mo.name in PROTECT_MOVES_OPT for mo, _ in candidate_moves(n, merged, moves_db)):
                    sc -= 0.6 * len(enemy_names) * 0.25
            scored.append((sc, n, it, mv))

    # 3. Greedy global assignment: best remaining (mon, item) pair wins the item.
    scored.sort(key=lambda x: -x[0])
    for sc, n, it, mv in scored:
        if n in out or it in taken:
            continue
        out[n] = {"item": it, "moves": mv, "score": sc}
        taken.add(it)

    # 4. Anything left over (all its preferred items taken) gets its best free item.
    for n in pending:
        if n in out:
            continue
        for it in legal_items(n, merged):
            if it not in taken:
                mv, sc = best_moveset(n, merged, moves_db, natures, typechart, enemy_names,
                                       item=it, slots=slots)
                out[n] = {"item": it, "moves": mv, "score": sc}
                taken.add(it)
                break
        else:
            mv, sc = best_moveset(n, merged, moves_db, natures, typechart, enemy_names, slots=slots)
            out[n] = {"item": "(none available)", "moves": mv, "score": sc}
    return out


def enemy_individuals(teams: dict):
    """Every distinct Pokemon appearing across all teams.csv rosters."""
    seen = []
    for roster in teams.values():
        for m in roster:
            if m not in seen:
                seen.append(m)
    return seen


def optimise_team_unique(team, merged, moves_db, natures, typechart, enemy_names, slots=4):
    """Optimise items+movesets for a whole team with ITEM UNIQUENESS enforced:
    no two members may hold the same item (Mega Stones are per-species so they
    never collide).

    Greedy assignment by strength of preference: score every (mon, item) pair,
    then assign in order of how much each mon loses by being denied its first
    choice, so the Pokemon that most needs a specific item gets it.
    """
    from species_data import find_mega_stone

    # Score every legal (mon, item) combination once.
    table = {}
    for n in team:
        stone = find_mega_stone(n, merged)
        items = [stone] if stone else legal_items(n, merged)
        threat = None if stone else incoming_threat(n, merged, moves_db, natures, typechart,
                                                     enemy_names)
        row = {}
        for it in items:
            mv, sc = best_moveset(n, merged, moves_db, natures, typechart, enemy_names,
                                   item=it, slots=slots)
            if not stone:
                sc += defensive_bonus(it, n, merged, threat)
                if it.startswith("Choice"):
                    status = [x for x in mv if x in PROTECT_MOVES_OPT or x in ("Fake Out",)]
                    sc *= 0.45 if status else 0.9
            row[it] = (sc, mv)
        table[n] = row

    # Assign most-constrained first: the mon whose best item beats its second best
    # by the widest margin gets first pick.
    def regret(n):
        vals = sorted((v[0] for v in table[n].values()), reverse=True)
        return (vals[0] - vals[1]) if len(vals) > 1 else 1e9

    out, taken = {}, set()
    for n in sorted(team, key=regret, reverse=True):
        best_it, best_sc, best_mv = None, -1e18, None
        for it, (sc, mv) in table[n].items():
            if it in taken:
                continue
            if sc > best_sc:
                best_it, best_sc, best_mv = it, sc, mv
        if best_it is None:  # everything taken -- fall back to its top legal item
            best_it, (best_sc, best_mv) = next(iter(table[n].items()))
        taken.add(best_it)
        out[n] = {"item": best_it, "moves": best_mv, "score": best_sc}
    return out


def move_contributions(name, item, moves, merged, moves_db, natures, typechart, enemy_names):
    """Per-move value for one Pokemon's chosen set.

    For each move, report how many enemy individuals it is that Pokemon's BEST
    option against, and the largest fraction of any enemy's HP it removes. A
    move that is best against nobody and does little damage is dead weight --
    that slot is a candidate for a setup or utility move instead.
    """
    table = move_value_table(name, merged, moves_db, natures, typechart, enemy_names, item=item)
    table = {k: v for k, v in table.items() if k in moves}
    out = {}
    for mvn in moves:
        row = table.get(mvn, {})
        best_for = [en for en in enemy_names
                    if row.get(en, 0) >= max((table[o].get(en, 0) for o in table), default=0)
                    and row.get(en, 0) > 0]
        out[mvn] = {
            "best_against": len(best_for),
            "examples": best_for[:3],
            "max_value": round(max(row.values()), 2) if row else 0.0,
            "dead_weight": len(best_for) == 0 and (max(row.values()) if row else 0) < 0.35,
        }
    return out


def suggest_replacements(name, item, moves, merged, moves_db, natures, typechart, enemy_names,
                          top_k=3):
    """For any dead-weight slot, propose the best unused alternatives.

    Deliberately includes setup and utility moves, which a pure damage score
    ranks poorly but which can convert a losing matchup into a winning one.
    """
    contrib = move_contributions(name, item, moves, merged, moves_db, natures, typechart,
                                  enemy_names)
    dead = [mvn for mvn, d in contrib.items() if d["dead_weight"]]
    if not dead:
        return {}
    full = move_value_table(name, merged, moves_db, natures, typechart, enemy_names, item=item)
    unused = [mvn for mvn in full if mvn not in moves]
    scored = sorted(unused, key=lambda mvn: -sum(full[mvn].values()))
    return {d: scored[:top_k] for d in dead}
