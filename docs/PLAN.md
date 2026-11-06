# Sentinel: battery-powered edge-AI vision camera (plan)

**Status:** PLAN ONLY, waiting for Ashton's review. Nothing gets designed or coded until you say proceed.
**Date:** 2026-11-06
**Lineage:** A much more ambitious successor to the ELEC 290 Smart Doorbell. (I don't have the doorbell's design files. Where this plan assumes something about it, it says so. If you share the old repo or report, I'll fold those lessons in.)

---

## 1. What we're building, in one paragraph

A custom-PCB, battery (optionally solar) powered camera that sleeps at tens of microamps, wakes on motion, decides *on the device* whether it's seeing a person, whether that person's face is someone it knows, and only then spends energy on the radio. It updates its firmware and its models over the air with signed A/B images, and it gets smarter over time through on-device enrollment plus a federated learning loop across a small fleet of units, so raw faces never leave the house.

The interesting engineering is the tension between three things: **NPU-class compute** (hundreds of GOPS), **a battery budget of a few milliwatts average**, and **a trustworthy update and learning loop**. Most hobby cameras pick one. This picks all three.

## 2. Goals and non-goals

### Goals (measurable)
| # | Goal | Target |
|---|------|--------|
| G1 | Battery life | ≥ 6 months on a 5000 mAh 1S Li-ion at 10 events/day with Wi-Fi kept associated; ≥ 12 months with radio-off standby |
| G2 | Standby current | ≤ 50 µA radio-off, ≤ 500 µA average with Wi-Fi associated (TWT) |
| G3 | Wake-to-decision latency | ≤ 600 ms from PIR edge to "person / not person" |
| G4 | Person detection | ≥ 0.80 mAP@0.5 on a held-out doorstep set; false-alert rate < 1/day |
| G5 | Face recognition | ≥ 95% TAR at 1% FAR for enrolled household members at 1–3 m, daylight |
| G6 | Energy per inference | Measured and reported in mJ per model, per frame |
| G7 | OTA | Signed A/B firmware *and* separately versioned model OTA, automatic rollback on failed boot, zero bricked units in 50 forced-failure tests |
| G8 | Learning loop | On-device enrollment in < 30 s; federated round across ≥ 3 physical units + simulated clients that measurably lowers false alerts |
| G9 | Privacy | Face embeddings and raw frames never leave the device unless the owner opts in per clip |

### Non-goals (for v1)
- Continuous video streaming or NVR-style 24/7 recording (kills the battery; it's a different product).
- Cellular / LTE-M (possible v2 stretch, see §11).
- A polished consumer mobile app. A simple web dashboard is enough.
- Certification (FCC/ISED). We'll use a pre-certified radio module so this stays possible later.

## 3. System architecture

### 3.1 The core idea: a "brainstem" and a "cortex"

```
                    ┌───────────────────────────── always-on domain (~10–400 µA) ─────────────────────────────┐
  PIR ──────────────►                                                                                          │
  mmWave presence ──►   ESP32-C6 module  ("brainstem")                                                         │
  Doorbell button ──►   - LP core watches wake sources                                                         │
  Light sensor ─────►   - Wi-Fi 6 with Target Wake Time keeps the AP association cheap                         │
                    │   - BLE for provisioning / phone enrollment                                              │
                    │   - Owns the power switches for the cortex domain                                         │
                    │   - Network co-processor for the N6 (ESP-Hosted over SPI)                                 │
                    └───────────────┬──────────────────────────────────────────────────────────────────────────┘
                                    │ load-switch enable, SPI (hosted net), UART (control), GPIO wake
                    ┌───────────────▼────────────── switched domain (0 µA when off, ~0.6–1.2 W when on) ───────┐
                    │   STM32N6 ("cortex")                                                                     │
                    │   - Cortex-M55 @ 800 MHz + Neural-ART NPU (~600 GOPS int8)                               │
                    │   - MIPI CSI-2 + hardware ISP + H.264 encoder                                             │
  Image sensor ─────►   - Runs person detect → face detect → face embed → match                               │
  IR LEDs + IR-cut ◄─   - Encodes a short clip only if the policy says so                                     │
  Mic / speaker ◄──►   - Octal PSRAM (frames, tensors) + octal NOR (A/B firmware + model slots)               │
                    └──────────────────────────────────────────────────────────────────────────────────────────┘
```

The big power win: the expensive chip is **fully unpowered** (load switch off) most of the time, not just asleep. The cheap chip with a radio is the only thing alive.

### 3.2 Event pipeline

1. **Wake.** PIR fires (or mmWave confirms presence, or the button is pressed). The C6 LP core debounces and, if the light sensor says it's dark, schedules IR LEDs.
2. **Power up cortex.** C6 enables the N6 rails in sequence. N6 boots from external flash (target < 150 ms to first frame; measured in M2).
3. **Stage 1: person detector** on the NPU at ~256×256. No person in N frames → log a "motion, no person" event, power down. Most false wakes (cars, trees, cats) end here, costing ~1 J.
4. **Stage 2: face detector** on person crops. **Stage 3: face embedding** (128-d), cosine match against the on-device gallery of enrolled people.
5. **Policy.** Known person → silent log (or "Mom's home" notification). Unknown person → notify + encode 5–10 s H.264 clip + thumbnail. Button press → always ring + notify.
6. **Uplink.** N6 sends metadata + clip through the C6 (MQTT over TLS). Then the cortex powers off.
7. **Learning hooks.** User feedback on a notification ("this was a false alarm", "this is Alex") becomes a labeled example that stays on the device and feeds §5.4.

### 3.3 Software stack
| Layer | Choice | Why |
|---|---|---|
| N6 firmware | STM32Cube HAL + Eclipse ThreadX (ST's default), or Zephyr if N6 support is mature by M1 | ThreadX has first-party N6 camera/NPU examples; Zephyr is nicer long-term. Decide in M1. |
| NPU toolchain | ST Edge AI Core / STM32Cube.AI (int8 TFLite or ONNX QDQ in, Neural-ART code out) | Only real path to the NPU |
| C6 firmware | ESP-IDF (LP core program + ESP-Hosted slave + Wi-Fi TWT) | First-party, mature low-power Wi-Fi |
| Bootloader | ST secure boot ROM → signed first-stage → A/B app slots (MCUboot-style swap or slot select) | Signed images, rollback |
| Backend | Mosquitto/EMQX (MQTT/TLS) + MinIO (S3) + small FastAPI service + web dashboard; Flower server for federated rounds | Self-hostable on a Pi or a $5 VPS |
| Training | PyTorch → quantization-aware training → ONNX/TFLite int8 → ST Edge AI Core | Matches your nanoGPT-era PyTorch workflow |

## 4. Hardware: PCB and BOM

### 4.1 SoC choice (the main decision)

| Option | Compute | Power story | Dev ecosystem | Verdict |
|---|---|---|---|---|
| **STM32N6** (recommended) | M55 @ 800 MHz + Neural-ART NPU, ~600 GOPS; CSI-2, ISP, H.264 HW | MCU-class: boots in ms, can be fully power-gated | STM32N6570-DK, ST model zoo has person/face examples | Best fit: real NPU *and* MCU-style power gating |
| Kendryte K230 | Dual RISC-V C908 + KPU (several TOPS-equiv), runs Linux | Linux boot is seconds; hard to power-gate per event | CanMV boards, less polished docs | More compute, worse battery story; better for a mains-powered v2 |
| ESP32-S3 alone | Dual LX7 + vector extensions, no NPU | Excellent | Excellent | Fine for 96×96 person detect, too weak for face embeddings at useful speed. This is roughly doorbell-tier. |

**Recommendation:** STM32N6 + ESP32-C6. It's the most "hardware-y" option (real BGA, real power sequencing, MIPI) while still being battery-viable.

### 4.2 Rev A board BOM (estimates at 5-unit quantity, USD)

| Block | Part (primary / fallback) | Notes | Est. $/unit |
|---|---|---|---|
| Vision SoC | STM32N6 (N657, package with CSI-2 + 2× XSPI exposed) | Fine-pitch BGA, needs 6-layer | 12–18 |
| External RAM | 32 MB octal PSRAM (APS256XX class) on XSPI1 | Frame buffers + activations | 4–6 |
| External flash | 64–128 MB octal NOR (Macronix MX66UW class) on XSPI2 | A/B firmware, A/B model slots, gallery, logs | 4–7 |
| Radio / always-on MCU | ESP32-C6-MINI-1 module (pre-certified) | Wi-Fi 6 TWT, BLE 5, LP core | 3–4 |
| Image sensor | ST VD66GY (1.5 MP color, CSI-2) / Sony IMX335 module (what the DK ships with) | Choose on low-light results in M1 | 10–20 |
| Optics | M12 lens, ~100° HFOV, switchable IR-cut filter | Doorstep framing | 6–10 |
| Night vision | 2–4× 850 nm IR LEDs + constant-current driver | Duty-cycled only during capture | 3 |
| Wake sensors | Panasonic EKMB low-power PIR (~1–6 µA) + optional Infineon BGT60LTR11 radar | Radar cuts PIR false wakes; can be DNP'd | 4 (+8 radar) |
| Light sensor | VEML7700 | Day/night switch | 1 |
| Audio | 2× MEMS PDM mics + I2S class-D amp (MAX98357A) + small speaker | Doorbell chime, two-way talk later | 4 |
| Charger | TI BQ25798 (buck-boost, solar MPPT, USB-C) / BQ25620 | Solar input is a nice stretch for free | 5 |
| Fuel gauge | MAX17048 | ~4 µA, ModelGauge | 2 |
| Always-on rail | TPS62840 (60 nA Iq buck) → 3.3 V | Powers C6 + sensors | 1.5 |
| Cortex rails | Dedicated bucks for VDDCORE (~0.8–0.9 V, high current) and 1.8 V I/O, gated by TPS22917 load switches; sequencing per N6 datasheet | Biggest bring-up risk; see §8 | 5 |
| Security | N6 secure boot + TrustZone; optional ATECC608B for device identity keys | Fleet identity for TLS + federated auth | 0–1 |
| Battery | 1S 5000 mAh Li-ion (pouch or 2× 18650 parallel) with NTC | ~18.5 Wh | 8–12 |
| Misc | USB-C, button + ring light, test points, SWD + UART headers, power-measurement shunt jumpers | Shunt jumpers per rail are non-negotiable for G1/G2 | 5 |
| PCB | 6-layer, ~60×60 mm, ENIG, impedance-controlled CSI-2 pairs | | 8–15 |
| **Total** | | | **~$85–130/unit** (Rev A, tiny qty) |

### 4.3 PCB plan
- **Rev 0 (no PCB):** STM32N6570-DK + ESP32-C6 DevKit + PIR/radar breakout + a Nordic PPK2 on each rail. De-risks everything except layout.
- **Rev A:** 6-layer, all blocks, generous test points, every rail behind a 0 Ω/jumper for current measurement, PIR/radar footprints both populated. Assembled by JLCPCB/PCBWay (BGA + X-ray).
- **Rev B:** Fix Rev A errata, shrink to enclosure, move to final antenna placement, DNP the debug cruft.
- **Design tool:** KiCad 9. Layout rules: 100 Ω differential CSI-2 pairs, length-matched XSPI, solid ground under the C6 antenna keep-out, separate analog ground for mics.

### 4.4 Power budget (the number that decides if this works)

Battery: 5000 mAh × 3.7 V ≈ 18.5 Wh. Six months ≈ 4,380 h → **average budget ≈ 4.2 mW**.

| State | Power | Duty | Avg contribution |
|---|---|---|---|
| Standby, Wi-Fi associated with TWT (C6 + PIR + gauge + regulators) | ~1.5 mW (≈400 µA @ 3.7 V) | always | 1.5 mW |
| Event: cortex on, inference (~1 W × 3 s) | 3 J | 10/day | 0.35 mW |
| Event: clip encode + upload (~1.2 W × 10 s), unknown-person events only | 12 J | ~4/day | 0.55 mW |
| False wakes (stage-1 reject, ~1 J) | 1 J | 20/day | 0.23 mW |
| Self-discharge + charger/gauge leakage | | | ~0.3 mW |
| **Total** | | | **≈ 2.9 mW → ~9 months** |

So G1 has headroom, *if* the Wi-Fi TWT standby number holds. That's measured first (M2) because if it's 3× worse, the fallback is radio-off standby with reconnect-on-event (adds ~1–2 s latency to notifications).

## 5. Model pipeline

### 5.1 Models
| Stage | Candidate | Input | Target on N6 NPU |
|---|---|---|---|
| Person detect | YOLOv8n / YOLOX-nano / ST model-zoo tiny detector, int8 | 256×256 or 320×320 | < 30 ms, < 30 mJ |
| Face detect | YuNet or BlazeFace-style, int8 | person crop → 160×160 | < 10 ms |
| Face embed | MobileFaceNet (128-d), int8 | aligned 112×112 | < 15 ms |
| Anti-spoof (stretch) | Tiny liveness classifier using IR frame | 64×64 | < 5 ms |
| Audio (stretch) | Glass-break / knock classifier on mic | 1 s log-mel | M55 core, no NPU |

### 5.2 Training and data
- **Pretrain:** COCO person + CrowdHuman (detection), WIDER FACE (face det), MS1M-cleaned / Glint-style public sets for the embedder (check licenses; some are research-only, which is fine for a personal project but noted).
- **Domain set:** Capture ~2–5k frames from the Rev 0 rig at your actual door: day, night/IR, rain, backlit, delivery people, pets. This is where most of the accuracy comes from.
- **Quantization-aware training** to int8, per-channel weights. Track the float vs int8 gap per model; > 2 mAP gap means QAT, not post-training quant.
- **NPU-fit loop:** every model export is pushed through ST Edge AI Core in CI, which reports op support, NPU vs CPU fallback layers, RAM/flash footprint, and cycles. A layer that falls back to the M55 is treated as a bug.

### 5.3 Evaluation harness
A host-side harness replays recorded event sequences through (a) the float model, (b) the int8 model in simulation, and (c) the real device over USB, and reports mAP, TAR@FAR, latency, and **energy per event** (PPK2 traces synced to firmware GPIO markers). Every model OTA must beat the current one on this harness before it ships.

### 5.4 On-device and federated learning (the research-y part)

Three layers, from cheap and certain to ambitious:

1. **Enrollment (no backprop, certain to work).** Owner enrolls a person via BLE from a phone or by tagging an event. The device stores 5–20 embeddings per person as prototypes in flash. Matching is cosine similarity with a per-person threshold. Raw images are discarded after embedding.
2. **On-device head adaptation (likely to work).** A tiny trainable head sits on top of frozen backbone features: (a) a "false-alert filter" that learns the scene's recurring non-threats (the neighbour's flag, headlights on the wall), and (b) a projection layer that tightens household-member clusters. Trained on the M55 core with int8/fp16 forward and fp32 head gradients, in the background while plugged in or with charge > 60%. Small enough (thousands of parameters) to train in seconds.
3. **Federated loop (ambitious, the showpiece).** Devices share **only head-weight deltas** (never images or embeddings) to a Flower server. FedAvg (then FedProx to handle non-IID homes) with clipping + Gaussian noise for differential privacy, and secure aggregation so the server sees only the sum. Realistically we'll have 3–5 physical units, so the fleet is padded with simulated clients built from public data partitioned by "home". The research question we can actually answer: *does federating the false-alert head across heterogeneous scenes beat each device learning alone, and what does DP cost?*

Model OTA and federated updates share one mechanism: a signed, versioned model blob in its own flash slot, separate from firmware.

## 6. OTA and security

- **Two independent update channels:** firmware (N6 app + C6 app) and models (blob + manifest). A model update never needs a firmware reboot cycle unless the op set changes.
- **A/B slots** in octal NOR for both. New image boots in "trial" mode; app must confirm health (camera up, NPU self-test passes, network up) within 60 s or the watchdog reverts.
- **Signing:** ECDSA P-256 over image + manifest; version anti-rollback counter in OTP/secure storage.
- **Transport:** C6 fetches from the backend over TLS, streams to N6 over the hosted link; N6 verifies before marking the slot bootable. The C6 updates itself with ESP-IDF's native A/B OTA.
- **Fault-injection tests:** pull power at random points during 50+ updates; corrupt signatures; ship a model that crashes the NPU. G7 requires zero bricks.
- **Device identity:** per-device keypair (secure element or N6 secure storage) used for MQTT mTLS and federated client auth.

## 7. Milestones (part-time pace, ~10–12 h/week)

| # | Milestone | Duration | Exit criteria |
|---|---|---|---|
| M0 | Requirements freeze + parts sourcing | 1 wk | This plan approved, decisions in §10 made, dev kits ordered |
| M1 | Rev 0 vision bring-up | 3 wk | Person detector running on the N6 NPU from the DK camera; latency + energy measured; RTOS chosen |
| M2 | Rev 0 power + wake chain | 3 wk | C6 LP core wakes on PIR, power-gates the DK's N6 (or a cut-trace equivalent), Wi-Fi TWT standby measured. **Go/no-go on G1.** |
| M3 | Face pipeline + enrollment | 3 wk | Detect → embed → match on device, BLE enrollment from phone, TAR@FAR measured |
| M4 | Rev A schematic + layout | 4 wk | KiCad schematic reviewed (I can do a structured design review pass), DRC clean, ordered |
| M5 | OTA + backend (overlaps fab wait) | 3 wk | Signed A/B OTA working on Rev 0, MQTT + clip storage + dashboard up |
| M6 | Rev A bring-up | 3 wk | Rails, boot, camera, NPU, radio all alive on custom board; errata list |
| M7 | On-device + federated learning | 4 wk | Head adaptation on device; Flower rounds across ≥ 3 units + sim clients; DP ablation |
| M8 | Rev B + enclosure + field test | 4 wk | Weatherproof-ish 3D-printed enclosure, 30-day battery soak test, final report |
| | **Total** | **~28 weeks** (~6–7 months) | |

Each milestone ends with a short written report in this project folder, the same way the analysis threads did.

## 8. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| N6 power rails / sequencing wrong on Rev A | Medium | High (dead board) | Copy the DK reference design closely; per-rail jumpers; populate rails incrementally during bring-up |
| BGA assembly defects | Medium | High | Fab-house assembly with X-ray; order 5 boards, assemble 3 first |
| Model ops not supported by Neural-ART (CPU fallback kills latency/energy) | Medium | Medium | NPU-fit check in CI from day 1; pick architectures from ST's model zoo as baselines |
| Wi-Fi TWT standby worse than 400 µA (AP doesn't support TWT, needs Wi-Fi 6 router) | Medium | High for G1 | Measure in M2; fallback = radio-off standby + reconnect-on-event; note router requirement |
| Low-light / IR face recognition is poor | High | Medium | Sensor bake-off in M1; IR-specific fine-tuning; accept "person only" at night |
| CSI-2 signal integrity on first layout | Low–Med | Medium | Short traces, impedance control, follow sensor vendor layout guide |
| Federated loop shows no benefit at 3–5 devices | High | Low | Frame it as a measured result; simulated clients make the experiment meaningful either way |
| Part availability (N6 packages, sensor modules) | Low–Med | Medium | Fallback parts listed in BOM; buy Rev A parts in M0 |
| Privacy / legal (biometric data, recording public space) | — | Real | Embeddings stay on device; recognition is opt-in per person; mask regions outside your property; mind Canadian privacy norms if it's ever more than a personal device |
| Scope creep (audio, radar, solar, LTE) | High | Medium | Each is a DNP-able footprint or a v2 item; none block M8 |

## 9. Cost estimate

| Item | Est. USD |
|---|---|
| STM32N6570-DK | ~$200 |
| 2× ESP32-C6 DevKit | ~$20 |
| Sensors/breakouts for Rev 0 (PIR, radar eval, light, mics, IR LEDs) | ~$60 |
| Nordic PPK2 (power profiler) | ~$100 (Joulescope JS220 ~$500 if you want lab-grade) |
| Rev A: 5 PCBs, 6-layer + assembly of 3, BGA/X-ray | ~$400–700 |
| Rev A parts not covered by assembly house | ~$150 |
| Rev B: 5 PCBs + assembly | ~$350–600 |
| Batteries, lenses, enclosures (3D print), cables | ~$120 |
| Backend hosting (or free on a Pi you own) | $0–5/month |
| Training compute (fine-tuning small models; Colab/own GPU) | $0–50 |
| **Total** | **~$1,400–2,000** (≈ $1,000 if you skip Rev B and the Joulescope) |

## 10. Decisions I need from you before "proceed"

1. **SoC:** STM32N6 + ESP32-C6 (recommended) vs K230 (more compute, Linux, worse battery) vs ESP32-S3 only (cheap, doorbell-tier).
2. **Budget ceiling:** is ~$1.5k OK, or should I plan a leaner path (~$700: Rev 0 + one Rev A spin only)?
3. **Scope of v1:** include audio (chime + two-way) and mmWave radar on Rev A, or DNP them?
4. **Backend:** self-hosted (Pi/VPS, recommended for privacy) or a managed IoT service?
5. **Repo:** should this live in a new GitHub repo you create (e.g. `sentinel-cam`), with firmware/, hardware/ (KiCad), ml/, backend/ folders? I can't create repos, so you'd create it and I'd attach it.
6. **Doorbell lessons:** if you can share the ELEC 290 repo or report, I'll fold in what went wrong/right there.

## 11. Stretch / v2 ideas
- Solar top-up via the BQ25798 MPPT input (footprint is free on Rev A).
- LTE-M/NB-IoT variant for off-grid placement.
- IR-based liveness / anti-spoofing for the face recognizer.
- Port the person detector to your **tiny-gpu-extended** work as a "what would a custom accelerator need" comparison against Neural-ART (cycles, SRAM, energy per MAC).
- Mains-powered K230 "hub" that does heavier re-identification across several Sentinel cameras.
