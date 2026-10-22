"""The team builder: variants, type rules, scoring, and exact game counts."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("selfplay._engine")

from selfplay.build import (Arena, Pool, from_core, from_paste, legal, load_start, neighbours,  # noqa: E402
                            objective, play, preview_matrix, rule_breaks, team_paste, type_profile)
from selfplay.env import DEFAULT_CORPUS  # noqa: E402
from selfplay.model import PolicyNet  # noqa: E402
from selfplay.showdown.data import to_id  # noqa: E402

SEED = "lovejapanfrombr's Salamence Sand Team"


@pytest.fixture(scope="module")
def pool():
    return Pool()


def test_pastes_round_trip_and_the_seed_breaks_the_type_rules(pool):
    team = load_start(SEED)
    assert from_paste(team_paste(team)) == team
    assert legal(team, pool)
    prof = type_profile(team, pool)
    assert prof["Fire"] == (3, 3) and prof["Ground"] == (3, 2)
    assert rule_breaks(team, pool) == ["2 types hit 3+ members (Fire, Ground)"]
    assert rule_breaks(team, pool, rules=False) == []


def test_variants_keep_the_core_and_walk_into_the_rules(pool):
    team = load_start(SEED)
    rng = np.random.default_rng(0)
    keep = {"tyranitar", "excadrill"}
    kinds = set()
    for t in neighbours(team, pool, rng, 60, keep, rules=True):
        assert keep <= {to_id(m.species) for m in t}
        assert legal(t, pool)
        assert len(rule_breaks(t, pool)) <= 1  # the seed breaks one rule; a variant may not break more
        changed = [a.species == b.species for a, b in zip(team, t) if a != b]
        assert len(changed) == 1
        kinds.add("set" if changed[0] else "swap")
    # Not only swaps that fix the rules: the sets can change too.
    assert kinds == {"set", "swap"}
    free = neighbours(team, pool, rng, 30, keep, rules=False)
    assert len(free) == 30 and len({t for t in free}) == 30


def test_a_team_from_the_core_is_legal_and_within_the_rules(pool):
    team = from_core(["Tyranitar", "Excadrill"], pool, np.random.default_rng(0), True)
    names = [to_id(m.species) for m in team]
    assert names[:2] == ["tyranitar", "excadrill"] and len(set(names)) == 6
    assert legal(team, pool) and not rule_breaks(team, pool)


def test_the_score_blends_the_mean_with_the_worst_matchups():
    p = np.array([[0.9, 0.9, 0.1, 0.5], [0.6, 0.6, 0.6, 0.6]])
    w = np.array([1.0, 1.0, 1.0, 1.0])
    score, mean, worst = objective(p, w, tail=0.5, alpha=0.25)
    assert np.allclose(mean, [0.6, 0.6]) and np.allclose(worst, [0.1, 0.6])
    assert score[1] > score[0]
    # A weight split across the cut: the worst half of [0.1, 0.5] by weight.
    _, _, worst = objective(np.array([[0.1, 0.5, 0.9]]), np.array([1.0, 2.0, 1.0]), 0.0, 0.5)
    assert np.isclose(worst[0], (0.25 * 0.1 + 0.25 * 0.5) / 0.5)


def test_every_scheduled_game_counts_once_and_previews_are_read(pool):
    torch.manual_seed(0)
    model = PolicyNet(d=32, layers=1)
    team = load_start(SEED)
    arena = Arena(DEFAULT_CORPUS, [team], envs=16, seed=3)
    try:
        me = arena.index[0]
        assert me is not None
        field = [0, 1, 2]
        est = preview_matrix(model, arena, [me], field, amp=False)
        assert est.shape == (1, 3) and ((0 <= est) & (est <= 1)).all()
        # Fewer games than the env runs at once: each is played and counted
        # exactly once, in the schedule's order.
        pairs = [(me, 0), (1, me), (me, 2), (2, me), (me, 1)]
        res = play(model, arena.env, pairs, amp=False)
        assert [(a, b) for a, b, *_ in res] == pairs
        assert all(r[2] in (0.0, 0.5, 1.0) for r in res)
        assert all(len(r[3]) == 4 and len(r[4]) == 4 and r[3].isdigit() for r in res)
    finally:
        arena.close()
