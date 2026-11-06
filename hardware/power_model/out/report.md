# Sentinel power model report (generated: `make report`)

All numbers trace to `constants.yaml`; MC = Monte-Carlo over every constant's [low, high] range and over event counts.

## Plan section 4.4, reproduced exactly

| Row | Plan (mW) |
|---|---|
| standby | 1.500 |
| person_inference | 0.347 |
| clip | 0.556 |
| false_wakes | 0.231 |
| self_discharge_and_leakage | 0.300 |
| **total** | **2.934** |

Life = 18.5 Wh / 2.934 mW = 6305 h = **8.63 months** (the plan's "~9 months"; no derating).

## Same rows from the sourced model (plan mix, TWT, nominal constants)

| Row | Plan (mW) | Model (mW) | Ratio |
|---|---|---|---|
| standby | 1.500 | 1.838 | 1.23x |
| person_inference | 0.347 | 0.205 | 0.59x |
| clip | 0.556 | 0.576 | 1.04x |
| false_wakes | 0.231 | 0.308 | 1.33x |
| self_discharge_and_leakage | 0.300 | 0.186 | 0.62x |
| total | 2.934 | 3.113 | 1.06x |

## Battery life by scenario

| Scenario | Mode | Standby I_bat (uA) | Avg P nominal (mW) | Life nominal (mo) | MC median (mo) | MC p5-p95 (mo) | P(>=6 mo) | P(>=12 mo) |
|---|---|---|---|---|---|---|---|---|
| plan_mix_twt | twt | 514 | 3.113 | 7.0 | 6.4 | 4.6-8.7 | 64% | 0% |
| plan_mix_radio_off | radio_off | 40 | 1.443 | 15.0 | 11.5 | 7.8-16.9 | 100% | 43% |
| busy_door_twt | twt | 514 | 4.922 | 4.5 | 3.8 | 2.7-5.4 | 1% | 0% |
| quiet_radio_off | radio_off | 40 | 0.786 | 27.2 | 21.3 | 15.0-29.5 | 100% | 100% |
| plan_mix_twt_radar | twt | 829 | 4.123 | 5.3 | 4.5 | 3.3-6.2 | 8% | 0% |

## Power breakdown: plan_mix_twt (nominal)

| Component | mW | share |
|---|---|---|
| standby.c6 | 1.793 | 57.6% |
| clip.person_unknown | 0.576 | 18.5% |
| event.false_wake | 0.308 | 9.9% |
| event.person_known | 0.123 | 3.9% |
| self_discharge | 0.090 | 2.9% |
| event.person_unknown | 0.082 | 2.6% |
| standby.charger | 0.074 | 2.4% |
| periodic.status_publish | 0.032 | 1.0% |
| standby.sensors | 0.012 | 0.4% |
| standby.gauge | 0.011 | 0.4% |
| standby.pack_protection | 0.011 | 0.4% |
| standby.regulators_switches | 0.000 | 0.0% |
| **total** | **3.113** | |

Per-event energy (J, battery side): person_known 1.77, person_unknown 14.22, false_wake 1.33, button 14.22

### Sensitivity: one-at-a-time swing (plan_mix_twt)

| Constant | life @low (mo) | life @high (mo) | swing (mo) |
|---|---|---|---|
| aon_loads.twt_interval | 5.4 | 9.5 | 4.14 |
| aon_loads.c6_twt_wake_energy | 9.1 | 5.0 | 4.05 |
| rate.person_unknown (x1/2..x2) | 7.9 | 5.8 | 2.05 |
| rate.false_wake (x1/2..x2) | 7.4 | 6.4 | 0.99 |
| cortex.sensor_start_time | 7.3 | 6.3 | 0.97 |
| battery.deploy_temp | 6.1 | 7.0 | 0.96 |
| battery.usable_fraction | 6.5 | 7.4 | 0.91 |
| aon_loads.c6_light_sleep | 7.3 | 6.5 | 0.83 |
| cortex.ir_led_power | 7.3 | 6.5 | 0.82 |
| rails.aon_efficiency | 6.5 | 7.3 | 0.81 |

### Sensitivity: Spearman rank correlation with life (MC, plan_mix_twt)

| Input | rho |
|---|---|
| aon_loads.twt_interval | +0.54 |
| rate.person_unknown | -0.45 |
| aon_loads.c6_twt_wake_energy | -0.38 |
| rate.false_wake | -0.28 |
| cortex.sensor_start_time | -0.16 |
| battery.usable_fraction | +0.15 |
| cortex.ir_led_power | -0.14 |
| battery.deploy_temp | +0.14 |
| aon_loads.c6_light_sleep | -0.13 |
| cortex.clip_time | -0.11 |

## Power breakdown: plan_mix_radio_off (nominal)

| Component | mW | share |
|---|---|---|
| clip.person_unknown | 0.576 | 39.9% |
| event.false_wake | 0.281 | 19.5% |
| event.person_known | 0.182 | 12.6% |
| event.person_unknown | 0.122 | 8.4% |
| self_discharge | 0.090 | 6.2% |
| standby.charger | 0.074 | 5.1% |
| periodic.checkin | 0.045 | 3.1% |
| standby.c6 | 0.039 | 2.7% |
| standby.sensors | 0.012 | 0.8% |
| standby.gauge | 0.011 | 0.8% |
| standby.pack_protection | 0.011 | 0.8% |
| standby.regulators_switches | 0.000 | 0.0% |
| **total** | **1.443** | |

Per-event energy (J, battery side): person_known 2.62, person_unknown 15.07, false_wake 1.21, button 15.07

### Sensitivity: one-at-a-time swing (plan_mix_radio_off)

| Constant | life @low (mo) | life @high (mo) | swing (mo) |
|---|---|---|---|
| rate.person_unknown (x1/2..x2) | 19.7 | 10.2 | 9.52 |
| cortex.sensor_start_time | 16.3 | 12.2 | 4.14 |
| rate.false_wake (x1/2..x2) | 16.6 | 12.6 | 4.01 |
| cortex.ir_led_power | 16.5 | 12.9 | 3.60 |
| cortex.clip_time | 17.3 | 13.8 | 3.51 |
| rate.person_known (x1/2..x2) | 16.0 | 13.4 | 2.65 |
| cortex.encode_power | 16.0 | 13.5 | 2.54 |
| cortex.night_fraction | 16.2 | 14.0 | 2.21 |
| cortex.reject_pipeline_time | 15.6 | 13.5 | 2.09 |
| radio.reconnect_time | 15.8 | 13.8 | 2.04 |

### Sensitivity: Spearman rank correlation with life (MC, plan_mix_radio_off)

| Input | rho |
|---|---|
| rate.person_unknown | -0.70 |
| rate.false_wake | -0.38 |
| cortex.sensor_start_time | -0.23 |
| rate.person_known | -0.22 |
| cortex.ir_led_power | -0.20 |
| cortex.clip_time | -0.17 |
| cortex.night_fraction | -0.13 |
| battery.usable_fraction | +0.12 |
| cortex.person_pipeline_time | -0.12 |
| cortex.reject_pipeline_time | -0.12 |

