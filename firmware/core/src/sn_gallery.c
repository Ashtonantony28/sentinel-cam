#include "sn_gallery.h"
#include "sn_crc32.h"
#include <string.h>
#include <math.h>

static const uint8_t k_magic[SN_GAL_MAGIC_LEN] = { 'S', 'G', 'A', 'L' };

static uint16_t rd_u16(const uint8_t *p) { return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8)); }
static uint32_t rd_u32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static void wr_u16(uint8_t *p, uint16_t v) { p[0] = (uint8_t)(v & 0xFFu); p[1] = (uint8_t)(v >> 8); }
static void wr_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFFu); p[1] = (uint8_t)((v >> 8) & 0xFFu);
    p[2] = (uint8_t)((v >> 16) & 0xFFu); p[3] = (uint8_t)(v >> 24);
}

const char *sn_gal_status_name(sn_gal_status_t st)
{
    switch (st) {
    case SN_GAL_OK:                  return "OK";
    case SN_GAL_ERR_ARG:             return "ERR_ARG";
    case SN_GAL_ERR_TRUNCATED:       return "TRUNCATED";
    case SN_GAL_ERR_CRC:             return "BAD_CRC";
    case SN_GAL_ERR_MAGIC:           return "BAD_MAGIC";
    case SN_GAL_ERR_VERSION:         return "BAD_VERSION";
    case SN_GAL_ERR_TOO_MANY_PEOPLE: return "TOO_MANY_PEOPLE";
    case SN_GAL_ERR_PROTO_COUNT:     return "BAD_PROTO_COUNT";
    case SN_GAL_ERR_TRAILING:        return "TRAILING_BYTES";
    case SN_GAL_ERR_DUP_ID:          return "DUPLICATE_ID";
    case SN_GAL_ERR_NO_SPACE:        return "NO_SPACE";
    default:                         return "?";
    }
}

void sn_gallery_clear(sn_gallery_t *g)
{
    if (g != NULL) {
        (void)memset(g, 0, sizeof(*g));
    }
}

int32_t sn_int8_dot(const int8_t *a, const int8_t *b, size_t n)
{
    int32_t acc = 0;
    size_t i;
    for (i = 0u; i < n; i++) {
        acc += (int32_t)a[i] * (int32_t)b[i];   /* |acc| <= 128*127*127 < 2^31 */
    }
    return acc;
}

float sn_int8_norm(const int8_t *a, size_t n)
{
    return (float)sqrt((double)sn_int8_dot(a, a, n));
}

double sn_int8_cosine(const int8_t *a, const int8_t *b, size_t n)
{
    double na = sqrt((double)sn_int8_dot(a, a, n));
    double nb = sqrt((double)sn_int8_dot(b, b, n));
    if ((na <= 0.0) || (nb <= 0.0)) {
        return 0.0;
    }
    return (double)sn_int8_dot(a, b, n) / (na * nb);
}

void sn_embed_quantize(const float in[SN_EMB_DIM], int8_t out[SN_EMB_DIM])
{
    uint32_t i;
    for (i = 0u; i < SN_EMB_DIM; i++) {
        float v = in[i] * 127.0f;
        if (isnan(v)) {
            v = 0.0f;
        }
        if (v > 127.0f) { v = 127.0f; }
        if (v < -127.0f) { v = -127.0f; }
        out[i] = (int8_t)roundf(v);  /* half away from zero */
    }
}

sn_gal_status_t sn_gallery_parse(sn_gallery_t *g, const uint8_t *blob, size_t len)
{
    size_t off;
    size_t end;
    uint16_t n_people;
    uint16_t i;
    uint16_t j;

    if ((g == NULL) || (blob == NULL)) {
        return SN_GAL_ERR_ARG;
    }
    sn_gallery_clear(g);
    if (len < (SN_GAL_HEADER_SIZE + SN_GAL_CRC_SIZE)) {
        return SN_GAL_ERR_TRUNCATED;
    }
    end = len - SN_GAL_CRC_SIZE;
    /* CRC first: never interpret fields of a corrupted blob. */
    if (sn_crc32(blob, end) != rd_u32(&blob[end])) {
        return SN_GAL_ERR_CRC;
    }
    if (memcmp(blob, k_magic, SN_GAL_MAGIC_LEN) != 0) {
        return SN_GAL_ERR_MAGIC;
    }
    if (rd_u16(&blob[4]) != SN_GAL_VERSION) {
        return SN_GAL_ERR_VERSION;
    }
    n_people = rd_u16(&blob[6]);
    if (n_people > SN_GAL_MAX_PEOPLE) {
        return SN_GAL_ERR_TOO_MANY_PEOPLE;
    }

    off = SN_GAL_HEADER_SIZE;
    for (i = 0u; i < n_people; i++) {
        sn_gal_person_t *p = &g->people[i];
        size_t body;
        uint8_t k;

        if ((end - off) < SN_GAL_PERSON_HDR_SIZE) {
            sn_gallery_clear(g);
            return SN_GAL_ERR_TRUNCATED;
        }
        p->id = rd_u16(&blob[off]);
        p->n_protos = blob[off + 2u];
        p->threshold_q15 = (int16_t)rd_u16(&blob[off + 4u]);
        (void)memcpy(p->name, &blob[off + 6u], SN_GAL_NAME_LEN);
        p->name[SN_GAL_NAME_LEN] = '\0';
        if ((p->n_protos == 0u) || (p->n_protos > SN_GAL_MAX_PROTOS)) {
            sn_gallery_clear(g);
            return SN_GAL_ERR_PROTO_COUNT;
        }
        off += SN_GAL_PERSON_HDR_SIZE;
        body = (size_t)p->n_protos * SN_EMB_DIM;
        if ((end - off) < body) {
            sn_gallery_clear(g);
            return SN_GAL_ERR_TRUNCATED;
        }
        p->protos = (const int8_t *)(const void *)&blob[off];
        for (k = 0u; k < p->n_protos; k++) {
            const int8_t *q = &p->protos[(size_t)k * SN_EMB_DIM];
            p->proto_sq[k] = (uint32_t)sn_int8_dot(q, q, SN_EMB_DIM);
        }
        off += body;
    }
    if (off != end) {
        sn_gallery_clear(g);
        return SN_GAL_ERR_TRAILING;
    }
    for (i = 1u; i < n_people; i++) {
        for (j = 0u; j < i; j++) {
            if (g->people[j].id == g->people[i].id) {
                sn_gallery_clear(g);
                return SN_GAL_ERR_DUP_ID;
            }
        }
    }
    g->n_people = n_people;
    return SN_GAL_OK;
}

sn_gal_status_t sn_gallery_write(uint8_t *buf, size_t cap,
                                 const sn_gal_person_spec_t *people, uint16_t n_people,
                                 size_t *out_len)
{
    size_t need = SN_GAL_HEADER_SIZE + SN_GAL_CRC_SIZE;
    size_t off;
    uint16_t i;
    uint16_t j;

    if ((buf == NULL) || (out_len == NULL) || ((people == NULL) && (n_people > 0u))) {
        return SN_GAL_ERR_ARG;
    }
    *out_len = 0u;
    if (n_people > SN_GAL_MAX_PEOPLE) {
        return SN_GAL_ERR_TOO_MANY_PEOPLE;
    }
    for (i = 0u; i < n_people; i++) {
        if ((people[i].n_protos == 0u) || (people[i].n_protos > SN_GAL_MAX_PROTOS)) {
            return SN_GAL_ERR_PROTO_COUNT;
        }
        if (people[i].protos == NULL) {
            return SN_GAL_ERR_ARG;
        }
        for (j = 0u; j < i; j++) {
            if (people[j].id == people[i].id) {
                return SN_GAL_ERR_DUP_ID;
            }
        }
        need += SN_GAL_PERSON_HDR_SIZE + ((size_t)people[i].n_protos * SN_EMB_DIM);
    }
    if (need > cap) {
        return SN_GAL_ERR_NO_SPACE;
    }
    (void)memcpy(buf, k_magic, SN_GAL_MAGIC_LEN);
    wr_u16(&buf[4], (uint16_t)SN_GAL_VERSION);
    wr_u16(&buf[6], n_people);
    off = SN_GAL_HEADER_SIZE;
    for (i = 0u; i < n_people; i++) {
        size_t nlen = 0u;
        wr_u16(&buf[off], people[i].id);
        buf[off + 2u] = people[i].n_protos;
        buf[off + 3u] = 0u;
        wr_u16(&buf[off + 4u], (uint16_t)people[i].threshold_q15);
        (void)memset(&buf[off + 6u], 0, SN_GAL_NAME_LEN);
        if (people[i].name != NULL) {
            while ((nlen < SN_GAL_NAME_LEN) && (people[i].name[nlen] != '\0')) {
                nlen++;
            }
            (void)memcpy(&buf[off + 6u], people[i].name, nlen);
        }
        off += SN_GAL_PERSON_HDR_SIZE;
        (void)memcpy(&buf[off], people[i].protos, (size_t)people[i].n_protos * SN_EMB_DIM);
        off += (size_t)people[i].n_protos * SN_EMB_DIM;
    }
    wr_u32(&buf[off], sn_crc32(buf, off));
    *out_len = off + SN_GAL_CRC_SIZE;
    return SN_GAL_OK;
}

double sn_gallery_person_score(const sn_gal_person_t *p, const int8_t emb[SN_EMB_DIM])
{
    double best = -1.0;
    double ne;
    uint8_t k;
    if ((p == NULL) || (emb == NULL) || (p->n_protos == 0u)) {
        return -1.0;
    }
    ne = sqrt((double)sn_int8_dot(emb, emb, SN_EMB_DIM));
    for (k = 0u; k < p->n_protos; k++) {
        const int8_t *proto = &p->protos[(size_t)k * SN_EMB_DIM];
        double np = sqrt((double)p->proto_sq[k]);
        double s = 0.0;
        if ((np > 0.0) && (ne > 0.0)) {
            s = (double)sn_int8_dot(emb, proto, SN_EMB_DIM) / (ne * np);
        }
        if (s > best) {
            best = s;
        }
    }
    return best;
}

void sn_gallery_match(const sn_gallery_t *g, const int8_t emb[SN_EMB_DIM], sn_match_t *out)
{
    double best = -1.0;
    double second = -1.0;
    int32_t best_idx = -1;
    uint16_t i;

    if (out == NULL) {
        return;
    }
    out->matched = false;
    out->id = 0u;
    out->best_idx = -1;
    out->best_score = -1.0;
    out->second_score = -1.0;
    if ((g == NULL) || (emb == NULL)) {
        return;
    }
    for (i = 0u; i < g->n_people; i++) {
        double s = sn_gallery_person_score(&g->people[i], emb);
        if (g->people[i].n_protos == 0u) {
            continue;   /* no prototypes: cannot be best or second */
        }
        if ((best_idx < 0) || (s > best)) {
            second = (best_idx < 0) ? second : best;
            best = s;
            best_idx = (int32_t)i;
        } else if (s > second) {
            second = s;
        } else {
            /* not in top two */
        }
    }
    if (best_idx < 0) {
        return;
    }
    out->best_idx = (int8_t)best_idx;
    out->best_score = best;
    out->second_score = second;
    {
        const sn_gal_person_t *p = &g->people[best_idx];
        double thr = (double)p->threshold_q15 / 32768.0;
        bool thr_ok = (best + SN_GAL_EPS) >= thr;
        bool margin_ok = ((best - second) + SN_GAL_EPS) >= SN_GAL_MARGIN;
        if (thr_ok && margin_ok) {
            out->matched = true;
            out->id = p->id;
        }
    }
}
