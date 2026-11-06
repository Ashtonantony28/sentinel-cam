"""Power model tests: hand-computed cases, monotonicity, unit consistency, MC sanity."""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import numpy as np
import pytest
import yaml

from sentinel_power import constants as C
from sentinel_power import model as M
from sentinel_power.montecarlo import rank_sensitivity, run_mc, sample_rates, spearman, tornado
from sentinel_power.plan import model_as_plan_rows, plan_table
from sentinel_power.units import HOURS_PER_MONTH, SECONDS_PER_DAY, UNITS, to_si

HW = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def const():
    return C.load_constants()


@pytest.fixture(scope="module")
def scen():
    return C.load_scenarios()


def simple_params(const):
    """Round-number parameter set for hand calculation."""
    p = const.nominal()
    p.update({
        "rails.aon_v": 3.0, "rails.aon_efficiency": 1.0, "rails.cortex_efficiency": 1.0,
        "battery.nominal_v": 4.0, "rails.tps62840_iq": 0.0, "rails.load_switch_off": 0.0,
        "aon_loads.c6_light_sleep": 100e-6, "aon_loads.c6_twt_wake_energy": 30e-3, "aon_loads.twt_interval": 30.0,
        "aon_loads.c6_deep_sleep": 10e-6, "aon_loads.c6_lp_core_overhead": 0.0,
        "aon_loads.pir": 0.0, "aon_loads.light_sensor": 0.0, "aon_loads.secure_element": 0.0, "aon_loads.radar": 0.0,
        "battery_loads.charger_iq": 0.0, "battery_loads.gauge": 0.0, "battery.protection_ic": 0.0,
        "cortex.boot_power": 1.0, "cortex.boot_time": 0.1, "cortex.sensor_start_power": 1.0, "cortex.sensor_start_time": 0.4,
        "cortex.pipeline_power": 1.0, "cortex.person_pipeline_time": 1.0, "cortex.reject_pipeline_time": 0.5,
        "cortex.shutdown_power": 1.0, "cortex.shutdown_time": 0.0,
        "cortex.encode_power": 1.0, "cortex.clip_time": 10.0, "cortex.upload_power": 1.0,
        "cortex.clip_size": 1e6 * 8 / 8, "cortex.hosted_throughput": 8e6,  # 1e6 bits? -> see below
        "cortex.night_fraction": 0.0, "npu.npu_pj_per_mac": 0.0, "npu.npu_overhead_per_inference": 0.0,
        "radio.c6_awake_current": 0.0, "radio.c6_tx_avg_current": 0.0, "radio.reconnect_current": 0.0,
        "battery.self_discharge": 0.0, "battery.calendar_fade": 0.0,
        "battery.capacity": to_si(1000, "mAh"), "battery.usable_fraction": 1.0, "battery.deploy_temp": 25.0,
    })
    p["cortex.clip_size"] = 8e6  # bits (= 1 MB): upload 1 s at 8 Mbit/s
    return p


# ---------------------------------------------------------------- constants file contract
def test_constants_load_and_contract(const):
    c = const["npu.npu_pj_per_mac"]  # key consumed by ml/ (INTERFACES 6)
    assert c.unit == "pJ_per_MAC"
    assert math.isclose(c.raw_value, 3.6)
    assert 0 < c.low < c.value < c.high
    for name, k in const.entries.items():
        assert k.low <= k.value <= k.high, name
        assert k.status in C.STATUSES, name
        if k.status in C.NEEDS_SOURCE:
            assert k.src, name


def test_constant_sources_resolve_to_urls_or_repo_docs(const):
    for sid, s in const.sources.items():
        assert s.startswith(("https://", "docs/", "hardware/")), (sid, s)


def test_verify_ids_exist_in_register(const):
    reg = yaml.safe_load((HW / "spec" / "to_verify.yaml").read_text())["open"]
    for name, k in const.entries.items():
        if k.verify:
            assert k.verify in reg, (name, k.verify)
    tv = yaml.safe_load((HW / "power_model" / "constants.yaml").read_text())["tables"]["temp_derating"]["verify"]
    assert tv in reg


def test_loader_rejects_bad_entries(tmp_path):
    good = yaml.safe_load((HW / "power_model" / "constants.yaml").read_text())
    cases = [
        ("cortex", "boot_time", {"value": 1, "low": 2, "high": 3, "unit": "s", "status": "estimate"}),
        ("cortex", "boot_time", {"value": 1, "low": 0, "high": 3, "unit": "furlongs", "status": "estimate"}),
        ("cortex", "boot_time", {"value": 1, "low": 0, "high": 3, "unit": "s", "status": "datasheet"}),
        ("cortex", "boot_time", {"value": 1, "low": 0, "high": 3, "unit": "s", "status": "datasheet", "src": ["NOPE"]}),
        ("cortex", "boot_time", {"value": 1, "low": 0, "high": 3, "unit": "s", "status": "guess"}),
        ("cortex", "boot_time", {"value": 1, "unit": "s", "status": "estimate"}),
    ]
    for group, key, entry in cases:
        bad = yaml.safe_load(yaml.safe_dump(good))
        bad[group][key] = entry
        f = tmp_path / "c.yaml"
        f.write_text(yaml.safe_dump(bad))
        with pytest.raises(C.ConstantsError):
            C.load_constants(f)


# ---------------------------------------------------------------- units
def test_unit_conversions():
    assert to_si(1, "mA") == pytest.approx(1e-3)
    assert to_si(5000, "mAh") == pytest.approx(18000.0)  # coulombs
    assert to_si(3.6, "pJ_per_MAC") == pytest.approx(3.6e-12)
    assert to_si(1, "bytes") == 8
    assert to_si(100, "pct_per_month") * 730.5 * 3600 == pytest.approx(1.0)
    # 1 mA at 3.3 V = 3.3 mW
    assert to_si(1, "mA") * 3.3 == pytest.approx(to_si(3.3, "mW"))
    with pytest.raises(ValueError):
        to_si(1, "parsec")


EXPECTED_DIMENSIONS = {
    "_time": "time", "_power": "power", "_current": "current", "_energy": "energy",
    "_efficiency": "dimensionless", "_fraction": "dimensionless", "_v": "voltage",
}


def test_dimensions_match_names(const):
    checked = 0
    for name, k in const.entries.items():
        for suffix, dim in EXPECTED_DIMENSIONS.items():
            if name.endswith(suffix):
                assert UNITS[k.unit][0] == dim, (name, k.unit)
                checked += 1
    assert checked >= 20


def test_result_invariant_to_unit_choice(tmp_path, scen):
    """Restating constants in other units must not change the answer."""
    raw = yaml.safe_load((HW / "power_model" / "constants.yaml").read_text())
    ref = C.load_constants()
    raw["aon_loads"]["pir"].update(value=0.001, low=0.001, high=0.002, unit="mA")
    raw["aon_loads"]["c6_twt_wake_energy"].update(value=0.0279, low=0.010, high=0.060, unit="J")
    raw["cortex"]["boot_time"].update(value=150, low=80, high=500, unit="ms")
    raw["cortex"]["pipeline_power"].update(value=850, low=600, high=1300, unit="mW")
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump(raw))
    alt = C.load_constants(f)
    sc = scen["plan_mix_twt"]
    a = M.battery_life_hours(ref.nominal(), sc, ref.temp_derate)
    b = M.battery_life_hours(alt.nominal(), sc, alt.temp_derate)
    assert a == pytest.approx(b, rel=1e-12)


# ---------------------------------------------------------------- plan 4.4
def test_plan_table_reproduced():
    t = plan_table()
    assert t["total"] == pytest.approx(1.5e-3 + 98 / 86400 + 0.3e-3, rel=1e-12)
    assert t["total"] * 1e3 == pytest.approx(2.934, abs=5e-4)  # plan: "~2.9 mW"
    assert t["life_hours"] == pytest.approx(18.5 / t["total"])
    assert 8.5 < t["life_months"] < 9.0  # plan: "~9 months"
    assert t["person_inference"] * 1e3 == pytest.approx(0.35, abs=0.005)
    assert t["clip"] * 1e3 == pytest.approx(0.55, abs=0.01)
    assert t["false_wakes"] * 1e3 == pytest.approx(0.23, abs=0.005)


def test_plan_rows_partition_total(const, scen):
    for name in ("plan_mix_twt", "plan_mix_radio_off"):
        rows = model_as_plan_rows(const.nominal(), scen[name])
        ap = M.average_power(const.nominal(), scen[name])
        assert rows["total"] == pytest.approx(float(ap["total"]), rel=1e-12)


# ---------------------------------------------------------------- hand-computed cases
def test_standby_twt_hand(const):
    p = simple_params(const)
    sb = M.standby_breakdown(p, {"mode": "twt"})
    # 3.0 V x 100 uA + 30 mJ / 30 s = 0.3 mW + 1.0 mW
    assert float(sb["c6"]) == pytest.approx(1.3e-3)
    assert sum(float(v) for v in sb.values()) == pytest.approx(1.3e-3)


def test_standby_radio_off_hand(const):
    p = simple_params(const)
    p["battery_loads.charger_iq"] = 20e-6
    sb = M.standby_breakdown(p, {"mode": "radio_off"})
    assert float(sb["c6"]) == pytest.approx(3.0 * 10e-6)
    assert float(sb["charger"]) == pytest.approx(4.0 * 20e-6)
    assert float(M.standby_current_at_battery(p, {"mode": "radio_off"})) == pytest.approx((30e-6 + 80e-6) / 4.0)


def test_event_energy_hand(const):
    p = simple_params(const)
    # false wake: boot 0.1 + sensor 0.4 + reject 0.5 (all 1 W) = 1.0 J
    e = M.event_energy(p, "false_wake", "twt")
    assert float(e["base"]) == pytest.approx(1.0)
    assert float(e["clip"]) == 0
    # known person: 0.1 + 0.4 + 1.0 = 1.5 J
    assert float(M.event_energy(p, "person_known", "twt")["base"]) == pytest.approx(1.5)
    # unknown: base 1.5 J + clip (encode 10 s x 1 W + upload 1 s x 1 W) = 11 J
    e = M.event_energy(p, "person_unknown", "twt")
    assert float(e["clip"]) == pytest.approx(11.0)
    # radio: C6 awake 10 mA for the 1.5 s cortex window at 3 V = 45 mJ, + TX 100 mA x 0.2 s x 3 V = 60 mJ
    p.update({"radio.c6_awake_current": 10e-3, "radio.c6_tx_avg_current": 100e-3, "radio.publish_time": 0.2})
    e = M.event_energy(p, "person_known", "twt")
    assert float(e["base"]) == pytest.approx(1.5 + 0.045 + 0.060)
    # radio-off adds a reconnect: 100 mA x 2 s x 3 V = 0.6 J
    p.update({"radio.reconnect_current": 100e-3, "radio.reconnect_time": 2.0})
    e2 = M.event_energy(p, "person_known", "radio_off")
    assert float(e2["base"]) - float(e["base"]) == pytest.approx(0.6)
    # radio-off false wakes are batched: no publish, no reconnect
    f_off = M.event_energy(p, "false_wake", "radio_off")
    assert float(f_off["base"]) == pytest.approx(1.0 + 3.0 * 10e-3 * 1.0)


def test_ir_and_npu_terms_hand(const):
    p = simple_params(const)
    p.update({"cortex.night_fraction": 0.5, "cortex.ir_led_power": 2.0})
    # IR on during sensor start + pipeline: 0.5 x 2 W x (0.4 + 0.5) s = 0.9 J
    assert float(M.event_energy(p, "false_wake", "twt")["base"]) == pytest.approx(1.0 + 0.9)
    p = simple_params(const)
    p.update({"npu.npu_pj_per_mac": 4e-12, "npu.person_det_gmac": 1e9, "npu.face_stage_gmac": 0.5e9,
              "npu.npu_overhead_per_inference": 1e-4, "npu.frames_per_event": 4})
    # false wake: 4 x (1e9 x 4 pJ + 0.1 mJ) = 16.4 mJ; person adds 0.5e9 x 4 pJ + 2 x 0.1 mJ = 2.2 mJ
    assert float(M.npu_energy(p, "false_wake")) == pytest.approx(16.4e-3)
    assert float(M.npu_energy(p, "person_known")) == pytest.approx(18.6e-3)


def test_average_power_and_life_hand(const):
    p = simple_params(const)
    sc = {"mode": "twt", "rates": {"false_wake": 86.4, "person_known": 0, "person_unknown": 0, "button": 0}}
    ap = M.average_power(p, sc)
    # standby 1.3 mW + 86.4 J/day = 1.0 mW
    assert float(ap["total"]) == pytest.approx(2.3e-3)
    # 1000 mAh x 4 V = 14400 J; life = 14400 / 2.3e-3 s
    life = M.battery_life_hours(p, sc, const.temp_derate)
    assert float(life) == pytest.approx(14400 / 2.3e-3 / 3600)


def test_self_discharge_hand(const):
    p = simple_params(const)
    p.update({"battery.self_discharge": to_si(2.0, "pct_per_month"), "battery.self_discharge_soc_factor": 0.5})
    e_full = 14400.0
    assert float(M.self_discharge_power(p)) == pytest.approx(0.02 * e_full * 0.5 / (730.5 * 3600))
    p["battery.deploy_temp"] = 15.0  # halves per -10 C
    assert float(M.self_discharge_power(p)) == pytest.approx(0.01 * e_full * 0.5 / (730.5 * 3600))


def test_ageing_closed_form_is_fixed_point():
    e, pw, f = 50000.0, 3e-3, to_si(3.0, "pct_per_year")
    L = M.life_hours_from_power(pw, e, f) * 3600
    assert L == pytest.approx(e * (1 - f * L / 2) / pw)
    assert M.life_hours_from_power(pw, e, 0.0) * 3600 == pytest.approx(e / pw)


def test_temp_derating_interp(const):
    assert const.temp_derate(25.0) == pytest.approx(1.0)
    assert const.temp_derate(5.0) == pytest.approx((0.86 + 0.95) / 2)
    assert const.temp_derate(-40.0) == pytest.approx(0.60)  # clamped
    assert np.all(np.diff(const.temp_table[:, 1]) >= 0)


# ---------------------------------------------------------------- monotonicity
MORE_IS_WORSE = {
    "battery.self_discharge", "battery.self_discharge_soc_factor", "battery.protection_ic", "battery.calendar_fade",
    "rails.tps62840_iq", "rails.load_switch_off",
    "aon_loads.c6_light_sleep", "aon_loads.c6_deep_sleep", "aon_loads.c6_lp_core_overhead", "aon_loads.c6_twt_wake_energy",
    "aon_loads.pir", "aon_loads.light_sensor", "aon_loads.secure_element", "aon_loads.radar_populated",
    "battery_loads.charger_iq", "battery_loads.gauge",
    "cortex.boot_power", "cortex.boot_time", "cortex.sensor_start_power", "cortex.sensor_start_time",
    "cortex.pipeline_power", "cortex.person_pipeline_time", "cortex.reject_pipeline_time",
    "cortex.encode_power", "cortex.clip_time", "cortex.clip_size", "cortex.upload_power",
    "cortex.shutdown_power", "cortex.shutdown_time", "cortex.ir_led_power", "cortex.night_fraction",
    "npu.npu_pj_per_mac", "npu.npu_overhead_per_inference", "npu.person_det_gmac", "npu.face_stage_gmac", "npu.frames_per_event",
    "radio.c6_awake_current", "radio.c6_tx_avg_current", "radio.publish_time", "radio.reconnect_time", "radio.reconnect_current",
}
MORE_IS_BETTER = {
    "battery.capacity", "battery.usable_fraction", "rails.aon_efficiency", "rails.cortex_efficiency",
    "aon_loads.twt_interval", "cortex.hosted_throughput",
}
NON_MONOTONIC = {"battery.nominal_v", "battery.deploy_temp"}  # nominal_v: scales both capacity and Iq power


def test_every_uncertain_constant_is_classified(const):
    unc = {n for n, c in const.entries.items() if c.uncertain}
    unclassified = unc - MORE_IS_WORSE - MORE_IS_BETTER - NON_MONOTONIC
    assert not unclassified, f"classify these for the monotonicity test: {sorted(unclassified)}"


@pytest.mark.parametrize("scenario", ["plan_mix_twt", "plan_mix_radio_off", "plan_mix_twt_radar"])
def test_monotonic_in_constants(const, scen, scenario):
    sc = scen[scenario]
    for name, c in const.entries.items():
        if not c.uncertain or name in NON_MONOTONIC:
            continue
        lo = float(M.battery_life_hours(const.at(name, "low"), sc, const.temp_derate))
        hi = float(M.battery_life_hours(const.at(name, "high"), sc, const.temp_derate))
        if name in MORE_IS_WORSE:
            assert hi <= lo + 1e-9, name
        else:
            assert hi >= lo - 1e-9, name


def test_monotonic_in_event_rates(const, scen):
    nom = const.nominal()
    sc = scen["plan_mix_twt"]
    base = float(M.battery_life_hours(nom, sc, const.temp_derate))
    for kind in ("person_known", "person_unknown", "false_wake", "button", "status_publish"):
        r = dict(sc["rates"]); r[kind] = r.get(kind, 0) + 5
        assert float(M.battery_life_hours(nom, sc, const.temp_derate, r)) < base, kind


def test_event_energy_ordering(const):
    nom = const.nominal()
    for mode in ("twt", "radio_off"):
        e = {k: sum(float(v) for v in M.event_energy(nom, k, mode).values()) for k in M.EVENT_KINDS}
        assert e["false_wake"] < e["person_known"] < e["person_unknown"]
        assert e["button"] == pytest.approx(e["person_unknown"])
    assert sum(M.event_energy(nom, "person_known", "radio_off").values()) > sum(M.event_energy(nom, "person_known", "twt").values())


def test_npu_compute_is_minor_share_of_event(const):
    """Key finding: per-MAC NPU energy is small next to system overhead (sensor, boot, radio)."""
    nom = const.nominal()
    for kind in ("false_wake", "person_known"):
        share = float(M.npu_energy(nom, kind)) / float(M.event_energy(nom, kind, "twt")["base"])
        assert share < 0.05, (kind, share)


def test_radio_off_beats_twt(const, scen):
    nom = const.nominal()
    a = M.battery_life_hours(nom, scen["plan_mix_twt"], const.temp_derate)
    b = M.battery_life_hours(nom, scen["plan_mix_radio_off"], const.temp_derate)
    assert b > a


def test_vectorised_matches_scalar(const, scen):
    rng = np.random.default_rng(0)
    s = const.sample(rng, 7)
    sc = scen["plan_mix_twt"]
    vec = M.battery_life_hours(s, sc, const.temp_derate)
    for i in range(7):
        pi = {k: float(v[i]) for k, v in s.items()}
        assert float(M.battery_life_hours(pi, sc, const.temp_derate)) == pytest.approx(float(vec[i]))


# ---------------------------------------------------------------- Monte-Carlo and sensitivity
def test_mc_reproducible_and_ordered(const, scen):
    a = run_mc(const, scen["plan_mix_twt"], n=3000, seed=7).summary()
    b = run_mc(const, scen["plan_mix_twt"], n=3000, seed=7).summary()
    assert a == b
    assert a["p5_months"] < a["p25_months"] < a["median_months"] < a["p75_months"] < a["p95_months"]
    assert 0 <= a["prob_ge_12_months"] <= a["prob_ge_9_months"] <= a["prob_ge_6_months"] <= 1


def test_mc_collapses_to_nominal_without_uncertainty(tmp_path, scen):
    raw = yaml.safe_load((HW / "power_model" / "constants.yaml").read_text())
    for g, items in raw.items():
        if g in C.NON_CONSTANT_GROUPS:
            continue
        for d in items.values():
            d["low"] = d["high"] = d["value"]
    f = tmp_path / "c.yaml"
    f.write_text(yaml.safe_dump(raw))
    const = C.load_constants(f)
    sc = dict(scen["plan_mix_twt"]); sc["rate_cv"] = 0.0
    res = run_mc(const, sc, n=4000, seed=3)
    nominal = float(M.battery_life_hours(const.nominal(), sc, const.temp_derate))
    # only Poisson count noise remains; it averages out over a 180-day horizon
    assert np.median(res.life_hours) == pytest.approx(nominal, rel=0.01)
    assert np.std(res.life_hours) / nominal < 0.02


def test_sample_rates_mean(scen):
    rng = np.random.default_rng(5)
    r = sample_rates(scen["plan_mix_twt"], rng, 40000)
    assert r["false_wake"].mean() == pytest.approx(20, rel=0.02)
    assert np.all(r["button"] == 0)


def test_tornado_and_spearman(const, scen):
    rows = tornado(const, scen["plan_mix_twt"])
    swings = [r["swing_h"] for r in rows]
    assert swings == sorted(swings, reverse=True)
    top = {r["name"] for r in rows[:3]}
    assert {"aon_loads.twt_interval", "aon_loads.c6_twt_wake_energy"} <= top  # TWT dominates the associated mode
    res = run_mc(const, scen["plan_mix_twt"], n=4000, seed=2)
    sp = rank_sensitivity(res)
    assert abs(sp[0]["spearman"]) >= abs(sp[-1]["spearman"])
    x = np.arange(100.0)
    assert spearman(x, x**3) == pytest.approx(1.0)
    assert spearman(x, -x) == pytest.approx(-1.0)


def test_report_runs(tmp_path):
    from sentinel_power.report import main

    assert main(["--n", "500", "--no-plots", "--out", str(tmp_path)]) == 0
    md = (tmp_path / "report.md").read_text()
    assert "Plan section 4.4" in md and "plan_mix_radio_off" in md
