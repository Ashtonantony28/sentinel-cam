/*
 * sn_pipeline.h - N6-side event pipeline + policy state machine
 * (docs/INTERFACES.md 2).
 *
 *  IDLE -> CAMERA_START -> PERSON_DETECT (frames 0..3, early exit when the
 *  2-of-4 decision is settled) -> [person or BUTTON] FACE_DETECT ->
 *  EMBED_MATCH (one face per step) -> POLICY -> [clip] CLIP -> REPORT -> DONE
 *
 *  Person present  : conf >= person_thresh (0.5) in >= 2 of first 4 frames.
 *  Faces           : face detection runs on frames that passed the person
 *                    threshold, best confidence first, until a frame yields
 *                    >= 1 face (at most face_frame_attempts frames). Each
 *                    face is embedded and matched (INTERFACES.md 1).
 *  Policy          : BUTTON                          -> RING (clip iff person)
 *                    no person                       -> NONE
 *                    person, >=1 face, all known      -> LOG_KNOWN
 *                    person, otherwise (unknown face,
 *                    or no usable face)              -> NOTIFY_UNKNOWN_WITH_CLIP
 *  Energy          : energy_mj = job.boot_energy_mj (from the C6, or the
 *                    model estimate if !boot_energy_known) + cortex_active_mw x elapsed ms
 *                    (excluding clip) + cortex_clip_mw x clip ms
 *                    + uplink_est_mj.
 */
#ifndef SN_PIPELINE_H
#define SN_PIPELINE_H
#include "sentinel_hal.h"
#include "sn_gallery.h"
#include "sn_event.h"
#include "sn_energy.h"

#define SN_PIPE_FRAMES_MAX 8u
#define SN_PIPE_TRACE_MAX  24u

typedef enum {
    SN_PIPE_IDLE = 0,
    SN_PIPE_CAMERA_START,
    SN_PIPE_PERSON_DETECT,
    SN_PIPE_FACE_DETECT,
    SN_PIPE_EMBED_MATCH,
    SN_PIPE_POLICY,
    SN_PIPE_CLIP,
    SN_PIPE_REPORT,
    SN_PIPE_DONE
} sn_pipe_state_t;

typedef struct {
    sn_wake_src_t wake;
    uint64_t      ts_unix_ms;
    uint16_t      battery_mv;
    uint8_t       soc_pct;
    bool          boot_energy_known; /* false: N6 uses pm->boot_est_mj        */
    uint32_t      boot_energy_mj;    /* measured/integrated by the C6         */
} sn_job_t;

typedef struct {
    const char              *dev;               /* 12 hex chars */
    const char              *fw;
    const char              *model;
    float                    person_thresh;     /* 0.5  */
    uint8_t                  frames_window;     /* 4    */
    uint8_t                  frames_required;   /* 2    */
    uint8_t                  face_frame_attempts;
    uint32_t                 clip_ms;
    const sn_power_model_t  *pm;
} sn_pipe_cfg_t;

/* Fills INTERFACES.md defaults (dev/fw/model must still be set). */
void sn_pipe_cfg_default(sn_pipe_cfg_t *cfg);

typedef struct {
    uint32_t elapsed_ms;    /* job start -> report published */
    uint32_t clip_ms;       /* time spent in clip_record     */
    bool     published;
} sn_job_result_t;

typedef struct {
    sn_pipe_cfg_t         cfg;
    const sn_infer_ops_t *ops;
    const sn_gallery_t   *gallery;
    sn_pipe_state_t       state;
    sn_job_t              job;
    uint32_t              t_start;
    uint32_t              t_clip;          /* clip duration actually spent */
    /* person stage */
    uint8_t               frame;           /* next frame to run */
    uint8_t               hits;
    float                 conf[SN_PIPE_FRAMES_MAX];
    bool                  person_present;
    float                 person_conf;     /* max over evaluated frames */
    /* face stage */
    uint8_t               face_attempts;
    uint32_t              tried_mask;      /* frames already face-detected */
    uint8_t               face_frame;
    uint8_t               n_faces_found;
    uint8_t               face_idx;
    /* outputs */
    sn_event_record_t     rec;
    char                  json[SN_EVENT_JSON_MAX];
    size_t                json_len;
    sn_job_result_t       result;
    /* state trace for tests / debug logs */
    sn_pipe_state_t       trace[SN_PIPE_TRACE_MAX];
    uint8_t               trace_len;
} sn_pipeline_t;

void            sn_pipe_init(sn_pipeline_t *p, const sn_pipe_cfg_t *cfg,
                             const sn_infer_ops_t *ops, const sn_gallery_t *gallery);
bool            sn_pipe_start(sn_pipeline_t *p, const sn_job_t *job);
/* Executes one stage; returns the new state. */
sn_pipe_state_t sn_pipe_step(sn_pipeline_t *p);
/* Starts and runs to DONE; returns the record (NULL on bad args). */
const sn_event_record_t *sn_pipe_run(sn_pipeline_t *p, const sn_job_t *job);
const char     *sn_pipe_state_name(sn_pipe_state_t s);
/* Pure policy function (exposed for tests). */
sn_action_t     sn_policy_decide(sn_wake_src_t wake, bool person_present,
                                 uint8_t n_faces, uint8_t n_known);
/* "<dev>-<ts as 16 hex>" into out (cap >= SN_CLIP_ID_MAX). */
void            sn_make_clip_id(const char *dev, uint64_t ts_ms, char *out, size_t cap);

#endif
