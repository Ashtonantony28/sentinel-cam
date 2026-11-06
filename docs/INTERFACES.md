# Shared contracts

All multi-byte integers are little-endian. Versions are semver `major.minor.patch` packed as `u32 = major<<16 | minor<<8 | patch`.

## 1. Face embedding and gallery
- Embedding: 128 dims. Float form is L2-normalized fp32. Device form is int8, `q = round(clamp(x * 127, -127, 127))` (scale 1/127, zero-point 0), then cosine via int32 dot product of int8 vectors divided by the product of their int8 norms.
- Gallery limits: ≤ 16 people, ≤ 20 prototypes per person.
- Match rule: score(person) = max cosine over that person's prototypes; match if best score ≥ that person's threshold (default 0.55, per-person override) AND best − second-best person ≥ 0.05 margin. Otherwise "unknown".
- Gallery flash blob: header `{ magic "SGAL", u16 version=1, u16 n_people }`, then per person `{ u16 id, u8 n_protos, u8 reserved, i16 threshold_q15, char name[24], int8 protos[n_protos][128] }`, then CRC32 (IEEE) over everything before it.

## 2. Event pipeline (device side)
Wake sources: `PIR`, `RADAR`, `BUTTON`, `TIMER`.
Stages: person detect → face detect → embed → match → policy.
Policy outputs (enum `action`): `NONE` (no person), `LOG_KNOWN`, `NOTIFY_UNKNOWN_WITH_CLIP`, `RING` (button, always notifies, clip if person seen).
Person present = detector confidence ≥ 0.5 in ≥ 2 of the first 4 frames.

## 3. MQTT (device ↔ backend)
Topics, `{dev}` = device id (12 hex chars from MAC):
- `sentinel/{dev}/event` (device → backend, QoS 1) JSON:
  `{"v":1,"dev":str,"ts":int_unix_ms,"wake":"PIR|RADAR|BUTTON|TIMER","action":"NONE|LOG_KNOWN|NOTIFY_UNKNOWN_WITH_CLIP|RING","person_conf":float,"faces":[{"id":int|null,"score":float}],"clip_id":str|null,"battery_mv":int,"soc_pct":int,"fw":str,"model":str,"energy_mj":int}`
- `sentinel/{dev}/clip/{clip_id}` (device → backend): chunks, header JSON `{"seq":int,"last":bool}` then binary; or HTTP PUT to `/api/clips/{clip_id}` (preferred).
- `sentinel/{dev}/feedback` (backend → device, QoS 1): `{"v":1,"event_ts":int,"label":"false_alarm|person|known","person_id":int|null}`
- `sentinel/{dev}/ota` (backend → device, retained): `{"v":1,"kind":"fw_n6|fw_c6|model","version":str,"url":str,"size":int,"sha256":hex}`
- `sentinel/{dev}/status` (device → backend, retained, LWT "offline").

## 4. OTA image + manifest
An image is a 256-byte manifest header followed immediately by the payload:
```
offset size field
0      4    magic "SOTA"
4      2    manifest_version = 1
6      1    kind (1=fw_n6, 2=fw_c6, 3=model)
7      1    reserved = 0
8      4    version (packed semver)
12     4    security_counter (monotonic, anti-rollback)
16     4    payload_size
20     32   payload_sha256
52     4    load_addr (fw) or 0 (model)
56     4    flags (bit0 = requires_reboot)
60     32   model_op_set_hash (model: hash of required op set; fw: hash of supported op set)
92     100  reserved = 0
192    64   ECDSA P-256 signature (r||s, 32 bytes each, big-endian) over SHA-256 of bytes [0,192) of the manifest
```
The signature covers the manifest; the manifest covers the payload via sha256.

Slot states: `EMPTY`, `PENDING` (written + verified, not yet booted), `TRIAL` (booted once, not confirmed), `CONFIRMED`, `BAD`.
Boot rule: boot PENDING → mark TRIAL; if a TRIAL slot is found at boot (meaning it didn't confirm), mark BAD and boot the other CONFIRMED slot. App confirms within 60 s of boot after health checks. Reject any image whose security_counter < stored counter; on confirm, stored counter = max(stored, image counter).

## 5. False-alert head (on-device learning + FL)
- Input: 64-d feature vector (global-average-pooled person-detector neck features, projected), fp32.
- Model: logistic regression, `w[64] + b`, output P(real person event).
- FL exchanges only `Δw, Δb` (65 floats) per round. DP: clip L2 of the update to C=1.0, add Gaussian noise σ·C at aggregation.
- Serialization: 65 little-endian fp32, `w` then `b`.

## 6. Energy model constants (until measured on hardware)
Used by `ml/` (energy per inference) and `hardware/` (battery life). Source of truth: `hardware/power_model/constants.yaml`.
