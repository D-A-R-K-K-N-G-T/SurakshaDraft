"""Phase 5: derived outputs & LOR revisions are persisted and queryable.

Repository-level, using a Session bound to the test engine (no app-engine coupling).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_pipeline import models as M
from agentic_pipeline import repository as repo
from agentic_pipeline.schemas import (
    DocumentRecord,
    DraftOutput,
    LORPack,
    QCGuardOutput,
    RequirementResult,
    RequirementSeverity,
    RequirementStatus,
    RequirementVerification,
)
from agentic_pipeline.state import ClaimState

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def session(migrated_db) -> Session:
    with Session(migrated_db) as s:
        yield s


def _result(rid, status, satisfied_by=()):
    return RequirementResult(
        requirement_id=rid, label=rid, status=status,
        severity=RequirementSeverity.BLOCKING,
        verification=RequirementVerification.CLASSIFIED,
        satisfied_by=list(satisfied_by),
    )


def _rev1():
    return LORPack(revision=1, basis="universal_only",
                   satisfied=[_result("REQ-ID", RequirementStatus.SATISFIED, ["DOC-1"])])


def _rev2():
    return LORPack(
        revision=2, basis="claim_type_narrowed", claim_type="fire",
        claim_type_label="Fire & Allied Perils", blocking_missing=["REQ-FIR"],
        satisfied=[_result("REQ-ID", RequirementStatus.SATISFIED, ["DOC-1"])],
        missing=[_result("REQ-FIR", RequirementStatus.MISSING)],
    )


def _submit_state():
    return ClaimState(
        policy={"insurer": "default"},
        event={"account_type": "commercial"},
        documents=[DocumentRecord(document_id="DOC-1", document_type="GovtID",
                                  file_ref="fs://x", uploaded_at=NOW)],
        lor=_rev1(),
    )


def _final_state(submit: ClaimState) -> ClaimState:
    final = ClaimState.model_validate(submit.model_dump())
    final.intake_ok = True
    final.qc_retries = 1
    final.reserve_estimate = {
        "confirmed": 100.0, "conditional": 10.0, "excluded": 0.0,
        "unclassified": 0.0, "pending": 5.0, "screened_out": 0.0,
    }
    final.draft_pack = DraftOutput(
        main_schedule="Main: LI-1", rejected_items_annexure="",
        pending_verification_annexure="", excluded_items_annexure="", narrative="n",
    )
    final.qc = QCGuardOutput(pass_qc=True, flags=[])
    final.proof_receipt = {
        "receipt_hash": "a" * 64, "sent_at": NOW.isoformat(), "recipient": "Insurer Intake API",
    }
    final.lor = _rev2()
    return final


def test_rev1_lor_persisted_on_create(session):
    ref, run_id = repo.create_claim(session, state=_submit_state())
    session.commit()
    packs = session.scalars(select(M.LORPackRow)).all()
    assert len(packs) == 1 and packs[0].revision == 1 and packs[0].run_id is None
    # one result, with its satisfied_by document ref
    assert session.scalar(select(func.count()).select_from(M.LORRequirementResult)) == 1
    assert session.scalar(select(func.count()).select_from(M.LORResultDocument)) == 1


def test_run_outputs_persisted(session):
    submit = _submit_state()
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()
    repo.persist_success(session, run_id, _final_state(submit))
    session.commit()

    reserve = session.scalars(select(M.ReserveEstimate)).one()
    assert float(reserve.confirmed) == 100.0 and float(reserve.pending) == 5.0

    draft = session.scalars(select(M.DraftPack)).one()
    assert draft.is_final is True and draft.attempt == 1 and len(draft.content_hash) == 64

    qc = session.scalars(select(M.QCResult)).one()
    assert qc.pass_qc is True and qc.check_source == "llm" and qc.draft_pack_id == draft.id

    proof = session.scalars(select(M.ProofReceipt)).one()
    assert proof.recipient == "Insurer Intake API" and proof.draft_pack_id == draft.id

    # both LOR revisions are queryable
    revs = session.scalars(select(M.LORPackRow.revision).order_by(M.LORPackRow.revision)).all()
    assert revs == [1, 2]
    rev2 = session.scalars(select(M.LORPackRow).where(M.LORPackRow.revision == 2)).one()
    assert rev2.claim_type_key == "fire" and rev2.blocking_missing == ["REQ-FIR"]
    assert {r.requirement_id for r in rev2.results} == {"REQ-ID", "REQ-FIR"}


def test_every_revision_queryable_through_override(session):
    submit = _submit_state()
    ref, run_id = repo.create_claim(session, state=submit)
    session.commit()
    repo.persist_success(session, run_id, _final_state(submit))
    session.commit()

    pack3 = LORPack(revision=3, basis="claim_type_narrowed", claim_type="burglary",
                    blocking_missing=[])
    repo.override_claim_type(session, ref, claim_type_id="burglary", pack=pack3)
    session.commit()

    revs = session.scalars(select(M.LORPackRow.revision).order_by(M.LORPackRow.revision)).all()
    assert revs == [1, 2, 3]
