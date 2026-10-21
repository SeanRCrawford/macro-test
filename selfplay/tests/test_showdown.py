"""The Showdown client's tracker against Showdown's own state.

tests/fixtures/protocol.json.gz (tools/showdown/protocol_fixtures.js) holds
random-play battles run by Showdown's simulator: player 1's protocol stream
and Showdown's true state at each of player 1's decisions. The tracker
follows the stream, the engine rebuilds its position, and the rebuilt
battle must agree with the truth: exactly on our own side, and on the
opponent's as far as the log shows it.

    python tests/test_showdown.py      # the mismatch report
"""
from __future__ import annotations

import collections
import gzip
import json
from pathlib import Path

import pytest

from selfplay.env import SelfPlayEnv
from selfplay.showdown.tracker import Tracker

import os

FIXTURE = Path(os.environ.get("PROTOCOL_FIXTURE") or Path(__file__).parent / "fixtures" / "protocol.json.gz")


def battles():
    return json.loads(gzip.open(FIXTURE).read())["battles"]


def shown(hp, maxhp):
    """An opponent's HP as Champions shows it."""
    return max(1, 100 * hp // maxhp) if hp and maxhp else 0


# Volatiles whose length is random (and not shown), and the choice lock
# of a Pokemon whose item hasn't shown.
RANDOM_LENGTH = {"confusion", "partiallytrapped", "lockedmove", "stockpile"}


def compare_durations(truth: dict, pos: dict, ours: bool, check):
    """Durations the tracker counts itself: volatiles' turns left and the
    Toxic counter, by species."""
    side = "ours" if ours else "foe"
    for t in truth["pokemon"]:
        p = next((x for x in pos["pokemon"] if x["species"] == t["species"]), None)
        if p is None or not t["active"]:
            continue
        vols = {v["id"]: v for v in p.get("volatiles", [])}
        for vid, turns in t["volatileTurns"].items():
            if turns is None or vid in RANDOM_LENGTH or vid not in vols:
                continue
            check(f"{side}.durations", (vid, turns), (vid, vols[vid].get("duration")))
        if t["status"] == "tox":
            check(f"{side}.toxic", t["statusStage"], p.get("status_stage", 0))


def compare(truth: dict, snap: dict, ours: bool, out: collections.Counter, seen: collections.Counter,
            where: str, examples: dict, ots: bool = True):
    """Count agreements and mismatches between Showdown's side `truth` and
    the rebuilt side `snap` (position order)."""
    def check(key, a, b):
        seen[key] += 1
        if a != b:
            out[key] += 1
            examples.setdefault(key, f"{where}: {a!r} vs {b!r}"[:300])

    tp, sp = truth["pokemon"], snap["pokemon"]
    for k, t in enumerate(tp):
        if ours:
            if k >= len(sp):
                check("ours.count", len(tp), len(sp))
                break
            s = sp[k]
            check("ours.species", t["species"], s["species"])
            check("ours.hp", (t["hp"], t["maxhp"]), (s["hp"], s["maxhp"]))
            check("ours.status", t["status"], s["status"])
            check("ours.active", t["active"], s["active"])
            check("ours.boosts", t["boosts"], s["boosts"])
            check("ours.item", t["item"], s["item"])
            check("ours.ability", t["ability"], s["ability"])
            check("ours.pp", t["pp"], s["pp"])
            check("ours.volatiles", t["volatiles"], s["volatiles"])
        else:
            # The opponent's Pokemon by species (positions past the actives
            # are guesses).
            s = next((x for x in sp if x["species"] == t["species"]), None)
            if not t["active"] and t["status"] != "fnt":
                continue
            if s is None:
                check("foe.species", t["species"], None)
                continue
            check("foe.hp", shown(t["hp"], t["maxhp"]), shown(s["hp"], s["maxhp"]))
            check("foe.status", t["status"], s["status"])
            check("foe.active", t["active"], s["active"])
            check("foe.boosts", t["boosts"], s["boosts"])
            hidden = set() if ots else {"choicelock"}
            check("foe.volatiles", [v for v in t["volatiles"] if v not in hidden],
                  [v for v in s["volatiles"] if v not in hidden])
    check("side.conditions" if ours else "foe.conditions", truth["sideConditions"], snap["sideConditions"])


def perspectives(b):
    """(our side, our paste, our stream, our decisions) for each player
    recorded (truth sides in Showdown's order)."""
    yield 0, b["teams"][0], b["lines"], b["decisions"]
    if "lines_p2" in b:
        yield 1, b["teams"][1], b["lines_p2"], b["decisions_p2"]


def run(limit=None, verbose=False, ots=None):
    env = SelfPlayEnv(1, seed=0)
    out, seen = collections.Counter(), collections.Counter()
    examples: dict = {}
    errors = []
    for n, b, (me, paste, lines, decisions) in ((n, b, p) for n, b in enumerate(battles()[:limit])
                                                for p in perspectives(b)):
        if ots is not None and b["ots"] != ots:
            continue
        t = Tracker(paste, our_side=f"p{me + 1}")
        fed = 0
        for d in decisions:
            t.feed_all(lines[fed:d["at"]])
            fed = d["at"]
            truth = d["truth"]
            if truth["requests"][me] == "teampreview":
                continue
            where = f"battle {n} p{me + 1} turn {truth['turn']}"
            pos = t.position()
            try:
                env._env.set_position(0, json.dumps(pos), 0)
            except ValueError as e:
                errors.append(f"{where}: {e}")
                continue
            snap = json.loads(env.snapshot(0))

            def check(key, a, b_):
                seen[key] += 1
                if a != b_:
                    out[key] += 1
                    examples.setdefault(key, f"{where}: {a!r} vs {b_!r}"[:300])

            if "legal" in d:
                ours = sorted(c for _, c in env.legal_choices(0, 0))
                check("legal choices", sorted(d["legal"]), ours)
            check("turn", truth["turn"], snap["turn"])
            check("requests.ours", truth["requests"][me], snap["requests"][0])
            check("weather", (truth["weather"], truth["weatherTurns"]), (snap["weather"], snap["weatherTurns"]))
            check("terrain", (truth["terrain"], truth["terrainTurns"]), (snap["terrain"], snap["terrainTurns"]))
            check("pseudoweather", truth["pseudoWeather"], snap["pseudoWeather"])
            compare(truth["sides"][me], snap["sides"][0], True, out, seen, where, examples)
            compare(truth["sides"][1 - me], snap["sides"][1], False, out, seen, where, examples, b["ots"])
            compare_durations(truth["sides"][me], pos["sides"][0], True, check)
            compare_durations(truth["sides"][1 - me], pos["sides"][1], False, check)
        if t.warnings and verbose:
            print(f"battle {n} warnings: {t.warnings[:5]}")
    return out, seen, examples, errors


@pytest.mark.skipif(not FIXTURE.exists(), reason="no protocol fixtures")
def test_tracker_matches_showdown():
    out, seen, examples, errors = run()
    assert not errors, errors[:5]
    # Our side and the field must be exact; the opponent's HP is a
    # percentage, and some durations Showdown never shows.
    exact = [k for k in seen if k.startswith("ours.") or k in ("turn", "requests.ours", "pseudoweather",
                                                                 "legal choices")]
    for k in exact:
        assert out[k] == 0, f"{k}: {out[k]}/{seen[k]} wrong, e.g. {examples[k]}"
    for k in seen:
        assert out[k] <= 0.02 * seen[k], f"{k}: {out[k]}/{seen[k]} wrong, e.g. {examples[k]}"


@pytest.mark.skipif(not FIXTURE.exists(), reason="no protocol fixtures")
def test_bot_choices_are_legal():
    """The bot (an untrained network) answers every request of the first
    battles with a choice Showdown takes."""
    import torch

    from selfplay.model import PolicyNet
    from selfplay.showdown.bot import Bot

    torch.manual_seed(0)
    model = PolicyNet(32, 1).eval()
    for me, paste, lines, decisions in (p for b in battles()[:12] for p in perspectives(b)):
        bot = Bot(model, paste, sample=True, seed=1)
        t = Tracker(paste, our_side=f"p{me + 1}")
        fed = 0
        for d in decisions:
            t.feed_all(lines[fed:d["at"]])
            fed = d["at"]
            with torch.no_grad():
                choice, _ = bot.decide(t)
            assert choice in d["legal"], (choice, d["legal"][:5])


if __name__ == "__main__":
    for ots in (True, False):
        out, seen, examples, errors = run(verbose=False, ots=ots)
        print(f"\nOpen Team Sheets: {ots}; {len(errors)} positions the engine refused"
              + "".join(f"\n  {e}" for e in errors[:10]))
        for k in sorted(seen):
            print(f"{k:22s} {out[k]:5d} / {seen[k]:5d} wrong" + (f"   e.g. {examples[k]}" if k in examples else ""))


def test_completed_pastes_upload_the_team_the_engine_plays():
    """A paste without EVs (or with 'Nature: X') is uploaded as the engine
    reads it: the same spread and nature, nothing left for usage to fill."""
    from selfplay import parse_team
    from selfplay.showdown.team import complete_paste, pack_team, parse_paste
    teams = sorted((Path(__file__).resolve().parents[2] / "data" / "teams").glob("*.txt"))
    if not teams:
        pytest.skip("no data/teams")
    filled = 0
    for path in teams:
        text = path.read_text(encoding="utf-8-sig")
        try:
            before = parse_team(text)
        except ValueError:
            continue  # not a paste the engine reads
        done, _ = complete_paste(text)
        after = parse_team(done)
        filled += sum(s["points_filled"] for s in before)
        for b, a, p in zip(before, after, parse_paste(done)):
            assert (a["points"], a["nature"], a["points_filled"]) == (b["points"], b["nature"], False), path.name
            assert p["evs"] == a["points"] and (p["nature"] or "Serious") == a["nature"], path.name
        assert pack_team(done).count("]") == len(after) - 1
    assert filled > 0


def test_login_gets_past_a_server_that_refuses_pythons_default_client():
    """Showdown's login server (behind Cloudflare) answers Python-urllib's
    user agent with 403; the client sends its own, and on a 403 tries the
    requests package."""
    import threading
    import urllib.parse
    from http.server import BaseHTTPRequestHandler, HTTPServer

    from selfplay.showdown.client import login

    blocked = []

    class Handler(BaseHTTPRequestHandler):
        def reply(self, form):
            agent = self.headers.get("User-Agent", "")
            if any(b in agent for b in blocked):
                self.send_response(403)
                self.end_headers()
                return
            ok = form.get("challstr") == ["4|abc"]
            body = (']{"actionsuccess":true,"assertion":"signed"}' if form.get("act") == ["login"]
                    else "signed") if ok else ";;bad challstr"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body.encode())

        def do_GET(self):
            self.reply(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query))

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0))
            self.reply(urllib.parse.parse_qs(self.rfile.read(n).decode()))

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/action.php"
    try:
        for block in (["Python-urllib"], ["Python-urllib", "selfplay-showdown-client"]):
            blocked[:] = block
            assert login("MyBot", "secret", "4|abc", url) == "signed"
            assert login("MyBot", None, "4|abc", url) == "signed"
        blocked[:] = ["Python-urllib", "selfplay-showdown-client", "python-requests"]
        with pytest.raises(RuntimeError, match="403"):
            login("MyBot", "secret", "4|abc", url)
    finally:
        server.shutdown()


def test_the_client_names_the_encoding_of_every_file_it_reads():
    """Windows reads a file as cp1252 unless told otherwise, and dex.json
    isn't valid cp1252: loading the client's data with Python's missing-
    encoding warning as an error must pass."""
    import os
    import subprocess
    import sys
    code = ("from selfplay.showdown.data import Data; Data().corpus_usage(); "
            "from selfplay.showdown.team import complete_paste, team_folders, check_team; "
            "[check_team(complete_paste(p.read_text(encoding='utf-8-sig'))[0]) for f in team_folders() for p in sorted(f.glob('*.txt'))[:2]]")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    r = subprocess.run([sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning",
                        "-c", code], env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]


def test_replay_links_go_to_replays_txt(tmp_path):
    """After a battle the client saves its replay; the server answers with
    a popup holding the link (Room.uploadReplay), which is matched to its
    battle and written down."""
    from selfplay.showdown.client import Client, replay_id
    fmt = "gen9championsvgc2026regmc"
    assert replay_id(f"battle-{fmt}-123") == f"{fmt}-123"
    assert replay_id(f"battle-{fmt}-123-abcdefpw") == f"{fmt}-123"
    out = []
    c = Client(lambda: None, "", "MyBot", log_dir=str(tmp_path), out=out.append)
    c.replays_waiting = {f"{fmt}-5": (f"battle-{fmt}-5", "loss", "Other"),
                         f"{fmt}-55": (f"battle-{fmt}-55", "win", "mikumiku37")}
    url = f"https://replay.pokemonshowdown.com/{fmt}-55-a1b2c3pw"
    popup = (f"|html|<p>Your replay has been uploaded! It's available at:</p><p> "
             f'<a class="no-panel-intercept" href="{url}" target="_blank">{url}</a> '
             f'<copytext value="{url}">Copy</copytext>')
    assert c.replay_popup(popup)
    line = (tmp_path / "replays.txt").read_text(encoding="utf-8")
    assert url in line and "win" in line and "mikumiku37" in line
    assert list(c.replays_waiting) == [f"{fmt}-5"]
    # Hiding a replay the opponent saved first saves it again: one link.
    assert c.replay_popup(popup) and list(c.replays_waiting) == [f"{fmt}-5"]
    assert (tmp_path / "replays.txt").read_text(encoding="utf-8") == line
    assert not c.replay_popup("|html|<p>Something else</p>")
    assert c.replay_popup("Your replay could not be saved: Error: no database")
    assert not c.replays_waiting and "replay not saved" in out[-1]


def test_teams_are_found_in_every_folder_the_bot_trains_on():
    from selfplay.showdown.team import check_team, find_team, team_folders
    folders = team_folders()
    corpus = [f for f in folders if f.name == "corpus"]
    if not corpus:
        pytest.skip("no corpus")
    paste = sorted(corpus[0].glob("*.txt"))[0]
    assert find_team(paste.stem) == paste
    assert find_team(paste.name) == paste
    assert find_team(f"corpus/{paste.stem}") == paste
    teams = [f for f in folders if f.name == "teams"]
    if teams and (teams[0] / "Big 6 Chople.txt").is_file():
        assert find_team("teams/Big 6 Chople") == find_team("Big 6 Chople") == teams[0] / "Big 6 Chople.txt"
    with pytest.raises(SystemExit, match="did you mean"):
        find_team(paste.stem[:-2] + "zz")
    # A team Showdown would refuse.
    bad = corpus[0] / "nihileyugo's Golisopod Swampert Team.txt"
    if bad.is_file():
        errors, _ = check_team(bad.read_text(encoding="utf-8"))
        assert any("Precipice Blades" in e for e in errors)
    assert check_team(paste.read_text(encoding="utf-8")) == ([], [])


def test_review_replays_a_saved_battle_decision_by_decision():
    """selfplay.showdown.review on recorded battles: one line per decision
    the player made, each answered, with what happened next; decision lines
    the client saved ("|bot|...") are shown as what was played."""
    import torch

    from selfplay.model import PolicyNet
    from selfplay.showdown.bot import Bot
    from selfplay.showdown.review import describe, review

    torch.manual_seed(0)
    model = PolicyNet(32, 1).eval()
    for b in battles()[:3]:
        for me, paste, lines, decisions in perspectives(b):
            out = []
            lines = list(lines)
            lines.insert(decisions[0]["at"] + 1, "|bot|team preview: team 1234  | a saved decision")
            with torch.no_grad():
                review(lines, Bot(model, paste, seed=1), paste, out=out.append)
            heads = [o for o in out if o.startswith("== ")]
            assert len(heads) == len(decisions), (len(heads), len(decisions))
            assert not any("couldn't decide" in o for o in out)
            assert any(o.startswith("   then: ") for o in out)
            assert "   played: team preview: team 1234  | a saved decision" in out
    assert describe("|move|p2b: Floette|Dazzling Gleam|p1b: Excadrill|[spread] p1a,p1b", "p1") == \
        "(opp) Floette: Dazzling Gleam (spread)"
    assert describe("|faint|p1a: Salamence", "p1") == "Salamence fainted"


def test_diagnose_reads_the_opponents_reply_from_the_log():
    """The opponent's joint action that turn, from what the log shows: a
    switch to a Pokemon in the bot's picture, a Mega Evolution and a move's
    target; a Pokemon the bot didn't expect is marked."""
    from selfplay.showdown.data import data
    from selfplay.showdown.diagnose import observed_reply
    moves = lambda *ids: [{"id": i} for i in ids]
    position = {"sides": [{"pokemon": []}, {"pokemon": [
        {"species": "kingambit", "moves": moves("kowtowcleave", "suckerpunch", "ironhead", "lowkick")},
        {"species": "floetteeternal", "moves": moves("moonblast", "dazzlinggleam", "lightofruin", "protect")},
        {"species": "whimsicott", "moves": []}, {"species": "garchomp", "moves": []}]}]}
    lines = ["|t:|1", "|switch|p2a: Garchomp|Garchomp, L50, F|100/100",
             "|detailschange|p2b: Floette|Floette-Mega, L50, F", "|-mega|p2b: Floette|Floette|Floettite",
             "|move|p2b: Floette|Moonblast|p1a: Tyranitar", "|-damage|p1a: Tyranitar|0 fnt", "|upkeep",
             "|move|p2a: Garchomp|Earthquake|p1a: Tyranitar"]
    assert observed_reply(lines, "p2", position, data()) == [["switch 4"], ["move 1 1 mega", "move 1 mega"]]
    lines[1] = "|switch|p2a: Charizard|Charizard, L50, M|100/100"
    assert observed_reply(lines, "p2", position, data())[0] == ["?unseen"]
    assert observed_reply(["|t:|1", "|upkeep"], "p2", position, data()) is None
    # The bot's own action, read the same way from its side of the position.
    lines[1] = "|switch|p2a: Garchomp|Garchomp, L50, F|100/100"
    ours = {"sides": [position["sides"][1], {"pokemon": []}]}
    assert observed_reply(lines, "p2", ours, data(), side=0) == [["switch 4"], ["move 1 1 mega", "move 1 mega"]]


def test_arena_client_logs_in_with_the_token_reconnects_and_skips_random_teams(tmp_path):
    """Against a stand-in for VGC Arena: the bot logs in with /trn NAME,0,TOKEN,
    sends its team, comes back after a dropped connection (logging in and
    sending the team again), concedes a random-team battle, sends nothing
    the arena doesn't take (/leave, replays, /accept) and never prints the
    token."""
    import asyncio

    websockets = pytest.importorskip("websockets")
    from selfplay.showdown.client import Client
    from selfplay.showdown.team import complete_paste, find_team
    team = complete_paste(find_team("Big 6 Chople").read_text(encoding="utf-8-sig"))[0] \
        if (Path(__file__).resolve().parents[2] / "data" / "teams").is_dir() else None
    if team is None:
        pytest.skip("no data/teams")
    token = "pka_TESTTOKEN123"
    connections: list[list[str]] = []

    async def arena(ws):
        got: list[str] = []
        connections.append(got)
        await ws.send("|updateuser| Guest 1|0|1|{}\n|challstr|4|abc")
        async for msg in ws:
            got.append(msg)
            if msg.startswith("|/trn "):
                await ws.send("|updateuser| Scronk|1|1|{}")
            elif msg.startswith("|/utm "):
                if len(connections) == 1:
                    await ws.close()  # dropped: the bot should come back
                    return
                await ws.send(">battle-gen9championsvgc2026regmcotsrandom-7\n|init|battle\n|title|Scronk vs. X")
            elif msg.endswith("|/forfeit"):
                await ws.send("|nametaken|Scronk|that's all")

    out: list[str] = []

    async def main():
        async with websockets.serve(arena, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            client = Client(lambda: None, team, "Scronk", token, server=f"ws://127.0.0.1:{port}",
                            fmt="gen9championsvgc2026regmcots", games=10 ** 9, arena=True,
                            reconnect_delay=0.05, log_dir=str(tmp_path), out=out.append)
            await asyncio.wait_for(client.run(), 30)

    asyncio.run(main())
    assert len(connections) == 2
    for got in connections:
        assert got[0] == f"|/trn Scronk,0,{token}"
        assert any(m.startswith("|/utm ") for m in got)
    assert "battle-gen9championsvgc2026regmcotsrandom-7|/forfeit" in connections[1]
    sent = [m for got in connections for m in got]
    assert not any(w in m for m in sent for w in ("/leave", "replay", "/accept"))
    assert not any(token in line for line in out)
    assert any("reconnecting" in line for line in out) and any("random-team" in line for line in out)


def test_stopping_finishes_the_battle_in_progress_then_goes_offline(tmp_path):
    """Asked to stop mid-battle (Ctrl+C), the client keeps playing that
    battle, and goes offline (no reconnect) only once it's over."""
    import asyncio

    import torch

    websockets = pytest.importorskip("websockets")
    from selfplay.model import PolicyNet
    from selfplay.showdown.bot import Bot
    from selfplay.showdown.client import Client
    b = battles()[0]
    paste, lines, at = b["teams"][0], b["lines"], b["decisions"][0]["at"]
    room = "battle-gen9championsvgc2026regmcots-9"
    torch.manual_seed(0)
    model = PolicyNet(32, 1).eval()
    connections: list[list[str]] = []
    client = None

    async def arena(ws):
        got: list[str] = []
        connections.append(got)
        await ws.send("|challstr|4|abc")
        async for msg in ws:
            got.append(msg)
            if msg.startswith("|/trn "):
                await ws.send("|updateuser| p1|1|1|{}")
            elif msg.startswith("|/utm "):
                await ws.send(f">{room}\n|init|battle\n" + "\n".join(lines[:at]))
            elif "|/choose " in msg:
                client.request_stop()  # Ctrl+C while the battle is on
                await ws.send(f">{room}\n|win|p1")

    out: list[str] = []

    async def main():
        nonlocal client
        async with websockets.serve(arena, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            with torch.no_grad():
                client = Client(lambda: Bot(model, paste, seed=1), paste, "p1", "pka_x",
                                server=f"ws://127.0.0.1:{port}", games=10 ** 9, arena=True,
                                reconnect_delay=0.05, log_dir=str(tmp_path), out=out.append)
                await asyncio.wait_for(client.run(), 60)

    asyncio.run(main())
    assert len(connections) == 1 and client.stopped
    assert any(m.startswith(f"{room}|/choose team ") for m in connections[0])
    assert any("finishing 1 battle" in line for line in out)
    assert any("going offline" in line for line in out) and client.record["win"] == 1


def test_one_ctrl_c_asks_the_client_to_stop_instead_of_killing_it():
    import asyncio
    import os
    import signal
    import sys
    if sys.platform == "win32":
        pytest.skip("sends itself SIGINT")
    from selfplay.showdown.__main__ import run_until_stopped

    class FakeClient:
        asked = 0

        def request_stop(self):
            self.asked += 1
            self.stopped.set()

        async def run(self):
            self.stopped = asyncio.Event()
            asyncio.get_running_loop().call_later(0.2, os.kill, os.getpid(), signal.SIGINT)
            await asyncio.wait_for(self.stopped.wait(), 5)

    c = FakeClient()
    asyncio.run(run_until_stopped(c))
    assert c.asked == 1
