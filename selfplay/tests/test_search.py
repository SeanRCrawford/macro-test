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


def test_tree_search_plays_legal_mixes_and_looks_ahead():
    from selfplay.mcts import TreeConfig, TreeSearch
    torch.manual_seed(3)
    env = SelfPlayEnv(8, seed=21)
    search = TreeSearch(PolicyNet(d=32, layers=1), TreeConfig(budget=300, max_outcomes=8))
    deep = 0
    for obs, games in positions(env, 12, seed=5):
        for g, r in zip(games, search.run(env, games)):
            assert abs(sum(r["row"]) - 1) < 1e-3 and abs(sum(r["col"]) - 1) < 1e-3
            assert -1 <= r["value"] <= 1
            assert all(obs.masks[g, 0, a] for a in r["candidates"][0])
            assert obs.masks[g, 0, search.pick(r, 0)]
            assert r["leaf_evals"] < 300 + 600
            assert len(r["alternatives"][0]) == len(r["candidates"][0])
            deep += r["max_depth"] >= 1
    assert deep > 0


def test_tiebreak_prefers_the_action_better_against_other_replies():
    """Two actions tie against the opponent's equilibrium reply (it knocks
    our user out first, so our move doesn't matter); the one that does
    better if the opponent plays otherwise wins the tie, whatever the
    solver's mix weighted. A worse action never wins it."""
    from selfplay.mcts import tiebreak
    r = {"candidates": ([10, 11, 12], [20, 21]),
         # Rows ours: 10 "Ice Beam into the partner", 11 "Ice Beam into the
         # foe", 12 "something worse". Cols theirs: 20 (equilibrium), 21.
         "matrix": [-0.7, -0.7,
                    -0.7, 0.4,
                    -0.9, 0.9],
         "row": [0.6, 0.4, 0.0], "col": [1.0, 0.0], "priors": ([0.3, 0.3, 0.4], [0.5, 0.5])}
    assert tiebreak(r, 0, 0.02, default=0) == 1
    assert tiebreak(r, 0, 0.0, default=0) == 1      # exact ties still break
    # Side 1's view: its results are the negation, its candidates the rows.
    r1 = {"candidates": ([20, 21], [10, 11]), "matrix": [0.7, 0.7, 0.7, -0.4],
          "row": [1.0, 0.0], "col": [0.6, 0.4], "priors": ([0.5, 0.5], [0.5, 0.5])}
    assert tiebreak(r1, 1, 0.02, default=0) == 1
    assert tiebreak({"candidates": ([1], [2]), "matrix": []}, 0, 0.02, default=0) == 0
    # An action the mix barely plays isn't brought back, however it does
    # against the policy's picture of the opponent.
    r2 = dict(r, row=[0.98, 0.02, 0.0])
    assert tiebreak(r2, 0, 0.02, default=0) == 0


def test_tree_search_stops_at_its_time_limit():
    import time

    from selfplay.mcts import TreeConfig, TreeSearch
    torch.manual_seed(3)
    env = SelfPlayEnv(1, seed=21)
    _, games = next(positions(env, 1, seed=5))
    search = TreeSearch(PolicyNet(d=32, layers=1), TreeConfig(budget=10_000_000, max_seconds=1.0))
    start = time.perf_counter()
    r = search.run(env, games)[0]
    assert time.perf_counter() - start < 6
    assert r["leaf_evals"] > 0 and abs(sum(r["row"]) - 1) < 1e-3


def test_tree_search_reports_its_lookahead_and_root_share_deepens_it():
    """Per root cell, the turns its value looks past its own and the share
    the network values one turn later; stopping the root's widening at a
    share of the budget puts the rest into the turns after its likely
    plays."""
    from selfplay.mcts import TreeConfig, TreeSearch
    from selfplay.mcts import lookahead_of
    torch.manual_seed(3)
    net = PolicyNet(d=32, layers=1)
    looks = {}
    for share in (1.0, 0.3):
        env = SelfPlayEnv(8, seed=21)
        _, games = next(positions(env, 8, seed=5))
        search = TreeSearch(net, TreeConfig(budget=1500, root_share=share, sample=False), seed=1)
        total = []
        for r in search.run(env, games):
            cells = len(r["candidates"][0]) * len(r["candidates"][1])
            assert len(r["lookahead"]) == len(r["static_share"]) == cells
            assert all(0 <= x <= 1 + 1e-5 for x in r["static_share"])
            assert r["depth_nodes"][0] == 1
            assert len(r["oracle_gain"]) == len(r["unprobed"]) == 2 and min(r["oracle_gain"]) >= 0
            total.append(lookahead_of(r, search.pick(r, 0)))
        looks[share] = float(np.mean(total))
    assert looks[0.3] > looks[1.0]
