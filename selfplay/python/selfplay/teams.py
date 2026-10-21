"""Which teams are strongest: the trained bot plays every team in the corpus
against random opponents and against the top-placed teams, on both sides,
and a Bradley-Terry fit turns the results into a rating per team.

    python -m selfplay.teams runs/ppo3/model.pt --device cuda
    python -m selfplay.teams runs/ppo3/model.pt --games 2000 --top 4 --csv teams.csv

Half of each team's games are against a team drawn from the whole pool and
half against a team that placed --top or better ("Top 8": Champion, Runner
Up, Top 4 and Top 8). The report lists:

- every team's Elo (0 = the average team; a 100 Elo gap is a 64% score),
  its plain score, and its score against the top-placed teams;
- the mean Elo by tournament placement: whether the teams that placed
  well are the ones the bot wins with;
- the mean Elo of the teams each species appears on.

Both sides are the same network sampling its policy, so the ratings say how
well the bot plays each team, not only how good the team is. Teams that
need precise play (Trick Room modes, setup) rate low while the bot plays
them badly, and the corpus' sampling weights mean it has practised the
placed teams and your own teams most.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import time
from collections import defaultdict

import numpy as np
import torch

from selfplay.env import SelfPlayEnv
from selfplay.ladder import fit_elo
from selfplay.train import act, to_tensors

ELO = 400 / math.log(10)


def placement(name: str) -> int:
    """The placement in a corpus paste's name as a "Top N" number
    (Champion 1, Runner Up 2), or 0 for none. Mirrors corpus.rs."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    best = 0
    for i, w in enumerate(words):
        nxt = words[i + 1] if i + 1 < len(words) else ""
        p = 0
        if w == "champion":
            p = 1
        elif (w == "runner" and nxt == "up") or w == "runnerup":
            p = 2
        elif w == "top" and nxt.isdigit():
            p = int(nxt)
        if p and (best == 0 or p < best):
            best = p
    return best


def tier(p: int) -> str:
    if p == 0:
        return "unplaced"
    if p <= 2:
        return ["", "Champion", "Runner Up"][p]
    for n in (4, 8, 16, 32):
        if p <= n:
            return f"Top {n}"
    return "Top 64+"


@torch.no_grad()
def play(model, env: SelfPlayEnv, pairs: list[tuple[int, int]], amp: bool,
         report_every: float = 30.0) -> list[tuple[int, int, float]]:
    """Play each (side 0 team, side 1 team) pair once, the model on both
    sides sampling its policy. Returns (side 0 team, side 1 team, side 0
    score) per game."""
    env.set_matchups(pairs)
    n = env.num_envs
    current = [env.game_teams(g) for g in range(n)]
    # The env deals the schedule out in order (finished games restart in game
    # order) and plays on past its end: only scheduled games count, so the
    # short ones aren't favoured at the end.
    slot = list(range(n))
    next_slot = n
    results: list = [None] * len(pairs)
    left = len(pairs)
    start = last = time.time()
    while left:
        obs = env.observe()
        rows = np.flatnonzero(obs.decisions.reshape(-1) != 0)
        actions = np.full((n, 2), -1, np.int64)
        if len(rows):
            a, _, _ = act(model, to_tensors(obs, rows), amp=amp)
            actions.reshape(-1)[rows] = a.numpy()
        r = env.step(actions)
        for g in np.flatnonzero(r.done):
            if slot[g] < len(pairs):
                a_, b_ = current[g]
                results[slot[g]] = (a_, b_, float(r.reward[g, 0] + 1) / 2)
                left -= 1
            slot[g] = next_slot
            next_slot += 1
            current[g] = env.game_teams(g)
        if time.time() - last > report_every:
            last = time.time()
            done = len(pairs) - left
            rate = done / (last - start)
            print(f"  {done:,}/{len(pairs):,} games, {rate:.0f}/s, ~{left / max(rate, 1e-9) / 60:.0f} min left",
                  flush=True)
    return results


def schedule(rated: list[int], pool: list[int], top: list[int], games: int,
             rng: np.random.Generator) -> list[tuple[int, int]]:
    """About `games` games for each rated team: as many as it takes, half
    against the pool and half against the top teams, sides at random (no
    mirror matches)."""
    pairs = []
    for t in rated:
        for k in range(games):
            opps = top if (k % 2 and top) else pool
            o = t
            while o == t:
                o = int(opps[rng.integers(len(opps))])
            pairs.append((t, o) if rng.random() < 0.5 else (o, t))
    order = rng.permutation(len(pairs))
    return [pairs[i] for i in order]


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--games", type=int, default=600,
                   help="games each team starts (it also plays as others' opponent)")
    p.add_argument("--top", type=int, default=8, help="placed this or better counts as a top team")
    p.add_argument("--teams", nargs="*", default=None,
                   help="rate only these (names or parts of names); default every team")
    p.add_argument("--show", type=int, default=50, help="teams to list (0: all)")
    p.add_argument("--envs", type=int, default=1024)
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--csv", default=None, help="write every team's ratings here")
    a = p.parse_args()

    env = SelfPlayEnv(a.envs, seed=a.seed)
    names = env.team_names()
    rejected = env.rejected()
    if rejected:
        print(f"{len(rejected)} paste(s) left out (fix or remove them to include them):")
        for name, why in rejected[:20]:
            print(f"  {name}: {why}")
    place = [placement(n) if "/" not in n else 0 for n in names]
    top = [i for i, pl in enumerate(place) if 0 < pl <= a.top]
    pool = list(range(len(names)))
    if a.teams:
        from selfplay.meta import select
        rated = select(names, a.teams)
    else:
        rated = pool
    rng = np.random.default_rng(a.seed)
    pairs = schedule(rated, pool, top, a.games, rng)
    print(f"{len(rated)} team(s) rated in a pool of {len(pool)}, {len(top)} placed Top {a.top} or better; "
          f"{len(pairs):,} games")

    torch.set_grad_enabled(False)
    model = load(a.model, a.device)
    t0 = time.time()
    results = play(model, env, pairs, amp=a.device.startswith("cuda"))
    print(f"played {len(results):,} games in {(time.time() - t0) / 60:.1f} min")

    n = len(names)
    elo = fit_elo(n, [(i, j, s, 1) for i, j, s in results], iters=3000)
    # Every team's games, score, and games and score against the top teams.
    games = np.zeros(n)
    score = np.zeros(n)
    top_games = np.zeros(n)
    top_score = np.zeros(n)
    info = np.zeros(n)
    is_top = np.zeros(n, bool)
    is_top[top] = True
    for i, j, s in results:
        for t, o, st in ((i, j, s), (j, i, 1 - s)):
            games[t] += 1
            score[t] += st
            if is_top[o]:
                top_games[t] += 1
                top_score[t] += st
    # Standard errors from the fit's curvature (Fisher information).
    gamma = 10 ** (elo / 400)
    for i, j, _ in results:
        q = gamma[i] / (gamma[i] + gamma[j])
        info[i] += q * (1 - q)
        info[j] += q * (1 - q)
    played = games > 0
    elo = elo - elo[played].mean()
    se = ELO / np.sqrt(np.maximum(info, 1e-9))

    def row(t):
        vs = top_score[t] / top_games[t] if top_games[t] else float("nan")
        ci = 1.96 * math.sqrt(max(vs * (1 - vs), 1e-9) / top_games[t]) if top_games[t] else float("nan")
        return vs, ci

    order = sorted((t for t in rated if played[t]), key=lambda t: -elo[t])
    shown = order if a.show == 0 else order[:a.show]
    print(f"\n{'#':>3} {'team':<58} {'Elo':>5} {'±':>4} {'score':>6} {'vs top':>6} {'±':>5} "
          f"{'games':>6}  placed")
    for k, t in enumerate(shown, 1):
        vs, ci = row(t)
        print(f"{k:3d} {names[t][:58]:<58} {elo[t]:+5.0f} {1.96 * se[t]:4.0f} {score[t] / games[t]:6.3f} "
              f"{vs:6.3f} {ci:5.3f} {games[t]:6.0f}  {tier(place[t])}")
    if len(order) > len(shown):
        print(f"... {len(order) - len(shown)} more (--show 0 lists all)")

    print(f"\n{'placement':<12} {'teams':>5} {'mean Elo':>9} {'vs top':>7}")
    by_tier = defaultdict(list)
    for t in order:
        by_tier[tier(place[t]) if "/" not in names[t] else names[t].split("/")[0] + "/"].append(t)
    tiers = ["Champion", "Runner Up", "Top 4", "Top 8", "Top 16", "Top 32", "Top 64+", "unplaced"]
    for name in tiers + sorted(k for k in by_tier if k not in tiers):
        ts = by_tier.get(name)
        if not ts:
            continue
        vs = sum(top_score[t] for t in ts) / max(1, sum(top_games[t] for t in ts))
        print(f"{name:<12} {len(ts):5d} {np.mean([elo[t] for t in ts]):+9.0f} {vs:7.3f}")

    species = defaultdict(list)
    for t in order:
        for s in set(env.team_species(t)):
            species[s].append(t)
    common = [(s, ts) for s, ts in species.items() if len(ts) >= 5]
    common.sort(key=lambda x: -np.mean([elo[t] for t in x[1]]))
    print(f"\nSpecies on 5+ teams, by the mean Elo of their teams ({len(common)} species):")
    print(f"{'species':<24} {'teams':>5} {'mean Elo':>9} {'vs top':>7}")
    for s, ts in common:
        vs = sum(top_score[t] for t in ts) / max(1, sum(top_games[t] for t in ts))
        print(f"{s:<24} {len(ts):5d} {np.mean([elo[t] for t in ts]):+9.0f} {vs:7.3f}")

    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rank", "team", "elo", "elo_se", "score", "games", "vs_top", "vs_top_games",
                        "placed", "species"])
            for k, t in enumerate(order, 1):
                vs, _ = row(t)
                w.writerow([k, names[t], f"{elo[t]:.1f}", f"{se[t]:.1f}", f"{score[t] / games[t]:.4f}",
                            int(games[t]), f"{vs:.4f}", int(top_games[t]), tier(place[t]),
                            " / ".join(env.team_species(t))])
        print(f"wrote {a.csv}")


if __name__ == "__main__":
    main()
