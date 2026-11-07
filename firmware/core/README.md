# firmware/core (WS2): Sentinel firmware core

Portable C11 libraries for both chips, a simulated HAL, and the tests that drive them on a
host. The same sources are meant to be compiled for the STM32N6 "cortex" (Cortex-M55) and
the ESP32-C6 "brainstem" (RV32IMAC).

## Contract notes (read first)

None of these change `docs/INTERFACES.md`. They pin down points the contract leaves open,
so the device matches what WS1 (`ml/golden`) and WS4 (backend) assume.

1. **Quantization rounding (§1).** The contract says `round(...)`. The device uses
   round-half-away-from-zero in float32 (C `roundf`), which is what the `ml/golden`
   generator uses. NaN becomes 0 and ±inf clamps to ±127. numpy and torch round half to
   even, so WS1 export code must use the same rule. The golden test checks this.
2. **Gallery blob validity (§1).** `n_protos` must be between 1 and 20; 0 is rejected as
   `BAD_PROTO_COUNT`. `threshold_q15` is taken literally (0 does not mean "use the
   default"); writers put 18022 (0.55) for the default. Checks run in the order the golden
   MANIFEST gives: length, CRC, magic, version, people count, then each person, trailing
   bytes, duplicate ids. Error names match the golden names.
3. **Match rule edge cases (§1).** If the gallery has one person, `second = -1`, so the
   margin rule always passes. If two people tie for best, the first in gallery order wins
   and the margin rule then fails. Both comparisons allow a 1e-9 slack in favour of a match,
   so float64 references agree exactly at the boundary.
4. **Policy details (§2), where the contract is silent:**
   - A person with no usable face (none detected, or embedding failed) gets
     `NOTIFY_UNKNOWN_WITH_CLIP`.
   - Several faces give `LOG_KNOWN` only if every face matched a known person. Otherwise
     the event is `NOTIFY_UNKNOWN_WITH_CLIP`.
   - `RING` records a clip only when a person was present.
   - The 2-of-4 person rule stops early once the answer is settled (2 hits, or 2 hits no
     longer reachable). The decision is the same, and frames are saved.
   - `person_conf` is the highest confidence over the frames that were evaluated.
5. **Event JSON (§3).** Fields appear in the contract's order with no extra fields. Floats
   use 4 decimals. `clip_id` is `<dev>-<ts_ms as 16 lowercase hex digits>`. `energy_mj` is
   the N6's estimate: the boot energy reported by the C6, plus active power × time, plus
   clip power × clip time, plus an uplink estimate. Over the scripted day it is within 1%
   of what the C6 integrates.
6. **Faults go on the §3 status topic.** When the brainstem refuses or loses the cortex
   (low battery, brown-out, power fault, job timeout) there is no §3 event, because the N6
   never ran. `sn_status` maps each `sn_bs_ops_t.report` to a reason
   (`sn_status_reason_from_report`: LOW_BATT_REFUSED → `LOW_BATTERY`, POWER_FAULT →
   `POWER_FAULT`, CORTEX_LOST → `BROWNOUT` if the power error was a brown-out, else
   `POWER_FAULT`, JOB_TIMEOUT → `JOB_TIMEOUT`; WAKE_DROPPED is not a fault) and
   `sn_status_to_json` writes `{"state","reason","ts","battery_mv"}`. Tightening: the
   writer only emits a reason with `state:"fault"` and refuses `fault` without one (the
   backend requires the latter and tolerates a reason on online/offline).
7. **Energy constants are generated.** `src/sn_power_model_gen.c` (`SN_POWER_MODEL_DEFAULT`)
   is written by `tools/gen_fw_power_model.py` from `hardware/power_model/constants.yaml`;
   `tools/tests/test_gen_fw_power_model.py` fails when it is stale. Do not edit it.

## Layout

```
hal/sentinel_hal.h      HAL: time, GPIO, per-rail load switches + PG, cortex reset/ready,
                        wake flags, battery, and sn_infer_ops_t (sensor/inference provider)
include/, src/
  sn_types.h            shared enums/limits (wake sources, actions)
  sn_crc32              CRC-32/IEEE (nibble table)
  sn_energy             power model struct + nJ accumulators
  sn_power_model_gen.c  GENERATED SN_POWER_MODEL_DEFAULT (tools/gen_fw_power_model.py)
  sn_status             §3 status/fault JSON writer + report -> reason mapping
  sn_head               §5 false-alert head: load 65 LE fp32 (fl/ wire form), logistic
  sn_json               bounded, allocation-free JSON writer (sticky overflow)
  sn_event              event record + INTERFACES.md §3 serializer
  sn_gallery            SGAL parse (zero-copy) / write, int8 quantize, cosine, matcher
  sn_power_seq          C6 power sequencer for the N6 domain (rail table, retries, brown-out)
  sn_wake               C6 wake debounce / fusion / holdoff / rate limit + cooldown / timer
  sn_pipeline           N6 pipeline + policy state machine (stage per step)
  sn_brainstem          C6 top level: wake -> power -> job dispatch -> power down, energy
sim/                    host-only: sim_hal.c (time, rails with PG delays/faults, battery
                        with load sag, cortex boot/fail), sim_infer.c (scripted detector)
tests/                  sn_test.h framework, unit + scenario tests, golden test,
                        audit/cross-compile scripts
```

Rules for `src/`: C11 only. No `malloc`. No libc beyond `string.h`, `stdint.h`,
`stdbool.h`, `stddef.h` and `math.h`; the `core.audit.libc_headers` test enforces this.
There is no static mutable state: every module works on a struct the caller owns. Time is
a wrapping `uint32_t` count of milliseconds compared with `sn_time_reached()`, and the
tests cross the 2^32 wrap.

## Build and test

```
make -C firmware test        # plain build + ASan/UBSan build, ctest both (all of firmware/)
make -C firmware test-plain  # just the plain build
cmake -S firmware -B b -DSENTINEL_WITH_OTA=OFF   # core only
```

Each `RUN_TEST(name)` line becomes its own ctest case (`core.<exe>.<name>`). To run one
case directly: `build/core/test_wake wake_pir_storm_rate_limited`.

Flags: `-std=c11 -Wall -Wextra -Werror -Wpedantic -Wshadow -Wstrict-prototypes
-Wmissing-prototypes -Wcast-align -Wdouble-promotion -Wundef -Wvla`. The sanitizer build
adds `-fsanitize=address,undefined -fno-sanitize-recover=all` to everything.

If `clang` is installed, `core.cross.m55` and `core.cross.rv32` compile every library
source for `thumbv8.1m.main` (Cortex-M55, hard float) and `riscv32 rv32imac` (ESP32-C6).
Both are freestanding, use `-nostdlibinc` and `-Werror`, and build against
`tests/cross_shim/` (only the allowed libc functions). They report code size: about 12 KB
on M55 and 14 KB on RV32, with zero static RAM.

### Test inventory

| Executable | What it covers |
|---|---|
| test_crc32 | check value, known vectors, incremental = one-shot |
| test_json | nesting, escaping, int extremes, float rounding/null, every buffer capacity (no overrun, prefix-only), misuse |
| test_event | exact §3 strings, all wake×action names, invalid enums, worst-case size; `core.event_json.python_schema` parses the output with Python's `json` |
| test_gallery | writer/parser roundtrip and byte layout, every error, every single-bit flip rejected, every truncation rejected (exact-size buffers under ASan), structure-aware fuzz, quantization rule, cosine, threshold/margin/per-person/boundary/single-person/empty, 16-person population TAR/FAR |
| test_power_seq | rail order + settle timing, reverse shutdown with off delays, idempotent requests, PG timeout → retries → FAULT → cooldown, slow PG inside/outside timeout, boot timeout + retry, low-battery refusal + hysteresis, debounced brown-out under load + lockout, brown-out during boot, rail lost (running / sequencing), off during RAILS_UP / RETRY_WAIT, clock wrap, coarse 10 ms ticks |
| test_wake | button debounce/chatter/edge-only press, never rate-limited; PIR spike rejection, holdoff, storm, exact window/cooldown; radar absent/confirm/any; timer (no burst after oversleep); priority; clock wrap |
| test_pipeline | all 16 hit patterns of 2-of-4, early exit, known/unknown/no-face/mixed/two-known, face on 2nd frame, button with/without person, timer, camera/embed/clip failures, NaN/inf confidences, null ops, state trace, energy formula, record fields, policy table, clip id |
| test_brainstem | end-to-end events via the scenario world: C6 vs N6 energy agreement, button queued during a job and served back-to-back without re-boot, low-battery refusal (cortex never powered), brown-out, power fault, quiet day (deep sleep, exact standby energy), timer, latched short pulse, job timeout, wake dropped while busy |
| test_status | exact §3 status strings, every reason, invalid state/reason combinations, every buffer capacity, report → reason mapping |
| test_head | 260-byte LE layout, rejects (length, NaN/inf, NULL; output untouched), logistic incl. ±1000 logits |
| test_scenario_day | scripted 24 h day (below); also prints EVENT/STATUS JSON lines that `tests/integration` ingests into the backend |
| test_golden | consumes `ml/golden`: gallery blob (and byte-identical re-write), quantization edge cases, f32→i8 queries, 64 expected decisions + scores, 512 per-person scores, every bad blob's error |

### Scripted day (test_scenario_day)

The day runs through the brainstem and the pipeline concurrently. While the N6 is in a
long stage, the simulated HAL keeps ticking the C6 in 10 ms steps, so inputs such as a
doorbell press during a clip are seen at the right time.

| Time | Script | Expected |
|---|---|---|
| 00:30–11:35 | 20 PIR false wakes (wind, cat with one frame over 0.5, headlights at 0.45) | 20 × `NONE`, about 0.9 J each |
| 12:00, 13:10, 21:30 | 3 known people (ids 1, 2, 3) | 3 × `LOG_KNOWN` with the correct id |
| 12:40, 15:00, 15:30, 17:30, 18:00 | unknown, back turned, unknown, known plus stranger, unknown | 5 × `NOTIFY_UNKNOWN_WITH_CLIP` |
| 14:00–14:20 | PIR storm, one pulse every 3 s (400 pulses) | rate-limited to 16 wakes (13.9 J); without the limiter, 100 wakes (86.6 J) |
| 19:30 / 19:30:05 | visitor walks up, then presses the button during the clip | `NOTIFY` then `RING` with a clip, back to back with no second boot |
| 20:00–21:00 | battery at 3350 mV; person at 20:15 | refused (`LOW_BATT_REFUSED` → status fault `LOW_BATTERY`), cortex never powered |
| 23:00 | cold cell sags 300 mV under load | brown-out during boot, `CORTEX_LOST` (status fault `BROWNOUT`), 10 min lockout |
| 08:00, 16:00 | 8 h health-check timer | 2 × `NONE` (TIMER) |

The energy checks derive every expectation from `SN_POWER_MODEL_DEFAULT` (generated from
`constants.yaml`) and the sim latencies, so they follow the constants: standby is exactly
`standby_uw` × 24 h; the sum of the N6 per-event `energy_mj` values is within 5% of what the C6
integrates (measured: 97.6 J vs 98.2 J); outside the clip each event is within [0.5, 1.6] × the
nominal no-clip event (`sc_nominal_event_mj`: boot_est + active × (camera start + 4 frames) +
uplink_est; measured 0.77–0.92 J vs 0.92 J nominal) and the clip adds exactly clip_mw × clip_ms.
With the constants.yaml numbers (standby 1.902 mW at the battery, cortex 350/850/800 mW boot/active/clip)
the day averages **3.04 mW, about 254 days** on 18.5 Wh (it was 3.15 mW / 245 days with the plan
placeholders). Note the cortex powers are rail-side (no converter loss, no C6 radio-awake current,
no IR LEDs, no clip upload), so this is more optimistic than the hardware model's battery-side
figure; see `hardware/README.md` for the battery-life number of record.

## Golden vectors (integration hook)

`SN_GOLDEN_DIR` is a CMake cache variable; the default is `ml/golden`. If
`MANIFEST.json` exists there when CMake configures, `test_golden` is registered.
Otherwise CMake warns and the `core.golden.*` tests are skipped. To regenerate the
vectors: `cd ml && python -m sentinel_ml.golden`, then re-run cmake. To use another copy:
`-DSN_GOLDEN_DIR=/path`.

## Porting the HAL

Implement the `sn_hal_*` functions in `hal/sentinel_hal.h` for each target:

- **ESP32-C6 (brainstem).** It runs `sn_brainstem` (wake, power sequencer and
  dispatch).
  - `sn_hal_wake_flags_take` returns the LP-core or GPIO edge latches.
  - Rails map to the TPS22917 enables, and PG to the regulator PG pins.
  - `dispatch_job` sends the job to the N6 over UART or SPI.
  - Call `sn_bs_tick()` every 10 ms while `sn_bs_needs_tick()` is true. Otherwise deep
    sleep until a wake GPIO fires or `sn_wake_next_timer()` is due.
- **STM32N6 (cortex).** It runs `sn_pipeline` and `sn_gallery`.
  - Implement `sn_infer_ops_t` on top of the camera, ISP and ST Edge AI runtime.
  - Only `sn_hal_time_ms` is needed from the HAL.
  - `sn_pipe_step()` runs one stage per call, so a ThreadX task can yield between
    stages.

## Placeholders to replace

- `SN_N6_RAIL_SEQ` and the reset and boot timings in `src/sn_power_seq.c` are marked
  `TODO-verify-against-STM32N6-datasheet`: the rail order (3V3 → 1V8 → VDDCORE → VDDA),
  settle times, PG timeouts and off delays.
- `SN_POWER_MODEL_DEFAULT` is generated from `hardware/power_model/constants.yaml`
  (change the YAML, run `python3 tools/gen_fw_power_model.py`). `SIM_LATENCY_DEFAULT`
  (`sim/sim_infer.c`) is TODO-measure; its `publish_ms` must equal `radio.publish_time`
  (test_pipeline checks `uplink_est_mj` = active power × `publish_ms`).
- The battery thresholds (`batt_min_on_mv` 3400, hysteresis 50, brown-out 3200, lockout
  10 min) depend on the cell's internal resistance and temperature.

## Known limitations and trade-offs

- **Cooldown can miss a person.** During a PIR-storm cooldown (default: more than 4
  motion wakes in 60 s, then 5 min lockout), a real person arriving is missed. The button
  is never rate-limited. With radar fitted, `SN_RADAR_CONFIRM` removes most storms before
  they reach the limiter.
- **One queued wake.** While the cortex is busy, only one BUTTON wake is queued. Other
  wakes are coalesced (holdoff) or dropped and reported.
- **Gallery math precision.** Scores are computed in double. That is fine on the N6
  (M55 with the double-precision FPU option, or a software fallback, about 1 µs per
  prototype). The C6 never runs the matcher.
