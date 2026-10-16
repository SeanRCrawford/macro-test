import json

from selfplay.summary import elo, read, summarize


def test_summary_pools_evaluations_and_drops_repeated_updates(tmp_path):
    log = tmp_path / "log.jsonl"
    lines = [{"distill_round": 1, "minutes": 10.0, "samples": 1, "kl": 0.1, "value_mse": 0.01,
              "opponent_kl": 0.1, "agreement": 0.9, "vs_teacher": 0.48}]
    entry = lambda u, s: {"update": u, "minutes": float(u), "games": 100 * u, "lr": 1e-4, "entropy": 1.0,
                          "value_loss": 0.3, "opponent_ce": 1.2, "games_per_s": 300.0, "vs_baseline": s,
                          "vs_greedy": 0.9}
    # Updates 3-4 were played, then the run resumed from update 2 and replayed them.
    lines += [entry(1, 0.5), entry(2, 0.5), entry(3, 0.0), entry(4, 0.0), entry(3, 0.7), entry(4, 0.7)]
    log.write_text("\n".join(json.dumps(e) for e in lines) + "\n", encoding="utf-8")
    distill, train = read(log)
    assert len(distill) == 1 and [e["update"] for e in train] == [1, 2, 3, 4]
    out = summarize(tmp_path, rows=2, last=2)
    assert out[-1].startswith("last 2 evaluations against the baseline: 0.700")
    assert round(elo(0.7)) == 147
