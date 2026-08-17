"""
Full multi-turn doubles battle loop for Champions M-B.

Scope (deliberately not full Showdown parity -- see README notes at bottom):
  - 2 active slots per side, bench up to 2 more (bring-4 doubles)
  - Move resolution in speed/priority order, spread-move multi-target
  - Protect / Wide Guard / Quick Guard blocking
  - Choice item move-lock enforcement
  - Fainting -> forced replacement next available action
  - End-of-turn: burn/poison damage, weather/Tailwind/Trick Room/screen countdowns
  - Deterministic by default (no miss/crit/full-para RNG) so lines are
    reproducible for planning; an optional rng seed can be supplied to
    sample variance instead.

NOT modeled yet (flagged for later, not needed for the format's core lines):
  sleep/freeze/confusion turn mechanics, abilities beyond the set already
  wired in engine.py/damage.py, secondary effect triggers (e.g. Rock Slide
  flinch chance) beyond a togglable flag.

Follow Me/Rage Powder redirection and Wide Guard/Quick Guard ARE modeled --
see Side.follow_me_target / _resolve_move's redirection block, and
Side.wide_guard/quick_guard / _blocked_by_guard.
"""
import copy
from dataclasses import dataclass, field
import random

from damage import (Combatant, DRAW_ABILITIES, MoveInfo, is_spread_move, damage_roll, apply_intimidate,
                    defensive_stat, move_from_showdown,
                     apply_boosts, effective_stat, hit_count_for, CHARGE_WEATHER_SKIP)
from engine import (FieldState, Action, on_switch_in, turn_order, effective_speed,
                     WEATHER_SETTERS)

CHOICE_ITEMS = {"Choice Band", "Choice Specs", "Choice Scarf"}
PROTECT_MOVES = {"Protect", "Detect", "Spiky Shield", "Baneful Bunker", "Silk Trap"}


@dataclass
class Side:
    name: str                    # 'p1' | 'p2'
    roster: list                 # up to 4 Combatant brought to this game
    active: list                 # length-2 list, Combatant or None (fainted/not yet sent out)
    bench: list = field(default_factory=list)  # Combatants not currently active but alive
    wide_guard: bool = False
    quick_guard: bool = False
    mega_used: bool = False        # at most one Mega Evolution per side, per battle
    follow_me_target: object = None  # Combatant drawing single-target moves this turn
    screens_reflect: int = 0         # turns remaining
    screens_lightscreen: int = 0     # turns remaining
    screens_auroraveil: int = 0      # turns remaining -- blocks BOTH categories, snow-only

    def __deepcopy__(self, memo):
        """Hand-written for the same reason Battle and Combatant are: the
        reflective path costs more than the copy.

        The subtlety is IDENTITY. `active` and `bench` hold the very same
        Combatant objects as `roster`, and `follow_me_target` points at one of
        them; the copy has to preserve that or a Pokemon can be damaged in one
        list and healthy in another. Everything therefore goes through the
        shared `memo`, which is what makes `copy.deepcopy(c, memo)` hand back
        the SAME copy for the same original rather than a second one.
        """
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new
        new.name = self.name
        new.roster = [copy.deepcopy(c, memo) for c in self.roster]
        # Not re-copied: looked up in the memo, so they stay the same objects.
        new.active = [None if c is None else copy.deepcopy(c, memo)
                      for c in self.active]
        new.bench = [copy.deepcopy(c, memo) for c in self.bench]
        new.wide_guard = self.wide_guard
        new.quick_guard = self.quick_guard
        new.mega_used = self.mega_used
        new.follow_me_target = (None if self.follow_me_target is None
                                else copy.deepcopy(self.follow_me_target, memo))
        new.screens_reflect = self.screens_reflect
        new.screens_lightscreen = self.screens_lightscreen
        new.screens_auroraveil = self.screens_auroraveil
        return new

    def alive_roster(self):
        return [c for c in self.roster if not c.fainted]

    def has_lost(self):
        return len(self.alive_roster()) == 0


@dataclass
class BattleLog:
    lines: list = field(default_factory=list)

    def add(self, msg: str):
        self.lines.append(msg)

    def dump(self):
        return "\n".join(self.lines)


class Battle:
    def __init__(self, p1_roster, p2_roster, typechart, moves_db, rng_seed: int | None = None,
                  tie_bias: str | None = None):
        """p1_roster / p2_roster: list of up to 4 Combatant, already lead-ordered
        (roster[0], roster[1] are the opening leads)."""
        self.typechart = typechart
        self.moves_db = moves_db
        self.field = FieldState()
        self.log = BattleLog()
        self.events = []  # structured per-turn events, for exports (Excel/HTML)
        self.speed_ties = []  # (turn, name_a, name_b) 50/50s encountered
        self._departed_slots = {}
        self.turn_num = 0
        self.rng = random.Random(rng_seed) if rng_seed is not None else None
        # 'p1' | 'p2' | None -- which side wins EXACT speed ties. Real games flip a
        # coin; forcing it lets a line be checked against losing the flip.
        self.tie_bias = tie_bias

        self.p1 = Side("p1", p1_roster, [p1_roster[0], p1_roster[1]], p1_roster[2:])
        self.p2 = Side("p2", p2_roster, [p2_roster[0], p2_roster[1]], p2_roster[2:])

        # Per-Pokemon usage stats for this battle: how often each move was clicked
        # (selected for the turn, regardless of whether it then whiffed/was
        # blocked/flinched away), total damage dealt as %-of-target-max-HP summed
        # across every hit landed, and KOs directly secured. Keyed by (side, name)
        # so a mirrored species on both sides never merges its stats. See
        # _track_move_use (usage) and _resolve_move (damage/KOs).
        self.stats = {(side, c.name): {"moves": {}, "damage_pct": 0.0, "kos": 0}
                       for side, roster in (("p1", p1_roster), ("p2", p2_roster))
                       for c in roster}

        for c in p1_roster + p2_roster:
            c.current_hp = c.max_hp()

        # Opening switch-in triggers (Intimidate, weather setters, Hospitality),
        # leads only. The ARRIVAL is announced first: an ability announcing
        # itself above the line that says who is on the field reads as coming
        # from nowhere, and it is not how the game presents it either.
        self.log.add(f"Leads: {self.p1.active[0].name}/{self.p1.active[1].name} "
                      f"vs {self.p2.active[0].name}/{self.p2.active[1].name}")
        on_switch_in(self.p1.active[0], self.p2.active, self.field, ally=self.p1.active[1], log=self.log)
        on_switch_in(self.p1.active[1], self.p2.active, self.field, ally=self.p1.active[0], log=self.log)
        on_switch_in(self.p2.active[0], self.p1.active, self.field, ally=self.p2.active[1], log=self.log)
        on_switch_in(self.p2.active[1], self.p1.active, self.field, ally=self.p2.active[0], log=self.log)
        self._emit(event="leads", p1=[c.name for c in self.p1.active],
                   p2=[c.name for c in self.p2.active], weather_after=self.field.weather)

    def make_move(self, move_key: str) -> MoveInfo:
        return move_from_showdown(self.moves_db[move_key])

    def _roll(self, min_v, max_v, avg_v):
        # force_roll lets a caller demand worst-case ('min') or best-case ('max')
        # damage instead of the average, so a plan can be checked for being
        # GUARANTEED rather than merely winning on an average roll.
        #
        # force_roll_index (set separately, see self.force_roll_index and its use
        # in _resolve_move's damage_roll call) demands one SPECIFIC discrete roll
        # (0-15) instead -- e.g. index 5 for "the 5/16 roll" -- for inspecting a
        # named path rather than just min/max/avg. avg_v already IS that discrete
        # value when force_roll_index was set, so no extra branch is needed here;
        # just don't set force_roll at the same time (force_roll takes priority).
        forced = getattr(self, "force_roll", None)
        if forced == "min":
            return min_v
        if forced == "max":
            return max_v
        if self.rng is None:
            return avg_v
        return self.rng.uniform(min_v, max_v)

    def _enforce_choice_lock(self, action: Action):
        c = action.combatant
        if c.item in CHOICE_ITEMS and action.kind == "move" and action.move.name != "Protect":
            if c.choice_locked_move and c.choice_locked_move != action.move.name:
                raise ValueError(f"{c.name} is Choice-locked into {c.choice_locked_move}, "
                                  f"cannot use {action.move.name}")
            c.choice_locked_move = action.move.name

    def side_of(self, combatant: Combatant) -> Side:
        return self.p1 if combatant in self.p1.roster else self.p2

    def __deepcopy__(self, memo):
        """Copy only the MUTABLE battle state.

        The solver deep-copies a Battle for every candidate action it explores,
        and a naive deepcopy also copied `moves_db` (954 moves) and `typechart`
        on every branch -- profiling showed 98.7% of solver runtime was spent
        in copy.deepcopy, with under 2% in the actual battle logic. Those two
        tables are read-only, so they're shared by reference instead.

        p1 and p2 are copied under a SHARED memo so cross-references between
        sides (e.g. Side.follow_me_target pointing at a Combatant) stay
        internally consistent in the copy.
        """
        cls = self.__class__
        new = cls.__new__(cls)
        memo[id(self)] = new

        # Shared, read-only -- never mutated during a battle.
        new.typechart = self.typechart
        new.moves_db = self.moves_db
        new.rng = self.rng
        # Optional: per-species move lists, attached by callers that want the
        # threat matrix / answer-preservation evaluation term. Must be carried
        # across the copy or the term silently evaluates to zero in exactly the
        # simulated states the search cares about -- a failure that looks like
        # "the term does nothing" rather than like a bug.
        new.movesets = getattr(self, "movesets", None)
        new.wide_movesets = getattr(self, "wide_movesets", None)

        # Mutable state.
        new.field = copy.deepcopy(self.field, memo)
        new.p1 = copy.deepcopy(self.p1, memo)
        new.p2 = copy.deepcopy(self.p2, memo)
        new.turn_num = self.turn_num

        # Log/event entries are append-only and never mutated after creation,
        # so a shallow list copy is both safe and much cheaper.
        new.log = BattleLog(list(self.log.lines))
        new.events = list(self.events)
        new.speed_ties = list(self.speed_ties)
        new.stats = {k: {"moves": dict(v["moves"]), "damage_pct": v["damage_pct"], "kos": v["kos"]}
                     for k, v in self.stats.items()}
        new.force_roll = getattr(self, 'force_roll', None)
        # force_roll_index pins damage to one of the 16 discrete rolls. It must
        # survive the copy for the same reason force_roll does: the solver
        # evaluates every candidate on a COPY, so dropping it here would silently
        # revert those branches to the average roll -- which looks like "roll
        # scenarios make no difference" rather than like a bug.
        new.force_roll_index = getattr(self, 'force_roll_index', None)
        new._departed_slots = {}
        new.tie_bias = self.tie_bias
        return new

    def tag(self, c):
        """Display name for logs. When BOTH sides field the same species (a
        Garchomp mirror), bare names make a log impossible to read -- and hid a
        real bug where one side's Pokemon appeared to act twice. Disambiguate
        with the side only when the species actually appears on both sides."""
        if c is None:
            return "?"
        mine = self.side_of(c)
        other = self.p2 if mine is self.p1 else self.p1
        base = ""
        if c.is_mega_pick and not c.mega_evolved:
            # It is on the field in BASE form -- different stats, typing and ability
            # from the Mega. Critical to show when a team brings two Mega stones,
            # since only one of them will ever transform.
            base = " [base form]"
        if any(x.name == c.name for x in other.roster):
            return f"{c.name}({mine.name}){base}"
        return f"{c.name}{base}"

    HP_BERRIES = {"Sitrus Berry": 0.25, "Oran Berry": 10, "Aguav Berry": 0.33,
                   "Figy Berry": 0.33, "Iapapa Berry": 0.33, "Mago Berry": 0.33,
                   "Wiki Berry": 0.33}

    def _check_berry(self, c):
        """Consume a pinch-healing berry once HP drops to half or below.

        Sitrus Berry restores 25% max HP at <=50% and is by far the most common
        item in this metagame -- it was never being consumed at all, so every
        Sitrus holder was effectively itemless and undervalued in every search.
        """
        if c is None or c.fainted or c.item not in self.HP_BERRIES:
            return
        if c.current_hp > c.max_hp() // 2:
            return
        amt = self.HP_BERRIES[c.item]
        heal = int(amt) if amt >= 1 else max(1, int(c.max_hp() * amt))
        before = c.current_hp
        c.current_hp = min(c.max_hp(), c.current_hp + heal)
        c.item = ""  # consumed
        self.log.add(f"{self.tag(c)} ate its berry and restored {c.current_hp - before} HP!")
        self._emit(event="berry", side=self.side_of(c).name, actor=c.name,
                   damage=-(c.current_hp - before), target_hp_after=c.current_hp,
                   target_max_hp=c.max_hp())

    def _emit(self, **kwargs):
        self.events.append({"turn": self.turn_num, **kwargs})

    def _mega_evolve_now(self, c):
        """Mega Evolve a single Pokemon at the moment it is about to move.

        Mega Evolution is declared with the move, so a Pokemon that is KO'd by a
        faster attack before acting never transforms -- and its weather ability
        (Drought, Snow Warning) never comes up. Still at most one per side per
        battle.
        """
        from engine import mega_evolve
        if c is None or c.fainted or not c.is_mega_pick or c.mega_evolved:
            return
        side = self.side_of(c)
        if side.mega_used:
            return
        before = c.ability
        mega_evolve(c)
        side.mega_used = True
        if c.ability in WEATHER_SETTERS:
            new_weather = WEATHER_SETTERS[c.ability]
            # Same-weather setters don't refresh the duration -- only a genuinely
            # new weather resets the 5-turn count (see engine.on_switch_in).
            if self.field.weather != new_weather:
                self.field.weather = new_weather
                self.field.weather_turns_left = 5
        if c.ability == "Intimidate" and before != "Intimidate":
            opp = self.p2 if side is self.p1 else self.p1
            for foe in opp.active:
                if foe and not foe.fainted:
                    apply_intimidate(foe)
        self.log.add(f"{self.tag(c)} Mega Evolved! (ability: {c.ability})"
                      + (f" Weather -> {self.field.weather}." if c.ability in WEATHER_SETTERS else ""))
        self._emit(event="mega_evolve", side=side.name, actor=c.name,
                   ability_after=c.ability, weather_after=self.field.weather)

    def _resolve_mega_evolutions(self, eligible=None):
        """Mega-evolve any eligible active Pokemon, at most ONE per side per
        battle. Runs after all switches (so switch-in weather is already up)
        and before any move, so a Mega's weather ability overwrites it."""
        from engine import mega_evolve
        for side in (self.p1, self.p2):
            if side.mega_used:
                continue
            for c in side.active:
                if c is None or c.fainted or not c.is_mega_pick or c.mega_evolved:
                    continue
                # A Pokemon Mega Evolves only when it is about to MOVE. A Pokemon that
                # just switched in this turn does not act, so it stays in base form
                # until the following turn -- which matters a lot, because its base
                # stats, typing and ability (e.g. Gyarados's Intimidate, not Mold
                # Breaker) are what is on the field in the meantime.
                if eligible is not None and id(c) not in eligible:
                    continue
                before_ability = c.ability
                mega_evolve(c)
                side.mega_used = True
                # A Mega's ability activates on transform -- weather setters included.
                # Same-weather setters don't refresh the duration (see engine.on_switch_in).
                if c.ability in WEATHER_SETTERS:
                    new_weather = WEATHER_SETTERS[c.ability]
                    if self.field.weather != new_weather:
                        self.field.weather = new_weather
                        self.field.weather_turns_left = 5
                if c.ability == "Intimidate" and before_ability != "Intimidate":
                    opp = self.p2 if side is self.p1 else self.p1
                    for foe in opp.active:
                        if foe and not foe.fainted:
                            apply_intimidate(foe)
                self.log.add(f"{self.tag(c)} Mega Evolved! (ability: {c.ability})"
                              + (f" Weather -> {self.field.weather}." if c.ability in WEATHER_SETTERS else ""))
                self._emit(event="mega_evolve", side=side.name, actor=c.name,
                           ability_after=c.ability, weather_after=self.field.weather)
                break  # only one Mega per side, ever

    def run_turn(self, p1_actions: list, p2_actions: list):
        """
        p1_actions / p2_actions: list of Action for each currently-active,
        non-fainted Combatant on that side (switches included). Caller
        (policy/user) is responsible for choosing legal actions; Choice
        lock is enforced here and will raise if violated.
        """
        self.turn_num += 1
        self.log.add(f"\n--- Turn {self.turn_num} ---")

        for c in self.p1.roster + self.p2.roster:
            c.protecting = False
            c.volatile.pop("flinch", None)
        # Redirection lasts one turn only.
        self.p1.follow_me_target = None
        self.p2.follow_me_target = None
        self._departed_slots = {}

        all_actions = p1_actions + p2_actions

        # 1. Switches resolve first (fastest switcher first). Switch-in abilities
        #    (Intimidate, Drizzle, ...) fire here for EVERY switch before ANY Mega
        #    Evolution happens -- see step 1b.
        switches = [a for a in all_actions if a.kind == "switch"]
        for a in sorted(switches, key=lambda a: -effective_speed(a.combatant, self.field, a.side)):
            side = self.p1 if a.side == "p1" else self.p2
            opp = self.p2 if a.side == "p1" else self.p1
            if self.is_trapped(a.combatant):
                self.log.add(f"{self.tag(a.combatant)} is trapped and cannot switch out!")
                self._emit(event="trapped", side=a.side, actor=a.combatant.name)
                continue
            slot = side.active.index(a.combatant)
            incoming = a.targets[0]  # the bench Combatant coming in
            side.bench[:] = [b for b in side.bench if b is not incoming]
            if not side.active[slot].fainted:
                side.bench.append(side.active[slot])
            side.active[slot] = incoming
            # Remember which SLOT the outgoing Pokemon occupied, so moves aimed at it
            # this turn hit whatever now stands in that slot -- not the other slot.
            self._departed_slots[id(a.combatant)] = (side.name, slot)
            ally = side.active[1 - slot] if len(side.active) == 2 else None
            incoming.choice_locked_move = None
            # Announce the switch BEFORE its abilities fire. A voluntary switch
            # applies to whatever is on the field right now, so the ability
            # message belongs immediately after the arrival that caused it --
            # not above it, where it reads as coming from nobody.
            self.log.add(f"{a.side} switches {self.tag(a.combatant)} -> {self.tag(incoming)}")
            on_switch_in(incoming, opp.active, self.field, ally=ally, log=self.log)
            # Emitted after, so weather_after is the weather the switch produced.
            self._emit(event="switch", side=a.side, out=a.combatant.name, incoming=incoming.name,
                       ability_after=incoming.ability, weather_after=self.field.weather)

        # NOTE: Mega Evolution is NOT resolved here. A Pokemon mega-evolves only when
        # it is about to move (see the move loop below), so one that is KO'd by a
        # faster attack first never transforms at all.

        # 2. Moves resolve in priority/speed order.
        move_actions = [a for a in all_actions if a.kind in ("move", "protect")]
        # A Pokemon mid-charge on a two-turn move (Solar Beam/Blade, Electro Shot)
        # MUST complete it this turn -- override whatever action was submitted for
        # it, same as the real games not offering a choice on the release turn.
        for a in move_actions:
            charging = a.combatant.volatile.get("charging_move")
            if charging and a.kind == "move":
                key = charging.lower().replace(" ", "").replace("-", "").replace("'", "")
                if key in self.moves_db:
                    a.move = self.make_move(key)
                    names = a.combatant.volatile.get("charging_target_names") or []
                    live = [c for c in self.p1.active + self.p2.active
                            if c is not None and c.name in names and not c.fainted]
                    if live:
                        a.targets = live
        for a in move_actions:
            self._enforce_choice_lock(a)
        # Usage stats: count the move as "clicked" for whoever selected it this turn,
        # regardless of whether it goes on to whiff/get blocked/flinched away -- that
        # matches how usage is normally reported (e.g. "used Protect" even if it
        # failed the consecutive-use check).
        for a in move_actions:
            if a.move is not None and not a.combatant.fainted:
                st = self.stats.get((a.side, a.combatant.name))
                if st is not None:
                    st["moves"][a.move.name] = st["moves"].get(a.move.name, 0) + 1
        # With an RNG active, pre-shuffle so that equal sort keys (true speed ties)
        # resolve randomly -- Python's sort is stable, so without this a coin flip
        # would always fall the same way and risk analysis couldn't see it.
        if self.tie_bias in ("p1", "p2"):
            move_actions.sort(key=lambda a: 0 if a.side == self.tie_bias else 1)
        elif self.rng is not None:
            self.rng.shuffle(move_actions)
        # Mega Evolution: resolved at the START of the turn -- after all switches,
        # before ANY move -- in speed order, but ONLY for Pokemon that are acting
        # this turn. A Pokemon that just switched in does not act, so it stays in
        # base form until next turn.
        for a in sorted(move_actions,
                         key=lambda x: -effective_speed(x.combatant, self.field, x.side)):
            self._mega_evolve_now(a.combatant)

        ordered = turn_order(move_actions, self.field)

        # Record any 50/50 speed ties this turn. turn_order resolves them
        # deterministically, so without this a coin-flip would be reported as a
        # certainty (e.g. our Garchomp vs their Garchomp).
        from engine import find_speed_ties
        for a, bb in find_speed_ties(move_actions, self.field):
            self.speed_ties.append((self.turn_num, a.combatant.name, bb.combatant.name))
            self.log.add(f"SPEED TIE: {a.combatant.name} and {bb.combatant.name} "
                          f"are equally fast in the same priority bracket (coin flip in a real game)")
            self._emit(event="speed_tie", side=a.side, actor=a.combatant.name,
                       target=bb.combatant.name)

        # Resolve Protect/Wide Guard/Quick Guard declarations before damage moves this turn.
        # NO DOUBLE PROTECT: a protect-type move fails outright if the same Pokemon
        # successfully protected on the immediately preceding turn. (Real games use a
        # decaying 1/3^n success chance; this format's rule -- and this tool's
        # deterministic-by-design stance -- is the simpler "consecutive use always
        # fails". Without this the solver farms free turns by protecting forever.)
        protect_users_this_turn = set()
        for a in ordered:
            if a.kind == "protect" or (a.move and a.move.name in PROTECT_MOVES):
                if a.combatant.protected_last_turn:
                    self.log.add(f"{a.combatant.name} tried to protect, but it failed! "
                                  f"(cannot Protect twice in a row)")
                    self._emit(event="protect_failed", side=a.side, actor=a.combatant.name)
                else:
                    a.combatant.protecting = True
                    protect_users_this_turn.add(id(a.combatant))
                    self.log.add(f"{self.tag(a.combatant)} protects itself!")
                    self._emit(event="protect", side=a.side, actor=a.combatant.name)
            elif a.move and a.move.name == "Wide Guard":
                side = self.side_of(a.combatant)
                side.wide_guard = True
                self.log.add(f"{a.combatant.name} sets up Wide Guard!")
                self._emit(event="wide_guard", side=a.side, actor=a.combatant.name)
            elif a.move and a.move.name == "Quick Guard":
                side = self.side_of(a.combatant)
                side.quick_guard = True
                self.log.add(f"{a.combatant.name} sets up Quick Guard!")
                self._emit(event="quick_guard", side=a.side, actor=a.combatant.name)
            elif a.move and a.move.volatile_status in ("followme", "ragepowder"):
                side = self.side_of(a.combatant)
                side.follow_me_target = a.combatant
                self.log.add(f"{a.combatant.name} became the center of attention!")
                self._emit(event="redirect_set", side=a.side, actor=a.combatant.name,
                           move=a.move.name)
            elif a.move and a.move.name in ("Reflect", "Light Screen", "Aurora Veil"):
                self._set_screen(a)

        # Resolve moves one at a time, RE-SORTING the remainder after each. Speed can
        # change mid-turn -- Tailwind going up, an Intimidate, a Speed drop -- and from
        # Gen 5 onward that affects the order of Pokemon that have not moved yet.
        # Sorting once at the start meant a Tailwind set this turn did nothing until
        # the next one.
        remaining = list(ordered)
        while remaining:
            remaining = turn_order(remaining, self.field)
            a = remaining.pop(0)
            if a.combatant.fainted:
                continue
            if a.kind == "protect" or (a.move and a.move.name in PROTECT_MOVES.union({"Wide Guard", "Quick Guard"})):
                continue  # already resolved above
            if a.move and a.move.volatile_status in ("followme", "ragepowder"):
                continue  # redirection already registered above
            if a.move and a.move.name in ("Reflect", "Light Screen", "Aurora Veil"):
                continue  # screens already registered above
            if a.move and a.move.name == "Sucker Punch":
                # Sucker Punch fails unless the target is using a DAMAGING move this
                # turn (it whiffs into status, Protect, or a switch).
                tgt_ids = {id(x) for x in a.targets}
                if not any(id(o.combatant) in tgt_ids and o.kind == "move" and o.move
                            and o.move.category != "Status"
                            for o in ordered if o is not a):
                    self.log.add(f"{self.tag(a.combatant)}'s Sucker Punch failed -- "
                                  f"the target was not attacking.")
                    continue
            if a.move and a.move.name == "Upper Hand":
                # Upper Hand only connects if the target is itself using a damaging
                # priority move this turn (it pre-empts Fake Out, Sucker Punch, Aqua
                # Jet). Being priority itself, it is stopped by Armor Tail / Queenly
                # Majesty / Dazzling on the other side.
                tgt_ids = {id(x) for x in a.targets}
                if not any(id(o.combatant) in tgt_ids and o.move
                            and o.move.priority > 0 and o.move.category != "Status"
                            for o in ordered if o is not a):
                    self.log.add(f"{self.tag(a.combatant)}'s Upper Hand failed -- "
                                  f"the target was not using a priority move.")
                    continue
            if a.combatant.volatile.get("flinch"):
                self.log.add(f"{self.tag(a.combatant)} flinched and couldn't move!")
                self._emit(event="flinched", side=a.side, actor=a.combatant.name)
                continue
            self._resolve_move(a)

        # 3. End of turn effects
        self._end_of_turn()

        # 4. Fainted Pokemon are replaced at the END of the turn, so the incoming
        #    mon is on the field and able to act starting NEXT turn.
        self._replace_fainted()

    PRIORITY_BLOCK_ABILITIES = {"Queenly Majesty", "Dazzling", "Armor Tail"}

    def _blocked_by_guard(self, attacker: Action, target: Combatant) -> bool:
        target_side = self.side_of(target)
        if target.protecting:
            return True
        if target_side.wide_guard and is_spread_move(attacker.move.target):
            return True
        if target_side.quick_guard and attacker.move.priority > 0:
            return True
        # Queenly Majesty / Dazzling / Armor Tail block ALL incoming priority moves
        # aimed at that side -- the standard answer to a Fake Out lead. Held by the
        # target or its partner; ignored by Mold Breaker-style abilities.
        if attacker.move.priority > 0 and attacker.combatant.ability not in (
                "Mold Breaker", "Teravolt", "Turboblaze"):
            for c in target_side.active:
                if c is not None and not c.fainted and c.ability in self.PRIORITY_BLOCK_ABILITIES:
                    self.log.add(f"{self.tag(c)}'s {c.ability} blocked the priority move!")
                    return True
        return False

    def _set_screen(self, a: Action):
        """Reflect / Light Screen / Aurora Veil. Duration is 5 turns, 8 if the
        setter holds Light Clay. Aurora Veil requires snow and blocks BOTH
        damage categories in place of Reflect/Light Screen -- it fails outside
        snow rather than silently doing nothing."""
        side = self.side_of(a.combatant)
        duration = 8 if a.combatant.item == "Light Clay" else 5
        name = a.move.name
        if name == "Aurora Veil" and self.field.weather != "snow":
            self.log.add(f"{self.tag(a.combatant)}'s Aurora Veil failed! (snow required)")
            self._emit(event="screen_failed", side=a.side, actor=a.combatant.name, move=name)
            return
        if name == "Reflect":
            side.screens_reflect = duration
        elif name == "Light Screen":
            side.screens_lightscreen = duration
        elif name == "Aurora Veil":
            side.screens_auroraveil = duration
        self.log.add(f"{self.tag(a.combatant)}'s side is protected by {name}! ({duration} turns)")
        self._emit(event="screen_set", side=a.side, actor=a.combatant.name, move=name, turns=duration)

    def is_trapped(self, c) -> bool:
        """True if `c` cannot switch out. Shadow Tag traps everything except Ghost
        types (and Shadow Tag users themselves); Shed Shell always escapes.
        Switching MOVES (U-turn, Parting Shot) are unaffected -- this only blocks
        the switch action."""
        if c is None or c.fainted:
            return False
        if "Ghost" in c.types or c.item == "Shed Shell" or c.ability == "Shadow Tag":
            return False
        opp = self.p2 if self.side_of(c) is self.p1 else self.p1
        return any(f is not None and not f.fainted and f.ability == "Shadow Tag"
                   for f in opp.active)

    def _active_auras(self):
        """Ability names on the field that modify damage globally (Fairy Aura,
        Dark Aura, Aura Break). These affect BOTH sides, so they are gathered
        from every active Pokemon."""
        out = set()
        for c in list(self.p1.active) + list(self.p2.active):
            if c is not None and not c.fainted and c.ability in ("Fairy Aura", "Dark Aura",
                                                                  "Aura Break"):
                out.add(c.ability)
        return out

    def _retarget(self, targets, attacker):
        """Resolve targets at execution time, not selection time.

        Two cases this fixes:
          * the chosen target SWITCHED OUT before the move resolved -- the attack
            should hit whatever is now in that slot, not follow the Pokemon that
            left (previously a move aimed at Charizard still hit Charizard after
            it had been swapped for Mega Gyarados);
          * the chosen target FAINTED earlier in the turn -- a single-target move
            redirects to the remaining opposing Pokemon rather than fizzling.
        """
        out, seen = [], set()
        for t in targets:
            side = self.side_of(t)
            live = [c for c in side.active if c is not None and not c.fainted]
            pick = t
            if t not in side.active:
                # Switched out this turn: the attack follows the SLOT, so it hits
                # whoever replaced it. Falling back to "the other active Pokemon"
                # was wrong -- it sent moves at the wrong target entirely.
                slot_info = getattr(self, "_departed_slots", {}).get(id(t))
                if slot_info is not None:
                    _, slot = slot_info
                    occupant = side.active[slot] if slot < len(side.active) else None
                    if occupant is not None and not occupant.fainted:
                        pick = occupant
                    else:
                        continue
                else:
                    others = [c for c in live if c is not attacker]
                    if not others:
                        continue
                    pick = others[0]
            elif t.fainted:
                # Fainted earlier this turn: a single-target move redirects to the
                # remaining opposing Pokemon.
                others = [c for c in live if c is not attacker]
                if not others:
                    continue
                pick = others[0]
            if id(pick) not in seen:
                seen.add(id(pick))
                out.append(pick)
        return out

    def _resolve_move(self, action: Action):
        attacker = action.combatant
        move = action.move

        if move.category == "Status":
            self._resolve_status_move(action)
            return

        # Two-turn (charge) moves: Solar Beam/Solar Blade skip the charge turn
        # in sun, Electro Shot skips it in rain -- both resolve immediately
        # instead. Electro Shot's self +1 SpA applies either way, the turn
        # it's used (charging or not).
        already_charged = attacker.volatile.get("charging_move") == move.name
        if move.flags and move.flags.get("charge") and not already_charged:
            if move.name == "Electro Shot":
                changed = apply_boosts(attacker, {"spa": 1}, from_foe=False)
                if changed:
                    self.log.add(f"{self.tag(attacker)}'s {self._fmt_boosts(changed)}")
                    self._emit(event="stat_change", side=action.side, actor=attacker.name,
                               detail=self._fmt_boosts(changed), source=f"{move.name} (self)")
            skip_weather = CHARGE_WEATHER_SKIP.get(move.name)
            if not (skip_weather and self.field.weather == skip_weather):
                attacker.volatile["charging_move"] = move.name
                attacker.volatile["charging_target_names"] = [t.name for t in action.targets]
                self.log.add(f"{self.tag(attacker)} is charging power for {move.name}!")
                self._emit(event="charge_start", side=action.side, actor=attacker.name, move=move.name)
                return
            self.log.add(f"{self.tag(attacker)}'s {move.name} strikes immediately! (weather)")
        if already_charged:
            attacker.volatile.pop("charging_move", None)
            attacker.volatile.pop("charging_target_names", None)

        # Targets resolve through _retarget, which follows the SLOT the intended
        # target occupied. An earlier duplicate of this logic picked "the first live
        # Pokemon on that side" instead, which sent moves at the wrong slot entirely
        # whenever the opposing lead had switched.
        live_targets = self._retarget(action.targets, attacker)
        if not live_targets:
            self.log.add(f"{attacker.name} uses {move.name}")
            return

        num_hit = 0
        # Follow Me / Rage Powder redirection: a single-target move aimed at the
        # opposing side gets pulled onto the redirector instead. Spread moves are
        # unaffected, and a fainted/absent redirector doesn't redirect.
        target_side = self.side_of(live_targets[0]) if live_targets else None
        single_target = (not is_spread_move(move.target)
                         and move.category != "Status")
        ignores_redirect = attacker.ability in ("Stalwart", "Propeller Tail")
        if (target_side is not None and target_side.follow_me_target is not None
                and single_target):
            redirector = target_side.follow_me_target
            if (not redirector.fainted and redirector in target_side.active
                    and redirector not in live_targets
                    and not ignores_redirect):
                self.log.add(f"{redirector.name} drew in {attacker.name}'s {move.name}!")
                self._emit(event="redirected", side=target_side.name, actor=redirector.name,
                           move=move.name, source=attacker.name)
                live_targets = [redirector]

        # Lightning Rod / Storm Drain: draw the whole type off whoever it was
        # aimed at. Checked after Follow Me, which is an active choice made this
        # turn and outranks a passive ability.
        if target_side is not None and single_target and not ignores_redirect:
            for drawer in target_side.active:
                if drawer is None or drawer.fainted or drawer in live_targets:
                    continue
                draw = DRAW_ABILITIES.get(drawer.ability)
                if draw and draw[0] == move.move_type:
                    self.log.add(f"{self.tag(drawer)}'s {drawer.ability} drew in "
                                 f"{attacker.name}'s {move.name}!")
                    self._emit(event="redirected", side=target_side.name,
                               actor=drawer.name, move=move.name,
                               source=attacker.name)
                    live_targets = [drawer]
                    break

        hit_targets = [t for t in live_targets if not self._blocked_by_guard(action, t)]
        blocked = [t for t in live_targets if t not in hit_targets]
        for t in blocked:
            self.log.add(f"{self.tag(attacker)}'s {move.name} was blocked by {self.tag(t)}'s guard!")
        num_hit = len(hit_targets) if is_spread_move(move.target) else min(1, len(hit_targets))
        total_damage_dealt = 0  # summed across targets; drives recoil/drain amounts
        # Last Respects: 50 BP base, +50 per fainted ally on the user's own team
        # (bench included, not just the current partner), capped at 200 -- so it
        # ramps up hard in the endgame once teammates have gone down.
        base_power = move.power
        if move.name == "Last Respects":
            side = self.side_of(attacker)
            fainted_allies = sum(1 for c in side.roster if c is not attacker and c.fainted)
            base_power = min(200, 50 + 50 * fainted_allies)
        # Helping Hand: a partner's Helping Hand this same turn boosts this move's
        # power by 1.5x, once, regardless of how many targets it hits.
        move_power = base_power * 1.5 if attacker.volatile.pop("helping_hand", False) else base_power
        for target in hit_targets:
            # Lightning Rod / Storm Drain absorbing the move they drew: no
            # damage (the type immunity already gives 0) and +1 to their stat.
            # Done before the damage calculation rather than after, because the
            # boost must not be applied to the attack that is being nullified.
            draw = DRAW_ABILITIES.get(target.ability)
            if draw and draw[0] == move.move_type and move.category != "Status":
                stat = draw[1]
                if target.stages[stat] < 6:
                    target.stages[stat] += 1
                    self.log.add(f"{self.tag(target)}'s {target.ability} absorbed "
                                 f"{move.name}! Its {stat.upper()} rose!")
                else:
                    self.log.add(f"{self.tag(target)}'s {target.ability} absorbed "
                                 f"{move.name}!")
                self._emit(event="absorbed", side=self.side_of(target).name,
                           actor=target.name, move=move.name, ability=target.ability)
                continue
            atk_key = "atk" if move.category == "Physical" else "spa"
            def_key = "def" if move.category == "Physical" else "spd"
            atk_stat = effective_stat(attacker.stats[atk_key], attacker.stages[atk_key])
            if attacker.status == "brn" and attacker.ability == "Guts" and atk_key == "atk":
                atk_stat *= 1.5
            if attacker.item == "Choice Band" and atk_key == "atk":
                atk_stat *= 1.5
            if attacker.item == "Choice Specs" and atk_key == "spa":
                atk_stat *= 1.5
            def_stat = defensive_stat(target, def_key, move)

            target_side = self.side_of(target)
            screens_active = target_side.screens_auroraveil > 0 or (
                target_side.screens_reflect > 0 if move.category == "Physical"
                else target_side.screens_lightscreen > 0)

            mn, mx, avg, eff = damage_roll(
                50, move_power, atk_stat, def_stat, attacker, target, move, self.typechart,
                auras=self._active_auras(), weather=self.field.weather, num_targets_hit=num_hit, screens=screens_active,
                roll_index=getattr(self, "force_roll_index", None),
            )
            # Multi-hit moves (Bullet Seed, Population Bomb, ...): aggregate total
            # damage as hits * single-hit damage rather than simulating each hit.
            hits = hit_count_for(move.name, attacker)
            if hits != 1:
                mn, mx, avg = mn * hits, mx * hits, avg * hits
            dmg = self._roll(mn, mx, avg)
            # Focus Sash / Sturdy: survive a would-be KO at 1 HP, but only from FULL HP.
            sashed = False
            if (dmg >= target.current_hp and target.current_hp == target.max_hp()
                    and (target.item == "Focus Sash" or target.ability == "Sturdy")):
                dmg = target.current_hp - 1
                sashed = True
            dmg_applied = min(dmg, target.current_hp)  # recoil/drain scale off damage actually dealt
            total_damage_dealt += dmg_applied
            was_fainted_before = target.fainted
            target.apply_damage(dmg)
            # Usage stats: damage dealt as %-of-target-max-HP, and a KO credited to
            # whoever's hit actually caused it.
            atk_st = self.stats.get((action.side, attacker.name))
            if atk_st is not None and target.max_hp():
                atk_st["damage_pct"] += 100.0 * dmg_applied / target.max_hp()
                if target.fainted and not was_fainted_before:
                    atk_st["kos"] += 1
            if target.ability == "Stamina" and dmg_applied > 0 and not target.fainted:
                changed = apply_boosts(target, {"def": 1}, from_foe=False)
                if changed:
                    self.log.add(f"{self.tag(target)}'s {self._fmt_boosts(changed)} (Stamina)")
                    self._emit(event="stat_change", side=self.side_of(target).name,
                               actor=target.name, detail=self._fmt_boosts(changed), source="Stamina")
            self.log.add(f"{self.tag(attacker)} uses {move.name} on {self.tag(target)}: "
                          f"{dmg:.0f} dmg ({eff}x eff) -> {self.tag(target)} at "
                          f"{target.current_hp}/{target.max_hp()} HP"
                          f"{' [FAINTED]' if target.fainted else ''}")
            if sashed:
                held = target.item == "Focus Sash"
                if held:
                    target.item = ""   # consumed
                self.log.add(f"{self.tag(target)} hung on with "
                              f"{'its Focus Sash' if held else 'Sturdy'}!")
                self._emit(event="focus_sash", side=self.side_of(target).name,
                           actor=target.name, target_hp_after=target.current_hp,
                           target_max_hp=target.max_hp())
            self._check_berry(target)   # pinch berries trigger right after the hit lands
            self._check_berry(target)   # after the damage line, so the log reads in order

            # Knock Off strips a removable item on hit (the matching 1.5x power boost
            # is in damage_roll) -- not a Mega Stone the target needs this battle, and
            # Sticky Hold blocks the removal outright (the 1.5x power still applies).
            if move.name == "Knock Off" and target.item and target.ability != "Sticky Hold" \
                    and dmg_applied > 0 and not target.fainted:
                low = target.item.lower()
                is_stone = (low.endswith("ite") or low.endswith("ite x") or low.endswith("ite y")
                            or low.endswith("itex") or low.endswith("itey"))
                if not is_stone:
                    knocked = target.item
                    target.item = ""
                    self.log.add(f"{self.tag(target)} lost its {knocked} to Knock Off!")
                    self._emit(event="knock_off", side=self.side_of(target).name,
                               actor=target.name, item=knocked)

            self._emit(event="move_damage", side=action.side, actor=attacker.name, move=move.name,
                       target=target.name, target_side=self.side_of(target).name, damage=round(dmg),
                       type_eff=eff, target_hp_after=target.current_hp, target_max_hp=target.max_hp(),
                       fainted=target.fainted)

            # Sheer Force trades the secondary effect for +30% power (applied in
            # damage_roll), so the secondary must NOT also fire.
            if move.secondary and not target.fainted and attacker.ability != "Sheer Force":
                chance = move.secondary.get("chance", 100)
                triggers = (chance >= 100) if self.rng is None else (self.rng.uniform(0, 100) < chance)
                if triggers:
                    if move.secondary.get("volatileStatus") == "flinch":
                        target.volatile["flinch"] = True
                        self.log.add(f"{self.tag(target)} flinched!")
                    # Target stat drops from secondaries, e.g. Snarl -1 SpA, Icy Wind -1 Spe.
                    if move.secondary.get("boosts"):
                        changed = apply_boosts(target, move.secondary["boosts"], from_foe=True)
                        if changed:
                            self.log.add(f"{self.tag(target)}'s {self._fmt_boosts(changed)}")
                            self._emit(event="stat_change", side=self.side_of(target).name,
                                       actor=target.name, detail=self._fmt_boosts(changed),
                                       source=move.name)

        # Self stat changes (Draco Meteor / Overheat / Leaf Storm -2 SpA, Close Combat -1 Def/SpD).
        # Applies once per move use, only if it actually hit something, and is NOT blocked by
        # Clear Body nor does it proc Defiant/Competitive (self-inflicted, not from a foe).
        if move.self_effect and move.self_effect.get("boosts") and hit_targets and not attacker.fainted:
            changed = apply_boosts(attacker, move.self_effect["boosts"], from_foe=False)
            if changed:
                self.log.add(f"{self.tag(attacker)}'s {self._fmt_boosts(changed)}")
                self._emit(event="stat_change", side=action.side, actor=attacker.name,
                           detail=self._fmt_boosts(changed), source=f"{move.name} (self)")

        if total_damage_dealt > 0:
            # Recoil (Flare Blitz/Wave Crash 33%, Head Smash 50%, ...) as a fraction of
            # damage actually dealt. Rock Head / Magic Guard negate it.
            if move.recoil and attacker.ability not in ("Rock Head", "Magic Guard"):
                num, den = move.recoil
                rec = max(1, int(round(total_damage_dealt * num / den)))
                attacker.apply_damage(rec)
                self.log.add(f"{attacker.name} is hit with recoil! ({rec} dmg -> "
                              f"{attacker.current_hp}/{attacker.max_hp()})"
                              f"{' [FAINTED]' if attacker.fainted else ''}")
                self._emit(event="recoil", side=action.side, actor=attacker.name, damage=rec,
                           target_hp_after=attacker.current_hp, target_max_hp=attacker.max_hp(),
                           fainted=attacker.fainted, source=move.name)

            # Drain (Giga Drain 50%, Draining Kiss 75%) heals the attacker.
            if move.drain and not attacker.fainted:
                num, den = move.drain
                heal = max(1, int(round(total_damage_dealt * num / den)))
                before = attacker.current_hp
                attacker.current_hp = min(attacker.max_hp(), attacker.current_hp + heal)
                if attacker.current_hp != before:
                    self.log.add(f"{attacker.name} drained HP! (+{attacker.current_hp-before} -> "
                                  f"{attacker.current_hp}/{attacker.max_hp()})")

        # Life Orb: 10% max HP recoil on any damaging move that connected. This is a real
        # cost that was previously unmodeled while the 1.3x damage boost WAS applied,
        # systematically overrating Life Orb users.
        # Sheer Force also cancels Life Orb recoil (its signature interaction) when the
        # move it boosted had a secondary effect.
        _sheer_force_lo = attacker.ability == "Sheer Force" and bool(move.secondary)
        if (attacker.item == "Life Orb" and total_damage_dealt > 0
                and attacker.ability != "Magic Guard" and not _sheer_force_lo
                and not attacker.fainted):
            lo = max(1, attacker.max_hp() // 10)
            attacker.apply_damage(lo)
            self.log.add(f"{self.tag(attacker)} is hurt by its Life Orb! ({lo} dmg -> "
                          f"{attacker.current_hp}/{attacker.max_hp()})"
                          f"{' [FAINTED]' if attacker.fainted else ''}")
            self._emit(event="life_orb", side=action.side, actor=attacker.name, damage=lo,
                       target_hp_after=attacker.current_hp, target_max_hp=attacker.max_hp(),
                       fainted=attacker.fainted)

        # Self-switch damaging moves (U-turn, Volt Switch, Flip Turn): pivot out
        # after the hit lands, same repositioning value as Parting Shot -- see
        # _voluntary_switch_out. The Status self-switch moves are handled in
        # _resolve_status_move.
        # A self-switch move only pivots IF IT CONNECTED. Protect (or Wide
        # Guard, or a Lightning Rod that ate it) blocks the hit, and a blocked
        # U-turn leaves you exactly where you were -- which is the whole risk of
        # clicking one. Previously the switch happened regardless, so the engine
        # believed a Protect could not deny the pivot and priced U-turn and Flip
        # Turn as free repositioning.
        if move.self_switch and not attacker.fainted and hit_targets:
            self._voluntary_switch_out(attacker, action.side)

    @staticmethod
    def _fmt_boosts(changed: dict) -> str:
        names = {"atk": "Attack", "def": "Defense", "spa": "Sp. Atk", "spd": "Sp. Def", "spe": "Speed"}
        parts = []
        for stat, delta in changed.items():
            word = "rose" if delta > 0 else "fell"
            amount = {1: "", 2: " sharply", 3: " drastically"}.get(abs(delta), f" by {abs(delta)}")
            parts.append(f"{names.get(stat, stat)} {word}{amount}")
        return ", ".join(parts) + "!"

    def _resolve_status_move(self, action: Action):
        """Handles the field/side-effect status moves relevant to this format.
        Anything not listed here is a no-op (logged only) -- flagged as a gap,
        not silently pretended-away."""
        attacker = action.combatant
        move = action.move
        side = self.side_of(attacker)
        # Set False only by a status move that aims at a foe and was blocked --
        # see Parting Shot. Everything else connects with itself.
        status_connected = True

        if move.name == "Trick Room":
            if self.field.trick_room:
                self.field.trick_room = False
                self.field.trick_room_turns_left = 0
                self.log.add(f"{attacker.name} reversed dimensions back to normal! Trick Room ended.")
            else:
                self.field.trick_room = True
                self.field.trick_room_turns_left = 5
                self.log.add(f"{attacker.name} twisted the dimensions! Trick Room active.")
            self._emit(event="trick_room", side=action.side, actor=attacker.name,
                       trick_room_now=self.field.trick_room)
        elif move.name == "Tailwind":
            if side.name == "p1":
                self.field.tailwind_p1 = 4
            else:
                self.field.tailwind_p2 = 4
            self.log.add(f"Tailwind blew from behind {attacker.name}'s side!")
            self._emit(event="tailwind", side=action.side, actor=attacker.name)
        elif move.name == "Perish Song":
            # Every Pokemon on the field that can hear it faints in 3 turns.
            # Soundproof is immune. The counter follows the Pokemon, so switching
            # out does NOT clear it -- that is what makes Perish Trap work.
            hit = []
            for c in list(self.p1.active) + list(self.p2.active):
                if c is None or c.fainted or c.ability == "Soundproof":
                    continue
                if c.perish_counter is None:
                    c.perish_counter = 3
                    hit.append(self.tag(c))
            self.log.add(f"{self.tag(attacker)} sang a Perish Song! "
                          f"({', '.join(hit) if hit else 'no effect'} will faint in 3 turns)")
            self._emit(event="perish_song", side=action.side, actor=attacker.name,
                       detail=", ".join(hit))
        elif move.name == "Parting Shot":
            # SINGLE target, -1 Atk AND -1 SpA, and it can be Protected. All
            # three were wrong: it hit both foes, took only Attack (it reused
            # the Intimidate helper), and connected through Protect -- then
            # pivoted anyway. That made it a free double debuff plus a free
            # switch, which is why the solver liked it so much.
            opp_side = self.p2 if side.name == "p1" else self.p1
            live = [f for f in opp_side.active if f and not f.fainted]
            target = next((f for f in live if not self._blocked_by_guard(action, f)),
                          None)
            if target is None:
                self.log.add(f"{attacker.name}'s Parting Shot was blocked!")
                status_connected = False
            else:
                changed = apply_boosts(target, {"atk": -1, "spa": -1}, from_foe=True)
                self.log.add(f"{attacker.name} used Parting Shot on "
                             f"{self.tag(target)}! {self._fmt_boosts(changed)}")
                self._emit(event="parting_shot", side=action.side,
                           actor=attacker.name, target=target.name)
        elif move.name == "Helping Hand":
            ally = next((c for c in side.active if c is not None and c is not attacker
                         and not c.fainted), None)
            if ally is not None:
                ally.volatile["helping_hand"] = True
                self.log.add(f"{self.tag(attacker)} is ready to help {self.tag(ally)}!")
                self._emit(event="helping_hand", side=action.side, actor=attacker.name,
                           target=ally.name)
            else:
                self.log.add(f"{self.tag(attacker)} used Helping Hand, but there was no ally to help!")
        elif move.boosts:
            # Pure stat-changing status move (Swords Dance, Nasty Plot, Calm Mind, ...).
            # Self-targeting moves boost the user; ally-targeting ones (Coaching) boost the
            # partner; foe-targeting ones (Growl etc.) hit the foes.
            if move.target in ("self", "allySide"):
                changed = apply_boosts(attacker, move.boosts, from_foe=False)
                if changed:
                    self.log.add(f"{self.tag(attacker)}'s {self._fmt_boosts(changed)}")
                    self._emit(event="stat_change", side=action.side, actor=attacker.name,
                               detail=self._fmt_boosts(changed), source=move.name)
            elif move.target == "adjacentAlly":
                ally = next((c for c in side.active if c is not None and c is not attacker
                             and not c.fainted), None)
                if ally is not None:
                    changed = apply_boosts(ally, move.boosts, from_foe=False)
                    if changed:
                        self.log.add(f"{self.tag(ally)}'s {self._fmt_boosts(changed)} (from {attacker.name}'s {move.name})")
                        self._emit(event="stat_change", side=action.side, actor=ally.name,
                                   detail=self._fmt_boosts(changed), source=move.name)
            else:
                opp_side = self.p2 if side.name == "p1" else self.p1
                targets = [f for f in opp_side.active if f and not f.fainted]
                if not is_spread_move(move.target):
                    targets = targets[:1]
                for foe in targets:
                    changed = apply_boosts(foe, move.boosts, from_foe=True)
                    if changed:
                        self.log.add(f"{foe.name}'s {self._fmt_boosts(changed)}")
                        self._emit(event="stat_change", side=opp_side.name, actor=foe.name,
                                   detail=self._fmt_boosts(changed), source=move.name)
        else:
            self.log.add(f"{attacker.name} uses {move.name} (effect not modeled yet)")
            self._emit(event="status_move_unmodeled", side=action.side, actor=attacker.name, move=move.name)

        # Self-switch (U-turn/Volt Switch handle their own copy in _resolve_move,
        # since they're damaging; this covers the Status ones -- Parting Shot,
        # Baton Pass, Chilly Reception, Shed Tail. Only the switch itself is
        # modeled for the latter three, not their extra pass-along effects.
        # A self-switch move only pivots IF IT CONNECTED. Parting Shot aims at a
        # foe and can be Protected; Baton Pass, Shed Tail and Chilly Reception
        # target the user and always connect, so `status_connected` stays True.
        if move.self_switch and not attacker.fainted and status_connected:
            self._voluntary_switch_out(attacker, action.side)

    def _voluntary_switch_out(self, outgoing: Combatant, action_side: str):
        """A self-switch move (U-turn, Volt Switch, Parting Shot, Flip Turn,
        Chilly Reception, Baton Pass, Shed Tail -- see MoveInfo.self_switch)
        just resolved; bring in the best bench replacement for `outgoing`,
        MID-TURN rather than at the end like a faint-replacement.

        This is what gives these moves their real repositioning value: unlike
        a forced end-of-turn replacement (which is safe until next turn), a
        mid-turn switch-in is exposed to the rest of THIS turn's still-to-act
        moves -- e.g. a partner's spread move, or a faster attacker later in
        turn order can still hit whatever just came in. Escaping a bad
        matchup or bringing in a check still costs the tempo of the move
        itself, but doesn't buy the safety a KO-forced replacement does.
        """
        side = self.side_of(outgoing)
        opp = self.p2 if side is self.p1 else self.p1
        if outgoing not in side.active:
            return
        slot = side.active.index(outgoing)
        alive_bench = [b for b in side.bench if not b.fainted]
        if not alive_bench:
            return
        # A scripted opening (see scripted_openings.py) can pin exactly who comes
        # back for a specific rehearsed line -- e.g. Perish Trap's Incineroar
        # Parting Shot is scripted to bring Mega Gengar back specifically, to
        # keep Shadow Tag continuously on the field, not just "whoever the
        # generic matchup heuristic likes best" this turn.
        forced = getattr(self, "_forced_switch_in", None)
        forced_mon = forced.get(id(outgoing)) if forced else None
        if forced_mon is not None and forced_mon in alive_bench:
            incoming = forced_mon
        else:
            incoming = self._best_replacement(alive_bench, opp.active)
        side.bench[:] = [b for b in side.bench if b is not incoming]
        if not outgoing.fainted:
            side.bench.append(outgoing)
        side.active[slot] = incoming
        # Same bookkeeping as a voluntary pre-turn switch: later actions this turn
        # aimed at `outgoing` should follow the SLOT and hit `incoming` instead.
        self._departed_slots[id(outgoing)] = (side.name, slot)
        ally = side.active[1 - slot] if len(side.active) == 2 else None
        incoming.choice_locked_move = None
        # The pivot is announced before its abilities fire, same as a voluntary
        # switch -- see the note there.
        self.log.add(f"{self.tag(outgoing)} pivots out -> {self.tag(incoming)}")
        on_switch_in(incoming, opp.active, self.field, ally=ally, log=self.log)
        self._emit(event="switch", side=action_side, out=outgoing.name, incoming=incoming.name,
                   ability_after=incoming.ability, weather_after=self.field.weather)

    def _best_replacement(self, alive_bench, opposing_actives):
        """Which bench member is the best strategic answer to what's currently
        opposing this slot -- NOT just whoever has the most HP.

        A player picking a replacement (forced by a KO, or the opponent AI
        choosing how to respond) brings in their best answer to the board,
        weighing type matchup and raw offensive threat alongside health, not
        purely whoever happens to be healthiest. Choosing on HP alone
        previously meant the opposing side never modeled bringing in a
        genuine answer to punish a bad board state -- which in turn meant the
        planner never had to account for that risk, systematically
        undervaluing how dangerous forcing a free replacement can be.
        """
        from damage import type_multiplier
        live_foes = [f for f in opposing_actives if f is not None and not f.fainted]

        def score(b):
            s = b.current_hp_frac * 40.0
            s += max(b.stats.get("atk", 0), b.stats.get("spa", 0)) * 0.25
            for f in live_foes:
                danger_to_b = max((type_multiplier(t, b.types, self.typechart) for t in f.types),
                                   default=1.0)
                if danger_to_b >= 4.0:
                    s -= 45.0
                elif danger_to_b >= 2.0:
                    s -= 22.0
                elif danger_to_b <= 0.25:
                    s += 22.0
                elif danger_to_b <= 0.5:
                    s += 12.0
                # How much b itself threatens the foe (STAB proxy: does b resist/hit into it).
                b_into_f = max((type_multiplier(t, f.types, self.typechart) for t in b.types),
                                default=1.0)
                if b_into_f >= 2.0:
                    s += 15.0
            return s
        return max(alive_bench, key=score)

    def _replace_fainted(self):
        """Send in replacements for fainted actives at the END of the turn, so
        they are on the field ready to act on the NEXT turn (they do not get an
        action on the turn they came in). Mega Evolution is NOT resolved here --
        that happens at the start of the next turn, after any voluntary
        switches, in _resolve_mega_evolutions().

        TWO PHASES, and the split is the whole point. Everything is sent in
        first; only then do switch-in abilities fire. Doing both in one pass
        (which is what this used to do) gets Intimidate wrong in the common
        double-KO case, because the first side's replacement resolves against a
        field where the other side's fainted Pokemon is still standing in its
        slot:

            p1's Intimidate dropped the Attack of a FAINTED Pokemon -- an
            effect that goes nowhere -- and then p2's replacement came in
            afterwards and was never intimidated at all.

        A fainted Pokemon cannot be intimidated; its replacement can, and is.
        The second phase also runs in SPEED ORDER, which is how simultaneous
        switch-in abilities resolve -- it decides which of two weather setters
        ends up owning the weather.
        """
        arrivals = []
        for side in (self.p1, self.p2):
            opp = self.p2 if side is self.p1 else self.p1
            for slot, c in enumerate(side.active):
                if c is None or not c.fainted:
                    continue
                alive_bench = [b for b in side.bench if not b.fainted]
                if not alive_bench:
                    continue
                incoming = self._best_replacement(alive_bench, opp.active)
                side.bench[:] = [b for b in side.bench if b is not incoming]
                side.active[slot] = incoming
                arrivals.append((side, slot, incoming, c))
                self.log.add(f"{side.name} sends in {self.tag(incoming)} "
                             f"(replacing fainted {self.tag(c)})")

        arrivals.sort(key=lambda a: -effective_speed(a[2], self.field, a[0].name))
        for side, slot, incoming, replaced in arrivals:
            opp = self.p2 if side is self.p1 else self.p1
            ally = side.active[1 - slot] if len(side.active) == 2 else None
            on_switch_in(incoming, opp.active, self.field, ally=ally, log=self.log)
            self._emit(event="replacement", side=side.name, out=replaced.name,
                       incoming=incoming.name, weather_after=self.field.weather)

    # Sandstorm chips everything that is not Ground, Rock or Steel for 1/16 of
    # its max HP at the end of every turn. Nothing implemented it: weather only
    # ever counted down, so a sand team's central pressure -- the reason to run
    # it at all -- was worth exactly zero, and any line that stalled under sand
    # was scored as free.
    #
    # The type list is the rule as stated. The abilities and the item are the
    # standard exemptions on top of it: without them Excadrill and friends take
    # damage from their own team's weather, which is the opposite of why they
    # are on it. Sand Veil / Sand Rush / Sand Force are the sand-synergy
    # abilities, Overcoat and Safety Goggles are the general weather-immunity
    # pair, and Magic Guard blocks all indirect damage.
    SAND_IMMUNE_TYPES = {"Ground", "Rock", "Steel"}
    SAND_IMMUNE_ABILITIES = {"Sand Veil", "Sand Rush", "Sand Force", "Overcoat",
                             "Magic Guard"}
    SAND_IMMUNE_ITEMS = {"Safety Goggles"}

    def _sand_immune(self, c) -> bool:
        return (bool(self.SAND_IMMUNE_TYPES.intersection(c.types))
                or c.ability in self.SAND_IMMUNE_ABILITIES
                or c.item in self.SAND_IMMUNE_ITEMS)

    def _sandstorm_damage(self):
        """1/16 max HP to every ACTIVE Pokemon the sand can touch.

        Active only: a Pokemon on the bench is not in the weather. (Burn and
        poison above do run over the whole roster, which is a separate and
        older discrepancy -- left alone here rather than folded into a weather
        change.)

        Runs before the burn/poison block, matching the real residual order --
        weather resolves first -- and before `_check_berry` in that same loop,
        so a Sitrus triggered by sand damage fires on the turn it happens.
        """
        if self.field.weather != "sand":
            return
        for side in (self.p1, self.p2):
            for c in side.active:
                if c is None or c.fainted or self._sand_immune(c):
                    continue
                c.apply_damage(c.max_hp() / 16)
                self.log.add(f"{self.tag(c)} is buffeted by the sandstorm!"
                             + (" [FAINTED]" if c.fainted else ""))
                self._emit(event="weather_damage", side=side.name, actor=c.name,
                           detail="sandstorm", fainted=c.fainted)

    def _end_of_turn(self):
        # Carry this turn's successful Protect into next turn's "no double Protect" check.
        # (Set for every roster member, not just actives, so a mon that protected and then
        # got switched out doesn't come back still flagged.)
        for c in self.p1.roster + self.p2.roster:
            c.protected_last_turn = c.protecting
            # An unused Helping Hand boost (ally protected, used a status move, or never
            # got to act) does NOT carry over -- it must not silently buff a future turn.
            c.volatile.pop("helping_hand", None)

        for c in self.p1.active + self.p2.active:
            if c is None or c.fainted:
                continue
            c.active_turn_count += 1
            if c.ability == "Speed Boost":
                changed = apply_boosts(c, {"spe": 1}, from_foe=False)
                if changed:
                    self.log.add(f"{self.tag(c)}'s {self._fmt_boosts(changed)} (Speed Boost)")
                    self._emit(event="stat_change", side=self.side_of(c).name, actor=c.name,
                               detail=self._fmt_boosts(changed), source="Speed Boost")
        self._sandstorm_damage()

        for c in self.p1.roster + self.p2.roster:
            if c.fainted:
                continue
            self._check_berry(c)
            if c.status == "brn":
                c.apply_damage(c.max_hp() / 16)
                self.log.add(f"{self.tag(c)} is hurt by its burn!")
            elif c.status == "psn":
                c.apply_damage(c.max_hp() / 8)
                self.log.add(f"{self.tag(c)} is hurt by poison!")

        # Perish Song countdown -- decremented at end of turn, faint at 0.
        for c in list(self.p1.active) + list(self.p2.active):
            if c is None or c.fainted or c.perish_counter is None:
                continue
            c.perish_counter -= 1
            if c.perish_counter <= 0:
                c.current_hp = 0
                c.fainted = True
                self.log.add(f"{self.tag(c)}'s Perish count hit 0 -- it fainted!")
                self._emit(event="perish_faint", side=self.side_of(c).name, actor=c.name)
            else:
                self.log.add(f"{self.tag(c)}'s Perish count fell to {c.perish_counter}.")

        for side in (self.p1, self.p2):
            if side.wide_guard:
                side.wide_guard = False
            if side.quick_guard:
                side.quick_guard = False
            if side.screens_reflect > 0:
                side.screens_reflect -= 1
            if side.screens_lightscreen > 0:
                side.screens_lightscreen -= 1
            if side.screens_auroraveil > 0:
                side.screens_auroraveil -= 1

        if self.field.weather_turns_left > 0:
            self.field.weather_turns_left -= 1
            if self.field.weather_turns_left == 0:
                self.log.add(f"The {self.field.weather} weather ended.")
                self.field.weather = None
        if self.field.trick_room_turns_left > 0:
            self.field.trick_room_turns_left -= 1
            if self.field.trick_room_turns_left == 0:
                self.field.trick_room = False
                self.log.add("Trick Room ended.")
        if self.field.tailwind_p1 > 0:
            self.field.tailwind_p1 -= 1
        if self.field.tailwind_p2 > 0:
            self.field.tailwind_p2 -= 1

    def is_over(self):
        return self.p1.has_lost() or self.p2.has_lost()

    def winner(self):
        if self.p1.has_lost() and self.p2.has_lost():
            return "draw"
        if self.p1.has_lost():
            return "p2"
        if self.p2.has_lost():
            return "p1"
        return None

    def adjudicate(self):
        """Who is winning if the clock runs out. Never None.

        THE REAL RULE, not an invention: VGC decides a timed-out game on the
        number of Pokemon remaining, and then on the percentage of HP left. A
        turn cap is exactly a clock running out, so it is scored the same way.

        Why this matters: counting a capped game as a LOSS is not conservative,
        it is wrong. A position where we are 3-1 up with the opponent's last
        Pokemon at 8 HP is a won game, and calling it a loss because the
        simulation ran out of turns discards precisely the lines that grind out
        wins -- which is the shape of most real VGC games.

        Returns "p1" | "p2" | "draw".
        """
        decided = self.winner()
        if decided is not None:
            return decided
        alive = [sum(1 for c in side.roster if not c.fainted)
                 for side in (self.p1, self.p2)]
        if alive[0] != alive[1]:
            return "p1" if alive[0] > alive[1] else "p2"
        # Same count: total remaining HP as a FRACTION of maximum, so a bulky
        # survivor does not outweigh a healthy frail one by raw hit points.
        share = []
        for side in (self.p1, self.p2):
            total = sum(c.max_hp() for c in side.roster) or 1
            share.append(sum(c.current_hp for c in side.roster) / total)
        if abs(share[0] - share[1]) < 1e-9:
            return "draw"
        return "p1" if share[0] > share[1] else "p2"


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
    garchomp = make_combatant("Garchomp")
    kingambit = make_combatant("Kingambit")

    battle = Battle([incineroar, farigiraf], [garchomp, kingambit], typechart, moves)
    print(battle.log.dump())

    fo = battle.make_move("fakeout")
    eq = battle.make_move("earthquake")
    protect = MoveInfo("Protect", 0, "Normal", "Status", "self", priority=4)

    # Turn 1: Incineroar Fake Out on Garchomp, Farigiraf Protects; Garchomp+Kingambit both EQ.
    p1_actions = [
        Action(incineroar, "p1", "move", fo, [garchomp]),
        Action(farigiraf, "p1", "protect", protect, [farigiraf]),
    ]
    p2_actions = [
        Action(garchomp, "p2", "move", eq, [incineroar, farigiraf]),
        Action(kingambit, "p2", "move", eq, [incineroar, farigiraf]),
    ]
    battle.run_turn(p1_actions, p2_actions)
    print(battle.log.dump())

    print(f"\nHP after turn 1: Incineroar {incineroar.current_hp}/{incineroar.max_hp()}, "
          f"Farigiraf {farigiraf.current_hp}/{farigiraf.max_hp()} (protected), "
          f"Garchomp {garchomp.current_hp}/{garchomp.max_hp()}, "
          f"Kingambit {kingambit.current_hp}/{kingambit.max_hp()}")
    print(f"Battle over? {battle.is_over()}  Winner: {battle.winner()}")
