"""
SQLAlchemy 2.0 models mirroring the physical schema (§4 of the DB plan).

Phase 1 (foundation) defines only the enum registry plus the three
tenancy/identity/blob tables: ``file_blobs``, ``insurers``, ``users``. Later
phases add the claim aggregate, ruleset catalogue, outputs, LOR, and ops tables.

Enum single-source-of-truth
----------------------------
Every closed set the pipeline already models as a Pydantic ``Enum`` in
``schemas.py`` is reused here so the DB enum and the Python enum cannot drift
silently: the label list is derived from the Pydantic members. The four enums
with no Pydantic equivalent yet (``account_type``, ``claim_status``,
``note_kind`` and the ``*_source``/``*_mode`` free-text columns stay text) are
declared with literal label tuples below. ``ENUM_LABELS`` is the registry the
enum-parity test iterates against the live PG catalogue.
"""
from __future__ import annotations

import datetime as _dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.orm import Mapped, mapped_column, relationship

from agentic_pipeline.db import Base
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


def _labels(enum_cls) -> tuple[str, ...]:
    """The .value of every member, in definition order."""
    return tuple(member.value for member in enum_cls)


# --- labels for the enums with no Pydantic counterpart ------------------------
# claim_status matches service.STATUS_* exactly (service.py:59-62).
CLAIM_STATUS_LABELS = ("processing", "completed", "awaiting_documents", "failed")
ACCOUNT_TYPE_LABELS = ("personal", "commercial", "insurance")
NOTE_KIND_LABELS = (
    "warning",
    "anomaly",
    "intake_reason",
    "doc_gate_reason",
    "gateway_blocking_reason",
)

# --- the enum registry: pg type name -> label tuple ---------------------------
# Source of truth for both the DB (migration 002) and the parity test. Any drift
# between this and the live PG catalogue fails test_enum_parity.
ENUM_LABELS: dict[str, tuple[str, ...]] = {
    "capture_stage": _labels(CaptureStage),
    "policy_status": _labels(PolicyStatus),
    "value_source": _labels(ValueSource),
    "triage_verdict": _labels(TriageVerdict),
    "req_severity": _labels(RequirementSeverity),
    "req_verification": _labels(RequirementVerification),
    "req_status": _labels(RequirementStatus),
    "document_kind": _labels(DocumentKind),
    "claim_status": CLAIM_STATUS_LABELS,
    "account_type": ACCOUNT_TYPE_LABELS,
    "note_kind": NOTE_KIND_LABELS,
}


def _pg_enum(name: str) -> pg.ENUM:
    """A reference to an existing PG enum type (created by migration 002).

    ``create_type=False`` keeps SQLAlchemy from trying to emit CREATE TYPE when a
    table using it is created — the migration owns the type's lifecycle.
    """
    return pg.ENUM(*ENUM_LABELS[name], name=name, create_type=False)


# ---------------------------------------------------------------------------
# Phase 1 tables
# ---------------------------------------------------------------------------

def _uuid_pk():
    # A fresh mapped_column per call — a single column object cannot be shared
    # across two mapped classes.
    return mapped_column(
        pg.UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )


def _ts(nullable: bool = True):
    return mapped_column(pg.TIMESTAMP(timezone=True), nullable=nullable)


def _ts_now():
    return mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class FileBlob(Base):
    """Content-addressed storage row. Referenced by evidence and documents
    (later phases) via the sha256 FK; deduplicates the 60+ orphaned uploads."""

    __tablename__ = "file_blobs"

    sha256: Mapped[str] = mapped_column(pg.CHAR(64), primary_key=True)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    original_filename: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[_dt.datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )

    __table_args__ = (
        CheckConstraint("byte_size > 0", name="byte_size_positive"),
    )


class Insurer(Base):
    """Tenancy root. slug is requirements.slugify() output."""

    __tablename__ = "insurers"

    id: Mapped[str] = _uuid_pk()
    slug: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[_dt.datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )


class User(Base):
    """One authenticated (or demo) claimant. account_type mirrors the app's
    SharedPreferences 'user_category'."""

    __tablename__ = "users"

    id: Mapped[str] = _uuid_pk()
    auth_provider: Mapped[str] = mapped_column(Text, nullable=False)
    auth_subject: Mapped[str] = mapped_column(Text, nullable=False)
    email: Mapped[str | None] = mapped_column(pg.CITEXT)
    display_name: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)
    account_type: Mapped[str] = mapped_column(
        _pg_enum("account_type"), nullable=False, server_default=text("'personal'")
    )
    created_at: Mapped[_dt.datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    last_seen_at: Mapped[_dt.datetime | None] = mapped_column(
        pg.TIMESTAMP(timezone=True)
    )

    __table_args__ = (
        UniqueConstraint("auth_provider", "auth_subject", name="auth_identity"),
    )


# ---------------------------------------------------------------------------
# Phase 7 tables: policies (§4.3)
#
# user_id is NULLABLE here (deviation from the plan's NOT NULL): claims and
# policies may be anonymous until Firebase auth is wired (§11 ownership
# decision). premises_lat/lon activate the previously-dead geofence check.
# ---------------------------------------------------------------------------


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = _uuid_pk()
    user_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE")
    )
    insurer_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("insurers.id", ondelete="SET NULL")
    )
    policy_number: Mapped[str | None] = mapped_column(Text)
    product: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped["_dt.datetime | None"] = mapped_column(pg.TIMESTAMP(timezone=True))
    end_date: Mapped["_dt.datetime | None"] = mapped_column(pg.TIMESTAMP(timezone=True))
    excess: Mapped[float | None] = mapped_column(Numeric(14, 2))
    asset_categories: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    clauses_text: Mapped[str | None] = mapped_column(Text)
    clauses_assumed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    terms_source: Mapped[str | None] = mapped_column(Text)
    assumed_fields: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    premises_lat: Mapped[float | None] = mapped_column(pg.DOUBLE_PRECISION)
    premises_lon: Mapped[float | None] = mapped_column(pg.DOUBLE_PRECISION)
    created_at: Mapped[_dt.datetime] = _ts_now()
    updated_at: Mapped[_dt.datetime] = _ts_now()

    sums_insured: Mapped[list[PolicySumInsured]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )
    clauses: Mapped[list[PolicyClause]] = relationship(
        back_populates="policy", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("excess IS NULL OR excess >= 0", name="excess_non_negative"),
        CheckConstraint(
            "start_date IS NULL OR end_date IS NULL OR start_date < end_date",
            name="policy_period_ordered",
        ),
        CheckConstraint("(premises_lat IS NULL) = (premises_lon IS NULL)", name="premises_geo_paired"),
        Index("policies_number_uq", "insurer_id", "policy_number", unique=True,
              postgresql_where=text("policy_number IS NOT NULL")),
    )


class PolicySumInsured(Base):
    __tablename__ = "policy_sums_insured"

    policy_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("policies.id", ondelete="CASCADE"), primary_key=True
    )
    category_key: Mapped[str] = mapped_column(Text, primary_key=True)
    category_label: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)

    policy: Mapped[Policy] = relationship(back_populates="sums_insured")

    __table_args__ = (
        CheckConstraint("amount >= 0", name="amount_non_negative"),
    )


class PolicyClause(Base):
    __tablename__ = "policy_clauses"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    policy_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("policies.id", ondelete="CASCADE"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    clause_text: Mapped[str] = mapped_column(Text, nullable=False)

    policy: Mapped[Policy] = relationship(back_populates="clauses")

    __table_args__ = (
        UniqueConstraint("policy_id", "ordinal", name="clause_ordinal"),
    )


# ---------------------------------------------------------------------------
# Phase 2 tables: ruleset catalogue (§4.4)
#
# The master LOR, versioned and immutable per (slug, version). A claim assessed
# against v1 stays explainable after v2 activates. requirements.load_ruleset
# reconstructs the RequirementRuleSet Pydantic shape from these rows, so narrow()
# and evaluate() are untouched. Ordinal columns preserve authoring order so the
# reconstruction is byte-identical to the JSON it was imported from.
# ---------------------------------------------------------------------------


class Ruleset(Base):
    __tablename__ = "rulesets"

    id: Mapped[str] = _uuid_pk()
    insurer_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("insurers.id", ondelete="CASCADE")
    )  # NULL = built-in 'default'
    ruleset_slug: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'draft'")
    )
    coverage_report: Mapped[str | None] = mapped_column(Text)  # §6.5 backlog dump
    ingested_at: Mapped[_dt.datetime] = mapped_column(
        pg.TIMESTAMP(timezone=True), nullable=False, server_default=text("now()")
    )
    activated_at: Mapped[_dt.datetime | None] = mapped_column(pg.TIMESTAMP(timezone=True))
    activated_by: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("users.id")
    )

    claim_types: Mapped[list[RulesetClaimType]] = relationship(
        back_populates="ruleset", cascade="all, delete-orphan"
    )
    rules: Mapped[list[RequirementRuleRow]] = relationship(
        back_populates="ruleset", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ruleset_slug", "version", name="slug_version"),
        CheckConstraint(
            "status IN ('draft','active','retired')", name="status_valid"
        ),
        # At most one active version per slug — load_ruleset() must be unambiguous.
        Index(
            "rulesets_one_active",
            "ruleset_slug",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )


class RulesetClaimType(Base):
    __tablename__ = "ruleset_claim_types"

    id: Mapped[str] = _uuid_pk()
    ruleset_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True),
        ForeignKey("rulesets.id", ondelete="CASCADE"),
        nullable=False,
    )
    section_key: Mapped[str] = mapped_column(Text, nullable=False)  # 'fire', ...
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    aliases: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    ruleset: Mapped[Ruleset] = relationship(back_populates="claim_types")

    __table_args__ = (
        UniqueConstraint("ruleset_id", "section_key", name="section_key"),
    )


class RequirementRuleRow(Base):
    __tablename__ = "requirement_rules"

    id: Mapped[str] = _uuid_pk()
    ruleset_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True),
        ForeignKey("rulesets.id", ondelete="CASCADE"),
        nullable=False,
    )
    requirement_id: Mapped[str] = mapped_column(Text, nullable=False)  # 'REQ-POLICY'
    label: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    verification: Mapped[str] = mapped_column(
        _pg_enum("req_verification"), nullable=False, server_default=text("'attested'")
    )
    accepts: Mapped[list[str]] = mapped_column(
        pg.ARRAY(_pg_enum("document_kind")), nullable=False, server_default=text("'{}'")
    )
    severity: Mapped[str] = mapped_column(
        _pg_enum("req_severity"), nullable=False, server_default=text("'advisory'")
    )
    # applies_when flattened (§4.4): _condition_matches tests exactly these three.
    when_categories: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    when_item_types: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    when_account_types: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    ruleset: Mapped[Ruleset] = relationship(back_populates="rules")
    claim_type_links: Mapped[list[RequirementRuleClaimType]] = relationship(
        back_populates="rule", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("ruleset_id", "requirement_id", name="requirement_id"),
        # Mirrors schemas.py:384 "Must be empty when ATTESTED".
        CheckConstraint(
            "verification <> 'attested' OR cardinality(accepts) = 0",
            name="attested_accepts_empty",
        ),
        CheckConstraint(
            "verification <> 'classified' OR cardinality(accepts) > 0",
            name="classified_accepts_present",
        ),
        Index("requirement_rules_accepts_gin", "accepts", postgresql_using="gin"),
    )


class RequirementRuleClaimType(Base):
    """Stage-1 narrowing join. Empty set for a rule = UNIVERSAL."""

    __tablename__ = "requirement_rule_claim_types"

    rule_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True),
        ForeignKey("requirement_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    claim_type_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True),
        ForeignKey("ruleset_claim_types.id", ondelete="CASCADE"),
        primary_key=True,
    )

    rule: Mapped[RequirementRuleRow] = relationship(back_populates="claim_type_links")
    claim_type: Mapped[RulesetClaimType] = relationship()


# ---------------------------------------------------------------------------
# Phase 4 tables: the claim aggregate (§4.5–4.7)
#
# Design notes for this phase:
#  * claims.latest_state / latest_lor (JSONB) hold the full ClaimState / LORPack
#    of the most recent run. They back GET /claim/{ref} byte-for-byte and make
#    "submit -> restart -> GET" work before the derived OUTPUT tables (Phase 5)
#    exist. The normalized input tables below (evidence/documents/line_items)
#    are the source of truth for RESUME and for the Phase 6 fraud queries.
#  * sha256 columns are plain char(64) (no file_blobs FK yet) so persistence is
#    not coupled to blob ingestion; the index still powers the fraud lookup.
#  * user_id/policy_id/insurer_id/ruleset_id are nullable and unused this phase
#    (anonymous claims now; Phase 7 wires identity).
# ---------------------------------------------------------------------------

class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[str] = _uuid_pk()
    claim_ref: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    user_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    policy_id: Mapped[str | None] = mapped_column(pg.UUID(as_uuid=True))
    insurer_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("insurers.id", ondelete="SET NULL")
    )
    ruleset_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("rulesets.id")
    )
    status: Mapped[str] = mapped_column(
        _pg_enum("claim_status"), nullable=False, server_default=text("'processing'")
    )
    account_type: Mapped[str] = mapped_column(_pg_enum("account_type"), nullable=False)

    # event block
    event_date: Mapped[_dt.datetime | None] = _ts()
    event_description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    event_item_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))

    # the terms this claim was assessed against, plus the full state/lor snapshots
    policy_snapshot: Mapped[dict] = mapped_column(pg.JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    latest_state: Mapped[dict | None] = mapped_column(pg.JSONB)
    latest_lor: Mapped[dict | None] = mapped_column(pg.JSONB)

    # claim-type classification
    claim_type_key: Mapped[str | None] = mapped_column(Text)
    claim_type_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    claim_type_source: Mapped[str | None] = mapped_column(Text)
    claim_type_candidates: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    claim_type_ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    # gates
    intake_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    awaiting_documents: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    kyc_status: Mapped[str | None] = mapped_column(Text)

    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    retention_expires_at: Mapped[_dt.datetime | None] = _ts()  # nightly GC deletes past this
    created_at: Mapped[_dt.datetime] = _ts_now()
    updated_at: Mapped[_dt.datetime] = _ts_now()

    runs: Mapped[list[ClaimRun]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    documents: Mapped[list[Document]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    line_items: Mapped[list[LineItem]] = relationship(back_populates="claim", cascade="all, delete-orphan")
    notes: Mapped[list[ClaimNote]] = relationship(back_populates="claim", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "claim_type_source <> 'user_override' OR claim_type_key IS NOT NULL",
            name="override_needs_type",
        ),
        CheckConstraint(
            "claim_type_source IS NULL OR claim_type_source IN ('inferred','user_override')",
            name="claim_type_source_valid",
        ),
        Index("claims_user_recent", "user_id", text("created_at DESC")),
        Index("claims_open", "status", postgresql_where=text("status IN ('processing','awaiting_documents')")),
    )


class ClaimRun(Base):
    __tablename__ = "claim_runs"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    run_number: Mapped[int] = mapped_column(Integer, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[_dt.datetime] = _ts_now()
    finished_at: Mapped[_dt.datetime | None] = _ts()
    outcome: Mapped[str | None] = mapped_column(_pg_enum("claim_status"))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_traceback: Mapped[str | None] = mapped_column(Text)

    claim: Mapped[Claim] = relationship(back_populates="runs")

    __table_args__ = (
        UniqueConstraint("claim_id", "run_number", name="run_number"),
        CheckConstraint(
            "trigger IN ('submit','documents_added','claim_type_override','manual')",
            name="trigger_valid",
        ),
        # Exactly one in-flight run per claim (R9).
        Index("claim_runs_one_active", "claim_id", unique=True, postgresql_where=text("finished_at IS NULL")),
    )


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    evidence_ref: Mapped[str] = mapped_column(Text, nullable=False)
    capture_stage: Mapped[str] = mapped_column(_pg_enum("capture_stage"), nullable=False)
    sha256: Mapped[str] = mapped_column(pg.CHAR(64), nullable=False)
    captured_at: Mapped[_dt.datetime] = _ts(nullable=False)
    geotag_lat: Mapped[float | None] = mapped_column(pg.DOUBLE_PRECISION)
    geotag_lon: Mapped[float | None] = mapped_column(pg.DOUBLE_PRECISION)
    gyroscope: Mapped[dict | None] = mapped_column(pg.JSONB)
    device_attestation_ok: Mapped[bool | None] = mapped_column(Boolean)
    verified: Mapped[bool | None] = mapped_column(Boolean)
    verification_reasons: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    vision_processed_at: Mapped[_dt.datetime | None] = _ts()
    created_at: Mapped[_dt.datetime] = _ts_now()

    claim: Mapped[Claim] = relationship(back_populates="evidence")

    __table_args__ = (
        UniqueConstraint("claim_id", "evidence_ref", name="evidence_ref"),
        CheckConstraint("(geotag_lat IS NULL) = (geotag_lon IS NULL)", name="geotag_paired"),
        Index("evidence_sha256", "sha256"),
        Index("evidence_claim", "claim_id"),
    )


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    document_ref: Mapped[str] = mapped_column(Text, nullable=False)
    document_type: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str | None] = mapped_column(pg.CHAR(64))
    requirement_id: Mapped[str | None] = mapped_column(Text)
    uploaded_at: Mapped[_dt.datetime] = _ts(nullable=False)
    invoice_date: Mapped[_dt.datetime | None] = _ts()
    added_in_run: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id")
    )
    # document_extract_node
    extracted_quantity: Mapped[float | None] = mapped_column(Numeric(14, 3))
    extracted_unit_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    extracted_description: Mapped[str | None] = mapped_column(Text)
    extraction_done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    # document_triage_node
    classification_kind: Mapped[str | None] = mapped_column(_pg_enum("document_kind"))
    classification_verdict: Mapped[str | None] = mapped_column(_pg_enum("triage_verdict"))
    classification_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    classification_legible: Mapped[bool | None] = mapped_column(Boolean)
    classification_markers: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    classification_done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    claim: Mapped[Claim] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("claim_id", "document_ref", name="document_ref"),
        Index("documents_untriaged", "claim_id", postgresql_where=text("NOT classification_done")),
        Index("documents_claim_type", "claim_id", "document_type"),
        Index("documents_sha256", "sha256"),
    )


class LineItem(Base):
    __tablename__ = "line_items"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    item_ref: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    category: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    quantity: Mapped[float] = mapped_column(Numeric(14, 3), nullable=False, server_default=text("1"))
    serial_number: Mapped[str | None] = mapped_column(Text)
    vision_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'vision'"))
    # valuation
    unit_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    purchase_value: Mapped[float | None] = mapped_column(Numeric(14, 2))
    depreciation_pct: Mapped[float | None] = mapped_column(Numeric(5, 4))
    net_loss: Mapped[float | None] = mapped_column(Numeric(14, 2))
    value_source: Mapped[str] = mapped_column(
        _pg_enum("value_source"), nullable=False, server_default=text("'unvalued'")
    )
    # plausibility
    original_quantity_claimed: Mapped[float | None] = mapped_column(Numeric(14, 3))
    quantity_capped: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    plausibility_notes: Mapped[list[str]] = mapped_column(
        pg.ARRAY(Text()), nullable=False, server_default=text("'{}'")
    )
    # policy
    policy_status: Mapped[str | None] = mapped_column(_pg_enum("policy_status"))
    policy_clause: Mapped[str | None] = mapped_column(Text)
    policy_reasoning: Mapped[str | None] = mapped_column(Text)
    clause_grounded: Mapped[bool | None] = mapped_column(Boolean)

    claim: Mapped[Claim] = relationship(back_populates="line_items")
    evidence_links: Mapped[list[LineItemEvidence]] = relationship(
        back_populates="line_item", cascade="all, delete-orphan"
    )
    document_links: Mapped[list[LineItemDocument]] = relationship(
        back_populates="line_item", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("claim_id", "item_ref", name="item_ref"),
        CheckConstraint(
            "source IN ('vision','user_confirmed')", name="source_valid"
        ),
        CheckConstraint(
            "value_source <> 'invoice_matched' OR unit_value IS NOT NULL",
            name="valued_needs_price",
        ),
        CheckConstraint(
            "NOT quantity_capped OR original_quantity_claimed IS NOT NULL",
            name="capped_records_original",
        ),
        Index("line_items_serial", "serial_number", postgresql_where=text("serial_number IS NOT NULL")),
    )


class LineItemEvidence(Base):
    __tablename__ = "line_item_evidence"

    line_item_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("line_items.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    line_item: Mapped[LineItem] = relationship(back_populates="evidence_links")


class LineItemDocument(Base):
    __tablename__ = "line_item_documents"

    line_item_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("line_items.id", ondelete="CASCADE"), primary_key=True
    )
    document_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), primary_key=True
    )
    line_item: Mapped[LineItem] = relationship(back_populates="document_links")


class ClaimNote(Base):
    """The claim-level list[str] channels (warnings/anomalies/…), unified and
    ordered. run_id NULL = gateway-supplied input note."""

    __tablename__ = "claim_notes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(_pg_enum("note_kind"), nullable=False)
    node_name: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[_dt.datetime] = _ts_now()

    claim: Mapped[Claim] = relationship(back_populates="notes")

    __table_args__ = (
        Index("claim_notes_lookup", "claim_id", "kind", "ordinal"),
    )


# ---------------------------------------------------------------------------
# Phase 5 tables: derived outputs & LOR (§4.8–4.9)
#
# These are run-scoped projections written from each run's final ClaimState by
# repository.persist_success (+ lor_packs at submit and on override), so every
# checklist revision and every shipped pack is queryable and out/*.json is
# optional. lor_packs.ruleset_slug is text (not a uuid FK): the LORPack carries
# a slug, and file-source rulesets have no DB row.
# ---------------------------------------------------------------------------


class ReserveEstimate(Base):
    __tablename__ = "reserve_estimates"

    run_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id", ondelete="CASCADE"), primary_key=True
    )
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    confirmed: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    conditional: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    excluded: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    unclassified: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    pending: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    screened_out: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False, server_default=text("0"))
    computed_at: Mapped[_dt.datetime] = _ts_now()


class DraftPack(Base):
    __tablename__ = "draft_packs"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    main_schedule: Mapped[str] = mapped_column(Text, nullable=False)
    rejected_items_annexure: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    pending_verification_annexure: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    excluded_items_annexure: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    narrative: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(pg.CHAR(64), nullable=False)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[_dt.datetime] = _ts_now()

    qc_results: Mapped[list[QCResult]] = relationship(back_populates="draft_pack", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("run_id", "attempt", name="run_attempt"),
        Index("draft_packs_one_final", "run_id", unique=True, postgresql_where=text("is_final")),
    )


class QCResult(Base):
    __tablename__ = "qc_results"

    id: Mapped[str] = _uuid_pk()
    draft_pack_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("draft_packs.id", ondelete="CASCADE"), nullable=False
    )
    pass_qc: Mapped[bool] = mapped_column(Boolean, nullable=False)
    flags: Mapped[list[str]] = mapped_column(pg.ARRAY(Text()), nullable=False, server_default=text("'{}'"))
    check_source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[_dt.datetime] = _ts_now()

    draft_pack: Mapped[DraftPack] = relationship(back_populates="qc_results")

    __table_args__ = (
        CheckConstraint("check_source IN ('deterministic','llm')", name="check_source_valid"),
    )


class ProofReceipt(Base):
    __tablename__ = "proof_receipts"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id", ondelete="CASCADE"), nullable=False
    )
    draft_pack_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("draft_packs.id")
    )
    receipt_hash: Mapped[str] = mapped_column(pg.CHAR(64), nullable=False)
    sent_at: Mapped[_dt.datetime] = _ts(nullable=False)
    recipient: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "receipt_hash", name="receipt_hash"),
    )


class LORPackRow(Base):
    __tablename__ = "lor_packs"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id", ondelete="SET NULL")
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type_key: Mapped[str | None] = mapped_column(Text)
    claim_type_label: Mapped[str | None] = mapped_column(Text)
    claim_type_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    claim_type_ambiguous: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    ruleset_slug: Mapped[str | None] = mapped_column(Text)
    ruleset_version: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    blocking_missing: Mapped[list[str]] = mapped_column(pg.ARRAY(Text()), nullable=False, server_default=text("'{}'"))
    notes: Mapped[list[str]] = mapped_column(pg.ARRAY(Text()), nullable=False, server_default=text("'{}'"))
    generated_at: Mapped[_dt.datetime] = _ts_now()

    results: Mapped[list[LORRequirementResult]] = relationship(
        back_populates="lor_pack", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("claim_id", "revision", name="revision"),
        CheckConstraint("basis IN ('universal_only','claim_type_narrowed')", name="basis_valid"),
        CheckConstraint("revision > 1 OR basis = 'universal_only'", name="rev1_is_universal"),
    )


class LORRequirementResult(Base):
    __tablename__ = "lor_requirement_results"

    id: Mapped[str] = _uuid_pk()
    lor_pack_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("lor_packs.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    help_text: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    status: Mapped[str] = mapped_column(_pg_enum("req_status"), nullable=False)
    severity: Mapped[str] = mapped_column(_pg_enum("req_severity"), nullable=False)
    verification: Mapped[str] = mapped_column(_pg_enum("req_verification"), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)

    lor_pack: Mapped[LORPackRow] = relationship(back_populates="results")
    documents: Mapped[list[LORResultDocument]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Distinct explicit name — "requirement_id" is already taken by
        # requirement_rules (index names are schema-global).
        UniqueConstraint("lor_pack_id", "requirement_id", name="lor_pack_requirement"),
    )


class LORResultDocument(Base):
    """satisfied_by document/evidence refs. Deliberately NOT an FK to documents:
    requirements.py substitutes evidence_ids to satisfy a photo requirement."""

    __tablename__ = "lor_result_documents"

    result_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("lor_requirement_results.id", ondelete="CASCADE"), primary_key=True
    )
    document_ref: Mapped[str] = mapped_column(Text, primary_key=True)

    result: Mapped[LORRequirementResult] = relationship(back_populates="documents")


# ---------------------------------------------------------------------------
# Phase 8 tables: observability & ops (§4.10)
# ---------------------------------------------------------------------------


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False)  # 'claim_submit' | 'documents_add'
    claim_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE")
    )
    response_body: Mapped[dict | None] = mapped_column(pg.JSONB)
    created_at: Mapped[_dt.datetime] = _ts_now()
    expires_at: Mapped[_dt.datetime] = _ts(nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(pg.UUID(as_uuid=True), ForeignKey("users.id"))
    actor_kind: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)  # 'claim_type.override', ...
    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    entity_id: Mapped[str] = mapped_column(Text, nullable=False)
    before: Mapped[dict | None] = mapped_column(pg.JSONB)
    after: Mapped[dict | None] = mapped_column(pg.JSONB)
    created_at: Mapped[_dt.datetime] = _ts_now()

    __table_args__ = (
        CheckConstraint(
            "actor_kind IN ('user','system','pipeline','operator')", name="actor_kind_valid"
        ),
        Index("audit_log_entity", "entity_type", "entity_id", text("created_at DESC")),
    )


class LLMInvocation(Base):
    """One structured LLM call. node_name is nullable here (deviation from the
    plan's NOT NULL): the recorder wraps invoke_structured, which does not know
    the node."""

    __tablename__ = "llm_invocations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE")
    )
    run_id: Mapped[str | None] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claim_runs.id", ondelete="CASCADE")
    )
    node_name: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    used_vision: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    input_summary: Mapped[dict | None] = mapped_column(pg.JSONB)
    output_raw: Mapped[dict | None] = mapped_column(pg.JSONB)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    succeeded: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_type: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[_dt.datetime] = _ts_now()

    __table_args__ = (
        Index("llm_invocations_run", "run_id", "created_at"),
    )


class OutboxMessage(Base):
    """Transactional outbox: proof_of_intimation enqueues here so delivery to the
    (not-yet-existent) insurer intake API is decoupled and retryable."""

    __tablename__ = "outbox_messages"

    id: Mapped[str] = _uuid_pk()
    claim_id: Mapped[str] = mapped_column(
        pg.UUID(as_uuid=True), ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(pg.JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_retry_at: Mapped[_dt.datetime | None] = _ts()
    created_at: Mapped[_dt.datetime] = _ts_now()

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','sent','failed','abandoned')", name="outbox_status_valid"
        ),
        Index("outbox_due", "next_retry_at", postgresql_where=text("status = 'pending'")),
    )
