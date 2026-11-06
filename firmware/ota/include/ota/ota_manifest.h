/* OTA manifest (INTERFACES.md section 4): 256-byte header before the payload. */
#ifndef OTA_MANIFEST_H
#define OTA_MANIFEST_H
#include <stdint.h>
#include "ota_crypto.h"
#include "ota_types.h"
#ifdef __cplusplus
extern "C" {
#endif

#define OTA_MANIFEST_SIZE 256u
#define OTA_MANIFEST_SIGNED_LEN 192u
#define OTA_MANIFEST_MAGIC "SOTA"
#define OTA_MANIFEST_VERSION 1u
#define OTA_FLAG_REQUIRES_REBOOT 0x1u
#define OTA_FLAGS_KNOWN (OTA_FLAG_REQUIRES_REBOOT)

/* Byte offsets. */
#define OTA_MOFF_MAGIC 0
#define OTA_MOFF_MVER 4
#define OTA_MOFF_KIND 6
#define OTA_MOFF_RSV0 7
#define OTA_MOFF_VERSION 8
#define OTA_MOFF_SECCTR 12
#define OTA_MOFF_PSIZE 16
#define OTA_MOFF_PSHA 20
#define OTA_MOFF_LOADADDR 52
#define OTA_MOFF_FLAGS 56
#define OTA_MOFF_OPHASH 60
#define OTA_MOFF_RSV1 92
#define OTA_MOFF_SIG 192

typedef struct {
    uint16_t manifest_version;
    uint8_t kind;
    uint32_t version;
    uint32_t security_counter;
    uint32_t payload_size;
    uint8_t payload_sha256[32];
    uint32_t load_addr;
    uint32_t flags;
    uint8_t op_set_hash[32];
    uint8_t signature[OTA_P256_SIG_LEN];
} ota_manifest_t;

/* Decode and structurally validate (magic, manifest_version, kind, reserved
 * fields, flags, payload_size != 0, load_addr rule). Does NOT check the
 * signature. */
int ota_manifest_parse(const uint8_t raw[OTA_MANIFEST_SIZE], ota_manifest_t *out);

/* Verify the ECDSA signature at [192,256) over SHA-256 of [0,192). */
int ota_manifest_verify_sig(const uint8_t raw[OTA_MANIFEST_SIZE], const uint8_t pubkey[OTA_P256_PUBKEY_LEN]);

/* Policy checks applied to every image before it is accepted or booted. */
typedef struct {
    uint8_t expect_kind;          /* 0 = any */
    uint32_t min_security_counter;
    uint32_t max_payload_size;    /* 0 = unlimited */
    const uint8_t *required_op_set_hash; /* NULL = skip; for kind=model, must equal */
} ota_policy_t;

/* parse + signature + policy (kind, counter, size, op-set). Payload hash not checked. */
int ota_manifest_check(const uint8_t raw[OTA_MANIFEST_SIZE], const uint8_t pubkey[OTA_P256_PUBKEY_LEN],
                       const ota_policy_t *policy, ota_manifest_t *out);

/* Verify a complete in-memory image (manifest + payload). image_len must be
 * exactly 256 + payload_size. Used by host tools and by the C6 bridge. */
int ota_image_verify_buffer(const uint8_t *image, uint32_t image_len, const uint8_t pubkey[OTA_P256_PUBKEY_LEN],
                            const ota_policy_t *policy, ota_manifest_t *out);

#ifdef __cplusplus
}
#endif
#endif
