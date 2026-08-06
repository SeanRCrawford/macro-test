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


def load_teams(with_meta=False):
    """Rosters from teams.csv.

    with_meta=True also returns {team: {"lead": [a, b] or None, "note": str}}.
    A team may declare a FIXED LEAD (e.g. "Incineroar+Farigiraf") when its whole
    plan depends on one specific opening -- searching all 15 lead pairs for such
    a team wastes time on brings it would never make.
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
        meta[name] = {"lead": lead, "note": note if isinstance(note, str) else ""}
    return (teams, meta) if with_meta else teams


def fixed_lead(team_name, meta):
    """The declared lead for a team, or None if it may open any way."""
    return (meta.get(team_name) or {}).get("lead")


def load_preferences():
    df = pd.read_csv(DATA_DIR / "preferences.csv")
    return {
        "include": [x for x in df["Include"].dropna().tolist()],
        "exclude": [x for x in df["Exclude"].dropna().tolist()],
        "prefer": [x for x in df["Prefer"].dropna().tolist()],
    }


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
