#include "sn_test.h"
#include "sn_event.h"

static void fill_base(sn_event_record_t *e)
{
    memset(e, 0, sizeof(*e));
    sn_strlcpy(e->dev, "a1b2c3d4e5f6", sizeof(e->dev));
    e->ts_ms = 1762450000123ull;
    e->wake = SN_WAKE_PIR;
    e->action = SN_ACTION_NONE;
    e->person_conf = 0.25f;
    e->battery_mv = 3912u;
    e->soc_pct = 81u;
    sn_strlcpy(e->fw, "0.1.0", sizeof(e->fw));
    sn_strlcpy(e->model, "det-v3+emb-v2", sizeof(e->model));
    e->energy_mj = 1234u;
}

static void event_json_none(void)
{
    sn_event_record_t e;
    char b[SN_EVENT_JSON_MAX];
    size_t n;
    fill_base(&e);
    n = sn_event_to_json(&e, b, sizeof(b));
    CHECK(n > 0u);
    CHECK_EQ_INT(n, strlen(b));
    CHECK_STR(b, "{\"v\":1,\"dev\":\"a1b2c3d4e5f6\",\"ts\":1762450000123,\"wake\":\"PIR\","
                 "\"action\":\"NONE\",\"person_conf\":0.25,\"faces\":[],\"clip_id\":null,"
                 "\"battery_mv\":3912,\"soc_pct\":81,\"fw\":\"0.1.0\",\"model\":\"det-v3+emb-v2\","
                 "\"energy_mj\":1234}");
}

static void event_json_faces_and_clip(void)
{
    sn_event_record_t e;
    char b[SN_EVENT_JSON_MAX];
    fill_base(&e);
    e.wake = SN_WAKE_BUTTON;
    e.action = SN_ACTION_RING;
    e.person_conf = 0.91f;
    e.n_faces = 2u;
    e.faces[0].known = true;  e.faces[0].id = 7u; e.faces[0].score = 0.8125f;
    e.faces[1].known = false; e.faces[1].score = 0.31f;
    e.has_clip = true;
    sn_strlcpy(e.clip_id, "a1b2c3d4e5f6-0000019a5bd0a35b", sizeof(e.clip_id));
    CHECK(sn_event_to_json(&e, b, sizeof(b)) > 0u);
    CHECK_STR(b, "{\"v\":1,\"dev\":\"a1b2c3d4e5f6\",\"ts\":1762450000123,\"wake\":\"BUTTON\","
                 "\"action\":\"RING\",\"person_conf\":0.91,\"faces\":[{\"id\":7,\"score\":0.8125},"
                 "{\"id\":null,\"score\":0.31}],\"clip_id\":\"a1b2c3d4e5f6-0000019a5bd0a35b\","
                 "\"battery_mv\":3912,\"soc_pct\":81,\"fw\":\"0.1.0\",\"model\":\"det-v3+emb-v2\","
                 "\"energy_mj\":1234}");
}

static void event_json_all_enum_names(void)
{
    static const char *wakes[] = { "PIR", "RADAR", "BUTTON", "TIMER" };
    static const char *acts[] = { "NONE", "LOG_KNOWN", "NOTIFY_UNKNOWN_WITH_CLIP", "RING" };
    sn_event_record_t e;
    char b[SN_EVENT_JSON_MAX];
    char want[64];
    int w;
    int a;
    for (w = 0; w < 4; w++) {
        for (a = 0; a < 4; a++) {
            fill_base(&e);
            e.wake = (sn_wake_src_t)(w + 1);
            e.action = (sn_action_t)a;
            CHECK(sn_event_to_json(&e, b, sizeof(b)) > 0u);
            snprintf(want, sizeof(want), "\"wake\":\"%s\",\"action\":\"%s\"", wakes[w], acts[a]);
            CHECK(strstr(b, want) != NULL);
        }
    }
}

static void event_json_rejects_invalid(void)
{
    sn_event_record_t e;
    char b[SN_EVENT_JSON_MAX];
    fill_base(&e);
    e.wake = SN_WAKE_NONE;
    CHECK_EQ_INT(sn_event_to_json(&e, b, sizeof(b)), 0u);
    CHECK_STR(b, "");
    fill_base(&e);
    e.action = (sn_action_t)9;
    CHECK_EQ_INT(sn_event_to_json(&e, b, sizeof(b)), 0u);
    fill_base(&e);
    CHECK_EQ_INT(sn_event_to_json(&e, b, 40u), 0u);   /* too small */
    CHECK_EQ_INT(sn_event_to_json(NULL, b, sizeof(b)), 0u);
}

/* Max-size record must fit SN_EVENT_JSON_MAX. */
static void event_json_worst_case_fits(void)
{
    sn_event_record_t e;
    char b[SN_EVENT_JSON_MAX];
    uint32_t i;
    size_t n;
    fill_base(&e);
    e.ts_ms = UINT64_MAX;
    e.wake = SN_WAKE_BUTTON;
    e.action = SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP;
    e.person_conf = 1.0f;
    e.n_faces = 200u;                         /* clamped to SN_MAX_FACES */
    for (i = 0u; i < SN_MAX_FACES; i++) {
        e.faces[i].known = true;
        e.faces[i].id = 65535u;
        e.faces[i].score = 0.99999f;
    }
    e.has_clip = true;
    memset(e.clip_id, '"', SN_CLIP_ID_MAX - 1u);   /* every char escaped */
    e.clip_id[SN_CLIP_ID_MAX - 1u] = '\0';
    memset(e.fw, '\\', SN_VERSION_STR_MAX - 1u);
    e.fw[SN_VERSION_STR_MAX - 1u] = '\0';
    memset(e.model, '\x01', SN_VERSION_STR_MAX - 1u); /* \u0001 each */
    e.model[SN_VERSION_STR_MAX - 1u] = '\0';
    e.battery_mv = 65535u;
    e.soc_pct = 255u;
    e.energy_mj = UINT32_MAX;
    n = sn_event_to_json(&e, b, sizeof(b));
    CHECK(n > 0u);
    CHECK(n < SN_EVENT_JSON_MAX);
    printf("  worst-case event JSON: %u bytes\n", (unsigned)n);
}

static void strlcpy_bounds(void)
{
    char d[4];
    sn_strlcpy(d, "abcdef", sizeof(d));
    CHECK_STR(d, "abc");
    sn_strlcpy(d, NULL, sizeof(d));
    CHECK_STR(d, "");
    sn_strlcpy(d, "x", sizeof(d));
    CHECK_STR(d, "x");
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(event_json_none);
    RUN_TEST(event_json_faces_and_clip);
    RUN_TEST(event_json_all_enum_names);
    RUN_TEST(event_json_rejects_invalid);
    RUN_TEST(event_json_worst_case_fits);
    RUN_TEST(strlcpy_bounds);
    TEST_END();
}
