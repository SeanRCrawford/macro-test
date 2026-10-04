"""Turn search through the Python API (needs torch and the built module)."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("selfplay._engine")

from selfplay._engine import solve_matrix  # noqa: E402
from selfplay.env import DECISION_SLOTS, SelfPlayEnv  # noqa: E402
from selfplay.model import PolicyNet  # noqa: E402
from selfplay.search import Search, SearchConfig  # noqa: E402


def positions(env, n, seed=0):
    """Advance random play until `n` positions where side 0 chooses moves;
    yields (games) batches."""
    rng = np.random.default_rng(seed)
    found = 0
    while found < n:
        obs = env.observe()
        games = np.flatnonzero(obs.decisions[:, 0] == DECISION_SLOTS)
        if len(games):
            found += len(games)
            yield obs, games
        env.step(env.random_actions(int(rng.integers(1 << 30))))


def test_one_turn_search_plays_legal_equilibrium_mixes():
    torch.manual_seed(0)
    env = SelfPlayEnv(8, seed=11)
    search = Search(PolicyNet(d=32, layers=1), SearchConfig(k=3, max_outcomes=8))
    for obs, games in positions(env, 20):
        for g, r in zip(games, search.run(env, games)):
            rows, cols = len(r["candidates"][0]), len(r["candidates"][1])
            assert len(r["matrix"]) == rows * cols
            assert abs(sum(r["row"]) - 1) < 1e-4 and abs(sum(r["col"]) - 1) < 1e-4
            assert all(-1 <= v <= 1 for v in r["matrix"])
            assert r["gap"] < 0.02
            assert all(obs.masks[g, 0, a] for a in r["candidates"][0])
            assert obs.masks[g, 0, search.pick(r, 0)]


def test_double_oracle_matches_the_full_matrix():
    """Where each side has few legal actions, the double oracle's value
    equals that of the full matrix of every legal pair."""
    torch.manual_seed(1)
    model = PolicyNet(d=32, layers=1)
    env = SelfPlayEnv(32, seed=5)
    full = Search(model, SearchConfig(k=10_000, max_outcomes=8))
    do = Search(model, SearchConfig(double_oracle=True, start=1, add=1, max_iters=50,
                                    eps=1e-4, max_outcomes=8))
    checked = 0
    for obs, games in positions(env, 400, seed=2):
        legal = obs.masks[games].sum(-1)
        small = games[(legal.max(1) <= 12) & (obs.decisions[games] == DECISION_SLOTS).all(1)]
        if not len(small):
            continue
        tree = env.search_tree(small)
        ids = list(range(len(small)))
        for a, b in zip(full.search_nodes(tree, ids), do.search_nodes(tree, ids)):
            assert abs(a["value"] - b["value"]) < 0.01, (a["value"], b["value"])
            assert b["gap"] < 0.01
            # Every legal action was scanned, none beats the mix by more than eps.
            assert len(b["alternatives"][0]) == len(a["candidates"][0])
            assert max(loss for _, loss in b["alternatives"][0]) < 0.01
            checked += 1
        if checked >= 10:
            break
    assert checked >= 5, f"only {checked} small positions"


def test_deepening_replaces_leaf_values():
    torch.manual_seed(2)
    env = SelfPlayEnv(8, seed=3)
    search = Search(PolicyNet(d=32, layers=1),
                    SearchConfig(k=3, max_outcomes=8, deepen=4, deepen_k=2))
    total = 0
    for obs, games in positions(env, 10, seed=4):
        for r in search.run(env, games):
            assert r["deepened"] <= 4
            assert abs(sum(r["row"]) - 1) < 1e-4
            total += r["deepened"]
    assert total > 0 and search.stats["deepened"] == total


def test_solve_matrix():
    x, y, v, gap = solve_matrix([0, -1, 1, 1, 0, -1, -1, 1, 0], 3, 3, 2000)
    assert abs(v) < 0.01 and gap < 0.01 and max(x) < 0.35
