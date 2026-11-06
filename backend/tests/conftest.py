import pytest
from fastapi.testclient import TestClient

from sentinel_backend.api import Settings, create_app
from sentinel_backend.auth import device_tag, hash_token
from sentinel_backend.bus import InMemoryBus
from sentinel_backend.db import Database
from sentinel_backend.storage import LocalFSStore

TOKEN = "test-token-123"
SECRET = b"device-secret"
DEV = "a4cf12000001"
DEV2 = "a4cf12000002"


def make_event(**kw):
    e = {"v": 1, "dev": DEV, "ts": 1_793_000_000_000, "wake": "PIR", "action": "NOTIFY_UNKNOWN_WITH_CLIP",
         "person_conf": 0.91, "faces": [{"id": None, "score": 0.31}], "clip_id": "clip-1",
         "battery_mv": 3950, "soc_pct": 82, "fw": "1.2.0", "model": "pd-0.3.1", "energy_mj": 14200}
    e.update(kw)
    return e


@pytest.fixture
def env(tmp_path):
    db = Database(str(tmp_path / "t.db"))
    store = LocalFSStore(tmp_path / "clips")
    bus = InMemoryBus()
    app = create_app(Settings(api_token_hashes=[hash_token(TOKEN)], device_secret=SECRET), db, store, bus)
    client = TestClient(app)
    client.headers["Authorization"] = f"Bearer {TOKEN}"

    class Env:
        pass

    e = Env()
    e.db, e.store, e.bus, e.app, e.client = db, store, bus, app, client
    e.ingest = app.state.ingest
    return e


def dev_auth(dev=DEV):
    return {"Authorization": f"Device {dev}:{device_tag(SECRET, dev)}"}
