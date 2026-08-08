"""Does the threat-matrix prescreen drop candidates the full search would pick?

A prescreen that eliminates the eventual winner is worse than no prescreen: it
makes the search faster and wrong, and the wrongness is invisible because the
discarded candidate never appears in the output. So before using it, measure
its RECALL -- of the brings the full sweep ranks best, how many survive the
cheap filter.

Reported at several filter widths, because the width is the knob that trades
speed for safety and the right value is a measurement rather than a guess:

    keep    how many candidates the prescreen lets through
    recall  share of the full sweep's top-3 brings that survived
    saved   share of candidates eliminated (the speed win)

A prescreen is only worth enabling at a width where recall is 100%, or close
enough that the misses are candidates the sweep ranked marginally.
"""
import argparse
import itertools
import sys

from _harness import load_world

sys.path.insert(0, "../src")
from matchup_search import search_robust_composition  # noqa: E402
from prescreen import rank_candidates  # noqa: E402

WIDTHS = (3, 5, 8, 12)


def our_configs(pool6):
    """Every bring-4 / lead-2 arrangement, as search_robust_composition builds them."""
    seen, out = set(), []
    for bring4 in itertools.combinations(pool6, 4):
        for lead2 in itertools.combinations(bring4, 2):
            back2 = tuple(x for x in bring4 if x not in lead2)
            key = tuple(list(lead2) + list(back2))
            if key not in seen:
                seen.add(key)
                out.append(list(key))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=8)
    ap.add_argument("--top", type=int, default=3,
                    help="how many of the full sweep's best count as 'the answer'")
    args = ap.parse_args()

    world = load_world()
    names = list(world["teams"])
    hits = {w: 0 for w in WIDTHS}
    totals = 0
    pairings = 0

    for ours in names:
        for theirs in names:
            if ours == theirs:
                continue
            pool = list(world["teams"][ours])
            enemy = list(world["teams"][theirs])

            full = search_robust_composition(
                pool, enemy, world["merged"], world["moves"], world["natures"],
                world["typechart"], max_turns=args.turns, verify_top=args.top)
            if not full:
                continue
            wanted = [tuple(r["our_bring4"]) for r in full[:args.top]]

            ranked = rank_candidates(our_configs(pool), enemy, world["merged"],
                                     world["moves"], world["natures"],
                                     world["typechart"])
            order = [tuple(c) for c, _s in ranked]
            for width in WIDTHS:
                survivors = set(order[:width])
                hits[width] += sum(1 for w in wanted if w in survivors)
            totals += len(wanted)
            pairings += 1
            print(f"  {ours} vs {theirs}", flush=True)

    if not totals:
        print("nothing measured")
        return

    n_candidates = len(our_configs(list(world["teams"][names[0]])))
    print(f"\n===== prescreen recall over {pairings} pairings =====")
    print(f"candidates per pairing: {n_candidates}")
    print(f"{'keep':>6}{'recall':>10}{'saved':>10}")
    for width in WIDTHS:
        recall = hits[width] / totals
        saved = 1 - width / n_candidates
        print(f"{width:>6}{100 * recall:>9.0f}%{100 * saved:>9.0f}%")
    print("\nUse the narrowest width that still recalls ~100%. Anything lower is")
    print("a search that is faster and silently wrong.")


if __name__ == "__main__":
    main()
