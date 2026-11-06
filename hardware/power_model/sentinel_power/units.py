"""Unit table. Every constant is converted to SI on load so the model never sees mA or mJ.

SI targets: A, V, W, J, s, C (coulomb), fraction, per-second rates, J/MAC, MAC, bit/s.
"""

from __future__ import annotations

HOURS_PER_MONTH = 730.5  # 365.25 * 24 / 12
SECONDS_PER_DAY = 86400.0
SECONDS_PER_HOUR = 3600.0
SECONDS_PER_MONTH = HOURS_PER_MONTH * SECONDS_PER_HOUR
SECONDS_PER_YEAR = 365.25 * SECONDS_PER_DAY

# unit -> (SI dimension, multiplier to SI)
UNITS: dict[str, tuple[str, float]] = {
    "A": ("current", 1.0),
    "mA": ("current", 1e-3),
    "uA": ("current", 1e-6),
    "nA": ("current", 1e-9),
    "V": ("voltage", 1.0),
    "W": ("power", 1.0),
    "mW": ("power", 1e-3),
    "J": ("energy", 1.0),
    "mJ": ("energy", 1e-3),
    "s": ("time", 1.0),
    "ms": ("time", 1e-3),
    "mAh": ("charge", 3.6),  # coulombs
    "fraction": ("dimensionless", 1.0),
    "count": ("dimensionless", 1.0),
    "pct_per_month": ("rate", 0.01 / SECONDS_PER_MONTH),  # fraction per second
    "pct_per_year": ("rate", 0.01 / SECONDS_PER_YEAR),  # fraction per second
    "degC": ("temperature", 1.0),
    "pJ_per_MAC": ("energy_per_op", 1e-12),  # J per MAC
    "GMAC": ("ops", 1e9),  # MAC
    "GOPS": ("op_rate", 1e9),  # ops per second
    "bytes": ("data", 8.0),  # bits
    "bit_per_s": ("data_rate", 1.0),
}

DIMENSION_OF = {u: d for u, (d, _) in UNITS.items()}


def to_si(value: float, unit: str) -> float:
    """Convert a value in `unit` to SI. Temperature stays in degC (offset unit)."""
    if unit not in UNITS:
        raise ValueError(f"unknown unit {unit!r}; add it to units.UNITS")
    return value * UNITS[unit][1]
