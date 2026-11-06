#include "ota_flash_sim.h"

#include <string.h>

static uint64_t rnd(ota_sim_flash_t *s) {
    /* splitmix64 */
    uint64_t z = (s->rng += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

/* Returns 1 if this op is the one that loses power. */
static int tick(ota_sim_flash_t *s, uint32_t addr, uint32_t len) {
    if (s->watch_lo != s->watch_hi && addr < s->watch_hi && s->watch_lo < addr + len) s->n_watch_ops++;
    if (s->cut_countdown < 0) return 0;
    if (s->cut_lo != s->cut_hi && !(addr < s->cut_hi && s->cut_lo < addr + len)) return 0;
    if (s->cut_countdown == 0) {
        s->cut_countdown = -1;
        return 1;
    }
    s->cut_countdown--;
    return 0;
}

static int sim_read(void *ctx, uint32_t addr, uint8_t *buf, uint32_t len) {
    ota_sim_flash_t *s = (ota_sim_flash_t *)ctx;
    if (!s->powered) return OTA_ERR_FLASH;
    if (addr > s->size || len > s->size - addr) return OTA_ERR_PARAM;
    memcpy(buf, s->mem + addr, len);
    return OTA_OK;
}

static int sim_program(void *ctx, uint32_t addr, const uint8_t *buf, uint32_t len) {
    ota_sim_flash_t *s = (ota_sim_flash_t *)ctx;
    if (!s->powered) return OTA_ERR_FLASH;
    if (len == 0) return OTA_OK;
    if (addr > s->size || len > s->size - addr) return OTA_ERR_PARAM;
    if (addr / s->page_size != (addr + len - 1u) / s->page_size) return OTA_ERR_PARAM; /* crosses a page */
    if (s->strict) {
        for (uint32_t i = 0; i < len; i++) {
            if ((uint8_t)(~s->mem[addr + i] & buf[i])) {
                s->n_strict_violations++;
                return OTA_ERR_FLASH;
            }
        }
    }
    s->n_program++;
    if (tick(s, addr, len)) {
        uint32_t cut = (uint32_t)(rnd(s) % (len + 1u));
        for (uint32_t i = 0; i < cut; i++) s->mem[addr + i] &= buf[i];
        if (cut < len) s->mem[addr + cut] &= (uint8_t)(buf[cut] | (uint8_t)rnd(s));
        s->powered = 0;
        s->n_cuts++;
        s->last_cut_addr = addr;
        s->last_cut_was_erase = 0;
        return OTA_ERR_FLASH;
    }
    for (uint32_t i = 0; i < len; i++) s->mem[addr + i] &= buf[i];
    return OTA_OK;
}

static int sim_erase(void *ctx, uint32_t addr) {
    ota_sim_flash_t *s = (ota_sim_flash_t *)ctx;
    if (!s->powered) return OTA_ERR_FLASH;
    if (addr % s->erase_size || addr >= s->size) return OTA_ERR_PARAM;
    s->n_erase++;
    if (s->erase_counts) s->erase_counts[addr / s->erase_size]++;
    if (tick(s, addr, s->erase_size)) {
        for (uint32_t i = 0; i < s->erase_size; i++) s->mem[addr + i] |= (uint8_t)rnd(s);
        s->powered = 0;
        s->n_cuts++;
        s->last_cut_addr = addr;
        s->last_cut_was_erase = 1;
        return OTA_ERR_FLASH;
    }
    memset(s->mem + addr, 0xFF, s->erase_size);
    return OTA_OK;
}

void ota_sim_init(ota_sim_flash_t *s, uint8_t *mem, uint32_t size, uint32_t erase_size, uint32_t page_size,
                  ota_flash_t *out) {
    memset(s, 0, sizeof *s);
    s->mem = mem;
    s->size = size;
    s->erase_size = erase_size;
    s->page_size = page_size;
    s->strict = 1;
    s->powered = 1;
    s->cut_countdown = -1;
    s->rng = 0x5EED5EED5EEDull;
    ota_sim_wipe(s);
    out->read = sim_read;
    out->program = sim_program;
    out->erase = sim_erase;
    out->ctx = s;
    out->size = size;
    out->erase_size = erase_size;
    out->page_size = page_size;
}

void ota_sim_wipe(ota_sim_flash_t *s) { memset(s->mem, 0xFF, s->size); }
void ota_sim_seed(ota_sim_flash_t *s, uint64_t seed) { s->rng = seed; }

void ota_sim_arm_cut(ota_sim_flash_t *s, uint64_t n) { ota_sim_arm_cut_range(s, n, 0, 0); }

void ota_sim_arm_cut_range(ota_sim_flash_t *s, uint64_t n, uint32_t lo, uint32_t hi) {
    s->cut_countdown = (int64_t)n;
    s->cut_lo = lo;
    s->cut_hi = hi;
}

void ota_sim_disarm(ota_sim_flash_t *s) { s->cut_countdown = -1; }

void ota_sim_power_on(ota_sim_flash_t *s) {
    s->powered = 1;
    s->cut_countdown = -1;
}

uint64_t ota_sim_ops(const ota_sim_flash_t *s) { return s->n_program + s->n_erase; }
