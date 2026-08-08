"""
Local-disk image loading + message content-block construction for the
vision node. In production `file_ref` will point at an S3 key post-sync
(see EvidenceRecord) — swap load_image_as_data_url's body for a GetObject
call and nothing upstream (vision_node, prompts) needs to change.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path


def resolve_file_ref(ref: str) -> bytes:
    if ref.startswith("s3://"):
        # TODO: Implement S3 fetch
        raise NotImplementedError("S3 fetch not implemented")
    elif ref.startswith("https://") or ref.startswith("http://"):
        # TODO: Implement HTTPS fetch
        raise NotImplementedError("HTTPS fetch not implemented")
    else:
        # Default to local file path
        # Strip file:// prefix if present
        local_path = ref.replace("file://", "", 1)
        path = Path(local_path)
        return path.read_bytes()


def load_image_as_data_url(file_ref: str) -> str:
    local_path = file_ref.replace("file://", "", 1) if file_ref.startswith("file://") else file_ref
    path = Path(local_path)
    mime_type, _ = mimetypes.guess_type(path.name)
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