"""Batched self-play environment over the Rust engine (DESIGN.md phase 2).

Each of the `num_envs` games is played by both sides; observations and
actions come in pairs, side 0 then side 1. A side with nothing to decide
(`decisions == 0`) has its action ignored. Finished games are replaced by
new ones drawn from the team corpus, weighted by tournament placement.

Action spaces (see engine/src/env/action.rs):
- team preview (`decisions == 1`): one of 180 ordered picks of 4 from 6;
- moves and switches (`decisions == 2`): a joint action
  `slot0 * 47 + slot1` over 47 per-slot actions (move x target x Mega,
  switch to party position, pass).
`masks` marks the legal ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from selfplay._engine import VecEnv as _VecEnv

SIZES: dict = _VecEnv.sizes()
DEFAULT_CORPUS = Path(__file__).resolve().parents[2] / "data" / "corpus"

DECISION_NONE, DECISION_PREVIEW, DECISION_SLOTS = 0, 1, 2


@dataclass
class Observation:
    """Both sides' views, shaped [num_envs, 2, ...]."""
    ints: np.ndarray        # [n, 2, tokens, int_fields] int32: dex ids + 1 (0 = none)
    mons: np.ndarray        # [n, 2, tokens, mon_floats] float32
    field: np.ndarray       # [n, 2, field_floats] float32
    masks: np.ndarray       # [n, 2, mask_len] uint8: 1 = legal action
    decisions: np.ndarray   # [n, 2] uint8: 0 none, 1 team preview, 2 moves/switches


@dataclass
class StepResult:
    done: np.ndarray        # [n] uint8: the game ended (and was replaced)
    reward: np.ndarray      # [n, 2] float32: +1 win, -1 loss, 0 draw, for finished games
    turns: np.ndarray       # [n] int32: length of finished games


class SelfPlayEnv:
    def __init__(self, num_envs: int, corpus: str | Path = DEFAULT_CORPUS, seed: int = 0,
                 turn_limit: int = 30, perfect_info: bool = True, threads: int = 0):
        self.num_envs = num_envs
        self._env = _VecEnv(num_envs, str(corpus), seed, turn_limit, perfect_info, threads)
        s, n = SIZES, num_envs
        self.obs = Observation(
            ints=np.zeros((n, 2, s["tokens"], s["int_fields"]), np.int32),
            mons=np.zeros((n, 2, s["tokens"], s["mon_floats"]), np.float32),
            field=np.zeros((n, 2, s["field_floats"]), np.float32),
            masks=np.zeros((n, 2, s["mask_len"]), np.uint8),
            decisions=np.zeros((n, 2), np.uint8),
        )
        self.result = StepResult(
            done=np.zeros(n, np.uint8),
            reward=np.zeros((n, 2), np.float32),
            turns=np.zeros(n, np.int32),
        )

    def observe(self) -> Observation:
        """Refresh and return the (reused) observation arrays."""
        o = self.obs
        self._env.observe(o.ints, o.mons, o.field, o.masks, o.decisions)
        return o

    def step(self, actions: np.ndarray) -> StepResult:
        """Play `actions` [n, 2] (int64 action indices) and return the (reused)
        result arrays."""
        actions = np.ascontiguousarray(actions, dtype=np.int64)
        r = self.result
        self._env.step(actions, r.done, r.reward, r.turns)
        return r

    def random_actions(self, seed: int) -> np.ndarray:
        """Uniformly random legal actions [n, 2] for the last observation
        (-1 where a side has nothing to decide)."""
        out = np.empty((self.num_envs, 2), np.int64)
        self._env.random_actions(self.obs.masks, self.obs.decisions, out, seed)
        return out

    def greedy_actions(self) -> np.ndarray:
        """The greedy-damage baseline's actions [n, 2] (-1 where a side has
        nothing to decide)."""
        out = np.empty((self.num_envs, 2), np.int64)
        self._env.greedy_actions(out)
        return out

    def search_tree(self, games):
        """A search tree (selfplay._engine.SearchTree) whose node i is a copy
        of game games[i]."""
        return self._env.search_tree(list(map(int, games)))

    def legal_choices(self, game: int, side: int) -> list[tuple[int, str]]:
        """(action index, Showdown choice string) pairs, for debugging."""
        return self._env.legal_choices(game, side)

    def snapshot(self, game: int) -> str:
        return self._env.snapshot(game)


def random_actions(obs: Observation, rng: np.random.Generator) -> np.ndarray:
    """A uniformly random legal action for every side that has a decision
    (-1 elsewhere), in numpy. `SelfPlayEnv.random_actions` is faster."""
    masks = obs.masks.reshape(-1, obs.masks.shape[-1])
    counts = masks.sum(axis=1, dtype=np.int32)
    pick = (rng.random(len(counts)) * counts).astype(np.int32)
    actions = (masks.cumsum(axis=1, dtype=np.int16) > pick[:, None]).argmax(axis=1)
    actions[(obs.decisions.reshape(-1) == DECISION_NONE) | (counts == 0)] = -1
    return actions.reshape(obs.decisions.shape)
