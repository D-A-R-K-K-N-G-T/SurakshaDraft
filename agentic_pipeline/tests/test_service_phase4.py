"""Phase 4 service integration (§8 layer 3): FastAPI TestClient + real DB, graph stubbed.

Covers the acceptance criteria: submit persists; a fresh client (≈ restart) still
sees the claim; resume adds no duplicate warnings; duplicate docs 409; override.

Requires DATABASE_URL == TEST_DATABASE_URL (the app writes through its own engine).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from agentic_pipeline import service
from agentic_pipeline.config import settings

NOW = datetime.now(timezone.utc).isoformat()


class _FakeGraph:
    """Mimics a node appending to the additive `warnings` channel, so a resume
    that failed to reset inputs would visibly double the warning."""

    def invoke(self, state: dict) -> dict:
        st = dict(state)
        st["warnings"] = list(st.get("warnings") or []) + ["pipeline-warning"]
        st["intake_ok"] = True
        return st


@pytest.fixture()
def client(migrated_db, test_database_url, monkeypatch):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL for service tests")
    monkeypatch.setattr(service, "graph", _FakeGraph())
    return TestClient(service.app)


def _submit_body() -> dict:
    return {
        "policy": {"insurer": "default", "asset_categories": ["Stock"]},
        "event": {"description": "fire", "item_type": "Stock", "account_type": "commercial"},
        "evidence": [],
        "documents": [{
            "document_id": "DOC-1", "document_type": "Invoice",
            "file_ref": "fs://blob/doc1.pdf", "sha256": "b" * 64, "uploaded_at": NOW,
        }],
    }


def _doc(doc_id: str) -> dict:
    return {
        "document_id": doc_id, "document_type": "Supporting",
        "file_ref": f"fs://blob/{doc_id}.pdf", "sha256": "c" * 64, "uploaded_at": NOW,
    }


def test_submit_persists_and_get_returns_state(client):
    r = client.post("/api/v1/claim/submit", json=_submit_body())
    assert r.status_code == 200, r.text
    claim_id = r.json()["claim_id"]

    got = client.get(f"/api/v1/claim/{claim_id}").json()
    assert got["status"] == "completed"
    assert "pipeline-warning" in got["state"]["warnings"]


def test_survives_restart(client):
    claim_id = client.post("/api/v1/claim/submit", json=_submit_body()).json()["claim_id"]
    # A brand-new client instance = a fresh process reading the same DB.
    fresh = TestClient(service.app)
    got = fresh.get(f"/api/v1/claim/{claim_id}").json()
    assert got["status"] == "completed"
    assert got["state"]["documents"][0]["document_id"] == "DOC-1"


def test_resume_adds_no_duplicate_warnings(client):
    claim_id = client.post("/api/v1/claim/submit", json=_submit_body()).json()["claim_id"]
    before = client.get(f"/api/v1/claim/{claim_id}").json()["state"]["warnings"]
    assert before.count("pipeline-warning") == 1

    r = client.post(f"/api/v1/claim/{claim_id}/documents", json={"documents": [_doc("DOC-2")]})
    assert r.status_code == 200, r.text
    after = client.get(f"/api/v1/claim/{claim_id}").json()["state"]["warnings"]
    # The bug _RESET_ON_RESUME existed to prevent: still exactly one.
    assert after.count("pipeline-warning") == 1
    docs = {d["document_id"] for d in client.get(f"/api/v1/claim/{claim_id}").json()["state"]["documents"]}
    assert {"DOC-1", "DOC-2"} <= docs


def test_duplicate_documents_conflict(client):
    claim_id = client.post("/api/v1/claim/submit", json=_submit_body()).json()["claim_id"]
    assert client.post(f"/api/v1/claim/{claim_id}/documents", json={"documents": [_doc("DOC-9")]}).status_code == 200
    dup = client.post(f"/api/v1/claim/{claim_id}/documents", json={"documents": [_doc("DOC-9")]})
    assert dup.status_code == 409


def test_get_unknown_claim_404(client):
    assert client.get("/api/v1/claim/CLM-NOPE").status_code == 404


def test_override_claim_type(client):
    claim_id = client.post("/api/v1/claim/submit", json=_submit_body()).json()["claim_id"]
    r = client.post(f"/api/v1/claim/{claim_id}/claim-type", json={"claim_type_id": "fire"})
    assert r.status_code == 200, r.text
    assert r.json()["claim_type"] == "fire"
    state = client.get(f"/api/v1/claim/{claim_id}").json()["state"]
    assert state["claim_type"] == "fire" and state["claim_type_source"] == "user_override"
