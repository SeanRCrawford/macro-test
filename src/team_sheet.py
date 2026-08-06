"""
Team sheet file format -- the bridge between the two tools.

`generate_team.py` writes `team.json` (names + optimised items + movesets),
and `run_search.py` reads it back with `--team-file team.json`, so the exact
team you generated (including its items and 4-move sets) is what gets
searched for leads/backs and gameplans. Without this, run_search would fall
back to raw usage-stat defaults and you'd be planning around a different
team than the one you generated.

Format:
{
  "pool": ["Incineroar", "Mega Charizard Y", ...],
  "sets": {
     "Incineroar": {"item": "Sitrus Berry",
                     "moves": ["Fake Out", "Parting Shot", "Flare Blitz", "Darkest Lariat"]},
     ...
  }
}
It's plain JSON -- edit it by hand freely (swap an item, change a move) and
re-run run_search to see the effect.
"""
import json
from pathlib import Path


def save_team(path, pool, sets=None):
    data = {"pool": list(pool), "sets": sets or {}}
    Path(path).write_text(json.dumps(data, indent=2))
    return path


def load_team(path):
    """Returns (pool, sets). Tolerates a bare list of names as well."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, list):
        return list(data), {}
    pool = data.get("pool") or []
    sets = data.get("sets") or {}
    if not pool:
        raise ValueError(f"{path}: no 'pool' list found in team file")
    return pool, sets


def load_sets_override(path):
    """Load a standalone sets override file: either {"sets": {name: {...}}}
    (same shape team.json uses) or a bare {name: {...}} dict. Used for
    enemy-set overrides (e.g. pin a specific enemy Kingambit's exact
    moveset), which have no 'pool' of their own since they only override
    a subset of whatever roster the enemy team already brings."""
    data = json.loads(Path(path).read_text())
    if "sets" in data and isinstance(data["sets"], dict):
        return data["sets"]
    return data


def describe_sets(pool, sets, merged=None):
    """Human-readable summary lines for a loaded team sheet."""
    lines = []
    for n in pool:
        spec = sets.get(n) or {}
        item = spec.get("item")
        moves = spec.get("moves")
        if item or moves:
            lines.append(f"  {n:<20}{str(item or '(usage default)'):<18}"
                          f"{', '.join(moves) if moves else '(usage default moves)'}")
        else:
            lines.append(f"  {n:<20}(usage defaults)")
    return lines
