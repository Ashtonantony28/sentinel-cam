#include "scenario.h"
#include "test_util.h"
#include <string.h>


static void background_tick(void *ctx);

static void cb_dispatch(void *ctx, const sn_job_t *job)
{
    sc_world_t *w = (sc_world_t *)ctx;
    w->job = *job;
    w->job_pending = true;
}

static void cb_report(void *ctx, sn_bs_report_t kind, sn_wake_src_t wake, uint32_t detail)
{
    sc_world_t *w = (sc_world_t *)ctx;
    (void)wake;
    (void)detail;
    w->reports[kind]++;
}

void sc_world_init(sc_world_t *w, const sn_bs_cfg_t *cfg, uint32_t start_ms)
{
    sn_gal_person_spec_t specs[SC_KNOWN];
    sn_bs_ops_t ops;
    size_t len;
    uint32_t i;
    uint32_t k;
    uint32_t s = 2026u;

    memset(w, 0, sizeof(*w));
    sim_hal_reset(start_ms, SC_UNIX_BASE);
    sim_battery_set(3950u, 85u);
    w->t0 = start_ms;
    w->seed = 99u;

    for (i = 0u; i < SC_KNOWN; i++) {
        tu_unit_vec(&s, w->centers[i]);
        for (k = 0u; k < 5u; k++) {
            tu_noisy_q(&s, w->centers[i], 0.25f, w->protos[i][k]);
        }
        specs[i].id = (uint16_t)(i + 1u);
        specs[i].n_protos = 5u;
        specs[i].threshold_q15 = SN_GAL_DEFAULT_THRESHOLD_Q15;
        specs[i].name = "household";
        specs[i].protos = &w->protos[i][0][0];
    }
    (void)sn_gallery_write(w->blob, sizeof(w->blob), specs, SC_KNOWN, &len);
    (void)sn_gallery_parse(&w->gal, w->blob, len);

    sim_infer_init(&w->infer, &w->ops);
    sn_pipe_cfg_default(&w->pipe_cfg);
    w->pipe_cfg.dev = "c6n6a1b2c3d4";
    w->pipe_cfg.fw = "0.1.0";
    w->pipe_cfg.model = "det1-emb1";
    sn_pipe_init(&w->pipe, &w->pipe_cfg, &w->ops, &w->gal);

    if (cfg != NULL) {
        w->bs_cfg = *cfg;
    } else {
        sn_bs_cfg_default(&w->bs_cfg);
    }
    ops.ctx = w;
    ops.dispatch_job = cb_dispatch;
    ops.report = cb_report;
    sn_bs_init(&w->bs, &w->bs_cfg, &ops);
    sim_set_advance_hook(background_tick, w, SC_TICK_MS);
}

static void conf4(sim_scene_t *sc, float a, float b, float c, float d)
{
    sc->n_frames = 4u;
    sc->conf[0] = a; sc->conf[1] = b; sc->conf[2] = c; sc->conf[3] = d;
}

static void stranger(sc_world_t *w, int8_t out[SN_EMB_DIM])
{
    float v[SN_EMB_DIM];
    tu_unit_vec(&w->seed, v);
    sn_embed_quantize(v, out);
}

/* Fresh randomness per event so faces are never bit-identical. */
static void build_scene(sc_world_t *w, sc_scene_kind_t kind)
{
    sim_scene_t *sc = &w->cur_scene;
    memset(sc, 0, sizeof(*sc));
    switch (kind) {
    case SCN_CAT:        conf4(sc, 0.2f, 0.62f, 0.3f, 0.1f); break;
    case SCN_HEADLIGHTS: conf4(sc, 0.45f, 0.45f, 0.45f, 0.45f); break;
    case SCN_UNKNOWN:
        conf4(sc, 0.7f, 0.85f, 0.9f, 0.8f);
        sc->n_faces = 1u;
        stranger(w, sc->emb[0]);
        break;
    case SCN_NOFACE:     conf4(sc, 0.8f, 0.8f, 0.7f, 0.7f); break;
    case SCN_KNOWN1:
    case SCN_KNOWN2:
    case SCN_KNOWN3:
        conf4(sc, 0.9f, 0.9f, 0.9f, 0.9f);
        sc->n_faces = 1u;
        tu_noisy_q(&w->seed, w->centers[kind - SCN_KNOWN1], 0.25f, sc->emb[0]);
        break;
    case SCN_MIXED:
        conf4(sc, 0.9f, 0.9f, 0.9f, 0.9f);
        sc->n_faces = 2u;
        tu_noisy_q(&w->seed, w->centers[2], 0.25f, sc->emb[0]);
        stranger(w, sc->emb[1]);
        break;
    case SCN_EMPTY:
    case SCN_COUNT:
    default:
        conf4(sc, 0.1f, 0.05f, 0.12f, 0.08f);
        break;
    }
}

/* Starts the N6 job; stages then run one per loop iteration so the C6 keeps
 * ticking (and sees inputs) while the cortex works, like two real chips. */
static void start_job(sc_world_t *w)
{
    uint32_t now = sn_hal_time_ms();
    sc_scene_kind_t kind = sn_time_reached(now, w->scene_until) ? SCN_EMPTY : w->scene;
    w->job_pending = false;
    build_scene(w, kind);
    sim_infer_set_scene(&w->infer, &w->cur_scene);
    w->job_scene = kind;
    w->t_job_start = now;
    w->job_aborted = false;
    w->job_active = sn_pipe_start(&w->pipe, &w->job);
}

static void step_job(sc_world_t *w)
{
    const sn_event_record_t *r;
    if (sn_pipe_step(&w->pipe) != SN_PIPE_DONE) {
        return;
    }
    w->job_active = false;
    if (w->job_aborted) {
        w->n_aborted++;            /* cortex lost power: no record, no done */
        return;
    }
    r = &w->pipe.rec;
    {
        uint32_t now = w->t_job_start;
        sc_scene_kind_t kind = w->job_scene;
        if (w->n_recs < SC_MAX_RECORDS) {
            w->recs[w->n_recs].rec = *r;
            w->recs[w->n_recs].t_dispatch = now - w->t0;
            w->recs[w->n_recs].scene = kind;
            w->n_recs++;
        }
        if ((uint32_t)r->action < 4u) {
            w->actions[r->action]++;
        }
        w->sum_event_mj += r->energy_mj;
    }
    sn_bs_job_done(&w->bs, &w->pipe.result);
}

static sn_gpio_t pin_of(sc_item_kind_t k)
{
    return (k == SCI_BUTTON) ? SN_GPIO_BUTTON : ((k == SCI_RADAR) ? SN_GPIO_RADAR : SN_GPIO_PIR);
}

/* Applies the item; returns false if it must wait (pin still held). */
static bool apply_item(sc_world_t *w, const sc_item_t *it, uint32_t now)
{
    switch (it->kind) {
    case SCI_PIR:
    case SCI_BUTTON:
    case SCI_RADAR: {
        sn_gpio_t pin = pin_of(it->kind);
        if (w->pin_release_pending[pin]) {
            return false;
        }
        sim_gpio_set(pin, true);
        w->pin_release_pending[pin] = true;
        w->pin_release_t[pin] = now + ((it->dur_ms > 0u) ? it->dur_ms : 1u);
        if (it->kind != SCI_RADAR) {
            w->scene = it->scene;
            w->scene_until = now + it->dur_ms + 20000u;   /* person lingers */
        }
        break;
    }
    case SCI_BATTERY:
        sim_battery_set(it->mv, it->soc);
        sim_battery_set_load_sag(it->sag_mv);
        break;
    case SCI_RAIL_FAULT:
        sim_rail_config((sn_rail_t)it->mv, 1u, true);
        break;
    default:
        break;
    }
    return true;
}

/* Releases first, then due items (a press and its release are never applied
 * in the same tick). */
static void apply_inputs(sc_world_t *w, uint32_t now)
{
    uint32_t p;
    for (p = 0u; p < (uint32_t)SN_GPIO_COUNT; p++) {
        if (w->pin_release_pending[p] && sn_time_reached(now, w->pin_release_t[p])) {
            sim_gpio_set((sn_gpio_t)p, false);
            w->pin_release_pending[p] = false;
        }
    }
    while ((w->idx < w->n_items) && sn_time_reached(now, w->t0 + w->items[w->idx].t_ms)) {
        if (!apply_item(w, &w->items[w->idx], now)) {
            break;
        }
        w->idx++;
    }
}

/* Runs inside sim_advance() while an N6 stage is "executing". */
static void background_tick(void *ctx)
{
    sc_world_t *w = (sc_world_t *)ctx;
    apply_inputs(w, sn_hal_time_ms());
    sn_bs_tick(&w->bs);
    if (w->job_active && (sn_bs_state(&w->bs) != SN_BS_JOB)) {
        w->job_aborted = true;     /* C6 cut cortex power mid-job */
    }
}

void sc_world_run(sc_world_t *w, const sc_item_t *items, uint32_t n_items, uint32_t until_ms)
{
    uint32_t end = w->t0 + until_ms;
    w->items = items;
    w->n_items = n_items;
    w->idx = 0u;
    for (;;) {
        uint32_t now = sn_hal_time_ms();
        uint32_t next;
        uint32_t p;
        if (sn_time_reached(now, end) && !w->job_active) {
            sn_bs_tick(&w->bs);   /* account the final interval */
            break;
        }
        w->loop_iters++;
        apply_inputs(w, now);
        sn_bs_tick(&w->bs);
        if (w->job_pending) {
            start_job(w);
        }
        if (w->job_active) {
            step_job(w);      /* advances sim time by the stage's latency */
            continue;
        }
        /* next wake-up of the simulated C6 */
        next = end;
        if ((w->idx < n_items) && ((int32_t)((w->t0 + items[w->idx].t_ms) - next) < 0)) {
            next = w->t0 + items[w->idx].t_ms;
        }
        for (p = 0u; p < (uint32_t)SN_GPIO_COUNT; p++) {
            if (w->pin_release_pending[p] && ((int32_t)(w->pin_release_t[p] - next) < 0)) {
                next = w->pin_release_t[p];
            }
        }
        if ((w->bs.wake.cfg.timer_period_ms > 0u) &&
            ((int32_t)(sn_wake_next_timer(&w->bs.wake) - next) < 0)) {
            next = sn_wake_next_timer(&w->bs.wake);
        }
        if (sn_bs_needs_tick(&w->bs) && ((int32_t)((now + SC_TICK_MS) - next) < 0)) {
            next = now + SC_TICK_MS;
        }
        if (!((int32_t)(next - now) > 0)) {
            next = now + 1u;
        }
        sim_set_time(next);
    }
}

uint32_t sc_count(const sc_world_t *w, int action, int wake)
{
    uint32_t i;
    uint32_t n = 0u;
    for (i = 0u; i < w->n_recs; i++) {
        if (((action < 0) || ((int)w->recs[i].rec.action == action)) &&
            ((wake < 0) || ((int)w->recs[i].rec.wake == wake))) {
            n++;
        }
    }
    return n;
}

uint64_t sc_cortex_mj(const sc_world_t *w)
{
    return (w->bs.energy.cortex_boot.nj + w->bs.energy.cortex_active.nj +
            w->bs.energy.cortex_clip_extra.nj + 500000u) / 1000000u;
}
