"""Follow a Showdown battle from one player's protocol stream and build the
engine position (engine/src/battle/position.rs) at each of its decisions.

Our side is exact: the requests give HP, items, abilities, PP and the order
of the Pokemon, and our team is our own paste. The opponent's side is what
the log shows: HP as a percentage, the Pokemon that have appeared, the
moves, items and abilities revealed so far, every boost and condition. The
rest comes from Open Team Sheets when both players accepted them, or from
usage (showdown/data.py). Durations Showdown doesn't print (weather,
screens, Taunt...) are counted from when they started.

    t = Tracker(our_paste)
    for line in lines:
        t.feed(line)          # protocol lines, including |request|
        if t.request is not None and t.needs_decision():
            position = t.position()

tests/test_showdown.py checks the tracker against Showdown's own state over
the battles in tests/fixtures/protocol.json.gz.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from selfplay.showdown.data import Data, data, to_id

BOOSTS = ["atk", "def", "spa", "spd", "spe", "accuracy", "evasion"]

# -start / -end effects -> the engine's volatile ids.
VOLATILES = {
    "confusion": "confusion", "substitute": "substitute", "encore": "encore", "taunt": "taunt",
    "disable": "disable", "yawn": "yawn", "leechseed": "leechseed", "healblock": "healblock",
    "throatchop": "throatchop", "saltcure": "saltcure", "focusenergy": "focusenergy",
    "dragoncheer": "dragoncheer", "charge": "charge", "imprison": "imprison", "smackdown": "smackdown",
    "curse": "curse", "noretreat": "noretreat", "flashfire": "flashfire", "minimize": "minimize",
    "destinybond": "destinybond", "glaiverush": "glaiverush", "partiallytrapped": "partiallytrapped",
}
# How long each lasts as it starts (the engine's VolatileId::duration).
DURATIONS = {"encore": 3, "taunt": 3, "disable": 5, "yawn": 2, "healblock": 2, "throatchop": 2,
             "perishsong": 4, "partiallytrapped": 5, "stall": 2, "mustrecharge": 2, "twoturnmove": 2}
# Field effects' turns, and the items that extend them.
WEATHERS = {"raindance": ("raindance", "damprock"), "sunnyday": ("sunnyday", "heatrock"),
            "sandstorm": ("sandstorm", "smoothrock"), "snowscape": ("snowscape", "icyrock"),
            "snow": ("snowscape", "icyrock"), "hail": ("snowscape", "icyrock")}
TERRAINS = {"electricterrain", "grassyterrain", "mistyterrain", "psychicterrain"}
SCREENS = {"reflect", "lightscreen", "auroraveil"}
SIDE_TURNS = {"tailwind": 4, "reflect": 5, "lightscreen": 5, "auroraveil": 5}
HAZARDS = {"stealthrock", "spikes", "toxicspikes", "stickyweb"}
TWO_TURN = {"electroshot", "meteorbeam", "solarbeam", "solarblade", "phantomforce", "shadowforce",
            "fly", "bounce", "dig", "dive"}
SEMI_INVULNERABLE = {"phantomforce", "shadowforce", "fly", "bounce", "dig", "dive"}


def effect_id(text: str) -> str:
    """'move: Taunt' -> 'taunt'."""
    for prefix in ("move: ", "ability: ", "item: "):
        if text.startswith(prefix):
            text = text[len(prefix):]
    return to_id(text)


def parse_ident(ident: str) -> tuple[str, int | None, str]:
    """'p2a: Indeedee' -> ('p2', 0, 'Indeedee'); 'p1: X' -> ('p1', None, 'X')."""
    head, _, name = ident.partition(": ")
    side = head[:2]
    slot = "ab".index(head[2]) if len(head) > 2 and head[2] in "ab" else None
    return side, slot, name.strip()


def parse_condition(text: str) -> tuple[int, int, str]:
    """'120/196 par' -> (120, 196, 'par'); '0 fnt' -> (0, 0, 'fnt')."""
    parts = text.split()
    status = parts[1] if len(parts) > 1 else ""
    if parts[0].endswith("fnt") or parts[0] == "0":
        return 0, 0, "fnt"
    hp, _, mx = parts[0].partition("/")
    # Champions marks 20% and 50% with the HP bar's colour: "20/100y".
    mx = mx.rstrip("gyr")
    return int(float(hp)), int(float(mx or hp)), status


def unpack_team(packed: str) -> list[dict]:
    """Showdown's packed team format (|showteam|) -> set dicts."""
    sets = []
    for entry in packed.split("]"):
        f = entry.split("|")
        if len(f) < 6:
            continue
        name, species = f[0], f[1] or f[0]
        sets.append({"species": to_id(species), "item": to_id(f[2]), "ability": to_id(f[3]),
                     "moves": [to_id(m) for m in f[4].split(",") if m], "nature": f[5] or ""})
    return sets


@dataclass
class Mon:
    uid: int
    species: str
    base_species: str
    # Our own: HP in points. The opponent's: HP as a percentage.
    hp: float = 100.0
    max_hp: int = 100
    exact: bool = False
    status: str = ""
    sleep_turns: int = 0
    toxic_stage: int = 0
    boosts: list = field(default_factory=lambda: [0] * 7)
    item: str | None = None        # None: unknown
    last_item: str = ""
    ability: str | None = None     # None: unknown
    base_ability: str | None = None
    moves: list = field(default_factory=list)   # move ids as known
    pp_used: dict = field(default_factory=dict)
    pp: dict = field(default_factory=dict)      # exact PP (our actives, from requests)
    disabled: dict = field(default_factory=dict)
    volatiles: dict = field(default_factory=dict)
    types: list | None = None
    active: bool = False
    slot: int | None = None
    fainted: bool = False
    revealed: bool = False
    last_move: str | None = None
    last_move_target_loc: int = 0
    moved_this_turn: bool = False
    active_turns: int = 0
    move_actions: int = 0
    times_attacked: int = 0
    fallen: int = 0
    syrup: bool = False
    names: set = field(default_factory=set)


class Side:
    def __init__(self, sid: str):
        self.id = sid
        self.name = ""
        self.sets: list[dict] = []        # the six as known (species at least)
        self.complete = False             # sets known in full (ours, or Open Team Sheets)
        self.mons: list[Mon] = []
        self.order: list[int] = []        # brought, by position (uids); actives first
        self.team_size = 4
        self.conditions: dict = {}        # id -> turns left or layers
        self.mega_used = False
        self.total_fainted = 0
        self.fainted_this_turn = False
        self.fainted_last_turn = False

    def by_name(self, name: str, species: str | None = None) -> Mon | None:
        for m in self.mons:
            if name in m.names:
                return m
        if species is not None:
            d = data()
            want = d.base_of(species)
            for m in self.mons:
                if d.base_of(m.species) == want or d.base_of(m.base_species) == want:
                    m.names.add(name)
                    return m
        return None

    def active(self, slot: int) -> Mon | None:
        for m in self.mons:
            if m.active and m.slot == slot:
                return m
        return None


class Tracker:
    def __init__(self, our_team: list[dict] | str | None = None, our_side: str | None = None,
                 d: Data | None = None):
        """`our_team`: our six sets, as position set dicts or a paste (the
        order we uploaded). `our_side`: 'p1' or 'p2' if known (else the
        first request tells)."""
        self.d = d or data()
        self.our_team = our_team
        self.us: str | None = our_side
        self.sides = {"p1": Side("p1"), "p2": Side("p2")}
        self.turn = 0
        self.upkeep_seen = False          # the residual of this turn has run
        self.request: dict | None = None
        self.rqid = None
        self.field = {"weather": "", "weather_turns": 0, "terrain": "", "terrain_turns": 0,
                      "trick_room": 0, "gravity": 0}
        self.ots = False
        self.winner: str | None = None
        self.ended = False
        self.warnings: list[str] = []
        self._last_user: Mon | None = None
        self._last_move: str | None = None
        self._stalling: Mon | None = None

    # --- Sides --------------------------------------------------------------

    @property
    def mine(self) -> Side:
        return self.sides[self.us or "p1"]

    @property
    def foe(self) -> Side:
        return self.sides["p2" if (self.us or "p1") == "p1" else "p1"]

    def mon(self, ident: str, details: str | None = None) -> Mon | None:
        sid, slot, name = parse_ident(ident)
        side = self.sides.get(sid)
        if side is None:
            return None
        # Our own active Pokemon are known by slot (an Illusion user shows
        # under another's name).
        if side is self.mine and slot is not None and side.active(slot) is not None:
            return side.active(slot)
        species = self.d.species_id(details) if details else None
        m = side.by_name(name, species)
        if m is None and slot is not None:
            m = side.active(slot)
        return m

    def _setup_side(self, side: Side, sets: list[dict], complete: bool):
        side.sets = sets
        side.complete = complete
        if not side.mons:
            side.mons = [Mon(uid=i, species=s["species"], base_species=s["species"]) for i, s in enumerate(sets)]
        for m, s in zip(side.mons, sets):
            if complete:
                m.item = s.get("item", "")
                m.ability = s.get("ability")
                m.base_ability = m.ability
                m.moves = list(s.get("moves", []))

    def _our_sets(self) -> list[dict]:
        t = self.our_team
        if isinstance(t, str):
            from selfplay.showdown.team import parse_paste
            t = parse_paste(t)
        return [dict(s) for s in t]

    def _setup_our_side(self):
        sets = self._our_sets()
        self._setup_side(self.mine, sets, True)
        for m, s in zip(self.mine.mons, sets):
            if s.get("name"):
                m.names.add(s["name"])

    def _team_json(self, side: Side):
        """Our team as the engine should read it: the paste itself."""
        if side is self.mine and isinstance(self.our_team, str):
            return self.our_team
        return None

    # --- Feeding ------------------------------------------------------------

    def feed(self, line: str) -> None:
        if not line.startswith("|"):
            return
        parts = line[1:].split("|")
        kind, args = parts[0], parts[1:]
        name = "_on_minus_" + kind[1:] if kind.startswith("-") else "_on_" + kind
        handler = getattr(self, name, None)
        if handler is not None:
            try:
                handler(args)
            except Exception as e:  # keep following the battle; report it
                self.warnings.append(f"{line[:120]}: {type(e).__name__}: {e}")

    def feed_all(self, lines) -> None:
        for line in lines:
            self.feed(line)

    def _on_player(self, a):
        if len(a) >= 2:
            self.sides[a[0]].name = a[1]

    def _on_teamsize(self, a):
        self.sides[a[0]].team_size = int(a[1])

    def _on_poke(self, a):
        side = self.sides[a[0]]
        species = self.d.species_id(a[1])
        if side.id == self.us and not side.mons and self.our_team is not None:
            self._setup_our_side()
        if side.id == self.us or side.complete:
            return
        side.sets.append({"species": species})
        side.mons.append(Mon(uid=len(side.mons), species=species, base_species=species))

    def _on_clearpoke(self, a):
        for side in self.sides.values():
            if side.id != self.us:
                side.sets, side.mons = [], []

    def _on_showteam(self, a):
        side = self.sides[a[0]]
        if side.id == self.us:
            return
        self.ots = True
        sets = unpack_team("|".join(a[1:]))
        side.mons = []
        self._setup_side(side, sets, True)

    def _on_request(self, a):
        req = json.loads("|".join(a)) if a and a[0] else None
        if not req:
            return
        sid = req.get("side", {}).get("id")
        if sid and self.us is None:
            self.us = sid
        side = self.mine
        if not side.mons:
            if self.our_team is not None:
                self._setup_our_side()
            else:
                self._setup_side(side, [{"species": self.d.species_id(p["details"])}
                                        for p in req["side"]["pokemon"]], False)
        if req.get("teamPreview"):
            for m, p in zip(side.mons, req["side"]["pokemon"]):
                m.names.add(parse_ident(p["ident"])[2])
        self.request = req
        self.rqid = req.get("rqid")
        self._sync_request(req)

    def _sync_request(self, req):
        """Our side from a request: HP, status, items, abilities, order, PP."""
        side = self.mine
        pokemon = req.get("side", {}).get("pokemon", [])
        if req.get("teamPreview"):
            return
        order = []
        for i, p in enumerate(pokemon):
            name = parse_ident(p["ident"])[2]
            m = side.by_name(name, self.d.species_id(p["details"]))
            if m is None:
                self.warnings.append(f"request: no Pokemon {p['ident']}")
                continue
            order.append(m.uid)
            hp, mx, status = parse_condition(p["condition"])
            m.exact = True
            m.hp, m.max_hp = hp, (mx or m.max_hp)
            m.fainted = status == "fnt"
            m.status = "" if status == "fnt" else status
            m.item = to_id(p.get("item", ""))
            m.ability = to_id(p.get("ability") or p.get("baseAbility") or "")
            m.base_ability = to_id(p.get("baseAbility") or p.get("ability") or "")
            # Details show permanent formes (Mega); a temporary one (Stance
            # Change) stays as the log left it.
            species = self.d.species_id(p["details"])
            if self.d.num(species) != self.d.num(m.species) or self.d.is_mega(species):
                m.species = species
                if self.d.is_mega(species):
                    m.base_species = species
            m.moves = [to_id(x) for x in p.get("moves", [])]
            m.active = bool(p.get("active")) and not m.fainted
            if m.active or (i < 2 and m.fainted):
                m.slot = i
        if order:
            side.order = order
        for slot, act in enumerate(req.get("active", []) or []):
            if slot >= len(order):
                break
            m = side.mons[order[slot]]
            for mv in act.get("moves", []):
                mid = to_id(mv.get("id") or mv.get("move", ""))
                if "pp" in mv:
                    m.pp[mid] = mv["pp"]
                m.disabled[mid] = bool(mv.get("disabled"))

    def _on_start(self, a):
        self.turn = 0

    def _on_turn(self, a):
        self.turn = int(a[0])
        self.upkeep_seen = False
        for side in self.sides.values():
            side.fainted_last_turn = side.fainted_this_turn
            side.fainted_this_turn = False
            for m in side.mons:
                if m.active and not m.fainted:
                    m.active_turns += 1
                m.moved_this_turn = False
                # A Choice lock without its item ends with the turn.
                if "choicelock" in m.volatiles and not self.d.is_choice(self._item_of(m)):
                    del m.volatiles["choicelock"]

    def _on_upkeep(self, a):
        """The turn's residual has run: count durations down."""
        self.upkeep_seen = True
        f = self.field
        for key, turns in (("weather", "weather_turns"), ("terrain", "terrain_turns")):
            if f[key] and f[turns] > 0:
                f[turns] -= 1
                if f[turns] == 0:
                    f[key] = ""
        for key in ("trick_room", "gravity"):
            if f[key] > 0:
                f[key] -= 1
        for side in self.sides.values():
            for c in list(side.conditions):
                if c in SIDE_TURNS or c in ("wideguard", "quickguard"):
                    side.conditions[c] -= 1
                    if side.conditions[c] <= 0:
                        del side.conditions[c]
            for m in side.mons:
                if not m.active:
                    continue
                for vid in list(m.volatiles):
                    v = m.volatiles[vid]
                    if v.get("duration") is not None:
                        v["duration"] -= 1
                        if v["duration"] <= 0:
                            del m.volatiles[vid]

    def _on_win(self, a):
        self.ended = True
        self.winner = a[0] if a else None

    def _on_tie(self, a):
        self.ended = True

    # --- Switching ----------------------------------------------------------

    def _switch(self, a, drag=False):
        sid, slot, name = parse_ident(a[0])
        side = self.sides[sid]
        species = self.d.species_id(a[1])
        m = side.by_name(name, species)
        if m is None:
            m = Mon(uid=len(side.mons), species=species, base_species=species)
            side.mons.append(m)
            side.sets.append({"species": species})
            self.warnings.append(f"{a[0]} wasn't in the team preview")
        # Our own Illusion user comes in under another's name; its max HP
        # (exact on our side) gives it away.
        hp0, mx0, _ = parse_condition(a[2]) if len(a) > 2 else (0, 0, "")
        if side is self.mine and m.exact and mx0 and m.max_hp and mx0 != m.max_hp:
            real = [x for x in side.mons if x.exact and x.max_hp == mx0 and not x.fainted]
            real.sort(key=lambda x: self._ability(x) != "illusion")
            if real:
                m = real[0]
        else:
            m.names.add(name)
        old = side.active(slot)
        baton = any(x.startswith("[from] Baton Pass") for x in a[3:])
        if old is not None and old is not m:
            if baton and not old.fainted:
                m.boosts = list(old.boosts)
                keep = {k: v for k, v in old.volatiles.items()
                        if k in ("substitute", "focusenergy", "leechseed", "curse", "perishsong", "healblock",
                                 "dragoncheer", "saltcure", "partiallytrapped", "noretreat")}
            else:
                keep = {}
            self._leave(old)
            m.volatiles = keep
            if not baton:
                m.boosts = [0] * 7
        else:
            m.volatiles = {}
            m.boosts = [0] * 7
        # Positions swap, as in Showdown's switchIn.
        if m.uid in side.order:
            new_pos = side.order.index(m.uid)
            if slot < len(side.order):
                side.order[slot], side.order[new_pos] = side.order[new_pos], side.order[slot]
        else:
            while len(side.order) < slot:
                side.order.append(-1)
            if slot < len(side.order):
                if side.order[slot] != -1:
                    side.order.append(side.order[slot])
                side.order[slot] = m.uid
            else:
                side.order.append(m.uid)
        m.active, m.slot, m.revealed = True, slot, True
        m.fainted = False
        hp, mx, status = parse_condition(a[2]) if len(a) > 2 else (100, 100, "")
        self._set_hp(m, hp, mx)
        m.status = "" if status == "fnt" else status
        if m.status != "slp":
            m.sleep_turns = 0
        m.toxic_stage = 0
        m.active_turns = 0
        m.move_actions = 0
        m.last_move = None
        m.types = None
        if self.d.species.get(species) and species != m.species:
            m.species = species
            if self.d.base_of(species) != species:
                m.base_species = species
        # Supreme Overlord counts the fallen as it comes in.
        if self._ability(m) == "supremeoverlord":
            m.fallen = min(side.total_fainted, 5)

    def _leave(self, m: Mon):
        m.species = m.base_species
        m.active = False
        m.slot = None
        m.volatiles = {}
        m.boosts = [0] * 7
        m.types = None
        m.toxic_stage = 0
        if m.ability and m.base_ability:
            m.ability = m.base_ability
        # Regenerator: our HP comes with the next request; theirs we can't see.
        if self._ability(m) == "naturalcure":
            m.status = ""

    def _on_switch(self, a):
        self._switch(a)

    def _on_drag(self, a):
        self._switch(a, drag=True)

    def _on_replace(self, a):
        """Illusion broke: the Pokemon in this slot was really another."""
        sid, slot, name = parse_ident(a[0])
        side = self.sides[sid]
        fake = side.active(slot)
        species = self.d.species_id(a[1])
        real = next((x for x in side.mons if self.d.base_of(x.base_species) == self.d.base_of(species)), None)
        if real is None or fake is None or real is fake:
            return
        real.names.add(name)
        for attr in ("hp", "status", "boosts", "volatiles", "active_turns", "move_actions", "last_move",
                     "moved_this_turn", "toxic_stage", "sleep_turns", "times_attacked"):
            setattr(real, attr, getattr(fake, attr))
        real.active, real.slot, real.revealed, real.fainted = True, slot, True, False
        fake.active, fake.slot = False, None
        fake.volatiles, fake.boosts = {}, [0] * 7
        fake.names.discard(name)
        if fake.uid in side.order and real.uid in side.order:
            i, j = side.order.index(fake.uid), side.order.index(real.uid)
            side.order[i], side.order[j] = side.order[j], side.order[i]
        elif fake.uid in side.order:
            side.order[side.order.index(fake.uid)] = real.uid
        # The disguise's own appearance never happened.
        fake.revealed = any(fake is side.mons[u] for u in side.order[:0])

    def _on_swap(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        side = self.sides[parse_ident(a[0])[0]]
        to = int(a[1])
        other = side.active(to)
        frm = m.slot
        if frm is None or frm == to:
            return
        m.slot = to
        if other is not None:
            other.slot = frm
        if to < len(side.order) and frm < len(side.order):
            side.order[to], side.order[frm] = side.order[frm], side.order[to]

    def _on_detailschange(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        species = self.d.species_id(a[1])
        m.species = species
        m.base_species = species

    def _on_minus_formechange(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.species = self.d.species_id(a[1])

    def _on_minus_mega(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        side = self.sides[parse_ident(a[0])[0]]
        side.mega_used = True
        if len(a) > 2:
            m.item = to_id(a[2])
        mega = self.d.species.get(m.species, {})
        if mega.get("abilities"):
            ab = to_id(mega["abilities"].get("0", ""))
            m.ability = m.base_ability = ab

    def _on_faint(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        side = self.sides[parse_ident(a[0])[0]]
        m.fainted = True
        m.hp = 0
        m.status = ""
        m.species = m.base_species
        m.types = None
        m.volatiles = {}
        m.boosts = [0] * 7
        side.total_fainted += 1
        side.fainted_this_turn = True

    # --- Moves --------------------------------------------------------------

    def _on_move(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        move = to_id(a[1])
        extras = a[3:]
        called = any(x.startswith("[from]") and "lockedmove" not in x for x in extras)
        locked = any("lockedmove" in x for x in extras)
        self._last_user, self._last_move = m, move
        side = self.sides[parse_ident(a[0])[0]]
        if not called:
            m.move_actions += 1
            m.moved_this_turn = True
            if move not in m.moves and move != "struggle":
                m.moves.append(move)
            if not locked and move != "struggle":
                target = self.mon(a[2]) if len(a) > 2 and a[2] and ":" in a[2] else None
                cost = 1 + self._pressure(side, move, target)
                m.pp_used[move] = m.pp_used.get(move, 0) + cost
                if move in m.pp:
                    m.pp[move] = max(0, m.pp[move] - cost)
            m.last_move = move
            if len(a) > 2 and a[2] and ":" in a[2]:
                tsid, tslot, _ = parse_ident(a[2])
                if tslot is not None:
                    m.last_move_target_loc = (tslot + 1) * (1 if tsid != side.id else -1)
        # A charged move lands: the move's own charge ends (twoturnmove
        # runs out at the residual, as in Showdown).
        if locked:
            m.volatiles.pop("charging:" + move, None)
        # Choice items lock the first move.
        if m.item and self.d.is_choice(m.item) and "choicelock" not in m.volatiles and not called:
            m.volatiles["choicelock"] = {"move": move}
        if move == "glaiverush":
            m.volatiles["glaiverush"] = {}
        else:
            m.volatiles.pop("glaiverush", None)
        mv = self.d.moves.get(move, {})
        self._stalling = m if mv.get("stallingMove") else None
        if move == "minimize":
            m.volatiles["minimize"] = {}

    def _pressure(self, side: Side, move: str, target: Mon | None) -> int:
        """Extra PP a move costs: one per targeted foe with Pressure (every
        foe for field moves and the mustpressure ones), as the engine's
        DeductPP."""
        foe = self.foe if side is self.mine else self.mine
        foes = [f for f in foe.mons if f.active and not f.fainted]
        data = self.d.moves.get(move, {})
        kind = data.get("target", "normal")
        if data.get("flags", {}).get("mustpressure") or kind == "all":
            targets = foes
        elif kind in ("allySide", "foeSide", "allyTeam", "self", "adjacentAlly", "adjacentAllyOrSelf"):
            targets = []
        elif kind in ("allAdjacentFoes", "allAdjacent"):
            targets = foes
        else:
            targets = [target] if target is not None and target in foe.mons else []
        return sum(1 for t in targets if self._ability(t) == "pressure")

    def _on_cant(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        m.move_actions += 1
        m.moved_this_turn = True
        reason = a[1] if len(a) > 1 else ""
        if reason == "slp":
            m.sleep_turns += 1
        if reason == "recharge":
            m.volatiles.pop("mustrecharge", None)

    def _on_minus_singleturn(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        eff = effect_id(a[1])
        mv = self.d.moves.get(eff, {})
        single = {"protect": "protect", "detect": "protect", "spikyshield": "spikyshield",
                  "kingsshield": "kingsshield", "banefulbunker": "banefulbunker", "followme": "followme",
                  "ragepowder": "ragepowder", "helpinghand": "helpinghand", "endure": "endure", "roost": "roost"}
        if eff in single:
            prev = m.volatiles.get(single[eff], {})
            m.volatiles[single[eff]] = {"duration": 1}
            if eff == "helpinghand":
                m.volatiles["helpinghand"]["counter"] = prev.get("counter", 0) + 1
        if eff in ("wideguard", "quickguard"):
            side = self._side_of(m)
            if side is not None:
                side.conditions[eff] = 1
        if mv.get("stallingMove") or eff in ("protect", "wideguard", "quickguard"):
            v = m.volatiles.get("stall")
            if v:
                v["counter"] = min(v.get("counter", 3) * 3, 729)
                v["duration"] = 2
            else:
                m.volatiles["stall"] = {"duration": 2, "counter": 3}
        self._stalling = None

    def _on_minus_singlemove(self, a):
        m = self.mon(a[0])
        if m is not None and effect_id(a[1]) == "destinybond":
            m.volatiles["destinybond"] = {}

    def _on_minus_fail(self, a):
        m = self.mon(a[0])
        if m is not None and m is self._stalling:
            m.volatiles.pop("stall", None)
            self._stalling = None
        self._missed()

    def _missed(self):
        """The last move didn't land: Glaive Rush's lowered guard only comes
        with a hit."""
        if self._last_move == "glaiverush" and self._last_user is not None:
            self._last_user.volatiles.pop("glaiverush", None)

    def _on_minus_miss(self, a):
        self._missed()

    def _on_minus_immune(self, a):
        self._missed()

    def _on_minus_prepare(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        move = to_id(a[1])
        m.volatiles["twoturnmove"] = {"move": move, "duration": 2}
        m.volatiles["charging:" + move] = {"duration": 2 if move in SEMI_INVULNERABLE else None}

    def _on_minus_anim(self, a):
        # A charge move that fires at once (Electro Shot in rain, Solar Beam
        # in sun): no charging turn after all.
        m = self.mon(a[0]) if a and ":" in a[0] else None
        if m is not None and len(a) > 1:
            move = to_id(a[1])
            if move in TWO_TURN:
                m.volatiles.pop("twoturnmove", None)
                m.volatiles.pop("charging:" + move, None)

    def _on_minus_mustrecharge(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.volatiles["mustrecharge"] = {"duration": 2}

    # --- HP, status, boosts --------------------------------------------------

    def _set_hp(self, m: Mon, hp: int, mx: int):
        if m.exact:
            m.hp = hp
            if mx:
                m.max_hp = mx
        else:
            m.hp = hp if mx in (0, 100) else 100.0 * hp / max(mx, 1)
            m.max_hp = 100

    def _on_minus_damage(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        hp, mx, status = parse_condition(a[1])
        before = m.hp
        self._set_hp(m, hp, mx)
        if status != "fnt":
            m.status = status
        src = [x for x in a[2:] if x.startswith("[from]")]
        if not src and hp < before:
            m.times_attacked += 1
            # Fake Out always flinches (the volatile shows only if it acts).
            if self._last_move == "fakeout" and self._last_user is not m and hp > 0 \
                    and self._ability(m) not in ("innerfocus", "shielddust") and self._item_of(m) != "covertcloak":
                m.volatiles["flinch"] = {"duration": 1}
        self._reveal_from(m, a[2:])
        if any(x == "[from] psn" for x in a[2:]) and m.status == "tox":
            m.toxic_stage += 1

    def _on_minus_heal(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        hp, mx, status = parse_condition(a[1])
        self._set_hp(m, hp, mx)
        if status and status != "fnt":
            m.status = status
        self._reveal_from(m, a[2:])

    def _on_minus_sethp(self, a):
        m = self.mon(a[0])
        if m is not None:
            hp, mx, _ = parse_condition(a[1])
            self._set_hp(m, hp, mx)

    def _on_minus_status(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        m.status = a[1]
        m.sleep_turns = 0
        m.toxic_stage = 0
        self._reveal_from(m, a[2:])

    def _on_minus_curestatus(self, a):
        m = self.mon(a[0]) if ":" in a[0] else None
        if m is not None:
            m.status = ""
            m.sleep_turns = 0

    def _on_minus_cureteam(self, a):
        side = self.sides[parse_ident(a[0])[0]]
        for m in side.mons:
            m.status = ""

    def _boost(self, a, sign):
        m = self.mon(a[0])
        if m is None or a[1] not in BOOSTS:
            return
        i = BOOSTS.index(a[1])
        m.boosts[i] = max(-6, min(6, m.boosts[i] + sign * int(a[2])))
        self._reveal_from(m, a[3:])

    def _on_minus_boost(self, a):
        self._boost(a, 1)

    def _on_minus_unboost(self, a):
        self._boost(a, -1)

    def _on_minus_setboost(self, a):
        m = self.mon(a[0])
        if m is not None and a[1] in BOOSTS:
            m.boosts[BOOSTS.index(a[1])] = int(a[2])

    def _on_minus_clearboost(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.boosts = [0] * 7

    def _on_minus_clearallboost(self, a):
        for side in self.sides.values():
            for m in side.mons:
                if m.active:
                    m.boosts = [0] * 7

    def _on_minus_clearnegativeboost(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.boosts = [max(0, b) for b in m.boosts]

    def _on_minus_clearpositiveboost(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.boosts = [min(0, b) for b in m.boosts]

    def _on_minus_invertboost(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.boosts = [-b for b in m.boosts]

    def _on_minus_copyboost(self, a):
        # |-copyboost|A|B: A takes B's boosts (Psych Up's user is A).
        dst, src = self.mon(a[0]), self.mon(a[1])
        if src is not None and dst is not None:
            dst.boosts = list(src.boosts)

    def _on_minus_swapboost(self, a):
        src, dst = self.mon(a[0]), self.mon(a[1])
        if src is None or dst is None:
            return
        stats = [s.strip() for s in a[2].split(",")] if len(a) > 2 and a[2] else BOOSTS
        for s in stats:
            if s in BOOSTS:
                i = BOOSTS.index(s)
                src.boosts[i], dst.boosts[i] = dst.boosts[i], src.boosts[i]

    # --- Items and abilities --------------------------------------------------

    def _reveal_from(self, m: Mon, extras):
        """'[from] item: Life Orb' / '[from] ability: Rough Skin|[of] X'."""
        src = next((x[7:] for x in extras if x.startswith("[from] ")), None)
        of = next((x[5:] for x in extras if x.startswith("[of] ")), None)
        if not src:
            return
        holder = self.mon(of) if of else m
        if holder is None:
            return
        if src.startswith("item: "):
            if holder.item is None:
                holder.item = to_id(src[6:])
        elif src.startswith("ability: "):
            ab = to_id(src[9:])
            if holder.ability is None or holder.ability != ab:
                holder.ability = ab
                if holder.base_ability is None:
                    holder.base_ability = ab

    def _on_minus_item(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        # Trick takes an item before giving one: Unburden starts.
        if any(x.startswith("[from] move: Trick") or x.startswith("[from] move: Switcheroo") for x in a[2:]) \
                and self._item_of(m) and self._ability(m) == "unburden":
            m.volatiles["unburden"] = {}
        m.item = to_id(a[1])
        if any(x.startswith("[from] ability: Frisk") for x in a[2:]):
            return
        self._reveal_from(m, [x for x in a[2:] if not x.startswith("[from] move")])

    def _on_minus_enditem(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        item = to_id(a[1])
        extras = a[2:]
        knocked = any("Knock Off" in x or "Thief" in x or "Covet" in x or "Bug Bite" in x or "Pluck" in x
                      or "Incinerate" in x for x in extras)
        m.item = ""
        if not knocked:
            m.last_item = item
        if self._ability(m) == "unburden":
            m.volatiles["unburden"] = {}
        m.volatiles.pop("choicelock", None)

    def _on_minus_ability(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        ab = to_id(a[1])
        extras = a[2:]
        if any(x.startswith("[from] ability: Trace") or x.startswith("[from] move:") for x in extras):
            if m.base_ability is None:
                m.base_ability = "trace" if any("Trace" in x for x in extras) else None
            m.ability = ab
        else:
            m.ability = ab
            if m.base_ability is None:
                m.base_ability = ab
        if ab == "supersweetsyrup":
            m.syrup = True
        if ab == "supremeoverlord":
            m.fallen = min(self.sides[parse_ident(a[0])[0]].total_fainted, 5)

    def _on_minus_endability(self, a):
        m = self.mon(a[0])
        if m is not None:
            m.ability = "noability"

    def _ability(self, m: Mon) -> str:
        """Its ability as far as we know: revealed, else its set's (Open
        Team Sheets), else the most likely."""
        if m.ability:
            return m.ability
        side = self._side_of(m)
        if side is not None and m.uid < len(side.sets) and side.sets[m.uid].get("ability"):
            return side.sets[m.uid]["ability"]
        return self.d.guess_set(self.d.base_of(m.base_species))[0]["ability"]

    def _side_of(self, m: Mon) -> Side | None:
        for s in self.sides.values():
            if m in s.mons:
                return s
        return None

    # --- Volatiles ------------------------------------------------------------

    def _on_minus_start(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        eff = effect_id(a[1])
        extras = a[2:]
        self._reveal_from(m, extras)
        if eff.startswith("perish") and eff[6:].isdigit():
            # Shown in the residual, after the count went down; the upkeep
            # that follows shouldn't take another.
            n = int(eff[6:])
            m.volatiles["perishsong"] = {"duration": n + (0 if self.upkeep_seen else 1)}
            return
        if eff.startswith("stockpile") and eff[9:].isdigit():
            m.volatiles["stockpile"] = {"counter": int(eff[9:])}
            return
        if eff == "typechange":
            types = a[2] if len(a) > 2 and not a[2].startswith("[") else ""
            ts = [t for t in types.split("/") if t]
            if ts:
                m.types = (ts + ts)[:2]
            return
        vid = VOLATILES.get(eff)
        if vid is None:
            return
        v: dict = {}
        if vid in DURATIONS:
            v["duration"] = DURATIONS[vid]
        if vid == "encore":
            v["move"] = m.last_move
            v["duration"] = 3 if not m.moved_this_turn else 4
        elif vid == "taunt":
            v["duration"] = 4 if (m.moved_this_turn and m.active_turns > 0) else 3
        elif vid == "disable":
            v["move"] = to_id(a[2]) if len(a) > 2 and not a[2].startswith("[") else m.last_move
            # A turn shorter if it is still to move, or is moving now
            # (Cursed Body).
            cursed = any("Cursed Body" in x for x in extras)
            v["duration"] = 4 if (cursed or not m.moved_this_turn) else 5
        elif vid == "confusion":
            v["counter"] = 3
        elif vid == "substitute":
            v["counter"] = None
        elif vid == "leechseed":
            src = self._last_user
            s = self._side_of(src) if src is not None else None
            v["target_loc"] = ((0 if s is self.mine else 1) * 2 + (src.slot or 0)) if s else 0
        elif vid == "dragoncheer":
            types = self._types(m)
            v["counter"] = int("Dragon" in types)
        m.volatiles[vid] = v

    def _types(self, m: Mon) -> list:
        if m.types:
            return m.types
        return list(self.d.species.get(m.species, {}).get("types", []))

    def _on_minus_end(self, a):
        m = self.mon(a[0])
        if m is None:
            return
        eff = effect_id(a[1])
        if any(x == "[partiallytrapped]" for x in a[2:]):
            m.volatiles.pop("partiallytrapped", None)
        elif eff.startswith("perish"):
            m.volatiles.pop("perishsong", None)
        elif eff.startswith("stockpile"):
            m.volatiles.pop("stockpile", None)
        elif eff == "typechange":
            m.types = None
        else:
            vid = VOLATILES.get(eff)
            if vid:
                m.volatiles.pop(vid, None)

    def _on_minus_activate(self, a):
        m = self.mon(a[0]) if ":" in a[0] else None
        if m is None:
            return
        eff = effect_id(a[1])
        if eff in ("protect", "spikyshield", "kingsshield", "banefulbunker", "wideguard", "quickguard"):
            self._missed()
        if eff == "confusion":
            v = m.volatiles.get("confusion")
            if v:
                v["counter"] = max(1, v.get("counter", 3) - 1)
        elif eff in ("firespin", "whirlpool", "sandtomb", "infestation", "magmastorm", "bind", "wrap",
                     "clamp", "thundercage", "snaptrap"):
            src = next((x[5:] for x in a[2:] if x.startswith("[of] ")), None)
            s = self.mon(src) if src else None
            ss = self._side_of(s) if s else None
            counter = (((0 if ss is self.mine else 1) << 8) | s.uid) if s else 0
            if "partiallytrapped" not in m.volatiles:
                m.volatiles["partiallytrapped"] = {"duration": 5, "counter": counter}
        elif eff == "substitute" and any("[damage]" in x for x in a[2:]):
            pass
        self._reveal_from(m, a[2:])

    # --- Field ----------------------------------------------------------------

    def _source_item(self, extras) -> str:
        of = next((x[5:] for x in extras if x.startswith("[of] ")), None)
        m = self.mon(of) if of else self._last_user
        if m is None:
            return ""
        if m.item:
            return m.item
        side = self._side_of(m)
        if side is not None and m.item is None and m.uid < len(side.sets):
            return side.sets[m.uid].get("item", "") or ""
        return ""

    def _on_minus_weather(self, a):
        w = to_id(a[0])
        extras = a[1:]
        if w == "none" or not w:
            self.field["weather"], self.field["weather_turns"] = "", 0
            return
        if any(x == "[upkeep]" for x in extras):
            return
        weather, rock = WEATHERS.get(w, (w, ""))
        src = next((x for x in extras if x.startswith("[from] ability: ")), None)
        if src is not None:
            of = next((x[5:] for x in extras if x.startswith("[of] ")), None)
            holder = self.mon(of) if of else None
            if holder is not None:
                holder.ability = to_id(src[16:])
        self.field["weather"] = weather
        self.field["weather_turns"] = 8 if self._source_item(extras) == rock else 5

    def _on_minus_fieldstart(self, a):
        eff = effect_id(a[0])
        extras = a[1:]
        if eff in TERRAINS:
            src = next((x for x in extras if x.startswith("[from] ability: ")), None)
            if src is not None:
                of = next((x[5:] for x in extras if x.startswith("[of] ")), None)
                holder = self.mon(of) if of else None
                if holder is not None:
                    holder.ability = to_id(src[16:])
            self.field["terrain"] = eff
            self.field["terrain_turns"] = 8 if self._source_item(extras) == "terrainextender" else 5
        elif eff == "trickroom":
            self.field["trick_room"] = 5
        elif eff == "gravity":
            self.field["gravity"] = 5

    def _on_minus_fieldend(self, a):
        eff = effect_id(a[0])
        if eff in TERRAINS:
            self.field["terrain"], self.field["terrain_turns"] = "", 0
        elif eff == "trickroom":
            self.field["trick_room"] = 0
        elif eff == "gravity":
            self.field["gravity"] = 0

    def _side_arg(self, text: str) -> Side:
        return self.sides[text[:2]]

    def _on_minus_sidestart(self, a):
        side = self._side_arg(a[0])
        eff = effect_id(a[1])
        if eff in HAZARDS:
            side.conditions[eff] = side.conditions.get(eff, 0) + 1
        elif eff in SIDE_TURNS:
            turns = SIDE_TURNS[eff]
            if eff in SCREENS and self._last_user is not None and self._item_of(self._last_user) == "lightclay":
                turns = 8
            side.conditions[eff] = turns

    def _item_of(self, m: Mon) -> str:
        if m.item is not None:
            return m.item
        side = self._side_of(m)
        return (side.sets[m.uid].get("item", "") if side and m.uid < len(side.sets) else "") or ""

    def _on_minus_sideend(self, a):
        side = self._side_arg(a[0])
        side.conditions.pop(effect_id(a[1]), None)

    def _on_minus_swapsideconditions(self, a):
        p1, p2 = self.sides["p1"], self.sides["p2"]
        p1.conditions, p2.conditions = p2.conditions, p1.conditions

    # --- Decisions ------------------------------------------------------------

    def needs_decision(self) -> bool:
        r = self.request
        return bool(r) and not r.get("wait") and not self.ended

    def _foe_needs_switch(self) -> list[bool]:
        """The opponent's slots it must refill too: at the end of the turn,
        those whose Pokemon fainted, while its bench has one left."""
        foe = self.foe
        bench = foe.team_size - foe.total_fainted - sum(1 for m in foe.mons if m.active and not m.fainted)
        return [bench > 0 and self.upkeep_seen and any(m.fainted and m.slot == slot for m in foe.mons)
                for slot in (0, 1)]

    def position(self, guess_brought=None) -> dict:
        """The engine position for our current request (our side is side
        0). `guess_brought(tracker) -> list of uids` picks the opponent's
        unseen Pokemon (default: the first unseen in team order)."""
        req = self.request or {}
        sides = []
        for s, side in ((0, self.mine), (1, self.foe)):
            sets, replaced = self._sets(side)
            for r in replaced:
                w = f"{side.id}: {r} isn't supported; used a stand-in"
                if w not in self.warnings:
                    self.warnings.append(w)
            order = [u for u in side.order if u >= 0]
            if s == 1:
                order = self._foe_order(order, guess_brought)
            mons = [self._mon_position(side, side.mons[u], i, s) for i, u in enumerate(order)]
            conditions = {k: v for k, v in side.conditions.items() if v > 0}
            sides.append({
                "team": self._team_json(side) or sets,
                "pokemon": mons,
                "mega_used": side.mega_used,
                "total_fainted": side.total_fainted,
                "fainted_this_turn": side.fainted_this_turn,
                "fainted_last_turn": side.fainted_last_turn,
                "conditions": conditions,
                "slot_filled": [any(m.slot == p and (m.active or m.fainted) for m in side.mons) for p in (0, 1)],
            })
        if req.get("forceSwitch"):
            ours = [bool(x) for x in req["forceSwitch"]] + [False]
            requests = [{"switch": ours[:2]}, {"switch": self._foe_needs_switch()}]
            residual = not self.upkeep_seen
        else:
            requests = ["move", "move"]
            residual = False
        return {"turn": max(self.turn, 1), "requests": requests, "residual": residual,
                "field": dict(self.field), "sides": sides}

    def _sets(self, side: Side) -> tuple[list[dict], list[str]]:
        if side is self.mine and side.complete:
            return [dict(s) for s in side.sets], []
        out, replaced = [], []
        taken = set()
        for m, s in zip(side.mons, side.sets):
            if side.complete:
                st, rep = self.d.guess_set(m.base_species if self.d.base_of(m.base_species) == m.base_species
                                           else self.d.base_of(m.base_species),
                                           item=s.get("item", ""), ability=s.get("ability"),
                                           moves=s.get("moves"), nature=s.get("nature"), complete=True)
            else:
                item = m.item if m.item is not None else None
                if item == "" and m.last_item:
                    item = m.last_item
                st, rep = self.d.guess_set(self.d.base_of(m.base_species), item=item, ability=m.base_ability,
                                           moves=m.moves or None, taken_items=taken)
            taken.add(st["item"])
            out.append(st)
            replaced += rep
        return out, replaced

    def _foe_order(self, order: list[int], guess_brought) -> list[int]:
        foe = self.foe
        size = foe.team_size
        if len(order) < size:
            if guess_brought is not None:
                extra = [u for u in guess_brought(self) if u not in order]
            else:
                extra = [m.uid for m in foe.mons if m.uid not in order]
            order = order + extra[:size - len(order)]
        return order[:max(size, len(order))]

    def _mon_position(self, side: Side, m: Mon, pos: int, s: int) -> dict:
        d = self.d
        e: dict = {"uid": m.uid, "species": m.species, "base_species": m.base_species,
                   "status": "" if m.fainted else m.status, "boosts": m.boosts,
                   "active": m.active and not m.fainted, "fainted": m.fainted, "revealed": m.revealed,
                   "active_turns": m.active_turns, "active_move_actions": m.move_actions,
                   "times_attacked": m.times_attacked, "fallen": m.fallen, "syrup_triggered": m.syrup}
        if m.exact:
            e["hp"] = int(m.hp)
        elif m.revealed:
            e["hp_pct"] = float(m.hp)
        if m.status == "slp":
            e["status_time"] = max(1, 3 - m.sleep_turns)
        if m.status == "tox":
            e["status_stage"] = m.toxic_stage
        if m.item is not None:
            e["item"] = m.item
        if m.last_item:
            e["last_item"] = m.last_item
        if m.ability:
            e["ability"] = m.ability
        if m.base_ability:
            e["base_ability"] = m.base_ability
        if m.types:
            e["types"] = m.types
        if m.last_move:
            e["last_move"] = m.last_move
            e["last_move_target_loc"] = m.last_move_target_loc
        # Moves with PP: ours exact from the requests (actives) or counted.
        moves = []
        team_moves = self._set_moves(side, m)
        for mid in team_moves:
            mx = d.max_pp(mid)
            pp = m.pp.get(mid, mx - m.pp_used.get(mid, 0)) if m.exact else mx - m.pp_used.get(mid, 0)
            slot = {"id": mid, "pp": max(0, min(mx, pp))}
            moves.append(slot)
        if moves:
            e["moves"] = moves
        vols = []
        for vid, v in m.volatiles.items():
            x = {"id": vid}
            if vid.startswith("charging:"):
                x = {"id": vid.split(":", 1)[1], "charging": True}
            for k in ("duration", "counter", "move", "target_loc"):
                if k in v and v[k] is not None:
                    x[k] = v[k]
            if vid == "substitute":
                x.pop("counter", None)
            vols.append(x)
        if vols:
            e["volatiles"] = vols
        return e

    def _set_moves(self, side: Side, m: Mon) -> list[str]:
        """The move slots the engine will give this Pokemon: its set's."""
        sets, _ = self._sets(side) if side is not self.mine else ([dict(s) for s in side.sets], [])
        return list(sets[m.uid]["moves"]) if m.uid < len(sets) else list(m.moves)
