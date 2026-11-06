"""FastAPI application: REST API + static dashboard."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .auth import Authenticator, device_dep, user_dep
from .bus import Bus
from .db import Database
from .ingest import Ingest
from .schemas import Action, FeedbackIn, FeedbackMsg, OtaIn, OtaNotice, valid_clip_id, valid_dev
from .storage import ClipNotFound, ClipStore, clip_key, sha256_hex

STATIC = Path(__file__).parent / "static"


@dataclass
class Settings:
    api_token_hashes: list[str]
    device_secret: bytes
    max_clip_bytes: int = 20 * 1024 * 1024
    clip_content_types: tuple[str, ...] = ("video/mp4", "video/h264", "image/jpeg", "application/octet-stream")
    extra: dict = field(default_factory=dict)


def _dev_or_404(dev: str) -> str:
    if not valid_dev(dev):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "unknown device")
    return dev


def create_app(settings: Settings, db: Database, store: ClipStore, bus: Bus, ingest: Optional[Ingest] = None) -> FastAPI:
    app = FastAPI(title="Sentinel backend", version="0.1.0")
    app.state.auth = Authenticator(settings.api_token_hashes, settings.device_secret)
    app.state.db, app.state.store, app.state.bus = db, store, bus
    if ingest is None:
        ingest = Ingest(db, store, max_clip_bytes=settings.max_clip_bytes)
        ingest.attach(bus)
    app.state.ingest = ingest
    user = [Depends(user_dep)]

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "events": db.count_events()}

    @app.get("/", include_in_schema=False)
    def dashboard() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    # ------------------------------------------------------------ devices / events
    @app.get("/api/devices", dependencies=user)
    def list_devices() -> list[dict]:
        out = db.devices()
        for d in out:
            d["ota"] = db.ota(d["dev"])
        return out

    @app.get("/api/events", dependencies=user)
    def list_events(
        dev: Optional[str] = None,
        action: Optional[Action] = None,
        since: Optional[int] = Query(default=None, ge=0, description="unix ms, inclusive"),
        until: Optional[int] = Query(default=None, ge=0, description="unix ms, exclusive"),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
    ) -> list[dict]:
        if dev is not None and not valid_dev(dev):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad dev")
        return db.events(dev=dev, action=action, since=since, until=until, limit=limit, offset=offset)

    @app.get("/api/events/{event_id}", dependencies=user)
    def get_event(event_id: int) -> dict:
        e = db.event(event_id)
        if not e:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such event")
        return e

    @app.get("/api/rejects", dependencies=user)
    def list_rejects(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict]:
        return db.rejects(limit)

    @app.get("/api/stats", dependencies=user)
    def stats() -> dict:
        s = app.state.ingest.stats
        return {"events": s.events, "duplicates": s.duplicates, "rejected": s.rejected,
                "status": s.status, "clip_chunks": s.clip_chunks}

    # ------------------------------------------------------------ clips
    @app.put("/api/clips/{clip_id}", status_code=status.HTTP_201_CREATED)
    async def put_clip(clip_id: str, request: Request, response: Response, dev: str = Depends(device_dep)) -> dict:
        if not valid_clip_id(clip_id):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "bad clip_id")
        ctype = request.headers.get("content-type", "application/octet-stream").split(";")[0].strip()
        if ctype not in settings.clip_content_types:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, f"unsupported content-type {ctype}")
        clen = request.headers.get("content-length")
        if clen is not None and clen.isdigit() and int(clen) > settings.max_clip_bytes:
            raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "clip too large")
        # Ownership: if an event already references this clip id, it must be the same device.
        owner = db.clip_owner_from_events(clip_id)
        if owner is not None and owner != dev:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "clip belongs to another device")
        buf = bytearray()
        async for chunk in request.stream():
            buf += chunk
            if len(buf) > settings.max_clip_bytes:
                raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "clip too large")
        if not buf:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "empty clip")
        data = bytes(buf)
        digest = sha256_hex(data)
        claimed = request.headers.get("x-content-sha256")
        if claimed and claimed.lower() != digest:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "sha256 mismatch")
        existing = db.clip(clip_id)
        if existing:
            if existing["dev"] != dev:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "clip belongs to another device")
            if existing["sha256"] != digest:
                raise HTTPException(status.HTTP_409_CONFLICT, "clip exists with different content")
            response.status_code = status.HTTP_200_OK  # idempotent retry
            return {"clip_id": clip_id, "size": existing["size"], "sha256": digest}
        store.put(clip_key(dev, clip_id), data, ctype)
        db.insert_clip(clip_id, dev, len(data), digest, ctype)
        return {"clip_id": clip_id, "size": len(data), "sha256": digest}

    @app.get("/api/clips/{clip_id}", dependencies=user)
    def get_clip(clip_id: str) -> Response:
        if not valid_clip_id(clip_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such clip")
        meta = db.clip(clip_id)
        if not meta:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no such clip")
        try:
            data = store.get(clip_key(meta["dev"], clip_id))
        except ClipNotFound:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "clip missing from store")
        return Response(content=data, media_type=meta["content_type"],
                        headers={"X-Content-SHA256": meta["sha256"]})

    # ------------------------------------------------------------ feedback
    @app.post("/api/devices/{dev}/feedback", dependencies=user, status_code=status.HTTP_201_CREATED)
    def post_feedback(dev: str, body: FeedbackIn) -> dict:
        _dev_or_404(dev)
        if db.event_by_ts(dev, body.event_ts) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "no event with that event_ts for this device")
        if body.label == "known" and body.person_id is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "label 'known' requires person_id")
        if body.label != "known" and body.person_id is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "person_id only allowed with label 'known'")
        msg = FeedbackMsg(event_ts=body.event_ts, label=body.label, person_id=body.person_id)
        payload = msg.model_dump_json().encode()
        bus.publish(f"sentinel/{dev}/feedback", payload, qos=1, retain=False)
        fid = db.insert_feedback(dev, body.event_ts, body.label, body.person_id)
        return {"id": fid, "published": json.loads(payload)}

    @app.get("/api/devices/{dev}/feedback", dependencies=user)
    def list_feedback(dev: str) -> list[dict]:
        _dev_or_404(dev)
        return db.feedback(dev)

    # ------------------------------------------------------------ OTA notices
    @app.post("/api/devices/{dev}/ota", dependencies=user, status_code=status.HTTP_201_CREATED)
    def post_ota(dev: str, body: OtaIn) -> dict:
        _dev_or_404(dev)
        try:
            notice = OtaNotice(**body.model_dump())
        except ValidationError as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                                [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()])
        payload = notice.model_dump_json().encode()
        bus.publish(f"sentinel/{dev}/ota", payload, qos=1, retain=True)
        db.upsert_ota(dev, notice.kind, notice.version, notice.url, notice.size, notice.sha256)
        return {"published": json.loads(payload), "retained": True}

    @app.delete("/api/devices/{dev}/ota", dependencies=user, status_code=status.HTTP_204_NO_CONTENT)
    def clear_ota(dev: str) -> Response:
        _dev_or_404(dev)
        bus.publish(f"sentinel/{dev}/ota", b"", qos=1, retain=True)  # empty retained = clear
        db.delete_ota(dev)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
