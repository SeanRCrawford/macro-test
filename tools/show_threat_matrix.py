"""Print the threat matrix and answer map for a matchup.

Doubles as the inspection tool for the "answer map" UI feature and as the
sanity check that the numbers are believable -- a matrix nobody has ever looked
at is a matrix nobody should trust.
"""
import sys
import time

from _harness import enemy_bring, load_world, setup_battle

sys.path.insert(0, "../src")
from threat import build_threat_matrix  # noqa: E402

OUR_TEAM = ["Incineroar", "Farigiraf", "Gallade", "Hydreigon"]


def main():
    world = load_world()
    team_name = sys.argv[1] if len(sys.argv) > 1 else list(world["teams"])[0]
    enemy4 = enemy_bring(team_name, world)
    battle, movesets = setup_battle(OUR_TEAM, enemy4, world)

    t0 = time.perf_counter()
    T = build_threat_matrix(battle, movesets)
    build_s = time.perf_counter() - t0

    print(f"=== {team_name}: {OUR_TEAM} vs {enemy4}")
    print(f"built {len(T.ours)}x{len(T.theirs)} matrix "
          f"({2 * len(T.ours) * len(T.theirs)} directed edges) in {build_s * 1000:.1f} ms\n")

    print("--- our best move into each of theirs (avg dmg %, KO, speed)")
    header = f"{'':<14}" + "".join(f"{t.name[:13]:>15}" for t in T.theirs)
    print(header)
    for o in T.ours:
        cells = []
        for t in T.theirs:
            th = T.threat(o, t)
            ko = "OHKO" if th.ohko else ("ohko?" if th.ohko_possible else f"{th.nhko}HKO")
            spd = ">" if th.outspeeds else "<"
            cells.append(f"{100 * th.dmg_avg:5.0f}% {ko:>5}{spd}")
        print(f"{o.name[:13]:<14}" + "".join(f"{c:>15}" for c in cells))

    print("\n--- answer map: who handles what (handles_score, higher = better for us)")
    for t in T.theirs:
        ranked = T.answers_to(t)
        best = ", ".join(f"{m.name} {s:+.0f}" for m, s in ranked[:3])
        threats = [a.name for a in T.pressured_by(t)]
        print(f"  vs {t.name:<20} best: {best}")
        if threats:
            print(f"     {'':<20} it is pressured by: {', '.join(threats)}")

    print("\n--- focus fire: pairs of ours that together KO one of theirs")
    any_joint = False
    for t in T.theirs:
        for i, a1 in enumerate(T.ours):
            for a2 in T.ours[i + 1:]:
                solo = T.threat(a1, t).ohko_possible or T.threat(a2, t).ohko_possible
                if T.joint_kos(a1, a2, t) and not solo:
                    print(f"  {a1.name} + {a2.name} -> {t.name}"
                          f"  (neither OHKOs alone)")
                    any_joint = True
    if not any_joint:
        print("  (none that a pairwise matrix would have missed here)")


main()
