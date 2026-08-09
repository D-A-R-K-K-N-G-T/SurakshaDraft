"""
Node function implementations and the wiring that connects them into the
LangGraph StateGraph — kept in one file per the project's flat structure
(no separate nodes/ package).
"""
from __future__ import annotations

import json
import hashlib
import os
import math
import re
from datetime import datetime, timezone
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from agentic_pipeline.llm import get_structured_llm, invoke_structured
from agentic_pipeline.prompts import (
    VISION_HUMAN_PROMPT_TEMPLATE, VISION_SYSTEM_PROMPT,
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
    DraftOutput, QCGuardOutput, DocumentRecord
)
from agentic_pipeline.state import ClaimState
from agentic_pipeline.images import build_image_block, load_image_as_data_url
from agentic_pipeline.config import settings


def intake_node(state: ClaimState) -> dict:
    intake_ok = True
    
    event_date_str = state.event.get("event_date", "")
    policy_start_str = state.policy.get("start_date")
    policy_end_str = state.policy.get("end_date")
    
    try:
        event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=timezone.utc)
            
        if policy_start_str and policy_end_str:
            p_start = datetime.fromisoformat(policy_start_str.replace("Z", "+00:00"))
            if p_start.tzinfo is None: p_start = p_start.replace(tzinfo=timezone.utc)
            
            p_end = datetime.fromisoformat(policy_end_str.replace("Z", "+00:00"))
            if p_end.tzinfo is None: p_end = p_end.replace(tzinfo=timezone.utc)
            
            if not (p_start <= event_date <= p_end):
                intake_ok = False
    except ValueError:
        pass
        
    has_receipt = any(d.document_type == "PremiumReceipt" for d in state.documents)
    if not has_receipt:
        intake_ok = False
        
    return {"intake_ok": intake_ok}


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
    event_date_str = state.event.get("event_date", "")
    try:
        event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=timezone.utc)
    except ValueError:
        event_date = None
        
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
    for e in new_evidence:
        content.append({
            "type": "text",
            "text": f"[evidence_id={e.evidence_id}, capture_stage={e.capture_stage.value}]",
        })
        content.append(build_image_block(load_image_as_data_url(e.file_ref)))

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
            evidence_refs=item.evidence_refs,
            vision_confidence=item.vision_confidence,
        )
        for i, item in enumerate(result.items)
    ]

    return {
        "line_items": state.line_items + new_line_items,
        "anomalies": result.anomalies,
        "pending_signals": result.missing_signals,
        "vision_processed_evidence_ids": [e.evidence_id for e in new_evidence],
    }


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
    llm_updates = {item.item_ref: item for item in result.items}
    
    new_line_items = []
    for item in state.line_items:
        if item.item_ref in llm_updates:
            llm_item = llm_updates[item.item_ref]
            
            # Map LLM outputs to the actual LineItem
            valid_matches = [did for did in llm_item.matched_document_ids if did in valid_doc_ids]
            item.matched_document_ids = valid_matches
            item.unit_value = llm_item.unit_value
            
            if valid_matches and item.unit_value is not None:
                item.value_source = ValueSource.INVOICE_MATCHED
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
    retries = state.valuation_retries
    if unpriced_items_after:
        retries += 1
            
    return {"line_items": new_line_items, "valuation_retries": retries}


def check_valuation(state: ClaimState):
    unpriced = any(i.value_source == ValueSource.UNVALUED for i in state.line_items)
    if not unpriced:
        return "policy_agent"
    if state.valuation_retries >= 2:
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


def policy_agent_node(state: ClaimState) -> dict:
    llm = get_structured_llm(PolicyOutput)
    
    unreviewed = [i for i in state.line_items if i.policy_status is None]
    if not unreviewed:
        return {"line_items": state.line_items}
        
    human_msg = POLICY_HUMAN_PROMPT_TEMPLATE.format(
        policy_clauses=state.policy.get("clauses", "Standard Flood Coverage applies."),
        line_items=json.dumps([i.model_dump() for i in unreviewed], indent=2, default=str)
    )
    result: PolicyOutput = invoke_structured(llm, [
        SystemMessage(content=POLICY_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    policy_clauses = state.policy.get("clauses", "")
    llm_updates = {item.item_ref: item for item in result.items}
    
    new_line_items = []
    for item in state.line_items:
        if item.item_ref in llm_updates:
            llm_item = llm_updates[item.item_ref]
            
            item.policy_status = llm_item.policy_status
            item.policy_clause = llm_item.policy_clause
            item.policy_reasoning = llm_item.policy_reasoning
            
            if item.policy_clause and item.policy_clause not in policy_clauses:
                item.policy_status = PolicyStatus.REVIEW
                item.policy_reasoning = f"Hallucinated clause cited: {item.policy_clause}. Original reasoning: {item.policy_reasoning}"
                
        new_line_items.append(item)
            
    return {"line_items": new_line_items}


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
    if not event_date:
        return None
    for doc_id in item.matched_document_ids:
        doc = next((d for d in documents if d.document_id == doc_id), None)
        if doc:
            doc_time = doc.uploaded_at
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

def _check_duplicate_hash(item: LineItem, state: ClaimState, mock_hash_registry: dict, seen_hashes: set) -> str | None:
    for ref in item.evidence_refs:
        e = next((ev for ev in state.evidence if ev.evidence_id == ref), None)
        if not e:
            continue
            
        if e.sha256 in mock_hash_registry:
            return f"Duplicate hash {e.sha256} matched against {mock_hash_registry[e.sha256]}."
            
        if e.sha256 in seen_hashes:
            return f"Duplicate hash {e.sha256} matched within this claim."
        seen_hashes.add(e.sha256)
    return None

def _check_duplicate_serial(item: LineItem, mock_serial_registry: dict) -> str | None:
    if item.serial_number and item.serial_number in mock_serial_registry:
        prior = mock_serial_registry[item.serial_number]
        return f"Duplicate serial {item.serial_number} matched against {prior}."
    return None

def _check_sum_insured_ceiling(state: ClaimState) -> list[str]:
    warnings = []
    totals = {}
    for item in state.line_items:
        if item.net_loss:
            totals[item.category] = totals.get(item.category, 0.0) + item.net_loss
            
    for cat, total in totals.items():
        key = f"sum_insured_{cat.lower()}"
        if "furniture" in cat.lower() or "fff" in cat.lower():
            key = "sum_insured_fff"
        elif "machinery" in cat.lower():
            key = "sum_insured_machinery"
            
        limit = state.policy.get(key)
        if limit is not None and total > limit:
            warnings.append(f"Category '{cat}' total net loss ({total}) exceeds sum insured ({limit}).")
            
    return warnings


def plausibility_check_node(state: ClaimState) -> dict:
    mock_serial_registry = {"SN-RP4471": "Claim #C-88210"}
    mock_hash_registry = {"mock_duplicate_cross_claim_hash": "Claim #C-99999"}
    
    event_date_str = state.event.get("event_date", "")
    try:
        event_date = datetime.fromisoformat(event_date_str.replace("Z", "+00:00"))
        if event_date.tzinfo is None:
            event_date = event_date.replace(tzinfo=timezone.utc)
    except ValueError:
        event_date = None

    kept_items = []
    rejected = []
    seen_hashes = set()
    
    for item in state.line_items:
        _check_invoice_quantity_ceiling(item, state.documents)
        
        reasons = []
        
        chrono_err = _check_chronology(item, event_date, state.documents)
        if chrono_err: reasons.append(chrono_err)
            
        missing_ev_err = _check_missing_evidence(item)
        if missing_ev_err: reasons.append(missing_ev_err)
            
        hash_err = _check_duplicate_hash(item, state, mock_hash_registry, seen_hashes)
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
            
    warnings = _check_sum_insured_ceiling(state)
            
    return {"line_items": kept_items, "rejected_items": rejected, "warnings": warnings}


def reserve_estimate_node(state: ClaimState) -> dict:
    confirmed = sum(i.net_loss or 0 for i in state.line_items if i.policy_status == PolicyStatus.COVERED)
    conditional = sum(i.net_loss or 0 for i in state.line_items if i.policy_status == PolicyStatus.REVIEW)
    pending = sum(i.claimed_total or 0 for i in state.pending_verification)
    screened_out = sum(i.line_item_snapshot.net_loss or 0 for i in state.rejected_items)
    
    return {
        "reserve_estimate": {
            "confirmed": confirmed,
            "conditional": conditional,
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


def qc_guardian_node(state: ClaimState) -> dict:
    if state.draft_pack:
        # Pre-LLM deterministic checks
        valid_items_count = len([i for i in state.line_items if i.policy_status in (PolicyStatus.COVERED, PolicyStatus.REVIEW)])
        schedule_refs = len(set(re.findall(r"LI-\d+", state.draft_pack.main_schedule)))
        if schedule_refs != valid_items_count:
            return {"qc": QCGuardOutput(pass_qc=False, flags=[f"Deterministic check failed: main_schedule contains {schedule_refs} items but state has {valid_items_count} valid items."])}
            
        rej_count = len(state.rejected_items)
        rej_refs = len(set(re.findall(r"LI-\d+", state.draft_pack.rejected_items_annexure)))
        if rej_refs != rej_count:
            return {"qc": QCGuardOutput(pass_qc=False, flags=[f"Deterministic check failed: rejected_items_annexure contains {rej_refs} items but state has {rej_count} rejected items."])}

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
    if state.qc_retries >= 2:
        return "send"
    return "drafter"


def send_node(state: ClaimState) -> dict:
    if state.draft_pack:
        os.makedirs("./out", exist_ok=True)
        pack_str = json.dumps(state.draft_pack.model_dump(), sort_keys=True, default=str)
        h = hashlib.sha256(pack_str.encode("utf-8")).hexdigest()
        with open(f"./out/claim_pack_{h}.json", "w") as f:
            f.write(pack_str)
    return {}


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

graph_builder.add_node("intake", intake_node)
graph_builder.add_node("evidence_verify", evidence_verify_node)
graph_builder.add_node("vision", vision_node)
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

graph_builder.add_edge(START, "intake")
graph_builder.add_edge("intake", "evidence_verify")
graph_builder.add_edge("evidence_verify", "vision")
graph_builder.add_edge("vision", "valuation_agent")

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