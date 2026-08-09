import os
from dotenv import load_dotenv
load_dotenv(".env")

import logging
import traceback
import uuid
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_pipeline.schemas import (
    VisionCandidateItem, VisionOutput, DocumentRecord, LORPack,
)
from agentic_pipeline.state import ClaimState
from agentic_pipeline.graph import graph
from agentic_pipeline.llm import get_structured_llm, invoke_structured
from agentic_pipeline.prompts import VISION_SYSTEM_PROMPT, VISION_HUMAN_PROMPT_TEMPLATE
from agentic_pipeline.images import build_image_block
from agentic_pipeline import requirements as reqs

app = FastAPI(title="SurakshaDraft Agent API")
_log = logging.getLogger("agentic_pipeline.service")

@app.get("/")
def read_root():
    return {"status": "online", "service": "SurakshaDraft LangGraph Pipeline API"}

class VisionPreviewRequest(BaseModel):
    image_base64: str
    mime_type: str = "image/jpeg"
    capture_stage: str = "item"
    declared_asset_categories: List[str] = Field(default_factory=list)

class ClaimSubmitResponse(BaseModel):
    claim_id: str
    status: str
    # Revision 1 of the Letter of Requirement, computed synchronously below so
    # the claimant sees a checklist immediately rather than after the pipeline.
    lor: Optional[LORPack] = None

class VisionPreviewResponse(BaseModel):
    items: List[VisionCandidateItem]
    anomalies: List[str] = Field(default_factory=list)

class AddDocumentsRequest(BaseModel):
    documents: List[DocumentRecord]

class ClaimTypeOverrideRequest(BaseModel):
    claim_type_id: str

# In-memory store
_CLAIMS_DB: dict[str, dict] = {}


# Claim statuses. `awaiting_documents` is terminal for this run but NOT final:
# the claim resumes when the missing documents arrive.
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_AWAITING = "awaiting_documents"
STATUS_FAILED = "failed"


@app.post("/api/v1/vision/preview", response_model=VisionPreviewResponse)
def vision_preview(req: VisionPreviewRequest):
    # Construct content explicitly for this single image
    categories = req.declared_asset_categories or ["Stock", "Furniture, Fixtures & Fittings", "Plant & Machinery"]

    content = [{
        "type": "text",
        "text": VISION_HUMAN_PROMPT_TEMPLATE.format(
            asset_categories=", ".join(categories)
        ),
    }]

    content.append({
        "type": "text",
        "text": f"[evidence_id=PREVIEW, capture_stage={req.capture_stage}]",
    })

    data_url = f"data:{req.mime_type};base64,{req.image_base64}"
    content.append(build_image_block(data_url))

    llm = get_structured_llm(VisionOutput, want_vision=True)
    try:
        result: VisionOutput = invoke_structured(llm, [
            SystemMessage(content=VISION_SYSTEM_PROMPT),
            HumanMessage(content=content),
        ])
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    if not result.items:
        # No items — surface anomalies so the client can show why.
        raise HTTPException(status_code=422, detail={"anomalies": result.anomalies})

    # Return ALL identified items for the user to confirm/edit. evidence_refs is
    # not meaningful at preview time (evidence ids are assigned at submit).
    for item in result.items:
        item.evidence_refs = []
    return VisionPreviewResponse(items=result.items, anomalies=result.anomalies)


def _safe_universal_lor(state: ClaimState) -> Optional[LORPack]:
    """The instant checklist returned in the submit response.

    Universal requirements only: the claim type has not been inferred yet, and
    matching runs off the slots the claimant declared rather than off triage.
    Pure Python and must never raise — a checklist is a nicety, and failing to
    build one must not cost someone their claim submission.
    """
    try:
        return reqs.build_lor(
            policy=state.policy,
            event=state.event,
            documents=state.documents,
            evidence=state.evidence,
            basis=reqs.BASIS_UNIVERSAL,
            revision=1,
        )
    except Exception:
        _log.exception("Could not build the provisional LOR")
        return None


def _run_claim_pipeline(claim_id: str, state: ClaimState):
    existing = _CLAIMS_DB.get(claim_id) or {}
    try:
        final_state = graph.invoke(state.model_dump())
        # Normalise to plain JSON before storing. LangGraph hands back live
        # Pydantic objects inside the state dict; the resume path reads this
        # store as data (documents[i]["document_id"]), so keeping objects here
        # would break it, and mode="json" also gives the app clean ISO dates.
        stored = ClaimState.model_validate(final_state).model_dump(mode="json")
        status = STATUS_AWAITING if stored.get("awaiting_documents") else STATUS_COMPLETED
        _CLAIMS_DB[claim_id] = {"status": status, "state": stored, "lor": stored.get("lor")}
    except Exception as e:
        _log.exception("Claim pipeline failed for %s", claim_id)
        _CLAIMS_DB[claim_id] = {
            "status": STATUS_FAILED,
            "error": str(e),
            "traceback": traceback.format_exc(),
            # Keep whatever we had so a failed re-run doesn't erase the claim.
            "state": existing.get("state"),
        }


@app.post("/api/v1/claim/submit", response_model=ClaimSubmitResponse)
def submit_claim(state: ClaimState, background_tasks: BackgroundTasks):
    claim_id = f"CLM-{uuid.uuid4().hex[:8].upper()}"
    lor = _safe_universal_lor(state)
    state.lor = lor
    _CLAIMS_DB[claim_id] = {"status": STATUS_PROCESSING, "lor": lor}
    background_tasks.add_task(_run_claim_pipeline, claim_id, state)
    return ClaimSubmitResponse(claim_id=claim_id, status=STATUS_PROCESSING, lor=lor)


@app.get("/api/v1/claim/{claim_id}")
def get_claim(claim_id: str):
    if claim_id not in _CLAIMS_DB:
        raise HTTPException(status_code=404, detail="Claim not found")
    return _CLAIMS_DB[claim_id]


# Everything a run DERIVES from the inputs. Re-invoking the graph on a stored
# state would otherwise append to the Annotated[..., operator.add] channels
# (warnings, anomalies, rejected_items, pending_verification), duplicating every
# entry on each resume. Reset these; preserve everything else.
#
# Deliberately PRESERVED and absent from this dict:
#   documents  — classification_done makes triage skip already-verified files
#   line_items — the user's confirmed items from the preview screen
#   claim_type* — a user_override must survive every subsequent re-run
#   vision_processed_evidence_ids — a dedupe ledger; clearing it would re-run
#                                   vision over every photo again
#   evidence, policy, event, gateway_blocking_reasons — the original inputs
_RESET_ON_RESUME = {
    "warnings": [],
    "anomalies": [],
    "rejected_items": [],
    "pending_verification": [],
    "intake_ok": False,
    "intake_reasons": [],
    "doc_gate_reasons": [],
    "draft_pack": None,
    "qc": None,
    "proof_receipt": None,
    "reserve_estimate": None,
    "valuation_retries": 0,
    "qc_retries": 0,
    "awaiting_documents": False,
}


def _stored_state(claim_id: str) -> dict:
    entry = _CLAIMS_DB.get(claim_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Claim not found")
    state = entry.get("state")
    if not state:
        raise HTTPException(
            status_code=409,
            detail="This claim is still being processed; try again in a moment.",
        )
    return state


@app.post("/api/v1/claim/{claim_id}/documents", response_model=ClaimSubmitResponse)
def add_documents(claim_id: str, req: AddDocumentsRequest, background_tasks: BackgroundTasks):
    """Attach documents to an existing claim and re-run the pipeline.

    New files carry classification_done=False, so document_triage_node's existing
    guard means only the new uploads cost an LLM call. No LangGraph checkpointer
    is involved: every expensive node already skips work it has done, so
    re-invoking from START is both simpler and nearly free.
    """
    if not req.documents:
        raise HTTPException(status_code=400, detail="No documents supplied.")

    state = dict(_stored_state(claim_id))
    existing_ids = {d.get("document_id") for d in (state.get("documents") or [])}
    incoming = [d for d in req.documents if d.document_id not in existing_ids]
    if not incoming:
        raise HTTPException(status_code=409, detail="These documents are already on the claim.")

    state["documents"] = (state.get("documents") or []) + [
        d.model_dump() for d in incoming
    ]
    state.update(_RESET_ON_RESUME)

    claim_state = ClaimState.model_validate(state)
    _CLAIMS_DB[claim_id] = {
        "status": STATUS_PROCESSING,
        "state": state,
        "lor": state.get("lor"),
    }
    background_tasks.add_task(_run_claim_pipeline, claim_id, claim_state)
    return ClaimSubmitResponse(claim_id=claim_id, status=STATUS_PROCESSING, lor=None)


@app.post("/api/v1/claim/{claim_id}/claim-type", response_model=LORPack)
def override_claim_type(claim_id: str, req: ClaimTypeOverrideRequest):
    """Correct a wrongly inferred claim type and rebuild the checklist.

    The claimant never confirmed the inference, so this is their only route to
    recover from a wrong one. Deterministic and in-process: no graph run, no LLM
    call. The override is recorded on the state so claim_type_classify_node
    skips itself on every subsequent re-run.
    """
    state = dict(_stored_state(claim_id))
    ruleset = reqs.load_ruleset((state.get("policy") or {}).get("insurer"))
    if ruleset is None:
        raise HTTPException(status_code=404, detail="No requirement list is configured.")
    if req.claim_type_id not in {s.id for s in ruleset.claim_types}:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown claim type '{req.claim_type_id}' for ruleset '{ruleset.ruleset_id}'.",
        )

    documents = [DocumentRecord.model_validate(d) for d in (state.get("documents") or [])]
    prev = (state.get("lor") or {}).get("revision", 1)
    pack = reqs.build_lor(
        policy=state.get("policy") or {},
        event=state.get("event") or {},
        documents=documents,
        evidence=state.get("evidence") or [],
        basis=reqs.BASIS_NARROWED,
        revision=prev + 1,
        claim_type=req.claim_type_id,
        claim_type_confidence=1.0,
        ambiguous=False,
    )

    state["claim_type"] = req.claim_type_id
    state["claim_type_confidence"] = 1.0
    state["claim_type_source"] = "user_override"
    state["claim_type_candidates"] = [req.claim_type_id]
    state["claim_type_ambiguous"] = False
    state["lor"] = pack.model_dump(mode="json")
    state["awaiting_documents"] = bool(pack.blocking_missing)

    entry = _CLAIMS_DB[claim_id]
    entry["state"] = state
    entry["lor"] = state["lor"]
    entry["status"] = STATUS_AWAITING if pack.blocking_missing else entry.get("status", STATUS_COMPLETED)
    return pack


@app.get("/api/v1/requirements/{slug}")
def get_requirements(slug: str):
    """Read back a stored ruleset — for onboarding review, and to populate the
    claim-type picker behind the app's "not the right claim type?" action."""
    ruleset = reqs.load_ruleset(slug)
    if ruleset is None:
        raise HTTPException(status_code=404, detail="No requirement list is configured.")
    return ruleset


@app.get("/api/v1/requirements")
def list_requirements():
    return {"rulesets": reqs.list_ruleset_ids()}


def main():
    import uvicorn
    uvicorn.run("agentic_pipeline.service:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
