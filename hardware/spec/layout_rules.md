# Rev A layout rules (KiCad 9)

Status: proposal for M4. Numbers without a source are engineering defaults to confirm with the fab and vendor guides; each open number names its TO-VERIFY item.

## 1. Stackup: 6 layers, 1.6 mm, ENIG

| Layer | Use | Notes |
|---|---|---|
| L1 (top) | components, CSI-2 pairs, XSPI, BGA fan-out | CSI-2 and XSPI route here over L2 ground; nothing crosses a plane split |
| L2 | solid GND | reference for L1; no traces |
| L3 | signals (BGA escape, slow I/O, I2C, UART, SPI to C6) | referenced to L2 and L4 |
| L4 | power planes: VDDCORE pour under the N6, 3V3_CTX, 1V8_CTX, SYS | VDDCORE pour sized for 2 A (AN6000) with via-in-pad/dog-bone vias to the BGA power balls |
| L5 | solid GND | reference for L6 |
| L6 (bottom) | battery connector, charger power stage, test points, shunt jumpers | keep test points reachable from the bottom for a pogo fixture |

Impedance targets: **100 Ω ±10 % differential** for CSI-2, **50 Ω ±10 % single-ended** for XSPI and the hosted SPI clock. Trace width/gap must come from the fab's stackup calculator for its 6-layer stack (e.g. JLCPCB's), not from this document: TO-VERIFY[TV-32]. Order with impedance control and the coupon.

## 2. STM32N6 BGA (VFBGA-264, 14 × 14 mm)

- Fan-out with the via structure the fab supports at that pitch (ball pitch from the ST package drawing; check before choosing via-in-pad vs dog-bone) TO-VERIFY[TV-14].
- Decoupling per AN5967 (not yet read) TO-VERIFY[TV-12]: put the smallest caps on L6 directly under the ball field, one per power ball pair where possible; bulk caps at the regulator outputs.
- VDDCORE regulator (U15) within ~10 mm of the BGA, sense the output at the BGA (remote-sense if the part supports it, otherwise route the feedback from the load point). AN6000 ripple budget is 30 mVpp and ±35 mV transient.
- Keep the DVFS MOSFET (Q1) and feedback divider tight to U15's FB pin; FB node is high-impedance and noise-sensitive.

## 3. MIPI CSI-2 (camera → N6)

- 100 Ω differential, routed on L1 over unbroken L2 GND, no layer changes if possible; if a layer change is unavoidable, both lines change together with a GND stitching via within 1 mm.
- Intra-pair skew ≤ 0.1 mm (≈ 0.7 ps), pair-to-clock skew ≤ 1 mm; both to be checked against the sensor vendor's layout guide TO-VERIFY[TV-22]. D-PHY at 800 Mbps/lane (VD66GY full res) leaves margin; the N6 host supports 2.5 Gbit/s/lane.
- Spacing to other signals ≥ 3× the trace width (3W); no stubs, no test points on the pairs (put TPs on the camera I2C instead).
- Total length as short as the mechanical design allows (sensor on the same board, lens centred). If the FFC fallback (J7) is used, keep the on-board segment < 30 mm and follow the module vendor's FFC pin order.
- The CSI-2 PHY supply (VDDA1V8 group) gets its own ferrite + local decoupling TO-VERIFY[TV-22].

## 4. XSPI (PSRAM on XSPI1, NOR on XSPI2)

- 200 MHz DDR (UM3300 DK parts). Route each bus as a group on L1/L3 with continuous ground reference.
- Length-match DQ and DQS to CLK within ±1 mm per port as a starting rule; confirm against ST's hardware guide and the DK layout TO-VERIFY[TV-23]. Matching between XSPI1 and XSPI2 is not needed.
- Series termination footprints (0 Ω default, 22–33 Ω option) on CLK and DQS near the N6.
- PSRAM and NOR within 15 mm of the N6; neither crosses under the C6 or the antenna.

## 5. ESP32-C6-MINI-1 antenna and RF

- Module on a board edge with the PCB antenna overhanging or over a copper-free keep-out on **all six layers**; no ground pour, traces or components in the keep-out. Exact keep-out dimensions are in the module datasheet layout section, not read yet TO-VERIFY[TV-24].
- Solid GND under the rest of the module, stitched to L2/L5 with a via fence along the keep-out boundary.
- Keep the antenna ≥ 15 mm from the battery, the speaker magnet and metal lens holder (design default, re-check with an RSSI test in M6).
- Footprint for the -1U (u.FL) fallback is the same module outline; leave space for the cable route.

## 6. Power

- Charger (U12) power stage on L6/L1 corner away from the camera and mics; follow the BQ25798 layout example (input caps, switching node area minimised).
- Every rail passes through its JPx shunt footprint before any load (rails.yaml); JP7 (VDDCORE, 2 A) uses a 2512 shunt or two parallel 0603 0 Ω.
- Switching nodes (U14, U15, U16, U17) are short and never routed under CSI-2 or the mics.
- Load switches (U23–U25) placed between SYS and their loads with QOD tied to VOUT.

## 7. Audio and sensors

- PDM mics on a quiet edge, separate analog ground island joined to GND at one point near the N6 (plan 4.3), port holes aligned with the enclosure.
- MAX98357A output traces short and paired; speaker leads away from the antenna.
- PIR lens with a clear field of view; keep IR LEDs out of the PIR field and out of the camera field (stray IR on the lens causes flare).
- VEML7700 under a window, away from the IR LEDs.

## 8. Test and bring-up

- 40 test points (rails.yaml, interfaces.yaml) on L6 at 2.54 mm grid where possible, labelled on silkscreen.
- SWD Tag-Connect (J3), C6 UART/EN header (J4), control UART tap (J5).
- Silkscreen the sequencing order next to JP4–JP9 so bring-up can populate rails one at a time (plan risk 8).
