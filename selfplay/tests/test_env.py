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
