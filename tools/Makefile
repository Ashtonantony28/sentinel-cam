# Sentinel tools (WS3): OTA signing tool + cross tests against the C library.
#
#   make test        pytest (signing tool + Python->C cross tests), then the
#                    firmware/ota ctest suite (unit + fault injection, ASan/UBSan)
#   make test-py     pytest only (incl. the gen_fw_power_model.py freshness test)
#   make test-ota    firmware/ota ctest only
#   make fault-soak  long fault-injection run: make fault-soak RUNS=20000 SEED=123
#   make deps        pip install -r requirements.txt
#
# OTA_CTEST=1 forces the ctest part of `make test`; see the default below.

PY        ?= python3
OTA_DIR   := $(abspath ../firmware/ota)
OTA_BUILD ?= $(OTA_DIR)/build
# Default: skip when firmware/CMakeLists.txt already add_subdirectory()s ota
# (root `make test` then runs those tests via firmware/, plain + ASan).
OTA_CTEST ?= $(if $(shell grep -s 'add_subdirectory(ota)' ../firmware/CMakeLists.txt),0,1)
RUNS      ?= 20000
SEED      ?= 1582809310
JOBS      ?= $(shell nproc 2>/dev/null || echo 4)

.PHONY: test test-py test-ota build-ota fault-soak deps clean

test: test-py
ifeq ($(OTA_CTEST),1)
test: test-ota
endif

build-ota:
	cmake -S $(OTA_DIR) -B $(OTA_BUILD)
	cmake --build $(OTA_BUILD) -j $(JOBS)

test-py: build-ota
	OTA_HOST_VERIFY=$(OTA_BUILD)/ota_host_verify $(PY) -m pytest -q tests

test-ota: build-ota
	ctest --test-dir $(OTA_BUILD) --output-on-failure -j $(JOBS)

fault-soak: build-ota
	$(OTA_BUILD)/ota_test_fault --runs $(RUNS) --seed $(SEED)

deps:
	$(PY) -m pip install -r requirements.txt

clean:
	rm -rf $(OTA_BUILD) .pytest_cache tests/__pycache__ __pycache__
