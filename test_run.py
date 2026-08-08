from datetime import datetime
from graph import graph
from schemas import CaptureStage, DocumentRecord, EvidenceRecord
from state import ClaimState

def main():
    state = ClaimState(
        policy={
            "product": "Bharat Sookshma Udyam Suraksha",
            "sum_insured_stock": 1200000,
            "excess": 5000,
            "clauses": "Flood damage to stock and machinery is covered. Internal electrical short-circuit is excluded. Missing property whose disappearance can be linked to the flood event is covered.",
            "asset_categories": ["Stock", "Furniture, Fixtures & Fittings", "Plant & Machinery"]
        },
        event={
            "event_date": "2025-12-02T04:30:00Z",
            "description": "Heavy overnight rainfall caused inundation of Thanikachalam Road; floodwater entered the shop."
        },
        evidence=[
            EvidenceRecord(
                evidence_id="IMG-001",
                capture_stage=CaptureStage.ITEM,
                file_ref="data:image/jpeg;base64,...",
                sha256="mockhash1",
                captured_at=datetime.utcnow(),
                verified=True
            ),
            EvidenceRecord(
                evidence_id="IMG-002",
                capture_stage=CaptureStage.ITEM,
                file_ref="data:image/jpeg;base64,...", 
                sha256="mockhash2",
                captured_at=datetime.utcnow(),
                verified=True
            )
        ],
        documents=[
            DocumentRecord(
                document_id="DOC-INV-1187",
                document_type="Invoice",
                file_ref="INV/2025/1187",
                uploaded_at=datetime.utcnow()
            ),
            DocumentRecord(
                document_id="DOC-INV-2201",
                document_type="Invoice",
                file_ref="INV/2025/2201",
                uploaded_at=datetime.utcnow()
            ),
            DocumentRecord(
                document_id="DOC-REG-001",
                document_type="Stock Register",
                file_ref="Stock Register Page 12",
                uploaded_at=datetime.utcnow()
            )
        ]
    )

    from schemas import LineItem, VisionMissingSignal
    
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
            description="Serial SN-RP4471",
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
    
    # Monkeypatch LLM for testing without valid API key
    import graph
    class MockLLM:
        def __init__(self, schema):
            self.schema = schema
        def invoke(self, messages):
            from schemas import ValuationOutput, PolicyOutput, ReconciliationOutput, DraftOutput, QCGuardOutput, ValueSource, PolicyStatus, PendingVerificationItem, LineItem
            
            if self.schema == ValuationOutput:
                # Return prices
                out_items = []
                for i in state.line_items:
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
                    else:
                        li.value_source = ValueSource.UNVALUED
                    out_items.append(li)
                return ValuationOutput(items=out_items)
            elif self.schema == PolicyOutput:
                out_items = []
                for i in state.line_items:
                    li = LineItem(**i.model_dump())
                    if li.item_ref == "LI-1":
                        li.policy_status = PolicyStatus.COVERED
                    else:
                        li.policy_status = PolicyStatus.REVIEW
                    out_items.append(li)
                return PolicyOutput(items=out_items)
            elif self.schema == ReconciliationOutput:
                return ReconciliationOutput(pending_items=[
                    PendingVerificationItem(
                        item_label="Fabric rolls",
                        quantity_claimed=28,
                        claimed_total=91000,
                        supporting_documents=["DOC-REG-001"]
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

    graph.get_structured_llm = lambda schema, **kwargs: MockLLM(schema)
    
    print("Invoking graph...")
    # Because vision node requires actual images and base64 parsing which fails on dummy data,
    # we bypass vision by overriding the entry point to valuation_agent
    
    result = graph.graph.invoke(state.model_dump())
    
    import json
    print("\n--- Final Draft Pack ---")
    draft = result.get("draft_pack")
    if draft:
        print(json.dumps(draft, indent=2))
        
    print("\n--- Final Receipt ---")
    print(json.dumps(result.get("proof_receipt"), indent=2))
    
    print("\n--- Rejected Items ---")
    for r in result.get("rejected_items", []):
        print(f"{r['item_ref']}: {r['reasons']}")
        
    print("\n--- Pending Verification ---")
    for p in result.get("pending_verification", []):
        print(f"{p['item_label']}: {p['claimed_total']}")

if __name__ == "__main__":
    main()
