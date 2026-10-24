"""Replay a saved Showdown battle with a model: at each of the bot's
decisions, what it would choose now (the search's mix and win estimate),
what it expects the opponent to do, what it chose in the game (from logs
saved since its decisions are kept), and what happened next.

    python -m selfplay.showdown.review runs/showdown/battle-...-123.log runs/ppo6k/model.pt --device cuda

The bot's team is found among the team folders by matching the battle's
stats (or give --team). The search is random, so a rerun can pick
differently where the mix is close.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import torch

from selfplay.showdown.data import to_id


def match_team(req: dict) -> Path | None:
    """The team file whose sets give exactly the stats in a request."""
    from selfplay import compute_stats, parse_team
    from selfplay.showdown.team import complete_paste, team_folders
    from selfplay.showdown.data import data
    d = data()
    want = {}
    for p in req.get("side", {}).get("pokemon", []):
        sp = p["details"].split(",")[0]
        hp = int(p["condition"].split("/")[1].split()[0]) if "/" in p["condition"] else None
        if d.is_mega(to_id(sp)):
            # Mega Evolved: its stats are the Mega's; match the base species.
            base = d.species.get(d.base_of(to_id(sp)), {}).get("name", sp)
            want[base] = {}
            continue
        want[sp] = {"hp": hp, **p["stats"]}
    for folder in team_folders():
        for path in sorted(folder.glob("*.txt")):
            try:
                sets = parse_team(complete_paste(path.read_text(encoding="utf-8-sig"))[0])
            except Exception:
                continue
            # Every Pokemon in the request (all six at team preview, the
            # four brought later) is a set of the paste, stats and all.
            by_species = {s["species"]: s for s in sets}
            ok = len(sets) >= len(want)
            for sp, w in want.items():
                s = by_species.get(sp)
                if s is None:
                    ok = False
                    break
                st = compute_stats(s["species"], s["nature"], s["points"]) if w else {}
                if any(st[k] != v for k, v in w.items() if v is not None):
                    ok = False
                    break
            if ok:
                return path
    return None


def describe(line: str, us: str) -> str | None:
    """A protocol line as a few words ("Floette: Dazzling Gleam"), or None."""
    a = line.split("|")[1:]
    if not a:
        return None
    kind = a[0]
    name = lambda ident: ident.split(": ", 1)[-1]
    side = lambda ident: "" if ident.startswith(us) else "(opp) "
    if kind == "move" and len(a) > 2:
        target = f" -> {name(a[3])}" if len(a) > 3 and a[3] and a[3] != a[1] and "[spread]" not in line \
            else (" (spread)" if "[spread]" in line else "")
        return f"{side(a[1])}{name(a[1])}: {a[2]}{target}"
    if kind in ("switch", "drag") and len(a) > 1:
        return f"{side(a[1])}in: {name(a[1])}"
    if kind == "faint" and len(a) > 1:
        return f"{side(a[1])}{name(a[1])} fainted"
    if kind == "-mega" and len(a) > 1:
        return f"{side(a[1])}{name(a[1])} Mega Evolves"
    if kind == "-activate" and len(a) > 2 and a[2] in ("move: Protect", "Protect"):
        return f"({name(a[1])} protected)"
    if kind == "-fail" and len(a) > 1:
        return f"({name(a[1])}'s move failed)"
    if kind == "win" and len(a) > 1:
        return f"{a[1]} wins"
    return None


def review(lines: list[str], bot, team: str, out=print):
    from selfplay.showdown.bot import active_names, choice_text
    from selfplay.showdown.tracker import Tracker
    first = next((json.loads(l[9:]) for l in lines if l.startswith("|request|{")), {})
    side = first.get("side", {})
    us = side.get("id", "p1")
    t = Tracker(team, our_name=side.get("name"))
    pending = None      # a request waiting for its decision
    then: list[str] = []

    def flush():
        if then:
            out("   then: " + "; ".join(then))
            then.clear()

    def decide():
        nonlocal pending
        req = t.request or {}
        label = "team preview" if req.get("teamPreview") else (
            f"turn {t.turn} (switch-in)" if req.get("forceSwitch") else f"turn {t.turn}")
        try:
            choice, why = bot.decide(t)
            out(f"== {label}: now {choice_text(choice, t, active_names(t, bot.guess_brought))}  | {why}")
        except Exception as e:
            out(f"== {label}: couldn't decide: {type(e).__name__}: {e}")
        pending = None

    for line in lines:
        if pending is not None and (line.startswith("|t:|") or line.startswith("|request|")
                                    or line.startswith("|bot|")):
            flush()
            decide()
        if line.startswith("|bot|"):
            out("   played: " + line[5:])
            continue
        t.feed(line)
        if line.startswith("|request|") and t.needs_decision():
            pending = True
            continue
        d = describe(line, us)
        if d:
            then.append(d)
    if pending is not None:
        flush()
        decide()
    flush()
    if t.warnings:
        out("tracker notes: " + "; ".join(t.warnings[:8]))


def main():
    from selfplay.evaluate import load
    from selfplay.mcts import TreeConfig
    from selfplay.showdown.bot import Bot
    from selfplay.showdown.team import complete_paste, find_team
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("log", help="a battle log the client saved (runs/showdown/battle-....log)")
    p.add_argument("model")
    p.add_argument("--team", default=None, help="the bot's team (default: found by the battle's stats)")
    p.add_argument("--policy", action="store_true", help="the network alone, no search")
    p.add_argument("--value-model", default=None, help="the search values positions with this model.pt")
    p.add_argument("--budget", type=int, default=2000)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--seed", type=int, default=0)
    a = p.parse_args()
    lines = Path(a.log).read_text(encoding="utf-8").splitlines()
    if a.team:
        path = find_team(a.team)
    else:
        first = next((json.loads(l[9:]) for l in lines if l.startswith("|request|{")), {})
        path = match_team(first)
        if path is None:
            raise SystemExit("no team file matches this battle's stats; give --team")
    print(f"team: {path}")
    team = complete_paste(path.read_text(encoding="utf-8-sig"))[0]
    torch.set_grad_enabled(False)
    model = load(a.model, a.device)
    value_model = load(a.value_model, a.device) if a.value_model else None
    bot = Bot(model, team, None if a.policy else TreeConfig(budget=a.budget), seed=a.seed, value_model=value_model)
    review(lines, bot, team)


if __name__ == "__main__":
    main()
