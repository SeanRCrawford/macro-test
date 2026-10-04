"""PPO self-play with a league of past versions (DESIGN.md 3, phase 3).

Every game is played by the current policy on side 0. Side 1 is the current
policy too (self-play), or, for a share of games, a frozen past version or
the greedy-damage baseline from the league. Only the current policy's
decisions are trained on. Rewards: +1 win, -1 loss, 0 draw, at the end.

    python -m selfplay.train --minutes 30 --out runs/tiny
"""
from __future__ import annotations

import argparse
import copy
import json
import random
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import torch

from selfplay.env import SelfPlayEnv
from selfplay.model import PolicyNet


@dataclass
class Config:
    envs: int = 256
    steps: int = 64                 # env steps per rollout
    epochs: int = 3
    minibatch: int = 4096
    lr: float = 3e-4
    gamma: float = 1.0
    lam: float = 0.95
    clip: float = 0.2
    entropy: float = 0.01
    value_coef: float = 0.5
    max_grad_norm: float = 1.0
    league_every: int = 10          # updates between snapshots
    league_size: int = 8
    league_frac: float = 0.3        # games whose side 1 is a league member
    greedy_frac: float = 0.15       # of those, how many use the greedy baseline
    eval_every: int = 10
    eval_games: int = 400
    d: int = 64
    layers: int = 2
    seed: int = 0
    threads: int = 0
    perfect_info: bool = True


def to_tensors(obs, rows=None):
    """The observation arrays for (game, side) rows, flattened to a batch."""
    def flat(a):
        a = a.reshape(-1, *a.shape[2:])
        return torch.from_numpy(a if rows is None else a[rows])
    return (flat(obs.ints), flat(obs.mons), flat(obs.field), flat(obs.masks), flat(obs.decisions))


@torch.no_grad()
def act(model, batch, greedy=False):
    logp, value = model(*batch)
    if greedy:
        a = logp.argmax(-1)
    else:
        a = torch.distributions.Categorical(logits=logp).sample()
    return a, logp.gather(1, a[:, None]).squeeze(1), value


@torch.no_grad()
def evaluate(model, opponent: str, games: int, seed: int, perfect_info: bool = True) -> float:
    """Score (win 1, draw 0.5) of the model, playing deterministically on
    side 0, against 'random' or 'greedy' on side 1."""
    env = SelfPlayEnv(min(128, games), seed=seed, perfect_info=perfect_info)
    model.eval()
    done = score = 0.0
    step = 0
    while done < games:
        obs = env.observe()
        if opponent == "greedy":
            actions = env.greedy_actions()
        else:
            actions = env.random_actions(seed * 100_003 + step)
        rows = np.flatnonzero(obs.decisions[:, 0] != 0) * 2        # side 0 rows
        if len(rows):
            a, _, _ = act(model, to_tensors(obs, rows), greedy=True)
            actions[rows // 2, 0] = a.numpy()
        r = env.step(actions)
        fin = r.done.astype(bool)
        done += fin.sum()
        score += ((r.reward[fin, 0] + 1) / 2).sum()
        step += 1
    model.train()
    return float(score / done)


class Trainer:
    def __init__(self, cfg: Config, out: Path):
        self.cfg, self.out = cfg, out
        out.mkdir(parents=True, exist_ok=True)
        torch.manual_seed(cfg.seed)
        self.rng = random.Random(cfg.seed)
        self.model = PolicyNet(cfg.d, cfg.layers)
        self.opt = torch.optim.Adam(self.model.parameters(), lr=cfg.lr)
        self.env = SelfPlayEnv(cfg.envs, seed=cfg.seed, threads=cfg.threads,
                               perfect_info=cfg.perfect_info)
        self.league: list[PolicyNet] = []
        # Per game: None (self-play), "greedy", or a league index.
        self.opponent: list = [None] * cfg.envs
        self.updates = self.games = self.decisions = 0
        self.log = open(out / "log.jsonl", "a")

    def pick_opponent(self):
        c = self.cfg
        if self.rng.random() >= c.league_frac:
            return None
        if not self.league or self.rng.random() < c.greedy_frac:
            return "greedy"
        return self.rng.randrange(len(self.league))

    def rollout(self):
        c, env, n = self.cfg, self.env, self.cfg.envs
        buf = {k: [] for k in ("ints", "mons", "field", "masks", "dec", "act", "logp", "value",
                               "stream")}
        rewards: list[float] = []
        dones: list[bool] = []
        last = {}                                   # stream -> index of its last transition
        results = []
        for _ in range(c.steps):
            obs = env.observe()
            actions = np.full((n, 2), -1, np.int64)
            dec = obs.decisions
            learner = dec != 0
            opp = np.array([o is not None for o in self.opponent])
            learner[:, 1] &= ~opp
            rows = np.flatnonzero(learner.reshape(-1))
            if len(rows):
                batch = to_tensors(obs, rows)
                a, logp, value = act(self.model, batch)
                actions.reshape(-1)[rows] = a.numpy()
                for k, t in zip(("ints", "mons", "field", "masks", "dec"), batch):
                    buf[k].append(t)
                buf["act"].append(a)
                buf["logp"].append(logp)
                buf["value"].append(value)
                base = len(rewards)
                for i, r in enumerate(rows):
                    last[int(r)] = base + i
                buf["stream"].append(torch.from_numpy(rows))
                rewards.extend([0.0] * len(rows))
                dones.extend([False] * len(rows))
            # League opponents on side 1.
            opp_rows = np.flatnonzero((dec[:, 1] != 0) & opp)
            if len(opp_rows):
                greedy = None
                for g in opp_rows:
                    o = self.opponent[g]
                    if o == "greedy":
                        if greedy is None:
                            greedy = env.greedy_actions()
                        actions[g, 1] = greedy[g, 1]
                by = {}
                for g in opp_rows:
                    if self.opponent[g] != "greedy":
                        by.setdefault(self.opponent[g], []).append(g * 2 + 1)
                for idx, rs in by.items():
                    a, _, _ = act(self.league[idx], to_tensors(obs, np.array(rs)))
                    actions.reshape(-1)[rs] = a.numpy()
            r = env.step(actions)
            for g in np.flatnonzero(r.done):
                results.append((self.opponent[g], float(r.reward[g, 0]), int(r.turns[g])))
                for side in (0, 1):
                    s = int(g * 2 + side)
                    if s in last:
                        rewards[last[s]] += float(r.reward[g, side])
                        dones[last[s]] = True
                        del last[s]
                self.opponent[g] = self.pick_opponent()
            self.decisions += len(rows)
        # Bootstrap: each unfinished stream's value now.
        obs = env.observe()
        _, _, boot = act(self.model, to_tensors(obs))
        data = {k: torch.cat(v) for k, v in buf.items()}
        data["reward"] = torch.tensor(rewards)
        data["done"] = torch.tensor(dones)
        data["adv"], data["ret"] = self.gae(data, boot)
        self.games += len(results)
        return data, results

    def gae(self, d, boot):
        c = self.cfg
        n = len(d["reward"])
        adv = torch.zeros(n)
        nv = boot.clone()                           # next value per stream
        na = torch.zeros_like(boot)                 # next advantage per stream
        stream, value, reward, done = d["stream"], d["value"], d["reward"], d["done"]
        for i in range(n - 1, -1, -1):
            s = stream[i]
            if done[i]:
                nv[s], na[s] = 0.0, 0.0
            delta = reward[i] + c.gamma * nv[s] - value[i]
            a = delta + c.gamma * c.lam * na[s]
            adv[i] = a
            nv[s], na[s] = value[i], a
        return adv, adv + value

    def update(self, d):
        c = self.cfg
        n = len(d["act"])
        adv = (d["adv"] - d["adv"].mean()) / (d["adv"].std() + 1e-8)
        stats = []
        for _ in range(c.epochs):
            perm = torch.randperm(n)
            for i in range(0, n, c.minibatch):
                idx = perm[i:i + c.minibatch]
                logp_all, value = self.model(d["ints"][idx], d["mons"][idx], d["field"][idx],
                                             d["masks"][idx], d["dec"][idx])
                logp = logp_all.gather(1, d["act"][idx, None]).squeeze(1)
                ratio = torch.exp(logp - d["logp"][idx])
                a = adv[idx]
                pg = -torch.min(ratio * a, ratio.clamp(1 - c.clip, 1 + c.clip) * a).mean()
                vloss = ((value - d["ret"][idx]) ** 2).mean()
                p = logp_all.exp()
                legal = d["masks"][idx].bool()
                ent = -(p * logp_all).masked_fill(~legal, 0).sum(-1).mean()
                loss = pg + c.value_coef * vloss - c.entropy * ent
                self.opt.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), c.max_grad_norm)
                self.opt.step()
                stats.append((pg.item(), vloss.item(), ent.item()))
        return np.mean(stats, axis=0)

    def imitate(self, minutes: float):
        """Warm start: learn the greedy-damage baseline's choices (greedy
        against greedy, both sides) by cross-entropy, before PPO takes over."""
        c, env = self.cfg, self.env
        start = time.time()
        rounds = 0
        while time.time() - start < minutes * 60:
            batches = []
            for _ in range(c.steps):
                obs = env.observe()
                actions = env.greedy_actions()
                rows = np.flatnonzero(obs.decisions.reshape(-1) != 0)
                if len(rows):
                    batches.append((to_tensors(obs, rows), torch.from_numpy(actions.reshape(-1)[rows])))
                env.step(actions)
            ints, mons, field, masks, dec = (torch.cat([b[0][k] for b in batches]) for k in range(5))
            acts = torch.cat([b[1] for b in batches])
            total = len(acts)
            for _ in range(c.epochs):
                perm = torch.randperm(total)
                for i in range(0, total, c.minibatch):
                    idx = perm[i:i + c.minibatch]
                    logp, _ = self.model(ints[idx], mons[idx], field[idx], masks[idx], dec[idx])
                    loss = -logp.gather(1, acts[idx, None]).mean()
                    self.opt.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), c.max_grad_norm)
                    self.opt.step()
            rounds += 1
            with torch.no_grad():
                idx = torch.randperm(total)[:2048]
                logp, _ = self.model(ints[idx], mons[idx], field[idx], masks[idx], dec[idx])
                agree = (logp.argmax(-1) == acts[idx]).float().mean().item()
            print(f"imitate round={rounds} minutes={(time.time() - start) / 60:.2f} samples={total} "
                  f"loss={loss.item():.3f} agreement={agree:.3f}", flush=True)
        entry = {"imitation_minutes": minutes, "rounds": rounds,
                 "vs_random": evaluate(self.model, "random", c.eval_games, 3000, c.perfect_info),
                 "vs_greedy": evaluate(self.model, "greedy", c.eval_games, 3001, c.perfect_info)}
        print(" ".join(f"{k}={v}" for k, v in entry.items()), flush=True)
        self.log.write(json.dumps(entry) + "\n")

    def run(self, minutes: float, max_updates: int | None = None):
        c = self.cfg
        start = time.time()
        while time.time() - start < minutes * 60 and (max_updates is None or self.updates < max_updates):
            t0 = time.time()
            data, results = self.rollout()
            t1 = time.time()
            pg, vl, ent = self.update(data)
            self.updates += 1
            vs_self = [r for o, r, _ in results if o is None]
            entry = {"update": self.updates, "minutes": (time.time() - start) / 60,
                     "games": self.games, "decisions": self.decisions,
                     "samples": len(data["act"]), "pg": pg, "value_loss": vl, "entropy": ent,
                     "rollout_s": t1 - t0, "update_s": time.time() - t1,
                     "mean_turns": float(np.mean([t for *_, t in results])) if results else 0.0,
                     "self_draws": float(np.mean([r == 0 for r in vs_self])) if vs_self else 0.0}
            if self.updates % c.league_every == 0:
                self.league.append(copy.deepcopy(self.model).eval())
                self.league = self.league[-c.league_size:]
            if self.updates % c.eval_every == 0:
                entry["vs_random"] = evaluate(self.model, "random", c.eval_games, 1000 + self.updates,
                                              c.perfect_info)
                entry["vs_greedy"] = evaluate(self.model, "greedy", c.eval_games, 2000 + self.updates,
                                              c.perfect_info)
                torch.save({"model": self.model.state_dict(), "config": asdict(c)},
                           self.out / "model.pt")
            self.log.write(json.dumps(entry) + "\n")
            self.log.flush()
            print(" ".join(f"{k}={v:.3g}" if isinstance(v, float) else f"{k}={v}"
                           for k, v in entry.items()), flush=True)
        torch.save({"model": self.model.state_dict(), "config": asdict(c)}, self.out / "model.pt")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--updates", type=int, default=None)
    p.add_argument("--out", default="runs/tiny")
    p.add_argument("--imitate-minutes", type=float, default=0.0,
                   help="first learn the greedy baseline's choices for this long")
    for k, v in asdict(Config()).items():
        if k == "perfect_info":
            p.add_argument("--hidden", action="store_true", help="Open Team Sheets observations")
        else:
            p.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = p.parse_args()
    cfg = Config(**{k: getattr(a, k) for k in asdict(Config()) if k != "perfect_info"},
                 perfect_info=not a.hidden)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    t = Trainer(cfg, Path(a.out))
    if a.imitate_minutes > 0:
        t.imitate(a.imitate_minutes)
    t.run(a.minutes, a.updates)


if __name__ == "__main__":
    main()
