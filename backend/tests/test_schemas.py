"""INTERFACES.md §3 schema conformance."""
import json

import pytest
from pydantic import ValidationError

from sentinel_backend.schemas import EventMsg, FeedbackMsg, OtaNotice, StatusMsg, ota_topic, pack_semver
from conftest import make_event

CONTRACT_EXAMPLE = (
    '{"v":1,"dev":"a4cf12000001","ts":1793000000000,"wake":"BUTTON","action":"RING","person_conf":0.73,'
    '"faces":[{"id":3,"score":0.81},{"id":null,"score":0.2}],"clip_id":null,"battery_mv":3900,"soc_pct":75,'
    '"fw":"1.2.0","model":"pd-0.3.1","energy_mj":2900}'
)


def test_valid_event_roundtrip():
    e = EventMsg.model_validate_json(CONTRACT_EXAMPLE)
    assert e.faces[0].id == 3 and e.faces[1].id is None
    # Serializing back gives the same keys, in contract order.
    assert list(json.loads(e.model_dump_json()).keys()) == list(json.loads(CONTRACT_EXAMPLE).keys())


@pytest.mark.parametrize("wake", ["PIR", "RADAR", "BUTTON", "TIMER"])
@pytest.mark.parametrize("action", ["NONE", "LOG_KNOWN", "NOTIFY_UNKNOWN_WITH_CLIP", "RING"])
def test_all_enum_values_accepted(wake, action):
    EventMsg.model_validate(make_event(wake=wake, action=action))


def test_person_conf_accepts_json_integer():
    EventMsg.model_validate_json(CONTRACT_EXAMPLE.replace('"person_conf":0.73', '"person_conf":1'))


@pytest.mark.parametrize("field,value", [
    ("v", 2), ("v", True), ("v", "1"),
    ("dev", "a4cf1200000"), ("dev", "a4cf120000zz"), ("dev", 123),
    ("ts", 1.5), ("ts", "1793000000000"), ("ts", -1), ("ts", True),
    ("wake", "MOTION"), ("wake", "pir"),
    ("action", "NOTIFY"),
    ("person_conf", 1.5), ("person_conf", -0.1), ("person_conf", "0.9"), ("person_conf", True), ("faces", [{"id": 1, "score": "0.5"}]),
    ("faces", None), ("faces", [{"id": "3", "score": 0.5}]), ("faces", [{"id": 3}]),
    ("faces", [{"id": 3, "score": 0.5, "name": "x"}]),
    ("clip_id", 5), ("clip_id", "../etc/passwd"), ("clip_id", ".."), ("clip_id", "a/b"),
    ("battery_mv", 3.9), ("soc_pct", 101), ("soc_pct", -1),
    ("fw", 1), ("model", None), ("energy_mj", -5), ("energy_mj", 1.0),
])
def test_invalid_fields_rejected(field, value):
    with pytest.raises(ValidationError):
        EventMsg.model_validate(make_event(**{field: value}))


@pytest.mark.parametrize("missing", ["v", "dev", "ts", "wake", "action", "person_conf", "faces", "clip_id",
                                     "battery_mv", "soc_pct", "fw", "model", "energy_mj"])
def test_every_field_required(missing):
    e = make_event()
    del e[missing]
    with pytest.raises(ValidationError):
        EventMsg.model_validate(e)


def test_extra_field_rejected():
    with pytest.raises(ValidationError):
        EventMsg.model_validate(make_event(extra=1))


def test_feedback_shape():
    m = FeedbackMsg(event_ts=5, label="known", person_id=2)
    assert json.loads(m.model_dump_json()) == {"v": 1, "event_ts": 5, "label": "known", "person_id": 2}
    with pytest.raises(ValidationError):
        FeedbackMsg(event_ts=5, label="maybe", person_id=None)


def test_ota_shape():
    sha = "AB" * 32
    m = OtaNotice(kind="model", version="1.4.2", url="https://x/y.bin", size=10, sha256=sha)
    assert json.loads(m.model_dump_json()) == {"v": 1, "kind": "model", "version": "1.4.2",
                                               "url": "https://x/y.bin", "size": 10, "sha256": sha.lower()}
    for bad in [dict(version="1.4"), dict(version="1.256.0"), dict(sha256="ab"), dict(kind="fw"),
                dict(size=0), dict(url="ftp://x")]:
        args = dict(kind="model", version="1.4.2", url="https://x/y.bin", size=10, sha256=sha)
        args.update(bad)
        with pytest.raises(ValidationError):
            OtaNotice(**args)


def test_pack_semver():
    assert pack_semver("1.2.3") == 0x010203
    assert pack_semver("300.0.1") == (300 << 16) | 1


def test_status_shape():
    m = StatusMsg.model_validate_json('{"state":"fault","reason":"JOB_TIMEOUT","ts":5,"battery_mv":3400}')
    assert m.reason == "JOB_TIMEOUT"
    StatusMsg.model_validate_json('{"state":"online","reason":null,"ts":5,"battery_mv":3400}')
    for bad in ['{"state":"fault","reason":null,"ts":5,"battery_mv":1}', '{"state":"online","ts":5,"battery_mv":1}']:
        with pytest.raises(ValidationError):
            StatusMsg.model_validate_json(bad)


def test_ota_topic():
    assert ota_topic("a4cf12000001", "fw_c6") == "sentinel/a4cf12000001/ota/fw_c6"
    with pytest.raises(ValueError):
        ota_topic("a4cf12000001", "fw")
