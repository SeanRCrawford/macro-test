"""One-turn matrix search through the Python API (needs torch and the built
module)."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("selfplay._engine")

from selfplay.env import DECISION_SLOTS, SelfPlayEnv  # noqa: E402
from selfplay.model import PolicyNet  # noqa: E402
from selfplay.search import OneTurnSearch, SearchConfig  # noqa: E402


def test_search_plays_legal_equilibrium_mixes():
    torch.manual_seed(0)
    env = SelfPlayEnv(8, seed=11)
    search = OneTurnSearch(PolicyNet(d=32, layers=1), SearchConfig(k=3, max_outcomes=8))
    searched = 0
    for _ in range(30):
        obs = env.observe()
        actions = env.random_actions(searched)
        games = np.flatnonzero(obs.decisions[:, 0] == DECISION_SLOTS)
        if len(games):
            results = search.run(env, obs, games)
            for g, r in zip(games, results):
                rows, cols = len(r["candidates"][0]), len(r["candidates"][1])
                assert len(r["matrix"]) == rows * cols
                assert abs(sum(r["row"]) - 1) < 1e-4 and abs(sum(r["col"]) - 1) < 1e-4
                assert all(-1 <= v <= 1 for v in r["matrix"])
                assert r["gap"] < 0.02
                assert all(obs.masks[g, 0, a] for a in r["candidates"][0])
            actions[games, 0] = search.act(env, obs, games, side=0)
            assert all(obs.masks[g, 0, a] for g, a in zip(games, actions[games, 0]))
            searched += len(games)
        env.step(actions)
    assert searched > 20
