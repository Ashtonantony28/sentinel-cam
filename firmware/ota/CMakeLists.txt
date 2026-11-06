# Sentinel WS3: OTA + security library (firmware/ota).
# Works standalone (cmake -S firmware/ota) and as add_subdirectory(ota).
cmake_minimum_required(VERSION 3.20)
project(sentinel_ota LANGUAGES C)

if(CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
  set(OTA_TOP_LEVEL ON)
  if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE RelWithDebInfo CACHE STRING "Build type" FORCE)
  endif()
else()
  set(OTA_TOP_LEVEL OFF)
endif()

if(CMAKE_CROSSCOMPILING)
  set(_ota_host OFF)
else()
  set(_ota_host ON)
endif()
option(OTA_BUILD_TESTS "Build OTA host tests and tools" ${_ota_host})
# Standalone host builds sanitize by default. As a subdirectory the parent
# decides (firmware/ builds once plain and once with SENTINEL_SANITIZE=ON,
# whose global flags our targets inherit).
if(_ota_host AND OTA_TOP_LEVEL)
  set(_ota_san_default ON)
else()
  set(_ota_san_default OFF)
endif()
option(OTA_SANITIZE "Build OTA targets with ASan + UBSan" ${_ota_san_default})
set(OTA_FI_RUNS 2000 CACHE STRING "Randomized power-cut runs for ota.fault_injection")
set(OTA_FI_SEED "" CACHE STRING "Seed for ota.fault_injection (empty = fixed default)")

set(OTA_WARN -Wall -Wextra -Werror -Wpedantic -Wshadow -Wstrict-prototypes -Wcast-qual -Wconversion
             -Wmissing-prototypes -Wformat=2)
set(OTA_SAN_FLAGS -fsanitize=address,undefined -fno-sanitize-recover=all -fno-omit-frame-pointer)

function(ota_target_setup tgt)
  set_target_properties(${tgt} PROPERTIES C_STANDARD 11 C_STANDARD_REQUIRED ON C_EXTENSIONS OFF)
  if(OTA_SANITIZE)
    target_compile_options(${tgt} PRIVATE ${OTA_SAN_FLAGS})
    target_link_options(${tgt} PUBLIC -fsanitize=address,undefined)
  endif()
endfunction()

# --- vendored micro-ecc (BSD-2-Clause), P-256 only ----------------------------
add_library(ota_uecc STATIC vendor/micro-ecc/uECC.c)
target_include_directories(ota_uecc PUBLIC vendor/micro-ecc)
target_compile_definitions(ota_uecc PUBLIC
  uECC_SUPPORTS_secp160r1=0 uECC_SUPPORTS_secp192r1=0 uECC_SUPPORTS_secp224r1=0
  uECC_SUPPORTS_secp256r1=1 uECC_SUPPORTS_secp256k1=0 uECC_SUPPORT_COMPRESSED_POINT=0)
# -Wpedantic is left off for vendored code only (it uses unsigned __int128 on 64-bit hosts).
# Always optimized: ECDSA verify dominates test time and the target build will be -O2 anyway.
target_compile_options(ota_uecc PRIVATE -Wall -Wextra -Werror -O2)
ota_target_setup(ota_uecc)

# --- the library ----------------------------------------------------------------
add_library(ota STATIC
  src/ota_sha256.c src/ota_crc32.c src/ota_crypto_uecc.c src/ota_manifest.c
  src/ota_flash.c src/ota_meta.c src/ota_slots.c src/ota_strings.c)
target_include_directories(ota PUBLIC include)
# Hash is on the hot path of every verify; keep it optimized in Debug builds too.
set_source_files_properties(src/ota_sha256.c PROPERTIES COMPILE_OPTIONS -O2)
target_link_libraries(ota PRIVATE ota_uecc)
target_compile_options(ota PRIVATE ${OTA_WARN})
ota_target_setup(ota)

if(NOT OTA_BUILD_TESTS)
  return()
endif()

# --- host simulator, tests, tools --------------------------------------------
add_library(ota_sim STATIC sim/ota_flash_sim.c)
target_include_directories(ota_sim PUBLIC sim)
target_link_libraries(ota_sim PUBLIC ota)
target_compile_options(ota_sim PRIVATE ${OTA_WARN})
ota_target_setup(ota_sim)

add_library(ota_test_support STATIC tests/test_support.c)
target_include_directories(ota_test_support PUBLIC tests)
target_link_libraries(ota_test_support PUBLIC ota ota_sim ota_uecc)
target_compile_options(ota_test_support PRIVATE ${OTA_WARN})
ota_target_setup(ota_test_support)

add_executable(ota_test_unit tests/test_unit.c)
target_link_libraries(ota_test_unit PRIVATE ota_test_support)
target_compile_options(ota_test_unit PRIVATE ${OTA_WARN})
ota_target_setup(ota_test_unit)

add_executable(ota_test_fault tests/test_fault_injection.c)
target_link_libraries(ota_test_fault PRIVATE ota_test_support)
target_compile_options(ota_test_fault PRIVATE ${OTA_WARN})
ota_target_setup(ota_test_fault)

add_executable(ota_host_verify host/ota_host_verify.c)
target_link_libraries(ota_host_verify PRIVATE ota ota_sim)
target_compile_options(ota_host_verify PRIVATE ${OTA_WARN})
ota_target_setup(ota_host_verify)

enable_testing()
set(OTA_SAN_ENV "ASAN_OPTIONS=detect_leaks=1:abort_on_error=0:halt_on_error=1"
                "UBSAN_OPTIONS=halt_on_error=1:print_stacktrace=1")

set(OTA_UNIT_CASES
  sha256_vectors sha256_streaming crc32 crypto_basic manifest_parse manifest_check
  image_verify_tamper sim_semantics layout_check meta_roundtrip meta_power_cut
  writer_chunks writer_rejects writer_state_rules boot_trial_never_confirms
  boot_pending_corrupted counter_rules physical_downgrade model_opset
  model_trial_never_confirms app_attach_second_ctx power_cut_every_op)
foreach(c ${OTA_UNIT_CASES})
  add_test(NAME ota.unit.${c} COMMAND ota_test_unit ${c})
  set_tests_properties(ota.unit.${c} PROPERTIES ENVIRONMENT "${OTA_SAN_ENV}" LABELS "ota;unit")
endforeach()

set(_fi_args --runs ${OTA_FI_RUNS})
if(NOT OTA_FI_SEED STREQUAL "")
  list(APPEND _fi_args --seed ${OTA_FI_SEED})
endif()
add_test(NAME ota.fault_injection COMMAND ota_test_fault ${_fi_args})
set_tests_properties(ota.fault_injection PROPERTIES ENVIRONMENT "${OTA_SAN_ENV}" LABELS "ota;fault" TIMEOUT 1800)
