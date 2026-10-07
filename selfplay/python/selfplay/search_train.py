"""Search-labelled self-play (DESIGN.md 4.14 step 4 and 4.16, Nessie's
recipe with AlphaZero-style tree search).

Both sides play the search's equilibrium mix every turn: the one-turn
search, or with `--tree` the tree search (selfplay.mcts). With
`--fast-budget`, most turns get a cheap search that only moves the game on,
and a random `--full-frac` of them a full one that becomes training data
(KataGo's playout cap randomisation). Each fully searched turn becomes a
training example for both sides:

- policy target: the side's equilibrium mix over its candidates;
- opponent target: the opponent's equilibrium mix (the opponent head);
- value target: a blend of the search's value and the game's result.

The network trains on a replay buffer of recent examples between rounds of
games. Team preview is the policy's own (sampled) and isn't trained here.

    python -m selfplay.search_train --init runs/tiny/model.pt --minutes 60 --out runs/search
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch

from selfplay.env import DECISION_SLOTS, SIZES, SelfPlayEnv
from selfplay.model import PolicyNet
from selfplay.mcts import TreeConfig, TreeSearch
from selfplay.search import Search, SearchConfig, play_vs_policy
from selfplay.train import act, to_tensors

MASK = SIZES["mask_len"]


@dataclass
class Config:
    envs: int = 64
    turns: int = 32                 # env steps per round of games
    buffer: int = 200_000           # examples kept (one per side per searched turn)
    epochs: int = 2                 # passes over the newest round's worth of samples
    minibatch: int = 512
    lr: float = 3e-4
    result_weight: float = 0.5      # value target: this much game result, the rest search value
    value_coef: float = 1.0
    opponent_coef: float = 0.5
    k: int = 8
    max_outcomes: int = 16
    roll_bands: int = 1
    double_oracle: bool = False
    deepen: int = 0                 # leaves deepened per searched turn
    tree: bool = False              # the tree search instead of the one-turn search
    tree_budget: int = 800          # value evaluations per fully searched turn
    fast_budget: int = 0            # >0: other turns get this cheap search, not trained on
    full_frac: float = 0.25         # share of turns fully searched (with fast_budget)
    root_noise: float = 0.0         # Dirichlet noise share at the roots of full searches
    prior_root: bool = False        # widen full searches' roots by the prior, not the double oracle
    eval_every: int = 10            # rounds
    eval_games: int = 200
    d: int = 64
    layers: int = 2
    seed: int = 0
    threads: int = 0
    perfect_info: bool = True
    device: str = "cpu"


class Buffer:
    """Examples as numpy arrays, ring-overwritten once full. Masks are
    bit-packed and targets stored sparsely (candidates and their mix)."""

    def __init__(self, size: int, k: int):
        s = SIZES
        packed = (MASK + 7) // 8
        self.size, self.k, self.n, self.pos = size, k, 0, 0
        self.ints = np.zeros((size, s["tokens"], s["int_fields"]), np.int32)
        self.mons = np.zeros((size, s["tokens"], s["mon_floats"]), np.float32)
        self.field = np.zeros((size, s["field_floats"]), np.float32)
        self.masks = np.zeros((size, packed), np.uint8)
        self.opp_masks = np.zeros((size, packed), np.uint8)
        self.dec = np.zeros(size, np.uint8)
        self.cand = np.full((size, k), -1, np.int16)       # target mix (all -1: no target)
        self.mix = np.zeros((size, k), np.float32)
        self.opp_cand = np.full((size, k), -1, np.int16)
        self.opp_mix = np.zeros((size, k), np.float32)
        self.value = np.zeros(size, np.float32)

    def add(self, **cols) -> np.ndarray:
        n = len(cols["value"])
        idx = (self.pos + np.arange(n)) % self.size
        for key in ("masks", "opp_masks"):
            cols[key] = np.packbits(cols[key], axis=1)
        for key, v in cols.items():
            getattr(self, key)[idx] = v
        self.pos = (self.pos + n) % self.size
        self.n = min(self.size, self.n + n)
        return idx

    def masks_of(self, name: str, idx) -> np.ndarray:
        return np.unpackbits(getattr(self, name)[idx], axis=1, count=MASK)


def dense(cand: torch.Tensor, mix: torch.Tensor) -> torch.Tensor:
    """[B, mask_len] target distributions from candidates and their mix."""
    out = torch.zeros(len(cand), MASK, device=mix.device)
    valid = cand >= 0
    return out.scatter_add_(1, cand.clamp(min=0).long(), mix * valid)


class SearchTrainer:
    def __init__(self, cfg: Config, out: Path, init: str | None = None):
        self.cfg, self.out = cfg, out
        out.mkdir(parents=True, exist_ok=True)
        torch.manual_seed(cfg.seed)
        self.rng = np.random.default_rng(cfg.seed)
        self.model = PolicyNet(cfg.d, cfg.layers).to(cfg.device)
        if init:
            self.model.load_state_dict(torch.load(init, map_location=cfg.device)["model"])
        self.opt = torch.optim.Adam(self.model.parameters(), lr=cfg.lr)
        self.env = SelfPlayEnv(cfg.envs, seed=cfg.seed, threads=cfg.threads,
                               perfect_info=cfg.perfect_info)
        self.search_cfg = SearchConfig(k=cfg.k, max_outcomes=cfg.max_outcomes,
                                       roll_bands=cfg.roll_bands, double_oracle=cfg.double_oracle,
                                       deepen=cfg.deepen)
        if cfg.tree:
            self.search_cfg = TreeConfig(budget=cfg.tree_budget, max_outcomes=cfg.max_outcomes,
                                         roll_bands=cfg.roll_bands, root_noise=cfg.root_noise,
                                         root_oracle=not cfg.prior_root)
            self.search = TreeSearch(self.model, self.search_cfg, cfg.seed)
            self.fast = (TreeSearch(self.model, TreeConfig(budget=cfg.fast_budget,
                                                           max_outcomes=cfg.max_outcomes,
                                                           roll_bands=cfg.roll_bands,
                                                           root_oracle=False), cfg.seed + 1)
                         if cfg.fast_budget > 0 else None)
            self.width = self.search_cfg.max_candidates
        else:
            self.search = Search(self.model, self.search_cfg, cfg.seed)
            self.fast = None
            self.width = cfg.k
        self.buffer = Buffer(cfg.buffer, self.width)
        # Per game: buffer indices of its examples so far, and their sides.
        self.pending: list[list[tuple[int, int]]] = [[] for _ in range(cfg.envs)]
        self.rounds = self.games = self.examples = 0
        self.log = open(out / "log.jsonl", "a")

    def play(self) -> int:
        """One round of games; returns the examples added."""
        c, env, buf = self.cfg, self.env, self.buffer
        added = 0
        self.model.eval()
        for _ in range(c.turns):
            obs = env.observe()
            dec = obs.decisions
            actions = np.full((c.envs, 2), -1, np.int64)
            # Team preview (and anything not searched): the policy, sampled.
            rows = np.flatnonzero(dec.reshape(-1) == 1)
            if len(rows):
                a, _, _ = act(self.model, to_tensors(obs, rows))
                actions.reshape(-1)[rows] = a.numpy()
            games = np.flatnonzero((dec == DECISION_SLOTS).any(1))
            if len(games) and self.fast is not None:
                # Playout cap randomisation: the cheap search only moves on.
                full = self.rng.random(len(games)) < c.full_frac
                quick = games[~full]
                if len(quick):
                    for g, r in zip(quick, self.fast.run(env, quick)):
                        for side in (0, 1):
                            if r["candidates"][side][0] >= 0:
                                actions[g, side] = self.fast.pick(r, side)
                games = games[full]
            if len(games):
                results = (self.search.run(env, games, noise=True) if c.tree
                           else self.search.run(env, games))
                n, k = len(games), self.width
                cands = np.full((n, 2, k), -1, np.int16)
                mixes = np.zeros((n, 2, k), np.float32)
                val = np.zeros((n, 2), np.float32)
                for i, (g, r) in enumerate(zip(games, results)):
                    for side, key in ((0, "row"), (1, "col")):
                        cand = np.asarray(r["candidates"][side])
                        mix = np.asarray(r[key], np.float64)
                        if cand[0] >= 0:
                            cands[i, side, :len(cand)] = cand
                            mixes[i, side, :len(mix)] = mix
                            j = self.rng.choice(len(mix), p=mix / mix.sum())
                            actions[g, side] = cand[j]
                    val[i] = (r["value"], -r["value"])
                flat = lambda a: a[games].reshape(-1, *a.shape[2:])
                masks = flat(obs.masks)
                opp_masks = obs.masks[games][:, ::-1].reshape(-1, MASK)
                idx = buf.add(ints=flat(obs.ints), mons=flat(obs.mons), field=flat(obs.field),
                              masks=masks, opp_masks=opp_masks, dec=flat(dec),
                              cand=cands.reshape(-1, k), mix=mixes.reshape(-1, k),
                              opp_cand=cands[:, ::-1].reshape(-1, k),
                              opp_mix=mixes[:, ::-1].reshape(-1, k),
                              value=val.reshape(-1))
                for i, g in enumerate(games):
                    self.pending[g] += [(int(idx[2 * i]), 0), (int(idx[2 * i + 1]), 1)]
                added += len(idx)
            r = env.step(actions)
            for g in np.flatnonzero(r.done):
                w = c.result_weight
                for k, side in self.pending[g]:
                    buf.value[k] = (1 - w) * buf.value[k] + w * float(r.reward[g, side])
                self.pending[g] = []
                self.games += 1
        self.model.train()
        return added

    def update(self, new: int) -> dict:
        c, buf, dev = self.cfg, self.buffer, self.cfg.device
        steps = max(1, c.epochs * new // c.minibatch)
        stats = []
        for _ in range(steps):
            idx = self.rng.integers(0, buf.n, c.minibatch)
            t = lambda a, dtype=None: torch.from_numpy(a[idx]).to(dev, dtype)
            masks = torch.from_numpy(buf.masks_of("masks", idx)).to(dev)
            opp_masks = torch.from_numpy(buf.masks_of("opp_masks", idx)).to(dev)
            logp, value, opp_logp = self.model(t(buf.ints), t(buf.mons), t(buf.field), masks,
                                               t(buf.dec), opp_masks)
            pol = dense(t(buf.cand), t(buf.mix))
            opp = dense(t(buf.opp_cand), t(buf.opp_mix))
            has, has_opp = pol.sum(-1) > 0.5, opp.sum(-1) > 0.5
            ce = -(pol * logp.clamp(min=-1e4)).sum(-1)
            ce = ce[has].mean() if has.any() else torch.zeros((), device=dev)
            oce = -(opp * opp_logp.clamp(min=-1e4)).sum(-1)
            oce = oce[has_opp].mean() if has_opp.any() else torch.zeros((), device=dev)
            vloss = ((value - t(buf.value)) ** 2).mean()
            loss = ce + c.value_coef * vloss + c.opponent_coef * oce
            self.opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            self.opt.step()
            stats.append((ce.item(), vloss.item(), oce.item()))
        ce, vl, oce = np.mean(stats, axis=0)
        return {"policy_ce": ce, "value_loss": vl, "opponent_ce": oce, "steps": steps}

    def run(self, minutes: float, max_rounds: int | None = None):
        c = self.cfg
        start = time.time()
        while time.time() - start < minutes * 60 and (max_rounds is None or self.rounds < max_rounds):
            searches = [x for x in (self.search, getattr(self, "fast", None)) if x is not None]
            before = [(x.stats["seconds"], x.stats.get("net_seconds", 0.0)) for x in searches]
            t0 = time.time()
            new = self.play()
            t1 = time.time()
            search_s = sum(x.stats["seconds"] - b[0] for x, b in zip(searches, before))
            net_s = sum(x.stats.get("net_seconds", 0.0) - b[1] for x, b in zip(searches, before))
            self.examples += new
            entry = {"round": self.rounds + 1, "minutes": (time.time() - start) / 60,
                     "games": self.games, "examples": self.examples, "new": new,
                     "play_s": t1 - t0, "search_s": search_s, "net_s": net_s,
                     **self.update(new), "update_s": time.time() - t1,
                     "mean_gap": self.search.stats["gap"] / max(1, self.search.stats["roots"])}
            self.rounds += 1
            if self.rounds % c.eval_every == 0:
                entry.update({f"search_vs_policy_{k}": v for k, v in play_vs_policy(
                    self.model, c.eval_games, self.search_cfg, 5000 + self.rounds,
                    perfect_info=c.perfect_info).items() if k in ("score", "ci95")})
                self.save()
            self.log.write(json.dumps(entry) + "\n")
            self.log.flush()
            print(" ".join(f"{k}={v:.3g}" if isinstance(v, float) else f"{k}={v}"
                           for k, v in entry.items()), flush=True)
        self.save()

    def save(self):
        torch.save({"model": self.model.state_dict(), "config": asdict(self.cfg)},
                   self.out / "model.pt")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--minutes", type=float, default=60)
    p.add_argument("--rounds", type=int, default=None)
    p.add_argument("--out", default="runs/search")
    p.add_argument("--init", default=None, help="start from this model.pt (e.g. a PPO run)")
    for k, v in asdict(Config()).items():
        if k == "perfect_info":
            p.add_argument("--hidden", action="store_true", help="Open Team Sheets observations")
        elif isinstance(v, bool):
            p.add_argument(f"--{k.replace('_', '-')}", action="store_true")
        else:
            p.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = p.parse_args()
    if a.init:
        # The model's size comes from the checkpoint.
        saved = torch.load(a.init, map_location="cpu").get("config", {})
        a.d, a.layers = saved.get("d", a.d), saved.get("layers", a.layers)
    cfg = Config(**{k: getattr(a, k) for k in asdict(Config()) if k != "perfect_info"},
                 perfect_info=not a.hidden)
    SearchTrainer(cfg, Path(a.out), a.init).run(a.minutes, a.rounds)


if __name__ == "__main__":
    main()
