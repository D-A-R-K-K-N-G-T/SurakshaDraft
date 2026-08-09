"""Phase 3 pure-function tests: content-addressed helpers + image resolver.

No DB required.
"""
from __future__ import annotations

import importlib.util

import pytest

from agentic_pipeline import blobs, images


@pytest.fixture(autouse=True)
def _clear_image_cache():
    images._cache.clear()
    yield
    images._cache.clear()


def test_shard_relpath_and_uri_roundtrip(tmp_path):
    sha = "ab" + "c" * 62
    assert blobs.shard_relpath(sha, ".pdf") == f"ab/{sha}.pdf"
    p = tmp_path / "x" / "y.jpg"
    uri = blobs.fs_uri(p)
    assert uri.startswith("fs://") and "/" in uri and "\\" not in uri
    # local_path_from_ref strips the scheme back to a usable path
    assert blobs.local_path_from_ref(uri).endswith("x/y.jpg")


def test_store_bytes_dedup(tmp_path):
    data = b"hello-blob"
    sha1, path1, uri1, existed1 = blobs.store_bytes(tmp_path, data, "a.txt")
    assert existed1 is False and path1.exists()
    assert sha1 == blobs.sha256_bytes(data)
    # Same content again -> already existed, same destination, not rewritten.
    sha2, path2, uri2, existed2 = blobs.store_bytes(tmp_path, data, "a.txt")
    assert existed2 is True and path2 == path1 and uri2 == uri1


def test_resolve_fs_uri(tmp_path):
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x00\x01\x02")
    assert images.resolve_file_ref(blobs.fs_uri(f)) == b"\x00\x01\x02"


def test_resolve_bare_path_and_file_uri(tmp_path):
    f = tmp_path / "doc.dat"
    f.write_bytes(b"payload")
    assert images.resolve_file_ref(str(f)) == b"payload"
    assert images.resolve_file_ref("file://" + f.as_posix()) == b"payload"


def test_resolve_cache_survives_file_deletion(tmp_path):
    f = tmp_path / "cached.bin"
    f.write_bytes(b"once")
    ref = blobs.fs_uri(f)
    assert images.resolve_file_ref(ref) == b"once"  # caches
    f.unlink()
    assert images.resolve_file_ref(ref) == b"once"  # served from cache, no error


def test_load_pdf_data_url_mime(tmp_path):
    f = tmp_path / "policy.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    url = images.load_image_as_data_url(blobs.fs_uri(f))
    assert url.startswith("data:application/pdf;base64,")


@pytest.mark.skipif(
    importlib.util.find_spec("boto3") is not None,
    reason="boto3 installed; s3 path would attempt a real fetch",
)
def test_s3_without_boto3_raises():
    with pytest.raises(NotImplementedError):
        images.resolve_file_ref("s3://bucket/key")
