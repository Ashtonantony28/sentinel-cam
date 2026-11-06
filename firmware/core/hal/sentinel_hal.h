/*
 * sentinel_hal.h - hardware abstraction for Sentinel firmware core.
 *
 * The core libraries (power sequencer, wake fusion, pipeline, gallery) only
 * touch hardware through this header. Two kinds of interface:
 *
 *  1. Link-time HAL functions (sn_hal_*): implemented once per target
 *     (ESP-IDF on the ESP32-C6 brainstem, STM32Cube on the STM32N6 cortex,
 *     and firmware/core/sim/sim_hal.c on the host).
 *  2. The inference provider (sn_infer_ops_t): a table of function pointers
 *     passed to the pipeline, so on host the pipeline can be driven by
 *     scripted fake detector outputs and on target by the NPU runtime.
 *
 * Conventions: monotonic time is a wrapping uint32 millisecond counter
 * (compare with sn_time_reached()); calls never block unless documented.
 */
#ifndef SENTINEL_HAL_H
#define SENTINEL_HAL_H

#include "sn_types.h"

/* ---------------------------------------------------------------- time -- */

/* Monotonic milliseconds since boot (wraps after ~49.7 days). */
uint32_t sn_hal_time_ms(void);
/* Wall clock, unix epoch milliseconds (SNTP-disciplined on target). */
uint64_t sn_hal_unix_ms(void);
/* Busy/sleep wait. The core state machines are tick driven and do not call
 * this; it exists for board bring-up code. */
void sn_hal_delay_ms(uint32_t ms);

/* ---------------------------------------------------------------- GPIO -- */

typedef enum {
    SN_GPIO_PIR = 0,       /* PIR output (active high)                   */
    SN_GPIO_RADAR,         /* mmWave presence output (optional, DNP-able) */
    SN_GPIO_BUTTON,        /* doorbell button (active high after inverter)*/
    SN_GPIO_IR_LED,        /* IR illuminator enable                        */
    SN_GPIO_CORTEX_WAKE,   /* C6 -> N6 wake / job-pending line            */
    SN_GPIO_COUNT
} sn_gpio_t;

void sn_hal_gpio_write(sn_gpio_t pin, bool level);
bool sn_hal_gpio_read(sn_gpio_t pin);

/* ------------------------------------------------- cortex power domain -- */

/* Switched rails feeding the STM32N6 domain (each behind a load switch). */
typedef enum {
    SN_RAIL_VDD_3V3 = 0,   /* VDD / VDDIO 3.3 V                    */
    SN_RAIL_VDD_1V8,       /* 1.8 V: XSPI PSRAM + NOR, VDDIO2/3    */
    SN_RAIL_VDDCORE,       /* ~0.8-0.9 V core buck                 */
    SN_RAIL_VDDA_1V8,      /* analog / CSI PHY / USB PHY 1.8 V     */
    SN_RAIL_COUNT
} sn_rail_t;

void sn_hal_rail_enable(sn_rail_t rail, bool on);
bool sn_hal_rail_power_good(sn_rail_t rail);
/* NRST of the N6. asserted == held in reset. */
void sn_hal_cortex_reset(bool asserted);
/* N6 -> C6 "booted and ready for a job" handshake (GPIO or UART message). */
bool sn_hal_cortex_ready(void);

/* --------------------------------------------------------- wake flags -- */

#define SN_WAKE_FLAG_PIR    (1u << 0)
#define SN_WAKE_FLAG_RADAR  (1u << 1)
#define SN_WAKE_FLAG_BUTTON (1u << 2)
#define SN_WAKE_FLAG_TIMER  (1u << 3)

/* Atomically read and clear latched wake-source edges (set by the LP core /
 * GPIO edge interrupts while the main core slept, so short pulses are not
 * lost between ticks). */
uint32_t sn_hal_wake_flags_take(void);

/* ------------------------------------------------------------ battery -- */

uint16_t sn_hal_battery_mv(void);
uint8_t  sn_hal_battery_soc_pct(void);

/* ------------------------------------------- sensor/inference provider -- */

/*
 * The pipeline calls these in order: camera_start, person_detect for frames
 * 0..3 (may stop early), face_detect/face_embed on chosen frames,
 * clip_record if the policy wants a clip, publish once.
 * On target each call blocks for the real latency; on host the scripted
 * provider advances simulated time instead.
 * Any call may return false to signal a stage failure; the pipeline degrades
 * (frame counted as no-person, face skipped, clip_id null) and still reports.
 */
typedef struct sn_infer_ops {
    void *ctx;
    /* Power up the image sensor and wait for AE/AWB to settle. */
    bool (*camera_start)(void *ctx);
    /* Capture frame `frame` and run the person detector; *conf = max person
     * confidence in [0,1]. */
    bool (*person_detect)(void *ctx, uint8_t frame, float *conf);
    /* Run the face detector on the person crops of a captured frame; returns
     * number of faces found (<= max_faces). Boxes stay inside the provider. */
    uint8_t (*face_detect)(void *ctx, uint8_t frame, uint8_t max_faces);
    /* Align + embed face `face` of frame `frame`, int8 per INTERFACES.md 1. */
    bool (*face_embed)(void *ctx, uint8_t frame, uint8_t face, int8_t emb[SN_EMB_DIM]);
    /* Encode an H.264 clip of duration_ms and store/upload it under clip_id. */
    bool (*clip_record)(void *ctx, const char *clip_id, uint32_t duration_ms);
    /* Publish the event JSON on sentinel/{dev}/event (via the C6). */
    bool (*publish)(void *ctx, const char *json, size_t len);
    /* Power the image sensor down. Optional (may be NULL). */
    void (*camera_stop)(void *ctx);
} sn_infer_ops_t;

#endif /* SENTINEL_HAL_H */
