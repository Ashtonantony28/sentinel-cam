/* SHA-256 (FIPS 180-4), streaming. */
#ifndef OTA_SHA256_H
#define OTA_SHA256_H
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define OTA_SHA256_LEN 32

typedef struct {
    uint32_t h[8];
    uint64_t total_len;
    uint8_t buf[64];
    uint32_t buf_len;
} ota_sha256_ctx_t;

void ota_sha256_init(ota_sha256_ctx_t *c);
void ota_sha256_update(ota_sha256_ctx_t *c, const void *data, size_t len);
void ota_sha256_final(ota_sha256_ctx_t *c, uint8_t out[OTA_SHA256_LEN]);
void ota_sha256(const void *data, size_t len, uint8_t out[OTA_SHA256_LEN]);

/* Constant-time comparison; returns 1 if equal. */
int ota_ct_equal(const uint8_t *a, const uint8_t *b, size_t len);

#ifdef __cplusplus
}
#endif
#endif
