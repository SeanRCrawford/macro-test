"""How much reduced precision changes a model's outputs on real positions.

The tree search values its leaves in float16; training and the
evaluations use bfloat16. This measures both against float32: the values the search uses (mean and largest difference, how
often one is off by more than 0.1, any that come out infinite or NaN), the
policy's most likely action, and the largest activation inside the network
(float16 overflows past 65,504).

    python -m selfplay.precision runs/ppo6k/model.pt
"""
from __future__ import annotations

import argparse

import numpy as np
import torch

from selfplay.benchmark import positions


@torch.no_grad()
def outputs(model, batch, dev, dtype):
    ints, mons, field, masks, dec = (t.to(dev) for t in batch)
    with torch.autocast(dev.type, dtype=dtype or torch.float32, enabled=dtype is not None):
        value = model.value(ints, mons, field).float()
        logp, _ = model(ints, mons, field, masks, dec)
    return value.cpu().numpy(), logp.float().argmax(-1).cpu().numpy()


@torch.no_grad()
def largest_activation(model, batch, dev) -> float:
    big = [0.0]
    hooks = [m.register_forward_hook(lambda _m, _i, out: big.__setitem__(0, max(big[0], out.abs().max().item())))
             for m in model.modules() if isinstance(m, torch.nn.Linear)]
    ints, mons, field, masks, dec = (t.to(dev) for t in batch)
    model(ints, mons, field, masks, dec)
    for h in hooks:
        h.remove()
    return big[0]


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--positions", type=int, default=4096)
    p.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = p.parse_args()
    dev = torch.device(a.device)
    model = load(a.model, a.device)
    batch = positions(a.positions)
    ref_v, ref_a = outputs(model, batch, dev, None)
    print(f"{a.positions} positions on {dev}; largest activation (float32): "
          f"{largest_activation(model, batch, dev):,.0f} (float16 overflows past 65,504)")
    kinds = [("bfloat16", torch.bfloat16)] + ([("float16", torch.float16)] if dev.type == "cuda" else [])
    for name, dtype in kinds:
        v, act = outputs(model, batch, dev, dtype)
        bad = ~np.isfinite(v)
        d = np.abs(np.where(bad, 0, v) - ref_v)
        print(f"{name:9s} values: mean diff {d.mean():.4f}, largest {d.max():.4f}, "
              f"{(d > 0.1).mean():.1%} off by > 0.1, {bad.sum()} not finite; "
              f"same most likely action {np.mean(act == ref_a):.1%}")


if __name__ == "__main__":
    main()
