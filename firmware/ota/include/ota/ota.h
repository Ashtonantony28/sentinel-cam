/* Sentinel OTA: slot manager, boot decision, streaming writer.
 *
 * Typical use on the N6:
 *   bootloader: ota_init(); ota_boot_decide(); jump to result.slot
 *   app:        ota_init(); ota_app_attach(); ota_model_select();
 *               ... health checks ... ota_fw_confirm(); ota_model_confirm();
 *               OTA: ota_writer_begin(); ota_writer_write() per chunk; ota_writer_finish();
 * On the host, a single ota_ctx_t can play both roles. */
#ifndef OTA_H
#define OTA_H
#include <stdint.h>
#include "ota_crc32.h"
#include "ota_crypto.h"
#include "ota_flash.h"
#include "ota_manifest.h"
#include "ota_meta.h"
#include "ota_sha256.h"
#include "ota_types.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    const ota_flash_t *flash;
    const ota_layout_t *layout;
    const uint8_t *pubkey; /* 64 bytes, must outlive ctx */
    ota_meta_t meta;
    int meta_copy;
    /* Running firmware / model, filled by ota_boot_decide / ota_app_attach / ota_model_select. */
    int fw_running;        /* 1 if fw_manifest valid */
    ota_manifest_t fw_manifest;
    int model_running;
    ota_manifest_t model_manifest;
} ota_ctx_t;

typedef struct {
    int slot;              /* 0 = A, 1 = B */
    int trial;             /* 1 if this boot is the image's trial boot */
    uint32_t addr;         /* slot base address (manifest at addr, payload at addr+256) */
    ota_manifest_t manifest;
} ota_boot_result_t;

int ota_init(ota_ctx_t *ctx, const ota_flash_t *flash, const ota_layout_t *layout, const uint8_t *pubkey);

/* Bootloader decision for fw_n6 (INTERFACES.md section 4 boot rule):
 *   - any TRIAL slot -> BAD (it did not confirm)
 *   - a PENDING slot that verifies -> TRIAL, boot it
 *   - else the CONFIRMED slot (active first) that verifies
 * Any image is fully re-verified (signature, sha256, counter >= stored)
 * before it is chosen; failures are marked BAD. Metadata is written at most
 * once. Returns OTA_OK, OTA_ERR_NO_IMAGE, or a flash error. */
int ota_boot_decide(ota_ctx_t *ctx, ota_boot_result_t *out);

/* App side: load the running (active) fw manifest without changing state. */
int ota_app_attach(ota_ctx_t *ctx);

/* App confirms health. TRIAL -> CONFIRMED and stored counter = max(stored, image).
 * No-op if already CONFIRMED. */
int ota_fw_confirm(ota_ctx_t *ctx);

/* Same rule as ota_boot_decide, for the model slots, with the extra
 * requirement that model op_set_hash == running fw op_set_hash. An
 * op-set-incompatible model is skipped, not marked BAD (it may be needed
 * if the firmware rolls back). */
int ota_model_select(ota_ctx_t *ctx, ota_boot_result_t *out);
int ota_model_confirm(ota_ctx_t *ctx);

/* Streaming writer. Accepts the image in chunks of any size. */
typedef enum { OTA_W_IDLE = 0, OTA_W_ACTIVE, OTA_W_DONE, OTA_W_FAILED } ota_writer_state_t;

typedef struct {
    ota_ctx_t *ctx;
    uint8_t kind;
    int slot;
    uint32_t base;
    uint32_t cap;
    uint32_t received;   /* image bytes accepted so far */
    uint32_t total;      /* 256 + payload_size once the manifest is in, else 0 */
    uint8_t man_raw[OTA_MANIFEST_SIZE];
    ota_manifest_t manifest;
    uint8_t page[OTA_MAX_PAGE_SIZE];
    uint32_t page_fill;
    uint32_t page_addr;
    uint32_t erased_end;
    ota_sha256_ctx_t sha;
    int err;             /* sticky first error */
    ota_writer_state_t state;
} ota_writer_t;

/* kind = OTA_KIND_FW_N6 or OTA_KIND_MODEL (fw_c6 is applied by ESP-IDF on the C6). */
int ota_writer_begin(ota_ctx_t *ctx, ota_writer_t *w, uint8_t kind);
int ota_writer_write(ota_writer_t *w, const uint8_t *data, uint32_t len);
/* Checks length + sha, re-verifies the image from flash, marks the slot PENDING. */
int ota_writer_finish(ota_writer_t *w);
void ota_writer_abort(ota_writer_t *w);

/* Verify an image stored in flash at addr (slot of size cap). */
int ota_slot_verify(const ota_flash_t *f, uint32_t addr, uint32_t cap, const uint8_t *pubkey,
                    const ota_policy_t *policy, ota_manifest_t *out);

#ifdef __cplusplus
}
#endif
#endif
