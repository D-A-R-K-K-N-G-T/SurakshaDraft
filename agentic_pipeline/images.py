"""
Image/PDF loading + message content-block construction for the vision node.

`file_ref` is a URI produced by the gateway (Phase 3):
  * fs://<abspath>  — content-addressed local blob (dev, shared filesystem)
  * s3://<bucket>/<key>  — object store (prod)
  * file://<path> / bare path — legacy, still accepted

resolve_file_ref returns raw bytes; a small bounded cache avoids re-reading the
same blob within a single graph run (e.g. the policy PDF is loaded by both
document_triage_node and policy_extract_node).
"""
from __future__ import annotations

import base64
import mimetypes
from collections import OrderedDict
from pathlib import Path

from agentic_pipeline.blobs import local_path_from_ref

# Bounded LRU keyed by file_ref. Small on purpose — this is a within-run hot
# cache, not a store. Values are raw bytes; the cap bounds memory.
_CACHE_MAX = 32
_cache: "OrderedDict[str, bytes]" = OrderedDict()


def _cache_get(ref: str) -> bytes | None:
    data = _cache.get(ref)
    if data is not None:
        _cache.move_to_end(ref)
    return data


def _cache_put(ref: str, data: bytes) -> None:
    _cache[ref] = data
    _cache.move_to_end(ref)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


def _fetch_s3(ref: str) -> bytes:
    # s3://bucket/key -> GetObject. boto3 is imported lazily so dev/tests never
    # need it installed.
    try:
        import boto3  # type: ignore
    except ImportError as exc:  # pragma: no cover - prod-only path
        raise NotImplementedError(
            "s3:// file_ref requires boto3; install it or use fs:// in dev."
        ) from exc
    without_scheme = ref[len("s3://"):]
    bucket, _, key = without_scheme.partition("/")
    if not bucket or not key:
        raise ValueError(f"Malformed s3 URI: {ref!r}")
    obj = boto3.client("s3").get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()


def resolve_file_ref(ref: str) -> bytes:
    cached = _cache_get(ref)
    if cached is not None:
        return cached

    if ref.startswith("s3://"):
        data = _fetch_s3(ref)
    elif ref.startswith("https://") or ref.startswith("http://"):
        # TODO: Implement HTTPS fetch (presigned URLs). Not needed in dev.
        raise NotImplementedError("HTTPS fetch not implemented")
    else:
        # fs://, file://, or a bare local path.
        data = Path(local_path_from_ref(ref)).read_bytes()

    _cache_put(ref, data)
    return data


def load_image_as_data_url(file_ref: str) -> str:
    """Load a file into a base64 data URL.

    Despite the name this is not image-only: the mime type is derived from the
    extension, so PDFs (policy schedules, invoices) become
    `data:application/pdf;base64,...`, which Gemini accepts through the same
    content block as images (verified against the live API).
    """
    # Guess mime from the path portion of the URI (content-addressed blobs keep
    # their original extension, so this still works for fs:// refs).
    name = Path(local_path_from_ref(file_ref)).name
    mime_type, _ = mimetypes.guess_type(name)
    mime_type = mime_type or "image/jpeg"

    b_data = resolve_file_ref(file_ref)
    b64 = base64.b64encode(b_data).decode("utf-8")
    return f"data:{mime_type};base64,{b64}"


def build_image_block(data_url: str) -> dict:
    """
    The one place to edit if a provider/library version rejects this shape.
    This nested-dict form ({"image_url": {"url": ...}}) is the
    widest-compatibility option going by current docs for both
    langchain_ibm and langchain_google_genai — but some
    langchain-google-genai versions accept a bare string for `image_url`
    instead. Try that first if Gemini errors on this.
    """
    return {"type": "image_url", "image_url": {"url": data_url}}
