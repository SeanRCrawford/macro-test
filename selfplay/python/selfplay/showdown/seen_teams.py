"""Opponents' teams, collected for training: every open team sheet the bot
plays against is saved as a paste in data/seen_teams (one file per
distinct team, however often it's met), and training always includes that
folder (selfplay.env.DEFAULT_CORPUS) as a fixed share of its games (20%),
a team weighted by the square root of how often it was met, so the bot
practises what it meets without the corpus being swamped.

The Showdown client adds each opponent's sheet after every battle. To add
the ones already played, or the arena's strongest bots' teams:

    python -m selfplay.showdown.seen_teams --logs "runs/showdown/*.log"
    python -m selfplay.showdown.seen_teams --arena runs/arena --top-bots 30 --min-battles 2
    python -m selfplay.showdown.seen_teams                 # what's there

A sheet shows everything but stat points, which the usage spreads fill
when the corpus loads it. Teams the engine can't play yet stay in the
folder and are left out of training (selfplay.corpus lists them).
data/seen_teams/index.json keeps, per team, who played it and how often.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from pathlib import Path

from selfplay.showdown.tracker import unpack_team

SEEN = Path(__file__).resolve().parents[4] / "data" / "seen_teams"


def team_key(sets: list[dict]) -> str:
    return json.dumps(sorted([s["species"], s["item"], s["ability"], s["nature"], sorted(s["moves"])]
                             for s in sets))


def safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip() or "opponent"


def add(sets: list[dict], who: str, folder: Path = SEEN, n: int = 1) -> tuple[str, bool]:
    """Record a team `who` played (`n` times): its paste the first time it's
    seen. Returns (its name, whether it was new)."""
    from selfplay.showdown.arena_field import paste_of
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "index.json"
    index = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    key = team_key(sets)
    now = time.strftime("%Y-%m-%d")
    entry = index.get(key)
    new = entry is None
    if new:
        k = 1
        while (folder / f"{safe(who)} {k}.txt").exists():
            k += 1
        entry = index[key] = {"name": f"{safe(who)} {k}", "seen": 0, "by": {}, "first": now}
        (folder / f"{entry['name']}.txt").write_text(paste_of(sets), encoding="utf-8")
    entry["seen"] += n
    entry["by"][who] = entry["by"].get(who, 0) + n
    entry["last"] = now
    path.write_text(json.dumps(index, indent=1), encoding="utf-8")
    # The corpus loader weighs the folder's teams against each other by
    # this: the square root of how often each was met.
    weights = {e["name"]: round(e["seen"] ** 0.5, 4) for e in index.values()}
    (folder / "weights.json").write_text(json.dumps(weights, indent=1), encoding="utf-8")
    return entry["name"], new


def opponents_in(log: str, us: str | None = None) -> list[tuple[str, list[dict]]]:
    """(player name, sets) for each team sheet in a battle log but ours (`us`
    is our side, "p1"/"p2"; None: every side's)."""
    players, sheets = {}, []
    for line in log.splitlines():
        a = line.split("|")
        if line.startswith("|player|") and len(a) > 3 and a[3]:
            players[a[2]] = a[3]
        elif line.startswith("|showteam|") and len(a) > 3 and a[2] != us:
            sets = unpack_team("|".join(a[3:]))
            if len(sets) == 6:
                sheets.append((a[2], sets))
    return [(players.get(side, side), sets) for side, sets in sheets]


def our_side(log: str) -> str | None:
    """Our side in a log the client saved (from its requests)."""
    for line in log.splitlines():
        if line.startswith("|request|{"):
            side = json.loads(line[9:]).get("side", {}).get("id")
            if side:
                return side
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--logs", nargs="*", default=[], help="battle logs the client saved (their opponents' sheets)")
    p.add_argument("--arena", default=None, help="a folder selfplay.showdown.arena_battles downloaded")
    p.add_argument("--top-bots", type=int, default=30, help="with --arena: the bots with the highest rating")
    p.add_argument("--min-rating", type=float, default=None, help="with --arena: instead, bots rated this or more")
    p.add_argument("--min-battles", type=int, default=2, help="with --arena: teams played this often")
    p.add_argument("--folder", default=str(SEEN))
    a = p.parse_args()
    folder = Path(a.folder)
    added = 0
    logs = sorted({f for x in a.logs for f in (glob.glob(x) if glob.has_magic(x) else [x])})
    for path in logs:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
        for who, sets in opponents_in(text, our_side(text)):
            added += add(sets, who, folder)[1]
    if a.arena:
        from selfplay.showdown.arena_field import collect, strongest
        entries = [json.loads(line) for line in (Path(a.arena) / "battles.jsonl").read_text(encoding="utf-8")
                   .splitlines() if line.strip()]
        bots = strongest(entries, a.top_bots, a.min_rating)
        for t in collect(Path(a.arena), bots):
            if t["battles"] >= a.min_battles:
                who = max(t["bots"].items(), key=lambda kv: kv[1])[0]
                added += add(t["sets"], who, folder, n=t["battles"])[1]
    index_path = folder / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else {}
    print(f"{added} new teams; {len(index)} in {folder}")
    for e in sorted(index.values(), key=lambda e: -e["seen"])[:15]:
        print(f"  {e['seen']:5d}  {e['name']}  (by {', '.join(sorted(e['by'], key=e['by'].get, reverse=True)[:3])})")


if __name__ == "__main__":
    main()
