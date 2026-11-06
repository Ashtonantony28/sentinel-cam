"""Validates firmware event JSON against docs/INTERFACES.md section 3."""
import json
import subprocess
import sys

WAKES = {"PIR", "RADAR", "BUTTON", "TIMER"}
ACTIONS = {"NONE", "LOG_KNOWN", "NOTIFY_UNKNOWN_WITH_CLIP", "RING"}
KEYS = ["v", "dev", "ts", "wake", "action", "person_conf", "faces", "clip_id",
        "battery_mv", "soc_pct", "fw", "model", "energy_mj"]


def check(doc):
    assert list(doc.keys()) == KEYS, doc.keys()
    assert doc["v"] == 1
    assert isinstance(doc["dev"], str) and len(doc["dev"]) == 12
    int(doc["dev"], 16)
    assert isinstance(doc["ts"], int) and doc["ts"] > 0
    assert doc["wake"] in WAKES and doc["action"] in ACTIONS
    assert isinstance(doc["person_conf"], float) and 0.0 <= doc["person_conf"] <= 1.0
    assert isinstance(doc["faces"], list)
    for f in doc["faces"]:
        assert list(f.keys()) == ["id", "score"]
        assert f["id"] is None or isinstance(f["id"], int)
        assert isinstance(f["score"], float)
    assert doc["clip_id"] is None or isinstance(doc["clip_id"], str)
    for k in ("battery_mv", "soc_pct", "energy_mj"):
        assert isinstance(doc[k], int) and not isinstance(doc[k], bool)
    assert isinstance(doc["fw"], str) and isinstance(doc["model"], str)


def main():
    out = subprocess.run([sys.argv[1]], check=True, capture_output=True, text=True).stdout
    lines = [l for l in out.splitlines() if l]
    assert len(lines) == 16, len(lines)
    for line in lines:
        check(json.loads(line))
    assert json.loads(lines[0])["model"] == 'm"odel\\\n'
    print(f"{len(lines)} event JSON documents valid")


if __name__ == "__main__":
    main()
