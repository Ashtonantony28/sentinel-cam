#include "sn_status.h"
#include "sn_json.h"

const char *sn_status_state_name(sn_status_state_t s)
{
    switch (s) {
    case SN_STATUS_ONLINE:  return "online";
    case SN_STATUS_OFFLINE: return "offline";
    case SN_STATUS_FAULT:   return "fault";
    default:                return NULL;
    }
}

const char *sn_fault_reason_name(sn_fault_reason_t r)
{
    switch (r) {
    case SN_FAULT_LOW_BATTERY: return "LOW_BATTERY";
    case SN_FAULT_BROWNOUT:    return "BROWNOUT";
    case SN_FAULT_POWER_FAULT: return "POWER_FAULT";
    case SN_FAULT_JOB_TIMEOUT: return "JOB_TIMEOUT";
    default:                   return NULL;
    }
}

sn_fault_reason_t sn_status_reason_from_report(sn_bs_report_t kind, uint32_t detail)
{
    switch (kind) {
    case SN_BS_REPORT_LOW_BATT_REFUSED: return SN_FAULT_LOW_BATTERY;
    case SN_BS_REPORT_POWER_FAULT:      return SN_FAULT_POWER_FAULT;
    case SN_BS_REPORT_CORTEX_LOST:
        return (detail == (uint32_t)SN_PWR_ERR_BROWNOUT) ? SN_FAULT_BROWNOUT : SN_FAULT_POWER_FAULT;
    case SN_BS_REPORT_JOB_TIMEOUT:      return SN_FAULT_JOB_TIMEOUT;
    default:                            return SN_FAULT_NONE;
    }
}

size_t sn_status_to_json(const sn_status_t *st, char *buf, size_t cap)
{
    sn_json_t j;
    const char *state;
    const char *reason;
    if ((buf == NULL) || (cap == 0u)) {
        return 0u;
    }
    buf[0] = '\0';
    if (st == NULL) {
        return 0u;
    }
    state = sn_status_state_name(st->state);
    reason = sn_fault_reason_name(st->reason);
    if ((state == NULL) || ((st->reason != SN_FAULT_NONE) && (reason == NULL))) {
        return 0u;
    }
    if ((st->state == SN_STATUS_FAULT) != (reason != NULL)) {
        return 0u;
    }
    sn_json_init(&j, buf, cap);
    sn_json_obj_begin(&j);
    sn_json_key(&j, "state");      sn_json_str(&j, state);
    sn_json_key(&j, "reason");     sn_json_str(&j, reason);   /* NULL -> null */
    sn_json_key(&j, "ts");         sn_json_u64(&j, st->ts_ms);
    sn_json_key(&j, "battery_mv"); sn_json_u64(&j, st->battery_mv);
    sn_json_obj_end(&j);
    if (!sn_json_finish(&j)) {
        buf[0] = '\0';
        return 0u;
    }
    return j.len;
}
