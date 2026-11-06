"""Cross-checks between spec/*.yaml, bom.csv, constants.yaml and the TO-VERIFY register."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

import check_bom as cb
from sentinel_power.constants import load_constants

HW = Path(__file__).resolve().parents[1]
SPEC = HW / "spec"


def y(name):
    return yaml.safe_load((SPEC / name).read_text())


@pytest.fixture(scope="module")
def rails():
    return y("rails.yaml")


@pytest.fixture(scope="module")
def ifc():
    return y("interfaces.yaml")


@pytest.fixture(scope="module")
def bom():
    rep = cb.check_file(HW / "bom" / "bom.csv")
    assert rep.ok
    return rep


@pytest.fixture(scope="module")
def register():
    return y("to_verify.yaml")["open"]


def _bom_refs(bom):
    refs = {}
    for r in bom.rows:
        for d in cb.expand_refdes(r["refdes"]):
            refs[d] = r
    return refs


# ------------------------------------------------------------------ TO-VERIFY register
def _text_files():
    for p in HW.rglob("*"):
        if p.is_file() and p.suffix in (".md", ".yaml", ".csv") and "out" not in p.parts and p.name != "to_verify.yaml":
            yield p


def test_every_to_verify_cites_a_register_id(register):
    bad = []
    used = set()
    for p in _text_files():
        txt = p.read_text()
        for m in re.finditer(r"TO-VERIFY(?! (?:ID|item|register))(\[(TV-\d+)\])?", txt):
            if not m.group(2):
                bad.append(f"{p.relative_to(HW)}: bare TO-VERIFY near {txt[max(0, m.start() - 40):m.end()]!r}")
            elif m.group(2) not in register:
                bad.append(f"{p.relative_to(HW)}: unknown id {m.group(2)}")
            else:
                used.add(m.group(2))
        used |= set(re.findall(r"verify: (TV-\d+)", txt))
    assert not bad, "\n".join(bad)
    orphans = set(register) - used
    assert not orphans, f"register items never referenced: {sorted(orphans)}"


def test_register_ids_well_formed(register):
    for k, v in register.items():
        assert re.fullmatch(r"TV-\d{2}", k)
        assert v["topic"] and v["when"] and v["owner"]


# ------------------------------------------------------------------ rails
def test_rail_regulators_and_jumpers_exist_in_bom(rails, bom):
    refs = _bom_refs(bom)
    jumpers, tps = [], []
    for name, r in rails["rails"].items():
        reg = r["regulator"]
        assert reg["refdes"] in refs, (name, reg["refdes"])
        assert refs[reg["refdes"]]["mpn_primary"] == reg["mpn"], name
        if r.get("jumper"):
            assert r["jumper"] in refs, (name, r["jumper"])
            jumpers.append(r["jumper"])
        tps.append(r["test_point"])
    assert len(jumpers) == len(set(jumpers))
    assert len(tps) == len(set(tps))


def test_rail_voltage_windows(rails):
    for name, r in rails["rails"].items():
        assert r["v_min"] <= r["v_nom"] <= r["v_max"], name
        assert r["max_current_a"] > 0, name


def test_rail_tree_and_domains(rails, ifc):
    rs = rails["rails"]
    sig_names = {s["name"] for s in ifc["signals"]}
    for name, r in rs.items():
        src = r["source"]
        assert src == "CELL" or src in rs, (name, src)
        if r["domain"] == "AON":
            assert rs[src]["domain"] in ("BAT", "AON"), name  # AON never depends on a gated rail
        if r["domain"] == "BAT":
            assert src in ("CELL", "VBAT"), name
        en = r["enable"]
        assert en in ("always", "VDDCORE_EN") or en in rs or en in sig_names, (name, en)
        if r["domain"] in ("BAT", "AON"):
            assert en == "always", name
    # walk up: CTX rails are either below SYS_CTX or enabled by an N6-only (CTX-domain) signal
    ctx_signals = {s["name"] for s in ifc["signals"] if s["domain"] == "CTX"}
    for name, r in rs.items():
        if r["domain"] != "CTX":
            continue
        cur, chain = name, []
        while cur in rs:
            chain.append(cur)
            cur = rs[cur]["source"]
        assert "SYS_CTX" in chain or r["enable"] in ctx_signals, name


def test_aon_rail_matches_power_model(rails):
    c = load_constants()
    assert rails["rails"]["3V3_AON"]["v_nom"] == pytest.approx(c["rails.aon_v"].value)


def test_load_switch_count_matches_power_model(bom):
    n = sum(int(r["qty"]) for r in bom.rows if r["mpn_primary"] == "TPS22917DBVR" and r["dnp"] == "N")
    assert n == load_constants()["rails.n_load_switches"].value


def _step_of(seq, rail, key):
    for s in seq:
        if rail in s.get(key, []):
            return s["step"]
    return None


def test_power_up_order(rails):
    up = rails["sequencing"]["power_up"]
    vdd = _step_of(up, "3V3_CTX", "rails_up")
    aon = _step_of(up, "VDDA1V8_AON", "rails_up")
    core = _step_of(up, "VDDCORE", "rails_up")
    assert vdd is not None and aon is not None and core is not None
    assert vdd < core and aon < core  # AN6000: VDD and VDDA18AON before PWR_ON-enabled rails
    assert _step_of(up, "SYS_CTX", "rails_up") < vdd
    assert _step_of(up, "VDDA1V8", "rails_up") == core
    ctx = [n for n, r in rails["rails"].items() if r["domain"] == "CTX"]
    for n in ctx:
        assert _step_of(up, n, "rails_up") is not None, f"{n} never powered up"
    steps = [s["step"] for s in up]
    assert steps == sorted(steps) and len(steps) == len(set(steps))


@pytest.mark.parametrize("which", ["power_down", "forced_power_down"])
def test_power_down_order_obeys_an6000(rails, which):
    seq = rails["sequencing"][which]
    first = {n: _step_of(seq, n, "rails_down") for n in rails["rails"] if rails["rails"][n]["domain"] == "CTX"}
    for n, st in first.items():
        assert st is not None, f"{which}: {n} never powered down"
    for early in ("VDDCORE", "VDDA1V8"):
        for late in ("3V3_CTX", "VDDA1V8_AON"):
            assert first[early] < first[late], (which, early, late)
    assert first["SYS_CTX"] == max(first.values())


# ------------------------------------------------------------------ interfaces
def test_c6_pins_unique_and_match_module_table(ifc):
    pads = ifc["c6_mini1"]["pads"]
    used = {}
    for s in ifc["signals"]:
        g = s.get("c6_gpio")
        if g is None:
            continue
        assert g in pads, s["name"]
        assert s["c6_pad"] == pads[g], (s["name"], s["c6_pad"], pads[g])
        assert g not in used, f"GPIO{g} used by {used.get(g)} and {s['name']}"
        used[g] = s["name"]
    # pin-budget finding: every MINI-1 GPIO is allocated
    assert set(used) == set(pads)
    assert len(pads) == 22


def test_wake_pins_are_lp_gpio(ifc):
    lp = set(ifc["c6_mini1"]["lp_gpio"])
    for s in ifc["signals"]:
        if s.get("lp_wake"):
            assert s["c6_gpio"] in lp, s["name"]


def test_strapping_pins_are_acknowledged(ifc):
    strap = set(ifc["c6_mini1"]["strapping"])
    for s in ifc["signals"]:
        if s.get("c6_gpio") in strap:
            assert s.get("strapping_ok"), f"{s['name']} on strapping GPIO{s['c6_gpio']} needs a strapping_ok note"


def test_no_n6_pins_invented(ifc):
    """Rev A must not carry STM32 port pins (e.g. PE15) or BGA balls: those come from CubeMX (TV-14)."""
    port_pin = re.compile(r"\bP[A-Z]\d{1,2}\b")
    for s in ifc["signals"]:
        assert not port_pin.search(str(s.get("n6") or "")), (s["name"], s["n6"])
        assert not any(k.startswith("n6_ball") for k in s), s["name"]


def test_test_points_unique_and_in_bom(ifc, rails, bom):
    refs = _bom_refs(bom)
    rail_tps = [r["test_point"] for r in rails["rails"].values()]
    sig_tps = list(ifc["test_points"])
    allt = rail_tps + sig_tps
    assert len(allt) == len(set(allt))
    for tp in allt:
        assert tp in refs, tp
    assert set(allt) == {d for d in refs if d.startswith("TP")}


# ------------------------------------------------------------------ Rev 0 wiring
def test_rev0_wires_use_documented_pins_once():
    w = y("rev0_wiring.yaml")
    c6h, dkh = w["devkitc_headers"], w["dk_arduino"]
    used_c6, used_dk = {}, {}
    for wire in w["wires"]:
        c6, dk = wire.get("c6"), wire.get("dk")
        if c6:
            assert c6 in c6h, wire
            assert c6 not in used_c6, f"{c6} used by {used_c6.get(c6)} and {wire['signal']}"
            used_c6[c6] = wire["signal"]
            g = c6h[c6]
            if isinstance(g, int) and g in w["devkitc_avoid"]:
                assert "strap" in wire.get("note", "").lower(), f"{wire['signal']} uses avoided GPIO{g}"
        if dk:
            assert dk in dkh, wire
            assert dk not in used_dk, f"{dk} used twice"
            used_dk[dk] = wire["signal"]
            label, mcu, fn = dkh[dk]
            if "GND" in wire["signal"]:
                assert fn == "GND", wire
            else:
                assert label.startswith("D") and label not in ("D14", "D15"), wire  # no ADC pins, no shared I2C1
    # hosted SPI wiring lands on SPI5 on the DK
    by_sig = {x["signal"]: x for x in w["wires"]}
    assert dkh[by_sig["HOSTED_SCLK"]["dk"]][2] == "SPI5_SCK"
    assert dkh[by_sig["HOSTED_MOSI"]["dk"]][2] == "SPI5_MOSI"
    assert dkh[by_sig["HOSTED_MISO"]["dk"]][2] == "SPI5_MISO"
    assert dkh[by_sig["CTRL_UART_C6_TX"]["dk"]][2] == "USART2_RX"
    assert dkh[by_sig["CTRL_UART_C6_RX"]["dk"]][2] == "USART2_TX"
    # wake inputs on LP GPIOs
    for sig in ("PIR_OUT", "BUTTON_N", "I2C_AON_SDA", "I2C_AON_SCL"):
        assert c6h[by_sig[sig]["c6"]] in range(0, 8), sig
