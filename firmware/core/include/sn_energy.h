/*
 * sn_energy.h - energy model constants and accumulators.
 *
 * Units: power in mW (or uW for standby), time in ms, energy accumulated in
 * nanojoules (uW * ms = nJ, mW * ms = uJ). Reported energy is in mJ.
 *
 * TODO-measure: every constant in SN_POWER_MODEL_DEFAULT is a placeholder
 * from docs/PLAN.md 4.4 until PPK2 measurements exist (milestone M2). The
 * source of truth will be hardware/power_model/constants.yaml
 * (INTERFACES.md 6); keep these in sync when it lands.
 */
#ifndef SN_ENERGY_H
#define SN_ENERGY_H
#include <stdint.h>

typedef struct {
    uint32_t standby_uw;        /* always-on domain: C6 (Wi-Fi TWT) + PIR + gauge + regs */
    uint32_t cortex_boot_mw;    /* N6 domain while rails sequence / boot / shut down     */
    uint32_t cortex_active_mw;  /* N6 domain running camera + NPU inference              */
    uint32_t cortex_clip_mw;    /* N6 domain encoding + uploading a clip                 */
    uint32_t boot_est_mj;       /* N6-side estimate of boot energy when C6 gives none    */
    uint32_t uplink_est_mj;     /* estimate for publishing the event (after the record)  */
} sn_power_model_t;

extern const sn_power_model_t SN_POWER_MODEL_DEFAULT;

typedef struct {
    uint64_t nj;
} sn_energy_acc_t;

void     sn_energy_reset(sn_energy_acc_t *acc);
void     sn_energy_add_mw(sn_energy_acc_t *acc, uint32_t mw, uint32_t ms);
void     sn_energy_add_uw(sn_energy_acc_t *acc, uint32_t uw, uint32_t ms);
void     sn_energy_add_nj(sn_energy_acc_t *acc, uint64_t nj);
uint32_t sn_energy_mj(const sn_energy_acc_t *acc);  /* rounded, saturating */

/* Energy in mJ of `mw` for `ms` (rounded, saturating). */
uint32_t sn_energy_mw_ms_to_mj(uint32_t mw, uint32_t ms);

#endif
