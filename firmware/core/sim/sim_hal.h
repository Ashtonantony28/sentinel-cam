/*
 * sim_hal.h - host simulation of sentinel_hal.h (single global instance).
 * Time only moves when the test calls sim_advance()/sim_set_time() or when
 * the scripted inference provider charges latency.
 */
#ifndef SIM_HAL_H
#define SIM_HAL_H
#include "sentinel_hal.h"

#define SIM_RAIL_LOG_MAX 256u

typedef struct {
    uint32_t  t_ms;
    sn_rail_t rail;
    bool      on;
} sim_rail_evt_t;

void     sim_hal_reset(uint32_t start_ms, uint64_t unix_base_ms);
/* Advances time. With an advance hook set, time moves in chunk_ms steps and
 * the hook runs after each (models the C6 running while the N6 works). */
void     sim_advance(uint32_t ms);
typedef void (*sim_advance_hook_t)(void *ctx);
void     sim_set_advance_hook(sim_advance_hook_t hook, void *ctx, uint32_t chunk_ms);
void     sim_set_time(uint32_t now_ms);   /* absolute (monotonic, may wrap) */

/* Inputs (PIR, RADAR, BUTTON): latches a wake flag on rising edges. */
void     sim_gpio_set(sn_gpio_t pin, bool level);
bool     sim_gpio_level(sn_gpio_t pin);   /* also reflects outputs */

/* Battery: base voltage, SoC, and sag applied while any cortex rail is on. */
void     sim_battery_set(uint16_t mv, uint8_t soc_pct);
void     sim_battery_set_load_sag(uint16_t sag_mv);

/* Rails: PG asserts pg_delay_ms after enable unless never_pg; drop forces
 * PG low while enabled (until the rail is cycled). */
void     sim_rail_config(sn_rail_t rail, uint32_t pg_delay_ms, bool never_pg);
void     sim_rail_drop(sn_rail_t rail);
bool     sim_rail_is_on(sn_rail_t rail);
bool     sim_any_rail_on(void);
uint32_t sim_rail_log(const sim_rail_evt_t **out);
void     sim_rail_log_clear(void);

/* Cortex: ready boot_ms after reset release with all rails good. Fail the
 * first `fail_boots` boot attempts (never ready). */
void     sim_cortex_config(uint32_t boot_ms, uint32_t fail_boots);
bool     sim_cortex_in_reset(void);
uint32_t sim_cortex_boot_attempts(void);

#endif
