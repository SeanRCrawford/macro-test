"""The Python environment over the Rust engine (needs the built module:
`maturin develop --release` in selfplay/)."""
import numpy as np
import pytest

env_mod = pytest.importorskip("selfplay.env")
SelfPlayEnv, SIZES = env_mod.SelfPlayEnv, env_mod.SIZES


def test_shapes_and_masks():
    env = SelfPlayEnv(16, seed=1)
    o = env.observe()
    assert o.ints.shape == (16, 2, SIZES["tokens"], SIZES["int_fields"])
    assert o.mons.shape == (16, 2, SIZES["tokens"], SIZES["mon_floats"])
    assert o.field.shape == (16, 2, SIZES["field_floats"])
    # Every game starts at team preview: 180 legal orders for both sides.
    assert (o.decisions == env_mod.DECISION_PREVIEW).all()
    assert (o.masks[..., :SIZES["preview_actions"]].sum(axis=-1) == 180).all()
    assert o.masks[..., SIZES["preview_actions"]:].sum() == 0


def test_mask_matches_legal_choices():
    env = SelfPlayEnv(8, seed=2)
    for step in range(40):
        o = env.observe()
        for g in range(8):
            for side in range(2):
                legal = {i for i, _ in env.legal_choices(g, side)}
                assert legal == set(np.flatnonzero(o.masks[g, side])), (step, g, side)
        env.step(env.random_actions(step))


def test_random_games_finish_zero_sum():
    env = SelfPlayEnv(64, seed=3, perfect_info=False)
    finished = 0
    for step in range(1500):
        o = env.observe()
        assert np.isfinite(o.mons).all() and np.isfinite(o.field).all()
        r = env.step(env_mod.random_actions(o, np.random.default_rng(step)))
        done = r.done.astype(bool)
        finished += int(done.sum())
        assert (r.reward[done, 0] == -r.reward[done, 1]).all()
        assert (r.turns[done] <= 30).all()
    assert finished > 100


def test_illegal_action_is_an_error():
    env = SelfPlayEnv(1, seed=4)
    env.observe()
    with pytest.raises(ValueError):
        env.step(np.array([[10_000, 0]]))


def test_matchups_cycle_through_fixed_pairs():
    from selfplay.env import SelfPlayEnv
    env = SelfPlayEnv(6, seed=4)
    pairs = [(0, 1), (2, 3), (4, 5)]
    env.set_matchups(pairs)
    current = [env.game_teams(g) for g in range(env.num_envs)]
    assert set(current) <= set(pairs)
    counts = {p: 0 for p in pairs}
    finished = 0
    while finished < 30:
        env.observe()
        r = env.step(env.random_actions(finished))
        for g in np.flatnonzero(r.done):
            counts[current[g]] += 1
            current[g] = env.game_teams(g)
            finished += 1
    assert all(c >= 6 for c in counts.values()), counts
    env.set_matchups([])


def test_team_lookup_and_views():
    from selfplay.env import SelfPlayEnv
    env = SelfPlayEnv(1, seed=1)
    names = env.team_names()
    assert env.team_index(names[5]) == 5
    with pytest.raises(ValueError):
        env.team_index("Team")              # matches many
    env.set_matchups([(3, 7)])
    v = env.view(0, 0)
    assert len(v["you"]["pokemon"]) == 6 and len(v["foe"]["pokemon"]) == 6
    assert all(len(p["moves"]) == 4 for p in v["foe"]["pokemon"])
