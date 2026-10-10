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
    opponent_coef: float = 0.5      # auxiliary: predict the opponent's joint action
    uniform_kl: float = 0.003       # zero-avoiding KL(uniform || policy) over legal actions
    max_grad_norm: float = 1.0
    league_every: int = 10          # updates between snapshots
    league_size: int = 8
    league_frac: float = 0.3        # games whose side 1 is a league member
    league_active: int = 2          # members a rollout's new league games draw from (PFSP)
    greedy_frac: float = 0.15       # of those, how many use the greedy baseline
    eval_every: int = 10
    eval_games: int = 400
    d: int = 64
    layers: int = 2
    seed: int = 0
    threads: int = 0
    perfect_info: bool = True
    device: str = "cpu"             # or "cuda"
    amp: bool = True                # CUDA: the transformer body in bfloat16 (heads stay float32)
    amp_heads: bool = True          # the heads in bfloat16 too (log-probabilities stay float32;
                                    # they move ~0.001, measured): 20% faster updates
    profile: bool = False           # profile 10 minibatches of the second update, print the table
    save_every: int = 0             # keep a numbered checkpoint every this many updates (0: none)
    baseline: str = ""              # a model.pt to play head to head at each evaluation (sampled)


def to_tensors(obs, rows=None):
    """The observation arrays for (game, side) rows, flattened to a batch."""
    def flat(a):
        a = a.reshape(-1, *a.shape[2:])
        return torch.from_numpy(a if rows is None else a[rows])
    return (flat(obs.ints), flat(obs.mons), flat(obs.field), flat(obs.masks), flat(obs.decisions))


def autocast(dev, amp: bool):
    """bfloat16 autocast for the network body on CUDA (PolicyNet keeps its
    heads in float32)."""
    dev = torch.device(dev)
    return torch.autocast("cuda", dtype=torch.bfloat16, enabled=amp and dev.type == "cuda")


@torch.no_grad()
def act(model, batch, greedy=False, amp=False):
    dev = next(model.parameters()).device
    with autocast(dev, amp):
        logp, value = model(*(t.to(dev, non_blocking=True) for t in batch))
    if greedy:
        a = logp.argmax(-1)
    else:
        a = torch.distributions.Categorical(logits=logp).sample()
    return a.cpu(), logp.gather(1, a[:, None]).squeeze(1).cpu(), value.cpu()


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
        self.model = PolicyNet(cfg.d, cfg.layers).to(cfg.device)
        self.model.amp_heads = cfg.amp_heads
        self.baseline = None
        self.opt = torch.optim.Adam(self.model.parameters(), lr=cfg.lr)
        self.env = SelfPlayEnv(cfg.envs, seed=cfg.seed, threads=cfg.threads,
                               perfect_info=cfg.perfect_info)
        if torch.device(cfg.device).type == "cuda":
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        # Past versions: id -> model, and the learner's record against each
        # (score, games) for prioritised fictitious self-play.
        self.members: dict[int, PolicyNet] = {}
        self.league: list[int] = []                 # ids of the current league
        self.record: dict[int, list[float]] = {}
        self.active: list[int] = []                 # this rollout's draw for new games
        self.next_id = 0
        # Per game: None (self-play), "greedy", or a league member's id.
        self.opponent: list = [None] * cfg.envs
        self.updates = self.games = self.decisions = 0
        self.log = open(out / "log.jsonl", "a")

    def pfsp_weight(self, m: int) -> float:
        """PFSP: favour the past versions the learner still struggles
        against (AlphaStar's (1 - p)^2 on its win rate p, prior 1/2)."""
        score, games = self.record.get(m, (0.0, 0.0))
        p = (score + 1.0) / (games + 2.0)
        return (1.0 - p) ** 2 + 0.02

    def draw_active(self):
        """The league members this rollout's new league games play."""
        c = self.cfg
        if not self.league:
            self.active = []
            return
        k = min(c.league_active, len(self.league))
        w = np.array([self.pfsp_weight(m) for m in self.league])
        idx = np.random.default_rng(self.rng.randrange(1 << 30)).choice(
            len(self.league), size=k, replace=False, p=w / w.sum())
        self.active = [self.league[i] for i in idx]

    def pick_opponent(self):
        c = self.cfg
        if self.rng.random() >= c.league_frac:
            return None
        if not self.active or self.rng.random() < c.greedy_frac:
            return "greedy"
        return self.rng.choice(self.active)

    def snapshot(self):
        """Add the current model to the league; forget members no longer in
        it once no game still plays them."""
        m = self.next_id
        self.next_id += 1
        self.members[m] = copy.deepcopy(self.model).eval()
        self.league = (self.league + [m])[-self.cfg.league_size:]
        playing = {o for o in self.opponent if isinstance(o, int)}
        for old in list(self.members):
            if old not in self.league and old not in playing:
                del self.members[old]
                self.record.pop(old, None)

    def rollout(self):
        c, env, n = self.cfg, self.env, self.cfg.envs
        dev = torch.device(c.device)
        timing = {"engine_s": 0.0, "net_s": 0.0, "gae_s": 0.0}
        tick = time.perf_counter
        self.draw_active()
        buf = {k: [] for k in ("ints", "mons", "field", "masks", "dec", "act", "logp", "value",
                               "stream", "opp_masks", "opp_act")}
        rewards: list[float] = []
        dones: list[bool] = []
        last = np.full(2 * n, -1, np.int64)        # stream -> index of its last transition
        results = []
        for _ in range(c.steps):
            t = tick()
            obs = env.observe()
            timing["engine_s"] += tick() - t
            actions = np.full((n, 2), -1, np.int64)
            dec = obs.decisions
            learner = dec != 0
            opp = np.array([o is not None for o in self.opponent])
            learner[:, 1] &= ~opp
            rows = np.flatnonzero(learner.reshape(-1))
            t = tick()
            if len(rows):
                batch = tuple(x.to(dev, non_blocking=True) for x in to_tensors(obs, rows))
                a, logp, value = act(self.model, batch, amp=c.amp)
                actions.reshape(-1)[rows] = a.numpy()
                for k, x in zip(("ints", "mons", "field", "masks", "dec"), batch):
                    buf[k].append(x)
                buf["act"].append(a)
                buf["logp"].append(logp)
                buf["value"].append(value)
                base = len(rewards)
                last[rows] = base + np.arange(len(rows))
                buf["stream"].append(torch.from_numpy(rows))
                rewards.extend([0.0] * len(rows))
                dones.extend([False] * len(rows))
            # League opponents on side 1.
            opp_rows = np.flatnonzero((dec[:, 1] != 0) & opp)
            if len(opp_rows):
                greedy_games = [g for g in opp_rows if self.opponent[g] == "greedy"]
                if greedy_games:
                    t2 = tick()
                    greedy = env.greedy_actions()
                    timing["engine_s"] += tick() - t2
                    actions[greedy_games, 1] = greedy[greedy_games, 1]
                by = {}
                for g in opp_rows:
                    if self.opponent[g] != "greedy":
                        by.setdefault(self.opponent[g], []).append(g * 2 + 1)
                for m, rs in by.items():
                    a, _, _ = act(self.members[m], to_tensors(obs, np.array(rs)), amp=c.amp)
                    actions.reshape(-1)[rs] = a.numpy()
            if len(rows):
                # The opponent's actual joint action, where it chose moves.
                opp = rows ^ 1
                opp_act = actions.reshape(-1)[opp].copy()
                opp_act[dec.reshape(-1)[opp] != 2] = -1
                buf["opp_act"].append(torch.from_numpy(opp_act))
                buf["opp_masks"].append(
                    torch.from_numpy(obs.masks.reshape(-1, obs.masks.shape[-1])[opp]).to(dev, non_blocking=True))
            timing["net_s"] += tick() - t
            t = tick()
            r = env.step(actions)
            timing["engine_s"] += tick() - t
            for g in np.flatnonzero(r.done):
                o = self.opponent[g]
                results.append((o, float(r.reward[g, 0]), int(r.turns[g])))
                if isinstance(o, int) and o in self.members:
                    rec = self.record.setdefault(o, [0.0, 0.0])
                    rec[0] += (float(r.reward[g, 0]) + 1) / 2
                    rec[1] += 1
                for side in (0, 1):
                    s_ = g * 2 + side
                    if last[s_] >= 0:
                        rewards[last[s_]] += float(r.reward[g, side])
                        dones[last[s_]] = True
                        last[s_] = -1
                self.opponent[g] = self.pick_opponent()
            self.decisions += len(rows)
        # Bootstrap: each unfinished stream's value now.
        obs = env.observe()
        _, _, boot = act(self.model, to_tensors(obs), amp=c.amp)
        t = tick()
        blocks = [len(x) for x in buf["stream"]]
        data = {k: torch.cat(v) for k, v in buf.items()}
        data["reward"] = torch.tensor(rewards)
        data["done"] = torch.tensor(dones)
        data["adv"], data["ret"] = self.gae(data, boot, blocks)
        timing["gae_s"] = tick() - t
        self.games += len(results)
        return data, results, timing

    def gae(self, d, boot, blocks):
        """Generalised advantage estimation per stream (game side), vectorised
        over each env step's block of transitions (one per stream)."""
        c = self.cfg
        stream = d["stream"].numpy()
        value = d["value"].float().numpy()
        reward = d["reward"].numpy()
        done = d["done"].numpy()
        adv = np.zeros(len(reward), np.float32)
        nv = boot.float().numpy().copy()            # next value per stream
        na = np.zeros_like(nv)                      # next advantage per stream
        end = len(reward)
        for size in reversed(blocks):
            i = slice(end - size, end)
            s, dn = stream[i], done[i]
            nv_s = np.where(dn, 0.0, nv[s])
            na_s = np.where(dn, 0.0, na[s])
            delta = reward[i] + c.gamma * nv_s - value[i]
            a = delta + c.gamma * c.lam * na_s
            adv[i] = a
            nv[s], na[s] = value[i], a
            end -= size
        adv = torch.from_numpy(adv)
        return adv, adv + d["value"].float()

    def update(self, d):
        c = self.cfg
        dev = torch.device(c.device)
        n = len(d["act"])
        # Everything on the device once; minibatches are drawn there.
        d = {k: v.to(dev, non_blocking=True) for k, v in d.items()}
        adv_all = (d["adv"] - d["adv"].mean()) / (d["adv"].std() + 1e-8)
        stats = []
        prof = None
        if c.profile and self.updates == 1:
            acts = [torch.profiler.ProfilerActivity.CPU]
            if dev.type == "cuda":
                acts.append(torch.profiler.ProfilerActivity.CUDA)
            prof = torch.profiler.profile(activities=acts)
            prof.start()
        for _ in range(c.epochs):
            perm = torch.randperm(n, device=dev)
            for i in range(0, n, c.minibatch):
                if prof is not None and len(stats) == 10:
                    self.print_profile(prof, dev)
                    prof = None
                idx = perm[i:i + c.minibatch]
                mb = {k: d[k][idx] for k in ("ints", "mons", "field", "masks", "dec", "act",
                                             "logp", "ret", "opp_masks", "opp_act")}
                with autocast(dev, c.amp):
                    logp_all, value, opp_logp = self.model(mb["ints"], mb["mons"], mb["field"], mb["masks"],
                                                           mb["dec"], mb["opp_masks"])
                logp = logp_all.gather(1, mb["act"][:, None]).squeeze(1)
                ratio = torch.exp(logp - mb["logp"])
                a = adv_all[idx]
                pg = -torch.min(ratio * a, ratio.clamp(1 - c.clip, 1 + c.clip) * a).mean()
                vloss = ((value.float() - mb["ret"]) ** 2).mean()
                p = logp_all.exp()
                legal = mb["masks"].bool()
                ent = -(p * logp_all).masked_fill(~legal, 0).sum(-1).mean()
                # KL(uniform || policy): keeps rare, situational actions alive.
                n_legal = legal.sum(-1).clamp(min=1)
                kl_u = (-logp_all.masked_fill(~legal, 0).sum(-1) / n_legal - n_legal.log()).mean()
                seen = mb["opp_act"] >= 0
                nll = -opp_logp.gather(1, mb["opp_act"].clamp(min=0)[:, None]).squeeze(1)
                opp_ce = (nll * seen).sum() / seen.sum().clamp(min=1)
                loss = (pg + c.value_coef * vloss - c.entropy * ent + c.uniform_kl * kl_u
                        + c.opponent_coef * opp_ce)
                self.opt.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self.model.parameters(), c.max_grad_norm)
                self.opt.step()
                # Kept on the device: one sync per update, not per minibatch.
                stats.append(torch.stack([pg, vloss, ent, opp_ce]).detach())
        if prof is not None:
            self.print_profile(prof, dev)
        return torch.stack(stats).mean(0).tolist()

    @staticmethod
    def print_profile(prof, dev):
        if dev.type == "cuda":
            torch.cuda.synchronize()
        prof.stop()
        key = "self_cuda_time_total" if dev.type == "cuda" else "self_cpu_time_total"
        print(prof.key_averages().table(sort_by=key, row_limit=30), flush=True)

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
                    dev = c.device
                    logp, _ = self.model(ints[idx].to(dev), mons[idx].to(dev), field[idx].to(dev),
                                         masks[idx].to(dev), dec[idx].to(dev))
                    loss = -logp.gather(1, acts[idx, None].to(dev)).mean()
                    self.opt.zero_grad()
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), c.max_grad_norm)
                    self.opt.step()
            rounds += 1
            with torch.no_grad():
                idx = torch.randperm(total)[:2048]
                a, _, _ = act(self.model, (ints[idx], mons[idx], field[idx], masks[idx], dec[idx]), greedy=True)
                agree = (a == acts[idx]).float().mean().item()
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
            data, results, timing = self.rollout()
            t1 = time.time()
            pg, vl, ent, opp_ce = self.update(data)
            self.updates += 1
            vs_self = [r for o, r, _ in results if o is None]
            entry = {"update": self.updates, "minutes": (time.time() - start) / 60,
                     "games": self.games, "decisions": self.decisions,
                     "samples": len(data["act"]), "pg": pg, "value_loss": vl, "entropy": ent,
                     "opponent_ce": opp_ce,
                     "rollout_s": t1 - t0, **timing, "update_s": time.time() - t1,
                     "games_per_s": len(results) / max(time.time() - t0, 1e-9),
                     "mean_turns": float(np.mean([t for *_, t in results])) if results else 0.0,
                     "self_draws": float(np.mean([r == 0 for r in vs_self])) if vs_self else 0.0}
            if self.updates % c.league_every == 0:
                self.snapshot()
            if c.save_every and self.updates % c.save_every == 0:
                torch.save({"model": self.model.state_dict(), "config": asdict(c),
                            "updates": self.updates, "games": self.games},
                           self.out / f"model_{self.updates:05d}.pt")
            if self.updates % c.eval_every == 0:
                if c.baseline:
                    from selfplay.evaluate import head_to_head, load
                    if self.baseline is None:
                        self.baseline = load(c.baseline, c.device)
                    self.model.eval()
                    entry["vs_baseline"] = head_to_head(self.model, self.baseline, c.eval_games,
                                                        4000 + self.updates, c.perfect_info, sample=True)
                    self.model.train()
                entry["vs_random"] = evaluate(self.model, "random", c.eval_games, 1000 + self.updates,
                                              c.perfect_info)
                entry["vs_greedy"] = evaluate(self.model, "greedy", c.eval_games, 2000 + self.updates,
                                              c.perfect_info)
                torch.save({"model": self.model.state_dict(), "config": asdict(c),
                            "updates": self.updates, "games": self.games},
                           self.out / "model.pt")
            self.log.write(json.dumps(entry) + "\n")
            self.log.flush()
            print(" ".join(f"{k}={v:.3g}" if isinstance(v, float) else f"{k}={v}"
                           for k, v in entry.items()), flush=True)
        torch.save({"model": self.model.state_dict(), "config": asdict(c),
                            "updates": self.updates, "games": self.games}, self.out / "model.pt")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--minutes", type=float, default=30)
    p.add_argument("--updates", type=int, default=None)
    p.add_argument("--out", default="runs/tiny")
    p.add_argument("--init", default=None, help="start from this model.pt")
    p.add_argument("--imitate-minutes", type=float, default=0.0,
                   help="first learn the greedy baseline's choices for this long")
    for k, v in asdict(Config()).items():
        if k == "perfect_info":
            p.add_argument("--hidden", action="store_true", help="Open Team Sheets observations")
        elif isinstance(v, bool):
            name = k.replace("_", "-")
            p.add_argument(f"--no-{name}" if v else f"--{name}", dest=k,
                           action="store_false" if v else "store_true")
        else:
            p.add_argument(f"--{k.replace('_', '-')}", type=type(v), default=v)
    a = p.parse_args()
    if a.init:
        # The model's size comes from the checkpoint.
        saved = torch.load(a.init, map_location="cpu").get("config", {})
        a.d, a.layers = saved.get("d", a.d), saved.get("layers", a.layers)
    cfg = Config(**{k: getattr(a, k) for k in asdict(Config()) if k != "perfect_info"},
                 perfect_info=not a.hidden)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    t = Trainer(cfg, Path(a.out))
    if a.init:
        t.model.load_state_dict(torch.load(a.init, map_location=cfg.device)["model"])
    if a.imitate_minutes > 0:
        t.imitate(a.imitate_minutes)
    t.run(a.minutes, a.updates)


if __name__ == "__main__":
    main()
