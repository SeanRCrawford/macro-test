"""The policy network and the PPO plumbing (needs torch and the built module)."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("selfplay._engine")

from selfplay.env import SelfPlayEnv  # noqa: E402
from selfplay.model import PolicyNet, preview_orders  # noqa: E402
from selfplay.train import Config, Trainer, act, to_tensors  # noqa: E402


def test_preview_orders_match_the_engine():
    env = SelfPlayEnv(1)
    env.observe()
    orders = preview_orders()
    for i, s in env.legal_choices(0, 0):
        assert s == "team " + "".join(str(x + 1) for x in orders[i])


def test_policy_only_picks_legal_actions():
    env = SelfPlayEnv(32, seed=7)
    model = PolicyNet(d=32, layers=1)
    for _ in range(60):
        obs = env.observe()
        rows = np.flatnonzero(obs.decisions.reshape(-1) != 0)
        logp, value = model(*to_tensors(obs, rows))
        assert torch.isfinite(value).all() and (value.abs() <= 1).all()
        # Probability mass only on legal actions.
        masks = torch.from_numpy(obs.masks.reshape(-1, obs.masks.shape[-1])[rows]).bool()
        assert torch.allclose(logp.exp().masked_fill(~masks, 0).sum(-1), torch.ones(len(rows)), atol=1e-4)
        a, _, _ = act(model, to_tensors(obs, rows))
        actions = np.full(64, -1, np.int64)
        actions[rows] = a.numpy()
        env.step(actions.reshape(32, 2))


def test_gae_credits_the_result_to_each_sides_last_decision(tmp_path):
    cfg = Config(envs=16, steps=200, d=16, layers=1, league_frac=0.0)
    t = Trainer(cfg, tmp_path)
    data, results = t.rollout()
    assert results, "no game finished"
    done = data["done"]
    # Every terminal transition carries the game's result.
    assert set(data["reward"][done].tolist()) <= {-1.0, 0.0, 1.0}
    assert (data["reward"][~done] == 0).all()
    # With gamma 1, a terminal decision's return is its reward.
    assert torch.allclose(data["ret"][done], data["reward"][done], atol=1e-5)


def test_joint_head_learns_a_correlated_mix():
    """Half "a0 + b0", half "a1 + b1", never the crossed pairs: a product of
    per-slot distributions can put at most 1/4 on each wanted pair."""
    torch.manual_seed(0)
    env = SelfPlayEnv(16, seed=3)
    for _ in range(40):
        obs = env.observe()
        rows = np.flatnonzero(obs.decisions.reshape(-1) == 2)
        masks = obs.masks.reshape(-1, 47, 47)
        found = None
        for r in rows:
            m = masks[r]
            a = np.flatnonzero(m.any(1))
            b = np.flatnonzero(m.any(0))
            for a0, a1 in zip(a, a[1:]):
                for b0, b1 in zip(b, b[1:]):
                    if m[a0, b0] and m[a1, b1] and m[a0, b1] and m[a1, b0]:
                        found = (r, a0, a1, b0, b1)
                        break
                if found:
                    break
            if found:
                break
        if found:
            break
        env.step(env.random_actions(0))
    assert found, "no position with two free choices per slot"
    r, a0, a1, b0, b1 = found
    model = PolicyNet(d=32, layers=1)
    batch = to_tensors(obs, np.array([r]))
    opt = torch.optim.Adam(model.parameters(), lr=3e-3)
    want = [a0 * 47 + b0, a1 * 47 + b1]
    for _ in range(300):
        logp, _ = model(*batch)
        loss = -logp[0, want].mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    p = logp[0].exp().detach()
    assert p[want].min() > 0.4, p[want]
    assert p[a0 * 47 + b1] + p[a1 * 47 + b0] < 0.05
