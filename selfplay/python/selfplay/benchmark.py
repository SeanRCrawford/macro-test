"""How fast the network runs on this machine: inference (a rollout step's
batch) and a training step (forward, backward and Adam on a minibatch),
for the current code and for the code before the bfloat16 attention and
action embeddings (the same weights), with peak GPU memory.

    python -m selfplay.benchmark runs/ppo3/model.pt --device cuda

Inputs are real positions from self-play games.
"""
from __future__ import annotations

import argparse
import copy
import time

import numpy as np
import torch
from torch import nn

from selfplay.env import SelfPlayEnv
from selfplay.train import autocast, to_tensors


def positions(n: int, seed: int = 0):
    """n observation rows (both sides of games a few random moves in)."""
    env = SelfPlayEnv(max(64, n // 8), seed=seed)
    rows = []
    for step in range(40):
        obs = env.observe()
        r = np.flatnonzero(obs.decisions.reshape(-1) != 0)
        rows.append([t[r] for t in to_tensors(obs)])
        env.step(env.random_actions(seed * 1000 + step))
    cat = [torch.cat(parts) for parts in zip(*rows)]
    idx = torch.randperm(len(cat[0]), generator=torch.Generator().manual_seed(seed))
    idx = idx.repeat(n // len(idx) + 1)[:n]
    return [t[idx] for t in cat]


def legacy(model):
    """The same network as the code before: nn.TransformerEncoder (PyTorch's
    math attention) and action embeddings promoted to float32."""
    m = copy.deepcopy(model)
    d = model.encoder.layers[0].linear1.in_features
    heads = model.encoder.layers[0].heads
    layer = nn.TransformerEncoderLayer(d, heads, 2 * d, dropout=0.0, batch_first=True,
                                       norm_first=True, activation="gelu")
    enc = nn.TransformerEncoder(layer, len(model.encoder.layers), enable_nested_tensor=False)
    enc.load_state_dict(model.encoder.state_dict())
    m.encoder = enc.to(next(model.parameters()).device)
    m.actions.low_precision = False
    return m


def bench(model, batch, dev, amp, train: bool, iters: int) -> tuple[float, float]:
    """Samples per second, and peak memory (GB) on CUDA."""
    ints, mons, field, masks, dec = (t.to(dev) for t in batch)
    B = len(ints)
    legal = masks.bool()
    act = torch.multinomial(legal.float() + 1e-9, 1).squeeze(1)
    opt = torch.optim.Adam(model.parameters(), lr=1e-6) if train else None
    model.train(train)

    def step():
        if not train:
            with torch.no_grad(), autocast(dev, amp):
                model(ints, mons, field, masks, dec)
            return
        with autocast(dev, amp):
            logp, value, opp = model(ints, mons, field, masks, dec, masks)
        loss = -logp.gather(1, act[:, None]).mean() + (value.float() ** 2).mean() \
            - opp.gather(1, act[:, None]).mean()
        opt.zero_grad(set_to_none=True)
        loss.backward()
        opt.step()

    sync = (lambda: torch.cuda.synchronize(dev)) if dev.type == "cuda" else (lambda: None)
    for _ in range(3):
        step()
    sync()
    if dev.type == "cuda":
        torch.cuda.reset_peak_memory_stats(dev)
    t = time.perf_counter()
    for _ in range(iters):
        step()
    sync()
    dt = time.perf_counter() - t
    peak = torch.cuda.max_memory_allocated(dev) / 2**30 if dev.type == "cuda" else float("nan")
    return B * iters / dt, peak


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--batch", type=int, default=4096, help="rows per call (a rollout step / minibatch)")
    p.add_argument("--iters", type=int, default=20)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    p.add_argument("--no-amp", action="store_true")
    a = p.parse_args()
    dev = torch.device(a.device)
    model = load(a.model, a.device)
    batch = positions(a.batch)
    variants = [("before", legacy(model)), ("now", model)]
    print(f"{a.batch} rows per call, {a.iters} calls, {'float32' if a.no_amp else 'bfloat16 autocast'} on {dev}")
    print(f"{'code':8s} {'inference/s':>12s} {'train/s':>10s} {'peak GB (train)':>16s}")
    results = {}
    for name, m in variants:
        inf, _ = bench(m, batch, dev, not a.no_amp, False, a.iters)
        tr, peak = bench(m, batch, dev, not a.no_amp, True, a.iters)
        results[name] = (inf, tr)
        print(f"{name:8s} {inf:12,.0f} {tr:10,.0f} {peak:16.2f}", flush=True)
    b, n = results["before"], results["now"]
    print(f"speedup: inference x{n[0] / b[0]:.2f}, training x{n[1] / b[1]:.2f}")


if __name__ == "__main__":
    main()
