# firmware/core: portable C11 libraries (no malloc, static buffers) + host
# simulated HAL + unit/scenario tests.

# Strict warnings for everything in core.
add_library(sn_warnings INTERFACE)
target_compile_options(sn_warnings INTERFACE
  -Wall -Wextra -Werror -Wpedantic -Wshadow -Wstrict-prototypes
  -Wmissing-prototypes -Wcast-align -Wpointer-arith -Wundef -Wdouble-promotion
  -Wformat=2 -Wvla)

# Target-independent libraries (what gets cross-compiled for M55 / RISC-V).
add_library(sn_core STATIC
  src/sn_crc32.c
  src/sn_energy.c
  src/sn_json.c
  src/sn_event.c
  src/sn_gallery.c
  src/sn_power_seq.c
  src/sn_wake.c
  src/sn_pipeline.c
  src/sn_brainstem.c)
target_include_directories(sn_core PUBLIC include hal)
target_link_libraries(sn_core PUBLIC m PRIVATE sn_warnings)

# Host simulation of the HAL + scripted inference provider.
add_library(sn_sim STATIC sim/sim_hal.c sim/sim_infer.c)
target_include_directories(sn_sim PUBLIC sim)
target_link_libraries(sn_sim PUBLIC sn_core PRIVATE sn_warnings)

# Golden vectors from ml/ (WS1). If present at configure time, the golden
# test consumes them; otherwise it runs on vectors generated in-test.
set(SN_GOLDEN_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../ml/golden" CACHE PATH
    "Directory with ml/ golden vectors for the gallery matcher")

# Each RUN_TEST(name) in a test file becomes its own ctest case.
function(sn_add_test_exe name)
  add_executable(${name} tests/${name}.c ${ARGN})
  target_include_directories(${name} PRIVATE tests)
  target_link_libraries(${name} PRIVATE sn_sim sn_core sn_warnings)
  file(STRINGS ${CMAKE_CURRENT_SOURCE_DIR}/tests/${name}.c lines REGEX "^[ \t]*RUN_TEST\\(")
  foreach(line IN LISTS lines)
    string(REGEX REPLACE "^[ \t]*RUN_TEST\\(([A-Za-z0-9_]+)\\).*" "\\1" case "${line}")
    add_test(NAME core.${name}.${case} COMMAND ${name} ${case})
  endforeach()
endfunction()

sn_add_test_exe(test_crc32)
sn_add_test_exe(test_json)
sn_add_test_exe(test_event)
sn_add_test_exe(test_gallery)
sn_add_test_exe(test_power_seq)
sn_add_test_exe(test_wake)
sn_add_test_exe(test_pipeline)
sn_add_test_exe(test_brainstem tests/scenario.c)
sn_add_test_exe(test_scenario_day tests/scenario.c)
if(EXISTS ${SN_GOLDEN_DIR}/MANIFEST.json)
  sn_add_test_exe(test_golden)
  target_compile_definitions(test_golden PRIVATE SN_GOLDEN_DIR="${SN_GOLDEN_DIR}")
else()
  # Integration hook: regenerate with `python -m sentinel_ml.golden` (ml/) or
  # point -DSN_GOLDEN_DIR=... at a copy, then re-run cmake.
  message(WARNING "No golden vectors at ${SN_GOLDEN_DIR}; core.golden.* tests not registered")
endif()

# Portability audit: core sources may only include the allowed libc headers.
add_test(NAME core.audit.libc_headers
  COMMAND ${CMAKE_COMMAND} -DSRC_DIR=${CMAKE_CURRENT_SOURCE_DIR}
          -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/audit_headers.cmake)

# Portability: compile the core for Cortex-M55 (N6) and RV32IMAC (C6) with
# clang, freestanding, when clang is installed.
find_program(SN_CLANG NAMES clang clang-18)
if(SN_CLANG)
  foreach(tgt m55 rv32)
    add_test(NAME core.cross.${tgt}
      COMMAND ${CMAKE_COMMAND} -DCLANG=${SN_CLANG} -DSRC_DIR=${CMAKE_CURRENT_SOURCE_DIR}
              -DOUT_DIR=${CMAKE_CURRENT_BINARY_DIR}/cross_${tgt} -DARCH=${tgt}
              -P ${CMAKE_CURRENT_SOURCE_DIR}/tests/cross_compile.cmake)
  endforeach()
endif()

# Cross-check the event JSON with Python's json parser when available.
find_package(Python3 COMPONENTS Interpreter QUIET)
if(Python3_Interpreter_FOUND)
  add_executable(event_json_dump tests/event_json_dump.c)
  target_link_libraries(event_json_dump PRIVATE sn_core sn_warnings)
  add_test(NAME core.event_json.python_schema
    COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/check_event_json.py
            $<TARGET_FILE:event_json_dump>)
endif()
