"""Shared fixtures: repo paths, the firmware host build, import paths of every workstream."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
for sub in ("backend", "fl", "ml", "tools", "hardware/power_model"):
    p = str(REPO / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

FW_BUILD = Path(os.environ.get("FW_BUILD", REPO / "firmware" / "build" / "plain"))


def _bin(rel: str) -> Path:
    p = FW_BUILD / rel
    if not p.is_file():
        pytest.fail(f"{p} missing: run `make -C tests/integration fw` (or `make -C firmware build`)")
    return p


@pytest.fixture(scope="session")
def fw_bin():
    return _bin


@pytest.fixture(scope="session")
def scenario_day_output(fw_bin):
    """stdout of the scripted 24 h firmware day (EVENT / STATUS JSON lines)."""
    exe = fw_bin("core/test_scenario_day")
    out = ""
    for case in ("day_records_serialize", "day_status_json"):
        r = subprocess.run([str(exe), case], capture_output=True, text=True, check=True)
        out += r.stdout
    return out
