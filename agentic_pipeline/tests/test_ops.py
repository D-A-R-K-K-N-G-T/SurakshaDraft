"""Phase 8 ops: idempotency, audit, outbox, retention GC, llm invocations.

Acceptance: duplicate submit returns one claim; override appears in audit_log.
"""
from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_pipeline import llm
from agentic_pipeline import models as M
from agentic_pipeline import repository as repo
from agentic_pipeline import service
from agentic_pipeline.config import settings
from agentic_pipeline.schemas import DraftOutput, LORPack, QCGuardOutput
from agentic_pipeline.state import ClaimState
from scripts import retention_gc

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def session(migrated_db) -> Session:
    with Session(migrated_db) as s:
        yield s


# --- repo-level ------------------------------------------------------------

def _completed_claim(session):
    submit = ClaimState(policy={"insurer": "default"}, event={"account_type": "personal"},
                        lor=LORPack(revision=1, basis="universal_only"))
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()
    final = ClaimState.model_validate(submit.model_dump())
    final.intake_ok = True
    final.lor = LORPack(revision=1, basis="universal_only")
    repo.persist_success(session, run_id, final)
    session.commit()
    return ref


def test_override_writes_audit_log(session):
    ref = _completed_claim(session)
    repo.override_claim_type(session, ref, claim_type_id="fire",
                             pack=LORPack(revision=2, basis="claim_type_narrowed", claim_type="fire"))
    session.commit()
    rows = session.scalars(select(M.AuditLog).where(M.AuditLog.action == "claim_type.override")).all()
    assert len(rows) == 1
    assert rows[0].entity_id == ref and rows[0].after["claim_type_key"] == "fire"


def test_proof_receipt_enqueues_outbox(session):
    submit = ClaimState(policy={}, event={"account_type": "personal"})
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()
    final = ClaimState.model_validate(submit.model_dump())
    final.draft_pack = DraftOutput(main_schedule="m", rejected_items_annexure="",
                                   pending_verification_annexure="", excluded_items_annexure="")
    final.qc = QCGuardOutput(pass_qc=True, flags=[])
    final.proof_receipt = {"receipt_hash": "a" * 64, "sent_at": NOW.isoformat(),
                           "recipient": "Insurer Intake API"}
    repo.persist_success(session, run_id, final)
    session.commit()
    msg = session.scalars(select(M.OutboxMessage)).one()
    assert msg.topic == "insurer.intimation" and msg.status == "pending"


def test_retention_gc_deletes_expired_and_orphan_blobs(session):
    # An expired claim + an unreferenced blob.
    ref, _ = repo.create_claim(session, state=ClaimState(policy={}, event={"account_type": "personal"}))
    claim = session.execute(select(M.Claim).where(M.Claim.claim_ref == ref)).scalar_one()
    claim.retention_expires_at = NOW - dt.timedelta(days=1)
    session.add(M.FileBlob(sha256="z" * 64, storage_uri="fs:///nope/zz", mime_type="image/jpeg", byte_size=1))
    session.commit()

    result = retention_gc.gc(session, now=NOW, dry_run=False)
    session.commit()
    assert result["expired_claims"] == 1 and result["orphan_blobs"] >= 1
    assert session.scalar(select(func.count()).select_from(M.Claim)) == 0
    assert session.scalar(select(func.count()).select_from(M.FileBlob).where(M.FileBlob.sha256 == "z" * 64)) == 0


# --- service / app-engine level (needs DATABASE_URL == TEST_DATABASE_URL) ---

class _FakeGraph:
    def invoke(self, state: dict) -> dict:
        st = dict(state)
        st["intake_ok"] = True
        return st


def _body():
    return {
        "policy": {"insurer": "default"}, "event": {"account_type": "commercial"},
        "evidence": [], "documents": [],
    }


@pytest.fixture()
def client(migrated_db, test_database_url, monkeypatch):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL for service ops tests")
    monkeypatch.setattr(service, "graph", _FakeGraph())
    return TestClient(service.app)


def test_duplicate_submit_returns_one_claim(client, migrated_db):
    h = {"Idempotency-Key": "submit-key-1"}
    c1 = client.post("/api/v1/claim/submit", json=_body(), headers=h).json()["claim_id"]
    c2 = client.post("/api/v1/claim/submit", json=_body(), headers=h).json()["claim_id"]
    assert c1 == c2
    with Session(migrated_db) as s:
        assert s.scalar(select(func.count()).select_from(M.Claim)) == 1


def test_llm_invocation_recorded_when_enabled(migrated_db, test_database_url, monkeypatch):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL for the llm recorder test")
    monkeypatch.setattr(settings, "record_llm_invocations", True)

    with Session(migrated_db) as s:
        ref, run_id = repo.create_claim(s, state=ClaimState(policy={}, event={"account_type": "personal"}))
        s.commit()

    class _Model:
        def invoke(self, messages, config=None):
            return {"ok": True}

    token = llm.set_run_context(run_id)
    try:
        llm.invoke_structured(_Model(), [])
    finally:
        llm.reset_run_context(token)

    with Session(migrated_db) as s:
        row = s.scalars(select(M.LLMInvocation)).one()
        assert row.succeeded is True and str(row.run_id) == str(run_id)
