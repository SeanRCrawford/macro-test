"""Check the team corpus after adding pastes: how many load from each
folder, the sampling weight each placement gets, and every paste left out
with the reason (not six Pokemon, not Reg M-C legal, or a move, ability or
item the engine doesn't implement yet).

    python -m selfplay.corpus

Training samples teams by weight: the placement in a corpus paste's name
("... Top 8 Team.txt" 4, "Champion" 8, none 1); data/teams and
data/my_teams count 4 each.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict


def main():
    from selfplay.env import DEFAULT_CORPUS, SelfPlayEnv
    env = SelfPlayEnv(1)
    names, weights = env.team_names(), env.team_weights()
    folders = defaultdict(list)
    for n, w in zip(names, weights):
        folders[n.split("/")[0] + "/" if "/" in n else "corpus"].append(w)
    print("sources: " + ", ".join(DEFAULT_CORPUS))
    total = sum(weights)
    for f, ws in folders.items():
        by = Counter(ws)
        print(f"{f}: {len(ws)} teams, {sum(ws) / total:.1%} of the games; weights "
              + ", ".join(f"{w:g} x{k}" for w, k in sorted(by.items(), reverse=True)))
    rejected = env.rejected()
    print(f"\n{len(rejected)} left out" + (":" if rejected else ""))
    missing = Counter()
    for name, why in rejected:
        print(f"  {name}: {why}")
        m = re.match(r"unsupported: (.*)", why)
        if m:
            missing.update(x.strip() for x in m.group(1).split(","))
    if missing:
        print("\nnot implemented yet, by teams that need it: "
              + ", ".join(f"{k} ({v})" for k, v in missing.most_common()))


if __name__ == "__main__":
    main()
