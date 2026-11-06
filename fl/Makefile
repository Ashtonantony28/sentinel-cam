# WS4 federated learning. `make test` builds a local venv on first use (needs network once).
PY ?= python3.13
BIN := .venv/bin

.PHONY: test test-fast venv experiment experiment-quick
venv: .venv/.stamp
.venv/.stamp: requirements.txt requirements-dev.txt
	test -x $(BIN)/python || $(PY) -m venv .venv
	$(BIN)/pip install -q -r requirements-dev.txt
	touch $@

test: venv
	$(BIN)/python -m pytest -q

# Everything except the Ray-backed Flower simulation.
test-fast: venv
	$(BIN)/python -m pytest -q -m "not flower"

# Full grid: 10 configs x 3 seeds x 300 homes x 100 rounds (1–2.5 min on 4 CPUs). Writes results/.
experiment: venv
	$(BIN)/python -m sentinel_fl.experiment

experiment-quick: venv
	$(BIN)/python -m sentinel_fl.experiment --quick --out results/quick
