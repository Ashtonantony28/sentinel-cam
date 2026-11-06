/* ota_crypto backend on vendored micro-ecc (see VENDORED.md). */
#include "ota/ota_crypto.h"

#include "ota/ota_types.h"
#include "uECC.h"

int ota_p256_pubkey_check(const uint8_t pubkey[OTA_P256_PUBKEY_LEN]) {
    if (!pubkey) return OTA_ERR_PARAM;
    return uECC_valid_public_key(pubkey, uECC_secp256r1()) ? OTA_OK : OTA_ERR_KEY;
}

int ota_p256_verify(const uint8_t pubkey[OTA_P256_PUBKEY_LEN], const uint8_t digest[32],
                    const uint8_t sig[OTA_P256_SIG_LEN]) {
    if (!pubkey || !digest || !sig) return OTA_ERR_PARAM;
    return uECC_verify(pubkey, digest, 32, sig, uECC_secp256r1()) ? OTA_OK : OTA_ERR_SIGNATURE;
}
