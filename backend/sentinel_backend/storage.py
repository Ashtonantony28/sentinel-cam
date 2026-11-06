"""Clip storage behind one interface: local filesystem or S3/MinIO."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Optional, Protocol

from .schemas import valid_clip_id


class ClipNotFound(KeyError):
    pass


class ClipStore(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...

    def get(self, key: str) -> bytes: ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None: ...


def clip_key(dev: str, clip_id: str) -> str:
    """Object key for a clip. Keys are namespaced per device so devices cannot collide."""
    if not valid_clip_id(clip_id):
        raise ValueError("bad clip_id")
    return f"clips/{dev}/{clip_id}"


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class LocalFSStore:
    def __init__(self, root: str | os.PathLike) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        p = (self.root / key).resolve()
        if self.root not in p.parents:
            raise ValueError("key escapes storage root")
        return p

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # Atomic: write to a temp file in the same directory, then rename.
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".up-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, p)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.is_file():
            raise ClipNotFound(key)
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        p = self._path(key)
        if p.is_file():
            p.unlink()


class S3Store:
    """S3-compatible store (AWS S3 or MinIO via `endpoint_url`)."""

    def __init__(
        self,
        bucket: str,
        endpoint_url: Optional[str] = None,
        region: str = "us-east-1",
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        client=None,
        create_bucket: bool = True,
    ) -> None:
        if client is None:
            import boto3

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        self.s3 = client
        self.bucket = bucket
        if create_bucket:
            try:
                self.s3.head_bucket(Bucket=bucket)
            except Exception:
                self.s3.create_bucket(Bucket=bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def get(self, key: str) -> bytes:
        try:
            return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except self.s3.exceptions.NoSuchKey as e:
            raise ClipNotFound(key) from e

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)
