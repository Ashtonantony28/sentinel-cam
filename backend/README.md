# Sentinel backend (WS4)

MQTT ingest, clip store, REST API, and a minimal dashboard for the Sentinel camera fleet
(PLAN §3.3, INTERFACES §3). Self-hostable: Mosquitto + MinIO + one FastAPI process.

> **Contract notes (read first).** This workstream follows INTERFACES.md §3 and changes
> nothing in it. Where §3 is silent, or a stricter rule was needed for safety, the choice
> is listed under [Interpretations and tightenings](#interpretations-and-tightenings-of-interfacesmd-3).
> Implements the two 2026-11-06 contract changes: **per-kind retained OTA topics**
> `sentinel/{dev}/ota/{kind}`, and the **status JSON** `{"state","reason","ts","battery_mv"}`
> with `fault` states and reasons (the plain `offline` LWT is still accepted).

## Layout

| Path | What |
|---|---|
| `sentinel_backend/schemas.py` | Strict pydantic models for every §3 payload (event, feedback, OTA, clip-chunk header) |
| `sentinel_backend/bus.py` | `Bus` interface; `InMemoryBus` (MQTT topic-filter + retained semantics, for tests) and `PahoBus` (real broker, mTLS-capable) |
| `sentinel_backend/ingest.py` | Subscribes `sentinel/+/event`, `+/status`, `+/clip/+`; validates, deduplicates QoS 1 redeliveries, stores; bad messages go to a `rejects` table |
| `sentinel_backend/db.py` | SQLite (WAL): devices, events, clips, feedback, OTA notices, rejects |
| `sentinel_backend/storage.py` | `ClipStore` interface: `LocalFSStore` (atomic writes, path containment) and `S3Store` (AWS S3 / MinIO) |
| `sentinel_backend/api.py` | FastAPI app factory: REST endpoints and the dashboard |
| `sentinel_backend/auth.py` | User API tokens (hashed) and device upload credentials (HMAC tags) |
| `sentinel_backend/static/index.html` | Dashboard: event list, filters, clip viewer, feedback buttons |
| `sentinel_backend/simulator.py` | Device-fleet simulator (realistic event mix, battery drain, clips, QoS 1 duplicates, status JSON with occasional faults, subscribes to its feedback and per-kind OTA topics) |
| `sentinel_backend/main.py` | Production entrypoint configured from env vars |
| `docker-compose.yml`, `Dockerfile`, `deploy/mosquitto/` | Mosquitto (mTLS + per-device ACL), MinIO, API. Not needed for tests. |

## Run

```bash
make test        # builds .venv on first use, runs pytest (~10 s)
make run         # dev server on :8000 with an in-memory bus; API token "dev-token"
make sim         # in a second shell: 3 simulated devices -> needs a broker on localhost:1883
```

With Docker: create `.env` (see the header of `docker-compose.yml`), put certificates in
`deploy/certs/`, then `docker compose up -d`. Then run the simulator against it:
`python -m sentinel_backend.simulator --mqtt-host localhost --mqtt-port 1883 --api http://localhost:8000 --device-secret …`
(the compose broker is mTLS-only on 8883; for a quick local trial run a plain `mosquitto -p 1883`).

## REST API

All endpoints except `GET /api/health` and `GET /` (the dashboard shell, which then asks for a
token) need `Authorization: Bearer <api token>`. Clip upload is the one device endpoint.

| Method + path | Who | Purpose |
|---|---|---|
| `GET /api/health` | public | liveness + event count |
| `GET /api/devices` | user | devices with current status (`online`/`offline`/`fault`), `status_reason`, `status_ts`, battery, fw/model, current OTA notices (one per kind), last 5 faults |
| `GET /api/status-log?dev=&state=&limit=` | user | status history, newest first (e.g. `state=fault` for all faults with reasons) |
| `GET /api/events?dev=&action=&since=&until=&limit=&offset=` | user | newest first; `since` inclusive, `until` exclusive (unix ms) |
| `GET /api/events/{id}` | user | one event |
| `PUT /api/clips/{clip_id}` | device | upload a clip (≤ 20 MiB; `video/mp4`, `video/h264`, `image/jpeg`, `application/octet-stream`). Optional `X-Content-SHA256` is verified. Idempotent: the same bytes again → 200, different bytes → 409, another device's clip → 403 |
| `GET /api/clips/{clip_id}` | user | download (`X-Content-SHA256` header) |
| `POST /api/devices/{dev}/feedback` `{event_ts, label, person_id?}` | user | must reference a stored event; publishes `sentinel/{dev}/feedback` at QoS 1 |
| `GET /api/devices/{dev}/feedback` | user | feedback history |
| `POST /api/devices/{dev}/ota` `{kind, version, url, size, sha256}` | user | validates and publishes a **retained** notice on `sentinel/{dev}/ota/{kind}`; each kind has its own retained slot, so a sleeping device sees all three |
| `DELETE /api/devices/{dev}/ota/{kind}` | user | clears one kind's retained notice (empty retained publish) |
| `DELETE /api/devices/{dev}/ota` | user | clears all three kinds |
| `GET /api/rejects`, `GET /api/stats` | user | invalid MQTT messages, ingest counters |

## Security model

**Devices ↔ broker: mutual TLS, identity = certificate CN.**
- A small private CA (kept offline) issues each device a client certificate at provisioning,
  with `CN = <12-hex device id>`. The key pair is generated on the device (N6 secure storage or
  an ATECC608B, PLAN §6) and only the CSR leaves it.
- Mosquitto (`deploy/mosquitto/mosquitto.conf`) listens only on 8883 with `require_certificate true`
  and `use_identity_as_username true`, so the MQTT username *is* the certificate CN and cannot be
  claimed by password.
- The ACL (`deploy/mosquitto/acl`) uses `%u` patterns: a device may **write** only
  `sentinel/<own id>/{event,status,clip/+}` and **read** only `sentinel/<own id>/feedback` and
  `sentinel/<own id>/ota/+`.
  One device cannot spoof another's events or read another's feedback. The backend has its own
  certificate (`CN=backend`) with `sentinel/#`.
- Defence in depth: ingest also rejects an event whose payload `dev` differs from the topic's `{dev}`.
- Revocation: remove the device's cert from service by adding it to a CRL (`crlfile` in
  mosquitto.conf) and deleting its upload tag.

**REST.**
- *Users*: bearer tokens. The server is configured with SHA-256 hashes only
  (`SENTINEL_API_TOKEN_SHA256`), compares in constant time, and returns 401 without detail.
- *Devices (clip upload)*: `Authorization: Device <dev>:<HMAC-SHA256(device_secret, dev)>`. The tag is
  issued at provisioning next to the client certificate. It binds the upload to a device id, so a
  device can only write clips under its own key prefix (`clips/<dev>/…`) and cannot overwrite another
  device's clip. In production the API sits behind a TLS reverse proxy that can also require the same
  client certificate (mTLS) for `/api/clips/*`.
- Clip ids are validated (no path separators, no `..`); the local store additionally checks that every
  resolved path stays inside its root.
- Not done yet (Phase 2): rate limiting, token scopes, audit log, CRL automation.

## Interpretations and tightenings of INTERFACES.md §3

None of these changes the wire format that a correct device produces.

1. **`clip_id`** must match `[A-Za-z0-9._-]{1,128}` and not be `.`/`..`, because it becomes an object key
   and a URL path segment. §3 only says `str`.
2. **`dev`** must be exactly 12 hex characters (either case). Integers must be JSON integers (not `1.0`,
   not `true`); floats accept JSON integers (`"person_conf": 1`). Unknown keys are rejected. `person_conf`
   ∈ [0, 1], `soc_pct` ∈ [0, 100], `battery_mv`/`energy_mj`/`ts` ≥ 0.
3. **Status payload.** Validated strictly per §3 (`state` ∈ online/offline/fault; `reason` null or one of
   LOW_BATTERY/BROWNOUT/POWER_FAULT/JOB_TIMEOUT; `ts`, `battery_mv` integers ≥ 0; no extra keys). The only
   non-JSON payload accepted is the LWT string `offline`. Tightening: `state: "fault"` requires a non-null
   `reason`. Every status is appended to a `status_log` table; the device row keeps the latest state,
   reason, `ts` and `battery_mv`. A JSON status older (by `ts`) than the stored one is ignored, so a stale
   retained message replayed on reconnect cannot overwrite a newer fault. Existing SQLite files are migrated
   in place (two new `devices` columns).
4. **MQTT clip chunks.** §3 says "header JSON then binary" without a delimiter. Implemented as
   `<header JSON>\n<binary>`. Chunks may arrive out of order or duplicated; the clip is stored once all
   `0..last` chunks are present. HTTP PUT remains the preferred path.
5. **OTA notices** go to `sentinel/{dev}/ota/{kind}` (§3 as amended); the payload's `kind` always equals
   the topic's `{kind}`. The old single `sentinel/{dev}/ota` topic is no longer published to.
6. **Feedback** requires `person_id` when `label == "known"` and forbids it otherwise; it must reference a
   stored event `(dev, event_ts)`.
7. **Duplicate events.** `(dev, ts)` is unique, so QoS 1 redeliveries are stored once. Two genuinely distinct
   events from one device in the same millisecond would collide (not possible with the device pipeline's timing).

## Tests (162, all passing)

| File | Covers |
|---|---|
| `test_schemas.py` | every §3 field: required, typed, ranged, enums, extra keys; feedback/OTA shapes; semver packing |
| `test_bus_ingest.py` | MQTT topic-filter matching, retained semantics, ingest of valid/invalid/duplicate events, status JSON (all four fault reasons, recovery, stale retained replay, strict rejection incl. plain `online`) + `offline` LWT, chunked clips (out of order, duplicate, oversize, bad header) |
| `test_api.py` | auth on every endpoint, event filters/pagination, clip upload (idempotency, conflict, ownership, size, content-type, sha256), feedback publish (QoS 1, not retained), OTA publish on per-kind topics (retained, a late subscriber sees all three kinds, clear one / clear all), device list and status log surfacing faults |
| `test_storage.py` | the same contract test run against `LocalFSStore` and `S3Store` (moto), traversal rejection, API over S3 |
| `test_simulator.py` | simulated events all validate, realistic action mix, time order and battery monotone, end-to-end simulator → bus → ingest → API with HTTP clips and with MQTT clips, QoS 1 dedupe, simulated devices receive per-kind OTA notices published while offline, simulated status JSON with faults (no rejects) |
| `test_real_broker.py` | full flow over a **real** MQTT broker on TCP (amqtt in-process, or `mosquitto` if installed; skipped if neither starts): QoS 1 event ingest, status JSON incl. a `fault`, feedback delivery to the device, three per-kind retained OTA notices to a late subscriber, LWT `offline` on ungraceful disconnect |
| `test_main.py` | env-driven wiring; in-place migration of a pre-change SQLite file |
