#include "sn_test.h"
#include "scenario.h"

static sc_world_t W;

#define SEC(x) ((uint32_t)(x) * 1000u)

static void bs_single_unknown_event(void)
{
    const sc_item_t items[] = { { SEC(10), SCI_PIR, 2000u, SCN_UNKNOWN, 0u, 0u, 0u } };
    sc_world_init(&W, NULL, 1000u);
    sc_world_run(&W, items, 1u, SEC(60));
    CHECK_EQ_INT(W.n_recs, 1u);
    CHECK_EQ_INT(W.recs[0].rec.action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(W.recs[0].rec.wake, SN_WAKE_PIR);
    CHECK(W.recs[0].rec.has_clip);
    CHECK_EQ_INT(W.bs.pwr.stats.power_ups, 1u);
    CHECK_EQ_INT(sn_bs_state(&W.bs), SN_BS_IDLE);
    CHECK_EQ_INT(sn_pwr_state(&W.bs.pwr), SN_PWR_OFF);
    CHECK(!sim_any_rail_on());
    CHECK_EQ_INT(W.bs.stats.jobs_done, 1u);
    /* ts = wall-clock time of the PIR qualifying (rise + 100 ms) */
    CHECK_EQ_INT(W.recs[0].rec.ts_ms, SC_UNIX_BASE + SEC(10) + 100u);
    CHECK_EQ_INT(W.recs[0].rec.battery_mv, 3950u);
    CHECK_EQ_INT(W.recs[0].rec.soc_pct, 85u);
    /* C6-integrated cortex energy agrees with the N6's event estimate */
    printf("  event %u mJ, brainstem cortex %u mJ\n", (unsigned)W.recs[0].rec.energy_mj,
           (unsigned)sc_cortex_mj(&W));
    CHECK_NEAR((double)sc_cortex_mj(&W), (double)W.recs[0].rec.energy_mj,
               0.05 * (double)W.recs[0].rec.energy_mj);
}

/* The C6 charges cortex_active_mw while the job runs and then books the clip
 * at cortex_clip_mw, whether the clip draws more or less than the pipeline
 * (constants.yaml has encode_power < pipeline_power). N6 and C6 agree both ways. */
static void bs_clip_energy_either_direction(void)
{
    static const int32_t deltas[3] = { -400, 0, 400 };
    const sc_item_t items[] = { { SEC(10), SCI_PIR, 2000u, SCN_UNKNOWN, 0u, 0u, 0u } };
    uint32_t k;
    for (k = 0u; k < 3u; k++) {
        sn_power_model_t pm = SN_POWER_MODEL_DEFAULT;
        sn_bs_cfg_t cfg;
        uint32_t clip_mj;
        pm.cortex_clip_mw = (uint32_t)((int32_t)pm.cortex_active_mw + deltas[k]);
        sn_bs_cfg_default(&cfg);
        cfg.pm = &pm;
        sc_world_init(&W, &cfg, 1000u);
        sc_world_run(&W, items, 1u, SEC(60));
        CHECK_EQ_INT(W.n_recs, 1u);
        CHECK(W.recs[0].rec.has_clip);
        clip_mj = sn_energy_mw_ms_to_mj(pm.cortex_clip_mw, W.pipe_cfg.clip_ms);
        CHECK(W.recs[0].rec.energy_mj > clip_mj);
        CHECK_NEAR((double)sc_cortex_mj(&W), (double)W.recs[0].rec.energy_mj,
                   0.03 * (double)W.recs[0].rec.energy_mj);
        CHECK_EQ_INT(sn_energy_mj(&W.bs.energy.cortex_clip_extra),
                     (deltas[k] > 0) ? sn_energy_mw_ms_to_mj((uint32_t)deltas[k], W.pipe_cfg.clip_ms) : 0u);
        printf("  clip %+d mW vs active: event %u mJ, C6 %u mJ\n", (int)deltas[k],
               (unsigned)W.recs[0].rec.energy_mj, (unsigned)sc_cortex_mj(&W));
    }
}

static void bs_false_wake_costs_about_a_joule(void)
{
    const sc_item_t items[] = { { SEC(5), SCI_PIR, 2000u, SCN_CAT, 0u, 0u, 0u } };
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, items, 1u, SEC(30));
    CHECK_EQ_INT(W.n_recs, 1u);
    CHECK_EQ_INT(W.recs[0].rec.action, SN_ACTION_NONE);
    /* about one nominal no-clip event (power model + sim latencies) */
    CHECK(W.recs[0].rec.energy_mj >= sc_nominal_event_mj(&W, false) / 2u);
    CHECK(W.recs[0].rec.energy_mj <= (sc_nominal_event_mj(&W, false) * 3u) / 2u);
    CHECK_EQ_INT(W.infer.n_clips, 0u);
    CHECK_NEAR((double)sc_cortex_mj(&W), (double)W.recs[0].rec.energy_mj,
               0.10 * (double)W.recs[0].rec.energy_mj);
}

/* Visitor walks up (PIR), rings 5 s later while the first job is running:
 * the button is queued and served without a power cycle. */
static void bs_button_during_job_back_to_back(void)
{
    const sc_item_t items[] = {
        { SEC(10), SCI_PIR,    3000u, SCN_UNKNOWN, 0u, 0u, 0u },
        { SEC(15), SCI_BUTTON, 300u,  SCN_UNKNOWN, 0u, 0u, 0u },
    };
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, items, 2u, SEC(90));
    CHECK_EQ_INT(W.n_recs, 2u);
    CHECK_EQ_INT(W.recs[0].rec.action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(W.recs[1].rec.action, SN_ACTION_RING);
    CHECK_EQ_INT(W.recs[1].rec.wake, SN_WAKE_BUTTON);
    CHECK(W.recs[1].rec.has_clip);                 /* person still in view */
    CHECK_EQ_INT(W.bs.pwr.stats.power_ups, 1u);    /* no power cycle */
    CHECK_EQ_INT(W.bs.stats.jobs_dispatched, 2u);
    CHECK(W.recs[1].rec.energy_mj < W.recs[0].rec.energy_mj + 1u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_WAKE_DROPPED], 0u);
    CHECK(!sim_any_rail_on());
}

static void bs_button_while_idle_rings(void)
{
    const sc_item_t items[] = { { SEC(3), SCI_BUTTON, 200u, SCN_EMPTY, 0u, 0u, 0u } };
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, items, 1u, SEC(30));
    CHECK_EQ_INT(W.n_recs, 1u);
    CHECK_EQ_INT(W.recs[0].rec.action, SN_ACTION_RING);
    CHECK(!W.recs[0].rec.has_clip);
    CHECK_EQ_INT(W.recs[0].rec.ts_ms, SC_UNIX_BASE + SEC(3) + 30u);   /* debounce */
}

static void bs_low_battery_refuses_and_reports(void)
{
    const sc_item_t items[] = {
        { SEC(1),  SCI_BATTERY, 0u,    SCN_EMPTY,   3300u, 3u, 0u },
        { SEC(10), SCI_PIR,     2000u, SCN_UNKNOWN, 0u,    0u, 0u },
        { SEC(40), SCI_BUTTON,  200u,  SCN_UNKNOWN, 0u,    0u, 0u },
    };
    const sim_rail_evt_t *log;
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, items, 3u, SEC(60));
    CHECK_EQ_INT(W.n_recs, 0u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_LOW_BATT_REFUSED], 2u);
    CHECK_EQ_INT(sim_rail_log(&log), 0u);          /* cortex never powered */
    CHECK_EQ_INT(sc_cortex_mj(&W), 0u);
    CHECK_EQ_INT(W.bs.pwr.stats.low_batt_refusals, 2u);
}

static void bs_brownout_mid_boot_reports_cortex_lost(void)
{
    const sc_item_t items[] = {
        { SEC(1),  SCI_BATTERY, 0u,    SCN_EMPTY,   3500u, 9u, 350u },  /* 3150 under load */
        { SEC(10), SCI_PIR,     2000u, SCN_UNKNOWN, 0u,    0u, 0u },
        { SEC(30), SCI_PIR,     2000u, SCN_UNKNOWN, 0u,    0u, 0u },
    };
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, items, 3u, SEC(60));
    CHECK_EQ_INT(W.n_recs, 0u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_CORTEX_LOST], 1u);
    CHECK_EQ_INT(W.bs.pwr.stats.brownouts, 1u);
    /* Unloaded 3500 mV would clear the hysteresis latch, but the brown-out
     * lockout keeps the second wake from browning out again. */
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_LOW_BATT_REFUSED], 1u);
    CHECK(!sim_any_rail_on());
    CHECK_EQ_INT(sn_bs_state(&W.bs), SN_BS_IDLE);
}

static void bs_power_fault_reports_then_refuses(void)
{
    const sc_item_t items[] = {
        { SEC(1),  SCI_RAIL_FAULT, 0u,    SCN_EMPTY,   (uint16_t)SN_RAIL_VDDCORE, 0u, 0u },
        { SEC(10), SCI_PIR,        2000u, SCN_UNKNOWN, 0u, 0u, 0u },
        { SEC(30), SCI_BUTTON,     200u,  SCN_UNKNOWN, 0u, 0u, 0u },  /* inside cooldown */
    };
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, items, 3u, SEC(50));
    CHECK_EQ_INT(W.n_recs, 0u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_POWER_FAULT], 2u);
    CHECK_EQ_INT(W.bs.pwr.stats.faults, 1u);
    CHECK_EQ_INT(W.bs.pwr.stats.retries, SN_PWR_CFG_DEFAULT.max_retries);
    CHECK(!sim_any_rail_on());
}

static void bs_quiet_day_sleeps(void)
{
    sc_world_init(&W, NULL, 0u);
    sc_world_run(&W, NULL, 0u, SEC(86400));
    CHECK_EQ_INT(W.n_recs, 0u);
    CHECK(W.loop_iters < 10u);                    /* deep sleep, no polling */
    CHECK_EQ_INT(sn_energy_mj(&W.bs.energy.standby), sc_standby_mj(&W, SEC(86400)));
    CHECK_EQ_INT(sc_cortex_mj(&W), 0u);
}

static void bs_timer_wake(void)
{
    sn_bs_cfg_t cfg;
    sn_bs_cfg_default(&cfg);
    cfg.wake.timer_period_ms = SEC(3600);
    sc_world_init(&W, &cfg, 0u);
    sc_world_run(&W, NULL, 0u, SEC(3 * 3600 + 60));
    CHECK_EQ_INT(W.n_recs, 3u);
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_NONE, SN_WAKE_TIMER), 3u);
    CHECK_EQ_INT(W.recs[1].rec.ts_ms, SC_UNIX_BASE + SEC(7200));
}

/* Edge latched while asleep but already gone at the tick -> glitch, no wake. */
static void bs_latched_short_pulse_is_glitch(void)
{
    sn_brainstem_t bs;
    sim_hal_reset(0u, SC_UNIX_BASE);
    sn_bs_init(&bs, NULL, NULL);
    sim_gpio_set(SN_GPIO_BUTTON, true);
    sim_gpio_set(SN_GPIO_BUTTON, false);
    sim_advance(10u);
    sn_bs_tick(&bs);
    sim_advance(100u);
    sn_bs_tick(&bs);
    CHECK_EQ_INT(bs.stats.wakes, 0u);
    CHECK_EQ_INT(bs.wake.stats.button_glitches, 1u);
}

/* N6 never answers: job timeout -> report, orderly power down. */
static int g_dispatched;
static void hang_dispatch(void *ctx, const sn_job_t *job) { (void)ctx; (void)job; g_dispatched++; }

static void bs_job_timeout(void)
{
    sn_brainstem_t bs;
    sn_bs_ops_t ops = { NULL, hang_dispatch, NULL };
    uint32_t i;
    sim_hal_reset(0u, SC_UNIX_BASE);
    g_dispatched = 0;
    sn_bs_init(&bs, NULL, &ops);
    sim_gpio_set(SN_GPIO_PIR, true);
    for (i = 0u; i < 4000u; i++) {             /* 40 s at 10 ms */
        if (i == 200u) {
            sim_gpio_set(SN_GPIO_PIR, false);
        }
        sim_advance(10u);
        sn_bs_tick(&bs);
    }
    CHECK_EQ_INT(g_dispatched, 1);
    CHECK_EQ_INT(bs.stats.reports[SN_BS_REPORT_JOB_TIMEOUT], 1u);
    CHECK_EQ_INT(sn_bs_state(&bs), SN_BS_IDLE);
    CHECK(!sim_any_rail_on());
    /* a late job_done after the timeout is ignored */
    sn_bs_job_done(&bs, NULL);
    CHECK_EQ_INT(bs.stats.jobs_done, 0u);
}

static void bs_timer_during_job_dropped(void)
{
    sn_bs_cfg_t cfg;
    const sc_item_t items[] = { { SEC(9), SCI_PIR, 2000u, SCN_UNKNOWN, 0u, 0u, 0u } };
    sn_bs_cfg_default(&cfg);
    cfg.wake.timer_period_ms = SEC(15);        /* fires mid clip */
    sc_world_init(&W, &cfg, 0u);
    sc_world_run(&W, items, 1u, SEC(25));
    CHECK_EQ_INT(sc_count(&W, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP, SN_WAKE_PIR), 1u);
    CHECK_EQ_INT(W.reports[SN_BS_REPORT_WAKE_DROPPED], 1u);
    CHECK_EQ_INT(sc_count(&W, -1, SN_WAKE_TIMER), 0u);
    CHECK_STR(sn_bs_report_name(SN_BS_REPORT_WAKE_DROPPED), "WAKE_DROPPED");
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(bs_single_unknown_event);
    RUN_TEST(bs_false_wake_costs_about_a_joule);
    RUN_TEST(bs_clip_energy_either_direction);
    RUN_TEST(bs_button_during_job_back_to_back);
    RUN_TEST(bs_button_while_idle_rings);
    RUN_TEST(bs_low_battery_refuses_and_reports);
    RUN_TEST(bs_brownout_mid_boot_reports_cortex_lost);
    RUN_TEST(bs_power_fault_reports_then_refuses);
    RUN_TEST(bs_quiet_day_sleeps);
    RUN_TEST(bs_timer_wake);
    RUN_TEST(bs_latched_short_pulse_is_glitch);
    RUN_TEST(bs_job_timeout);
    RUN_TEST(bs_timer_during_job_dropped);
    TEST_END();
}
