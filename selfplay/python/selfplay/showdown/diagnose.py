"""How the bot's search fares against what an opponent actually played.

For every decision of the bot in saved battle logs: rebuild the position,
read the opponent's actual joint action that turn from the log, and with
each search setting

- where that reply stood in the bot's picture of the opponent: its rank in
  the policy's prediction, whether the search considered it, and the weight
  its equilibrium gave it;
- what the setting's choice scored against that reply after the turn, and
  the best any legal action scored against it (regret = the gap), judged
  by the network's value and, independently, by the HP left on each side
  (knocked out = 0);
- whether the opponent's Pokemon were even in the bot's picture (the bot
  guesses the two it hasn't seen).

    python -m selfplay.showdown.diagnose MODEL LOG [LOG ...] --settings default budget=8000 oracle_pool=0

A setting is `name` or `key=value,key=value` over TreeConfig ("policy": no
search). Each observed reply is one sample of how the opponent plays, so
the averages over many decisions estimate how a setting fares against it.
"""
from __future__ import annotations

import argparse
import glob
import json
from dataclasses import fields
from pathlib import Path

import numpy as np
import torch

from selfplay.env import DECISION_SLOTS, SelfPlayEnv
from selfplay.mcts import TreeConfig, TreeSearch
from selfplay.showdown.data import to_id
from selfplay.train import to_tensors


def parse_setting(text: str):
    """'default' / 'policy' / 'budget=8000,oracle_pool=0' -> (name, TreeConfig or None)."""
    if text == "policy":
        return text, None
    cfg = TreeConfig(sample=False)
    if text != "default":
        kinds = {f.name: f.type for f in fields(TreeConfig)}
        for kv in text.split(","):
            k, v = kv.split("=")
            cur = getattr(cfg, k)
            setattr(cfg, k, (v.lower() in ("1", "true", "yes")) if isinstance(cur, bool) else type(cur)(v))
            assert k in kinds
    return text, cfg


def observed_reply(lines: list[str], foe: str, position: dict, d) -> list[list[str]] | None:
    """Per opponent slot, the slot actions ('move 2 1', 'move 1 mega',
    'switch 3', ...) consistent with what the log shows this turn (until
    the end of the turn); [] where it can't be told (it fainted first,
    flinched...). None when the turn shows nothing of the opponent."""
    mons = position["sides"][1]["pokemon"]
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
    """Opponent joint actions (indices) consistent with the observed slots."""
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


@torch.no_grad()
def score_actions(model, position: str, ours: list[int], theirs: list[int], seeds: int, dev):
    """For each of our actions: (network value, HP margin) after one turn
    against `theirs` (averaged over them and over chance). The HP margin is
    the brought Pokemon's HP fractions summed, ours less theirs (fainted =
    0); a turn that ends the game counts as +-1 and +-4."""
    uids = [[m["uid"] for m in side["pokemon"]] for side in json.loads(position)["sides"]]
    pairs = [(a, b, s) for a in ours for b in theirs for s in range(seeds)]
    vals = np.zeros(len(pairs))
    hps = np.zeros(len(pairs))
    chunk = 256
    for start in range(0, len(pairs), chunk):
        part = pairs[start:start + chunk]
        env = SelfPlayEnv(len(part), seed=0, threads=0)
        for g, (a, b, s) in enumerate(part):
            env._env.set_position(g, position, 1000 + s)
        r = env.step(np.array([[a, b] for a, b, _ in part], np.int64))
        done, reward = r.done.astype(bool).copy(), r.reward.copy()
        obs = env.observe()
        _, v = model(*(t.to(dev) for t in to_tensors(obs)))
        v = v.float().cpu().numpy().reshape(-1, 2)
        m = obs.mons  # [n, 2 views, tokens, floats]: view 0 is ours
        for g in range(len(part)):
            if done[g]:
                vals[start + g] = reward[g, 0]
                hps[start + g] = 4.0 * reward[g, 0]
                continue
            vals[start + g] = (v[g, 0] - v[g, 1]) / 2
            hp = lambda tok: 0.0 if m[g, 0, tok, 28] > 0.5 else float(m[g, 0, tok, 1])
            hps[start + g] = sum(hp(u) for u in uids[0]) - sum(hp(6 + u) for u in uids[1])
    n = len(theirs) * seeds
    return vals.reshape(len(ours), n).mean(1), hps.reshape(len(ours), n).mean(1)


def diagnose(model, logs: list[Path], settings: list[str], seeds: int, dev, out=print, repeats: int = 1):
    parsed = [parse_setting(s) for s in settings]
    rows = []
    for log in logs:
        try:
            rows += diagnose_log(model, log, parsed, seeds, dev, out, repeats)
        except Exception as e:  # one bad log shouldn't end the run
            out(f"{log.name}: skipped ({type(e).__name__}: {e})")
    return rows


def diagnose_log(model, log: Path, parsed, seeds: int, dev, out, repeats: int) -> list[dict]:
    from selfplay.showdown.bot import Bot
    from selfplay.showdown.review import match_team
    from selfplay.showdown.team import complete_paste
    from selfplay.showdown.tracker import Tracker
    rows = []
    lines = log.read_text(encoding="utf-8").splitlines()
    first = next((json.loads(l[9:]) for l in lines if l.startswith("|request|{")), {})
    path = match_team(first)
    if path is None:
        out(f"{log.name}: no team matches its stats; skipped")
        return rows
    team = complete_paste(path.read_text(encoding="utf-8-sig"))[0]
    side = first.get("side", {})
    us = side.get("id", "p1")
    foe = "p2" if us == "p1" else "p1"
    t = Tracker(team, our_name=side.get("name"))
    bot = Bot(model, team, None, seed=0)
    out(f"== {log.name} ({path.stem})")
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
        j = k
        if req.get("teamPreview"):
            bot.preview(t)  # its picture of what the opponent brings
            continue
        if req.get("forceSwitch"):
            continue  # the opponent doesn't act on a replacement
        position = t.position(bot.guess_brought)
        ps = json.dumps(position)
        env = SelfPlayEnv(1, seed=0, threads=1)
        env._env.set_position(0, ps, 1)
        obs = env.observe()
        if obs.decisions[0, 0] != DECISION_SLOTS or obs.decisions[0, 1] != DECISION_SLOTS:
            continue
        per_slot = observed_reply(lines[j:], foe, position, t.d)
        theirs = dict(env.legal_choices(0, 1))
        actual = matching(theirs, per_slot) if per_slot else []
        unseen = per_slot and any(p.startswith("?") for s in per_slot for p in s)
        if not actual:
            out(f"   turn {t.turn}: opponent's reply {'involves a Pokemon the bot did not expect' if unseen else 'not readable'}; skipped")
            continue
        ours = [i for i in range(obs.masks.shape[-1]) if obs.masks[0, 0, i]]
        ours_text = dict(env.legal_choices(0, 0))
        logp, _ = model(*(x.to(dev) for x in to_tensors(obs, np.array([0, 1]))))
        p1 = np.exp(logp[1].float().cpu().numpy())
        prior_rank = sorted(theirs, key=lambda a: -p1[a])
        rank = min(prior_rank.index(a) for a in actual) + 1
        val, hp = score_actions(model, ps, ours, actual, seeds, dev)
        best_v, best_h = val.max(), hp.max()
        row = {"log": log.name, "turn": t.turn, "rank": rank, "prior": float(p1[actual].sum()),
               "best_v": best_v, "best_h": best_h, "n_ours": len(ours)}
        desc = [f"   turn {t.turn}: their reply {theirs[actual[0]]} (prior rank {rank}, "
                f"p={p1[actual].sum():.3f}); best vs it: value {best_v:+.2f}, HP {best_h:+.2f} "
                f"({ours_text[ours[int(val.argmax())]]})"]
        for name, cfg in parsed:
            runs = []
            for rep in range(1 if cfg is None else repeats):
                env._env.set_position(0, ps, 1)
                if cfg is None:
                    a = max(ours, key=lambda x: logp[0][x].item())
                    seen = weight = float("nan")
                else:
                    search = TreeSearch(model, TreeConfig(**vars(cfg)), seed=1 + rep)
                    r = search.search(env, [0])[0]
                    a = search.pick(r, 0)
                    c1 = r["candidates"][1]
                    seen = float(any(x in c1 for x in actual))
                    weight = sum(w for x, w in zip(c1, r["col"]) if x in actual)
                i = ours.index(a) if a in ours else None
                runs.append((a, val[i] if i is not None else float("nan"),
                             hp[i] if i is not None else float("nan"), seen, weight))
            v, h, seen, weight = (float(np.mean([x[k] for x in runs])) for k in (1, 2, 3, 4))
            row[name] = {"value": v, "hp": h, "seen": seen, "weight": weight}
            plays = "; ".join(sorted({str(ours_text.get(x[0], x[0])) for x in runs}))
            desc.append(f"      {name:28s} value {v:+.2f} (regret {best_v - v:.2f}), HP {h:+.2f}; "
                        f"reply considered {seen:.0%}, weight {weight:.2f}; plays {plays}")
        out("\n".join(desc))
        rows.append(row)
    return rows


def summary(rows: list[dict], settings: list[str], out=print):
    if not rows:
        out("no decisions with a readable reply")
        return
    out(f"\n{len(rows)} decisions with the opponent's reply known")
    out(f"their reply's rank in the bot's prediction: median {np.median([r['rank'] for r in rows]):.0f}, "
        f"top 4 in {np.mean([r['rank'] <= 4 for r in rows]):.0%}, top 24 in {np.mean([r['rank'] <= 24 for r in rows]):.0%}; "
        f"mean predicted probability {np.mean([r['prior'] for r in rows]):.3f}")
    out(f"{'setting':28s} {'value':>7s} {'regret':>7s} {'HP':>6s} {'considered':>10s} {'weight':>7s}")
    for s in settings:
        v = np.array([r[s]["value"] for r in rows])
        reg = np.array([r["best_v"] - r[s]["value"] for r in rows])
        h = np.array([r[s]["hp"] for r in rows])
        seen = [r[s]["seen"] for r in rows]
        w = [r[s]["weight"] for r in rows]
        seen, w = np.array(seen, float), np.array(w, float)
        considered = f"{np.nanmean(seen):10.0%} {np.nanmean(w):7.2f}" if np.isfinite(seen).any() else f"{'-':>10s} {'-':>7s}"
        out(f"{s:28s} {np.nanmean(v):+7.3f} {np.nanmean(reg):7.3f} {np.nanmean(h):+6.2f} {considered}")


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("logs", nargs="+")
    p.add_argument("--settings", nargs="+", default=["policy", "default"])
    p.add_argument("--seeds", type=int, default=6, help="chance samples per action pair")
    p.add_argument("--repeats", type=int, default=2, help="searches per setting and decision (they're random)")
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = p.parse_args()
    torch.set_grad_enabled(False)
    model = load(a.model, a.device)
    # Windows shells pass wildcards on unexpanded: expand them here.
    logs = sorted({Path(f) for x in a.logs for f in (glob.glob(x) if glob.has_magic(x) else [x])})
    if not logs:
        raise SystemExit(f"no logs match {' '.join(a.logs)}")
    rows = diagnose(model, logs, a.settings, a.seeds, torch.device(a.device), repeats=a.repeats)
    summary(rows, a.settings)


if __name__ == "__main__":
    main()
