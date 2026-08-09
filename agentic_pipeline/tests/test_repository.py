"""Phase 4 repository round-trip (§8 layer 2) — the highest-value DB test.

Requires TEST_DATABASE_URL. Uses a Session bound directly to the test engine, so
it does NOT need DATABASE_URL == TEST_DATABASE_URL (repository takes the session).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_pipeline import models as M
from agentic_pipeline import repository as repo
from agentic_pipeline.schemas import (
    CaptureStage,
    DocumentKind,
    DocumentRecord,
    EvidenceRecord,
    GeoPoint,
    LineItem,
    LORPack,
    TriageVerdict,
)
from agentic_pipeline.state import ClaimState

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def session(migrated_db) -> Session:
    with Session(migrated_db) as s:
        yield s


def _submit_state() -> ClaimState:
    return ClaimState(
        policy={"insurer": "default", "asset_categories": ["Stock"]},
        event={"description": "fire", "item_type": "Stock", "account_type": "commercial"},
        evidence=[EvidenceRecord(
            evidence_id="IMG-1", capture_stage=CaptureStage.SCENE,
            file_ref="fs://blob/img1.jpg", sha256="a" * 64, captured_at=NOW,
            geotag=GeoPoint(lat=1.5, lon=2.5),
        )],
        documents=[DocumentRecord(
            document_id="DOC-1", document_type="Invoice",
            file_ref="fs://blob/doc1.pdf", sha256="b" * 64, uploaded_at=NOW,
        )],
        line_items=[LineItem(
            item_ref="LI-1", name="Laptop", serial_number="SN-1",
            evidence_refs=["IMG-1"], matched_document_ids=["DOC-1"],
        )],
        warnings=["gateway-warning"],
        gateway_blocking_reasons=["dup file"],
    )


def _final_state(submit: ClaimState) -> ClaimState:
    final = ClaimState.model_validate(submit.model_dump())
    final.intake_ok = True
    final.warnings = ["gateway-warning", "pipeline-warning"]
    final.anomalies = ["odd"]
    final.documents[0].classification_kind = DocumentKind.TAX_INVOICE
    final.documents[0].classification_verdict = TriageVerdict.MATCH
    final.documents[0].classification_done = True
    final.vision_processed_evidence_ids = ["IMG-1"]
    final.lor = LORPack(revision=1, basis="universal_only")
    return final


def test_create_then_processing_view(session):
    ref, run_id = repo.create_claim(session, state=_submit_state())
    session.commit()
    view = repo.view_claim(session, ref)
    assert view["status"] == "processing"
    assert "state" not in view          # no completed run yet
    # normalized inputs were written
    assert session.scalar(select(func.count()).select_from(M.Evidence)) == 1
    assert session.scalar(select(func.count()).select_from(M.Document)) == 1
    assert session.scalar(select(func.count()).select_from(M.LineItem)) == 1
    # gateway note preserved (run_id NULL)
    assert session.scalar(
        select(func.count()).select_from(M.ClaimNote).where(M.ClaimNote.run_id.is_(None))
    ) == 1


def test_persist_success_roundtrip_and_view(session):
    submit = _submit_state()
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()

    repo.persist_success(session, run_id, _final_state(submit))
    session.commit()

    view = repo.view_claim(session, ref)
    assert view["status"] == "completed"
    assert view["state"]["warnings"] == ["gateway-warning", "pipeline-warning"]
    assert view["state"]["intake_ok"] is True
    assert view["lor"]["revision"] == 1

    # normalized projection reflects the run
    li = session.scalars(select(M.LineItem)).one()
    assert li.serial_number == "SN-1"
    assert session.scalar(select(func.count()).select_from(M.LineItemEvidence)) == 1
    assert session.scalar(select(func.count()).select_from(M.LineItemDocument)) == 1
    ev = session.scalars(select(M.Evidence)).one()
    assert ev.vision_processed_at is not None
    doc = session.scalars(select(M.Document)).one()
    assert doc.classification_done is True and doc.classification_kind == "tax_invoice"
    # derived notes for this run: 2 warnings + 1 anomaly
    assert session.scalar(
        select(func.count()).select_from(M.ClaimNote).where(M.ClaimNote.run_id == run_id)
    ) == 3


def test_resume_state_is_inputs_only(session):
    submit = _submit_state()
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()
    repo.persist_success(session, run_id, _final_state(submit))
    session.commit()

    resume = repo.build_resume_state(session, ref)
    # derived channels reset -> re-run cannot duplicate warnings
    assert resume.warnings == []
    assert resume.anomalies == []
    assert resume.draft_pack is None
    # inputs preserved, incl. document classification (so triage skips it)
    assert len(resume.documents) == 1 and resume.documents[0].classification_done is True
    assert resume.vision_processed_evidence_ids == ["IMG-1"]


def test_one_active_run_conflict(session):
    ref, run_id = repo.create_claim(session, state=_submit_state())
    session.commit()
    # run #1 is still in flight -> starting another must conflict (R9)
    with pytest.raises(repo.Conflict):
        repo.start_run(session, ref, "documents_added")
    session.rollback()
    repo.persist_success(session, run_id, _final_state(_submit_state()))
    session.commit()
    # now a new run is allowed
    run2 = repo.start_run(session, ref, "documents_added")
    session.commit()
    assert run2 != run_id


def test_override_patches_snapshot(session):
    submit = _submit_state()
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()
    repo.persist_success(session, run_id, _final_state(submit))
    session.commit()

    pack = LORPack(revision=2, basis="claim_type_narrowed", claim_type="fire",
                   blocking_missing=["REQ-FIRE-BRIGADE"])
    repo.override_claim_type(session, ref, claim_type_id="fire", pack=pack)
    session.commit()

    view = repo.view_claim(session, ref)
    assert view["status"] == "awaiting_documents"
    assert view["state"]["claim_type"] == "fire"
    assert view["state"]["claim_type_source"] == "user_override"
    assert view["state"]["lor"]["revision"] == 2
