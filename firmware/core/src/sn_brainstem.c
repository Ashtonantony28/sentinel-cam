#include "sn_brainstem.h"
#include <string.h>

const char *sn_bs_report_name(sn_bs_report_t r)
{
    switch (r) {
    case SN_BS_REPORT_LOW_BATT_REFUSED: return "LOW_BATT_REFUSED";
    case SN_BS_REPORT_POWER_FAULT:      return "POWER_FAULT";
    case SN_BS_REPORT_CORTEX_LOST:      return "CORTEX_LOST";
    case SN_BS_REPORT_JOB_TIMEOUT:      return "JOB_TIMEOUT";
    case SN_BS_REPORT_WAKE_DROPPED:     return "WAKE_DROPPED";
    default:                            return "?";
    }
}

void sn_bs_cfg_default(sn_bs_cfg_t *cfg)
{
    if (cfg == NULL) {
        return;
    }
    cfg->wake = SN_WAKE_CFG_DEFAULT;
    cfg->pwr = &SN_PWR_CFG_DEFAULT;
    cfg->pm = &SN_POWER_MODEL_DEFAULT;
    cfg->job_timeout_ms = 30000u;
}

static void energy_hook(void *ctx, sn_pwr_state_t st, uint32_t dt_ms)
{
    sn_brainstem_t *bs = (sn_brainstem_t *)ctx;
    switch (st) {
    case SN_PWR_RAILS_UP:
    case SN_PWR_BOOTING:
        sn_energy_add_mw(&bs->energy.cortex_boot, bs->cfg.pm->cortex_boot_mw, dt_ms);
        sn_energy_add_mw(&bs->boot_this_cycle, bs->cfg.pm->cortex_boot_mw, dt_ms);
        break;
    case SN_PWR_SHUTDOWN:
        sn_energy_add_mw(&bs->energy.cortex_boot, bs->cfg.pm->cortex_boot_mw, dt_ms);
        break;
    case SN_PWR_READY:
    case SN_PWR_RUNNING:
        sn_energy_add_mw(&bs->energy.cortex_active, bs->cfg.pm->cortex_active_mw, dt_ms);
        break;
    case SN_PWR_OFF:
    case SN_PWR_RETRY_WAIT:
    case SN_PWR_FAULT:
    default:
        break;   /* cortex domain unpowered */
    }
}

static void report(sn_brainstem_t *bs, sn_bs_report_t kind, sn_wake_src_t w, uint32_t detail)
{
    bs->stats.reports[kind]++;
    if (bs->ops.report != NULL) {
        bs->ops.report(bs->ops.ctx, kind, w, detail);
    }
}

void sn_bs_init(sn_brainstem_t *bs, const sn_bs_cfg_t *cfg, const sn_bs_ops_t *ops)
{
    uint32_t now;
    if (bs == NULL) {
        return;
    }
    (void)memset(bs, 0, sizeof(*bs));
    if (cfg != NULL) {
        bs->cfg = *cfg;
    } else {
        sn_bs_cfg_default(&bs->cfg);
    }
    if (bs->cfg.pm == NULL) {
        bs->cfg.pm = &SN_POWER_MODEL_DEFAULT;
    }
    if (bs->cfg.pwr == NULL) {
        bs->cfg.pwr = &SN_PWR_CFG_DEFAULT;
    }
    if (ops != NULL) {
        bs->ops = *ops;
    }
    now = sn_hal_time_ms();
    bs->t_last = now;
    sn_wake_init(&bs->wake, &bs->cfg.wake, now);
    sn_pwr_init(&bs->pwr, bs->cfg.pwr, energy_hook, bs, now);
    bs->state = SN_BS_IDLE;
}

static void feed_inputs(sn_brainstem_t *bs, uint32_t now)
{
    static const struct { sn_wake_src_t src; sn_gpio_t pin; uint32_t flag; } k_map[3] = {
        { SN_WAKE_PIR,    SN_GPIO_PIR,    SN_WAKE_FLAG_PIR },
        { SN_WAKE_RADAR,  SN_GPIO_RADAR,  SN_WAKE_FLAG_RADAR },
        { SN_WAKE_BUTTON, SN_GPIO_BUTTON, SN_WAKE_FLAG_BUTTON },
    };
    uint32_t flags = sn_hal_wake_flags_take();
    uint32_t i;
    for (i = 0u; i < 3u; i++) {
        bool level = sn_hal_gpio_read(k_map[i].pin);
        if (((flags & k_map[i].flag) != 0u) && !level) {
            /* Edge latched while asleep but already gone: zero-width pulse,
             * which the debouncer classifies as a spike/glitch. */
            sn_wake_input(&bs->wake, k_map[i].src, true, now);
        }
        sn_wake_input(&bs->wake, k_map[i].src, level, now);
    }
}

static void dispatch(sn_brainstem_t *bs, const sn_wake_event_t *ev, uint32_t now)
{
    sn_job_t job;
    uint32_t age = now - ev->t_ms;
    uint64_t unix_now = sn_hal_unix_ms();
    (void)memset(&job, 0, sizeof(job));
    job.wake = ev->src;
    job.ts_unix_ms = (unix_now >= age) ? (unix_now - age) : unix_now;
    job.battery_mv = sn_hal_battery_mv();
    job.soc_pct = sn_hal_battery_soc_pct();
    job.boot_energy_known = true;
    job.boot_energy_mj = bs->boot_reported ? 0u : sn_energy_mj(&bs->boot_this_cycle);
    bs->boot_reported = true;
    bs->cur = *ev;
    bs->t_job = now;
    (void)sn_pwr_mark_running(&bs->pwr);
    bs->state = SN_BS_JOB;
    bs->stats.jobs_dispatched++;
    if (bs->ops.dispatch_job != NULL) {
        bs->ops.dispatch_job(bs->ops.ctx, &job);
    }
}

static void start_wake(sn_brainstem_t *bs, const sn_wake_event_t *ev, uint32_t now)
{
    sn_pwr_req_t r = sn_pwr_request_on(&bs->pwr, now);
    switch (r) {
    case SN_PWR_REQ_OK:
        sn_energy_reset(&bs->boot_this_cycle);
        bs->boot_reported = false;
        bs->cur = *ev;
        bs->state = SN_BS_POWERING_UP;
        break;
    case SN_PWR_REQ_REFUSED_LOW_BATT:
        report(bs, SN_BS_REPORT_LOW_BATT_REFUSED, ev->src, sn_hal_battery_mv());
        break;
    case SN_PWR_REQ_REFUSED_FAULT:
        report(bs, SN_BS_REPORT_POWER_FAULT, ev->src, (uint32_t)bs->pwr.last_error);
        break;
    case SN_PWR_REQ_REFUSED_BUSY:
    default:
        report(bs, SN_BS_REPORT_WAKE_DROPPED, ev->src, 0u);
        break;
    }
}

static void on_wake(sn_brainstem_t *bs, const sn_wake_event_t *ev, uint32_t now)
{
    bs->stats.wakes++;
    if (bs->state == SN_BS_IDLE) {
        start_wake(bs, ev, now);
    } else if ((ev->src == SN_WAKE_BUTTON) && !bs->have_pending) {
        bs->pending = *ev;
        bs->have_pending = true;
    } else {
        report(bs, SN_BS_REPORT_WAKE_DROPPED, ev->src, 0u);
    }
}

void sn_bs_tick(sn_brainstem_t *bs)
{
    uint32_t now;
    sn_wake_event_t ev;
    sn_pwr_state_t ps;
    if (bs == NULL) {
        return;
    }
    now = sn_hal_time_ms();
    sn_energy_add_uw(&bs->energy.standby, bs->cfg.pm->standby_uw, now - bs->t_last);
    bs->t_last = now;

    feed_inputs(bs, now);
    sn_pwr_tick(&bs->pwr, now);
    while (sn_wake_poll(&bs->wake, now, &ev)) {
        on_wake(bs, &ev, now);
    }

    ps = sn_pwr_state(&bs->pwr);
    switch (bs->state) {
    case SN_BS_POWERING_UP:
        if (ps == SN_PWR_READY) {
            dispatch(bs, &bs->cur, now);
        } else if ((ps == SN_PWR_OFF) || (ps == SN_PWR_FAULT)) {
            report(bs, (bs->pwr.last_error == SN_PWR_ERR_BROWNOUT) ?
                       SN_BS_REPORT_CORTEX_LOST : SN_BS_REPORT_POWER_FAULT,
                   bs->cur.src, (uint32_t)bs->pwr.last_error);
            bs->state = SN_BS_IDLE;
        } else {
            /* sequencing */
        }
        break;
    case SN_BS_JOB:
        if (!sn_pwr_is_on(&bs->pwr)) {
            report(bs, SN_BS_REPORT_CORTEX_LOST, bs->cur.src, (uint32_t)bs->pwr.last_error);
            bs->state = (ps == SN_PWR_OFF) ? SN_BS_IDLE : SN_BS_POWERING_DOWN;
        } else if ((now - bs->t_job) >= bs->cfg.job_timeout_ms) {
            report(bs, SN_BS_REPORT_JOB_TIMEOUT, bs->cur.src, now - bs->t_job);
            sn_pwr_request_off(&bs->pwr, now);
            bs->state = SN_BS_POWERING_DOWN;
        } else {
            /* waiting for job_done */
        }
        break;
    case SN_BS_POWERING_DOWN:
        if ((ps == SN_PWR_OFF) || (ps == SN_PWR_FAULT)) {
            bs->state = SN_BS_IDLE;
        }
        break;
    case SN_BS_IDLE:
    default:
        break;
    }
    if ((bs->state == SN_BS_IDLE) && bs->have_pending) {
        bs->have_pending = false;
        start_wake(bs, &bs->pending, now);
    }
}

void sn_bs_job_done(sn_brainstem_t *bs, const sn_job_result_t *res)
{
    uint32_t now;
    if ((bs == NULL) || (bs->state != SN_BS_JOB)) {
        return;
    }
    now = sn_hal_time_ms();
    /* Charge the cortex time up to now before changing state. */
    sn_pwr_tick(&bs->pwr, now);
    bs->stats.jobs_done++;
    if ((res != NULL) && (bs->cfg.pm->cortex_clip_mw > bs->cfg.pm->cortex_active_mw)) {
        sn_energy_add_mw(&bs->energy.cortex_clip_extra,
                         bs->cfg.pm->cortex_clip_mw - bs->cfg.pm->cortex_active_mw, res->clip_ms);
    }
    if (!sn_pwr_is_on(&bs->pwr)) {
        bs->state = SN_BS_IDLE;
        return;
    }
    (void)sn_pwr_mark_idle(&bs->pwr);
    if (bs->have_pending) {
        /* Cortex is still up: serve the queued button press back to back. */
        bs->have_pending = false;
        dispatch(bs, &bs->pending, now);
    } else {
        sn_pwr_request_off(&bs->pwr, now);
        bs->state = SN_BS_POWERING_DOWN;
    }
}

bool sn_bs_needs_tick(const sn_brainstem_t *bs)
{
    if (bs == NULL) {
        return false;
    }
    return (bs->state != SN_BS_IDLE) || !sn_pwr_is_idle_off(&bs->pwr) || bs->have_pending ||
           sn_wake_busy(&bs->wake) || sn_hal_gpio_read(SN_GPIO_PIR) ||
           sn_hal_gpio_read(SN_GPIO_BUTTON) ||
           ((bs->wake.cfg.radar_mode != SN_RADAR_ABSENT) && sn_hal_gpio_read(SN_GPIO_RADAR));
}

sn_bs_state_t sn_bs_state(const sn_brainstem_t *bs) { return (bs == NULL) ? SN_BS_IDLE : bs->state; }

uint64_t sn_bs_total_nj(const sn_brainstem_t *bs)
{
    if (bs == NULL) {
        return 0u;
    }
    return bs->energy.standby.nj + bs->energy.cortex_boot.nj + bs->energy.cortex_active.nj +
           bs->energy.cortex_clip_extra.nj;
}
