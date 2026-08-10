import os
from dotenv import load_dotenv
load_dotenv(".env")

import logging
import traceback
from typing import List, Optional
from fastapi import FastAPI, HTTPException, BackgroundTasks, Header
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
from agentic_pipeline import repository as repo
from agentic_pipeline import auth
from agentic_pipeline.db import session_scope
from agentic_pipeline.tasks import run_pipeline_task


def _resolve_user(session, authorization: Optional[str]) -> Optional[str]:
    """Resolve the optional bearer token to a user id (None = anonymous)."""
    return auth.resolve_user(session, auth.bearer_token(authorization))

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

class SumInsuredItem(BaseModel):
    category_key: str
    category_label: str
    amount: float

class PolicyCreateRequest(BaseModel):
    policy_number: Optional[str] = None
    product: Optional[str] = None
    start_date: Optional[str] = None       # ISO; coerced by SQLAlchemy/psycopg
    end_date: Optional[str] = None
    excess: Optional[float] = None
    asset_categories: List[str] = Field(default_factory=list)
    clauses: List[str] = Field(default_factory=list)
    clauses_text: Optional[str] = None
    clauses_assumed: bool = True
    terms_source: Optional[str] = None
    assumed_fields: List[str] = Field(default_factory=list)
    premises_lat: Optional[float] = None
    premises_lon: Optional[float] = None
    sums_insured: List[SumInsuredItem] = Field(default_factory=list)

class PolicyCreateResponse(BaseModel):
    policy_id: str

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





@app.post("/api/v1/claim/submit", response_model=ClaimSubmitResponse)
def submit_claim(
    state: ClaimState,
    background_tasks: BackgroundTasks,
    authorization: Optional[str] = Header(default=None),
    idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
):
    policy_id = (state.policy or {}).get("policy_id")
    with session_scope() as s:
        # Idempotency (R4): a retried submit (same key) returns the SAME claim,
        # never a duplicate. Reserve+create+store happen in one transaction.
        if idempotency_key:
            if not repo.reserve_idempotency(s, key=idempotency_key, scope="claim_submit"):
                row = repo.get_idempotency(s, idempotency_key)
                if row is not None and row.response_body:
                    return ClaimSubmitResponse(**row.response_body)
                raise HTTPException(
                    status_code=409, detail="This submission is already being processed."
                )
        lor = _safe_universal_lor(state)
        state.lor = lor
        user_id = _resolve_user(s, authorization)  # None when anonymous/disabled
        claim_ref, run_id = repo.create_claim(
            s, state=state, user_id=user_id, policy_id=policy_id
        )
        response = ClaimSubmitResponse(
            claim_id=claim_ref, status=repo.STATUS_PROCESSING, lor=lor
        )
        if idempotency_key:
            repo.fill_idempotency(
                s, idempotency_key, claim_ref=claim_ref, response=response.model_dump(mode="json")
            )
    # The pipeline needs its own ref to exclude itself from the fraud registries.
    state.claim_ref = claim_ref
    # Enqueue only after the create transaction has committed.
    run_pipeline_task.delay(claim_ref, run_id, state.model_dump(mode="json"))
    return response


@app.post("/api/v1/policies", response_model=PolicyCreateResponse)
def create_policy(req: PolicyCreateRequest, authorization: Optional[str] = Header(default=None)):
    """Persist an onboarding policy once; the app then sends its policy_id on
    submit. This is where premises_lat/lon (the geofence anchor) get captured."""
    with session_scope() as s:
        user_id = _resolve_user(s, authorization)
        policy_id = repo.create_policy(s, user_id=user_id, payload=req)
    return PolicyCreateResponse(policy_id=policy_id)


@app.get("/api/v1/claims")
def list_claims(
    user_id: Optional[str] = None,
    limit: int = 20,
    cursor: Optional[str] = None,
    authorization: Optional[str] = Header(default=None),
):
    """The claimant's claim history (replaces the app's in-memory list). Scoped
    to the authenticated user; falls back to an explicit ?user_id= for
    dev/testing. Returns [] when no user can be determined."""
    limit = max(1, min(limit, 100))
    with session_scope() as s:
        uid = _resolve_user(s, authorization) or user_id
        if uid is None:
            return {"claims": [], "next_cursor": None}
        items, next_cursor = repo.list_claims(s, user_id=uid, limit=limit, cursor=cursor)
        return {"claims": items, "next_cursor": next_cursor}


@app.get("/api/v1/firm/{firm_name}/claims")
def list_firm_claims(
    firm_name: str,
    limit: int = 20,
    cursor: Optional[str] = None,
):
    """The firm's claim history, created_at-cursor paginated."""
    limit = max(1, min(limit, 100))
    with session_scope() as s:
        items, next_cursor = repo.list_firm_claims(s, firm_name=firm_name, limit=limit, cursor=cursor)
        return {"claims": items, "next_cursor": next_cursor}


@app.get("/api/v1/claim/{claim_id}/lor")
def get_claim_lor(claim_id: str):
    """Latest LOR pack only, so polling need not ship the entire claim state."""
    try:
        with session_scope() as s:
            return repo.get_latest_lor(s, claim_id) or {}
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Claim not found")


@app.get("/api/v1/claim/{claim_id}")
def get_claim(claim_id: str):
    try:
        with session_scope() as s:
            return repo.view_claim(s, claim_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Claim not found")


@app.post("/api/v1/claim/{claim_id}/documents", response_model=ClaimSubmitResponse)
def add_documents(claim_id: str, req: AddDocumentsRequest, background_tasks: BackgroundTasks):
    """Attach documents to an existing claim and re-run the pipeline.

    Resume rebuilds an INPUTS-ONLY state from the snapshot (repository), so the
    additive channels start empty and re-running cannot duplicate warnings — the
    structural replacement for the old _RESET_ON_RESUME. New files carry
    classification_done=False, so only they cost an LLM call in document_triage.
    """
    if not req.documents:
        raise HTTPException(status_code=400, detail="No documents supplied.")
    try:
        with session_scope() as s:
            state = repo.build_resume_state(s, claim_id)
            state.claim_ref = claim_id  # ensure the fraud registry can exclude self
            existing_ids = {d.document_id for d in state.documents}
            incoming = [d for d in req.documents if d.document_id not in existing_ids]
            if not incoming:
                raise HTTPException(
                    status_code=409, detail="These documents are already on the claim."
                )
            state.documents = list(state.documents) + incoming
            run_id = repo.start_run(s, claim_id, "documents_added")
            repo.append_document_rows(s, claim_id, incoming, run_id)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Claim not found")
    except repo.Conflict as c:
        raise HTTPException(status_code=409, detail=str(c))

    run_pipeline_task.delay(claim_id, run_id, state.model_dump(mode="json"))
    return ClaimSubmitResponse(claim_id=claim_id, status=repo.STATUS_PROCESSING, lor=None)


@app.post("/api/v1/claim/{claim_id}/claim-type", response_model=LORPack)
def override_claim_type(claim_id: str, req: ClaimTypeOverrideRequest):
    """Correct a wrongly inferred claim type and rebuild the checklist.

    Deterministic and in-process: no graph run, no LLM call. Persisted through
    the repository, which also patches the stored snapshot so GET's state.lor
    reflects the correction immediately.
    """
    try:
        with session_scope() as s:
            view = repo.view_claim(s, claim_id)
            snap = view.get("state")
            if snap is None:
                raise HTTPException(
                    status_code=409,
                    detail="This claim is still being processed; try again in a moment.",
                )
            ruleset = reqs.load_ruleset((snap.get("policy") or {}).get("insurer"))
            if ruleset is None:
                raise HTTPException(status_code=404, detail="No requirement list is configured.")
            if req.claim_type_id not in {sec.id for sec in ruleset.claim_types}:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown claim type '{req.claim_type_id}' for ruleset '{ruleset.ruleset_id}'.",
                )

            documents = [DocumentRecord.model_validate(d) for d in (snap.get("documents") or [])]
            prev = (view.get("lor") or {}).get("revision", 1)
            pack = reqs.build_lor(
                policy=snap.get("policy") or {},
                event=snap.get("event") or {},
                documents=documents,
                evidence=snap.get("evidence") or [],
                basis=reqs.BASIS_NARROWED,
                revision=prev + 1,
                claim_type=req.claim_type_id,
                claim_type_confidence=1.0,
                ambiguous=False,
            )
            repo.override_claim_type(s, claim_id, claim_type_id=req.claim_type_id, pack=pack)
    except repo.NotFound:
        raise HTTPException(status_code=404, detail="Claim not found")
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
