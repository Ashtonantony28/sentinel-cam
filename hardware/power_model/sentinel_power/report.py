"""Generate power_model/out/report.md (+ JSON, optional PNGs)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .constants import POWER_MODEL_DIR, Constants, load_constants, load_scenarios
from .model import average_power, battery_life_hours, event_energy, standby_current_at_battery
from .montecarlo import rank_sensitivity, run_mc, tornado
from .plan import PLAN_ROWS, model_as_plan_rows, plan_table
from .units import HOURS_PER_MONTH

OUT_DIR = POWER_MODEL_DIR / "out"


def _mw(x) -> str:
    return f"{float(x) * 1e3:.3f}"


def analyse(const: Constants, scenarios: dict, n: int = 20000, seed: int = 1) -> dict:
    nom = const.nominal()
    result: dict = {"plan": plan_table(), "scenarios": {}}
    for name, sc in scenarios.items():
        ap = average_power(nom, sc)
        life_h = float(battery_life_hours(nom, sc, const.temp_derate))
        mc = run_mc(const, sc, n=n, seed=seed)
        result["scenarios"][name] = {
            "description": sc.get("description", ""),
            "mode": sc["mode"],
            "nominal_power_w": {k: float(v) for k, v in ap.items()},
            "nominal_standby_current_ua": float(standby_current_at_battery(nom, sc)) * 1e6,
            "nominal_life_months": life_h / HOURS_PER_MONTH,
            "mc": mc.summary(),
            "plan_rows_w": {k: float(v) for k, v in model_as_plan_rows(nom, sc).items()},
            "tornado": tornado(const, sc)[:12],
            "spearman": rank_sensitivity(mc)[:12],
            "event_energy_j": {k: {kk: float(vv) for kk, vv in event_energy(nom, k, sc["mode"]).items()} for k in ("person_known", "person_unknown", "false_wake", "button")},
            "_mc_months": mc.life_months,  # stripped before JSON
        }
    return result


def render_markdown(res: dict) -> str:
    L = []
    pt = res["plan"]
    L.append("# Sentinel power model report (generated: `make report`)\n")
    L.append("All numbers trace to `constants.yaml`; MC = Monte-Carlo over every constant's [low, high] range and over event counts.\n")
    L.append("## Plan section 4.4, reproduced exactly\n")
    L.append("| Row | Plan (mW) |\n|---|---|")
    for k in PLAN_ROWS:
        L.append(f"| {k} | {_mw(pt[k])} |")
    L.append(f"| **total** | **{_mw(pt['total'])}** |")
    L.append(f"\nLife = 18.5 Wh / {_mw(pt['total'])} mW = {pt['life_hours']:.0f} h = **{pt['life_months']:.2f} months** (the plan's \"~9 months\"; no derating).\n")

    s = res["scenarios"]["plan_mix_twt"]
    L.append("## Same rows from the sourced model (plan mix, TWT, nominal constants)\n")
    L.append("| Row | Plan (mW) | Model (mW) | Ratio |\n|---|---|---|---|")
    for k in list(PLAN_ROWS) + ["total"]:
        a, b = pt[k], s["plan_rows_w"][k]
        L.append(f"| {k} | {_mw(a)} | {_mw(b)} | {b / a:.2f}x |")
    L.append("")

    L.append("## Battery life by scenario\n")
    L.append("| Scenario | Mode | Standby I_bat (uA) | Avg P nominal (mW) | Life nominal (mo) | MC median (mo) | MC p5-p95 (mo) | P(>=6 mo) | P(>=12 mo) |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for name, s in res["scenarios"].items():
        mc = s["mc"]
        L.append(
            f"| {name} | {s['mode']} | {s['nominal_standby_current_ua']:.0f} | {_mw(s['nominal_power_w']['total'])} | "
            f"{s['nominal_life_months']:.1f} | {mc['median_months']:.1f} | {mc['p5_months']:.1f}-{mc['p95_months']:.1f} | "
            f"{mc['prob_ge_6_months']:.0%} | {mc['prob_ge_12_months']:.0%} |"
        )
    L.append("")
    for name in ("plan_mix_twt", "plan_mix_radio_off"):
        s = res["scenarios"][name]
        L.append(f"## Power breakdown: {name} (nominal)\n")
        L.append("| Component | mW | share |\n|---|---|---|")
        tot = s["nominal_power_w"]["total"]
        for k, v in sorted(s["nominal_power_w"].items(), key=lambda kv: -kv[1]):
            if k == "total" or v == 0:
                continue
            L.append(f"| {k} | {_mw(v)} | {v / tot:.1%} |")
        L.append(f"| **total** | **{_mw(tot)}** | |\n")
        L.append("Per-event energy (J, battery side): " + ", ".join(f"{k} {v['base'] + v['clip']:.2f}" for k, v in s["event_energy_j"].items()) + "\n")
        L.append(f"### Sensitivity: one-at-a-time swing ({name})\n")
        L.append("| Constant | life @low (mo) | life @high (mo) | swing (mo) |\n|---|---|---|---|")
        for r in s["tornado"][:10]:
            L.append(f"| {r['name']} | {r['life_at_low_h'] / HOURS_PER_MONTH:.1f} | {r['life_at_high_h'] / HOURS_PER_MONTH:.1f} | {r['swing_h'] / HOURS_PER_MONTH:.2f} |")
        L.append(f"\n### Sensitivity: Spearman rank correlation with life (MC, {name})\n")
        L.append("| Input | rho |\n|---|---|")
        for r in s["spearman"][:10]:
            L.append(f"| {r['name']} | {r['spearman']:+.2f} |")
        L.append("")
    return "\n".join(L) + "\n"


def plots(res: dict, out_dir: Path) -> list[Path]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return []
    paths = []
    fig, ax = plt.subplots(figsize=(7, 4))
    for name in ("plan_mix_twt", "plan_mix_radio_off", "busy_door_twt"):
        m = res["scenarios"][name]["_mc_months"]
        ax.hist(m, bins=80, histtype="step", density=True, label=name)
    ax.axvline(res["plan"]["life_months"], ls="--", color="k", lw=1, label="plan 4.4 (8.6 mo)")
    ax.axvline(6, ls=":", color="r", lw=1, label="G1 (6 mo)")
    ax.set_xlabel("battery life (months)")
    ax.set_ylabel("density")
    ax.legend(fontsize=8)
    fig.tight_layout()
    p = out_dir / "life_histogram.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)

    s = res["scenarios"]["plan_mix_twt"]
    rows = s["tornado"][:10][::-1]
    base = rows[0]["base_h"] / HOURS_PER_MONTH if rows else 0
    fig, ax = plt.subplots(figsize=(7, 4.5))
    for i, r in enumerate(rows):
        lo, hi = r["life_at_low_h"] / HOURS_PER_MONTH, r["life_at_high_h"] / HOURS_PER_MONTH
        ax.barh(i, max(lo, hi) - min(lo, hi), left=min(lo, hi), color="#4a7fb5")
    ax.axvline(base, color="k", lw=1)
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r["name"] for r in rows], fontsize=7)
    ax.set_xlabel("battery life (months), plan_mix_twt")
    fig.tight_layout()
    p = out_dir / "tornado_plan_mix_twt.png"
    fig.savefig(p, dpi=120)
    plt.close(fig)
    paths.append(p)
    return paths


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(prog="sentinel_power")
    ap.add_argument("--n", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--no-plots", action="store_true")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    a = ap.parse_args(argv)
    const = load_constants()
    scen = load_scenarios()
    res = analyse(const, scen, n=a.n, seed=a.seed)
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / "report.md").write_text(render_markdown(res))
    if not a.no_plots:
        plots(res, a.out)
    for s in res["scenarios"].values():
        s.pop("_mc_months")
    (a.out / "report.json").write_text(json.dumps(res, indent=1, default=float))
    print((a.out / "report.md").read_text())
    return 0
