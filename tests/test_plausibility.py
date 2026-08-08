import pytest
from datetime import datetime, timezone

from schemas import LineItem, DocumentRecord, EvidenceRecord, ValueSource, CaptureStage
from state import ClaimState
from graph import _check_invoice_quantity_ceiling, _check_duplicate_hash

def test_quantity_ceiling_no_op():
    item = LineItem(
        item_ref="LI-1",
        name="Cotton Saree",
        quantity=180,
        matched_document_ids=["DOC-1"]
    )
    docs = [
        DocumentRecord(
            document_id="DOC-1",
            document_type="Invoice",
            file_ref="INV_200",
            uploaded_at=datetime.now(timezone.utc),
            extracted_quantity=200.0
        )
    ]
    
    _check_invoice_quantity_ceiling(item, docs)
    
    assert item.quantity == 180
    assert not item.quantity_capped
    assert len(item.plausibility_notes) == 0

def test_quantity_ceiling_caps_excess():
    item = LineItem(
        item_ref="LI-1",
        name="Cotton Saree",
        quantity=220,
        unit_value=10.0,
        purchase_value=2200.0,
        net_loss=2200.0,
        matched_document_ids=["DOC-1"]
    )
    docs = [
        DocumentRecord(
            document_id="DOC-1",
            document_type="Invoice",
            file_ref="INV_200",
            uploaded_at=datetime.now(timezone.utc),
            extracted_quantity=200.0
        )
    ]
    
    _check_invoice_quantity_ceiling(item, docs)
    
    assert item.quantity == 200
    assert item.quantity_capped is True
    assert item.original_quantity_claimed == 220
    assert item.net_loss == 2000.0
    assert len(item.plausibility_notes) == 1
    assert "Capped to 200" in item.plausibility_notes[0]

def test_quantity_ceiling_multi_invoice_sum():
    item = LineItem(
        item_ref="LI-1",
        name="Cotton Saree",
        quantity=150,
        matched_document_ids=["DOC-1", "DOC-2"]
    )
    docs = [
        DocumentRecord(
            document_id="DOC-1",
            document_type="Invoice",
            file_ref="INV_80",
            uploaded_at=datetime.now(timezone.utc),
            extracted_quantity=80.0
        ),
        DocumentRecord(
            document_id="DOC-2",
            document_type="Invoice",
            file_ref="INV_90",
            uploaded_at=datetime.now(timezone.utc),
            extracted_quantity=90.0
        )
    ]
    
    _check_invoice_quantity_ceiling(item, docs)
    
    assert item.quantity == 150
    assert not item.quantity_capped

def test_duplicate_hash_within_claim():
    state = ClaimState(policy={}, event={})
    state.evidence = [
        EvidenceRecord(evidence_id="IMG-1", capture_stage=CaptureStage.ITEM, file_ref="", sha256="samehash", captured_at=datetime.now(timezone.utc)),
        EvidenceRecord(evidence_id="IMG-2", capture_stage=CaptureStage.ITEM, file_ref="", sha256="samehash", captured_at=datetime.now(timezone.utc))
    ]
    
    item1 = LineItem(item_ref="LI-1", name="A", evidence_refs=["IMG-1"])
    item2 = LineItem(item_ref="LI-2", name="B", evidence_refs=["IMG-2"])
    
    seen_hashes = set()
    mock_hash_registry = {}
    
    err1 = _check_duplicate_hash(item1, state, mock_hash_registry, seen_hashes)
    assert err1 is None
    
    err2 = _check_duplicate_hash(item2, state, mock_hash_registry, seen_hashes)
    assert err2 is not None
    assert "within this claim" in err2

def test_duplicate_hash_against_seeded_registry():
    state = ClaimState(policy={}, event={})
    state.evidence = [
        EvidenceRecord(evidence_id="IMG-1", capture_stage=CaptureStage.ITEM, file_ref="", sha256="badhash", captured_at=datetime.now(timezone.utc)),
    ]
    item = LineItem(item_ref="LI-1", name="A", evidence_refs=["IMG-1"])
    
    seen_hashes = set()
    mock_hash_registry = {"badhash": "Claim #C-99999"}
    
    err = _check_duplicate_hash(item, state, mock_hash_registry, seen_hashes)
    assert err is not None
    assert "Claim #C-99999" in err
