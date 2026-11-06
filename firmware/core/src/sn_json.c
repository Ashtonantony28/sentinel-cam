#include "sn_json.h"
#include <math.h>

static void put_c(sn_json_t *j, char c)
{
    if (j->overflow) {
        return;
    }
    if ((j->len + 1u) >= j->cap) {   /* keep room for NUL */
        j->overflow = true;
        return;
    }
    j->buf[j->len] = c;
    j->len++;
    j->buf[j->len] = '\0';
}

static void put_s(sn_json_t *j, const char *s)
{
    while ((*s != '\0') && !j->overflow) {
        put_c(j, *s);
        s++;
    }
}

/* Comma/placement handling before any value or key. */
static void pre_value(sn_json_t *j)
{
    if (j->after_key) {
        j->after_key = false;
        return;
    }
    if (j->depth > 0u) {
        if (!j->first[j->depth - 1u]) {
            put_c(j, ',');
        }
        j->first[j->depth - 1u] = false;
    }
}

void sn_json_init(sn_json_t *j, char *buf, size_t cap)
{
    uint32_t i;
    if (j == NULL) {
        return;
    }
    j->buf = buf;
    j->cap = cap;
    j->len = 0u;
    j->overflow = (buf == NULL) || (cap == 0u);
    j->error = false;
    j->depth = 0u;
    j->after_key = false;
    for (i = 0u; i < SN_JSON_MAX_DEPTH; i++) {
        j->first[i] = true;
    }
    if (!j->overflow) {
        buf[0] = '\0';
    }
}

static void open_(sn_json_t *j, char c)
{
    pre_value(j);
    if (j->depth >= SN_JSON_MAX_DEPTH) {
        j->error = true;
        return;
    }
    put_c(j, c);
    j->first[j->depth] = true;
    j->depth++;
}

static void close_(sn_json_t *j, char c)
{
    if ((j->depth == 0u) || j->after_key) {
        j->error = true;
        return;
    }
    j->depth--;
    put_c(j, c);
}

void sn_json_obj_begin(sn_json_t *j) { open_(j, '{'); }
void sn_json_obj_end(sn_json_t *j)   { close_(j, '}'); }
void sn_json_arr_begin(sn_json_t *j) { open_(j, '['); }
void sn_json_arr_end(sn_json_t *j)   { close_(j, ']'); }

static void put_escaped(sn_json_t *j, const char *s)
{
    static const char hex[] = "0123456789abcdef";
    put_c(j, '"');
    while ((*s != '\0') && !j->overflow) {
        unsigned char c = (unsigned char)*s;
        if (c == (unsigned char)'"') {
            put_s(j, "\\\"");
        } else if (c == (unsigned char)'\\') {
            put_s(j, "\\\\");
        } else if (c == (unsigned char)'\n') {
            put_s(j, "\\n");
        } else if (c == (unsigned char)'\r') {
            put_s(j, "\\r");
        } else if (c == (unsigned char)'\t') {
            put_s(j, "\\t");
        } else if ((c < 0x20u) || (c == 0x7Fu)) {
            put_s(j, "\\u00");
            put_c(j, hex[(c >> 4) & 0x0Fu]);
            put_c(j, hex[c & 0x0Fu]);
        } else {
            put_c(j, (char)c);   /* UTF-8 bytes pass through */
        }
        s++;
    }
    put_c(j, '"');
}

void sn_json_key(sn_json_t *j, const char *key)
{
    if ((j->depth == 0u) || j->after_key || (key == NULL)) {
        j->error = true;
        return;
    }
    pre_value(j);
    put_escaped(j, key);
    put_c(j, ':');
    j->after_key = true;
}

void sn_json_str(sn_json_t *j, const char *s)
{
    if (s == NULL) {
        sn_json_null(j);
        return;
    }
    pre_value(j);
    put_escaped(j, s);
}

void sn_json_u64(sn_json_t *j, uint64_t v)
{
    char tmp[21];
    uint32_t n = 0u;
    pre_value(j);
    do {
        tmp[n] = (char)('0' + (int)(v % 10u));
        n++;
        v /= 10u;
    } while (v != 0u);
    while (n > 0u) {
        n--;
        put_c(j, tmp[n]);
    }
}

void sn_json_i64(sn_json_t *j, int64_t v)
{
    uint64_t mag;
    if (v < 0) {
        pre_value(j);
        put_c(j, '-');
        j->after_key = true;   /* suppress comma for the magnitude */
        mag = (uint64_t)(-(v + 1)) + 1u;
    } else {
        mag = (uint64_t)v;
    }
    sn_json_u64(j, mag);
}

void sn_json_float(sn_json_t *j, double v, uint8_t decimals)
{
    static const uint64_t pow10[7] = { 1u, 10u, 100u, 1000u, 10000u, 100000u, 1000000u };
    uint64_t scaled;
    uint64_t ip;
    uint64_t fp;
    char frac[7];
    uint32_t nd;
    uint32_t i;
    bool neg;

    if (decimals > 6u) {
        decimals = 6u;
    }
    if (!isfinite(v) || (fabs(v) >= 1e12)) {
        sn_json_null(j);
        return;
    }
    neg = (v < 0.0);
    scaled = (uint64_t)floor((fabs(v) * (double)pow10[decimals]) + 0.5);
    ip = scaled / pow10[decimals];
    fp = scaled % pow10[decimals];
    pre_value(j);
    if (neg && (scaled != 0u)) {
        put_c(j, '-');
    }
    j->after_key = true;
    sn_json_u64(j, ip);
    put_c(j, '.');
    nd = decimals;
    for (i = 0u; i < nd; i++) {
        frac[nd - 1u - i] = (char)('0' + (int)(fp % 10u));
        fp /= 10u;
    }
    while ((nd > 1u) && (frac[nd - 1u] == '0')) {
        nd--;
    }
    if (nd == 0u) {
        put_c(j, '0');
    }
    for (i = 0u; i < nd; i++) {
        put_c(j, frac[i]);
    }
}

void sn_json_bool(sn_json_t *j, bool v)
{
    pre_value(j);
    put_s(j, v ? "true" : "false");
}

void sn_json_null(sn_json_t *j)
{
    pre_value(j);
    put_s(j, "null");
}

bool sn_json_finish(sn_json_t *j)
{
    if (j == NULL) {
        return false;
    }
    return !j->overflow && !j->error && (j->depth == 0u) && !j->after_key && (j->len > 0u);
}
