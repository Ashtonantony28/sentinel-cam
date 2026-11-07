/*
 * sn_status.h - INTERFACES.md 3 status message (brainstem -> backend,
 * topic sentinel/{dev}/status, retained):
 *   {"state":"online|offline|fault",
 *    "reason":null|"LOW_BATTERY|BROWNOUT|POWER_FAULT|JOB_TIMEOUT",
 *    "ts":int_unix_ms,"battery_mv":int}
 * The brainstem publishes state "fault" when it refuses or loses the N6
 * (no 3 event exists then). The LWT "offline" is the broker's, not this.
 *
 * Validity (matches the backend's StatusMsg): "fault" needs a reason, and
 * "online"/"offline" carry reason null.
 */
#ifndef SN_STATUS_H
#define SN_STATUS_H
#include "sn_brainstem.h"

typedef enum {
    SN_STATUS_ONLINE = 0,
    SN_STATUS_OFFLINE,
    SN_STATUS_FAULT,
    SN_STATUS_STATE_COUNT
} sn_status_state_t;

typedef enum {
    SN_FAULT_NONE = 0,          /* -> "reason":null */
    SN_FAULT_LOW_BATTERY,
    SN_FAULT_BROWNOUT,
    SN_FAULT_POWER_FAULT,
    SN_FAULT_JOB_TIMEOUT,
    SN_FAULT_REASON_COUNT
} sn_fault_reason_t;

typedef struct {
    sn_status_state_t state;
    sn_fault_reason_t reason;
    uint64_t          ts_ms;
    uint16_t          battery_mv;
} sn_status_t;

/* Longest valid document is ~80 bytes. */
#define SN_STATUS_JSON_MAX 128u

const char *sn_status_state_name(sn_status_state_t s);   /* NULL if invalid */
const char *sn_fault_reason_name(sn_fault_reason_t r);   /* NULL for NONE/invalid */

/* Maps a brainstem report (kind, detail as passed to sn_bs_ops_t.report) to
 * a fault reason. Returns SN_FAULT_NONE for reports that are not faults
 * (WAKE_DROPPED). CORTEX_LOST is BROWNOUT when detail is SN_PWR_ERR_BROWNOUT,
 * otherwise POWER_FAULT (rail lost while up). */
sn_fault_reason_t sn_status_reason_from_report(sn_bs_report_t kind, uint32_t detail);

/* Serializes; returns length (excluding NUL), or 0 if it did not fit or the
 * status is invalid (bad enum, fault without reason, reason without fault). */
size_t sn_status_to_json(const sn_status_t *st, char *buf, size_t cap);

#endif
