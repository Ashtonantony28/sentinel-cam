#!/usr/bin/env python3
"""Validate hardware/bom/bom.csv and total its cost.

Checks:
  * required columns present
  * refdes non-empty, well formed (U1, C1-C160, D1,D3 ...); no duplicate or overlapping designators
  * qty is a positive integer and equals the number of designators when a range/list is given
  * every part has an MPN fallback or the explicit word "none"
  * unit price is a non-negative number; price_source is "estimate" or an https:// URL
  * DNP flag is Y or N
Exit status 0 when clean, 1 when any error was found.
"""

from __future__ import annotations

import csv
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_COLUMNS = (
    "refdes", "block", "description", "manufacturer", "mpn_primary", "mpn_fallback",
    "package", "qty", "unit_price_usd_q5", "price_source", "dnp", "notes",
)
DEFAULT_BOM = Path(__file__).resolve().parent / "bom.csv"
_DESIG = re.compile(r"^([A-Z]+)(\d+)$")


class BomError(ValueError):
    pass


def expand_refdes(field_: str) -> list[str]:
    """'C1-C3' -> [C1, C2, C3]; 'D1,D3' -> [D1, D3]; 'U4' -> [U4]."""
    out: list[str] = []
    for part in [x.strip() for x in field_.split(",") if x.strip()]:
        if "-" in part:
            a, b = (s.strip() for s in part.split("-", 1))
            ma, mb = _DESIG.match(a), _DESIG.match(b)
            if not ma or not mb or ma.group(1) != mb.group(1):
                raise BomError(f"bad refdes range {part!r}")
            lo, hi = int(ma.group(2)), int(mb.group(2))
            if hi < lo:
                raise BomError(f"descending refdes range {part!r}")
            out += [f"{ma.group(1)}{i}" for i in range(lo, hi + 1)]
        else:
            if not _DESIG.match(part):
                raise BomError(f"bad refdes {part!r}")
            out.append(part)
    if not out:
        raise BomError("empty refdes")
    return out


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rows: list[dict] = field(default_factory=list)
    total_populated: float = 0.0
    total_dnp: float = 0.0
    by_block: dict[str, float] = field(default_factory=dict)
    n_estimated_prices: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors


def check_rows(header: list[str], rows: list[dict]) -> Report:
    rep = Report(rows=rows)
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        rep.errors.append(f"missing columns: {missing}")
        return rep
    seen: dict[str, int] = {}
    by_block: dict[str, float] = defaultdict(float)
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        where = f"line {i} ({r.get('refdes', '')!s})"
        try:
            desigs = expand_refdes(r["refdes"] or "")
        except BomError as e:
            rep.errors.append(f"{where}: {e}")
            continue
        for d in desigs:
            if d in seen:
                rep.errors.append(f"{where}: duplicate refdes {d} (also on line {seen[d]})")
            else:
                seen[d] = i
        for col in ("block", "description", "mpn_primary", "package"):
            if not (r[col] or "").strip():
                rep.errors.append(f"{where}: empty {col}")
        fb = (r["mpn_fallback"] or "").strip()
        if not fb:
            rep.errors.append(f"{where}: mpn_fallback must be an MPN or 'none'")
        elif fb.lower() == "none" and fb != "none":
            rep.errors.append(f"{where}: write the explicit fallback as lowercase 'none'")
        try:
            qty = int(r["qty"])
            if qty <= 0 or str(qty) != r["qty"].strip():
                raise ValueError
        except (ValueError, TypeError):
            rep.errors.append(f"{where}: qty must be a positive integer, got {r['qty']!r}")
            continue
        if len(desigs) > 1 and qty != len(desigs):
            rep.errors.append(f"{where}: qty {qty} != {len(desigs)} designators")
        try:
            price = float(r["unit_price_usd_q5"])
            if price < 0 or price != price:
                raise ValueError
        except (ValueError, TypeError):
            rep.errors.append(f"{where}: bad unit price {r['unit_price_usd_q5']!r}")
            continue
        src = (r["price_source"] or "").strip()
        if src == "estimate":
            rep.n_estimated_prices += 1
        elif not src.startswith("https://"):
            rep.errors.append(f"{where}: price_source must be 'estimate' or an https:// URL")
        dnp = (r["dnp"] or "").strip()
        if dnp not in ("Y", "N"):
            rep.errors.append(f"{where}: dnp must be Y or N, got {dnp!r}")
            continue
        ext = qty * price
        if dnp == "Y":
            rep.total_dnp += ext
        else:
            rep.total_populated += ext
            by_block[r["block"]] += ext
    rep.by_block = dict(sorted(by_block.items(), key=lambda kv: -kv[1]))
    if rep.n_estimated_prices:
        rep.warnings.append(f"{rep.n_estimated_prices} lines have estimated prices (TO-VERIFY[TV-25])")
    return rep


def check_file(path: str | Path = DEFAULT_BOM) -> Report:
    with open(path, newline="") as f:
        rd = csv.DictReader(f)
        header = list(rd.fieldnames or [])
        rows = list(rd)
    for r in rows:
        if None in r:  # more cells than headers
            return Report(errors=[f"row {r.get('refdes')} has extra cells (unquoted comma?)"])
    return check_rows(header, rows)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = Path(argv[0]) if argv else DEFAULT_BOM
    rep = check_file(path)
    for e in rep.errors:
        print(f"ERROR: {e}")
    for w in rep.warnings:
        print(f"warning: {w}")
    if rep.ok:
        print(f"BOM OK: {len(rep.rows)} lines")
        print(f"Populated cost per board (qty-5 est.): ${rep.total_populated:.2f}")
        print(f"DNP options if fitted:                 ${rep.total_dnp:.2f}")
        for b, v in rep.by_block.items():
            print(f"  {b:12s} ${v:7.2f}")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
