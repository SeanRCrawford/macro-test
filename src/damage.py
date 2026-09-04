"""
Doubles damage formula (Gen 9 mechanics), built to be extended with more
items/abilities in Claude Code later. Covers what the user flagged as
relevant now: STAB, type effectiveness, spread-move penalty, weather boosts,
stat stages, Intimidate/Defiant/Competitive, Choice item lock (stat boost
only -- move restriction is a battle-state concern, not a damage concern),
Life Orb, weather-boosting items, crit, and the 16-step random roll.
"""
from dataclasses import dataclass, field
from species_data import TYPES, is_mega_stone_name

STAGE_MULT = {
    -6: 2/8, -5: 2/7, -4: 2/6, -3: 2/5, -2: 2/4, -1: 2/3,
    0: 1.0,
    1: 3/2, 2: 4/2, 3: 5/2, 4: 6/2, 5: 7/2, 6: 8/2,
}
ACC_STAGE_MULT = {  # accuracy/evasion use a different table; not needed for damage calc
    -6: 3/9, -5: 3/8, -4: 3/7, -3: 3/6, -2: 3/5, -1: 3/4,
    0: 1.0,
    1: 4/3, 2: 5/3, 3: 6/3, 4: 7/3, 5: 8/3, 6: 9/3,
}

# Damage-relevant type-boosting held items -> boosted type
TYPE_ITEM_BOOST = {
    "Soft Sand": "Ground", "Silk Scarf": "Normal", "Charcoal": "Fire",
    "Mystic Water": "Water", "Magnet": "Electric", "Miracle Seed": "Grass",
    "Never-Melt Ice": "Ice", "Black Belt": "Fighting", "Poison Barb": "Poison",
    "Sharp Beak": "Flying", "Twisted Spoon": "Psychic", "Silver Powder": "Bug",
    "Hard Stone": "Rock", "Spell Tag": "Ghost", "Dragon Fang": "Dragon",
    "Black Glasses": "Dark", "Metal Coat": "Steel", "Pixie Plate": "Fairy",
    "Fairy Feather": "Fairy",
}


@dataclass(eq=False)   # identity equality -- see note below
class Combatant:
    """A Pokemon in battle.

    eq=False is REQUIRED, not cosmetic. With dataclass value equality, two
    distinct Pokemon of the same species and set (e.g. your Garchomp and the
    opponent's Garchomp at full HP) compare EQUAL. Everything built on `==`
    then mis-attributes them: `side_of()` uses `combatant in side.roster`,
    the switch handler uses `side.active.index(...)`, and guard/redirection
    checks use `in`. That caused mirrored species to be assigned to the wrong
    side mid-turn -- which showed up as one Garchomp appearing to act twice.
    """
    name: str
    stats: dict           # computed Lv50 stats {'hp','atk','def','spa','spd','spe'}
    types: list            # e.g. ['Fire','Flying']
    ability: str = ""
    item: str = ""
    stages: dict = field(default_factory=lambda: {"atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0})
    status: str | None = None  # 'brn', 'par', 'psn', 'slp', 'frz'
    current_hp: int = 0
    fainted: bool = False
    protecting: bool = False           # set true this turn if it used Protect successfully
    protected_last_turn: bool = False  # used to enforce "no double Protect" (see battle.run_turn)
    choice_locked_move: str | None = None  # move name locked in by a Choice item
    volatile: dict = field(default_factory=dict)  # e.g. {'flinch': True}
    revealed: bool = False              # has been on the field, so the foe has seen it
    perish_counter: int | None = None  # turns until fainting from Perish Song
    active_turn_count: int = 0  # 0 = this is the turn it was sent out (Fake Out/First Impression legal)
    is_mega_pick: bool = False
    mega_evolved: bool = False
    mega_stats: dict | None = None
    mega_ability: str | None = None
    mega_types: list | None = None
    weight_kg: float | None = None       # current form's weight -- Low Kick/Grass Knot
    mega_weight_kg: float | None = None  # swapped in on mega_evolve(), like mega_stats

    def __deepcopy__(self, memo):
        """Fast copy. Every field is a scalar, a flat dict of scalars, or a
        read-only template, so recursive deepcopy is pure overhead -- and it
        dominated search runtime (76% of a composition search) before this.
        `mega_stats` / `mega_types` are never mutated in place (mega_evolve
        installs fresh copies), so they can be shared by reference."""
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        new.name = self.name
        new.stats = dict(self.stats)
        new.types = list(self.types)
        new.ability = self.ability
        new.item = self.item
        new.stages = dict(self.stages)
        new.status = self.status
        new.current_hp = self.current_hp
        new.fainted = self.fainted
        new.protecting = self.protecting
        new.protected_last_turn = self.protected_last_turn
        new.choice_locked_move = self.choice_locked_move
        new.volatile = dict(self.volatile)
        new.active_turn_count = self.active_turn_count
        new.perish_counter = self.perish_counter
        new.revealed = self.revealed
        new.is_mega_pick = self.is_mega_pick
        new.mega_evolved = self.mega_evolved
        new.mega_stats = self.mega_stats
        new.mega_ability = self.mega_ability
        new.mega_types = self.mega_types
        new.weight_kg = self.weight_kg
        new.mega_weight_kg = self.mega_weight_kg
        return new

    def max_hp(self) -> int:
        return self.stats["hp"]

    @property
    def current_hp_frac(self) -> float:
        return self.current_hp / self.max_hp() if self.max_hp() else 0.0

    def apply_damage(self, dmg: float):
        self.current_hp = max(0, self.current_hp - int(round(dmg)))
        if self.current_hp <= 0:
            self.fainted = True


@dataclass
class MoveInfo:
    name: str
    power: int
    move_type: str
    category: str          # 'Physical' | 'Special' | 'Status'
    target: str             # showdown target field, e.g. 'normal', 'allAdjacentFoes'
    priority: int = 0
    crit_stage: int = 0     # 0 normally, +1 for high-crit moves
    secondary: dict | None = None  # raw showdown 'secondary', e.g. {'chance':100,'volatileStatus':'flinch'}
                                    # or {'chance':100,'boosts':{'spa':-1}} for Snarl
    self_effect: dict | None = None  # raw showdown 'self', e.g. {'boosts':{'spa':-2}} for Draco Meteor
    boosts: dict | None = None        # raw showdown 'boosts' for pure status stat moves (Swords Dance etc.)
    recoil: list | None = None        # e.g. [33,100] = 33% of damage dealt back (Flare Blitz)
    drain: list | None = None         # e.g. [1,2] = heal 50% of damage dealt (Drain Punch)
    volatile_status: str | None = None  # e.g. 'followme', 'ragepowder', 'protect'
    flags: dict | None = None           # showdown move flags (slicing, punch, contact, bite, ...)
    has_crash: bool = False            # High Jump Kick style crash damage on miss (not modeled)
    self_switch: bool | str | None = None  # raw showdown 'selfSwitch': True (U-turn, Volt Switch,
                                            # Parting Shot, Flip Turn, Chilly Reception), or a string
                                            # for a variant effect ('copyvolatile' = Baton Pass passes
                                            # stat stages/volatiles, 'shedtail' = Shed Tail leaves a
                                            # Substitute) -- only the switch itself is modeled here,
                                            # not those extra pass-along effects.
    ignore_defensive: bool = False      # showdown 'ignoreDefensive': Sacred Sword, Chip Away and
                                        # Darkest Lariat hit as if the target's Defence stage were 0.
                                        # ('ignoreEvasion' rides along with it in the data and is a
                                        # no-op here -- moves never miss in this simulation.)
    accuracy: bool | int = True            # raw showdown 'accuracy': True = never misses, else 1-100.
                                            # Battle resolution doesn't roll a miss chance (moves
                                            # always hit) -- this exists purely so the greedy/candidate
                                            # move-choice scoring can prefer a more reliable move when
                                            # two options are otherwise equally good.


_MOVE_INFO_CACHE: dict[int, "MoveInfo"] = {}


def move_from_showdown(m: dict) -> MoveInfo:
    """Build a MoveInfo from a raw gen9moves.json entry.

    THE one place that reads the raw dict. There were four hand-written copies
    of this, and they had already drifted (one forgot `has_crash`); adding
    `ignore_defensive` to all of them separately is how a move flag ends up
    working in the simulator but not in the solver that chooses the move.

    Cached by `id(m)`: `moves_db`'s entries are loaded once and never mutated,
    so the same raw dict always produces the same `MoveInfo`, and every caller
    already treats a `MoveInfo` as immutable (a converted move, e.g. Pixilate's
    type change in `damage.py` itself, builds a NEW instance rather than
    mutating one in place). Measured mattering: `solver.build_moveset` calls
    this fresh for every Pokemon in every position a search or sweep builds,
    uncached, which showed up as 11%+ of `lead_scan.full_report`'s own cost in
    `tools/lead_sweep.py`'s sweep stage.
    """
    cached = _MOVE_INFO_CACHE.get(id(m))
    if cached is not None:
        return cached
    info = MoveInfo(m["name"], m["basePower"], m["type"], m["category"], m["target"],
                    priority=m.get("priority", 0), secondary=m.get("secondary"),
                    self_effect=m.get("self"), boosts=m.get("boosts"),
                    recoil=m.get("recoil"), drain=m.get("drain"),
                    has_crash=bool(m.get("hasCrashDamage")),
                    volatile_status=m.get("volatileStatus"), flags=m.get("flags"),
                    self_switch=m.get("selfSwitch"),
                    ignore_defensive=bool(m.get("ignoreDefensive")),
                    accuracy=m.get("accuracy", True))
    _MOVE_INFO_CACHE[id(m)] = info
    return info


def is_spread_move(move_target: str) -> bool:
    return move_target in ("allAdjacentFoes", "allAdjacent", "all", "foeSide")


def hits_ally(move_target: str) -> bool:
    """True for moves that hit EVERY adjacent Pokemon including your partner.

    Earthquake, Surf, Discharge, Bulldoze and Explosion are `allAdjacent` --
    they hit your own ally as well. `allAdjacentFoes` moves (Rock Slide, Heat
    Wave, Dazzling Gleam, Muddy Water) hit only the opposing side.
    """
    return move_target == "allAdjacent"


def spread_targets(move_target: str, live_foes: list, allies: list, user) -> list:
    """All Pokemon a spread move actually hits, ally included where relevant."""
    targets = list(live_foes)
    if hits_ally(move_target):
        targets += [a for a in allies if a is not user and a is not None and not a.fainted]
    return targets


# Moves whose effectiveness against one type is not what their own type says.
# Freeze-Dry is the only one in this format: an Ice move that is SUPER effective
# on Water, rather than resisted by it. The multiplier is replaced, not
# multiplied, so Water/Ground takes 2x (Water) x 2x (Ground) = 4x.
TYPE_OVERRIDE_MOVES = {"Freeze-Dry": {"water": 2.0}}


_TYPE_MULT_CACHE = {}
_TYPE_MAPPING = {0: 1.0, 1: 2.0, 2: 0.5, 3: 0.0}


# Defender-side type-resist berries: {item: the move type it halves}. Inverted
# from optimize_sets.TYPE_RESIST_BERRY rather than restated, so the two cannot
# drift -- the salvage flow picks the berry from that table and this is what makes
# the pick mean something.
BERRY_RESIST_TYPE = {
    "Chilan Berry": "Normal", "Occa Berry": "Fire", "Passho Berry": "Water",
    "Wacan Berry": "Electric", "Rindo Berry": "Grass", "Yache Berry": "Ice",
    "Chople Berry": "Fighting", "Kebia Berry": "Poison", "Shuca Berry": "Ground",
    "Coba Berry": "Flying", "Payapa Berry": "Psychic", "Tanga Berry": "Bug",
    "Charti Berry": "Rock", "Kasib Berry": "Ghost", "Haban Berry": "Dragon",
    "Colbur Berry": "Dark", "Babiri Berry": "Steel", "Roseli Berry": "Fairy",
}


def type_multiplier(move_type: str, defender_types: list, typechart: dict,
                    move_name: str | None = None) -> float:
    """Type effectiveness, memoised.

    A pure function of (move type, defender types, move name) for a fixed
    typechart, and profiling a standard pairing found 3.6 MILLION calls costing
    8.2 s of self time -- there are only a few thousand distinct answers, so
    almost all of that was recomputing the same dictionary lookups. The
    typechart's identity is part of the key so a caller holding a different
    chart cannot be served another one's answers.
    """
    key = (id(typechart), move_type, tuple(defender_types), move_name)
    hit = _TYPE_MULT_CACHE.get(key)
    if hit is not None:
        return hit
    override = TYPE_OVERRIDE_MOVES.get(move_name or "", {})
    mult = 1.0
    for dtype in defender_types:
        dkey = dtype.lower()
        if dkey in override:
            mult *= override[dkey]
            continue
        mult *= _TYPE_MAPPING[typechart[dkey]["damageTaken"].get(move_type, 0)]
    _TYPE_MULT_CACHE[key] = mult
    return mult


def effective_stat(base_val: int, stage: int, boost_mult: float = 1.0) -> float:
    return base_val * STAGE_MULT[stage] * boost_mult


def defensive_stat(target: Combatant, def_key: str, move: "MoveInfo",
                    weather=None) -> float:
    """The defence a move actually hits, honouring `ignoreDefensive`.

    Sacred Sword, Chip Away and Darkest Lariat hit as though the target's
    Defence stage were 0 -- so a +2 Iron Defence does not blunt them. Showdown
    ignores the stage in BOTH directions, so an already-lowered target does not
    take extra damage from them either.

    Every damage site in the codebase goes through this, because four of them
    computed the defence independently and only one of them would otherwise
    have learned about the flag.
    """
    stage = 0 if getattr(move, "ignore_defensive", False) else target.stages[def_key]
    stat = effective_stat(target.stats[def_key], stage)
    if weather:
        stat *= weather_defence_boost(target, def_key, weather)
    return stat


# Weather boosts a defence for one type, and neither was modelled: reported as
# "Ninetales-Alola doesn't get the defence boost from its snow". Sand's Rock
# Special Defence boost had the same gap. {weather: (type, stat)}.
WEATHER_DEFENCE = {"snow": ("ice", "def"), "hail": ("ice", "def"),
                   "sand": ("rock", "spd"), "sandstorm": ("rock", "spd")}


def weather_defence_boost(target: Combatant, def_key: str, weather) -> float:
    """1.5x when the weather protects this type's relevant defence, else 1.0."""
    entry = WEATHER_DEFENCE.get((weather or "").lower())
    if not entry:
        return 1.0
    want_type, want_stat = entry
    if want_stat != def_key:
        return 1.0
    return 1.5 if any(t.lower() == want_type for t in target.types) else 1.0


STAT_DROP_IMMUNE_ABILITIES = {"Clear Body", "Full Metal Body", "White Smoke"}


def apply_boosts(target: Combatant, boosts: dict, from_foe: bool = False) -> dict:
    """Apply a {'spa': -2, 'atk': +1, ...} stat-stage change to `target`.

    from_foe: True if this drop was inflicted by the opponent (only then do
    Defiant/Competitive/Clear Body-style protections trigger -- a self-inflicted
    Draco Meteor drop is NOT blocked by Clear Body and does NOT proc Defiant).

    Contrary reverses the sign of EVERY stat change applied to its holder --
    self-inflicted (Close Combat's own -1 Def/SpD becomes +1/+1 for a
    Contrary holder, e.g. Mega Staraptor) or foe-inflicted alike -- so it's
    applied first, before the from_foe/Defiant/Competitive/Clear-Body branch
    below even looks at direction. An ability is singular, so a Contrary
    holder is never ALSO Defiant/Competitive/immune -- this ordering just
    keeps the logic correct regardless.

    Returns the dict of stages actually changed, for logging.
    """
    changed = {}
    if target.ability == "Contrary":
        boosts = {stat: -delta for stat, delta in boosts.items()}
    lowering = any(v < 0 for v in boosts.values())

    if from_foe and lowering:
        if target.ability in STAT_DROP_IMMUNE_ABILITIES:
            return changed
        if target.ability == "Defiant":
            before = target.stages["atk"]
            target.stages["atk"] = min(6, before + 2)
            return {"atk": target.stages["atk"] - before}
        if target.ability == "Competitive":
            before = target.stages["spa"]
            target.stages["spa"] = min(6, before + 2)
            return {"spa": target.stages["spa"] - before}

    for stat, delta in boosts.items():
        if stat not in target.stages:
            continue
        before = target.stages[stat]
        target.stages[stat] = max(-6, min(6, before + delta))
        if target.stages[stat] != before:
            changed[stat] = target.stages[stat] - before
    return changed


def apply_intimidate(target: Combatant):
    """Call when `target` switches in against a foe with Intimidate.

    Returns a short description of what actually happened, so a caller can log
    it truthfully. There are four outcomes and they are not interchangeable:
    announcing "Attack fell" when the target has Defiant, Competitive or an
    immunity would be worse than saying nothing.
    """
    if target.ability == "Contrary":
        before = target.stages["atk"]
        target.stages["atk"] = min(6, before + 1)
        return f"{target.name}'s Contrary raised its Attack instead!"
    if target.ability == "Defiant":
        # Defiant raises Attack SHARPLY (+2 stages) whenever a foe lowers
        # any of its stats -- matches `apply_boosts`'s own Defiant branch;
        # this one was a plain +1 (a separate bug this fix surfaced, not
        # what was reported).
        before = target.stages["atk"]
        target.stages["atk"] = min(6, before + 2)
        return f"{target.name}'s Defiant sharply raised its Attack!"
    if target.ability == "Competitive":
        before = target.stages["spa"]
        target.stages["spa"] = min(6, before + 2)
        return f"{target.name}'s Competitive sharply raised its Sp. Atk!"
    if target.ability in ("Clear Body", "Full Metal Body", "White Smoke", "Hyper Cutter",
                          "Inner Focus", "Own Tempo", "Oblivious", "Scrappy"):
        # immune to the stat drop (Hyper Cutter only blocks Atk drops specifically)
        return f"{target.name}'s {target.ability} blocked the Attack drop!"
    if target.stages["atk"] <= -6:
        return f"{target.name}'s Attack won't go any lower!"
    target.stages["atk"] = max(-6, target.stages["atk"] - 1)
    return f"{target.name}'s Attack fell!"


SLICING_BOOST_ABILITY = "Sharpness"

# Abilities that DRAW single-target moves of a type onto themselves from
# anywhere on the opposing side -- the same redirection Follow Me performs, but
# permanent and type-specific -- and take a stat boost instead of damage.
#
# The immunity below already made them take 0; what was missing is that the
# move is pulled off the ally it was aimed at in the first place. That is most
# of what the ability is for: it is not "my Electric resistance", it is "your
# single-target Electric moves do not get to choose a target".
#
# Deliberately just these two. Motor Drive, Volt Absorb, Sap Sipper and the
# Water absorbers are immunities that do NOT redirect, and they keep the
# existing behaviour.
DRAW_ABILITIES = {
    "Lightning Rod": ("Electric", "spa"),
    "Storm Drain": ("Water", "spa"),
}

# Defender abilities that grant a full immunity to a whole type.
TYPE_IMMUNITY_ABILITIES = {
    "Levitate": "Ground",
    "Flash Fire": "Fire",
    "Water Absorb": "Water", "Storm Drain": "Water", "Dry Skin": "Water",
    "Volt Absorb": "Electric", "Motor Drive": "Electric", "Lightning Rod": "Electric",
    "Sap Sipper": "Grass",
}


def ability_type_immunity(defender: "Combatant", move_type: str) -> bool:
    """True if the defender's ability makes it outright immune to this type."""
    return TYPE_IMMUNITY_ABILITIES.get(defender.ability) == move_type


def is_grounded(combatant: "Combatant") -> bool:
    """True if `combatant` is touching the ground -- Grassy/Electric/Psychic/
    Misty Terrain (and Spikes/Toxic Spikes, not modeled) only affect grounded
    Pokemon. Not a Flying-type, and not Levitate -- this engine doesn't model
    Air Balloon or Magnet Rise, so those two checks are the whole rule."""
    return "Flying" not in combatant.types and combatant.ability != "Levitate"


def grassy_glide_priority_bonus(combatant: "Combatant", move: "MoveInfo",
                                terrain: str | None) -> int:
    """+1 for Grassy Glide, used by a grounded Pokemon, while Grassy Terrain
    is up -- else 0. A separate `if` (not folded into an ability-priority
    elif chain) since this is move+terrain gated, independent of ability;
    shared by `engine.py`'s two turn-order functions and
    `counter_finder.py`'s three cheap-model speed-key closures so the rule
    is defined exactly once."""
    if terrain == "grassy" and move is not None and move.name == "Grassy Glide" \
            and is_grounded(combatant):
        return 1
    return 0


def _offensive_ability_mult(attacker: "Combatant", move: "MoveInfo", type_eff: float,
                             weather: str | None, power: int | None = None) -> float:
    """Attacker-side ability damage multipliers.

    `power`: the move's RESOLVED power (defaults to `move.power` for callers
    that don't have one), needed because `move.power` is 0 for Low Kick/Grass
    Knot -- checking `move.power <= 60` there would always be true regardless
    of the real (weight-dependent) power, wrongly Technicianing a 120-power
    Low Kick against something heavy.
    """
    mult = 1.0
    ab = attacker.ability
    power = move.power if power is None else power
    if ab == "Sheer Force" and move.secondary:
        # +30% power, and the secondary effect is suppressed (handled in battle.py).
        mult *= 1.3
    if ab == SLICING_BOOST_ABILITY and (move.flags or {}).get("slicing"):
        mult *= 1.5
    if ab == "Technician" and power <= 60:
        mult *= 1.5
    if ab == "Tinted Lens" and type_eff < 1.0:
        mult *= 2.0
    if ab in ("Iron Fist",) and (move.flags or {}).get("punch"):
        mult *= 1.2
    if ab == "Strong Jaw" and (move.flags or {}).get("bite"):
        mult *= 1.5
    if ab == "Tough Claws" and (move.flags or {}).get("contact"):
        mult *= 1.3
    if ab == "Mega Launcher" and (move.flags or {}).get("pulse"):
        mult *= 1.5
    return mult


def _defensive_ability_mult(defender: "Combatant", move: "MoveInfo", type_eff: float) -> float:
    """Defender-side ability damage multipliers (immunities handled separately)."""
    mult = 1.0
    ab = defender.ability
    if ab == "Thick Fat" and move.move_type in ("Fire", "Ice"):
        mult *= 0.5
    if ab == "Heatproof" and move.move_type == "Fire":
        mult *= 0.5
    if ab in ("Filter", "Solid Rock", "Prism Armor") and type_eff > 1.0:
        mult *= 0.75
    if ab == "Multiscale" and defender.current_hp >= defender.max_hp():
        mult *= 0.5
    if ab in ("Fur Coat",) and move.category == "Physical":
        mult *= 0.5
    if ab == "Ice Scales" and move.category == "Special":
        mult *= 0.5
    # Regulation M-C: Mega Lucario Z's own "Aura Break" is scoped -- besides
    # the real, unchanged board-wide aura-inversion effect every "Aura
    # Break" holder gets (see `aura_multiplier`/`_AURA_ABILITIES`, untouched
    # by this), THIS ONE SPECIES additionally halves damage from contact
    # moves. A per-mon override, not a redefinition of what "Aura Break"
    # means for anyone else -- e.g. Zygarde's real Aura Break stays exactly
    # as before, since `defender.name` there is never "Mega Lucario Z".
    if (ab == "Aura Break" and defender.name == "Mega Lucario Z"
            and (move.flags or {}).get("contact")):
        mult *= 0.5
    return mult


AURA_TYPES = {"Fairy Aura": "Fairy", "Dark Aura": "Dark"}


def aura_multiplier(move_type, auras):
    """Fairy Aura / Dark Aura boost moves of their type by 1.33x for EVERY
    Pokemon on the field, both sides -- they are field effects, not personal
    boosts. Aura Break inverts them to 0.75x. Mega Floette carries Fairy Aura
    (its BASE form has Flower Veil, which protects Grass allies from stat
    drops and is not a damage boost at all)."""
    if not auras:
        return 1.0
    active = {a for a in auras if AURA_TYPES.get(a) == move_type}
    if not active:
        return 1.0
    return 0.75 if "Aura Break" in auras else 5461 / 4096   # ~1.333


# Multi-hit moves: (min_hits, max_hits, planning_hits). `planning_hits` is
# what this tool uses for its single-aggregate-damage model (avg 3.17 for
# standard 2-5 hit moves is the usual competitive convention: 3/8 chance each
# of 2/3, 1/8 chance each of 4/5). Population Bomb is handled separately by
# hit_count_for() -- it only ever counts as reliable with Wide Lens.
MULTI_HIT = {
    "Bullet Seed": (2, 5, 3.17), "Icicle Spear": (2, 5, 3.17), "Rock Blast": (2, 5, 3.17),
    "Pin Missile": (2, 5, 3.17), "Bone Rush": (2, 5, 3.17), "Tail Slap": (2, 5, 3.17),
    "Scale Shot": (2, 5, 3.17), "Arm Thrust": (2, 5, 3.17), "Barrage": (2, 5, 3.17),
    "Double Hit": (2, 2, 2.0), "Dual Wingbeat": (2, 2, 2.0), "Double Kick": (2, 2, 2.0),
    "Twineedle": (2, 2, 2.0), "Gear Grind": (2, 2, 2.0), "Dragon Darts": (2, 2, 2.0),
    "Population Bomb": (1, 10, 1.0),
}

# Two-turn (charge) moves that resolve in a single turn under the right weather
# instead of spending a turn charging first (see battle.py's charge handling and
# solver.py/fast_eval.py's move-choice filtering, which both key off this).
CHARGE_WEATHER_SKIP = {"Solar Beam": "sun", "Solar Blade": "sun", "Electro Shot": "rain"}

# Moves whose base power depends on the TARGET's weight rather than being a
# fixed number. Both use the same breakpoint table (current-gen Bulbapedia).
# Showdown's raw data gives these `basePower: 0` with a `basePowerCallback` JS
# function computing the real number at battle time -- which, unhandled, made
# every Low Kick and Grass Knot in this simulator deal exactly zero damage.
WEIGHT_BASED_POWER = ("Low Kick", "Grass Knot")

# Moves whose real power depends on the USER's own current HP fraction --
# floor(150 * current_hp / max_hp), minimum 1 -- rather than a fixed number.
# Unlike Low Kick/Grass Knot, Showdown's raw data gives these `basePower: 150`
# (the full-HP ceiling) with a `basePowerCallback` computing the real,
# HP-scaled number at battle time -- which, unhandled, meant every Eruption/
# Water Spout dealt full-HP damage regardless of how much the user had
# already taken. "Eruption damage should also scale with health."
HP_BASED_POWER = ("Eruption", "Water Spout")

# Moves whose real power depends on the TARGET's current HP fraction --
# floor(100 * target_current_hp / target_max_hp), minimum 1 -- the opposite
# direction from Eruption/Water Spout (`HP_BASED_POWER`, scales off the
# USER's own HP): "Hard Press has base power equal to the enemy % health
# (100bp at 100%)". Like Low Kick/Grass Knot (`WEIGHT_BASED_POWER`),
# Showdown's raw data gives this `basePower: 0` with a `basePowerCallback`
# computing the real number at battle time -- unhandled, it dealt zero
# damage every time.
DEFENDER_HP_BASED_POWER = ("Hard Press",)

# Every move whose raw `basePower` is 0 in Showdown's data but resolves to
# something real at battle time (`WEIGHT_BASED_POWER` + `DEFENDER_HP_BASED_
# POWER` -- both families share this "0 looks like a no-op, isn't" shape).
# The union every "is this move actually worth considering" guard across
# this codebase checks against, so a THIRD such move only ever needs
# touching here, not every guard site individually.
ZERO_BASE_POWER_MOVES = WEIGHT_BASED_POWER + DEFENDER_HP_BASED_POWER

# (weight threshold in kg, power below that threshold). The last row has no
# threshold -- 200kg+ is the final bracket.
_WEIGHT_POWER_BREAKPOINTS = ((10, 20), (25, 40), (50, 60), (100, 80), (200, 100))


def weight_based_power(weight_kg: float | None) -> int | None:
    """Low Kick / Grass Knot's real power against a target this heavy, or
    None if the weight is unknown (a handful of cosmetic form variants have
    no recorded weight -- see `damage_roll`, which leaves power alone rather
    than guessing when this returns None)."""
    if weight_kg is None:
        return None
    for threshold, power in _WEIGHT_POWER_BREAKPOINTS:
        if weight_kg < threshold:
            return power
    return 120


def defender_hp_based_power(defender: "Combatant") -> int:
    """Hard Press's real power against `defender` right now -- floor(100 *
    current_hp / max_hp), minimum 1 (never truly 0, matching this whole
    "0 basePower callback" family's usual floor -- a move that could deal
    true zero damage isn't meaningfully a move at all)."""
    max_hp = defender.max_hp()
    if not max_hp:
        return 1
    return max(1, int(100 * defender.current_hp / max_hp))


def hit_count_for(move_name: str, attacker: "Combatant") -> float:
    """How many times this move hits, for this tool's aggregate-damage model
    (total damage = hits * single-hit damage). Population Bomb is a flat 10
    hits with Wide Lens (Skill Link-style guaranteed count), otherwise it's
    too unreliable to plan around and is treated as a single hit -- matching
    it being excluded from candidate movesets without Wide Lens in the first
    place (optimize_sets.candidate_moves)."""
    if move_name == "Population Bomb":
        return 10 if attacker.item == "Wide Lens" else 1
    entry = MULTI_HIT.get(move_name)
    return entry[2] if entry else 1


EARTHQUAKE_FAMILY = ("Earthquake", "Bulldoze", "Magnitude")


def damage_roll(level: int, power: int, atk_stat: float, def_stat: float,
                 attacker: Combatant, defender: Combatant, move: MoveInfo,
                 typechart: dict, weather: str | None = None, auras=None,
                 num_targets_hit: int = 1, is_crit: bool = False,
                 screens: bool = False, tera_type: str | None = None,
                 roll_index: int | None = None, terrain: str | None = None):
    """
    Returns (min_dmg, max_dmg, avg_dmg, type_eff_mult) as raw HP damage
    (16-step roll, 0.85x-1.00x), following the standard formula:
        dmg = ((2*Level/5 + 2) * Power * A/D / 50 + 2) * modifiers

    roll_index: if given (0-15), the THIRD element of the return tuple is
    that specific discrete roll (e.g. 0 = worst-case 0.85x, 15 = best-case
    1.00x, 5 = the "5/16" roll callers sometimes want to inspect a specific
    path) instead of the true average. min/max/type_eff are unaffected, so
    this is a drop-in for any caller that only reads the avg slot.

    `power` is resolved here for Low Kick/Grass Knot, from the DEFENDER's
    weight, whenever the caller passes 0 -- which is every caller except
    `battle.py`, since Showdown's raw data gives these `basePower: 0` (see
    `WEIGHT_BASED_POWER`). Only when `power == 0`: `battle.py` resolves it
    itself BEFORE this call so it can still apply Helping Hand's 1.5x
    multiplier on top -- if this unconditionally overrode `power`, that
    multiplier (already baked into whatever `battle.py` passed in) would be
    silently discarded.

    Hard Press (`DEFENDER_HP_BASED_POWER`) is the SAME `power == 0` sentinel
    shape as Low Kick/Grass Knot -- resolved here from the DEFENDER's
    CURRENT HP fraction (floor(100 * current_hp / max_hp), min 1: "Hard
    Press has base power equal to the enemy % health (100bp at 100%)"),
    with the identical `battle.py` pre-resolution exception for the same
    Helping Hand reason.

    Eruption/Water Spout (`HP_BASED_POWER`) are handled differently: their
    raw `basePower` is already a real, nonzero number (150, the full-HP
    ceiling), so there's no `power == 0` sentinel to key off -- this always
    scales whatever `power` it's given by the ATTACKER's current HP
    fraction, treating the incoming value as "the nominal, pre-HP-scaling
    number". That's safe for every caller including `battle.py` (which
    still applies Helping Hand's 1.5x to the nominal 150 before this runs)
    since multiplication commutes: 150 * 1.5 * frac == 150 * frac * 1.5
    either way, so there is no double-scaling and no caller needs its own
    special-cased pre-resolution the way Low Kick needed.
    """
    if move.name in WEIGHT_BASED_POWER and power == 0:
        got = weight_based_power(defender.weight_kg)
        if got is not None:
            power = got
    if move.name in DEFENDER_HP_BASED_POWER and power == 0:
        power = defender_hp_based_power(defender)
    if move.name in HP_BASED_POWER:
        max_hp = attacker.max_hp()
        if max_hp:
            power = max(1, int(power * attacker.current_hp / max_hp))

    if power == 0 or move.category == "Status":
        return 0, 0, 0, 1.0

    base = ((2 * level / 5 + 2) * power * atk_stat / def_stat) / 50 + 2

    modifier = 1.0

    # Spread move penalty (doubles: 0.75x if hitting 2 targets)
    if is_spread_move(move.target) and num_targets_hit > 1:
        modifier *= 0.75

    # Weather
    if weather == "rain":
        if move.move_type == "Water":
            modifier *= 1.5
        elif move.move_type == "Fire":
            modifier *= 0.5
    elif weather == "sun":
        if move.move_type == "Fire":
            modifier *= 1.5
        elif move.move_type == "Water":
            modifier *= 0.5

    # Grassy Terrain (Regulation M-C: Rillaboom's Grassy Surge): grounded
    # Grass moves get +30% power; the Earthquake family gets -50% power
    # against a grounded defender (real-game "terrain absorbs the shockwave"
    # rule). Terrain is not exclusive with weather -- both can be active.
    if terrain == "grassy":
        if move.move_type == "Grass" and is_grounded(attacker):
            modifier *= 1.3
        if move.name in EARTHQUAKE_FAMILY and is_grounded(defender):
            modifier *= 0.5

    # Crit (gen6+: flat 1.5x; stage resets handled by caller via stat selection)
    if is_crit:
        modifier *= 1.5

    # STAB
    atk_types = tera_type and [tera_type] or attacker.types
    if move.move_type in atk_types:
        modifier *= 2.0 if attacker.ability == "Adaptability" else 1.5

    # Ability-granted type immunities (Levitate vs Ground, Flash Fire vs Fire,
    # Water/Volt Absorb, Sap Sipper, Lightning Rod, Storm Drain, ...). These are
    # full immunities and short-circuit the whole calculation -- previously they
    # were ignored entirely, so e.g. Levitate users took full Earthquake damage.
    if ability_type_immunity(defender, move.move_type):
        return 0, 0, 0, 0.0

    # Pixilate / Aerilate / Refrigerate / Galvanize: Normal moves become the
    # ability's type and gain 1.2x.
    #
    # NOTE the ORDER. STAB is applied above, on the move's ORIGINAL type, which is
    # wrong for a converted move: Sylveon's Hyper Voice becomes Fairy and should
    # get Fairy STAB. Corrected below by re-applying the difference, rather than
    # moving the STAB block, because several callers depend on `move` still being
    # the original object at that point.
    ATE = {"Pixilate": "Fairy", "Aerilate": "Flying", "Refrigerate": "Ice",
           "Galvanize": "Electric"}
    # LIQUID VOICE was missing entirely, and the consequence was visible in the
    # output: Primarina's best spread move came out as Dazzling Gleam, because its
    # Hyper Voice stayed Normal-typed and so had no STAB and no Water boost. Sound
    # moves only, and NO 1.2x -- Liquid Voice is a type conversion, not an -ate.
    converted = None
    if attacker.ability in ATE and move.move_type == "Normal":
        converted = ATE[attacker.ability]
        modifier *= 1.2
    elif (attacker.ability == "Liquid Voice"
          and (move.flags or {}).get("sound")):
        converted = "Water"
    if converted:
        was_stab = move.move_type in atk_types
        now_stab = converted in atk_types
        stab = 2.0 if attacker.ability == "Adaptability" else 1.5
        if now_stab and not was_stab:
            modifier *= stab
        elif was_stab and not now_stab:
            modifier /= stab
        move = MoveInfo(**{**move.__dict__, "move_type": converted})

    # Knock Off: 1.5x if the target holds a removable item. Not a Mega Stone it needs
    # this battle (Showdown's real exemption list also covers Z-crystals/plates/primal
    # orbs, not modeled here since this dataset doesn't use them); Sticky Hold still
    # takes the 1.5x (only the removal, done in battle.py, is blocked). The item is
    # actually removed in battle.py's move resolution -- this function only computes
    # the damage multiplier, it doesn't mutate state.
    if move.name == "Knock Off" and defender.item:
        if not is_mega_stone_name(defender.item):
            modifier *= 1.5

    # Weather Ball: becomes the weather's type at 100 BP.
    if move.name == "Weather Ball" and weather:
        wtype = {"sun": "Fire", "rain": "Water", "sand": "Rock", "snow": "Ice"}.get(weather)
        if wtype:
            move = MoveInfo(**{**move.__dict__, "move_type": wtype})
            power = 100
            base = ((2 * level / 5 + 2) * power * atk_stat / def_stat) / 50 + 2

    # Sand boosts Rock-types' Sp. Def; snow boosts Ice-types' Defence, both
    # 1.5x -- applied ONCE, by `defensive_stat` (via `weather_defence_boost`)
    # baked into the `def_stat` this function was CALLED with. There used to
    # be a second copy of this exact check here, applied again as its own
    # damage `modifier` -- every caller that (correctly) pre-resolves
    # `def_stat` through `defensive_stat(..., weather=...)` before calling
    # this function, and ALSO passes its own `weather=...` through (needed
    # for the rain/sun offensive boosts and Weather Ball just above, which
    # DO belong here) was silently applying the 1.5x defensive boost TWICE:
    # "0 SpA Mega Raichu Y Focus Blast vs. Tyranitar in Sand ... guaranteed
    # OHKO" was actually landing at 82-97%, roughly 2/3 of the real number,
    # because Tyranitar's sand-boosted bulk was counted twice. Removed --
    # `defensive_stat` is the one place this boost belongs.

    modifier *= aura_multiplier(move.move_type, auras)

    # Type effectiveness
    def_types = defender.types
    # `move.name` matters here, not just its type: Freeze-Dry is an Ice move
    # that is super effective on Water (TYPE_OVERRIDE_MOVES).
    type_eff = type_multiplier(move.move_type, def_types, typechart, move.name)
    modifier *= type_eff

    # Ability damage modifiers (Sheer Force, Sharpness, Thick Fat, Filter, ...)
    modifier *= _offensive_ability_mult(attacker, move, type_eff, weather, power=power)
    modifier *= _defensive_ability_mult(defender, move, type_eff)

    # Burn halves physical damage unless Guts (Guts instead boosts Atk stat, handled upstream)
    if attacker.status == "brn" and move.category == "Physical" and attacker.ability != "Guts":
        modifier *= 0.5

    # Screens (Reflect/Light Screen halve damage from that category in singles;
    # in doubles the reduction is 2732/4096 (~0.667x) instead of 0.5x)
    if screens:
        modifier *= 2732 / 4096

    # Item: Life Orb
    if attacker.item == "Life Orb":
        modifier *= 1.3
    # Item: type-boosting item (Soft Sand etc.)
    boosted_type = TYPE_ITEM_BOOST.get(attacker.item)
    if boosted_type and boosted_type == move.move_type:
        modifier *= 1.2
    # Item: Expert Belt (super-effective boost)
    if attacker.item == "Expert Belt" and type_eff > 1.0:
        modifier *= 1.2

    # DEFENDER'S item. Type-resist berries halve a SUPER-EFFECTIVE hit of their
    # type -- Chilan Berry is the exception and halves any Normal hit. The berry
    # TABLE has existed in optimize_sets since the salvage flow was written, and
    # the salvage flow has been handing them out, but the halving was never
    # implemented here: measured, a Roseli Berry changed Light of Ruin's damage by
    # exactly nothing. So every "give it a resist berry" suggestion the system has
    # ever made was cosmetic.
    berry_type = BERRY_RESIST_TYPE.get(defender.item)
    if berry_type and berry_type == move.move_type and (
            type_eff > 1.0 or berry_type == "Normal"):
        modifier *= 0.5

    # Assault Vest: 1.5x SpD, so 1/1.5 damage from special moves. Same story --
    # it was in item lists and did nothing.
    if defender.item == "Assault Vest" and move.category == "Special":
        modifier /= 1.5

    rolls = [base * modifier * (0.85 + 0.01 * i) for i in range(16)]
    avg_or_indexed = rolls[roll_index] if roll_index is not None else sum(rolls) / len(rolls)
    return min(rolls), max(rolls), avg_or_indexed, type_eff


if __name__ == "__main__":
    from species_data import build_merged_dataset
    from stats import compute_stats

    merged, _, moves, natures, typechart = build_merged_dataset()

    def make_combatant(name, ability=None, item=None):
        p = merged[name]
        nat = natures[p["nature"].lower()]
        stats = compute_stats(p["base_stats"], nat, p["evs"])
        ab = ability or (p["abilities_usage"][0][0] if p["abilities_usage"] else "")
        it = item or (p["items_usage"][0][0] if p["items_usage"] else "")
        return Combatant(name=name, stats=stats, types=p["types"], ability=ab, item=it)

    incineroar = make_combatant("Incineroar")
    farigiraf = make_combatant("Farigiraf")

    flare_blitz = moves["flareblitz"]
    move = MoveInfo(name="Flare Blitz", power=flare_blitz["basePower"], move_type=flare_blitz["type"],
                     category=flare_blitz["category"], target=flare_blitz["target"])

    atk_stat = effective_stat(incineroar.stats["atk"], incineroar.stages["atk"])
    def_stat = effective_stat(farigiraf.stats["def"], farigiraf.stages["def"])

    mn, mx, avg, eff = damage_roll(50, move.power, atk_stat, def_stat, incineroar, farigiraf,
                                    move, typechart)
    print(f"Incineroar Flare Blitz -> Farigiraf: {mn:.1f}-{mx:.1f} dmg "
          f"(Farigiraf HP {farigiraf.stats['hp']}), type eff {eff}x")
    print(f"  As % HP: {100*mn/farigiraf.stats['hp']:.1f}% - {100*mx/farigiraf.stats['hp']:.1f}%")
