"""Generate selfplay/data/dex.json from the Showdown gen9 data bundled with poke-env.

The Rust engine embeds dex.json at compile time, so building the engine never
needs Python or poke-env, and a poke-env upgrade can only change engine data
through a reviewed diff of this file. Regenerate with:

    python selfplay/tools/gen_dex.py

Same source files as src/species_data.load_showdown_static(), so the engine and
the existing Python tools read identical numbers.
"""
import json
from importlib.metadata import version
from pathlib import Path

import poke_env

STATIC = Path(poke_env.__file__).parent / "data" / "static"
OUT = Path(__file__).resolve().parent.parent / "data" / "dex.json"

STATS = ["hp", "atk", "def", "spa", "spd", "spe"]

# Species fields the engine can use. Everything else (egg groups, colour,
# evolution data, height) has no battle effect.
SPECIES_KEEP = ["name", "num", "types", "baseStats", "abilities", "weightkg",
                "baseSpecies", "forme", "requiredItem", "requiredItems",
                "battleOnly", "changesFrom", "otherFormes"]

# Move fields with no battle effect.
MOVE_DROP = {"contestType", "desc", "shortDesc", "isNonstandard"}


def main():
    pokedex = json.load(open(STATIC / "pokedex" / "gen9pokedex.json"))
    moves = json.load(open(STATIC / "moves" / "gen9moves.json"))
    natures = json.load(open(STATIC / "natures.json"))
    typechart = json.load(open(STATIC / "typechart" / "gen9typechart.json"))

    species = {}
    for sid, entry in pokedex.items():
        if "baseStats" not in entry:
            continue  # cosmetic formes (Alcremie flavours etc.) battle as their base species
        if entry["num"] <= 0:
            continue  # MissingNo. and Create-a-Pokemon fakemons
        species[sid] = {k: entry[k] for k in SPECIES_KEEP if k in entry}
        species[sid]["baseStats"] = [entry["baseStats"][s] for s in STATS]

    out_moves = {mid: {k: v for k, v in entry.items() if k not in MOVE_DROP}
                 for mid, entry in moves.items()}

    out_natures = {}
    for nid, mults in natures.items():
        plus = [s for s in STATS[1:] if mults.get(s, 1) > 1]
        minus = [s for s in STATS[1:] if mults.get(s, 1) < 1]
        out_natures[nid] = {"plus": plus[0] if plus else None,
                            "minus": minus[0] if minus else None}

    # damageTaken codes as Showdown stores them: 0 neutral, 1 weak (2x),
    # 2 resist (0.5x), 3 immune. Keyed defending type -> attacking type.
    types = sorted(t.capitalize() for t in typechart)
    chart = {d.capitalize(): {a: typechart[d]["damageTaken"][a] for a in types}
             for d in typechart}

    dex = {
        "source": f"poke-env {version('poke-env')} gen9 static data",
        "types": types,
        "typechart": chart,
        "natures": out_natures,
        "species": species,
        "moves": out_moves,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(dex, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(species)} species, {len(out_moves)} moves, "
          f"{len(out_natures)} natures, {len(types)} types)")


if __name__ == "__main__":
    main()
