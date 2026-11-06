/*
 * sn_gallery.h - int8 face gallery: SGAL blob parse/write + matcher.
 * Contract: docs/INTERFACES.md 1.
 *
 * Blob layout (little-endian):
 *   "SGAL" u16 version(=1) u16 n_people
 *   n_people x { u16 id, u8 n_protos, u8 reserved, i16 threshold_q15,
 *                char name[24], int8 protos[n_protos][128] }
 *   u32 crc32_ieee(all preceding bytes)
 *
 * The parser is zero-copy: sn_gallery_t points into the blob (which on
 * target is memory-mapped flash), so the blob must outlive the gallery.
 */
#ifndef SN_GALLERY_H
#define SN_GALLERY_H
#include "sn_types.h"

#define SN_GAL_MAGIC_LEN        4u
#define SN_GAL_VERSION          1u
#define SN_GAL_HEADER_SIZE      8u
#define SN_GAL_PERSON_HDR_SIZE  30u   /* 2 + 1 + 1 + 2 + 24 */
#define SN_GAL_CRC_SIZE         4u
#define SN_GAL_MAX_BLOB_SIZE    (SN_GAL_HEADER_SIZE + SN_GAL_MAX_PEOPLE * \
        (SN_GAL_PERSON_HDR_SIZE + SN_GAL_MAX_PROTOS * SN_EMB_DIM) + SN_GAL_CRC_SIZE)

#define SN_GAL_DEFAULT_THRESHOLD_Q15  18022   /* round(0.55 * 32768) */
#define SN_GAL_MARGIN                 0.05
/* Tolerance applied in favour of a match at exact boundaries, so a host
 * float64 reference and this implementation agree at score == threshold. */
#define SN_GAL_EPS                    1e-9

/* Validation order (matches ml/golden MANIFEST "blob_validation_order"):
 * TRUNCATED(len < 12) -> BAD_CRC -> BAD_MAGIC -> BAD_VERSION ->
 * TOO_MANY_PEOPLE -> per person TRUNCATED | BAD_PROTO_COUNT ->
 * TRAILING_BYTES -> DUPLICATE_ID. */
typedef enum {
    SN_GAL_OK = 0,
    SN_GAL_ERR_ARG,
    SN_GAL_ERR_TRUNCATED,        /* "TRUNCATED"       */
    SN_GAL_ERR_CRC,              /* "BAD_CRC"         */
    SN_GAL_ERR_MAGIC,            /* "BAD_MAGIC"       */
    SN_GAL_ERR_VERSION,          /* "BAD_VERSION"     */
    SN_GAL_ERR_TOO_MANY_PEOPLE,  /* "TOO_MANY_PEOPLE" */
    SN_GAL_ERR_PROTO_COUNT,      /* "BAD_PROTO_COUNT": n_protos not in 1..20 */
    SN_GAL_ERR_TRAILING,         /* "TRAILING_BYTES"  */
    SN_GAL_ERR_DUP_ID,           /* "DUPLICATE_ID"    */
    SN_GAL_ERR_NO_SPACE          /* writer: output buffer too small */
} sn_gal_status_t;

typedef struct {
    uint16_t      id;
    uint8_t       n_protos;
    int16_t       threshold_q15;
    char          name[SN_GAL_NAME_LEN + 1u];   /* NUL-terminated copy */
    const int8_t *protos;                       /* n_protos * 128, in the blob */
    uint32_t      proto_sq[SN_GAL_MAX_PROTOS];  /* sum q^2 per prototype (exact) */
} sn_gal_person_t;

typedef struct {
    uint16_t        n_people;
    sn_gal_person_t people[SN_GAL_MAX_PEOPLE];
} sn_gallery_t;

typedef struct {
    bool     matched;
    uint16_t id;            /* valid when matched                          */
    int8_t   best_idx;      /* index into people[], -1 if gallery empty    */
    double   best_score;    /* best person's score (max over prototypes)   */
    double   second_score;  /* second-best person's score, -1 if none      */
} sn_match_t;

/* Writer input for one person. */
typedef struct {
    uint16_t      id;
    uint8_t       n_protos;
    int16_t       threshold_q15;
    const char   *name;          /* truncated to 24 bytes, NUL padded */
    const int8_t *protos;        /* n_protos * 128 */
} sn_gal_person_spec_t;

void            sn_gallery_clear(sn_gallery_t *g);
sn_gal_status_t sn_gallery_parse(sn_gallery_t *g, const uint8_t *blob, size_t len);
/* Writes a blob; *out_len receives its size. */
sn_gal_status_t sn_gallery_write(uint8_t *buf, size_t cap,
                                 const sn_gal_person_spec_t *people, uint16_t n_people,
                                 size_t *out_len);
const char     *sn_gal_status_name(sn_gal_status_t st);

/* INTERFACES.md 1 quantization: q = round(clamp(x*127, -127, 127)) computed
 * in float32 with round-half-away-from-zero (C roundf), as pinned by the
 * ml/golden generator. NaN -> 0; +-inf clamp to +-127. */
void    sn_embed_quantize(const float in[SN_EMB_DIM], int8_t out[SN_EMB_DIM]);
int32_t sn_int8_dot(const int8_t *a, const int8_t *b, size_t n);
float   sn_int8_norm(const int8_t *a, size_t n);
/* Cosine via int32 dot / (norm_a * norm_b); 0 if either norm is 0. */
double  sn_int8_cosine(const int8_t *a, const int8_t *b, size_t n);

/* Person score = max cosine over prototypes; -1 if no prototypes. */
double  sn_gallery_person_score(const sn_gal_person_t *p, const int8_t emb[SN_EMB_DIM]);
/* INTERFACES.md 1 match rule. With a single person, there is no second best
 * and second_score = -1 (margin vacuously satisfied). Ties for best go to
 * the first person in gallery order (and then fail the margin). */
void    sn_gallery_match(const sn_gallery_t *g, const int8_t emb[SN_EMB_DIM], sn_match_t *out);

#endif
