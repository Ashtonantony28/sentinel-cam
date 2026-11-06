# Runs every workstream's tests. Each sub-target must exit non-zero on failure.
.PHONY: test test-ml test-firmware test-backend test-fl test-hardware test-tools
test: test-firmware test-tools test-hardware test-backend test-fl test-ml
test-ml:       ; $(MAKE) -C ml test
test-firmware: ; $(MAKE) -C firmware test
test-tools:    ; $(MAKE) -C tools test
test-backend:  ; $(MAKE) -C backend test
test-fl:       ; $(MAKE) -C fl test
test-hardware: ; $(MAKE) -C hardware test
