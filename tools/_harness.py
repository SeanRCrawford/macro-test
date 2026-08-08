"""Shared setup for the measurement tools in this directory.

The tools all need the same three things: load the dataset, stand up a Battle
from two lists of species names, and advance a COPY of a battle by one turn
without disturbing the original. That last one is fiddly enough (actions hold
references to Combatant objects, which deepcopy replaces) that having one
correct implementation beats three.
"""
import copy
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from species_data import build_merged_dataset, load_teams  # noqa: E402
from combatants import make_team  # noqa: E402
from battle import Battle  # noqa: E402
from engine import Action  # noqa: E402
from solver import build_moveset, heuristic_eval  # noqa: E402


def load_world():
    """Dataset plus the team library. Slow (parses the sheets), so call once."""
    merged, _usage, moves, natures, typechart = build_merged_dataset()
    teams, meta = load_teams(with_meta=True, merged=merged)
    return dict(merged=merged, moves=moves, natures=natures,
                typechart=typechart, teams=teams, meta=meta)


def setup_battle(our4, enemy4, world):
    """A fresh Battle plus the movesets dict the solver functions expect."""
    ours = make_team(our4, world["merged"], world["natures"])
    theirs = make_team(enemy4, world["merged"], world["natures"])
    movesets = {c.name: build_moveset(world["merged"][c.name], world["moves"])
                for c in ours + theirs}
    battle = Battle(ours, theirs, world["typechart"], world["moves"])
    return battle, movesets


def enemy_bring(team_name, world):
    """The lead pair from team metadata, padded to four from the roster."""
    roster = world["teams"][team_name]
    lead = world["meta"].get(team_name, {}).get("lead") or roster[:2]
    return list(lead) + [x for x in roster if x not in lead][:2]


def step(battle, our_actions, their_actions):
    """Advance a DEEPCOPY of `battle` by one turn. Returns the copy, or None.

    Actions reference Combatant objects belonging to `battle`, so they have to
    be rebuilt against the copy's objects or the turn mutates the wrong state.
    Deterministic: rng is cleared so damage takes the average roll, matching
    what the current solver does.
    """
    sim = copy.deepcopy(battle)
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


def evaluate(battle, side="p1"):
    return heuristic_eval(battle, side)


LOSS = -1e4  # score for a joint action that fails to simulate
