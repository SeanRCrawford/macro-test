"""How passively does the pilot play, and does it see the pins?

The report that started this: against Pelipper / Mega Swampert our side made
6 switches and 8 protects across 11 turns, to their 1 switch, and lost 0-4 --

    "Far too many switches and protects into a winning match up (too many
     predicts, not necessarily safe)"

So the thing to measure is not the win rate alone but the SHAPE of the line:
how many of our turns spend the turn doing nothing to them. A line that wins by
protecting eight times is still a line that has not understood the position.

Usage:
    python tools/measure_pin.py                 # the reported matchup
    python tools/measure_pin.py --all           # every lead pair from the two sixes
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))

from _harness import load_world, setup_battle  # noqa: E402

OURS = ["Mega Charizard Y", "Garchomp", "Mega Aerodactyl", "Farigiraf"]
THEIRS = ["Pelipper", "Mega Swampert", "Archaludon", "Grimmsnarl"]


def count_actions(line):
    """Passive slots per side, counted from the recorded action kinds.

    Not from the prose: an attack and a switch both render with an arrow
    ("Solar Beam -> Swampert" against "Garchomp -> Farigiraf"), so counting
    arrows reports every attacking turn as a switch.
    """
    ours = {"protect": 0, "switch": 0, "attack": 0}
    theirs = {"protect": 0, "switch": 0, "attack": 0}
    for turn in line["turns"]:
        for key, bucket in (("kinds", ours), ("their_kinds", theirs)):
            for kind in turn.get(key) or []:
                bucket[kind] = bucket.get(kind, 0) + 1
    return ours, theirs


def pins_at_start(our_bring, their_bring, world):
    """What the Pin module sees before a move is made."""
    import pin
    from threat import build_threat_matrix
    battle, movesets = setup_battle(list(our_bring), list(their_bring), world)
    return pin.describe(build_threat_matrix(battle, movesets), battle, "p1")


def run(our_bring, their_bring, world, max_turns=16, win_samples=8):
    from preview_lead import line_for
    line = line_for(our_bring, their_bring, world, max_turns=max_turns,
                    win_samples=win_samples)
    line["their_lead"] = list(their_bring[:2])
    return line


def report(our_bring, their_bring, world):
    print(f"\n=== {' / '.join(our_bring[:2])}  vs  {' / '.join(their_bring[:2])} ===")
    for text in pins_at_start(our_bring, their_bring, world):
        print("  pin:", text)
    line = run(our_bring, their_bring, world)
    ours, theirs = count_actions(line)
    prob = line.get("win_prob")
    print(f"  outcome {line['outcome']} in {line['length']} turns"
          + (f", win {prob:.0%}" if prob is not None else ""))
    print(f"  ours   protect {ours['protect']:2d}  switch {ours['switch']:2d}"
          f"  attack {ours['attack']:2d}")
    print(f"  theirs protect {theirs['protect']:2d}  switch {theirs['switch']:2d}"
          f"  attack {theirs['attack']:2d}")
    for turn in line["turns"][:12]:
        print(f"    T{turn['turn']}: {turn['play']}")
        if turn["their_reply"]:
            print(f"          they: {turn['their_reply']}")
    return line, ours, theirs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="every lead pair, not just the reported one")
    args = ap.parse_args()

    world = load_world()
    if not args.all:
        report(OURS, THEIRS, world)
        return

    import itertools
    totals = {"protect": 0, "switch": 0, "attack": 0}
    for our_lead in itertools.combinations(OURS, 2):
        bring = list(our_lead) + [x for x in OURS if x not in our_lead]
        _line, ours, _theirs = report(bring, THEIRS, world)
        for key in totals:
            totals[key] += ours[key]
    print("\nTOTAL ours:", totals)


if __name__ == "__main__":
    main()
