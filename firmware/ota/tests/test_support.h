/* Shared host-test fixtures: sim flash + layout, test keys, C-side signer. */
#ifndef OTA_TEST_SUPPORT_H
#define OTA_TEST_SUPPORT_H
#include <stdint.h>
#include "ota/ota.h"
#include "ota_flash_sim.h"

#define TB_FLASH_SIZE (256u * 1024u)
#define TB_ERASE 4096u
#define TB_PAGE 256u
#define TB_FW_SIZE (64u * 1024u)
#define TB_MODEL_SIZE (32u * 1024u)
#define TB_LOAD_ADDR 0x70100000u

typedef struct {
    uint8_t mem[TB_FLASH_SIZE];
    ota_sim_flash_t sim;
    ota_flash_t flash;
    ota_layout_t layout;
} tb_dev_t;

void tb_dev_init(tb_dev_t *d);

/* Deterministic RNG used by micro-ecc for key generation and signing. */
void tb_rng_seed(uint64_t seed);
uint64_t tb_rand(void);
uint32_t tb_rand_below(uint32_t n);

typedef struct {
    uint8_t priv[32];
    uint8_t pub[64];
} tb_key_t;
void tb_key_gen(tb_key_t *k);

typedef struct {
    uint8_t kind;
    uint32_t version;
    uint32_t counter;
    uint32_t load_addr;
    uint32_t flags;
    uint8_t op_hash[32];
    uint32_t payload_size;
    uint32_t payload_seed;
} tb_spec_t;

void tb_spec_default(tb_spec_t *s, uint8_t kind);
/* Build manifest + payload into out (cap bytes). Returns image length. */
uint32_t tb_build(const tb_spec_t *s, const tb_key_t *k, uint8_t *out, uint32_t cap);
/* Re-sign the manifest at image[0..256). */
void tb_sign_manifest(uint8_t *image, const tb_key_t *k);

/* Stream an image through the writer in random chunk sizes in [1, max_chunk]. */
int tb_stream(ota_ctx_t *ctx, uint8_t kind, const uint8_t *img, uint32_t len, uint32_t max_chunk);

/* Factory provisioning: format, write fw + model, boot, confirm both. */
int tb_factory(tb_dev_t *d, ota_ctx_t *ctx, const tb_key_t *k, const uint8_t op_hash[32], uint8_t *scratch,
               uint32_t scratch_cap);

extern const uint8_t TB_OPHASH_A[32];
extern const uint8_t TB_OPHASH_B[32];
#endif
