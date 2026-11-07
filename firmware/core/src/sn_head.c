#include "sn_head.h"
#include <math.h>
#include <string.h>

static float le_f32(const uint8_t *p)
{
    uint32_t u = (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) |
                 ((uint32_t)p[3] << 24);
    float f;
    memcpy(&f, &u, sizeof(f));
    return f;
}

bool sn_head_load(sn_head_t *h, const uint8_t *blob, size_t len)
{
    sn_head_t t;
    uint32_t i;
    if ((h == NULL) || (blob == NULL) || (len != SN_HEAD_BYTES)) {
        return false;
    }
    for (i = 0u; i < SN_HEAD_DIM; i++) {
        t.w[i] = le_f32(&blob[4u * i]);
        if (!isfinite(t.w[i])) {
            return false;
        }
    }
    t.b = le_f32(&blob[4u * SN_HEAD_DIM]);
    if (!isfinite(t.b)) {
        return false;
    }
    *h = t;
    return true;
}

double sn_head_logit(const sn_head_t *h, const float x[SN_HEAD_DIM])
{
    double z = (double)h->b;
    uint32_t i;
    for (i = 0u; i < SN_HEAD_DIM; i++) {
        z += (double)h->w[i] * (double)x[i];
    }
    return z;
}

double sn_head_prob(const sn_head_t *h, const float x[SN_HEAD_DIM])
{
    double z = sn_head_logit(h, x);
    double e;
    if (isnan(z)) {
        return 0.5;
    }
    e = exp(-fabs(z));                 /* in (0, 1]: never overflows */
    return (z >= 0.0) ? (1.0 / (1.0 + e)) : (e / (1.0 + e));
}
