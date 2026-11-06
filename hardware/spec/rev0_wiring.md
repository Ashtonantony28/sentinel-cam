# Rev 0 bench wiring guide

STM32N6570-DK + ESP32-C6-DevKitC-1 + EKMB PIR + MAX17048/VEML7700 breakouts + Nordic PPK2. Machine-readable: [`rev0_wiring.yaml`](rev0_wiring.yaml) (`make test` checks that every wire uses a header pin that exists in the vendor tables, that no pin is used twice, and that GPIO numbers match the DevKitC silkscreen).

Only pins taken from vendor documents are used:
- DevKitC-1 J1/J3 tables and J5 note: [ESP32-C6-DevKitC-1 user guide](https://docs.espressif.com/projects/esp-dev-kits/en/latest/esp32c6/esp32-c6-devkitc-1/user_guide.html)
- DK Arduino (Table 16), STMod+ (Table 13), CN2 IDD pins, VCP: [UM3300](https://www.st.com.cn/resource/en/user_manual/um3300-discovery-kit-with-stm32n657x0-mcu-stmicroelectronics.pdf)
- PPK2 limits: [PPK2 user guide](https://docs.nordicsemi.com/r/bundle/ug_ppk2/page/ug/ppk/ppk_user_guide_intro.html)

Both boards use 3.3 V logic on these pins (UM3300: Arduino digital I/O are 3.3 V; ADC pins are 1.8 V, so do not use A0–A5 for these signals). Tie grounds with two wires before connecting any signal.

## Wire list

| Signal | C6 DevKitC-1 pin (GPIO) | DK pin (Arduino / MCU pin) | Direction |
|---|---|---|---|
| HOSTED_SCLK | J3-9 (GPIO19) | CN12-6 (D13 / PE15, SPI5_SCK) | DK → C6 |
| HOSTED_MOSI | J3-8 (GPIO20) | CN12-4 (D11 / PG2, SPI5_MOSI) | DK → C6 |
| HOSTED_MISO | J3-7 (GPIO21) | CN12-5 (D12 / PH8, SPI5_MISO) | C6 → DK |
| HOSTED_CS | J3-10 (GPIO18) | CN12-3 (D10 / PA3) | DK → C6 |
| HOSTED_HANDSHAKE | J3-6 (GPIO22) | CN11-3 (D2 / PD0) | C6 → DK |
| HOSTED_DATA_READY | J3-5 (GPIO23) | CN11-5 (D4 / PH5) | C6 → DK |
| CTRL_UART C6 TX | J1-10 (GPIO10) | CN11-1 (D0 / PF6, USART2_RX) | C6 → DK |
| CTRL_UART C6 RX | J1-11 (GPIO11) | CN11-2 (D1 / PD5, USART2_TX) | DK → C6 |
| N6_READY | J1-7 (GPIO0) | CN11-8 (D7 / PD6) | DK → C6 |
| GND | J3-1 | CN12-7 | — |
| GND | J3-12 | CN8-6 | — |
| PIR_OUT | J1-13 (GPIO3, LP) | — | PIR → C6 |
| BUTTON_N | J1-12 (GPIO2, LP) | — | button → C6 |
| I2C_AON SDA / SCL | J1-5 (GPIO6) / J1-6 (GPIO7) | — | breakouts |
| CTX_EN | J1-8 (GPIO1) | — | → load switch on DK 5 V input |
| 3V3 for PIR + breakouts | J1-1 | — | — |
| Marker DK 0 / 1 | — | CN12-1 (D8 / PE7), CN12-2 (D9 / PE14) | → PPK2 D0 / D1 |
| Marker C6 | J3-4 (GPIO15) | — | → PPK2 D2 |

Differences from Rev A (deliberate): the control UART uses GPIO10/11 because DevKitC GPIO16/17 go to the on-board USB-UART bridge (TO-VERIFY[TV-33]); the DevKitC headers do not bring out GPIO14, so CTX_EN is on GPIO1; there is no PWR_ON on the DK headers, so N6_READY (PD6) stands in for the PWR_ON sense.

Pins avoided on the DevKitC: GPIO8 (RGB LED + strap), GPIO9 (boot strap), GPIO4/5 (MTMS/MTDI straps), GPIO12/13 (USB). GPIO15 is a strap too; it is only used as an output marker after boot.

Pins avoided on the DK: Arduino D14/D15 (PC1/PH9 = I2C1, shared with the camera connector CN14; MB1854 ToF/IMU addresses unknown TO-VERIFY[TV-11]); A0–A5 (1.8 V ADC pins). The VCP is USART1 on PE5/PE6 via ST-LINK, so USART2 on D0/D1 is free for the control link.

## Power gating the DK on Rev 0

The DK cannot gate the N6 rails from outside. Rev 0 gates the whole DK: JP2 set to 5V_VIN, 5 V fed into CN8-8 (VIN) through a high-side load switch (TPS22917 breakout or a P-FET with a level shifter) enabled by CTX_EN. Whether the DK boots cleanly from 5V_VIN with the ST-LINK USB unplugged, and what the ST-LINK draws, is TO-VERIFY[TV-20]. This measures the energy of a gated wake but includes DK overheads (ST-LINK, LEDs, regulators), so Rev 0 numbers are an upper bound for Rev A.

## PPK2 hookups (one at a time; PPK2 measures 500 nA–1 A, 100 kS/s, source 0.8–5.0 V)

1. **C6 module standby / TWT (M2 go/no-go, TV-01/TV-02).** USB powers the DevKitC; remove J5 and put the PPK2 in ampere-meter mode across J5 (the guide: with J5 removed, an ammeter across J5 measures the module current only). Log PIR wake, TWT service periods and publishes. Marker C6 on PPK2 D2.
2. **N6 rails (M1, TV-04).** DK CN2 pairs, each usable when its series resistor is removed (UM3300): 11/12 VDDIO (R79), 13/14 VDDA1V8 (R187), 3/4 VDDCORE in external-SMPS mode (R31). VDDCORE can exceed the PPK2's 1 A at full NPU load (AN6000 rates it at 2 A): use a 50 mΩ shunt + scope or a second meter for that rail TO-VERIFY[TV-21]. Markers DK 0/1 on PPK2 D0/D1 bracket inference and encode.
3. **Whole-DK event energy (upper bound).** PPK2 in source-meter mode at 5.0 V into the load switch feeding CN8-8 VIN; only valid if the DK peak stays under 1 A, otherwise use a bench supply + shunt.
4. **Breakout standby.** PPK2 source-meter at 3.3 V into the PIR + MAX17048 + VEML7700 rail (not the C6) to confirm the µA-level sensor numbers in constants.yaml.

Logic-port thresholds for 3.3 V markers are TO-VERIFY[TV-21]. Record every result in `power_model/constants.yaml` with `status: measured` and close the TO-VERIFY item in `spec/to_verify.yaml`.
