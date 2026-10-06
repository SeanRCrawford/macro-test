"""Tree search (DESIGN.md 4.16): the network-guided simultaneous-move search
over many games at once. The tree lives in Rust (engine/src/mcts.rs); this
module runs the network for it in batches.

Each wave: the trees run their simulations (`select`); the new nodes get a
policy from the network (each side's legal actions, ranked, from its own
view); the new cells' chance outcomes are played (`expand`) and valued by
the network (`set_values`), and the trees back the values up. A root stops
when it has spent `budget` value evaluations or is solved.

    python -m selfplay.search MODEL --tree --tree-budget 2000 --vs-search
"""
from __future__ import annotations

import time
from dataclasses import dataclass, fields

import numpy as np
import torch

from selfplay.env import SIZES, SelfPlayEnv

MASK = SIZES["mask_len"]


@dataclass
class TreeConfig:
    budget: int = 2000          # value evaluations per root
    root_candidates: int = 4    # candidates per side a root starts with
    node_candidates: int = 2    # ... and any other node
    max_candidates: int = 12
    widen: float = 0.5          # candidates allowed: start + widen * sqrt(visits)
    c_explore: float = 1.0      # prior bonus in action selection
    chance_floor: float = 0.1   # outcome selection weight of a decided position
    static_weight: float = 1.0  # a node's own estimate counts as this many visits
    max_outcomes: int = 16
    roll_bands: int = 1
    solve_iters: int = 200
    max_depth: int = 8
    sims_per_wave: int = 4
    root_oracle: bool = False   # widen the root by best reply (Nessie's double oracle)
    oracle_eps: float = 0.005
    prior_top: int = 24         # ranked actions kept per side from the policy
    root_noise: float = 0.0     # Dirichlet noise share at the roots (self-play exploration)
    noise_alpha: float = 0.3
    leaf_batch: int = 8192
    sample: bool = True         # play a sample of the root mix (else its most likely action)

    def engine_kwargs(self) -> dict:
        keys = ("root_candidates", "node_candidates", "max_candidates", "widen", "c_explore",
                "chance_floor", "static_weight", "max_outcomes", "roll_bands", "solve_iters",
                "max_depth", "sims_per_wave", "root_oracle", "oracle_eps")
        return {k: getattr(self, k) for k in keys}


class TreeSearch:
    def __init__(self, model, cfg: TreeConfig = TreeConfig(), seed: int = 0):
        self.model, self.cfg = model, cfg
        self.dev = next(model.parameters()).device
        self.rng = np.random.default_rng(seed)
        self.stats = {"roots": 0, "leaves": 0, "nodes": 0, "gap": 0.0, "seconds": 0.0,
                      "depth": 0, "waves": 0, "exact": 0}

    def _t(self, a):
        return torch.from_numpy(np.ascontiguousarray(a)).to(self.dev)

    @torch.no_grad()
    def _priors(self, forest, n: int, noise: bool):
        s, m = SIZES, self.cfg.prior_top
        ints = np.empty((n, 2, s["tokens"], s["int_fields"]), np.int32)
        mons = np.empty((n, 2, s["tokens"], s["mon_floats"]), np.float32)
        fld = np.empty((n, 2, s["field_floats"]), np.float32)
        masks = np.empty((n, 2, MASK), np.uint8)
        dec = np.empty((n, 2), np.uint8)
        forest.policy_inputs(ints, mons, fld, masks, dec)
        flat = lambda a: a.reshape(-1, *a.shape[2:])
        actions = np.full((n * 2, m), -1, np.int64)
        probs = np.zeros((n * 2, m), np.float32)
        for i in range(0, n * 2, self.cfg.leaf_batch):
            j = min(n * 2, i + self.cfg.leaf_batch)
            logp, _ = self.model(*(self._t(flat(a)[i:j]) for a in (ints, mons, fld, masks, dec)))
            legal = self._t(flat(masks)[i:j]).bool()
            p = logp.exp().masked_fill(~legal, 0.0)
            if noise and self.cfg.root_noise > 0:
                # AlphaZero's root noise: some exploration the prior wouldn't give.
                d = torch.distributions.Dirichlet(torch.full_like(p, self.cfg.noise_alpha)).sample()
                d = d.masked_fill(~legal, 0.0)
                d = d / d.sum(-1, keepdim=True).clamp(min=1e-9)
                p = (1 - self.cfg.root_noise) * p + self.cfg.root_noise * d
            top = p.topk(min(m, p.shape[-1]), -1)
            ok = (top.values > 0).cpu().numpy()
            a = top.indices.cpu().numpy()
            pr = top.values.cpu().numpy()
            actions[i:j, :a.shape[1]] = np.where(ok, a, -1)
            probs[i:j, :a.shape[1]] = np.where(ok, pr, 0.0)
        return actions.reshape(n, 2, m), probs.reshape(n, 2, m)

    @torch.no_grad()
    def _values(self, forest, n: int) -> np.ndarray:
        s = SIZES
        ints = np.empty((n, 2, s["tokens"], s["int_fields"]), np.int32)
        mons = np.empty((n, 2, s["tokens"], s["mon_floats"]), np.float32)
        fld = np.empty((n, 2, s["field_floats"]), np.float32)
        forest.leaf_inputs(ints, mons, fld)
        out = np.empty(n, np.float32)
        for i in range(0, n, self.cfg.leaf_batch):
            j = min(n, i + self.cfg.leaf_batch)
            v = self.model.value(*(self._t(a[i:j].reshape(-1, *a.shape[2:])) for a in (ints, mons, fld)))
            v = v.view(-1, 2)
            # Side 0's value: its own estimate and the negation of side 1's.
            out[i:j] = ((v[:, 0] - v[:, 1]) / 2).cpu().numpy()
        return out

    def search(self, env: SelfPlayEnv, games, noise: bool = False) -> list[dict]:
        """Search the current positions of `games`. Per game: candidates,
        priors, matrix, row, col (equilibrium mixes), value, gap, visits,
        alternatives, and tree statistics."""
        t0 = time.perf_counter()
        c = self.cfg
        forest = env._env.mcts(list(map(int, games)), seed=int(self.rng.integers(1 << 62)),
                               **c.engine_kwargs())
        first = True
        waves = 0
        while waves < 100_000:
            waves += 1
            n = forest.select(c.budget)
            if n:
                actions, probs = self._priors(forest, n, noise and first)
                forest.set_policy(actions, probs, c.prior_top)
            first = False
            m = forest.expand()
            if m:
                forest.set_values(self._values(forest, m))
            if n == 0 and m == 0:
                break
        out = forest.results()
        s = self.stats
        for r in out:
            add_alternatives(r)
            s["leaves"] += r["leaf_evals"]
            s["nodes"] += r["nodes"]
            s["depth"] += r["max_depth"]
            s["exact"] += int(r["exact"])
            s["gap"] += r["gap"]
        s["roots"] += len(out)
        s["waves"] += waves
        s["seconds"] += time.perf_counter() - t0
        return out

    # The same interface as selfplay.search.Search.
    def run(self, env: SelfPlayEnv, games, noise: bool = False) -> list[dict]:
        return self.search(env, games, noise)

    def pick(self, result: dict, side: int) -> int:
        mix = np.asarray(result["row" if side == 0 else "col"], np.float64)
        cand = result["candidates"][side]
        mix = np.clip(mix, 0, None)
        if mix.sum() <= 0:
            mix = np.ones(len(cand))
        j = self.rng.choice(len(mix), p=mix / mix.sum()) if self.cfg.sample else int(mix.argmax())
        return cand[j]

    def act(self, env: SelfPlayEnv, games, side: int) -> np.ndarray:
        return np.array([self.pick(r, side) for r in self.run(env, games)], np.int64)


def add_alternatives(r: dict) -> None:
    """Each candidate's loss against the opponent's equilibrium mix (0 for
    the actions in the mix's support), best first."""
    rows, cols = len(r["candidates"][0]), len(r["candidates"][1])
    if not rows or not cols:
        r["alternatives"] = ([], [])
        return
    m = np.asarray(r["matrix"], np.float64).reshape(rows, cols)
    x, y, v = np.asarray(r["row"]), np.asarray(r["col"]), r["value"]
    u0, u1 = m @ y, x @ m
    r["alternatives"] = (
        sorted(((a, float(v - q)) for a, q in zip(r["candidates"][0], u0)), key=lambda t: t[1]),
        sorted(((b, float(q - v)) for b, q in zip(r["candidates"][1], u1)), key=lambda t: t[1]),
    )


def config_from(args, prefix: str = "tree_") -> TreeConfig:
    return TreeConfig(**{f.name: getattr(args, prefix + f.name) for f in fields(TreeConfig)})


def config_args(p, prefix: str = "tree-") -> None:
    for f in fields(TreeConfig):
        name, v = prefix + f.name.replace("_", "-"), getattr(TreeConfig(), f.name)
        dest = prefix.replace("-", "_") + f.name
        if isinstance(v, bool):
            p.add_argument(f"--{prefix}no-{f.name.replace('_', '-')}" if v else f"--{name}", dest=dest,
                           action="store_false" if v else "store_true")
        else:
            p.add_argument(f"--{name}", dest=dest, type=type(v), default=v)
