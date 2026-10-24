"""The team builder: variants, type rules, scoring, and exact game counts."""
import numpy as np
import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("selfplay._engine")

from selfplay.build import (Arena, Member, Pool, canon, from_core, from_paste, legal, load_start,  # noqa: E402
                            mutate, neighbours, objective, play, preview_matrix, rule_breaks, rule_excess,
                            score_se, score_weights, team_paste, type_profile, within)
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
    assert rule_excess(team, pool) == {"4+ weak": 0, "3+ weak types": 1, "2 weak types": 0,
                                       "net +1 types": 0, "net +2": 0, "megas": 0}
    assert rule_breaks(team, pool, rules=False) == []


def test_a_fourth_weak_member_breaks_the_rules_further(pool):
    """The sand team has three members weak to Fire and three to Ground;
    Kingambit for Salamence makes four of each (and a third such type,
    Fighting): rules broken further, so not allowed."""
    team = load_start(SEED)
    kingambit = Member("Kingambit", "Black Glasses", "Defiant", "Adamant", (2, 32, 0, 0, 0, 32),
                       ("Kowtow Cleave", "Sucker Punch", "Iron Head", "Protect"))
    worse = tuple(kingambit if m.species == "Salamence" else m for m in team)
    prof = type_profile(worse, pool)
    assert prof["Ground"][0] == 4 and prof["Fire"][0] == 4
    assert rule_excess(worse, pool) == {"4+ weak": 2, "3+ weak types": 2, "2 weak types": 0,
                                        "net +1 types": 0, "net +2": 4, "megas": 0}
    assert "4+ members weak to Fire, Ground" in rule_breaks(worse, pool)
    assert not within(rule_excess(worse, pool), rule_excess(team, pool))
    assert within(rule_excess(team, pool), rule_excess(team, pool))


def test_variants_keep_the_core_and_walk_into_the_rules(pool):
    team = load_start(SEED)
    rng = np.random.default_rng(0)
    keep = {"tyranitar", "excadrill"}
    kinds = set()
    limit = rule_excess(team, pool)
    for t in neighbours(team, pool, rng, 300, keep, rules=True):
        assert keep <= {to_id(m.species) for m in t}
        assert legal(t, pool)
        # The seed breaks one rule: no variant breaks any rule further.
        assert within(rule_excess(t, pool), limit)
        assert max(w for w, _ in type_profile(t, pool).values()) <= 3
        changed = [a.species == b.species for a, b in zip(team, t) if a != b]
        assert 1 <= len(changed) <= 2
        kinds.add(("set" if changed[0] else "swap") if len(changed) == 1 else "two")
        for a, b in zip(team, t):
            if (a.species, a.item, a.ability, set(a.moves)) == (b.species, b.item, b.ability, set(b.moves)) \
                    and (a.nature, a.points) != (b.nature, b.points):
                # No spread nudged by a point or two (on its own).
                assert a.nature != b.nature or sum(abs(x - y) for x, y in zip(a.points, b.points)) >= 8
    # Not only swaps that fix the rules: sets change too, and two at once.
    assert kinds == {"set", "swap", "two"}


def test_joint_changes_swap_two_members_and_no_op_variants_are_dropped(pool):
    team = load_start(SEED)
    rng = np.random.default_rng(4)
    keep = {"tyranitar", "excadrill"}
    pairs = [t for t in (mutate(team, pool, rng, keep, "pair") for _ in range(40)) if t is not None]
    assert pairs
    for t in pairs:
        swapped = [b for a, b in zip(team, t) if a.species != b.species]
        assert len(swapped) == 2 and {to_id(m.species) for m in t} >= keep
        assert len({pool.d.base_of(to_id(m.species)) for m in t}) == 6
    # The same team with its moves in another order is no variant.
    shuffled = tuple(m.__class__(**{**m.__dict__, "moves": tuple(reversed(m.moves))}) for m in team)
    assert canon(shuffled) == canon(team) and shuffled != team
    free = neighbours(team, pool, rng, 30, keep, rules=False)
    assert len(free) == 30 and len({t for t in free}) == 30


def test_a_team_from_the_core_is_legal_and_within_the_rules(pool):
    team = from_core(["Tyranitar", "Excadrill"], pool, np.random.default_rng(0), True)
    names = [to_id(m.species) for m in team]
    assert names[:2] == ["tyranitar", "excadrill"] and len(set(names)) == 6
    assert legal(team, pool) and not rule_breaks(team, pool)
    # Its variants stay within every rule.
    for t in neighbours(team, pool, np.random.default_rng(1), 100, {"tyranitar", "excadrill"}, rules=True):
        assert not rule_breaks(t, pool)


def test_the_scores_error_counts_the_worst_matchups_weight():
    p = np.array([0.2, 0.5, 0.8, 0.6])
    w = np.ones(4)
    c = score_weights(p, w, tail=0.5, alpha=0.25)
    assert np.isclose(c.sum(), 1.0) and np.isclose(c[0], 0.5 * 0.25 + 0.5)
    n = np.full(4, 100.0)
    assert score_se(p, n, w, 0.5, 0.25) > score_se(p, n, w)  # the tail's one matchup is noisy


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


P1 = ("Charizard||CharizarditeY|Blaze|HeatWave,WeatherBall,SolarBeam,Protect|Modest||M|||50|]Sylveon||FairyFeather|"
      "Pixilate|HyperVoice,QuickAttack,HyperBeam,Detect|Modest||M|||50|]Kingambit||FocusSash|Defiant|KowtowCleave,"
      "IronHead,LowKick,SuckerPunch|Adamant||M|||50|]Farigiraf||SitrusBerry|ArmorTail|ExpandingForce,Thunderbolt,"
      "TrickRoom,Protect|Bold||M|||50|]Aerodactyl||Aerodactylite|Unnerve|RockSlide,DualWingbeat,Tailwind,Protect|"
      "Jolly||F|||50|]Garchomp||LifeOrb|RoughSkin|DragonClaw,Earthquake,Protect,StompingTantrum|Jolly||F|||50|")
P2 = ("Sneasler||WhiteHerb|Unburden|DireClaw,CloseCombat,RockSlide,Protect|Adamant||M|||50|]Floette-Eternal||"
      "Floettite|FlowerVeil|Moonblast,DazzlingGleam,LightofRuin,Protect|Timid||F|||50|]Pelipper||DampRock|Drizzle|"
      "WeatherBall,Hurricane,HelpingHand,WideGuard|Calm||M|||50|]Archaludon||Leftovers|Stamina|ElectroShot,"
      "DragonPulse,AuraSphere,Protect|Calm||M|||50|]Basculegion-F||FocusSash|SwiftSwim|MuddyWater,ShadowBall,IceBeam,"
      "Protect|Modest||F|||50|]Golisopod||Golisopite|EmergencyExit|LeechLife,SuckerPunch,IronHead,Protect|"
      "Adamant||F|||50|")


def test_the_arena_field_is_the_strong_bots_teams_weighted_by_battles(tmp_path):
    """From downloaded open-sheet battles: the top-rated bots' teams as
    pastes, one per distinct team, weighted by battles; the builder takes
    the folder as its field."""
    import json

    from selfplay.build import dir_field
    from selfplay.env import SelfPlayEnv
    from selfplay.showdown.arena_field import collect, strongest, write_field
    battles = tmp_path / "battles"
    battles.mkdir()
    entries = []
    for i, (a, b, win) in enumerate([("Top", "Mid", "p1"), ("Mid", "Top", "p1"), ("Low", "Top", "p2")], 1):
        entries.append({"id": i, "format": "gen9championsvgc2026regmcots", "winner": win,
                        "p1": {"bot": {"name": a}, "ratingAfter": {"Top": 1800, "Mid": 1600, "Low": 1300}[a]},
                        "p2": {"bot": {"name": b}, "ratingAfter": {"Top": 1800, "Mid": 1600, "Low": 1300}[b]}})
        sheet = {"Top": P1, "Mid": P2, "Low": P2}
        (battles / f"{i}.log").write_text(f"|init|battle\n|showteam|p1|{sheet[a]}\n|showteam|p2|{sheet[b]}\n|win|x\n")
    (battles / "battles.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    bots = strongest(entries, top=2, min_rating=None)
    assert list(bots) == ["Top", "Mid"]
    teams = collect(battles, bots)
    assert [(t["battles"], t["wins"]) for t in teams] == [(3, 2), (2, 1)]  # Top's team thrice; Mid's twice
    out = tmp_path / "field"
    assert write_field(teams, out, say=lambda *_: None, min_battles=3) == {"Top arena team 1": 3 ** 0.5}
    weights = write_field(teams, out, say=lambda *_: None, weight="battles")
    assert weights == {"Top arena team 1": 3, "Mid arena team 1": 2}
    text = (out / "Top arena team 1.txt").read_text()
    assert "Charizard @ Charizardite Y" in text and "Modest Nature" in text and "- Weather Ball" in text
    env = SelfPlayEnv(1, corpus=[DEFAULT_CORPUS[0], str(out)])  # after the corpus, as the builder loads it
    assert not [r for r in env.rejected() if r[0].startswith("field/")]
    field, w = dir_field(env.team_names(), out)
    assert len(field) == 2 and sorted(w) == [2.0, 3.0]
