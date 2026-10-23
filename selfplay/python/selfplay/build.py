"""Team builder: search for a team that does well against a field of strong
teams, starting from a team (or from a core of Pokemon) and changing it a
step at a time.

    python -m selfplay.build runs/ppo7/model.pt --start "lovejapanfrombr's Salamence Sand Team" --keep Tyranitar Excadrill --device cuda
    python -m selfplay.build runs/ppo7/model.pt --keep Tyranitar Excadrill --device cuda      # from the core

Each step:

1. **Neighbours.** Many variants of the current team: one change (a
   Pokemon swapped for another species, drawn by how often Reg M-C players
   pair it with the rest of the team, from the usage stats; one move,
   item, ability or spread, from what players run on it, a spread only if
   it moves 8+ points or the nature; the Mega Stone on or off; a set
   redrawn), or two at once: two members swapped for a pair players run
   together with the rest, or two single changes. Only legal teams the
   engine can play, without removing a `--keep` Pokemon, and within the
   type rules (below).
2. **Screening.** The network's own win estimate at team preview, the
   team against every field team (both sides): one evaluation per
   matchup, so hundreds of variants take seconds. On corpus teams it
   tracks the games' results (correlation 0.74 per matchup, 0.91 per team
   over 12 opponents), but among close variants its top picks are the
   ones it overrates: half the racers are its best, half drawn from its
   better half.
3. **Racing.** Those and the current team play the field (the policy on
   both sides, as in `selfplay.teams`) in rounds of more and more games;
   each round keeps the better half (successive halving).
4. **Confirming.** The race's winner is the luckiest of a dozen, so it and
   the current team play the field again on fresh games (`--confirm`), and
   it replaces the current team only if it wins those by `--accept-se`
   (2) standard errors.

The score of a team is its mean win chance against the field (weighted by
placement) blended with its worst matchups (`--tail` of the weight on the
mean of the worst `--alpha` share): good overall, without a hard counter.

Type rules (`--no-rules` to turn off), counting by member against each
attacking type (abilities that grant immunities count; a Mega with its
stone counts as the Mega):

- no type that 4 or more members are weak to;
- at most one type that 3 members are weak to;
- at most seven types that exactly 2 members are weak to;
- at most four types where the members weak to it outnumber those that
  resist it by one;
- no type where they outnumber them by two or more;
- at most two Mega Stones.

A team within the rules stays within them. One that breaks some (the
starting one, say) may still be changed, but no variant may break any rule
further than it does, each rule on its own (a fourth member weak to a
type is a step further, so is a second type with two more weak members
than resisting); every step past a rule costs `--rule-penalty` of score,
so a variant that breaks less wins a near tie.

The games are the bot playing both sides, so a team is rated as the bot
plays it: a new team the network hasn't practised may rate low until it
has (add the finalists to data/my_teams and train). Everything goes to
--out: each accepted team's paste, log.jsonl, and the final report (every
field matchup; the team's leads and brings, by win rate).
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import torch

from selfplay.env import DEFAULT_CORPUS, SelfPlayEnv
from selfplay.showdown.data import data, to_id
from selfplay.train import autocast, to_tensors

STATS = ("HP", "Atk", "Def", "SpA", "SpD", "Spe")


@dataclass(frozen=True)
class Member:
    species: str                 # the paste's species (a Mega's base forme)
    item: str                    # item name, "" for none
    ability: str
    nature: str
    points: tuple[int, ...]      # stat points, HP Atk Def SpA SpD Spe
    moves: tuple[str, ...]

    def paste(self) -> str:
        evs = " / ".join(f"{p} {s}" for p, s in zip(self.points, STATS) if p)
        return "\n".join([self.species + (f" @ {self.item}" if self.item else ""),
                          f"Ability: {self.ability}", "Level: 50"]
                         + ([f"EVs: {evs}"] if evs else []) + [f"{self.nature} Nature"]
                         + [f"- {m}" for m in self.moves])


Team = tuple  # of six Members


def team_paste(team: Team) -> str:
    return "\n\n".join(m.paste() for m in team) + "\n"


def from_paste(text: str) -> Team:
    from selfplay import parse_team
    from selfplay.showdown.team import complete_paste
    sets = parse_team(complete_paste(text)[0])
    return tuple(Member(s["species"], s["item"] or "", s["ability"], s["nature"], tuple(s["points"]),
                        tuple(s["moves"])) for s in sets)


def changes(old: Team, new: Team) -> str:
    """What differs, in a few words."""
    out = []
    olds = {to_id(m.species): m for m in old}
    for m in new:
        o = olds.get(to_id(m.species))
        if o is None:
            gone = [x.species for x in old if to_id(x.species) not in {to_id(y.species) for y in new}]
            out.append(f"{'/'.join(gone)} -> {m.species}" + (f" @ {m.item}" if m.item else ""))
            continue
        if set(o.moves) != set(m.moves):
            out.append(f"{m.species}: {', '.join(sorted(set(o.moves) - set(m.moves)))} -> "
                       f"{', '.join(sorted(set(m.moves) - set(o.moves)))}")
        if o.item != m.item:
            out.append(f"{m.species}: {o.item or 'no item'} -> {m.item or 'no item'}")
        if o.ability != m.ability:
            out.append(f"{m.species}: {o.ability} -> {m.ability}")
        if (o.nature, o.points) != (m.nature, m.points):
            out.append(f"{m.species}: spread {o.nature} {'/'.join(map(str, o.points))} -> "
                       f"{m.nature} {'/'.join(map(str, m.points))}")
    return "; ".join(out) or "no change"


# --- What a member can be ----------------------------------------------------

class Pool:
    """Per usage entry (a species, or its Mega): the supported moves it can
    learn, items, abilities and spreads Reg M-C players run on it, with
    their usage; and who they pair it with."""

    def __init__(self):
        d = self.d = data()
        self.entries: dict[str, dict] = {}
        self._swap: dict = {}
        for key, e in d.pool.items():
            base = d.base_of(key)
            sp = d.species.get(base)
            if not sp:
                continue
            mega = d.is_mega(key)
            learn = set(sp.get("learnset") or [])
            moves = [(n, p) for n, p in e.get("moves", [])
                     if to_id(n) in d.supported_moves and (not learn or to_id(n) in learn)]
            if mega:
                stones = [n for n, _ in e.get("items", []) if d.mega_of(base, to_id(n)) == key]
                items = [(stones[0], 100.0)] if stones and to_id(stones[0]) in d.supported_items else []
                # A paste names the base forme's ability.
                abil = (d.pool.get(base) or {}).get("abilities") or [(a, 1.0) for a in sp["abilities"].values()]
            else:
                items = [(n, p) for n, p in e.get("items", [])
                         if to_id(n) in d.supported_items and not (d.items.get(to_id(n)) or {}).get("megaStone")]
                abil = e.get("abilities", [])
            abilities = [(n, p) for n, p in abil if to_id(n) in d.supported_abilities]
            spreads = [(n, tuple(pts), p) for n, pts, p in e.get("spreads", []) if pts]
            if len(moves) < 4 or not abilities or not spreads or (mega and not items):
                continue
            self.entries[key] = {
                "species": sp["name"], "base": base, "mega": mega, "moves": moves, "items": items,
                "abilities": abilities, "spreads": spreads, "count": float(e.get("raw_count") or 0),
                "teammates": {to_id(n): p for n, p in e.get("teammates", [])},
            }

    def key_of(self, m: Member) -> str:
        sid = to_id(m.species)
        mega = self.d.mega_of(sid, to_id(m.item)) if m.item else None
        return mega if mega and mega in self.entries else sid

    def member(self, key: str, rng: np.random.Generator, taken_items=(), top: bool = False) -> Member | None:
        """A set for entry `key`: its most used choices (`top`), or drawn by
        usage; no item another member holds."""
        e = self.entries[key]
        pick = lambda opts: opts[0] if top else opts[rng.choice(len(opts), p=_probs([o[-1] for o in opts]))]
        items = [i for i in e["items"] if i[0] not in taken_items]
        if not items and e["mega"]:
            return None
        item = pick(items)[0] if items else ""
        ability = pick(e["abilities"])[0]
        nature, points, _ = pick(e["spreads"])
        names = [m[0] for m in e["moves"]]
        if top:
            moves = names[:4]
        else:
            idx = rng.choice(len(names), 4, replace=False, p=_probs([m[1] for m in e["moves"]]))
            moves = [names[i] for i in sorted(idx)]
        return Member(e["species"], item, ability, nature, tuple(points), tuple(moves))

    def swap_weights(self, rest: Team) -> tuple[list[str], np.ndarray]:
        """The entries that could join `rest` (no species it has), and the
        chance to draw each: how often players pair it with `rest`, times
        the square root of its usage."""
        if rest not in self._swap:
            bases = {self.d.base_of(to_id(x.species)) for x in rest}
            keys = [k for k, v in self.entries.items() if v["base"] not in bases]
            w = np.array([(self.affinity(k, rest) + 1.0) * math.sqrt(self.entries[k]["count"] + 1.0) for k in keys])
            if len(self._swap) > 256:
                self._swap.clear()
            self._swap[rest] = (keys, w / w.sum())
        return self._swap[rest]

    def affinity(self, key: str, team: Team) -> float:
        """How often players pair `key` with the team's members (usage
        teammate shares, summed both ways)."""
        mine = self.entries[key]["teammates"]
        s = 0.0
        for m in team:
            k = self.key_of(m)
            s += mine.get(k, 0.0) + mine.get(to_id(m.species), 0.0)
            other = self.entries.get(k, {}).get("teammates", {})
            s += other.get(key, 0.0) + other.get(self.entries[key]["base"], 0.0)
        return s


def _probs(w) -> np.ndarray:
    w = np.maximum(np.asarray(w, np.float64), 1e-3)
    return w / w.sum()


# --- Type rules --------------------------------------------------------------

# Abilities that change what a type does to their holder: x0 or x0.5.
ABILITY_TYPES = {
    "levitate": {"Ground": 0}, "eartheater": {"Ground": 0}, "flashfire": {"Fire": 0},
    "wellbakedbody": {"Fire": 0}, "waterabsorb": {"Water": 0}, "stormdrain": {"Water": 0},
    "dryskin": {"Water": 0}, "voltabsorb": {"Electric": 0}, "lightningrod": {"Electric": 0},
    "motordrive": {"Electric": 0}, "sapsipper": {"Grass": 0}, "thickfat": {"Fire": 0.5, "Ice": 0.5},
    "heatproof": {"Fire": 0.5}, "waterbubble": {"Fire": 0.5}, "purifyingsalt": {"Ghost": 0.5},
}


def type_profile(team: Team, pool: Pool) -> dict[str, tuple[int, int]]:
    """Per attacking type: (members weak to it, members resisting or
    immune). A Mega Stone holder counts as its Mega."""
    d = pool.d
    chart = d_typechart()
    effect = {0: 1.0, 1: 2.0, 2: 0.5, 3: 0.0}
    out = {}
    forms = []
    for m in team:
        key = pool.key_of(m)
        sp = d.species.get(key) or d.species.get(to_id(m.species)) or {}
        ability = to_id(m.ability)
        if key != to_id(m.species):  # a Mega: its own ability
            ability = to_id(next(iter((sp.get("abilities") or {}).values()), m.ability))
        forms.append((sp.get("types", []), ability))
    for a in chart["Normal"]:
        weak = resist = 0
        for types, ability in forms:
            x = 1.0
            for t in types:
                x *= effect[chart[t][a]]
            x *= ABILITY_TYPES.get(ability, {}).get(a, 1.0)
            weak += x > 1
            resist += x < 1
        out[a] = (weak, resist)
    return out


_CHART = None


def d_typechart() -> dict:
    global _CHART
    if _CHART is None:
        from selfplay.showdown.data import DATA
        _CHART = json.loads((DATA / "dex.json").read_text(encoding="utf-8"))["typechart"]
    return _CHART


RULES = ("4+ weak", "3+ weak types", "2 weak types", "net +1 types", "net +2", "megas")


def rule_excess(team: Team, pool: Pool, rules: bool = True) -> dict[str, int]:
    """How far the team goes past each rule (0: within it), by member:

    - 4+ weak: members past 3 weak to one type, summed over types (no type
      may hit four);
    - 3+ weak types: types 3 or more members are weak to, past one;
    - 2 weak types: types exactly 2 members are weak to, past seven;
    - net +1 types: types with one more weak member than resisting, past four;
    - net +2: past one more weak than resisting, summed over types (none may
      have two more);
    - megas: Mega Stones past two."""
    if not rules:
        return dict.fromkeys(RULES, 0)
    prof = type_profile(team, pool)
    weak = [w for w, _ in prof.values()]
    net = [w - r for w, r in prof.values()]
    megas = sum(pool.key_of(m) != to_id(m.species) for m in team)
    return {
        "4+ weak": sum(max(0, w - 3) for w in weak),
        "3+ weak types": max(0, sum(w >= 3 for w in weak) - 1),
        "2 weak types": max(0, sum(w == 2 for w in weak) - 7),
        "net +1 types": max(0, sum(n == 1 for n in net) - 4),
        "net +2": sum(max(0, n - 1) for n in net),
        "megas": max(0, megas - 2),
    }


def within(excess: dict[str, int], limit: dict[str, int]) -> bool:
    """No rule broken further than `limit` breaks it (a team within the
    rules must stay within them)."""
    return all(excess[k] <= limit[k] for k in RULES)


def rule_breaks(team: Team, pool: Pool, rules: bool = True) -> list[str]:
    """The rules the team breaks, in words."""
    if not rules:
        return []
    prof = type_profile(team, pool)
    ex = rule_excess(team, pool, rules)
    out = []
    if ex["4+ weak"]:
        out.append("4+ members weak to " + ", ".join(a for a, (w, _) in prof.items() if w >= 4))
    if ex["3+ weak types"]:
        out.append(f"{ex['3+ weak types'] + 1} types hit 3+ members "
                   f"({', '.join(a for a, (w, _) in prof.items() if w >= 3)})")
    if ex["2 weak types"]:
        out.append(f"{ex['2 weak types'] + 7} types hit exactly 2 members")
    if ex["net +1 types"]:
        out.append(f"{ex['net +1 types'] + 4} types with one more weak member than resisting "
                   f"({', '.join(a for a, (w, r) in prof.items() if w - r == 1)})")
    for a, (w, r) in prof.items():
        if w - r >= 2:
            out.append(f"{a}: {w} weak, {r} resisting")
    if ex["megas"]:
        out.append(f"{ex['megas'] + 2} Mega Stones")
    return out


def rule_cost(team: Team, pool: Pool, rules: bool = True) -> int:
    """All the rules' excesses together (what the score's penalty counts)."""
    return sum(rule_excess(team, pool, rules).values())


def legal(team: Team, pool: Pool) -> bool:
    """A Reg M-C team (item clause and all) the engine can play."""
    from selfplay import validate_team
    d = pool.d
    items = [to_id(m.item) for m in team if m.item]
    if len(set(items)) != len(items) or any(i not in d.supported_items for i in items):
        return False
    for m in team:
        if to_id(m.ability) not in d.supported_abilities or any(to_id(x) not in d.supported_moves for x in m.moves):
            return False
    return all(kind == "ItemNotInUsage" for kind, _ in validate_team(team_paste(team)))


# --- Changes -------------------------------------------------------------------

KINDS = ("swap", "pair", "two", "move", "item", "spread", "ability", "mega", "reroll")
KIND_WEIGHTS = (0.25, 0.15, 0.10, 0.22, 0.10, 0.06, 0.04, 0.05, 0.03)


def canon(team: Team) -> tuple:
    """A team as a set of sets (member and move order don't matter)."""
    return tuple(sorted((to_id(m.species), to_id(m.item), to_id(m.ability), m.nature, m.points,
                         tuple(sorted(to_id(x) for x in m.moves))) for m in team))


def spread_differs(a: Member, nature: str, points) -> bool:
    """A spread worth trying: another nature, or 8+ stat points moved (a
    point or two can't show in any number of games the builder plays)."""
    return nature != a.nature or sum(abs(x - y) for x, y in zip(points, a.points)) >= 8


def mutate(team: Team, pool: Pool, rng: np.random.Generator, keep: set[str], kind: str | None = None) -> Team | None:
    """A variant of the team, or None if the draw led nowhere. One change
    (a member swapped for another species; one move, item, ability or
    spread; with or without its Mega Stone; its whole set redrawn), or two
    at once: "pair" swaps two members for two that players pair with each
    other and the rest, "two" makes two single changes."""
    kind = kind or KINDS[rng.choice(len(KINDS), p=KIND_WEIGHTS)]
    if kind == "two":
        singles = ("swap", "move", "item", "spread", "ability", "mega")
        w = np.array([KIND_WEIGHTS[KINDS.index(k)] for k in singles])
        first = mutate(team, pool, rng, keep, singles[rng.choice(len(singles), p=w / w.sum())])
        return None if first is None else mutate(first, pool, rng, keep, singles[rng.choice(len(singles), p=w / w.sum())])
    free = [j for j, x in enumerate(team) if to_id(x.species) not in keep]
    if kind == "pair":
        if len(free) < 2:
            return None
        i, j = sorted(int(x) for x in rng.choice(free, 2, replace=False))
        rest = tuple(x for k, x in enumerate(team) if k not in (i, j))
        gone = {pool.d.base_of(to_id(team[k].species)) for k in (i, j)}
        new = []
        for _ in range(2):
            have = rest + tuple(new)
            keys, w = pool.swap_weights(have)
            fresh = np.array([pool.entries[k]["base"] not in gone for k in keys])
            if not fresh.any():
                return None
            w = np.where(fresh, w, 0.0)
            k = keys[rng.choice(len(keys), p=w / w.sum())]
            m = pool.member(k, rng, {x.item for x in have if x.item})
            if m is None:
                return None
            new.append(m)
        out = list(team)
        out[i], out[j] = new
        return tuple(out)
    i = int(rng.integers(len(team)))
    m = team[i]
    key = pool.key_of(m)
    e = pool.entries.get(key)
    taken = {x.item for j, x in enumerate(team) if j != i and x.item}
    if kind == "swap":
        if not free:
            return None
        i = int(rng.choice(free))
        rest = team[:i] + team[i + 1:]
        keys, w = pool.swap_weights(rest)
        own = pool.d.base_of(to_id(team[i].species))
        k = keys[rng.choice(len(keys), p=w)]
        if pool.entries[k]["base"] == own:
            return None  # another species, not the same one redrawn
        new = pool.member(k, rng, {x.item for x in rest if x.item})
    elif e is None:
        return None
    elif kind == "reroll":
        new = pool.member(key, rng, taken)
    elif kind == "move":
        options = [n for n, _ in e["moves"] if n not in m.moves]
        if not options:
            return None
        w = _probs([p for n, p in e["moves"] if n not in m.moves])
        moves = list(m.moves)
        moves[int(rng.integers(len(moves)))] = options[rng.choice(len(options), p=w)]
        new = replace(m, moves=tuple(moves))
    elif kind == "item":
        options = [(n, p) for n, p in e["items"] if n != m.item and n not in taken]
        if e["mega"] or not options:
            return None
        new = replace(m, item=options[rng.choice(len(options), p=_probs([p for _, p in options]))][0])
    elif kind == "spread":
        options = [(n, pts, p) for n, pts, p in e["spreads"] if spread_differs(m, n, pts)]
        if not options:
            return None
        n, pts, _ = options[rng.choice(len(options), p=_probs([p for *_, p in options]))]
        new = replace(m, nature=n, points=pts)
    elif kind == "ability":
        options = [n for n, _ in e["abilities"] if n != m.ability]
        if not options:
            return None
        new = replace(m, ability=options[int(rng.integers(len(options)))])
    else:  # mega: the same species with or without its Mega Stone
        base = pool.d.base_of(key)
        other = [k for k, v in pool.entries.items() if v["base"] == base and k != key]
        if not other:
            return None
        new = pool.member(other[int(rng.integers(len(other)))], rng, taken)
    if new is None:
        return None
    if (new.species, new.item, new.ability, set(new.moves)) == (m.species, m.item, m.ability, set(m.moves)) \
            and not spread_differs(m, new.nature, new.points):
        return None  # the same set, a point or two moved
    return team[:i] + (new,) + team[i + 1:]


def neighbours(team: Team, pool: Pool, rng: np.random.Generator, n: int, keep: set[str], rules: bool,
               tries: int = 40) -> list[Team]:
    """Up to `n` distinct legal one-change variants within the rules, or, if
    `team` breaks some, breaking none further than it does (each rule on its
    own: its sets can still improve, and the score's penalty favours the
    variants that break less)."""
    limit = rule_excess(team, pool, rules)
    seen, out = {canon(team)}, []
    for _ in range(n * tries):
        if len(out) >= n:
            break
        t = mutate(team, pool, rng, keep)
        if t is None or canon(t) in seen:
            continue  # nothing new (a move order or a no-op isn't a change)
        seen.add(canon(t))
        if within(rule_excess(t, pool, rules), limit) and legal(t, pool):
            out.append(t)
    return out


def from_core(keep: list[str], pool: Pool, rng: np.random.Generator, rules: bool) -> Team:
    """A team built around `keep`: each next member the one players pair
    most with those chosen so far (within the rules where possible)."""
    team: list[Member] = []
    megas = lambda t: sum(pool.key_of(x) != to_id(x.species) for x in t)
    for name in keep:
        sid = to_id(name)
        # Its most used form (with or without its Mega Stone), within two Megas.
        forms = sorted((k for k, v in pool.entries.items() if v["base"] == sid), key=lambda k: -pool.entries[k]["count"])
        forms = [k for k in forms if not pool.entries[k]["mega"] or megas(team) < 2]
        if not forms:
            raise SystemExit(f"no usage data for {name}")
        team.append(pool.member(forms[0], rng, {x.item for x in team if x.item}, top=True))
    while len(team) < 6:
        bases = {pool.d.base_of(to_id(x.species)) for x in team}
        best = None
        for k in sorted((k for k, v in pool.entries.items() if v["base"] not in bases),
                        key=lambda k: -(pool.affinity(k, tuple(team)) + 1e-6 * pool.entries[k]["count"]))[:60]:
            m = pool.member(k, rng, {x.item for x in team if x.item}, top=True)
            if m is None or megas(tuple(team) + (m,)) > 2:
                continue
            # The most paired-with member that breaks the fewest rules.
            breaks = rule_cost(tuple(team) + (m,), pool, rules)
            if best is None or breaks < best[0]:
                best = (breaks, m)
            if breaks == 0:
                break
        if best is None:
            raise SystemExit("couldn't fill the team")
        team.append(best[1])
    return tuple(team)


# --- Scoring ------------------------------------------------------------------

def objective(p: np.ndarray, w: np.ndarray, tail: float, alpha: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per row of win chances `p` [n, field]: (score, weighted mean, mean
    of the worst `alpha` share of the weight)."""
    w = w / w.sum()
    mean = p @ w
    worst = np.zeros(len(p))
    for r, row in enumerate(p):
        order = np.argsort(row)
        acc = val = 0.0
        for j in order:
            take = min(w[j], alpha - acc)
            if take <= 0:
                break
            val += take * row[j]
            acc += take
        worst[r] = val / max(acc, 1e-12)
    return (1 - tail) * mean + tail * worst, mean, worst


class Arena:
    """The corpus plus a folder of candidate teams, for the network to
    estimate and play."""

    def __init__(self, sources: list[str], teams: list[Team], envs: int, seed: int):
        self.dir = Path(tempfile.mkdtemp(prefix="build-"))
        for i, t in enumerate(teams):
            (self.dir / f"cand{i:05d}.txt").write_text(team_paste(t), encoding="utf-8")
        self.env = SelfPlayEnv(envs, corpus=list(sources) + [str(self.dir)], seed=seed)
        names = self.env.team_names()
        where = {n: i for i, n in enumerate(names)}
        prefix = self.dir.name + "/"
        self.index = [where.get(f"{prefix}cand{i:05d}") for i in range(len(teams))]

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


@torch.no_grad()
def preview_matrix(model, arena: Arena, cands: list[int], field: list[int], amp: bool) -> np.ndarray:
    """The network's win chance at team preview for each candidate (arena
    team index) against each field team, averaged over both sides."""
    env = arena.env
    pairs = [(c, f) for c in cands for f in field]
    both = pairs + [(f, c) for c, f in pairs]
    v0 = np.zeros(len(both))
    n = env.num_envs
    dev = next(model.parameters()).device
    for s in range(0, len(both), n):
        chunk = both[s:s + n]
        env.set_matchups(chunk)
        obs = env.observe()
        with autocast(dev, amp):
            _, v = model(*(t.to(dev) for t in to_tensors(obs)))
        v = v.float().cpu().numpy().reshape(-1, 2)
        v0[s:s + len(chunk)] = ((v[:, 0] - v[:, 1]) / 2)[:len(chunk)]
    k = len(pairs)
    p = ((v0[:k] - v0[k:]) / 2 + 1) / 2
    return p.reshape(len(cands), len(field))


@torch.no_grad()
def play(model, env: SelfPlayEnv, pairs: list[tuple[int, int]], amp: bool, out=print, every: float = 60.0):
    """Each pair played once, the policy sampled on both sides. Returns
    (side 0 team, side 1 team, side 0 score, side 0 preview, side 1 preview)
    per game; a preview is the order picked ("2536": the first two lead)."""
    from selfplay.train import act
    env.set_matchups(pairs)
    n = env.num_envs
    obs = env.observe()
    table = {i: c.split()[-1] for i, c in env.legal_choices(0, 0)} if obs.decisions[0, 0] == 1 else {}
    current = [env.game_teams(g) for g in range(n)]
    picks = [["", ""] for _ in range(n)]
    # Which scheduled game each one plays: the env deals the schedule out in
    # order (finished games restart in game order), and keeps going past its
    # end; those extra games don't count, so short games aren't favoured.
    slot = list(range(n))
    next_slot = n
    results: list = [None] * len(pairs)
    left = len(pairs)
    start = last = time.time()
    while left:
        obs = env.observe()
        rows = np.flatnonzero(obs.decisions.reshape(-1) != 0)
        actions = np.full((n, 2), -1, np.int64)
        if len(rows):
            a, _, _ = act(model, to_tensors(obs, rows), amp=amp)
            actions.reshape(-1)[rows] = a.numpy()
            for r in rows[obs.decisions.reshape(-1)[rows] == 1]:
                picks[r // 2][r % 2] = table.get(int(actions.reshape(-1)[r]), "?")
        res = env.step(actions)
        for g in np.flatnonzero(res.done):
            if slot[g] < len(pairs):
                a_, b_ = current[g]
                results[slot[g]] = (a_, b_, float(res.reward[g, 0] + 1) / 2, picks[g][0], picks[g][1])
                left -= 1
            slot[g] = next_slot
            next_slot += 1
            current[g] = env.game_teams(g)
            picks[g] = ["", ""]
        if time.time() - last > every:
            last = time.time()
            done = len(pairs) - left
            out(f"    {done:,}/{len(pairs):,} games, {done / (last - start):.0f}/s")
    return results


def race(model, arena: Arena, cands: list[int], field: list[int], w: np.ndarray, rounds: list[int],
         keep: float, tail: float, alpha: float, amp: bool, rng, out=print, penalty=None):
    """Successive halving: every live candidate plays each field team
    rounds[r] games a side; after each round but the last the better half
    (by score; the first candidate, the current team, always stays) goes
    on; `penalty` (per candidate) comes off its score. Returns per candidate
    the score sums and game counts [field], and the games themselves."""
    penalty = np.zeros(len(cands)) if penalty is None else np.asarray(penalty, np.float64)
    sums = np.zeros((len(cands), len(field)))
    counts = np.zeros((len(cands), len(field)))
    games = []
    alive = list(range(len(cands)))
    pos = {f: j for j, f in enumerate(field)}
    for r, g in enumerate(rounds):
        pairs = []
        for i in alive:
            for f in field:
                pairs += [(cands[i], f)] * g + [(f, cands[i])] * g
        order = rng.permutation(len(pairs))
        played = play(model, arena.env, [pairs[k] for k in order], amp, out)
        who = {c: i for i, c in enumerate(cands)}
        for a, b, s, pa, pb in played:
            if a in who and b in pos:
                i, j, sc, pick = who[a], pos[b], s, pa
            elif b in who and a in pos:
                i, j, sc, pick = who[b], pos[a], 1 - s, pb
            else:
                continue
            sums[i, j] += sc
            counts[i, j] += 1
            games.append((i, j, sc, pick))
        if r < len(rounds) - 1 and len(alive) > 2:
            score, _, _ = objective(sums[alive] / np.maximum(counts[alive], 1), w, tail, alpha)
            ranked = [alive[k] for k in np.argsort(-(score - penalty[alive]))]
            n_keep = max(2, math.ceil(len(alive) * keep))
            alive = [0] + [i for i in ranked if i != 0][:n_keep - 1]
    return sums, counts, games, alive


def score_weights(p: np.ndarray, w: np.ndarray, tail: float, alpha: float) -> np.ndarray:
    """The weight of each field team in a row's score (the mean's, plus the
    worst matchups'): score = weights @ p."""
    w = w / w.sum()
    worst = np.zeros(len(p))
    acc = 0.0
    for j in np.argsort(p):
        take = min(w[j], alpha - acc)
        if take <= 0:
            break
        worst[j] = take
        acc += take
    return (1 - tail) * w + tail * worst / max(acc, 1e-12)


def score_se(p: np.ndarray, n: np.ndarray, w: np.ndarray, tail: float = 0.0, alpha: float = 0.25) -> float:
    """Standard error of a row's score (its worst matchups taken as given)."""
    c = score_weights(p, w, tail, alpha)
    return float(math.sqrt(np.sum(c ** 2 * p * (1 - p) / np.maximum(n, 1))))


# --- Reports ------------------------------------------------------------------

def report(team: Team, names: list[str], field: list[int], w: np.ndarray, sums, counts, games,
           tail: float, alpha: float) -> list[str]:
    p = sums / np.maximum(counts, 1)
    score, mean, worst = objective(p[None], w, tail, alpha)
    lines = [team_paste(team), f"score {score[0]:.3f}: mean {mean[0]:.3f} against the field "
             f"(+-{score_se(p, counts, w):.3f}), worst {alpha:.0%} {worst[0]:.3f}; {int(counts.sum())} games", ""]
    lines.append(f"{'field team':60s} {'win':>6s} {'games':>6s}  most picked bring (its win rate)")
    by_field = defaultdict(lambda: defaultdict(list))
    leads, brings = defaultdict(list), defaultdict(list)
    label = lambda digits: " + ".join(team[int(c) - 1].species for c in digits)
    for _i, j, s, pick in games:
        if len(pick) == 4 and pick.isdigit():
            by_field[j]["".join(sorted(pick))].append(s)
            leads["".join(sorted(pick[:2]))].append(s)
            brings["".join(sorted(pick))].append(s)
    for j in np.argsort(p):
        common = max(by_field[j].items(), key=lambda kv: len(kv[1]), default=None)
        extra = f"  {label(common[0])} ({np.mean(common[1]):.2f}, {len(common[1])})" if common else ""
        lines.append(f"{names[field[j]][:60]:60s} {p[j]:6.3f} {int(counts[j]):6d}{extra}")
    lines += ["", "leads (pairs), by games:"]
    for k, v in sorted(leads.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"  {label(k):40s} {np.mean(v):.3f} over {len(v)}")
    lines += ["", "brings, by games:"]
    for k, v in sorted(brings.items(), key=lambda kv: -len(kv[1]))[:15]:
        lines.append(f"  {label(k):60s} {np.mean(v):.3f} over {len(v)}")
    return lines


def field_teams(names: list[str], weights: list[float], vs: list[str], top_weight: float) -> list[int]:
    corpus = [i for i, n in enumerate(names) if "/" not in n]
    if vs == ["placed"]:
        return [i for i in corpus if weights[i] >= top_weight]
    if vs == ["all"]:
        return corpus
    from selfplay.meta import select
    return select(names, vs)


def load_start(spec: str) -> Team:
    from selfplay.showdown.team import find_team
    path = Path(spec)
    if not path.is_file():
        corpus = Path(DEFAULT_CORPUS[0]) / (spec if spec.endswith(".txt") else spec + ".txt")
        path = corpus if corpus.is_file() else find_team(spec)
    return from_paste(path.read_text(encoding="utf-8-sig"))


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--start", default=None, help="the team to start from (a paste, or a team's name)")
    p.add_argument("--keep", nargs="*", default=[], help="Pokemon never swapped out (their sets may change)")
    p.add_argument("--vs", nargs="+", default=["placed"],
                   help="the field: 'placed' (corpus teams placed --top or better), 'all', or names/prefixes")
    p.add_argument("--top", type=int, default=8, help="'placed' means this placement or better")
    p.add_argument("--steps", type=int, default=1000, help="stop after this many steps")
    p.add_argument("--minutes", type=float, default=120.0, help="... or after this long (0: only --steps)")
    p.add_argument("--neighbours", type=int, default=300, help="variants screened per step")
    p.add_argument("--race", type=int, default=11, help="best variants by the estimate that play games")
    p.add_argument("--rounds", default="4,8,16", help="games a side per field team, per racing round")
    p.add_argument("--keep-share", type=float, default=0.5, help="share of the racers each round keeps")
    p.add_argument("--tail", type=float, default=0.3, help="weight of the worst matchups in the score")
    p.add_argument("--alpha", type=float, default=0.25, help="the worst matchups: this share of the field")
    p.add_argument("--confirm", type=int, default=16,
                   help="games a side per field team for the race winner and the current team, fresh")
    p.add_argument("--accept-se", type=float, default=2.0,
                   help="the winner must beat the current team on those games by this many standard errors")
    p.add_argument("--no-rules", action="store_true", help="no type rules or Mega limit")
    p.add_argument("--rule-penalty", type=float, default=0.02,
                   help="score taken off per rule a team breaks (a start that breaks them may keep doing so)")
    p.add_argument("--final-games", type=int, default=64, help="games a side per field team for the report")
    p.add_argument("--envs", type=int, default=2048)
    p.add_argument("--out", default="runs/build")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()

    rng = np.random.default_rng(a.seed)
    pool = Pool()
    rules = not a.no_rules
    keep = {to_id(k) for k in a.keep}
    team = load_start(a.start) if a.start else from_core(a.keep, pool, rng, rules)
    start = team
    missing = keep - {to_id(m.species) for m in team}
    if missing:
        raise SystemExit(f"the start team has no {', '.join(sorted(missing))}")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    log = open(out / "log.jsonl", "a", encoding="utf-8")
    torch.set_grad_enabled(False)
    model = load(a.model, a.device)
    amp = a.device.startswith("cuda")
    sources = list(DEFAULT_CORPUS)
    base = SelfPlayEnv(1, corpus=sources)
    names, weights = base.team_names(), base.team_weights()
    top_weight = {1: 8.0, 2: 6.0, 4: 5.0, 8: 4.0, 16: 3.0, 32: 2.0}.get(a.top, 1.5)
    field = field_teams(names, weights, a.vs, top_weight)
    w = np.array([weights[f] for f in field], np.float64)
    rounds = [int(x) for x in a.rounds.split(",")]
    print(f"field: {len(field)} teams; start:\n{team_paste(team)}")
    breaks = rule_breaks(team, pool, rules)
    if breaks:
        print("the start breaks the rules: " + "; ".join(breaks)
              + f" (variants may break none of them further; each step past a rule costs "
              f"{a.rule_penalty} of score)")
    t0 = time.time()
    (out / "start.txt").write_text(team_paste(team), encoding="utf-8")
    for step in range(1, a.steps + 1):
        if a.minutes and time.time() - t0 > 60 * a.minutes:
            break
        cands = neighbours(team, pool, rng, a.neighbours, keep, rules)
        if not cands:
            print("no legal variants; stopping")
            break
        arena = Arena(sources, [team] + cands, a.envs, seed=a.seed + step)
        try:
            ok = [i for i, ix in enumerate(arena.index) if ix is not None]
            if 0 not in ok:
                raise SystemExit(f"the engine rejects the current team: {arena.env.rejected()[:3]}")
            est = preview_matrix(model, arena, [arena.index[i] for i in ok], field, amp)
            sc, _, _ = objective(est, w, a.tail, a.alpha)
            pen = {i: a.rule_penalty * rule_cost(([team] + cands)[i], pool, rules) for i in ok}
            sc = sc - np.array([pen[i] for i in ok])
            # Half the racers by the estimate, half drawn from its better
            # half: among close variants its top picks are the ones it
            # overrates most (on the sand team it ranked its racers
            # backwards), so it mostly trims the clear losers.
            order = [ok[k] for k in np.argsort(-sc) if ok[k] != 0]
            n_top = math.ceil(a.race / 2)
            ranked = order[:n_top]
            rest = order[n_top:max(n_top, len(order) // 2)]
            if rest and a.race > n_top:
                pick = rng.choice(len(rest), min(len(rest), a.race - n_top), replace=False)
                ranked += [rest[k] for k in sorted(pick)]
            racers = [0] + ranked
            est_of = {ok[k]: sc[k] for k in range(len(ok))}
            pens = np.array([pen[i] for i in racers])
            sums, counts, games, alive = race(model, arena, [arena.index[i] for i in racers], field, w, rounds,
                                              a.keep_share, a.tail, a.alpha, amp, rng,
                                              out=lambda s: print(s, flush=True), penalty=pens)
            pr = sums / np.maximum(counts, 1)
            score, mean, worst = objective(pr, w, a.tail, a.alpha)
            score = score - pens  # the rules broken, priced
            final = [i for i in alive]
            best = max((i for i in final if i != 0), key=lambda i: score[i], default=None)
            gain = score[best] - score[0] if best is not None else 0.0
            confirm = None
            if best is not None and gain > 0:
                # The race's winner is the luckiest of a dozen: it replaces
                # the current team only if it wins again on fresh games.
                cs, cc, _, _ = race(model, arena, [arena.index[0], arena.index[racers[best]]], field, w,
                                    [a.confirm], 1.0, a.tail, a.alpha, amp, rng,
                                    out=lambda s: print(s, flush=True))
                cp = cs / np.maximum(cc, 1)
                csc, _, _ = objective(cp, w, a.tail, a.alpha)
                csc = csc - pens[[0, best]]
                cse = math.hypot(score_se(cp[0], cc[0], w, a.tail, a.alpha),
                                 score_se(cp[1], cc[1], w, a.tail, a.alpha))
                confirm = (float(csc[1] - csc[0]), cse)
        finally:
            arena.close()
        # How well the estimate ranked the racers (once all have games).
        raced = [k for k in range(len(racers)) if counts[k].sum()]
        corr = float(np.corrcoef([est_of[racers[k]] for k in raced], [score[k] for k in raced])[0, 1]) \
            if len(raced) > 2 else float("nan")
        accepted = confirm is not None and confirm[0] > a.accept_se * confirm[1]
        old = team
        if accepted:
            team = cands[racers[best] - 1]
            (out / f"step{step:03d}.txt").write_text(team_paste(team), encoding="utf-8")
            (out / "best.txt").write_text(team_paste(team), encoding="utf-8")
        what = changes(old, cands[racers[best] - 1]) if best is not None else "-"
        again = f"; on fresh games {confirm[0]:+.3f} +-{confirm[1]:.3f}" if confirm else ""
        print(f"step {step}: current {score[0]:.3f} (mean {mean[0]:.3f}, worst {worst[0]:.3f}); best challenger "
              f"{score[best] if best is not None else float('nan'):.3f} ({gain:+.3f} in the race{again}): {what} -> "
              f"{'accepted' if accepted else 'kept'}; {len(cands)} variants, estimate vs games r={corr:.2f}; "
              f"{(time.time() - t0) / 60:.0f} min", flush=True)
        # Every finalist, for judging how clear the step was.
        tried = sorted(((float(score[k] - score[0]), changes(old, cands[racers[k] - 1]), int(counts[k].sum()))
                        for k in final if k != 0), reverse=True)
        for g_, what_, n_ in tried[1:4]:
            print(f"    also: {g_:+.3f} ({n_} games) {what_}")
        log.write(json.dumps({"step": step, "score": float(score[0]), "mean": float(mean[0]), "worst": float(worst[0]),
                              "challenger": float(score[best]) if best is not None else None, "change": what,
                              "confirm": confirm,
                              "accepted": bool(accepted), "variants": len(cands), "estimate_corr": corr,
                              "rules_broken": rule_excess(team, pool, rules), "finalists": tried,
                              "minutes": (time.time() - t0) / 60}) + "\n")
        log.flush()
    # Final: the team and the start against the field, on fresh games (the
    # racing's estimates of the team that won are biased up by the
    # selection), and the team's report.
    finalists = [team] + ([start] if start != team else [])
    arena = Arena(sources, finalists, a.envs, seed=a.seed + 99_999)
    try:
        sums, counts, games, _ = race(model, arena, [arena.index[i] for i in range(len(finalists))], field, w,
                                      [a.final_games], 1.0, a.tail, a.alpha, amp, rng,
                                      out=lambda s: print(s, flush=True))
    finally:
        arena.close()
    lines = report(team, names, field, w, sums[0], counts[0], [g for g in games if g[0] == 0], a.tail, a.alpha)
    if len(finalists) > 1:
        pr = sums / np.maximum(counts, 1)
        score, mean, worst = objective(pr, w, a.tail, a.alpha)
        se = math.hypot(score_se(pr[0], counts[0], w, a.tail, a.alpha), score_se(pr[1], counts[1], w, a.tail, a.alpha))
        lines.insert(2, f"the start, on the same fresh games: score {score[1]:.3f} (mean {mean[1]:.3f}, worst "
                        f"{worst[1]:.3f}); the team is {score[0] - score[1]:+.3f} better (+-{se:.3f}), "
                        f"{mean[0] - mean[1]:+.3f} on the mean")
        lines.insert(3, "changes from the start: " + changes(start, team))
    breaks = rule_breaks(team, pool, rules)
    if breaks:
        lines.append("\nstill breaks the rules: " + "; ".join(breaks))
    (out / "best.txt").write_text(team_paste(team), encoding="utf-8")
    (out / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwritten to {out}")


if __name__ == "__main__":
    main()
