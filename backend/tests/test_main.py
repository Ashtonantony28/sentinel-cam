"""Env-driven production wiring (in-memory bus, local storage)."""
import hashlib

from fastapi.testclient import TestClient

from sentinel_backend.main import build_from_env


def test_build_from_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SENTINEL_DB", str(tmp_path / "x" / "s.db"))
    monkeypatch.setenv("SENTINEL_CLIP_DIR", str(tmp_path / "clips"))
    monkeypatch.setenv("SENTINEL_DEVICE_SECRET", "abc")
    monkeypatch.setenv("SENTINEL_API_TOKEN_SHA256", hashlib.sha256(b"tok").hexdigest())
    monkeypatch.delenv("SENTINEL_MQTT_HOST", raising=False)
    c = TestClient(build_from_env())
    assert c.get("/api/health").status_code == 200
    assert c.get("/api/events").status_code == 401
    assert c.get("/api/events", headers={"Authorization": "Bearer tok"}).json() == []


def test_db_migrates_pre_status_json_schema(tmp_path):
    import sqlite3

    from sentinel_backend.db import Database

    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE devices (dev TEXT PRIMARY KEY, first_seen_ms INTEGER NOT NULL, last_seen_ms INTEGER NOT NULL,"
              " status TEXT, fw TEXT, model TEXT, battery_mv INTEGER, soc_pct INTEGER)")
    c.execute("INSERT INTO devices VALUES ('a4cf12000001', 1, 1, 'online', NULL, NULL, NULL, NULL)")
    c.commit(); c.close()
    db = Database(str(p))
    assert db.record_status("a4cf12000001", "fault", "BROWNOUT", 5, 3300)
    assert db.device("a4cf12000001")["status_reason"] == "BROWNOUT"
