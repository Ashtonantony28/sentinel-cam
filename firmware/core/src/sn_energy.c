#include "sn_energy.h"
#include <stddef.h>

/* TODO-measure: placeholders from docs/PLAN.md 4.4 (see header). */
const sn_power_model_t SN_POWER_MODEL_DEFAULT = {
    .standby_uw       = 1500u,  /* ~400 uA @ 3.7 V with Wi-Fi TWT        */
    .cortex_boot_mw   = 600u,
    .cortex_active_mw = 1000u,  /* "~1 W x 3 s" inference                 */
    .cortex_clip_mw   = 1200u,  /* "~1.2 W x 10 s" clip encode + upload    */
    .boot_est_mj      = 120u,
    .uplink_est_mj    = 300u,
};

void sn_energy_reset(sn_energy_acc_t *acc)
{
    if (acc != NULL) {
        acc->nj = 0u;
    }
}

void sn_energy_add_nj(sn_energy_acc_t *acc, uint64_t nj)
{
    if (acc == NULL) {
        return;
    }
    if (acc->nj > (UINT64_MAX - nj)) {
        acc->nj = UINT64_MAX;
    } else {
        acc->nj += nj;
    }
}

void sn_energy_add_mw(sn_energy_acc_t *acc, uint32_t mw, uint32_t ms)
{
    sn_energy_add_nj(acc, (uint64_t)mw * (uint64_t)ms * 1000u);
}

void sn_energy_add_uw(sn_energy_acc_t *acc, uint32_t uw, uint32_t ms)
{
    sn_energy_add_nj(acc, (uint64_t)uw * (uint64_t)ms);
}

static uint32_t nj_to_mj(uint64_t nj)
{
    uint64_t mj = (nj + 500000u) / 1000000u;
    return (mj > UINT32_MAX) ? UINT32_MAX : (uint32_t)mj;
}

uint32_t sn_energy_mj(const sn_energy_acc_t *acc)
{
    return (acc == NULL) ? 0u : nj_to_mj(acc->nj);
}

uint32_t sn_energy_mw_ms_to_mj(uint32_t mw, uint32_t ms)
{
    return nj_to_mj((uint64_t)mw * (uint64_t)ms * 1000u);
}
