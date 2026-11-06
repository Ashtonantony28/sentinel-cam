#include "ota/ota_meta.h"

#include <string.h>

#include "ota/ota_crc32.h"

static int state_ok(uint8_t s) { return s >= OTA_SLOT_EMPTY && s <= OTA_SLOT_BAD; }

void ota_meta_encode(const ota_meta_t *m, uint8_t rec[OTA_META_RECORD_SIZE]) {
    memset(rec, 0, OTA_META_RECORD_SIZE);
    memcpy(rec, "SMET", 4);
    ota_put_le16(rec + 4, OTA_META_FORMAT);
    ota_put_le16(rec + 6, OTA_META_RECORD_SIZE);
    ota_put_le32(rec + 8, m->seq);
    rec[12] = m->fw_state[0];
    rec[13] = m->fw_state[1];
    rec[14] = m->model_state[0];
    rec[15] = m->model_state[1];
    rec[16] = m->fw_active;
    rec[17] = m->model_active;
    for (int i = 0; i < OTA_KIND_COUNT; i++) ota_put_le32(rec + 20 + 4 * i, m->sec_counter[i]);
    ota_put_le32(rec + 36, ota_crc32(rec, 36));
}

int ota_meta_decode(const uint8_t rec[OTA_META_RECORD_SIZE], ota_meta_t *m) {
    if (memcmp(rec, "SMET", 4) != 0) return OTA_ERR_NO_META;
    if (ota_get_le16(rec + 4) != OTA_META_FORMAT || ota_get_le16(rec + 6) != OTA_META_RECORD_SIZE) {
        return OTA_ERR_NO_META;
    }
    if (ota_get_le32(rec + 36) != ota_crc32(rec, 36)) return OTA_ERR_NO_META;
    if (rec[18] || rec[19] || ota_get_le32(rec + 32)) return OTA_ERR_NO_META;
    ota_meta_t t;
    t.seq = ota_get_le32(rec + 8);
    t.fw_state[0] = rec[12];
    t.fw_state[1] = rec[13];
    t.model_state[0] = rec[14];
    t.model_state[1] = rec[15];
    t.fw_active = rec[16];
    t.model_active = rec[17];
    for (int i = 0; i < OTA_KIND_COUNT; i++) t.sec_counter[i] = ota_get_le32(rec + 20 + 4 * i);
    for (int i = 0; i < 2; i++) {
        if (!state_ok(t.fw_state[i]) || !state_ok(t.model_state[i])) return OTA_ERR_NO_META;
    }
    if (t.fw_active > 1 || t.model_active > 1) return OTA_ERR_NO_META;
    *m = t;
    return OTA_OK;
}

/* Serial-number comparison so the scheme survives seq wrap-around. */
static int seq_newer(uint32_t a, uint32_t b) { return (int32_t)(a - b) > 0; }

int ota_meta_load(const ota_flash_t *f, const ota_layout_t *l, ota_meta_t *m, int *copy) {
    if (!f || !l || !m || !copy) return OTA_ERR_PARAM;
    ota_meta_t c[2];
    int ok[2];
    for (int i = 0; i < 2; i++) {
        uint8_t rec[OTA_META_RECORD_SIZE];
        int rc = f->read(f->ctx, l->meta_addr[i], rec, OTA_META_RECORD_SIZE);
        if (rc) return rc;
        ok[i] = ota_meta_decode(rec, &c[i]) == OTA_OK;
    }
    int pick;
    if (ok[0] && ok[1]) {
        pick = seq_newer(c[1].seq, c[0].seq) ? 1 : 0;
    } else if (ok[0]) {
        pick = 0;
    } else if (ok[1]) {
        pick = 1;
    } else {
        return OTA_ERR_NO_META;
    }
    *m = c[pick];
    *copy = pick;
    return OTA_OK;
}

static int write_record(const ota_flash_t *f, uint32_t addr, const uint8_t rec[OTA_META_RECORD_SIZE]) {
    int rc = f->erase(f->ctx, addr);
    if (rc) return rc;
    rc = ota_flash_program_range(f, addr, rec, OTA_META_RECORD_SIZE);
    if (rc) return rc;
    uint8_t back[OTA_META_RECORD_SIZE];
    rc = f->read(f->ctx, addr, back, OTA_META_RECORD_SIZE);
    if (rc) return rc;
    return memcmp(back, rec, OTA_META_RECORD_SIZE) == 0 ? OTA_OK : OTA_ERR_FLASH;
}

int ota_meta_store(const ota_flash_t *f, const ota_layout_t *l, ota_meta_t *m, int *copy) {
    if (!f || !l || !m || !copy || *copy < 0 || *copy > 1) return OTA_ERR_PARAM;
    int target = *copy ^ 1;
    ota_meta_t t = *m;
    t.seq = m->seq + 1u;
    uint8_t rec[OTA_META_RECORD_SIZE];
    ota_meta_encode(&t, rec);
    int rc = write_record(f, l->meta_addr[target], rec);
    if (rc) return rc;
    m->seq = t.seq;
    *copy = target;
    return OTA_OK;
}

int ota_meta_format(const ota_flash_t *f, const ota_layout_t *l) {
    if (!f || !l) return OTA_ERR_PARAM;
    ota_meta_t m;
    memset(&m, 0, sizeof m);
    m.seq = 1;
    m.fw_state[0] = m.fw_state[1] = OTA_SLOT_EMPTY;
    m.model_state[0] = m.model_state[1] = OTA_SLOT_EMPTY;
    int rc = f->erase(f->ctx, l->meta_addr[1]);
    if (rc) return rc;
    uint8_t rec[OTA_META_RECORD_SIZE];
    ota_meta_encode(&m, rec);
    return write_record(f, l->meta_addr[0], rec);
}
