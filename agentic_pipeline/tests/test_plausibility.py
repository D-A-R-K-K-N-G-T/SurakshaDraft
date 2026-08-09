import pytest
from datetime import datetime, timezone

from agentic_pipeline.schemas import LineItem, DocumentRecord, EvidenceRecord, ValueSource, CaptureStage
from agentic_pipeline.state import ClaimState
from agentic_pipeline.graph import (
    _check_invoice_quantity_ceiling, _check_duplicate_hash,
    _clause_is_grounded, _depreciation_for, _check_chronology,
)
from datetime import timezone
from agentic_pipeline.schemas import LineItem as _LineItem


def test_clause_grounded_accepts_paraphrase_overlap():
    src = "Flood damage to stock and machinery is covered. Internal electrical short-circuit is excluded."
    # Verbatim / high-overlap clause is grounded.
    assert _clause_is_grounded("Flood damage to stock and machinery is covered.", src) is True
    # A clause with no overlap is not grounded.
    assert _clause_is_grounded("Loss of cash in transit is covered.", src) is False


def test_depreciation_lookup():
    assert _depreciation_for("Stock") == 0.0
    assert _depreciation_for("Electronics") == 0.25
    # Unknown category -> default
    assert _depreciation_for("Spaceship") == 0.10
    assert _depreciation_for(None) == 0.10


def test_chronology_uses_invoice_date_not_upload():
    from datetime import datetime as _dt
    event = _dt.fromisoformat("2025-12-02T00:00:00+00:00")
    item = _LineItem(item_ref="LI-1", name="X", matched_document_ids=["DOC-1"])
    # Uploaded after the loss (normal) but invoice_date BEFORE the loss -> no flag.
    ok_doc = DocumentRecord(document_id="DOC-1", document_type="Invoice", file_ref="f",
                            uploaded_at=_dt.fromisoformat("2025-12-05T00:00:00+00:00"),
                            invoice_date=_dt.fromisoformat("2025-11-01T00:00:00+00:00"))
    assert _check_chronology(item, event, [ok_doc]) is None
    # Invoice dated AFTER the loss -> flag.
    bad_doc = DocumentRecord(document_id="DOC-1", document_type="Invoice", file_ref="f",
                             uploaded_at=_dt.fromisoformat("2025-12-05T00:00:00+00:00"),
                             invoice_date=_dt.fromisoformat("2025-12-10T00:00:00+00:00"))
    assert _check_chronology(item, event, [bad_doc]) is not None

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

def test_duplicate_hash_shared_within_claim_is_allowed():
    # Two line items extracted from the SAME scene photo legitimately share an
    # evidence_ref (and therefore a sha256). This must NOT be flagged — only a
    # cross-claim collision (via the registry) is fraud.
    state = ClaimState(policy={}, event={})
    state.evidence = [
        EvidenceRecord(evidence_id="IMG-1", capture_stage=CaptureStage.ITEM, file_ref="", sha256="samehash", captured_at=datetime.now(timezone.utc)),
    ]

    item1 = LineItem(item_ref="LI-1", name="A", evidence_refs=["IMG-1"])
    item2 = LineItem(item_ref="LI-2", name="B", evidence_refs=["IMG-1"])

    mock_hash_registry = {}

    assert _check_duplicate_hash(item1, state, mock_hash_registry) is None
    assert _check_duplicate_hash(item2, state, mock_hash_registry) is None

def test_duplicate_hash_against_seeded_registry():
    state = ClaimState(policy={}, event={})
    state.evidence = [
        EvidenceRecord(evidence_id="IMG-1", capture_stage=CaptureStage.ITEM, file_ref="", sha256="badhash", captured_at=datetime.now(timezone.utc)),
    ]
    item = LineItem(item_ref="LI-1", name="A", evidence_refs=["IMG-1"])

    mock_hash_registry = {"badhash": "Claim #C-99999"}

    err = _check_duplicate_hash(item, state, mock_hash_registry)
    assert err is not None
    assert "Claim #C-99999" in err
