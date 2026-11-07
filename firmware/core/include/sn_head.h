/*
 * sn_head.h - false-alert head (INTERFACES.md 5): logistic regression on the
 * 64-d person-detector feature, P(real person event) = sigmoid(w.x + b).
 *
 * Wire/flash form (what fl/ sends after a round): 65 little-endian IEEE-754
 * fp32, w[0..63] then b (260 bytes). The loader decodes byte by byte, so it
 * is independent of host endianness and alignment, and rejects any
 * non-finite parameter (fl/ refuses to serialize those too).
 */
#ifndef SN_HEAD_H
#define SN_HEAD_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define SN_HEAD_DIM   64u
#define SN_HEAD_BYTES ((SN_HEAD_DIM + 1u) * 4u)   /* 260 */

typedef struct {
    float w[SN_HEAD_DIM];
    float b;
} sn_head_t;

/* Returns false (and leaves *h untouched) if len != SN_HEAD_BYTES, a pointer
 * is NULL, or any value is NaN/inf. */
bool sn_head_load(sn_head_t *h, const uint8_t *blob, size_t len);

/* w.x + b, accumulated in double. */
double sn_head_logit(const sn_head_t *h, const float x[SN_HEAD_DIM]);

/* sigmoid(logit), numerically stable for large |logit|. */
double sn_head_prob(const sn_head_t *h, const float x[SN_HEAD_DIM]);

#endif
