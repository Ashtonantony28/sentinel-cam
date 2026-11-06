/* CRC-32 (IEEE 802.3, reflected, poly 0xEDB88320), bitwise, no table. */
#ifndef OTA_CRC32_H
#define OTA_CRC32_H
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
uint32_t ota_crc32(const void *data, size_t len);
#ifdef __cplusplus
}
#endif
#endif
