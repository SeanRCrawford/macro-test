"""Where the bot's games turned, quickly, over any number of saved battles:
no network needed, only the win estimate the bot logged at each decision.

    python -m selfplay.showdown.swings "runs/showdown/*.log"
    python -m selfplay.showdown.swings "runs/showdown/*.log" --played-by ppo11 --opponent Wolfe_Alt6767 --top 40

For every decision, the swing is the bot's estimate at its next decision
(or the result, at the end) less its estimate when it chose. The biggest
drops come first, each with the turn, what the bot played and expected,
what happened (the opponent's moves, knockouts) and any luck in it (a
critical hit, a miss, a flinch, full paralysis, a freeze or a self-hit in
confusion, for either side), and a link to watch it. Then the totals: how
much of the lost estimate came on turns with bad luck for the bot, and the
swings by turn and by opponent.

A drop with no luck in it is a turn to look at: the opponent did better
than the bot expected, or the bot chose badly. `selfplay.showdown.review`
replays a game decision by decision; `selfplay.showdown.diagnose` judges
the choices with the network.
"""
from __future__ import annotations

import argparse
import glob
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

DECISION = re.compile(r"^\|bot\|(team preview|turn (\d+)): (.*?)  \| (?:team preview: )?win (\d+)%(.*)$")


@dataclass
class Decision:
    turn: int
    played: str
    estimate: float          # the bot's win chance, 0-1
    expects: str
    after: float = float("nan")
    events: list = field(default_factory=list)
    luck: list = field(default_factory=list)    # (side it helped: "us"/"them", what)


@dataclass
class Game:
    path: Path
    room: str
    opponent: str
    won: float
    link: str
    decisions: list


def luck_event(line: str, us: str) -> tuple[str, str] | None:
    """(who it helped, what) for a line showing luck, or None."""
    a = line.split("|")[1:]
    if not a:
        return None
    kind = a[0]
    ours = lambda ident: ident.startswith(us)
    name = lambda ident: ident.split(": ", 1)[-1]
    if kind == "-crit" and len(a) > 1:   # a critical hit on a[1]
        return ("them" if ours(a[1]) else "us", f"critical hit on {name(a[1])}")
    if kind == "-miss" and len(a) > 1:   # a[1] missed
        return ("them" if ours(a[1]) else "us", f"{name(a[1])} missed")
    if kind == "cant" and len(a) > 2 and a[2] in ("flinch", "par", "frz", "slp"):
        what = {"flinch": "flinched", "par": "fully paralysed", "frz": "frozen", "slp": "asleep"}[a[2]]
        return ("them" if ours(a[1]) else "us", f"{name(a[1])} {what}")
    if kind == "-damage" and "[from] confusion" in line and len(a) > 1:
        return ("them" if ours(a[1]) else "us", f"{name(a[1])} hit itself in confusion")
    if kind == "-status" and len(a) > 2 and a[2] in ("frz", "par", "brn", "slp") and "[from]" not in line:
        return ("them" if ours(a[1]) else "us", f"{name(a[1])} {'frozen' if a[2] == 'frz' else a[2]}")
    return None


def links(folders: set[Path]) -> dict[str, str]:
    """Room -> replay link, from the client's replays.txt files."""
    out = {}
    for folder in folders:
        f = folder / "replays.txt"
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.search(r"(https?://\S+)\s+\((\S+)\)", line)
                if m:
                    out[m.group(2)] = m.group(1)
    return out


def read_game(path: Path, replay_links: dict[str, str]) -> Game | None:
    from selfplay.showdown.review import describe
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    players = {p[2]: p[3] for p in (l.split("|") for l in lines if l.startswith("|player|")) if len(p) > 3 and p[3]}
    us = next((m.group(1) for l in lines for m in [re.search(r'"side":\{"name":"[^"]*","id":"(p[12])"', l)] if m),
              None)
    winner = next((l.split("|")[2] for l in lines if l.startswith("|win|")), None)
    if us is None or winner is None:
        return None
    foe = "p2" if us == "p1" else "p1"
    decisions: list[Decision] = []
    for line in lines:
        m = DECISION.match(line)
        if m:
            rest = m.group(5)
            decisions.append(Decision(int(m.group(2) or 0), m.group(3), int(m.group(4)) / 100,
                                      rest.split("|| expects: ", 1)[1] if "|| expects: " in rest else ""))
            continue
        if decisions and not line.startswith("|bot|"):
            d = decisions[-1]
            ev = luck_event(line, us)
            if ev:
                d.luck.append(ev)
            text = describe(line, us)
            if text:
                d.events.append(text)
    if not decisions:
        return None
    won = 1.0 if winner == players.get(us) else 0.0
    for d, nxt in zip(decisions, decisions[1:] + [None]):
        d.after = nxt.estimate if nxt else won
    room = path.stem
    num = room.rsplit("-", 1)[-1]
    link = replay_links.get(room) or (f"https://vgc.bot/battles/{num}" if num.isdigit() and int(num) < 10 ** 8 else room)
    return Game(path, room, players.get(foe, "?"), won, link, decisions)


def report(games: list[Game], top: int = 25, out=print):
    rows = [(g, d) for g in games for d in g.decisions if d.turn > 0]
    if not rows:
        out("no decisions with a logged estimate")
        return
    won = np.mean([g.won for g in games])
    out(f"{len(games)} games ({won:.0%} won), {len(rows)} decisions with the bot's estimate")
    bad = lambda d: [w for who, w in d.luck if who == "them"]
    good = lambda d: [w for who, w in d.luck if who == "us"]
    swing = lambda d: d.after - d.estimate
    ordered = sorted(rows, key=lambda gd: swing(gd[1]))
    out(f"\nThe {top} biggest drops in the bot's win estimate (at its choice -> at its next decision):")
    for g, d in ordered[:top]:
        luck = "; ".join([f"bad luck: {x}" for x in bad(d)] + [f"good luck: {x}" for x in good(d)])
        out(f"  {100 * swing(d):+4.0f}  {100 * d.estimate:3.0f}% -> {100 * d.after:3.0f}%  turn {d.turn:2d} vs "
            f"{g.opponent} ({'won' if g.won else 'lost'})  {g.link}")
        out(f"         played: {d.played}" + (f"  | expected: {d.expects.split(';')[0]}" if d.expects else ""))
        then = [e for e in d.events if e.startswith("(opp)") or "fainted" in e][:6]
        if then:
            out(f"         then: {'; '.join(then)}")
        if luck:
            out(f"         {luck}")
    drops = [(g, d) for g, d in rows if swing(d) < 0]
    lost = sum(-swing(d) for _, d in drops)
    unlucky = sum(-swing(d) for _, d in drops if bad(d))
    big = [(g, d) for g, d in drops if swing(d) <= -0.25]
    out(f"\nOf all the estimate the bot lost on its turns, {unlucky / max(lost, 1e-9):.0%} was lost on turns with "
        f"bad luck for it; of the {len(big)} drops of 25 points or more, {sum(bool(bad(d)) for _, d in big)} "
        f"had bad luck in them and {sum(not bad(d) for _, d in big)} didn't (those are the ones to look at).")
    out(f"Luck over the games: {sum(len(bad(d)) for _, d in rows)} events against the bot, "
        f"{sum(len(good(d)) for _, d in rows)} for it.")
    by_turn = defaultdict(list)
    for _, d in rows:
        by_turn[min(d.turn, 7)].append(swing(d))
    out("\nmean swing by turn (points; negative: the estimate fell):  " + "  ".join(
        f"{'7+' if t == 7 else t}: {100 * np.mean(v):+.1f} ({len(v)})" for t, v in sorted(by_turn.items())))
    first = [(g.won, g.decisions[0].estimate) for g in games if g.decisions and g.decisions[0].turn <= 1]
    if first:
        w = [e for r, e in first if r]
        l_ = [e for r, e in first if not r]
        out(f"estimate at the first move: {100 * np.mean([e for _, e in first]):.0f}% on average"
            + (f" ({100 * np.mean(w):.0f}% in games won, {100 * np.mean(l_):.0f}% in games lost)" if w and l_ else ""))
    by_opp = defaultdict(list)
    for g in games:
        by_opp[g.opponent].append(g)
    out("\nby opponent: games, won, the mean of each game's biggest drop, and its share with bad luck in it")
    for o, gs in sorted(by_opp.items(), key=lambda kv: -len(kv[1])):
        worst = [min(g.decisions, key=swing) for g in gs]
        out(f"  {o:24s} {len(gs):4d}  {np.mean([g.won for g in gs]):4.0%}  "
            f"{100 * np.mean([swing(d) for d in worst]):+5.0f}  {np.mean([bool(bad(d)) for d in worst]):4.0%}")


def main():
    from selfplay.showdown.diagnose import filter_logs
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("logs", nargs="+")
    p.add_argument("--opponent", default=None)
    p.add_argument("--played-by", default=None, help="only logs whose '|bot|played by' line contains this")
    p.add_argument("--after", default=None, help="only logs saved after this date (YYYY-MM-DD)")
    p.add_argument("--top", type=int, default=25, help="drops to list")
    a = p.parse_args()
    paths = sorted({Path(f) for x in a.logs for f in (glob.glob(x) if glob.has_magic(x) else [x])})
    paths = filter_logs(paths, a.played_by, a.after)
    replay_links = links({p.parent for p in paths})
    games = [g for g in (read_game(p, replay_links) for p in paths) if g]
    if a.opponent:
        games = [g for g in games if g.opponent.lower() == a.opponent.lower()]
    report(games, a.top)


if __name__ == "__main__":
    main()
