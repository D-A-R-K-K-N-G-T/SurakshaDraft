"""Phase 6 (the payoff): cross-claim fraud registries replace the mocks.

Acceptance: submit claim A with photo X, then claim B with the same photo ->
B's item is rejected citing A's claim_ref.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.orm import Session

from agentic_pipeline import repository as repo
from agentic_pipeline.config import settings
from agentic_pipeline.graph import plausibility_check_node
from agentic_pipeline.schemas import CaptureStage, EvidenceRecord, LineItem
from agentic_pipeline.state import ClaimState

NOW = datetime.now(timezone.utc)
PHOTO_X = "d" * 64


@pytest.fixture()
def session(migrated_db) -> Session:
    with Session(migrated_db) as s:
        yield s


def _claim_with_photo(session, sha) -> str:
    ref, _ = repo.create_claim(session, state=ClaimState(
        policy={}, event={"account_type": "personal"},
        evidence=[EvidenceRecord(evidence_id="IMG", capture_stage=CaptureStage.ITEM,
                                 file_ref="fs://x", sha256=sha, captured_at=NOW)],
    ))
    return ref


def _claim_with_serial(session, serial) -> str:
    ref, _ = repo.create_claim(session, state=ClaimState(
        policy={}, event={"account_type": "personal"},
        line_items=[LineItem(item_ref="LI-1", name="Laptop", serial_number=serial)],
    ))
    return ref


def test_find_cross_claim_hashes_excludes_self(session):
    a_ref = _claim_with_photo(session, PHOTO_X)
    b_ref = _claim_with_photo(session, "e" * 64)
    session.commit()
    # From B's perspective, photo X belongs to A.
    assert repo.find_cross_claim_hashes(session, b_ref, [PHOTO_X]) == {PHOTO_X: a_ref}
    # From A's own perspective, it is not a cross-claim hit.
    assert repo.find_cross_claim_hashes(session, a_ref, [PHOTO_X]) == {}


def test_find_cross_claim_serials(session):
    a_ref = _claim_with_serial(session, "SN-RP4471")
    b_ref = _claim_with_serial(session, "SN-OTHER")
    session.commit()
    assert repo.find_cross_claim_serials(session, b_ref, ["SN-RP4471"]) == {"SN-RP4471": a_ref}
    assert repo.find_cross_claim_serials(session, a_ref, ["SN-RP4471"]) == {}


def test_node_rejects_reused_photo_citing_prior_claim(migrated_db, test_database_url):
    # Integration: the node's load_fraud_registries hits the DB via the app
    # engine, so it needs DATABASE_URL == TEST_DATABASE_URL.
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL to run the fraud integration test")

    with Session(migrated_db) as s:
        a_ref = _claim_with_photo(s, PHOTO_X)
        s.commit()

    # Claim B reuses the same photo behind a line item.
    b_state = ClaimState(
        claim_ref="CLM-BTEST", policy={}, event={"account_type": "personal"},
        evidence=[EvidenceRecord(evidence_id="IMG-B", capture_stage=CaptureStage.ITEM,
                                 file_ref="fs://b", sha256=PHOTO_X, captured_at=NOW)],
        line_items=[LineItem(item_ref="LI-1", name="Laptop", evidence_refs=["IMG-B"])],
    )
    result = plausibility_check_node(b_state)

    assert result["line_items"] == [], "the reused-photo item must be screened out"
    assert len(result["rejected_items"]) == 1
    reason = result["rejected_items"][0].reasons[0]
    assert a_ref in reason and PHOTO_X in reason


def test_node_allows_unique_photo(migrated_db, test_database_url):
    if settings.database_url != test_database_url:
        pytest.skip("set DATABASE_URL == TEST_DATABASE_URL to run the fraud integration test")
    b_state = ClaimState(
        claim_ref="CLM-CLEAN", policy={}, event={"account_type": "personal"},
        evidence=[EvidenceRecord(evidence_id="IMG-U", capture_stage=CaptureStage.ITEM,
                                 file_ref="fs://u", sha256="f" * 64, captured_at=NOW)],
        line_items=[LineItem(item_ref="LI-1", name="Laptop", evidence_refs=["IMG-U"],
                             value_source="catalog_estimate", unit_value=10.0)],
    )
    result = plausibility_check_node(b_state)
    assert len(result["line_items"]) == 1 and result["rejected_items"] == []
