"""The field a decision is made against must be the field the move fires into.

Mega Evolution is resolved after all switches and before ANY move (battle.py
`run_turn`, step "Mega Evolution: resolved at the START of the turn"). So on the
turn a Mega transforms, `battle.field` at DECISION time is a state that no
longer exists by the time the chosen move resolves -- and the Pokemon choosing
is still in its base form, with base stats, base typing and base ability.

That gap is not cosmetic. Measured on Mega Charizard Y leading into Pelipper:

    decision time   rain (Drizzle), Charizard base, SpA 109
    resolution      sun  (Drought),  Mega Y,        SpA 159

and the consequences compound:

  * Solar Beam is dropped from the candidate list entirely. The charge filter in
    solver.candidate_actions skips a two-turn move unless `field.weather` is
    already the skip weather, and at decision time it reads "rain". The move
    that wins the game is never even offered.
  * Weather Ball is typed off the weather, so it is priced as a Water move --
    resisted by the Water/Ground target it is actually meant to delete.
  * Damage is estimated from base-form stats, understating every special attack
    by the whole mega stat jump.

The payoff matrix itself was never wrong: every cell comes from an actual
`run_turn`, which resolves the mega properly. The bug is upstream of it -- the
winning ROW is missing from the matrix, so the equilibrium is solved over the
wrong game. On the position above that is the difference between a turn-1 value
of -58.5 (play scared: protect, switch, concede the lead) and +170.

Deliberately a projection, not a mutation. Nothing here touches the battle: it
answers "what will be true when I move?" and leaves the real transform to the
engine, which alone knows whether the Pokemon ended up acting at all.

The one place the projection can be wrong is a Pokemon that mega-evolves in the
projection and then SWITCHES OUT, which does not transform for real. That
costs nothing: it only affects how attractive its own attacks looked, and it
did not attack.
"""
import copy

from engine import WEATHER_SETTERS, effective_speed


def pending_mega(battle, combatant):
    """Will `combatant` transform before it moves this turn?

    False once the side has spent its stone, which is what makes the second
    mega pick on a team stay in base form forever.
    """
    if combatant is None or combatant.fainted:
        return False
    if not combatant.is_mega_pick or combatant.mega_evolved:
        return False
    side = battle.side_of(combatant)
    if side.mega_used:
        return False
    # Only a Pokemon that is ON THE FIELD transforms this turn. A mega pick
    # sitting on the bench is still a base-form Pokemon for every purpose --
    # which is exactly why a mega-based weather setter provides no weather until
    # it comes in (engine.mega_evolve).
    return any(c is combatant for c in side.active)


def pending_megas(battle):
    """Every active that will transform this turn, in the order it happens.

    Speed order, at most one per side, matching run_turn -- and pre-mega speed,
    because run_turn computes the whole sort key before any transform lands.
    """
    out = []
    for side_name, side in (("p1", battle.p1), ("p2", battle.p2)):
        for c in side.active:
            if pending_mega(battle, c):
                out.append((effective_speed(c, battle.field, side_name), c))
                break   # one stone per side
    out.sort(key=lambda pair: -pair[0])
    return [c for _speed, c in out]


def projected_weather(battle):
    """The weather the turn resolves under, once pending megas have fired.

    The SLOWEST weather-setting mega wins, because run_turn transforms in
    descending speed order and each setter overwrites the last.
    """
    weather = battle.field.weather
    for c in pending_megas(battle):
        setter = WEATHER_SETTERS.get(c.mega_ability)
        if setter:
            weather = setter
    return weather


def projected_field(battle):
    """`battle.field`, but with the weather the turn will actually resolve in.

    Returns the REAL field object unchanged when nothing is pending, so the
    common case allocates nothing and callers can pass the result everywhere a
    field is wanted.
    """
    weather = projected_weather(battle)
    if weather == battle.field.weather:
        return battle.field
    field = copy.copy(battle.field)
    field.weather = weather
    return field


def mega_view(battle, combatant):
    """`combatant` as it will be when it moves: mega stats, typing and ability.

    A shallow copy with the mega fields installed. Only ever handed to damage
    ESTIMATORS -- never to an Action, which must always carry the real object so
    that the engine mutates the Pokemon actually on the field.
    """
    if not pending_mega(battle, combatant):
        return combatant
    view = copy.copy(combatant)
    view.stats = dict(combatant.mega_stats)
    view.ability = combatant.mega_ability
    view.types = list(combatant.mega_types)
    view.mega_evolved = True
    return view
