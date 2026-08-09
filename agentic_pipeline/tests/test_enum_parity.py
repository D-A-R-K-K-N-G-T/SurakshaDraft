"""Enum parity (DB plan §4.1 / §8).

Catches three drifts:
  * offline: the migration 0002 label snapshot vs models.ENUM_LABELS
  * offline: models.ENUM_LABELS vs the Pydantic enums it claims to mirror
  * online:  the live PG enum types vs models.ENUM_LABELS

The online check is what stops DOC_TRIAGE_SYSTEM_PROMPT / DocumentKind from
silently diverging from the database.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import text

from agentic_pipeline.models import ENUM_LABELS
from agentic_pipeline.schemas import (
    CaptureStage,
    DocumentKind,
    PolicyStatus,
    RequirementSeverity,
    RequirementStatus,
    RequirementVerification,
    TriageVerdict,
    ValueSource,
)

_PYDANTIC_SOURCES = {
    "capture_stage": CaptureStage,
    "policy_status": PolicyStatus,
    "value_source": ValueSource,
    "triage_verdict": TriageVerdict,
    "req_severity": RequirementSeverity,
    "req_verification": RequirementVerification,
    "req_status": RequirementStatus,
    "document_kind": DocumentKind,
}


def _load_migration_enums() -> dict[str, tuple[str, ...]]:
    path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "versions"
        / "0002_enums.py"
    )
    spec = importlib.util.spec_from_file_location("_mig_0002_enums", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module.ENUMS


def test_migration_matches_registry():
    """The frozen migration snapshot must equal the app's enum registry."""
    assert _load_migration_enums() == ENUM_LABELS


def test_registry_matches_pydantic():
    """Every registry entry with a Pydantic source must mirror it exactly."""
    for name, enum_cls in _PYDANTIC_SOURCES.items():
        expected = tuple(m.value for m in enum_cls)
        assert ENUM_LABELS[name] == expected, name


def test_live_pg_enums_match_registry(migrated_db):
    """The types actually in Postgres must match the registry, label-for-label."""
    with migrated_db.connect() as conn:
        for name, expected in ENUM_LABELS.items():
            rows = conn.execute(
                text(
                    "SELECT e.enumlabel FROM pg_type t "
                    "JOIN pg_enum e ON e.enumtypid = t.oid "
                    "WHERE t.typname = :name ORDER BY e.enumsortorder"
                ),
                {"name": name},
            ).fetchall()
            actual = tuple(r[0] for r in rows)
            assert actual == expected, f"{name}: {actual} != {expected}"
