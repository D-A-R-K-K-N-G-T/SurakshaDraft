"""Phase 7 repository/node tests: users, policies, the now-live geofence, listing.

Repository-level with a Session on the test engine (no app-engine coupling).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentic_pipeline import models as M
from agentic_pipeline import repository as repo
from agentic_pipeline.graph import evidence_verify_node
from agentic_pipeline.schemas import CaptureStage, EvidenceRecord, GeoPoint
from agentic_pipeline.service import PolicyCreateRequest, SumInsuredItem
from agentic_pipeline.state import ClaimState

NOW = datetime.now(timezone.utc)


@pytest.fixture()
def session(migrated_db) -> Session:
    with Session(migrated_db) as s:
        yield s


def test_upsert_user_is_get_or_create(session):
    a = repo.upsert_user(session, provider="demo", subject="abc", email="a@b.com")
    session.commit()
    again = repo.upsert_user(session, provider="demo", subject="abc")
    other = repo.upsert_user(session, provider="demo", subject="xyz")
    session.commit()
    assert a == again and other != a
    assert session.scalar(select(func.count()).select_from(M.User)) == 2


def test_create_policy_roundtrip(session):
    pid = repo.create_policy(session, user_id=None, payload=PolicyCreateRequest(
        asset_categories=["Stock"], clauses=["Flood damage to stock is covered."],
        premises_lat=19.076, premises_lon=72.877,
        sums_insured=[SumInsuredItem(category_key="sum_insured_stock",
                                     category_label="Stock", amount=1_000_000)],
    ))
    session.commit()
    p = session.get(M.Policy, pid)
    assert p.premises_lat == 19.076 and p.clauses_assumed is True
    assert session.scalar(select(func.count()).select_from(M.PolicySumInsured)) == 1
    assert session.scalar(select(func.count()).select_from(M.PolicyClause)) == 1
    assert repo.get_policy_terms(session, pid)["premises_geo"] == {"lat": 19.076, "lon": 72.877}


def test_geofence_goes_live_from_policy(session):
    # Policy premises in Delhi; the photo geotag is in Mumbai (~1150 km away).
    pid = repo.create_policy(session, user_id=None, payload=PolicyCreateRequest(
        premises_lat=28.6139, premises_lon=77.2090))
    session.commit()

    state = ClaimState(
        policy={}, event={},
        evidence=[EvidenceRecord(evidence_id="IMG", capture_stage=CaptureStage.ITEM,
                                 file_ref="", sha256="x" * 64, captured_at=NOW,
                                 geotag=GeoPoint(lat=19.076, lon=72.877))],
    )
    repo.create_claim(session, state=state, policy_id=pid)
    session.commit()

    # create_claim merged premises_geo into the claim policy -> geofence is live.
    assert state.policy.get("premises_geo") == {"lat": 28.6139, "lon": 77.2090}
    ev = evidence_verify_node(state)["evidence"][0]
    assert ev.verified is False
    assert any("Geofence miss" in r for r in ev.verification_reasons)


def test_list_claims_pagination(session):
    uid = repo.upsert_user(session, provider="demo", subject="lister")
    session.commit()
    refs = []
    for i in range(3):
        ref, _ = repo.create_claim(session, state=ClaimState(
            policy={}, event={"account_type": "personal", "description": f"claim {i}"},
        ), user_id=uid)
        session.commit()  # distinct created_at per claim (now() is per-transaction)
        refs.append(ref)

    page1, cursor = repo.list_claims(session, user_id=uid, limit=2)
    assert len(page1) == 2 and cursor is not None
    page2, cursor2 = repo.list_claims(session, user_id=uid, limit=2, cursor=cursor)
    assert len(page2) == 1 and cursor2 is None
    assert {c["claim_ref"] for c in page1 + page2} == set(refs)
