#include "sn_test.h"
#include "test_util.h"
#include "sim_hal.h"
#include "sim_infer.h"
#include "sn_pipeline.h"

static uint8_t        g_blob[SN_GAL_MAX_BLOB_SIZE];
static int8_t         g_protos[3][4][SN_EMB_DIM];
static float          g_centers[3][SN_EMB_DIM];
static sn_gallery_t   G;
static sim_infer_t    S;
static sn_infer_ops_t OPS;
static sn_pipeline_t  P;
static sn_pipe_cfg_t  CFG;
static sim_scene_t    SC;
static uint32_t       g_seed;

static void setup(void)
{
    sn_gal_person_spec_t specs[3];
    size_t len;
    uint32_t s = 42u;
    uint32_t i;
    uint32_t k;
    sim_hal_reset(5000u, 1762450000000ull);
    for (i = 0u; i < 3u; i++) {
        tu_unit_vec(&s, g_centers[i]);
        for (k = 0u; k < 4u; k++) {
            tu_noisy_q(&s, g_centers[i], 0.25f, g_protos[i][k]);
        }
        specs[i].id = (uint16_t)(i + 1u);
        specs[i].n_protos = 4u;
        specs[i].threshold_q15 = SN_GAL_DEFAULT_THRESHOLD_Q15;
        specs[i].name = "known";
        specs[i].protos = &g_protos[i][0][0];
    }
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, 3u, &len), SN_GAL_OK);
    CHECK_EQ_INT(sn_gallery_parse(&G, g_blob, len), SN_GAL_OK);
    sim_infer_init(&S, &OPS);
    sn_pipe_cfg_default(&CFG);
    CFG.dev = "a1b2c3d4e5f6";
    CFG.fw = "0.1.0";
    CFG.model = "det1-emb1";
    sn_pipe_init(&P, &CFG, &OPS, &G);
    g_seed = 777u;
    memset(&SC, 0, sizeof(SC));
    sim_infer_set_scene(&S, &SC);
}

static sn_job_t job(sn_wake_src_t w)
{
    sn_job_t j;
    memset(&j, 0, sizeof(j));
    j.wake = w;
    j.ts_unix_ms = 1762450000123ull;
    j.battery_mv = 3800u;
    j.soc_pct = 70u;
    j.boot_energy_known = true;
    j.boot_energy_mj = 80u;
    return j;
}

static void frames(float a, float b, float c, float d)
{
    SC.n_frames = 4u;
    SC.conf[0] = a; SC.conf[1] = b; SC.conf[2] = c; SC.conf[3] = d;
}

static void face_known(uint8_t slot, uint32_t person)
{
    tu_noisy_q(&g_seed, g_centers[person], 0.25f, SC.emb[slot]);
}

static void face_stranger(uint8_t slot)
{
    float v[SN_EMB_DIM];
    tu_unit_vec(&g_seed, v);
    sn_embed_quantize(v, SC.emb[slot]);
}

static const sn_event_record_t *run(sn_wake_src_t w)
{
    sn_job_t j = job(w);
    return sn_pipe_run(&P, &j);
}

/* All 16 hit patterns over 4 frames: present iff popcount >= 2. */
static void pipe_person_rule_2_of_4(void)
{
    uint32_t mask;
    setup();
    for (mask = 0u; mask < 16u; mask++) {
        uint32_t bits = (mask & 1u) + ((mask >> 1) & 1u) + ((mask >> 2) & 1u) + ((mask >> 3) & 1u);
        const sn_event_record_t *r;
        frames((mask & 1u) ? 0.5f : 0.49f, (mask & 2u) ? 0.9f : 0.1f,
               (mask & 4u) ? 0.7f : 0.0f, (mask & 8u) ? 0.6f : 0.3f);
        SC.n_faces = 0u;
        S.n_frames = 0u;
        r = run(SN_WAKE_PIR);
        CHECK(r != NULL);
        CHECK_EQ_INT(r->action, (bits >= 2u) ? SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP : SN_ACTION_NONE);
        CHECK_EQ_INT(P.person_present, bits >= 2u);
        CHECK(S.n_frames <= 4u);
        CHECK(r->person_conf >= 0.49f);
    }
}

/* The decision stops early: 2 hits -> 2 frames; 0 hits in 3 frames -> 3. */
static void pipe_person_early_exit(void)
{
    setup();
    frames(0.9f, 0.8f, 0.0f, 0.0f);
    (void)run(SN_WAKE_PIR);
    CHECK_EQ_INT(S.n_frames, 2u);
    S.n_frames = 0u;
    S.n_face_detect = 0u;
    S.n_clips = 0u;
    frames(0.1f, 0.2f, 0.3f, 0.95f);
    CHECK_EQ_INT(run(SN_WAKE_PIR)->action, SN_ACTION_NONE);
    CHECK_EQ_INT(S.n_frames, 3u);                 /* frame 4 can't reach 2 hits */
    CHECK_NEAR(P.rec.person_conf, 0.3, 1e-6);     /* max over evaluated frames */
    CHECK_EQ_INT(S.n_face_detect, 0u);
    CHECK_EQ_INT(S.n_clips, 0u);
}

static void pipe_known_person_logs(void)
{
    const sn_event_record_t *r;
    setup();
    frames(0.8f, 0.9f, 0.7f, 0.6f);
    SC.n_faces = 1u;
    face_known(0u, 1u);
    r = run(SN_WAKE_PIR);
    CHECK_EQ_INT(r->action, SN_ACTION_LOG_KNOWN);
    CHECK_EQ_INT(r->n_faces, 1u);
    CHECK(r->faces[0].known);
    CHECK_EQ_INT(r->faces[0].id, 2u);
    CHECK(r->faces[0].score >= 0.55f);
    CHECK(!r->has_clip);
    CHECK_EQ_INT(S.n_clips, 0u);
    CHECK_EQ_INT(S.n_publish, 1u);
    CHECK(strstr(S.last_json, "\"action\":\"LOG_KNOWN\"") != NULL);
    CHECK(strstr(S.last_json, "\"faces\":[{\"id\":2,") != NULL);
    CHECK(strstr(S.last_json, "\"clip_id\":null") != NULL);
}

static void pipe_unknown_person_notifies_with_clip(void)
{
    const sn_event_record_t *r;
    char want[SN_CLIP_ID_MAX];
    setup();
    frames(0.8f, 0.9f, 0.7f, 0.6f);
    SC.n_faces = 1u;
    face_stranger(0u);
    r = run(SN_WAKE_PIR);
    CHECK_EQ_INT(r->action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(r->n_faces, 1u);
    CHECK(!r->faces[0].known);
    CHECK(r->has_clip);
    sn_make_clip_id("a1b2c3d4e5f6", 1762450000123ull, want, sizeof(want));
    CHECK_STR(r->clip_id, want);
    CHECK_STR(want, "a1b2c3d4e5f6-0000019a5a34c8fb");
    CHECK_STR(S.last_clip_id, want);
    CHECK(strstr(S.last_json, "\"id\":null") != NULL);
    CHECK_EQ_INT(S.n_clips, 1u);
}

static void pipe_person_without_face_is_unknown(void)
{
    setup();
    frames(0.8f, 0.9f, 0.7f, 0.6f);
    SC.n_faces = 0u;                              /* back turned */
    CHECK_EQ_INT(run(SN_WAKE_PIR)->action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(P.rec.n_faces, 0u);
    /* face detection attempts are bounded: 2 frames ran (2-of-4 settled at 2) */
    CHECK_EQ_INT(S.n_face_detect, 2u);
}

static void pipe_mixed_known_and_unknown_notifies(void)
{
    const sn_event_record_t *r;
    setup();
    frames(0.8f, 0.9f, 0.7f, 0.6f);
    SC.n_faces = 2u;
    face_known(0u, 0u);
    face_stranger(1u);
    r = run(SN_WAKE_RADAR);
    CHECK_EQ_INT(r->action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(r->n_faces, 2u);
    CHECK(r->faces[0].known);
    CHECK_EQ_INT(r->faces[0].id, 1u);
    CHECK(!r->faces[1].known);
    CHECK(strstr(S.last_json, "\"wake\":\"RADAR\"") != NULL);
}

static void pipe_two_known_logs(void)
{
    setup();
    frames(0.8f, 0.9f, 0.7f, 0.6f);
    SC.n_faces = 2u;
    face_known(0u, 0u);
    face_known(1u, 2u);
    CHECK_EQ_INT(run(SN_WAKE_PIR)->action, SN_ACTION_LOG_KNOWN);
    CHECK_EQ_INT(P.rec.faces[1].id, 3u);
}

static void pipe_faces_found_on_second_frame(void)
{
    setup();
    frames(0.6f, 0.95f, 0.0f, 0.0f);
    SC.n_faces = 1u;
    SC.face_mask = 1u << 0;                       /* face only in frame 0 */
    face_known(0u, 2u);
    CHECK_EQ_INT(run(SN_WAKE_PIR)->action, SN_ACTION_LOG_KNOWN);
    /* tries frame 1 (best conf) first, then frame 0 */
    CHECK_EQ_INT(S.n_face_detect, 2u);
    CHECK_EQ_INT(P.face_frame, 0u);
}

static void pipe_button_rings(void)
{
    const sn_event_record_t *r;
    setup();
    frames(0.1f, 0.1f, 0.1f, 0.1f);               /* nobody in view */
    r = run(SN_WAKE_BUTTON);
    CHECK_EQ_INT(r->action, SN_ACTION_RING);
    CHECK(!r->has_clip);
    CHECK_EQ_INT(S.n_clips, 0u);
    /* person seen -> clip, even when known */
    frames(0.9f, 0.9f, 0.9f, 0.9f);
    SC.n_faces = 1u;
    face_known(0u, 1u);
    r = run(SN_WAKE_BUTTON);
    CHECK_EQ_INT(r->action, SN_ACTION_RING);
    CHECK(r->has_clip);
    CHECK(r->faces[0].known);
    CHECK(strstr(S.last_json, "\"wake\":\"BUTTON\",\"action\":\"RING\"") != NULL);
}

static void pipe_timer_wake(void)
{
    setup();
    frames(0.0f, 0.0f, 0.0f, 0.0f);
    CHECK_EQ_INT(run(SN_WAKE_TIMER)->action, SN_ACTION_NONE);
    CHECK(strstr(S.last_json, "\"wake\":\"TIMER\"") != NULL);
}

static void pipe_stage_failures_degrade(void)
{
    const sn_event_record_t *r;
    setup();
    /* camera failure: no frames, report NONE */
    frames(0.9f, 0.9f, 0.9f, 0.9f);
    SC.fail_camera = true;
    r = run(SN_WAKE_PIR);
    CHECK_EQ_INT(r->action, SN_ACTION_NONE);
    CHECK_EQ_INT(S.n_frames, 0u);
    CHECK_EQ_INT(S.n_publish, 1u);
    /* camera failure + button still rings */
    CHECK_EQ_INT(run(SN_WAKE_BUTTON)->action, SN_ACTION_RING);
    SC.fail_camera = false;
    /* embed failure: face dropped -> person without usable face -> notify */
    SC.n_faces = 1u;
    face_known(0u, 0u);
    SC.fail_embed = true;
    r = run(SN_WAKE_PIR);
    CHECK_EQ_INT(r->action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(r->n_faces, 0u);
    SC.fail_embed = false;
    /* clip failure: action unchanged, clip_id null */
    face_stranger(0u);
    SC.fail_clip = true;
    r = run(SN_WAKE_PIR);
    CHECK_EQ_INT(r->action, SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK(!r->has_clip);
    CHECK(strstr(S.last_json, "\"clip_id\":null") != NULL);
    /* garbage confidences are sanitized */
    SC.fail_clip = false;
    frames(NAN, 7.0f, -1.0f, INFINITY);
    SC.n_faces = 0u;
    r = run(SN_WAKE_PIR);
    CHECK(r->person_conf <= 1.0f);
    CHECK(r->person_conf >= 0.0f);
}

static void pipe_null_ops_are_safe(void)
{
    sn_infer_ops_t none;
    sn_job_t j = job(SN_WAKE_PIR);
    setup();
    memset(&none, 0, sizeof(none));
    sn_pipe_init(&P, &CFG, &none, &G);
    CHECK(sn_pipe_run(&P, &j) != NULL);
    CHECK_EQ_INT(P.state, SN_PIPE_DONE);
    CHECK_EQ_INT(P.rec.action, SN_ACTION_NONE);
    CHECK(!P.result.published);
    j.wake = SN_WAKE_NONE;
    CHECK(sn_pipe_run(&P, &j) == NULL);
    CHECK(sn_pipe_run(NULL, &j) == NULL);
}

static void pipe_state_trace(void)
{
    static const sn_pipe_state_t want[] = {
        SN_PIPE_CAMERA_START, SN_PIPE_PERSON_DETECT, SN_PIPE_FACE_DETECT,
        SN_PIPE_EMBED_MATCH, SN_PIPE_POLICY, SN_PIPE_CLIP, SN_PIPE_REPORT, SN_PIPE_DONE
    };
    uint32_t i;
    setup();
    frames(0.9f, 0.9f, 0.0f, 0.0f);
    SC.n_faces = 1u;
    face_stranger(0u);
    (void)run(SN_WAKE_PIR);
    CHECK_EQ_INT(P.trace_len, 8u);
    for (i = 0u; i < 8u; i++) {
        CHECK_EQ_INT(P.trace[i], want[i]);
    }
    CHECK_STR(sn_pipe_state_name(P.trace[3]), "EMBED_MATCH");
    /* no-person path skips faces/clip */
    frames(0.0f, 0.0f, 0.0f, 0.0f);
    (void)run(SN_WAKE_PIR);
    CHECK_EQ_INT(P.trace_len, 5u);
    CHECK_EQ_INT(P.trace[2], SN_PIPE_POLICY);
    CHECK_EQ_INT(P.trace[3], SN_PIPE_REPORT);
}

/* energy_mj = boot + active_mw * (elapsed - clip) + clip_mw * clip + uplink_est */
static void pipe_energy_accounting(void)
{
    const sn_power_model_t *pm = &SN_POWER_MODEL_DEFAULT;
    const sn_event_record_t *r;
    uint32_t active;
    uint32_t want;
    setup();
    frames(0.0f, 0.0f, 0.0f, 0.0f);
    r = run(SN_WAKE_PIR);
    active = SIM_LATENCY_DEFAULT.camera_start_ms + 3u * SIM_LATENCY_DEFAULT.frame_ms;
    want = 80u + sn_energy_mw_ms_to_mj(pm->cortex_active_mw, active) + pm->uplink_est_mj;
    CHECK_EQ_INT(r->energy_mj, want);
    CHECK_EQ_INT(P.result.elapsed_ms, active + SIM_LATENCY_DEFAULT.publish_ms);
    CHECK_EQ_INT(P.result.clip_ms, 0u);

    frames(0.9f, 0.9f, 0.0f, 0.0f);
    SC.n_faces = 1u;
    face_stranger(0u);
    r = run(SN_WAKE_PIR);
    active = SIM_LATENCY_DEFAULT.camera_start_ms + 2u * SIM_LATENCY_DEFAULT.frame_ms +
             SIM_LATENCY_DEFAULT.face_detect_ms + SIM_LATENCY_DEFAULT.embed_ms;
    want = 80u + sn_energy_mw_ms_to_mj(pm->cortex_active_mw, active) +
           sn_energy_mw_ms_to_mj(pm->cortex_clip_mw, CFG.clip_ms) + pm->uplink_est_mj;
    CHECK_EQ_INT(r->energy_mj, want);
    CHECK_EQ_INT(P.result.clip_ms, CFG.clip_ms);
    printf("  false wake %u mJ, unknown+clip %u mJ\n",
           (unsigned)(80u + sn_energy_mw_ms_to_mj(pm->cortex_active_mw,
                       SIM_LATENCY_DEFAULT.camera_start_ms + 3u * SIM_LATENCY_DEFAULT.frame_ms) +
                       pm->uplink_est_mj), (unsigned)want);

    /* N6 falls back to the model estimate when the C6 gives no boot energy */
    {
        sn_job_t j = job(SN_WAKE_PIR);
        j.boot_energy_known = false;
        frames(0.0f, 0.0f, 0.0f, 0.0f);
        r = sn_pipe_run(&P, &j);
        active = SIM_LATENCY_DEFAULT.camera_start_ms + 3u * SIM_LATENCY_DEFAULT.frame_ms;
        CHECK_EQ_INT(r->energy_mj, pm->boot_est_mj +
                     sn_energy_mw_ms_to_mj(pm->cortex_active_mw, active) + pm->uplink_est_mj);
    }
}

static void pipe_record_fields(void)
{
    const sn_event_record_t *r;
    setup();
    frames(0.0f, 0.0f, 0.0f, 0.0f);
    r = run(SN_WAKE_PIR);
    CHECK_STR(r->dev, "a1b2c3d4e5f6");
    CHECK_STR(r->fw, "0.1.0");
    CHECK_STR(r->model, "det1-emb1");
    CHECK_EQ_INT(r->ts_ms, 1762450000123ull);
    CHECK_EQ_INT(r->battery_mv, 3800u);
    CHECK_EQ_INT(r->soc_pct, 70u);
    CHECK_EQ_INT(strlen(P.json), P.json_len);
    CHECK(P.result.published);
}

static void policy_table(void)
{
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_PIR, false, 0u, 0u), SN_ACTION_NONE);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_PIR, false, 2u, 2u), SN_ACTION_NONE);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_PIR, true, 0u, 0u), SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_PIR, true, 1u, 1u), SN_ACTION_LOG_KNOWN);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_PIR, true, 2u, 1u), SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_TIMER, true, 1u, 1u), SN_ACTION_LOG_KNOWN);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_RADAR, true, 1u, 0u), SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_BUTTON, false, 0u, 0u), SN_ACTION_RING);
    CHECK_EQ_INT(sn_policy_decide(SN_WAKE_BUTTON, true, 1u, 1u), SN_ACTION_RING);
}

static void clip_id_format(void)
{
    char b[SN_CLIP_ID_MAX];
    char small[10];
    sn_make_clip_id("0123456789ab", 0x0123456789ABCDEFull, b, sizeof(b));
    CHECK_STR(b, "0123456789ab-0123456789abcdef");
    sn_make_clip_id("0123456789ab", 0u, small, sizeof(small));   /* no room: dev prefix only */
    CHECK(strlen(small) < sizeof(small));
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(pipe_person_rule_2_of_4);
    RUN_TEST(pipe_person_early_exit);
    RUN_TEST(pipe_known_person_logs);
    RUN_TEST(pipe_unknown_person_notifies_with_clip);
    RUN_TEST(pipe_person_without_face_is_unknown);
    RUN_TEST(pipe_mixed_known_and_unknown_notifies);
    RUN_TEST(pipe_two_known_logs);
    RUN_TEST(pipe_faces_found_on_second_frame);
    RUN_TEST(pipe_button_rings);
    RUN_TEST(pipe_timer_wake);
    RUN_TEST(pipe_stage_failures_degrade);
    RUN_TEST(pipe_null_ops_are_safe);
    RUN_TEST(pipe_state_trace);
    RUN_TEST(pipe_energy_accounting);
    RUN_TEST(pipe_record_fields);
    RUN_TEST(policy_table);
    RUN_TEST(clip_id_format);
    TEST_END();
}
