"""End-to-end over the real HTTP surface: submit -> pause -> re-upload -> resume.

Drives the FastAPI app with TestClient and a mocked LLM, so the whole LOR
lifecycle is exercised against the actual endpoints the gateway calls. The
resume path is the reason this file exists: re-invoking the graph on a stored
state silently duplicates every Annotated[..., operator.add] channel unless the
reset is right, and that only shows up across two runs.

TestClient runs BackgroundTasks to completion before returning, so a submit has
already finished its pipeline run by the time the response arrives.
"""
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import agentic_pipeline.graph as g
from agentic_pipeline import requirements as reqs
from agentic_pipeline import service
from agentic_pipeline.schemas import (
    ClaimTypeClassification,
    ClaimTypeSection,
    DocumentKind,
    DocumentTriageOutput,
    LLMDocumentTriage,
    RequirementRule,
    RequirementRuleSet,
    RequirementSeverity,
    RequirementVerification,
)

NOW = datetime.now(timezone.utc).isoformat()


def _ruleset():
    return RequirementRuleSet(
        ruleset_id="test", version="1",
        claim_types=[
            ClaimTypeSection(id="fire", label="Fire & Allied Perils"),
            ClaimTypeSection(id="burglary", label="Burglary & Theft"),
        ],
        rules=[
            RequirementRule(requirement_id="REQ-ID", label="Government ID",
                            verification=RequirementVerification.CLASSIFIED,
                            accepts=[DocumentKind.GOVT_ID],
                            severity=RequirementSeverity.BLOCKING),
            RequirementRule(requirement_id="REQ-FIR", label="Police FIR",
                            claim_types=["burglary"],
                            verification=RequirementVerification.CLASSIFIED,
                            accepts=[DocumentKind.FIR_REPORT],
                            severity=RequirementSeverity.BLOCKING),
            RequirementRule(requirement_id="REQ-SUBROG", label="Letter of subrogation",
                            claim_types=["burglary"],
                            verification=RequirementVerification.ATTESTED,
                            severity=RequirementSeverity.ADVISORY),
        ],
    )


# Everything the pipeline asks for AFTER the requirement gate opens. Minimal
# valid objects — this file is about the LOR lifecycle, not the assessment path
# (test_graph_smoke.py covers that), but the nodes must still get valid shapes.
def _downstream(schema):
    from agentic_pipeline.schemas import (
        DocumentExtractionOutput, DraftOutput, PolicyExtractionOutput, PolicyOutput,
        QCGuardOutput, ReconciliationOutput, ValuationOutput, VisionOutput,
    )
    return {
        VisionOutput: lambda: VisionOutput(items=[], missing_signals=[], anomalies=[]),
        PolicyExtractionOutput: lambda: PolicyExtractionOutput(),
        DocumentExtractionOutput: lambda: DocumentExtractionOutput(documents=[]),
        ValuationOutput: lambda: ValuationOutput(items=[]),
        PolicyOutput: lambda: PolicyOutput(items=[]),
        ReconciliationOutput: lambda: ReconciliationOutput(pending_items=[]),
        DraftOutput: lambda: DraftOutput(main_schedule="none", rejected_items_annexure="none",
                                         pending_verification_annexure="none",
                                         excluded_items_annexure="none"),
        QCGuardOutput: lambda: QCGuardOutput(pass_qc=True, flags=[]),
    }.get(schema)


class _LLM:
    def __init__(self, schema, kinds, calls):
        self.schema, self.kinds, self.calls = schema, kinds, calls

    def invoke(self, messages, config=None):
        self.calls.append(self.schema.__name__)
        if self.schema is ClaimTypeClassification:
            return ClaimTypeClassification(claim_type_id="burglary", confidence=0.95,
                                           rationale="forced entry overnight")
        if self.schema is DocumentTriageOutput:
            return DocumentTriageOutput(documents=[
                LLMDocumentTriage(document_id=doc_id, doc_kind=kind,
                                  legible=True, confidence=0.95, markers=["m"])
                for doc_id, kind in self.kinds.items()
            ])
        factory = _downstream(self.schema)
        if factory is None:
            raise AssertionError(f"unexpected schema {self.schema.__name__}")
        return factory()


# Schemas that only ever run once the requirement gate has opened.
GATED_SCHEMAS = {"VisionOutput", "ValuationOutput", "PolicyOutput", "DraftOutput"}


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(reqs, "load_ruleset", lambda insurer: _ruleset())
    service._CLAIMS_DB.clear()

    calls: list[str] = []
    kinds = {"D-ID": DocumentKind.GOVT_ID, "D-FIR": DocumentKind.FIR_REPORT}
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: _LLM(schema, kinds, calls))
    # Triage reads the file off disk before calling the model.
    for name in ("id", "fir", "subrog"):
        (tmp_path / name).write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(g, "load_image_as_data_url", lambda ref: "data:application/pdf;base64,eA==")
    return TestClient(service.app), tmp_path, calls


def _doc(doc_id, doc_type, tmp_path, name, requirement_id=None):
    return {
        "document_id": doc_id, "document_type": doc_type,
        "file_ref": str(tmp_path / name), "uploaded_at": NOW,
        "requirement_id": requirement_id,
    }


def _submit(c, tmp_path):
    return c.post("/api/v1/claim/submit", json={
        "policy": {"insurer": "Test Insurer", "asset_categories": ["Electronics"]},
        "event": {"description": "laptops stolen overnight", "event_date": NOW,
                  "account_type": "commercial"},
        "documents": [_doc("D-ID", "GovtID", tmp_path, "id")],
    })


def test_submit_returns_an_instant_checklist(client):
    c, tmp_path, calls = client
    body = _submit(c, tmp_path).json()

    lor = body["lor"]
    assert body["claim_id"].startswith("CLM-")
    assert lor["revision"] == 1
    assert lor["basis"] == "universal_only", "the claim type is not known yet at submit"
    # Only universal rules; the burglary-specific FIR cannot appear yet.
    seen = {r["requirement_id"] for r in lor["satisfied"] + lor["missing"] + lor["unverified"]}
    assert seen == {"REQ-ID"}


def test_claim_pauses_with_a_narrowed_checklist(client):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]

    got = c.get(f"/api/v1/claim/{claim_id}").json()
    assert got["status"] == "awaiting_documents"

    lor = got["state"]["lor"]
    assert lor["revision"] == 2
    assert lor["basis"] == "claim_type_narrowed"
    assert lor["claim_type"] == "burglary"
    assert lor["blocking_missing"] == ["REQ-FIR"]
    assert [r["requirement_id"] for r in lor["satisfied"]] == ["REQ-ID"]
    # The advisory attested rule is listed but does not hold the claim up.
    assert "REQ-SUBROG" in {r["requirement_id"] for r in lor["missing"]}
    assert got["state"]["draft_pack"] is None, "no draft for a paused claim"
    # The gate's whole purpose: an unfinishable claim costs 2 LLM calls, not 12.
    assert not GATED_SCHEMAS & set(calls), f"assessment ran on a paused claim: {calls}"
    assert set(calls) == {"DocumentTriageOutput", "ClaimTypeClassification"}


def test_reupload_resumes_the_claim_without_duplicating_warnings(client):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]
    first = c.get(f"/api/v1/claim/{claim_id}").json()["state"]["warnings"]

    r = c.post(f"/api/v1/claim/{claim_id}/documents",
               json={"documents": [_doc("D-FIR", "Supporting", tmp_path, "fir")]})
    assert r.status_code == 200

    after = c.get(f"/api/v1/claim/{claim_id}").json()
    assert after["state"]["lor"]["blocking_missing"] == []
    assert after["state"]["awaiting_documents"] is False

    # The whole point of _RESET_ON_RESUME: additive channels must not double up.
    warnings = after["state"]["warnings"]
    assert len(warnings) == len(set(warnings)), f"duplicated warnings: {warnings}"
    for w in first:
        assert warnings.count(w) <= 1

    # Both documents survive, and the original keeps its verdict.
    docs = {d["document_id"]: d for d in after["state"]["documents"]}
    assert set(docs) == {"D-ID", "D-FIR"}
    assert docs["D-ID"]["classification_done"] is True


def test_attested_requirement_needs_the_tag(client):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]

    # Untagged: does not satisfy REQ-SUBROG.
    c.post(f"/api/v1/claim/{claim_id}/documents",
           json={"documents": [_doc("D-X", "Supporting", tmp_path, "subrog")]})
    lor = c.get(f"/api/v1/claim/{claim_id}").json()["state"]["lor"]
    assert "REQ-SUBROG" in {r["requirement_id"] for r in lor["missing"]}

    # Tagged: satisfies it, and says plainly that we did not check the contents.
    c.post(f"/api/v1/claim/{claim_id}/documents",
           json={"documents": [_doc("D-SUB", "Supporting", tmp_path, "subrog",
                                    requirement_id="REQ-SUBROG")]})
    lor = c.get(f"/api/v1/claim/{claim_id}").json()["state"]["lor"]
    row = next(r for r in lor["satisfied"] if r["requirement_id"] == "REQ-SUBROG")
    assert "not verified" in row["message"].lower()


def test_claim_type_override_rebuilds_the_checklist(client):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]
    before = c.get(f"/api/v1/claim/{claim_id}").json()["state"]["lor"]
    assert before["blocking_missing"] == ["REQ-FIR"]

    r = c.post(f"/api/v1/claim/{claim_id}/claim-type", json={"claim_type_id": "fire"})
    assert r.status_code == 200
    pack = r.json()
    assert pack["claim_type"] == "fire"
    assert pack["revision"] > before["revision"]
    assert pack["blocking_missing"] == [], "the FIR belonged to the other section"

    stored = c.get(f"/api/v1/claim/{claim_id}").json()["state"]
    assert stored["claim_type_source"] == "user_override"


def test_override_rejects_an_unknown_claim_type(client):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]
    r = c.post(f"/api/v1/claim/{claim_id}/claim-type", json={"claim_type_id": "marine"})
    assert r.status_code == 400


def test_override_survives_a_later_reupload(client, monkeypatch):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]
    c.post(f"/api/v1/claim/{claim_id}/claim-type", json={"claim_type_id": "fire"})

    # If the classifier ran again it would overwrite the correction back to
    # "burglary" — the node must skip itself entirely on a user override.
    c.post(f"/api/v1/claim/{claim_id}/documents",
           json={"documents": [_doc("D-FIR", "Supporting", tmp_path, "fir")]})

    stored = c.get(f"/api/v1/claim/{claim_id}").json()["state"]
    assert stored["claim_type"] == "fire"
    assert stored["claim_type_source"] == "user_override"


def test_duplicate_document_is_rejected(client):
    c, tmp_path, calls = client
    claim_id = _submit(c, tmp_path).json()["claim_id"]
    r = c.post(f"/api/v1/claim/{claim_id}/documents",
               json={"documents": [_doc("D-ID", "GovtID", tmp_path, "id")]})
    assert r.status_code == 409


def test_unknown_claim_is_404(client):
    c, _, _calls = client
    assert c.get("/api/v1/claim/CLM-NOPE").status_code == 404
    assert c.post("/api/v1/claim/CLM-NOPE/documents",
                  json={"documents": []}).status_code in (400, 404)


def test_requirements_readback_powers_the_claim_type_picker(client):
    c, _, _calls = client
    body = c.get("/api/v1/requirements/test").json()
    assert [s["id"] for s in body["claim_types"]] == ["fire", "burglary"]
