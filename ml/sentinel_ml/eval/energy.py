"""Energy-per-inference estimate: E = MACs x (energy per MAC).

The per-MAC constant is read from ``hardware/power_model/constants.yaml`` (key
``npu_pj_per_mac``, searched at any nesting depth) — the source of truth per
INTERFACES.md §6 — and falls back to a documented default of 1.0 pJ/MAC when that
file or key does not exist yet. 1.0 pJ/MAC is a round planning number for an int8
NPU in a 16 nm-class MCU including local SRAM traffic; it ignores external-memory
traffic, NPU idle/ramp and the M55's pre/post-processing, so it is a floor, not a
prediction. Replace with PPK2 measurements in M1 (PLAN.md G6).
"""
from __future__ import annotations

import os
import time

import numpy as np

DEFAULT_PJ_PER_MAC = 1.0
KEY = "npu_pj_per_mac"
CONSTANTS_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "hardware",
                                               "power_model", "constants.yaml"))


def _find(d, key):
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            r = _find(v, key)
            if r is not None:
                return r
    elif isinstance(d, list):
        for v in d:
            r = _find(v, key)
            if r is not None:
                return r
    return None


def load_pj_per_mac(path: str | None = None):
    """Returns (pJ/MAC, source string)."""
    path = path or CONSTANTS_PATH
    if os.path.exists(path):
        import yaml

        with open(path) as f:
            v = _find(yaml.safe_load(f), KEY)
        if isinstance(v, dict):  # allow {value: x, unit: ...}
            v = v.get("value")
        if v is not None:
            return float(v), _rel(path)
    return DEFAULT_PJ_PER_MAC, f"default ({DEFAULT_PJ_PER_MAC} pJ/MAC; {KEY} not found in {_rel(path)})"


def _rel(path):
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    p = os.path.abspath(path)
    return os.path.relpath(p, root) if p.startswith(root + os.sep) else path


def load_energy_constants(path: str | None = None) -> dict:
    """Value plus optional uncertainty range (``low``/``high``) and the optional fixed
    per-inference overhead ``npu_overhead_per_inference`` (mJ) if the hardware file has them."""
    path = path or CONSTANTS_PATH
    v, src = load_pj_per_mac(path)
    out = {"pj_per_mac": v, "low": v, "high": v, "overhead_mj": None, "source": src}
    if os.path.exists(path):
        import yaml

        with open(path) as f:
            d = yaml.safe_load(f)
        raw = _find(d, KEY)
        if isinstance(raw, dict):
            out["low"] = float(raw.get("low", v))
            out["high"] = float(raw.get("high", v))
        ov = _find(d, "npu_overhead_per_inference")
        if isinstance(ov, dict):
            ov = ov.get("value")
        if ov is not None:
            out["overhead_mj"] = float(ov)
    return out


def energy_mj(macs: int, pj_per_mac: float) -> float:
    return macs * pj_per_mac * 1e-9  # pJ → mJ


def measure_latency_ms(fn, x, warmup: int = 3, runs: int = 20) -> dict:
    for _ in range(warmup):
        fn(x)
    ts = []
    for _ in range(runs):
        t = time.perf_counter()
        fn(x)
        ts.append((time.perf_counter() - t) * 1e3)
    return {"median_ms": float(np.median(ts)), "p90_ms": float(np.percentile(ts, 90)), "runs": runs}
