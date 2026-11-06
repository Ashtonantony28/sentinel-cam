#include "sn_test.h"
#include "sn_crc32.h"

static void crc_check_value(void)
{
    const uint8_t s[] = "123456789";
    CHECK_EQ_INT(sn_crc32(s, 9u), 0xCBF43926u);
}

static void crc_empty_and_null(void)
{
    CHECK_EQ_INT(sn_crc32(NULL, 0u), 0u);
    CHECK_EQ_INT(sn_crc32((const uint8_t *)"", 0u), 0u);
    CHECK_EQ_INT(sn_crc32_update(0x1234u, NULL, 5u), 0x1234u);
}

static void crc_known_vectors(void)
{
    const uint8_t a[] = "a";
    const uint8_t fox[] = "The quick brown fox jumps over the lazy dog";
    uint8_t zeros[32] = { 0 };
    CHECK_EQ_INT(sn_crc32(a, 1u), 0xE8B7BE43u);
    CHECK_EQ_INT(sn_crc32(fox, sizeof(fox) - 1u), 0x414FA339u);
    CHECK_EQ_INT(sn_crc32(zeros, 32u), 0x190A55ADu);
}

static void crc_incremental_matches_oneshot(void)
{
    uint8_t buf[257];
    uint32_t i;
    uint32_t split;
    for (i = 0u; i < sizeof(buf); i++) {
        buf[i] = (uint8_t)((i * 37u) ^ (i >> 3));
    }
    for (split = 0u; split <= sizeof(buf); split += 13u) {
        uint32_t c = sn_crc32_update(0u, buf, split);
        c = sn_crc32_update(c, &buf[split], sizeof(buf) - split);
        CHECK_EQ_INT(c, sn_crc32(buf, sizeof(buf)));
    }
}

int main(int argc, char **argv)
{
    TEST_BEGIN(argc, argv);
    RUN_TEST(crc_check_value);
    RUN_TEST(crc_empty_and_null);
    RUN_TEST(crc_known_vectors);
    RUN_TEST(crc_incremental_matches_oneshot);
    TEST_END();
}
