"""Migration integrity tests (DB plan §5 CI gate).

Require TEST_DATABASE_URL -> a throwaway Postgres; skip otherwise. See conftest.
"""
from __future__ import annotations

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from agentic_pipeline.db import Base
import agentic_pipeline.models  # noqa: F401  (register tables on Base.metadata)

PHASE1_TABLES = {"file_blobs", "insurers", "users"}


def test_upgrade_downgrade_upgrade(alembic_config, test_database_url):
    """upgrade head -> downgrade base -> upgrade head must all succeed."""
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")

    from sqlalchemy import create_engine

    engine = create_engine(test_database_url)
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
        command.downgrade(alembic_config, "base")

    assert PHASE1_TABLES <= tables, f"missing tables after upgrade: {PHASE1_TABLES - tables}"


def test_no_model_drift(migrated_db):
    """Autogenerate must see zero diffs between models and the migrated DB.

    This is the `alembic check` equivalent, run in-process so a failure prints
    the offending diffs rather than just a non-zero exit.
    """
    with migrated_db.connect() as conn:
        ctx = MigrationContext.configure(
            conn,
            opts={"compare_type": True, "compare_server_default": False},
        )
        diffs = compare_metadata(ctx, Base.metadata)

    assert diffs == [], f"unexpected model/DB drift: {diffs}"


def test_extensions_present(migrated_db):
    with migrated_db.connect() as conn:
        rows = conn.exec_driver_sql(
            "SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto','citext')"
        ).fetchall()
    names = {r[0] for r in rows}
    assert {"pgcrypto", "citext"} <= names
