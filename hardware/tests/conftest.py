import sys
from pathlib import Path

HW = Path(__file__).resolve().parents[1]
for sub in ("power_model", "bom", "spec"):
    p = str(HW / sub)
    if p not in sys.path:
        sys.path.insert(0, p)
