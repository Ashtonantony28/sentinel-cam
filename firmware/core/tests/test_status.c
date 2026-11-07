#include "sn_test.h"
#include "sn_status.h"

static sn_status_t mk(sn_status_state_t s, sn_fault_reason_t r)
{
    sn_status_t st;
    st.state = s;
    st.reason = r;
    st.ts_ms = 1793923200123ull;
    st.battery_mv = 3480u;
    return st;
}

static void status_exact_strings(void)
{
    char b[SN_STATUS_JSON_MAX];
    sn_status_t st = mk(SN_STATUS_ONLINE, SN_FAULT_NONE);
    size_t n = sn_status_to_json(&st, b, sizeof(b));
    CHECK_EQ_INT(n, strlen(b));
    CHECK_STR(b, "{\"state\":\"online\",\"reason\":null,\"ts\":1793923200123,\"battery_mv\":3480}");
    st = mk(SN_STATUS_OFFLINE, SN_FAULT_NONE);
    (void)sn_status_to_json(&st, b, sizeof(b));
    CHECK_STR(b, "{\"state\":\"offline\",\"reason\":null,\"ts\":1793923200123,\"battery_mv\":3480}");
    st = mk(SN_STATUS_FAULT, SN_FAULT_BROWNOUT);
    (void)sn_status_to_json(&st, b, sizeof(b));
    CHECK_STR(b, "{\"state\":\"fault\",\"reason\":\"BROWNOUT\",\"ts\":1793923200123,\"battery_mv\":3480}");
}

static void status_every_reason(void)
{
    static const char *const names[] = { NULL, "LOW_BATTERY", "BROWNOUT", "POWER_FAULT", "JOB_TIMEOUT" };
    int r;
    for (r = 1; r < (int)SN_FAULT_REASON_COUNT; r++) {
        char b[SN_STATUS_JSON_MAX];
        char want[64];
        sn_status_t st = mk(SN_STATUS_FAULT, (sn_fault_reason_t)r);
        CHECK(sn_status_to_json(&st, b, sizeof(b)) > 0u);
        CHECK_STR(sn_fault_reason_name((sn_fault_reason_t)r), names[r]);
        (void)snprintf(want, sizeof(want), "\"reason\":\"%s\"", names[r]);
        CHECK(strstr(b, want) != NULL);
    }
    CHECK(sn_fault_reason_name(SN_FAULT_NONE) == NULL);
}

static void status_invalid_rejected(void)
{
    char b[SN_STATUS_JSON_MAX];
    sn_status_t st = mk(SN_STATUS_FAULT, SN_FAULT_NONE);          /* fault needs a reason */
    CHECK_EQ_INT(sn_status_to_json(&st, b, sizeof(b)), 0u);
    CHECK_STR(b, "");
    st = mk(SN_STATUS_ONLINE, SN_FAULT_JOB_TIMEOUT);              /* reason needs fault */
    CHECK_EQ_INT(sn_status_to_json(&st, b, sizeof(b)), 0u);
    st = mk((sn_status_state_t)7, SN_FAULT_NONE);
    CHECK_EQ_INT(sn_status_to_json(&st, b, sizeof(b)), 0u);
    st = mk(SN_STATUS_FAULT, (sn_fault_reason_t)99);
    CHECK_EQ_INT(sn_status_to_json(&st, b, sizeof(b)), 0u);
    CHECK_EQ_INT(sn_status_to_json(NULL, b, sizeof(b)), 0u);
    CHECK_EQ_INT(sn_status_to_json(&st, NULL, 10u), 0u);
    CHECK_EQ_INT(sn_status_to_json(&st, b, 0u), 0u);
}

/* Every capacity below the needed size fails cleanly, never overruns. */
static void status_every_capacity(void)
{
    char ref[SN_STATUS_JSON_MAX];
    sn_status_t st = mk(SN_STATUS_FAULT, SN_FAULT_LOW_BATTERY);
    size_t need;
    size_t cap;
    st.ts_ms = UINT64_MAX;
    st.battery_mv = UINT16_MAX;
    need = sn_status_to_json(&st, ref, sizeof(ref));
    CHECK(need > 0u);
    CHECK(need < SN_STATUS_JSON_MAX);
    for (cap = 1u; cap <= need + 1u; cap++) {
        char b[SN_STATUS_JSON_MAX + 8u];
        size_t n;
        memset(b, 'X', sizeof(b));
        n = sn_status_to_json(&st, b, cap);
        CHECK(b[cap] == 'X');                                     /* no write past cap */
        if (cap <= need) {
            CHECK_EQ_INT(n, 0u);
            CHECK_EQ_INT(b[0], '\0');
        } else {
            CHECK_EQ_INT(n, need);
            CHECK_STR(b, ref);
        }
    }
}

static void status_reason_from_report(void)
{
    CHECK_EQ_INT(sn_status_reason_from_report(SN_BS_REPORT_LOW_BATT_REFUSED, 3300u), SN_FAULT_LOW_BATTERY);
    CHECK_EQ_INT(sn_status_reason_from_report(SN_BS_REPORT_POWER_FAULT, SN_PWR_ERR_PG_TIMEOUT), SN_FAULT_POWER_FAULT);
    CHECK_EQ_INT(sn_status_reason_from_report(SN_BS_REPORT_CORTEX_LOST, SN_PWR_ERR_BROWNOUT), SN_FAULT_BROWNOUT);
    CHECK_EQ_INT(sn_status_reason_from_report(SN_BS_REPORT_CORTEX_LOST, SN_PWR_ERR_RAIL_LOST), SN_FAULT_POWER_FAULT);
    CHECK_EQ_INT(sn_status_reason_from_report(SN_BS_REPORT_JOB_TIMEOUT, 30000u), SN_FAULT_JOB_TIMEOUT);
    CHECK_EQ_INT(sn_status_reason_from_report(SN_BS_REPORT_WAKE_DROPPED, 0u), SN_FAULT_NONE);
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(status_exact_strings);
    RUN_TEST(status_every_reason);
    RUN_TEST(status_invalid_rejected);
    RUN_TEST(status_every_capacity);
    RUN_TEST(status_reason_from_report);
    TEST_END();
}
