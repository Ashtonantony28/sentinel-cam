"""MQTT ingest: validates device messages per INTERFACES.md §3 and stores them.

Subscriptions: `sentinel/+/event`, `sentinel/+/status`, `sentinel/+/clip/+`.
Invalid messages are never stored as events; they go to the `rejects` table so a
misbehaving firmware build is visible on the dashboard rather than silently dropped.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from pydantic import ValidationError

from .bus import Bus
from .db import Database
from .schemas import ClipChunkHeader, EventMsg, StatusMsg, valid_clip_id, valid_dev
from .storage import ClipStore, clip_key, sha256_hex

log = logging.getLogger(__name__)

MAX_EVENT_BYTES = 8 * 1024


@dataclass
class IngestStats:
    events: int = 0
    duplicates: int = 0
    rejected: int = 0
    status: int = 0
    faults: int = 0
    stale_status: int = 0
    clip_chunks: int = 0


@dataclass
class _ChunkBuf:
    dev: str
    parts: dict[int, bytes] = field(default_factory=dict)
    last_seq: Optional[int] = None
    size: int = 0


class Ingest:
    def __init__(
        self,
        db: Database,
        store: ClipStore,
        max_clip_bytes: int = 20 * 1024 * 1024,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.db = db
        self.store = store
        self.max_clip_bytes = max_clip_bytes
        self.on_event = on_event
        self.stats = IngestStats()
        self._chunks: dict[str, _ChunkBuf] = {}
        self._lock = threading.Lock()

    def attach(self, bus: Bus) -> None:
        bus.subscribe("sentinel/+/event", self.handle, qos=1)
        bus.subscribe("sentinel/+/status", self.handle, qos=1)
        bus.subscribe("sentinel/+/clip/+", self.handle, qos=1)

    def _reject(self, topic: str, reason: str, payload: bytes) -> None:
        self.stats.rejected += 1
        log.warning("reject %s: %s", topic, reason)
        self.db.insert_reject(topic, reason, payload)

    def handle(self, topic: str, payload: bytes) -> None:
        parts = topic.split("/")
        if len(parts) < 3 or parts[0] != "sentinel" or not valid_dev(parts[1]):
            return self._reject(topic, "bad topic", payload)
        dev, kind = parts[1], parts[2]
        if kind == "event" and len(parts) == 3:
            self._event(topic, dev, payload)
        elif kind == "status" and len(parts) == 3:
            self._status(topic, dev, payload)
        elif kind == "clip" and len(parts) == 4:
            self._clip_chunk(topic, dev, parts[3], payload)
        else:
            self._reject(topic, "unknown topic", payload)

    # ---------------------------------------------------------------- event
    def _event(self, topic: str, dev: str, payload: bytes) -> None:
        if len(payload) > MAX_EVENT_BYTES:
            return self._reject(topic, "payload too large", payload)
        try:
            raw = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            return self._reject(topic, f"invalid JSON: {e}", payload)
        try:
            ev = EventMsg.model_validate(raw)
        except ValidationError as e:
            return self._reject(topic, "schema: " + "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()), payload)
        if ev.dev != dev:
            # A device may only publish its own events (the broker ACL also enforces this).
            return self._reject(topic, f"dev mismatch: topic {dev} payload {ev.dev}", payload)
        row_id = self.db.insert_event(ev)
        if row_id is None:
            self.stats.duplicates += 1
            return
        self.stats.events += 1
        if self.on_event:
            self.on_event(self.db.event(row_id))

    # ---------------------------------------------------------------- status
    def _status(self, topic: str, dev: str, payload: bytes) -> None:
        """INTERFACES §3: retained JSON `{"state","reason","ts","battery_mv"}`; the broker LWT
        may be the plain string `offline`. `fault` statuses (brainstem refused or lost the N6)
        are stored with their reason and surfaced on the device list and dashboard."""
        if payload == b"":
            return  # retained-clear
        if payload.strip() == b"offline":
            self.stats.status += 1
            self.db.record_status(dev, "offline", None, None, None, lwt=True)
            return
        if len(payload) > MAX_EVENT_BYTES:
            return self._reject(topic, "payload too large", payload)
        try:
            st = StatusMsg.model_validate_json(payload)
        except ValidationError as e:
            return self._reject(topic, "bad status payload: " + "; ".join(
                f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()), payload)
        self.stats.status += 1
        if not self.db.record_status(dev, st.state, st.reason, st.ts, st.battery_mv):
            self.stats.stale_status += 1
        elif st.state == "fault":
            self.stats.faults += 1
            log.warning("device %s fault: %s (battery %d mV)", dev, st.reason, st.battery_mv)

    # ---------------------------------------------------------------- MQTT clip fallback
    def _clip_chunk(self, topic: str, dev: str, clip_id: str, payload: bytes) -> None:
        """Chunk = header JSON, a newline, then binary. Reassembled in memory, stored on `last`."""
        if not valid_clip_id(clip_id):
            return self._reject(topic, "bad clip_id", payload)
        nl = payload.find(b"\n")
        if nl < 0:
            return self._reject(topic, "chunk missing header", payload)
        try:
            hdr = ClipChunkHeader.model_validate_json(payload[:nl])
        except ValidationError as e:
            return self._reject(topic, f"chunk header: {e.errors()[0]['msg']}", payload)
        body = payload[nl + 1:]
        self.stats.clip_chunks += 1
        with self._lock:
            buf = self._chunks.setdefault(clip_id, _ChunkBuf(dev=dev))
            if buf.dev != dev:
                return self._reject(topic, "clip_id owned by another device", payload)
            if hdr.seq not in buf.parts:
                buf.parts[hdr.seq] = body
                buf.size += len(body)
            if buf.size > self.max_clip_bytes:
                del self._chunks[clip_id]
                return self._reject(topic, "clip too large", b"")
            if hdr.last:
                buf.last_seq = hdr.seq
            if buf.last_seq is None or len(buf.parts) != buf.last_seq + 1:
                return
            del self._chunks[clip_id]
        data = b"".join(buf.parts[i] for i in range(buf.last_seq + 1))
        if self.db.clip(clip_id):
            return
        self.store.put(clip_key(dev, clip_id), data, "video/mp4")
        self.db.insert_clip(clip_id, dev, len(data), sha256_hex(data), "video/mp4")
