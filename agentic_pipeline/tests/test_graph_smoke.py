"""
Full-graph smoke test that actually exercises vision + document extraction,
rather than pre-seeding line_items and skipping vision (which the old version
did). All LLM calls are mocked; image loads use real temp files so the vision
and document_extract nodes run their real bodies.

Scenario:
  - Vision extracts 3 items from one scene photo:
      LI-1 Cotton sarees (Stock, qty 180)          -> valued, COVERED
      LI-2 Steam press   (Plant & Machinery, serial SN-RP4471, dup) -> REJECTED (serial)
      LI-3 Wiring panel  (Electronics)             -> EXCLUDED by policy
  - One invoice is OCR'd to give the saree unit value.
Asserts the item lands in the right bucket and depreciation is applied.
"""
import hashlib
import pytest
from datetime import datetime, timezone

from agentic_pipeline.graph import graph
import agentic_pipeline.graph as g
from agentic_pipeline.schemas import (
    CaptureStage, DocumentRecord, EvidenceRecord,
    VisionOutput, VisionCandidateItem,
    DocumentExtractionOutput, LLMDocumentExtraction,
    ValuationOutput, LLMValuationItem,
    PolicyOutput, LLMPolicyItem,
    ReconciliationOutput,
    DraftOutput, QCGuardOutput,
    PolicyStatus, ClaimTypeClassification,
)
from agentic_pipeline.state import ClaimState
from agentic_pipeline.config import settings


class MockLLM:
    def __init__(self, schema):
        self.schema = schema

    def invoke(self, messages, config=None):
        if self.schema == VisionOutput:
            return VisionOutput(items=[
                VisionCandidateItem(name="Cotton sarees", category="Stock", quantity=180,
                                    vision_confidence=0.9, evidence_refs=["IMG-001"]),
                VisionCandidateItem(name="Steam press", category="Plant & Machinery", quantity=1,
                                    serial_number="SN-RP4471", vision_confidence=0.95, evidence_refs=["IMG-001"]),
                VisionCandidateItem(name="Wiring panel", category="Electronics", quantity=1,
                                    vision_confidence=0.8, evidence_refs=["IMG-001"]),
            ], missing_signals=[], anomalies=[])
        if self.schema == DocumentExtractionOutput:
            return DocumentExtractionOutput(documents=[
                LLMDocumentExtraction(document_id="DOC-INV-1", unit_value=1000.0,
                                      quantity=180, description="Cotton Sarees"),
            ])
        if self.schema == ValuationOutput:
            # Value only the sarees; the press and panel find no invoice.
            return ValuationOutput(items=[
                LLMValuationItem(item_ref="LI-1", unit_value=1000.0, matched_document_ids=["DOC-INV-1"]),
                LLMValuationItem(item_ref="LI-2", unit_value=None, matched_document_ids=[]),
                LLMValuationItem(item_ref="LI-3", unit_value=None, matched_document_ids=[]),
            ])
        if self.schema == PolicyOutput:
            return PolicyOutput(items=[
                LLMPolicyItem(item_ref="LI-1", policy_status=PolicyStatus.COVERED,
                              policy_clause="Flood damage to stock and machinery is covered."),
                LLMPolicyItem(item_ref="LI-2", policy_status=PolicyStatus.COVERED,
                              policy_clause="Flood damage to stock and machinery is covered."),
                LLMPolicyItem(item_ref="LI-3", policy_status=PolicyStatus.EXCLUDED,
                              policy_clause="Internal electrical short-circuit is excluded."),
            ])
        if self.schema == ReconciliationOutput:
            return ReconciliationOutput(pending_items=[])
        if self.schema == DraftOutput:
            # One ref per section, matching the expected buckets so the
            # deterministic QC check passes:
            #   valid (covered/review) = {LI-1}, excluded = {LI-3}, rejected = {LI-2}
            return DraftOutput(
                main_schedule="LI-1: Cotton sarees.",
                excluded_items_annexure="LI-3: Wiring panel — excluded.",
                rejected_items_annexure="LI-2: Steam press — rejected.",
                pending_verification_annexure="None",
            )
        if self.schema == QCGuardOutput:
            return QCGuardOutput(pass_qc=True, flags=[])
        if self.schema == ClaimTypeClassification:
            return ClaimTypeClassification(claim_type_id="fire", confidence=0.95,
                                           rationale="Flood damage to stock.")
        raise AssertionError(f"Unexpected schema in MockLLM: {self.schema}")


@pytest.fixture
def scenario_state(tmp_path):
    photo = tmp_path / "scene.jpg"
    photo.write_bytes(b"scene-image-bytes")
    invoice = tmp_path / "invoice.jpg"
    invoice.write_bytes(b"invoice-image-bytes")

    photo_hash = hashlib.sha256(photo.read_bytes()).hexdigest()

    state = ClaimState(
        policy={
            "start_date": "2025-01-01T00:00:00Z",
            "end_date": "2025-12-31T23:59:59Z",
            "clauses": "Flood damage to stock and machinery is covered. Internal electrical short-circuit is excluded.",
            "asset_categories": ["Stock", "Plant & Machinery", "Electronics"],
        },
        event={"event_date": "2025-12-02T04:30:00Z"},
        evidence=[
            EvidenceRecord(
                evidence_id="IMG-001", capture_stage=CaptureStage.SCENE, file_ref=str(photo),
                sha256=photo_hash, captured_at=datetime.fromisoformat("2025-12-02T10:00:00+00:00"),
            ),
        ],
        documents=[
            DocumentRecord(document_id="DOC-INV-1", document_type="Invoice", file_ref=str(invoice),
                           uploaded_at=datetime.fromisoformat("2025-12-03T10:00:00+00:00")),
            DocumentRecord(document_id="DOC-REG-001", document_type="PremiumReceipt", file_ref="system",
                           uploaded_at=datetime.fromisoformat("2025-01-02T10:00:00+00:00")),
        ],
    )
    return state


def test_graph_smoke(monkeypatch, scenario_state):
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **kwargs: MockLLM(schema))
    # Cross-claim fraud registries come from the DB now (Phase 6); inject the
    # seeded serial registry so the dup-serial screen fires without a database.
    monkeypatch.setattr(g, "load_fraud_registries",
                        lambda state: ({}, {"SN-RP4471": "Claim #C-88210"}))
    # This scenario deliberately carries only an invoice — no ID, no policy
    # schedule — so the LOR gate upstream would legitimately pause it. That gate
    # has its own coverage in test_claim_type.py; here we want the assessment
    # path that runs AFTER it, so the gate is put in warn_only for this test.
    monkeypatch.setattr(settings, "lor_gate_mode", "warn_only")

    result = graph.invoke(scenario_state.model_dump())

    # Node-written channels come back as Pydantic objects; reserve_estimate and
    # proof_receipt are plain dicts.
    line_items = {li.item_ref: li for li in result["line_items"]}

    # LI-1 sarees: valued from the OCR'd invoice, with depreciation applied
    # (Stock depreciation is 0.0, so net_loss == purchase_value == 180 * 1000).
    assert "LI-1" in line_items
    li1 = line_items["LI-1"]
    assert li1.value_source.value == "invoice_matched"
    assert li1.quantity == 180
    assert li1.net_loss == 180 * 1000.0
    assert li1.policy_status == PolicyStatus.COVERED

    # LI-2 steam press: rejected for the duplicate serial (dup-serial check revived)
    rejected = {r.item_ref for r in result["rejected_items"]}
    assert "LI-2" in rejected
    li2_reasons = next(r for r in result["rejected_items"] if r.item_ref == "LI-2").reasons
    assert any("Duplicate serial" in reason for reason in li2_reasons)

    # LI-3 wiring panel: EXCLUDED by policy — must survive to line_items (not rejected)
    assert "LI-3" in line_items
    assert line_items["LI-3"].policy_status == PolicyStatus.EXCLUDED
    assert "LI-3" not in rejected

    # Reserve estimate now has an excluded bucket
    reserve = result["reserve_estimate"]
    assert reserve["confirmed"] == 180 * 1000.0
    assert "excluded" in reserve

    # QC passed (draft refs match the buckets), pack shipped with a receipt
    assert result["qc"].pass_qc is True
    assert result.get("draft_pack") is not None
    assert result.get("proof_receipt") is not None


def test_policy_extract_overrides_assumed_terms(monkeypatch, tmp_path):
    """The uploaded schedule is authoritative over gateway-assumed terms."""
    from agentic_pipeline.schemas import PolicyExtractionOutput, LLMSumInsured
    from datetime import datetime as _dt

    pdf = tmp_path / "policy.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")

    class PolicyLLM:
        def invoke(self, messages, config=None):
            return PolicyExtractionOutput(
                policy_number="POL-1",
                start_date=_dt.fromisoformat("2026-04-01T00:00:00+00:00"),
                end_date=_dt.fromisoformat("2027-03-31T00:00:00+00:00"),
                excess=5000,
                sums_insured=[LLMSumInsured(category="Plant & Machinery", amount=800000)],
                clauses=["Flood damage to stock is covered.", "Loss of cash is excluded."],
            )

    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **kwargs: PolicyLLM())

    state = ClaimState(
        # Gateway-assumed values that must be replaced by the document.
        policy={"start_date": "2025-01-01T00:00:00Z", "end_date": "2026-12-31T23:59:59Z",
                "clauses": "Flood damage to stock and machinery is covered."},
        event={},
        documents=[DocumentRecord(document_id="DOC-POL-1", document_type="PolicyDocument",
                                  file_ref=str(pdf), uploaded_at=datetime.now(timezone.utc))],
    )
    out = g.policy_extract_node(state)
    p = out["policy"]

    assert p["start_date"].startswith("2026-04-01")
    assert p["end_date"].startswith("2027-03-31")
    assert p["excess"] == 5000
    assert p["sum_insured_machinery"] == 800000
    assert p["terms_source"] == "policy_document"
    # Clauses become the verbatim grounding corpus for the coverage agent.
    assert "Loss of cash is excluded." in p["clauses"]


def test_policy_extract_no_document_is_noop(tmp_path):
    state = ClaimState(policy={"clauses": "assumed"}, event={}, documents=[])
    assert g.policy_extract_node(state) == {}


def test_assumed_clauses_cap_covered_to_review(monkeypatch):
    """An item can never be COVERED off gateway-assumed generic terms."""
    from agentic_pipeline.schemas import PolicyOutput, LLMPolicyItem
    from agentic_pipeline.schemas import LineItem

    class PolicyLLM:
        def invoke(self, messages, config=None):
            return PolicyOutput(items=[
                LLMPolicyItem(item_ref="LI-1", policy_status=PolicyStatus.COVERED,
                              policy_clause="Flood damage to stock and machinery is covered."),
            ])

    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: PolicyLLM())
    state = ClaimState(
        policy={"clauses": "Flood damage to stock and machinery is covered.", "clauses_assumed": True},
        event={},
    )
    state.line_items = [LineItem(item_ref="LI-1", name="Sarees", category="Stock")]
    out = g.policy_agent_node(state)
    assert out["line_items"][0].policy_status == PolicyStatus.REVIEW
    assert any("assumed" in w for w in out.get("warnings", []))


def test_real_clauses_still_yield_covered(monkeypatch):
    """Without the clauses_assumed flag, a covered item stays covered (guards
    the smoke fixture's contract)."""
    from agentic_pipeline.schemas import PolicyOutput, LLMPolicyItem, LineItem

    class PolicyLLM:
        def invoke(self, messages, config=None):
            return PolicyOutput(items=[
                LLMPolicyItem(item_ref="LI-1", policy_status=PolicyStatus.COVERED,
                              policy_clause="Flood damage to stock and machinery is covered."),
            ])

    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: PolicyLLM())
    state = ClaimState(policy={"clauses": "Flood damage to stock and machinery is covered."}, event={})
    state.line_items = [LineItem(item_ref="LI-1", name="Sarees", category="Stock")]
    out = g.policy_agent_node(state)
    assert out["line_items"][0].policy_status == PolicyStatus.COVERED


def test_document_extract_populates_fields(monkeypatch, tmp_path):
    invoice = tmp_path / "inv.jpg"
    invoice.write_bytes(b"inv")
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **kwargs: MockLLM(schema))

    state = ClaimState(
        policy={}, event={},
        documents=[DocumentRecord(document_id="DOC-INV-1", document_type="Invoice",
                                  file_ref=str(invoice), uploaded_at=datetime.now(timezone.utc))],
    )
    out = g.document_extract_node(state)
    doc = out["documents"][0]
    assert doc.extraction_done is True
    assert doc.extracted_unit_value == 1000.0
    assert doc.extracted_quantity == 180
