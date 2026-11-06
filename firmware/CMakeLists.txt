# Sentinel firmware: host build of the portable libraries + tests.
#   make -C firmware test   (plain + ASan/UBSan builds, ctest each)
cmake_minimum_required(VERSION 3.20)
project(sentinel_firmware C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)
set(CMAKE_C_EXTENSIONS OFF)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Debug)
endif()

option(SENTINEL_SANITIZE "Build everything with -fsanitize=address,undefined" OFF)
if(SENTINEL_SANITIZE)
  add_compile_options(-fsanitize=address,undefined -fno-omit-frame-pointer -fno-sanitize-recover=all)
  add_link_options(-fsanitize=address,undefined)
endif()

enable_testing()

add_subdirectory(core)
option(SENTINEL_WITH_OTA "Include firmware/ota when it has a CMakeLists.txt" ON)
if(SENTINEL_WITH_OTA AND EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/ota/CMakeLists.txt)
  add_subdirectory(ota)
endif()
