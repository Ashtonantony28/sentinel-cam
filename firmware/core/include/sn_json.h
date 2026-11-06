/*
 * sn_json.h - bounded, allocation-free JSON writer.
 * Overflow is sticky: once the buffer is full every call is a no-op and
 * sn_json_finish() returns false. Output is always NUL-terminated (cap > 0).
 */
#ifndef SN_JSON_H
#define SN_JSON_H
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define SN_JSON_MAX_DEPTH 8u

typedef struct {
    char    *buf;
    size_t   cap;
    size_t   len;
    bool     overflow;
    bool     error;                       /* misuse: depth / key placement */
    uint8_t  depth;
    bool     first[SN_JSON_MAX_DEPTH];    /* no element written yet at depth */
    bool     after_key;
} sn_json_t;

void sn_json_init(sn_json_t *j, char *buf, size_t cap);
void sn_json_obj_begin(sn_json_t *j);
void sn_json_obj_end(sn_json_t *j);
void sn_json_arr_begin(sn_json_t *j);
void sn_json_arr_end(sn_json_t *j);
void sn_json_key(sn_json_t *j, const char *key);
void sn_json_str(sn_json_t *j, const char *s);    /* NULL -> null */
void sn_json_i64(sn_json_t *j, int64_t v);
void sn_json_u64(sn_json_t *j, uint64_t v);
/* Fixed-point with `decimals` (0..6) digits, trailing zeros trimmed to at
 * least one decimal. Non-finite or |v| >= 1e12 -> null. */
void sn_json_float(sn_json_t *j, double v, uint8_t decimals);
void sn_json_bool(sn_json_t *j, bool v);
void sn_json_null(sn_json_t *j);
/* Returns true if the document is complete (depth 0), fit, and well formed. */
bool sn_json_finish(sn_json_t *j);

#endif
