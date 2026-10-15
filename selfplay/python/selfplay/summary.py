"""A training run's log in a screenful: the distillation's progress, then
the run cut into equal stretches of time, each with its evaluations
pooled (24 evaluations of 400 games measure a score to about +-0.005,
one alone to +-0.025).

    python -m selfplay.summary runs/ppo5

The last line pools the final evaluations: the run's score against its
baseline, with its Elo difference.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np


def elo(score: float) -> float:
    s = min(max(score, 1e-4), 1 - 1e-4)
    return 400 * math.log10(s / (1 - s))


def read(path: Path) -> tuple[list[dict], list[dict]]:
    """Distillation entries, and training entries; a resumed run's repeated
    updates (since its last saved model) replace the earlier ones."""
    distill, train = [], []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        if "distill_round" in e:
            distill.append(e)
        elif "update" in e:
            while train and train[-1]["update"] >= e["update"]:
                train.pop()
            train.append(e)
    return distill, train


def pooled(entries: list[dict], key: str) -> tuple[float, int]:
    vals = [e[key] for e in entries if key in e]
    return (float(np.mean(vals)), len(vals)) if vals else (float("nan"), 0)


def summarize(run: Path, rows: int = 16, last: int = 10, eval_games: int = 400) -> list[str]:
    distill, train = read(run / "log.jsonl" if run.is_dir() else run)
    out = []
    if distill:
        evals = [e for e in distill if "vs_teacher" in e]
        out.append(f"distillation: {len(distill)} rounds, {distill[-1]['minutes']:.0f} min")
        out.append(f"{'min':>6s} {'agreement':>9s} {'kl':>7s} {'value_mse':>9s} {'opp_kl':>7s} {'vs_teacher':>10s}")
        for e in evals or distill[-1:]:
            out.append(f"{e['minutes']:6.0f} {e['agreement']:9.3f} {e['kl']:7.3f} {e['value_mse']:9.3f} "
                       f"{e['opponent_kl']:7.3f} {e.get('vs_teacher', float('nan')):10.3f}")
        out.append("")
    if not train:
        return out or ["no training entries"]
    span = train[-1]["minutes"]
    edges = np.linspace(train[0]["minutes"] - 1e-9, span, rows + 1)
    out.append(f"training: {train[-1]['update']} updates, {train[-1]['minutes']:.0f} min, "
               f"{train[-1]['games'] / 1e6:.1f}M games")
    out.append(f"{'min':>6s} {'games':>7s} {'g/s':>5s} {'lr':>8s} {'entropy':>7s} {'v_loss':>6s} "
               f"{'opp_ce':>6s} {'vs_baseline':>11s} {'(Elo)':>6s} {'n':>3s} {'vs_greedy':>9s} "
               f"{'gpu_W':>5s} {'vram':>5s}")
    for lo, hi in zip(edges[:-1], edges[1:]):
        group = [e for e in train if lo < e["minutes"] <= hi]
        if not group:
            continue
        e = group[-1]
        base, n = pooled(group, "vs_baseline")
        greedy, _ = pooled(group, "vs_greedy")
        gps, _ = pooled(group, "games_per_s")
        watts, _ = pooled(group, "gpu_w")
        vram = max((g.get("vram_used_gb", float("nan")) for g in group), default=float("nan"))
        out.append(f"{e['minutes']:6.0f} {e['games'] / 1e6:6.1f}M {gps:5.0f} {e['lr']:8.2e} "
                   f"{np.mean([g['entropy'] for g in group]):7.3f} "
                   f"{np.mean([g['value_loss'] for g in group]):6.3f} "
                   f"{np.mean([g['opponent_ce'] for g in group]):6.3f} "
                   f"{base:11.3f} {elo(base) if n else float('nan'):+6.0f} {n:3d} {greedy:9.3f} "
                   f"{watts:5.1f} {vram:5.2f}")
    evals = [e for e in train if "vs_baseline" in e][-last:]
    if evals:
        s, n = pooled(evals, "vs_baseline")
        se = math.sqrt(s * (1 - s) / (n * eval_games))
        out.append(f"last {n} evaluations against the baseline: {s:.3f} +-{se:.3f} "
                   f"({n * eval_games} games), {elo(s):+.0f} Elo "
                   f"({elo(s - 2 * se):+.0f} to {elo(s + 2 * se):+.0f})")
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("run", help="a run's folder (or its log.jsonl)")
    p.add_argument("--rows", type=int, default=16, help="stretches of time to cut the run into")
    p.add_argument("--last", type=int, default=10, help="final evaluations to pool")
    p.add_argument("--eval-games", type=int, default=400, help="games per evaluation (train's --eval-games)")
    a = p.parse_args()
    print("\n".join(summarize(Path(a.run), a.rows, a.last, a.eval_games)))


if __name__ == "__main__":
    main()
