/*
 * sn_wake.h - wake-source debouncing, fusion and rate limiting (C6 side).
 *
 * Inputs are level samples / edges fed with a timestamp (from GPIO edge
 * interrupts, latched LP-core wake flags, or periodic sampling). Outputs are
 * fused wake events polled by the brainstem.
 *
 *  BUTTON  level must stay high for button_debounce_ms; one event per press,
 *          re-armed on release. Never rate limited (a doorbell must ring).
 *  PIR     a pulse must stay high for pir_min_pulse_ms (rejects spikes).
 *          radar_mode ABSENT  : PIR alone wakes.
 *          radar_mode CONFIRM : PIR wakes only if radar presence is seen
 *                               within radar_confirm_window_ms of the PIR
 *                               qualifying (before or after).
 *          radar_mode ANY     : PIR or radar rising edge each wake.
 *  RADAR   (ANY mode) presence high for radar_min_ms.
 *  TIMER   periodic, period timer_period_ms (0 = off). Not rate limited.
 *
 *  Motion (PIR/RADAR) wakes are then:
 *   - coalesced: ignored for motion_holdoff_ms after an emitted motion wake;
 *   - rate limited: at most rate_max_wakes within rate_window_ms; the wake
 *     that would exceed it is dropped and starts a cooldown_ms lockout in
 *     which every motion wake is dropped (PIR storm protection).
 *  Priority when several are pending: BUTTON > motion > TIMER.
 */
#ifndef SN_WAKE_H
#define SN_WAKE_H
#include "sn_types.h"

#define SN_WAKE_RATE_MAX 16u   /* capacity of the sliding-window history */

typedef enum {
    SN_RADAR_ABSENT = 0,
    SN_RADAR_CONFIRM,
    SN_RADAR_ANY
} sn_radar_mode_t;

typedef struct {
    uint16_t        button_debounce_ms;
    uint16_t        pir_min_pulse_ms;
    sn_radar_mode_t radar_mode;
    uint16_t        radar_min_ms;
    uint16_t        radar_confirm_window_ms;
    uint32_t        motion_holdoff_ms;
    uint8_t         rate_max_wakes;     /* <= SN_WAKE_RATE_MAX */
    uint32_t        rate_window_ms;
    uint32_t        cooldown_ms;
    uint32_t        timer_period_ms;
} sn_wake_cfg_t;

extern const sn_wake_cfg_t SN_WAKE_CFG_DEFAULT;

typedef struct {
    sn_wake_src_t src;
    uint32_t      t_ms;    /* when the wake qualified */
} sn_wake_event_t;

typedef struct {
    uint32_t emitted[5];        /* indexed by sn_wake_src_t */
    uint32_t pir_spikes;        /* pulses shorter than pir_min_pulse_ms */
    uint32_t button_glitches;
    uint32_t radar_unconfirmed; /* PIR not confirmed by radar */
    uint32_t coalesced;         /* inside holdoff */
    uint32_t rate_limited;      /* dropped by window or cooldown */
    uint32_t cooldowns;         /* times the lockout started */
} sn_wake_stats_t;

typedef struct {
    sn_wake_cfg_t   cfg;
    /* raw input tracking */
    bool            btn_level;
    uint32_t        btn_t_rise;
    bool            btn_fired;
    bool            pir_level;
    uint32_t        pir_t_rise;
    bool            pir_qualified;      /* this pulse already qualified */
    bool            radar_level;
    uint32_t        radar_t_rise;
    bool            radar_qualified;
    bool            radar_seen;         /* radar presence observed ... */
    uint32_t        radar_t_seen;       /* ... last at this time        */
    /* PIR waiting for radar confirmation */
    bool            pir_wait_confirm;
    uint32_t        pir_t_qualified;
    /* pending outputs */
    bool            pend_button;
    uint32_t        pend_button_t;
    sn_wake_src_t   pend_motion;        /* SN_WAKE_NONE if none */
    uint32_t        pend_motion_t;
    bool            pend_timer;
    uint32_t        pend_timer_t;
    /* timer */
    uint32_t        timer_next;
    /* coalescing + rate limiting */
    bool            have_last_motion;
    uint32_t        last_motion_t;
    uint32_t        hist[SN_WAKE_RATE_MAX];
    uint8_t         hist_head;
    uint8_t         hist_count;
    bool            cooling;
    uint32_t        cooldown_until;
    sn_wake_stats_t stats;
} sn_wake_t;

void sn_wake_init(sn_wake_t *w, const sn_wake_cfg_t *cfg, uint32_t now);
/* Feed the current level of PIR/RADAR/BUTTON (edges or samples). */
void sn_wake_input(sn_wake_t *w, sn_wake_src_t src, bool level, uint32_t now);
/* Advance time-based qualification; returns true and fills *ev for the
 * highest-priority qualified wake. Call repeatedly until it returns false. */
bool sn_wake_poll(sn_wake_t *w, uint32_t now, sn_wake_event_t *ev);
/* True if any input is mid-qualification (caller should keep ticking). */
bool sn_wake_busy(const sn_wake_t *w);
/* Earliest time a timer wake is due (valid if timer_period_ms != 0). */
uint32_t sn_wake_next_timer(const sn_wake_t *w);
bool sn_wake_cooling(const sn_wake_t *w, uint32_t now);

#endif
