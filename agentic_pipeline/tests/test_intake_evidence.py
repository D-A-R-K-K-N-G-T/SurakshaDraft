import pytest
from datetime import datetime, timezone
import hashlib
import os

from agentic_pipeline.graph import evidence_verify_node, intake_node
from agentic_pipeline.state import ClaimState
from agentic_pipeline.schemas import EvidenceRecord, CaptureStage, DocumentRecord

def test_intake_node_valid():
    state = ClaimState(
        policy={
            "start_date": "2025-01-01T00:00:00Z",
            "end_date": "2025-12-31T23:59:59Z",
        },
        event={
            "event_date": "2025-06-01T12:00:00Z"
        },
        documents=[
            DocumentRecord(
                document_id="DOC-1",
                document_type="PremiumReceipt",
                file_ref="path",
                uploaded_at=datetime.now(timezone.utc)
            )
        ]
    )
    res = intake_node(state)
    assert res["intake_ok"] is True

def test_intake_node_expired_date():
    state = ClaimState(
        policy={
            "start_date": "2024-01-01T00:00:00Z",
            "end_date": "2024-12-31T23:59:59Z",
        },
        event={
            "event_date": "2025-06-01T12:00:00Z"
        },
        documents=[
            DocumentRecord(
                document_id="DOC-1",
                document_type="PremiumReceipt",
                file_ref="path",
                uploaded_at=datetime.now(timezone.utc)
            )
        ]
    )
    res = intake_node(state)
    assert res["intake_ok"] is False

def test_intake_node_null_event_date():
    # A JSON null event_date used to raise AttributeError (None.replace) and kill
    # the run; now it must fail intake cleanly with a reason.
    state = ClaimState(
        policy={"start_date": "2025-01-01T00:00:00Z", "end_date": "2025-12-31T23:59:59Z"},
        event={"event_date": None},
        documents=[
            DocumentRecord(document_id="DOC-1", document_type="PremiumReceipt",
                           file_ref="path", uploaded_at=datetime.now(timezone.utc))
        ],
    )
    res = intake_node(state)
    assert res["intake_ok"] is False
    assert any("Event date" in r for r in res["intake_reasons"])


def test_intake_node_no_receipt_is_not_a_gate():
    # The old "no premium receipt" gate was a control in name only — the gateway
    # fabricated a receipt for every claim. It has been removed: a missing
    # receipt no longer fails intake (a warning is emitted instead).
    state = ClaimState(
        policy={
            "start_date": "2025-01-01T00:00:00Z",
            "end_date": "2025-12-31T23:59:59Z",
        },
        event={
            "event_date": "2025-06-01T12:00:00Z"
        },
        documents=[]
    )
    res = intake_node(state)
    assert res["intake_ok"] is True
    assert any("Premium payment" in w for w in res["warnings"])


def test_intake_merges_gate_reasons():
    # Blocking findings from document_triage and the gateway must flow into the
    # intake decision and its reasons.
    state = ClaimState(
        policy={"start_date": "2025-01-01T00:00:00Z", "end_date": "2025-12-31T23:59:59Z"},
        event={"event_date": "2025-06-01T12:00:00Z"},
        documents=[],
        doc_gate_reasons=["The file uploaded as your policy schedule appears to be a menu."],
        gateway_blocking_reasons=["The same file was uploaded for more than one document."],
    )
    res = intake_node(state)
    assert res["intake_ok"] is False
    assert any("menu" in r for r in res["intake_reasons"])
    assert any("same file" in r for r in res["intake_reasons"])


def test_intake_boundary_date_is_warning_not_rejection():
    # 12 hours after end_date, within the 1-day tolerance -> warn, still ok.
    state = ClaimState(
        policy={"start_date": "2025-01-01T00:00:00Z", "end_date": "2025-12-31T23:59:59Z"},
        event={"event_date": "2026-01-01T11:00:00Z"},
        documents=[],
    )
    res = intake_node(state)
    assert res["intake_ok"] is True
    assert any("boundary" in w for w in res["warnings"])
    # 3 days out -> still rejected.
    state.event = {"event_date": "2026-01-03T12:00:00Z"}
    assert intake_node(state)["intake_ok"] is False

def test_evidence_verify_node():
    # Setup a dummy file
    with open("dummy.jpg", "wb") as f:
        f.write(b"dummy")
    actual_hash = hashlib.sha256(b"dummy").hexdigest()
    
    state = ClaimState(
        policy={
            "premises_geo": {"lat": 10.0, "lon": 10.0}
        },
        event={
            "event_date": "2025-06-01T12:00:00Z"
        },
        evidence=[
            # 1. valid
            EvidenceRecord(
                evidence_id="E-1", capture_stage=CaptureStage.ITEM, file_ref="dummy.jpg", 
                sha256=actual_hash, captured_at=datetime.fromisoformat("2025-06-01T14:00:00+00:00"),
                geotag={"lat": 10.0, "lon": 10.0}
            ),
            # 2. hash mismatch
            EvidenceRecord(
                evidence_id="E-2", capture_stage=CaptureStage.ITEM, file_ref="dummy.jpg", 
                sha256="wronghash", captured_at=datetime.fromisoformat("2025-06-01T14:00:00+00:00"),
                geotag={"lat": 10.0, "lon": 10.0}
            ),
            # 3. geofence miss (lat 11 is far)
            EvidenceRecord(
                evidence_id="E-3", capture_stage=CaptureStage.ITEM, file_ref="dummy.jpg", 
                sha256=actual_hash, captured_at=datetime.fromisoformat("2025-06-01T14:00:00+00:00"),
                geotag={"lat": 11.0, "lon": 10.0}
            ),
            # 4. out of window (10 days later)
            EvidenceRecord(
                evidence_id="E-4", capture_stage=CaptureStage.ITEM, file_ref="dummy.jpg", 
                sha256=actual_hash, captured_at=datetime.fromisoformat("2025-06-11T14:00:00+00:00"),
                geotag={"lat": 10.0, "lon": 10.0}
            ),
        ]
    )
    
    evidence_verify_node(state)
    
    assert state.evidence[0].verified is True
    assert state.evidence[1].verified is False
    assert any("Hash mismatch" in r for r in state.evidence[1].verification_reasons)
    
    assert state.evidence[2].verified is False
    assert any("Geofence miss" in r for r in state.evidence[2].verification_reasons)
    
    assert state.evidence[3].verified is False
    assert any("Timestamp out of bounds" in r for r in state.evidence[3].verification_reasons)
    
    os.remove("dummy.jpg")
