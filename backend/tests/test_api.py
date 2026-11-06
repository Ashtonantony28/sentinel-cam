import hashlib
import json

import pytest

from sentinel_backend.storage import clip_key
from conftest import DEV, DEV2, TOKEN, dev_auth, make_event


def pub(env, **kw):
    ev = make_event(**kw)
    env.bus.publish(f"sentinel/{ev['dev']}/event", json.dumps(ev).encode(), qos=1)
    return ev


# ---------------------------------------------------------------- auth
@pytest.mark.parametrize("method,path", [
    ("get", "/api/events"), ("get", "/api/devices"), ("get", "/api/events/1"), ("get", "/api/clips/x"),
    ("post", f"/api/devices/{DEV}/feedback"), ("post", f"/api/devices/{DEV}/ota"), ("get", "/api/rejects"),
    ("delete", f"/api/devices/{DEV}/ota"), ("get", "/api/stats"),
])
def test_user_endpoints_require_token(env, method, path):
    env.client.headers.pop("Authorization")
    assert getattr(env.client, method)(path).status_code == 401
    env.client.headers["Authorization"] = "Bearer wrong"
    assert getattr(env.client, method)(path).status_code == 401


def test_health_and_dashboard_are_public(env):
    env.client.headers.pop("Authorization")
    assert env.client.get("/api/health").json()["ok"] is True
    r = env.client.get("/")
    assert r.status_code == 200 and "Sentinel events" in r.text


def test_device_cannot_use_user_endpoints(env):
    r = env.client.get("/api/events", headers=dev_auth())
    assert r.status_code == 401


# ---------------------------------------------------------------- events
def test_list_and_filter_events(env):
    pub(env, ts=1000, action="NONE", clip_id=None, faces=[])
    pub(env, ts=2000, action="LOG_KNOWN", clip_id=None, faces=[{"id": 2, "score": 0.8}])
    pub(env, ts=3000)
    pub(env, dev=DEV2, ts=1500, action="RING", wake="BUTTON")
    all_ = env.client.get("/api/events").json()
    assert [e["ts"] for e in all_] == [3000, 2000, 1500, 1000]
    assert [e["ts"] for e in env.client.get(f"/api/events?dev={DEV}").json()] == [3000, 2000, 1000]
    assert [e["ts"] for e in env.client.get("/api/events?action=RING").json()] == [1500]
    assert [e["ts"] for e in env.client.get("/api/events?since=1500&until=3000").json()] == [2000, 1500]
    assert [e["ts"] for e in env.client.get("/api/events?limit=2&offset=1").json()] == [2000, 1500]
    assert env.client.get("/api/events?action=BOGUS").status_code == 422
    assert env.client.get("/api/events?dev=../x").status_code == 422
    assert env.client.get("/api/events?limit=0").status_code == 422
    one = env.client.get(f"/api/events/{all_[0]['id']}").json()
    assert one["ts"] == 3000
    assert env.client.get("/api/events/9999").status_code == 404
    devs = env.client.get("/api/devices").json()
    assert {d["dev"] for d in devs} == {DEV, DEV2}


# ---------------------------------------------------------------- clips
def test_clip_upload_and_download(env):
    data = b"\x00\x00\x00\x18ftypisom" + b"x" * 5000
    r = env.client.put("/api/clips/clip-1", content=data, headers={**dev_auth(), "Content-Type": "video/mp4"})
    assert r.status_code == 201 and r.json()["sha256"] == hashlib.sha256(data).hexdigest()
    assert env.store.get(clip_key(DEV, "clip-1")) == data
    # idempotent retry
    r = env.client.put("/api/clips/clip-1", content=data, headers={**dev_auth(), "Content-Type": "video/mp4"})
    assert r.status_code == 200
    # different content for same id
    r = env.client.put("/api/clips/clip-1", content=b"other", headers={**dev_auth(), "Content-Type": "video/mp4"})
    assert r.status_code == 409
    # another device cannot overwrite
    r = env.client.put("/api/clips/clip-1", content=data, headers={**dev_auth(DEV2), "Content-Type": "video/mp4"})
    assert r.status_code == 403
    g = env.client.get("/api/clips/clip-1")
    assert g.status_code == 200 and g.content == data and g.headers["content-type"] == "video/mp4"


def test_clip_upload_auth_and_validation(env):
    h = {"Content-Type": "video/mp4"}
    assert env.client.put("/api/clips/c", content=b"x", headers={**h, "Authorization": f"Bearer {TOKEN}"}).status_code == 401
    assert env.client.put("/api/clips/c", content=b"x", headers={**h, "Authorization": f"Device {DEV}:00"}).status_code == 401
    assert env.client.put("/api/clips/c", content=b"x", headers={**h, "Authorization": "Device zz:00"}).status_code == 401
    assert env.client.put("/api/clips/bad%20id", content=b"x", headers={**h, **dev_auth()}).status_code == 422
    assert env.client.put("/api/clips/..", content=b"x", headers={**h, **dev_auth()}).status_code in (404, 422)
    assert env.client.put("/api/clips/c", content=b"", headers={**h, **dev_auth()}).status_code == 400
    assert env.client.put("/api/clips/c", content=b"x", headers={**dev_auth(), "Content-Type": "text/html"}).status_code == 415
    assert env.client.put("/api/clips/c", content=b"x",
                          headers={**h, **dev_auth(), "X-Content-SHA256": "0" * 64}).status_code == 400


def test_clip_too_large(env):
    big = b"x" * (21 * 1024 * 1024)
    r = env.client.put("/api/clips/big", content=big, headers={**dev_auth(), "Content-Type": "video/mp4"})
    assert r.status_code == 413
    assert env.db.clip("big") is None


def test_clip_owned_by_event_device(env):
    pub(env, dev=DEV2, clip_id="theirs")
    r = env.client.put("/api/clips/theirs", content=b"abc", headers={**dev_auth(DEV), "Content-Type": "video/mp4"})
    assert r.status_code == 403


def test_get_missing_clip(env):
    assert env.client.get("/api/clips/nope").status_code == 404


# ---------------------------------------------------------------- feedback
def test_feedback_publishes_qos1(env):
    pub(env, ts=4242)
    r = env.client.post(f"/api/devices/{DEV}/feedback", json={"event_ts": 4242, "label": "false_alarm"})
    assert r.status_code == 201
    msgs = env.bus.messages(f"sentinel/{DEV}/feedback")
    assert len(msgs) == 1 and msgs[0].qos == 1 and not msgs[0].retain
    assert json.loads(msgs[0].payload) == {"v": 1, "event_ts": 4242, "label": "false_alarm", "person_id": None}
    r = env.client.post(f"/api/devices/{DEV}/feedback", json={"event_ts": 4242, "label": "known", "person_id": 3})
    assert r.status_code == 201
    assert json.loads(env.bus.messages(f"sentinel/{DEV}/feedback")[1].payload)["person_id"] == 3
    assert [f["label"] for f in env.client.get(f"/api/devices/{DEV}/feedback").json()] == ["false_alarm", "known"]


@pytest.mark.parametrize("body,code", [
    ({"event_ts": 4242, "label": "known"}, 422),
    ({"event_ts": 4242, "label": "person", "person_id": 1}, 422),
    ({"event_ts": 4242, "label": "nah"}, 422),
    ({"event_ts": 1, "label": "person"}, 404),
    ({"event_ts": 4242, "label": "person", "x": 1}, 422),
])
def test_feedback_validation(env, body, code):
    pub(env, ts=4242)
    assert env.client.post(f"/api/devices/{DEV}/feedback", json=body).status_code == code
    assert env.bus.messages(f"sentinel/{DEV}/feedback") == []


def test_feedback_bad_device(env):
    assert env.client.post("/api/devices/xyz/feedback", json={"event_ts": 1, "label": "person"}).status_code == 404


# ---------------------------------------------------------------- OTA
OTA = {"kind": "model", "version": "0.4.0", "url": "https://ota.example/model-0.4.0.bin", "size": 123456,
       "sha256": "ab" * 32}


def test_ota_published_retained(env):
    r = env.client.post(f"/api/devices/{DEV}/ota", json=OTA)
    assert r.status_code == 201
    m = env.bus.messages(f"sentinel/{DEV}/ota")[-1]
    assert m.retain and m.qos == 1
    assert json.loads(m.payload) == {"v": 1, **OTA}
    # A device that connects later still gets it.
    got = []
    env.bus.subscribe(f"sentinel/{DEV}/ota", lambda t, p: got.append(json.loads(p) if p else None))
    assert got[0]["version"] == "0.4.0"
    assert env.db.ota(DEV)[0]["version"] == "0.4.0"
    assert env.client.delete(f"/api/devices/{DEV}/ota").status_code == 204
    assert f"sentinel/{DEV}/ota" not in env.bus.retained and env.db.ota(DEV) == []
    assert got[-1] is None  # subscribers see the empty retained-clear message


@pytest.mark.parametrize("patch", [{"version": "1.2"}, {"sha256": "zz"}, {"kind": "fw"}, {"size": 0},
                                   {"url": "file:///etc/passwd"}, {"size": "10"}])
def test_ota_validation(env, patch):
    assert env.client.post(f"/api/devices/{DEV}/ota", json={**OTA, **patch}).status_code == 422
    assert env.bus.messages(f"sentinel/{DEV}/ota") == []
