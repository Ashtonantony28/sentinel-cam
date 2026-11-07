/*
 * sn_brainstem.h - ESP32-C6 "brainstem" top level: wake fusion -> power
 * sequencer -> cortex job dispatch -> power down, with energy accounting.
 *
 *  IDLE --wake--> POWERING_UP --READY--> JOB --job_done--> POWERING_DOWN --> IDLE
 *  - Low battery at wake: power-up refused, report(LOW_BATT_REFUSED).
 *  - A BUTTON wake while busy is queued (one slot) and dispatched back to
 *    back without a power cycle if the cortex is still up; other wakes while
 *    busy are dropped and counted.
 *  - Cortex lost mid-job (brown-out / rail lost) or job timeout -> report.
 *
 * Energy: standby_uw always; cortex_boot_mw while rails sequence / boot /
 * shut down; cortex_active_mw while READY/RUNNING; plus
 * (cortex_clip_mw - cortex_active_mw) x clip_ms reported by the job (added to
 * cortex_clip_extra when positive; when the clip draws less than the
 * pipeline, the difference is taken back off cortex_active).
 */
#ifndef SN_BRAINSTEM_H
#define SN_BRAINSTEM_H
#include "sn_wake.h"
#include "sn_power_seq.h"
#include "sn_pipeline.h"
#include "sn_energy.h"

typedef enum {
    SN_BS_IDLE = 0,
    SN_BS_POWERING_UP,
    SN_BS_JOB,
    SN_BS_POWERING_DOWN
} sn_bs_state_t;

typedef enum {
    SN_BS_REPORT_LOW_BATT_REFUSED = 0,
    SN_BS_REPORT_POWER_FAULT,       /* PG/boot failure after retries */
    SN_BS_REPORT_CORTEX_LOST,       /* brown-out or rail lost mid-job */
    SN_BS_REPORT_JOB_TIMEOUT,
    SN_BS_REPORT_WAKE_DROPPED,      /* wake while busy, not queued    */
    SN_BS_REPORT_COUNT
} sn_bs_report_t;

typedef struct {
    void *ctx;
    /* C6 -> N6: run the pipeline for this job (UART/SPI control message). */
    void (*dispatch_job)(void *ctx, const sn_job_t *job);
    /* Optional: status events for the status topic / local log. */
    void (*report)(void *ctx, sn_bs_report_t kind, sn_wake_src_t wake, uint32_t detail);
} sn_bs_ops_t;

typedef struct {
    sn_wake_cfg_t            wake;
    const sn_pwr_cfg_t      *pwr;
    const sn_power_model_t  *pm;
    uint32_t                 job_timeout_ms;
} sn_bs_cfg_t;

void sn_bs_cfg_default(sn_bs_cfg_t *cfg);

typedef struct {
    sn_energy_acc_t standby;
    sn_energy_acc_t cortex_boot;     /* rails/boot/shutdown */
    sn_energy_acc_t cortex_active;   /* READY/RUNNING       */
    sn_energy_acc_t cortex_clip_extra;
} sn_bs_energy_t;

typedef struct {
    uint32_t wakes;                  /* wakes that reached the brainstem */
    uint32_t jobs_dispatched;
    uint32_t jobs_done;
    uint32_t reports[SN_BS_REPORT_COUNT];
} sn_bs_stats_t;

typedef struct {
    sn_bs_cfg_t      cfg;
    sn_bs_ops_t      ops;
    sn_wake_t        wake;
    sn_pwr_t         pwr;
    sn_bs_state_t    state;
    sn_wake_event_t  cur;            /* wake being served           */
    bool             have_pending;
    sn_wake_event_t  pending;        /* queued BUTTON               */
    uint32_t         t_job;
    uint32_t         t_last;
    sn_energy_acc_t  boot_this_cycle;
    bool             boot_reported;  /* boot energy already charged to a job */
    sn_bs_energy_t   energy;
    sn_bs_stats_t    stats;
} sn_brainstem_t;

void          sn_bs_init(sn_brainstem_t *bs, const sn_bs_cfg_t *cfg, const sn_bs_ops_t *ops);
void          sn_bs_tick(sn_brainstem_t *bs);
/* N6 -> C6: job finished (pipeline result). */
void          sn_bs_job_done(sn_brainstem_t *bs, const sn_job_result_t *res);
/* True when the brainstem must keep ticking at a fast rate (inputs being
 * qualified, cortex domain not off, job in flight). When false the C6 can
 * deep-sleep until a wake flag or sn_wake_next_timer(). */
bool          sn_bs_needs_tick(const sn_brainstem_t *bs);
sn_bs_state_t sn_bs_state(const sn_brainstem_t *bs);
uint64_t      sn_bs_total_nj(const sn_brainstem_t *bs);
const char   *sn_bs_report_name(sn_bs_report_t r);

#endif
