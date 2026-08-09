"""Alembic environment.

The DB URL comes from ``settings.database_url`` (not alembic.ini) so migrations,
the app, and the tests all agree on one source of truth. ``target_metadata`` is
the app's declarative Base, with every model imported so its tables register.

Autogenerate is configured to compare column types and to ignore Postgres
extension/enum objects it cannot reason about cleanly (enum value additions and
CHECK changes are added by hand in their own revisions — see the DB plan §5).
"""
from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from agentic_pipeline.config import settings
from agentic_pipeline.db import Base

# Import every model module so its tables attach to Base.metadata before
# autogenerate compares. (Phase 1: models.py holds them all.)
import agentic_pipeline.models  # noqa: F401  (side-effect import)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # Never let autogenerate touch Postgres' internal / extension-owned tables.
    if type_ == "table" and getattr(obj, "schema", None) not in (None, "public"):
        return False
    return True


def _url() -> str:
    # Precedence lets tests point at a throwaway DB without touching .env:
    #   alembic.ini sqlalchemy.url  >  DATABASE_URL env  >  settings default.
    return (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("DATABASE_URL")
        or settings.database_url
    )


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        # Off deliberately: PG renders enum defaults with an explicit cast
        # ('personal'::account_type) and functions like now()/gen_random_uuid(),
        # which the comparator reports as false drift. Re-enable per-column later
        # if a real default needs guarding.
        compare_server_default=False,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    # NullPool: a migration run makes one short-lived connection; no pooling
    # needed, and it plays nicely behind PgBouncer.
    connectable = create_engine(_url(), poolclass=pool.NullPool, future=True)
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=False,  # see run_migrations_offline
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
