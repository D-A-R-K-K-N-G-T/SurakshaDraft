"""Phase 2 DB-backed tests: ruleset catalogue.

Acceptance: a ruleset reconstructed from the DB is byte-identical to the JSON it
was imported from, so `RULESET_SOURCE=db` is a drop-in for the file source and
GET /api/v1/requirements/default is unchanged. Plus the §4.4 CHECK / partial
unique constraints actually reject bad rows.

Requires TEST_DATABASE_URL (a throwaway DB). Because the importer and loader use
the app engine (settings.database_url), these tests also require
DATABASE_URL == TEST_DATABASE_URL; they skip otherwise.
"""
from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from agentic_pipeline import requirements as reqs
from agentic_pipeline import rulesets_cli
from agentic_pipeline.config import settings


@pytest.fixture()
def db_default_ruleset(migrated_db, test_database_url, monkeypatch):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL to run DB ruleset tests")
    path = reqs.ruleset_path("default")
    version = reqs._load_file(path).version
    assert rulesets_cli.main(["import-json", path]) == 0
    assert rulesets_cli.main(["activate", "default", version]) == 0
    monkeypatch.setattr(settings, "ruleset_source", "db")
    reqs._DB_CACHE.clear()
    yield


def test_db_ruleset_byte_identical_to_file(db_default_ruleset):
    file_rs = reqs._load_file(reqs.ruleset_path("default"))
    db_rs = reqs.load_ruleset(None)  # dispatches to DB now
    assert db_rs is not None
    assert db_rs == file_rs
    assert db_rs.model_dump_json() == file_rs.model_dump_json()


def test_db_list_ruleset_ids(db_default_ruleset):
    assert reqs.list_ruleset_ids() == ["default"]


def test_load_ruleset_by_insurer_falls_back_to_default(db_default_ruleset):
    # Unknown insurer -> its slug has no active row -> falls back to default.
    got = reqs.load_ruleset("No Such Insurer Ltd")
    assert got is not None and got.ruleset_id == "default"


def test_fail_open_when_source_db_but_empty(migrated_db, test_database_url, monkeypatch):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL to run DB ruleset tests")
    monkeypatch.setattr(settings, "ruleset_source", "db")
    reqs._DB_CACHE.clear()
    # Nothing imported: load returns None (claim proceeds), list returns [].
    assert reqs.load_ruleset(None) is None
    assert reqs.list_ruleset_ids() == []


# --- §4.4 constraint tests (Layer 4) --------------------------------------

def _new_ruleset(conn, slug="t", version="1", status="draft"):
    return conn.execute(
        text(
            "INSERT INTO rulesets (ruleset_slug, version, status) "
            "VALUES (:s, :v, :st) RETURNING id"
        ),
        {"s": slug, "v": version, "st": status},
    ).scalar()


def test_attested_rule_cannot_have_accepts(migrated_db):
    with migrated_db.begin() as conn:
        rid = _new_ruleset(conn)
    with pytest.raises(IntegrityError):
        with migrated_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requirement_rules "
                    "(ruleset_id, requirement_id, label, verification, accepts, severity, ordinal) "
                    "VALUES (:r, 'R', 'L', 'attested', ARRAY['govt_id']::document_kind[], 'advisory', 0)"
                ),
                {"r": rid},
            )


def test_classified_rule_must_have_accepts(migrated_db):
    with migrated_db.begin() as conn:
        rid = _new_ruleset(conn, slug="t2")
    with pytest.raises(IntegrityError):
        with migrated_db.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO requirement_rules "
                    "(ruleset_id, requirement_id, label, verification, accepts, severity, ordinal) "
                    "VALUES (:r, 'R', 'L', 'classified', ARRAY[]::document_kind[], 'advisory', 0)"
                ),
                {"r": rid},
            )


def test_only_one_active_version_per_slug(migrated_db):
    with pytest.raises(IntegrityError):
        with migrated_db.begin() as conn:
            _new_ruleset(conn, slug="dup", version="1", status="active")
            _new_ruleset(conn, slug="dup", version="2", status="active")


def test_bad_status_rejected(migrated_db):
    with pytest.raises(IntegrityError):
        with migrated_db.begin() as conn:
            _new_ruleset(conn, slug="bad", status="published")
