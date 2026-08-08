"""
Pydantic data contracts shared across every node. These are the nouns of
the pipeline — nodes only ever produce/consume these shapes, never ad-hoc
dicts, which is what lets each node be tested in isolation with a plain
Python object as input.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class CaptureStage(str, Enum):
    SCENE = "scene"
    ZONE = "zone"
    ITEM = "item"


class GeoPoint(BaseModel):
    lat: float
    lon: float


class EvidenceRecord(BaseModel):
    """One photo, captured offline, hashed on-device before it ever left the phone."""

    evidence_id: str
    capture_stage: CaptureStage
    file_ref: str  # local path pre-sync, S3 key post-sync
    sha256: str
    captured_at: datetime
    geotag: Optional[GeoPoint] = None
    gyroscope: Optional[dict] = None  # raw sensor reading, kept loose on purpose
    device_attestation_ok: Optional[bool] = None

    # set by evidence_verify — None means "not yet checked"
    verified: Optional[bool] = None
    verification_reasons: list[str] = Field(default_factory=list)


class PolicyStatus(str, Enum):
    COVERED = "covered"
    EXCLUDED = "excluded"
    REVIEW = "review"


class ValueSource(str, Enum):
    INVOICE_MATCHED = "invoice_matched"
    CATALOG_ESTIMATE = "catalog_estimate"
    UNVALUED = "unvalued"


class LineItem(BaseModel):
    item_ref: str  # e.g. "LI-1"
    name: str
    description: str = ""
    category: str = ""  # Stock / Machinery / FFF — free text for now
    quantity: float = 1
    evidence_refs: list[str] = Field(default_factory=list)
    vision_confidence: Optional[float] = None  # 0-1, set by `vision`

    # set by valuation_agent
    unit_value: Optional[float] = None
    purchase_value: Optional[float] = None
    depreciation_pct: Optional[float] = None
    net_loss: Optional[float] = None
    value_source: ValueSource = ValueSource.UNVALUED
    matched_invoice_ref: Optional[str] = None
    matched_document_ids: list[str] = Field(default_factory=list)

    # set by policy_agent
    policy_status: Optional[PolicyStatus] = None
    policy_clause: Optional[str] = None
    policy_reasoning: Optional[str] = None


class VisionCandidateItem(BaseModel):
    """
    An item vision can actually see, with item-level photo evidence behind
    it. This is what becomes a LineItem.

    Deliberate deviation from the doc's literal "VisionOutput.items:
    list[LineItem]": the doc also describes LI-7 (fabric rolls, no item
    photos, only empty-shelf/debris shots) as "a partial entry ... flagged
    absent from shelf" coming out of this same phase. But every downstream
    phase treats LI-7 as never entering line_items at all (drafter's main
    schedule is explicitly "LI-1,2,3,5,6", reconciliation_agent "never
    touches line_items"). So rather than inventing a half-populated
    LineItem, "no photo of the item itself" is split into its own bucket
    below (VisionMissingSignal) that GATE2 and reconciliation_agent read
    directly. Flag if you intended LI-7 to get a real (if partial) LineItem.
    """

    name: str
    description: str = ""
    category: str = ""
    quantity: float = 1
    vision_confidence: float = Field(ge=0, le=1)
    evidence_refs: list[str] = Field(default_factory=list)


class VisionMissingSignal(BaseModel):
    """
    No item photo exists — only an empty-shelf/debris signal suggesting
    something is gone. Not enough on its own to become a LineItem;
    reconciliation_agent decides (using paper records) whether it becomes
    a PendingVerificationItem.
    """

    item_label_guess: str
    location_notes: str = ""
    evidence_refs: list[str] = Field(default_factory=list)


class VisionOutput(BaseModel):
    items: list[VisionCandidateItem]
    missing_signals: list[VisionMissingSignal] = Field(default_factory=list)
    anomalies: list[str] = Field(default_factory=list)


class RejectedItem(BaseModel):
    item_ref: str
    line_item_snapshot: LineItem
    reasons: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class PendingVerificationItem(BaseModel):
    item_label: str
    quantity_claimed: float
    unit_value_from_records: Optional[float] = None
    claimed_total: Optional[float] = None
    user_notes: Optional[str] = None


class DocumentRecord(BaseModel):
    document_id: str
    document_type: str
    file_ref: str
    uploaded_at: datetime


class ValuationOutput(BaseModel):
    items: list[LineItem]


class PolicyOutput(BaseModel):
    items: list[LineItem]


class ReconciliationOutput(BaseModel):
    pending_items: list[PendingVerificationItem]


class PlausibilityOutput(BaseModel):
    line_items: list[LineItem]
    rejected_items: list[RejectedItem]


class DraftOutput(BaseModel):
    main_schedule: str
    rejected_items_annexure: str
    pending_verification_annexure: str
    narrative: Optional[str] = None


class QCGuardOutput(BaseModel):
    pass_qc: bool
    flags: list[str] = Field(default_factory=list)