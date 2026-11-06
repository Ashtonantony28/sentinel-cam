/* Redundant slot metadata.
 *
 * Two copies, each in its own erase block. A record carries a sequence number
 * and a CRC32. Load picks the valid copy with the highest sequence. Store
 * always writes the copy that is NOT the newest (erase, then program a record
 * with seq+1), so a power cut at any point leaves the previous newest record
 * intact. On-flash record (little-endian, 40 bytes):
 *   0  4  magic "SMET"
 *   4  2  format = 1
 *   6  2  record length = 40
 *   8  4  seq
 *   12 2  fw_state[2]
 *   14 2  model_state[2]
 *   16 1  fw_active
 *   17 1  model_active
 *   18 2  reserved = 0
 *   20 12 sec_counter[3]   (index = kind - 1)
 *   32 4  reserved = 0
 *   36 4  crc32 over [0,36)
 */
#ifndef OTA_META_H
#define OTA_META_H
#include <stdint.h>
#include "ota_flash.h"
#include "ota_types.h"
#ifdef __cplusplus
extern "C" {
#endif

#define OTA_META_RECORD_SIZE 40u
#define OTA_META_FORMAT 1u

typedef struct {
    uint32_t seq;
    uint8_t fw_state[2];
    uint8_t model_state[2];
    uint8_t fw_active;
    uint8_t model_active;
    uint32_t sec_counter[OTA_KIND_COUNT];
} ota_meta_t;

void ota_meta_encode(const ota_meta_t *m, uint8_t rec[OTA_META_RECORD_SIZE]);
/* Returns OTA_OK if rec is a valid record. */
int ota_meta_decode(const uint8_t rec[OTA_META_RECORD_SIZE], ota_meta_t *m);

/* Load the newest valid copy. *copy receives 0 or 1. OTA_ERR_NO_META if none. */
int ota_meta_load(const ota_flash_t *f, const ota_layout_t *l, ota_meta_t *m, int *copy);

/* Write m to the copy other than *copy with seq = m->seq + 1. On success
 * m->seq and *copy are updated. On failure neither is changed and the
 * previous record is still the newest valid one. */
int ota_meta_store(const ota_flash_t *f, const ota_layout_t *l, ota_meta_t *m, int *copy);

/* Factory format: both slots of both groups EMPTY, counters zero. */
int ota_meta_format(const ota_flash_t *f, const ota_layout_t *l);

#ifdef __cplusplus
}
#endif
#endif
