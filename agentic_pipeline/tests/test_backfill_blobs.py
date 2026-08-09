"""Phase 3 DB-backed test: uploads backfill -> file_blobs + sharded layout.

Requires TEST_DATABASE_URL; also DATABASE_URL == TEST_DATABASE_URL because the
backfill writes through the app engine.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from agentic_pipeline.config import settings
from scripts import backfill_blobs


def test_backfill_dedupes_and_is_idempotent(migrated_db, test_database_url, tmp_path):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL to run backfill tests")

    root = tmp_path / "uploads"
    root.mkdir()
    (root / "a.pdf").write_bytes(b"PDF-CONTENT")
    (root / "a-copy.pdf").write_bytes(b"PDF-CONTENT")  # identical -> dedup
    (root / "b.jpg").write_bytes(b"IMAGE-CONTENT")

    assert backfill_blobs.main(["--root", str(root)]) == 0

    with migrated_db.connect() as conn:
        n_blobs = conn.execute(text("SELECT count(*) FROM file_blobs")).scalar()
        uris = conn.execute(text("SELECT storage_uri FROM file_blobs")).scalars().all()
    assert n_blobs == 2  # two distinct contents

    # Flat files are gone; blobs live in 2-char shard dirs.
    assert not (root / "a.pdf").exists()
    assert not (root / "a-copy.pdf").exists()
    shard_dirs = [d for d in root.iterdir() if d.is_dir()]
    assert shard_dirs and all(len(d.name) == 2 for d in shard_dirs)
    assert all(u.startswith("fs://") for u in uris)

    # Idempotent: nothing left to move, no duplicate rows.
    assert backfill_blobs.main(["--root", str(root)]) == 0
    with migrated_db.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM file_blobs")).scalar() == 2
