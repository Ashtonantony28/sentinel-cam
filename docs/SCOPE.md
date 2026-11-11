# Sentinel: scope and work breakdown

Decisions taken (plan §10, using the plan's recommendations, approved 2026-11-06):
- SoC: STM32N6 (vision "cortex") + ESP32-C6 (always-on "brainstem").
- Budget: ~$1.5k path (Rev 0 dev kits → Rev A → Rev B).
- v1 scope: audio and mmWave radar get footprints on Rev A but are DNP-able; firmware treats them as optional.
- Backend: self-hosted (MQTT + FastAPI + S3-compatible store + Flower).
- Repo layout: `ml/`, `firmware/`, `backend/`, `fl/`, `hardware/`, `tools/`, `docs/`.

## Phase 1: everything that needs no hardware (now)

| WS | Directory | Deliverable | How it is tested without hardware |
|----|-----------|-------------|-----------------------------------|
| WS1 ML | `ml/` | Person detector, face detector, MobileFaceNet embedder in PyTorch; QAT → int8 ONNX export; NPU-fit checker; eval harness (mAP, TAR@FAR, latency/energy model) | pytest on CPU with synthetic data; export round-trip parity float vs int8 |
| WS2 Firmware core | `firmware/core/` | Portable C11 libs: event pipeline/policy state machine, brainstem power-sequencer state machine, int8 gallery matcher, wake debouncer, HAL interface | CMake + ctest on host, simulated HAL, scenario tests |
| WS3 OTA + security | `firmware/ota/`, `tools/` | A/B slot manager with trial boot, health confirm, rollback, anti-rollback counter; manifest parser; ECDSA P-256 verify; Python signing tool | ctest on host with simulated flash; randomized power-cut fault injection (≥ 1000 runs, zero bricks) |
| WS4 Backend + FL | `backend/`, `fl/` | MQTT event ingest, clip store, REST API, dashboard stub; federated learning of the false-alert head with FedAvg/FedProx, DP clipping+noise, pairwise-mask secure aggregation; simulated non-IID "homes" | pytest; FL simulation shows result vs local-only baseline |
| WS5 Hardware | `hardware/` | Power/battery-life model, BOM with fallbacks, rail + sequencing spec, pin/interface map, Rev 0 bench wiring, schematic review checklist | pytest on the power model; BOM consistency checks |

Shared contracts live in `docs/INTERFACES.md`. A workstream may not change a contract silently: if it must, it notes the change at the top of its README.

## Phase 2: needs hardware (after dev kits arrive)
M1–M3 on the STM32N6570-DK + ESP32-C6 DevKit: port WS2/WS3 libs onto real HAL, run WS1 models through ST Edge AI Core, measure power with a PPK2. Then KiCad Rev A (M4), bring-up (M6), field test (M8).

## Testing rule
Every workstream ships tests, and `make test` at the repo root runs all of them. CI (`.github/workflows/ci.yml`) runs the same target.

## Integration status (2026-11-07)

`make test` at the root runs every workstream plus `tests/integration` (cross-workstream
contract tests), from a clean checkout (`make clean-build` removes all build dirs and venvs).
CI: `.github/workflows/ci.yml` runs root `make test` on ubuntu-latest / Python 3.13, plus an
OTA power-cut soak job with two extra seeds.

| Boundary | How it is held | Test |
|---|---|---|
| hardware → firmware energy | `SN_POWER_MODEL_DEFAULT` is generated from `hardware/power_model/constants.yaml` by `tools/gen_fw_power_model.py` (reuses `sentinel_power`); firmware energy tests derive their expectations from it | `tools/tests/test_gen_fw_power_model.py` fails on a stale `sn_power_model_gen.c` |
| ml → hardware | `npu.person_det_gmac` / `npu.face_stage_gmac` hold ml's MAC counts (status `measured_on_model`) | hardware pytest |
| firmware → backend (§3 event) | event JSON from `event_json_dump` and from the scripted 24 h day validates with `EventMsg` and ingests via the in-memory bus into SQLite | `tests/integration/test_event_status_ingest.py` |
| firmware → backend (§3 status) | new `sn_status` writer; brainstem fault reports map to `LOW_BATTERY / BROWNOUT / POWER_FAULT / JOB_TIMEOUT` | same file + `core.test_status.*` |
| ml → tools → firmware/ota (§4 model OTA) | int8 ONNX export → `npu_fit` op-set hash → `sentinel_sign` kind=model → `ota_host_verify`: accepted when the firmware op-set hash matches, `ERR_OPSET` otherwise | `tests/integration/test_model_ota.py` |
| fl → firmware (§5 head) | new `sn_head` reader of the 65-float LE blob; probabilities equal fl's numpy within 1e-6 | `tests/integration/test_fl_head.py` + `core.test_head.*` |

Energy models (updated 2026-11-11): the generated firmware constants are now battery-side
(cortex powers divided by `rails.cortex_efficiency`, clip upload folded into `cortex_clip_mw`).
The firmware's scripted day averages 3.29 mW (235 days); it differs from the hardware model's
3.11 mW plan mix because the scripted day includes a PIR storm, a brown-out and more events. It
still leaves out the C6's radio-awake current and IR LEDs. The hardware model's battery life is
the number of record.
