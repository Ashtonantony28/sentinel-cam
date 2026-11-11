#!/usr/bin/env python3
"""gen_fw_power_model: derive the firmware power model from hardware/power_model/constants.yaml.

constants.yaml is the single source of truth for power numbers (docs/INTERFACES.md section 6).
This tool turns it into firmware/core/src/sn_power_model_gen.c, which defines
SN_POWER_MODEL_DEFAULT (struct in firmware/core/include/sn_energy.h). It reuses the hardware
model (hardware/power_model/sentinel_power) so no formula is written twice.

    python tools/gen_fw_power_model.py            # (re)write the generated C file
    python tools/gen_fw_power_model.py --check    # exit 1 if the checked-in file is stale
    python tools/gen_fw_power_model.py --print    # show the derived fields

Field mapping (nominal values; integers rounded half away from zero):
  standby_uw       battery-side standby power of the hardware model in TWT mode (scenario
                   plan_mix_twt: C6 light sleep + TWT service periods, sensors, regulator Iq,
                   charger, gauge, pack protection), i.e. sum(standby_breakdown()).
  All cortex fields are battery-side: rail-side power / rails.cortex_efficiency.
  cortex_boot_mw   cortex.boot_power
  cortex_active_mw cortex.pipeline_power
  cortex_clip_mw   average over the clip window of encode (encode_power x clip_time) plus the
                   clip upload (upload_power x clip_size / hosted_throughput)
  boot_est_mj      cortex.boot_power x cortex.boot_time
  uplink_est_mj    cortex.pipeline_power x radio.publish_time.
                   Rationale: in firmware, uplink_est_mj is charged to EVERY event (not only
                   clip events) for the time the N6 stays up at active power while the event
                   JSON is handed to the C6 and published (sn_pipeline adds it after the
                   record; the C6 integrates cortex_active_mw over the same window). The clip
                   upload itself (cortex.upload_power x clip_size / hosted_throughput, ~0.4 J)
                   only applies to clip events, so it must not go into a per-event constant.
                   It is folded into cortex_clip_mw instead.
Everything is battery-side, matching the hardware model (changed 2026-11-11; before that the
cortex fields were rail-side and the firmware day average was optimistic).
"""
from __future__ import annotations

import argparse
import hashlib
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
POWER_MODEL_DIR = REPO / "hardware" / "power_model"
CONSTANTS = POWER_MODEL_DIR / "constants.yaml"
SCENARIOS = POWER_MODEL_DIR / "scenarios.yaml"
OUT = REPO / "firmware" / "core" / "src" / "sn_power_model_gen.c"
STANDBY_SCENARIO = "plan_mix_twt"

FIELDS = ("standby_uw", "cortex_boot_mw", "cortex_active_mw", "cortex_clip_mw", "boot_est_mj", "uplink_est_mj")


def _sentinel_power():
    if str(POWER_MODEL_DIR) not in sys.path:
        sys.path.insert(0, str(POWER_MODEL_DIR))
    import sentinel_power  # noqa: PLC0415  (hardware/power_model/sentinel_power)

    return sentinel_power


def round_half_away(x: float) -> int:
    return int(math.floor(abs(x) + 0.5)) * (1 if x >= 0 else -1)


def derive(constants_path: Path = CONSTANTS, scenarios_path: Path = SCENARIOS) -> dict:
    """Returns {field: (int value, exact float, explanation)}."""
    sp = _sentinel_power()
    const = sp.load_constants(constants_path)
    scen = sp.load_scenarios(scenarios_path)[STANDBY_SCENARIO]
    p = const.nominal()  # SI
    standby_w = float(sum(sp.standby_breakdown(p, scen).values()))
    eta = p["rails.cortex_efficiency"]
    upload_s = p["cortex.clip_size"] / p["cortex.hosted_throughput"]  # clip_size is in bits after SI conversion
    clip_j = p["cortex.encode_power"] * p["cortex.clip_time"] + p["cortex.upload_power"] * upload_s
    exact = {
        "standby_uw": (standby_w * 1e6, f"hardware model standby at the battery, {STANDBY_SCENARIO} (TWT)"),
        "cortex_boot_mw": (p["cortex.boot_power"] / eta * 1e3, "cortex.boot_power / rails.cortex_efficiency"),
        "cortex_active_mw": (p["cortex.pipeline_power"] / eta * 1e3, "cortex.pipeline_power / rails.cortex_efficiency"),
        "cortex_clip_mw": (clip_j / p["cortex.clip_time"] / eta * 1e3,
                           "(encode_power x clip_time + upload_power x clip_size/hosted_throughput) / clip_time / rails.cortex_efficiency"),
        "boot_est_mj": (p["cortex.boot_power"] * p["cortex.boot_time"] / eta * 1e3,
                        "cortex.boot_power x cortex.boot_time / rails.cortex_efficiency"),
        "uplink_est_mj": (p["cortex.pipeline_power"] * p["radio.publish_time"] / eta * 1e3,
                          "cortex.pipeline_power x radio.publish_time / rails.cortex_efficiency"),
    }
    out = {}
    for k in FIELDS:
        v, why = exact[k]
        iv = round_half_away(v)
        if not (0 < iv < 2**32):
            raise ValueError(f"{k} = {v} does not fit a non-zero uint32")
        out[k] = (iv, v, why)
    return out


def source_hash(constants_path: Path = CONSTANTS, scenarios_path: Path = SCENARIOS) -> str:
    h = hashlib.sha256()
    h.update(Path(constants_path).read_bytes())
    h.update(Path(scenarios_path).read_bytes())
    return h.hexdigest()


def render(constants_path: Path = CONSTANTS, scenarios_path: Path = SCENARIOS) -> str:
    d = derive(constants_path, scenarios_path)
    units = {"standby_uw": "uW", "boot_est_mj": "mJ", "uplink_est_mj": "mJ"}
    width = max(len(k) for k in FIELDS)
    lines = [
        "/*",
        " * GENERATED FILE - DO NOT EDIT.",
        " * Generator: tools/gen_fw_power_model.py (run it to regenerate; its --check mode is a test).",
        " * Source:    hardware/power_model/constants.yaml + scenarios.yaml (INTERFACES.md section 6)",
        f" * Source sha256: {source_hash(constants_path, scenarios_path)}",
        " *",
        " * Firmware power model (struct in include/sn_energy.h), nominal constants:",
    ]
    for k in FIELDS:
        iv, v, why = d[k]
        lines.append(f" *   {k:<{width}} = {why} = {v:.3f} {units.get(k, 'mW')}")
    lines += [
        " * Cortex powers are rail-side (load); standby is battery-side. See the generator's docstring.",
        " */",
        '#include "sn_energy.h"',
        "",
        "const sn_power_model_t SN_POWER_MODEL_DEFAULT = {",
    ]
    for k in FIELDS:
        lines.append(f"    .{k}{' ' * (width - len(k))} = {d[k][0]}u,")
    lines += ["};", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--constants", type=Path, default=CONSTANTS)
    ap.add_argument("--scenarios", type=Path, default=SCENARIOS)
    ap.add_argument("--out", type=Path, default=OUT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="fail if --out differs from what would be generated")
    g.add_argument("--print", action="store_true", help="print the derived fields")
    a = ap.parse_args(argv)
    text = render(a.constants, a.scenarios)
    if a.print:
        for k, (iv, v, why) in derive(a.constants, a.scenarios).items():
            print(f"{k:<17} {iv:>7}   ({v:.3f}; {why})")
        return 0
    if a.check:
        cur = a.out.read_text() if a.out.exists() else ""
        if cur != text:
            print(f"STALE: {a.out} does not match {a.constants}; run tools/gen_fw_power_model.py", file=sys.stderr)
            return 1
        print(f"OK: {a.out} is up to date")
        return 0
    a.out.write_text(text)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
