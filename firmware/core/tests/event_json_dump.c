/* Prints a set of event records as JSON lines for check_event_json.py. */
#include <stdio.h>
#include <string.h>
#include "sn_event.h"

int main(void)
{
    sn_event_record_t e;
    char b[SN_EVENT_JSON_MAX];
    int w;
    int a;
    for (w = 1; w <= 4; w++) {
        for (a = 0; a <= 3; a++) {
            unsigned i;
            memset(&e, 0, sizeof(e));
            sn_strlcpy(e.dev, "0123456789ab", sizeof(e.dev));
            e.ts_ms = 1762450000000ull + (uint64_t)(w * 10 + a);
            e.wake = (sn_wake_src_t)w;
            e.action = (sn_action_t)a;
            e.person_conf = 0.1f * (float)a;
            e.n_faces = (uint8_t)a;
            for (i = 0u; i < e.n_faces; i++) {
                e.faces[i].known = (i % 2u) == 0u;
                e.faces[i].id = (uint16_t)(i + 1u);
                e.faces[i].score = 0.5f + (0.1f * (float)i);
            }
            e.has_clip = (a == 2) || (a == 3);
            sn_strlcpy(e.clip_id, "0123456789ab-0000019a5bd0a35b", sizeof(e.clip_id));
            e.battery_mv = 3700u;
            e.soc_pct = 55u;
            sn_strlcpy(e.fw, "1.2.3", sizeof(e.fw));
            sn_strlcpy(e.model, "m\"odel\\\n", sizeof(e.model));
            e.energy_mj = 1000u * (uint32_t)a;
            if (sn_event_to_json(&e, b, sizeof(b)) == 0u) {
                return 1;
            }
            puts(b);
        }
    }
    return 0;
}
