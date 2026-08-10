"""Tests for src/prescreen.py -- cheap candidate filtering before the sweep.

A prescreen that eliminates the eventual winner is worse than no prescreen: the
search gets faster and silently wrong, because the discarded candidate never
appears in the output. These pin the properties that keep it honest; the recall
question ("does it drop candidates the full sweep would pick") is answered by
tools/measure_prescreen.py, which is a measurement rather than a test.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from _harness import load_world  # noqa: E402
from prescreen import coverage_score, keep_top, rank_candidates  # noqa: E402

_WORLD = None


def world():
    global _WORLD
    if _WORLD is None:
        _WORLD = load_world()
    return _WORLD


def candidates(w, team):
    roster = list(w["teams"][team])
    return [roster[:4], roster[1:5], roster[2:6], [roster[0]] + roster[3:6]]


def test_scores_are_finite_and_comparable():
    w = world()
    names = list(w["teams"])
    enemy = list(w["teams"][names[1]])
    for cand in candidates(w, names[0]):
        s = coverage_score(cand, enemy, w["merged"], w["moves"], w["natures"],
                           w["typechart"])
        assert s == s and abs(s) < 1e6, s


def test_ranking_is_sorted_best_first():
    w = world()
    names = list(w["teams"])
    ranked = rank_candidates(candidates(w, names[0]), list(w["teams"][names[1]]),
                             w["merged"], w["moves"], w["natures"], w["typechart"])
    scores = [s for _c, s in ranked]
    assert scores == sorted(scores, reverse=True), scores


def test_keep_top_is_a_no_op_unless_asked():
    """A prescreen silently discarding candidates must be opted into."""
    w = world()
    names = list(w["teams"])
    cands = candidates(w, names[0])
    enemy = list(w["teams"][names[1]])
    for disabled in (0, None):
        assert keep_top(cands, enemy, w["merged"], w["moves"], w["natures"],
                        w["typechart"], disabled) == cands


def test_keep_top_returns_everything_when_width_exceeds_the_pool():
    w = world()
    names = list(w["teams"])
    cands = candidates(w, names[0])
    kept = keep_top(cands, list(w["teams"][names[1]]), w["merged"], w["moves"],
                    w["natures"], w["typechart"], len(cands) + 10)
    assert len(kept) == len(cands)


def test_keep_top_keeps_the_highest_scoring():
    w = world()
    names = list(w["teams"])
    cands = candidates(w, names[0])
    enemy = list(w["teams"][names[1]])
    ranked = rank_candidates(cands, enemy, w["merged"], w["moves"], w["natures"],
                             w["typechart"])
    kept = keep_top(cands, enemy, w["merged"], w["moves"], w["natures"],
                    w["typechart"], 2)
    assert len(kept) == 2
    assert kept[0] == ranked[0][0]


def test_an_unbuildable_candidate_ranks_last_rather_than_raising():
    """One bad candidate must not kill an overnight search."""
    w = world()
    names = list(w["teams"])
    cands = candidates(w, names[0]) + [["Definitely Not A Pokemon"] * 4]
    ranked = rank_candidates(cands, list(w["teams"][names[1]]), w["merged"],
                             w["moves"], w["natures"], w["typechart"])
    assert ranked[-1][0] == ["Definitely Not A Pokemon"] * 4
    assert ranked[-1][1] == float("-inf")


def test_no_tier_enables_the_prescreen():
    """Measured recall is 4-15%: enabling it deletes the winner most of the time.

    This asserts the negative result stays enforced, so a future change cannot
    quietly switch on a filter that makes the search faster and wrong.
    """
    from search_effort import TIERS
    for name, settings in TIERS.items():
        assert not settings.get("prescreen"), (
            f"tier {name} enables the prescreen; measured recall is 4-15%")


def test_coverage_is_blind_to_lead_order():
    """The diagnosis, pinned: this is WHY recall is so low.

    The sweep ranks by fast_pair_score, which reads only the lead pair; this
    score is a set-based matching that cannot distinguish lead orderings at all.
    """
    w = world()
    names = list(w["teams"])
    four = list(w["teams"][names[0]])[:4]
    enemy = list(w["teams"][names[1]])
    swapped = [four[1], four[0], four[2], four[3]]
    a = coverage_score(four, enemy, w["merged"], w["moves"], w["natures"],
                       w["typechart"])
    b = coverage_score(swapped, enemy, w["merged"], w["moves"], w["natures"],
                       w["typechart"])
    assert abs(a - b) < 1e-9, "unexpectedly lead-aware -- re-measure recall"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {t.__name__}: {exc}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
