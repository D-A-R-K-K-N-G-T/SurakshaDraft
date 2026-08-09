"""Content-addressed blob helpers (Phase 3).

One layout, shared by the backfill script and the image resolver, and mirrored
by the Express gateway in JS: a blob for content hash ``sha`` lives at
``<root>/<sha[:2]>/<sha><ext>`` and is referenced by an ``fs://`` URI.

Keeping the original extension in the filename lets the resolver derive a mime
type without a DB round-trip while still deduplicating on content.
"""
from __future__ import annotations

import hashlib
import os
import boto3
from pathlib import Path
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from agentic_pipeline.config import settings

ENCRYPTION_MAGIC = b'SURAKSHA_ENC_V1'

def decrypt_envelope(data: bytes) -> bytes:
    if not data.startswith(ENCRYPTION_MAGIC):
        return data
    if not settings.pii_master_key:
        raise ValueError("Encrypted blob encountered but pii_master_key not set")
    master_key = bytes.fromhex(settings.pii_master_key)
    pos = 15
    dek_iv = data[pos:pos+12]; pos += 12
    dek_tag = data[pos:pos+16]; pos += 16
    enc_dek = data[pos:pos+32]; pos += 32
    master_aesgcm = AESGCM(master_key)
    dek = master_aesgcm.decrypt(dek_iv, enc_dek + dek_tag, None)
    iv = data[pos:pos+12]; pos += 12
    tag = data[pos:pos+16]; pos += 16
    ciphertext = data[pos:]
    aesgcm = AESGCM(dek)
    return aesgcm.decrypt(iv, ciphertext + tag, None)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_ext(filename: str | None) -> str:
    """Lowercased extension incl. the dot ('' if none). Keeps dedup stable."""
    if not filename:
        return ""
    return os.path.splitext(filename)[1].lower()


def shard_relpath(sha: str, ext: str = "") -> str:
    """'ab/abcdef...<ext>' — always forward slashes."""
    return f"{sha[:2]}/{sha}{ext}"


def blob_abspath(root: str | os.PathLike, sha: str, ext: str = "") -> Path:
    return Path(root) / sha[:2] / f"{sha}{ext}"


def fs_uri(path: str | os.PathLike) -> str:
    """Absolute local path -> fs:// URI (forward slashes, cross-platform)."""
    abs_posix = Path(path).resolve().as_posix()
    return f"fs://{abs_posix}"

def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key
    )


def local_path_from_ref(ref: str) -> str:
    """Strip a fs:// or s3:// prefix. For s3://, download to a temp file and return its path."""
    if ref.startswith("s3://"):
        if not settings.s3_bucket:
            raise ValueError("s3:// URI encountered but S3 is not configured")
        key = ref[len("s3://"):]
        tmp_path = Path("/tmp") / key.replace("/", "_")
        if not tmp_path.exists():
            s3 = get_s3_client()
            s3.download_file(settings.s3_bucket, key, str(tmp_path))
        if key.endswith('.enc'):
            data = decrypt_envelope(tmp_path.read_bytes())
            dec_path = tmp_path.with_suffix('.dec')
            dec_path.write_bytes(data)
            return str(dec_path)
        return str(tmp_path)
    
    if ref.startswith("fs://") or ref.startswith("file://"):
        path = ref[len("fs://"):] if ref.startswith("fs://") else ref[len("file://"):]
        if path.endswith('.enc'):
            data = decrypt_envelope(Path(path).read_bytes())
            dec_path = Path("/tmp") / Path(path).name.replace('.enc', '.dec')
            dec_path.write_bytes(data)
            return str(dec_path)
        return path
        
    return ref


def store_bytes(
    root: str | os.PathLike, data: bytes, original_filename: str | None = None
) -> tuple[str, Path, str, bool]:
    """Write ``data`` into the content-addressed layout under ``root`` or S3.

    Returns (sha256, abspath, fs_uri/s3_uri, already_existed).
    """
    sha = sha256_bytes(data)
    ext = normalize_ext(original_filename)
    rel = shard_relpath(sha, ext)
    
    if settings.s3_bucket:
        s3 = get_s3_client()
        key = f"{root}/{rel}" if str(root) != "." else rel
        try:
            s3.head_object(Bucket=settings.s3_bucket, Key=key)
            existed = True
        except Exception:
            existed = False
            s3.put_object(Bucket=settings.s3_bucket, Key=key, Body=data)
        
        # We don't have a local Path, but we return a fake Path for compatibility
        return sha, Path(key), f"s3://{key}", existed

    dest = blob_abspath(root, sha, ext)
    existed = dest.exists()
    if not existed:
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Atomic-ish: write to a temp sibling then rename.
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    return sha, dest, fs_uri(dest), existed
