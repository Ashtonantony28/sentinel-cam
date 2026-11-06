#include "sn_crc32.h"

/* Nibble table: 64 bytes of const data, 2 lookups per byte. */
static const uint32_t k_crc_nibble[16] = {
    0x00000000u, 0x1DB71064u, 0x3B6E20C8u, 0x26D930ACu,
    0x76DC4190u, 0x6B6B51F4u, 0x4DB26158u, 0x5005713Cu,
    0xEDB88320u, 0xF00F9344u, 0xD6D6A3E8u, 0xCB61B38Cu,
    0x9B64C2B0u, 0x86D3D2D4u, 0xA00AE278u, 0xBDBDF21Cu
};

uint32_t sn_crc32_update(uint32_t crc, const uint8_t *data, size_t len)
{
    uint32_t c = ~crc;
    size_t i;
    if (data == NULL) {
        return crc;
    }
    for (i = 0u; i < len; i++) {
        c ^= (uint32_t)data[i];
        c = (c >> 4) ^ k_crc_nibble[c & 0x0Fu];
        c = (c >> 4) ^ k_crc_nibble[c & 0x0Fu];
    }
    return ~c;
}

uint32_t sn_crc32(const uint8_t *data, size_t len)
{
    return sn_crc32_update(0u, data, len);
}
