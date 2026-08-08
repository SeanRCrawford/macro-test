"""Advance a COPY of a battle by one turn.

Fiddly enough to deserve one correct implementation: actions hold references to
Combatant objects, and deepcopy replaces those objects, so the actions have to
be rebuilt against the copy or the turn silently mutates the wrong state.

Lives in src/ rather than tools/ because the turn-level matrix solver needs it
in production, not only in measurement harnesses.
"""
import copy

from engine import Action


def step(battle, our_actions, their_actions, deterministic=True):
    """Run one turn on a copy of `battle`. Returns the copy, or None on failure.

    `deterministic` clears the RNG so damage takes the average roll, matching
    what the rest of the solver does. Section 4c is where that assumption is
    revisited; until then every consumer wants the same one.
    """
    sim = copy.deepcopy(battle)
    if deterministic:
        sim.rng = None
    sim.tie_bias = battle.tie_bias

    forward = {}
    for old, new in zip(battle.p1.roster, sim.p1.roster):
        forward[id(old)] = new
    for old, new in zip(battle.p2.roster, sim.p2.roster):
        forward[id(old)] = new

    def rebuild(a):
        return Action(forward.get(id(a.combatant), a.combatant), a.side, a.kind,
                      a.move, [forward.get(id(t), t) for t in a.targets])

    try:
        sim.run_turn([rebuild(a) for a in our_actions],
                     [rebuild(a) for a in their_actions])
    except Exception:
        return None
    return sim
