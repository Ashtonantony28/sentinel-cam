"""REST authentication.

Two principals:

* **Users** (dashboard, scripts): `Authorization: Bearer <api token>`. Tokens are
  configured as SHA-256 hashes (`SENTINEL_API_TOKEN_SHA256`, comma separated) so the
  server never stores a usable token; comparison is constant time.
* **Devices** (clip upload only): `Authorization: Device <dev>:<tag>` where
  `tag = HMAC-SHA256(device_secret, dev)` hex. The device receives its tag at
  provisioning, alongside its MQTT client certificate. In production the clip PUT is
  also expected to arrive over mTLS at the reverse proxy; the HMAC tag lets the API
  bind the upload to a device id without parsing client certs itself.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Iterable, Optional

from fastapi import Header, HTTPException, Request, status

from .schemas import valid_dev


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def device_tag(secret: bytes, dev: str) -> str:
    return hmac.new(secret, dev.lower().encode(), hashlib.sha256).hexdigest()


class Authenticator:
    def __init__(self, token_hashes: Iterable[str], device_secret: bytes) -> None:
        self.token_hashes = [h.lower() for h in token_hashes]
        self.device_secret = device_secret

    def check_user(self, authorization: Optional[str]) -> None:
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing bearer token",
                                headers={"WWW-Authenticate": "Bearer"})
        h = hash_token(authorization[len("Bearer "):].strip())
        ok = False
        for th in self.token_hashes:  # no early exit: constant work per configured token
            ok |= hmac.compare_digest(h, th)
        if not ok:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid token",
                                headers={"WWW-Authenticate": "Bearer"})

    def check_device(self, authorization: Optional[str]) -> str:
        if not authorization or not authorization.startswith("Device "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "missing device credentials")
        cred = authorization[len("Device "):].strip()
        dev, _, tag = cred.partition(":")
        if not valid_dev(dev) or not tag:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "malformed device credentials")
        if not hmac.compare_digest(device_tag(self.device_secret, dev), tag.lower()):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid device credentials")
        return dev


def user_dep(request: Request, authorization: Optional[str] = Header(default=None)) -> None:
    request.app.state.auth.check_user(authorization)


def device_dep(request: Request, authorization: Optional[str] = Header(default=None)) -> str:
    return request.app.state.auth.check_device(authorization)
