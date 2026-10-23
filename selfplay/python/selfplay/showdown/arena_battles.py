"""Download VGC Arena's finished battles (https://vgc.bot: public, free to
download for analysis or training) and keep the copy up to date.

    python -m selfplay.showdown.arena_battles --out runs/arena
    python -m selfplay.showdown.arena_battles --out runs/arena --format gen9championsvgc2026regmcots

Lists the battles after the highest id already downloaded, oldest first
(`/api/battles?after=ID`, as the arena's guide says, so none is skipped),
saves each log once as OUT/<id>.log (the Showdown protocol as a spectator
saw it: on the open team sheets ladder both teams' sets, but neither bot's
private choices) and its entry (format, both bots, winner, turns) as a
line of OUT/battles.jsonl. Run it again later to fetch only the new ones.
It keeps under the API's 300 requests a minute and waits as told on a 429.

At the end: battles per format, and the bots with the most battles, with
their win rates.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from selfplay.showdown.client import USER_AGENT

BASE = "https://vgc.bot"


class Api:
    """GETs to the arena, spaced to stay under its rate limit."""

    def __init__(self, base: str = BASE, per_minute: int = 240, out=print):
        self.base = base.rstrip("/")
        self.gap = 60.0 / per_minute
        self.last = 0.0
        self.out = out

    def get(self, path: str, params: dict | None = None) -> str:
        url = self.base + path + ("?" + urllib.parse.urlencode(params) if params else "")
        for attempt in range(8):
            wait = self.last + self.gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last = time.monotonic()
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    return r.read().decode("utf-8")
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    delay = float(e.headers.get("Retry-After") or 0) if e.code == 429 else 0.0
                    delay = max(delay, 2.0 ** attempt if e.code >= 500 else delay)
                    self.out(f"  {e.code} from the arena; waiting {delay:.0f}s")
                    time.sleep(delay)
                    continue
                raise
        raise RuntimeError(f"{url}: the arena kept refusing")


def entries_of(reply) -> list[dict]:
    """The battle entries in a list reply (a list, or the list inside an
    object)."""
    if isinstance(reply, list):
        return reply
    if isinstance(reply, dict):
        for key in ("battles", "items", "data", "results"):
            if isinstance(reply.get(key), list):
                return reply[key]
        return next((v for v in reply.values() if isinstance(v, list)), [])
    return []


def side_name(v) -> str | None:
    """A side's bot name: "name", {"name": ...} or {"bot": {"name": ...}}."""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        if isinstance(v.get("bot"), dict):
            return v["bot"].get("name")
        return v.get("name")
    return None


def bots_of(entry: dict) -> list[str]:
    """The two bots' names in an entry (p1 first)."""
    for key in ("bots", "players"):
        v = entry.get(key)
        if isinstance(v, list):
            return [side_name(x) or str(x) for x in v]
    return [n for n in (side_name(entry.get(k)) for k in ("p1", "p2")) if n]


def winner_of(entry: dict) -> str | None:
    """The winning bot's name ("winner" is "p1"/"p2" on the arena)."""
    w = entry.get("winner")
    if w in ("p1", "p2"):
        return side_name(entry.get(w))
    return side_name(w)


def rating_after(entry: dict, side: str) -> float | None:
    v = entry.get(side)
    return v.get("ratingAfter") if isinstance(v, dict) else None


def download(out: Path, api: Api, fmt: str | None = None, bot: str | None = None, most: int = 0,
             say=print) -> int:
    """Fetch the battles newer than those in `out`; returns how many."""
    out.mkdir(parents=True, exist_ok=True)
    index = out / "battles.jsonl"
    known = set()
    last = 0
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            if line.strip():
                e = json.loads(line)
                known.add(int(e["id"]))
                last = max(last, int(e["id"]))
    got = 0
    start = time.monotonic()
    with open(index, "a", encoding="utf-8") as f:
        while True:
            params = {"after": last, "limit": 100}
            if fmt:
                params["format"] = fmt
            if bot:
                params["bot"] = bot
            page = entries_of(json.loads(api.get("/api/battles", params)))
            if not page:
                break
            for e in page:
                bid = int(e["id"])
                last = max(last, bid)
                if bid in known:
                    continue
                path = out / f"{bid}.log"
                if not path.exists():
                    path.write_text(api.get(f"/api/battles/{bid}/log"), encoding="utf-8")
                f.write(json.dumps(e) + "\n")
                f.flush()
                known.add(bid)
                got += 1
                if got % 100 == 0:
                    say(f"  {got} battles ({got / max(time.monotonic() - start, 1e-9) * 60:.0f} a minute), "
                        f"up to id {bid}")
                if most and got >= most:
                    return got
    return got


def summary(out: Path, top: int = 25) -> list[str]:
    entries = [json.loads(line) for line in (out / "battles.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    lines = [f"{len(entries)} battles in {out}"]
    for fmt, n in Counter(e.get("format", "?") for e in entries).most_common():
        lines.append(f"  {fmt}: {n}")
    games, wins, rating = Counter(), Counter(), {}
    for e in sorted(entries, key=lambda e: int(e["id"])):
        w = winner_of(e)
        for side, b in zip(("p1", "p2"), bots_of(e)):
            games[b] += 1
            wins[b] += b == w
            r = rating_after(e, side)
            if r is not None:
                rating[b] = r  # the latest
    if games:
        order = sorted(games, key=lambda b: (-(rating.get(b) or -1e9), -games[b]))[:top] if rating \
            else [b for b, _ in games.most_common(top)]
        lines.append(f"bots by {'rating' if rating else 'battles'} (of {len(games)}):")
        for b in order:
            r = f"  rating {rating[b]:.0f}" if b in rating else ""
            lines.append(f"  {b:24s} {games[b]:6d} battles, won {wins[b] / games[b]:.0%}{r}")
    return lines


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default="runs/arena", help="where the logs and battles.jsonl go")
    p.add_argument("--format", default=None, help="only this format (e.g. gen9championsvgc2026regmcots)")
    p.add_argument("--bot", default=None, help="only battles of this bot")
    p.add_argument("--most", type=int, default=0, help="stop after this many new battles (0: all)")
    p.add_argument("--base", default=BASE, help=argparse.SUPPRESS)
    a = p.parse_args()
    out = Path(a.out)
    got = download(out, Api(a.base), a.format, a.bot, a.most)
    print(f"downloaded {got} new battles")
    print("\n".join(summary(out)))


if __name__ == "__main__":
    main()
