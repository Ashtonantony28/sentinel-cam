"""Load and validate constants.yaml (the single source of truth for power numbers)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

import numpy as np
import yaml

from .units import UNITS, to_si

PKG_DIR = Path(__file__).resolve().parent
POWER_MODEL_DIR = PKG_DIR.parent
DEFAULT_CONSTANTS = POWER_MODEL_DIR / "constants.yaml"
DEFAULT_SCENARIOS = POWER_MODEL_DIR / "scenarios.yaml"

STATUSES = {
    "datasheet",
    "third_party_measurement",
    "derived",
    "estimate",
    "design_choice",
    "spec",
    "measured",  # our own bench measurement; src must point at the log under hardware/
    "measured_on_model",  # counted on our own exported model (e.g. MACs from ml/ npu_fit)
}
NEEDS_SOURCE = {"datasheet", "third_party_measurement", "derived", "spec", "measured", "measured_on_model"}
REQUIRED_FIELDS = ("value", "low", "high", "unit", "status")
NON_CONSTANT_GROUPS = {"tables", "sources", "schema_version"}


class ConstantsError(ValueError):
    pass


@dataclass(frozen=True)
class Constant:
    name: str
    value: float  # SI
    low: float  # SI
    high: float  # SI
    unit: str
    status: str
    src: tuple[str, ...] = ()
    verify: str | None = None
    note: str = ""
    raw_value: float = 0.0  # in `unit`

    @property
    def uncertain(self) -> bool:
        return self.high > self.low


@dataclass
class Constants:
    entries: dict[str, Constant]
    temp_table: np.ndarray  # shape (n, 2): degC, fraction
    sources: dict[str, str]
    raw: dict = field(repr=False, default_factory=dict)

    # ---- access -------------------------------------------------------------------------
    def __getitem__(self, name: str) -> Constant:
        return self.entries[name]

    def names(self) -> list[str]:
        return list(self.entries)

    def nominal(self) -> dict[str, float]:
        return {k: c.value for k, c in self.entries.items()}

    def at(self, name: str, which: str) -> dict[str, float]:
        """Nominal set with one constant moved to its 'low' or 'high' bound."""
        p = self.nominal()
        p[name] = getattr(self.entries[name], which)
        return p

    def sample(self, rng: np.random.Generator, n: int) -> dict[str, np.ndarray]:
        """Triangular(low, value, high) for every uncertain constant; fixed otherwise."""
        out: dict[str, np.ndarray] = {}
        for k, c in self.entries.items():
            if c.uncertain:
                out[k] = rng.triangular(c.low, c.value, c.high, size=n)
            else:
                out[k] = np.full(n, c.value)
        return out

    def temp_derate(self, temp_c):
        t = self.temp_table
        return np.interp(temp_c, t[:, 0], t[:, 1])


def _parse_entry(name: str, d: Mapping, sources: Mapping[str, str]) -> Constant:
    missing = [f for f in REQUIRED_FIELDS if f not in d]
    if missing:
        raise ConstantsError(f"{name}: missing fields {missing}")
    unit = d["unit"]
    if unit not in UNITS:
        raise ConstantsError(f"{name}: unknown unit {unit!r}")
    status = d["status"]
    if status not in STATUSES:
        raise ConstantsError(f"{name}: status {status!r} not in {sorted(STATUSES)}")
    src = tuple(d.get("src", []) or [])
    if status in NEEDS_SOURCE and not src:
        raise ConstantsError(f"{name}: status {status} requires a src")
    for s in src:
        if s not in sources:
            raise ConstantsError(f"{name}: unknown source id {s!r}")
    v, lo, hi = (float(d[k]) for k in ("value", "low", "high"))
    if not (lo <= v <= hi):
        raise ConstantsError(f"{name}: need low <= value <= high, got {lo}, {v}, {hi}")
    return Constant(
        name=name,
        value=to_si(v, unit),
        low=to_si(lo, unit),
        high=to_si(hi, unit),
        unit=unit,
        status=status,
        src=src,
        verify=d.get("verify"),
        note=d.get("note", ""),
        raw_value=v,
    )


def load_constants(path: str | Path = DEFAULT_CONSTANTS) -> Constants:
    raw = yaml.safe_load(Path(path).read_text())
    sources = raw.get("sources", {}) or {}
    entries: dict[str, Constant] = {}
    for group, items in raw.items():
        if group in NON_CONSTANT_GROUPS:
            continue
        if not isinstance(items, dict):
            raise ConstantsError(f"group {group!r} must be a mapping")
        for key, d in items.items():
            name = f"{group}.{key}"
            if name in entries:
                raise ConstantsError(f"duplicate constant {name}")
            entries[name] = _parse_entry(name, d, sources)
    try:
        pts = np.array(raw["tables"]["temp_derating"]["points"], dtype=float)
    except KeyError as e:
        raise ConstantsError("tables.temp_derating.points missing") from e
    if pts.ndim != 2 or pts.shape[1] != 2 or np.any(np.diff(pts[:, 0]) <= 0):
        raise ConstantsError("temp_derating points must be [[degC, frac], ...] with increasing degC")
    if np.any((pts[:, 1] <= 0) | (pts[:, 1] > 1)):
        raise ConstantsError("temp_derating fractions must be in (0, 1]")
    return Constants(entries=entries, temp_table=pts, sources=sources, raw=raw)


def load_scenarios(path: str | Path = DEFAULT_SCENARIOS) -> dict[str, dict]:
    raw = yaml.safe_load(Path(path).read_text())
    sc = raw["scenarios"]
    for name, s in sc.items():
        if s.get("mode") not in ("twt", "radio_off"):
            raise ConstantsError(f"scenario {name}: mode must be twt or radio_off")
        for k, v in s["rates"].items():
            if v < 0:
                raise ConstantsError(f"scenario {name}: negative rate {k}")
    return sc
