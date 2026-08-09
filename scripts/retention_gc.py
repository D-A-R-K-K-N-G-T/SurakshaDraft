"""Retention garbage collection (Phase 8, §9).

Deletes claims whose retention_expires_at has passed (cascading to every child
row), then removes file_blobs no longer referenced by any evidence or document
(and best-effort unlinks their on-disk files). Intended to run nightly.

    python -m scripts.retention_gc --dry-run
    python -m scripts.retention_gc

Requires DATABASE_URL -> a migrated database.
"""
from __future__ import annotations

import argparse
import datetime as dt

from sqlalchemy import delete, select, text

from agentic_pipeline import models as M
from agentic_pipeline.blobs import local_path_from_ref

_ORPHAN_BLOBS = text(
    "SELECT sha256, storage_uri FROM file_blobs fb "
    "WHERE NOT EXISTS (SELECT 1 FROM evidence e WHERE e.sha256 = fb.sha256) "
    "AND NOT EXISTS (SELECT 1 FROM documents d WHERE d.sha256 = fb.sha256)"
)


def gc(session, *, now: dt.datetime | None = None, dry_run: bool = False) -> dict:
    """Core GC. Returns counts; deletes unless dry_run."""
    now = now or dt.datetime.now(dt.timezone.utc)

    expired = session.execute(
        select(M.Claim.claim_ref).where(
            M.Claim.retention_expires_at.is_not(None),
            M.Claim.retention_expires_at < now,
        )
    ).scalars().all()

    if dry_run:
        orphans = session.execute(_ORPHAN_BLOBS).all()
        return {"expired_claims": len(expired), "orphan_blobs": len(orphans), "deleted": False}

    if expired:
        session.execute(delete(M.Claim).where(M.Claim.claim_ref.in_(expired)))
        session.flush()  # deleting claims may orphan more blobs

    orphans = session.execute(_ORPHAN_BLOBS).all()
    shas = [row[0] for row in orphans]
    if shas:
        session.execute(delete(M.FileBlob).where(M.FileBlob.sha256.in_(shas)))

    # Best-effort unlink of the on-disk blob files (fs:// only).
    unlinked = 0
    for _sha, uri in orphans:
        if uri and uri.startswith("fs://"):
            try:
                import os
                p = local_path_from_ref(uri)
                if os.path.isfile(p):
                    os.remove(p)
                    unlinked += 1
            except Exception:
                pass

    return {"expired_claims": len(expired), "orphan_blobs": len(shas),
            "files_unlinked": unlinked, "deleted": True}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true", help="Report only; delete nothing")
    args = p.parse_args(argv)

    from agentic_pipeline.db import session_scope

    with session_scope() as s:
        result = gc(s, dry_run=args.dry_run)
    if args.dry_run:
        print(f"Would delete {result['expired_claims']} expired claim(s) and "
              f"{result['orphan_blobs']} orphan blob(s). DRY RUN.")
    else:
        print(f"Deleted {result['expired_claims']} expired claim(s), "
              f"{result['orphan_blobs']} orphan blob row(s), "
              f"{result.get('files_unlinked', 0)} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
