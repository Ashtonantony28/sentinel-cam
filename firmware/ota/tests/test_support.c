#include "test_support.h"

#include <stdlib.h>
#include <string.h>

#include "ota_test.h"
#include "uECC.h"

int tf_failures = 0;
int tf_checks = 0;

int tf_main(int argc, char **argv, const tf_case_t *cases, int n) {
    int ran = 0;
    for (int i = 0; i < n; i++) {
        if (argc > 1 && strcmp(argv[1], cases[i].name) != 0) continue;
        int before = tf_failures;
        cases[i].fn();
        printf("[%s] %s\n", tf_failures == before ? "PASS" : "FAIL", cases[i].name);
        ran++;
    }
    if (!ran) {
        fprintf(stderr, "no test case named '%s'\n", argc > 1 ? argv[1] : "");
        return 2;
    }
    printf("%d cases, %d checks, %d failures\n", ran, tf_checks, tf_failures);
    return tf_failures ? 1 : 0;
}

const uint8_t TB_OPHASH_A[32] = {0xA1, 0xA2, 0xA3, 0xA4, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
                                 13,   14,   15,   16,   17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28};
const uint8_t TB_OPHASH_B[32] = {0xB1, 0xB2, 0xB3, 0xB4, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12,
                                 13,   14,   15,   16,   17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28};

void tb_dev_init(tb_dev_t *d) {
    ota_sim_init(&d->sim, d->mem, TB_FLASH_SIZE, TB_ERASE, TB_PAGE, &d->flash);
    d->layout.meta_addr[0] = 0;
    d->layout.meta_addr[1] = TB_ERASE;
    d->layout.fw_addr[0] = 2 * TB_ERASE;
    d->layout.fw_addr[1] = 2 * TB_ERASE + TB_FW_SIZE;
    d->layout.fw_size = TB_FW_SIZE;
    d->layout.model_addr[0] = 2 * TB_ERASE + 2 * TB_FW_SIZE;
    d->layout.model_addr[1] = 2 * TB_ERASE + 2 * TB_FW_SIZE + TB_MODEL_SIZE;
    d->layout.model_size = TB_MODEL_SIZE;
}

static uint64_t g_rng = 1;

void tb_rng_seed(uint64_t seed) { g_rng = seed; }

uint64_t tb_rand(void) {
    uint64_t z = (g_rng += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

uint32_t tb_rand_below(uint32_t n) { return n ? (uint32_t)(tb_rand() % n) : 0; }

static int uecc_rng(uint8_t *dest, unsigned size) {
    for (unsigned i = 0; i < size; i++) dest[i] = (uint8_t)tb_rand();
    return 1;
}

void tb_key_gen(tb_key_t *k) {
    uECC_set_rng(uecc_rng);
    if (!uECC_make_key(k->pub, k->priv, uECC_secp256r1())) abort();
}

void tb_spec_default(tb_spec_t *s, uint8_t kind) {
    memset(s, 0, sizeof *s);
    s->kind = kind;
    s->version = OTA_VERSION(1, 0, 0);
    s->counter = 1;
    s->load_addr = kind == OTA_KIND_MODEL ? 0 : TB_LOAD_ADDR;
    s->flags = kind == OTA_KIND_MODEL ? 0 : OTA_FLAG_REQUIRES_REBOOT;
    memcpy(s->op_hash, TB_OPHASH_A, 32);
    s->payload_size = 3000;
    s->payload_seed = 7;
}

void tb_sign_manifest(uint8_t *image, const tb_key_t *k) {
    uint8_t digest[32];
    ota_sha256(image, OTA_MANIFEST_SIGNED_LEN, digest);
    uECC_set_rng(uecc_rng);
    if (!uECC_sign(k->priv, digest, 32, image + OTA_MOFF_SIG, uECC_secp256r1())) abort();
}

uint32_t tb_build(const tb_spec_t *s, const tb_key_t *k, uint8_t *out, uint32_t cap) {
    uint32_t len = OTA_MANIFEST_SIZE + s->payload_size;
    if (len > cap) abort();
    memset(out, 0, OTA_MANIFEST_SIZE);
    uint32_t x = s->payload_seed * 2654435761u + 1u;
    for (uint32_t i = 0; i < s->payload_size; i++) {
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        out[OTA_MANIFEST_SIZE + i] = (uint8_t)x;
    }
    memcpy(out, "SOTA", 4);
    ota_put_le16(out + OTA_MOFF_MVER, 1);
    out[OTA_MOFF_KIND] = s->kind;
    ota_put_le32(out + OTA_MOFF_VERSION, s->version);
    ota_put_le32(out + OTA_MOFF_SECCTR, s->counter);
    ota_put_le32(out + OTA_MOFF_PSIZE, s->payload_size);
    ota_sha256(out + OTA_MANIFEST_SIZE, s->payload_size, out + OTA_MOFF_PSHA);
    ota_put_le32(out + OTA_MOFF_LOADADDR, s->load_addr);
    ota_put_le32(out + OTA_MOFF_FLAGS, s->flags);
    memcpy(out + OTA_MOFF_OPHASH, s->op_hash, 32);
    tb_sign_manifest(out, k);
    return len;
}

int tb_stream(ota_ctx_t *ctx, uint8_t kind, const uint8_t *img, uint32_t len, uint32_t max_chunk) {
    ota_writer_t w;
    int rc = ota_writer_begin(ctx, &w, kind);
    if (rc) return rc;
    uint32_t off = 0;
    while (off < len) {
        uint32_t n = 1u + tb_rand_below(max_chunk);
        if (n > len - off) n = len - off;
        rc = ota_writer_write(&w, img + off, n);
        if (rc) return rc;
        off += n;
    }
    return ota_writer_finish(&w);
}

int tb_factory(tb_dev_t *d, ota_ctx_t *ctx, const tb_key_t *k, const uint8_t op_hash[32], uint8_t *scratch,
               uint32_t scratch_cap) {
    int rc = ota_meta_format(&d->flash, &d->layout);
    if (rc) return rc;
    rc = ota_init(ctx, &d->flash, &d->layout, k->pub);
    if (rc) return rc;
    tb_spec_t s;
    tb_spec_default(&s, OTA_KIND_FW_N6);
    memcpy(s.op_hash, op_hash, 32);
    uint32_t n = tb_build(&s, k, scratch, scratch_cap);
    rc = tb_stream(ctx, OTA_KIND_FW_N6, scratch, n, 300);
    if (rc) return rc;
    ota_boot_result_t br;
    rc = ota_boot_decide(ctx, &br);
    if (rc) return rc;
    rc = ota_fw_confirm(ctx);
    if (rc) return rc;
    tb_spec_default(&s, OTA_KIND_MODEL);
    memcpy(s.op_hash, op_hash, 32);
    s.payload_seed = 99;
    n = tb_build(&s, k, scratch, scratch_cap);
    rc = tb_stream(ctx, OTA_KIND_MODEL, scratch, n, 300);
    if (rc) return rc;
    rc = ota_model_select(ctx, &br);
    if (rc) return rc;
    return ota_model_confirm(ctx);
}
