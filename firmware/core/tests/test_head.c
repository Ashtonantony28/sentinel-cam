#include "sn_test.h"
#include "sn_head.h"

static void put_f32(uint8_t *p, float f)
{
    uint32_t u;
    memcpy(&u, &f, sizeof(u));
    p[0] = (uint8_t)u;
    p[1] = (uint8_t)(u >> 8);
    p[2] = (uint8_t)(u >> 16);
    p[3] = (uint8_t)(u >> 24);
}

static void blob_of(uint8_t *b, const float *w, float bias)
{
    uint32_t i;
    for (i = 0u; i < SN_HEAD_DIM; i++) {
        put_f32(&b[4u * i], w[i]);
    }
    put_f32(&b[4u * SN_HEAD_DIM], bias);
}

static void head_load_layout(void)
{
    uint8_t b[SN_HEAD_BYTES];
    float w[SN_HEAD_DIM];
    sn_head_t h;
    uint32_t i;
    for (i = 0u; i < SN_HEAD_DIM; i++) {
        w[i] = (float)i * 0.25f - 8.0f;
    }
    blob_of(b, w, 1.5f);
    /* w[0] = -8.0f = 0xC1000000, little-endian */
    CHECK_EQ_INT(b[0], 0x00);
    CHECK_EQ_INT(b[3], 0xC1);
    CHECK(sn_head_load(&h, b, sizeof(b)));
    for (i = 0u; i < SN_HEAD_DIM; i++) {
        CHECK(h.w[i] == w[i]);
    }
    CHECK(h.b == 1.5f);
}

static void head_load_rejects(void)
{
    uint8_t b[SN_HEAD_BYTES + 1u];
    float w[SN_HEAD_DIM];
    sn_head_t h;
    sn_head_t before;
    memset(w, 0, sizeof(w));
    blob_of(b, w, 0.0f);
    memset(&h, 0x5A, sizeof(h));
    before = h;
    CHECK(!sn_head_load(&h, b, SN_HEAD_BYTES - 1u));
    CHECK(!sn_head_load(&h, b, SN_HEAD_BYTES + 1u));
    CHECK(!sn_head_load(NULL, b, SN_HEAD_BYTES));
    CHECK(!sn_head_load(&h, NULL, SN_HEAD_BYTES));
    put_f32(&b[4u * 10u], NAN);
    CHECK(!sn_head_load(&h, b, SN_HEAD_BYTES));
    put_f32(&b[4u * 10u], 0.0f);
    put_f32(&b[4u * SN_HEAD_DIM], INFINITY);
    CHECK(!sn_head_load(&h, b, SN_HEAD_BYTES));
    CHECK(memcmp(&h, &before, sizeof(h)) == 0);           /* untouched on failure */
}

static void head_logistic(void)
{
    uint8_t b[SN_HEAD_BYTES];
    float w[SN_HEAD_DIM];
    float x[SN_HEAD_DIM];
    sn_head_t h;
    uint32_t i;
    for (i = 0u; i < SN_HEAD_DIM; i++) {
        w[i] = (i == 0u) ? 1.0f : 0.0f;
        x[i] = 0.0f;
    }
    blob_of(b, w, 0.0f);
    CHECK(sn_head_load(&h, b, sizeof(b)));
    CHECK_NEAR(sn_head_prob(&h, x), 0.5, 1e-15);
    x[0] = 2.0f;
    CHECK_NEAR(sn_head_prob(&h, x), 1.0 / (1.0 + exp(-2.0)), 1e-15);
    x[0] = -2.0f;
    CHECK_NEAR(sn_head_prob(&h, x), 1.0 / (1.0 + exp(2.0)), 1e-15);
    x[0] = 1000.0f;                                         /* no overflow */
    CHECK(sn_head_prob(&h, x) == 1.0);
    x[0] = -1000.0f;
    CHECK(sn_head_prob(&h, x) >= 0.0);
    CHECK(sn_head_prob(&h, x) < 1e-300);
    x[0] = 3.0f;
    CHECK_NEAR(sn_head_logit(&h, x), 3.0, 0.0);
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(head_load_layout);
    RUN_TEST(head_load_rejects);
    RUN_TEST(head_logistic);
    TEST_END();
}
