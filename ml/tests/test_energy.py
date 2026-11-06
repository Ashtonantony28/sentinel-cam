import pytest

from sentinel_ml.eval.energy import (DEFAULT_PJ_PER_MAC, energy_mj, load_energy_constants, load_pj_per_mac,
                                     measure_latency_ms)


def test_default_when_missing(tmp_path):
    v, src = load_pj_per_mac(str(tmp_path / "nope.yaml"))
    assert v == DEFAULT_PJ_PER_MAC == 1.0 and "default" in src


def test_reads_nested_and_flat(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("npu_pj_per_mac: 0.35\n")
    assert load_pj_per_mac(str(p))[0] == 0.35
    p.write_text("compute:\n  npu:\n    npu_pj_per_mac: 2.5\n")
    assert load_pj_per_mac(str(p))[0] == 2.5
    p.write_text("npu_pj_per_mac: {value: 0.8, unit: pJ}\n")
    assert load_pj_per_mac(str(p))[0] == 0.8
    p.write_text("other: 1\n")
    assert load_pj_per_mac(str(p))[0] == DEFAULT_PJ_PER_MAC


def test_energy_units():
    assert energy_mj(100_000_000, 1.0) == pytest.approx(0.1)  # 1e8 MAC x 1 pJ = 0.1 mJ


def test_latency_helper():
    r = measure_latency_ms(lambda x: sum(range(1000)), None, warmup=1, runs=5)
    assert r["median_ms"] >= 0 and r["runs"] == 5


def test_energy_constants_range_and_overhead(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("npu:\n  npu_pj_per_mac: {value: 3.6, low: 0.67, high: 16.0, unit: pJ_per_MAC}\n"
                 "  npu_overhead_per_inference: {value: 0.153, unit: mJ}\n")
    c = load_energy_constants(str(p))
    assert (c["pj_per_mac"], c["low"], c["high"], c["overhead_mj"]) == (3.6, 0.67, 16.0, 0.153)
    c = load_energy_constants(str(tmp_path / "missing.yaml"))
    assert c["pj_per_mac"] == 1.0 and c["overhead_mj"] is None
