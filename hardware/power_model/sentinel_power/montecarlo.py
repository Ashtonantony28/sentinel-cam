"""Monte-Carlo over constant uncertainty and event counts; one-at-a-time and rank sensitivity."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from .constants import Constants
from .model import battery_life_hours
from .units import HOURS_PER_MONTH


@dataclass
class MCResult:
    life_hours: np.ndarray
    samples: dict[str, np.ndarray]
    rates: dict[str, np.ndarray]

    @property
    def life_months(self) -> np.ndarray:
        return self.life_hours / HOURS_PER_MONTH

    def summary(self) -> dict[str, float]:
        m = self.life_months
        p5, p25, p50, p75, p95 = np.percentile(m, [5, 25, 50, 75, 95])
        return {
            "n": int(m.size),
            "mean_months": float(m.mean()),
            "p5_months": float(p5),
            "p25_months": float(p25),
            "median_months": float(p50),
            "p75_months": float(p75),
            "p95_months": float(p95),
            "prob_ge_6_months": float(np.mean(m >= 6.0)),
            "prob_ge_9_months": float(np.mean(m >= 9.0)),
            "prob_ge_12_months": float(np.mean(m >= 12.0)),
        }


def sample_rates(scenario: Mapping, rng: np.random.Generator, n: int, horizon_days: float = 180.0) -> dict[str, np.ndarray]:
    """Gamma-distributed household mean rate (CV = scenario.rate_cv), then Poisson counts over the
    horizon, returned as realised events/day."""
    cv = float(scenario.get("rate_cv", 0.0))
    out = {}
    for kind, mean in scenario["rates"].items():
        if mean <= 0:
            out[kind] = np.zeros(n)
            continue
        if cv > 0:
            k = 1.0 / cv**2
            lam = rng.gamma(k, mean / k, size=n)
        else:
            lam = np.full(n, float(mean))
        out[kind] = rng.poisson(lam * horizon_days) / horizon_days
    return out


def run_mc(const: Constants, scenario: Mapping, n: int = 20000, seed: int = 1) -> MCResult:
    rng = np.random.default_rng(seed)
    samples = const.sample(rng, n)
    rates = sample_rates(scenario, rng, n)
    life = battery_life_hours(samples, scenario, const.temp_derate, rates)
    return MCResult(life_hours=np.asarray(life), samples=samples, rates=rates)


def tornado(const: Constants, scenario: Mapping, rate_factor: float = 2.0) -> list[dict]:
    """One-at-a-time swing: each uncertain constant at its low and high bound, others nominal.
    Event rates are swung by 1/rate_factor .. rate_factor. Sorted by swing, largest first."""
    base = float(battery_life_hours(const.nominal(), scenario, const.temp_derate))
    rows = []
    for name, c in const.entries.items():
        if not c.uncertain:
            continue
        lo = float(battery_life_hours(const.at(name, "low"), scenario, const.temp_derate))
        hi = float(battery_life_hours(const.at(name, "high"), scenario, const.temp_derate))
        rows.append({"name": name, "life_at_low_h": lo, "life_at_high_h": hi, "swing_h": abs(hi - lo)})
    for kind, mean in scenario["rates"].items():
        if mean <= 0:
            continue
        lo_r = dict(scenario["rates"]); lo_r[kind] = mean / rate_factor
        hi_r = dict(scenario["rates"]); hi_r[kind] = mean * rate_factor
        lo = float(battery_life_hours(const.nominal(), scenario, const.temp_derate, lo_r))
        hi = float(battery_life_hours(const.nominal(), scenario, const.temp_derate, hi_r))
        rows.append({"name": f"rate.{kind} (x1/{rate_factor:g}..x{rate_factor:g})", "life_at_low_h": lo, "life_at_high_h": hi, "swing_h": abs(hi - lo)})
    rows.sort(key=lambda r: r["swing_h"], reverse=True)
    for r in rows:
        r["base_h"] = base
    return rows


def _rank(x: np.ndarray) -> np.ndarray:
    r = np.empty(x.size)
    r[np.argsort(x, kind="mergesort")] = np.arange(x.size)
    return r


def spearman(x: np.ndarray, y: np.ndarray) -> float:
    rx, ry = _rank(np.asarray(x, float)), _rank(np.asarray(y, float))
    if rx.std() == 0 or ry.std() == 0:
        return 0.0
    return float(np.corrcoef(rx, ry)[0, 1])


def rank_sensitivity(res: MCResult) -> list[dict]:
    rows = []
    for name, s in {**res.samples, **{f"rate.{k}": v for k, v in res.rates.items()}}.items():
        if np.ptp(s) == 0:
            continue
        rows.append({"name": name, "spearman": spearman(s, res.life_hours)})
    rows.sort(key=lambda r: abs(r["spearman"]), reverse=True)
    return rows
