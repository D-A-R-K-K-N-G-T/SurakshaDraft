import pytest
from datetime import datetime, timezone
import json
import os
import hashlib

from graph import graph
from schemas import (
    CaptureStage, DocumentRecord, EvidenceRecord, LineItem, 
    VisionMissingSignal, ValuationOutput, PolicyOutput, 
    ReconciliationOutput, DraftOutput, QCGuardOutput, ValueSource, PolicyStatus, PendingVerificationItem
)
from state import ClaimState

@pytest.fixture
def scenario_a_state():
    state = ClaimState(
        policy={
            "product": "Bharat Sookshma Udyam Suraksha",
            "start_date": "2025-01-01T00:00:00Z",
            "end_date": "2025-12-31T23:59:59Z",
            "sum_insured_stock": 1200000,
            "excess": 5000,
            "clauses": "Flood damage to stock and machinery is covered. Internal electrical short-circuit is excluded.",
            "asset_categories": ["Stock", "Furniture, Fixtures & Fittings", "Plant & Machinery"],
            "premises_geo": {"lat": 10.0, "lon": 10.0}
        },
        event={
            "event_date": "2025-12-02T04:30:00Z",
            "description": "Heavy overnight rainfall caused inundation."
        },
        evidence=[
            EvidenceRecord(
                evidence_id="IMG-001",
                capture_stage=CaptureStage.ITEM,
                file_ref="dummy1.jpg",
                sha256="mockhash1",
                captured_at=datetime.fromisoformat("2025-12-02T10:00:00+00:00"),
                geotag={"lat": 10.0, "lon": 10.0}
            ),
            EvidenceRecord(
                evidence_id="IMG-002",
                capture_stage=CaptureStage.ITEM,
                file_ref="dummy2.jpg", 
                sha256="mockhash2",
                captured_at=datetime.fromisoformat("2025-12-02T10:05:00+00:00"),
                geotag={"lat": 10.0, "lon": 10.0}
            )
        ],
        documents=[
            DocumentRecord(
                document_id="DOC-INV-1187",
                document_type="Invoice",
                file_ref="INV/2025/1187",
                uploaded_at=datetime.fromisoformat("2025-11-01T10:00:00+00:00"),
                extracted_quantity=180.0
            ),
            DocumentRecord(
                document_id="DOC-INV-2201",
                document_type="Invoice",
                file_ref="INV/2025/2201",
                uploaded_at=datetime.fromisoformat("2025-11-05T10:00:00+00:00"),
                extracted_quantity=1.0
            ),
            DocumentRecord(
                document_id="DOC-REG-001",
                document_type="PremiumReceipt",
                file_ref="Receipt",
                uploaded_at=datetime.fromisoformat("2025-01-02T10:00:00+00:00")
            ),
            DocumentRecord(
                document_id="DOC-REG-002",
                document_type="Stock Register",
                file_ref="Stock Register Page 12",
                uploaded_at=datetime.fromisoformat("2025-12-01T10:00:00+00:00")
            )
        ]
    )

    state.line_items = [
        LineItem(
            item_ref="LI-1",
            name="Cotton sarees",
            description="Water-stained",
            category="Stock",
            quantity=180,
            evidence_refs=["IMG-001"]
        ),
        LineItem(
            item_ref="LI-4",
            name="Steam press machine",
            description="Steam Press",
            serial_number="SN-RP4471",
            category="Plant & Machinery",
            quantity=1,
            evidence_refs=["IMG-002"]
        )
    ]
    state.pending_signals = [
        VisionMissingSignal(
            item_label_guess="Fabric rolls",
            location_notes="Empty rack covered in debris",
            evidence_refs=[]
        )
    ]
    state.vision_processed_evidence_ids = ["IMG-001", "IMG-002"]
    
    return state

def test_graph_smoke(monkeypatch, scenario_a_state):
    # Setup dummy files for hash check
    with open("dummy1.jpg", "wb") as f: f.write(b"1")
    with open("dummy2.jpg", "wb") as f: f.write(b"2")
    scenario_a_state.evidence[0].sha256 = hashlib.sha256(b"1").hexdigest()
    scenario_a_state.evidence[1].sha256 = hashlib.sha256(b"2").hexdigest()

    import graph as g
    
    class MockLLM:
        def __init__(self, schema):
            self.schema = schema
        def invoke(self, messages, config=None):
            if self.schema == ValuationOutput:
                out_items = []
                for i in scenario_a_state.line_items:
                    li = LineItem(**i.model_dump())
                    li.value_source = ValueSource.INVOICE_MATCHED
                    if li.item_ref == "LI-1":
                        li.unit_value = 1450
                        li.purchase_value = 1450 * li.quantity
                        li.net_loss = 1450 * li.quantity
                        li.matched_document_ids = ["DOC-INV-1187"]
                    elif li.item_ref == "LI-4":
                        li.unit_value = 34000
                        li.purchase_value = 34000
                        li.net_loss = 34000
                        li.matched_document_ids = ["DOC-INV-2201"]
                    out_items.append(li)
                return ValuationOutput(items=out_items)
            elif self.schema == PolicyOutput:
                out_items = []
                for i in scenario_a_state.line_items:
                    li = LineItem(**i.model_dump())
                    li.policy_status = PolicyStatus.COVERED
                    out_items.append(li)
                return PolicyOutput(items=out_items)
            elif self.schema == ReconciliationOutput:
                return ReconciliationOutput(pending_items=[
                    PendingVerificationItem(
                        item_label="Fabric rolls",
                        quantity_claimed=28,
                        claimed_total=91000,
                        supporting_documents=["DOC-REG-002"]
                    )
                ])
            elif self.schema == DraftOutput:
                return DraftOutput(
                    main_schedule="Mock schedule",
                    rejected_items_annexure="Mock rejected",
                    pending_verification_annexure="Mock pending"
                )
            elif self.schema == QCGuardOutput:
                return QCGuardOutput(pass_qc=True, flags=[])

    g.get_structured_llm = lambda schema, **kwargs: MockLLM(schema)
    
    result = g.graph.invoke(scenario_a_state.model_dump())
    
    # Assert LI-4 ends up in rejected_items with a serial-based reason
    rejected = result.get("rejected_items", [])
    li4_rejected = next((r for r in rejected if (r.item_ref if hasattr(r, "item_ref") else r["item_ref"]) == "LI-4"), None)
    assert li4_rejected is not None
    reasons = li4_rejected.reasons if hasattr(li4_rejected, "reasons") else li4_rejected["reasons"]
    assert any("Duplicate serial SN-RP4471" in reason for reason in reasons)
    
    # Assert pending_verification contains the fabric-rolls entry
    pending = result.get("pending_verification", [])
    assert any("Fabric rolls" in (p.item_label if hasattr(p, "item_label") else p["item_label"]) for p in pending)
    
    # Assert draft_pack is non-null
    draft = result.get("draft_pack")
    assert draft is not None
    assert (draft.main_schedule if hasattr(draft, "main_schedule") else draft["main_schedule"]) == "Mock schedule"
    
    # Assert proof_receipt has a receipt_hash
    receipt = result.get("proof_receipt")
    assert receipt is not None
    assert "receipt_hash" in receipt
    
    # Save to scenario files to fulfill the requirement
    with open("scenario_A_mixed_case.json", "w") as f:
        json.dump(scenario_a_state.model_dump(), f, default=str, indent=2)
        
    os.remove("dummy1.jpg")
    os.remove("dummy2.jpg")
