"""Who wins the damageslop war: spread output x how long you survive.

The idea, as it was put to me:

    "Spread damage appears to be highly valuable, as this game is what I like to
     call a 'damageslop' format. Spread damage, given it attacks two enemies at
     0.75x, is a way to maximise damage output, and chip enemies into kills by
     partner. If your spread attacker is bulky and not threatened by
     supereffective damage, it can stay on the field and trade favourably vs the
     enemy (they cannot match its output). It also is simple because there is no
     target selection, so is harder to punish and harder to switch in on."

Two halves, and the product is the point.

OUTPUT. A spread move hits both foes at 0.75x, so its turn total is 1.5x a
single-target hit of the same power -- before any of the multipliers that make
the good ones absurd. The ones that matter are all modelled in `damage.py`
already and are simply switched on here: SELF-GENERATED WEATHER (Drought sun on
Heat Wave, Drizzle rain on Muddy Water), the -ate abilities (Pixilate turns
Hyper Voice into a 1.2x Fairy move, and then Fairy Feather multiplies it again),
Liquid Voice, Sheer Force, Aura.

SURVIVAL. Output you do not get to repeat is worth one turn of output. So the
score multiplies by how many hits the attacker takes to fall:

    hits_to_ko = 1 / mean(incoming physical, incoming special)

against a generic attacker -- 125 in the relevant offensive stat, 100 BP.

NEUTRAL THROUGHOUT, in both directions. Every figure divides out the actual type
multiplier, so the table ranks the Pokemon rather than the matchup: a Fire move
into a benchmark that happens to resist it is not evidence about Charizard. The
type chart is what the rest of the system is for; this answers the narrower
question of who brings the most raw, repeatable, untargetable damage.

WHAT THIS IS NOT. It is a screen, not a verdict. It does not know that the
opponent has a Fairy for your Dragon, that Wide Guard exists, that your spread
move hits your own partner (Earthquake, Muddy Water, Blizzard), or that Prankster
Tailwind changes who repeats first. Nor does it price the ally-hitting downside:
a `allAdjacent` move is scored on its two FOES only, so Earthquake looks like
Heat Wave here and does not play like it. Read it as "who is worth building
around", then let the real search argue.
"""
from damage import (Combatant, MoveInfo, damage_roll, defensive_stat,
                    effective_stat, hits_ally, is_spread_move,
                    move_from_showdown)
from engine import WEATHER_SETTERS

# The benchmark defender: base 100 across HP/Def/SpD, neutral nature, no item,
# no ability. Level 50, 0 EVs, so it is a fixed yardstick rather than a
# plausible Pokemon -- the point is that every row is divided by the same thing.
BENCH_BASE = 100
BENCH_LEVEL = 50

# The benchmark attacker used for the incoming half: 125 in the offensive stat,
# 100 BP. Chosen to be a real VGC threat rather than an average one -- "how many
# hits do I take" is a question about the hits you actually face.
INCOMING_STAT = 125
INCOMING_POWER = 100

# Spread moves below this are not the phenomenon being measured. Dazzling Gleam
# at 80 is; Snarl at 55 and Icy Wind at 55 are speed control and chip, and they
# would crowd the table with rows nobody is building a game plan around.
MIN_SPREAD_POWER = 70


def _stat_at_50(base, evs=0, boost=1.0):
    """Showdown's level-50 stat formula, neutral nature."""
    return int((((2 * base + 31 + evs // 4) * BENCH_LEVEL) // 100 + 5) * boost)


def _bench_hp():
    return int(((2 * BENCH_BASE + 31) * BENCH_LEVEL) // 100 + BENCH_LEVEL + 10)


def benchmark_defender():
    """A neutral 100/100/100 target, and deliberately TYPELESS.

    The first version typed it Normal, which quietly broke the table: a Normal
    benchmark is IMMUNE to nothing but is hit 0x by nothing either -- while the
    incoming probe, also Normal, did 0 damage to every Ghost-type, so thirteen
    Ghosts came out with infinite survivability and swept the top of the
    ranking. An empty type list makes `type_multiplier` return 1.0 by
    construction, in both directions, which is what "neutral" was supposed to
    mean. STAB is unaffected -- that reads the ATTACKER's types.
    """
    hp = _bench_hp()
    stat = _stat_at_50(BENCH_BASE)
    return Combatant(
        name="Benchmark", types=[], ability="", item="",
        stats={"hp": hp, "atk": stat, "def": stat, "spa": stat, "spd": stat,
               "spe": stat},
        current_hp=hp,
    )


def neutral_probe_type(defender, typechart):
    """A move type this defender takes at exactly 1.0x, for the incoming half.

    Needed because the DEFENDER here is a real Pokemon with real typing, so a
    fixed probe type cannot be neutral for everyone -- Normal is 0x into Ghost,
    and that is what produced the infinite-survivability bug. Falls back to the
    type it resists least, so a Pokemon with no neutral type at all (rare, but
    Shedinja-like typings exist) still gets a finite, honest number.
    """
    from damage import type_multiplier
    best = None
    for t in typechart:
        mult = type_multiplier(t, list(defender.types), typechart)
        if mult == 1.0:
            return t, 1.0
        if mult > 0 and (best is None or mult < best[1]):
            best = (t, mult)
    return best if best is not None else (next(iter(typechart)), 1.0)


def spread_moves(record, moves_db, min_power=MIN_SPREAD_POWER,
                 foes_only=False):
    """Every damaging spread move this Pokemon is known to run, above `min_power`.

    Returns (MoveInfo, raw_showdown_dict) pairs, because two things that decide
    whether a move belongs in this table live only in the raw entry.

    Reads the same usage list the rest of the system reads, so a Pokemon that
    does not actually carry Heat Wave does not get credit for it.

    SUICIDE MOVES ARE EXCLUDED, and this is not a detail. The first run of the
    table put Metagross Explosion at #1 and Snorlax Self-Destruct at #2, scoring
    250 and 200 BP multiplied by four to six turns of survivability -- for a
    move that faints the user on use. The score's whole premise is REPEATABLE
    output, and `selfdestruct` is the exact negation of it.

    `foes_only` drops `allAdjacent` moves, which hit your own partner. Earthquake
    and Sludge Wave are real damageslop, but they constrain the partner slot in a
    way Heat Wave and Hyper Voice do not, so it is worth being able to ask the
    question without them.
    """
    out = []
    seen = set()
    for name, _pct in record.get("moves_usage") or []:
        if name == "Other":
            continue
        key = name.lower().replace(" ", "").replace("-", "").replace("'", "")
        if key not in moves_db or key in seen:
            continue
        seen.add(key)
        raw = moves_db[key]
        if raw.get("selfdestruct"):
            continue
        move = move_from_showdown(raw)
        if move.category == "Status" or (move.power or 0) < min_power:
            continue
        if not is_spread_move(move.target):
            continue
        if foes_only and hits_ally(move.target):
            continue
        out.append((move, raw))
    return out


def _neutral_damage(attacker, defender, move, typechart, weather, targets):
    """Average damage as a FRACTION of the defender's max HP, type divided out.

    `targets` is how many Pokemon the move hits, which is what applies the 0.75x
    spread penalty inside `damage_roll`.
    """
    physical = move.category == "Physical"
    atk_key = "atk" if physical else "spa"
    def_key = "def" if physical else "spd"
    atk_stat = effective_stat(attacker.stats[atk_key], attacker.stages[atk_key])
    if attacker.item == "Choice Band" and physical:
        atk_stat *= 1.5
    if attacker.item == "Choice Specs" and not physical:
        atk_stat *= 1.5
    def_stat = defensive_stat(defender, def_key, move)
    _lo, _hi, avg, eff = damage_roll(
        BENCH_LEVEL, move.power, atk_stat, def_stat, attacker, defender, move,
        typechart, weather=weather, num_targets_hit=targets)
    if not eff:
        return 0.0
    return (avg / eff) / (defender.stats["hp"] or 1)


def self_weather(combatant):
    """The weather this Pokemon brings on its own, or None.

    Sun on Heat Wave is not a lucky matchup, it is the same Pokemon: Drought
    fires the moment it lands. Reads the MEGA ability too, since Mega Charizard
    Y's whole identity is that the Mega is the one with Drought.
    """
    for ability in (combatant.mega_ability, combatant.ability):
        if ability and ability in WEATHER_SETTERS:
            return WEATHER_SETTERS[ability]
    return None


def incoming_per_hit(combatant, typechart, weather=None):
    """Mean fraction of `combatant`'s HP a generic 100 BP hit removes.

    The average of a physical and a special hit from 125 in the matching stat,
    neutral. Averaging the two rather than taking the worse one is deliberate:
    the question is how long it lasts against a metagame, and a Pokemon with one
    good defence and one terrible one really does survive some matchups.
    """
    probe_type, probe_mult = neutral_probe_type(combatant, typechart)
    total = 0.0
    for category, key in (("Physical", "def"), ("Special", "spd")):
        move = MoveInfo(name=f"Benchmark {category}", power=INCOMING_POWER,
                        move_type=probe_type, category=category, target="normal")
        def_stat = defensive_stat(combatant, key, move)
        attacker = benchmark_defender()
        attacker.stats[("atk" if key == "def" else "spa")] = INCOMING_STAT
        _lo, _hi, avg, eff = damage_roll(
            BENCH_LEVEL, INCOMING_POWER, INCOMING_STAT, def_stat, attacker,
            combatant, move, typechart, weather=weather, num_targets_hit=1)
        eff = eff or probe_mult
        total += (avg / eff) / (combatant.max_hp() or 1)
    return total / 2.0


def rate(name, merged, natures, moves_db, typechart, item=None,
         min_power=MIN_SPREAD_POWER, foes_only=False):
    """One row of the table, or None if this Pokemon runs no real spread move.

    Returns a dict: the best spread move, its per-target and two-target neutral
    output, how many hits the attacker takes, and the product.
    """
    from combatants import make_combatant
    record = merged.get(name)
    if not record:
        return None
    attacker = make_combatant(name, merged, natures, item=item)
    moves = spread_moves(record, moves_db, min_power, foes_only)
    if not moves:
        return None
    weather = self_weather(attacker)
    defender = benchmark_defender()

    # The MEGA's offence, where there is one: a Mega Charizard Y rated on base
    # Charizard's 109 SpA is not the Pokemon anyone is talking about.
    view = attacker
    if attacker.mega_stats:
        from copy import deepcopy
        view = deepcopy(attacker)
        view.stats = dict(attacker.mega_stats)
        if attacker.mega_ability:
            view.ability = attacker.mega_ability
        if attacker.mega_types:
            view.types = list(attacker.mega_types)

    best = None
    for move, raw in moves:
        per_target = _neutral_damage(view, defender, move, typechart, weather, 2)
        if best is None or per_target > best[1]:
            best = (move, per_target, raw)
    move, per_target, raw = best
    hits = incoming_per_hit(attacker, typechart, weather)
    hits_to_ko = (1.0 / hits) if hits > 0 else float("inf")
    two_target = per_target * 2.0
    return {
        "name": name,
        "move": move.name,
        "type": move.move_type,
        "power": move.power,
        "weather": weather,
        "ability": view.ability,
        # Earthquake / Sludge Wave hit our own partner: real output, but it
        # constrains the slot next to it, which the score does not price.
        "hits_ally": hits_ally(move.target),
        # Eruption and Water Spout scale with the user's CURRENT hp, so this
        # row is a full-health number and decays as the game goes on -- the
        # opposite of the repeatability the score is meant to reward.
        "hp_scaled": bool(raw.get("basePowerCallback")),
        "per_target": per_target,
        "two_target": two_target,
        "incoming": hits,
        "hits_to_ko": hits_to_ko,
        "score": two_target * hits_to_ko,
    }


def table(names, merged, natures, moves_db, typechart,
          min_power=MIN_SPREAD_POWER, foes_only=False):
    """Every Pokemon in `names` that runs a real spread move, best score first."""
    rows = []
    for name in names:
        row = rate(name, merged, natures, moves_db, typechart,
                   min_power=min_power, foes_only=foes_only)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda r: -r["score"])
    return rows
