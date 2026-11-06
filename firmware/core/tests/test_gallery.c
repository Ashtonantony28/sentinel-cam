#include "sn_test.h"
#include "test_util.h"
#include "sn_gallery.h"
#include "sn_crc32.h"

static uint8_t g_blob[SN_GAL_MAX_BLOB_SIZE];
static int8_t  g_protos[SN_GAL_MAX_PEOPLE][SN_GAL_MAX_PROTOS][SN_EMB_DIM];
static float   g_centers[SN_GAL_MAX_PEOPLE][SN_EMB_DIM];

/* n people, each with n_protos noisy prototypes around a random center. */
static size_t build(uint16_t n, uint8_t n_protos, sn_gal_person_spec_t *specs, uint32_t seed)
{
    uint32_t s = seed;
    uint16_t i;
    uint8_t k;
    size_t len = 0u;
    static const char *names[] = { "Ashton", "Mom", "Dad", "Alex" };
    for (i = 0u; i < n; i++) {
        tu_unit_vec(&s, g_centers[i]);
        for (k = 0u; k < n_protos; k++) {
            tu_noisy_q(&s, g_centers[i], 0.3f, g_protos[i][k]);
        }
        specs[i].id = (uint16_t)(100u + i);
        specs[i].n_protos = n_protos;
        specs[i].threshold_q15 = SN_GAL_DEFAULT_THRESHOLD_Q15;
        specs[i].name = names[i % 4u];
        specs[i].protos = &g_protos[i][0][0];
    }
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, n, &len), SN_GAL_OK);
    return len;
}

static void gallery_roundtrip(void)
{
    sn_gal_person_spec_t specs[3];
    sn_gallery_t g;
    size_t len = build(3u, 5u, specs, 1u);
    CHECK_EQ_INT(len, 8u + 3u * (30u + 5u * 128u) + 4u);
    CHECK(memcmp(g_blob, "SGAL", 4) == 0);
    CHECK_EQ_INT(g_blob[4] | (g_blob[5] << 8), 1);
    CHECK_EQ_INT(g_blob[6] | (g_blob[7] << 8), 3);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
    CHECK_EQ_INT(g.n_people, 3u);
    CHECK_EQ_INT(g.people[1].id, 101u);
    CHECK_EQ_INT(g.people[1].n_protos, 5u);
    CHECK_EQ_INT(g.people[1].threshold_q15, SN_GAL_DEFAULT_THRESHOLD_Q15);
    CHECK_STR(g.people[1].name, "Mom");
    CHECK(memcmp(g.people[2].protos, g_protos[2][0], 5u * 128u) == 0);
    /* byte-level layout of person 0's header */
    CHECK_EQ_INT(g_blob[8] | (g_blob[9] << 8), 100);
    CHECK_EQ_INT(g_blob[10], 5);
    CHECK_EQ_INT(g_blob[11], 0);
    CHECK_EQ_INT((int16_t)(g_blob[12] | (g_blob[13] << 8)), SN_GAL_DEFAULT_THRESHOLD_Q15);
    CHECK(memcmp(&g_blob[14], "Ashton\0\0", 8) == 0);
    CHECK_EQ_INT(sn_crc32(g_blob, len - 4u),
                 (uint32_t)g_blob[len - 4u] | ((uint32_t)g_blob[len - 3u] << 8) |
                 ((uint32_t)g_blob[len - 2u] << 16) | ((uint32_t)g_blob[len - 1u] << 24));
}

static void gallery_max_size_and_empty(void)
{
    sn_gal_person_spec_t specs[SN_GAL_MAX_PEOPLE];
    sn_gallery_t g;
    size_t len = build(SN_GAL_MAX_PEOPLE, SN_GAL_MAX_PROTOS, specs, 7u);
    CHECK_EQ_INT(len, SN_GAL_MAX_BLOB_SIZE);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
    CHECK_EQ_INT(g.n_people, SN_GAL_MAX_PEOPLE);
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), NULL, 0u, &len), SN_GAL_OK);
    CHECK_EQ_INT(len, 12u);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
    CHECK_EQ_INT(g.n_people, 0u);
}

static void put_crc(uint8_t *b, size_t len)
{
    uint32_t c = sn_crc32(b, len - 4u);
    b[len - 4u] = (uint8_t)c; b[len - 3u] = (uint8_t)(c >> 8);
    b[len - 2u] = (uint8_t)(c >> 16); b[len - 1u] = (uint8_t)(c >> 24);
}

static void gallery_parse_errors(void)
{
    sn_gal_person_spec_t specs[2];
    sn_gallery_t g;
    size_t len = build(2u, 3u, specs, 3u);
    uint8_t save;

    CHECK_EQ_INT(sn_gallery_parse(NULL, g_blob, len), SN_GAL_ERR_ARG);
    CHECK_EQ_INT(sn_gallery_parse(&g, NULL, len), SN_GAL_ERR_ARG);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, 11u), SN_GAL_ERR_TRUNCATED);

    g_blob[0] = 'X'; put_crc(g_blob, len);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_MAGIC);
    g_blob[0] = 'S';
    g_blob[4] = 2u; put_crc(g_blob, len);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_VERSION);
    g_blob[4] = 1u;
    g_blob[6] = 17u; put_crc(g_blob, len);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_TOO_MANY_PEOPLE);
    g_blob[6] = 3u; put_crc(g_blob, len);      /* claims 3, holds 2 */
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_TRUNCATED);
    g_blob[6] = 1u; put_crc(g_blob, len);      /* claims 1, holds 2 */
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_TRAILING);
    g_blob[6] = 2u;
    save = g_blob[10];
    g_blob[10] = 21u; put_crc(g_blob, len);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_PROTO_COUNT);
    g_blob[10] = 0u; put_crc(g_blob, len);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_PROTO_COUNT);
    g_blob[10] = save;
    /* CRC is checked before anything else */
    g_blob[0] = 'X';
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_CRC);
    g_blob[0] = 'S';
    /* duplicate id: person 1's id := person 0's */
    {
        size_t p1 = 8u + 30u + 3u * 128u;
        g_blob[p1] = g_blob[8]; g_blob[p1 + 1u] = g_blob[9];
        put_crc(g_blob, len);
        CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_ERR_DUP_ID);
        CHECK_EQ_INT(g.n_people, 0u);       /* cleared on failure */
    }
}

/* Any single bit flip anywhere must be rejected (CRC or magic). */
static void gallery_rejects_every_bit_flip(void)
{
    sn_gal_person_spec_t specs[2];
    sn_gallery_t g;
    size_t len = build(2u, 2u, specs, 5u);
    size_t i;
    int bit;
    int accepted = 0;
    for (i = 0u; i < len; i++) {
        for (bit = 0; bit < 8; bit++) {
            g_blob[i] ^= (uint8_t)(1u << bit);
            if (sn_gallery_parse(&g, g_blob, len) == SN_GAL_OK) {
                accepted++;
            }
            g_blob[i] ^= (uint8_t)(1u << bit);
        }
    }
    CHECK_EQ_INT(accepted, 0);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
}

/* Every truncation (and extension) must be rejected without reading OOB. */
static void gallery_rejects_every_truncation(void)
{
    static uint8_t copy[8u + 2u * (30u + 2u * 128u) + 4u + 8u];
    sn_gal_person_spec_t specs[2];
    sn_gallery_t g;
    size_t len = build(2u, 2u, specs, 9u);
    size_t l;
    int accepted = 0;
    for (l = 0u; l < len; l++) {
        memcpy(copy, g_blob, l);              /* exact-size copy: ASan catches OOB */
        if (sn_gallery_parse(&g, copy, l) == SN_GAL_OK) {
            accepted++;
        }
    }
    CHECK_EQ_INT(accepted, 0);
    memcpy(copy, g_blob, len);
    memset(&copy[len], 0, 8u);
    CHECK(sn_gallery_parse(&g, copy, len + 8u) != SN_GAL_OK);
}

/* Random garbage (with valid header/CRC) must never crash or overread. */
static void gallery_fuzz_random(void)
{
    static uint8_t buf[2048];
    sn_gallery_t g;
    uint32_t s = 0xC0FFEEu;
    int iter;
    int ok = 0;
    for (iter = 0; iter < 3000; iter++) {
        size_t len = 12u + (tu_rng(&s) % 2000u);
        size_t i;
        for (i = 0u; i < len; i++) {
            buf[i] = (uint8_t)tu_rng(&s);
        }
        memcpy(buf, "SGAL\x01\x00", 6);
        buf[6] = (uint8_t)(tu_rng(&s) % 4u);
        buf[7] = 0u;
        if ((iter % 2) == 0) {
            /* structurally consistent: lay out records and size exactly */
            size_t off = 8u;
            uint8_t p;
            for (p = 0u; (p < buf[6]) && ((off + 30u + 4u) < sizeof(buf)); p++) {
                uint8_t np = (uint8_t)(1u + (tu_rng(&s) % 3u));
                if ((off + 30u + ((size_t)np * 128u) + 4u) > sizeof(buf)) {
                    break;
                }
                buf[off] = p;                 /* unique ids */
                buf[off + 1u] = 0u;
                buf[off + 2u] = np;
                off += 30u + ((size_t)np * 128u);
            }
            len = off + 4u;
        }
        put_crc(buf, len);
        if (sn_gallery_parse(&g, buf, len) == SN_GAL_OK) {
            ok++;
            CHECK(g.n_people <= 3u);
        }
    }
    printf("  fuzz: %d/3000 random blobs parsed OK\n", ok);
    CHECK(ok > 1000);
}

static void gallery_writer_errors(void)
{
    sn_gal_person_spec_t specs[2];
    size_t len;
    (void)build(2u, 2u, specs, 11u);
    CHECK_EQ_INT(sn_gallery_write(g_blob, 100u, specs, 2u, &len), SN_GAL_ERR_NO_SPACE);
    CHECK_EQ_INT(len, 0u);
    specs[1].id = specs[0].id;
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, 2u, &len), SN_GAL_ERR_DUP_ID);
    specs[1].id = 5u;
    specs[1].n_protos = 21u;
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, 2u, &len), SN_GAL_ERR_PROTO_COUNT);
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, 17u, &len), SN_GAL_ERR_TOO_MANY_PEOPLE);
    CHECK_EQ_INT(sn_gallery_write(NULL, 10u, specs, 1u, &len), SN_GAL_ERR_ARG);
    specs[0].name = "a-very-long-name-longer-than-24-bytes";
    specs[1].n_protos = 1u;
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, 2u, &len), SN_GAL_OK);
    {
        sn_gallery_t g;
        CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
        CHECK_EQ_INT(strlen(g.people[0].name), 24u);    /* 24 bytes, no NUL in blob */
        CHECK(strncmp(g.people[0].name, "a-very-long-name-longer-", 24) == 0);
    }
    CHECK_STR(sn_gal_status_name(SN_GAL_ERR_CRC), "BAD_CRC");
    specs[1].n_protos = 0u;
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, 2u, &len), SN_GAL_ERR_PROTO_COUNT);
}

static void quantize_rule(void)
{
    float in[SN_EMB_DIM];
    int8_t q[SN_EMB_DIM];
    memset(in, 0, sizeof(in));
    in[0] = 1.0f;            /* 127   */
    in[1] = -1.0f;           /* -127  */
    in[2] = 2.0f;            /* clamp 127 */
    in[3] = -5.0f;           /* clamp -127 */
    in[4] = 0.5f / 127.0f;   /* 0.5   -> 1 (half away from zero) */
    in[5] = 1.5f / 127.0f;   /* 1.5   -> 2 */
    in[6] = 2.5f / 127.0f;   /* 2.5   -> 3 */
    in[7] = -0.5f / 127.0f;  /* -0.5  -> -1 */
    in[8] = 0.1f;            /* 12.7  -> 13 */
    in[9] = NAN;             /* -> 0 */
    in[10] = INFINITY;       /* -> 127 (clamped) */
    in[11] = -INFINITY;      /* -> -127 */
    sn_embed_quantize(in, q);
    CHECK_EQ_INT(q[0], 127); CHECK_EQ_INT(q[1], -127);
    CHECK_EQ_INT(q[2], 127); CHECK_EQ_INT(q[3], -127);
    CHECK_EQ_INT(q[4], 1);   CHECK_EQ_INT(q[5], 2);
    CHECK_EQ_INT(q[6], 3);   CHECK_EQ_INT(q[7], -1);
    CHECK_EQ_INT(q[8], 13);  CHECK_EQ_INT(q[9], 0);
    CHECK_EQ_INT(q[10], 127); CHECK_EQ_INT(q[11], -127);
    CHECK_EQ_INT(q[12], 0);
}

static void cosine_basics(void)
{
    int8_t a[SN_EMB_DIM];
    int8_t b[SN_EMB_DIM];
    int8_t z[SN_EMB_DIM];
    uint32_t i;
    for (i = 0u; i < SN_EMB_DIM; i++) {
        a[i] = (int8_t)((i % 2u) ? 127 : -127);
        b[i] = (int8_t)(-a[i]);
        z[i] = 0;
    }
    CHECK_NEAR(sn_int8_cosine(a, a, SN_EMB_DIM), 1.0, 1e-12);
    CHECK_NEAR(sn_int8_cosine(a, b, SN_EMB_DIM), -1.0, 1e-12);
    CHECK_NEAR(sn_int8_cosine(a, z, SN_EMB_DIM), 0.0, 0.0);
    CHECK_EQ_INT(sn_int8_dot(a, a, SN_EMB_DIM), 128 * 127 * 127);   /* no overflow */
    /* scale invariance in int8 */
    for (i = 0u; i < SN_EMB_DIM; i++) {
        b[i] = (int8_t)(a[i] / 2);
    }
    CHECK_NEAR(sn_int8_cosine(a, b, SN_EMB_DIM), 1.0, 1e-12);
}

/* Hand-built 2-person gallery where scores are exactly controllable. */
static int8_t h_protos[3][SN_EMB_DIM];
static sn_gallery_t h_gal;

static void hand_gallery(int16_t thr0, int16_t thr1, uint16_t n)
{
    sn_gal_person_spec_t specs[2];
    size_t len;
    memset(h_protos, 0, sizeof(h_protos));
    h_protos[0][0] = 100;            /* person 0: e0 */
    h_protos[1][1] = 100;            /* person 1: e1 */
    specs[0].id = 1u; specs[0].n_protos = 1u; specs[0].threshold_q15 = thr0;
    specs[0].name = "p0"; specs[0].protos = h_protos[0];
    specs[1].id = 2u; specs[1].n_protos = 1u; specs[1].threshold_q15 = thr1;
    specs[1].name = "p1"; specs[1].protos = h_protos[1];
    CHECK_EQ_INT(sn_gallery_write(g_blob, sizeof(g_blob), specs, n, &len), SN_GAL_OK);
    CHECK_EQ_INT(sn_gallery_parse(&h_gal, g_blob, len), SN_GAL_OK);
}

/* query = (x, y, rest) so cos with e0 = x/|q|, with e1 = y/|q|. */
static void query(int8_t x, int8_t y, int8_t rest, int8_t q[SN_EMB_DIM])
{
    memset(q, 0, SN_EMB_DIM);
    q[0] = x; q[1] = y; q[2] = rest;
}

static void match_rules(void)
{
    int8_t q[SN_EMB_DIM];
    sn_match_t m;
    hand_gallery(SN_GAL_DEFAULT_THRESHOLD_Q15, SN_GAL_DEFAULT_THRESHOLD_Q15, 2u);

    /* (3,0,4): cos0 = 0.6, cos1 = 0 -> match person 1 (id 1) */
    query(3, 0, 4, q);
    sn_gallery_match(&h_gal, q, &m);
    CHECK(m.matched); CHECK_EQ_INT(m.id, 1u);
    CHECK_NEAR(m.best_score, 0.6, 1e-12); CHECK_NEAR(m.second_score, 0.0, 1e-12);

    /* (1,0,2): cos0 = 0.447 < 0.55 -> unknown */
    query(1, 0, 2, q);
    sn_gallery_match(&h_gal, q, &m);
    CHECK(!m.matched); CHECK_EQ_INT(m.best_idx, 0);

    /* (10,9,0): cos0=0.743, cos1=0.669 -> margin 0.074 >= 0.05 -> match */
    query(10, 9, 0, q);
    sn_gallery_match(&h_gal, q, &m);
    CHECK(m.matched); CHECK_EQ_INT(m.id, 1u);

    /* (20,19,0): cos0=0.725, cos1=0.689 -> margin 0.036 < 0.05 -> unknown */
    query(20, 19, 0, q);
    sn_gallery_match(&h_gal, q, &m);
    CHECK(!m.matched);
    CHECK(m.best_score - m.second_score < 0.05);

    /* person 1 wins */
    query(0, 50, 10, q);
    sn_gallery_match(&h_gal, q, &m);
    CHECK(m.matched); CHECK_EQ_INT(m.id, 2u); CHECK_EQ_INT(m.best_idx, 1);
}

static void match_per_person_threshold(void)
{
    int8_t q[SN_EMB_DIM];
    sn_match_t m;
    /* person 0 strict (0.9), person 1 lenient (0.3) */
    hand_gallery(29491, 9830, 2u);
    query(3, 0, 4, q);                         /* cos0 = 0.6 < 0.9 */
    sn_gallery_match(&h_gal, q, &m);
    CHECK(!m.matched);
    query(0, 1, 2, q);                         /* cos1 = 0.447 >= 0.3 */
    sn_gallery_match(&h_gal, q, &m);
    CHECK(m.matched); CHECK_EQ_INT(m.id, 2u);
}

static void match_threshold_boundary(void)
{
    int8_t q[SN_EMB_DIM];
    sn_match_t m;
    /* threshold exactly 0.6 in q15 is 19660.8; 19661 -> 0.600006 > 0.6 */
    hand_gallery(19661, SN_GAL_DEFAULT_THRESHOLD_Q15, 2u);
    query(3, 0, 4, q);                         /* exactly 0.6 */
    sn_gallery_match(&h_gal, q, &m);
    CHECK(!m.matched);
    hand_gallery(19660, SN_GAL_DEFAULT_THRESHOLD_Q15, 2u);   /* 0.59998 */
    sn_gallery_match(&h_gal, q, &m);
    CHECK(m.matched);
    /* q15 = 16384 = exactly 0.5; cos = 0.5 exactly via (1,0,sqrt3)? use (5,0,..)
     * cos0 = 0.5 needs |q| = 2*q0: (1, 0, 1.732) isn't integer, so check via
     * the >= rule on a 3-4-5 triangle with threshold 0.6 = 19660.8 above. */
}

static void match_single_person_and_empty(void)
{
    int8_t q[SN_EMB_DIM];
    sn_match_t m;
    sn_gallery_t empty;
    hand_gallery(SN_GAL_DEFAULT_THRESHOLD_Q15, SN_GAL_DEFAULT_THRESHOLD_Q15, 1u);
    query(3, 0, 4, q);
    sn_gallery_match(&h_gal, q, &m);
    CHECK(m.matched);                          /* no second best: margin vacuous */
    CHECK_NEAR(m.second_score, -1.0, 0.0);
    sn_gallery_clear(&empty);
    sn_gallery_match(&empty, q, &m);
    CHECK(!m.matched); CHECK_EQ_INT(m.best_idx, -1);
    sn_gallery_match(NULL, q, &m);
    CHECK(!m.matched);
    /* zero query vector never matches */
    hand_gallery(SN_GAL_DEFAULT_THRESHOLD_Q15, SN_GAL_DEFAULT_THRESHOLD_Q15, 2u);
    memset(q, 0, sizeof(q));
    sn_gallery_match(&h_gal, q, &m);
    CHECK(!m.matched);
}

static void match_max_over_prototypes(void)
{
    sn_gal_person_spec_t specs[2];
    sn_gallery_t g;
    size_t len;
    uint32_t s = 77u;
    int8_t q[SN_EMB_DIM];
    sn_match_t m;
    double best = -2.0;
    uint8_t k;
    len = build(2u, 6u, specs, 21u);
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
    tu_noisy_q(&s, g_centers[1], 0.2f, q);
    for (k = 0u; k < 6u; k++) {
        double c = sn_int8_cosine(q, g_protos[1][k], SN_EMB_DIM);
        best = (c > best) ? c : best;
    }
    CHECK_NEAR(sn_gallery_person_score(&g.people[1], q), best, 1e-12);
    sn_gallery_match(&g, q, &m);
    CHECK(m.matched); CHECK_EQ_INT(m.id, 101u);
    CHECK_NEAR(m.best_score, best, 1e-12);
}

/* Statistical sanity: noisy views of enrolled people match, strangers don't. */
static void match_population(void)
{
    sn_gal_person_spec_t specs[SN_GAL_MAX_PEOPLE];
    sn_gallery_t g;
    size_t len = build(SN_GAL_MAX_PEOPLE, 10u, specs, 99u);
    uint32_t s = 1234u;
    int i;
    int tp = 0;
    int fp = 0;
    int wrong = 0;
    CHECK_EQ_INT(sn_gallery_parse(&g, g_blob, len), SN_GAL_OK);
    for (i = 0; i < 400; i++) {
        int8_t q[SN_EMB_DIM];
        sn_match_t m;
        uint16_t who = (uint16_t)(i % (int)SN_GAL_MAX_PEOPLE);
        tu_noisy_q(&s, g_centers[who], 0.3f, q);
        sn_gallery_match(&g, q, &m);
        if (m.matched) {
            tp++;
            if (m.id != (uint16_t)(100u + who)) { wrong++; }
        }
        {
            float stranger[SN_EMB_DIM];
            tu_unit_vec(&s, stranger);
            sn_embed_quantize(stranger, q);
            sn_gallery_match(&g, q, &m);
            if (m.matched) { fp++; }
        }
    }
    printf("  population: TAR %d/400, FAR %d/400, misid %d\n", tp, fp, wrong);
    CHECK(tp >= 390);
    CHECK_EQ_INT(fp, 0);
    CHECK_EQ_INT(wrong, 0);
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(gallery_roundtrip);
    RUN_TEST(gallery_max_size_and_empty);
    RUN_TEST(gallery_parse_errors);
    RUN_TEST(gallery_rejects_every_bit_flip);
    RUN_TEST(gallery_rejects_every_truncation);
    RUN_TEST(gallery_fuzz_random);
    RUN_TEST(gallery_writer_errors);
    RUN_TEST(quantize_rule);
    RUN_TEST(cosine_basics);
    RUN_TEST(match_rules);
    RUN_TEST(match_per_person_threshold);
    RUN_TEST(match_threshold_boundary);
    RUN_TEST(match_single_person_and_empty);
    RUN_TEST(match_max_over_prototypes);
    RUN_TEST(match_population);
    TEST_END();
}
