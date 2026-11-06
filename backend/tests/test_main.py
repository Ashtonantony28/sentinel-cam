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
