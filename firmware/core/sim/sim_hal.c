#include "sim_hal.h"
#include <string.h>

typedef struct {
    bool     en;
    uint32_t t_en;
    uint32_t pg_delay;
    bool     never_pg;
    bool     dropped;
} sim_rail_t;

static struct {
    uint32_t       now;
    uint32_t       start;
    uint64_t       unix_base;
    bool           gpio[SN_GPIO_COUNT];
    uint32_t       wake_flags;
    uint16_t       batt_mv;
    uint8_t        soc;
    uint16_t       sag_mv;
    sim_rail_t     rail[SN_RAIL_COUNT];
    sim_rail_evt_t log[SIM_RAIL_LOG_MAX];
    uint32_t       log_n;
    bool           in_reset;
    uint32_t       t_release;
    uint32_t       boot_ms;
    uint32_t       fail_boots;
    uint32_t       boot_attempts;
    sim_advance_hook_t hook;
    void          *hook_ctx;
    uint32_t       hook_chunk;
    bool           in_hook;
} S;

void sim_hal_reset(uint32_t start_ms, uint64_t unix_base_ms)
{
    uint32_t i;
    (void)memset(&S, 0, sizeof(S));
    S.now = start_ms;
    S.start = start_ms;
    S.unix_base = unix_base_ms;
    S.batt_mv = 3900u;
    S.soc = 80u;
    S.in_reset = true;
    S.boot_ms = 120u;
    for (i = 0u; i < (uint32_t)SN_RAIL_COUNT; i++) {
        S.rail[i].pg_delay = 1u;
    }
}

void sim_advance(uint32_t ms)
{
    if ((S.hook == NULL) || S.in_hook || (S.hook_chunk == 0u)) {
        S.now += ms;
        return;
    }
    while (ms > 0u) {
        uint32_t step = (ms < S.hook_chunk) ? ms : S.hook_chunk;
        S.now += step;
        ms -= step;
        S.in_hook = true;
        S.hook(S.hook_ctx);
        S.in_hook = false;
    }
}

void sim_set_advance_hook(sim_advance_hook_t hook, void *ctx, uint32_t chunk_ms)
{
    S.hook = hook;
    S.hook_ctx = ctx;
    S.hook_chunk = chunk_ms;
}
void sim_set_time(uint32_t now_ms) { S.now = now_ms; }

uint32_t sn_hal_time_ms(void) { return S.now; }
uint64_t sn_hal_unix_ms(void) { return S.unix_base + (uint64_t)(uint32_t)(S.now - S.start); }
void sn_hal_delay_ms(uint32_t ms) { S.now += ms; }

void sim_gpio_set(sn_gpio_t pin, bool level)
{
    static const uint32_t flag[SN_GPIO_COUNT] = {
        SN_WAKE_FLAG_PIR, SN_WAKE_FLAG_RADAR, SN_WAKE_FLAG_BUTTON, 0u, 0u
    };
    if ((uint32_t)pin >= (uint32_t)SN_GPIO_COUNT) {
        return;
    }
    if (level && !S.gpio[pin]) {
        S.wake_flags |= flag[pin];
    }
    S.gpio[pin] = level;
}

bool sim_gpio_level(sn_gpio_t pin)
{
    return ((uint32_t)pin < (uint32_t)SN_GPIO_COUNT) ? S.gpio[pin] : false;
}

void sn_hal_gpio_write(sn_gpio_t pin, bool level)
{
    if ((uint32_t)pin < (uint32_t)SN_GPIO_COUNT) {
        S.gpio[pin] = level;
    }
}

bool sn_hal_gpio_read(sn_gpio_t pin) { return sim_gpio_level(pin); }

uint32_t sn_hal_wake_flags_take(void)
{
    uint32_t f = S.wake_flags;
    S.wake_flags = 0u;
    return f;
}

void sim_battery_set(uint16_t mv, uint8_t soc_pct) { S.batt_mv = mv; S.soc = soc_pct; }
void sim_battery_set_load_sag(uint16_t sag_mv) { S.sag_mv = sag_mv; }

bool sim_any_rail_on(void)
{
    uint32_t i;
    for (i = 0u; i < (uint32_t)SN_RAIL_COUNT; i++) {
        if (S.rail[i].en) {
            return true;
        }
    }
    return false;
}

uint16_t sn_hal_battery_mv(void)
{
    if (sim_any_rail_on() && (S.batt_mv > S.sag_mv)) {
        return (uint16_t)(S.batt_mv - S.sag_mv);
    }
    return S.batt_mv;
}

uint8_t sn_hal_battery_soc_pct(void) { return S.soc; }

void sim_rail_config(sn_rail_t rail, uint32_t pg_delay_ms, bool never_pg)
{
    if ((uint32_t)rail < (uint32_t)SN_RAIL_COUNT) {
        S.rail[rail].pg_delay = pg_delay_ms;
        S.rail[rail].never_pg = never_pg;
    }
}

void sim_rail_drop(sn_rail_t rail)
{
    if ((uint32_t)rail < (uint32_t)SN_RAIL_COUNT) {
        S.rail[rail].dropped = true;
    }
}

bool sim_rail_is_on(sn_rail_t rail)
{
    return ((uint32_t)rail < (uint32_t)SN_RAIL_COUNT) && S.rail[rail].en;
}

void sn_hal_rail_enable(sn_rail_t rail, bool on)
{
    sim_rail_t *r;
    if ((uint32_t)rail >= (uint32_t)SN_RAIL_COUNT) {
        return;
    }
    r = &S.rail[rail];
    if (on == r->en) {
        return;
    }
    r->en = on;
    if (on) {
        r->t_en = S.now;
    } else {
        r->dropped = false;
    }
    if (S.log_n < SIM_RAIL_LOG_MAX) {
        S.log[S.log_n].t_ms = S.now;
        S.log[S.log_n].rail = rail;
        S.log[S.log_n].on = on;
        S.log_n++;
    }
}

bool sn_hal_rail_power_good(sn_rail_t rail)
{
    const sim_rail_t *r;
    if ((uint32_t)rail >= (uint32_t)SN_RAIL_COUNT) {
        return false;
    }
    r = &S.rail[rail];
    return r->en && !r->never_pg && !r->dropped && ((S.now - r->t_en) >= r->pg_delay);
}

uint32_t sim_rail_log(const sim_rail_evt_t **out)
{
    if (out != NULL) {
        *out = S.log;
    }
    return S.log_n;
}

void sim_rail_log_clear(void) { S.log_n = 0u; }

void sim_cortex_config(uint32_t boot_ms, uint32_t fail_boots)
{
    S.boot_ms = boot_ms;
    S.fail_boots = fail_boots;
}

void sn_hal_cortex_reset(bool asserted)
{
    if (!asserted && S.in_reset) {
        S.t_release = S.now;
        S.boot_attempts++;
    }
    S.in_reset = asserted;
}

bool sim_cortex_in_reset(void) { return S.in_reset; }
uint32_t sim_cortex_boot_attempts(void) { return S.boot_attempts; }

bool sn_hal_cortex_ready(void)
{
    uint32_t i;
    if (S.in_reset || (S.boot_attempts <= S.fail_boots)) {
        return false;
    }
    for (i = 0u; i < (uint32_t)SN_RAIL_COUNT; i++) {
        if (!sn_hal_rail_power_good((sn_rail_t)i)) {
            return false;
        }
    }
    return (S.now - S.t_release) >= S.boot_ms;
}
