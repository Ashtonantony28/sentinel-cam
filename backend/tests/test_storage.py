"""Both ClipStore implementations must satisfy the same contract."""
import boto3
import pytest
from moto import mock_aws

from sentinel_backend.api import Settings, create_app
from sentinel_backend.auth import hash_token
from sentinel_backend.bus import InMemoryBus
from sentinel_backend.db import Database
from sentinel_backend.storage import ClipNotFound, LocalFSStore, S3Store, clip_key
from conftest import DEV, SECRET, TOKEN, dev_auth


@pytest.fixture(params=["local", "s3"])
def store(request, tmp_path):
    if request.param == "local":
        yield LocalFSStore(tmp_path / "clips")
    else:
        with mock_aws():
            yield S3Store("sentinel-clips", client=boto3.client("s3", region_name="us-east-1"))


def test_put_get_exists_delete(store):
    k = clip_key(DEV, "c1")
    assert not store.exists(k)
    store.put(k, b"hello", "video/mp4")
    assert store.exists(k) and store.get(k) == b"hello"
    store.put(k, b"world")
    assert store.get(k) == b"world"
    store.delete(k)
    assert not store.exists(k)
    with pytest.raises(ClipNotFound):
        store.get(k)


def test_binary_and_large(store):
    data = bytes(range(256)) * 4096  # 1 MiB
    store.put("clips/x/big", data)
    assert store.get("clips/x/big") == data


def test_clip_key_rejects_traversal():
    for bad in ["..", "../x", "a/b", "", "x" * 200]:
        with pytest.raises(ValueError):
            clip_key(DEV, bad)


def test_local_store_containment(tmp_path):
    s = LocalFSStore(tmp_path / "root")
    with pytest.raises(ValueError):
        s.put("../escape", b"x")
    assert not (tmp_path / "escape").exists()


def test_s3_store_created_bucket_and_content_type():
    with mock_aws():
        c = boto3.client("s3", region_name="us-east-1")
        s = S3Store("new-bucket", client=c)
        s.put("clips/a/b", b"x", "video/mp4")
        head = c.head_object(Bucket="new-bucket", Key="clips/a/b")
        assert head["ContentType"] == "video/mp4"


def test_api_with_s3_backend(tmp_path):
    """The API is storage-agnostic: same upload/download flow over S3."""
    from fastapi.testclient import TestClient

    with mock_aws():
        store = S3Store("clips", client=boto3.client("s3", region_name="us-east-1"))
        app = create_app(Settings([hash_token(TOKEN)], SECRET), Database(), store, InMemoryBus())
        c = TestClient(app)
        r = c.put("/api/clips/s3clip", content=b"abc" * 100, headers={**dev_auth(), "Content-Type": "video/mp4"})
        assert r.status_code == 201
        assert store.get(clip_key(DEV, "s3clip")) == b"abc" * 100
        g = c.get("/api/clips/s3clip", headers={"Authorization": f"Bearer {TOKEN}"})
        assert g.content == b"abc" * 100
