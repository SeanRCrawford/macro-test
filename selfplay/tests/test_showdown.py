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
            examples.setdefault(key, f"{where}: {a!r} vs {b!r}")

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


def run(limit=None, verbose=False, ots=None):
    env = SelfPlayEnv(1, seed=0)
    out, seen = collections.Counter(), collections.Counter()
    examples: dict = {}
    errors = []
    for n, b in enumerate(battles()[:limit]):
        if ots is not None and b["ots"] != ots:
            continue
        t = Tracker(b["teams"][0], our_side="p1")
        fed = 0
        for d in b["decisions"]:
            t.feed_all(b["lines"][fed:d["at"]])
            fed = d["at"]
            truth = d["truth"]
            if truth["requests"][0] == "teampreview":
                continue
            where = f"battle {n} turn {truth['turn']}"
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
                    examples.setdefault(key, f"{where}: {a!r} vs {b_!r}")

            check("turn", truth["turn"], snap["turn"])
            check("requests.ours", truth["requests"][0], snap["requests"][0])
            check("weather", (truth["weather"], truth["weatherTurns"]), (snap["weather"], snap["weatherTurns"]))
            check("terrain", (truth["terrain"], truth["terrainTurns"]), (snap["terrain"], snap["terrainTurns"]))
            check("pseudoweather", truth["pseudoWeather"], snap["pseudoWeather"])
            compare(truth["sides"][0], snap["sides"][0], True, out, seen, where, examples)
            compare(truth["sides"][1], snap["sides"][1], False, out, seen, where, examples, b["ots"])
            compare_durations(truth["sides"][0], pos["sides"][0], True, check)
            compare_durations(truth["sides"][1], pos["sides"][1], False, check)
        if t.warnings and verbose:
            print(f"battle {n} warnings: {t.warnings[:5]}")
    return out, seen, examples, errors


@pytest.mark.skipif(not FIXTURE.exists(), reason="no protocol fixtures")
def test_tracker_matches_showdown():
    out, seen, examples, errors = run()
    assert not errors, errors[:5]
    # Our side and the field must be exact; the opponent's HP is a
    # percentage, and some durations Showdown never shows.
    exact = [k for k in seen if k.startswith("ours.") or k in ("turn", "requests.ours", "pseudoweather")]
    for k in exact:
        assert out[k] == 0, f"{k}: {out[k]}/{seen[k]} wrong, e.g. {examples[k]}"
    for k in seen:
        assert out[k] <= 0.02 * seen[k], f"{k}: {out[k]}/{seen[k]} wrong, e.g. {examples[k]}"


if __name__ == "__main__":
    for ots in (True, False):
        out, seen, examples, errors = run(verbose=False, ots=ots)
        print(f"\nOpen Team Sheets: {ots}; {len(errors)} positions the engine refused"
              + "".join(f"\n  {e}" for e in errors[:10]))
        for k in sorted(seen):
            print(f"{k:22s} {out[k]:5d} / {seen[k]:5d} wrong" + (f"   e.g. {examples[k]}" if k in examples else ""))
