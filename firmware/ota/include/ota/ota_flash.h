/* NOR flash abstraction.
 * Semantics (match octal NOR such as MX66UW):
 *   - erase(addr): addr is erase_size aligned; sets the whole block to 0xFF.
 *   - program(addr, buf, len): must not cross a page boundary; can only
 *     clear bits (1 -> 0). Callers always erase first.
 *   - read(addr, buf, len): any range inside the device.
 * Every callback returns OTA_OK or a negative ota_err_t. */
#ifndef OTA_FLASH_H
#define OTA_FLASH_H
#include <stdint.h>
#include "ota_types.h"
#ifdef __cplusplus
extern "C" {
#endif

#ifndef OTA_MAX_PAGE_SIZE
#define OTA_MAX_PAGE_SIZE 512u
#endif

typedef struct ota_flash {
    int (*read)(void *ctx, uint32_t addr, uint8_t *buf, uint32_t len);
    int (*program)(void *ctx, uint32_t addr, const uint8_t *buf, uint32_t len);
    int (*erase)(void *ctx, uint32_t addr);
    void *ctx;
    uint32_t size;
    uint32_t erase_size;
    uint32_t page_size;
} ota_flash_t;

/* Program an arbitrary range, splitting at page boundaries. */
int ota_flash_program_range(const ota_flash_t *f, uint32_t addr, const uint8_t *buf, uint32_t len);
/* Erase every block overlapping [addr, addr+len); addr must be block aligned. */
int ota_flash_erase_range(const ota_flash_t *f, uint32_t addr, uint32_t len);

/* Where everything lives. All addresses erase-block aligned, regions disjoint. */
typedef struct {
    uint32_t meta_addr[2];  /* two redundant metadata copies, one erase block each */
    uint32_t fw_addr[2];    /* fw_n6 slots A/B */
    uint32_t fw_size;
    uint32_t model_addr[2]; /* model slots A/B */
    uint32_t model_size;
} ota_layout_t;

int ota_layout_check(const ota_layout_t *l, const ota_flash_t *f);

#ifdef __cplusplus
}
#endif
#endif
