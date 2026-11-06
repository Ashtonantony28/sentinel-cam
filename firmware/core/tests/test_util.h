/* test_util.h - deterministic vectors for tests (host only). */
#ifndef TEST_UTIL_H
#define TEST_UTIL_H
#include <stdint.h>
#include <math.h>
#include "sn_gallery.h"

static inline uint32_t tu_rng(uint32_t *s)
{
    /* xorshift32 */
    uint32_t x = *s;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    *s = x;
    return x;
}

/* Uniform in [-1, 1). */
static inline float tu_uniform(uint32_t *s)
{
    return ((float)(tu_rng(s) >> 8) / 8388608.0f) - 1.0f;
}

/* Random L2-normalized float vector. */
static inline void tu_unit_vec(uint32_t *s, float v[SN_EMB_DIM])
{
    double n = 0.0;
    uint32_t i;
    for (i = 0u; i < SN_EMB_DIM; i++) {
        v[i] = tu_uniform(s);
        n += (double)v[i] * (double)v[i];
    }
    n = sqrt(n);
    for (i = 0u; i < SN_EMB_DIM; i++) {
        v[i] = (float)((double)v[i] / n);
    }
}

/* out = normalize(base + noise * random), quantized. */
static inline void tu_noisy_q(uint32_t *s, const float base[SN_EMB_DIM], float noise,
                              int8_t out[SN_EMB_DIM])
{
    float r[SN_EMB_DIM];
    float v[SN_EMB_DIM];
    double n = 0.0;
    uint32_t i;
    tu_unit_vec(s, r);
    for (i = 0u; i < SN_EMB_DIM; i++) {
        v[i] = base[i] + (noise * r[i]);
        n += (double)v[i] * (double)v[i];
    }
    n = sqrt(n);
    for (i = 0u; i < SN_EMB_DIM; i++) {
        v[i] = (float)((double)v[i] / n);
    }
    sn_embed_quantize(v, out);
}

#endif
