/* Signature-verification abstraction. The default backend is vendored
 * micro-ecc (src/ota_crypto_uecc.c). A hardware backend (e.g. the STM32N6
 * PKA) can replace that one file without touching the rest of the library. */
#ifndef OTA_CRYPTO_H
#define OTA_CRYPTO_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif

#define OTA_P256_PUBKEY_LEN 64 /* uncompressed X||Y, big-endian, no 0x04 prefix */
#define OTA_P256_SIG_LEN 64    /* r||s, big-endian */

/* Returns OTA_OK if pubkey is a valid point on P-256, else OTA_ERR_KEY. */
int ota_p256_pubkey_check(const uint8_t pubkey[OTA_P256_PUBKEY_LEN]);

/* Verify an ECDSA P-256 signature over a precomputed SHA-256 digest.
 * Returns OTA_OK or OTA_ERR_SIGNATURE. */
int ota_p256_verify(const uint8_t pubkey[OTA_P256_PUBKEY_LEN], const uint8_t digest[32],
                    const uint8_t sig[OTA_P256_SIG_LEN]);

#ifdef __cplusplus
}
#endif
#endif
