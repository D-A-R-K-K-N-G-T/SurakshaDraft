"""
Node function implementations and the wiring that connects them into the
LangGraph StateGraph — kept in one file per the project's flat structure
(no separate nodes/ package).
"""
from __future__ import annotations

import json
import hashlib
import logging
import os
import math
import re
from datetime import datetime, timezone, timedelta

_log = logging.getLogger("agentic_pipeline.graph")
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from agentic_pipeline.llm import get_structured_llm, invoke_structured
from agentic_pipeline.prompts import (
    VISION_HUMAN_PROMPT_TEMPLATE, VISION_SYSTEM_PROMPT,
    DOC_TRIAGE_SYSTEM_PROMPT, DOC_TRIAGE_HUMAN_PROMPT_TEMPLATE,
    POLICY_EXTRACT_SYSTEM_PROMPT, POLICY_EXTRACT_HUMAN_PROMPT_TEMPLATE,
    DOC_EXTRACT_SYSTEM_PROMPT, DOC_EXTRACT_HUMAN_PROMPT_TEMPLATE,
    VALUATION_SYSTEM_PROMPT, VALUATION_HUMAN_PROMPT_TEMPLATE,
    POLICY_SYSTEM_PROMPT, POLICY_HUMAN_PROMPT_TEMPLATE,
    RECONCILIATION_SYSTEM_PROMPT, RECONCILIATION_HUMAN_PROMPT_TEMPLATE,
    DRAFTER_SYSTEM_PROMPT, DRAFTER_HUMAN_PROMPT_TEMPLATE,
    QC_GUARDIAN_SYSTEM_PROMPT, QC_GUARDIAN_HUMAN_PROMPT_TEMPLATE
)
from agentic_pipeline.schemas import (
    LineItem, VisionOutput, ValueSource, PolicyStatus,
    PendingVerificationItem, RejectedItem, ValuationOutput,
    PolicyOutput, ReconciliationOutput, PlausibilityOutput,
    DraftOutput, QCGuardOutput, DocumentRecord, DocumentExtractionOutput,
    PolicyExtractionOutput, DocumentTriageOutput, DocumentKind, TriageVerdict
)
from agentic_pipeline.state import ClaimState
from agentic_pipeline.images import build_image_block, load_image_as_data_url
from agentic_pipeline.config import settings, DEPRECIATION_BY_CATEGORY, DEPRECIATION_DEFAULT


def _depreciation_for(category: str | None) -> float:
    return DEPRECIATION_BY_CATEGORY.get((category or "").strip().lower(), DEPRECIATION_DEFAULT)


def _parse_iso_datetime(value) -> datetime | None:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime.

    Returns None for anything unparseable — including None, JSON null, numbers,
    or malformed strings. Callers get a single, total contract instead of the
    scattered `.replace(...)` that raised AttributeError on None (which the old
    `except ValueError` missed, killing the whole run).
    """
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# Which document kinds legitimately belong in each client-declared slot.
# A slot absent from this map is never gated.
ROLE_EXPECTED_KINDS = {
    "PolicyDocument": {DocumentKind.POLICY_SCHEDULE},
    "GovtID": {DocumentKind.GOVT_ID},
    "Invoice": {DocumentKind.TAX_INVOICE, DocumentKind.PREMIUM_RECEIPT, DocumentKind.STOCK_REGISTER},
}
# Only these slots can fail a claim. A misfiled invoice is a warning, not a block.
BLOCKING_ROLES = {"PolicyDocument", "GovtID"}
# The model's explicit "I cannot tell" answers — never blocking.
NON_BLOCKING_KINDS = {DocumentKind.UNKNOWN, DocumentKind.UNREADABLE}

ROLE_LABELS = {
    "PolicyDocument": "policy schedule",
    "GovtID": "government ID",
    "Invoice": "purchase invoice",
}


def _triage_verdict(role: str, t, min_confidence: float) -> TriageVerdict:
    """Compare what a document IS against the slot it was uploaded into.

    Deliberately asymmetric. Confirming a document is MISMATCH (which can fail a
    real person's flood claim) requires every one of: a positive identification
    of a different, named class; a legible page; at least one quoted marker; high
    confidence; and no domain anchors suggesting we simply mislabelled a genuine
    document. Anything short of all five degrades to UNVERIFIED, which never blocks.
    """
    expected = ROLE_EXPECTED_KINDS.get(role)
    if expected is None:
        return TriageVerdict.UNVERIFIED
    if t.doc_kind in expected:
        return TriageVerdict.MATCH
    if t.doc_kind in NON_BLOCKING_KINDS:
        return TriageVerdict.UNVERIFIED
    if not t.legible:
        return TriageVerdict.UNVERIFIED          # couldn't read it -> can't condemn it
    if not t.markers:
        return TriageVerdict.UNVERIFIED          # no cited evidence -> not actionable
    if (t.confidence or 0.0) < min_confidence:
        return TriageVerdict.UNVERIFIED
    # Anchor rescue: a real policy/ID in any script carries these even if the
    # model picked the wrong kind label.
    if role == "PolicyDocument" and t.has_insurance_anchors:
        return TriageVerdict.UNVERIFIED
    if role == "GovtID" and t.has_identity_anchors:
        return TriageVerdict.UNVERIFIED
    return TriageVerdict.MISMATCH


def _doc_gate_reason(role: str, observed: DocumentKind | None) -> str:
    label = ROLE_LABELS.get(role, role)
    observed_label = (observed.value if observed else "unidentified").replace("_", " ")
    if role == "PolicyDocument":
        return (
            f"The file uploaded as your {label} appears to be a {observed_label}, not an "
            f"insurance document. Please upload the policy schedule your insurer issued — "
            f"the page showing your policy number and period of insurance."
        )
    if role == "GovtID":
        return (
            f"The file uploaded as your {label} appears to be a {observed_label}, not an "
            f"identity document. Please upload your Aadhaar, PAN, Voter ID, Driving Licence "
            f"or Passport (a masked Aadhaar is fine)."
        )
    return f"The file uploaded as your {label} appears to be a {observed_label}."


def document_triage_node(state: ClaimState) -> dict:
    """Verify that each uploaded file IS what the client said it is.

    Runs first, before any policy reading or vision work, because document_type
    is a client-authored assertion that later nodes otherwise treat as fact.
    All documents go in ONE batched vision call, and the model is never shown
    the claimed type — it answers "what is this?" and Python does the comparing.
    Fails OPEN: any load/LLM error warns and lets the claim proceed.
    """
    candidates = [
        d for d in state.documents
        if d.file_ref and d.file_ref != "system-generated" and not d.classification_done
    ]
    if not candidates:
        return {}

    content = [{"type": "text", "text": DOC_TRIAGE_HUMAN_PROMPT_TEMPLATE}]
    sent = []
    warnings = []
    for d in candidates:
        try:
            block = build_image_block(load_image_as_data_url(d.file_ref))
        except Exception as exc:
            warnings.append(
                f"Could not open the file uploaded as your "
                f"{ROLE_LABELS.get(d.document_type, d.document_type)} "
                f"({type(exc).__name__}); it was not verified."
            )
            continue
        # NOTE: only the opaque document_id is labelled — never the claimed type.
        content.append({"type": "text", "text": f"[document_id={d.document_id}]"})
        content.append(block)
        sent.append(d)

    if not sent:
        return {"warnings": warnings} if warnings else {}

    llm = get_structured_llm(DocumentTriageOutput, want_vision=True)
    try:
        result: DocumentTriageOutput = invoke_structured(llm, [
            SystemMessage(content=DOC_TRIAGE_SYSTEM_PROMPT),
            HumanMessage(content=content),
        ])
    except Exception as exc:
        # Fail open: a provider outage must not reject every claimant.
        return {"warnings": warnings + [
            f"Document verification unavailable ({type(exc).__name__}); "
            f"documents were not verified and the claim was allowed to proceed."
        ]}

    sent_ids = {d.document_id for d in sent}
    triaged = {t.document_id: t for t in result.documents if t.document_id in sent_ids}

    reasons = []
    verdicts = {}
    for d in sent:
        t = triaged.get(d.document_id)
        if t is None:
            verdicts[d.document_id] = (None, TriageVerdict.UNVERIFIED, None, None, [])
            warnings.append(
                f"The file uploaded as your {ROLE_LABELS.get(d.document_type, d.document_type)} "
                f"could not be verified."
            )
            continue

        verdict = _triage_verdict(d.document_type, t, settings.doc_triage_mismatch_confidence)
        verdicts[d.document_id] = (t.doc_kind, verdict, t.confidence, t.legible, t.markers)
        _log.info(
            "triage %s role=%s kind=%s legible=%s conf=%.2f verdict=%s markers=%s",
            d.document_id, d.document_type, t.doc_kind.value, t.legible,
            t.confidence or 0.0, verdict.value, t.markers[:3],
        )

        if verdict == TriageVerdict.MISMATCH and d.document_type in BLOCKING_ROLES:
            reasons.append(_doc_gate_reason(d.document_type, t.doc_kind))
        elif verdict == TriageVerdict.MISMATCH:
            warnings.append(
                f"The file uploaded as your {ROLE_LABELS.get(d.document_type, d.document_type)} "
                f"looks like a {t.doc_kind.value.replace('_', ' ')}; it was not used."
            )
        elif verdict == TriageVerdict.UNVERIFIED and d.document_type in BLOCKING_ROLES:
            warnings.append(
                f"Could not confirm the file uploaded as your "
                f"{ROLE_LABELS.get(d.document_type, d.document_type)}; "
                f"the claim is proceeding and has been flagged for manual verification."
            )

    new_documents = []
    for d in state.documents:
        if d.document_id in verdicts:
            kind, verdict, conf, legible, markers = verdicts[d.document_id]
            d.classification_kind = kind
            d.classification_verdict = verdict
            d.classification_confidence = conf
            d.classification_legible = legible
            d.classification_markers = markers or []
            d.classification_done = True
        new_documents.append(d)

    # Honest KYC wording: this establishes "an identity document was supplied",
    # NOT "it belongs to the claimant" — the app collects no claimant name.
    id_docs = [d for d in new_documents if d.document_type == "GovtID"]
    if not id_docs:
        kyc_status = "no_id_document_supplied"
    elif any(d.classification_verdict == TriageVerdict.MATCH for d in id_docs):
        kyc_status = "id_document_present_unverified"
    else:
        kyc_status = "id_document_unreadable"

    out = {"documents": new_documents, "kyc_status": kyc_status}
    if settings.doc_gate_mode == "enforce":
        out["doc_gate_reasons"] = reasons
    else:
        warnings.extend(reasons)
        out["doc_gate_reasons"] = []
    if warnings:
        out["warnings"] = warnings
    return out


def policy_extract_node(state: ClaimState) -> dict:
    """Read the insured's actual policy schedule and replace the assumed terms.

    Runs FIRST, before intake, so the policy-period gate and every downstream
    coverage decision use the real document rather than a generic stub. The
    document is authoritative; caller-supplied values are kept only where the
    document is silent.
    """
    policy_docs = [d for d in state.documents if d.document_type == "PolicyDocument"]
    if not policy_docs:
        return {}

    # Never read terms off a file triage positively identified as something else
    # — that garbage would overwrite `policy` before intake reads it.
    usable = [d for d in policy_docs if d.classification_verdict != TriageVerdict.MISMATCH]
    if not usable:
        return {"warnings": [
            f"Skipped policy extraction: the uploaded policy file does not appear "
            f"to be a policy schedule; proceeding with assumed terms."
        ]}

    doc = usable[0]  # one schedule per claim
    try:
        block = build_image_block(load_image_as_data_url(doc.file_ref))
    except Exception as exc:
        return {"warnings": [
            f"Could not read policy document {doc.document_id} ({type(exc).__name__}: {exc}); "
            f"proceeding with assumed policy terms."
        ]}

    llm = get_structured_llm(PolicyExtractionOutput, want_vision=True)
    try:
        result: PolicyExtractionOutput = invoke_structured(llm, [
            SystemMessage(content=POLICY_EXTRACT_SYSTEM_PROMPT),
            HumanMessage(content=[
                {"type": "text", "text": POLICY_EXTRACT_HUMAN_PROMPT_TEMPLATE},
                block,
            ]),
        ])
    except Exception as exc:
        return {"warnings": [
            f"Policy extraction failed ({type(exc).__name__}: {exc}); "
            f"proceeding with assumed policy terms."
        ]}

    policy = dict(state.policy)
    warnings = []
    extracted_fields = []

    if result.start_date:
        policy["start_date"] = result.start_date.isoformat()
        extracted_fields.append("start_date")
    if result.end_date:
        policy["end_date"] = result.end_date.isoformat()
        extracted_fields.append("end_date")
    if result.policy_number:
        policy["policy_number"] = result.policy_number
    if result.insurer:
        policy["insurer"] = result.insurer
    if result.excess is not None:
        policy["excess"] = result.excess
        extracted_fields.append("excess")

    for entry in result.sums_insured:
        policy[_sum_insured_key(entry.category)] = entry.amount
    if result.sums_insured:
        extracted_fields.append(f"{len(result.sums_insured)} sums insured")

    if result.clauses:
        # Newline-joined verbatim clauses: this exact text is shown to the policy
        # agent AND used as the grounding corpus for citation checks.
        policy["clauses"] = "\n".join(result.clauses)
        policy["clauses_source"] = "policy_document"
        # Real clauses were read: lift the coverage cap the gateway imposed.
        policy["clauses_assumed"] = False
        extracted_fields.append(f"{len(result.clauses)} clauses")
    else:
        warnings.append(
            "No clauses could be read from the policy document; coverage decisions "
            "fall back to assumed terms."
        )

    if extracted_fields:
        policy["terms_source"] = "policy_document"
        warnings.append(f"Policy terms read from the uploaded schedule: {', '.join(extracted_fields)}.")

    return {"policy": policy, "warnings": warnings}


def intake_node(state: ClaimState) -> dict:
    reasons = []

    event_date = _parse_iso_datetime(state.event.get("event_date"))
    p_start = _parse_iso_datetime(state.policy.get("start_date"))
    p_end = _parse_iso_datetime(state.policy.get("end_date"))

    warnings = []

    if event_date is None:
        # Can't confirm the loss fell within the policy period — don't pass silently.
        reasons.append("Event date missing or unparseable; cannot confirm the loss fell within the policy period.")
    elif p_start and p_end:
        tol = timedelta(days=settings.policy_period_boundary_tolerance_days)
        if p_start - tol <= event_date <= p_end + tol:
            if event_date < p_start or event_date > p_end:
                warnings.append("Loss date falls on the policy-period boundary; an assessor will confirm the applicable period.")
        else:
            reasons.append("Event date is outside the policy period.")

    # NOTE: the old "no premium receipt" gate is gone. The gateway fabricated a
    # receipt for every claim purely to satisfy it, so it could never fail — a
    # control in name only. Premium-payment status simply isn't verifiable here.
    warnings.append("Premium payment status could not be verified — no insurer record integration.")

    # Fold in blocking findings produced upstream: document triage (a file that
    # is positively a different document class) and the gateway (e.g. the same
    # file uploaded under two roles).
    reasons.extend(state.doc_gate_reasons)
    reasons.extend(state.gateway_blocking_reasons)

    intake_ok = not reasons
    out = {"intake_ok": intake_ok, "intake_reasons": reasons}
    if reasons:
        warnings.extend(f"Intake gate failed: {r}" for r in reasons)
    if warnings:
        out["warnings"] = warnings
    return out


def check_intake(state: ClaimState):
    return "proceed" if state.intake_ok else "intake_rejected"


def intake_rejected_node(state: ClaimState) -> dict:
    reasons = state.intake_reasons or ["Claim did not pass intake checks."]
    body = "This claim was not accepted at intake and was not processed:\n" + "\n".join(f"- {r}" for r in reasons)

    # List what we received and what each file looked like, so the JSON pack in
    # ./out/ is self-explanatory even without the app.
    received = [d for d in state.documents if d.file_ref and d.file_ref != "system-generated"]
    if received:
        body += "\n\nDocuments received:\n" + "\n".join(
            f"- {ROLE_LABELS.get(d.document_type, d.document_type)}: "
            f"looks like {(d.classification_kind.value.replace('_', ' ') if d.classification_kind else 'unidentified')} "
            f"({d.classification_verdict.value if d.classification_verdict else 'not checked'})"
            for d in received
        )
    return {
        "draft_pack": DraftOutput(
            main_schedule=body,
            rejected_items_annexure="",
            pending_verification_annexure="",
            excluded_items_annexure="",
            narrative="Intake rejection — no items were assessed.",
        )
    }


def _haversine(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = math.sin(delta_phi/2.0)**2 + math.cos(phi1)*math.cos(phi2) * math.sin(delta_lambda/2.0)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c


def evidence_verify_node(state: ClaimState) -> dict:
    event_date = _parse_iso_datetime(state.event.get("event_date"))

    premises_geo = state.policy.get("premises_geo")
        
    for e in state.evidence:
        if e.verified is not None:
            continue
            
        verified = True
        reasons = []
        
        # 1. SHA256 compare (stubbed implementation using local file path assumption)
        if e.file_ref and os.path.exists(e.file_ref):
            try:
                with open(e.file_ref, "rb") as f:
                    actual_hash = hashlib.sha256(f.read()).hexdigest()
                if actual_hash != e.sha256:
                    verified = False
                    reasons.append(f"Hash mismatch: expected {e.sha256}, got {actual_hash}")
            except Exception:
                pass
        
        # 2. Geofence
        if premises_geo and e.geotag:
            dist = _haversine(premises_geo.get("lat", 0), premises_geo.get("lon", 0), e.geotag.lat, e.geotag.lon)
            if dist > settings.geofence_radius_m:
                verified = False
                reasons.append(f"Geofence miss: distance {dist:.1f}m > {settings.geofence_radius_m}m")
                
        # 3. Timestamp window
        if event_date and e.captured_at:
            cap_time = e.captured_at
            if cap_time.tzinfo is None:
                cap_time = cap_time.replace(tzinfo=timezone.utc)
            delta = (cap_time - event_date).total_seconds() / 3600.0
            if delta < -settings.flood_window_hours_before or delta > settings.flood_window_hours_after:
                verified = False
                reasons.append(f"Timestamp out of bounds relative to event ({delta:.1f}h)")
                
        e.verified = verified
        e.verification_reasons.extend(reasons)
        
    return {"evidence": state.evidence}


def vision_node(state: ClaimState) -> dict:
    already_processed = set(state.vision_processed_evidence_ids)
    new_evidence = [
        e for e in state.evidence
        if e.verified and e.evidence_id not in already_processed
    ]
    if not new_evidence:
        return {}

    declared_categories = state.policy.get("asset_categories") or [
        "Stock", "Furniture, Fixtures & Fittings", "Plant & Machinery",
    ]

    content = [{
        "type": "text",
        "text": VISION_HUMAN_PROMPT_TEMPLATE.format(
            asset_categories=", ".join(declared_categories)
        ),
    }]
    load_errors = []
    loaded_evidence = []
    for e in new_evidence:
        # One unreadable image must not abort the whole claim. Skip it, note an
        # anomaly, and process the rest.
        try:
            image_block = build_image_block(load_image_as_data_url(e.file_ref))
        except Exception as exc:
            load_errors.append(
                f"Could not load evidence {e.evidence_id} ({e.file_ref}): {type(exc).__name__}: {exc}"
            )
            continue
        content.append({
            "type": "text",
            "text": f"[evidence_id={e.evidence_id}, capture_stage={e.capture_stage.value}]",
        })
        content.append(image_block)
        loaded_evidence.append(e)

    # Every evidence we attempted is marked processed (even failures) so a
    # permanently-broken file is not re-sent on a later vision pass.
    processed_ids = [e.evidence_id for e in new_evidence]

    if not loaded_evidence:
        return {
            "anomalies": load_errors,
            "vision_processed_evidence_ids": processed_ids,
        }

    llm = get_structured_llm(VisionOutput, want_vision=True)
    result: VisionOutput = invoke_structured(llm, [
        SystemMessage(content=VISION_SYSTEM_PROMPT),
        HumanMessage(content=content),
    ])

    start_idx = len(state.line_items) + 1
    new_line_items = [
        LineItem(
            item_ref=f"LI-{start_idx + i}",
            name=item.name,
            description=item.description,
            category=item.category,
            quantity=item.quantity,
            serial_number=item.serial_number,
            evidence_refs=item.evidence_refs,
            vision_confidence=item.vision_confidence,
        )
        for i, item in enumerate(result.items)
    ]

    return {
        "line_items": state.line_items + new_line_items,
        "anomalies": result.anomalies + load_errors,
        "pending_signals": result.missing_signals,
        "vision_processed_evidence_ids": processed_ids,
    }


def document_extract_node(state: ClaimState) -> dict:
    """OCR each not-yet-extracted invoice image into structured fields.

    The valuation agent works over TEXT, and DocumentRecords otherwise carry no
    financial content — only a file path. This node reads each invoice image via
    the vision model and fills unit_value/quantity/description/invoice_date so
    valuation has real numbers to match against.
    """
    pending = [
        d for d in state.documents
        if d.document_type == "Invoice" and not d.extraction_done
    ]
    if not pending:
        return {}

    llm = get_structured_llm(DocumentExtractionOutput, want_vision=True)

    content = [{"type": "text", "text": DOC_EXTRACT_HUMAN_PROMPT_TEMPLATE}]
    extractable = []
    load_errors = []
    for d in pending:
        try:
            block = build_image_block(load_image_as_data_url(d.file_ref))
        except Exception as exc:
            load_errors.append(f"Could not load invoice {d.document_id} ({d.file_ref}): {type(exc).__name__}: {exc}")
            continue
        content.append({"type": "text", "text": f"[document_id={d.document_id}]"})
        content.append(block)
        extractable.append(d)

    updates = {}
    if extractable:
        result: DocumentExtractionOutput = invoke_structured(llm, [
            SystemMessage(content=DOC_EXTRACT_SYSTEM_PROMPT),
            HumanMessage(content=content),
        ])
        updates = {e.document_id: e for e in result.documents}

    # Rebuild the full documents list (last-write-wins channel).
    new_documents = []
    pending_ids = {d.document_id for d in pending}
    for d in state.documents:
        if d.document_id in pending_ids:
            ext = updates.get(d.document_id)
            if ext is not None:
                d.extracted_unit_value = ext.unit_value
                d.extracted_quantity = ext.quantity
                d.extracted_description = ext.description
                if ext.invoice_date is not None:
                    d.invoice_date = ext.invoice_date
            # Mark done whether or not extraction produced values, so a permanently
            # unreadable invoice is not retried forever.
            d.extraction_done = True
        new_documents.append(d)

    out = {"documents": new_documents}
    if load_errors:
        out["anomalies"] = load_errors
    return out


def valuation_agent_node(state: ClaimState) -> dict:
    llm = get_structured_llm(ValuationOutput)

    unpriced_items = [i for i in state.line_items if i.value_source == ValueSource.UNVALUED]
    if not unpriced_items:
        return {"line_items": state.line_items, "valuation_retries": state.valuation_retries}

    human_msg = VALUATION_HUMAN_PROMPT_TEMPLATE.format(
        line_items=json.dumps([i.model_dump() for i in unpriced_items], indent=2, default=str),
        documents=json.dumps([d.model_dump() for d in state.documents], indent=2, default=str)
    )
    result: ValuationOutput = invoke_structured(llm, [
        SystemMessage(content=VALUATION_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])

    valid_doc_ids = {d.document_id for d in state.documents}
    # Only items we actually sent this pass may be updated. On a retry we send
    # only the still-unpriced subset, but the model can echo back already-valued
    # items (often with nulls); applying those would wipe good valuations.
    requested_refs = {i.item_ref for i in unpriced_items}
    llm_updates = {item.item_ref: item for item in result.items}

    new_line_items = []
    for item in state.line_items:
        if item.item_ref in llm_updates and item.item_ref in requested_refs:
            llm_item = llm_updates[item.item_ref]

            # Map LLM outputs to the actual LineItem
            valid_matches = [did for did in llm_item.matched_document_ids if did in valid_doc_ids]
            item.matched_document_ids = valid_matches
            item.unit_value = llm_item.unit_value

            if valid_matches and item.unit_value is not None:
                item.value_source = ValueSource.INVOICE_MATCHED
                if item.depreciation_pct is None:
                    item.depreciation_pct = _depreciation_for(item.category)
                dep_pct = item.depreciation_pct or 0.0
                item.purchase_value = item.quantity * item.unit_value
                item.net_loss = item.purchase_value * (1.0 - dep_pct)
            else:
                item.value_source = ValueSource.UNVALUED
                item.matched_document_ids = []
                item.unit_value = None
                item.purchase_value = None
                item.net_loss = None

        new_line_items.append(item)

    unpriced_items_after = [i for i in new_line_items if i.value_source == ValueSource.UNVALUED]
    # valuation_retries counts ATTEMPTS made (see check_valuation for the cap).
    retries = state.valuation_retries
    if unpriced_items_after:
        retries += 1

    return {"line_items": new_line_items, "valuation_retries": retries}


# Max LLM attempts before falling back / shipping. The counters
# (valuation_retries, qc_retries) count ATTEMPTS made, incremented once per
# node run, so a value of N means N attempts have completed.
MAX_VALUATION_ATTEMPTS = 2
MAX_DRAFT_ATTEMPTS = 2


def check_valuation(state: ClaimState):
    unpriced = any(i.value_source == ValueSource.UNVALUED for i in state.line_items)
    if not unpriced:
        return "policy_agent"
    if state.valuation_retries >= MAX_VALUATION_ATTEMPTS:
        return "valuation_fallback"
    return "valuation_retry"


def valuation_fallback_node(state: ClaimState) -> dict:
    new_items = []
    for item in state.line_items:
        if item.value_source == ValueSource.UNVALUED:
            item.value_source = ValueSource.CATALOG_ESTIMATE
            item.unit_value = 0.0
            item.net_loss = 0.0
        new_items.append(item)
    return {"line_items": new_items}


DEFAULT_POLICY_CLAUSES = "Standard Flood Coverage applies."


def _clause_is_grounded(cited_clause: str, source_clauses: str, threshold: float = 0.6) -> bool:
    """True if `cited_clause` is plausibly drawn from `source_clauses`.

    An exact substring test is too brittle: models paraphrase, re-case, or
    trim whitespace, and `clauses` may be a list rendered via json.dumps. We
    instead require a majority of the cited clause's content words to appear
    in the source text (both normalized to lowercase alphanumeric tokens).
    """
    def tokens(s: str) -> set[str]:
        # \w with UNICODE so Devanagari/Tamil/etc. clauses tokenize at all.
        # The old [a-z0-9]+ returned an empty set for non-Latin scripts, which
        # both force-downgraded every item (English citation vs Tamil source)
        # and silently disabled the check (Tamil citation -> cited == set()).
        return {t.casefold() for t in re.findall(r"\w+", s or "", flags=re.UNICODE)}

    cited = tokens(cited_clause)
    if not cited:
        return True  # nothing substantive was cited — don't brand it a hallucination
    source = tokens(source_clauses)
    overlap = len(cited & source) / len(cited)
    return overlap >= threshold


def policy_agent_node(state: ClaimState) -> dict:
    llm = get_structured_llm(PolicyOutput)

    unreviewed = [i for i in state.line_items if i.policy_status is None]
    if not unreviewed:
        return {"line_items": state.line_items}

    # Resolve the clause text ONCE so the model and the grounding check see the
    # exact same source. `clauses` may be a string or a list; render either.
    raw_clauses = state.policy.get("clauses") or DEFAULT_POLICY_CLAUSES
    policy_clauses = raw_clauses if isinstance(raw_clauses, str) else json.dumps(raw_clauses, default=str)

    human_msg = POLICY_HUMAN_PROMPT_TEMPLATE.format(
        policy_clauses=policy_clauses,
        line_items=json.dumps([i.model_dump() for i in unreviewed], indent=2, default=str)
    )
    result: PolicyOutput = invoke_structured(llm, [
        SystemMessage(content=POLICY_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])

    requested_refs = {i.item_ref for i in unreviewed}
    llm_updates = {item.item_ref: item for item in result.items}

    new_line_items = []
    warnings = []
    for item in state.line_items:
        if item.item_ref in llm_updates and item.item_ref in requested_refs:
            llm_item = llm_updates[item.item_ref]

            item.policy_status = llm_item.policy_status
            item.policy_clause = llm_item.policy_clause
            item.policy_reasoning = llm_item.policy_reasoning

            if item.policy_clause and not _clause_is_grounded(item.policy_clause, policy_clauses):
                item.policy_status = PolicyStatus.REVIEW
                item.policy_reasoning = (
                    f"Cited clause could not be grounded in the policy text: "
                    f"'{item.policy_clause}'. Original reasoning: {item.policy_reasoning}"
                )
                warnings.append(
                    f"{item.item_ref}: policy_agent cited a clause not found in the policy "
                    f"('{item.policy_clause}'); downgraded to REVIEW."
                )

            # Coverage cap: an item can never be COVERED off terms nobody read
            # from a real document. When the gateway assumed the clauses (menu
            # uploaded instead of a policy, or nothing uploaded), the strongest
            # verdict allowed is REVIEW. This is the independent safety net that
            # makes the menu case fail even if triage somehow misses it.
            if state.policy.get("clauses_assumed") and item.policy_status == PolicyStatus.COVERED:
                item.policy_status = PolicyStatus.REVIEW
                item.policy_reasoning = (
                    "Coverage assessed against assumed generic terms — the policy schedule "
                    f"was not read. Original reasoning: {item.policy_reasoning}"
                )
                warnings.append(
                    f"{item.item_ref}: coverage downgraded to REVIEW; policy terms were "
                    "assumed, not read from a document."
                )

        new_line_items.append(item)

    out = {"line_items": new_line_items}
    if warnings:
        out["warnings"] = warnings
    return out


def check_missing_items(state: ClaimState):
    if len(state.pending_signals) > 0:
        return "reconciliation_agent"
    return "plausibility_check"


def reconciliation_agent_node(state: ClaimState) -> dict:
    llm = get_structured_llm(ReconciliationOutput)
    
    human_msg = RECONCILIATION_HUMAN_PROMPT_TEMPLATE.format(
        missing_signals=json.dumps([s.model_dump() for s in state.pending_signals], indent=2, default=str),
        documents=json.dumps([d.model_dump() for d in state.documents], indent=2, default=str)
    )
    result: ReconciliationOutput = invoke_structured(llm, [
        SystemMessage(content=RECONCILIATION_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    valid_doc_ids = {d.document_id for d in state.documents}
    final_pending_items = []
    
    for llm_item in result.pending_items:
        valid_docs = [did for did in llm_item.supporting_documents if did in valid_doc_ids]
        
        claimed_total = None
        if llm_item.quantity_claimed is not None and llm_item.unit_value_from_records is not None:
            claimed_total = llm_item.quantity_claimed * llm_item.unit_value_from_records
            
        final_pending_items.append(PendingVerificationItem(
            item_label=llm_item.item_label,
            quantity_claimed=llm_item.quantity_claimed,
            unit_value_from_records=llm_item.unit_value_from_records,
            claimed_total=claimed_total,
            user_notes=llm_item.user_notes,
            supporting_documents=valid_docs
        ))
            
    return {"pending_verification": final_pending_items}


# --- Plausibility Helpers ---

def _check_chronology(item: LineItem, event_date: datetime | None, documents: list[DocumentRecord]) -> str | None:
    # Fraud signal: a purchase/invoice DATED after the loss can't be evidence of
    # a pre-loss asset. Compare against the document's own date (invoice_date),
    # NOT uploaded_at — every document is uploaded after the loss, so comparing
    # upload time would reject every legitimately-matched item.
    if not event_date:
        return None
    for doc_id in item.matched_document_ids:
        doc = next((d for d in documents if d.document_id == doc_id), None)
        if doc and doc.invoice_date is not None:
            doc_time = doc.invoice_date
            if doc_time.tzinfo is None:
                doc_time = doc_time.replace(tzinfo=timezone.utc)
            if doc_time > event_date:
                return f"Document {doc_id} is dated after the event date."
    return None

def _check_invoice_quantity_ceiling(item: LineItem, documents: list[DocumentRecord]) -> None:
    if not item.matched_document_ids:
        return
        
    invoiced_quantity_total = 0.0
    for doc_id in item.matched_document_ids:
        doc = next((d for d in documents if d.document_id == doc_id), None)
        if doc and doc.extracted_quantity:
            invoiced_quantity_total += doc.extracted_quantity
            
    if invoiced_quantity_total > 0 and item.quantity > invoiced_quantity_total:
        if item.original_quantity_claimed is None:
            item.original_quantity_claimed = item.quantity
        item.quantity = invoiced_quantity_total
        item.quantity_capped = True
        
        if item.unit_value is not None:
            item.purchase_value = item.quantity * item.unit_value
            dep_pct = item.depreciation_pct or 0.0
            item.net_loss = item.purchase_value * (1 - dep_pct)
            
        item.plausibility_notes.append(
            f"Claimed quantity ({item.original_quantity_claimed}) exceeded invoiced quantity "
            f"({invoiced_quantity_total}) across matched documents. Capped to "
            f"{invoiced_quantity_total}; excess not included in claimed value."
        )

def _check_missing_evidence(item: LineItem) -> str | None:
    if not item.evidence_refs and item.value_source == ValueSource.UNVALUED:
        return "Missing evidence AND no value source."
    return None

def _check_duplicate_hash(item: LineItem, state: ClaimState, mock_hash_registry: dict) -> str | None:
    # Only a CROSS-claim hash collision indicates fraud (the same photo file
    # reused across two different claims). We deliberately do NOT flag two
    # line items in THIS claim that share an evidence photo: vision routinely
    # emits several items from one scene/zone shot, so they legitimately carry
    # the same evidence_ref (and therefore the same sha256).
    for ref in item.evidence_refs:
        e = next((ev for ev in state.evidence if ev.evidence_id == ref), None)
        if not e:
            continue

        if e.sha256 in mock_hash_registry:
            return f"Duplicate hash {e.sha256} matched against {mock_hash_registry[e.sha256]}."
    return None

def _check_duplicate_serial(item: LineItem, mock_serial_registry: dict) -> str | None:
    if item.serial_number and item.serial_number in mock_serial_registry:
        prior = mock_serial_registry[item.serial_number]
        return f"Duplicate serial {item.serial_number} matched against {prior}."
    return None

def _sum_insured_key(category: str) -> str:
    cat = (category or "").lower()
    if "furniture" in cat or "fff" in cat:
        return "sum_insured_fff"
    if "machinery" in cat or "plant" in cat:
        return "sum_insured_machinery"
    if "stock" in cat:
        return "sum_insured_stock"
    return f"sum_insured_{cat}"


def _check_sum_insured_ceiling(items: list[LineItem], policy: dict) -> list[str]:
    # Operate on the KEPT items (post-rejection), not the full pre-filter list —
    # otherwise rejected items inflate the category totals against the ceiling.
    warnings = []
    totals = {}
    for item in items:
        if item.net_loss:
            totals[item.category] = totals.get(item.category, 0.0) + item.net_loss

    for cat, total in totals.items():
        limit = policy.get(_sum_insured_key(cat))
        if limit is not None and total > limit:
            warnings.append(f"Category '{cat}' total net loss ({total}) exceeds sum insured ({limit}).")

    return warnings


def plausibility_check_node(state: ClaimState) -> dict:
    mock_serial_registry = {"SN-RP4471": "Claim #C-88210"}
    mock_hash_registry = {"mock_duplicate_cross_claim_hash": "Claim #C-99999"}
    
    event_date = _parse_iso_datetime(state.event.get("event_date"))

    kept_items = []
    rejected = []

    for item in state.line_items:
        _check_invoice_quantity_ceiling(item, state.documents)

        reasons = []

        chrono_err = _check_chronology(item, event_date, state.documents)
        if chrono_err: reasons.append(chrono_err)

        missing_ev_err = _check_missing_evidence(item)
        if missing_ev_err: reasons.append(missing_ev_err)

        hash_err = _check_duplicate_hash(item, state, mock_hash_registry)
        if hash_err: reasons.append(hash_err)
            
        serial_err = _check_duplicate_serial(item, mock_serial_registry)
        if serial_err: reasons.append(serial_err)
            
        if reasons:
            rej = RejectedItem(
                item_ref=item.item_ref,
                line_item_snapshot=item.model_copy(deep=True),
                reasons=reasons,
                evidence_refs=item.evidence_refs
            )
            rejected.append(rej)
        else:
            kept_items.append(item)
            
    warnings = _check_sum_insured_ceiling(kept_items, state.policy)

    return {"line_items": kept_items, "rejected_items": rejected, "warnings": warnings}


def reserve_estimate_node(state: ClaimState) -> dict:
    confirmed = sum(i.net_loss or 0 for i in state.line_items if i.policy_status == PolicyStatus.COVERED)
    conditional = sum(i.net_loss or 0 for i in state.line_items if i.policy_status == PolicyStatus.REVIEW)
    excluded = sum(i.net_loss or 0 for i in state.line_items if i.policy_status == PolicyStatus.EXCLUDED)
    unclassified = sum(i.net_loss or 0 for i in state.line_items if i.policy_status is None)
    pending = sum(i.claimed_total or 0 for i in state.pending_verification)
    screened_out = sum(i.line_item_snapshot.net_loss or 0 for i in state.rejected_items)
    
    return {
        "reserve_estimate": {
            "confirmed": confirmed,
            "conditional": conditional,
            "excluded": excluded,
            "unclassified": unclassified,
            "pending": pending,
            "screened_out": screened_out
        }
    }


def drafter_node(state: ClaimState) -> dict:
    llm = get_structured_llm(DraftOutput)
    
    human_msg = DRAFTER_HUMAN_PROMPT_TEMPLATE.format(
        line_items=json.dumps([i.model_dump() for i in state.line_items], indent=2, default=str),
        rejected_items=json.dumps([r.model_dump() for r in state.rejected_items], indent=2, default=str),
        pending_verification=json.dumps([p.model_dump() for p in state.pending_verification], indent=2, default=str),
        event=json.dumps(state.event, indent=2, default=str),
        policy=json.dumps(state.policy, indent=2, default=str),
        qc_flags=json.dumps(state.qc.flags if state.qc else [], indent=2, default=str),
        previous_draft_pack=json.dumps(state.draft_pack.model_dump() if state.draft_pack else {}, indent=2, default=str)
    )
    result: DraftOutput = invoke_structured(llm, [
        SystemMessage(content=DRAFTER_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    return {"draft_pack": result, "qc_retries": state.qc_retries + 1}


def _qc_section_mismatch(section_name: str, text: str, expected_refs: set[str]) -> str | None:
    found = set(re.findall(r"LI-\d+", text or ""))
    if found == expected_refs:
        return None
    missing = sorted(expected_refs - found)
    extra = sorted(found - expected_refs)
    return (
        f"{section_name} item refs do not match the source data. "
        f"Missing: {missing or 'none'}. Unexpected: {extra or 'none'}. "
        f"Include exactly one row per item, each prefixed with its item_ref, "
        f"and do not mention any other item's ref in this section."
    )


def qc_guardian_node(state: ClaimState) -> dict:
    if state.draft_pack:
        # Pre-LLM deterministic checks. Compare the SET of item refs the draft
        # cites against the set it should cite, so the flag names exactly which
        # refs are missing or extra — a bare count gives the drafter nothing to
        # act on, and counts can coincidentally match while the refs are wrong.
        # Each policy_status routes to exactly one section, so every item has a
        # home and nothing silently vanishes.
        expected_valid = {i.item_ref for i in state.line_items if i.policy_status in (PolicyStatus.COVERED, PolicyStatus.REVIEW)}
        expected_excluded = {i.item_ref for i in state.line_items if i.policy_status == PolicyStatus.EXCLUDED}
        expected_rejected = {r.item_ref for r in state.rejected_items}

        dp = state.draft_pack
        for section_name, text, expected in [
            ("main_schedule", dp.main_schedule, expected_valid),
            ("rejected_items_annexure", dp.rejected_items_annexure, expected_rejected),
            ("excluded_items_annexure", dp.excluded_items_annexure, expected_excluded),
        ]:
            flag = _qc_section_mismatch(section_name, text, expected)
            if flag:
                return {"qc": QCGuardOutput(pass_qc=False, flags=[flag])}

    llm = get_structured_llm(QCGuardOutput)
    
    human_msg = QC_GUARDIAN_HUMAN_PROMPT_TEMPLATE.format(
        draft_pack=json.dumps(state.draft_pack.model_dump() if state.draft_pack else {}, indent=2, default=str),
        line_items=json.dumps([i.model_dump() for i in state.line_items], indent=2, default=str),
        rejected_items=json.dumps([r.model_dump() for r in state.rejected_items], indent=2, default=str),
        pending_verification=json.dumps([p.model_dump() for p in state.pending_verification], indent=2, default=str)
    )
    result: QCGuardOutput = invoke_structured(llm, [
        SystemMessage(content=QC_GUARDIAN_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    return {"qc": result}


def check_qc(state: ClaimState):
    if state.qc and state.qc.pass_qc:
        return "send"
    if state.qc_retries >= MAX_DRAFT_ATTEMPTS:
        # Out of redraft attempts. We still ship (a human reviews downstream)
        # but send_node records the unresolved flags into warnings so the pack
        # is never silently treated as clean.
        return "send"
    return "drafter"


def send_node(state: ClaimState) -> dict:
    out = {}
    # If we are shipping a pack that never passed QC, surface the unresolved
    # flags loudly rather than letting a known-bad pack look clean.
    if state.qc and not state.qc.pass_qc and state.qc.flags:
        out["warnings"] = [f"Shipped without passing QC: {flag}" for flag in state.qc.flags]

    if state.draft_pack:
        os.makedirs("./out", exist_ok=True)
        pack_str = json.dumps(state.draft_pack.model_dump(), sort_keys=True, default=str)
        h = hashlib.sha256(pack_str.encode("utf-8")).hexdigest()
        with open(f"./out/claim_pack_{h}.json", "w") as f:
            f.write(pack_str)
    return out


def proof_of_intimation_node(state: ClaimState) -> dict:
    pack_str = json.dumps(state.draft_pack.model_dump() if state.draft_pack else {}, sort_keys=True, default=str)
    h = hashlib.sha256(pack_str.encode("utf-8")).hexdigest()
    receipt = {
        "receipt_hash": h,
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "recipient": "Insurer Intake API"
    }
    return {"proof_receipt": receipt}


graph_builder = StateGraph(ClaimState)

graph_builder.add_node("document_triage", document_triage_node)
graph_builder.add_node("policy_extract", policy_extract_node)
graph_builder.add_node("intake", intake_node)
graph_builder.add_node("intake_rejected", intake_rejected_node)
graph_builder.add_node("evidence_verify", evidence_verify_node)
graph_builder.add_node("vision", vision_node)
graph_builder.add_node("document_extract", document_extract_node)
graph_builder.add_node("valuation_agent", valuation_agent_node)
graph_builder.add_node("valuation_fallback", valuation_fallback_node)
graph_builder.add_node("policy_agent", policy_agent_node)
graph_builder.add_node("reconciliation_agent", reconciliation_agent_node)
graph_builder.add_node("plausibility_check", plausibility_check_node)
graph_builder.add_node("reserve_estimate", reserve_estimate_node)
graph_builder.add_node("drafter", drafter_node)
graph_builder.add_node("qc_guardian", qc_guardian_node)
graph_builder.add_node("send", send_node)
graph_builder.add_node("proof_of_intimation", proof_of_intimation_node)

graph_builder.add_edge(START, "document_triage")
graph_builder.add_edge("document_triage", "policy_extract")
graph_builder.add_edge("policy_extract", "intake")
graph_builder.add_conditional_edges("intake", check_intake, {
    "proceed": "evidence_verify",
    "intake_rejected": "intake_rejected",
})
graph_builder.add_edge("intake_rejected", END)
graph_builder.add_edge("evidence_verify", "vision")
graph_builder.add_edge("vision", "document_extract")
graph_builder.add_edge("document_extract", "valuation_agent")

graph_builder.add_conditional_edges("valuation_agent", check_valuation, {
    "policy_agent": "policy_agent",
    "valuation_fallback": "valuation_fallback",
    "valuation_retry": "valuation_agent"
})
graph_builder.add_edge("valuation_fallback", "policy_agent")

graph_builder.add_conditional_edges("policy_agent", check_missing_items, {
    "reconciliation_agent": "reconciliation_agent",
    "plausibility_check": "plausibility_check"
})
graph_builder.add_edge("reconciliation_agent", "plausibility_check")
graph_builder.add_edge("plausibility_check", "reserve_estimate")
graph_builder.add_edge("reserve_estimate", "drafter")
graph_builder.add_edge("drafter", "qc_guardian")

graph_builder.add_conditional_edges("qc_guardian", check_qc, {
    "send": "send",
    "drafter": "drafter"
})
graph_builder.add_edge("send", "proof_of_intimation")
graph_builder.add_edge("proof_of_intimation", END)

graph = graph_builder.compile()