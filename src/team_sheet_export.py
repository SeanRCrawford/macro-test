"""Team sheets for generated teams: what "gen03" actually is.

The shortlist that generation hands to the search is bare rosters -- six names
per team. The workbook then reports on "gen03" with no way to see which item,
nature, EVs or four moves that team is actually running, so a promising result
cannot be taken to a game without re-running generation to find out.

This writes the sheets alongside the shortlist, and the workbook renders them.
The moves come from the same `team_items` the interactive tools use, so what is
exported is exactly what was simulated -- if the sets were optimised, these are
the optimised sets; if they are usage defaults, they say so.
"""
from team_search import team_items


def build_sheets(rosters, merged, optimised=False, sets=None):
    """{name: [ {pokemon, types, item, ability, nature, evs, moves}, ... ]}.

    `sets` is {team: {pokemon: {"item":..., "moves":[...]}}} from
    optimize_sets. Where present it OVERRIDES the usage defaults, because the
    optimised set is what was simulated -- printing the default here would hand
    back a sheet that does not match the numbers it sits beside.
    """
    sets = sets or {}
    out = {}
    for name, members in rosters.items():
        chosen = sets.get(name) or {}
        rows = []
        for r in team_items(list(members), merged):
            spec = chosen.get(r["name"]) or {}
            # An edited stat-point spread is an override like any other, and
            # the whole point of this file is that it matches what was
            # simulated. Reading the dataset spread here printed a sheet that
            # disagreed with the numbers beside it.
            ev = spec.get("evs") or r.get("evs") or {}
            rows.append({
                "pokemon": r["name"],
                "types": r.get("types"),
                "item": spec.get("item") or r.get("item"),
                "ability": r.get("ability"),
                "nature": r.get("nature"),
                "evs": "/".join(str(ev.get(k, 0)) for k in
                                ("hp", "atk", "def", "spa", "spd", "spe")),
                "moves": list(spec.get("moves") or r.get("moves") or []),
                "score": r.get("score"),
                "sets": "optimised" if (optimised and spec) else "usage defaults",
            })
        out[name] = rows
    return out


def write_team_sheets(rosters, merged, path, optimised=False, sets=None):
    import json
    sheets = build_sheets(rosters, merged, optimised=optimised, sets=sets)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sheets, fh, indent=2)
    return sheets
