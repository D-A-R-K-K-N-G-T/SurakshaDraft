"""
The single object that flows through the LangGraph pipeline.

Fields wrapped in Annotated[..., operator.add] are additive across nodes —
a node returns only the *new* items it produced, and LangGraph appends
them to whatever's already there. Everything else is last-write-wins
(a node's return dict just overwrites that key).
"""
from __future__ import annotations

import operator
from typing import Annotated, Optional

from pydantic import BaseModel

from agentic_pipeline.schemas import (
    EvidenceRecord,
    LineItem,
    PendingVerificationItem,
    RejectedItem,
    VisionMissingSignal,
    DraftOutput,
    QCGuardOutput,
    DocumentRecord,
    LORPack,
)


class ClaimState(BaseModel):
    # Set by the service once the claim ref is known, so the fraud registries
    # (plausibility_check_node) can exclude this claim from its own lookups.
    claim_ref: Optional[str] = None

    policy: dict
    event: dict

    # Last-write-wins: document_extract_node rewrites the whole list with OCR
    # fields filled in. (Was operator.add, but nothing appends documents, and an
    # additive channel would duplicate the list when the extract node returns it.)
    documents: list[DocumentRecord] = []
    evidence: list[EvidenceRecord] = []
    line_items: list[LineItem] = []
    rejected_items: Annotated[list[RejectedItem], operator.add] = []
    pending_verification: Annotated[list[PendingVerificationItem], operator.add] = []
    warnings: Annotated[list[str], operator.add] = []
    intake_ok: bool = False
    intake_reasons: list[str] = []

    # Blocking findings from document_triage_node (a file positively identified
    # as a different document class than the slot it was uploaded into).
    # Last-write-wins: only the triage node writes it, and intake merges it.
    doc_gate_reasons: list[str] = []
    # Deterministic blocking findings computed by the gateway before the
    # pipeline ran (e.g. the same file uploaded under two different roles).
    gateway_blocking_reasons: list[str] = []
    # Honest description of what the ID check established. Never implies a
    # named-claimant KYC match — the app collects no claimant name.
    kyc_status: Optional[str] = None

    # free-text notes from any node that spots something off but isn't
    # itself the check that should act on it (added by vision so far)
    anomalies: Annotated[list[str], operator.add] = []

    # every evidence_id vision has ever sent to the model, whether or not it
    # produced a LineItem. Needed because filtering "already processed" off
    # of line_items.evidence_refs alone misses evidence that only produced a
    # missing_signal (or nothing) — that evidence would get resent to the
    # model on every subsequent vision call otherwise.
    vision_processed_evidence_ids: Annotated[list[str], operator.add] = []

    # set by vision, consumed by reconciliation_agent. NOT additive —
    # overwritten each time vision runs. Fine for the fixed one-pass
    # pipeline; if vision ever runs across multiple sync batches before
    # reconciliation_agent fires, switch this to operator.add.
    pending_signals: list[VisionMissingSignal] = []

    # --- Letter of Requirement ---
    # The claimant's document checklist. Revision 1 is computed synchronously at
    # submit from universal rules only; requirements_node overwrites it with the
    # claim-type-narrowed pack once triage and classification have run.
    # Last-write-wins: making it additive would append packs instead of replacing.
    lor: Optional[LORPack] = None
    # True when a BLOCKING requirement is genuinely absent. Distinct from
    # intake_ok=False: the claim is not rejected, it is paused pending documents.
    awaiting_documents: bool = False

    # Which section of the master LOR applies. Inferred by claim_type_classify_node
    # unless claim_type_source == "user_override", which that node then never
    # touches again — a claimant's correction must survive every re-run.
    claim_type: Optional[str] = None
    claim_type_confidence: Optional[float] = None
    claim_type_source: Optional[str] = None  # "inferred" | "user_override"
    # Runner-up sections, kept when the classifier could not settle on one.
    claim_type_candidates: list[str] = []
    claim_type_ambiguous: bool = False

    reserve_estimate: Optional[dict] = None
    draft_pack: Optional[DraftOutput] = None
    qc: Optional[QCGuardOutput] = None
    proof_receipt: Optional[dict] = None

    valuation_retries: int = 0
    qc_retries: int = 0