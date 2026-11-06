# WS4 backend. `make test` builds a local venv on first use (needs network once).
PY ?= python3.13
BIN := .venv/bin

.PHONY: test venv run sim
venv: .venv/.stamp
.venv/.stamp: requirements.txt requirements-dev.txt
	test -x $(BIN)/python || $(PY) -m venv .venv
	$(BIN)/pip install -q -r requirements-dev.txt
	touch $@

test: venv
	$(BIN)/python -m pytest -q

# Local dev server with an in-memory bus (token: dev-token)
run: venv
	SENTINEL_DEVICE_SECRET=dev-secret \
	SENTINEL_API_TOKEN_SHA256=$$(printf dev-token | sha256sum | cut -d' ' -f1) \
	$(BIN)/uvicorn sentinel_backend.main:app --reload

sim: venv
	$(BIN)/python -m sentinel_backend.simulator --devices 3 --events 30 --api http://localhost:8000 --device-secret dev-secret
