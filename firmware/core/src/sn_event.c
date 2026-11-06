#include "sn_event.h"
#include "sn_json.h"

const char *sn_wake_src_name(sn_wake_src_t src)
{
    switch (src) {
    case SN_WAKE_PIR:    return "PIR";
    case SN_WAKE_RADAR:  return "RADAR";
    case SN_WAKE_BUTTON: return "BUTTON";
    case SN_WAKE_TIMER:  return "TIMER";
    case SN_WAKE_NONE:   return "NONE";
    default:             return "?";
    }
}

const char *sn_action_name(sn_action_t action)
{
    switch (action) {
    case SN_ACTION_NONE:                     return "NONE";
    case SN_ACTION_LOG_KNOWN:                return "LOG_KNOWN";
    case SN_ACTION_NOTIFY_UNKNOWN_WITH_CLIP: return "NOTIFY_UNKNOWN_WITH_CLIP";
    case SN_ACTION_RING:                     return "RING";
    default:                                 return "?";
    }
}

void sn_strlcpy(char *dst, const char *src, size_t dst_cap)
{
    size_t i = 0u;
    if ((dst == NULL) || (dst_cap == 0u)) {
        return;
    }
    if (src != NULL) {
        while (((i + 1u) < dst_cap) && (src[i] != '\0')) {
            dst[i] = src[i];
            i++;
        }
    }
    dst[i] = '\0';
}

size_t sn_event_to_json(const sn_event_record_t *ev, char *buf, size_t cap)
{
    sn_json_t j;
    uint8_t i;
    uint8_t n;

    if ((ev == NULL) || (buf == NULL) || (cap == 0u)) {
        return 0u;
    }
    if ((ev->wake < SN_WAKE_PIR) || (ev->wake > SN_WAKE_TIMER) ||
        (ev->action > SN_ACTION_RING)) {
        buf[0] = '\0';
        return 0u;
    }
    n = (ev->n_faces > SN_MAX_FACES) ? (uint8_t)SN_MAX_FACES : ev->n_faces;

    sn_json_init(&j, buf, cap);
    sn_json_obj_begin(&j);
    sn_json_key(&j, "v");           sn_json_i64(&j, SN_EVENT_JSON_VERSION);
    sn_json_key(&j, "dev");         sn_json_str(&j, ev->dev);
    sn_json_key(&j, "ts");          sn_json_u64(&j, ev->ts_ms);
    sn_json_key(&j, "wake");        sn_json_str(&j, sn_wake_src_name(ev->wake));
    sn_json_key(&j, "action");      sn_json_str(&j, sn_action_name(ev->action));
    sn_json_key(&j, "person_conf"); sn_json_float(&j, (double)ev->person_conf, 4u);
    sn_json_key(&j, "faces");
    sn_json_arr_begin(&j);
    for (i = 0u; i < n; i++) {
        sn_json_obj_begin(&j);
        sn_json_key(&j, "id");
        if (ev->faces[i].known) {
            sn_json_u64(&j, ev->faces[i].id);
        } else {
            sn_json_null(&j);
        }
        sn_json_key(&j, "score");
        sn_json_float(&j, (double)ev->faces[i].score, 4u);
        sn_json_obj_end(&j);
    }
    sn_json_arr_end(&j);
    sn_json_key(&j, "clip_id");     sn_json_str(&j, ev->has_clip ? ev->clip_id : NULL);
    sn_json_key(&j, "battery_mv");  sn_json_u64(&j, ev->battery_mv);
    sn_json_key(&j, "soc_pct");     sn_json_u64(&j, ev->soc_pct);
    sn_json_key(&j, "fw");          sn_json_str(&j, ev->fw);
    sn_json_key(&j, "model");       sn_json_str(&j, ev->model);
    sn_json_key(&j, "energy_mj");   sn_json_u64(&j, ev->energy_mj);
    sn_json_obj_end(&j);
    if (!sn_json_finish(&j)) {
        buf[0] = '\0';
        return 0u;
    }
    return j.len;
}
