#include "sn_energy.h"
#include <stddef.h>

/* SN_POWER_MODEL_DEFAULT is generated from hardware/power_model/constants.yaml
 * into sn_power_model_gen.c by tools/gen_fw_power_model.py (do not hand-edit). */

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

void sn_energy_sub_nj(sn_energy_acc_t *acc, uint64_t nj)
{
    if (acc == NULL) {
        return;
    }
    acc->nj = (acc->nj > nj) ? (acc->nj - nj) : 0u;
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
