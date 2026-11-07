/*
 * Replays a scripted 24 h day through brainstem + pipeline (simulated HAL):
 *   - 20 PIR false wakes (wind, cat, headlights) 00:30-11:35
 *   - 10 people: 3 known (12:00, 13:10, 21:30), 7 unknown incl. a face-less
 *     visit, a known+stranger pair and a doorbell visitor
 *   - 1 button press (19:30:05, visitor already being processed)
 *   - a 20 min PIR storm 14:00-14:20 (pulse every 3 s)
 *   - battery sag: 3350 mV 20:00-21:00 (person at 20:15 refused), and a
 *     brown-out under load at 23:00
 *   - an 8 h health-check timer
 * and asserts actions, reports and energy.
 */
#include "sn_test.h"
#include "scenario.h"

#define SEC(x)  ((uint32_t)(x) * 1000u)
#define HMS(h, m, s) SEC((h) * 3600u + (m) * 60u + (s))
#define MAX_ITEMS 512u

static sc_item_t  g_items[MAX_ITEMS];
static uint32_t   g_n;
static sc_world_t W;
static int        g_ran;

static void add(uint32_t t, sc_item_kind_t k, uint32_t dur, sc_scene_kind_t sc)
{
    if (g_n < MAX_ITEMS) {
        g_items[g_n].t_ms = t;
        g_items[g_n].kind = k;
        g_items[g_n].dur_ms = dur;
        g_items[g_n].scene = sc;
        g_items[g_n].mv = 0u;
        g_items[g_n].soc = 0u;
        g_items[g_n].sag_mv = 0u;
        g_n++;
    }
}

static void add_batt(uint32_t t, uint16_t mv, uint8_t soc, uint16_t sag)
{
    add(t, SCI_BATTERY, 0u, SCN_EMPTY);
    g_items[g_n - 1u].mv = mv;
    g_items[g_n - 1u].soc = soc;
    g_items[g_n - 1u].sag_mv = sag;
}

static void build_day(bool with_storm)
{
    static const sc_scene_kind_t junk[3] = { SCN_EMPTY, SCN_CAT, SCN_HEADLIGHTS };
    uint32_t k;
    g_n = 0u;
    for (k = 0u; k < 20u; k++) {
        add(HMS(0, 30, 0) + k * SEC(35 * 60), SCI_PIR, 2000u, junk[k % 3u]);
    }
    add(HMS(12, 0, 0),  SCI_PIR, 2000u, SCN_KNOWN1);
    add(HMS(12, 40, 0), SCI_PIR, 2000u, SCN_UNKNOWN);
    add(HMS(13, 10, 0), SCI_PIR, 2000u, SCN_KNOWN2);
    if (with_storm) {
        for (k = 0u; k < 400u; k++) {
            add(HMS(14, 0, 0) + k * 3000u, SCI_PIR, 1000u, SCN_EMPTY);
        }
    }
    add(HMS(15, 0, 0),  SCI_PIR, 2000u, SCN_NOFACE);
    add(HMS(15, 30, 0), SCI_PIR, 2000u, SCN_UNKNOWN);
    add(HMS(17, 30, 0), SCI_PIR, 2000u, SCN_MIXED);
    add(HMS(18, 0, 0),  SCI_PIR, 2000u, SCN_UNKNOWN);
    add(HMS(19, 30, 0), SCI_PIR, 3000u, SCN_UNKNOWN);       /* visitor walks up */
    add(HMS(19, 30, 5), SCI_BUTTON, 300u, SCN_UNKNOWN);     /* ... and rings    */
    add_batt(HMS(20, 0, 0), 3350u, 4u, 0u);                 /* sag              */
    add(HMS(20, 15, 0), SCI_PIR, 2000u, SCN_UNKNOWN);       /* refused          */
    add_batt(HMS(21, 0, 0), 3480u, 8u, 0u);                 /* recovered        */
    add(HMS(21, 30, 0), SCI_PIR, 2000u, SCN_KNOWN3);
    add_batt(HMS(22, 59, 0), 3480u, 8u, 300u);              /* cold cell: sags under load */
    add(HMS(23, 0, 0),  SCI_PIR, 2000u, SCN_EMPTY);         /* brown-out        */
}

static void run_day(void)
{
    sn_bs_cfg_t cfg;
    if (g_ran) {
        return;
    }
    g_ran = 1;
    build_day(true);
    sn_bs_cfg_default(&cfg);
    cfg.wake.timer_period_ms = HMS(8, 0, 0);
    sc_world_init(&W, &cfg, 1000u);
    sc_world_run(&W, g_items, g_n, HMS(24, 0, 0) - 1u);   /* the 3rd 8 h timer is tomorrow */
}

static uint32_t storm_records(const sc_world_t *w, uint64_t *mj)
{
    uint32_t i;
    uint32_t n = 0u;
    *mj = 0u;
    for (i = 0u; i < w->n_recs; i++) {
        uint32_t t = w->recs[i].t_dispatch;
        if ((t >= HMS(14, 0, 0)) && (t < HMS(14, 25, 0))) {
            n++;
            *mj += w->recs[i].rec.energy_mj;
        }
    }
    return n;
}

static void day_actions(void)
{
    uint64_t storm_mj;
    uint32_t storm;
    run_day();
    storm = storm_records(&W, &storm_mj);
    printf("  records %u: NONE %u, LOG_KNOWN %u, NOTIFY %u, RING %u; storm wakes %u\n",
           (unsigned)W.n_recs, (unsigned)W.actions[0], (unsigned)W.actions[1],
           (unsigned)W.actions[2], (unsigned)W.actions[3], (unsigned)storm);
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_LOG_KNOWN, -1), 3u);
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP, -1), 6u);
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_RING, SN_WAKE_BUTTON), 1u);
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_RING, -1), 1u);
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_NONE, SN_WAKE_TIMER), 2u);
    /* 20 false wakes + storm wakes, all NONE from PIR */
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_NONE, SN_WAKE_PIR), 20u + storm);
    CHECK_EQ_INT(W.n_recs, 3u + 6u + 1u + 2u + 20u + storm);
    /* every person-present record came from a person scene, and vice versa */
    {
        uint32_t i;
        for (i = 0u; i < W.n_recs; i++) {
            const sc_record_t *r = &W.recs[i];
            bool person_scene = (r->scene >= SCN_UNKNOWN);
            if (r->rec.wake != SN_WAKE_BUTTON) {
                CHECK_EQ_INT(r->rec.action != SN_ACTION_NONE, person_scene);
            }
            if (r->rec.action == SN_ACTION_LOG_KNOWN) {
                CHECK_EQ_INT(r->rec.n_faces, 1u);
                CHECK(r->rec.faces[0].known);
                CHECK_EQ_INT(r->rec.faces[0].id, (uint32_t)(r->scene - SCN_KNOWN1 + 1));
            }
            if (r->scene == SCN_MIXED) {
                CHECK_EQ_INT(r->rec.action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
                CHECK_EQ_INT(r->rec.n_faces, 2u);
            }
            CHECK_EQ_INT(r->rec.has_clip,
                         (r->rec.action == SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP) ||
                         ((r->rec.action == SN_ACTION_RING) && (r->rec.person_conf >= 0.5f)));
            if (i > 0u) {
                CHECK(r->rec.ts_ms > W.recs[i - 1u].rec.ts_ms);
            }
        }
    }
}

static void day_storm_is_rate_limited(void)
{
    uint64_t mj;
    uint32_t storm;
    run_day();
    storm = storm_records(&W, &mj);
    /* 4 wakes per window, then 5 min cooldown: <= 4 per ~5.8 min over 20 min */
    CHECK(storm >= 8u);
    CHECK(storm <= 16u);
    CHECK(W.bs.wake.stats.cooldowns >= 2u);
    CHECK(W.bs.wake.stats.rate_limited >= 300u);
    printf("  storm: %u wakes, %llu mJ; %u PIR pulses rate-limited, %u coalesced\n",
           (unsigned)storm, (unsigned long long)mj, (unsigned)W.bs.wake.stats.rate_limited,
           (unsigned)W.bs.wake.stats.coalesced);
}

/* Same storm with the limiter disabled costs several times more. */
static void day_storm_limiter_saves_energy(void)
{
    sn_bs_cfg_t cfg;
    static sc_world_t U;
    uint64_t lim_mj;
    uint64_t unl_mj;
    uint32_t lim;
    uint32_t unl;
    run_day();
    lim = storm_records(&W, &lim_mj);
    build_day(true);
    sn_bs_cfg_default(&cfg);
    cfg.wake.timer_period_ms = HMS(8, 0, 0);
    cfg.wake.rate_max_wakes = 0u;              /* limiter off, holdoff only */
    sc_world_init(&U, &cfg, 1000u);
    sc_world_run(&U, g_items, g_n, HMS(24, 0, 0) - 1u);
    unl = storm_records(&U, &unl_mj);
    printf("  storm unlimited: %u wakes %llu mJ vs limited %u wakes %llu mJ\n",
           (unsigned)unl, (unsigned long long)unl_mj, (unsigned)lim, (unsigned long long)lim_mj);
    CHECK(unl >= 90u);
    CHECK(unl_mj >= (4u * lim_mj));
    g_ran = 0;                                 /* g_items rebuilt; keep W valid */
}

static void day_button_visitor_back_to_back(void)
{
    uint32_t i;
    int found = 0;
    run_day();
    for (i = 0u; i < W.n_recs; i++) {
        if (W.recs[i].rec.action == SN_ACTION_RING) {
            const sn_event_record_t *prev = &W.recs[i - 1u].rec;
            found = 1;
            CHECK_EQ_INT(prev->action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
            CHECK_EQ_INT(prev->wake, SN_WAKE_PIR);
            CHECK(W.recs[i].rec.has_clip);
            /* ring is stamped with the press time, served without re-boot */
            CHECK_NEAR((double)W.recs[i].rec.ts_ms,
                       (double)(SC_UNIX_BASE + HMS(19, 30, 5) + 30u), SC_TICK_MS);
            CHECK(W.recs[i].rec.energy_mj < prev->energy_mj);
        }
    }
    CHECK(found);
    /* power-ups = records - 1 (the ring shared the visitor's power cycle)
     *           + 1 (the 23:00 brown-out attempt never reached READY: 0) */
    CHECK_EQ_INT(W.bs.pwr.stats.power_ups, W.n_recs - 1u);
}

static void day_battery_sag(void)
{
    run_day();
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_LOW_BATT_REFUSED], 1u);   /* 20:15 */
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_CORTEX_LOST], 1u);        /* 23:00 */
    CHECK_EQ_INT(W.bs.pwr.stats.brownouts, 1u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_POWER_FAULT], 0u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_JOB_TIMEOUT], 0u);
    {
        uint32_t i;
        for (i = 0u; i < W.n_recs; i++) {
            uint32_t t = W.recs[i].t_dispatch;
            CHECK(!((t >= HMS(20, 0, 0)) && (t < HMS(21, 0, 0))));   /* nothing ran */
            if (t >= HMS(21, 0, 0)) {
                CHECK_EQ_INT(W.recs[i].rec.battery_mv, 3480u);
                CHECK_EQ_INT(W.recs[i].rec.soc_pct, 8u);
            }
        }
    }
    CHECK(!sim_any_rail_on());
    CHECK_EQ_INT(sn_bs_state(&W.bs), SN_BS_IDLE);
}

static void day_energy(void)
{
    uint64_t standby;
    uint64_t cortex;
    uint64_t total;
    double days;
    uint32_t i;
    run_day();
    standby = sn_energy_mj(&W.bs.energy.standby);
    cortex = sc_cortex_mj(&W);
    total = standby + cortex;
    days = (18.5 * 3600.0 * 1000.0) / (double)total;   /* 5000 mAh x 3.7 V */
    printf("  energy/day: standby %llu mJ, cortex %llu mJ (boot %u, active %u, clip+ %u), "
           "events sum %llu mJ\n", (unsigned long long)standby, (unsigned long long)cortex,
           (unsigned)sn_energy_mj(&W.bs.energy.cortex_boot),
           (unsigned)sn_energy_mj(&W.bs.energy.cortex_active),
           (unsigned)sn_energy_mj(&W.bs.energy.cortex_clip_extra),
           (unsigned long long)W.sum_event_mj);
    printf("  average %.3f mW -> %.0f days on 18.5 Wh\n", (double)total / 86400.0, days);
    /* standby: exactly standby_uw for 24 h (less 1 ms) */
    CHECK_EQ_INT(standby, sc_standby_mj(&W, HMS(24, 0, 0) - 1u));
    /* N6 per-event estimates agree with the C6's integration within 5 % */
    CHECK_NEAR((double)W.sum_event_mj, (double)cortex, 0.05 * (double)cortex);
    /* per-action energy envelopes, derived from the power model: the part of
     * each event outside the clip is within [0.5, 1.6] x the nominal no-clip
     * event (fewer frames / shared boot at the low end, faces + embeds at the
     * high end), and the clip adds exactly clip_mw x clip_ms. */
    {
        const uint32_t nom = sc_nominal_event_mj(&W, false);
        const uint32_t clip = sc_nominal_event_mj(&W, true) - nom;
        uint32_t lo = UINT32_MAX;
        uint32_t hi = 0u;
        for (i = 0u; i < W.n_recs; i++) {
            const sn_event_record_t *r = &W.recs[i].rec;
            uint32_t base = r->energy_mj;
            if (r->has_clip) {
                CHECK(r->energy_mj > clip);
                base = r->energy_mj - clip;
            }
            lo = (base < lo) ? base : lo;
            hi = (base > hi) ? base : hi;
            CHECK(base >= nom / 2u);
            CHECK(base <= (nom * 16u) / 10u);
        }
        printf("  per-event energy outside the clip: %u..%u mJ (nominal %u, clip +%u)\n",
               (unsigned)lo, (unsigned)hi, (unsigned)nom, (unsigned)clip);
    }
    /* plan 4.4 envelope: this busy day still clears the 6-month goal (G1) */
    CHECK(days >= 180.0);
    CHECK(total < 400000u);
}

static void day_records_serialize(void)
{
    uint32_t i;
    run_day();
    for (i = 0u; i < W.n_recs; i++) {
        char b[SN_EVENT_JSON_MAX];
        size_t n = sn_event_to_json(&W.recs[i].rec, b, sizeof(b));
        CHECK(n > 0u);
        CHECK(strncmp(b, "{\"v\":1,\"dev\":\"c6a1b2c3d4e5\"", 27) == 0);
        CHECK(strstr(b, sn_action_name(W.recs[i].rec.action)) != NULL);
        printf("  EVENT %s\n", b);              /* consumed by tests/integration */
    }
    CHECK_EQ_INT(W.infer.n_publish, W.n_recs + W.n_aborted);
    CHECK_EQ_INT(W.n_aborted, 0u);
}

/* The brainstem's fault reports become INTERFACES 3 status messages:
 * 20:15 refused (LOW_BATTERY), 23:00 brown-out during boot (BROWNOUT). */
static void day_status_json(void)
{
    uint32_t i;
    run_day();
    CHECK_EQ_INT(W.n_status, 2u);
    CHECK_EQ_INT(W.status[0].reason, SN_FAULT_LOW_BATTERY);
    CHECK_EQ_INT(W.status[1].reason, SN_FAULT_BROWNOUT);
    CHECK_EQ_INT(W.status[0].battery_mv, 3350u);
    CHECK_NEAR((double)W.status[0].ts_ms, (double)(SC_UNIX_BASE + 1000u + HMS(20, 15, 0)), 1000.0);
    for (i = 0u; i < W.n_status; i++) {
        char b[SN_STATUS_JSON_MAX];
        size_t n = sn_status_to_json(&W.status[i], b, sizeof(b));
        CHECK(n > 0u);
        CHECK(strncmp(b, "{\"state\":\"fault\",\"reason\":\"", 27) == 0);
        printf("  STATUS %s\n", b);             /* consumed by tests/integration */
    }
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(day_actions);
    RUN_TEST(day_storm_is_rate_limited);
    RUN_TEST(day_button_visitor_back_to_back);
    RUN_TEST(day_battery_sag);
    RUN_TEST(day_energy);
    RUN_TEST(day_records_serialize);
    RUN_TEST(day_status_json);
    RUN_TEST(day_storm_limiter_saves_energy);
    TEST_END();
}
