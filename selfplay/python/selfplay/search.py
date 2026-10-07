"""Turn search (DESIGN.md 4.14): each turn is a simultaneous-move game
between both sides' joint actions, solved for its equilibrium mix.

- One-turn search (mikumiku37): each side's policy proposes its top-k joint
  actions; every pair is played through its chance outcomes by the engine;
  the value network scores the positions they lead to; the k x k matrix is
  solved.
- Double oracle (Nessie): start from each side's top few actions; scan
  legal actions for best replies to the opponent's current mix, add those
  that improve on the game's value, and solve again, until neither side
  gains. The result is an equilibrium over all (scanned) legal actions, not
  just the ones the policy likes. Each scanned action's loss against the
  opponent's final mix is reported as an alternative.
- Selective deepening (Nessie): the leaves most worth a closer look, by
  (chance of reaching them)^2 x (entropy of their static evaluation), get a
  one-turn search of their own, whose value replaces the static one; the
  root is then solved again. That looks two turns ahead where it matters.

Values are side 0's expected result in [-1, 1]; side 1 minimises them.

    python -m selfplay.search runs/tiny/model.pt --games 400 --k 8
    python -m selfplay.search runs/tiny/model.pt --games 400 --double-oracle --deepen 16
    python -m selfplay.search runs/tiny/model.pt --tree --tree-budget 2000 --vs-search

`--tree` makes side 0 play the tree search (selfplay.mcts) instead, and
`--opp-tree` side 1 (with `--vs-search`).
"""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass, field

import numpy as np
import torch

from selfplay._engine import solve_matrix
from selfplay.env import DECISION_SLOTS, SIZES, SelfPlayEnv

MASK = SIZES["mask_len"]


@dataclass
class SearchConfig:
    k: int = 8                  # one-turn search: candidates per side
    double_oracle: bool = False
    start: int = 2              # double oracle: initial candidates per side
    scan: int = 0               # best-reply scan: each side's top-N legal actions by policy (0: all)
    add: int = 2                # improving replies added per side per iteration
    max_iters: int = 8
    eps: float = 0.005          # gain needed to add a reply
    deepen: int = 0             # leaves deepened per root (0: one turn only)
    deepen_k: int = 4           # candidates per side in a deepened leaf's one-turn search
    max_outcomes: int = 16      # chance outcomes per cell (the rest renormalised away)
    roll_bands: int = 1         # damage-roll bands besides the KO split
    iters: int = 1000           # regret-matching iterations
    leaf_batch: int = 8192      # leaves per network call
    sample: bool = True         # play a sample of the mix (else its most likely action)


@dataclass
class Root:
    """A root's restricted game while the double oracle runs."""
    node: int
    cand: list = field(default_factory=lambda: [[], []])   # per side; [-1]: nothing to decide
    pool: list = field(default_factory=lambda: [[], []])   # actions a best reply may come from
    values: dict = field(default_factory=dict)             # (a, b) -> cell value
    done: bool = False


def solve(values: dict, cand) -> tuple:
    rows, cols = cand
    m = [values[(a, b)] for a in rows for b in cols]
    x, y, v, gap = solve_matrix(m, len(rows), len(cols), 1000)
    return np.asarray(x), np.asarray(y), float(v), float(gap), m


class Search:
    def __init__(self, model, cfg: SearchConfig = SearchConfig(), seed: int = 0):
        self.model, self.cfg = model, cfg
        self.dev = next(model.parameters()).device
        self.rng = np.random.default_rng(seed)
        self.stats = {"roots": 0, "leaves": 0, "cells": 0, "gap": 0.0, "seconds": 0.0,
                      "iterations": 0, "deepened": 0}

    def _t(self, a):
        return torch.from_numpy(np.ascontiguousarray(a)).to(self.dev)

    # --- network ---------------------------------------------------------

    def observe(self, tree, ids):
        s, n = SIZES, len(ids)
        ints = np.empty((n, 2, s["tokens"], s["int_fields"]), np.int32)
        mons = np.empty((n, 2, s["tokens"], s["mon_floats"]), np.float32)
        fld = np.empty((n, 2, s["field_floats"]), np.float32)
        masks = np.empty((n, 2, MASK), np.uint8)
        dec = np.empty((n, 2), np.uint8)
        tree.observe(list(map(int, ids)), ints, mons, fld, masks, dec)
        return ints, mons, fld, masks, dec

    @torch.no_grad()
    def ranked(self, tree, ids) -> list:
        """Per node and side, its legal actions, most probable first under
        the side's own policy (`[-1]` where it has nothing to decide)."""
        ints, mons, fld, masks, dec = self.observe(tree, ids)
        flat = lambda a: a.reshape(-1, *a.shape[2:])
        logp, _ = self.model(*(self._t(flat(a)) for a in (ints, mons, fld, masks, dec)))
        logp = logp.masked_fill(~self._t(flat(masks)).bool(), -float("inf")).cpu().numpy()
        out = []
        for i in range(len(ids)):
            sides = []
            for s in range(2):
                r = i * 2 + s
                if dec[i, s] != DECISION_SLOTS:
                    sides.append([-1])
                    continue
                legal = np.flatnonzero(masks[i, s])
                sides.append(legal[np.argsort(-logp[r, legal], kind="stable")].tolist())
            out.append(sides)
        return out

    @torch.no_grad()
    def static_values(self, ints, mons, fld) -> np.ndarray:
        """Side 0's value of each position: the mean of side 0's estimate
        and the negation of side 1's."""
        n = len(ints)
        out = np.empty(n, np.float32)
        for i in range(0, n, self.cfg.leaf_batch):
            j = min(n, i + self.cfg.leaf_batch)
            v = self.model.value(*(self._t(a[i:j].reshape(-1, *a.shape[2:])) for a in (ints, mons, fld)))
            v = v.view(-1, 2)
            out[i:j] = ((v[:, 0] - v[:, 1]) / 2).cpu().numpy()
        return out

    # --- cells -----------------------------------------------------------

    def expand(self, tree, cells, keep=False):
        """Play `cells` [(node, a, b)] through their chance outcomes. Returns
        per cell its value, and per cell (leaf nodes or None, probs, leaf
        values)."""
        c = self.cfg
        if not cells:
            return np.zeros(0, np.float32), []
        n = tree.expand(np.asarray(cells, np.int64), c.max_outcomes, c.roll_bands,
                        int(self.rng.integers(1 << 62)), keep)
        s = SIZES
        ints = np.empty((n, 2, s["tokens"], s["int_fields"]), np.int32)
        mons = np.empty((n, 2, s["tokens"], s["mon_floats"]), np.float32)
        fld = np.empty((n, 2, s["field_floats"]), np.float32)
        probs, terminal = np.empty(n, np.float32), np.empty(n, np.float32)
        k = len(cells)
        first, count = np.empty(k, np.int64), np.empty(k, np.int64)
        unexplored = np.empty(k, np.float32)
        nodes = np.empty(n if keep else 0, np.int64)
        tree.leaves(ints, mons, fld, probs, terminal, first, count, unexplored, nodes)
        v = self.static_values(ints, mons, fld)
        v = np.where(np.isnan(terminal), v, terminal)
        values = np.empty(k, np.float32)
        per_cell = []
        for i in range(k):
            sl = slice(first[i], first[i] + count[i])
            p = probs[sl]
            values[i] = float((p * v[sl]).sum() / max(p.sum(), 1e-12))
            per_cell.append((nodes[sl] if keep else None, p, v[sl].copy(), terminal[sl]))
        self.stats["leaves"] += n
        self.stats["cells"] += k
        return values, per_cell

    # --- search ----------------------------------------------------------

    def search_nodes(self, tree, ids, deepen=True) -> list[dict]:
        """Search each node in `ids` (each must have a decision for at least
        one side). Per node: candidates (per side), matrix, row, col, value,
        gap, alternatives (per side, (action, loss) best first), deepened."""
        c = self.cfg
        ranked = self.ranked(tree, ids)
        roots = []
        for node, r in zip(ids, ranked):
            root = Root(node=int(node))
            for s in range(2):
                if c.double_oracle:
                    root.cand[s] = r[s][:max(1, c.start)]
                    root.pool[s] = r[s] if c.scan <= 0 else r[s][:c.scan]
                else:
                    root.cand[s] = r[s][:c.k]
                    root.pool[s] = root.cand[s]
            roots.append(root)
        self._fill(tree, [(root, a, b) for root in roots
                                 for a in root.cand[0] for b in root.cand[1]])
        if c.double_oracle:
            for it in range(c.max_iters):
                live = [r for r in roots if not r.done]
                if not live:
                    break
                self.stats["iterations"] += len(live)
                self._oracle_step(tree, live)
        results = [self._result(tree, r) for r in roots]
        if deepen and c.deepen > 0:
            results = self._deepen(tree, roots, results)
        for r in results:
            self.stats["gap"] += r["gap"]
        self.stats["roots"] += len(ids)
        return results

    def _fill(self, tree, wanted):
        """Evaluate the (root, a, b) cells not yet known."""
        need, seen = [], set()
        for root, a, b in wanted:
            key = (id(root), a, b)
            if (a, b) not in root.values and key not in seen:
                seen.add(key)
                need.append((root, a, b))
        values, _ = self.expand(tree, [(r.node, a, b) for r, a, b in need])
        for (root, a, b), v in zip(need, values):
            root.values[(a, b)] = float(v)

    def _scan(self, tree, roots):
        """Each side's pool against the opponent's current mix: per root,
        (x, y, value, u0, u1) with u0[a] / u1[b] each pool action's payoff."""
        mixes = [solve(r.values, r.cand) for r in roots]
        wanted = []
        for r, (x, y, *_ ) in zip(roots, mixes):
            supp0 = [a for a, p in zip(r.cand[0], x) if p > 0.01]
            supp1 = [b for b, p in zip(r.cand[1], y) if p > 0.01]
            wanted += [(r, a, b) for a in r.pool[0] for b in supp1]
            wanted += [(r, a, b) for a in supp0 for b in r.pool[1]]
        self._fill(tree, wanted)
        out = []
        for r, (x, y, v, gap, m) in zip(roots, mixes):
            s0 = [(a, p) for a, p in zip(r.cand[0], x) if p > 0.01]
            s1 = [(b, p) for b, p in zip(r.cand[1], y) if p > 0.01]
            n0, n1 = sum(p for _, p in s0), sum(p for _, p in s1)
            u0 = {a: sum(p * r.values[(a, b)] for b, p in s1) / n1 for a in r.pool[0]}
            u1 = {b: sum(p * r.values[(a, b)] for a, p in s0) / n0 for b in r.pool[1]}
            out.append((x, y, v, u0, u1))
        return out

    def _oracle_step(self, tree, roots):
        c = self.cfg
        for r, (x, y, v, u0, u1) in zip(roots, self._scan(tree, roots)):
            add0 = [a for a in sorted(u0, key=lambda a: -u0[a])
                    if a not in r.cand[0] and u0[a] > v + c.eps][:c.add]
            add1 = [b for b in sorted(u1, key=lambda b: u1[b])
                    if b not in r.cand[1] and u1[b] < v - c.eps][:c.add]
            r.cand[0] += add0
            r.cand[1] += add1
            r.done = not add0 and not add1
        self._fill(tree, [(r, a, b) for r in roots for a in r.cand[0] for b in r.cand[1]])

    def _result(self, tree, r: Root) -> dict:
        x, y, v, gap, m = solve(r.values, r.cand)
        res = {"node": r.node, "candidates": (list(r.cand[0]), list(r.cand[1])), "matrix": m,
               "row": x.tolist(), "col": y.tolist(), "value": v, "gap": gap,
               "alternatives": ([], []), "deepened": 0}
        if self.cfg.double_oracle:
            (_, _, _, u0, u1), = self._scan(tree, [r])
            res["alternatives"] = (sorted(((a, u0[a] - v) for a in u0), key=lambda t: -t[1]),
                                   sorted(((b, v - u1[b]) for b in u1), key=lambda t: -t[1]))
            # A best reply outside the matrix (max_iters reached): its gain.
            res["gap"] = gap + max(0.0, max(u0.values()) - v) + max(0.0, v - min(u1.values()))
        return res

    def _deepen(self, tree, roots, results):
        """Re-expand each root's support cells keeping their leaves, give the
        most uncertain, most likely leaves a one-turn search of their own,
        and solve the roots again with those values."""
        c = self.cfg
        cells, owners = [], []
        for i, (r, res) in enumerate(zip(roots, results)):
            for a, pa in zip(r.cand[0], res["row"]):
                for b, pb in zip(r.cand[1], res["col"]):
                    if pa > 0.01 and pb > 0.01:
                        cells.append((r.node, a, b))
                        owners.append((i, a, b, pa * pb))
        _, per_cell = self.expand(tree, cells, keep=True)
        picks = []                       # (root index, cell index, leaf index, node)
        for i in range(len(roots)):
            cand = []
            for ci, (ri, a, b, reach) in enumerate(owners):
                if ri != i:
                    continue
                nodes, p, v, term = per_cell[ci]
                q = np.clip((v + 1) / 2, 1e-6, 1 - 1e-6)
                h = -(q * np.log(q) + (1 - q) * np.log(1 - q))
                pr = (reach * p) ** 2 * h * np.isnan(term)
                cand += [(float(pr[j]), ci, j, int(nodes[j])) for j in range(len(p)) if pr[j] > 0]
            cand.sort(key=lambda t: -t[0])
            picks += [(i, ci, j, node) for _, ci, j, node in cand[:c.deepen]]
        if not picks:
            return results
        inner = Search(self.model, SearchConfig(k=c.deepen_k, max_outcomes=c.max_outcomes,
                                                roll_bands=c.roll_bands, iters=c.iters,
                                                leaf_batch=c.leaf_batch), int(self.rng.integers(1 << 62)))
        sub = inner.search_nodes(tree, [node for *_, node in picks], deepen=False)
        for k in ("leaves", "cells"):
            self.stats[k] += inner.stats[k]
        self.stats["deepened"] += len(picks)
        for (i, ci, j, _), s in zip(picks, sub):
            per_cell[ci][2][j] = s["value"]
        for ci, (i, a, b, _) in enumerate(owners):
            _, p, v, _ = per_cell[ci]
            roots[i].values[(a, b)] = float((p * v).sum() / max(p.sum(), 1e-12))
        out = []
        for i, r in enumerate(roots):
            res = self._result(tree, r)
            res["deepened"] = sum(1 for pi, *_ in picks if pi == i)
            out.append(res)
        return out

    # --- playing ---------------------------------------------------------

    def run(self, env: SelfPlayEnv, games) -> list[dict]:
        """Search the current position of each of `games`."""
        t0 = time.perf_counter()
        tree = env.search_tree(list(map(int, games)))
        out = self.search_nodes(tree, list(range(len(games))))
        self.stats["seconds"] += time.perf_counter() - t0
        return out

    def pick(self, result: dict, side: int) -> int:
        mix = np.asarray(result["row" if side == 0 else "col"], np.float64)
        cand = result["candidates"][side]
        j = self.rng.choice(len(mix), p=mix / mix.sum()) if self.cfg.sample else int(mix.argmax())
        return cand[j]

    def act(self, env: SelfPlayEnv, games, side: int) -> np.ndarray:
        """`side`'s action in each of `games`, from its equilibrium mix."""
        return np.array([self.pick(r, side) for r in self.run(env, games)], np.int64)



@torch.no_grad()
def make_searcher(model, cfg, seed: int = 0):
    """A Search for a SearchConfig, a TreeSearch for a TreeConfig."""
    from selfplay.mcts import TreeConfig, TreeSearch
    return TreeSearch(model, cfg, seed) if isinstance(cfg, TreeConfig) else Search(model, cfg, seed)


def play_vs_policy(model, games: int, cfg, seed: int = 0, envs: int = 32,
                   perfect_info: bool = True, sampled_policy: bool = False,
                   opponent_cfg=None, opponent_model=None) -> dict:
    """Side 0 searches with `cfg` (a SearchConfig or a TreeConfig); side 1
    plays the raw policy (most likely action, or a sample), or searches with
    `opponent_cfg`. Team preview is the policy's on both sides. Side 1 uses
    `opponent_model` (default: `model`) for its policy and its search."""
    from selfplay.train import act, to_tensors
    env = SelfPlayEnv(min(envs, games), seed=seed, perfect_info=perfect_info)
    search = make_searcher(model, cfg, seed)
    opp_model = opponent_model or model
    other = make_searcher(opp_model, opponent_cfg, seed + 1) if opponent_cfg else None
    model.eval()
    opp_model.eval()
    done = score = 0.0
    while done < games:
        obs = env.observe()
        actions = np.full((env.num_envs, 2), -1, np.int64)
        dec = obs.decisions
        rows = np.flatnonzero(dec.reshape(-1) != 0)
        for side, net in ((0, model), (1, opp_model)):
            r = rows[rows % 2 == side]
            if len(r):
                a, _, _ = act(net, to_tensors(obs, r), greedy=not sampled_policy)
                actions.reshape(-1)[r] = a.numpy()
        g0 = np.flatnonzero(dec[:, 0] == DECISION_SLOTS)
        if len(g0):
            actions[g0, 0] = search.act(env, g0, side=0)
        g1 = np.flatnonzero(dec[:, 1] == DECISION_SLOTS)
        if other is not None and len(g1):
            actions[g1, 1] = other.act(env, g1, side=1)
        r = env.step(actions)
        fin = r.done.astype(bool)
        done += fin.sum()
        score += ((r.reward[fin, 0] + 1) / 2).sum()
    p = score / done
    out = {"score": float(p), "ci95": float(1.96 * np.sqrt(p * (1 - p) / done)), "games": int(done)}
    for who, srch in (("", search), ("opp_", other)):
        if srch is None:
            continue
        s = srch.stats
        roots = max(1, s["roots"])
        out[who + "roots"] = s["roots"]
        for k, v in s.items():
            if k not in ("roots", "seconds"):
                out[f"{who}{k}_per_root"] = v / roots
        out[who + "ms_per_root"] = 1000 * s["seconds"] / roots
    return out


def config_args(p: argparse.ArgumentParser, prefix: str = ""):
    for k, v in vars(SearchConfig()).items():
        name = prefix + k.replace("_", "-")
        if isinstance(v, bool):
            flag = f"--{prefix}no-{k.replace('_', '-')}" if v else f"--{name}"
            p.add_argument(flag, dest=prefix.replace("-", "_") + k,
                           action="store_false" if v else "store_true")
        else:
            p.add_argument(f"--{name}", dest=prefix.replace("-", "_") + k, type=type(v), default=v)


def main():
    from selfplay.evaluate import load
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("model")
    p.add_argument("--games", type=int, default=400)
    p.add_argument("--envs", type=int, default=32)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--device", default="cpu")
    p.add_argument("--hidden", action="store_true")
    p.add_argument("--sampled-policy", action="store_true", help="the policy side samples instead of argmax")
    p.add_argument("--vs-search", action="store_true",
                   help="side 1 searches too, with the --opp-* settings (default: one-turn search)")
    p.add_argument("--tree", action="store_true", help="side 0 plays the tree search")
    p.add_argument("--opp-tree", action="store_true", help="side 1 plays the tree search")
    p.add_argument("--opp-model", default=None, help="side 1 plays this model.pt (default: MODEL)")
    config_args(p)
    config_args(p, "opp-")
    from selfplay import mcts
    mcts.config_args(p, "tree-")
    mcts.config_args(p, "opp-tree-")
    a = p.parse_args()
    model = load(a.model, a.device)
    if a.tree:
        cfg = mcts.config_from(a, "tree_")
    else:
        cfg = SearchConfig(**{k: getattr(a, k) for k in vars(SearchConfig())})
    opp = None
    if a.vs_search:
        if a.opp_tree:
            opp = mcts.config_from(a, "opp_tree_")
        else:
            opp = SearchConfig(**{k: getattr(a, "opp_" + k) for k in vars(SearchConfig())})
    opp_model = load(a.opp_model, a.device) if a.opp_model else None
    res = play_vs_policy(model, a.games, cfg, a.seed, a.envs, not a.hidden, a.sampled_policy, opp, opp_model)
    print(" ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in res.items()))


if __name__ == "__main__":
    main()
