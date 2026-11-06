"""Average-power and battery-life model.

All inputs are SI (see units.py). Every function is elementwise over numpy arrays, so the same
code computes the nominal point and 10^5 Monte-Carlo draws.

Energy bookkeeping is done at the BATTERY terminals:
  * always-on (3V3_AON) loads are divided by the TPS62840 efficiency,
  * cortex-domain loads are divided by the cortex converter efficiency,
  * charger/gauge/PCM/regulator-Iq drains are at battery voltage directly,
  * IR LED power is specified battery-side (constant-current driver from SYS).
"""

from __future__ import annotations

from typing import Mapping

import numpy as np

from .units import SECONDS_PER_DAY, SECONDS_PER_HOUR

EVENT_KINDS = ("person_known", "person_unknown", "false_wake", "button")
PERIODIC_KINDS = ("status_publish", "checkin")
CLIP_KINDS = {"person_unknown", "button"}
PERSON_KINDS = {"person_known", "person_unknown", "button"}

Params = Mapping[str, "float | np.ndarray"]


def _mode(scenario: Mapping) -> str:
    m = scenario["mode"]
    if m not in ("twt", "radio_off"):
        raise ValueError(f"bad mode {m}")
    return m


# ---------------------------------------------------------------------------------------------
# Standby
# ---------------------------------------------------------------------------------------------
def standby_breakdown(p: Params, scenario: Mapping) -> dict[str, np.ndarray]:
    """Battery-side standby power components in W."""
    mode = _mode(scenario)
    v_aon = p["rails.aon_v"]
    eff = p["rails.aon_efficiency"]
    v_bat = p["battery.nominal_v"]

    if mode == "twt":
        p_c6_rail = v_aon * p["aon_loads.c6_light_sleep"] + p["aon_loads.c6_twt_wake_energy"] / p["aon_loads.twt_interval"]
    else:
        p_c6_rail = v_aon * (p["aon_loads.c6_deep_sleep"] + p["aon_loads.c6_lp_core_overhead"])

    radar = p["aon_loads.radar_populated"] if scenario.get("radar_populated") else p["aon_loads.radar"]
    i_sensors = p["aon_loads.pir"] + p["aon_loads.light_sensor"] + p["aon_loads.secure_element"] + radar

    return {
        "c6": p_c6_rail / eff,
        "sensors": v_aon * i_sensors / eff,
        "regulators_switches": v_bat * (p["rails.tps62840_iq"] + p["rails.n_load_switches"] * p["rails.load_switch_off"]),
        "charger": v_bat * p["battery_loads.charger_iq"],
        "gauge": v_bat * p["battery_loads.gauge"],
        "pack_protection": v_bat * p["battery.protection_ic"],
    }


def standby_current_at_battery(p: Params, scenario: Mapping):
    """Average standby current drawn from the cell (A), for comparison with G2."""
    return sum(standby_breakdown(p, scenario).values()) / p["battery.nominal_v"]


# ---------------------------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------------------------
def npu_energy(p: Params, kind: str):
    """Core-domain NPU energy for one event (J): per-frame person detector, plus face stages."""
    frames = p["npu.frames_per_event"]
    pj = p["npu.npu_pj_per_mac"]
    e0 = p["npu.npu_overhead_per_inference"]
    e = frames * (p["npu.person_det_gmac"] * pj + e0)
    if kind in PERSON_KINDS:
        e = e + p["npu.face_stage_gmac"] * pj + 2 * e0  # face detect + embed
    return e


def upload_time(p: Params):
    return p["cortex.clip_size"] / p["cortex.hosted_throughput"]


def event_energy(p: Params, kind: str, mode: str) -> dict[str, np.ndarray]:
    """Battery-side energy (J) of one event, split into 'base' and 'clip' parts."""
    if kind not in EVENT_KINDS:
        raise ValueError(kind)
    eff_c = p["rails.cortex_efficiency"]
    eff_a = p["rails.aon_efficiency"]
    v_aon = p["rails.aon_v"]
    night = p["cortex.night_fraction"]
    ir = p["cortex.ir_led_power"]

    pipe_t = p["cortex.person_pipeline_time"] if kind in PERSON_KINDS else p["cortex.reject_pipeline_time"]
    base_t_on = p["cortex.boot_time"] + p["cortex.sensor_start_time"] + pipe_t + p["cortex.shutdown_time"]
    e_cortex = (
        p["cortex.boot_power"] * p["cortex.boot_time"]
        + p["cortex.sensor_start_power"] * p["cortex.sensor_start_time"]
        + p["cortex.pipeline_power"] * pipe_t
        + p["cortex.shutdown_power"] * p["cortex.shutdown_time"]
        + npu_energy(p, kind)
    ) / eff_c
    e_ir = night * ir * (p["cortex.sensor_start_time"] + pipe_t)

    # Radio: the C6 is awake (modem sleep) for the whole cortex-on window, then publishes.
    needs_net = (mode == "twt") or (kind != "false_wake")  # radio-off batches false-wake logs
    e_radio = v_aon * p["radio.c6_awake_current"] * base_t_on
    if needs_net:
        e_radio = e_radio + v_aon * p["radio.c6_tx_avg_current"] * p["radio.publish_time"]
    if mode == "radio_off" and needs_net:
        e_radio = e_radio + v_aon * p["radio.reconnect_current"] * p["radio.reconnect_time"]
    e_radio = e_radio / eff_a

    base = e_cortex + e_ir + e_radio
    if kind in CLIP_KINDS:
        t_up = upload_time(p)
        clip_t = p["cortex.clip_time"]
        e_clip = (p["cortex.encode_power"] * clip_t + p["cortex.upload_power"] * t_up) / eff_c
        e_clip = e_clip + night * ir * clip_t
        e_clip = e_clip + v_aon * (p["radio.c6_awake_current"] * clip_t + p["radio.c6_tx_avg_current"] * t_up) / eff_a
    else:
        e_clip = 0.0 * base
    return {"base": base, "clip": e_clip}


def periodic_energy(p: Params, kind: str, mode: str):
    """Battery-side energy (J) of one periodic radio action (no cortex)."""
    v_aon = p["rails.aon_v"]
    eff_a = p["rails.aon_efficiency"]
    tx = v_aon * p["radio.c6_tx_avg_current"] * p["radio.publish_time"]
    if kind == "status_publish":
        if mode != "twt":
            raise ValueError("status_publish only applies to twt mode; use checkin for radio_off")
        return tx / eff_a
    if kind == "checkin":
        return (tx + v_aon * p["radio.reconnect_current"] * p["radio.reconnect_time"]) / eff_a
    raise ValueError(kind)


# ---------------------------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------------------------
def full_energy_j(p: Params):
    """Nameplate energy (J): capacity (C) x nominal voltage."""
    return p["battery.capacity"] * p["battery.nominal_v"]


def self_discharge_power(p: Params):
    t = p["battery.deploy_temp"]
    rate = p["battery.self_discharge"] * np.power(2.0, (t - 25.0) / 10.0)  # fraction / s
    return rate * full_energy_j(p) * p["battery.self_discharge_soc_factor"]


def average_power(p: Params, scenario: Mapping, rates: Mapping | None = None) -> dict[str, np.ndarray]:
    """Battery-side average power (W) by component. `rates` (events/day) overrides scenario rates."""
    mode = _mode(scenario)
    rates = dict(scenario["rates"] if rates is None else rates)
    out: dict[str, np.ndarray] = {f"standby.{k}": v for k, v in standby_breakdown(p, scenario).items()}
    for kind in EVENT_KINDS:
        r = rates.get(kind, 0.0)
        e = event_energy(p, kind, mode)
        out[f"event.{kind}"] = r * e["base"] / SECONDS_PER_DAY
        out[f"clip.{kind}"] = r * e["clip"] / SECONDS_PER_DAY
    for kind in PERIODIC_KINDS:
        r = rates.get(kind, 0.0)
        if np.any(np.asarray(r) > 0):
            out[f"periodic.{kind}"] = r * periodic_energy(p, kind, mode) / SECONDS_PER_DAY
    out["self_discharge"] = self_discharge_power(p)
    out["total"] = sum(v for k, v in out.items())
    return out


def usable_energy_j(p: Params, temp_derate):
    """Energy actually available (J) before ageing: nameplate x usable fraction x temperature derate."""
    return full_energy_j(p) * p["battery.usable_fraction"] * temp_derate(p["battery.deploy_temp"])


def life_hours_from_power(p_total, e_use, fade_per_s):
    """Closed form with linear capacity fade: L = E(1 - f L / 2) / P  =>  L = E / (P + E f / 2)."""
    return e_use / (p_total + e_use * fade_per_s / 2.0) / SECONDS_PER_HOUR


def battery_life_hours(p: Params, scenario: Mapping, temp_derate, rates: Mapping | None = None):
    p_tot = average_power(p, scenario, rates)["total"]
    return life_hours_from_power(p_tot, usable_energy_j(p, temp_derate), p["battery.calendar_fade"])
