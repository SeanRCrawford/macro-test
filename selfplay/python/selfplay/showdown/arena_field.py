"""The teams the strongest VGC Arena bots play, as pastes: a field for the
team builder (`selfplay.build --vs-dir`), or teams to add to the corpus.

    python -m selfplay.showdown.arena_field runs/arena --out runs/arena_field --top-bots 15

From the battles `selfplay.showdown.arena_battles` downloaded: on the open
team sheets ladder each log shows both teams' sets (all but stat points,
which the usage spreads fill, as for any paste without EVs). Takes the
teams of the `--top-bots` bots with the highest latest rating (or those
rated `--min-rating` or more), one paste per distinct team played
`--min-battles` (5) times or more (many bots change team every battle),
and writes OUT/weights.json: each team's weight in the field, the square
root of its battles by default, so a team weighs more the more the strong
bots play it without one team deciding the score. Teams the engine can't
play yet are listed and left out.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from selfplay.showdown.arena_battles import bots_of, rating_after, winner_of
from selfplay.showdown.data import data, to_id
from selfplay.showdown.tracker import unpack_team

OTS = "gen9championsvgc2026regmcots"


def showteams(log: str) -> dict[str, str]:
    """The packed team of each side ("p1"/"p2") in a log's |showteam| lines."""
    out = {}
    for line in log.splitlines():
        if line.startswith("|showteam|"):
            _, _, side, packed = line.split("|", 3)
            out[side] = packed
    return out


def paste_of(sets: list[dict]) -> str:
    """Sets from a team sheet as a paste (no EVs: the usage spread fills
    them when it's loaded)."""
    d = data()
    name = lambda table, i: (table.get(i) or {}).get("name", i)
    out = []
    for s in sets:
        lines = [name(d.species, s["species"]) + (f" @ {name(d.items, s['item'])}" if s["item"] else ""),
                 f"Ability: {name(d.abilities, s['ability'])}", "Level: 50"]
        if s["nature"]:
            lines.append(f"{s['nature']} Nature")
        lines += [f"- {name(d.moves, m)}" for m in s["moves"]]
        out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


def team_key(sets: list[dict]) -> tuple:
    return tuple(sorted((s["species"], s["item"], s["ability"], s["nature"], tuple(sorted(s["moves"])))
                        for s in sets))


def strongest(entries: list[dict], top: int, min_rating: float | None) -> dict[str, float]:
    """Bots by their latest rating: the `top` best, or all rated
    `min_rating` or more."""
    rating = {}
    for e in sorted(entries, key=lambda e: int(e["id"])):
        for side, b in zip(("p1", "p2"), bots_of(e)):
            r = rating_after(e, side)
            if r is not None:
                rating[b] = r
    ranked = sorted(rating.items(), key=lambda kv: -kv[1])
    if min_rating is not None:
        return {b: r for b, r in ranked if r >= min_rating}
    return dict(ranked[:top])


def collect(folder: Path, bots: dict[str, float], fmt: str = OTS) -> list[dict]:
    """Each distinct team the bots played in `fmt`: its sets, the bots that
    played it, battles and wins."""
    entries = [json.loads(line) for line in (folder / "battles.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    teams: dict[tuple, dict] = {}
    for e in entries:
        if e.get("format") != fmt:
            continue
        names = bots_of(e)
        if not any(b in bots for b in names):
            continue
        log = folder / f"{e['id']}.log"
        if not log.exists():
            continue
        sheets = showteams(log.read_text(encoding="utf-8"))
        for side, b in zip(("p1", "p2"), names):
            if b not in bots or side not in sheets:
                continue
            sets = unpack_team(sheets[side])
            if len(sets) != 6:
                continue
            t = teams.setdefault(team_key(sets), {"sets": sets, "bots": defaultdict(int), "battles": 0, "wins": 0})
            t["bots"][b] += 1
            t["battles"] += 1
            t["wins"] += winner_of(e) == b
    return sorted(teams.values(), key=lambda t: -t["battles"])


def write_field(teams: list[dict], out: Path, say=print, min_battles: int = 1,
                weight: str = "sqrt") -> dict[str, float]:
    """One paste per team played `min_battles` or more times that the engine
    can play (named after the bot that played it most), and weights.json:
    name -> its battles, their square root ("sqrt") or 1 ("equal")."""
    from selfplay.showdown.team import check_team, complete_paste
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.txt"):
        old.unlink()
    weights, used = {}, defaultdict(int)
    for t in teams:
        if t["battles"] < min_battles:
            continue
        text = paste_of(t["sets"])
        errors, _ = check_team(complete_paste(text)[0])
        bot = max(t["bots"].items(), key=lambda kv: kv[1])[0]
        if errors:
            say(f"  left out a team of {bot} ({t['battles']} battles): {errors[0]}")
            continue
        used[bot] += 1
        safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in bot)
        name = f"{safe} arena team {used[bot]}"
        (out / f"{name}.txt").write_text(text, encoding="utf-8")
        weights[name] = {"battles": float(t["battles"]), "sqrt": t["battles"] ** 0.5, "equal": 1.0}[weight]
    (out / "weights.json").write_text(json.dumps(weights, indent=1), encoding="utf-8")
    return weights


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("battles", help="the folder selfplay.showdown.arena_battles downloaded to")
    p.add_argument("--out", default="runs/arena_field")
    p.add_argument("--top-bots", type=int, default=15, help="the bots with the highest latest rating")
    p.add_argument("--min-rating", type=float, default=None, help="instead: every bot rated this or more")
    p.add_argument("--format", default=OTS, help="the ladder (team sheets are shown on the open one only)")
    p.add_argument("--min-battles", type=int, default=5,
                   help="only teams played this often (many bots change team every battle)")
    p.add_argument("--weight", choices=["sqrt", "battles", "equal"], default="sqrt",
                   help="a team's weight in the field: the square root of its battles (default), its battles, or 1")
    a = p.parse_args()
    folder = Path(a.battles)
    entries = [json.loads(line) for line in (folder / "battles.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    bots = strongest(entries, a.top_bots, a.min_rating)
    print("bots: " + ", ".join(f"{b} ({r:.0f})" for b, r in bots.items()))
    teams = collect(folder, bots, a.format)
    weights = write_field(teams, Path(a.out), min_battles=a.min_battles, weight=a.weight)
    kept = sum(t["battles"] for t in teams if t["battles"] >= a.min_battles)
    print(f"{len(weights)} teams written to {a.out}, played {a.min_battles}+ times: {kept} of the "
          f"{sum(t['battles'] for t in teams)} battles; weights by {a.weight}; the most played:")
    for name, n in sorted(weights.items(), key=lambda kv: -kv[1])[:15]:
        species = [ln.split(" @")[0] for ln in (Path(a.out) / f"{name}.txt").read_text(encoding="utf-8").split("\n\n")]
        print(f"  {n:7.1f}  {name}: {' / '.join(species)}")


if __name__ == "__main__":
    main()
