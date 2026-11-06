#include "ota/ota_manifest.h"

#include <string.h>

#include "ota/ota.h"
#include "ota/ota_sha256.h"

int ota_manifest_parse(const uint8_t raw[OTA_MANIFEST_SIZE], ota_manifest_t *out) {
    if (!raw || !out) return OTA_ERR_PARAM;
    if (memcmp(raw + OTA_MOFF_MAGIC, OTA_MANIFEST_MAGIC, 4) != 0) return OTA_ERR_MAGIC;
    ota_manifest_t m;
    memset(&m, 0, sizeof m);
    m.manifest_version = ota_get_le16(raw + OTA_MOFF_MVER);
    if (m.manifest_version != OTA_MANIFEST_VERSION) return OTA_ERR_MANIFEST_VERSION;
    m.kind = raw[OTA_MOFF_KIND];
    if (m.kind < OTA_KIND_FW_N6 || m.kind > OTA_KIND_MODEL) return OTA_ERR_KIND;
    if (raw[OTA_MOFF_RSV0] != 0) return OTA_ERR_RESERVED;
    for (unsigned i = OTA_MOFF_RSV1; i < OTA_MANIFEST_SIGNED_LEN; i++) {
        if (raw[i] != 0) return OTA_ERR_RESERVED;
    }
    m.version = ota_get_le32(raw + OTA_MOFF_VERSION);
    m.security_counter = ota_get_le32(raw + OTA_MOFF_SECCTR);
    m.payload_size = ota_get_le32(raw + OTA_MOFF_PSIZE);
    memcpy(m.payload_sha256, raw + OTA_MOFF_PSHA, 32);
    m.load_addr = ota_get_le32(raw + OTA_MOFF_LOADADDR);
    m.flags = ota_get_le32(raw + OTA_MOFF_FLAGS);
    memcpy(m.op_set_hash, raw + OTA_MOFF_OPHASH, 32);
    memcpy(m.signature, raw + OTA_MOFF_SIG, OTA_P256_SIG_LEN);
    if (m.flags & ~(uint32_t)OTA_FLAGS_KNOWN) return OTA_ERR_FLAGS;
    if (m.payload_size == 0 || m.payload_size > 0xFFFFFFFFu - OTA_MANIFEST_SIZE) return OTA_ERR_SIZE;
    if (m.kind == OTA_KIND_MODEL) {
        if (m.load_addr != 0) return OTA_ERR_LOAD_ADDR;
    } else {
        if (m.load_addr == 0) return OTA_ERR_LOAD_ADDR;
    }
    *out = m;
    return OTA_OK;
}

int ota_manifest_verify_sig(const uint8_t raw[OTA_MANIFEST_SIZE], const uint8_t pubkey[OTA_P256_PUBKEY_LEN]) {
    if (!raw || !pubkey) return OTA_ERR_PARAM;
    uint8_t digest[32];
    ota_sha256(raw, OTA_MANIFEST_SIGNED_LEN, digest);
    return ota_p256_verify(pubkey, digest, raw + OTA_MOFF_SIG);
}

int ota_manifest_check(const uint8_t raw[OTA_MANIFEST_SIZE], const uint8_t pubkey[OTA_P256_PUBKEY_LEN],
                       const ota_policy_t *policy, ota_manifest_t *out) {
    ota_manifest_t m;
    int rc = ota_manifest_parse(raw, &m);
    if (rc) return rc;
    rc = ota_manifest_verify_sig(raw, pubkey);
    if (rc) return rc;
    if (policy) {
        if (policy->expect_kind && m.kind != policy->expect_kind) return OTA_ERR_KIND;
        if (policy->max_payload_size && m.payload_size > policy->max_payload_size) return OTA_ERR_SIZE;
        if (m.security_counter < policy->min_security_counter) return OTA_ERR_ROLLBACK;
        if (policy->required_op_set_hash && m.kind == OTA_KIND_MODEL &&
            !ota_ct_equal(m.op_set_hash, policy->required_op_set_hash, 32)) {
            return OTA_ERR_OPSET;
        }
    }
    if (out) *out = m;
    return OTA_OK;
}

int ota_image_verify_buffer(const uint8_t *image, uint32_t image_len, const uint8_t pubkey[OTA_P256_PUBKEY_LEN],
                            const ota_policy_t *policy, ota_manifest_t *out) {
    if (!image || !pubkey) return OTA_ERR_PARAM;
    if (image_len < OTA_MANIFEST_SIZE) return OTA_ERR_TRUNCATED;
    ota_manifest_t m;
    int rc = ota_manifest_check(image, pubkey, policy, &m);
    if (rc) return rc;
    uint32_t have = image_len - OTA_MANIFEST_SIZE;
    if (have < m.payload_size) return OTA_ERR_TRUNCATED;
    if (have > m.payload_size) return OTA_ERR_OVERFLOW;
    uint8_t digest[32];
    ota_sha256(image + OTA_MANIFEST_SIZE, m.payload_size, digest);
    if (!ota_ct_equal(digest, m.payload_sha256, 32)) return OTA_ERR_HASH;
    if (out) *out = m;
    return OTA_OK;
}

int ota_slot_verify(const ota_flash_t *f, uint32_t addr, uint32_t cap, const uint8_t *pubkey,
                    const ota_policy_t *policy, ota_manifest_t *out) {
    if (!f || !pubkey || cap < OTA_MANIFEST_SIZE) return OTA_ERR_PARAM;
    uint8_t raw[OTA_MANIFEST_SIZE];
    int rc = f->read(f->ctx, addr, raw, OTA_MANIFEST_SIZE);
    if (rc) return rc;
    ota_policy_t p;
    if (policy) {
        p = *policy;
    } else {
        memset(&p, 0, sizeof p);
    }
    uint32_t room = cap - OTA_MANIFEST_SIZE;
    if (p.max_payload_size == 0 || p.max_payload_size > room) p.max_payload_size = room;
    ota_manifest_t m;
    rc = ota_manifest_check(raw, pubkey, &p, &m);
    if (rc) return rc;
    ota_sha256_ctx_t sc;
    ota_sha256_init(&sc);
    uint8_t buf[256];
    uint32_t off = 0;
    while (off < m.payload_size) {
        uint32_t n = m.payload_size - off;
        if (n > sizeof buf) n = sizeof buf;
        rc = f->read(f->ctx, addr + OTA_MANIFEST_SIZE + off, buf, n);
        if (rc) return rc;
        ota_sha256_update(&sc, buf, n);
        off += n;
    }
    uint8_t digest[32];
    ota_sha256_final(&sc, digest);
    if (!ota_ct_equal(digest, m.payload_sha256, 32)) return OTA_ERR_HASH;
    if (out) *out = m;
    return OTA_OK;
}
