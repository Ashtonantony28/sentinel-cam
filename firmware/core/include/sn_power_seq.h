/*
 * sn_power_seq.h - brainstem (ESP32-C6) power sequencer for the STM32N6
 * "cortex" domain. Tick driven, non-blocking, no allocation.
 *
 *   OFF --request_on--> RAILS_UP (step i: enable, settle, wait PG)
 *       --> BOOTING (hold reset, release, wait cortex_ready)
 *       --> READY <--mark_running/mark_idle--> RUNNING
 *       --request_off--> SHUTDOWN (assert reset, rails off in reverse) --> OFF
 *   PG timeout / boot timeout -> all rails off -> RETRY_WAIT -> RAILS_UP
 *   (max_retries), then FAULT (auto-clears to OFF after fault_cooldown_ms).
 *   Battery < batt_min_on_mv at request_on -> refused (low-battery latch with
 *   hysteresis). Battery < batt_brownout_mv while powered (debounced) or a
 *   rail losing PG while READY/RUNNING -> emergency off, error reported.
 *   After a brown-out, power-up is also refused for brownout_lockout_ms.
 */
#ifndef SN_POWER_SEQ_H
#define SN_POWER_SEQ_H
#include "sentinel_hal.h"

typedef enum {
    SN_PWR_OFF = 0,
    SN_PWR_RAILS_UP,
    SN_PWR_BOOTING,
    SN_PWR_READY,
    SN_PWR_RUNNING,
    SN_PWR_SHUTDOWN,
    SN_PWR_RETRY_WAIT,
    SN_PWR_FAULT,
    SN_PWR_STATE_COUNT
} sn_pwr_state_t;

typedef enum {
    SN_PWR_ERR_NONE = 0,
    SN_PWR_ERR_PG_TIMEOUT,      /* rail never reported power-good         */
    SN_PWR_ERR_BOOT_TIMEOUT,    /* N6 never signalled ready               */
    SN_PWR_ERR_RAIL_LOST,       /* PG dropped while up                    */
    SN_PWR_ERR_BROWNOUT,        /* battery sagged below brownout while on */
    SN_PWR_ERR_LOW_BATT_REFUSED /* request_on refused                     */
} sn_pwr_err_t;

typedef enum {
    SN_PWR_REQ_OK = 0,          /* accepted (or already on / powering)  */
    SN_PWR_REQ_REFUSED_LOW_BATT,
    SN_PWR_REQ_REFUSED_FAULT,
    SN_PWR_REQ_REFUSED_BUSY     /* shutting down; ask again when OFF    */
} sn_pwr_req_t;

typedef struct {
    sn_rail_t rail;
    uint16_t  settle_ms;      /* wait after enable before checking PG   */
    uint16_t  pg_timeout_ms;  /* additional time allowed for PG         */
    uint16_t  off_delay_ms;   /* wait after disabling (shutdown order)  */
} sn_rail_step_t;

typedef struct {
    const sn_rail_step_t *steps;
    uint8_t   n_steps;
    uint16_t  reset_release_delay_ms;  /* all rails PG -> release NRST       */
    uint16_t  boot_timeout_ms;         /* NRST released -> cortex_ready       */
    uint8_t   max_retries;             /* extra attempts after first failure */
    uint16_t  retry_backoff_ms;
    uint16_t  batt_min_on_mv;          /* refuse power-up below this          */
    uint16_t  batt_hyst_mv;            /* latch clears at min_on + hyst      */
    uint16_t  batt_brownout_mv;        /* emergency off below this while on   */
    uint16_t  brownout_debounce_ms;
    uint32_t  brownout_lockout_ms;     /* after a brown-out, refuse power-up this
                                          long even if the unloaded voltage
                                          looks fine (sag under load repeats) */
    uint32_t  fault_cooldown_ms;
} sn_pwr_cfg_t;

/* Default N6 rail table + config (TODO-verify-against-STM32N6-datasheet). */
extern const sn_rail_step_t SN_N6_RAIL_SEQ[];
extern const uint8_t        SN_N6_RAIL_SEQ_LEN;
extern const sn_pwr_cfg_t   SN_PWR_CFG_DEFAULT;

/* Called on every tick with the state the domain was in for dt_ms. */
typedef void (*sn_pwr_energy_hook_t)(void *ctx, sn_pwr_state_t state, uint32_t dt_ms);

typedef struct {
    uint32_t power_ups;         /* successful READY transitions */
    uint32_t retries;
    uint32_t faults;
    uint32_t low_batt_refusals;
    uint32_t brownouts;
    uint32_t rail_lost;
} sn_pwr_stats_t;

typedef struct {
    const sn_pwr_cfg_t  *cfg;
    sn_pwr_energy_hook_t energy_hook;
    void                *energy_ctx;
    sn_pwr_state_t       state;
    uint32_t             t_state;      /* when current state/step started */
    uint32_t             t_last_tick;
    bool                 ticked;
    uint8_t              step;         /* RAILS_UP / SHUTDOWN step index  */
    bool                 reset_released;
    uint32_t             t_reset_release;
    uint8_t              attempt;      /* failures in this power-up       */
    bool                 low_batt_latched;
    bool                 brownout_pending;
    uint32_t             t_brownout;
    bool                 brownout_lock;
    uint32_t             t_brownout_lock;
    bool                 off_requested;
    sn_pwr_err_t         last_error;
    int8_t               last_error_rail;
    sn_pwr_stats_t       stats;
} sn_pwr_t;

void           sn_pwr_init(sn_pwr_t *p, const sn_pwr_cfg_t *cfg,
                           sn_pwr_energy_hook_t hook, void *hook_ctx, uint32_t now);
sn_pwr_req_t   sn_pwr_request_on(sn_pwr_t *p, uint32_t now);
void           sn_pwr_request_off(sn_pwr_t *p, uint32_t now);
void           sn_pwr_tick(sn_pwr_t *p, uint32_t now);
bool           sn_pwr_mark_running(sn_pwr_t *p);   /* READY -> RUNNING */
bool           sn_pwr_mark_idle(sn_pwr_t *p);      /* RUNNING -> READY */
sn_pwr_state_t sn_pwr_state(const sn_pwr_t *p);
bool           sn_pwr_is_on(const sn_pwr_t *p);    /* READY or RUNNING */
bool           sn_pwr_is_idle_off(const sn_pwr_t *p);
const char    *sn_pwr_state_name(sn_pwr_state_t s);
const char    *sn_pwr_err_name(sn_pwr_err_t e);

#endif
