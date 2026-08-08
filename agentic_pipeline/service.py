import os
from dotenv import load_dotenv
load_dotenv(".env")

import uuid
from typing import List
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage, SystemMessage

from agentic_pipeline.schemas import VisionCandidateItem, VisionOutput
from agentic_pipeline.state import ClaimState
from agentic_pipeline.graph import graph
from agentic_pipeline.llm import get_structured_llm, invoke_structured
from agentic_pipeline.prompts import VISION_SYSTEM_PROMPT, VISION_HUMAN_PROMPT_TEMPLATE
from agentic_pipeline.images import build_image_block

app = FastAPI(title="SurakshaDraft Agent API")

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

# In-memory store
_CLAIMS_DB: dict[str, dict] = {}


@app.post("/api/v1/vision/preview", response_model=VisionCandidateItem)
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
        
    if result.anomalies and not result.items:
        raise HTTPException(status_code=422, detail={"anomalies": result.anomalies})
        
    if not result.items:
        raise HTTPException(status_code=422, detail="No items identified in the image.")
        
    if len(result.items) > 1:
        print("WARNING: /api/v1/vision/preview returned multiple items, taking the first.")
        
    # Exclude evidence_refs as it's not applicable for preview
    item = result.items[0]
    item.evidence_refs = []
    return item


def _run_claim_pipeline(claim_id: str, state: ClaimState):
    try:
        final_state = graph.invoke(state.model_dump())
        _CLAIMS_DB[claim_id] = {"status": "completed", "state": final_state}
    except Exception as e:
        _CLAIMS_DB[claim_id] = {"status": "failed", "error": str(e)}


@app.post("/api/v1/claim/submit", response_model=ClaimSubmitResponse)
def submit_claim(state: ClaimState, background_tasks: BackgroundTasks):
    claim_id = f"CLM-{uuid.uuid4().hex[:8].upper()}"
    _CLAIMS_DB[claim_id] = {"status": "processing"}
    background_tasks.add_task(_run_claim_pipeline, claim_id, state)
    return ClaimSubmitResponse(claim_id=claim_id, status="processing")


@app.get("/api/v1/claim/{claim_id}")
def get_claim(claim_id: str):
    if claim_id not in _CLAIMS_DB:
        raise HTTPException(status_code=404, detail="Claim not found")
    return _CLAIMS_DB[claim_id]


def main():
    import uvicorn
    uvicorn.run("agentic_pipeline.service:app", host="0.0.0.0", port=8000, reload=True)

if __name__ == "__main__":
    main()
