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


def load_image_as_data_url(file_ref: str) -> str:
    path = Path(file_ref)
    mime_type, _ = mimetypes.guess_type(path.name)
    mime_type = mime_type or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
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