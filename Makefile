# Runs every workstream's tests. Each sub-target must exit non-zero on failure.
.PHONY: test test-ml test-firmware test-backend test-fl test-hardware test-tools test-integration clean-build
test: test-firmware test-tools test-hardware test-backend test-fl test-ml test-integration
test-ml:          ; $(MAKE) -C ml test
test-firmware:    ; $(MAKE) -C firmware test
test-tools:       ; $(MAKE) -C tools test
test-backend:     ; $(MAKE) -C backend test
test-fl:          ; $(MAKE) -C fl test
test-hardware:    ; $(MAKE) -C hardware test
# Cross-workstream contract tests (firmware <-> backend/ml/tools/fl); see tests/integration.
test-integration: ; $(MAKE) -C tests/integration test

# Removes every build dir and local venv, to prove the from-scratch path.
clean-build:
	rm -rf firmware/build firmware/ota/build backend/.venv fl/.venv tests/integration/.venv
