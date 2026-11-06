"""BOM checker tests (real BOM + synthetic bad BOMs)."""

from __future__ import annotations

import csv
import io
from pathlib import Path

import pytest

import check_bom as cb

HW = Path(__file__).resolve().parents[1]


def rows_from(text: str):
    rd = csv.DictReader(io.StringIO(text))
    return list(rd.fieldnames or []), list(rd)


HEADER = ",".join(cb.REQUIRED_COLUMNS)


def bom(*lines: str):
    return rows_from(HEADER + "\n" + "\n".join(lines) + "\n")


GOOD = "U1,soc,MCU,ST,PART1,none,BGA,1,10.00,estimate,N,"


def test_real_bom_is_clean():
    rep = cb.check_file(HW / "bom" / "bom.csv")
    assert rep.ok, rep.errors
    assert 100 < rep.total_populated < 200
    assert rep.total_dnp > 0  # radar is DNP


def test_real_bom_total_matches_manual_sum():
    with open(HW / "bom" / "bom.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    manual = sum(int(r["qty"]) * float(r["unit_price_usd_q5"]) for r in rows if r["dnp"] == "N")
    assert cb.check_file(HW / "bom" / "bom.csv").total_populated == pytest.approx(manual)


def test_real_bom_has_all_plan_blocks():
    rep = cb.check_file(HW / "bom" / "bom.csv")
    mpns = {r["mpn_primary"] for r in rep.rows}
    for must in ("STM32N657X0H3Q", "ESP32-C6-MINI-1-N4", "BQ25798RQMR", "MAX17048G+T10", "TPS62840DLCR",
                 "TPS22917DBVR", "VEML7700-TT", "MAX98357AETE+T", "EKMB1101111", "BGT60LTR11AIP"):
        assert must in mpns, must


def test_expand_refdes():
    assert cb.expand_refdes("C1-C3") == ["C1", "C2", "C3"]
    assert cb.expand_refdes("D1,D3") == ["D1", "D3"]
    assert cb.expand_refdes("U4") == ["U4"]
    for bad in ("", "C3-C1", "C1-R3", "1C", "U-1"):
        with pytest.raises(cb.BomError):
            cb.expand_refdes(bad)


def test_good_minimal():
    rep = cb.check_rows(*bom(GOOD, "C1-C4,mlcc,cap,x,GENERIC,none,0402,4,0.02,estimate,N,"))
    assert rep.ok, rep.errors
    assert rep.total_populated == pytest.approx(10.08)


@pytest.mark.parametrize("line,needle", [
    ("U1,soc,MCU,ST,PART2,none,BGA,1,1.00,estimate,N,", "duplicate refdes U1"),
    ("C1-C3,x,cap,x,P,none,0402,3,0.1,estimate,N,\nC3,x,cap,x,P,none,0402,1,0.1,estimate,N,", "duplicate refdes C3"),
    ("U2,soc,MCU,ST,PART2,,BGA,1,1.00,estimate,N,", "mpn_fallback"),
    ("U2,soc,MCU,ST,PART2,None,BGA,1,1.00,estimate,N,", "lowercase 'none'"),
    ("U2,soc,MCU,ST,PART2,none,BGA,0,1.00,estimate,N,", "qty"),
    ("U2,soc,MCU,ST,PART2,none,BGA,1.5,1.00,estimate,N,", "qty"),
    ("C5-C7,x,cap,x,P,none,0402,2,0.1,estimate,N,", "qty 2 != 3"),
    ("U2,soc,MCU,ST,PART2,none,BGA,1,-1,estimate,N,", "bad unit price"),
    ("U2,soc,MCU,ST,PART2,none,BGA,1,abc,estimate,N,", "bad unit price"),
    ("U2,soc,MCU,ST,PART2,none,BGA,1,1.00,digikey,N,", "price_source"),
    ("U2,soc,MCU,ST,PART2,none,BGA,1,1.00,estimate,maybe,", "dnp"),
    ("U2,soc,MCU,ST,,none,BGA,1,1.00,estimate,N,", "empty mpn_primary"),
    ("2U,soc,MCU,ST,P,none,BGA,1,1.00,estimate,N,", "bad refdes"),
])
def test_errors_detected(line, needle):
    rep = cb.check_rows(*bom(GOOD, *line.split("\n")))
    assert not rep.ok
    assert any(needle in e for e in rep.errors), rep.errors


def test_missing_column():
    hdr, rows = rows_from("refdes,block\nU1,soc\n")
    rep = cb.check_rows(hdr, rows)
    assert not rep.ok and "missing columns" in rep.errors[0]


def test_dnp_excluded_from_total_and_url_source_ok():
    rep = cb.check_rows(*bom(GOOD, "U9,radar,Radar,IFX,R1,none,AIP,1,8.00,https://example.com/x,Y,"))
    assert rep.ok, rep.errors
    assert rep.total_populated == pytest.approx(10.0)
    assert rep.total_dnp == pytest.approx(8.0)
    assert rep.n_estimated_prices == 1


def test_unquoted_comma_detected(tmp_path):
    f = tmp_path / "b.csv"
    f.write_text(HEADER + "\n" + "U1,soc,MCU, extra,ST,PART1,none,BGA,1,10.00,estimate,N,\n")
    assert not cb.check_file(f).ok


def test_cli_exit_codes(tmp_path, capsys):
    assert cb.main([str(HW / "bom" / "bom.csv")]) == 0
    assert "Populated cost" in capsys.readouterr().out
    f = tmp_path / "b.csv"
    f.write_text(HEADER + "\n" + GOOD + "\n" + GOOD + "\n")
    assert cb.main([str(f)]) == 1
