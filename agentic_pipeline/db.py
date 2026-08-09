"""
Database engine, session factory, and the declarative Base.

This is the single place the pipeline talks to Postgres. The Python service is
the SOLE schema owner (§2 of the DB plan); the Express gateway stays stateless.

Two rules that the rest of the codebase depends on:

  * The declarative ``Base`` carries an explicit naming_convention. Alembic's
    autogenerate produces stable constraint/index names from it, so revision
    diffs stay clean instead of churning on anonymous names. Do not remove it.

  * Background pipeline runs (FastAPI ``BackgroundTasks`` on the threadpool)
    MUST open their own session via ``session_scope()`` — never share the
    request's session across the thread boundary (§6.1).
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import MetaData, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from agentic_pipeline.config import settings

# Stable names for every constraint kind — see the note above.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every model and by Alembic's target_metadata."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# One engine per process. create_engine does not open a connection; the first
# real connection is established lazily on checkout. pool_pre_ping avoids handing
# out a connection the server has already dropped.
engine = create_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

# expire_on_commit=False: objects stay usable after commit, which the repository
# layer relies on when it returns hydrated rows to callers.
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
    class_=Session,
    future=True,
)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
