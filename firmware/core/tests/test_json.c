#include "sn_test.h"
#include "sn_json.h"
#include <stdint.h>

static void json_nested_document(void)
{
    char b[128];
    sn_json_t j;
    sn_json_init(&j, b, sizeof(b));
    sn_json_obj_begin(&j);
    sn_json_key(&j, "a"); sn_json_i64(&j, -5);
    sn_json_key(&j, "b"); sn_json_arr_begin(&j);
    sn_json_u64(&j, 1u); sn_json_null(&j); sn_json_bool(&j, true);
    sn_json_obj_begin(&j); sn_json_obj_end(&j);
    sn_json_arr_begin(&j); sn_json_arr_end(&j);
    sn_json_arr_end(&j);
    sn_json_key(&j, "c"); sn_json_str(&j, NULL);
    sn_json_key(&j, "d"); sn_json_bool(&j, false);
    sn_json_obj_end(&j);
    CHECK(sn_json_finish(&j));
    CHECK_STR(b, "{\"a\":-5,\"b\":[1,null,true,{},[]],\"c\":null,\"d\":false}");
}

static void json_string_escaping(void)
{
    char b[128];
    sn_json_t j;
    sn_json_init(&j, b, sizeof(b));
    sn_json_str(&j, "q\"b\\n\nt\tc\x01\x1f\x7f" "\xc3\xa9");
    CHECK(sn_json_finish(&j));
    CHECK_STR(b, "\"q\\\"b\\\\n\\nt\\tc\\u0001\\u001f\\u007f\xc3\xa9\"");
}

static void json_integers_extremes(void)
{
    char b[128];
    sn_json_t j;
    sn_json_init(&j, b, sizeof(b));
    sn_json_arr_begin(&j);
    sn_json_i64(&j, INT64_MIN);
    sn_json_i64(&j, INT64_MAX);
    sn_json_u64(&j, UINT64_MAX);
    sn_json_i64(&j, 0);
    sn_json_arr_end(&j);
    CHECK(sn_json_finish(&j));
    CHECK_STR(b, "[-9223372036854775808,9223372036854775807,18446744073709551615,0]");
}

static void json_float_formatting(void)
{
    char b[256];
    sn_json_t j;
    sn_json_init(&j, b, sizeof(b));
    sn_json_arr_begin(&j);
    sn_json_float(&j, 0.0, 4u);
    sn_json_float(&j, 0.5, 4u);
    sn_json_float(&j, 0.83456, 4u);       /* rounds */
    sn_json_float(&j, -1.25, 4u);
    sn_json_float(&j, -0.00001, 4u);      /* rounds to zero: no "-0" */
    sn_json_float(&j, 1.99999, 4u);       /* carry into integer part */
    sn_json_float(&j, 12.0, 0u);
    sn_json_float(&j, NAN, 4u);
    sn_json_float(&j, INFINITY, 4u);
    sn_json_float(&j, 1e13, 2u);
    sn_json_float(&j, 3.14159265, 9u);    /* decimals capped at 6 */
    sn_json_arr_end(&j);
    CHECK(sn_json_finish(&j));
    CHECK_STR(b, "[0.0,0.5,0.8346,-1.25,0.0,2.0,12.0,null,null,null,3.141593]");
}

/* Every capacity: never writes past cap, always NUL-terminated, and finish()
 * is true exactly when the full document fits. */
static void json_overflow_every_capacity(void)
{
    char full[128];
    size_t full_len;
    size_t cap;
    sn_json_t j;
    sn_json_init(&j, full, sizeof(full));
    sn_json_obj_begin(&j); sn_json_key(&j, "key"); sn_json_str(&j, "va\"lue");
    sn_json_key(&j, "n"); sn_json_float(&j, -3.5, 2u); sn_json_obj_end(&j);
    CHECK(sn_json_finish(&j));
    full_len = j.len;
    for (cap = 0u; cap <= (full_len + 2u); cap++) {
        char b[160];
        memset(b, 'X', sizeof(b));
        sn_json_init(&j, b, cap);
        sn_json_obj_begin(&j); sn_json_key(&j, "key"); sn_json_str(&j, "va\"lue");
        sn_json_key(&j, "n"); sn_json_float(&j, -3.5, 2u); sn_json_obj_end(&j);
        CHECK(sn_json_finish(&j) == (cap > full_len));
        CHECK(b[cap] == 'X');                        /* guard byte untouched */
        if (cap > 0u) {
            CHECK(strlen(b) < cap);
            CHECK(strncmp(b, full, strlen(b)) == 0);  /* a prefix of the doc */
        }
    }
}

static void json_misuse_detected(void)
{
    char b[64];
    sn_json_t j;
    sn_json_init(&j, b, sizeof(b));
    sn_json_key(&j, "top");                          /* key outside object */
    CHECK(!sn_json_finish(&j));
    sn_json_init(&j, b, sizeof(b));
    sn_json_obj_begin(&j); sn_json_key(&j, "a"); sn_json_obj_end(&j);   /* dangling key */
    CHECK(!sn_json_finish(&j));
    sn_json_init(&j, b, sizeof(b));
    sn_json_arr_begin(&j);                           /* unclosed */
    CHECK(!sn_json_finish(&j));
    sn_json_init(&j, b, sizeof(b));
    sn_json_arr_end(&j);                             /* close without open */
    CHECK(!sn_json_finish(&j));
    sn_json_init(&j, b, sizeof(b));
    {
        uint32_t i;
        for (i = 0u; i < (SN_JSON_MAX_DEPTH + 1u); i++) {
            sn_json_arr_begin(&j);
        }
    }
    CHECK(j.error);
    sn_json_init(&j, NULL, 10u);
    sn_json_null(&j);
    CHECK(!sn_json_finish(&j));
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(json_nested_document);
    RUN_TEST(json_string_escaping);
    RUN_TEST(json_integers_extremes);
    RUN_TEST(json_float_formatting);
    RUN_TEST(json_overflow_every_capacity);
    RUN_TEST(json_misuse_detected);
    TEST_END();
}
