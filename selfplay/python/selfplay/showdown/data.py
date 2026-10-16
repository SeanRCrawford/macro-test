"""Static data for the Showdown client: the engine's dex (data/dex.json),
the Reg M-C usage pool (data/regmc_pool.json) and what the engine supports
(data/support.json).

The opponent's sets come from Open Team Sheets when both players accept
them; otherwise from usage: each species' most used item, ability, moves and
spread, corrected as the battle reveals them. Anything the engine doesn't
implement is swapped for the next most used option that it does, so the
engine can rebuild every position (the swap is reported once).
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parents[3] / "data"


def to_id(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


class Data:
    def __init__(self, root: Path = DATA):
        self.root = root
        self._corpus = None
        dex = json.loads((root / "dex.json").read_text(encoding="utf-8"))
        self.species = dex["species"]
        self.moves = dex["moves"]
        self.items = dex["items"]
        self.abilities = dex["abilities"]
        pool = json.loads((root / "regmc_pool.json").read_text(encoding="utf-8"))
        self.pool = pool["species"]
        support = json.loads((root / "support.json").read_text(encoding="utf-8"))
        self.supported_moves = set(support["moves"])
        self.supported_abilities = set(support["abilities"])
        self.supported_items = set(support["items"])

    # --- Lookups ----------------------------------------------------------

    def species_id(self, name: str) -> str:
        """The species id of a name or a protocol DETAILS string
        ("Salamence-Mega, L50, F")."""
        return to_id(name.split(",")[0])

    def base_of(self, species: str) -> str:
        """The out-of-battle species: a Mega's base forme
        (Salamence-Mega -> salamence), else the species itself."""
        s = self.species.get(species)
        if s and s.get("battleOnly"):
            only = s["battleOnly"]
            return to_id(only if isinstance(only, str) else only[0])
        return species

    def mega_of(self, species: str, item: str) -> str | None:
        """The Mega forme `species` becomes holding `item`, if any."""
        it = self.items.get(item) or {}
        stone = it.get("megaStone") or {}
        name = self.species.get(species, {}).get("name", "")
        forme = stone.get(name)
        return to_id(forme) if forme else None

    def is_mega(self, species: str) -> bool:
        """A Mega forme (permanent in Champions), not a temporary one."""
        s = self.species.get(species) or {}
        return bool(s.get("requiredItem")) and str(s.get("forme", "")).startswith("Mega")

    def num(self, species: str) -> int:
        return (self.species.get(species) or {}).get("num", -1)

    def max_pp(self, move: str) -> int:
        m = self.moves.get(move)
        if not m:
            return 1
        pp = m.get("pp", 1)
        return pp if m.get("noPPBoosts") else (pp // 5 + 1) * 4

    def is_choice(self, item: str) -> bool:
        return bool((self.items.get(item) or {}).get("isChoice"))

    def move_target(self, move: str) -> str:
        return (self.moves.get(move) or {}).get("target", "normal")

    # --- Guessing sets ----------------------------------------------------

    def usage(self, species: str) -> dict:
        """Usage for a species: the Reg M-C stats, or for one they miss,
        counts over the corpus teams."""
        u = self.pool.get(species) or self.pool.get(self.base_of(species))
        if u and u.get("moves"):
            return u
        return self.corpus_usage().get(self.base_of(species)) or u or {}

    def corpus_usage(self) -> dict:
        if self._corpus is None:
            from collections import Counter, defaultdict

            from selfplay.showdown.team import parse_paste
            counts: dict = defaultdict(lambda: {k: Counter() for k in ("items", "abilities", "moves", "spreads")})
            for path in sorted((self.root / "corpus").glob("*.txt")):
                try:
                    sets = parse_paste(path.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    continue
                for st in sets:
                    c = counts[self.base_of(st["species"])]
                    if st["item_name"]:
                        c["items"][st["item_name"]] += 1
                    if st["ability_name"]:
                        c["abilities"][st["ability_name"]] += 1
                    for mv in st["move_names"]:
                        c["moves"][mv] += 1
                    if st["nature"]:
                        c["spreads"][st["nature"]] += 1
            self._corpus = {
                sp: {"items": c["items"].most_common(), "abilities": c["abilities"].most_common(),
                     "moves": c["moves"].most_common(),
                     "spreads": [[n, None, k] for n, k in c["spreads"].most_common()]}
                for sp, c in counts.items()}
        return self._corpus

    def guess_set(self, species: str, item: str | None = None, ability: str | None = None,
                  moves: list[str] | None = None, nature: str | None = None,
                  taken_items=frozenset(), complete: bool = False) -> tuple[dict, list[str]]:
        """A full set for `species`: what is known, the rest from usage.
        `complete`: the moves are the whole moveset (Open Team Sheets), so
        only unsupported ones are replaced. Returns (the set as the engine's
        position takes it, what was replaced because the engine doesn't
        support it)."""
        u = self.usage(species)
        replaced = []
        sp = self.species.get(species, {})
        # Item: known, else the most used one not already on the team.
        if item is None:
            options = [to_id(n) for n, _ in u.get("items", [])]
            options = [i for i in options if i in self.supported_items and i not in taken_items]
            item = options[0] if options else ""
        elif item and item not in self.supported_items:
            replaced.append(f"{species} item {item}")
            item = ""
        # Ability: known, else the most used one.
        legal = [to_id(a) for a in sp.get("abilities", {}).values()]
        usual = [to_id(n) for n, _ in u.get("abilities", [])] + legal
        if not ability:
            options = [a for a in usual if a in self.supported_abilities]
            ability = options[0] if options else "noability"
        elif ability not in self.supported_abilities:
            replaced.append(f"{species} ability {ability}")
            options = [a for a in usual if a in self.supported_abilities]
            ability = options[0] if options else "noability"
        # Moves: the known ones, then the most used.
        known = list(dict.fromkeys(m for m in (moves or []) if m))
        out = []
        for m in known:
            if m in self.supported_moves:
                out.append(m)
            else:
                replaced.append(f"{species} move {m}")
        want = min(4, len(known)) if complete and known else 4
        for n, _ in u.get("moves", []):
            if len(out) >= want:
                break
            m = to_id(n)
            if m in self.supported_moves and m not in out:
                out.append(m)
        if not out:
            out = ["struggle"]
        # Nature (and with it the spread the engine picks): known, else the
        # most used (the Mega's, for a Mega Stone holder).
        if not nature:
            mega = self.mega_of(species, item) if item else None
            spreads = (self.pool.get(mega, {}).get("spreads") if mega else None) or u.get("spreads") or []
            nature = spreads[0][0] if spreads else "Serious"
        return ({"species": species, "item": item, "ability": ability, "nature": nature, "moves": out[:4]},
                replaced)


@lru_cache(maxsize=1)
def data() -> Data:
    return Data()
