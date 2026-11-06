# Compiles every core library source for a 32-bit embedded target with clang,
# freestanding, against cross_shim/ (only the allowed libc functions), with
# warnings as errors. Then links all objects into one relocatable to report
# code/RAM size. Args: -DCLANG=... -DSRC_DIR=... -DOUT_DIR=... -DARCH=m55|rv32
if(ARCH STREQUAL "m55")
  set(TFLAGS --target=thumbv8.1m.main-none-eabihf -mcpu=cortex-m55 -mfloat-abi=hard)
elseif(ARCH STREQUAL "rv32")
  set(TFLAGS --target=riscv32-unknown-elf -march=rv32imac -mabi=ilp32)   # ESP32-C6 ISA
else()
  message(FATAL_ERROR "unknown ARCH ${ARCH}")
endif()
file(GLOB SRCS ${SRC_DIR}/src/*.c)
file(MAKE_DIRECTORY ${OUT_DIR})
set(objs "")
foreach(src IN LISTS SRCS)
  get_filename_component(name ${src} NAME_WE)
  set(obj ${OUT_DIR}/${name}.o)
  execute_process(
    COMMAND ${CLANG} ${TFLAGS} -std=c11 -ffreestanding -nostdlibinc -Os
            -ffunction-sections -fdata-sections
            -Wall -Wextra -Werror -Wpedantic -Wshadow -Wdouble-promotion -Wcast-align
            -Wno-unknown-warning-option
            -I${SRC_DIR}/tests/cross_shim -I${SRC_DIR}/include -I${SRC_DIR}/hal
            -c ${src} -o ${obj}
    RESULT_VARIABLE rc OUTPUT_VARIABLE out ERROR_VARIABLE err)
  if(NOT rc EQUAL 0)
    message(FATAL_ERROR "${ARCH}: ${name}.c failed:\n${out}${err}")
  endif()
  list(APPEND objs ${obj})
endforeach()
find_program(LLVM_SIZE NAMES llvm-size llvm-size-18)
if(LLVM_SIZE)
  execute_process(COMMAND ${LLVM_SIZE} -t ${objs} OUTPUT_VARIABLE sz)
  message(STATUS "${ARCH} object sizes (text/data/bss):\n${sz}")
endif()
list(LENGTH objs nobj)
message(STATUS "${ARCH}: ${nobj} core sources compiled")
