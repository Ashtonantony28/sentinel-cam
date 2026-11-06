"""I2C address map: no collisions on the real map; the checker catches the collisions we know about."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

import i2c_check as ic

HW = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def m():
    return ic.load_map()


def test_real_map_has_no_collisions(m):
    assert ic.check_map(m) == []


def test_every_bus_has_master_pullup_and_devices(m):
    for name, bus in m["buses"].items():
        assert bus["master"] and bus["pullup_rail"] and bus["devices"], name
        assert bus["speed_hz"] in (100000, 400000, 1000000), name


def test_known_addresses(m):
    aon = {d["part"]: d["address"] for d in m["buses"]["I2C_AON"]["devices"]}
    assert aon["MAX17048"] == 0x36
    assert aon["BQ25798"] == 0x6B
    assert aon["VEML7700"] == 0x10
    cam = {d["part"]: d["address"] for d in m["buses"]["I2C_CAM"]["devices"]}
    assert cam["VD66GY"] == 0x10


def test_verified_addresses_cite_a_url_or_carry_verify(m):
    reg = yaml.safe_load((HW / "spec" / "to_verify.yaml").read_text())["open"]
    for bus in m["buses"].values():
        for d in bus["devices"]:
            if d.get("verify"):
                assert d["verify"] in reg
            else:
                assert str(d.get("src", "")).startswith("https://"), d


def test_alternates_collisions_detected(m):
    alt = ic.alternate_problems(m)
    assert any("0x36" in p for p in alt["ATECC608B-TFLXTLS"])  # collides with MAX17048
    assert any("0x10" in p for p in alt["VEML7700 moved to camera bus"])  # collides with VD66GY
    assert alt["ATECC608B-TNGTLS"] == []
    assert alt["IMX335 (B-CAMS-IMX fallback)"] == []


@pytest.mark.parametrize("devices,needle", [
    ([{"ref": "A", "part": "x", "address": 0x36}, {"ref": "B", "part": "y", "address": 0x36}], "collision at 0x36"),
    ([{"ref": "A", "part": "x", "address": 0x03}], "reserved"),
    ([{"ref": "A", "part": "x", "address": 0x7C}], "reserved"),
    ([{"ref": "A", "part": "x", "address": 0x80}], "7-bit"),
    ([{"ref": "A", "part": "x", "address": "0x10"}], "7-bit"),
    ([{"ref": "A", "part": "x", "address": None}], "no address"),
])
def test_bus_problem_detection(devices, needle):
    errs = ic.bus_problems("T", devices)
    assert any(needle in e for e in errs), errs


def test_unknown_address_allowed_with_verify():
    assert ic.bus_problems("T", [{"ref": "A", "part": "x", "address": None, "verify": "TV-11"}]) == []


def test_bom_parts_on_i2c_are_in_map(m):
    """Every BOM part that we know speaks I2C appears on a Rev A bus with its refdes."""
    import check_bom as cb

    rep = cb.check_file(HW / "bom" / "bom.csv")
    refs = {r["refdes"]: r for r in rep.rows}
    on_bus = {d["ref"] for b in m["buses"].values() if b["rev"] == "A" for d in b["devices"]}
    for ref in ("U5", "U10", "U13", "U12", "U27"):
        assert ref in refs and ref in on_bus, ref


def test_cli(capsys):
    assert ic.main([]) == 0
    assert "no collisions" in capsys.readouterr().out
