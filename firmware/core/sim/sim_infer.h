/*
 * sim_infer.h - scripted fake inference provider (sn_infer_ops_t) for host
 * tests. Each call charges a modelled latency to the simulated clock.
 */
#ifndef SIM_INFER_H
#define SIM_INFER_H
#include "sentinel_hal.h"
#include "sn_event.h"

#define SIM_SCENE_FRAMES 8u

typedef struct {
    uint8_t n_frames;                     /* frames with given conf; later frames reuse the last */
    float   conf[SIM_SCENE_FRAMES];
    uint8_t n_faces;                      /* faces visible in frames listed in face_mask */
    uint8_t face_mask;                    /* bit i: faces visible in frame i; 0 = every frame */
    int8_t  emb[SN_MAX_FACES][SN_EMB_DIM];
    bool    fail_camera;
    bool    fail_embed;
    bool    fail_clip;
} sim_scene_t;

typedef struct {
    uint32_t camera_start_ms;   /* AE/AWB settle   */
    uint32_t frame_ms;          /* capture + detect */
    uint32_t face_detect_ms;
    uint32_t embed_ms;
    uint32_t publish_ms;
} sim_latency_t;

extern const sim_latency_t SIM_LATENCY_DEFAULT;

typedef struct {
    const sim_scene_t *scene;
    sim_latency_t      lat;
    uint32_t           n_camera_start;
    uint32_t           n_frames;
    uint32_t           n_face_detect;
    uint32_t           n_embed;
    uint32_t           n_clips;
    uint32_t           n_publish;
    char               last_clip_id[SN_CLIP_ID_MAX];
    char               last_json[SN_EVENT_JSON_MAX];
} sim_infer_t;

/* Initializes the provider and fills ops (ops->ctx = s). */
void sim_infer_init(sim_infer_t *s, sn_infer_ops_t *ops);
void sim_infer_set_scene(sim_infer_t *s, const sim_scene_t *scene);

/* Scene helpers. */
void sim_scene_empty(sim_scene_t *sc, float conf);             /* all frames conf */
void sim_scene_person(sim_scene_t *sc, float conf, uint8_t n_faces);

#endif
