"""Experiment grid: local-only vs FedAvg vs FedProx vs DP-FedAvg (+SecAgg, +fine-tuning)
on simulated non-IID homes, across seeds. Results are SIMULATED, not from devices.

    python -m sentinel_fl.experiment                 # full grid (3 seeds), writes results/
    python -m sentinel_fl.experiment --quick         # small smoke run
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from .dp import epsilon_pld
from .fed import FedConfig, evaluate, run_federated
from .head import TrainConfig
from .homes import WorldConfig, make_world

RESULTS = Path(__file__).resolve().parent.parent / "results"


def grid(rounds: int, q: float) -> list[FedConfig]:
    base = FedConfig(algo="fedavg", rounds=rounds, q=q, local=TrainConfig(epochs=5, lr=0.1, l2=1e-3))
    prox = replace(base, algo="fedprox", local=replace(base.local, prox_mu=0.5))
    dp = lambda s: replace(base, clip=1.0, noise_multiplier=s)  # noqa: E731
    return [
        replace(base, algo="local"),
        base,
        prox,
        replace(base, finetune_epochs=5),
        replace(base, clip=1.0),
        dp(0.5),
        dp(1.0),
        dp(2.0),
        replace(dp(1.0), secagg=True, dropout=0.1),  # SecAgg with 10% of each cohort dropping mid-round
        replace(dp(1.0), finetune_epochs=5),
    ]


def _one(args):
    seed, n_homes, cfg = args
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    world = make_world(n_homes, seed, WorldConfig())
    t0 = time.time()
    models, server = run_federated(world, cfg, seed)
    m = evaluate(models, world)
    eps = server.epsilon() if cfg.dp else math.inf
    return {
        "seed": seed, "label": cfg.label(), "summary": m.summary(), "eps": eps,
        "secs": time.time() - t0,
        "per_home": {"far": m.far.tolist(), "recall": m.recall.tolist(), "auc": m.auc.tolist(),
                     "n_train": m.n_train.tolist(), "quirk": m.quirk.tolist()},
        "survivors_mean": float(np.mean([r.survivors for r in server.log])) if server.log else 0.0,
    }


def _fmt(vals: list[float], pct: bool = True) -> str:
    a = np.array(vals, dtype=float)
    k = 100 if pct else 1
    return f"{k * a.mean():.1f} ± {k * a.std(ddof=1 if len(a) > 1 else 0):.1f}" if pct else f"{a.mean():.3f} ± {a.std(ddof=1 if len(a) > 1 else 0):.3f}"


def table(rows: list[dict], cfgs: list[FedConfig], n_homes: int, delta: float) -> str:
    by = {}
    for r in rows:
        by.setdefault(r["label"], []).append(r)
    head = (f"| Method | ε (δ={delta:g}) RDP / PLD | False-alert rate % | Person recall % | AUC | FAR %, homes <20 labels "
            "| Recall %, homes <20 labels | FAR %, homes with quirk | FAR %, worst-decile home |")
    lines = [head, "|" + "---|" * 9]
    for c in cfgs:
        rs = by[c.label()]
        s = [r["summary"] for r in rs]
        if c.dp:
            eps = f"{rs[0]['eps']:.2f} / {epsilon_pld(c.q, c.noise_multiplier, c.rounds, delta):.2f}"
        else:
            eps = "∞ (no DP)"
        lines.append("| " + " | ".join([
            c.label(), eps,
            _fmt([x["far_mean"] for x in s]), _fmt([x["recall_mean"] for x in s]),
            _fmt([x["auc_mean"] for x in s], pct=False),
            _fmt([x["far_small"] for x in s]), _fmt([x["recall_small"] for x in s]),
            _fmt([x["far_quirk"] for x in s]), _fmt([x["far_p90"] for x in s]),
        ]) + " |")
    return "\n".join(lines)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--homes", type=int, default=300)
    p.add_argument("--rounds", type=int, default=100)
    p.add_argument("--q", type=float, default=0.1)
    p.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1))
    p.add_argument("--quick", action="store_true", help="2 seeds, 60 homes, 20 rounds")
    p.add_argument("--out", type=Path, default=RESULTS)
    a = p.parse_args(argv)
    if a.quick:
        a.seeds, a.homes, a.rounds = 2, 60, 20
    cfgs = grid(a.rounds, a.q)
    jobs = [(seed, a.homes, c) for seed in range(a.seeds) for c in cfgs]
    t0 = time.time()
    with ProcessPoolExecutor(a.workers) as ex:
        rows = list(ex.map(_one, jobs))
    md = table(rows, cfgs, a.homes, cfgs[0].delta)
    a.out.mkdir(parents=True, exist_ok=True)
    meta = {"seeds": a.seeds, "homes": a.homes, "rounds": a.rounds, "q": a.q, "world": asdict(WorldConfig()),
            "configs": [dict(label=c.label(), **{k: v for k, v in asdict(c).items() if k != "local"},
                             local=asdict(c.local)) for c in cfgs],
            "wall_secs": time.time() - t0}
    (a.out / "results.json").write_text(json.dumps({"meta": meta, "rows": [{k: v for k, v in r.items() if k != "per_home"} for r in rows]}, indent=1))
    with open(a.out / "per_home.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["seed", "method", "home", "n_train", "has_quirk", "false_alert_rate", "person_recall", "auc"])
        for r in rows:
            ph = r["per_home"]
            for i in range(len(ph["far"])):
                w.writerow([r["seed"], r["label"], i, ph["n_train"][i], int(ph["quirk"][i]),
                            f"{ph['far'][i]:.4f}", f"{ph['recall'][i]:.4f}", f"{ph['auc'][i]:.4f}"])
    header = (f"SIMULATED results: {a.homes} synthetic homes, {a.rounds} rounds, Poisson client sampling "
              f"q={a.q} (~{a.q * a.homes:.0f} homes/round), mean ± std over {a.seeds} seeds; per-home metrics on "
              f"held-out test events at threshold 0.5. Wall time {meta['wall_secs']:.0f} s.\n\n")
    (a.out / "results.md").write_text(header + md + "\n")
    print(header + md)


if __name__ == "__main__":
    main()
