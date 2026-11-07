/*
 * GENERATED FILE - DO NOT EDIT.
 * Generator: tools/gen_fw_power_model.py (run it to regenerate; its --check mode is a test).
 * Source:    hardware/power_model/constants.yaml + scenarios.yaml (INTERFACES.md section 6)
 * Source sha256: 70111b1827c59c022cf3a7e21c220f4fc907d5d74b620fb24a163b59ba3315df
 *
 * Firmware power model (struct in include/sn_energy.h), nominal constants:
 *   standby_uw       = hardware model standby at the battery, plan_mix_twt (TWT) = 1901.704 uW
 *   cortex_boot_mw   = cortex.boot_power = 350.000 mW
 *   cortex_active_mw = cortex.pipeline_power = 850.000 mW
 *   cortex_clip_mw   = cortex.encode_power = 800.000 mW
 *   boot_est_mj      = cortex.boot_power x cortex.boot_time = 52.500 mJ
 *   uplink_est_mj    = cortex.pipeline_power x radio.publish_time = 127.500 mJ
 * Cortex powers are rail-side (load); standby is battery-side. See the generator's docstring.
 */
#include "sn_energy.h"

const sn_power_model_t SN_POWER_MODEL_DEFAULT = {
    .standby_uw       = 1902u,
    .cortex_boot_mw   = 350u,
    .cortex_active_mw = 850u,
    .cortex_clip_mw   = 800u,
    .boot_est_mj      = 53u,
    .uplink_est_mj    = 128u,
};
