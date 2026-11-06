import json

import pytest

from sentinel_backend.bus import InMemoryBus, topic_matches
from sentinel_backend.storage import clip_key
from conftest import DEV, DEV2, make_event


@pytest.mark.parametrize("f,t,ok", [
    ("sentinel/+/event", "sentinel/abc/event", True),
    ("sentinel/+/event", "sentinel/abc/status", False),
    ("sentinel/+/event", "sentinel/abc/event/x", False),
    ("sentinel/#", "sentinel/abc/clip/1", True),
    ("sentinel/+/clip/+", "sentinel/abc/clip/1", True),
    ("sentinel/+/clip/+", "sentinel/abc/clip", False),
    ("a/b", "a/b", True),
])
def test_topic_matches(f, t, ok):
    assert topic_matches(f, t) is ok


def test_retained_delivered_to_late_subscriber_and_cleared():
    bus = InMemoryBus()
    bus.publish("sentinel/x/ota/model", b"n1", retain=True)
    got = []
    bus.subscribe("sentinel/+/ota/+", lambda t, p: got.append(p))
    assert got == [b"n1"]
    bus.publish("sentinel/x/ota/model", b"", retain=True)
    assert "sentinel/x/ota/model" not in bus.retained


def pub(env, ev, dev=DEV):
    env.bus.publish(f"sentinel/{dev}/event", json.dumps(ev).encode(), qos=1)


def test_event_ingested_and_device_updated(env):
    pub(env, make_event())
    rows = env.db.events()
    assert len(rows) == 1 and rows[0]["clip_id"] == "clip-1" and rows[0]["faces"] == [{"id": None, "score": 0.31}]
    d = env.db.device(DEV)
    assert d["soc_pct"] == 82 and d["fw"] == "1.2.0"


def test_qos1_duplicate_is_deduplicated(env):
    pub(env, make_event())
    pub(env, make_event())
    assert env.db.count_events() == 1
    assert env.ingest.stats.duplicates == 1


@pytest.mark.parametrize("payload,reason", [
    (b"{not json", "invalid JSON"),
    (json.dumps(make_event(action="PANIC")).encode(), "schema"),
    (json.dumps(make_event(dev=DEV2)).encode(), "dev mismatch"),
    (b"\xff\xfe", "invalid JSON"),
    (b"[" * 9000, "too large"),
])
def test_bad_events_rejected_not_stored(env, payload, reason):
    env.bus.publish(f"sentinel/{DEV}/event", payload)
    assert env.db.count_events() == 0
    rej = env.db.rejects()
    assert len(rej) == 1 and reason in rej[0]["reason"]


def test_bad_topic_rejected(env):
    env.ingest.handle("sentinel/NOTADEV/event", json.dumps(make_event()).encode())
    env.ingest.handle(f"sentinel/{DEV}/event/extra", b"{}")
    assert env.db.count_events() == 0 and len(env.db.rejects()) == 2


def st(state="online", reason=None, ts=1000, battery_mv=3900, **kw):
    return json.dumps({"state": state, "reason": reason, "ts": ts, "battery_mv": battery_mv, **kw}).encode()


def test_status_json_and_lwt(env):
    env.bus.publish(f"sentinel/{DEV}/status", st(ts=1000), retain=True)
    d = env.db.device(DEV)
    assert (d["status"], d["status_reason"], d["status_ts"], d["battery_mv"]) == ("online", None, 1000, 3900)
    env.bus.publish(f"sentinel/{DEV}/status", b"offline", retain=True)  # plain-text LWT still allowed
    d = env.db.device(DEV)
    assert d["status"] == "offline" and d["status_reason"] is None
    assert env.db.status_log(DEV)[0]["lwt"] == 1


@pytest.mark.parametrize("reason", ["LOW_BATTERY", "BROWNOUT", "POWER_FAULT", "JOB_TIMEOUT"])
def test_fault_status_stored_with_reason(env, reason):
    env.bus.publish(f"sentinel/{DEV}/status", st("fault", reason, ts=2000, battery_mv=3310), retain=True)
    d = env.db.device(DEV)
    assert (d["status"], d["status_reason"], d["battery_mv"]) == ("fault", reason, 3310)
    faults = env.db.status_log(DEV, state="fault")
    assert len(faults) == 1 and faults[0]["reason"] == reason and faults[0]["ts"] == 2000
    assert env.ingest.stats.faults == 1
    # Recovery clears the device's current reason but keeps the fault history.
    env.bus.publish(f"sentinel/{DEV}/status", st(ts=3000), retain=True)
    d = env.db.device(DEV)
    assert d["status"] == "online" and d["status_reason"] is None
    assert len(env.db.status_log(DEV, state="fault")) == 1


def test_stale_retained_status_ignored(env):
    env.bus.publish(f"sentinel/{DEV}/status", st("fault", "BROWNOUT", ts=5000), retain=True)
    env.bus.publish(f"sentinel/{DEV}/status", st(ts=4000), retain=True)  # older, replayed
    assert env.db.device(DEV)["status"] == "fault" and env.ingest.stats.stale_status == 1


@pytest.mark.parametrize("payload", [
    b"online",                                   # plain text is no longer valid (only the LWT)
    b"\x00garbage",
    st(state="sleeping"),
    st("fault", None),                           # fault requires a reason
    st(reason="OVERHEAT"),
    st(ts=1.5), st(ts="1000"), st(ts=-1), st(battery_mv=None), st(battery_mv=True),
    st(rssi=-60),                                # unknown key
    b'{"state":"online","reason":null,"ts":1}',  # missing battery_mv
])
def test_bad_status_rejected(env, payload):
    env.bus.publish(f"sentinel/{DEV}/status", payload, retain=True)
    assert env.db.device(DEV) is None
    assert env.db.rejects()[0]["reason"].startswith("bad status payload")


def chunks(data, clip_id, size=1000):
    parts = [data[i:i + size] for i in range(0, len(data), size)]
    return [(json.dumps({"seq": i, "last": i == len(parts) - 1}).encode() + b"\n" + p) for i, p in enumerate(parts)]


def test_mqtt_clip_chunks_reassembled_out_of_order(env):
    data = bytes(range(256)) * 20
    msgs = chunks(data, "c9")
    for m in reversed(msgs):  # out of order, with a duplicate
        env.bus.publish(f"sentinel/{DEV}/clip/c9", m)
    env.bus.publish(f"sentinel/{DEV}/clip/c9", msgs[0])
    assert env.store.get(clip_key(DEV, "c9")) == data
    assert env.db.clip("c9")["size"] == len(data)


def test_mqtt_clip_bad_header(env):
    env.bus.publish(f"sentinel/{DEV}/clip/c9", b'{"seq":"x","last":true}\nabc')
    env.bus.publish(f"sentinel/{DEV}/clip/c9", b"no header")
    assert env.db.clip("c9") is None and len(env.db.rejects()) == 2


def test_mqtt_clip_too_large(env):
    env.ingest.max_clip_bytes = 1500
    for m in chunks(b"x" * 3000, "big"):
        env.bus.publish(f"sentinel/{DEV}/clip/big", m)
    assert env.db.clip("big") is None
