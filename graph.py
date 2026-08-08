"""
Node function implementations and the wiring that connects them into the
LangGraph StateGraph — kept in one file per the project's flat structure
(no separate nodes/ package).
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import StateGraph, START, END

from llm import get_structured_llm, invoke_structured
from prompts import (
    VISION_HUMAN_PROMPT_TEMPLATE, VISION_SYSTEM_PROMPT,
    VALUATION_SYSTEM_PROMPT, VALUATION_HUMAN_PROMPT_TEMPLATE,
    POLICY_SYSTEM_PROMPT, POLICY_HUMAN_PROMPT_TEMPLATE,
    RECONCILIATION_SYSTEM_PROMPT, RECONCILIATION_HUMAN_PROMPT_TEMPLATE,
    DRAFTER_SYSTEM_PROMPT, DRAFTER_HUMAN_PROMPT_TEMPLATE,
    QC_GUARDIAN_SYSTEM_PROMPT, QC_GUARDIAN_HUMAN_PROMPT_TEMPLATE
)
from schemas import (
    LineItem, VisionOutput, ValueSource, PolicyStatus, 
    PendingVerificationItem, RejectedItem, ValuationOutput, 
    PolicyOutput, ReconciliationOutput, PlausibilityOutput, 
    DraftOutput, QCGuardOutput
)
from state import ClaimState
from images import build_image_block, load_image_as_data_url


def intake_node(state: ClaimState) -> dict:
    # Phase A: Pure deterministic validation.
    # In a real app this would parse dates and check premium receipts.
    return {}


def evidence_verify_node(state: ClaimState) -> dict:
    # Phase C: Hash comparison, geofence, timestamps.
    event_date_str = state.event.get("event_date", "")
    for e in state.evidence:
        if e.verified is None:
            e.verified = True
            e.verification_reasons.append(f"Mock: passed geofence and time window relative to {event_date_str}")
    return {}


def vision_node(state: ClaimState) -> dict:
    """
    Phase D. Turns verified, not-yet-processed photos into structured,
    unvalued LineItems (+ missing_signals for reconciliation_agent to
    pick up later, + anomalies).
    """
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
            quantity=item.quantity,
            evidence_refs=item.evidence_refs,
            vision_confidence=item.vision_confidence,
        )
        for i, item in enumerate(result.items)
    ]

    return {
        "line_items": new_line_items,
        "anomalies": result.anomalies,
        "pending_signals": result.missing_signals,
        "vision_processed_evidence_ids": [e.evidence_id for e in new_evidence],
    }


def valuation_agent_node(state: ClaimState) -> dict:
    llm = get_structured_llm(ValuationOutput)
    
    unpriced_items = [i for i in state.line_items if i.value_source == ValueSource.UNVALUED]
    if not unpriced_items:
        return {"line_items": state.line_items, "valuation_retries": state.valuation_retries + 1}
        
    human_msg = VALUATION_HUMAN_PROMPT_TEMPLATE.format(
        line_items=[i.model_dump() for i in unpriced_items],
        documents=[d.model_dump() for d in state.documents]
    )
    result: ValuationOutput = invoke_structured(llm, [
        SystemMessage(content=VALUATION_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    updated_items = {item.item_ref: item for item in result.items}
    new_line_items = []
    for item in state.line_items:
        if item.item_ref in updated_items:
            new_line_items.append(updated_items[item.item_ref])
        else:
            new_line_items.append(item)
            
    return {"line_items": new_line_items, "valuation_retries": state.valuation_retries + 1}


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
        line_items=[i.model_dump() for i in unreviewed]
    )
    result: PolicyOutput = invoke_structured(llm, [
        SystemMessage(content=POLICY_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    updated_items = {item.item_ref: item for item in result.items}
    new_line_items = []
    for item in state.line_items:
        if item.item_ref in updated_items:
            new_line_items.append(updated_items[item.item_ref])
        else:
            new_line_items.append(item)
            
    return {"line_items": new_line_items}


def check_missing_items(state: ClaimState):
    has_missing = any(not item.evidence_refs for item in state.line_items) or len(state.pending_signals) > 0
    if has_missing:
        return "reconciliation_agent"
    return "plausibility_check"


def reconciliation_agent_node(state: ClaimState) -> dict:
    llm = get_structured_llm(ReconciliationOutput)
    
    human_msg = RECONCILIATION_HUMAN_PROMPT_TEMPLATE.format(
        missing_signals=[s.model_dump() for s in state.pending_signals],
        documents=[d.model_dump() for d in state.documents]
    )
    result: ReconciliationOutput = invoke_structured(llm, [
        SystemMessage(content=RECONCILIATION_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    return {"pending_verification": result.pending_items}


def plausibility_check_node(state: ClaimState) -> dict:
    mock_registry = {"SN-RP4471": "Claim #C-88210"}
    
    kept_items = []
    rejected = []
    
    for item in state.line_items:
        is_duplicate = False
        for bad_serial, prior_claim in mock_registry.items():
            if item.name and bad_serial in item.name or (item.description and bad_serial in item.description):
                rej = RejectedItem(
                    item_ref=item.item_ref,
                    line_item_snapshot=item,
                    reasons=[f"Duplicate serial {bad_serial} matched against {prior_claim}"],
                    evidence_refs=item.evidence_refs
                )
                rejected.append(rej)
                is_duplicate = True
                break
        if not is_duplicate:
            kept_items.append(item)
            
    return {"line_items": kept_items, "rejected_items": rejected}


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
        line_items=[i.model_dump() for i in state.line_items],
        rejected_items=[r.model_dump() for r in state.rejected_items],
        pending_verification=[p.model_dump() for p in state.pending_verification],
        event=state.event,
        policy=state.policy,
        qc_flags=state.qc.flags if state.qc else []
    )
    result: DraftOutput = invoke_structured(llm, [
        SystemMessage(content=DRAFTER_SYSTEM_PROMPT),
        HumanMessage(content=human_msg)
    ])
    
    return {"draft_pack": result, "qc_retries": state.qc_retries + 1}


def qc_guardian_node(state: ClaimState) -> dict:
    llm = get_structured_llm(QCGuardOutput)
    
    human_msg = QC_GUARDIAN_HUMAN_PROMPT_TEMPLATE.format(
        draft_pack=state.draft_pack.model_dump() if state.draft_pack else {},
        line_items=[i.model_dump() for i in state.line_items],
        rejected_items=[r.model_dump() for r in state.rejected_items],
        pending_verification=[p.model_dump() for p in state.pending_verification]
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
    return {}


def proof_of_intimation_node(state: ClaimState) -> dict:
    pack_str = json.dumps(state.draft_pack.model_dump() if state.draft_pack else {}, sort_keys=True)
    h = hashlib.sha256(pack_str.encode("utf-8")).hexdigest()
    receipt = {
        "receipt_hash": h,
        "sent_at": datetime.utcnow().isoformat(),
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