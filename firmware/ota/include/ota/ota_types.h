/* Sentinel OTA: common types, error codes, little-endian helpers.
 * C11, no dynamic allocation. */
#ifndef OTA_TYPES_H
#define OTA_TYPES_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    OTA_OK = 0,
    OTA_ERR_PARAM = -1,            /* bad argument / misuse of API */
    OTA_ERR_FLASH = -2,            /* flash driver error (includes simulated power loss) */
    OTA_ERR_MAGIC = -3,            /* manifest magic != "SOTA" */
    OTA_ERR_MANIFEST_VERSION = -4, /* manifest_version != 1 */
    OTA_ERR_KIND = -5,             /* unknown kind, or kind not valid for this operation */
    OTA_ERR_RESERVED = -6,         /* a reserved field is non-zero */
    OTA_ERR_FLAGS = -7,            /* unknown flag bits set */
    OTA_ERR_SIZE = -8,             /* payload_size zero or does not fit the slot */
    OTA_ERR_LOAD_ADDR = -9,        /* load_addr invalid for kind */
    OTA_ERR_SIGNATURE = -10,       /* ECDSA signature does not verify */
    OTA_ERR_HASH = -11,            /* payload sha256 mismatch */
    OTA_ERR_ROLLBACK = -12,        /* security_counter < stored counter */
    OTA_ERR_OPSET = -13,           /* model op-set hash incompatible with running firmware */
    OTA_ERR_STATE = -14,           /* operation not allowed in the current slot state */
    OTA_ERR_NO_META = -15,         /* no valid slot metadata copy */
    OTA_ERR_TRUNCATED = -16,       /* stream ended before payload_size bytes */
    OTA_ERR_OVERFLOW = -17,        /* stream has more bytes than the manifest declares */
    OTA_ERR_NO_IMAGE = -18,        /* nothing bootable / selectable */
    OTA_ERR_LAYOUT = -19,          /* flash layout invalid (alignment, overlap, range) */
    OTA_ERR_KEY = -20              /* public key is not a valid P-256 point */
} ota_err_t;

const char *ota_err_str(int err);

/* Image kinds (INTERFACES.md section 4, byte 6). */
enum {
    OTA_KIND_FW_N6 = 1,
    OTA_KIND_FW_C6 = 2,
    OTA_KIND_MODEL = 3
};
#define OTA_KIND_COUNT 3

/* Slot states (INTERFACES.md section 4). Values are the on-flash encoding. */
typedef enum {
    OTA_SLOT_EMPTY = 1,
    OTA_SLOT_PENDING = 2,
    OTA_SLOT_TRIAL = 3,
    OTA_SLOT_CONFIRMED = 4,
    OTA_SLOT_BAD = 5
} ota_slot_state_t;

const char *ota_slot_state_str(int st);

static inline uint16_t ota_get_le16(const uint8_t *p) {
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}
static inline uint32_t ota_get_le32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}
static inline void ota_put_le16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}
static inline void ota_put_le32(uint8_t *p, uint32_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
    p[2] = (uint8_t)(v >> 16);
    p[3] = (uint8_t)(v >> 24);
}

/* Semver packing per INTERFACES.md: major<<16 | minor<<8 | patch. */
#define OTA_VERSION(maj, min, pat) (((uint32_t)(maj) << 16) | ((uint32_t)(min) << 8) | (uint32_t)(pat))

#ifdef __cplusplus
}
#endif
#endif
