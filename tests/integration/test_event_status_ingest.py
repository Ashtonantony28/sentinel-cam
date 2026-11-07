"""3a / 3b: firmware event + status JSON -> backend pydantic models -> in-memory MQTT -> SQLite."""
import json
import subprocess

import pytest
from sentinel_backend.bus import InMemoryBus
from sentinel_backend.db import Database
from sentinel_backend.ingest import Ingest
from sentinel_backend.schemas import EventMsg, StatusMsg
from sentinel_backend.storage import LocalFSStore


def _lines(text, tag):
    pre = f"  {tag} "
    return [ln[len(pre):] for ln in text.splitlines() if ln.startswith(pre)]


@pytest.fixture
def backend(tmp_path):
    db = Database(str(tmp_path / "ingest.db"))
    bus = InMemoryBus()
    ing = Ingest(db, LocalFSStore(tmp_path / "clips"))
    ing.attach(bus)
    return db, bus, ing


@pytest.fixture(scope="module")
def dump_events(fw_bin):
    out = subprocess.run([str(fw_bin("core/event_json_dump"))], capture_output=True, text=True, check=True).stdout
    return [ln for ln in out.splitlines() if ln]


def test_event_dump_validates_with_backend_model(dump_events):
    assert len(dump_events) == 16                          # every wake x action
    seen = set()
    for line in dump_events:
        ev = EventMsg.model_validate_json(line)
        seen.add((ev.wake, ev.action))
        assert json.loads(line)["model"] == ev.model       # escaped strings survive
    assert len(seen) == 16


def test_event_dump_ingests_end_to_end(backend, dump_events):
    db, bus, ing = backend
    for line in dump_events:
        dev = json.loads(line)["dev"]
        bus.publish(f"sentinel/{dev}/event", line.encode(), qos=1)
    assert ing.stats.rejected == 0, db.rejects()
    assert ing.stats.events == 16 and db.count_events() == 16
    rows = {r["ts"]: r for r in db.events(limit=100)}
    for line in dump_events:
        src = json.loads(line)
        row = rows[src["ts"]]
        for k in ("wake", "action", "clip_id", "battery_mv", "soc_pct", "fw", "model", "energy_mj"):
            assert row[k] == src[k], k
        assert row["faces"] == src["faces"]
    # QoS 1 redelivery of the same firmware message is stored once
    bus.publish(f"sentinel/{json.loads(dump_events[0])['dev']}/event", dump_events[0].encode(), qos=1)
    assert db.count_events() == 16 and ing.stats.duplicates == 1


def test_scripted_day_events_ingest(backend, scenario_day_output):
    """Real pipeline output (brainstem + N6 pipeline over the simulated 24 h day)."""
    db, bus, ing = backend
    events = _lines(scenario_day_output, "EVENT")
    assert len(events) >= 40
    for line in events:
        ev = EventMsg.model_validate_json(line)
        bus.publish(f"sentinel/{ev.dev}/event", line.encode(), qos=1)
    assert ing.stats.rejected == 0, db.rejects()
    assert db.count_events() == len(events)
    acts = [r["action"] for r in db.events(limit=1000)]
    assert acts.count("LOG_KNOWN") == 3 and acts.count("RING") == 1
    assert all(r["clip_id"] for r in db.events(limit=1000) if r["action"] == "NOTIFY_UNKNOWN_WITH_CLIP")


def test_scripted_day_fault_status_ingests(backend, scenario_day_output):
    db, bus, ing = backend
    statuses = _lines(scenario_day_output, "STATUS")
    assert [StatusMsg.model_validate_json(s).reason for s in statuses] == ["LOW_BATTERY", "BROWNOUT"]
    dev = json.loads(_lines(scenario_day_output, "EVENT")[0])["dev"]
    for s in statuses:
        bus.publish(f"sentinel/{dev}/status", s.encode(), qos=1, retain=True)
    assert ing.stats.rejected == 0, db.rejects()
    assert ing.stats.faults == 2
    d = db.device(dev)
    assert d["status"] == "fault" and d["status_reason"] == "BROWNOUT"
    log = db.status_log(dev=dev)
    assert sorted(r["reason"] for r in log) == ["BROWNOUT", "LOW_BATTERY"]


def test_status_writer_all_states_validate(tmp_path, fw_bin):
    """Every status the C writer emits is accepted by StatusMsg, and everything StatusMsg
    rejects the writer refuses too. The writer is stricter in one place: it never puts a
    reason on online/offline (the backend tolerates that; INTERFACES 3 does not say)."""
    src = tmp_path / "status_all.c"
    src.write_text(r'''
#include <stdio.h>
#include "sn_status.h"
int main(void) {
    for (int s = 0; s < 4; s++) for (int r = 0; r < 6; r++) {
        sn_status_t st = { (sn_status_state_t)s, (sn_fault_reason_t)r, 1793923200000ull + (uint64_t)(s * 10 + r), 3700u };
        char b[SN_STATUS_JSON_MAX];
        size_t n = sn_status_to_json(&st, b, sizeof b);
        printf("%d %d %s\n", s, r, n ? b : "-");
    }
    return 0;
}''')
    from conftest import REPO, FW_BUILD
    exe = tmp_path / "status_all"
    core = REPO / "firmware" / "core"
    subprocess.run(["cc", "-std=c11", "-I", str(core / "include"), "-I", str(core / "hal"), str(src),
                    str(FW_BUILD / "core" / "libsn_core.a"), "-lm", "-o", str(exe)], check=True)
    states = ["online", "offline", "fault", None]
    reasons = [None, "LOW_BATTERY", "BROWNOUT", "POWER_FAULT", "JOB_TIMEOUT", "BOGUS"]
    n_ok = 0
    for line in subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout.splitlines():
        s, r, js = line.split(" ", 2)
        cand = {"state": states[int(s)], "reason": reasons[int(r)], "ts": 1, "battery_mv": 3700}
        try:
            StatusMsg.model_validate(cand)
            backend_ok = True
        except Exception:
            backend_ok = False
        if js != "-":
            assert backend_ok, (cand, js)
        elif backend_ok:
            assert cand["state"] in ("online", "offline") and cand["reason"] is not None, cand
        if js != "-":
            assert StatusMsg.model_validate_json(js).state == cand["state"]
            n_ok += 1
    assert n_ok == 2 + 4      # online/offline with null reason, fault with each reason
