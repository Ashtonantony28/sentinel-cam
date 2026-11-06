/*
 * sn_types.h - shared constants and enums for Sentinel firmware core.
 * Mirrors docs/INTERFACES.md sections 1 and 2.
 */
#ifndef SN_TYPES_H
#define SN_TYPES_H

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define SN_EMB_DIM              128u   /* INTERFACES.md 1: embedding dims          */
#define SN_GAL_MAX_PEOPLE       16u    /* INTERFACES.md 1: <= 16 people            */
#define SN_GAL_MAX_PROTOS       20u    /* INTERFACES.md 1: <= 20 prototypes/person */
#define SN_GAL_NAME_LEN         24u    /* INTERFACES.md 1: char name[24]           */
#define SN_MAX_FACES            4u     /* faces reported per event (device limit)  */
#define SN_DEV_ID_LEN           12u    /* INTERFACES.md 3: 12 hex chars from MAC   */
#define SN_CLIP_ID_MAX          32u    /* incl. NUL                                */
#define SN_VERSION_STR_MAX      24u    /* fw / model strings, incl. NUL            */

/* Wake sources, INTERFACES.md 2. */
typedef enum {
    SN_WAKE_NONE = 0,
    SN_WAKE_PIR,
    SN_WAKE_RADAR,
    SN_WAKE_BUTTON,
    SN_WAKE_TIMER
} sn_wake_src_t;

/* Policy outputs, INTERFACES.md 2. */
typedef enum {
    SN_ACTION_NONE = 0,
    SN_ACTION_LOG_KNOWN,
    SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP,
    SN_ACTION_RING
} sn_action_t;

/* Returns the INTERFACES.md string ("PIR", ...); "NONE"/"?" for invalid. */
const char *sn_wake_src_name(sn_wake_src_t src);
const char *sn_action_name(sn_action_t action);

/* Wrap-safe "a is at or after b" for 32-bit millisecond clocks. */
static inline bool sn_time_reached(uint32_t now, uint32_t deadline)
{
    return (int32_t)(now - deadline) >= 0;
}

#endif /* SN_TYPES_H */
