"""Play on a Showdown server: log in, challenge or accept, and play every
battle with the bot (selfplay.showdown.bot).

One connection, any number of battle rooms. Each battle has its own tracker
and bot; their decisions run one at a time in a worker thread so the
connection stays responsive while the search thinks.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from selfplay.showdown.bot import Bot, choice_text
from selfplay.showdown.data import to_id
from selfplay.showdown.team import pack_team
from selfplay.showdown.tracker import Tracker

MAIN_SERVER = "wss://sim3.psim.us/showdown/websocket"
LOGIN_URL = "https://play.pokemonshowdown.com/action.php"
FORMAT = "gen9championsvgc2026regmc"


# The login server sits behind Cloudflare, which refuses Python's default
# "Python-urllib" user agent (HTTP 403).
USER_AGENT = "Mozilla/5.0 (compatible; selfplay-showdown-client)"


def fetch(url: str, form: dict | None = None) -> str:
    """GET `url`, or POST `form` to it; on a 403, try once more with the
    requests package (another client signature) if it is installed."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode()
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise
        try:
            import requests
        except ImportError:
            raise RuntimeError(f"{url} refused the request (HTTP 403); pip install requests "
                               "to try another way") from e
        r = (requests.post(url, data=form, timeout=30) if form is not None
             else requests.get(url, timeout=30))
        if r.status_code == 403:
            raise RuntimeError(f"{url} refused the request (HTTP 403) from this connection; "
                               "try another network, or log in to play.pokemonshowdown.com in a "
                               "browser from it once") from e
        r.raise_for_status()
        return r.text


def login(name: str, password: str | None, challstr: str, url: str = LOGIN_URL) -> str:
    """The assertion for /trn: a registered name needs its password; an
    unregistered one gets an assertion without."""
    if password:
        text = fetch(url, {"act": "login", "name": name, "pass": password, "challstr": challstr})
        reply = json.loads(text[1:] if text.startswith("]") else text)
        if not reply.get("actionsuccess") or not reply.get("assertion"):
            raise RuntimeError(f"login failed: {reply.get('assertion') or reply}")
        assertion = reply["assertion"]
    else:
        q = urllib.parse.urlencode({"act": "getassertion", "userid": to_id(name), "challstr": challstr})
        assertion = fetch(f"{url}?{q}").strip()
    if assertion.startswith(";"):
        raise RuntimeError(f"login failed for {name}: {assertion} (a registered name needs --password)")
    return assertion


def replay_id(room: str) -> str:
    """The replay id a battle room's replay is saved under (its link adds
    a password for an unlisted one): Showdown's Room.getReplayData."""
    rest = room[len("battle-"):]
    if room.endswith("pw"):
        rest = rest[:rest.rfind("-", 0, len(rest) - 2)]
    return rest


@dataclass
class Battle:
    room: str
    tracker: Tracker
    bot: Bot
    log: list = field(default_factory=list)
    decided: object = None        # the rqid last answered
    errors: int = 0
    ots_offered: bool = False
    ots_rejected: bool = False
    pending: asyncio.Task | None = None
    started: float = field(default_factory=time.time)
    result: str | None = None
    series: bool = False          # a game of a best-of-3


class Client:
    def __init__(self, make_bot, team: str, name: str, password: str | None = None,
                 server: str = MAIN_SERVER, fmt: str = FORMAT, challenge: list[str] = (),
                 accept: list[str] | None = None, games: int = 1, ots: bool = True,
                 timer: bool = False, log_dir: str = "runs/showdown",
                 local: bool = False, think_delay: float = 0.3, ots_wait: float = 20.0,
                 replays: str = "unlisted", out=print):
        """`make_bot()` -> a fresh Bot per battle. `challenge`: users to
        challenge (one battle at a time, in turn). `accept`: accept
        challenges from these users ([] = anyone; None = none). `replays`:
        save each battle's replay on the server, "unlisted" (watchable by
        link), "public" or "off"."""
        self.make_bot, self.team, self.name, self.password = make_bot, team, name, password
        self.server, self.format = server, fmt
        self.to_challenge = list(challenge)
        self.accept = None if accept is None else {to_id(u) for u in accept}
        self.games_left = games
        self.ots, self.timer = ots, timer
        self.log_dir = Path(log_dir)
        self.local, self.think_delay, self.ots_wait = local, think_delay, ots_wait
        self.replays = replays
        self.replays_waiting: dict[str, tuple[str, str, str]] = {}  # replay id -> (room, result, opponent)
        self.out = out
        self.battles: dict[str, Battle] = {}
        self.record = {"win": 0, "loss": 0, "tie": 0}
        self.series_record = {"win": 0, "loss": 0, "tie": 0}
        self.series_wins: dict[str, dict[str, int]] = {}  # best-of room -> games won by name
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.ws = None
        self.logged_in = False
        self.challenging: str | None = None
        self.started: list[str] = []   # battle rooms, in order
        self.done = asyncio.Event()

    # --- Connection -------------------------------------------------------------

    async def run(self):
        try:
            import websockets
        except ImportError as e:
            raise SystemExit("the Showdown client needs the websockets package: pip install websockets") from e
        async with websockets.connect(self.server, max_size=None, ping_interval=30, ping_timeout=120) as ws:
            self.ws = ws
            reader = asyncio.create_task(self._read())
            await self.done.wait()
            # The last replays' links come a moment after the battle ends.
            deadline = time.time() + 20
            while self.replays_waiting and not reader.done() and time.time() < deadline:
                await asyncio.sleep(0.25)
            for room, *_ in self.replays_waiting.values():
                self.out(f"[{room}] no replay link came back from the server")
            reader.cancel()
        self.worker.shutdown(wait=False)

    async def _read(self):
        try:
            async for message in self.ws:
                try:
                    await self.handle(message)
                except Exception as e:  # a bug here: report it in full, keep the connection
                    self.out(f"error handling a message: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        except Exception as e:  # the connection itself: report and stop
            self.out(f"connection closed: {type(e).__name__}: {e}")
        self.done.set()

    async def send(self, text: str, room: str = ""):
        await self.ws.send(f"{room}|{text}")

    # --- Messages ---------------------------------------------------------------

    async def handle(self, message: str):
        lines = message.split("\n")
        room = ""
        if lines and lines[0].startswith(">"):
            room, lines = lines[0][1:].strip(), lines[1:]
        if room.startswith("battle-"):
            await self.battle_lines(room, lines)
            return
        for line in lines:
            await self.global_line(room, line)

    async def global_line(self, room: str, line: str):
        parts = line.split("|")
        kind = parts[1] if len(parts) > 1 else ""
        if room.startswith("game-bestof"):
            await self.series_line(room, line)
            return
        if kind == "challstr":
            challstr = "|".join(parts[2:])
            if self.local:
                await self.send(f"/trn {self.name},0,")
            else:
                loop = asyncio.get_running_loop()
                try:
                    assertion = await loop.run_in_executor(None, login, self.name, self.password, challstr)
                except Exception as e:
                    self.out(f"login failed: {type(e).__name__}: {e}")
                    self.done.set()
                    return
                await self.send(f"/trn {self.name},0,{assertion}")
        elif kind == "updateuser":
            user = parts[2].strip() if len(parts) > 2 else ""
            named = len(parts) > 3 and parts[3] == "1"
            if named and to_id(user) == to_id(self.name) and not self.logged_in:
                self.logged_in = True
                self.out(f"logged in as {user}")
                await self.send(f"/utm {pack_team(self.team)}")
                await self.next_challenge()
        elif kind == "nametaken":
            self.out(f"name refused: {'|'.join(parts[2:])}")
            self.done.set()
        elif kind == "popup":
            text = "|".join(parts[2:])
            if not self.replay_popup(text):
                self.out("server: " + text.replace("||", "\n  "))
        elif kind == "pm" and len(parts) > 4:
            sender, receiver, text = parts[2].strip(), parts[3].strip(), "|".join(parts[4:])
            if to_id(receiver) == to_id(self.name) and text.startswith("/challenge "):
                fmt = text[len("/challenge "):].split("|")[0]
                await self.incoming(sender, fmt)
            elif to_id(sender) == to_id(self.name) and text == "/challenge" and self.challenging:
                # Our challenge went: accepted (a battle starts) or declined.
                asyncio.create_task(self.check_declined(len(self.started)))
        elif kind == "updatechallenges" and len(parts) > 2:
            try:
                data = json.loads("|".join(parts[2:]))
            except ValueError:
                return
            for user, fmt in (data.get("challengesFrom") or {}).items():
                await self.incoming(user, fmt)

    def replay_popup(self, text: str) -> bool:
        """A saved replay's link (or the reason it wasn't saved): print it
        and add it to replays.txt. False: some other popup."""
        if "replay" not in text.lower():
            return False
        if not self.replays_waiting:
            return True  # a battle's replay saved again (hiding it re-saves it)
        m = re.search(r'href="(https?://[^"]+)"', text)
        fullid = m.group(1).rstrip("/").rsplit("/", 1)[-1] if m else ""
        saved = fullid not in ("", "undefined", "null")
        key = next((k for k in self.replays_waiting
                    if fullid == k or fullid.startswith(k + "-") or fullid.endswith("-" + k)
                    or f"-{k}-" in fullid), None)
        if key is None:
            if saved:
                return True  # another battle's, already written down
            key = next(iter(self.replays_waiting))  # an error: answers come in order
        room, result, foe = self.replays_waiting.pop(key)
        if not saved:
            reason = ("the server gave it no id (a local server stores no replays)" if m else
                      re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text).replace("|html|", "")).strip())
            self.out(f"[{room}] replay not saved: {reason}")
            return True
        url = m.group(1)
        self.out(f"[{room}] replay: {url}")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        with open(self.log_dir / "replays.txt", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M')}  {result:4s}  vs {foe}  {url}  ({room})\n")
        return True

    async def series_line(self, room: str, line: str):
        """A best-of-3's own room: who won each game, and its end."""
        wins = self.series_wins.setdefault(room, {})
        m = re.match(r"\|html\|(.+) won game \d+!$", line)
        if m:
            wins[to_id(m.group(1))] = wins.get(to_id(m.group(1)), 0) + 1
        elif "/confirmready" in line:
            m = re.search(r'value="(/msgroom [^"]+,/confirmready)"', line)
            if m:
                await self.send(m.group(1))
        elif line == "|allowleave|":
            ours = wins.get(to_id(self.name), 0)
            theirs = sum(v for k, v in wins.items() if k != to_id(self.name))
            result = "win" if ours > theirs else "loss" if theirs > ours else "tie"
            self.series_record[result] += 1
            r = self.series_record
            self.out(f"[{room}] series {result} {ours}-{theirs}. Series record: {r['win']}-{r['loss']}-{r['tie']}")
            await self.send(f"/leave {room}")
            self.games_left -= 1
            await self.after_match()

    async def after_match(self):
        if self.games_left <= 0 and not self.battles:
            self.done.set()
        elif self.games_left > 0:
            await self.next_challenge()

    async def check_declined(self, started_before: int):
        await asyncio.sleep(5)
        if len(self.started) == started_before and self.challenging:
            self.out(f"{self.challenging} didn't accept the challenge")
            self.challenging = None
            if not self.battles and self.accept is None:
                self.done.set()

    async def incoming(self, sender: str, fmt: str):
        uid = to_id(sender.lstrip("+%@*&#~ "))
        if self.accept is None or (self.accept and uid not in self.accept):
            self.out(f"ignoring a challenge from {sender} ({fmt})")
            return
        if self.games_left <= 0:
            return
        self.out(f"accepting a challenge from {sender} ({fmt})")
        await self.send(f"/utm {pack_team(self.team)}")
        await self.send(f"/accept {uid}")

    async def next_challenge(self):
        if not self.to_challenge or self.games_left <= 0:
            if self.accept is None and not self.battles:
                self.done.set()
            elif self.accept is not None and self.games_left > 0:
                self.out("waiting for challenges...")
            return
        user = self.to_challenge[0]
        self.to_challenge = self.to_challenge[1:] + [user]
        self.challenging = user
        self.out(f"challenging {user} to {self.format}")
        await self.send(f"/utm {pack_team(self.team)}")
        await self.send(f"/challenge {user}, {self.format}")

    # --- Battles ------------------------------------------------------------------

    async def battle_lines(self, room: str, lines: list[str]):
        b = self.battles.get(room)
        if b is None:
            if not any(l.startswith("|init|battle") for l in lines):
                # A room we've finished with: only its errors matter (say,
                # a replay command refused).
                for line in lines:
                    if line.startswith("|error|"):
                        self.out(f"[{room}] {line[7:]}")
                return
            try:
                b = Battle(room, Tracker(self.team, our_name=self.name), self.make_bot())
            except Exception as e:
                # It would fail the same way in every battle: concede this
                # one rather than leave the opponent waiting, and stop.
                self.out(f"[{room}] couldn't set up the bot, conceding and stopping: "
                         f"{type(e).__name__}: {e}\n{traceback.format_exc()}")
                await self.send("/forfeit", room)
                await self.send(f"/leave {room}")
                self.done.set()
                return
            self.battles[room] = b
            self.started.append(room)
            self.out(f"[{room}] started")
            if self.timer:
                await self.send("/timer on", room)
        new_request = False
        for line in lines:
            b.log.append(line)
            b.tracker.feed(line)
            if line.startswith("|request|"):
                new_request = True
            elif line.startswith("|uhtml|bestof|"):
                b.series = True
            elif line.startswith("|uhtml|otsrequest|"):
                b.ots_offered = True
                await self.send("/acceptopenteamsheets" if self.ots else "/rejectopenteamsheets", room)
            elif "rejected open team sheets" in line:
                b.ots_rejected = True
            elif line.startswith("|error|"):
                self.out(f"[{room}] {line[7:]}")
                if "[Invalid choice]" in line or "[Unavailable choice]" in line:
                    b.errors += 1
                    b.decided = None
                    if b.errors > 3:
                        await self.send("/choose default", room)
            elif line.startswith("|win|") or line == "|tie" or line.startswith("|tie|"):
                await self.finish(b, line)
                return
        if new_request or (b.decided is None and b.tracker.needs_decision()):
            if b.pending is None or b.pending.done():
                b.pending = asyncio.create_task(self.decide_soon(b))

    async def decide_soon(self, b: Battle):
        # Let the rest of this update arrive first.
        await asyncio.sleep(self.think_delay)
        t = b.tracker
        if not t.needs_decision() or (b.decided is not None and b.decided == t.rqid):
            return
        req = t.request
        if req.get("teamPreview") and b.ots_offered and self.ots:
            # Open Team Sheets: wait for the opponent's sheet (or its no).
            deadline = time.time() + self.ots_wait
            while time.time() < deadline and not t.ots and not b.ots_rejected:
                await asyncio.sleep(0.25)
        rqid = t.rqid
        loop = asyncio.get_running_loop()
        try:
            choice, why = await loop.run_in_executor(self.worker, b.bot.decide, t)
        except Exception as e:
            choice, why = "default", f"error: {type(e).__name__}: {e}"
        if t.rqid != rqid:
            return  # a newer request came in; it gets its own decision
        b.decided = rqid
        label = "team preview" if req.get("teamPreview") else f"turn {t.turn}"
        self.out(f"[{b.room}] {label}: {choice_text(choice, t)}  | {why}")
        await self.send(f"/choose {choice}|{rqid}", b.room)

    async def finish(self, b: Battle, line: str):
        winner = line.split("|")[2] if line.startswith("|win|") else None
        result = "tie" if winner is None else ("win" if to_id(winner) == to_id(self.name) else "loss")
        b.result = result
        self.record[result] += 1
        r = self.record
        self.out(f"[{b.room}] {result} after {b.tracker.turn} turns. Record: {r['win']}-{r['loss']}-{r['tie']}")
        if b.tracker.warnings:
            self.out(f"[{b.room}] tracker notes: " + "; ".join(b.tracker.warnings[:8]))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"{b.room}.log"
        path.write_text("\n".join(b.log) + "\n", encoding="utf-8")
        self.out(f"[{b.room}] log: {path}")
        if self.replays != "off":
            # Unlisted: the replay gets a password in its link and stays out
            # of searches.
            if self.replays == "unlisted":
                await self.send("/hidereplay", b.room)
            await self.send("/savereplay", b.room)
            self.replays_waiting[replay_id(b.room)] = (b.room, result, b.tracker.foe.name or "?")
        await self.send(f"/leave {b.room}")
        del self.battles[b.room]
        # A best-of-3 counts as one match, when its room says it's over.
        if not b.series:
            self.games_left -= 1
            await self.after_match()
