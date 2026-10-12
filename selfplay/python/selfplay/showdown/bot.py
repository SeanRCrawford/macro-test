"""The bot's decisions in a live Showdown battle.

At each request the tracker's position is rebuilt in the engine (one game
of a SelfPlayEnv), and the network or the tree search picks an action,
exactly as in self-play. The choice is checked against Showdown's own
request before it goes out; an action Showdown wouldn't take falls back to
the next best one.

The opponent's four: Showdown only shows the ones that have appeared. The
others are the network's own guess: its team-preview policy for the
opponent, given the leads it saw.
"""
from __future__ import annotations

import json
import re
import time

import numpy as np
import torch

from selfplay.env import DECISION_PREVIEW, DECISION_SLOTS, SelfPlayEnv
from selfplay.showdown.tracker import Tracker, parse_condition
from selfplay.train import to_tensors


class Bot:
    def __init__(self, model, team: str, tree=None, sample: bool = False, seed: int = 0, log=print):
        """`team`: our paste (the one uploaded). `tree`: a TreeConfig to
        search with (None: play the policy). `sample`: sample the policy
        (or the search's mix) instead of taking its most likely action."""
        self.model = model
        self.team = team
        self.env = SelfPlayEnv(1, seed=seed, threads=1)
        self.dev = next(model.parameters()).device
        self.sample = sample
        self.rng = np.random.default_rng(seed)
        self.log = log
        self.search = None
        if tree is not None:
            from selfplay.mcts import TreeSearch
            tree.sample = sample
            self.search = TreeSearch(model, tree, seed)
        self.foe_orders: list[tuple[tuple[int, ...], float]] = []

    # --- The network ----------------------------------------------------------

    @torch.no_grad()
    def _policy(self):
        """Both sides' log-probabilities and values at the loaded position."""
        obs = self.env.observe()
        logp, value = self.model(*(t.to(self.dev) for t in to_tensors(obs, np.array([0, 1]))))
        return obs, logp.float().cpu().numpy(), value.float().cpu().numpy()

    def _load(self, position: dict):
        self.env._env.set_position(0, json.dumps(position), int(self.rng.integers(1 << 62)))

    # --- Team preview -----------------------------------------------------------

    def preview(self, t: Tracker) -> tuple[str, str]:
        self._load(t.preview_position())
        obs, logp, value = self._policy()
        if obs.decisions[0, 0] != DECISION_PREVIEW:
            raise RuntimeError("the engine isn't at team preview")
        ours = dict(self.env.legal_choices(0, 0))
        theirs = dict(self.env.legal_choices(0, 1))
        # The opponent's likely picks, for guessing its unseen Pokemon later.
        p1 = np.exp(logp[1])
        self.foe_orders = sorted(((tuple(int(c) - 1 for c in s.split()[1]), float(p1[a]))
                                  for a, s in theirs.items()), key=lambda x: -x[1])
        a = self._pick(logp[0], list(ours))
        top = sorted(ours, key=lambda x: -logp[0][x])[:3]
        names = [m.species for m in t.mine.mons]
        desc = lambda s: " ".join(names[int(c) - 1] for c in s.split()[1])
        note = (f"team preview: win {100 * (value[0] + 1) / 2:.0f}%; "
                + "; ".join(f"{100 * np.exp(logp[0][x]):.0f}% {desc(ours[x])}" for x in top))
        return ours[a], note

    def guess_brought(self, t: Tracker) -> list[int]:
        """The opponent's four, most likely first: its preview policy's top
        pick among those that fit what has been seen."""
        foe = t.foe
        seen = {m.uid for m in foe.mons if m.revealed}
        leads = foe.leads
        for order, _ in self.foe_orders:
            if all(lead is None or order[i] == lead for i, lead in enumerate(leads)) and seen <= set(order):
                return list(order)
        return [m.uid for m in foe.mons]

    # --- Moves and switches --------------------------------------------------------

    def decide(self, t: Tracker) -> tuple[str, str]:
        """The choice for the current request, and a line saying why."""
        req = t.request or {}
        if req.get("teamPreview"):
            return self.preview(t)
        position = t.position(self.guess_brought)
        self._load(position)
        obs, logp, value = self._policy()
        if obs.decisions[0, 0] != DECISION_SLOTS:
            return "default", "the engine sees nothing to decide here"
        legal = dict(self.env.legal_choices(0, 0))
        start = time.perf_counter()
        info = f"win {100 * (value[0] + 1) / 2:.0f}% (network)"
        ranked = sorted(legal, key=lambda x: -logp[0][x])
        if self.search is not None:
            r = self.search.search(self.env, [0])[0]
            a = self.search.pick(r, 0)
            mix = sorted(zip(r["candidates"][0], r["row"]), key=lambda x: -x[1])
            ranked = [c for c, _ in mix] + [x for x in ranked if x not in r["candidates"][0]]
            ranked.remove(a)
            ranked.insert(0, a)
            info = (f"win {100 * (r['value'] + 1) / 2:.0f}% (search, {r['leaf_evals']} evals, "
                    f"{time.perf_counter() - start:.1f}s); "
                    + "; ".join(f"{100 * w:.0f}% {legal.get(c, c)}" for c, w in mix[:3] if w >= 0.01))
        else:
            a = self._pick(logp[0], list(legal))
            ranked.remove(a)
            ranked.insert(0, a)
            info += "; " + "; ".join(f"{100 * np.exp(logp[0][x]):.0f}% {legal[x]}" for x in ranked[:3])
        for x in ranked:
            if valid(legal[x], req):
                if x != a:
                    info += f" [{legal[a]} isn't allowed here; next best]"
                return legal[x], info
        return "default", info + " [no engine choice fits Showdown's request]"

    def _pick(self, logp: np.ndarray, legal: list[int]) -> int:
        if not self.sample:
            return max(legal, key=lambda x: logp[x])
        p = np.exp(np.array([logp[x] for x in legal], np.float64))
        return legal[int(self.rng.choice(len(legal), p=p / p.sum()))]


def valid(choice: str, req: dict) -> bool:
    """Whether Showdown would take `choice` for request `req`."""
    if choice == "default":
        return True
    if req.get("teamPreview"):
        return choice.startswith("team ")
    pokemon = req.get("side", {}).get("pokemon", [])
    parts = [p.strip() for p in choice.split(",")]
    force = req.get("forceSwitch")
    active = req.get("active") or []
    switched = set()
    megas = 0
    for slot, part in enumerate(parts):
        words = part.split()
        if not words:
            return False
        kind = words[0]
        if kind == "pass":
            continue
        if kind == "switch":
            k = int(words[1]) - 1
            if k < 0 or k >= len(pokemon) or k in switched:
                return False
            p = pokemon[k]
            if p.get("active") or parse_condition(p.get("condition", "0 fnt"))[2] == "fnt":
                return False
            # (A "maybeTrapped" Pokemon may try: Showdown says if it can't.)
            if not force and slot < len(active) and active[slot].get("trapped"):
                return False
            if force and not (slot < len(force) and force[slot]):
                return False
            switched.add(k)
            continue
        if kind != "move" or force or slot >= len(active):
            return False
        j = int(words[1]) - 1
        moves = active[slot].get("moves", [])
        if j < 0 or j >= len(moves) or moves[j].get("disabled"):
            return False
        if "mega" in words:
            megas += 1
            if not active[slot].get("canMegaEvo"):
                return False
    return megas <= 1


def choice_text(choice: str, t: Tracker) -> str:
    """'move 1 2, switch 3' with names: for the log."""
    req = t.request or {}
    pokemon = req.get("side", {}).get("pokemon", [])
    active = req.get("active") or []
    out = []
    for slot, part in enumerate(choice.split(",")):
        words = part.split()
        if not words:
            continue
        if words[0] == "move" and slot < len(active):
            moves = active[slot].get("moves", [])
            j = int(words[1]) - 1
            name = moves[j]["move"] if 0 <= j < len(moves) else words[1]
            target = ""
            if len(words) > 2 and re.fullmatch(r"-?\d", words[2]):
                loc = int(words[2])
                target = " -> " + ("foe " if loc > 0 else "ally ") + "ab"[abs(loc) - 1]
            out.append(name + target + (" (Mega)" if "mega" in words else ""))
        elif words[0] == "switch" and int(words[1]) - 1 < len(pokemon):
            out.append("switch to " + pokemon[int(words[1]) - 1]["ident"].split(": ", 1)[-1])
        else:
            out.append(part.strip())
    return ", ".join(out)
