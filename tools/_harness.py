"""Shared setup for the measurement tools in this directory.

The tools all need the same three things: load the dataset, stand up a Battle
from two lists of species names, and advance a COPY of a battle by one turn
without disturbing the original. That last one is fiddly enough (actions hold
references to Combatant objects, which deepcopy replaces) that having one
correct implementation beats three.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from species_data import build_merged_dataset, load_teams  # noqa: E402
from combatants import make_team  # noqa: E402
from battle import Battle  # noqa: E402
from solver import build_moveset, build_wide_movesets, heuristic_eval  # noqa: E402


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
    # The answer-preservation term needs to know what each Pokemon can do, and
    # Combatant does not carry its moves. Attaching them here turns the term on
    # for every measurement tool at once.
    battle.movesets = movesets
    # The opponent's plausible move space is wider than the four we assume;
    # see solver.build_wide_moveset for the measurement behind this.
    # Must cover BOTH rosters: solve_turn can be called from either seat, and
    # "their" side is whichever one is not being solved for. Only the enemy
    # entries are widened -- we know our own four moves.
    battle.wide_movesets = {
        **movesets,
        **build_wide_movesets([c.name for c in theirs], world["merged"],
                              world["moves"]),
    }
    return battle, movesets


def enemy_bring(team_name, world):
    """The lead pair from team metadata, padded to four from the roster."""
    roster = world["teams"][team_name]
    lead = world["meta"].get(team_name, {}).get("lead") or roster[:2]
    return list(lead) + [x for x in roster if x not in lead][:2]


# Re-exported from src/turn_step.py, which the production turn solver also
# uses. Kept importable from here so the existing harnesses do not change.
from turn_step import step  # noqa: E402,F401


def evaluate(battle, side="p1"):
    return heuristic_eval(battle, side)


LOSS = -1e4  # score for a joint action that fails to simulate
