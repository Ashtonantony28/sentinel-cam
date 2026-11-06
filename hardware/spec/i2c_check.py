#!/usr/bin/env python3
"""I2C address-map checker for spec/i2c_map.yaml.

Rules: 7-bit addresses only, none in the reserved ranges 0x00-0x07 / 0x78-0x7F, no two devices on
one bus at the same address, unknown (null) addresses must carry a TO-VERIFY ID. `alternates`
are reported as the collisions they would cause if swapped in.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import yaml

DEFAULT_MAP = Path(__file__).resolve().parent / "i2c_map.yaml"
RESERVED = set(range(0x00, 0x08)) | set(range(0x78, 0x80))


def load_map(path: str | Path = DEFAULT_MAP) -> dict:
    return yaml.safe_load(Path(path).read_text())


def bus_problems(bus_name: str, devices: list[dict]) -> list[str]:
    errs = []
    by_addr: dict[int, list[str]] = defaultdict(list)
    for d in devices:
        a = d.get("address")
        label = f"{d.get('ref')}:{d.get('part')}"
        if a is None:
            if not d.get("verify"):
                errs.append(f"{bus_name}: {label} has no address and no TO-VERIFY id")
            continue
        if not isinstance(a, int) or not 0 <= a <= 0x7F:
            errs.append(f"{bus_name}: {label} address {a!r} is not a 7-bit integer")
            continue
        if a in RESERVED:
            errs.append(f"{bus_name}: {label} uses reserved address 0x{a:02X}")
        by_addr[a].append(label)
    for a, labels in sorted(by_addr.items()):
        if len(labels) > 1:
            errs.append(f"{bus_name}: address collision at 0x{a:02X}: {', '.join(labels)}")
    return errs


def check_map(m: dict) -> list[str]:
    errs = []
    for name, bus in m["buses"].items():
        errs += bus_problems(name, bus["devices"])
    return errs


def alternate_problems(m: dict) -> dict[str, list[str]]:
    """For each alternate part, the problems the bus would have if it were swapped in."""
    out = {}
    for alt in m.get("alternates", []):
        bus = m["buses"][alt["bus"]]
        devs = [d for d in bus["devices"] if d.get("ref") != alt.get("replaces")]
        devs.append({"ref": "ALT", "part": alt["part"], "address": alt["address"], "verify": alt.get("verify")})
        out[alt["part"]] = bus_problems(alt["bus"], devs)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    m = load_map(argv[0] if argv else DEFAULT_MAP)
    errs = check_map(m)
    for e in errs:
        print(f"ERROR: {e}")
    for part, probs in alternate_problems(m).items():
        status = "; ".join(probs) if probs else "ok"
        print(f"alternate {part}: {status}")
    if not errs:
        n = sum(len(b["devices"]) for b in m["buses"].values())
        print(f"I2C map OK: {len(m['buses'])} buses, {n} devices, no collisions")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
