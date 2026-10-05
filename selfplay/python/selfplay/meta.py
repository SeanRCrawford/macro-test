"""How teams do against the meta: the trained bot plays each team against
each meta team, on both sides, and reports win rates.

The meta defaults to the placed tournament teams (Champion to Top 32) in
data/corpus. Both sides play the bot's policy, sampled (its own mixed
strategy); `--greedy` plays its most likely actions instead.

    python -m selfplay.meta runs/search/model.pt --teams my_teams teams/ --games 20
    python -m selfplay.meta runs/search/model.pt --teams "teams/sand" --vs placed --csv sand.csv

`--teams` takes names or name prefixes ("my_teams" = every team in
data/my_teams); `--vs` is "placed", "all" (the whole tournament corpus) or
a list of names/prefixes.
"""
from __future__ import annotations

import argparse
import csv
import math
import time

import numpy as np
import torch

from selfplay.env import SelfPlayEnv
from selfplay.train import act, to_tensors


def select(names: list[str], specs: list[str]) -> list[int]:
    out = []
    for spec in specs:
        hits = [i for i, n in enumerate(names) if n == spec] or \
               [i for i, n in enumerate(names) if n.startswith(spec)] or \
               [i for i, n in enumerate(names) if spec.lower() in n.lower()]
        if not hits:
            raise SystemExit(f"no team matches {spec!r}")
        out += [i for i in hits if i not in out]
    return out


def meta_teams(env: SelfPlayEnv, vs: list[str]) -> list[int]:
    names, weights = env.team_names(), env.team_weights()
    corpus = [i for i, n in enumerate(names) if "/" not in n]
    if vs == ["placed"]:
        return [i for i in corpus if weights[i] >= 2.0]
    if vs == ["all"]:
        return corpus
    return select(names, vs)


@torch.no_grad()
def play_matchups(model, env: SelfPlayEnv, pairs: list[tuple[int, int]], games_per_pair: int,
                  greedy: bool = False, report_every: float = 30.0) -> dict:
    """Play every (side 0 team, side 1 team) pair `games_per_pair` times
    (about), the model on both sides. Returns {pair: [side 0 score, games]}."""
    env.set_matchups(pairs)
    n = env.num_envs
    current = [env.game_teams(g) for g in range(n)]
    tally = {p: [0.0, 0] for p in pairs}
    target = len(pairs) * games_per_pair
    done = 0
    start = last = time.time()
    while done < target:
        obs = env.observe()
        rows = np.flatnonzero(obs.decisions.reshape(-1) != 0)
        actions = np.full((n, 2), -1, np.int64)
        if len(rows):
            a, _, _ = act(model, to_tensors(obs, rows), greedy=greedy)
            actions.reshape(-1)[rows] = a.numpy()
        r = env.step(actions)
        for g in np.flatnonzero(r.done):
            t = tally[current[g]]
            t[0] += (r.reward[g, 0] + 1) / 2
            t[1] += 1
            current[g] = env.game_teams(g)
            done += 1
        if time.time() - last > report_every:
            last = time.time()
            print(f"  {done}/{target} games, {done / (last - start):.0f}/s", flush=True)
    return tally


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--teams", nargs="+", default=["my_teams", "teams/"],
                   help="teams to rate (names or prefixes)")
    p.add_argument("--vs", nargs="+", default=["placed"], help="the meta: placed, all, or names")
    p.add_argument("--games", type=int, default=20, help="games per matchup and side")
    p.add_argument("--envs", type=int, default=256)
    p.add_argument("--greedy", action="store_true")
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--csv", default=None, help="also write every matchup's result here")
    a = p.parse_args()

    env = SelfPlayEnv(a.envs, seed=a.seed)
    names, weights = env.team_names(), env.team_weights()
    teams = select(names, a.teams)
    meta = [m for m in meta_teams(env, a.vs)]
    print(f"{len(teams)} team(s) against a meta of {len(meta)}, {a.games} games per matchup and side "
          f"({2 * a.games * len(teams) * len(meta)} games)")
    model = load(a.model, a.device)
    pairs = [(t, m) for t in teams for m in meta if t != m]
    pairs += [(m, t) for t, m in pairs]
    tally = play_matchups(model, env, pairs, a.games, a.greedy)

    rows = []
    for t in teams:
        per = []
        for m in meta:
            if m == t:
                continue
            s0, n0 = tally[(t, m)]
            s1, n1 = tally[(m, t)]
            games = n0 + n1
            score = (s0 + (n1 - s1)) / max(1, games)
            per.append((m, score, games))
        w = np.array([weights[m] for m, _, _ in per])
        sc = np.array([s for _, s, _ in per])
        total = sum(g for *_, g in per)
        mean = float(sc.mean()) if len(sc) else 0.0
        weighted = float((w * sc).sum() / w.sum()) if len(sc) else 0.0
        worst = sorted(per, key=lambda x: x[1])[:3]
        rows.append((t, mean, weighted, total, worst, per))
    rows.sort(key=lambda r: -r[2])
    print(f"\n{'team':<60} {'vs meta':>8} {'weighted':>9} {'±95%':>6}  worst matchups")
    for t, mean, weighted, total, worst, _ in rows:
        ci = 1.96 * math.sqrt(max(weighted * (1 - weighted), 1e-9) / max(1, total))
        print(f"{names[t][:60]:<60} {mean:8.3f} {weighted:9.3f} {ci:6.3f}  "
              + "; ".join(f"{names[m][:28]} {s:.2f}" for m, s, _ in worst))
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["team", "opponent", "opponent_weight", "score", "games"])
            for t, *_, per in rows:
                for m, s, g in per:
                    w.writerow([names[t], names[m], weights[m], f"{s:.4f}", g])
        print(f"wrote {a.csv}")


if __name__ == "__main__":
    main()
