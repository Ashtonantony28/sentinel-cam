"""Production entrypoint: `uvicorn sentinel_backend.main:app` (configured from env).

Env vars:
  SENTINEL_DB                path to SQLite file (default ./data/sentinel.db)
  SENTINEL_STORAGE           "local" (default) or "s3"
  SENTINEL_CLIP_DIR          local store root (default ./data/clips)
  SENTINEL_S3_BUCKET, SENTINEL_S3_ENDPOINT, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
  SENTINEL_API_TOKEN_SHA256  comma-separated SHA-256 hex of accepted API tokens
  SENTINEL_DEVICE_SECRET     secret for device upload tags (hex or text)
  SENTINEL_MQTT_HOST/PORT    broker (MQTT disabled if host unset: in-memory bus)
  SENTINEL_MQTT_CA/CERT/KEY  backend's own mTLS client credentials
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from .api import Settings, create_app
from .bus import InMemoryBus, PahoBus
from .db import Database
from .storage import LocalFSStore, S3Store


def build_from_env():
    env = os.environ
    db_path = env.get("SENTINEL_DB", "./data/sentinel.db")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db = Database(db_path)
    if env.get("SENTINEL_STORAGE", "local") == "s3":
        store = S3Store(env["SENTINEL_S3_BUCKET"], endpoint_url=env.get("SENTINEL_S3_ENDPOINT"))
    else:
        store = LocalFSStore(env.get("SENTINEL_CLIP_DIR", "./data/clips"))
    hashes = [h.strip() for h in env.get("SENTINEL_API_TOKEN_SHA256", "").split(",") if h.strip()]
    if not hashes:
        logging.warning("SENTINEL_API_TOKEN_SHA256 is empty: every REST call except /api/health will be refused")
    secret = env.get("SENTINEL_DEVICE_SECRET")
    if not secret:
        raise SystemExit("SENTINEL_DEVICE_SECRET must be set")
    if env.get("SENTINEL_MQTT_HOST"):
        bus = PahoBus(env["SENTINEL_MQTT_HOST"], int(env.get("SENTINEL_MQTT_PORT", "8883")),
                      tls_ca=env.get("SENTINEL_MQTT_CA"), tls_cert=env.get("SENTINEL_MQTT_CERT"),
                      tls_key=env.get("SENTINEL_MQTT_KEY"))
    else:
        logging.warning("SENTINEL_MQTT_HOST unset: using in-memory bus (no devices can connect)")
        bus = InMemoryBus()
    return create_app(Settings(api_token_hashes=hashes, device_secret=secret.encode()), db, store, bus)


if os.environ.get("SENTINEL_DEVICE_SECRET"):
    app = build_from_env()
