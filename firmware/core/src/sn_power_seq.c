#include "sn_power_seq.h"
#include <string.h>

/*
 * TODO-verify-against-STM32N6-datasheet: rail order, settle and PG timeouts
 * below are placeholders modelled on a typical "IO first, core next, analog
 * last; reverse on the way down" scheme and the STM32N6570-DK reference
 * design. Replace with DS14791 / AN5967 power-up sequencing requirements and
 * measured load-switch (TPS22917) rise times before Rev A bring-up.
 */
const sn_rail_step_t SN_N6_RAIL_SEQ[] = {
    /* rail              settle  pg_timeout  off_delay */
    { SN_RAIL_VDD_3V3,   2u,     10u,        2u },   /* TODO-verify-against-STM32N6-datasheet */
    { SN_RAIL_VDD_1V8,   2u,     10u,        2u },   /* TODO-verify-against-STM32N6-datasheet */
    { SN_RAIL_VDDCORE,   3u,     15u,        3u },   /* TODO-verify-against-STM32N6-datasheet */
    { SN_RAIL_VDDA_1V8,  1u,     10u,        1u },   /* TODO-verify-against-STM32N6-datasheet */
};
const uint8_t SN_N6_RAIL_SEQ_LEN = (uint8_t)(sizeof(SN_N6_RAIL_SEQ) / sizeof(SN_N6_RAIL_SEQ[0]));

const sn_pwr_cfg_t SN_PWR_CFG_DEFAULT = {
    .steps                  = SN_N6_RAIL_SEQ,
    .n_steps                = (uint8_t)(sizeof(SN_N6_RAIL_SEQ) / sizeof(SN_N6_RAIL_SEQ[0])),
    .reset_release_delay_ms = 2u,     /* TODO-verify-against-STM32N6-datasheet */
    .boot_timeout_ms        = 500u,   /* plan target < 150 ms to first frame   */
    .max_retries            = 2u,
    .retry_backoff_ms       = 200u,
    .batt_min_on_mv         = 3400u,  /* TODO-measure: 1S Li-ion under ~1 W load */
    .batt_hyst_mv           = 50u,
    .batt_brownout_mv       = 3200u,
    .brownout_debounce_ms   = 20u,
    .brownout_lockout_ms    = 600000u,  /* 10 min */
    .fault_cooldown_ms      = 60000u,
};

const char *sn_pwr_state_name(sn_pwr_state_t s)
{
    switch (s) {
    case SN_PWR_OFF:        return "OFF";
    case SN_PWR_RAILS_UP:   return "RAILS_UP";
    case SN_PWR_BOOTING:    return "BOOTING";
    case SN_PWR_READY:      return "READY";
    case SN_PWR_RUNNING:    return "RUNNING";
    case SN_PWR_SHUTDOWN:   return "SHUTDOWN";
    case SN_PWR_RETRY_WAIT: return "RETRY_WAIT";
    case SN_PWR_FAULT:      return "FAULT";
    default:                return "?";
    }
}

const char *sn_pwr_err_name(sn_pwr_err_t e)
{
    switch (e) {
    case SN_PWR_ERR_NONE:             return "NONE";
    case SN_PWR_ERR_PG_TIMEOUT:       return "PG_TIMEOUT";
    case SN_PWR_ERR_BOOT_TIMEOUT:     return "BOOT_TIMEOUT";
    case SN_PWR_ERR_RAIL_LOST:        return "RAIL_LOST";
    case SN_PWR_ERR_BROWNOUT:         return "BROWNOUT";
    case SN_PWR_ERR_LOW_BATT_REFUSED: return "LOW_BATT_REFUSED";
    default:                          return "?";
    }
}

static void enter(sn_pwr_t *p, sn_pwr_state_t s, uint32_t now)
{
    p->state = s;
    p->t_state = now;
}

/* Immediate, unordered-safe teardown: reset first, then rails in reverse. */
static void all_off(sn_pwr_t *p)
{
    uint8_t i = p->cfg->n_steps;
    sn_hal_cortex_reset(true);
    while (i > 0u) {
        i--;
        sn_hal_rail_enable(p->cfg->steps[i].rail, false);
    }
    p->reset_released = false;
}

static void start_rail_step(sn_pwr_t *p, uint8_t step, uint32_t now)
{
    p->step = step;
    p->t_state = now;
    sn_hal_rail_enable(p->cfg->steps[step].rail, true);
}

static void begin_power_up(sn_pwr_t *p, uint32_t now)
{
    sn_hal_cortex_reset(true);
    p->reset_released = false;
    p->state = SN_PWR_RAILS_UP;
    if (p->cfg->n_steps == 0u) {
        enter(p, SN_PWR_BOOTING, now);
    } else {
        start_rail_step(p, 0u, now);
    }
}

static void fail(sn_pwr_t *p, sn_pwr_err_t err, int8_t rail, uint32_t now)
{
    all_off(p);
    p->last_error = err;
    p->last_error_rail = rail;
    if (p->attempt < p->cfg->max_retries) {
        p->attempt++;
        p->stats.retries++;
        enter(p, SN_PWR_RETRY_WAIT, now);
    } else {
        p->stats.faults++;
        enter(p, SN_PWR_FAULT, now);
    }
}

static void emergency_off(sn_pwr_t *p, sn_pwr_err_t err, int8_t rail, uint32_t now)
{
    all_off(p);
    p->last_error = err;
    p->last_error_rail = rail;
    p->off_requested = false;
    enter(p, SN_PWR_OFF, now);
}

void sn_pwr_init(sn_pwr_t *p, const sn_pwr_cfg_t *cfg,
                 sn_pwr_energy_hook_t hook, void *hook_ctx, uint32_t now)
{
    if (p == NULL) {
        return;
    }
    (void)memset(p, 0, sizeof(*p));
    p->cfg = (cfg != NULL) ? cfg : &SN_PWR_CFG_DEFAULT;
    p->energy_hook = hook;
    p->energy_ctx = hook_ctx;
    p->last_error_rail = -1;
    p->t_last_tick = now;
    p->ticked = true;
    all_off(p);
    enter(p, SN_PWR_OFF, now);
}

static bool battery_allows_on(sn_pwr_t *p, uint32_t now)
{
    uint16_t mv = sn_hal_battery_mv();
    if (p->brownout_lock) {
        if ((now - p->t_brownout_lock) < p->cfg->brownout_lockout_ms) {
            return false;
        }
        p->brownout_lock = false;
    }
    uint32_t release = (uint32_t)p->cfg->batt_min_on_mv + (uint32_t)p->cfg->batt_hyst_mv;
    if (p->low_batt_latched) {
        if ((uint32_t)mv >= release) {
            p->low_batt_latched = false;
        }
    } else if (mv < p->cfg->batt_min_on_mv) {
        p->low_batt_latched = true;
    } else {
        /* ok */
    }
    return !p->low_batt_latched;
}

sn_pwr_req_t sn_pwr_request_on(sn_pwr_t *p, uint32_t now)
{
    if (p == NULL) {
        return SN_PWR_REQ_REFUSED_FAULT;
    }
    p->off_requested = false;
    switch (p->state) {
    case SN_PWR_OFF:
        if (!battery_allows_on(p, now)) {
            p->stats.low_batt_refusals++;
            p->last_error = SN_PWR_ERR_LOW_BATT_REFUSED;
            p->last_error_rail = -1;
            return SN_PWR_REQ_REFUSED_LOW_BATT;
        }
        p->attempt = 0u;
        p->brownout_pending = false;
        p->last_error = SN_PWR_ERR_NONE;
        p->last_error_rail = -1;
        begin_power_up(p, now);
        return SN_PWR_REQ_OK;
    case SN_PWR_FAULT:
        return SN_PWR_REQ_REFUSED_FAULT;
    case SN_PWR_SHUTDOWN:
        return SN_PWR_REQ_REFUSED_BUSY;
    default:
        return SN_PWR_REQ_OK;   /* already on or on the way */
    }
}

void sn_pwr_request_off(sn_pwr_t *p, uint32_t now)
{
    if (p == NULL) {
        return;
    }
    switch (p->state) {
    case SN_PWR_RAILS_UP:
    case SN_PWR_BOOTING:
    case SN_PWR_READY:
    case SN_PWR_RUNNING:
        /* Orderly: NRST first, then rails in reverse with off delays. In
         * RAILS_UP only steps 0..step are enabled. */
        {
            uint8_t n_on = (p->state == SN_PWR_RAILS_UP) ? (uint8_t)(p->step + 1u)
                                                         : p->cfg->n_steps;
            sn_hal_cortex_reset(true);
            p->reset_released = false;
            p->off_requested = true;
            if (n_on == 0u) {
                enter(p, SN_PWR_OFF, now);
            } else {
                p->step = (uint8_t)(n_on - 1u);
                sn_hal_rail_enable(p->cfg->steps[p->step].rail, false);
                enter(p, SN_PWR_SHUTDOWN, now);
            }
        }
        break;
    case SN_PWR_RETRY_WAIT:
        enter(p, SN_PWR_OFF, now);
        break;
    default:
        break;
    }
}

bool sn_pwr_mark_running(sn_pwr_t *p)
{
    if ((p != NULL) && (p->state == SN_PWR_READY)) {
        p->state = SN_PWR_RUNNING;
        return true;
    }
    return false;
}

bool sn_pwr_mark_idle(sn_pwr_t *p)
{
    if ((p != NULL) && (p->state == SN_PWR_RUNNING)) {
        p->state = SN_PWR_READY;
        return true;
    }
    return false;
}

sn_pwr_state_t sn_pwr_state(const sn_pwr_t *p) { return (p == NULL) ? SN_PWR_OFF : p->state; }

bool sn_pwr_is_on(const sn_pwr_t *p)
{
    return (p != NULL) && ((p->state == SN_PWR_READY) || (p->state == SN_PWR_RUNNING));
}

bool sn_pwr_is_idle_off(const sn_pwr_t *p)
{
    return (p != NULL) && (p->state == SN_PWR_OFF);
}

/* Any rail of the domain powered right now? */
static bool domain_powered(sn_pwr_state_t s)
{
    return (s == SN_PWR_RAILS_UP) || (s == SN_PWR_BOOTING) || (s == SN_PWR_READY) ||
           (s == SN_PWR_RUNNING) || (s == SN_PWR_SHUTDOWN);
}

static bool check_brownout(sn_pwr_t *p, uint32_t now)
{
    uint16_t mv = sn_hal_battery_mv();
    if (mv >= p->cfg->batt_brownout_mv) {
        p->brownout_pending = false;
        return false;
    }
    if (!p->brownout_pending) {
        p->brownout_pending = true;
        p->t_brownout = now;
    }
    if (sn_time_reached(now, p->t_brownout + p->cfg->brownout_debounce_ms)) {
        p->brownout_pending = false;
        p->stats.brownouts++;
        p->low_batt_latched = true;
        p->brownout_lock = true;
        p->t_brownout_lock = now;
        return true;
    }
    return false;
}

static void tick_rails_up(sn_pwr_t *p, uint32_t now)
{
    const sn_rail_step_t *st = &p->cfg->steps[p->step];
    uint32_t elapsed = now - p->t_state;
    if (elapsed < st->settle_ms) {
        return;
    }
    if (sn_hal_rail_power_good(st->rail)) {
        if ((uint8_t)(p->step + 1u) < p->cfg->n_steps) {
            start_rail_step(p, (uint8_t)(p->step + 1u), now);
        } else {
            enter(p, SN_PWR_BOOTING, now);
        }
    } else if (elapsed >= ((uint32_t)st->settle_ms + (uint32_t)st->pg_timeout_ms)) {
        fail(p, SN_PWR_ERR_PG_TIMEOUT, (int8_t)st->rail, now);
    } else {
        /* keep waiting */
    }
}

/* Returns index of first enabled rail that lost PG, or -1. */
static int8_t lost_rail(const sn_pwr_t *p, uint8_t upto)
{
    uint8_t i;
    for (i = 0u; i < upto; i++) {
        if (!sn_hal_rail_power_good(p->cfg->steps[i].rail)) {
            return (int8_t)p->cfg->steps[i].rail;
        }
    }
    return -1;
}

static void tick_booting(sn_pwr_t *p, uint32_t now)
{
    if (!p->reset_released) {
        if ((now - p->t_state) >= p->cfg->reset_release_delay_ms) {
            sn_hal_cortex_reset(false);
            p->reset_released = true;
            p->t_reset_release = now;
        }
        return;
    }
    if (sn_hal_cortex_ready()) {
        p->attempt = 0u;
        p->stats.power_ups++;
        enter(p, SN_PWR_READY, now);
    } else if ((now - p->t_reset_release) >= p->cfg->boot_timeout_ms) {
        fail(p, SN_PWR_ERR_BOOT_TIMEOUT, -1, now);
    } else {
        /* keep waiting */
    }
}

static void tick_shutdown(sn_pwr_t *p, uint32_t now)
{
    /* p->step = index of the rail most recently switched off; the next one
     * goes off once its off_delay has elapsed. */
    for (;;) {
        if ((now - p->t_state) < p->cfg->steps[p->step].off_delay_ms) {
            return;
        }
        if (p->step == 0u) {
            p->off_requested = false;
            enter(p, SN_PWR_OFF, now);
            return;
        }
        p->step--;
        sn_hal_rail_enable(p->cfg->steps[p->step].rail, false);
        p->t_state = now;
    }
}

void sn_pwr_tick(sn_pwr_t *p, uint32_t now)
{
    uint32_t dt;
    int8_t r;
    if (p == NULL) {
        return;
    }
    dt = now - p->t_last_tick;
    if ((p->energy_hook != NULL) && (dt > 0u)) {
        p->energy_hook(p->energy_ctx, p->state, dt);
    }
    p->t_last_tick = now;

    /* Brown-out protection applies whenever any rail is up. */
    if (domain_powered(p->state) && (p->state != SN_PWR_SHUTDOWN) && check_brownout(p, now)) {
        emergency_off(p, SN_PWR_ERR_BROWNOUT, -1, now);
        return;
    }

    switch (p->state) {
    case SN_PWR_RAILS_UP:
        r = lost_rail(p, p->step);   /* earlier rails must stay good */
        if (r >= 0) {
            fail(p, SN_PWR_ERR_RAIL_LOST, r, now);
        } else {
            tick_rails_up(p, now);
        }
        break;
    case SN_PWR_BOOTING:
        r = lost_rail(p, p->cfg->n_steps);
        if (r >= 0) {
            fail(p, SN_PWR_ERR_RAIL_LOST, r, now);
        } else {
            tick_booting(p, now);
        }
        break;
    case SN_PWR_READY:
    case SN_PWR_RUNNING:
        r = lost_rail(p, p->cfg->n_steps);
        if (r >= 0) {
            p->stats.rail_lost++;
            emergency_off(p, SN_PWR_ERR_RAIL_LOST, r, now);
        }
        break;
    case SN_PWR_SHUTDOWN:
        tick_shutdown(p, now);
        break;
    case SN_PWR_RETRY_WAIT:
        if ((now - p->t_state) >= p->cfg->retry_backoff_ms) {
            if (battery_allows_on(p, now)) {
                begin_power_up(p, now);
            } else {
                p->stats.low_batt_refusals++;
                p->last_error = SN_PWR_ERR_LOW_BATT_REFUSED;
                enter(p, SN_PWR_OFF, now);
            }
        }
        break;
    case SN_PWR_FAULT:
        if ((now - p->t_state) >= p->cfg->fault_cooldown_ms) {
            enter(p, SN_PWR_OFF, now);
        }
        break;
    case SN_PWR_OFF:
    default:
        break;
    }
}
