"""Backfill legacy flat uploads into the content-addressed blob store (Phase 3, §7).

Hashes every flat file directly under the blob root, inserts a file_blobs row
(idempotent), and moves the file into uploads/<sha[:2]>/<sha><ext>. Files already
inside a shard subdir (or tmp/) are left alone, so re-running is a no-op.

    python -m scripts.backfill_blobs --dry-run          # report only
    python -m scripts.backfill_blobs                    # do it

Requires DATABASE_URL -> a migrated database. --dry-run needs no DB.
"""
from __future__ import annotations

import argparse
import mimetypes
import os
import re
from pathlib import Path

from agentic_pipeline.blobs import blob_abspath, fs_uri, normalize_ext, sha256_file
from agentic_pipeline.config import settings

_SHARD_DIR = re.compile(r"^[0-9a-f]{2}$")
_SHA_NAME = re.compile(r"^([0-9a-f]{64})")


def _flat_files(root: Path) -> list[Path]:
    """Regular files directly under root that are not already sharded/temp."""
    if not root.exists():
        return []
    return [entry for entry in sorted(root.iterdir()) if entry.is_file()]


def _sharded_files(root: Path) -> list[Path]:
    """Blob files already in the <sha[:2]>/<sha><ext> layout."""
    out: list[Path] = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        if d.is_dir() and _SHARD_DIR.match(d.name):
            out.extend(f for f in sorted(d.iterdir()) if f.is_file())
    return out


def _reconcile_sharded(session, root: Path) -> int:
    """Ensure a file_blobs row exists for every already-sharded blob.

    Makes backfill a true reconcile: after the flat files are moved (or after a
    DB reset), running again reconstructs rows from what is on disk. Trusts the
    content-addressed filename for the sha; skips anything that isn't one.
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from agentic_pipeline import models as M

    ensured = 0
    for f in _sharded_files(root):
        m = _SHA_NAME.match(f.name)
        if not m:
            continue
        sha = m.group(1)
        size = f.stat().st_size
        if size == 0:
            continue
        mime, _ = mimetypes.guess_type(f.name)
        session.execute(
            pg_insert(M.FileBlob)
            .values(
                sha256=sha,
                storage_uri=fs_uri(f),
                mime_type=mime or "application/octet-stream",
                byte_size=size,
                original_filename=None,
            )
            .on_conflict_do_nothing(index_elements=["sha256"])
        )
        ensured += 1
    return ensured


def backfill(root: Path, dry_run: bool) -> int:
    files = _flat_files(root)
    if not files:
        sharded = _sharded_files(root)
        if not sharded:
            print(f"Nothing to backfill under {root} (empty).")
            return 0
        print(f"No flat files under {root}; reconciling {len(sharded)} sharded blob(s).")
        if dry_run:
            print("DRY RUN: no rows written.")
            return 0
        from agentic_pipeline.db import session_scope

        with session_scope() as session:
            ensured = _reconcile_sharded(session, root)
        print(f"Done: {ensured} sharded blob(s) reconciled into file_blobs.")
        return 0

    # Pre-scan for stats (and to fail loudly before touching anything).
    seen: dict[str, Path] = {}
    total_bytes = 0
    unique_bytes = 0
    empties: list[Path] = []
    plan: list[tuple[Path, str, str]] = []  # (src, sha, ext)
    for f in files:
        size = f.stat().st_size
        if size == 0:
            empties.append(f)
            continue
        sha = sha256_file(f)
        ext = normalize_ext(f.name)
        total_bytes += size
        if sha not in seen:
            seen[sha] = f
            unique_bytes += size
        plan.append((f, sha, ext))

    dupes = len(plan) - len(seen)
    ratio = (1 - unique_bytes / total_bytes) * 100 if total_bytes else 0.0
    print(f"Scanned {len(files)} flat file(s): {len(seen)} unique blob(s), {dupes} duplicate(s).")
    print(f"  bytes: {total_bytes:,} -> {unique_bytes:,} unique ({ratio:.1f}% saved by dedup)")
    if empties:
        print(f"  skipping {len(empties)} empty file(s) (violate byte_size > 0).")

    if dry_run:
        print("DRY RUN: no files moved, no rows written.")
        return 0

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from agentic_pipeline import models as M
    from agentic_pipeline.db import session_scope

    moved = deduped = 0
    with session_scope() as session:
        for src, sha, ext in plan:
            dest = blob_abspath(root, sha, ext)
            mime, _ = mimetypes.guess_type(src.name)
            # ON CONFLICT DO NOTHING makes re-runs and cross-file dup shas safe.
            session.execute(
                pg_insert(M.FileBlob)
                .values(
                    sha256=sha,
                    storage_uri=fs_uri(dest),
                    mime_type=mime or "application/octet-stream",
                    byte_size=src.stat().st_size,
                    original_filename=src.name,
                )
                .on_conflict_do_nothing(index_elements=["sha256"])
            )
            if dest.exists():
                src.unlink()  # identical blob already stored -> drop the dup
                deduped += 1
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                os.replace(src, dest)
                moved += 1

    # rowcount is unreliable for ON CONFLICT DO NOTHING; report the true count.
    print(f"Done: moved {moved}, deduped {deduped}; {len(seen)} unique file_blobs row(s) ensured.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", default=settings.blob_store_root, help="Blob store root (default from config)")
    p.add_argument("--dry-run", action="store_true", help="Report only; touch nothing")
    args = p.parse_args(argv)
    return backfill(Path(args.root), args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
