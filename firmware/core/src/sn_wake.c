#include "sn_wake.h"
#include <string.h>

const sn_wake_cfg_t SN_WAKE_CFG_DEFAULT = {
    .button_debounce_ms      = 30u,
    .pir_min_pulse_ms        = 100u,
    .radar_mode              = SN_RADAR_ABSENT,
    .radar_min_ms            = 200u,
    .radar_confirm_window_ms = 1500u,
    .motion_holdoff_ms       = 10000u,
    .rate_max_wakes          = 4u,     /* < 60 s / holdoff, so a steady storm trips it */
    .rate_window_ms          = 60000u,
    .cooldown_ms             = 300000u,
    .timer_period_ms         = 0u,
};

void sn_wake_init(sn_wake_t *w, const sn_wake_cfg_t *cfg, uint32_t now)
{
    if (w == NULL) {
        return;
    }
    (void)memset(w, 0, sizeof(*w));
    w->cfg = (cfg != NULL) ? *cfg : SN_WAKE_CFG_DEFAULT;
    if (w->cfg.rate_max_wakes > SN_WAKE_RATE_MAX) {
        w->cfg.rate_max_wakes = (uint8_t)SN_WAKE_RATE_MAX;
    }
    w->pend_motion = SN_WAKE_NONE;
    w->timer_next = now + w->cfg.timer_period_ms;
}

/* Called when a motion source has qualified at time t; applies holdoff and
 * rate limiting, then queues it. */
static void motion_candidate(sn_wake_t *w, sn_wake_src_t src, uint32_t t)
{
    uint8_t in_window = 0u;
    uint8_t i;

    if (w->cooling) {
        if (!sn_time_reached(t, w->cooldown_until)) {
            w->stats.rate_limited++;
            return;
        }
        w->cooling = false;
        w->hist_count = 0u;    /* fresh window after a cooldown */
    }
    if (w->have_last_motion && ((t - w->last_motion_t) < w->cfg.motion_holdoff_ms)) {
        w->stats.coalesced++;
        return;
    }
    if (w->pend_motion != SN_WAKE_NONE) {
        w->stats.coalesced++;  /* one already queued and not yet polled */
        return;
    }
    for (i = 0u; i < w->hist_count; i++) {
        uint8_t idx = (uint8_t)((w->hist_head + SN_WAKE_RATE_MAX - 1u - i) % SN_WAKE_RATE_MAX);
        if ((t - w->hist[idx]) < w->cfg.rate_window_ms) {
            in_window++;
        }
    }
    if ((w->cfg.rate_max_wakes > 0u) && (in_window >= w->cfg.rate_max_wakes)) {
        w->stats.rate_limited++;
        w->stats.cooldowns++;
        w->cooling = true;
        w->cooldown_until = t + w->cfg.cooldown_ms;
        return;
    }
    w->hist[w->hist_head] = t;
    w->hist_head = (uint8_t)((w->hist_head + 1u) % SN_WAKE_RATE_MAX);
    if (w->hist_count < SN_WAKE_RATE_MAX) {
        w->hist_count++;
    }
    w->have_last_motion = true;
    w->last_motion_t = t;
    w->pend_motion = src;
    w->pend_motion_t = t;
}

static void pir_qualified(sn_wake_t *w, uint32_t t)
{
    switch (w->cfg.radar_mode) {
    case SN_RADAR_CONFIRM:
        if (w->radar_level ||
            (w->radar_seen && ((t - w->radar_t_seen) <= w->cfg.radar_confirm_window_ms))) {
            motion_candidate(w, SN_WAKE_PIR, t);
        } else {
            w->pir_wait_confirm = true;
            w->pir_t_qualified = t;
        }
        break;
    case SN_RADAR_ABSENT:
    case SN_RADAR_ANY:
    default:
        motion_candidate(w, SN_WAKE_PIR, t);
        break;
    }
}

void sn_wake_input(sn_wake_t *w, sn_wake_src_t src, bool level, uint32_t now)
{
    if (w == NULL) {
        return;
    }
    switch (src) {
    case SN_WAKE_BUTTON:
        if (level && !w->btn_level) {
            w->btn_t_rise = now;
            w->btn_fired = false;
        } else if (!level && w->btn_level) {
            if (!w->btn_fired) {
                if ((now - w->btn_t_rise) >= w->cfg.button_debounce_ms) {
                    /* Held long enough but only seen at its edges (no poll in
                     * between, e.g. C6 asleep): still a press. */
                    w->btn_fired = true;
                    if (!w->pend_button) {
                        w->pend_button = true;
                        w->pend_button_t = w->btn_t_rise + w->cfg.button_debounce_ms;
                    }
                } else {
                    w->stats.button_glitches++;
                }
            }
        } else {
            /* no edge */
        }
        w->btn_level = level;
        break;
    case SN_WAKE_PIR:
        if (level && !w->pir_level) {
            w->pir_t_rise = now;
            w->pir_qualified = false;
        } else if (!level && w->pir_level) {
            if (!w->pir_qualified) {
                if ((now - w->pir_t_rise) >= w->cfg.pir_min_pulse_ms) {
                    /* Qualified exactly at falling edge (no poll in between). */
                    w->pir_qualified = true;
                    pir_qualified(w, w->pir_t_rise + w->cfg.pir_min_pulse_ms);
                } else {
                    w->stats.pir_spikes++;
                }
            }
        } else {
            /* no edge */
        }
        w->pir_level = level;
        break;
    case SN_WAKE_RADAR:
        if (w->cfg.radar_mode == SN_RADAR_ABSENT) {
            break;   /* radar not fitted / ignored */
        }
        if (level && !w->radar_level) {
            w->radar_t_rise = now;
            w->radar_qualified = false;
        }
        if (level) {
            w->radar_seen = true;
            w->radar_t_seen = now;
        }
        w->radar_level = level;
        break;
    case SN_WAKE_TIMER:
    case SN_WAKE_NONE:
    default:
        break;
    }
}

static void advance(sn_wake_t *w, uint32_t now)
{
    /* Button debounce. */
    if (w->btn_level && !w->btn_fired &&
        ((now - w->btn_t_rise) >= w->cfg.button_debounce_ms)) {
        w->btn_fired = true;
        if (!w->pend_button) {
            w->pend_button = true;
            w->pend_button_t = w->btn_t_rise + w->cfg.button_debounce_ms;
        }
    }
    /* PIR pulse qualification while still high. */
    if (w->pir_level && !w->pir_qualified &&
        ((now - w->pir_t_rise) >= w->cfg.pir_min_pulse_ms)) {
        w->pir_qualified = true;
        pir_qualified(w, w->pir_t_rise + w->cfg.pir_min_pulse_ms);
    }
    /* Radar. */
    if (w->cfg.radar_mode != SN_RADAR_ABSENT) {
        if (w->radar_level) {
            w->radar_seen = true;
            w->radar_t_seen = now;
        }
        if ((w->cfg.radar_mode == SN_RADAR_ANY) && w->radar_level && !w->radar_qualified &&
            ((now - w->radar_t_rise) >= w->cfg.radar_min_ms)) {
            w->radar_qualified = true;
            motion_candidate(w, SN_WAKE_RADAR, w->radar_t_rise + w->cfg.radar_min_ms);
        }
        if (w->pir_wait_confirm) {
            if (w->radar_seen && sn_time_reached(w->radar_t_seen, w->pir_t_qualified)) {
                w->pir_wait_confirm = false;
                motion_candidate(w, SN_WAKE_PIR, w->radar_t_seen);
            } else if ((now - w->pir_t_qualified) > w->cfg.radar_confirm_window_ms) {
                w->pir_wait_confirm = false;
                w->stats.radar_unconfirmed++;
            } else {
                /* keep waiting */
            }
        }
    }
    /* Timer. */
    if ((w->cfg.timer_period_ms > 0u) && sn_time_reached(now, w->timer_next)) {
        if (!w->pend_timer) {
            w->pend_timer = true;
            w->pend_timer_t = w->timer_next;
        }
        /* Skip missed periods instead of bursting. */
        while (sn_time_reached(now, w->timer_next)) {
            w->timer_next += w->cfg.timer_period_ms;
        }
    }
}

bool sn_wake_poll(sn_wake_t *w, uint32_t now, sn_wake_event_t *ev)
{
    if ((w == NULL) || (ev == NULL)) {
        return false;
    }
    advance(w, now);
    if (w->pend_button) {
        w->pend_button = false;
        ev->src = SN_WAKE_BUTTON;
        ev->t_ms = w->pend_button_t;
    } else if (w->pend_motion != SN_WAKE_NONE) {
        ev->src = w->pend_motion;
        ev->t_ms = w->pend_motion_t;
        w->pend_motion = SN_WAKE_NONE;
    } else if (w->pend_timer) {
        w->pend_timer = false;
        ev->src = SN_WAKE_TIMER;
        ev->t_ms = w->pend_timer_t;
    } else {
        return false;
    }
    w->stats.emitted[ev->src]++;
    return true;
}

bool sn_wake_busy(const sn_wake_t *w)
{
    if (w == NULL) {
        return false;
    }
    return (w->btn_level && !w->btn_fired) || (w->pir_level && !w->pir_qualified) ||
           w->pir_wait_confirm ||
           ((w->cfg.radar_mode != SN_RADAR_ABSENT) && w->radar_level) ||
           w->pend_button || (w->pend_motion != SN_WAKE_NONE) || w->pend_timer;
}

uint32_t sn_wake_next_timer(const sn_wake_t *w)
{
    return (w == NULL) ? 0u : w->timer_next;
}

bool sn_wake_cooling(const sn_wake_t *w, uint32_t now)
{
    return (w != NULL) && w->cooling && !sn_time_reached(now, w->cooldown_until);
}
