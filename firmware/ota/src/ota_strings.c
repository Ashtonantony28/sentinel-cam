#include "ota/ota_types.h"

const char *ota_err_str(int err) {
    switch (err) {
    case OTA_OK: return "OK";
    case OTA_ERR_PARAM: return "ERR_PARAM";
    case OTA_ERR_FLASH: return "ERR_FLASH";
    case OTA_ERR_MAGIC: return "ERR_MAGIC";
    case OTA_ERR_MANIFEST_VERSION: return "ERR_MANIFEST_VERSION";
    case OTA_ERR_KIND: return "ERR_KIND";
    case OTA_ERR_RESERVED: return "ERR_RESERVED";
    case OTA_ERR_FLAGS: return "ERR_FLAGS";
    case OTA_ERR_SIZE: return "ERR_SIZE";
    case OTA_ERR_LOAD_ADDR: return "ERR_LOAD_ADDR";
    case OTA_ERR_SIGNATURE: return "ERR_SIGNATURE";
    case OTA_ERR_HASH: return "ERR_HASH";
    case OTA_ERR_ROLLBACK: return "ERR_ROLLBACK";
    case OTA_ERR_OPSET: return "ERR_OPSET";
    case OTA_ERR_STATE: return "ERR_STATE";
    case OTA_ERR_NO_META: return "ERR_NO_META";
    case OTA_ERR_TRUNCATED: return "ERR_TRUNCATED";
    case OTA_ERR_OVERFLOW: return "ERR_OVERFLOW";
    case OTA_ERR_NO_IMAGE: return "ERR_NO_IMAGE";
    case OTA_ERR_LAYOUT: return "ERR_LAYOUT";
    case OTA_ERR_KEY: return "ERR_KEY";
    default: return "ERR_UNKNOWN";
    }
}

const char *ota_slot_state_str(int st) {
    switch (st) {
    case OTA_SLOT_EMPTY: return "EMPTY";
    case OTA_SLOT_PENDING: return "PENDING";
    case OTA_SLOT_TRIAL: return "TRIAL";
    case OTA_SLOT_CONFIRMED: return "CONFIRMED";
    case OTA_SLOT_BAD: return "BAD";
    default: return "INVALID";
    }
}
