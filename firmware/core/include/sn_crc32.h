/* sn_crc32.h - CRC-32/IEEE 802.3 (reflected poly 0xEDB88320, init/xorout 0xFFFFFFFF). */
#ifndef SN_CRC32_H
#define SN_CRC32_H
#include <stdint.h>
#include <stddef.h>

/* One-shot. sn_crc32(NULL, 0) == 0. "123456789" -> 0xCBF43926. */
uint32_t sn_crc32(const uint8_t *data, size_t len);
/* Incremental: start with crc = 0, feed chunks, result equals one-shot. */
uint32_t sn_crc32_update(uint32_t crc, const uint8_t *data, size_t len);

#endif
