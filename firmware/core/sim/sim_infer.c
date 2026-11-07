#include "sim_infer.h"
#include "sim_hal.h"
#include <string.h>

/* Sim latencies (TODO-measure on the STM32N6570-DK in M1). */
const sim_latency_t SIM_LATENCY_DEFAULT = {
    .camera_start_ms = 600u,
    .frame_ms        = 66u,
    .face_detect_ms  = 10u,
    .embed_ms        = 15u,
    .publish_ms      = 150u,   /* = radio.publish_time (constants.yaml); test_pipeline
                                  * checks uplink_est_mj = active power x this */
};

static const sim_scene_t k_empty_scene = { .n_frames = 1u, .conf = { 0.0f } };

static const sim_scene_t *scene_of(const sim_infer_t *s)
{
    return (s->scene != NULL) ? s->scene : &k_empty_scene;
}

static bool cb_camera_start(void *ctx)
{
    sim_infer_t *s = (sim_infer_t *)ctx;
    s->n_camera_start++;
    sim_advance(s->lat.camera_start_ms);
    return !scene_of(s)->fail_camera;
}

static bool cb_person(void *ctx, uint8_t frame, float *conf)
{
    sim_infer_t *s = (sim_infer_t *)ctx;
    const sim_scene_t *sc = scene_of(s);
    uint8_t idx;
    s->n_frames++;
    sim_advance(s->lat.frame_ms);
    if (sc->n_frames == 0u) {
        *conf = 0.0f;
        return true;
    }
    idx = (frame < sc->n_frames) ? frame : (uint8_t)(sc->n_frames - 1u);
    if (idx >= SIM_SCENE_FRAMES) {
        idx = (uint8_t)(SIM_SCENE_FRAMES - 1u);
    }
    *conf = sc->conf[idx];
    return true;
}

static uint8_t cb_face_detect(void *ctx, uint8_t frame, uint8_t max_faces)
{
    sim_infer_t *s = (sim_infer_t *)ctx;
    const sim_scene_t *sc = scene_of(s);
    uint8_t n;
    s->n_face_detect++;
    sim_advance(s->lat.face_detect_ms);
    if ((sc->face_mask != 0u) && ((sc->face_mask & (1u << frame)) == 0u)) {
        return 0u;
    }
    n = (sc->n_faces > SN_MAX_FACES) ? (uint8_t)SN_MAX_FACES : sc->n_faces;
    return (n > max_faces) ? max_faces : n;
}

static bool cb_embed(void *ctx, uint8_t frame, uint8_t face, int8_t emb[SN_EMB_DIM])
{
    sim_infer_t *s = (sim_infer_t *)ctx;
    const sim_scene_t *sc = scene_of(s);
    (void)frame;
    s->n_embed++;
    sim_advance(s->lat.embed_ms);
    if (sc->fail_embed || (face >= SN_MAX_FACES)) {
        return false;
    }
    (void)memcpy(emb, sc->emb[face], SN_EMB_DIM);
    return true;
}

static bool cb_clip(void *ctx, const char *clip_id, uint32_t duration_ms)
{
    sim_infer_t *s = (sim_infer_t *)ctx;
    s->n_clips++;
    sim_advance(duration_ms);
    sn_strlcpy(s->last_clip_id, clip_id, sizeof(s->last_clip_id));
    return !scene_of(s)->fail_clip;
}

static bool cb_publish(void *ctx, const char *json, size_t len)
{
    sim_infer_t *s = (sim_infer_t *)ctx;
    (void)len;
    s->n_publish++;
    sim_advance(s->lat.publish_ms);
    sn_strlcpy(s->last_json, json, sizeof(s->last_json));
    return true;
}

void sim_infer_init(sim_infer_t *s, sn_infer_ops_t *ops)
{
    (void)memset(s, 0, sizeof(*s));
    s->lat = SIM_LATENCY_DEFAULT;
    (void)memset(ops, 0, sizeof(*ops));
    ops->ctx = s;
    ops->camera_start = cb_camera_start;
    ops->person_detect = cb_person;
    ops->face_detect = cb_face_detect;
    ops->face_embed = cb_embed;
    ops->clip_record = cb_clip;
    ops->publish = cb_publish;
    ops->camera_stop = NULL;
}

void sim_infer_set_scene(sim_infer_t *s, const sim_scene_t *scene) { s->scene = scene; }

void sim_scene_empty(sim_scene_t *sc, float conf)
{
    (void)memset(sc, 0, sizeof(*sc));
    sc->n_frames = 1u;
    sc->conf[0] = conf;
}

void sim_scene_person(sim_scene_t *sc, float conf, uint8_t n_faces)
{
    (void)memset(sc, 0, sizeof(*sc));
    sc->n_frames = 1u;
    sc->conf[0] = conf;
    sc->n_faces = n_faces;
}
