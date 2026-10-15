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
            "from selfplay.showdown.__main__ import TEAMS; from selfplay.showdown.team import complete_paste; "
            "[complete_paste(p.read_text(encoding='utf-8-sig')) for p in sorted(TEAMS.glob('*.txt'))[:3]]")
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    r = subprocess.run([sys.executable, "-X", "warn_default_encoding", "-W", "error::EncodingWarning",
                        "-c", code], env=env, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr[-2000:]
