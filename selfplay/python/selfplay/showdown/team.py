"""Showdown pastes: read one (for the tracker) and pack it (for /utm).

The engine parses the paste itself when it rebuilds a position, so these
only need the names, species, item, ability, moves and spread.
"""
from __future__ import annotations

import re

from selfplay.showdown.data import to_id

STATS = ["HP", "Atk", "Def", "SpA", "SpD", "Spe"]


def _blocks(text: str) -> list[list[str]]:
    blocks, cur = [], []
    for line in text.splitlines() + [""]:
        line = line.strip()
        if not line:
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line)
    return blocks


def parse_set(lines: list[str]) -> dict:
    head, _, item = lines[0].partition(" @ ")
    head = head.strip()
    gender = ""
    m = re.search(r" \((M|F)\)$", head)
    if m:
        gender = m.group(1)
        head = head[:m.start()].strip()
    m = re.match(r"^(.*) \(([^()]+)\)$", head)
    name, species = (m.group(1).strip(), m.group(2).strip()) if m else (head, head)
    s = {"name": name, "species_name": species, "species": to_id(species), "item": to_id(item),
         "item_name": item.strip(), "ability": "", "ability_name": "", "nature": "", "evs": None,
         "ivs": None, "moves": [], "move_names": [], "gender": gender, "level": 50}
    for line in lines[1:]:
        if line.startswith("Ability:"):
            s["ability_name"] = line[8:].strip()
            s["ability"] = to_id(s["ability_name"])
        elif line.startswith("EVs:") or line.startswith("IVs:"):
            vals = [0] * 6 if line.startswith("EVs:") else [31] * 6
            for part in line[4:].split("/"):
                p = part.strip().split()
                if len(p) == 2 and p[1] in STATS:
                    vals[STATS.index(p[1])] = int(p[0])
            s["evs" if line.startswith("EVs:") else "ivs"] = vals
        elif line.endswith(" Nature"):
            s["nature"] = line[:-7].strip()
        elif line.startswith("Level:"):
            s["level"] = int(line[6:].strip() or 50)
        elif line.startswith("-") or line.startswith("~"):
            mv = line[1:].strip()
            s["move_names"].append(mv)
            s["moves"].append(to_id(mv))
    return s


def parse_paste(text: str) -> list[dict]:
    return [parse_set(b) for b in _blocks(text)]


def pack_team(text: str) -> str:
    """A paste in Showdown's packed format, for /utm."""
    out = []
    for s in parse_paste(text):
        evs = ",".join("" if not v else str(v) for v in (s["evs"] or [0] * 6))
        ivs = ",".join("" if v == 31 else str(v) for v in s["ivs"]) if s["ivs"] else ""
        species = "" if s["name"] == s["species_name"] else s["species_name"]
        out.append("|".join([
            s["name"], species, s["item_name"], s["ability_name"], ",".join(s["move_names"]), s["nature"],
            evs if evs.strip(",") else "", s["gender"], ivs if ivs.strip(",") else "", "",
            "" if s["level"] == 100 else str(s["level"]), "",
        ]))
    return "]".join(out)
