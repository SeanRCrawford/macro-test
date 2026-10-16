"""The policy network and the PPO plumbing (needs torch and the built module)."""
import json

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
    data, results, _ = t.rollout()
    assert results, "no game finished"
    done = data["done"]
    # Every terminal transition carries the game's result.
    assert set(data["reward"][done].tolist()) <= {-1.0, 0.0, 1.0}
    assert (data["reward"][~done] == 0).all()
    # With gamma 1, a terminal decision's return is its reward.
    assert torch.allclose(data["ret"][done], data["reward"][done], atol=1e-5)
    # The vectorised GAE matches the plain loop over transitions.
    c = t.cfg
    stream, value, reward = data["stream"], data["value"].float(), data["reward"]
    boot = torch.zeros(2 * cfg.envs)
    nv, na, adv = boot.clone(), torch.zeros_like(boot), torch.zeros(len(reward))
    for i in range(len(reward) - 1, -1, -1):
        s = stream[i]
        if done[i]:
            nv[s], na[s] = 0.0, 0.0
        delta = reward[i] + c.gamma * nv[s] - value[i]
        a = delta + c.gamma * c.lam * na[s]
        adv[i] = a
        nv[s], na[s] = value[i], a
    # Recover blocks of distinct streams (a stream appears at most once a
    # step, so these are steps or runs of steps).
    blocks, seen, size = [], set(), 0
    for s in stream.tolist():
        if s in seen:
            blocks.append(size)
            seen, size = set(), 0
        seen.add(s)
        size += 1
    blocks.append(size)
    got, _ = t.gae(data, boot, blocks)
    assert torch.allclose(got, adv, atol=1e-4)


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


def test_encoder_matches_torch_transformer():
    """The written-out encoder computes nn.TransformerEncoder's function
    with the same parameters (checkpoints load unchanged)."""
    from torch import nn

    from selfplay.model import Encoder

    torch.manual_seed(0)
    enc = Encoder(64, 4, 3)
    for p in enc.parameters():
        nn.init.normal_(p, std=0.1)
    ref = nn.TransformerEncoder(nn.TransformerEncoderLayer(64, 4, 128, dropout=0.0, batch_first=True,
                                                           norm_first=True, activation="gelu"),
                                3, enable_nested_tensor=False)
    ref.load_state_dict(enc.state_dict())
    x = torch.randn(16, 13, 64)
    assert (enc(x) - ref(x)).abs().max() < 1e-5


def test_bfloat16_paths_agree_with_the_old_code():
    """Under bfloat16 autocast the new attention and action embeddings give
    the old code's log-probabilities to within bfloat16 rounding; in
    float32, exactly."""
    from selfplay.benchmark import legacy, positions
    from selfplay.model import PolicyNet

    torch.manual_seed(0)
    model = PolicyNet(64, 2).eval()
    old = legacy(model).eval()
    ints, mons, field, masks, dec = positions(256)
    with torch.no_grad():
        a, va = model(ints, mons, field, masks, dec)
        b, vb = old(ints, mons, field, masks, dec)
        legal = masks.bool()
        assert (a - b)[legal].abs().max() < 1e-4 and (va - vb).abs().max() < 1e-5
        with torch.autocast("cpu", dtype=torch.bfloat16):
            a, va = model(ints, mons, field, masks, dec)
            b, vb = old(ints, mons, field, masks, dec)
        assert (a - b)[legal].abs().mean() < 0.02 and (va - vb).abs().max() < 0.05


def test_learning_rate_warms_up_after_each_start_then_follows_the_schedule():
    from selfplay.train import Config, scheduled_lr
    c = Config(lr=2e-4, lr_final=2e-5, lr_warmup=60)
    assert scheduled_lr(c, 0, 1000, 0) == pytest.approx(2e-5)            # 10% at a fresh start
    assert scheduled_lr(c, 30, 1000, 30) == pytest.approx(0.55 * (2e-4 - 1.8e-4 * 0.03))
    assert scheduled_lr(c, 500, 1000, 600) == pytest.approx(1.1e-4)      # the plain schedule
    assert scheduled_lr(c, 500, 1000, 0) == pytest.approx(1.1e-5)        # a resume warms up again
    assert scheduled_lr(Config(lr=3e-4), 10, 100, 0) == 3e-4             # neither: constant


def test_kickstarting_pulls_the_policy_toward_the_teacher(tmp_path):
    """PPO with a teacher KL: the weight fades over the schedule, the KL is
    logged, and with a large weight the student moves toward the teacher."""
    from dataclasses import asdict

    from selfplay.train import kickstart_weight, policy_kl
    torch.manual_seed(123)
    teacher = PolicyNet(16, 1)
    with torch.no_grad():  # a decided teacher, far from the student's near-uniform start
        for prm in teacher.policy_head.parameters():
            prm.mul_(8)
    path = tmp_path / "teacher.pt"
    torch.save({"model": teacher.state_dict(), "config": asdict(Config(d=16, layers=1))}, path)
    c = Config(kickstart=str(path), kickstart_coef=2.0, kickstart_minutes=100)
    assert kickstart_weight(c, 0) == 2.0 and kickstart_weight(c, 50) == 1.0 and kickstart_weight(c, 150) == 0.0
    assert kickstart_weight(Config(), 0) == 0.0

    def trained_gap(kick: bool, out):
        cfg = Config(envs=16, steps=16, d=16, layers=1, league_frac=0.0, minibatch=256, epochs=2, lr=3e-3,
                     kickstart=str(path) if kick else "", kickstart_coef=50.0, kickstart_minutes=1e6)
        t = Trainer(cfg, out)
        obs = t.env.observe()
        rows = np.flatnonzero(obs.decisions.reshape(-1) != 0)
        batch = to_tensors(obs, rows)
        legal = batch[3].bool()

        def gap():
            with torch.no_grad():
                return policy_kl(teacher(*batch)[0], t.model(*batch)[0], legal).mean().item()

        before = gap()
        t.run(minutes=60, max_updates=3)
        return before, gap()

    before, kicked = trained_gap(True, tmp_path / "kick")
    entries = [json.loads(line) for line in (tmp_path / "kick" / "log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert all("teacher_kl" in e and 49 < e["kickstart"] <= 50 for e in entries)
    _, plain = trained_gap(False, tmp_path / "plain")
    print("gap to teacher: start %.4f, kickstarted %.4f, plain %.4f" % (before, kicked, plain))
    assert kicked < 0.8 * before and kicked < 0.8 * plain, (before, kicked, plain)
