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
| person_inference | 0.347 | 0.204 | 0.59x |
| clip | 0.556 | 0.576 | 1.04x |
| false_wakes | 0.231 | 0.306 | 1.32x |
| self_discharge_and_leakage | 0.300 | 0.186 | 0.62x |
| total | 2.934 | 3.109 | 1.06x |

## Battery life by scenario

| Scenario | Mode | Standby I_bat (uA) | Avg P nominal (mW) | Life nominal (mo) | MC median (mo) | MC p5-p95 (mo) | P(>=6 mo) | P(>=12 mo) |
|---|---|---|---|---|---|---|---|---|
| plan_mix_twt | twt | 514 | 3.109 | 7.1 | 6.5 | 4.6-8.8 | 64% | 0% |
| plan_mix_radio_off | radio_off | 40 | 1.440 | 15.1 | 11.6 | 7.8-17.0 | 100% | 44% |
| busy_door_twt | twt | 514 | 4.914 | 4.5 | 3.8 | 2.7-5.4 | 1% | 0% |
| quiet_radio_off | radio_off | 40 | 0.785 | 27.2 | 21.4 | 15.0-29.6 | 100% | 100% |
| plan_mix_twt_radar | twt | 829 | 4.121 | 5.3 | 4.6 | 3.3-6.2 | 8% | 0% |

## Power breakdown: plan_mix_twt (nominal)

| Component | mW | share |
|---|---|---|
| standby.c6 | 1.793 | 57.7% |
| clip.person_unknown | 0.576 | 18.5% |
| event.false_wake | 0.306 | 9.8% |
| event.person_known | 0.122 | 3.9% |
| self_discharge | 0.090 | 2.9% |
| event.person_unknown | 0.081 | 2.6% |
| standby.charger | 0.074 | 2.4% |
| periodic.status_publish | 0.032 | 1.0% |
| standby.sensors | 0.012 | 0.4% |
| standby.gauge | 0.011 | 0.4% |
| standby.pack_protection | 0.011 | 0.4% |
| standby.regulators_switches | 0.000 | 0.0% |
| **total** | **3.109** | |

Per-event energy (J, battery side): person_known 1.76, person_unknown 14.21, false_wake 1.32, button 14.21

### Sensitivity: one-at-a-time swing (plan_mix_twt)

| Constant | life @low (mo) | life @high (mo) | swing (mo) |
|---|---|---|---|
| aon_loads.twt_interval | 5.4 | 9.6 | 4.15 |
| aon_loads.c6_twt_wake_energy | 9.1 | 5.0 | 4.05 |
| rate.person_unknown (x1/2..x2) | 7.9 | 5.8 | 2.05 |
| rate.false_wake (x1/2..x2) | 7.4 | 6.4 | 0.99 |
| cortex.sensor_start_time | 7.3 | 6.4 | 0.97 |
| battery.deploy_temp | 6.1 | 7.1 | 0.96 |
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
| clip.person_unknown | 0.576 | 40.0% |
| event.false_wake | 0.279 | 19.4% |
| event.person_known | 0.182 | 12.6% |
| event.person_unknown | 0.121 | 8.4% |
| self_discharge | 0.090 | 6.2% |
| standby.charger | 0.074 | 5.1% |
| periodic.checkin | 0.045 | 3.1% |
| standby.c6 | 0.039 | 2.7% |
| standby.sensors | 0.012 | 0.8% |
| standby.gauge | 0.011 | 0.8% |
| standby.pack_protection | 0.011 | 0.8% |
| standby.regulators_switches | 0.000 | 0.0% |
| **total** | **1.440** | |

Per-event energy (J, battery side): person_known 2.61, person_unknown 15.06, false_wake 1.20, button 15.06

### Sensitivity: one-at-a-time swing (plan_mix_radio_off)

| Constant | life @low (mo) | life @high (mo) | swing (mo) |
|---|---|---|---|
| rate.person_unknown (x1/2..x2) | 19.8 | 10.2 | 9.56 |
| cortex.sensor_start_time | 16.4 | 12.2 | 4.16 |
| rate.false_wake (x1/2..x2) | 16.7 | 12.7 | 3.99 |
| cortex.ir_led_power | 16.5 | 12.9 | 3.62 |
| cortex.clip_time | 17.4 | 13.9 | 3.53 |
| rate.person_known (x1/2..x2) | 16.1 | 13.4 | 2.66 |
| cortex.encode_power | 16.0 | 13.5 | 2.55 |
| cortex.night_fraction | 16.3 | 14.1 | 2.22 |
| cortex.reject_pipeline_time | 15.6 | 13.5 | 2.10 |
| radio.reconnect_time | 15.8 | 13.8 | 2.05 |

### Sensitivity: Spearman rank correlation with life (MC, plan_mix_radio_off)

| Input | rho |
|---|---|
| rate.person_unknown | -0.70 |
| rate.false_wake | -0.37 |
| cortex.sensor_start_time | -0.23 |
| rate.person_known | -0.22 |
| cortex.ir_led_power | -0.20 |
| cortex.clip_time | -0.17 |
| cortex.night_fraction | -0.13 |
| cortex.person_pipeline_time | -0.12 |
| battery.usable_fraction | +0.12 |
| cortex.reject_pipeline_time | -0.12 |

