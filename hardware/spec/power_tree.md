# Rev A power tree, rail table and STM32N6 sequencing

Machine-readable source: [`rails.yaml`](rails.yaml). If the two disagree, the YAML wins; `make test` checks the YAML.

## Tree

```
USB-C VBUS / solar ──► U12 BQ25798 (buck-boost charger, NVDC, I2C 0x6B)
                         │
              BT1 1S Li-ion 5000 mAh ──[JP1]── VBAT ── U13 MAX17048 (I2C 0x36)
                         │
                        SYS (3.0–4.2 V) [JP2]
     ┌───────────────────┼───────────────────────────────┬───────────────┐
     │                   │                               │               │
  U14 TPS62840        U23 TPS22917 (CTX_EN, from C6)  U24 TPS22917    U25 TPS22917
  60 nA Iq buck          │                            (IR_EN, N6)     (AUDIO_EN, N6)
     │                SYS_CTX [JP4]                      │               │
  3V3_AON [JP3]          ├─ U16 TPS63802 buck-boost ─ 3V3_CTX [JP5] ── N6 VDD / VDDIO 3.3 V
  ├ U4 ESP32-C6-MINI-1   │        ├─ U19 LDO ─ VDDA1V8_AON [JP6]   (EN = SYS_CTX)
  ├ U8 EKMB PIR          │        ├─ U18 LDO ─ VDDA1V8 [JP8]       (EN = VDDCORE_EN)
  ├ U10 VEML7700         │        └─ U20 LDO ─ CAM_2V8 [JP10]      (EN = CAM_PWR_EN)
  ├ U27 ATECC608B        ├─ U15 TPS62088 ─ VDDCORE 0.81/0.89 V [JP7] (EN = VDDCORE_EN, DVFS via Q1)
  └ U26 AND gate         └─ U17 TPS62088 ─ 1V8_CTX [JP9] (EN = VDDCORE_EN) ── PSRAM, NOR, XSPI VDDIO
                                              └─ U21 LDO ─ CAM_1V15 (EN = CAM_PWR_EN)
VDDCORE_EN = U26( N6 PWR_ON  AND  C6 CTX_CORE_EN )
```

## Rail table

| Rail | Domain | Regulator | V nom (min–max) | Design max I | Enable | Shunt / TP | Source of limits |
|---|---|---|---|---|---|---|---|
| VBAT | BAT | BT1 cell | 3.7 (3.0–4.2) | 2 A | always | JP1 / TP1 | cell |
| SYS | BAT | U12 BQ25798 | 3.7 (3.0–4.2) | 2 A | always | JP2 / TP2 | VSYSMIN TO-VERIFY[TV-03] |
| 3V3_AON | AON | U14 TPS62840 | 3.3 (3.0–3.6) | 0.5 A | always | JP3 / TP3 | C6 module 3.0–3.6 V, ≥0.5 A supply, 382 mA TX peak; TPS62840 750 mA |
| SYS_CTX | CTX | U23 TPS22917 | = SYS | 1.5 A | CTX_EN (C6 GPIO14) | JP4 / TP4 | TPS22917 2 A |
| 3V3_CTX | CTX | U16 TPS63802 | 3.3 (3.1–3.5) | 0.5 A | SYS_CTX | JP5 / TP5 | AN6000 VDD: 3.1/3.3/3.5 V, VRM ≥ 200 mA |
| VDDA1V8_AON | CTX | U19 TLV75518 | 1.8 (1.7–1.9) | 10 mA | SYS_CTX | JP6 / TP6 | AN6000 |
| VDDCORE | CTX | U15 TPS62088 | 0.81 (0.79–0.83) / 0.89 (0.868–0.912) | 2 A | VDDCORE_EN | JP7 / TP7 | AN6000: 2000 mA, 30 mVpp ripple, ±35 mV transient |
| VDDA1V8 | CTX | U18 TLV75518 | 1.8 (1.746–1.9) | 50 mA | VDDCORE_EN | JP8 / TP8 | AN6000 |
| 1V8_CTX | CTX | U17 TPS62088 | 1.8 (1.71–1.89) | 0.4 A | VDDCORE_EN | JP9 / TP9 | estimate (PSRAM + NOR + IO) |
| CAM_2V8 | CTX | U20 TLV75528 | 2.8 | 0.1 A | CAM_PWR_EN | JP10 / TP10 | VD66GY VANA 2.8 V |
| CAM_1V15 | CTX | U21 TLV75801 | 1.15 | 0.15 A | CAM_PWR_EN | — / TP11 | VD66GY VCORE 1.15 V |
| SYS_IR | CTX* | U24 TPS22917 | = SYS | 0.6 A | IR_EN (N6) | JP11 / TP12 | estimate |
| SYS_AUDIO | CTX* | U25 TPS22917 | = SYS | 0.5 A | AUDIO_EN (N6) | JP12 / TP13 | TO-VERIFY[TV-28] |

\* fed from SYS but only the N6 can enable it, so it is off whenever the cortex is off.

## STM32N6 sequencing (what is verified, what is not)

Verified from ST AN6000 Rev 1 ([PDF](https://www.st.com/resource/en/application_note/an6000-how-to-build-the-discrete-power-supply-for-stm32n6-mcus-stmicroelectronics.pdf)):

- VDD and VDDA1V8_AON come up first (from VIN). When both cross VPOR (1.67 V) the N6 asserts **PWR_ON**, which enables VDDCORE, VDDA18x and VDD_periph.
- VDDCORE: after crossing 0.61 V it must exceed 0.782 V within 200 µs (tdelay_VDDCORE min); power-up slew ≥ 0.86 V/ms. VRDY_VDDCORE = 0.66 V rising starts tdelay_VDDCORE = 340 µs, then HSI, option bytes, boot ROM (EADLY timer 5 ms), FSBL from external flash; FSBL clears SDEN (internal SMPS bypass).
- PWR_ON is 1 in Run/Sleep/Stop and 0 in Standby/VBAT; it is Hi-Z with an internal 40 kΩ pull-down when the MCU resets on power loss. POPL (PWR_CR1) sets the minimum PWR_ON low pulse and must exceed the VDDCORE regulator discharge time.
- **Power-down rule: VDDCORE and VDDA18x must be low before VDD and VDDA18AON.**
- PWR_VCORE_DVFS GPIO: 0 → 810 mV, 1 → 890 mV, through a MOSFET (IDSS ≪ 2 µA, VGS(th) < 1.8 V, Crss < 20 pF) on the feedback divider.
- UM3300 (DK): nominal mode VDDCORE 0.81 V, overdrive 0.89 V; the DK defaults to the external SMPS.

Not verified (do not lay out until closed): the AN5967 sequencing chapter for VDDA18PMU / VDDSMPS / VDDA18CSI / VDDA18USB and XSPI port supplies (an ST moderator says VDDA18PMU/VDDSMPS must rise after PWR_ON enables the external regulator: [ST community](https://community.st.com/t5/stm32-mcus-products/power-on-off-sequence-for-stm32n657x0h3q/td-p/892751)) TO-VERIFY[TV-12]; PWR_ON level while NRST is held, POPL range, and discharge time TO-VERIFY[TV-13].

### Our sequence (Rev A)

Power-up (C6 is the actor, N6 PWR_ON does the inner sequencing):

| Step | Actor | Action | Rails up | Timing |
|---|---|---|---|---|
| 1 | C6 | CTX_CORE_EN low, link GPIOs Hi-Z | — | — |
| 2 | C6 | CTX_EN high | SYS_CTX | ~1 ms ramp (U23 CT) |
| 3 | HW | buck-boost + AON LDO start from SYS_CTX | 3V3_CTX, VDDA1V8_AON | < 1 ms (TO-VERIFY[TV-19]) |
| 4 | C6 | CTX_CORE_EN high after ≥ 2 ms | — | design margin |
| 5 | N6 | VDD, VDDA1V8_AON > 1.67 V → PWR_ON = 1 | — | AN6000 |
| 6 | HW | VDDCORE_EN = PWR_ON ∧ CTX_CORE_EN | VDDCORE, VDDA1V8, 1V8_CTX | VDDCORE 0.61→0.782 V in < 200 µs |
| 7 | N6 | 340 µs → HSI → ROM → FSBL from NOR | — | boot target < 150 ms to first frame (TO-VERIFY[TV-05]) |
| 8 | N6 | CAM_PWR_EN, then IR_EN / AUDIO_EN only when needed | CAM_*, SYS_IR, SYS_AUDIO | sensor datasheet |
| 9 | N6→C6 | READY frame on control UART; C6 enables SPI slave | — | — |

Power-down (normal): C6 sends SHUTDOWN → N6 drops CAM/IR/AUDIO → N6 enters Standby (PWR_ON = 0 for ≥ POPL) → VDDCORE, VDDA1V8, 1V8_CTX discharge → C6 sees N6_PWR_ON_SENSE low (or 200 ms timeout) → CTX_CORE_EN low → wait ≥ 5 ms (TO-VERIFY[TV-13]) → link GPIOs Hi-Z → CTX_EN low (3V3_CTX, VDDA1V8_AON, SYS_CTX off).

Forced power-down (N6 hung): CTX_CORE_EN low kills VDDCORE/VDDA1V8/1V8_CTX through U26 regardless of PWR_ON → wait ≥ 5 ms → CTX_EN low. This keeps the AN6000 order even when firmware is dead, which is why U26 exists. `tests/test_spec_consistency.py` checks both lists against that rule.

### Back-powering

When SYS_CTX is off, any C6 pin driving an N6 pin high would power the N6 through its ESD diodes. The C6 firmware (WS2 brainstem sequencer) must put HOSTED_MISO, HANDSHAKE, DATA_READY and CTRL_UART_C6_TX into Hi-Z (or drive low) before CTX_EN goes low and keep them there until the N6 sends READY. Series resistors (33 Ω SPI, 1 kΩ UART) limit the current if that is violated. I2C_CAM pull-ups go to 3V3_CTX, not 3V3_AON.

### Measurement points

Every rail except CAM_1V15 has a 0 Ω/jumper (JP1–JP12) for a PPK2 in ampere-meter mode (PPK2: 500 nA–1 A, 100 kS/s, 0.8–5 V source). JP7 (VDDCORE, up to 2 A) needs a low-resistance shunt footprint and a >1 A meter (TO-VERIFY[TV-21]).
