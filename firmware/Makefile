# Sentinel firmware host tests. `make test` configures, builds and runs ctest
# for everything under firmware/ twice: a plain build and an ASan+UBSan build.
CMAKE      ?= cmake
CTEST      ?= ctest
BUILD      ?= build/plain
ASAN_BUILD ?= build/asan
JOBS       ?= $(shell nproc 2>/dev/null || echo 4)

.PHONY: all test test-plain test-asan build clean

all: build

build:
	$(CMAKE) -S . -B $(BUILD) -DCMAKE_BUILD_TYPE=Debug
	$(CMAKE) --build $(BUILD) -j $(JOBS)

test: test-plain test-asan

test-plain: build
	$(CTEST) --test-dir $(BUILD) --output-on-failure -j $(JOBS)

test-asan:
	$(CMAKE) -S . -B $(ASAN_BUILD) -DCMAKE_BUILD_TYPE=Debug -DSENTINEL_SANITIZE=ON
	$(CMAKE) --build $(ASAN_BUILD) -j $(JOBS)
	ASAN_OPTIONS=detect_leaks=1:abort_on_error=1 UBSAN_OPTIONS=print_stacktrace=1:halt_on_error=1 \
	  $(CTEST) --test-dir $(ASAN_BUILD) --output-on-failure -j $(JOBS)

clean:
	rm -rf build build-asan
