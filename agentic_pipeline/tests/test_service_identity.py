"""Phase 7 service tests: identity-scoped endpoints (demo auth), graph stubbed.

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
    def invoke(self, state: dict) -> dict:
        st = dict(state)
        st["intake_ok"] = True
        return st


@pytest.fixture()
def client(migrated_db, test_database_url, monkeypatch):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL for service tests")
    monkeypatch.setattr(settings, "auth_mode", "demo")  # bearer token == user subject
    monkeypatch.setattr(service, "graph", _FakeGraph())
    return TestClient(service.app)


def _submit_body(policy_id=None):
    policy = {"insurer": "default", "asset_categories": ["Stock"]}
    if policy_id:
        policy["policy_id"] = policy_id
    return {
        "policy": policy,
        "event": {"description": "fire", "account_type": "commercial"},
        "evidence": [],
        "documents": [{"document_id": "DOC-1", "document_type": "Invoice",
                       "file_ref": "fs://x", "sha256": "b" * 64, "uploaded_at": NOW}],
    }


def test_claims_are_scoped_to_the_authenticated_user(client):
    h1 = {"Authorization": "Bearer user-1"}
    h2 = {"Authorization": "Bearer user-2"}
    cid = client.post("/api/v1/claim/submit", json=_submit_body(), headers=h1).json()["claim_id"]

    mine = client.get("/api/v1/claims", headers=h1).json()
    assert any(c["claim_ref"] == cid for c in mine["claims"])
    # A different user does not see it.
    theirs = client.get("/api/v1/claims", headers=h2).json()
    assert all(c["claim_ref"] != cid for c in theirs["claims"])


def test_anonymous_claims_list_is_empty(client):
    # No token, no ?user_id -> nothing to scope.
    assert client.get("/api/v1/claims").json() == {"claims": [], "next_cursor": None}


def test_create_policy_returns_id_and_powers_geofence_merge(client):
    h = {"Authorization": "Bearer user-geo"}
    resp = client.post("/api/v1/policies", headers=h, json={
        "asset_categories": ["Stock"], "premises_lat": 28.61, "premises_lon": 77.20,
        "sums_insured": [{"category_key": "sum_insured_stock",
                          "category_label": "Stock", "amount": 500000}],
    })
    assert resp.status_code == 200
    pid = resp.json()["policy_id"]

    cid = client.post("/api/v1/claim/submit", json=_submit_body(policy_id=pid),
                      headers=h).json()["claim_id"]
    state = client.get(f"/api/v1/claim/{cid}").json()["state"]
    assert state["policy"]["premises_geo"] == {"lat": 28.61, "lon": 77.20}


def test_get_claim_lor_endpoint(client):
    cid = client.post("/api/v1/claim/submit", json=_submit_body(),
                      headers={"Authorization": "Bearer u"}).json()["claim_id"]
    lor = client.get(f"/api/v1/claim/{cid}/lor").json()
    assert lor.get("revision") == 1
    assert client.get("/api/v1/claim/CLM-NOPE/lor").status_code == 404
