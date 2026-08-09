"""Shared test fixtures for the DB-backed suite.

The pure-function tests (test_plausibility, test_requirements, …) need no DB and
are untouched. Anything that needs Postgres depends on ``migrated_db`` below,
which SKIPS cleanly unless ``TEST_DATABASE_URL`` is set to a THROWAWAY database.

    TEST_DATABASE_URL is required (never DATABASE_URL) because the round-trip
    test runs `downgrade base`, which drops every table — you do not want that
    pointed at a dev/prod DB by accident.

CI wires an ephemeral Postgres (docker service / the compose `db`) and exports
TEST_DATABASE_URL before invoking pytest.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

TEST_DB_ENV = "TEST_DATABASE_URL"


def _test_url() -> str | None:
    url = os.environ.get(TEST_DB_ENV)
    return url or None


def _reachable(url: str) -> bool:
    try:
        eng = create_engine(url)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        eng.dispose()
        return True
    except Exception:
        return False


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = _test_url()
    if not url:
        pytest.skip(f"{TEST_DB_ENV} not set; skipping DB-backed test")
    if not _reachable(url):
        pytest.skip(f"{TEST_DB_ENV} set but database is unreachable")
    return url


@pytest.fixture()
def alembic_config(test_database_url: str):
    """An Alembic Config pinned to the test DB and the project's script tree."""
    from pathlib import Path

    from alembic.config import Config

    root = Path(__file__).resolve().parents[2]  # SurakshaDraft/
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", "agentic_pipeline/migrations")
    cfg.set_main_option("sqlalchemy.url", test_database_url)
    return cfg


@pytest.fixture()
def migrated_db(alembic_config, test_database_url: str) -> Engine:
    """A pristine, migrated throwaway DB: reset to base, up to head, teardown to base.

    The leading downgrade guarantees a clean slate even if a previous run (or
    manual use) left tables/data behind.
    """
    from alembic import command

    from agentic_pipeline.db import engine as app_engine

    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    # The app engine (used by service/repository through SessionLocal) pools
    # connections that cache query plans; dropping+recreating tables between
    # tests invalidates those. Dispose so each test gets fresh connections.
    app_engine.dispose()
    engine = create_engine(test_database_url)
    try:
        yield engine
    finally:
        engine.dispose()
        app_engine.dispose()
        command.downgrade(alembic_config, "base")
