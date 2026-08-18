"""
Loads Showdown's static gen9 data (base stats, types, move mechanics, natures,
type chart) bundled with the poke-env package, and merges it with your own
mbsmogon.xlsx (nature/EVs/moves/items/abilities usage) + roster.csv (Score +
defensive type chart) into one clean per-Pokemon-set record.

Nothing here calls the network at runtime -- all source data is local.
"""
import json
import re
import pandas as pd
from pathlib import Path
import poke_env

POKE_ENV_STATIC = Path(poke_env.__file__).parent / "data" / "static"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

TYPES = ["Normal", "Fire", "Water", "Electric", "Grass", "Ice", "Fighting", "Poison",
         "Ground", "Flying", "Psychic", "Bug", "Rock", "Ghost", "Dragon", "Dark",
         "Steel", "Fairy"]


def _norm(name: str) -> str:
    """Normalize a Pokemon/move/item/ability name to Showdown's id format."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


_STATIC_CACHE = None


def load_showdown_static():
    """Loads (and caches) Showdown's static gen9 tables. Cached because these
    are large JSON files and were previously re-parsed on every Mega
    combatant construction -- which dominated search runtime."""
    global _STATIC_CACHE
    if _STATIC_CACHE is None:
        pokedex = json.load(open(POKE_ENV_STATIC / "pokedex" / "gen9pokedex.json"))
        moves = json.load(open(POKE_ENV_STATIC / "moves" / "gen9moves.json"))
        natures = json.load(open(POKE_ENV_STATIC / "natures.json"))
        typechart = json.load(open(POKE_ENV_STATIC / "typechart" / "gen9typechart.json"))
        _STATIC_CACHE = (pokedex, moves, natures, typechart)
    return _STATIC_CACHE


# Manual id -> canonical-name overrides for the mons in mbsmogon.xlsx whose
# Showdown species id doesn't match a naive normalize() of the display name.
SPECIES_ID_OVERRIDES = {
    "megacharizardy": "charizardmegay",
    "megacharizardx": "charizardmegax",
    "megamewtwoy": "mewtwomegay",
    "megamewtwox": "mewtwomegax",
}


def resolve_species(name: str, pokedex: dict):
    """Find the Showdown pokedex entry for a display name like 'Mega Charizard Y'."""
    raw_id = _norm(name)
    if raw_id in pokedex:
        return raw_id, pokedex[raw_id]

    if raw_id in SPECIES_ID_OVERRIDES:
        oid = SPECIES_ID_OVERRIDES[raw_id]
        if oid in pokedex:
            return oid, pokedex[oid]

    # "Mega X" -> "xmega"
    if name.lower().startswith("mega "):
        base = name[5:]
        cand = _norm(base) + "mega"
        if cand in pokedex:
            return cand, pokedex[cand]

    # "Mega X Y" (e.g. Mega Charizard Y) -> "xmegay"
    parts = name.split()
    if len(parts) >= 3 and parts[0].lower() == "mega" and len(parts[-1]) == 1:
        base = " ".join(parts[1:-1])
        cand = _norm(base) + "mega" + parts[-1].lower()
        if cand in pokedex:
            return cand, pokedex[cand]

    return None, None


def load_mbsmogon():
    df = pd.read_excel(DATA_DIR / "mbsmogon.xlsx")

    def parse_pct_list(cell):
        """'Dragon Claw 92.615%; Rock Slide 81.885%; ...' -> [(name, pct), ...]"""
        if pd.isna(cell):
            return []
        out = []
        for chunk in str(cell).split(";"):
            chunk = chunk.strip()
            m = re.match(r"^(.*?)\s+([\d.]+)%$", chunk)
            if m:
                label = m.group(1).strip()
                if label.lower() in ("items", "abilities", "moves"):
                    continue
                out.append((label, float(m.group(2))))
        return out

    records = {}
    duplicates = {}
    for _, row in df.iterrows():
        pname = row["Pokemon"]
        rec = {
            "name": pname,
            "nature": row["Nature"],
            "evs": {
                "hp": int(row["HP EV"]), "atk": int(row["Atk EV"]), "def": int(row["Def EV"]),
                "spa": int(row["SpAtk EV"]), "spd": int(row["SpDef EV"]), "spe": int(row["Spe EV"]),
            },
            "moves_usage": parse_pct_list(row["Moves"]),
            "items_usage": parse_pct_list(row["Items"]),
            "abilities_usage": parse_pct_list(row["Abilities"]),
        }
        if pname in records:
            duplicates.setdefault(pname, []).append(rec)
        else:
            records[pname] = rec

    # Some "Mega X" names appear TWICE in the sheet: one row is the real Mega
    # (holding its Mega Stone, with the Mega-exclusive ability), the other is
    # that species' BASE form mislabeled with the Mega name (ordinary item,
    # base ability). Previously the second row silently overwrote the first,
    # which lost the stone AND swapped in the wrong Nature/EVs/moves.
    # Resolve it properly: keep the stone-holding row under "Mega X", and file
    # the other under the base species name (if that name has no row already),
    # so it can be used for the pre-transform form.
    def _has_stone(rec):
        return any((i.lower().endswith(("ite", "ite x", "ite y", "itex", "itey")) and pct >= 50.0)
                   for i, pct in rec["items_usage"])

    for pname, extras in duplicates.items():
        all_rows = [records[pname]] + extras
        mega_rows = [r for r in all_rows if _has_stone(r)]
        other_rows = [r for r in all_rows if not _has_stone(r)]
        if mega_rows:
            records[pname] = mega_rows[0]
        if pname.startswith("Mega ") and other_rows:
            base = pname[5:]
            parts = base.split()
            if len(parts) >= 2 and parts[-1] in ("X", "Y"):
                base = " ".join(parts[:-1])
            if base not in records:
                rec = dict(other_rows[0])
                rec["name"] = base
                records[base] = rec

    return records, duplicates


def load_roster():
    df = pd.read_csv(DATA_DIR / "roster.csv", header=1)
    out = {}
    for _, row in df.iterrows():
        out[row["Name"]] = {
            "score": float(row["Score"]),
            "defensive_chart": {t: float(row[t]) for t in TYPES},
        }
    return out


def load_teams(with_meta=False, merged=None):
    """Rosters from teams.csv, plus any custom teams dropped into data/teams/*.txt
    as raw Showdown-export pastes (pokepast.es "Export" text) -- a reusable way
    to add a specific known enemy team (exact sets and all) alongside the
    usage-derived teams.csv entries, without editing teams.csv by hand.

    with_meta=True also returns {team: {"lead": [a, b] or None, "note": str,
    "sets": {name: {...}} or None}}. A team may declare a FIXED LEAD (e.g.
    "Incineroar+Farigiraf") when its whole plan depends on one specific
    opening -- searching all 15 lead pairs for such a team wastes time on
    brings it would never make. "sets" carries exact item/ability/nature/EVs/
    moves overrides for a custom pasted team (None for ordinary teams.csv rows,
    which use usage-derived defaults).

    merged: pass species_data.build_merged_dataset()'s `merged` dict to enable
    parsing data/teams/*.txt (needed to resolve "Species + Stone item" into
    this codebase's "Mega Species" roster names). Without it, custom team
    files are skipped -- only teams.csv is loaded.
    """
    df = pd.read_csv(DATA_DIR / "teams.csv")
    teams, meta = {}, {}
    for _, row in df.iterrows():
        members = [row[c] for c in ["1", "2", "3", "4", "5", "6"] if pd.notna(row[c])]
        name = row["Team"]
        teams[name] = members
        lead = None
        raw = row.get("Lead") if "Lead" in df.columns else None
        if isinstance(raw, str) and raw.strip():
            parts = [p.strip() for p in raw.replace("/", "+").split("+") if p.strip()]
            if len(parts) == 2 and all(p in members for p in parts):
                lead = parts
        note = row.get("Note") if "Note" in df.columns else ""
        meta[name] = {"lead": lead, "note": note if isinstance(note, str) else "", "sets": None}

    teams_dir = DATA_DIR / "teams"
    if merged is not None and teams_dir.is_dir():
        for f in sorted(teams_dir.glob("*.txt")):
            text = f.read_text()
            if not text.strip():
                continue
            names, sets = custom_team_from_export(text, merged)
            if not names:
                continue
            team_name = f.stem.replace("_", " ").replace("-", " ").title()
            teams[team_name] = names
            meta[team_name] = {"lead": None, "note": f"Custom team ({f.name})", "sets": sets}

    return (teams, meta) if with_meta else teams


def parse_showdown_export(text: str) -> list[dict]:
    """Parse Showdown export-format text (pokepast.es "Export" / "Paste" view)
    into a list of per-mon dicts: {"species", "item", "ability", "nature",
    "evs": {hp,atk,def,spa,spd,spe} or None, "moves": [...]}. Blank lines
    separate mons; unrecognised lines (Level/Shiny/Tera Type/IVs/...) are
    ignored since this tool doesn't model them."""
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]
    stat_keys = {"hp": "hp", "atk": "atk", "def": "def", "spa": "spa", "spd": "spd", "spe": "spe"}
    mons = []
    for block in blocks:
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue
        mon = {"species": None, "item": None, "ability": None, "nature": None,
               "evs": None, "moves": []}
        m = re.match(r"^(.*?)\s*@\s*(.+)$", lines[0])
        species_part = m.group(1).strip() if m else lines[0].strip()
        if m:
            mon["item"] = m.group(2).strip()
        gm = re.match(r"^(.*?)\s*\([MF]\)$", species_part)
        if gm:
            species_part = gm.group(1).strip()
        # "Nickname (Species)" form -- keep only the parenthesised real species.
        nm = re.match(r"^.+\(([^()]+)\)$", species_part)
        if nm:
            species_part = nm.group(1).strip()
        mon["species"] = species_part
        for line in lines[1:]:
            if line.startswith("Ability:"):
                mon["ability"] = line.split(":", 1)[1].strip()
            elif line.startswith("EVs:"):
                evs = {"hp": 0, "atk": 0, "def": 0, "spa": 0, "spd": 0, "spe": 0}
                for part in line.split(":", 1)[1].split("/"):
                    pm = re.match(r"^\s*(\d+)\s+(\w+)\s*$", part)
                    if pm and pm.group(2).lower() in stat_keys:
                        evs[stat_keys[pm.group(2).lower()]] = int(pm.group(1))
                mon["evs"] = evs
            elif line.endswith("Nature"):
                mon["nature"] = line.split()[0].strip()
            elif line.startswith("- "):
                mon["moves"].append(line[2:].strip())
        if mon["species"]:
            mons.append(mon)
    return mons


def custom_team_from_export(text: str, merged: dict) -> tuple[list, dict]:
    """Turn parsed Showdown-export mons into (roster_names, sets) in this
    codebase's convention: a base species holding its Mega Stone becomes the
    "Mega X" roster name (matching how teams.csv/mbsmogon.xlsx name Megas),
    and per-mon sets carry only the fields the paste actually specified so
    everything else still falls back to usage-derived defaults."""
    mons = parse_showdown_export(text)
    names, sets = [], {}
    for mon in mons:
        species, item = mon["species"], mon["item"]
        name = species
        if item:
            for cand in (f"Mega {species}", f"Mega {species} X", f"Mega {species} Y"):
                if cand in merged and find_mega_stone(cand, merged) == item:
                    name = cand
                    break
        spec = {}
        if mon["item"]:
            spec["item"] = mon["item"]
        if mon["ability"]:
            spec["ability"] = mon["ability"]
        if mon["nature"]:
            spec["nature"] = mon["nature"]
        if mon["evs"]:
            spec["evs"] = mon["evs"]
        if mon["moves"]:
            spec["moves"] = mon["moves"]
        names.append(name)
        sets[name] = spec
    return names, sets


def fixed_lead(team_name, meta):
    """The declared lead for a team, or None if it may open any way."""
    return (meta.get(team_name) or {}).get("lead")


_KNOWN_ITEMS = None


def _known_items():
    """Every item anything in the dataset is recorded holding, lowercased.

    Used to tell an item from a move inside a preferences bracket, so the file
    does not need two different syntaxes for the two things.
    """
    global _KNOWN_ITEMS
    if _KNOWN_ITEMS is None:
        mb, _dupes = load_mbsmogon()
        _KNOWN_ITEMS = {i.lower() for rec in mb.values()
                        for i, _pct in (rec.get("items_usage") or []) if i}
        _KNOWN_ITEMS |= {"safety goggles", "life orb", "leftovers",
                         "focus sash", "choice scarf", "choice band",
                         "choice specs", "assault vest", "sitrus berry"}
    return _KNOWN_ITEMS


def parse_preference_entry(entry):
    """"Mamoswine (Life Orb)" -> ("Mamoswine", {"item": "Life Orb"}).

    A bracket pins what that Pokemon actually runs, so a preference can say
    "this Pokemon, with THIS set" rather than only naming a species. Everything
    in the brackets is comma-separated; each part is an ITEM if the dataset has
    ever seen it as one, and a MOVE otherwise -- so both of these work and
    neither needs a special marker:

        Mamoswine (Life Orb)
        Mega Gengar (Substitute, Protect, Shadow Ball, Sludge Bomb)
        Mega Gengar (Life Orb, Substitute, Protect, Shadow Ball)

    Returns (name, spec) where spec is {} for a plain entry, and carries "item"
    and/or "moves" otherwise. A pinned set OVERRIDES the optimiser -- it is a
    statement about what you are bringing, not a suggestion.
    """
    text = str(entry).strip()
    if "(" not in text or not text.endswith(")"):
        return text, {}
    name, _, inside = text.partition("(")
    name = name.strip()
    parts = [p.strip() for p in inside[:-1].split(",") if p.strip()]
    spec, moves = {}, []
    for part in parts:
        if part.lower() in _known_items():
            spec["item"] = part
        else:
            moves.append(part)
    if moves:
        spec["moves"] = moves
    return name, spec


def load_preferences():
    """include / exclude / prefer, plus the sets any of them pinned.

    `sets` is {name: {"item":..., "moves":[...]}} in the same shape the rest of
    the tool uses for set overrides, so a pinned preference travels exactly like
    an optimised set does.
    """
    df = pd.read_csv(DATA_DIR / "preferences.csv")
    out, sets = {}, {}
    for column, key in (("Include", "include"), ("Exclude", "exclude"),
                        ("Prefer", "prefer")):
        names = []
        for raw in df[column].dropna().tolist():
            name, spec = parse_preference_entry(raw)
            names.append(name)
            if spec:
                sets[name] = {**sets.get(name, {}), **spec}
        out[key] = names
    out["sets"] = sets
    return out


def build_merged_dataset():
    pokedex, moves, natures, typechart = load_showdown_static()
    mb, duplicates = load_mbsmogon()
    roster = load_roster()

    merged = {}
    unresolved = []
    for name, rec in mb.items():
        sid, sdata = resolve_species(name, pokedex)
        if sdata is None:
            unresolved.append(name)
            continue
        rost = roster.get(name)
        merged[name] = {
            "name": name,
            "species_id": sid,
            "base_stats": sdata["baseStats"],
            "types": sdata["types"],
            "weight_kg": sdata.get("weightkg"),
            "legal_abilities": sdata.get("abilities", {}),
            "nature": rec["nature"],
            "evs": rec["evs"],
            "moves_usage": rec["moves_usage"],
            "items_usage": rec["items_usage"],
            "abilities_usage": rec["abilities_usage"],
            "score": rost["score"] if rost else None,
            "defensive_chart": rost["defensive_chart"] if rost else None,
        }

    merged["_duplicates"] = duplicates  # surfaced by callers that want to warn
    dup = merged.pop("_duplicates")
    build_merged_dataset.last_duplicates = dup
    return merged, unresolved, moves, natures, typechart


def find_mega_stone(name: str, merged: dict) -> str | None:
    """Return this Pokemon's Mega Stone if its usage data actually shows one.

    Most "Mega X" rows in mbsmogon.xlsx list their stone at ~100% usage
    (Gyaradosite, Charizardite Y, ...). A few do NOT -- e.g. "Mega Floette"
    in this dataset shows Choice Scarf / Life Orb instead, which suggests
    that row is a differently-named form rather than a stone-holding Mega.
    Rather than forcing a stone that doesn't exist (and emitting a null
    item), this returns None in that case so the normal item rules apply.
    """
    rec = merged.get(name)
    if not rec:
        return None
    for item, pct in rec.get("items_usage", []):
        low = item.lower()
        if (low.endswith("ite") or low.endswith("ite x") or low.endswith("ite y")
                or low.endswith("itex") or low.endswith("itey")) and pct >= 50.0:
            return item
    return None


# National dex number ranges per generation (inclusive), standard and stable.
GENERATION_DEX_RANGES = [
    (1, 1, 151), (2, 152, 251), (3, 252, 386), (4, 387, 493),
    (5, 494, 649), (6, 650, 721), (7, 722, 809), (8, 810, 905),
    (9, 906, 1025),
]


def generation_for_dex_num(num: int) -> int | None:
    for gen, lo, hi in GENERATION_DEX_RANGES:
        if lo <= num <= hi:
            return gen
    return None


def species_generation(name: str, pokedex: dict) -> int | None:
    """Which generation this Pokemon's BASE species originally debuted in.

    A Mega Evolution or regional/other form (Arcanine-Hisui, Galarian
    Zigzagoon, ...) counts as its base species' generation, not the
    generation the form itself was introduced in -- Mega Lucario is gen 4
    (Lucario's generation), Arcanine-Hisui is gen 1 (Arcanine's) -- because
    Showdown's pokedex gives every form of a species the SAME national dex
    number as the base species, which this keys off directly.
    """
    _, sdata = resolve_species(name, pokedex)
    if not sdata:
        return None
    return generation_for_dex_num(sdata.get("num", 0))


def parse_generations(spec: str) -> set[int]:
    """'3' -> {3}; '1-5' -> {1,2,3,4,5}; '1,3,5' -> {1,3,5}; '1-3,7' -> {1,2,3,7}."""
    out = set()
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(chunk))
    return out


def build_generation_map(names, pokedex) -> dict:
    """{name: generation} for every name in `names` that resolves."""
    out = {}
    for n in names:
        gen = species_generation(n, pokedex)
        if gen is not None:
            out[n] = gen
    return out


def base_form_name(name: str) -> str | None:
    """'Mega Charizard Y' -> 'Charizard', 'Mega Skarmory' -> 'Skarmory'.
    Returns None if `name` isn't a Mega pick."""
    if not name.startswith("Mega "):
        return None
    rest = name[5:]
    parts = rest.split()
    if len(parts) >= 2 and parts[-1] in ("X", "Y"):
        return " ".join(parts[:-1])
    return rest


def resolve_team_mega_slot(team_names: list[str], mega_transforms: str | None = None) -> tuple[str | None, list[str]]:
    """A brought team (4 or 6) may include MULTIPLE mega-capable picks (this
    is legal and a real tech: e.g. bringing both a would-be Mega Charizard Y
    and a would-be Mega Tyranitar), but only ONE of them can actually Mega
    Evolve per game -- the others run the whole game in base form (which is
    often still useful: Tyranitar's Sand Stream works unevolved, only the
    stat/ability boost needs the stone).

    mega_transforms: explicitly which name should be the one that
    transforms (must be one of the Mega-named entries in team_names). If
    None, defaults to the FIRST Mega-named entry in list order (backward
    compatible default) -- pass this explicitly to search over which one
    transforms without disturbing lead/back position order.

    Returns (name_that_mega_evolves_or_None, list_of_names_forced_to_base_form).
    """
    megas = [n for n in team_names if n.startswith("Mega ")]
    if not megas:
        return None, []
    if mega_transforms is not None:
        if mega_transforms not in megas:
            raise ValueError(f"mega_transforms='{mega_transforms}' not among this team's Mega picks {megas}")
        return mega_transforms, [m for m in megas if m != mega_transforms]
    return megas[0], megas[1:]


def mega_variants(team_names: list[str]) -> list[str | None]:
    """All legal values for `mega_transforms` given this team -- one per
    Mega-named pick present, or [None] if there's 0 or 1 (no ambiguity to
    search over)."""
    megas = [n for n in team_names if n.startswith("Mega ")]
    return [None] if len(megas) <= 1 else list(megas)


if __name__ == "__main__":
    merged, unresolved, moves, natures, typechart = build_merged_dataset()
    print(f"Merged {len(merged)} / expected 272 Pokemon sets")
    print(f"Unresolved species (need manual override): {unresolved}")
