# Cross-workstream contract tests (firmware <-> backend <-> ml <-> tools <-> fl).
# Uses a local .venv that sees the system site-packages (torch/onnx/numpy/cryptography
# are what ml/ and tools/ already use) and adds pydantic for the backend schemas.
PY    ?= python3.13
BIN   := .venv/bin
JOBS  ?= $(shell nproc 2>/dev/null || echo 4)
ROOT  := $(abspath ../..)
FW_BUILD ?= $(ROOT)/firmware/build/plain

.PHONY: test venv fw clean
venv: .venv/.stamp
.venv/.stamp: requirements.txt
	test -x $(BIN)/python || $(PY) -m venv --system-site-packages .venv
	$(BIN)/pip install -q -r requirements.txt
	touch $@

# The firmware host build provides event JSON, head_eval and ota_host_verify.
fw:
	cmake -S $(ROOT)/firmware -B $(FW_BUILD) -DCMAKE_BUILD_TYPE=Debug >/dev/null
	cmake --build $(FW_BUILD) -j $(JOBS) --target test_scenario_day event_json_dump head_eval ota_host_verify >/dev/null

test: venv fw
	FW_BUILD=$(FW_BUILD) $(BIN)/python -m pytest -q

clean:
	rm -rf .venv .pytest_cache __pycache__
