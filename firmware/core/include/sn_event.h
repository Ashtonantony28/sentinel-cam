/*
 * sn_event.h - event record (N6 side) and its INTERFACES.md 3 JSON form:
 * {"v":1,"dev":str,"ts":int_unix_ms,"wake":"PIR|RADAR|BUTTON|TIMER",
 *  "action":"NONE|LOG_KNOWN|NOTIFY_UNKNOWN_WITH_CLIP|RING","person_conf":float,
 *  "faces":[{"id":int|null,"score":float}],"clip_id":str|null,"battery_mv":int,
 *  "soc_pct":int,"fw":str,"model":str,"energy_mj":int}
 */
#ifndef SN_EVENT_H
#define SN_EVENT_H
#include "sn_types.h"

#define SN_EVENT_JSON_VERSION 1
/* Worst case (SN_MAX_FACES faces, max-length strings that are all escaped
 * control characters) is ~560 bytes; test_event checks it fits. */
#define SN_EVENT_JSON_MAX     768u

typedef struct {
    bool     known;    /* false -> "id":null */
    uint16_t id;
    float    score;    /* best gallery score for this face (cosine) */
} sn_face_result_t;

typedef struct {
    char             dev[SN_DEV_ID_LEN + 1u];
    uint64_t         ts_ms;
    sn_wake_src_t    wake;
    sn_action_t      action;
    float            person_conf;
    uint8_t          n_faces;
    sn_face_result_t faces[SN_MAX_FACES];
    bool             has_clip;
    char             clip_id[SN_CLIP_ID_MAX];
    uint16_t         battery_mv;
    uint8_t          soc_pct;
    char             fw[SN_VERSION_STR_MAX];
    char             model[SN_VERSION_STR_MAX];
    uint32_t         energy_mj;
} sn_event_record_t;

/* Serializes; returns length (excluding NUL) or 0 if it did not fit or the
 * record is invalid (bad wake/action enum). Scores/conf use 4 decimals. */
size_t sn_event_to_json(const sn_event_record_t *ev, char *buf, size_t cap);

/* Bounded string copy that always NUL-terminates (dst_cap > 0). */
void sn_strlcpy(char *dst, const char *src, size_t dst_cap);

#endif
