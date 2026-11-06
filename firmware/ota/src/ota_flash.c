#include "ota/ota_flash.h"

int ota_flash_program_range(const ota_flash_t *f, uint32_t addr, const uint8_t *buf, uint32_t len) {
    if (!f || (!buf && len)) return OTA_ERR_PARAM;
    if (addr > f->size || len > f->size - addr) return OTA_ERR_PARAM;
    while (len) {
        uint32_t room = f->page_size - (addr % f->page_size);
        uint32_t n = len < room ? len : room;
        int rc = f->program(f->ctx, addr, buf, n);
        if (rc) return rc;
        addr += n;
        buf += n;
        len -= n;
    }
    return OTA_OK;
}

int ota_flash_erase_range(const ota_flash_t *f, uint32_t addr, uint32_t len) {
    if (!f || addr % f->erase_size) return OTA_ERR_PARAM;
    if (addr > f->size || len > f->size - addr) return OTA_ERR_PARAM;
    for (uint32_t a = addr; a < addr + len; a += f->erase_size) {
        int rc = f->erase(f->ctx, a);
        if (rc) return rc;
    }
    return OTA_OK;
}

static int region_ok(const ota_flash_t *f, uint32_t addr, uint32_t len) {
    return len > 0 && addr % f->erase_size == 0 && len % f->erase_size == 0 && addr <= f->size &&
           len <= f->size - addr;
}

static int overlap(uint32_t a, uint32_t al, uint32_t b, uint32_t bl) { return a < b + bl && b < a + al; }

int ota_layout_check(const ota_layout_t *l, const ota_flash_t *f) {
    if (!l || !f || !f->read || !f->program || !f->erase) return OTA_ERR_PARAM;
    if (f->page_size == 0 || f->page_size > OTA_MAX_PAGE_SIZE || f->erase_size == 0 ||
        f->erase_size % f->page_size != 0) {
        return OTA_ERR_LAYOUT;
    }
    if (f->erase_size < 40u) return OTA_ERR_LAYOUT;
    uint32_t a[6] = {l->meta_addr[0], l->meta_addr[1], l->fw_addr[0], l->fw_addr[1], l->model_addr[0], l->model_addr[1]};
    uint32_t s[6] = {f->erase_size, f->erase_size, l->fw_size, l->fw_size, l->model_size, l->model_size};
    for (int i = 0; i < 6; i++) {
        if (!region_ok(f, a[i], s[i])) return OTA_ERR_LAYOUT;
        for (int j = 0; j < i; j++) {
            if (overlap(a[i], s[i], a[j], s[j])) return OTA_ERR_LAYOUT;
        }
    }
    if (l->fw_size <= 256u || l->model_size <= 256u) return OTA_ERR_LAYOUT;
    return OTA_OK;
}
