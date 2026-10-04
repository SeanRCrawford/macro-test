"""One-turn matrix search (DESIGN.md 4.14), the way mikumiku37 and Nessie
play a turn.

The turn is a simultaneous-move game. Each side's policy (on its own view)
proposes its top-k joint actions; every pair is played through its chance
outcomes by the engine (engine/src/search.rs); the value network scores the
positions they lead to; and the k x k payoff matrix is solved for both
sides' equilibrium mixes. The searching side plays its mix.

    python -m selfplay.search runs/tiny/model.pt --games 400 --k 8
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

import numpy as np
import torch

from selfplay.env import DECISION_SLOTS, SelfPlayEnv


@dataclass
class SearchConfig:
    k: int = 8                  # candidate joint actions per side
    max_outcomes: int = 16      # chance outcomes per cell (the rest renormalised away)
    roll_bands: int = 1         # damage-roll bands besides the KO split
    iters: int = 1000           # regret-matching iterations
    leaf_batch: int = 8192      # leaves per network call
    sample: bool = True         # play a sample of the mix (else its most likely action)


class OneTurnSearch:
    def __init__(self, model, cfg: SearchConfig = SearchConfig(), seed: int = 0):
        self.model, self.cfg = model, cfg
        self.dev = next(model.parameters()).device
        self.rng = np.random.default_rng(seed)
        self.stats = {"roots": 0, "leaves": 0, "gap": 0.0, "seconds": 0.0}

    def _t(self, a):
        return torch.from_numpy(np.ascontiguousarray(a)).to(self.dev)

    @torch.no_grad()
    def candidates(self, obs, games) -> np.ndarray:
        """[len(games), 2, k] int64: each side's k most probable legal joint
        actions under its own view (-1 padding, or nothing to decide)."""
        k = self.cfg.k
        rows = (np.asarray(games)[:, None] * 2 + np.arange(2)).reshape(-1)
        flat = lambda a: a.reshape(-1, *a.shape[2:])[rows]
        logp, _ = self.model(*(self._t(flat(a)) for a in
                               (obs.ints, obs.mons, obs.field, obs.masks, obs.decisions)))
        legal = self._t(flat(obs.masks)).bool()
        top = logp.masked_fill(~legal, -float("inf")).topk(k, -1)
        out = top.indices.cpu().numpy().astype(np.int64)
        out[~torch.isfinite(top.values).cpu().numpy()] = -1
        out[flat(obs.decisions) != DECISION_SLOTS] = -1
        return out.reshape(len(games), 2, k)

    @torch.no_grad()
    def leaf_values(self, leaves) -> np.ndarray:
        """Each leaf's value for side 0: the mean of side 0's estimate and
        the negation of side 1's."""
        n = len(leaves.probs)
        out = np.empty(n, np.float32)
        for i in range(0, n, self.cfg.leaf_batch):
            j = min(n, i + self.cfg.leaf_batch)
            v = self.model.value(*(self._t(a[i:j].reshape(-1, *a.shape[2:]))
                                   for a in (leaves.ints, leaves.mons, leaves.field)))
            v = v.view(-1, 2)
            out[i:j] = ((v[:, 0] - v[:, 1]) / 2).cpu().numpy()
        return out

    def run(self, env: SelfPlayEnv, obs, games) -> list[dict]:
        """Search each of `games` (each must have a moves/switches decision
        for at least one side). Per game: candidates, matrix, row, col,
        value, gap (see SelfPlayEnv.search_solve)."""
        t0 = time.perf_counter()
        c = self.cfg
        cands = self.candidates(obs, games)
        n = env.search_expand(games, cands, c.max_outcomes, c.roll_bands,
                              int(self.rng.integers(1 << 62)))
        leaves = env.search_leaves(n)
        out = env.search_solve(self.leaf_values(leaves), c.iters)
        s = self.stats
        s["roots"] += len(games)
        s["leaves"] += n
        s["gap"] += sum(r["gap"] for r in out)
        s["seconds"] += time.perf_counter() - t0
        return out

    def act(self, env: SelfPlayEnv, obs, games, side: int) -> np.ndarray:
        """`side`'s action in each of `games`, from its equilibrium mix."""
        results = self.run(env, obs, games)
        acts = np.empty(len(games), np.int64)
        for i, r in enumerate(results):
            mix = np.asarray(r["row" if side == 0 else "col"], np.float64)
            cand = r["candidates"][side]
            if self.cfg.sample:
                j = self.rng.choice(len(mix), p=mix / mix.sum())
            else:
                j = int(mix.argmax())
            acts[i] = cand[j]
        return acts


@torch.no_grad()
def play_vs_policy(model, games: int, cfg: SearchConfig, seed: int = 0, envs: int = 32,
                   perfect_info: bool = True, sampled_policy: bool = False) -> dict:
    """Side 0 plays the one-turn search, side 1 the raw policy (most likely
    action, or a sample). Team preview is the policy's on both sides."""
    from selfplay.train import act, to_tensors
    env = SelfPlayEnv(min(envs, games), seed=seed, perfect_info=perfect_info)
    search = OneTurnSearch(model, cfg, seed)
    model.eval()
    done = score = 0.0
    while done < games:
        obs = env.observe()
        actions = np.full((env.num_envs, 2), -1, np.int64)
        dec = obs.decisions
        rows = np.flatnonzero(dec.reshape(-1) != 0)
        if len(rows):
            a, _, _ = act(model, to_tensors(obs, rows), greedy=not sampled_policy)
            actions.reshape(-1)[rows] = a.numpy()
        games_s = np.flatnonzero(dec[:, 0] == DECISION_SLOTS)
        if len(games_s):
            actions[games_s, 0] = search.act(env, obs, games_s, side=0)
        r = env.step(actions)
        fin = r.done.astype(bool)
        done += fin.sum()
        score += ((r.reward[fin, 0] + 1) / 2).sum()
    s = search.stats
    p = score / done
    return {"score": float(p), "ci95": float(1.96 * np.sqrt(p * (1 - p) / done)), "games": int(done),
            "roots": s["roots"], "leaves_per_root": s["leaves"] / max(1, s["roots"]),
            "mean_gap": s["gap"] / max(1, s["roots"]),
            "ms_per_root": 1000 * s["seconds"] / max(1, s["roots"])}


def main():
    from selfplay.model import PolicyNet
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--games", type=int, default=400)
    p.add_argument("--envs", type=int, default=32)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default="cpu")
    p.add_argument("--sampled-policy", action="store_true", help="the policy side samples instead of argmax")
    for k, v in vars(SearchConfig()).items():
        if isinstance(v, bool):
            p.add_argument(f"--no-{k.replace('_', '-')}", dest=k, action="store_false")
        else:
            p.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = p.parse_args()
    ckpt = torch.load(a.model, map_location=a.device)
    conf = ckpt.get("config", {})
    model = PolicyNet(conf.get("d", 64), conf.get("layers", 2)).to(a.device)
    model.load_state_dict(ckpt["model"])
    cfg = SearchConfig(**{k: getattr(a, k) for k in vars(SearchConfig())})
    res = play_vs_policy(model, a.games, cfg, a.seed, a.envs, conf.get("perfect_info", True),
                         a.sampled_policy)
    print(" ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in res.items()))


if __name__ == "__main__":
    main()
