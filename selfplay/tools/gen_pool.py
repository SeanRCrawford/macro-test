"""Build selfplay/data/regmc_pool.json, the Reg M-C legal pool, from Smogon's
monthly usage stats (data/smogon_stats/, downloaded from smogon.com/stats).

Rules, as given by the user:
  - Species: everything in the leads file is legal (it lists every species
    used; the moveset file only details the ones above a usage cut-off), plus
    anything in the moveset file. Zoroark and Zoroark-Hisui never appear as
    leads because Illusion logs them as the Pokemon they disguise as.
  - Items: an item is legal only if some species' moveset entry names it.
    Any legal item may be held by any species; a Mega Stone on the wrong
    species simply does nothing.
  - Moves and abilities: the moveset file shows each species' common ones; the
    tail is folded into "Other", so these lists are what's used, not the full
    legal set.

Every name is checked against data/dex.json so a typo or a forme the dex
lacks fails here rather than inside the engine. Regenerate with:

    python selfplay/tools/gen_pool.py
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS_DIR = ROOT / "data" / "smogon_stats"
FORMAT = "gen9championsvgc2026regmc-1760"
OUT = ROOT / "data" / "regmc_pool.json"

STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"]


def to_id(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())


def parse_leads(text):
    """[(species, usage_pct)] from the leads table."""
    rows = []
    for line in text.splitlines():
        m = re.match(r"\|\s*\d+\s*\|\s*(.+?)\s*\|\s*([\d.]+)%", line)
        if m:
            rows.append((m.group(1), float(m.group(2))))
    return rows


def parse_moveset(text):
    """{species: {raw_count, abilities, items, spreads, moves, teammates}}.

    Each species is a run of boxes separated by +---+ lines: a name box, a
    raw-count box, then one box per section whose first line is its title.
    Percentages are of that species' raw count; "Other" rows are dropped.
    """
    out = {}
    boxes, cur = [], []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("+"):
            if cur:
                boxes.append(cur)
            cur = []
        elif line.startswith("|"):
            cur.append(line.strip("|").strip())
    if cur:
        boxes.append(cur)

    species = None
    for box in boxes:
        if len(box) == 1 and not box[0].startswith(("Raw count", "Avg.")):
            species = box[0]
            out[species] = {}
            continue
        title = box[0]
        if title.startswith("Raw count"):
            out[species]["raw_count"] = int(title.split(":")[1])
            continue
        key = title.lower().replace(" ", "_")
        if key not in ("abilities", "items", "spreads", "moves", "teammates"):
            continue  # e.g. "Checks and Counters", usually empty
        entries = []
        for row in box[1:]:
            m = re.match(r"(.+?)\s+([\d.]+)%$", row)
            if not m or m.group(1) in ("Other", "Nothing"):  # "Nothing": an empty slot
                continue
            entries.append((m.group(1), float(m.group(2))))
        out[species][key] = entries
    return out


def parse_spread(s):
    nature, pts = s.split(":")
    points = [int(x) for x in pts.split("/")]
    assert len(points) == 6, s
    return nature, points


def main():
    leads = parse_leads((STATS_DIR / f"leads-{FORMAT}.txt").read_text())
    moveset = parse_moveset((STATS_DIR / f"moveset-{FORMAT}.txt").read_text())
    dex = json.loads((ROOT / "data" / "dex.json").read_text())

    problems = []

    def check(kind, name, table):
        if to_id(name) not in table:
            problems.append(f"{kind} not in dex: {name}")

    lead_names = {n for n, _ in leads}
    leads = leads + [(n, 0.0) for n in moveset if n not in lead_names]
    species_out = {}
    for name, usage in leads:
        check("species", name, dex["species"])
        entry = {"name": name, "lead_usage_pct": usage}
        detail = moveset.get(name)
        if detail:
            entry["raw_count"] = detail["raw_count"]
            entry["abilities"] = detail["abilities"]
            entry["items"] = detail["items"]
            entry["moves"] = detail["moves"]
            entry["teammates"] = detail["teammates"]
            entry["spreads"] = []
            for s, pct in detail["spreads"]:
                nature, points = parse_spread(s)
                check("nature", nature, dex["natures"])
                if max(points) > 32 or sum(points) > 66:
                    problems.append(f"{name}: spread over the stat-point cap: {s}")
                entry["spreads"].append([nature, points, pct])
            for m, _ in detail["moves"]:
                check("move", m, dex["moves"])
        species_out[to_id(name)] = entry

    items = sorted({i for e in moveset.values() for i, _ in e["items"]})
    moves = sorted({m for e in moveset.values() for m, _ in e["moves"]})
    abilities = sorted({a for e in moveset.values() for a, _ in e["abilities"]})

    if problems:
        raise SystemExit("\n".join(problems))

    pool = {
        "format": FORMAT,
        "source": "smogon.com/stats/2026-09 leads + moveset",
        "stat_order": STAT_ORDER,
        "species": species_out,
        "items": items,
        "moves": moves,
        "abilities": abilities,
    }
    OUT.write_text(json.dumps(pool, indent=1) + "\n", encoding="utf-8")
    detailed = sum("spreads" in e for e in species_out.values())
    print(f"wrote {OUT}: {len(species_out)} species ({detailed} with moveset detail), "
          f"{len(items)} items, {len(moves)} moves, {len(abilities)} abilities")


if __name__ == "__main__":
    main()
