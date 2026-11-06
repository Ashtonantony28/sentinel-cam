/* Host NOR-flash simulator with power-cut injection.
 *
 * Program can only clear bits (strict mode reports an attempt to set one as
 * an error, which catches "programmed without erasing" bugs). Erase sets a
 * block to 0xFF.
 *
 * Power cut: when armed, the Nth program/erase operation (counted from the
 * arm call, optionally only counting operations that touch a given address
 * range) is applied partially and the device then "loses power": every later
 * read/program/erase returns OTA_ERR_FLASH until ota_sim_power_on(). The code
 * under test therefore cannot do anything further to flash, which is exactly
 * what a CPU losing power looks like from flash's point of view.
 *   partial program: bytes before a random cut point are written, the byte at
 *                    the cut point gets a random subset of its 1->0 bits.
 *   partial erase:   every byte of the block gets a random subset of its
 *                    0->1 bits set (undefined content, typical of NOR). */
#ifndef OTA_FLASH_SIM_H
#define OTA_FLASH_SIM_H
#include <stdint.h>
#include "ota/ota_flash.h"
#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    uint8_t *mem;
    uint32_t size, erase_size, page_size;
    int strict;
    int powered;
    /* fault injection */
    int64_t cut_countdown;       /* -1 = disarmed; else ops (in range) remaining before the cutting op */
    uint32_t cut_lo, cut_hi;     /* only ops overlapping [lo,hi) count; lo=hi=0 means all */
    uint64_t rng;
    /* stats */
    uint64_t n_program, n_erase, n_cuts, n_strict_violations;
    uint32_t watch_lo, watch_hi; /* ops overlapping this range are counted in n_watch_ops */
    uint64_t n_watch_ops;
    uint32_t last_cut_addr;      /* address of the op that lost power */
    int last_cut_was_erase;
    uint32_t *erase_counts;      /* optional, one per block, may be NULL */
} ota_sim_flash_t;

void ota_sim_init(ota_sim_flash_t *s, uint8_t *mem, uint32_t size, uint32_t erase_size, uint32_t page_size,
                  ota_flash_t *out);
/* Fill the whole device with 0xFF (factory-fresh) */
void ota_sim_wipe(ota_sim_flash_t *s);
void ota_sim_seed(ota_sim_flash_t *s, uint64_t seed);
/* Cut power during the n-th (0-based) program/erase from now. */
void ota_sim_arm_cut(ota_sim_flash_t *s, uint64_t n);
/* Same, counting only operations that touch [lo, hi). */
void ota_sim_arm_cut_range(ota_sim_flash_t *s, uint64_t n, uint32_t lo, uint32_t hi);
void ota_sim_disarm(ota_sim_flash_t *s);
/* Restore power (reboot). Disarms any pending cut. */
void ota_sim_power_on(ota_sim_flash_t *s);
uint64_t ota_sim_ops(const ota_sim_flash_t *s);

#ifdef __cplusplus
}
#endif
#endif
