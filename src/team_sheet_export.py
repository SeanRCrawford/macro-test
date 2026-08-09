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


def build_sheets(rosters, merged, optimised=False):
    """{name: [ {pokemon, types, item, ability, nature, evs, moves}, ... ]}."""
    out = {}
    for name, members in rosters.items():
        rows = []
        for r in team_items(list(members), merged):
            ev = r.get("evs") or {}
            rows.append({
                "pokemon": r["name"],
                "types": r.get("types"),
                "item": r.get("item"),
                "ability": r.get("ability"),
                "nature": r.get("nature"),
                "evs": "/".join(str(ev.get(k, 0)) for k in
                                ("hp", "atk", "def", "spa", "spd", "spe")),
                "moves": list(r.get("moves") or []),
                "score": r.get("score"),
                "sets": "optimised" if optimised else "usage defaults",
            })
        out[name] = rows
    return out


def write_team_sheets(rosters, merged, path, optimised=False):
    import json
    sheets = build_sheets(rosters, merged, optimised=optimised)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(sheets, fh, indent=2)
    return sheets
