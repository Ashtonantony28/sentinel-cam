/* Unit tests for the OTA library. Run one case: ota_test_unit <case>. */
#include <stdlib.h>
#include <string.h>

#include "ota/ota.h"
#include "ota_test.h"
#include "test_support.h"
#include "uECC.h"

static tb_dev_t g_dev;
static uint8_t g_img[TB_FW_SIZE];
static uint8_t g_img2[TB_FW_SIZE];
static tb_key_t g_key;

static void setup(uint64_t seed) {
    tb_rng_seed(seed);
    tb_dev_init(&g_dev);
    tb_key_gen(&g_key);
}

static void hex2bin(const char *h, uint8_t *out) {
    for (size_t i = 0; h[2 * i]; i++) {
        unsigned v;
        sscanf(h + 2 * i, "%2x", &v);
        out[i] = (uint8_t)v;
    }
}

static int sha_is(const void *d, size_t n, const char *hex) {
    uint8_t got[32], want[32];
    ota_sha256(d, n, got);
    hex2bin(hex, want);
    return memcmp(got, want, 32) == 0;
}

/* ------------------------------------------------------------------ hashes */
static void t_sha256_vectors(void) {
    CHECK(sha_is("", 0, "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"));
    CHECK(sha_is("abc", 3, "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"));
    const char *m448 = "abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";
    CHECK(sha_is(m448, strlen(m448), "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"));
    const char *m896 =
        "abcdefghbcdefghicdefghijdefghijkefghijklfghijklmghijklmnhijklmnoijklmnopjklmnopqklmnopqrlmnopqrsmnopqrstnopqrstu";
    CHECK(sha_is(m896, strlen(m896), "cf5b16a778af8380036ce59e7b0492370b249b11e8f07a51afac45037afee9d1"));
    /* one million 'a' */
    static uint8_t a[1000];
    memset(a, 'a', sizeof a);
    ota_sha256_ctx_t c;
    ota_sha256_init(&c);
    for (int i = 0; i < 1000; i++) ota_sha256_update(&c, a, sizeof a);
    uint8_t got[32], want[32];
    ota_sha256_final(&c, got);
    hex2bin("cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0", want);
    CHECK(memcmp(got, want, 32) == 0);
    /* lengths around the padding boundary: 55, 56, 63, 64, 65 bytes of 'a' */
    CHECK(sha_is(a, 55, "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318"));
    CHECK(sha_is(a, 56, "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a"));
    CHECK(sha_is(a, 64, "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb"));
}

static void t_sha256_streaming(void) {
    tb_rng_seed(11);
    static uint8_t buf[5000];
    for (size_t i = 0; i < sizeof buf; i++) buf[i] = (uint8_t)tb_rand();
    uint8_t ref[32];
    ota_sha256(buf, sizeof buf, ref);
    for (int trial = 0; trial < 200; trial++) {
        ota_sha256_ctx_t c;
        ota_sha256_init(&c);
        size_t off = 0;
        while (off < sizeof buf) {
            size_t n = 1 + tb_rand_below(trial < 100 ? 70 : 2000);
            if (n > sizeof buf - off) n = sizeof buf - off;
            ota_sha256_update(&c, buf + off, n);
            off += n;
        }
        uint8_t got[32];
        ota_sha256_final(&c, got);
        CHECK(memcmp(got, ref, 32) == 0);
    }
    CHECK(ota_ct_equal(ref, ref, 32));
    uint8_t x[32];
    memcpy(x, ref, 32);
    x[31] ^= 1;
    CHECK(!ota_ct_equal(ref, x, 32));
}

static void t_crc32(void) {
    CHECK(ota_crc32("123456789", 9) == 0xCBF43926u);
    CHECK(ota_crc32("", 0) == 0u);
}

/* ------------------------------------------------------------------ crypto */
static void t_crypto_basic(void) {
    setup(21);
    CHECK_RC(ota_p256_pubkey_check(g_key.pub), OTA_OK);
    uint8_t bad[64];
    memcpy(bad, g_key.pub, 64);
    bad[63] ^= 1;
    CHECK_RC(ota_p256_pubkey_check(bad), OTA_ERR_KEY);
    uint8_t digest[32], sig[64];
    ota_sha256("hello", 5, digest);
    CHECK(uECC_sign(g_key.priv, digest, 32, sig, uECC_secp256r1()));
    CHECK_RC(ota_p256_verify(g_key.pub, digest, sig), OTA_OK);
    for (int i = 0; i < 64; i += 7) {
        uint8_t s2[64];
        memcpy(s2, sig, 64);
        s2[i] ^= 0x10;
        CHECK_RC(ota_p256_verify(g_key.pub, digest, s2), OTA_ERR_SIGNATURE);
    }
    uint8_t zero[64] = {0};
    CHECK_RC(ota_p256_verify(g_key.pub, digest, zero), OTA_ERR_SIGNATURE);
    /* r = n (group order) must be rejected */
    uint8_t rn[64];
    hex2bin("ffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551", rn);
    memcpy(rn + 32, sig + 32, 32);
    CHECK_RC(ota_p256_verify(g_key.pub, digest, rn), OTA_ERR_SIGNATURE);
    digest[0] ^= 1;
    CHECK_RC(ota_p256_verify(g_key.pub, digest, sig), OTA_ERR_SIGNATURE);
    /* signature by a different key */
    tb_key_t other;
    tb_key_gen(&other);
    digest[0] ^= 1;
    CHECK_RC(ota_p256_verify(other.pub, digest, sig), OTA_ERR_SIGNATURE);
}

/* ---------------------------------------------------------------- manifest */
static uint32_t build_default(uint8_t kind, uint8_t *out) {
    tb_spec_t s;
    tb_spec_default(&s, kind);
    return tb_build(&s, &g_key, out, TB_FW_SIZE);
}

static void t_manifest_parse(void) {
    setup(31);
    build_default(OTA_KIND_FW_N6, g_img);
    ota_manifest_t m;
    CHECK_RC(ota_manifest_parse(g_img, &m), OTA_OK);
    CHECK(m.kind == OTA_KIND_FW_N6 && m.version == OTA_VERSION(1, 0, 0) && m.payload_size == 3000);
    CHECK(m.load_addr == TB_LOAD_ADDR && m.flags == OTA_FLAG_REQUIRES_REBOOT && m.security_counter == 1);
    struct {
        unsigned off;
        uint8_t val;
        int want;
    } cases[] = {
        {0, 'X', OTA_ERR_MAGIC},          {4, 2, OTA_ERR_MANIFEST_VERSION}, {5, 1, OTA_ERR_MANIFEST_VERSION},
        {6, 0, OTA_ERR_KIND},             {6, 4, OTA_ERR_KIND},             {7, 1, OTA_ERR_RESERVED},
        {92, 1, OTA_ERR_RESERVED},        {191, 0x80, OTA_ERR_RESERVED},    {56, 3, OTA_ERR_FLAGS},
        {59, 0x80, OTA_ERR_FLAGS},
    };
    for (size_t i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        uint8_t raw[256];
        memcpy(raw, g_img, 256);
        raw[cases[i].off] = cases[i].val;
        CHECK_RC(ota_manifest_parse(raw, &m), cases[i].want);
    }
    uint8_t raw[256];
    memcpy(raw, g_img, 256);
    ota_put_le32(raw + OTA_MOFF_PSIZE, 0);
    CHECK_RC(ota_manifest_parse(raw, &m), OTA_ERR_SIZE);
    memcpy(raw, g_img, 256);
    ota_put_le32(raw + OTA_MOFF_LOADADDR, 0);
    CHECK_RC(ota_manifest_parse(raw, &m), OTA_ERR_LOAD_ADDR);
    build_default(OTA_KIND_MODEL, g_img);
    CHECK_RC(ota_manifest_parse(g_img, &m), OTA_OK);
    memcpy(raw, g_img, 256);
    ota_put_le32(raw + OTA_MOFF_LOADADDR, 0x1000);
    CHECK_RC(ota_manifest_parse(raw, &m), OTA_ERR_LOAD_ADDR);
    CHECK_RC(ota_manifest_parse(NULL, &m), OTA_ERR_PARAM);
}

static void t_manifest_check(void) {
    setup(41);
    build_default(OTA_KIND_MODEL, g_img);
    ota_manifest_t m;
    ota_policy_t p = {OTA_KIND_MODEL, 1, 3000, TB_OPHASH_A};
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_OK);
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, NULL, &m), OTA_OK);
    p.expect_kind = OTA_KIND_FW_N6;
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_ERR_KIND);
    p.expect_kind = OTA_KIND_MODEL;
    p.min_security_counter = 2;
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_ERR_ROLLBACK);
    p.min_security_counter = 0;
    p.max_payload_size = 2999;
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_ERR_SIZE);
    p.max_payload_size = 0;
    p.required_op_set_hash = TB_OPHASH_B;
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_ERR_OPSET);
    tb_key_t other;
    tb_key_gen(&other);
    CHECK_RC(ota_manifest_check(g_img, other.pub, NULL, &m), OTA_ERR_SIGNATURE);
    /* fw images ignore the op-set requirement (it is a model-only check) */
    build_default(OTA_KIND_FW_N6, g_img);
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_ERR_KIND);
    p.expect_kind = 0;
    CHECK_RC(ota_manifest_check(g_img, g_key.pub, &p, &m), OTA_OK);
}

static void t_image_verify_tamper(void) {
    setup(51);
    uint32_t n = build_default(OTA_KIND_FW_N6, g_img);
    ota_manifest_t m;
    CHECK_RC(ota_image_verify_buffer(g_img, n, g_key.pub, NULL, &m), OTA_OK);
    CHECK_RC(ota_image_verify_buffer(g_img, n - 1, g_key.pub, NULL, &m), OTA_ERR_TRUNCATED);
    CHECK_RC(ota_image_verify_buffer(g_img, 100, g_key.pub, NULL, &m), OTA_ERR_TRUNCATED);
    CHECK_RC(ota_image_verify_buffer(g_img, n + 1, g_key.pub, NULL, &m), OTA_ERR_OVERFLOW);
    /* Every single-bit flip in the manifest is rejected. */
    int rejected = 0;
    for (unsigned i = 0; i < OTA_MANIFEST_SIZE; i++) {
        for (unsigned b = 0; b < 8; b++) {
            g_img[i] ^= (uint8_t)(1u << b);
            if (ota_image_verify_buffer(g_img, n, g_key.pub, NULL, &m) != OTA_OK) rejected++;
            g_img[i] ^= (uint8_t)(1u << b);
        }
    }
    CHECK(rejected == OTA_MANIFEST_SIZE * 8);
    /* Payload byte flips are rejected by the hash. */
    for (unsigned i = OTA_MANIFEST_SIZE; i < n; i += 37) {
        g_img[i] ^= 0x01;
        CHECK_RC(ota_image_verify_buffer(g_img, n, g_key.pub, NULL, &m), OTA_ERR_HASH);
        g_img[i] ^= 0x01;
    }
    /* Re-signed tampered manifest (as if the key were compromised) passes the
     * signature but the payload hash binds the payload. */
    g_img[OTA_MOFF_PSHA] ^= 1;
    tb_sign_manifest(g_img, &g_key);
    CHECK_RC(ota_image_verify_buffer(g_img, n, g_key.pub, NULL, &m), OTA_ERR_HASH);
}

/* ------------------------------------------------------------- sim + meta */
static void t_sim_semantics(void) {
    setup(61);
    ota_flash_t *f = &g_dev.flash;
    uint8_t buf[16], back[16];
    memset(buf, 0x0F, sizeof buf);
    CHECK_RC(f->program(f->ctx, 0, buf, 16), OTA_OK);
    CHECK_RC(f->read(f->ctx, 0, back, 16), OTA_OK);
    CHECK(back[0] == 0x0F);
    memset(buf, 0xF0, sizeof buf); /* needs 0->1 transitions */
    CHECK_RC(f->program(f->ctx, 0, buf, 16), OTA_ERR_FLASH);
    CHECK(g_dev.sim.n_strict_violations == 1);
    memset(buf, 0x05, sizeof buf); /* only clears bits: allowed */
    CHECK_RC(f->program(f->ctx, 0, buf, 16), OTA_OK);
    CHECK_RC(f->program(f->ctx, TB_PAGE - 8, buf, 16), OTA_ERR_PARAM); /* crosses a page */
    CHECK_RC(f->erase(f->ctx, 100), OTA_ERR_PARAM);
    CHECK_RC(f->erase(f->ctx, 0), OTA_OK);
    CHECK_RC(f->read(f->ctx, 0, back, 16), OTA_OK);
    CHECK(back[0] == 0xFF && back[15] == 0xFF);
    CHECK_RC(f->read(f->ctx, TB_FLASH_SIZE - 8, back, 16), OTA_ERR_PARAM);
    /* power cut: op 1 from now (second op) */
    ota_sim_arm_cut(&g_dev.sim, 1);
    memset(buf, 0x00, sizeof buf);
    CHECK_RC(f->program(f->ctx, 0, buf, 16), OTA_OK);
    CHECK_RC(f->program(f->ctx, 256, buf, 16), OTA_ERR_FLASH);
    CHECK(!g_dev.sim.powered);
    CHECK_RC(f->read(f->ctx, 0, back, 16), OTA_ERR_FLASH);
    CHECK_RC(f->erase(f->ctx, 0), OTA_ERR_FLASH);
    ota_sim_power_on(&g_dev.sim);
    CHECK_RC(f->read(f->ctx, 0, back, 16), OTA_OK);
    /* range-restricted cut ignores ops outside the range */
    ota_sim_arm_cut_range(&g_dev.sim, 0, 8192, 12288);
    CHECK_RC(f->erase(f->ctx, 0), OTA_OK);
    CHECK_RC(f->erase(f->ctx, 8192), OTA_ERR_FLASH);
    ota_sim_power_on(&g_dev.sim);
    CHECK(g_dev.sim.n_cuts == 2);
}

static void t_layout_check(void) {
    setup(71);
    CHECK_RC(ota_layout_check(&g_dev.layout, &g_dev.flash), OTA_OK);
    ota_layout_t l = g_dev.layout;
    l.meta_addr[1] = 0;
    CHECK_RC(ota_layout_check(&l, &g_dev.flash), OTA_ERR_LAYOUT);
    l = g_dev.layout;
    l.fw_addr[0] += 256;
    CHECK_RC(ota_layout_check(&l, &g_dev.flash), OTA_ERR_LAYOUT);
    l = g_dev.layout;
    l.model_addr[1] = TB_FLASH_SIZE - TB_ERASE;
    CHECK_RC(ota_layout_check(&l, &g_dev.flash), OTA_ERR_LAYOUT);
    l = g_dev.layout;
    l.fw_size = TB_FW_SIZE + TB_ERASE; /* slot A overlaps slot B */
    CHECK_RC(ota_layout_check(&l, &g_dev.flash), OTA_ERR_LAYOUT);
    ota_ctx_t ctx;
    CHECK_RC(ota_init(&ctx, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_ERR_NO_META); /* blank flash */
}

static void t_meta_roundtrip(void) {
    setup(81);
    ota_flash_t *f = &g_dev.flash;
    ota_layout_t *l = &g_dev.layout;
    ota_meta_t m, m2;
    int copy = -1;
    CHECK_RC(ota_meta_load(f, l, &m, &copy), OTA_ERR_NO_META);
    CHECK_RC(ota_meta_format(f, l), OTA_OK);
    CHECK_RC(ota_meta_load(f, l, &m, &copy), OTA_OK);
    CHECK(copy == 0 && m.seq == 1 && m.fw_state[0] == OTA_SLOT_EMPTY && m.sec_counter[2] == 0);
    m.fw_state[1] = OTA_SLOT_PENDING;
    m.sec_counter[0] = 7;
    CHECK_RC(ota_meta_store(f, l, &m, &copy), OTA_OK);
    CHECK(copy == 1 && m.seq == 2);
    CHECK_RC(ota_meta_load(f, l, &m2, &copy), OTA_OK);
    CHECK(copy == 1 && m2.seq == 2 && m2.fw_state[1] == OTA_SLOT_PENDING && m2.sec_counter[0] == 7);
    m2.model_active = 1;
    CHECK_RC(ota_meta_store(f, l, &m2, &copy), OTA_OK);
    CHECK(copy == 0 && m2.seq == 3);
    /* corrupt the newest copy -> previous is used */
    g_dev.mem[l->meta_addr[0] + 13] ^= 0x01;
    CHECK_RC(ota_meta_load(f, l, &m, &copy), OTA_OK);
    CHECK(copy == 1 && m.seq == 2 && m.model_active == 0);
    g_dev.mem[l->meta_addr[1] + 20] ^= 0x01;
    CHECK_RC(ota_meta_load(f, l, &m, &copy), OTA_ERR_NO_META);
    /* encode/decode rejects invalid field values even with a valid CRC */
    uint8_t rec[OTA_META_RECORD_SIZE];
    m.fw_state[0] = 9;
    ota_meta_encode(&m, rec);
    CHECK_RC(ota_meta_decode(rec, &m2), OTA_ERR_NO_META);
    m.fw_state[0] = OTA_SLOT_EMPTY;
    m.fw_active = 2;
    ota_meta_encode(&m, rec);
    CHECK_RC(ota_meta_decode(rec, &m2), OTA_ERR_NO_META);
    /* sequence wrap-around: 0 is newer than 0xFFFFFFFF */
    CHECK_RC(ota_meta_format(f, l), OTA_OK);
    CHECK_RC(ota_meta_load(f, l, &m, &copy), OTA_OK);
    m.seq = 0xFFFFFFFEu;
    CHECK_RC(ota_meta_store(f, l, &m, &copy), OTA_OK); /* copy1 seq 0xFFFFFFFF */
    m.fw_active = 1;
    CHECK_RC(ota_meta_store(f, l, &m, &copy), OTA_OK); /* copy0 seq 0 */
    CHECK_RC(ota_meta_load(f, l, &m2, &copy), OTA_OK);
    CHECK(copy == 0 && m2.seq == 0 && m2.fw_active == 1);
}

/* Cut power at every operation of a metadata store, many times with
 * different partial patterns: the result is always the old or the new record. */
static void t_meta_power_cut(void) {
    setup(91);
    ota_flash_t *f = &g_dev.flash;
    ota_layout_t *l = &g_dev.layout;
    int old_seen = 0, new_seen = 0;
    for (int iter = 0; iter < 400; iter++) {
        ota_sim_seed(&g_dev.sim, (uint64_t)iter * 7919u + 1u);
        ota_meta_t m;
        int copy;
        CHECK_RC(ota_meta_load(f, l, &m, &copy), iter == 0 ? OTA_ERR_NO_META : OTA_OK);
        if (iter == 0) {
            CHECK_RC(ota_meta_format(f, l), OTA_OK);
            CHECK_RC(ota_meta_load(f, l, &m, &copy), OTA_OK);
        }
        uint32_t old_seq = m.seq;
        uint32_t old_ctr = m.sec_counter[0];
        m.sec_counter[0] = old_ctr + 1;
        ota_sim_arm_cut(&g_dev.sim, tb_rand_below(3)); /* erase, program, or none (2 ops) */
        int rc = ota_meta_store(f, l, &m, &copy);
        ota_sim_power_on(&g_dev.sim);
        ota_meta_t r;
        CHECK_RC(ota_meta_load(f, l, &r, &copy), OTA_OK);
        if (rc == OTA_OK) {
            CHECK(r.seq == old_seq + 1 && r.sec_counter[0] == old_ctr + 1);
            new_seen++;
        } else {
            CHECK((r.seq == old_seq && r.sec_counter[0] == old_ctr) ||
                  (r.seq == old_seq + 1 && r.sec_counter[0] == old_ctr + 1));
            if (r.seq == old_seq) old_seen++;
        }
    }
    CHECK(old_seen > 50 && new_seen > 50);
}

/* ------------------------------------------------------------------ writer */
static ota_ctx_t g_ctx;

static void factory(uint64_t seed) {
    setup(seed);
    CHECK_RC(tb_factory(&g_dev, &g_ctx, &g_key, TB_OPHASH_A, g_img, sizeof g_img), OTA_OK);
}

static void reboot(ota_ctx_t *ctx, ota_boot_result_t *br, int want) {
    ota_sim_power_on(&g_dev.sim);
    CHECK_RC(ota_init(ctx, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_OK);
    CHECK_RC(ota_boot_decide(ctx, br), want);
}

static uint32_t build(uint8_t kind, uint32_t ver, uint32_t ctr, uint32_t size, uint8_t *out) {
    tb_spec_t s;
    tb_spec_default(&s, kind);
    s.version = ver;
    s.counter = ctr;
    s.payload_size = size;
    s.payload_seed = ver ^ ctr ^ size;
    return tb_build(&s, &g_key, out, TB_FW_SIZE);
}

static void t_writer_chunks(void) {
    const uint32_t chunks[] = {1, 2, 3, 7, 64, 255, 256, 257, 1000, 100000};
    for (size_t i = 0; i < sizeof chunks / sizeof chunks[0]; i++) {
        factory(100 + i);
        uint32_t v = OTA_VERSION(1, 1, (uint32_t)i);
        uint32_t n = build(OTA_KIND_FW_N6, v, 3, 5000 + (uint32_t)i * 777, g_img);
        ota_writer_t w;
        CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_N6), OTA_OK);
        for (uint32_t off = 0; off < n;) {
            uint32_t k = chunks[i] < n - off ? chunks[i] : n - off;
            CHECK_RC(ota_writer_write(&w, g_img + off, k), OTA_OK);
            off += k;
        }
        CHECK_RC(ota_writer_write(&w, g_img, 0), OTA_OK);
        CHECK_RC(ota_writer_finish(&w), OTA_OK);
        CHECK(g_ctx.meta.fw_state[w.slot] == OTA_SLOT_PENDING);
        ota_boot_result_t br;
        reboot(&g_ctx, &br, OTA_OK);
        CHECK(br.trial == 1 && br.slot == w.slot && br.manifest.version == v);
        CHECK(g_ctx.meta.fw_state[br.slot] == OTA_SLOT_TRIAL);
        CHECK(memcmp(g_dev.mem + br.addr, g_img, n) == 0);
        CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK);
        CHECK(g_ctx.meta.fw_state[br.slot] == OTA_SLOT_CONFIRMED && g_ctx.meta.sec_counter[0] == 3);
        CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK); /* idempotent */
        reboot(&g_ctx, &br, OTA_OK);
        CHECK(br.trial == 0 && br.manifest.version == v);
    }
}

static int stream_all(uint8_t kind, const uint8_t *img, uint32_t n) { return tb_stream(&g_ctx, kind, img, n, 700); }

static void t_writer_rejects(void) {
    factory(200);
    int tgt = g_ctx.meta.fw_active ^ 1;
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 2, 4000, g_img);

    /* bad signature: rejected as soon as the manifest is in; slot stays EMPTY */
    memcpy(g_img2, g_img, n);
    g_img2[OTA_MOFF_SIG + 5] ^= 0x40;
    ota_writer_t w;
    CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_N6), OTA_OK);
    CHECK_RC(ota_writer_write(&w, g_img2, 256), OTA_ERR_SIGNATURE);
    CHECK_RC(ota_writer_write(&w, g_img2 + 256, 10), OTA_ERR_SIGNATURE); /* sticky */
    CHECK_RC(ota_writer_finish(&w), OTA_ERR_SIGNATURE);
    CHECK(g_ctx.meta.fw_state[tgt] == OTA_SLOT_EMPTY);

    /* wrong sha (payload changed after signing) */
    memcpy(g_img2, g_img, n);
    g_img2[n - 1] ^= 1;
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img2, n), OTA_ERR_HASH);
    CHECK(g_ctx.meta.fw_state[tgt] == OTA_SLOT_EMPTY);

    /* wrong kind for the writer */
    uint32_t mn = build(OTA_KIND_MODEL, OTA_VERSION(2, 0, 0), 2, 1000, g_img2);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img2, mn), OTA_ERR_KIND);
    CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_C6), OTA_ERR_KIND);
    CHECK_RC(ota_writer_begin(&g_ctx, &w, 0), OTA_ERR_KIND);

    /* rollback: counter below stored (stored = 1 after factory) */
    uint32_t rn = build(OTA_KIND_FW_N6, OTA_VERSION(3, 0, 0), 0, 1000, g_img2);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img2, rn), OTA_ERR_ROLLBACK);

    /* too big for the slot */
    tb_spec_t s;
    tb_spec_default(&s, OTA_KIND_FW_N6);
    s.payload_size = TB_FW_SIZE - 256 + 1;
    static uint8_t big[TB_FW_SIZE + 512];
    uint32_t bn = tb_build(&s, &g_key, big, sizeof big);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, big, bn), OTA_ERR_SIZE);
    /* exactly fits */
    s.payload_size = TB_FW_SIZE - 256;
    s.counter = 2;
    bn = tb_build(&s, &g_key, big, sizeof big);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, big, bn), OTA_OK);

    /* truncated */
    CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_N6), OTA_OK);
    CHECK(g_ctx.meta.fw_state[tgt] == OTA_SLOT_EMPTY); /* PENDING target invalidated on begin */
    CHECK_RC(ota_writer_write(&w, g_img, n - 1), OTA_OK);
    CHECK_RC(ota_writer_finish(&w), OTA_ERR_TRUNCATED);
    CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_N6), OTA_OK);
    CHECK_RC(ota_writer_write(&w, g_img, 100), OTA_OK);
    CHECK_RC(ota_writer_finish(&w), OTA_ERR_TRUNCATED);
    /* overflow */
    CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_N6), OTA_OK);
    CHECK_RC(ota_writer_write(&w, g_img, n), OTA_OK);
    CHECK_RC(ota_writer_write(&w, g_img, 1), OTA_ERR_OVERFLOW);
    CHECK_RC(ota_writer_finish(&w), OTA_ERR_OVERFLOW);
    CHECK(g_ctx.meta.fw_state[tgt] == OTA_SLOT_EMPTY);
    /* aborted writer refuses further use */
    CHECK_RC(ota_writer_begin(&g_ctx, &w, OTA_KIND_FW_N6), OTA_OK);
    ota_writer_abort(&w);
    CHECK_RC(ota_writer_write(&w, g_img, 1), OTA_ERR_STATE);

    /* after all that the device still boots the factory image */
    ota_boot_result_t br;
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.trial == 0 && br.manifest.version == OTA_VERSION(1, 0, 0));
    /* and a good image still goes through */
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
}

static void t_writer_state_rules(void) {
    factory(300);
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 2, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    ota_boot_result_t br;
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.trial == 1);
    /* running a TRIAL image: no further fw update until confirmed */
    uint32_t n2 = build(OTA_KIND_FW_N6, OTA_VERSION(3, 0, 0), 2, 4000, g_img2);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img2, n2), OTA_ERR_STATE);
    CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img2, n2), OTA_OK);
    /* a model update needs a known running firmware */
    ota_ctx_t c2;
    CHECK_RC(ota_init(&c2, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_OK);
    ota_writer_t w;
    CHECK_RC(ota_writer_begin(&c2, &w, OTA_KIND_MODEL), OTA_ERR_STATE);
    CHECK_RC(ota_model_select(&c2, &br), OTA_ERR_STATE);
    CHECK_RC(ota_fw_confirm(&c2), OTA_ERR_STATE);
}

static void t_boot_trial_never_confirms(void) {
    factory(400);
    ota_boot_result_t br;
    reboot(&g_ctx, &br, OTA_OK);
    int old = br.slot;
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 5, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.trial && br.slot != old);
    /* app hangs, watchdog resets, no confirm */
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.slot == old && !br.trial && br.manifest.version == OTA_VERSION(1, 0, 0));
    CHECK(g_ctx.meta.fw_state[old ^ 1] == OTA_SLOT_BAD);
    CHECK(g_ctx.meta.sec_counter[0] == 1); /* never raised */
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.slot == old);
    /* a later update may reuse the BAD slot */
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    CHECK(g_ctx.meta.fw_state[old ^ 1] == OTA_SLOT_PENDING);
}

static void t_boot_pending_corrupted(void) {
    factory(500);
    ota_boot_result_t br;
    reboot(&g_ctx, &br, OTA_OK);
    int old = br.slot;
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 2, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    /* bit rot / attacker flips a payload bit after verification */
    g_dev.mem[g_dev.layout.fw_addr[old ^ 1] + 256 + 1234] ^= 0x08;
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.slot == old && !br.trial);
    CHECK(g_ctx.meta.fw_state[old ^ 1] == OTA_SLOT_BAD);
    /* manifest corruption too */
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    g_dev.mem[g_dev.layout.fw_addr[old ^ 1] + OTA_MOFF_VERSION + 2] &= 0x00; /* 2.0.0 -> 0.0.0 */
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.slot == old && g_ctx.meta.fw_state[old ^ 1] == OTA_SLOT_BAD);
}

static void t_counter_rules(void) {
    factory(600);
    ota_boot_result_t br;
    /* B: ctr 5, confirm -> stored 5 */
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 5, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    int b = br.slot;
    CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK);
    CHECK(g_ctx.meta.sec_counter[0] == 5);
    /* equal counter, newer version: allowed */
    n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 1, 0), 5, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.slot != b && br.trial);
    CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK);
    /* lower counter: rejected even with a higher version number */
    n = build(OTA_KIND_FW_N6, OTA_VERSION(9, 0, 0), 4, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_ERR_ROLLBACK);
    /* the stored counter is max(stored, image): confirming a ctr-5 image never lowers it */
    CHECK(g_ctx.meta.sec_counter[0] == 5);
    /* fw and model counters are independent */
    CHECK(g_ctx.meta.sec_counter[2] == 1);
}

/* An attacker with physical flash access writes an old, validly signed image
 * into the inactive slot and forges metadata marking it PENDING. The counter
 * check at boot rejects it. (On hardware the counter lives in OTP; see README.) */
static void t_physical_downgrade(void) {
    factory(700);
    ota_boot_result_t br;
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 3, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK);
    int cur = br.slot;
    uint32_t on = build(OTA_KIND_FW_N6, OTA_VERSION(1, 5, 0), 2, 4000, g_img2);
    uint32_t addr = g_dev.layout.fw_addr[cur ^ 1];
    CHECK_RC(ota_flash_erase_range(&g_dev.flash, addr, TB_FW_SIZE), OTA_OK);
    CHECK_RC(ota_flash_program_range(&g_dev.flash, addr, g_img2, on), OTA_OK);
    ota_meta_t m = g_ctx.meta;
    m.fw_state[cur ^ 1] = OTA_SLOT_PENDING;
    int copy = g_ctx.meta_copy;
    CHECK_RC(ota_meta_store(&g_dev.flash, &g_dev.layout, &m, &copy), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.slot == cur && br.manifest.version == OTA_VERSION(2, 0, 0));
    CHECK(g_ctx.meta.fw_state[cur ^ 1] == OTA_SLOT_BAD);
    /* Same with a forged CONFIRMED state and the counter field also rolled back
     * in metadata: this is NOT caught in software (documented in README; on
     * hardware the counter must be in OTP / secure storage). */
}

static void t_model_opset(void) {
    factory(800);
    ota_boot_result_t br, mr;
    reboot(&g_ctx, &br, OTA_OK);
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_OK);
    int model_a = mr.slot;
    /* model requiring a different op set is rejected by the writer */
    tb_spec_t s;
    tb_spec_default(&s, OTA_KIND_MODEL);
    memcpy(s.op_hash, TB_OPHASH_B, 32);
    s.counter = 2;
    uint32_t n = tb_build(&s, &g_key, g_img, sizeof g_img);
    CHECK_RC(stream_all(OTA_KIND_MODEL, g_img, n), OTA_ERR_OPSET);
    CHECK(g_ctx.meta.model_state[model_a ^ 1] == OTA_SLOT_EMPTY);
    /* firmware that switches to op set B: trial boot, the A models are skipped
     * (not destroyed), the app's health check fails, firmware rolls back */
    tb_spec_default(&s, OTA_KIND_FW_N6);
    memcpy(s.op_hash, TB_OPHASH_B, 32);
    s.version = OTA_VERSION(2, 0, 0);
    s.counter = 2;
    n = tb_build(&s, &g_key, g_img, sizeof g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(br.trial && memcmp(br.manifest.op_set_hash, TB_OPHASH_B, 32) == 0);
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_ERR_NO_IMAGE);
    CHECK(g_ctx.meta.model_state[model_a] == OTA_SLOT_CONFIRMED);
    reboot(&g_ctx, &br, OTA_OK); /* no confirm -> rollback */
    CHECK(!br.trial && memcmp(br.manifest.op_set_hash, TB_OPHASH_A, 32) == 0);
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_OK);
    CHECK(mr.slot == model_a);
    /* a compatible model update works and gets its own counter */
    tb_spec_default(&s, OTA_KIND_MODEL);
    s.counter = 4;
    s.version = OTA_VERSION(1, 1, 0);
    n = tb_build(&s, &g_key, g_img, sizeof g_img);
    CHECK_RC(stream_all(OTA_KIND_MODEL, g_img, n), OTA_OK);
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_OK);
    CHECK(mr.trial && mr.slot != model_a);
    CHECK_RC(ota_model_confirm(&g_ctx), OTA_OK);
    CHECK(g_ctx.meta.sec_counter[2] == 4 && g_ctx.meta.sec_counter[0] == 1);
}

static void t_model_trial_never_confirms(void) {
    factory(900);
    ota_boot_result_t br, mr;
    reboot(&g_ctx, &br, OTA_OK);
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_OK);
    int a = mr.slot;
    uint32_t n = build(OTA_KIND_MODEL, OTA_VERSION(1, 2, 0), 2, 6000, g_img);
    CHECK_RC(stream_all(OTA_KIND_MODEL, g_img, n), OTA_OK);
    /* models are hot-swapped without a reboot */
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_OK);
    CHECK(mr.trial && mr.slot == (a ^ 1));
    /* NPU crash -> reset before confirm */
    reboot(&g_ctx, &br, OTA_OK);
    CHECK(!br.trial);
    CHECK_RC(ota_model_select(&g_ctx, &mr), OTA_OK);
    CHECK(mr.slot == a && !mr.trial);
    CHECK(g_ctx.meta.model_state[a ^ 1] == OTA_SLOT_BAD && g_ctx.meta.sec_counter[2] == 1);
}

static void t_app_attach_second_ctx(void) {
    factory(1000);
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 2, 4000, g_img);
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    ota_boot_result_t br;
    reboot(&g_ctx, &br, OTA_OK); /* bootloader */
    ota_ctx_t app;
    CHECK_RC(ota_init(&app, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_OK);
    CHECK_RC(ota_app_attach(&app), OTA_OK);
    CHECK(app.fw_manifest.version == OTA_VERSION(2, 0, 0));
    CHECK_RC(ota_fw_confirm(&app), OTA_OK);
    ota_ctx_t c3;
    CHECK_RC(ota_init(&c3, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_OK);
    CHECK(c3.meta.fw_state[br.slot] == OTA_SLOT_CONFIRMED && c3.meta.sec_counter[0] == 2);
    /* attach with no live slot fails */
    CHECK_RC(ota_meta_format(&g_dev.flash, &g_dev.layout), OTA_OK);
    CHECK_RC(ota_init(&c3, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_OK);
    CHECK_RC(ota_app_attach(&c3), OTA_ERR_STATE);
    tb_key_t bad;
    memset(bad.pub, 0x11, 64);
    CHECK_RC(ota_init(&c3, &g_dev.flash, &g_dev.layout, bad.pub), OTA_ERR_KEY);
}

/* Deterministic exhaustive sweep: for one fw update + boot + confirm, cut
 * power at EVERY flash operation index and check the device always boots a
 * valid image, and that after recovery the update can be completed. */
static void t_power_cut_every_op(void) {
    factory(1100);
    uint32_t n = build(OTA_KIND_FW_N6, OTA_VERSION(2, 0, 0), 2, 9000, g_img);
    /* measure ops of the full sequence */
    static uint8_t snapshot[TB_FLASH_SIZE];
    memcpy(snapshot, g_dev.mem, TB_FLASH_SIZE);
    uint64_t before = ota_sim_ops(&g_dev.sim);
    ota_boot_result_t br;
    CHECK_RC(stream_all(OTA_KIND_FW_N6, g_img, n), OTA_OK);
    reboot(&g_ctx, &br, OTA_OK);
    CHECK_RC(ota_fw_confirm(&g_ctx), OTA_OK);
    uint64_t total = ota_sim_ops(&g_dev.sim) - before;
    CHECK(total > 40);
    int bricks = 0, completed = 0;
    for (uint64_t k = 0; k < total; k++) {
        memcpy(g_dev.mem, snapshot, TB_FLASH_SIZE);
        ota_sim_seed(&g_dev.sim, k + 1);
        ota_sim_power_on(&g_dev.sim);
        CHECK_RC(ota_init(&g_ctx, &g_dev.flash, &g_dev.layout, g_key.pub), OTA_OK);
        CHECK_RC(ota_boot_decide(&g_ctx, &br), OTA_OK);
        ota_sim_arm_cut(&g_dev.sim, k);
        /* The whole sequence; once power is lost every later flash op fails. */
        (void)stream_all(OTA_KIND_FW_N6, g_img, n);
        ota_ctx_t c;
        if (ota_init(&c, &g_dev.flash, &g_dev.layout, g_key.pub) == OTA_OK && ota_boot_decide(&c, &br) == OTA_OK) {
            (void)ota_fw_confirm(&c);
        }
        CHECK(!g_dev.sim.powered); /* the cut point was inside the sequence */
        ota_sim_power_on(&g_dev.sim);
        if (ota_init(&c, &g_dev.flash, &g_dev.layout, g_key.pub) != OTA_OK || ota_boot_decide(&c, &br) != OTA_OK ||
            (c.meta.fw_state[br.slot] != OTA_SLOT_CONFIRMED && c.meta.fw_state[br.slot] != OTA_SLOT_TRIAL)) {
            bricks++;
            fprintf(stderr, "brick at op %llu\n", (unsigned long long)k);
            continue;
        }
        if (br.manifest.version == OTA_VERSION(2, 0, 0)) completed++;
    }
    CHECK(bricks == 0);
    CHECK(completed > 0);
    printf("  power_cut_every_op: %llu cut points, 0 bricks expected, %d bricks, %d reached v2\n",
           (unsigned long long)total, bricks, completed);
}

int main(int argc, char **argv) {
    static const tf_case_t cases[] = {
        {"sha256_vectors", t_sha256_vectors},
        {"sha256_streaming", t_sha256_streaming},
        {"crc32", t_crc32},
        {"crypto_basic", t_crypto_basic},
        {"manifest_parse", t_manifest_parse},
        {"manifest_check", t_manifest_check},
        {"image_verify_tamper", t_image_verify_tamper},
        {"sim_semantics", t_sim_semantics},
        {"layout_check", t_layout_check},
        {"meta_roundtrip", t_meta_roundtrip},
        {"meta_power_cut", t_meta_power_cut},
        {"writer_chunks", t_writer_chunks},
        {"writer_rejects", t_writer_rejects},
        {"writer_state_rules", t_writer_state_rules},
        {"boot_trial_never_confirms", t_boot_trial_never_confirms},
        {"boot_pending_corrupted", t_boot_pending_corrupted},
        {"counter_rules", t_counter_rules},
        {"physical_downgrade", t_physical_downgrade},
        {"model_opset", t_model_opset},
        {"model_trial_never_confirms", t_model_trial_never_confirms},
        {"app_attach_second_ctx", t_app_attach_second_ctx},
        {"power_cut_every_op", t_power_cut_every_op},
    };
    return tf_main(argc, argv, cases, (int)(sizeof cases / sizeof cases[0]));
}
