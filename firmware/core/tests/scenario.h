/*
 * scenario.h - host "world" that replays a scripted timeline through the
 * brainstem (wake fusion + power sequencer) and the N6 pipeline, using the
 * simulated HAL and scripted inference provider.
 */
#ifndef SCENARIO_H
#define SCENARIO_H
#include "sim_hal.h"
#include "sim_infer.h"
#include "sn_status.h"
#include "sn_brainstem.h"
#include "sn_pipeline.h"

#define SC_MAX_RECORDS 128u
#define SC_MAX_STATUS  32u
#define SC_KNOWN       3u
#define SC_TICK_MS     10u
#define SC_UNIX_BASE   1793923200000ull   /* 2026-11-06T00:00:00Z */

typedef enum {
    SCN_EMPTY = 0,     /* wind / nothing: conf ~0.1                 */
    SCN_CAT,           /* one frame over threshold                   */
    SCN_HEADLIGHTS,    /* 0.45 every frame (just under)              */
    SCN_UNKNOWN,       /* person + stranger face                     */
    SCN_NOFACE,        /* person, back turned                        */
    SCN_KNOWN1,        /* person + face of gallery id 1              */
    SCN_KNOWN2,
    SCN_KNOWN3,
    SCN_MIXED,         /* known id 3 + stranger                      */
    SCN_COUNT
} sc_scene_kind_t;

typedef enum {
    SCI_PIR = 0,       /* PIR high for dur_ms, scene active          */
    SCI_BUTTON,        /* button held dur_ms, scene active           */
    SCI_RADAR,
    SCI_BATTERY,       /* set battery mv/soc and load sag            */
    SCI_RAIL_FAULT     /* rail never PG from now on (mv = rail id)   */
} sc_item_kind_t;

typedef struct {
    uint32_t        t_ms;      /* offset from scenario start */
    sc_item_kind_t  kind;
    uint32_t        dur_ms;
    sc_scene_kind_t scene;
    uint16_t        mv;
    uint8_t         soc;
    uint16_t        sag_mv;
} sc_item_t;

typedef struct {
    sn_event_record_t rec;
    uint32_t          t_dispatch;     /* monotonic, offset from start */
    sc_scene_kind_t   scene;
} sc_record_t;

typedef struct {
    sn_brainstem_t  bs;
    sn_bs_cfg_t     bs_cfg;
    sn_pipeline_t   pipe;
    sn_pipe_cfg_t   pipe_cfg;
    sim_infer_t     infer;
    sn_infer_ops_t  ops;
    sn_gallery_t    gal;
    uint8_t         blob[SN_GAL_MAX_BLOB_SIZE];
    int8_t          protos[SC_KNOWN][5][SN_EMB_DIM];
    float           centers[SC_KNOWN][SN_EMB_DIM];
    uint32_t        seed;
    uint32_t        t0;
    sc_scene_kind_t scene;
    uint32_t        scene_until;
    sim_scene_t     cur_scene;
    bool            job_pending;
    bool            job_active;
    bool            job_aborted;
    uint32_t        n_aborted;
    const sc_item_t *items;
    uint32_t        n_items;
    uint32_t        idx;
    sn_job_t        job;
    sc_scene_kind_t job_scene;
    uint32_t        t_job_start;
    bool            pin_release_pending[SN_GPIO_COUNT];
    uint32_t        pin_release_t[SN_GPIO_COUNT];
    sc_record_t     recs[SC_MAX_RECORDS];
    uint32_t        n_recs;
    uint32_t        actions[4];
    uint32_t        reports[SN_BS_REPORT_COUNT];
    uint64_t        sum_event_mj;
    uint32_t        loop_iters;
    sn_status_t     status[SC_MAX_STATUS];  /* fault statuses the C6 would publish */
    uint32_t        n_status;
} sc_world_t;

/* cfg may be NULL for defaults. start_ms is the sim monotonic start time. */
void sc_world_init(sc_world_t *w, const sn_bs_cfg_t *cfg, uint32_t start_ms);
/* items must be sorted by t_ms. Runs until t0 + until_ms. */
void sc_world_run(sc_world_t *w, const sc_item_t *items, uint32_t n_items, uint32_t until_ms);
/* Number of records with this action / wake. */
uint32_t sc_count(const sc_world_t *w, int action, int wake);
/* Cortex-domain energy integrated by the brainstem, mJ. */
uint64_t sc_cortex_mj(const sc_world_t *w);
/* Nominal N6 energy of one event from the power model the world runs with and
 * the sim latencies: boot_est + active x (camera start + 4 frames) + uplink_est,
 * plus clip_mw x clip_ms when with_clip. Tests derive their envelopes from this
 * instead of hard-coding numbers, so they follow constants.yaml. */
uint32_t sc_nominal_event_mj(const sc_world_t *w, bool with_clip);
/* Standby energy (mJ) the brainstem must integrate over ms, same rounding. */
uint64_t sc_standby_mj(const sc_world_t *w, uint32_t ms);

#endif
