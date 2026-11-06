# hardware/ — WS5: Sentinel hardware design package

Power/battery-life model, Rev A BOM, rail and sequencing spec, interface map, Rev 0 bench wiring, layout rules and a schematic review checklist. Everything that can be checked by a machine is in YAML/CSV and checked by `make test`.

```
make test      # 83 pytest tests + BOM checker + I2C collision checker
make report    # regenerates power_model/out/report.md, report.json, life_histogram.png, tornado_plan_mix_twt.png
make deps      # numpy, pyyaml, pytest (+ matplotlib, optional)
```

| Path | What |
|---|---|
| `power_model/constants.yaml` | **Single source of truth** for every current, power, duration and energy (INTERFACES.md §6). Each value has `low/high`, a unit, a status (datasheet / third_party_measurement / derived / estimate / design_choice / spec / measured) and a source URL or "estimate". |
| `power_model/scenarios.yaml` | Daily event mixes (plan mix, radio-off fallback, busy, quiet, radar populated). |
| `power_model/sentinel_power/` | The model: battery-side energy accounting, Monte-Carlo (constants triangular in [low, high]; household event rate gamma, CV 0.4; Poisson daily counts), capacity derating (temperature, calendar fade, usable fraction, temperature-dependent self-discharge), radio-off mode, one-at-a-time tornado and Spearman sensitivity. |
| `bom/bom.csv`, `bom/check_bom.py` | Rev A BOM and its checker. |
| `spec/power_tree.md`, `spec/rails.yaml` | Power tree, rail table, STM32N6 sequencing (normal and forced power-down). |
| `spec/interfaces.md`, `spec/interfaces.yaml` | Signal map: CSI-2, XSPI1/2, ESP-Hosted SPI, control UART, wake GPIOs, enables, test points. |
| `spec/i2c_map.yaml`, `spec/i2c_check.py` | I2C buses and addresses with collision check. |
| `spec/rev0_wiring.md`, `spec/rev0_wiring.yaml` | Rev 0 bench rig: DK + DevKitC-1 + PIR + PPK2, verified pins only. |
| `spec/layout_rules.md` | 6-layer stackup, CSI-2 100 Ω differential, XSPI matching, antenna keep-out. |
| `spec/schematic_review_checklist.md` | M4 Rev A review checklist. |
| `spec/to_verify.yaml` | Register of all 33 open TO-VERIFY items. A test fails on any TO-VERIFY item written without a register ID, and on any register item nothing refers to. |

## Contract notes for other workstreams

- **ML (WS1):** read `npu.npu_pj_per_mac` from `power_model/constants.yaml` (YAML path `npu → npu_pj_per_mac → value`, unit pJ per int8 MAC; `low`/`high` give the range). Nominal **3.6 pJ/MAC**, range 0.67–16. It is the *marginal* core-domain energy from a linear fit to published STM32N6 measurements (arXiv:2505.15622, L-Perf): E ≈ 0.153 mJ + 3.6 pJ × MACs per inference. Use `npu.npu_overhead_per_inference` (0.153 mJ) for the fixed part. The ST "3 TOPS/W" figure (0.67 pJ/MAC) is the optimistic low bound. This is core-domain only: sensor, PSRAM, NOR and I/O energy are in `cortex.*`.
- **Firmware (WS2):** the brainstem sequencer must follow `spec/rails.yaml` `sequencing` (including the forced path through CTX_CORE_EN) and the back-powering rule in `spec/power_tree.md`. ESP-Hosted's host-driven C6 reset is not wired.
- No shared contract in `docs/INTERFACES.md` was changed.

## Battery-life result

Monte-Carlo, 20,000 draws, seed 1 (`power_model/out/report.md`):

| Scenario | Standby I_bat | Avg power (nominal) | Life, nominal | **Life, MC median (p5–p95)** | P(≥ 6 mo) |
|---|---|---|---|---|---|
| Plan mix, Wi-Fi associated (TWT, 30 s interval) | 514 µA | 3.11 mW | 7.0 mo | **6.4 mo (4.6–8.7)** | 64 % |
| Plan mix, radio-off fallback | 40 µA | 1.44 mW | 15.0 mo | **11.5 mo (7.8–16.9)** | 100 % (P(≥ 12 mo) = 43 %) |
| Busy door, TWT (25 people, 40 false wakes, 2 rings/day) | 514 µA | 4.92 mW | 4.5 mo | 3.8 mo (2.7–5.4) | 1 % |
| Quiet street, radio-off (10 wakes/day) | 40 µA | 0.79 mW | 27 mo | 21 mo (15–30) | 100 % |
| Plan mix + radar populated, TWT | 829 µA | 4.12 mW | 5.3 mo | 4.5 mo (3.3–6.2) | 8 % |

Plan mix = plan §4.4: 10 person events/day (4 unknown → 8 s clip + upload), 20 false wakes/day, plus 24 status publishes/day.

### Against the plan's ~9 months

`sentinel_power.plan` reproduces §4.4 exactly: 2.934 mW, 18.5 Wh / 2.934 mW = 6,305 h = **8.6 months**.

Re-deriving the same rows from sourced constants changes the conclusion:

| Row | Plan | Model | Why |
|---|---|---|---|
| Standby (C6 + sensors + regulators) | 1.50 mW | 1.84 mW | The only public ESP32-C6 iTWT measurement is 708 µA average at a 16 s interval ([hackaday.io](https://hackaday.io/project/191398/log/223940)); the plan assumed 400 µA. At a 30 s interval the model gives 485 µA for the C6. |
| Person events (inference) | 0.35 mW | 0.21 mW | ~1.8 J per event rather than 3 J, if the N6 boots in 150 ms (unmeasured). |
| Clips | 0.56 mW | 0.58 mW | Roughly agrees: 14 J per unknown-person event. |
| False wakes | 0.23 mW | 0.31 mW | 1.3 J each, because the C6 stays awake and publishes too. |
| Self-discharge + charger/gauge | 0.30 mW | 0.19 mW | Self-discharge at 10 °C is about a third of its 25 °C value (it doubles per +10 °C). |
| **Total** | **2.93 mW** | **3.11 mW** | Plan is 6 % optimistic on power. |

The average power is close to the plan's. The life is not, because the plan divided by the full nameplate 18.5 Wh. The model applies a usable fraction (0.92: the C6 needs ≥ 3.0 V behind a buck, so the cell cannot be run to 2.5 V), a temperature derating (0.95 at a 10 °C annual mean) and 3 %/year calendar fade. Nominal life is therefore **7.0 months, not 9**. The Monte-Carlo median is **6.4 months** (the TWT energy range has a long upper tail). **G1 (≥ 6 months with Wi-Fi associated) is met only about two times in three on today's numbers. It is not the comfortable headroom the plan claims.** The radio-off fallback (median 11.5 months) is the robust path. With the plan mix it reaches G1's ≥ 12-month radio-off target only 43 % of the time. On a quiet street it reaches it easily.

The TWT interval is the strongest design lever. At the nominal per-wake energy, life is 5.4 months at a 16 s interval, 7.0 months at 30 s, 8.5 months at 60 s and 9.5 months at 120 s. Negotiating ≥ 60 s with the AP recovers the plan's number. Uplink latency is unaffected by the interval; downlink (feedback, OTA notices) waits up to one interval. M2 must measure both the floor current and the energy per TWT service period on our own router (TV-01, TV-02).

### Top sensitivity drivers (plan mix, TWT)

One-at-a-time swing over each constant's [low, high] range, nominal 7.0 months:

1. `aon_loads.twt_interval` (16–120 s): 5.4 ↔ 9.5 months
2. `aon_loads.c6_twt_wake_energy` (10–60 mJ): 9.1 ↔ 5.0 months
3. Unknown-person (clip) rate, ×½ to ×2: 7.9 ↔ 5.8 months
4. False-wake rate ×½ to ×2 (1.0 month); camera sensor start / AE time (1.0); deployment temperature (1.0); usable fraction (0.9); C6 light-sleep floor (0.8); IR LED power (0.8); always-on buck efficiency (0.8)

The Spearman ranking from the Monte-Carlo gives the same order: TWT interval +0.54, clip rate −0.45, TWT wake energy −0.38, false-wake rate −0.28. In radio-off mode the clip rate dominates (ρ = −0.70), then false wakes and sensor start time.

**NPU energy per MAC barely matters for battery life.** A person event uses about 12 mJ of NPU compute out of 1.8 J (under 1 %). Sensor start-up, boot and the radio dominate. A test enforces "< 5 %". The ML team should optimise frames per decision and early exit, not MACs, for battery life.

### Goal G2 (standby current)

- Radio-off: **40 µA** nominal, inside the 50 µA target. Half of it is the **BQ25798 battery-only quiescent current**, up to 24 µA per [TI E2E](https://e2e.ti.com/support/power-management-group/power-management/f/power-management-forum/1508998/bq25798-check-power-consumption-at-battery-only), with ADC disabled. That is now the largest non-radio drain, and the typical value is TO-VERIFY[TV-03]. The C6 is 10.5 µA (deep sleep + LP core), the gauge 3 µA and the PCM 3 µA.
- Wi-Fi associated: **514 µA** nominal at a 30 s TWT interval, **just over** the 500 µA target. It meets the target at an interval of ≥ 32 s.

## Other findings worth acting on

- **Radar is not free.** At an estimated 300 µA always-on (unverified, TV-27), populating the BGT60LTR11 costs more battery than halving the false wakes saves (5.3 vs 7.0 months nominal). Keep it DNP unless its measured average current is below about 40 µA (the break-even point where its draw equals the energy saved by halving false wakes).
- **C6 pin budget is exhausted.** The ESP32-C6-MINI-1 has 22 GPIOs, and the Rev A map uses all of them. A test enforces this, so new signals force a decision: drop the 32 kHz crystal or add an I2C GPIO expander.
- **I2C collision trap.** The ATECC608 TFLXTLS variant defaults to 0x36, the same address as the MAX17048. Order MAHDA (0x60) or TNGTLS (0x35). Also, the VEML7700 and the VD66GY are both at 0x10, so keep them on separate buses. The checker reports both cases.
- **AN6000 power-down rule** (VDDCORE/VDDA18 low before VDD/VDDA18AON) is enforced even when the N6 is hung. U26 ANDs PWR_ON with a C6-owned CTX_CORE_EN, and a test checks the order of both power-down paths.
- **PPK2 limit:** 1 A max, but the DK's VDDCORE is rated to 2 A. The Rev 0 guide puts VDDCORE on a shunt and scope.

## BOM

`python bom/check_bom.py`: 53 lines, no errors.

- **Populated Rev A board, qty-5 estimate: $135.15** (DNP radar adds $8.00). The plan said $85–130.
- Largest blocks: power $26.70, N6 $16.00, PIR $15.00, PCB $15.00, memories $13.50, camera $13.30, optics $10.90.
- The plan under-priced the EKMB PIR ($4 for all wake sensors; the EKMB alone is about $15 at qty 5) and did not count the buck-boost needed for N6 VDD at low cell voltage, the VDDCORE AND-gate/DVFS parts, or the camera LDOs.
- Only 2 of 53 prices have a distributor URL (BQ25798, APS256XX on LCSC). The other 51 are estimates (TV-25). Treat the total as ±25 %. Assembly is not included.

## TO-VERIFY items (33 open, `spec/to_verify.yaml`)

| ID | Item | Milestone |
|---|---|---|
| TV-01 | ESP32-C6 iTWT standby current on our AP and the granted TWT interval | M2 |
| TV-02 | Energy per TWT service period (derived from one third-party measurement) | M2 |
| TV-03 | BQ25798 battery-only typical Iq (only "up to 24 µA" verified) | M0 |
| TV-04 | Cortex-domain power per phase (boot, sensor start, pipeline, encode, upload) | M1 |
| TV-05 | N6 cold boot to first frame (plan target 150 ms) | M1 |
| TV-06 | ESP-Hosted SPI throughput for clip upload | M2 |
| TV-07 | Radio-off reconnect time and charge | M2 |
| TV-08 | Cell capacity vs temperature and calendar fade | M0 |
| TV-09 | `npu_pj_per_mac` fit inputs (ResNet-8 MAC count) | M1 |
| TV-10 | ATECC608B per-variant I2C address | M0 |
| TV-11 | MB1854 ToF/IMU I2C addresses (DK I2C1) | M1 |
| TV-12 | AN5967 sequencing chapter: PMU/SMPS/CSI/USB/XSPI supplies, decoupling | M0 |
| TV-13 | PWR_ON under NRST, POPL range, VDDCORE discharge time | M0 |
| TV-14 | N6 ball assignment (CubeMX), BGA pitch, boot pins | M4 |
| TV-15 | C6 strapping semantics for GPIO4/5/8/15 with our pulls | M0 |
| TV-16 | ESP-Hosted pin config, reset line, max SPI clock | M1 |
| TV-17 | Whether C6 needs the 32 kHz crystal | M2 |
| TV-18 | TPS62840 3.3 V setting and 100 % mode near cutoff | M0 |
| TV-19 | TPS62088 against AN6000 VDDCORE VRM spec; U23 ramp | M4 |
| TV-20 | DK from 5V_VIN with ST-LINK unpowered, gated by C6 | M2 |
| TV-21 | PPK2 logic thresholds; >1 A meter for VDDCORE | M1 |
| TV-22 | CSI-2 PHY supply and sensor skew limits | M4 |
| TV-23 | XSPI length-match tolerance at 200 MHz DDR | M4 |
| TV-24 | C6-MINI-1 antenna keep-out dimensions | M4 |
| TV-25 | Qty-5 prices for 51 estimated BOM lines | M0 |
| TV-26 | VD66GY small-qty availability, ordering code, power-up order, FFC fallback pinout | M0 |
| TV-27 | BGT60LTR11 supply and average current | M2 |
| TV-28 | MAX98357A supply range from SYS | M4 |
| TV-29 | EKMB 1 µA variant performance and stability time | M2 |
| TV-30 | Orderable MPN suffixes and fallback addresses | M0 |
| TV-31 | Pack PCM current | M0 |
| TV-32 | 6-layer stackup trace geometry from the fab | M4 |
| TV-33 | DevKitC-1 GPIO16/17 on USB-UART bridge; GPIO10/11/0/1 free | M2 |

## Sources used for electrical numbers

STM32N6 VRM and sequencing: [ST AN6000](https://www.st.com/resource/en/application_note/an6000-how-to-build-the-discrete-power-supply-for-stm32n6-mcus-stmicroelectronics.pdf). DK pins, memories and IDD jumpers: [UM3300](https://www.st.com.cn/resource/en/user_manual/um3300-discovery-kit-with-stm32n657x0-mcu-stmicroelectronics.pdf). Neural-ART: [ST overview](https://www.st.com/content/dam/st-developer-zone/st-edge-ai-suite/edge-ai-explained/pdf/presentation-ST-neural-art-accelerator-overview.pdf). N6 measured energy: [arXiv:2505.15622](https://arxiv.org/pdf/2505.15622). CSI-2 host: [RM0486 ch. 40 (third-party mirror)](https://docs.dirba.io/rm0486-stm32n6x5-x7/40-csi-2-host-csi/). ESP32-C6-MINI-1 currents and pads: [module datasheet v1.0 mirror](https://files.waveshare.com/wiki/ESP32-C6-Pico/ESP32-C6-MINI-1_Datasheet.pdf). DevKitC-1: [user guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c6/esp32-c6-devkitc-1/user_guide.html). The complete per-constant list is under `sources:` in `power_model/constants.yaml`, and the I2C sources are inline in `spec/i2c_map.yaml`.
