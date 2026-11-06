/* ota_host_verify: verify a Sentinel OTA image with the C library.
 * Used by tools/tests to cross-check images produced by sentinel_sign.py.
 *
 *   ota_host_verify --pubkey PUB.bin --image IMG.bin [--kind fw_n6|fw_c6|model]
 *                   [--min-counter N] [--op-hash HEX64] [--stream CHUNK [--fw-image FW.bin]]
 *
 * PUB.bin is the raw 64-byte X||Y public key (65 bytes with a 0x04 prefix is
 * also accepted). Default mode verifies the whole image in memory. --stream
 * feeds the image through the streaming writer of a simulated device in
 * CHUNK-byte pieces (0 = random sizes), then boots / selects it. In stream
 * mode --kind picks the slot writer (as the device would from the MQTT ota
 * message); without it the image's own kind byte is used. A model
 * image needs --fw-image (the firmware whose op-set hash it must match).
 *
 * Prints "OK ..." and exits 0, or "REJECT <error>" and exits 1. Usage errors exit 2. */
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ota/ota.h"
#include "ota_flash_sim.h"

#define MAX_IMAGE (2u * 1024u * 1024u)
#define FLASH_SIZE (8u * 1024u * 1024u)
#define ERASE 4096u
#define PAGE 256u
#define FW_SLOT (2u * 1024u * 1024u)
#define MODEL_SLOT (1u * 1024u * 1024u)

static uint8_t g_flash_mem[FLASH_SIZE];
static uint8_t g_image[MAX_IMAGE];
static uint8_t g_fw_image[MAX_IMAGE];

static long read_file(const char *path, uint8_t *buf, size_t cap) {
    FILE *f = fopen(path, "rb");
    if (!f) {
        fprintf(stderr, "cannot open %s\n", path);
        return -1;
    }
    size_t n = fread(buf, 1, cap, f);
    int more = fgetc(f) != EOF;
    fclose(f);
    if (more) {
        fprintf(stderr, "%s is too large\n", path);
        return -1;
    }
    return (long)n;
}

static int parse_hex32(const char *h, uint8_t out[32]) {
    if (strlen(h) != 64) return -1;
    for (int i = 0; i < 32; i++) {
        unsigned v;
        if (sscanf(h + 2 * i, "%2x", &v) != 1) return -1;
        out[i] = (uint8_t)v;
    }
    return 0;
}

static int parse_kind(const char *s) {
    if (!strcmp(s, "fw_n6")) return OTA_KIND_FW_N6;
    if (!strcmp(s, "fw_c6")) return OTA_KIND_FW_C6;
    if (!strcmp(s, "model")) return OTA_KIND_MODEL;
    int v = atoi(s);
    return v >= 0 && v <= 255 ? v : -1;
}

static int stream_into(ota_ctx_t *ctx, uint8_t kind, const uint8_t *img, uint32_t len, long chunk, uint64_t *rng) {
    ota_writer_t w;
    int rc = ota_writer_begin(ctx, &w, kind);
    if (rc) return rc;
    for (uint32_t off = 0; off < len;) {
        uint32_t k;
        if (chunk > 0) {
            k = (uint32_t)chunk;
        } else {
            *rng = *rng * 6364136223846793005ull + 1442695040888963407ull;
            k = 1u + (uint32_t)((*rng >> 33) % 4096u);
        }
        if (k > len - off) k = len - off;
        rc = ota_writer_write(&w, img + off, k);
        if (rc) return rc;
        off += k;
    }
    return ota_writer_finish(&w);
}

static int reject(int rc) {
    printf("REJECT %s\n", ota_err_str(rc));
    return 1;
}

int main(int argc, char **argv) {
    const char *pub_path = NULL, *img_path = NULL, *fw_path = NULL;
    int kind = 0;
    uint32_t min_counter = 0;
    uint8_t op_hash[32];
    int have_op = 0;
    long chunk = -1;
    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (!v) {
            fprintf(stderr, "missing value for %s\n", a);
            return 2;
        }
        i++;
        if (!strcmp(a, "--pubkey")) {
            pub_path = v;
        } else if (!strcmp(a, "--image")) {
            img_path = v;
        } else if (!strcmp(a, "--fw-image")) {
            fw_path = v;
        } else if (!strcmp(a, "--kind")) {
            kind = parse_kind(v);
            if (kind < 0) return 2;
        } else if (!strcmp(a, "--min-counter")) {
            min_counter = (uint32_t)strtoul(v, NULL, 0);
        } else if (!strcmp(a, "--op-hash")) {
            if (parse_hex32(v, op_hash)) return 2;
            have_op = 1;
        } else if (!strcmp(a, "--stream")) {
            chunk = strtol(v, NULL, 0);
        } else {
            fprintf(stderr, "unknown option %s\n", a);
            return 2;
        }
    }
    if (!pub_path || !img_path) {
        fprintf(stderr, "usage: %s --pubkey PUB.bin --image IMG.bin [options]\n", argv[0]);
        return 2;
    }
    uint8_t pub_raw[65];
    long pn = read_file(pub_path, pub_raw, sizeof pub_raw);
    const uint8_t *pub = pub_raw;
    if (pn == 65 && pub_raw[0] == 0x04) {
        pub = pub_raw + 1;
    } else if (pn != 64) {
        fprintf(stderr, "public key must be 64 raw bytes (X||Y)\n");
        return 2;
    }
    if (ota_p256_pubkey_check(pub) != OTA_OK) return reject(OTA_ERR_KEY);
    long n = read_file(img_path, g_image, sizeof g_image);
    if (n < 0) return 2;

    ota_manifest_t m;
    if (chunk < 0) {
        ota_policy_t pol = {(uint8_t)kind, min_counter, 0, have_op ? op_hash : NULL};
        int rc = ota_image_verify_buffer(g_image, (uint32_t)n, pub, &pol, &m);
        if (rc) return reject(rc);
    } else {
        ota_sim_flash_t sim;
        ota_flash_t flash;
        ota_sim_init(&sim, g_flash_mem, FLASH_SIZE, ERASE, PAGE, &flash);
        ota_layout_t lay = {{0, ERASE}, {2 * ERASE, 2 * ERASE + FW_SLOT}, FW_SLOT,
                            {2 * ERASE + 2 * FW_SLOT, 2 * ERASE + 2 * FW_SLOT + MODEL_SLOT}, MODEL_SLOT};
        uint64_t rng = 12345;
        if (ota_meta_format(&flash, &lay)) return reject(OTA_ERR_FLASH);
        ota_ctx_t ctx;
        int rc = ota_init(&ctx, &flash, &lay, pub);
        if (rc) return reject(rc);
        /* The device knows what it is downloading (the MQTT ota message has
         * a kind), so --kind selects the writer; otherwise trust byte 6. */
        uint8_t img_kind = kind ? (uint8_t)kind : (n >= 7 ? g_image[OTA_MOFF_KIND] : (uint8_t)OTA_KIND_FW_N6);
        ota_boot_result_t br;
        if (img_kind == OTA_KIND_MODEL) {
            if (!fw_path) {
                fprintf(stderr, "--stream of a model image needs --fw-image\n");
                return 2;
            }
            long fn = read_file(fw_path, g_fw_image, sizeof g_fw_image);
            if (fn < 0) return 2;
            rc = stream_into(&ctx, OTA_KIND_FW_N6, g_fw_image, (uint32_t)fn, 4096, &rng);
            if (rc == OTA_OK) rc = ota_boot_decide(&ctx, &br);
            if (rc == OTA_OK) rc = ota_fw_confirm(&ctx);
            if (rc) {
                fprintf(stderr, "firmware image for --fw-image was rejected\n");
                return reject(rc);
            }
        }
        if (min_counter && img_kind >= 1 && img_kind <= OTA_KIND_COUNT) {
            ctx.meta.sec_counter[img_kind - 1] = min_counter;
            rc = ota_meta_store(&flash, &lay, &ctx.meta, &ctx.meta_copy);
            if (rc) return reject(rc);
        }
        rc = stream_into(&ctx, img_kind, g_image, (uint32_t)n, chunk, &rng);
        if (rc) return reject(rc);
        rc = img_kind == OTA_KIND_MODEL ? ota_model_select(&ctx, &br) : ota_boot_decide(&ctx, &br);
        if (rc) return reject(rc);
        if (!br.trial) return reject(OTA_ERR_STATE);
        m = br.manifest;
    }
    printf("OK kind=%u version=%u.%u.%u counter=%" PRIu32 " payload_size=%" PRIu32 " flags=0x%" PRIx32 "\n", m.kind,
           (unsigned)(m.version >> 16), (unsigned)((m.version >> 8) & 0xFF), (unsigned)(m.version & 0xFF),
           m.security_counter, m.payload_size, m.flags);
    return 0;
}
