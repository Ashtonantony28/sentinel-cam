#include "sn_pipeline.h"
#include <string.h>
#include <math.h>

const char *sn_pipe_state_name(sn_pipe_state_t s)
{
    switch (s) {
    case SN_PIPE_IDLE:          return "IDLE";
    case SN_PIPE_CAMERA_START:  return "CAMERA_START";
    case SN_PIPE_PERSON_DETECT: return "PERSON_DETECT";
    case SN_PIPE_FACE_DETECT:   return "FACE_DETECT";
    case SN_PIPE_EMBED_MATCH:   return "EMBED_MATCH";
    case SN_PIPE_POLICY:        return "POLICY";
    case SN_PIPE_CLIP:          return "CLIP";
    case SN_PIPE_REPORT:        return "REPORT";
    case SN_PIPE_DONE:          return "DONE";
    default:                    return "?";
    }
}

void sn_pipe_cfg_default(sn_pipe_cfg_t *cfg)
{
    if (cfg == NULL) {
        return;
    }
    cfg->dev = "000000000000";
    cfg->fw = "0.0.0";
    cfg->model = "none";
    cfg->person_thresh = 0.5f;
    cfg->frames_window = 4u;
    cfg->frames_required = 2u;
    cfg->face_frame_attempts = 2u;
    cfg->clip_ms = 10000u;
    cfg->pm = &SN_POWER_MODEL_DEFAULT;
}

sn_action_t sn_policy_decide(sn_wake_src_t wake, bool person_present,
                             uint8_t n_faces, uint8_t n_known)
{
    if (wake == SN_WAKE_BUTTON) {
        return SN_ACTION_RING;
    }
    if (!person_present) {
        return SN_ACTION_NONE;
    }
    if ((n_faces > 0u) && (n_known == n_faces)) {
        return SN_ACTION_LOG_KNOWN;
    }
    return SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP;
}

void sn_make_clip_id(const char *dev, uint64_t ts_ms, char *out, size_t cap)
{
    static const char hex[] = "0123456789abcdef";
    size_t n = 0u;
    uint32_t i;
    if ((out == NULL) || (cap == 0u)) {
        return;
    }
    if (dev != NULL) {
        while ((dev[n] != '\0') && (n < SN_DEV_ID_LEN) && ((n + 1u) < cap)) {
            out[n] = dev[n];
            n++;
        }
    }
    if ((n + 1u + 16u + 1u) > cap) {
        out[n] = '\0';
        return;
    }
    out[n] = '-';
    n++;
    for (i = 0u; i < 16u; i++) {
        out[n + i] = hex[(ts_ms >> (60u - (4u * i))) & 0x0Fu];
    }
    out[n + 16u] = '\0';
}

void sn_pipe_init(sn_pipeline_t *p, const sn_pipe_cfg_t *cfg,
                  const sn_infer_ops_t *ops, const sn_gallery_t *gallery)
{
    if (p == NULL) {
        return;
    }
    (void)memset(p, 0, sizeof(*p));
    if (cfg != NULL) {
        p->cfg = *cfg;
    } else {
        sn_pipe_cfg_default(&p->cfg);
    }
    if (p->cfg.pm == NULL) {
        p->cfg.pm = &SN_POWER_MODEL_DEFAULT;
    }
    if (p->cfg.frames_window > SN_PIPE_FRAMES_MAX) {
        p->cfg.frames_window = (uint8_t)SN_PIPE_FRAMES_MAX;
    }
    if (p->cfg.frames_required == 0u) {
        p->cfg.frames_required = 1u;
    }
    p->ops = ops;
    p->gallery = gallery;
    p->state = SN_PIPE_IDLE;
}

static void go(sn_pipeline_t *p, sn_pipe_state_t s)
{
    p->state = s;
    if (p->trace_len < SN_PIPE_TRACE_MAX) {
        p->trace[p->trace_len] = s;
        p->trace_len++;
    }
}

bool sn_pipe_start(sn_pipeline_t *p, const sn_job_t *job)
{
    sn_event_record_t *r;
    if ((p == NULL) || (job == NULL) || (p->ops == NULL)) {
        return false;
    }
    if ((job->wake < SN_WAKE_PIR) || (job->wake > SN_WAKE_TIMER)) {
        return false;
    }
    p->job = *job;
    p->t_start = sn_hal_time_ms();
    p->t_clip = 0u;
    p->frame = 0u;
    p->hits = 0u;
    (void)memset(p->conf, 0, sizeof(p->conf));
    p->person_present = false;
    p->person_conf = 0.0f;
    p->face_attempts = 0u;
    p->tried_mask = 0u;
    p->face_frame = 0u;
    p->n_faces_found = 0u;
    p->face_idx = 0u;
    p->json[0] = '\0';
    p->json_len = 0u;
    (void)memset(&p->result, 0, sizeof(p->result));
    p->trace_len = 0u;

    r = &p->rec;
    (void)memset(r, 0, sizeof(*r));
    sn_strlcpy(r->dev, p->cfg.dev, sizeof(r->dev));
    sn_strlcpy(r->fw, p->cfg.fw, sizeof(r->fw));
    sn_strlcpy(r->model, p->cfg.model, sizeof(r->model));
    r->ts_ms = job->ts_unix_ms;
    r->wake = job->wake;
    r->action = SN_ACTION_NONE;
    r->battery_mv = job->battery_mv;
    r->soc_pct = job->soc_pct;
    go(p, SN_PIPE_CAMERA_START);
    return true;
}

static float sanitize_conf(float c)
{
    if (!isfinite(c) || (c < 0.0f)) {
        return 0.0f;
    }
    return (c > 1.0f) ? 1.0f : c;
}

static void step_person(sn_pipeline_t *p)
{
    float c = 0.0f;
    uint8_t remaining;
    if ((p->ops->person_detect == NULL) || !p->ops->person_detect(p->ops->ctx, p->frame, &c)) {
        c = 0.0f;   /* failed frame counts as no person */
    }
    c = sanitize_conf(c);
    p->conf[p->frame] = c;
    if (c > p->person_conf) {
        p->person_conf = c;
    }
    if (c >= p->cfg.person_thresh) {
        p->hits++;
    }
    p->frame++;
    remaining = (uint8_t)(p->cfg.frames_window - p->frame);
    if (p->hits >= p->cfg.frames_required) {
        p->person_present = true;
        go(p, SN_PIPE_FACE_DETECT);
    } else if (((uint32_t)p->hits + remaining) < p->cfg.frames_required) {
        /* Decision settled: cannot reach the required count. */
        p->person_present = false;
        go(p, SN_PIPE_POLICY);
    } else {
        /* stay */
    }
}

/* Next untried frame with conf >= thresh, highest confidence first. */
static int32_t next_face_frame(const sn_pipeline_t *p)
{
    int32_t best = -1;
    uint8_t i;
    for (i = 0u; i < p->frame; i++) {
        if (((p->tried_mask & (1u << i)) == 0u) && (p->conf[i] >= p->cfg.person_thresh)) {
            if ((best < 0) || (p->conf[i] > p->conf[best])) {
                best = (int32_t)i;
            }
        }
    }
    return best;
}

static void step_face_detect(sn_pipeline_t *p)
{
    int32_t f = next_face_frame(p);
    uint8_t n = 0u;
    if ((f < 0) || (p->face_attempts >= p->cfg.face_frame_attempts)) {
        go(p, SN_PIPE_POLICY);
        return;
    }
    p->face_attempts++;
    p->tried_mask |= (1u << (uint32_t)f);
    if (p->ops->face_detect != NULL) {
        n = p->ops->face_detect(p->ops->ctx, (uint8_t)f, (uint8_t)SN_MAX_FACES);
    }
    if (n > SN_MAX_FACES) {
        n = (uint8_t)SN_MAX_FACES;
    }
    if (n > 0u) {
        p->face_frame = (uint8_t)f;
        p->n_faces_found = n;
        p->face_idx = 0u;
        go(p, SN_PIPE_EMBED_MATCH);
    }
    /* else: stay in FACE_DETECT and try the next frame on the next step */
}

static void step_embed_match(sn_pipeline_t *p)
{
    int8_t emb[SN_EMB_DIM];
    if (p->face_idx >= p->n_faces_found) {
        go(p, SN_PIPE_POLICY);
        return;
    }
    if ((p->ops->face_embed != NULL) &&
        p->ops->face_embed(p->ops->ctx, p->face_frame, p->face_idx, emb)) {
        sn_match_t m;
        sn_face_result_t *fr = &p->rec.faces[p->rec.n_faces];
        sn_gallery_match(p->gallery, emb, &m);
        fr->known = m.matched;
        fr->id = m.matched ? m.id : 0u;
        fr->score = (m.best_idx >= 0) ? (float)m.best_score : 0.0f;
        if (fr->score < 0.0f) {
            fr->score = 0.0f;
        }
        p->rec.n_faces++;
    }
    /* embed failure: face dropped from the record */
    p->face_idx++;
    if (p->face_idx >= p->n_faces_found) {
        go(p, SN_PIPE_POLICY);
    }
}

static void step_policy(sn_pipeline_t *p)
{
    uint8_t i;
    uint8_t known = 0u;
    for (i = 0u; i < p->rec.n_faces; i++) {
        if (p->rec.faces[i].known) {
            known++;
        }
    }
    p->rec.person_conf = p->person_conf;
    p->rec.action = sn_policy_decide(p->job.wake, p->person_present, p->rec.n_faces, known);
    if ((p->rec.action == SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP) ||
        ((p->rec.action == SN_ACTION_RING) && p->person_present)) {
        go(p, SN_PIPE_CLIP);
    } else {
        go(p, SN_PIPE_REPORT);
    }
}

static void step_clip(sn_pipeline_t *p)
{
    char id[SN_CLIP_ID_MAX];
    uint32_t t0 = sn_hal_time_ms();
    sn_make_clip_id(p->rec.dev, p->rec.ts_ms, id, sizeof(id));
    if ((p->ops->clip_record != NULL) && p->ops->clip_record(p->ops->ctx, id, p->cfg.clip_ms)) {
        p->rec.has_clip = true;
        sn_strlcpy(p->rec.clip_id, id, sizeof(p->rec.clip_id));
    }
    p->t_clip = sn_hal_time_ms() - t0;
    go(p, SN_PIPE_REPORT);
}

static void step_report(sn_pipeline_t *p)
{
    const sn_power_model_t *pm = p->cfg.pm;
    uint32_t elapsed = sn_hal_time_ms() - p->t_start;
    uint32_t active_ms = (elapsed > p->t_clip) ? (elapsed - p->t_clip) : 0u;
    uint64_t e = p->job.boot_energy_known ? p->job.boot_energy_mj : pm->boot_est_mj;

    if (p->ops->camera_stop != NULL) {
        p->ops->camera_stop(p->ops->ctx);
    }
    e += sn_energy_mw_ms_to_mj(pm->cortex_active_mw, active_ms);
    e += sn_energy_mw_ms_to_mj(pm->cortex_clip_mw, p->t_clip);
    e += pm->uplink_est_mj;
    p->rec.energy_mj = (e > UINT32_MAX) ? UINT32_MAX : (uint32_t)e;
    p->json_len = sn_event_to_json(&p->rec, p->json, sizeof(p->json));
    if ((p->json_len > 0u) && (p->ops->publish != NULL)) {
        p->result.published = p->ops->publish(p->ops->ctx, p->json, p->json_len);
    }
    p->result.elapsed_ms = sn_hal_time_ms() - p->t_start;
    p->result.clip_ms = p->t_clip;
    go(p, SN_PIPE_DONE);
}

sn_pipe_state_t sn_pipe_step(sn_pipeline_t *p)
{
    if (p == NULL) {
        return SN_PIPE_IDLE;
    }
    switch (p->state) {
    case SN_PIPE_CAMERA_START:
        if ((p->ops->camera_start != NULL) && !p->ops->camera_start(p->ops->ctx)) {
            /* Camera failed: no frames; report what we know. */
            p->person_present = false;
            go(p, SN_PIPE_POLICY);
        } else {
            go(p, SN_PIPE_PERSON_DETECT);
        }
        break;
    case SN_PIPE_PERSON_DETECT:
        step_person(p);
        break;
    case SN_PIPE_FACE_DETECT:
        step_face_detect(p);
        break;
    case SN_PIPE_EMBED_MATCH:
        step_embed_match(p);
        break;
    case SN_PIPE_POLICY:
        step_policy(p);
        break;
    case SN_PIPE_CLIP:
        step_clip(p);
        break;
    case SN_PIPE_REPORT:
        step_report(p);
        break;
    case SN_PIPE_IDLE:
    case SN_PIPE_DONE:
    default:
        break;
    }
    return p->state;
}

const sn_event_record_t *sn_pipe_run(sn_pipeline_t *p, const sn_job_t *job)
{
    uint32_t guard = 0u;
    if (!sn_pipe_start(p, job)) {
        return NULL;
    }
    /* Bounded: every stage either advances or consumes a frame/face/attempt. */
    while ((p->state != SN_PIPE_DONE) && (guard < 64u)) {
        (void)sn_pipe_step(p);
        guard++;
    }
    return &p->rec;
}
