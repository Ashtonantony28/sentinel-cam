# Sentinel WS1 (ML). CPU only. `make test` must pass and stays well under ~5 min.
PY ?= python3
export PYTHONPATH := $(CURDIR)

.PHONY: test deps golden quick full npu-fit clean

test:
	$(PY) -m pytest tests -p no:warnings

deps:
	$(PY) -m pip install -r requirements.txt

# Regenerate the C-firmware golden vectors (byte-identical unless the generator changed;
# firmware/core consumes them byte for byte, so commit any change together with firmware).
golden:
	$(PY) -m sentinel_ml.golden --out golden

# ~1-2 min smoke run of the whole pipeline on tiny models.
quick:
	$(PY) -m sentinel_ml.pipeline --profile quick --out artifacts/quick

# The README numbers (~1-1.5 h on 4 CPUs): train → PTQ/QAT → int8 ONNX → parity → NPU-fit → eval.
full:
	$(PY) -m sentinel_ml.pipeline --profile full --out artifacts/full

npu-fit:
	$(PY) -m sentinel_ml.npu_fit artifacts/full/person_int8_qat.onnx artifacts/full/face_int8_qat.onnx \
		artifacts/full/emb_int8_qat.onnx --require-int8 --json artifacts/full/npu_fit.json

clean:
	rm -rf artifacts/quick .pytest_cache
