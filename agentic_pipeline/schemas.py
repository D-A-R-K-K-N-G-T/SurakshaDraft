"""
Pydantic data contracts shared across every node. These are the nouns of
the pipeline — nodes only ever produce/consume these shapes, never ad-hoc
dicts, which is what lets each node be tested in isolation with a plain
Python object as input.
"""
from __future__ import annotations

from datetime import datetime, timezone
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
    serial_number: Optional[str] = None

    # set by valuation_agent
    unit_value: Optional[float] = None
    purchase_value: Optional[float] = None
    depreciation_pct: Optional[float] = None
    net_loss: Optional[float] = None
    value_source: ValueSource = ValueSource.UNVALUED
    matched_document_ids: list[str] = Field(default_factory=list)
    
    # set by plausibility
    original_quantity_claimed: Optional[float] = None
    quantity_capped: bool = False
    plausibility_notes: list[str] = Field(default_factory=list)

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
    quantity: float = 1  # count of identical items in this group (default 1)
    serial_number: Optional[str] = None  # visible serial/model plate, if any
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
    supporting_documents: list[str] = Field(default_factory=list)


class DocumentKind(str, Enum):
    """What a document ACTUALLY is, as judged from its contents.

    Deliberately a closed set: the triage model picks one of these, so no
    injected text inside an uploaded file can smuggle in a novel verdict value.
    UNREADABLE and UNKNOWN are the model's explicit "I cannot tell" answers and
    never block a claim.

    This set stays SMALL on purpose. An insurer's master LOR names far more
    documents than belong here; growing this to 60+ overlapping classes would
    degrade both the injection defence and classification accuracy. Requirements
    naming a document with no member here are handled by the `attested`
    verification tier (see RequirementRule) instead of by adding a member.
    """
    POLICY_SCHEDULE = "policy_schedule"
    PREMIUM_RECEIPT = "premium_receipt"
    GOVT_ID = "govt_id"
    TAX_INVOICE = "tax_invoice"
    STOCK_REGISTER = "stock_register"
    MENU_OR_PRICE_LIST = "menu_or_price_list"
    MARKETING_OR_OTHER_COMMERCIAL = "marketing_or_other_commercial"
    DAMAGE_PHOTOGRAPH = "damage_photograph"
    # Added for LOR requirement matching. Each of these MUST also be described
    # in DOC_TRIAGE_SYSTEM_PROMPT or the model will never emit it.
    FIR_REPORT = "fir_report"
    FIRE_BRIGADE_REPORT = "fire_brigade_report"
    REPAIR_ESTIMATE = "repair_estimate"
    REPAIR_BILL = "repair_bill"
    BANK_PROOF = "bank_proof"
    MEDICAL_CERTIFICATE = "medical_certificate"
    DRIVING_LICENCE = "driving_licence"
    VEHICLE_RC = "vehicle_rc"
    UNREADABLE = "unreadable"
    UNKNOWN = "unknown"


class TriageVerdict(str, Enum):
    MATCH = "match"            # contents agree with the slot it was uploaded into
    UNVERIFIED = "unverified"  # could not confirm either way — never blocks
    MISMATCH = "mismatch"      # positively identified as a different document class


class LLMDocumentTriage(BaseModel):
    """One document's classification. NOTE: the model is never told which slot
    the claimant uploaded this into — it answers the open question "what is
    this?", and the comparison happens afterwards in Python."""
    document_id: str
    doc_kind: DocumentKind
    legible: bool = True
    confidence: float = Field(default=0.0, ge=0, le=1)
    # Verbatim strings actually read off the page that justify doc_kind. A
    # classification with no cited evidence is not allowed to block a claim.
    markers: list[str] = Field(default_factory=list)
    # Independent factual signals used to rescue a would-be rejection: a real
    # policy in any language carries insurance anchors even if the model
    # mislabels its kind.
    has_insurance_anchors: bool = False
    has_identity_anchors: bool = False
    observed_summary: str = ""


class DocumentTriageOutput(BaseModel):
    documents: list[LLMDocumentTriage]


class DocumentRecord(BaseModel):
    document_id: str
    document_type: str  # the slot the CLIENT claimed — an assertion, not a fact
    file_ref: str
    # Set when the user uploaded this file against a specific LOR checklist row.
    # This is the ONLY thing that can satisfy an `attested` requirement — we make
    # no claim about what such a file is, only that it was supplied for that row.
    requirement_id: Optional[str] = None
    uploaded_at: datetime  # when the file was submitted to us (always post-loss)
    # The date on the document itself (e.g. the invoice/purchase date). This —
    # not uploaded_at — is what chronology checks should compare to the event
    # date. None when unknown (e.g. not yet OCR'd / not user-entered).
    invoice_date: Optional[datetime] = None
    # Fields filled by document_extract_node (OCR of invoice images). None until
    # extracted. The valuation agent reads these instead of the raw file.
    extracted_quantity: Optional[float] = None
    extracted_unit_value: Optional[float] = None
    extracted_description: Optional[str] = None
    extraction_done: bool = False
    # Filled by document_triage_node.
    classification_kind: Optional[DocumentKind] = None
    classification_verdict: Optional[TriageVerdict] = None
    classification_confidence: Optional[float] = None
    classification_legible: Optional[bool] = None
    classification_markers: list[str] = Field(default_factory=list)
    classification_done: bool = False


class LLMSumInsured(BaseModel):
    category: str          # as written on the schedule, e.g. "Plant & Machinery"
    amount: float

class PolicyExtractionOutput(BaseModel):
    """Terms read off the insured's actual policy schedule document."""
    policy_number: Optional[str] = None
    insurer: Optional[str] = None
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    excess: Optional[float] = None
    sums_insured: list[LLMSumInsured] = Field(default_factory=list)
    # Verbatim clause sentences — coverage AND exclusions. The policy agent
    # quotes from these, and _clause_is_grounded checks citations against them.
    clauses: list[str] = Field(default_factory=list)


class LLMDocumentExtraction(BaseModel):
    document_id: str  # echo back unchanged — the join key
    unit_value: Optional[float] = None
    quantity: Optional[float] = None
    description: Optional[str] = None
    invoice_date: Optional[datetime] = None

class DocumentExtractionOutput(BaseModel):
    documents: list[LLMDocumentExtraction]


class LLMValuationItem(BaseModel):
    item_ref: str
    unit_value: Optional[float] = None
    matched_document_ids: list[str] = Field(default_factory=list)

class ValuationOutput(BaseModel):
    items: list[LLMValuationItem]


class LLMPolicyItem(BaseModel):
    item_ref: str
    policy_status: PolicyStatus
    policy_clause: Optional[str] = None
    policy_reasoning: Optional[str] = None

class PolicyOutput(BaseModel):
    items: list[LLMPolicyItem]


class LLMPendingItem(BaseModel):
    item_label: str
    quantity_claimed: float
    unit_value_from_records: Optional[float] = None
    user_notes: Optional[str] = None
    supporting_documents: list[str] = Field(default_factory=list)

class ReconciliationOutput(BaseModel):
    pending_items: list[LLMPendingItem]


class PlausibilityOutput(BaseModel):
    line_items: list[LineItem]
    rejected_items: list[RejectedItem]


class DraftOutput(BaseModel):
    main_schedule: str
    rejected_items_annexure: str
    pending_verification_annexure: str
    excluded_items_annexure: str = ""  # items the policy excludes (not the same as rejected)
    narrative: Optional[str] = None


class QCGuardOutput(BaseModel):
    pass_qc: bool
    flags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Letter of Requirement (LOR)
#
# A client onboards with an EXHAUSTIVE master LOR covering every class of
# business they write. These shapes hold that list after ingestion, and the
# narrowed, per-claim result we hand back to the claimant.
# --------------------------------------------------------------------------


class RequirementSeverity(str, Enum):
    BLOCKING = "blocking"    # the claim cannot proceed until this is supplied
    ADVISORY = "advisory"    # listed on the checklist; never halts the pipeline


class RequirementVerification(str, Enum):
    """How we decide a requirement has been met.

    CLASSIFIED — triage independently identified an uploaded file as one of the
    kinds in `accepts`. We are asserting what the document IS.
    ATTESTED   — the claimant uploaded a file against this checklist row. We
    assert only that something was supplied for it, never what it is. This is
    how the long tail of an exhaustive master LOR (letters of subrogation,
    indemnity bonds, consent-to-deduct forms) reaches the claimant without
    inflating DocumentKind or claiming a verification we did not perform.
    """
    CLASSIFIED = "classified"
    ATTESTED = "attested"


class RequirementStatus(str, Enum):
    SATISFIED = "satisfied"
    UNVERIFIED = "unverified"  # something arrived for it but we couldn't read it
    MISSING = "missing"


class RequirementCondition(BaseModel):
    """Stage-2 narrowing. AND across fields, OR within a field's list.

    An empty condition means the rule applies to every claim of its claim type.
    NOTE: no amount-based key — this is evaluated before valuation runs. See the
    module docstring of requirements.py.
    """
    categories: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)
    account_types: list[str] = Field(default_factory=list)


class ClaimTypeSection(BaseModel):
    """One section of the master LOR, e.g. "Fire & Allied Perils".

    `description` and `aliases` exist to be shown to the claim-type classifier —
    they carry the domain mapping (in Indian general insurance flood is a
    sub-peril of Fire & Allied Perils) that a bare label would not.
    """
    id: str
    label: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)


class RequirementRule(BaseModel):
    requirement_id: str
    label: str
    help_text: str = ""
    # Stage-1 narrowing. EMPTY means universal: applies to every claim regardless
    # of claim type. Universal rules are what the instant rev.1 LOR is built from.
    claim_types: list[str] = Field(default_factory=list)
    verification: RequirementVerification = RequirementVerification.ATTESTED
    # Any ONE of these kinds satisfies the rule. Must be empty when ATTESTED.
    accepts: list[DocumentKind] = Field(default_factory=list)
    severity: RequirementSeverity = RequirementSeverity.ADVISORY
    applies_when: RequirementCondition = Field(default_factory=RequirementCondition)


class RequirementRuleSet(BaseModel):
    ruleset_id: str
    version: str = ""
    source: str = ""
    claim_types: list[ClaimTypeSection] = Field(default_factory=list)
    rules: list[RequirementRule] = Field(default_factory=list)


class RequirementResult(BaseModel):
    """One row of the claimant's checklist."""
    requirement_id: str
    label: str
    help_text: str = ""
    status: RequirementStatus
    severity: RequirementSeverity
    verification: RequirementVerification
    # Human-readable, claimant-facing. Never phrased as "you failed to send X"
    # when the truth is we could not read what they did send.
    message: str = ""
    satisfied_by: list[str] = Field(default_factory=list)  # document_ids


class LORPack(BaseModel):
    """The Letter of Requirement handed back to the claimant.

    Revisions: 1 is the instant pack computed at submit from universal rules and
    claimed document roles only; 2+ are computed inside the graph once documents
    have been triaged and the claim type inferred, and after every re-upload.
    """
    revision: int = 1
    basis: str = "universal_only"  # "universal_only" | "claim_type_narrowed"
    claim_type: Optional[str] = None
    claim_type_label: Optional[str] = None
    claim_type_confidence: Optional[float] = None
    # True when the classifier could not settle on one section, so this pack is
    # the UNION of the candidates with non-universal rules demoted to advisory.
    claim_type_ambiguous: bool = False
    generated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ruleset_id: str = ""
    ruleset_version: str = ""
    satisfied: list[RequirementResult] = Field(default_factory=list)
    unverified: list[RequirementResult] = Field(default_factory=list)
    missing: list[RequirementResult] = Field(default_factory=list)
    # requirement_ids that halt the pipeline: MISSING and BLOCKING. Never
    # includes UNVERIFIED — we do not block a claimant over an unreadable scan.
    blocking_missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ClaimTypeAlternate(BaseModel):
    claim_type_id: str
    confidence: float = Field(default=0.0, ge=0, le=1)


class ClaimTypeClassification(BaseModel):
    """The classifier's answer, constrained to the ruleset's own section ids."""
    claim_type_id: str
    confidence: float = Field(default=0.0, ge=0, le=1)
    rationale: str = ""
    alternates: list[ClaimTypeAlternate] = Field(default_factory=list)


# --- master-LOR ingestion (onboarding only, never at claim time) ---

class LLMClaimTypeSection(BaseModel):
    id: str
    label: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)


class ClaimTypeSectionsOutput(BaseModel):
    claim_types: list[LLMClaimTypeSection]


class LLMRequirement(BaseModel):
    """One requirement as read off the master LOR.

    NOTE: the model names the document in the insurer's own words via
    `document_name`; it never picks the verification tier or the DocumentKind.
    Python maps `document_name` onto the enum afterwards, exactly as
    _triage_verdict keeps the verdict out of the model's hands.
    """
    requirement_id: str
    label: str
    document_name: str = ""
    help_text: str = ""
    claim_types: list[str] = Field(default_factory=list)
    severity: RequirementSeverity = RequirementSeverity.ADVISORY
    categories: list[str] = Field(default_factory=list)
    item_types: list[str] = Field(default_factory=list)
    account_types: list[str] = Field(default_factory=list)


class RequirementsOutput(BaseModel):
    requirements: list[LLMRequirement]