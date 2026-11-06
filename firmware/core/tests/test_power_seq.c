#include "sn_test.h"
#include "sim_hal.h"
#include "sn_power_seq.h"

typedef struct {
    uint32_t ms[SN_PWR_STATE_COUNT];
    uint32_t calls;
} hook_log_t;

static void hook(void *ctx, sn_pwr_state_t st, uint32_t dt)
{
    hook_log_t *h = (hook_log_t *)ctx;
    h->ms[st] += dt;
    h->calls++;
}

static sn_pwr_t   P;
static hook_log_t H;

static void setup(uint32_t start)
{
    sim_hal_reset(start, 1762450000000ull);
    memset(&H, 0, sizeof(H));
    sn_pwr_init(&P, &SN_PWR_CFG_DEFAULT, hook, &H, sn_hal_time_ms());
}

/* Tick every 1 ms until state == want or limit ms elapse; returns ms used. */
static uint32_t run_until(sn_pwr_state_t want, uint32_t limit)
{
    uint32_t t;
    for (t = 0u; t < limit; t++) {
        if (sn_pwr_state(&P) == want) {
            return t;
        }
        sim_advance(1u);
        sn_pwr_tick(&P, sn_hal_time_ms());
    }
    return limit;
}

static void run_for(uint32_t ms)
{
    uint32_t t;
    for (t = 0u; t < ms; t++) {
        sim_advance(1u);
        sn_pwr_tick(&P, sn_hal_time_ms());
    }
}

static void pwr_default_table_is_sane(void)
{
    uint8_t i;
    uint32_t seen = 0u;
    CHECK_EQ_INT(SN_N6_RAIL_SEQ_LEN, SN_RAIL_COUNT);
    for (i = 0u; i < SN_N6_RAIL_SEQ_LEN; i++) {
        CHECK(SN_N6_RAIL_SEQ[i].pg_timeout_ms > 0u);
        CHECK((seen & (1u << SN_N6_RAIL_SEQ[i].rail)) == 0u);   /* each rail once */
        seen |= 1u << SN_N6_RAIL_SEQ[i].rail;
    }
    CHECK(SN_PWR_CFG_DEFAULT.batt_brownout_mv < SN_PWR_CFG_DEFAULT.batt_min_on_mv);
    CHECK_STR(sn_pwr_state_name(SN_PWR_READY), "READY");
    CHECK_STR(sn_pwr_err_name(SN_PWR_ERR_BROWNOUT), "BROWNOUT");
}

static void pwr_happy_path_order_and_timing(void)
{
    const sim_rail_evt_t *log;
    uint32_t n;
    uint32_t t0;
    uint32_t i;
    setup(1000u);
    t0 = sn_hal_time_ms();
    CHECK(sim_cortex_in_reset());
    CHECK_EQ_INT(sn_pwr_request_on(&P, t0), SN_PWR_REQ_OK);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_RAILS_UP);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK(sn_pwr_is_on(&P));
    CHECK(!sim_cortex_in_reset());
    CHECK_EQ_INT(P.stats.power_ups, 1u);
    n = sim_rail_log(&log);
    CHECK_EQ_INT(n, SN_N6_RAIL_SEQ_LEN);
    for (i = 0u; i < n; i++) {
        CHECK_EQ_INT(log[i].rail, SN_N6_RAIL_SEQ[i].rail);
        CHECK(log[i].on);
        if (i > 0u) {
            /* next rail only after previous settle time */
            CHECK((log[i].t_ms - log[i - 1u].t_ms) >= SN_N6_RAIL_SEQ[i - 1u].settle_ms);
        }
    }
    /* reset is released only after every rail is up */
    CHECK(!sim_cortex_in_reset());
    CHECK_EQ_INT(sim_cortex_boot_attempts(), 1u);

    /* RUNNING / idle handshake */
    CHECK(sn_pwr_mark_running(&P));
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_RUNNING);
    CHECK(!sn_pwr_mark_running(&P));
    CHECK(sn_pwr_mark_idle(&P));
    CHECK(!sn_pwr_mark_idle(&P));

    /* orderly shutdown: reset first, reverse order, respecting off delays */
    sim_rail_log_clear();
    sn_pwr_request_off(&P, sn_hal_time_ms());
    CHECK(sim_cortex_in_reset());
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_SHUTDOWN);
    CHECK(run_until(SN_PWR_OFF, 1000u) < 1000u);
    n = sim_rail_log(&log);
    CHECK_EQ_INT(n, SN_N6_RAIL_SEQ_LEN);
    for (i = 0u; i < n; i++) {
        uint32_t step = SN_N6_RAIL_SEQ_LEN - 1u - i;
        CHECK_EQ_INT(log[i].rail, SN_N6_RAIL_SEQ[step].rail);
        CHECK(!log[i].on);
        if (i > 0u) {
            CHECK((log[i].t_ms - log[i - 1u].t_ms) >= SN_N6_RAIL_SEQ[step + 1u].off_delay_ms);
        }
    }
    CHECK(!sim_any_rail_on());
    /* energy hook saw every millisecond exactly once */
    {
        uint32_t total = 0u;
        for (i = 0u; i < SN_PWR_STATE_COUNT; i++) {
            total += H.ms[i];
        }
        CHECK_EQ_INT(total, sn_hal_time_ms() - t0);
        CHECK(H.ms[SN_PWR_RAILS_UP] > 0u);
        CHECK(H.ms[SN_PWR_BOOTING] >= 120u);   /* sim boot time */
        CHECK(H.ms[SN_PWR_SHUTDOWN] > 0u);
    }
}

static void pwr_request_on_idempotent(void)
{
    setup(0u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_OK);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_READY);
    CHECK_EQ_INT(sim_cortex_boot_attempts(), 1u);
    sn_pwr_request_off(&P, sn_hal_time_ms());
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_REFUSED_BUSY);
}

static void pwr_pg_timeout_retries_then_fault(void)
{
    uint32_t i;
    setup(0u);
    sim_rail_config(SN_RAIL_VDDCORE, 1u, true);     /* core buck never good */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_FAULT, 5000u) < 5000u);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_PG_TIMEOUT);
    CHECK_EQ_INT(P.last_error_rail, SN_RAIL_VDDCORE);
    CHECK_EQ_INT(P.stats.retries, SN_PWR_CFG_DEFAULT.max_retries);
    CHECK_EQ_INT(P.stats.faults, 1u);
    CHECK(!sim_any_rail_on());                     /* failed attempts tear down */
    CHECK(sim_cortex_in_reset());
    CHECK_EQ_INT(sim_cortex_boot_attempts(), 0u);  /* never released reset */
    /* later rails were never enabled */
    {
        const sim_rail_evt_t *log;
        uint32_t n = sim_rail_log(&log);
        for (i = 0u; i < n; i++) {
            CHECK(!(log[i].on && (log[i].rail == SN_RAIL_VDDA_1V8)));
        }
    }
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_REFUSED_FAULT);
    /* fault auto-clears after cooldown; fixed hardware powers up */
    run_for(SN_PWR_CFG_DEFAULT.fault_cooldown_ms);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);
    sim_rail_config(SN_RAIL_VDDCORE, 1u, false);
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_NONE);
}

static void pwr_slow_pg_within_timeout_ok(void)
{
    setup(0u);
    sim_rail_config(SN_RAIL_VDD_1V8, 11u, false);   /* settle 2 + 9 ms of 10 allowed */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK_EQ_INT(P.stats.retries, 0u);
    setup(0u);
    sim_rail_config(SN_RAIL_VDD_1V8, 13u, false);   /* just past settle + timeout */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    (void)run_until(SN_PWR_FAULT, 3000u);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_PG_TIMEOUT);
    CHECK_EQ_INT(P.last_error_rail, SN_RAIL_VDD_1V8);
}

static void pwr_boot_timeout_then_retry_succeeds(void)
{
    setup(0u);
    sim_cortex_config(120u, 1u);                    /* first boot hangs */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 3000u) < 3000u);
    CHECK_EQ_INT(P.stats.retries, 1u);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_BOOT_TIMEOUT);   /* last failure kept for report */
    CHECK_EQ_INT(sim_cortex_boot_attempts(), 2u);
    /* elapsed >= boot timeout + backoff */
    CHECK(sn_hal_time_ms() >= (uint32_t)(SN_PWR_CFG_DEFAULT.boot_timeout_ms +
                                         SN_PWR_CFG_DEFAULT.retry_backoff_ms));
}

static void pwr_low_battery_refusal_with_hysteresis(void)
{
    setup(0u);
    sim_battery_set(3399u, 5u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_REFUSED_LOW_BATT);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);
    CHECK(!sim_any_rail_on());
    CHECK_EQ_INT(P.stats.low_batt_refusals, 1u);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_LOW_BATT_REFUSED);
    sim_battery_set(3420u, 6u);                     /* above min, below min+hyst */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_REFUSED_LOW_BATT);
    sim_battery_set(3450u, 7u);                     /* clears latch */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
}

static void pwr_brownout_under_load(void)
{
    setup(0u);
    sim_battery_set(3440u, 10u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK(sn_pwr_mark_running(&P));
    /* a short dip below brownout is tolerated (debounce) */
    sim_battery_set_load_sag(300u);                 /* 3150 under load */
    run_for(SN_PWR_CFG_DEFAULT.brownout_debounce_ms / 2u);
    sim_battery_set_load_sag(0u);
    run_for(50u);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_RUNNING);
    /* sustained sag -> emergency off */
    sim_battery_set_load_sag(300u);
    run_for(SN_PWR_CFG_DEFAULT.brownout_debounce_ms + 2u);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_BROWNOUT);
    CHECK_EQ_INT(P.stats.brownouts, 1u);
    CHECK(!sim_any_rail_on());
    CHECK(sim_cortex_in_reset());
    /* latched: battery reads 3440 with no load, above min_on but < min_on + hyst */
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_REFUSED_LOW_BATT);
    sim_battery_set(3700u, 40u);                     /* charged */
    sim_battery_set_load_sag(0u);
    /* still inside the brown-out lockout */
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_REFUSED_LOW_BATT);
    sim_advance(SN_PWR_CFG_DEFAULT.brownout_lockout_ms);
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_OK);
}

static void pwr_brownout_during_boot(void)
{
    setup(0u);
    sim_battery_set(3500u, 10u);
    sim_battery_set_load_sag(400u);                 /* collapses as soon as rails come on */
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    (void)run_until(SN_PWR_OFF, 500u);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_BROWNOUT);
    CHECK_EQ_INT(P.stats.power_ups, 0u);
    CHECK(!sim_any_rail_on());
}

static void pwr_rail_lost_while_running(void)
{
    setup(0u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK(sn_pwr_mark_running(&P));
    sim_rail_drop(SN_RAIL_VDD_1V8);
    run_for(1u);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_RAIL_LOST);
    CHECK_EQ_INT(P.last_error_rail, SN_RAIL_VDD_1V8);
    CHECK_EQ_INT(P.stats.rail_lost, 1u);
    CHECK(!sim_any_rail_on());
}

static void pwr_rail_lost_during_sequencing_retries(void)
{
    setup(0u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    /* wait until step 2 (VDDCORE) is being enabled, then drop 3V3 */
    while (P.step < 2u) {
        sim_advance(1u);
        sn_pwr_tick(&P, sn_hal_time_ms());
    }
    sim_rail_drop(SN_RAIL_VDD_3V3);
    run_for(1u);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_RETRY_WAIT);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_RAIL_LOST);
    CHECK(!sim_any_rail_on());
    CHECK(run_until(SN_PWR_READY, 2000u) < 2000u);  /* drop cleared by cycling */
}

static void pwr_off_during_rails_up(void)
{
    const sim_rail_evt_t *log;
    uint32_t n;
    setup(0u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    while (P.step < 1u) {
        sim_advance(1u);
        sn_pwr_tick(&P, sn_hal_time_ms());
    }
    sim_rail_log_clear();
    sn_pwr_request_off(&P, sn_hal_time_ms());
    CHECK(run_until(SN_PWR_OFF, 100u) < 100u);
    n = sim_rail_log(&log);
    CHECK_EQ_INT(n, 2u);                             /* only rails 1 then 0 */
    CHECK_EQ_INT(log[0].rail, SN_N6_RAIL_SEQ[1].rail);
    CHECK_EQ_INT(log[1].rail, SN_N6_RAIL_SEQ[0].rail);
    CHECK(!sim_any_rail_on());
}

static void pwr_off_during_retry_wait(void)
{
    setup(0u);
    sim_cortex_config(120u, 5u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_RETRY_WAIT, 2000u) < 2000u);
    sn_pwr_request_off(&P, sn_hal_time_ms());
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);
    run_for(1000u);
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_OFF);      /* no zombie retry */
    CHECK(!sim_any_rail_on());
}

static void pwr_clock_wraparound(void)
{
    setup(0xFFFFFFF0u);                             /* wraps during power-up */
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_READY, 1000u) < 1000u);
    CHECK(sn_hal_time_ms() < 0x1000u);              /* clock did wrap */
    sn_pwr_request_off(&P, sn_hal_time_ms());
    CHECK(run_until(SN_PWR_OFF, 100u) < 100u);
    /* boot timeout across the wrap */
    setup(0xFFFFFF00u);
    sim_cortex_config(120u, 10u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, sn_hal_time_ms()), SN_PWR_REQ_OK);
    CHECK(run_until(SN_PWR_FAULT, 5000u) < 5000u);
    CHECK_EQ_INT(P.last_error, SN_PWR_ERR_BOOT_TIMEOUT);
}

/* Coarse ticks (as when the C6 wakes every 10 ms) still work. */
static void pwr_coarse_ticks(void)
{
    uint32_t i;
    setup(0u);
    CHECK_EQ_INT(sn_pwr_request_on(&P, 0u), SN_PWR_REQ_OK);
    for (i = 0u; (i < 100u) && (sn_pwr_state(&P) != SN_PWR_READY); i++) {
        sim_advance(10u);
        sn_pwr_tick(&P, sn_hal_time_ms());
    }
    CHECK_EQ_INT(sn_pwr_state(&P), SN_PWR_READY);
    CHECK_EQ_INT(P.stats.retries, 0u);
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(pwr_default_table_is_sane);
    RUN_TEST(pwr_happy_path_order_and_timing);
    RUN_TEST(pwr_request_on_idempotent);
    RUN_TEST(pwr_pg_timeout_retries_then_fault);
    RUN_TEST(pwr_slow_pg_within_timeout_ok);
    RUN_TEST(pwr_boot_timeout_then_retry_succeeds);
    RUN_TEST(pwr_low_battery_refusal_with_hysteresis);
    RUN_TEST(pwr_brownout_under_load);
    RUN_TEST(pwr_brownout_during_boot);
    RUN_TEST(pwr_rail_lost_while_running);
    RUN_TEST(pwr_rail_lost_during_sequencing_retries);
    RUN_TEST(pwr_off_during_rails_up);
    RUN_TEST(pwr_off_during_retry_wait);
    RUN_TEST(pwr_clock_wraparound);
    RUN_TEST(pwr_coarse_ticks);
    TEST_END();
}
