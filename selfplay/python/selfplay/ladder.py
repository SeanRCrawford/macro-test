"""Ratings of a run's checkpoints: each numbered checkpoint plays its
predecessor and an anchor (the run's start, or --anchor), both sampling
their policies, and a Bradley-Terry fit turns the scores into Elo relative
to the anchor. Progress shows as a rising curve; a flat one is a plateau.

    python -m selfplay.ladder runs/ppo1 --anchor runs/big/model.pt --games 400 --device cuda
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import torch

from selfplay.evaluate import head_to_head, load


def checkpoints(run: Path, every: int) -> list[Path]:
    found = sorted(run.glob("model_*.pt"), key=lambda p: int(re.findall(r"\d+", p.stem)[-1]))
    return found[::every] if every > 1 else found


def fit_elo(n: int, results: list[tuple[int, int, float, int]], iters: int = 2000) -> np.ndarray:
    """Bradley-Terry by minorise-maximise; results are (i, j, score of i,
    games). Player 0 is fixed at 0 Elo."""
    w = np.zeros((n, n))
    g = np.zeros((n, n))
    for i, j, s, k in results:
        w[i, j] += s * k
        w[j, i] += (1 - s) * k
        g[i, j] += k
        g[j, i] += k
    gamma = np.ones(n)
    for _ in range(iters):
        wins = w.sum(1) + 0.5  # a half-win prior keeps 0% and 100% finite
        denom = (g / (gamma[:, None] + gamma[None, :])).sum(1) + 1.0 / (gamma + 1.0)
        gamma = wins / denom
        gamma /= gamma[0]
    return 400 * np.log10(gamma)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run")
    p.add_argument("--anchor", default=None, help="model.pt at 0 Elo (default: the first checkpoint)")
    p.add_argument("--games", type=int, default=400, help="games per pairing")
    p.add_argument("--every", type=int, default=1, help="use every n-th checkpoint")
    p.add_argument("--device", default="cpu")
    p.add_argument("--seed", type=int, default=7)
    a = p.parse_args()
    paths = checkpoints(Path(a.run), a.every)
    if a.anchor:
        paths = [Path(a.anchor)] + paths
    if len(paths) < 2:
        raise SystemExit(f"need two checkpoints, found {len(paths)}")
    torch.set_grad_enabled(False)
    results = []
    prev = load(str(paths[0]), a.device)
    anchor = prev
    for i in range(1, len(paths)):
        cur = load(str(paths[i]), a.device)
        s = head_to_head(cur, prev, a.games, a.seed + i, sample=True)
        results.append((i, i - 1, s, a.games))
        line = f"{paths[i].name}: vs previous {s:.3f}"
        if i > 1:
            t = head_to_head(cur, anchor, a.games, a.seed + 1000 + i, sample=True)
            results.append((i, 0, t, a.games))
            line += f", vs anchor {t:.3f}"
        print(line, flush=True)
        prev = cur
    elo = fit_elo(len(paths), results)
    # One standard error of a 50% score over `games` games, in Elo.
    se = 400 / math.log(10) * 2 / math.sqrt(a.games)
    print(f"\nElo relative to {paths[0].name} (one pairing's standard error ~{se:.0f}):")
    for path, e in zip(paths, elo):
        meta = torch.load(path, map_location="cpu")
        extra = "".join(f"  {k} {meta[k]:,}" for k in ("updates", "games") if k in meta)
        print(f"  {path.name:28s} {e:+7.0f}{extra}")


if __name__ == "__main__":
    main()
