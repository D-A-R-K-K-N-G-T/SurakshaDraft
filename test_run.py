import json
import uuid
import os
from dotenv import load_dotenv

# .env lives at the repo root (next to this file), not inside agentic_pipeline/.
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from agentic_pipeline.graph import graph

def run_test():
    import hashlib
    
    TEST_IMAGE_PATH = "broken_bat.jpg"
    
    evidence_list = []
    if os.path.exists(TEST_IMAGE_PATH):
        with open(TEST_IMAGE_PATH, "rb") as f:
            file_hash = hashlib.sha256(f.read()).hexdigest()
        evidence_list.append({
            "evidence_id": "IMG-001",
            "capture_stage": "scene",
            "file_ref": TEST_IMAGE_PATH,
            "sha256": file_hash,
            "captured_at": "2026-08-01T12:00:00Z"
        })
    else:
        print(f"Warning: '{TEST_IMAGE_PATH}' not found in the directory. Vision AI will skip extracting items.")

    print("Initializing Test Claim State...")
    
    # Create a mock ClaimState
    test_state = {
        "policy": {
            "product": "Standard Flood Protection",
            "start_date": "2025-01-01T00:00:00Z",
            "end_date": "2026-12-31T23:59:59Z",
            "sum_insured_stock": 1000000,
            "clauses": "Flood damage to sporting goods, machinery, and electronics is covered.",
            "asset_categories": ["Sporting goods", "Machinery", "Electronics"]
        },
        "event": {
            "event_date": "2026-08-01T00:00:00Z",
            "description": "Shop flooded due to heavy rains."
        },
        "evidence": evidence_list,
        "documents": [
            {
                "document_id": "DOC-INV-101",
                "document_type": "Invoice",
                "file_ref": "mock/path/invoice.pdf",
                "uploaded_at": "2026-08-02T12:00:00Z"
            },
            {
                "document_id": "DOC-RECEIPT-1",
                "document_type": "PremiumReceipt",
                "file_ref": "system",
                "uploaded_at": "2026-08-02T12:00:00Z"
            }
        ],
        "line_items": [] # Vision will populate this
    }

    print("Starting LangGraph Execution...")
    
    try:
        final_state = graph.invoke(test_state)
        
        print("\n=== DEBUG: EVIDENCE STATUS ===")
        import json
        print(json.dumps(final_state.get("evidence", []), indent=2, default=str))
            
        print("\n=== DEBUG: LINE ITEMS EXTRACTED ===")
        print(json.dumps(final_state.get("line_items", []), indent=2, default=str))
        
        print("\nExecution Complete!")
        print("-" * 50)
        draft_pack = final_state.get("draft_pack")
        print("DRAFT PACK GENERATED:")
        if draft_pack:
            print(draft_pack.main_schedule)
            print("\nREJECTED ITEMS:")
            print(draft_pack.rejected_items_annexure)
        else:
            print("None")

        
    except Exception as e:
        import traceback
        print(f"\nExecution Failed: {e}")
        print(traceback.format_exc())

if __name__ == "__main__":
    run_test()
