"""How the bot's search fares against what an opponent actually played,
and why it loses where it does.

For every decision of the bot in saved battle logs (both sides choosing
moves), the position is rebuilt as the bot saw it, and the log gives what
each side actually did that turn. Then:

- **Did it expect the reply?** The reply's rank in the policy's prediction,
  whether the search considered it, and the weight its equilibrium gave it.
- **Was its answer good?** Each setting's choice is scored against the
  actual reply, by the network's value one turn later (and, separately, the
  HP left on each side), against the best any legal action scored. With
  `--judge-budget N` the strongest candidates are also judged deeper: the
  turn is played, then a search of N evaluations values the position from
  there (both sides then playing well), so a move that pays off on the
  turn after counts.
- **How good was the reply?** Against what the bot played, the reply's
  rank among all the opponent's replies (its view one turn later), and,
  judged deeper, how it compares with the replies the bot expected.
- **How far ahead did the search look?** For the chosen move, the turns its
  value looks beyond the first, over the opponent's equilibrium play.

    python -m selfplay.showdown.diagnose MODEL "runs/showdown/*.log" --opponent mikumiku37 \\
        --settings policy default budget=8000 --judge-budget 2000

A setting is `name` or `key=value,key=value` over TreeConfig ("policy": no
search). Every decision's searches run together, so the network sees large
batches. Each observed reply is one sample of how the opponent plays, so
the averages over many decisions estimate how a setting fares against it.
"""
from __future__ import annotations

import argparse
import glob
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

from selfplay.env import DECISION_SLOTS, SelfPlayEnv
from selfplay.mcts import TreeConfig, TreeSearch, lookahead_of, with_settings
from selfplay.showdown.data import to_id
from selfplay.train import to_tensors


def parse_setting(text: str):
    """'default' / 'policy' / 'budget=8000,oracle_pool=0' -> (name, TreeConfig or None)."""
    if text == "policy":
        return text, None
    cfg = TreeConfig(sample=False)
    return text, cfg if text == "default" else with_settings(cfg, text)


def observed_reply(lines: list[str], foe: str, position: dict, d, side: int = 1) -> list[list[str]] | None:
    """Per slot of player `foe` (side `side` of the position), the slot
    actions ('move 2 1', 'move 1 mega', 'switch 3', ...) consistent with what
    the log shows this turn (until the end of the turn); [] where it can't be
    told (it fainted first, flinched...). None when the turn shows nothing
    of that player."""
    mons = position["sides"][side]["pokemon"]
    names = [m["species"] for m in mons]
    base = lambda sp: d.base_of(sp)
    out: list[list[str] | None] = [None, None]
    mega = [False, False]
    saw = False
    for line in lines:
        if line.startswith("|upkeep") or line.startswith("|turn|") or line.startswith("|request|"):
            break
        a = line.split("|")[1:]
        if len(a) < 2 or not a[1].startswith(foe):
            continue
        slot = "ab".index(a[1][len(foe)]) if len(a[1]) > len(foe) and a[1][len(foe)] in "ab" else None
        if slot is None:
            continue
        kind = a[0]
        if kind == "-mega":
            mega[slot] = True
        elif kind == "switch" and out[slot] is None:
            sp = to_id(a[2].split(",")[0])
            k = next((i for i, n in enumerate(names) if base(n) == base(sp) or n == sp), None)
            out[slot] = [f"switch {k + 1}"] if k is not None and k >= 2 else ["?unseen"]
            saw = True
        elif kind == "move" and out[slot] is None:
            saw = True
            mon = mons[slot] if slot < len(mons) else None
            ids = [m["id"] for m in (mon or {}).get("moves", [])]
            mid = to_id(a[2])
            if mid not in ids:
                out[slot] = []  # a called move, or one the bot's picture lacks
                continue
            j = ids.index(mid) + 1
            target = a[3] if len(a) > 3 else ""
            if "[spread]" in line or not target or target == a[1]:
                locs = ["", " 1", " 2", " -1", " -2"]
            else:
                tslot = "ab".index(target.split(":")[0][-1]) if target.split(":")[0][-1] in "ab" else 0
                locs = [f" {tslot + 1}" if not target.startswith(foe) else f" {-(tslot + 1)}", ""]
            out[slot] = [f"move {j}{t}" for t in locs]
        elif kind == "cant" and out[slot] is None:
            out[slot] = []
            saw = True
    if not saw:
        return None
    res = []
    for s in range(2):
        acts = out[s]
        if acts is None or acts == []:
            res.append([])
        else:
            res.append([x + (" mega" if mega[s] and x.startswith("move") else "") for x in acts])
    return res


def matching(theirs: dict, per_slot: list[list[str]]) -> list[int]:
    """Joint actions (indices) consistent with the observed slots."""
    out = []
    for i, c in theirs.items():
        parts = [p.strip() for p in c.split(",")]
        if len(parts) != 2:
            continue
        if all(not per_slot[s] or parts[s] in per_slot[s] for s in range(2)):
            if any(p.startswith("?") for s in range(2) for p in per_slot[s]):
                continue
            out.append(i)
    return out


@dataclass
class Decision:
    """One decision of the bot, with what both sides did that turn."""
    log: str
    opponent: str
    turn: int
    won: float
    position: str
    reply: list[int]        # the opponent's joint actions consistent with the log
    played: list[int]       # ... and the bot's own
    ours: dict              # legal joint actions: index -> Showdown choice text
    theirs: dict
    prior: list = field(default_factory=list)  # filled in: (our policy, theirs) per action


def collect(model, logs: list[Path], out=print, opponent: str | None = None, games: list | None = None) -> list[Decision]:
    """The decisions in `logs` (against `opponent` only, if given) whose
    reply the log shows. `games`, if given, gets each game's guess of the
    opponent's four (at the bot's first decision) against the ones that
    appeared."""
    found = []
    for log in logs:
        try:
            found += collect_log(model, log, out, opponent, games)
        except Exception as e:  # one bad log shouldn't end the run
            out(f"{log.name}: skipped ({type(e).__name__}: {e})")
    return found


def collect_log(model, log: Path, out, opponent=None, games=None) -> list[Decision]:
    from selfplay.showdown.bot import Bot
    from selfplay.showdown.review import match_team
    from selfplay.showdown.team import complete_paste
    from selfplay.showdown.tracker import Tracker
    lines = log.read_text(encoding="utf-8").splitlines()
    players = {p[2]: p[3] for p in (l.split("|") for l in lines if l.startswith("|player|")) if len(p) > 3 and p[3]}
    first = next((json.loads(l[9:]) for l in lines if l.startswith("|request|{")), {})
    side = first.get("side", {})
    us = side.get("id", "p1")
    foe = "p2" if us == "p1" else "p1"
    if opponent and players.get(foe, "").lower() != opponent.lower():
        return []
    path = match_team(first)
    if path is None:
        out(f"{log.name}: no team matches its stats; skipped")
        return []
    winner = next((l.split("|")[2] for l in lines if l.startswith("|win|")), None)
    won = float("nan") if winner is None else float(winner == players.get(us))
    team = complete_paste(path.read_text(encoding="utf-8-sig"))[0]
    t = Tracker(team, our_name=side.get("name"))
    bot = Bot(model, team, None, seed=0)
    found = []
    guess = None  # the opponent's four the bot assumed at its first move
    skipped = 0
    k = 0
    while k < len(lines):
        line = lines[k]
        t.feed(line)
        k += 1
        if not (line.startswith("|request|") and t.needs_decision()):
            continue
        req = t.request or {}
        # The rest of this update first (the decision comes after it).
        while k < len(lines) and not lines[k].startswith("|t:|"):
            t.feed(lines[k])
            k += 1
        if req.get("teamPreview"):
            bot.preview(t)  # its picture of what the opponent brings
            continue
        if req.get("forceSwitch"):
            continue  # the opponent doesn't act on a replacement
        position = t.position(bot.guess_brought)
        if guess is None:
            guess = set(bot.guess_brought(t)[:4])
        ps = json.dumps(position)
        env = SelfPlayEnv(1, seed=0, threads=1)
        env._env.set_position(0, ps, 1)
        obs = env.observe()
        if obs.decisions[0, 0] != DECISION_SLOTS or obs.decisions[0, 1] != DECISION_SLOTS:
            continue
        theirs, ours = dict(env.legal_choices(0, 1)), dict(env.legal_choices(0, 0))
        per = observed_reply(lines[k:], foe, position, t.d)
        reply = matching(theirs, per) if per else []
        # The bot's own action, read the same way.
        per_us = observed_reply(lines[k:], us, position, t.d, side=0)
        played = matching(ours, per_us) if per_us else []
        if not reply:
            skipped += 1
            continue
        found.append(Decision(log.name, players.get(foe, "?"), t.turn, won, ps, reply, played, ours, theirs))
    out(f"{log.name} ({path.stem}) against {players.get(foe, '?')}: {len(found)} decisions"
        + (f" ({skipped} with a reply the log doesn't show or a Pokemon the bot didn't expect)" if skipped else ""))
    if guess is not None and games is not None:
        seen = {m.uid for m in t.foe.mons if m.revealed}
        games.append({"log": log.name, "all_seen": len(seen) == 4, "wrong": len(seen - guess)})
    return found


@torch.no_grad()
def priors(model, decisions: list[Decision], dev, chunk: int = 1024) -> None:
    """Each decision's policy (both sides' probabilities over the mask)."""
    for i in range(0, len(decisions), chunk):
        part = decisions[i:i + chunk]
        env = SelfPlayEnv(len(part), seed=0, threads=0)
        for g, d in enumerate(part):
            env._env.set_position(g, d.position, 1)
        obs = env.observe()
        logp, _ = model(*(x.to(dev) for x in to_tensors(obs)))
        p = logp.float().exp().cpu().numpy().reshape(len(part), 2, -1)
        for g, d in enumerate(part):
            d.prior = [p[g, 0], p[g, 1]]


def search_all(model, decisions: list[Decision], cfg: TreeConfig | None, seed: int = 1,
               batch: int = 256) -> tuple[list[dict], dict]:
    """Each decision searched with `cfg` (the policy's most likely action if
    None), in batches of positions. Per decision: the root result with
    `pick` (the action played), and the run's statistics."""
    stats = {"seconds": 0.0, "evals": 0}
    if cfg is None:
        return [{"pick": max(d.ours, key=lambda a: d.prior[0][a])} for d in decisions], stats
    out = []
    for i in range(0, len(decisions), batch):
        part = decisions[i:i + batch]
        env = SelfPlayEnv(len(part), seed=0, threads=0)
        for g, d in enumerate(part):
            env._env.set_position(g, d.position, 1)
        s = TreeSearch(model, TreeConfig(**vars(cfg)), seed=seed + i)
        res = s.search(env, list(range(len(part))))
        for r in res:
            r["pick"] = s.pick(r, 0)
        out += res
        stats["seconds"] += s.stats["seconds"]
        stats["evals"] += s.stats["leaves"]
    return out, stats


@torch.no_grad()
def after_turn(model, items: list[tuple[str, int, int]], seeds: int, dev, budget: int = 0,
               chunk: int = 2048) -> tuple[np.ndarray, np.ndarray]:
    """Per (position, our action, their action): side 0's value after
    playing the turn (averaged over `seeds` chance outcomes) and the HP
    margin then (the brought Pokemon's HP fractions summed, ours less
    theirs; fainted = 0; a turn that ends the game counts as +-1 and +-4).
    The value is the network's estimate of the position the turn leaves,
    or with `budget` a search's value of it (looking further ahead)."""
    vals = np.zeros(len(items) * seeds)
    hps = np.zeros(len(items) * seeds)
    runs = [(pos, a, b, s) for pos, a, b in items for s in range(seeds)]
    uid_cache: dict[str, list] = {}
    for start in range(0, len(runs), chunk):
        part = runs[start:start + chunk]
        env = SelfPlayEnv(len(part), seed=0, threads=0)
        for g, (pos, _a, _b, s) in enumerate(part):
            env._env.set_position(g, pos, 1000 + s)
        r = env.step(np.array([[a, b] for _p, a, b, _s in part], np.int64))
        done, reward = r.done.astype(bool).copy(), r.reward.copy()
        obs = env.observe()
        m = obs.mons.copy()  # [n, 2 views, tokens, floats]: view 0 is ours
        live = [g for g in range(len(part)) if not done[g]]
        if budget and live:
            ts = TreeSearch(model, TreeConfig(budget=budget, sample=False), seed=start)
            v = np.zeros(len(part))
            for i in range(0, len(live), 256):
                for g, rr in zip(live[i:i + 256], ts.search(env, live[i:i + 256])):
                    v[g] = rr["value"]
        else:
            _, vv = model(*(t.to(dev) for t in to_tensors(obs)))
            vv = vv.float().cpu().numpy().reshape(-1, 2)
            v = (vv[:, 0] - vv[:, 1]) / 2
        for g, (pos, _a, _b, _s) in enumerate(part):
            if done[g]:
                vals[start + g] = reward[g, 0]
                hps[start + g] = 4.0 * reward[g, 0]
                continue
            vals[start + g] = v[g]
            if pos not in uid_cache:
                uid_cache[pos] = [[mon["uid"] for mon in side["pokemon"]] for side in json.loads(pos)["sides"]]
            uids = uid_cache[pos]
            hp = lambda tok: 0.0 if m[g, 0, tok, 28] > 0.5 else float(m[g, 0, tok, 1])
            hps[start + g] = sum(hp(u) for u in uids[0]) - sum(hp(6 + u) for u in uids[1])
    return vals.reshape(len(items), seeds).mean(1), hps.reshape(len(items), seeds).mean(1)


def score_actions(model, position: str, ours: list[int], theirs: list[int], seeds: int, dev):
    """For each of our actions: (network value, HP margin) after one turn
    against `theirs` (averaged over them and over chance)."""
    items = [(position, a, b) for a in ours for b in theirs]
    v, h = after_turn(model, items, seeds, dev)
    return v.reshape(len(ours), len(theirs)).mean(1), h.reshape(len(ours), len(theirs)).mean(1)


def diagnose(model, decisions: list[Decision], settings: list[str], seeds: int, dev, out=print,
             judge_budget: int = 0, judge_top: int = 3, judge_seeds: int = 2, batch: int = 256) -> dict:
    """Every measurement over `decisions`; returns the per-decision rows and
    per-setting statistics."""
    t0 = time.perf_counter()
    priors(model, decisions, dev)
    parsed = [parse_setting(s) for s in settings]
    if "default" not in settings:
        parsed.append(parse_setting("default"))  # the bot's own picture of the opponent
    searched, run_stats = {}, {}
    for name, cfg in parsed:
        searched[name], run_stats[name] = search_all(model, decisions, cfg, batch=batch)
        out(f"searched {len(decisions)} positions with {name}: {run_stats[name]['seconds']:.0f}s"
            + (f", {run_stats[name]['evals'] / len(decisions):.0f} evaluations each" if cfg else ""))
    # One turn: every legal action of ours against the actual reply, and
    # every reply of theirs against what the bot played.
    items, owner = [], []
    for i, d in enumerate(decisions):
        for a in d.ours:
            for b in d.reply[:2]:  # (the rest are mostly the same move written another way)
                items.append((d.position, a, b))
                owner.append(("ours", i, a))
        if d.played:
            for b in d.theirs:
                items.append((d.position, d.played[0], b))
                owner.append(("theirs", i, b))
    v1, h1 = after_turn(model, items, seeds, dev)
    one = [{"ours": {}, "ours_hp": {}, "theirs": {}} for _ in decisions]
    for (kind, i, a), v, h in zip(owner, v1, h1):
        acc = one[i][kind].setdefault(a, [])
        acc.append(v)
        if kind == "ours":
            one[i]["ours_hp"].setdefault(a, []).append(h)
    for o in one:
        for k in ("ours", "ours_hp", "theirs"):
            o[k] = {a: float(np.mean(v)) for a, v in o[k].items()}
    out(f"one-turn values: {len(items) * seeds} turns played ({time.perf_counter() - t0:.0f}s so far)")
    # Deeper: the settings' choices, what the bot played and the best few
    # one turn ahead, against the reply; and against what the bot played,
    # the reply and the replies the bot expected most.
    deep = [{"ours": {}, "theirs": {}} for _ in decisions]
    if judge_budget:
        items, owner = [], []
        for i, d in enumerate(decisions):
            top = sorted(one[i]["ours"], key=lambda a: -one[i]["ours"][a])[:judge_top]
            mine = {searched[n][i]["pick"] for n, _ in parsed} | set(d.played[:1]) | set(top)
            for a in mine:
                items.append((d.position, a, d.reply[0]))
                owner.append(("ours", i, a))
            r = searched["default"][i]
            if d.played and r.get("candidates"):
                expected = sorted(zip(r["candidates"][1], r["col"]), key=lambda t: -t[1])[:judge_top]
                for b in {d.reply[0]} | {b for b, w in expected if w > 0}:
                    items.append((d.position, d.played[0], b))
                    owner.append(("theirs", i, b))
        vd, _ = after_turn(model, items, judge_seeds, dev, budget=judge_budget)
        for (kind, i, a), v in zip(owner, vd):
            deep[i][kind][a] = float(v)
        out(f"judged deeper: {len(items) * judge_seeds} positions searched with {judge_budget} evaluations "
            f"({time.perf_counter() - t0:.0f}s so far)")
    rows = []
    for i, d in enumerate(decisions):
        o, dp = one[i], deep[i]
        p1 = d.prior[1]
        rank = min(sorted(d.theirs, key=lambda b: -p1[b]).index(b) for b in d.reply) + 1
        best1 = max(o["ours"].values())
        row = {"log": d.log, "turn": d.turn, "won": d.won, "rank": rank,
               "prior": float(sum(p1[b] for b in d.reply)), "best1": best1,
               "best_hp": max(o["ours_hp"].values()), "settings": {}}
        if dp["ours"]:
            row["best_deep"] = max(dp["ours"].values())
            best_a = max(dp["ours"], key=dp["ours"].get)
            r = searched["default"][i]
            c0 = r.get("candidates", ([], []))[0]
            row["best_deep_considered"] = best_a in c0
            row["best_deep_weight"] = float(r["row"][c0.index(best_a)]) if best_a in c0 else 0.0
            row["best_deep_is_best1"] = o["ours"][best_a] >= best1 - 1e-9
        for name, _cfg in parsed:
            r = searched[name][i]
            a = r["pick"]
            c1 = r.get("candidates", ([], []))[1]
            row["settings"][name] = {
                "action": a,
                "value": o["ours"][a], "hp": o["ours_hp"][a],
                "deep": dp["ours"].get(a, float("nan")),
                "considered": float(any(b in c1 for b in d.reply)) if "candidates" in r else float("nan"),
                "weight": float(sum(w for b, w in zip(c1, r["col"]) if b in d.reply)) if "candidates" in r else float("nan"),
                "lookahead": lookahead_of(r, a),
                "estimate": float(r["value"]) if "value" in r else float("nan"),
                # The most a reply of theirs the root checked but left out
                # would gain against its mix (their view).
                "exposed": float(r["oracle_gain"][1]) if "oracle_gain" in r else float("nan"),
            }
        if d.played:
            a = d.played[0]
            row["played"] = {"value": o["ours"].get(a, float("nan")), "hp": o["ours_hp"].get(a, float("nan")),
                             "deep": dp["ours"].get(a, float("nan"))}
            # Their reply against what the bot played, from their side.
            th = o["theirs"]
            mine = min(th.get(b, np.inf) for b in d.reply)
            row["reply_rank"] = 1 + sum(v < mine - 1e-9 for v in th.values())
            row["reply_n"] = len(th)
            row["reply_loss"] = float(mine - min(th.values()))  # how much better their best was, for them
            r = searched["default"][i]
            if dp["theirs"] and r.get("candidates"):
                y = {b: w for b, w in zip(r["candidates"][1], r["col"]) if b in dp["theirs"] and b not in d.reply}
                if y:
                    tot = sum(y.values())
                    exp_v = sum(w * dp["theirs"][b] for b, w in y.items()) / tot if tot > 0 else float("nan")
                    # > 0: their actual reply did better for them than the
                    # replies the bot expected would have.
                    row["surprise"] = float(exp_v - dp["theirs"][d.reply[0]])
        rows.append(row)
        describe_row(d, row, parsed, out)
    return {"rows": rows, "stats": run_stats, "settings": [n for n, _ in parsed]}


def describe_row(d: Decision, row: dict, parsed, out) -> None:
    text = lambda a: d.ours.get(a, str(a))
    lines = [f"   {d.log} turn {d.turn}: their reply {d.theirs[d.reply[0]]} (prior rank {row['rank']}, "
             f"p={row['prior']:.3f}); best against it one turn ahead {row['best1']:+.2f}"
             + (f", judged deeper {row['best_deep']:+.2f}" if "best_deep" in row else "")]
    if "played" in row:
        lines.append(f"      played in the game: {text(d.played[0])}: value {row['played']['value']:+.2f}"
                     + (f", deeper {row['played']['deep']:+.2f}" if np.isfinite(row['played']['deep']) else "")
                     + f"; their reply ranked {row['reply_rank']} of {row['reply_n']} against it")
    for name, _ in parsed:
        s = row["settings"][name]
        lines.append(f"      {name:24s} {text(s['action'])}: value {s['value']:+.2f}"
                     + (f", deeper {s['deep']:+.2f}" if np.isfinite(s['deep']) else "")
                     + (f"; reply considered, weight {s['weight']:.2f}" if s['considered'] == 1
                        else "; reply not considered" if s['considered'] == 0 else "")
                     + (f"; looks {s['lookahead']:.2f} turns past this one" if np.isfinite(s['lookahead']) else ""))
    out("\n".join(lines))


def summary(result: dict, out=print, games: list | None = None):
    rows, settings, stats = result["rows"], result["settings"], result["stats"]
    if games:
        full = [g for g in games if g["all_seen"]]
        out(f"\nthe bot's guess of the opponent's four (at its first decision): a Pokemon it didn't expect "
            f"appeared in {sum(g['wrong'] > 0 for g in games)} of {len(games)} games"
            + (f"; right in {sum(g['wrong'] == 0 for g in full)} of the {len(full)} games where all four "
               f"appeared" if full else ""))
    if not rows:
        out("no decisions with a readable reply")
        return
    def mean(xs) -> float:
        a = np.array(list(xs), float)
        return float(np.nanmean(a)) if np.isfinite(a).any() else float("nan")
    out(f"\n{len(rows)} decisions with the opponent's reply known "
        f"({len({r['log'] for r in rows})} games, won {mean(r['won'] for r in rows):.0%} of the decisions' games)")
    out("\n1. Did the bot expect the reply?")
    out(f"   its rank in the policy's prediction: median {np.median([r['rank'] for r in rows]):.0f}; first in "
        f"{np.mean([r['rank'] == 1 for r in rows]):.0%}, top 4 in {np.mean([r['rank'] <= 4 for r in rows]):.0%}, "
        f"top 24 in {np.mean([r['rank'] <= 24 for r in rows]):.0%}; mean probability {mean(r['prior'] for r in rows):.3f}")
    d = [r["settings"]["default"] for r in rows]
    out(f"   the search (default) considered it in {mean(x['considered'] for x in d):.0%} of decisions, "
        f"equilibrium weight {mean(x['weight'] for x in d):.2f} on average")
    played = [r for r in rows if "played" in r]
    if played:
        out("\n2. How good was the reply, against what the bot played (one turn ahead, from their side)?")
        out(f"   its rank among all their replies: median {np.median([r['reply_rank'] for r in played]):.0f} "
            f"of {np.median([r['reply_n'] for r in played]):.0f}; their best in "
            f"{np.mean([r['reply_rank'] == 1 for r in played]):.0%}, top 5 in "
            f"{np.mean([r['reply_rank'] <= 5 for r in played]):.0%}; value left on the table (for them) "
            f"{mean(r['reply_loss'] for r in played):.3f}")
        sur = [r["surprise"] for r in played if "surprise" in r]
        if sur:
            out(f"   judged deeper, their reply against the replies the bot expected: better for them by "
                f"{np.mean(sur):+.3f} on average (better in {np.mean(np.array(sur) > 0.02):.0%}, "
                f"worse in {np.mean(np.array(sur) < -0.02):.0%}, over {len(sur)} decisions)")
    out("\n3. Was the bot's answer good, against the reply it got?")
    deep = any("best_deep" in r for r in rows)
    out(f"   {'setting':30s} {'value':>7s} {'regret':>7s} {'HP':>6s}" + (f" {'deeper':>7s} {'regret':>7s}" if deep else "")
        + f" {'looks ahead':>11s} {'exposed':>7s} {'estimate':>8s} {'s/decision':>10s}")
    lines = [("played in the game", [r["played"] for r in played], None)] if played else []
    lines += [(s, [r["settings"][s] for r in rows], s) for s in settings]
    for name, xs, s in lines:
        src = played if s is None else rows
        v = mean(x["value"] for x in xs)
        reg = mean(r["best1"] - x["value"] for r, x in zip(src, xs))
        h = mean(x["hp"] for x in xs)
        line = f"   {name:30s} {v:+7.3f} {reg:7.3f} {h:+6.2f}"
        if deep:
            dv = mean(x["deep"] for x in xs)
            dreg = mean(r.get("best_deep", np.nan) - x["deep"] for r, x in zip(src, xs))
            line += f" {dv:+7.3f} {dreg:7.3f}"
        look = mean(x["lookahead"] for x in xs) if s is not None else float("nan")
        est = mean(x["estimate"] for x in xs) if s is not None else float("nan")
        secs = stats[s]["seconds"] / len(rows) if s is not None and s in stats else float("nan")
        exposed = mean(x["exposed"] for x in xs) if s is not None else float("nan")
        line += f" {look:11.2f} {exposed:7.3f} {est:+8.3f} {secs:10.2f}"
        out(line.replace("nan", "  -").replace("+  -", "   -"))
    for s in settings:
        xs = [(r, r["settings"][s]) for r in rows if np.isfinite(r["settings"][s]["considered"])]
        parts = []
        for label, flag in (("considered their reply", 1.0), ("didn't", 0.0)):
            g = [(r, x) for r, x in xs if x["considered"] == flag]
            if g:
                parts.append(f"{label}: regret {mean(r['best1'] - x['value'] for r, x in g):.3f}"
                             + (f", deeper {mean(r.get('best_deep', np.nan) - x['deep'] for r, x in g):.3f}" if deep else "")
                             + f" ({len(g)} decisions)")
        if parts:
            out(f"   {s}, where it " + "; where it ".join(parts))
    if deep:
        b = [r for r in rows if "best_deep" in r]
        out(f"   the best answer judged deeper (of those judged) was among the default search's candidates in "
            f"{mean(r['best_deep_considered'] for r in b):.0%} of decisions (weight {mean(r['best_deep_weight'] for r in b):.2f}), "
            f"and the best one turn ahead too in {mean(r['best_deep_is_best1'] for r in b):.0%}")
    out("   (value: the network's estimate one turn later, side 0's, -1 to 1; regret: the best legal action's "
        "value against the same reply less this; deeper: a search's value of the position the turn leaves; "
        "looks ahead: turns past this one the search looked for its choice; exposed: the most a reply the root "
        "checked but left out would gain against its mix; estimate: the search's own value)")


def filter_logs(logs: list[Path], played_by: str | None = None, after: str | None = None) -> list[Path]:
    """The logs whose "|bot|played by" line contains `played_by` (logs from
    before the client wrote one never match), saved after `after`."""
    out = []
    since = None
    if after:
        fmt = "%Y-%m-%d %H:%M" if " " in after.strip() else "%Y-%m-%d"
        since = time.mktime(time.strptime(after.strip(), fmt))
    for path in logs:
        if since is not None and path.stat().st_mtime < since:
            continue
        if played_by:
            with open(path, encoding="utf-8") as f:
                head = f.readline()
            if not (head.startswith("|bot|played by ") and played_by in head):
                continue
        out.append(path)
    return out


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("logs", nargs="+")
    p.add_argument("--opponent", default=None, help="only games against this player")
    p.add_argument("--played-by", default=None,
                   help="only logs whose '|bot|played by' line contains this (e.g. ppo10, or budget=16000)")
    p.add_argument("--after", default=None, help="only logs saved after this date (YYYY-MM-DD or 'YYYY-MM-DD HH:MM')")
    p.add_argument("--settings", nargs="+", default=["policy", "default"])
    p.add_argument("--seeds", type=int, default=4, help="chance samples per action pair, one turn ahead")
    p.add_argument("--judge-budget", type=int, default=0,
                   help="also judge the strongest answers by a search of this many evaluations after the turn")
    p.add_argument("--judge-top", type=int, default=3, help="the best this many one turn ahead are judged deeper too")
    p.add_argument("--judge-seeds", type=int, default=2, help="chance samples per judged pair")
    p.add_argument("--limit", type=int, default=0, help="at most this many decisions (spread over the games)")
    p.add_argument("--batch", type=int, default=256, help="positions searched together")
    p.add_argument("--quiet", action="store_true", help="no line per decision")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = p.parse_args()
    torch.set_grad_enabled(False)
    model = load(a.model, a.device)
    # Windows shells pass wildcards on unexpanded: expand them here.
    logs = sorted({Path(f) for x in a.logs for f in (glob.glob(x) if glob.has_magic(x) else [x])})
    logs = filter_logs(logs, a.played_by, a.after)
    if not logs:
        raise SystemExit(f"no logs match {' '.join(a.logs)}"
                         + (" with those filters" if a.played_by or a.after else ""))
    games: list = []
    decisions = collect(model, logs, opponent=a.opponent, games=games)
    if a.limit and len(decisions) > a.limit:
        keep = np.linspace(0, len(decisions) - 1, a.limit).round().astype(int)
        decisions = [decisions[i] for i in sorted(set(keep))]
    say = (lambda *_: None) if a.quiet else print
    result = diagnose(model, decisions, a.settings, a.seeds, torch.device(a.device),
                      out=lambda s: (say(s) if s.startswith("   ") else print(s)),
                      judge_budget=a.judge_budget, judge_top=a.judge_top, judge_seeds=a.judge_seeds, batch=a.batch)
    summary(result, games=games)


if __name__ == "__main__":
    main()
