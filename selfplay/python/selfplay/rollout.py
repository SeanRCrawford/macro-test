"""Random-play rollouts through the Python API: phase 2's exit test
(DESIGN.md 3) and a throughput measurement.

    python -m selfplay.rollout --games 1000000 --envs 1024
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from selfplay.env import SelfPlayEnv


def rollout(games: int, envs: int, seed: int = 0, threads: int = 0, perfect_info: bool = True,
            report_every: float = 10.0) -> dict:
    env = SelfPlayEnv(envs, seed=seed, threads=threads, perfect_info=perfect_info)
    rng = np.random.default_rng(seed)
    finished = wins = draws = turns = decisions = 0
    start = last = time.perf_counter()
    while finished < games:
        obs = env.observe()
        decisions += int((obs.decisions != 0).sum())
        r = env.step(env.random_actions(int(rng.integers(1 << 62))))
        done = r.done.astype(bool)
        if done.any():
            finished += int(done.sum())
            wins += int((r.reward[done, 0] > 0).sum())
            draws += int((r.reward[done, 0] == 0).sum())
            turns += int(r.turns[done].sum())
        now = time.perf_counter()
        if report_every and now - last >= report_every:
            last = now
            el = now - start
            print(f"{finished:>9} games  {finished / el:8.0f} games/s  {turns / el:9.0f} turns/s", flush=True)
    elapsed = time.perf_counter() - start
    return {
        "games": finished,
        "seconds": elapsed,
        "games_per_s": finished / elapsed,
        "turns_per_s": turns / elapsed,
        "decisions_per_s": decisions / elapsed,
        "mean_turns": turns / max(finished, 1),
        "side0_win_rate": wins / max(finished, 1),
        "draw_rate": draws / max(finished, 1),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--games", type=int, default=100_000)
    p.add_argument("--envs", type=int, default=1024)
    p.add_argument("--threads", type=int, default=0, help="0 = all cores")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--hidden", action="store_true", help="Open Team Sheets view (not perfect info)")
    p.add_argument("--report-every", type=float, default=10.0, help="seconds between progress lines (0: none)")
    a = p.parse_args()
    stats = rollout(a.games, a.envs, a.seed, a.threads, not a.hidden, a.report_every)
    for k, v in stats.items():
        print(f"{k:>16}: {v:.4g}" if isinstance(v, float) else f"{k:>16}: {v}")


if __name__ == "__main__":
    main()
