#include "sn_test.h"
#include "sn_wake.h"

static sn_wake_t W;
static uint32_t  T;

static void setup_cfg(const sn_wake_cfg_t *cfg, uint32_t start)
{
    T = start;
    sn_wake_init(&W, cfg, T);
}

static void setup(void) { setup_cfg(&SN_WAKE_CFG_DEFAULT, 0u); }

/* Advance 1 ms at a time, collecting events. */
static uint32_t advance(uint32_t ms, sn_wake_event_t *evs, uint32_t max)
{
    uint32_t n = 0u;
    uint32_t i;
    sn_wake_event_t ev;
    for (i = 0u; i < ms; i++) {
        T++;
        while (sn_wake_poll(&W, T, &ev)) {
            if ((evs != NULL) && (n < max)) {
                evs[n] = ev;
            }
            n++;
        }
    }
    return n;
}

static uint32_t pulse(sn_wake_src_t src, uint32_t width, sn_wake_event_t *evs, uint32_t max)
{
    uint32_t n;
    sn_wake_input(&W, src, true, T);
    n = advance(width, evs, max);
    sn_wake_input(&W, src, false, T);
    {
        sn_wake_event_t ev;
        while (sn_wake_poll(&W, T, &ev)) {
            if ((evs != NULL) && (n < max)) {
                evs[n] = ev;
            }
            n++;
        }
    }
    return n;
}

static void wake_button_debounce(void)
{
    sn_wake_event_t ev[4];
    setup();
    CHECK_EQ_INT(pulse(SN_WAKE_BUTTON, 10u, ev, 4u), 0u);     /* glitch */
    CHECK_EQ_INT(W.stats.button_glitches, 1u);
    CHECK_EQ_INT(pulse(SN_WAKE_BUTTON, 29u, ev, 4u), 0u);     /* just under */
    CHECK_EQ_INT(pulse(SN_WAKE_BUTTON, 500u, ev, 4u), 1u);    /* real press, held */
    CHECK_EQ_INT(ev[0].src, SN_WAKE_BUTTON);
    CHECK_EQ_INT(W.stats.emitted[SN_WAKE_BUTTON], 1u);
    /* chatter: bounces inside one press are glitches, one event total */
    sn_wake_input(&W, SN_WAKE_BUTTON, true, T); advance(3u, NULL, 0u);
    sn_wake_input(&W, SN_WAKE_BUTTON, false, T); advance(2u, NULL, 0u);
    CHECK_EQ_INT(pulse(SN_WAKE_BUTTON, 100u, ev, 4u), 1u);
}

/* Press seen only at its edges (no poll while held) still counts. */
static void wake_button_edges_only(void)
{
    sn_wake_event_t e;
    setup();
    sn_wake_input(&W, SN_WAKE_BUTTON, true, T);
    T += 300u;
    sn_wake_input(&W, SN_WAKE_BUTTON, false, T);
    CHECK(sn_wake_poll(&W, T, &e));
    CHECK_EQ_INT(e.src, SN_WAKE_BUTTON);
    CHECK_EQ_INT(e.t_ms, T - 300u + 30u);
    CHECK(!sn_wake_poll(&W, T, &e));
    /* and a short one seen only at edges is a glitch */
    sn_wake_input(&W, SN_WAKE_BUTTON, true, T);
    T += 5u;
    sn_wake_input(&W, SN_WAKE_BUTTON, false, T);
    CHECK(!sn_wake_poll(&W, T, &e));
    CHECK_EQ_INT(W.stats.button_glitches, 1u);
}

static void wake_button_never_rate_limited(void)
{
    sn_wake_event_t ev[4];
    uint32_t i;
    uint32_t n = 0u;
    setup();
    for (i = 0u; i < 20u; i++) {
        n += pulse(SN_WAKE_BUTTON, 50u, ev, 4u);
        advance(200u, NULL, 0u);
    }
    CHECK_EQ_INT(n, 20u);
    CHECK_EQ_INT(W.stats.rate_limited, 0u);
}

static void wake_pir_spike_rejected(void)
{
    sn_wake_event_t ev[4];
    setup();
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 50u, ev, 4u), 0u);
    CHECK_EQ_INT(W.stats.pir_spikes, 1u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 2000u, ev, 4u), 1u);
    CHECK_EQ_INT(ev[0].src, SN_WAKE_PIR);
    CHECK_EQ_INT(ev[0].t_ms, T - 2000u + 100u);              /* qualified at rise + min */
    /* sampled only at edges (no polls in between) still qualifies */
    advance(20000u, NULL, 0u);
    sn_wake_input(&W, SN_WAKE_PIR, true, T);
    T += 500u;
    sn_wake_input(&W, SN_WAKE_PIR, false, T);
    CHECK(sn_wake_poll(&W, T, &ev[0]));
    CHECK_EQ_INT(ev[0].t_ms, T - 400u);
}

static void wake_pir_holdoff_coalesces(void)
{
    sn_wake_event_t ev[8];
    uint32_t n = 0u;
    uint32_t i;
    setup();
    for (i = 0u; i < 4u; i++) {                 /* 4 pulses within 10 s */
        n += pulse(SN_WAKE_PIR, 1000u, ev, 8u);
        advance(1500u, NULL, 0u);
    }
    CHECK_EQ_INT(n, 1u);
    CHECK_EQ_INT(W.stats.coalesced, 3u);
    advance(10000u, NULL, 0u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 1000u, ev, 8u), 1u);
}

/* PIR storm: pulse every 12 s for 20 min. Default limit 5/min with 5 min
 * cooldown -> at most 5 per window, cooldowns kick in. */
static void wake_pir_storm_rate_limited(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    uint32_t n = 0u;
    uint32_t i;
    sn_wake_event_t ev[4];
    cfg.motion_holdoff_ms = 1000u;               /* let the limiter do the work */
    setup_cfg(&cfg, 0u);
    for (i = 0u; i < 400u; i++) {               /* every 3 s for 20 min */
        n += pulse(SN_WAKE_PIR, 500u, ev, 4u);
        advance(2500u, NULL, 0u);
    }
    printf("  storm: %u wakes, %u limited, %u cooldowns\n", (unsigned)n,
           (unsigned)W.stats.rate_limited, (unsigned)W.stats.cooldowns);
    /* each cycle: 5 wakes in 15 s, 6th starts a 300 s cooldown -> ~5 per 318 s */
    CHECK(n >= 15u);
    CHECK(n <= 25u);
    CHECK(W.stats.cooldowns >= 3u);
    CHECK_EQ_INT(n + W.stats.rate_limited + W.stats.coalesced, 400u);
}

static void wake_rate_window_exact(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    sn_wake_event_t ev[4];
    uint32_t i;
    cfg.motion_holdoff_ms = 0u;
    cfg.rate_max_wakes = 3u;
    cfg.rate_window_ms = 10000u;
    cfg.cooldown_ms = 20000u;
    setup_cfg(&cfg, 0u);
    for (i = 0u; i < 3u; i++) {
        CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 1u);
        advance(800u, NULL, 0u);
    }
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 0u);  /* 4th within window */
    CHECK(sn_wake_cooling(&W, T));
    advance(15000u, NULL, 0u);                          /* window passed, still cooling */
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 0u);
    advance(6000u, NULL, 0u);                           /* cooldown over */
    CHECK(!sn_wake_cooling(&W, T));
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 1u);
    /* button during cooldown still goes through */
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 1u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 1u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 0u);
    CHECK_EQ_INT(pulse(SN_WAKE_BUTTON, 100u, ev, 4u), 1u);
    CHECK_EQ_INT(ev[0].src, SN_WAKE_BUTTON);
}

static void wake_radar_absent_ignored(void)
{
    sn_wake_event_t ev[4];
    setup();
    CHECK_EQ_INT(pulse(SN_WAKE_RADAR, 5000u, ev, 4u), 0u);
    CHECK(!sn_wake_busy(&W));
}

static void wake_radar_confirm(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    sn_wake_event_t ev[4];
    cfg.radar_mode = SN_RADAR_CONFIRM;
    setup_cfg(&cfg, 0u);
    /* PIR alone: unconfirmed after the window */
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 500u, ev, 4u), 0u);
    CHECK_EQ_INT(advance(2000u, ev, 4u), 0u);
    CHECK_EQ_INT(W.stats.radar_unconfirmed, 1u);
    /* radar already present when PIR qualifies */
    advance(20000u, NULL, 0u);
    sn_wake_input(&W, SN_WAKE_RADAR, true, T);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 500u, ev, 4u), 1u);
    CHECK_EQ_INT(ev[0].src, SN_WAKE_PIR);
    sn_wake_input(&W, SN_WAKE_RADAR, false, T);
    /* radar arrives 800 ms after PIR qualifies -> confirmed */
    advance(20000u, NULL, 0u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 200u, ev, 4u), 0u);
    advance(700u, NULL, 0u);
    sn_wake_input(&W, SN_WAKE_RADAR, true, T);
    CHECK_EQ_INT(advance(5u, ev, 4u), 1u);
    sn_wake_input(&W, SN_WAKE_RADAR, false, T);
    /* radar alone never wakes in CONFIRM mode */
    advance(20000u, NULL, 0u);
    CHECK_EQ_INT(pulse(SN_WAKE_RADAR, 3000u, ev, 4u), 0u);
}

static void wake_radar_any(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    sn_wake_event_t ev[4];
    cfg.radar_mode = SN_RADAR_ANY;
    setup_cfg(&cfg, 0u);
    CHECK_EQ_INT(pulse(SN_WAKE_RADAR, 100u, ev, 4u), 0u);   /* below radar_min_ms */
    CHECK_EQ_INT(pulse(SN_WAKE_RADAR, 400u, ev, 4u), 1u);
    CHECK_EQ_INT(ev[0].src, SN_WAKE_RADAR);
    /* PIR right after is coalesced into the same motion */
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 400u, ev, 4u), 0u);
}

static void wake_timer_periodic(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    sn_wake_event_t ev[8];
    sn_wake_event_t e;
    cfg.timer_period_ms = 1000u;
    setup_cfg(&cfg, 0u);
    CHECK_EQ_INT(advance(3500u, ev, 8u), 3u);
    CHECK_EQ_INT(ev[0].src, SN_WAKE_TIMER);
    CHECK_EQ_INT(ev[0].t_ms, 1000u);
    CHECK_EQ_INT(ev[2].t_ms, 3000u);
    CHECK_EQ_INT(sn_wake_next_timer(&W), 4000u);
    /* missed periods (deep sleep overshoot) -> one event, no burst */
    T += 5600u;
    CHECK(sn_wake_poll(&W, T, &e));
    CHECK(!sn_wake_poll(&W, T, &e));
    CHECK_EQ_INT(sn_wake_next_timer(&W), 10000u);
}

static void wake_priority_order(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    sn_wake_event_t e;
    cfg.timer_period_ms = 100u;
    setup_cfg(&cfg, 0u);
    sn_wake_input(&W, SN_WAKE_PIR, true, 0u);
    sn_wake_input(&W, SN_WAKE_BUTTON, true, 0u);
    T = 150u;     /* all three due at once */
    CHECK(sn_wake_poll(&W, T, &e)); CHECK_EQ_INT(e.src, SN_WAKE_BUTTON);
    CHECK(sn_wake_poll(&W, T, &e)); CHECK_EQ_INT(e.src, SN_WAKE_PIR);
    CHECK(sn_wake_poll(&W, T, &e)); CHECK_EQ_INT(e.src, SN_WAKE_TIMER);
    CHECK(!sn_wake_poll(&W, T, &e));
}

static void wake_clock_wraparound(void)
{
    sn_wake_cfg_t cfg = SN_WAKE_CFG_DEFAULT;
    sn_wake_event_t ev[4];
    cfg.timer_period_ms = 1000u;
    cfg.motion_holdoff_ms = 5000u;
    setup_cfg(&cfg, 0xFFFFFC00u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 300u, ev, 4u), 1u);     /* timer at +1000 too? no: 300 ms */
    CHECK_EQ_INT(advance(1000u, ev, 4u), 1u);               /* timer crosses the wrap */
    CHECK_EQ_INT(ev[0].src, SN_WAKE_TIMER);
    CHECK(T < 0x1000u);
    CHECK_EQ_INT(pulse(SN_WAKE_PIR, 300u, ev, 4u), 0u);     /* holdoff across wrap */
    advance(5000u, NULL, 0u);
    CHECK(pulse(SN_WAKE_PIR, 300u, ev, 4u) >= 1u);
}

static void wake_busy_flag(void)
{
    sn_wake_event_t e;
    setup();
    CHECK(!sn_wake_busy(&W));
    sn_wake_input(&W, SN_WAKE_BUTTON, true, T);
    CHECK(sn_wake_busy(&W));
    T += 40u;
    CHECK(sn_wake_poll(&W, T, &e));
    CHECK(!sn_wake_busy(&W));                     /* fired, held: nothing pending */
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(wake_button_debounce);
    RUN_TEST(wake_button_edges_only);
    RUN_TEST(wake_button_never_rate_limited);
    RUN_TEST(wake_pir_spike_rejected);
    RUN_TEST(wake_pir_holdoff_coalesces);
    RUN_TEST(wake_pir_storm_rate_limited);
    RUN_TEST(wake_rate_window_exact);
    RUN_TEST(wake_radar_absent_ignored);
    RUN_TEST(wake_radar_confirm);
    RUN_TEST(wake_radar_any);
    RUN_TEST(wake_timer_periodic);
    RUN_TEST(wake_priority_order);
    RUN_TEST(wake_clock_wraparound);
    RUN_TEST(wake_busy_flag);
    TEST_END();
}
