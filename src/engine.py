"""
Lightweight custom doubles battle engine for Champions M-B.

Not a full reimplementation of Showdown -- scoped deliberately to the
mechanics that matter for VGC doubles turn resolution:
  - Speed ordering: priority brackets, Trick Room inversion, Tailwind,
    paralysis, Choice Scarf, weather speed-boost abilities
  - Switch-in triggers: Intimidate (+ Defiant/Competitive/immunities),
    weather-setting abilities
  - Move resolution: damage (via damage.py), Protect-style moves,
    spread-move multi-target, priority moves (Fake Out etc.)
  - Field state: weather, Trick Room, Tailwind, screens

This is intentionally NOT a full simulator (no full status effect suite,
no every-ability coverage) -- it's built to cover exactly what's needed for
this format, and is meant to be extended incrementally.
"""
from dataclasses import dataclass, field
from damage import (Combatant, MoveInfo, is_spread_move, damage_roll, apply_intimidate,
                    effective_stat, defensive_stat)

# Display names, so the log reads like the game rather than like the code.
WEATHER_NAMES = {"rain": "a rainstorm", "sun": "harsh sunlight",
                 "sand": "a sandstorm", "snow": "snow"}

WEATHER_SETTERS = {
    "Drizzle": "rain", "Drought": "sun", "Sand Stream": "sand", "Snow Warning": "snow",
}
WEATHER_SPEED_BOOST = {
    # ability -> (weather required, multiplier)
    "Swift Swim": ("rain", 2.0),
    "Chlorophyll": ("sun", 2.0),
    "Sand Rush": ("sand", 2.0),
    "Slush Rush": ("snow", 2.0),
}


@dataclass
class FieldState:
    weather: str | None = None
    weather_turns_left: int = 0
    trick_room: bool = False
    trick_room_turns_left: int = 0
    tailwind_p1: int = 0   # turns remaining
    tailwind_p2: int = 0
    screens_p1: dict = field(default_factory=lambda: {"reflect": 0, "lightscreen": 0})
    screens_p2: dict = field(default_factory=lambda: {"reflect": 0, "lightscreen": 0})

    def __deepcopy__(self, memo):
        """Every field is a scalar or a flat dict of ints, so the reflective
        path `copy` takes by default is pure overhead.

        Profiling a standard pairing put `copy.deepcopy` at 24% cumulative --
        11.4 M calls -- and Battle and Combatant already had hand-written
        copies. FieldState and Side did not, so they were still being walked
        attribute by attribute through `_reconstruct` and `_deepcopy_dict`.
        """
        new = FieldState.__new__(FieldState)
        memo[id(self)] = new
        new.weather = self.weather
        new.weather_turns_left = self.weather_turns_left
        new.trick_room = self.trick_room
        new.trick_room_turns_left = self.trick_room_turns_left
        new.tailwind_p1 = self.tailwind_p1
        new.tailwind_p2 = self.tailwind_p2
        new.screens_p1 = dict(self.screens_p1)
        new.screens_p2 = dict(self.screens_p2)
        return new


@dataclass
class Action:
    combatant: Combatant
    side: str              # 'p1' or 'p2'
    kind: str               # 'move' | 'switch' | 'protect'
    move: MoveInfo | None = None
    targets: list = field(default_factory=list)  # list of Combatant


def mega_evolve(combatant: Combatant):
    """A Mega pick starts battle in its BASE form (Showdown/official mechanics:
    Mega Evolution happens the moment it's sent out and given an action, not
    before) -- transform it now. This matters a lot for weather: Charizard Y's
    Drought, Abomasnow's Snow Warning etc. only exist on the MEGA form, so a
    mega-based weather-setter benched for a few turns provides NO weather
    until it actually comes in, unlike a base-ability setter (Tyranitar's
    Sand Stream, Pelipper's Drizzle) which works from the instant it switches
    in regardless of any mega slot."""
    if not combatant.is_mega_pick or combatant.mega_evolved:
        return
    # Install copies, not the shared templates -- Combatant.__deepcopy__ shares
    # mega_stats/mega_types by reference on the assumption they're never mutated.
    combatant.stats = dict(combatant.mega_stats)
    combatant.ability = combatant.mega_ability
    combatant.types = list(combatant.mega_types)
    combatant.mega_evolved = True


def on_switch_in(entering: Combatant, opposing_active: list[Combatant], field_state: FieldState,
                  ally: Combatant | None = None, log=None):
    """Trigger switch-in abilities (Intimidate, weather setters, Hospitality)
    for a Pokemon entering the field.

    NOTE: Mega Evolution is deliberately NOT done here. It is resolved by
    Battle._resolve_mega_evolutions() after ALL switches on both sides have
    completed, so that a Mega's post-transform weather ability lands after
    (and therefore overwrites) any base-ability weather setter that switched
    in on the same turn.
    """
    entering.revealed = True   # seen by the opponent from now on
    entering.active_turn_count = 0
    entering.protected_last_turn = False  # switching out resets the Protect streak
    entering.perish_counter = None       # switching out clears the Perish count --
                                          # which is exactly why Shadow Tag is the other
                                          # half of a Perish Trap: it denies the escape
    if entering.ability == "Intimidate":
        # A FAINTED Pokemon cannot be intimidated. It can still be sitting in
        # its slot when this runs -- during a mid-turn pivot, or in the gap
        # before end-of-turn replacements -- and dropping its Attack is an
        # effect on nothing. Worse, it used to hide the real target: in a double
        # KO the drop landed on the corpse and the replacement walked in
        # unintimidated. See Battle._replace_fainted.
        live_foes = [f for f in opposing_active if f is not None and not f.fainted]
        if log is not None and live_foes:
            log.add(f"{entering.name}'s Intimidate!")
        for foe in live_foes:
            said = apply_intimidate(foe)
            # apply_intimidate reports which of its four outcomes fired -- a
            # Defiant boost and an Attack drop are not the same event and must
            # not be logged as one.
            if said and log is not None:
                log.add(f"  {said}")
    if entering.ability == "Hospitality" and ally is not None and not ally.fainted:
        heal = int(round(entering.max_hp() * 0.25))
        before = ally.current_hp
        ally.current_hp = min(ally.max_hp(), ally.current_hp + heal)
        healed = ally.current_hp - before
        if healed and log is not None:
            log.add(f"{entering.name}'s Hospitality restored {healed} HP to {ally.name}!")
    if entering.ability in WEATHER_SETTERS:
        new_weather = WEATHER_SETTERS[entering.ability]
        # A same-weather setter switching in does NOT refresh the duration --
        # only a genuinely new weather (or restarting from none) resets the
        # 5-turn count. Otherwise weather could be kept up indefinitely by
        # rotating same-weather setters in and out.
        if field_state.weather != new_weather:
            field_state.weather = new_weather
            field_state.weather_turns_left = 5  # 8 with the matching weather rock/seed item, simplified here
            # Every other switch-in ability here announces itself; this one did
            # not, so a Tyranitar lead set sand SILENTLY and the battle log gave
            # no reason for the Rock Sp.Def boost or the Weather Ball that
            # followed. Reported as "Tyranitar does not set sand" -- it did, it
            # just never said so.
            if log is not None:
                log.add(f"{entering.name}'s {entering.ability} whipped up "
                        f"{WEATHER_NAMES.get(new_weather, new_weather)}!")


def effective_speed(c: Combatant, field_state: FieldState, side: str) -> float:
    spe = effective_stat(c.stats["spe"], c.stages["spe"])

    if c.ability in WEATHER_SPEED_BOOST:
        req_weather, mult = WEATHER_SPEED_BOOST[c.ability]
        if field_state.weather == req_weather:
            spe *= mult

    if c.item == "Choice Scarf":
        spe *= 1.5

    tailwind_turns = field_state.tailwind_p1 if side == "p1" else field_state.tailwind_p2
    if tailwind_turns > 0:
        spe *= 2.0

    if c.status == "par" and c.ability != "Quick Feet":
        spe *= 0.5

    return spe


def turn_order(actions: list[Action], field_state: FieldState) -> list[Action]:
    """
    Sort actions for a turn: switches first (fastest to slowest among
    switches), then moves by priority bracket, then speed within a bracket
    (inverted if Trick Room is active). Ties broken arbitrarily (real games
    use a random roll; we keep original order for determinism, but flag it).
    """
    switches = [a for a in actions if a.kind == "switch"]
    others = [a for a in actions if a.kind != "switch"]

    def spd_key(a: Action):
        spd = effective_speed(a.combatant, field_state, a.side)
        return -spd if not field_state.trick_room else spd

    switches.sort(key=spd_key)

    def move_priority(a: Action) -> int:
        if a.kind == "protect":
            return 4  # Protect/Detect priority bracket
        if not a.move:
            return 0
        pri = a.move.priority
        # Ability priority modifiers. Prankster gives status moves +1 (so a
        # Prankster Tailwind/Trick Room resolves before attacks); Gale Wings
        # gives Flying moves +1, but only at full HP.
        ab = a.combatant.ability
        if ab == "Prankster" and a.move.category == "Status":
            pri += 1
        elif ab == "Gale Wings" and a.move.move_type == "Flying" \
                and a.combatant.current_hp >= a.combatant.max_hp():
            pri += 1
        elif ab == "Triage" and a.move.drain:
            pri += 3
        return pri

    others.sort(key=lambda a: (-move_priority(a), spd_key(a)))

    return switches + others


def find_speed_ties(actions: list, field_state: FieldState):
    """Return [(a, b), ...] pairs of opposing actions in the same priority
    bracket with EXACTLY equal effective Speed.

    Real games resolve these with a coin flip, so any line that depends on
    winning one is not guaranteed. `turn_order` breaks ties deterministically
    by list order, which silently presents a 50/50 as a certainty -- callers
    use this to detect and report that risk (see matchup_search.evaluate_risk).
    """
    def pri(a):
        if a.kind == "protect":
            return 4
        if not a.move:
            return 0
        p = a.move.priority
        ab = a.combatant.ability
        if ab == "Prankster" and a.move.category == "Status":
            p += 1
        elif ab == "Gale Wings" and a.move.move_type == "Flying" \
                and a.combatant.current_hp >= a.combatant.max_hp():
            p += 1
        elif ab == "Triage" and a.move.drain:
            p += 3
        return p

    ties = []
    acts = [a for a in actions if a.kind != "switch"]
    for i, a in enumerate(acts):
        for b in acts[i + 1:]:
            if a.side == b.side:
                continue
            if pri(a) != pri(b):
                continue
            if abs(effective_speed(a.combatant, field_state, a.side)
                   - effective_speed(b.combatant, field_state, b.side)) < 1e-9:
                ties.append((a, b))
    return ties


def resolve_damage_action(action: Action, field_state: FieldState, typechart: dict,
                           roll: str = "avg") -> dict:
    """
    roll: 'min' | 'max' | 'avg' -- which of the 16-step damage roll to apply.
    Returns {target: damage_dealt} for reporting/HP tracking.
    """
    if action.kind != "move" or action.move.category == "Status":
        return {}

    attacker = action.combatant
    results = {}
    live_targets = [t for t in action.targets if t.current_hp_frac > 0]
    num_hit = len(live_targets) if is_spread_move(action.move.target) else 1

    for target in live_targets:
        atk_key = "atk" if action.move.category == "Physical" else "spa"
        def_key = "def" if action.move.category == "Physical" else "spd"

        atk_stat = effective_stat(attacker.stats[atk_key], attacker.stages[atk_key])
        if attacker.status == "brn" and attacker.ability == "Guts" and atk_key == "atk":
            atk_stat *= 1.5
        if attacker.item == "Choice Band" and atk_key == "atk":
            atk_stat *= 1.5
        if attacker.item == "Choice Specs" and atk_key == "spa":
            atk_stat *= 1.5

        def_stat = defensive_stat(target, def_key, action.move,
                                  weather=field.weather)

        # NOTE: this standalone demo function doesn't track which side is which
        # (no Battle/Side objects here), so screens are not modeled -- the real,
        # live screens implementation is battle.Battle (Side.screens_reflect/
        # screens_lightscreen/screens_auroraveil + Battle._set_screen).
        mn, mx, avg, eff = damage_roll(
            50, action.move.power, atk_stat, def_stat, attacker, target, action.move,
            typechart, weather=field_state.weather, num_targets_hit=num_hit,
        )
        dmg = {"min": mn, "max": mx, "avg": avg}[roll]
        results[target.name] = {"damage": dmg, "type_eff": eff}

    return results


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

    # Scripted mini-scenario: Incineroar + Farigiraf (Trick Room) vs Garchomp + Kingambit
    incineroar = make_combatant("Incineroar")
    farigiraf = make_combatant("Farigiraf")
    garchomp = make_combatant("Garchomp")
    kingambit = make_combatant("Kingambit")

    fs = FieldState()
    on_switch_in(incineroar, [garchomp, kingambit], fs)  # Intimidate hits both foes
    print("After Incineroar switches in (Intimidate):")
    print(f"  Garchomp Atk stage: {garchomp.stages['atk']}  Kingambit Atk stage: {kingambit.stages['atk']}"
          f" (Kingambit ability={kingambit.ability})")

    fakeout = moves["fakeout"]
    fo_move = MoveInfo("Fake Out", fakeout["basePower"], fakeout["type"], fakeout["category"],
                        fakeout["target"], priority=fakeout["priority"])
    eq = moves["earthquake"]
    eq_move = MoveInfo("Earthquake", eq["basePower"], eq["type"], eq["category"], eq["target"],
                        priority=eq["priority"])

    actions = [
        Action(incineroar, "p1", "move", fo_move, [garchomp]),
        Action(garchomp, "p2", "move", eq_move, [incineroar, farigiraf]),
    ]
    ordered = turn_order(actions, fs)
    print("\nTurn order (no Trick Room):", [a.combatant.name for a in ordered])

    fs.trick_room = True
    ordered_tr = turn_order(actions, fs)
    print("Turn order (Trick Room active):", [a.combatant.name for a in ordered_tr])

    dmg = resolve_damage_action(actions[1], fs, typechart, roll="avg")
    print("\nGarchomp Earthquake (spread) into Incineroar+Farigiraf:", dmg)
