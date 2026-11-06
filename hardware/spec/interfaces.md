# Rev A interface and signal map

Machine-readable: [`interfaces.yaml`](interfaces.yaml) (signals, C6 pads, test points) and [`i2c_map.yaml`](i2c_map.yaml) (addresses). `make test` checks pin uniqueness, LP-wake placement, strapping notes, and I2C collisions.

**Pin policy.** ESP32-C6-MINI-1 pad numbers come from the module datasheet pin table ([mirror of v1.0](https://files.waveshare.com/wiki/ESP32-C6-Pico/ESP32-C6-MINI-1_Datasheet.pdf)). No STM32N6 ball is assigned in this package: the N6 end of every signal is a named function until STM32CubeMX assigns balls for the VFBGA-264 TO-VERIFY[TV-14].

## Links between the brainstem (C6) and the cortex (N6)

```
 ESP32-C6-MINI-1 (3V3_AON)                                 STM32N657 (CTX domain)
 GPIO19 pad25 HOSTED_SCLK      ◄──────33R─────────────────  SPIx_SCK      (master)
 GPIO20 pad26 HOSTED_MOSI      ◄──────────────────────────  SPIx_MOSI
 GPIO21 pad27 HOSTED_MISO      ───────33R────────────────►  SPIx_MISO
 GPIO18 pad24 HOSTED_CS        ◄──────────────────────────  SPIx_NSS
 GPIO22 pad28 HOSTED_HANDSHAKE ──────────────────────────►  EXTI
 GPIO23 pad29 HOSTED_DATA_READY──────────────────────────►  EXTI
 GPIO16 pad31 CTRL_UART_C6_TX  ───────1k─────────────────►  USARTx_RX
 GPIO17 pad30 CTRL_UART_C6_RX  ◄──────1k─────────────────   USARTx_TX
 GPIO14 pad19 CTX_EN           ───► U23 ON (100k pull-down)
 GPIO15 pad20 CTX_CORE_EN      ───► U26.B  (100k pull-down) ─┐
 GPIO8  pad22 N6_PWR_ON_SENSE  ◄──10k──────────────────────── PWR_ON ──► U26.A
                                                             └─► VDDCORE_EN (U15, U17, U18)
```

| Link | Purpose | Notes |
|---|---|---|
| ESP-Hosted SPI (6 wires) | N6 network stack ↔ C6 Wi-Fi (plan 3.3) | C6 = SPI slave, pins via GPIO matrix and set in ESP-Hosted menuconfig; max clock and reset-line handling TO-VERIFY[TV-16]. The host-driven C6 reset of stock ESP-Hosted is **not wired**: the C6 owns power and must never be reset by the N6. |
| Control UART | power/sequencing protocol (SHUTDOWN, READY, wake reason, battery data) | On C6 UART0 pads; C6 console/flash moves to USB-Serial-JTAG (GPIO12/13 on J1). The C6 ROM prints its boot log here while the N6 is off. |
| CTX_EN, CTX_CORE_EN, PWR_ON sense | power gating + AN6000-safe shutdown | See power_tree.md. |

## Wake sources and AON peripherals (C6)

| Signal | C6 GPIO / pad | Why there |
|---|---|---|
| PIR_OUT | GPIO3 / 6 | LP GPIO → deep-sleep / LP-core wake |
| BUTTON_N | GPIO2 / 5 | LP GPIO |
| RADAR_TD (DNP) | GPIO4 / 9 | LP GPIO; MTMS strap, radar output low at reset TO-VERIFY[TV-15] |
| PMIC_INT_N | GPIO5 / 10 | LP GPIO; wired-OR of BQ25798 INT and MAX17048 ALRT; MTDI strap TO-VERIFY[TV-15] |
| I2C_AON SDA/SCL | GPIO6 / 15, GPIO7 / 16 | LP_I2C so the LP core can read light/gauge without waking the HP core |
| XTAL_32K_P/N | GPIO0 / 12, GPIO1 / 13 | optional 32.768 kHz crystal TO-VERIFY[TV-17] |
| USB D−/D+ | GPIO12 / 17, GPIO13 / 18 | USB-Serial-JTAG |
| C6_BOOT | GPIO9 / 23 | boot strap only |

**Pin budget finding.** The MINI-1 exposes 22 GPIOs (0–9, 12–23) and this allocation uses all 22. Any new always-on signal (e.g. a second wake source, N6 NRST from the C6) needs either dropping the 32 kHz crystal (frees GPIO0/1) or an I2C GPIO expander on I2C_AON. The N6 does not need a separate reset from the C6 because power-cycling the cortex domain is the reset.

## I2C buses

| Bus | Master | Devices (7-bit) | Pull-ups |
|---|---|---|---|
| I2C_AON | C6 LP_I2C | MAX17048 0x36, BQ25798 0x6B, VEML7700 0x10, ATECC608B-MAHDA 0x60 (TO-VERIFY[TV-10]) | 3V3_AON |
| I2C_CAM | N6 I2Cx | VD66GY 0x10 (fallback IMX335 0x1A) | 3V3_CTX |

Collisions the checker catches if someone swaps parts: ATECC608B **TFLXTLS** provisioning defaults to 0x36 (ATECC608A table), same as the MAX17048, so order the MAHDA/TNGTLS variant; and VEML7700 (0x10) must not move to the camera bus where the VD66GY also sits at 0x10. Rev 0: the DK's I2C1 (PH9/PC1) is shared by the camera connector CN14 and Arduino D14/D15, and the MB1854 module's ToF and IMU addresses are unknown TO-VERIFY[TV-11], so add nothing to Arduino I2C on Rev 0; AON sensors hang off the C6 instead.

## Cortex-only interfaces (N6)

| Interface | Signals | Electrical | Source |
|---|---|---|---|
| MIPI CSI-2 | CLK ±, D0 ±, D1 ± | D-PHY v1.2, up to 2 data lanes at up to 2.5 Gbit/s/lane; software lane remap (DL0MAP/DL1MAP) | RM0486 ch. 40 ([third-party mirror](https://docs.dirba.io/rm0486-stm32n6x5-x7/40-csi-2-host-csi/)) |
| Camera control | CAM_I2C, CAM_XSHUTDOWN, CAM_GPIO/FSYNC, optional EXTCLK, CAM_PWR_EN | VD66GY: VANA 2.8 V, VDDIO 1.8 V, VCORE 1.15 V; 1–2 lanes, 2 × 800 Mbps at full res | [VD66GY datasheet](https://file.icallin.com/resource/en/datasheet/vd66gy.pdf) |
| XSPI1 → PSRAM | CLK, NCLK, NCS, DQS0/1, IO0–15 | 256 Mbit, 1.8 V, 200 MHz DDR (DK: XSPIM_P1) | UM3300 |
| XSPI2 → NOR | CLK, NCS, DQS, IO0–7, NOR_RESET | 1 Gbit, 1.8 V, 200 MHz (DK: XSPIM_P2); boot device | UM3300 |
| Audio out | SAI SCK/FS/SD → MAX98357A, SD_MODE, AUDIO_EN | I2S | — |
| Audio in | PDM CLK + DATA (2 mics, L/R) | MDF/ADF | — |
| IR | IR_EN, IR_LED_PWM, IRCUT_A/B | constant-current sink via Q2; DRV8837 for IR-cut coil | — |
| DVFS | PWR_VCORE_DVFS → Q1 | 0 = 0.81 V, 1 = 0.89 V | AN6000 |
| Debug | SWDIO, SWCLK, SWO, NRST (J3), BOOT pins (SW3) | | TO-VERIFY[TV-14] |

## Test points

TP1–TP13 are on rails (rails.yaml). TP14–TP40 are listed in interfaces.yaml: power-control nets (CTX_EN, CTX_CORE_EN, PWR_ON sense, VDDCORE_EN), every wake line, both I2C buses, hosted SPI clock/CS/handshake/data-ready, control UART, CAM_PWR_EN, IR PWM, two spare N6 GPIO markers for the PPK2 logic port, DVFS, NTC, and four GND pads for probe returns.
