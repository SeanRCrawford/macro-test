"""Evaluate a trained model against the fixed baselines, or head to head
against another model (each plays both sides, half the games each).

    python -m selfplay.evaluate runs/tiny/model.pt --games 2000
    python -m selfplay.evaluate runs/search/model.pt --against runs/tiny/model.pt
"""
from __future__ import annotations

import argparse
import math

import numpy as np
import torch

from selfplay.env import SelfPlayEnv
from selfplay.model import PolicyNet
from selfplay.train import act, evaluate, to_tensors


def load(path: str, device: str = "cpu") -> PolicyNet:
    ckpt = torch.load(path, map_location=device)
    cfg = ckpt["config"]
    model = PolicyNet(cfg["d"], cfg["layers"])
    model.load_state_dict(ckpt["model"])
    return model.to(device).eval()


@torch.no_grad()
def head_to_head(a: PolicyNet, b: PolicyNet, games: int, seed: int, perfect_info: bool = True,
                 sample: bool = False) -> float:
    """`a`'s score against `b`, both playing their most likely actions (or
    sampling their policies); `a` is side 0 in half the games and side 1 in
    the rest."""
    score = done = 0.0
    for a_side in (0, 1):
        env = SelfPlayEnv(min(128, games // 2), seed=seed + a_side, perfect_info=perfect_info)
        played = 0
        while played < games // 2:
            obs = env.observe()
            actions = np.full((env.num_envs, 2), -1, np.int64)
            for model, side in ((a, a_side), (b, 1 - a_side)):
                rows = np.flatnonzero(obs.decisions[:, side] != 0) * 2 + side
                if len(rows):
                    act_, _, _ = act(model, to_tensors(obs, rows), greedy=not sample)
                    actions[rows // 2, side] = act_.numpy()
            r = env.step(actions)
            fin = r.done.astype(bool)
            played += fin.sum()
            score += ((r.reward[fin, a_side] + 1) / 2).sum()
        done += played
    return float(score / done)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("model")
    p.add_argument("--games", type=int, default=2000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--hidden", action="store_true")
    p.add_argument("--device", default="cpu")
    p.add_argument("--against", default=None, help="another model.pt to play head to head")
    p.add_argument("--sample", action="store_true",
                   help="head to head: sample each policy instead of its most likely action")
    a = p.parse_args()
    model = load(a.model, a.device)
    if a.against:
        score = head_to_head(model, load(a.against, a.device), a.games, a.seed, not a.hidden, a.sample)
        se = math.sqrt(score * (1 - score) / a.games)
        print(f"vs {a.against}: {score:.3f} ± {1.96 * se:.3f} (95%, {a.games} games)")
        return
    for opp in ("random", "greedy"):
        score = evaluate(model, opp, a.games, a.seed, not a.hidden)
        se = math.sqrt(score * (1 - score) / a.games)
        print(f"vs {opp:>6}: {score:.3f} ± {1.96 * se:.3f} (95%, {a.games} games)")


if __name__ == "__main__":
    main()
