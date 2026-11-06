"""SQLite store for devices, events, clips, feedback, OTA notices and rejected messages."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any, Optional

from .schemas import EventMsg

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS devices (
    dev TEXT PRIMARY KEY,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL,
    status TEXT,
    fw TEXT, model TEXT,
    battery_mv INTEGER, soc_pct INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dev TEXT NOT NULL,
    ts INTEGER NOT NULL,
    wake TEXT NOT NULL,
    action TEXT NOT NULL,
    person_conf REAL NOT NULL,
    faces TEXT NOT NULL,
    clip_id TEXT,
    battery_mv INTEGER NOT NULL,
    soc_pct INTEGER NOT NULL,
    fw TEXT NOT NULL,
    model TEXT NOT NULL,
    energy_mj INTEGER NOT NULL,
    received_ms INTEGER NOT NULL,
    UNIQUE(dev, ts)
);
CREATE INDEX IF NOT EXISTS events_ts ON events(ts);
CREATE TABLE IF NOT EXISTS clips (
    clip_id TEXT PRIMARY KEY,
    dev TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    content_type TEXT NOT NULL,
    uploaded_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dev TEXT NOT NULL,
    event_ts INTEGER NOT NULL,
    label TEXT NOT NULL,
    person_id INTEGER,
    created_ms INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ota (
    dev TEXT NOT NULL,
    kind TEXT NOT NULL,
    version TEXT NOT NULL,
    url TEXT NOT NULL,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    published_ms INTEGER NOT NULL,
    PRIMARY KEY (dev, kind)
);
CREATE TABLE IF NOT EXISTS rejects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    reason TEXT NOT NULL,
    payload_head TEXT,
    received_ms INTEGER NOT NULL
);
"""


def now_ms() -> int:
    return int(time.time() * 1000)


class Database:
    def __init__(self, path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)

    def _x(self, sql: str, args: tuple = ()) -> sqlite3.Cursor:
        with self.lock:
            return self.conn.execute(sql, args)

    # ---------------------------------------------------------------- devices
    def touch_device(self, dev: str, **fields: Any) -> None:
        t = now_ms()
        with self.lock:
            self._x(
                "INSERT INTO devices(dev, first_seen_ms, last_seen_ms) VALUES(?,?,?) "
                "ON CONFLICT(dev) DO UPDATE SET last_seen_ms=excluded.last_seen_ms",
                (dev, t, t),
            )
            for k, v in fields.items():
                if k not in ("status", "fw", "model", "battery_mv", "soc_pct"):
                    raise KeyError(k)
                self._x(f"UPDATE devices SET {k}=? WHERE dev=?", (v, dev))

    def devices(self) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM devices ORDER BY dev")]

    def device(self, dev: str) -> Optional[dict]:
        r = self._x("SELECT * FROM devices WHERE dev=?", (dev,)).fetchone()
        return dict(r) if r else None

    # ---------------------------------------------------------------- events
    def insert_event(self, e: EventMsg) -> Optional[int]:
        """Insert; returns row id, or None if (dev, ts) was already stored (QoS 1 redelivery)."""
        with self.lock:
            cur = self._x(
                "INSERT OR IGNORE INTO events(dev, ts, wake, action, person_conf, faces, clip_id, battery_mv,"
                " soc_pct, fw, model, energy_mj, received_ms) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    e.dev, e.ts, e.wake, e.action, e.person_conf,
                    json.dumps([f.model_dump() for f in e.faces]),
                    e.clip_id, e.battery_mv, e.soc_pct, e.fw, e.model, e.energy_mj, now_ms(),
                ),
            )
            if cur.rowcount == 0:
                return None
            self.touch_device(e.dev, fw=e.fw, model=e.model, battery_mv=e.battery_mv, soc_pct=e.soc_pct)
            return cur.lastrowid

    @staticmethod
    def _event_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["faces"] = json.loads(d["faces"])
        return d

    def events(
        self,
        dev: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[int] = None,
        until: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        where, args = [], []
        if dev:
            where.append("dev=?"); args.append(dev)
        if action:
            where.append("action=?"); args.append(action)
        if since is not None:
            where.append("ts>=?"); args.append(since)
        if until is not None:
            where.append("ts<?"); args.append(until)
        sql = "SELECT * FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY ts DESC, id DESC LIMIT ? OFFSET ?"
        args += [limit, offset]
        return [self._event_row(r) for r in self._x(sql, tuple(args))]

    def event(self, event_id: int) -> Optional[dict]:
        r = self._x("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return self._event_row(r) if r else None

    def event_by_ts(self, dev: str, ts: int) -> Optional[dict]:
        r = self._x("SELECT * FROM events WHERE dev=? AND ts=?", (dev, ts)).fetchone()
        return self._event_row(r) if r else None

    def clip_owner_from_events(self, clip_id: str) -> Optional[str]:
        r = self._x("SELECT dev FROM events WHERE clip_id=? LIMIT 1", (clip_id,)).fetchone()
        return r["dev"] if r else None

    def count_events(self) -> int:
        return self._x("SELECT COUNT(*) FROM events").fetchone()[0]

    # ---------------------------------------------------------------- clips
    def clip(self, clip_id: str) -> Optional[dict]:
        r = self._x("SELECT * FROM clips WHERE clip_id=?", (clip_id,)).fetchone()
        return dict(r) if r else None

    def insert_clip(self, clip_id: str, dev: str, size: int, sha256: str, content_type: str) -> None:
        self._x(
            "INSERT INTO clips(clip_id, dev, size, sha256, content_type, uploaded_ms) VALUES(?,?,?,?,?,?)",
            (clip_id, dev, size, sha256, content_type, now_ms()),
        )

    # ---------------------------------------------------------------- feedback / ota / rejects
    def insert_feedback(self, dev: str, event_ts: int, label: str, person_id: Optional[int]) -> int:
        return self._x(
            "INSERT INTO feedback(dev, event_ts, label, person_id, created_ms) VALUES(?,?,?,?,?)",
            (dev, event_ts, label, person_id, now_ms()),
        ).lastrowid

    def feedback(self, dev: Optional[str] = None) -> list[dict]:
        if dev:
            rows = self._x("SELECT * FROM feedback WHERE dev=? ORDER BY id", (dev,))
        else:
            rows = self._x("SELECT * FROM feedback ORDER BY id")
        return [dict(r) for r in rows]

    def upsert_ota(self, dev: str, kind: str, version: str, url: str, size: int, sha256: str) -> None:
        self._x(
            "INSERT INTO ota(dev, kind, version, url, size, sha256, published_ms) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(dev, kind) DO UPDATE SET version=excluded.version, url=excluded.url,"
            " size=excluded.size, sha256=excluded.sha256, published_ms=excluded.published_ms",
            (dev, kind, version, url, size, sha256, now_ms()),
        )

    def delete_ota(self, dev: str) -> None:
        self._x("DELETE FROM ota WHERE dev=?", (dev,))

    def ota(self, dev: str) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM ota WHERE dev=? ORDER BY kind", (dev,))]

    def insert_reject(self, topic: str, reason: str, payload: bytes) -> None:
        self._x(
            "INSERT INTO rejects(topic, reason, payload_head, received_ms) VALUES(?,?,?,?)",
            (topic, reason[:2000], payload[:256].decode("utf-8", "replace"), now_ms()),
        )

    def rejects(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._x("SELECT * FROM rejects ORDER BY id DESC LIMIT ?", (limit,))]
