# WS5 hardware package. `make test` is called by the repo-root Makefile.
PYTHON ?= python3
export PYTHONPATH := $(CURDIR)/power_model:$(CURDIR)/bom:$(CURDIR)/spec

.PHONY: test report bom i2c deps
test:
	$(PYTHON) -m pytest tests -q
	$(PYTHON) bom/check_bom.py bom/bom.csv
	$(PYTHON) spec/i2c_check.py

report:            ## regenerate power_model/out/report.md (+ JSON, PNG if matplotlib is installed)
	$(PYTHON) -m sentinel_power --n 20000

bom:
	$(PYTHON) bom/check_bom.py bom/bom.csv

i2c:
	$(PYTHON) spec/i2c_check.py

deps:
	$(PYTHON) -m pip install -r requirements.txt
