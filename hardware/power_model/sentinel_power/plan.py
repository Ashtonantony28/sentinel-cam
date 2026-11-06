"""Reproduce docs/PLAN.md section 4.4 exactly, and map the detailed model onto the same rows."""

from __future__ import annotations

from typing import Mapping

from .model import average_power
from .units import HOURS_PER_MONTH, SECONDS_PER_DAY

PLAN_BATTERY_WH = 18.5  # 5000 mAh x 3.7 V
PLAN_ROWS = {
    # row: (power W) -- energy rows are J x events/day / 86400
    "standby": 1.5e-3,
    "person_inference": 3.0 * 10 / SECONDS_PER_DAY,
    "clip": 12.0 * 4 / SECONDS_PER_DAY,
    "false_wakes": 1.0 * 20 / SECONDS_PER_DAY,
    "self_discharge_and_leakage": 0.3e-3,
}


def plan_table() -> dict[str, float]:
    rows = dict(PLAN_ROWS)
    rows["total"] = sum(PLAN_ROWS.values())
    hours = PLAN_BATTERY_WH / rows["total"]
    rows["life_hours"] = hours
    rows["life_months"] = hours / HOURS_PER_MONTH
    return rows


def model_as_plan_rows(p: Mapping, scenario: Mapping) -> dict[str, float]:
    """Group the detailed model's components into the plan's five rows (W)."""
    ap = average_power(p, scenario)
    standby = sum(v for k, v in ap.items() if k.startswith("standby.") and k not in ("standby.charger", "standby.gauge", "standby.pack_protection"))
    standby += ap.get("periodic.status_publish", 0.0) + ap.get("periodic.checkin", 0.0)
    person = sum(ap[f"event.{k}"] for k in ("person_known", "person_unknown", "button"))
    clip = sum(v for k, v in ap.items() if k.startswith("clip."))
    false = ap["event.false_wake"]
    leak = ap["self_discharge"] + ap["standby.charger"] + ap["standby.gauge"] + ap["standby.pack_protection"]
    rows = {
        "standby": standby,
        "person_inference": person,
        "clip": clip,
        "false_wakes": false,
        "self_discharge_and_leakage": leak,
    }
    rows["total"] = sum(rows.values())
    return rows
