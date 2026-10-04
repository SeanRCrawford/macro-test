"""Evaluate a trained model against the fixed baselines.

    python -m selfplay.evaluate runs/tiny/model.pt --games 2000
"""
from __future__ import annotations

import argparse
import math

import torch

from selfplay.model import PolicyNet
from selfplay.train import evaluate


def load(path: str) -> PolicyNet:
    ckpt = torch.load(path)
    cfg = ckpt["config"]
    model = PolicyNet(cfg["d"], cfg["layers"])
    model.load_state_dict(ckpt["model"])
    return model.eval()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("model")
    p.add_argument("--games", type=int, default=2000)
    p.add_argument("--seed", type=int, default=12345)
    p.add_argument("--hidden", action="store_true")
    a = p.parse_args()
    model = load(a.model)
    for opp in ("random", "greedy"):
        score = evaluate(model, opp, a.games, a.seed, not a.hidden)
        se = math.sqrt(score * (1 - score) / a.games)
        print(f"vs {opp:>6}: {score:.3f} ± {1.96 * se:.3f} (95%, {a.games} games)")


if __name__ == "__main__":
    main()
