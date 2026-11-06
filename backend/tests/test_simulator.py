"""End-to-end: simulator -> bus -> ingest -> API, entirely in process."""
from collections import Counter

from sentinel_backend.auth import device_tag
from sentinel_backend.schemas import EventMsg
from sentinel_backend.simulator import make_devices, run, soc_to_mv, stream
from conftest import SECRET


def test_simulated_events_all_validate():
    devs = make_devices(4, seed=3)
    n = 0
    for d, ev, clip in stream(devs, 100):
        EventMsg.model_validate(ev)
        assert (clip is not None) == (ev["clip_id"] is not None)
        n += 1
    assert n == 400


def test_simulated_mix_is_realistic():
    actions = Counter()
    for _, ev, _ in stream(make_devices(5, seed=1), 200):
        actions[ev["action"]] += 1
    total = sum(actions.values())
    assert 0.35 < actions["NONE"] / total < 0.65  # nuisance + timer dominate
    assert actions["RING"] > 0 and actions["LOG_KNOWN"] > 0 and actions["NOTIFY_UNKNOWN_WITH_CLIP"] > 0


def test_stream_is_time_ordered_and_battery_monotone():
    last_ts = 0
    soc = {}
    for d, ev, _ in stream(make_devices(3, seed=2), 150):
        assert ev["ts"] >= last_ts
        last_ts = ev["ts"]
        assert ev["soc_pct"] <= soc.get(d.dev, 100)
        soc[d.dev] = ev["soc_pct"]


def test_soc_curve_monotone():
    vals = [soc_to_mv(i / 100) for i in range(101)]
    assert vals == sorted(vals) and vals[0] == 3300 and vals[-1] == 4200


def test_end_to_end_http_clips(env):
    def up(dev, clip_id, data):
        return env.client.put(f"/api/clips/{clip_id}", content=data,
                              headers={"Authorization": f"Device {dev}:{device_tag(SECRET, dev)}",
                                       "Content-Type": "video/mp4"}).status_code

    st = run(env.bus, up, n_devices=3, events_per_device=40, seed=5, dup_rate=0.1)
    assert st["upload_failures"] == 0 and st["dups"] > 0
    assert env.db.count_events() == 120  # duplicates removed
    assert env.ingest.stats.duplicates == st["dups"] and env.ingest.stats.rejected == 0
    events = env.client.get("/api/events?limit=1000").json()
    with_clip = [e for e in events if e["clip_id"]]
    assert len(with_clip) == st["clips"] > 0
    for e in with_clip[:5]:
        assert env.client.get(f"/api/clips/{e['clip_id']}").status_code == 200
    devs = env.client.get("/api/devices").json()
    assert len(devs) == 3 and all(d["status"] == "online" for d in devs)


def test_end_to_end_mqtt_clips(env):
    st = run(env.bus, None, n_devices=2, events_per_device=30, seed=9, mqtt_clips=True, chunk_size=1024)
    events = env.db.events(limit=1000)
    clip_ids = [e["clip_id"] for e in events if e["clip_id"]]
    assert len(clip_ids) == st["clips"] > 0
    assert all(env.db.clip(c) is not None for c in clip_ids)


def test_simulated_devices_receive_per_kind_ota_and_feedback(env):
    """OTA notices published while the fleet is offline are all seen when it connects."""
    from sentinel_backend.simulator import make_devices

    dev = make_devices(1, seed=5)[0].dev
    sha = "ef" * 32
    for kind, ver in [("fw_n6", "1.3.0"), ("model", "0.4.0"), ("fw_c6", "2.0.1")]:
        r = env.client.post(f"/api/devices/{dev}/ota",
                            json={"kind": kind, "version": ver, "url": f"https://x/{kind}", "size": 1, "sha256": sha})
        assert r.status_code == 201
    st = run(env.bus, None, n_devices=1, events_per_device=5, seed=5, mqtt_clips=True)
    assert {k: v["version"] for k, v in st["ota_notices"][dev].items()} == \
        {"fw_n6": "1.3.0", "model": "0.4.0", "fw_c6": "2.0.1"}
