"""One test against a real MQTT broker over TCP (amqtt in-process, or `mosquitto` if on PATH).

Skipped if no broker can be started. Exercises PahoBus: QoS 1 events, per-kind retained OTA notices
(`sentinel/{dev}/ota/{kind}`) all delivered to a late subscriber, feedback round-trip, and the LWT `offline` status.
"""
import asyncio
import json
import shutil
import socket
import subprocess
import threading
import time

import pytest

from sentinel_backend.bus import PahoBus
from conftest import DEV, make_event


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_port(port, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return True
        except OSError:
            time.sleep(0.05)
    return False


@pytest.fixture(scope="module")
def broker_port():
    port = _free_port()
    if shutil.which("mosquitto"):
        proc = subprocess.Popen(["mosquitto", "-p", str(port)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if _wait_port(port):
            yield port
            proc.terminate()
            return
        proc.kill()
    try:
        from amqtt.broker import Broker
    except Exception as e:  # pragma: no cover
        pytest.skip(f"no MQTT broker available: {e}")
    loop = asyncio.new_event_loop()
    cfg = {"listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}}, "sys_interval": 0,
           "auth": {"allow-anonymous": True}, "topic-check": {"enabled": False}}
    holder = {}

    def runner():
        asyncio.set_event_loop(loop)
        try:
            b = Broker(cfg, loop=loop)
            loop.run_until_complete(b.start())
            holder["broker"] = b
            loop.run_forever()
        except Exception as e:  # pragma: no cover
            holder["err"] = e

    th = threading.Thread(target=runner, daemon=True)
    th.start()
    if not _wait_port(port):
        pytest.skip(f"amqtt broker failed to start: {holder.get('err')}")
    yield port
    if "broker" in holder:
        fut = asyncio.run_coroutine_threadsafe(holder["broker"].shutdown(), loop)
        try:
            fut.result(timeout=5)
        except Exception:
            pass
    loop.call_soon_threadsafe(loop.stop)


def _until(pred, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_full_flow_over_real_broker(broker_port, tmp_path):
    from fastapi.testclient import TestClient

    from sentinel_backend.api import Settings, create_app
    from sentinel_backend.auth import hash_token
    from sentinel_backend.db import Database
    from sentinel_backend.storage import LocalFSStore

    backend_bus = PahoBus("127.0.0.1", broker_port, client_id="backend-test")
    db = Database()
    app = create_app(Settings([hash_token("t")], b"s"), db, LocalFSStore(tmp_path), backend_bus)
    client = TestClient(app, headers={"Authorization": "Bearer t"})

    device = PahoBus("127.0.0.1", broker_port, client_id=f"dev-{DEV}",
                     will=(f"sentinel/{DEV}/status", b"offline", True))
    feedback = []
    device.subscribe(f"sentinel/{DEV}/feedback", lambda t, p: feedback.append(json.loads(p)), qos=1)
    status = lambda state, reason=None, ts=1: json.dumps(  # noqa: E731
        {"state": state, "reason": reason, "ts": ts, "battery_mv": 3900}).encode()
    device.publish(f"sentinel/{DEV}/status", status("online"), qos=1, retain=True)
    device.publish(f"sentinel/{DEV}/event", json.dumps(make_event(ts=77)).encode(), qos=1)
    assert _until(lambda: db.count_events() == 1), "event not ingested over broker"
    assert _until(lambda: (db.device(DEV) or {}).get("status") == "online")
    device.publish(f"sentinel/{DEV}/status", status("fault", "BROWNOUT", ts=2), qos=1, retain=True)
    assert _until(lambda: db.device(DEV)["status_reason"] == "BROWNOUT")

    r = client.post(f"/api/devices/{DEV}/feedback", json={"event_ts": 77, "label": "false_alarm"})
    assert r.status_code == 201
    assert _until(lambda: feedback == [{"v": 1, "event_ts": 77, "label": "false_alarm", "person_id": None}])

    sha = "cd" * 32
    for kind, ver in [("fw_n6", "1.3.0"), ("fw_c6", "2.0.1"), ("model", "0.4.0")]:
        body = {"kind": kind, "version": ver, "url": f"https://x/{kind}.bin", "size": 9, "sha256": sha}
        assert client.post(f"/api/devices/{DEV}/ota", json=body).status_code == 201
    time.sleep(0.2)
    # A device that was asleep for all three publishes still gets one retained notice per kind.
    late = PahoBus("127.0.0.1", broker_port, client_id="late-sub")
    got = {}
    late.subscribe(f"sentinel/{DEV}/ota/+", lambda t, p: got.__setitem__(t.rsplit("/", 1)[1], json.loads(p)), qos=1)
    assert _until(lambda: len(got) == 3), f"retained OTA notices not all delivered: {sorted(got)}"
    assert {k: v["version"] for k, v in got.items()} == {"fw_n6": "1.3.0", "fw_c6": "2.0.1", "model": "0.4.0"}
    late.close()

    # Ungraceful disconnect -> broker publishes LWT "offline".
    device.client._sock.close() if device.client._sock else None
    device.client.loop_stop()
    assert _until(lambda: db.device(DEV)["status"] == "offline", timeout=60), "LWT not delivered"
    backend_bus.close()
