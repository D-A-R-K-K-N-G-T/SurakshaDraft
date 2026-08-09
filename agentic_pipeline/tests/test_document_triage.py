"""Document-authenticity gate: a file must be what the claimant said it is.

The unit tests exercise the pure verdict logic; the node tests run the real
document_triage_node body with a mocked vision LLM and real temp files.
"""
import hashlib
import pytest
from datetime import datetime, timezone

import agentic_pipeline.graph as g
from agentic_pipeline.graph import _triage_verdict, document_triage_node, intake_node
from agentic_pipeline.state import ClaimState
from agentic_pipeline.schemas import (
    DocumentRecord, DocumentKind, TriageVerdict,
    DocumentTriageOutput, LLMDocumentTriage,
)


def _t(kind, legible=True, confidence=0.95, markers=None, ins=False, idn=False):
    return LLMDocumentTriage(
        document_id="D", doc_kind=kind, legible=legible, confidence=confidence,
        markers=markers if markers is not None else ["x"],
        has_insurance_anchors=ins, has_identity_anchors=idn,
    )


# --- pure verdict logic (the axis that separates a menu from a blurry policy) ---

def test_menu_as_policy_is_mismatch():
    v = _triage_verdict("PolicyDocument", _t(DocumentKind.MENU_OR_PRICE_LIST, markers=["Paneer Tikka 220"]), 0.80)
    assert v == TriageVerdict.MISMATCH

def test_right_kind_is_match():
    assert _triage_verdict("PolicyDocument", _t(DocumentKind.POLICY_SCHEDULE), 0.80) == TriageVerdict.MATCH

def test_illegible_wrong_kind_does_not_block():
    v = _triage_verdict("PolicyDocument", _t(DocumentKind.MENU_OR_PRICE_LIST, legible=False), 0.80)
    assert v == TriageVerdict.UNVERIFIED

def test_wrong_kind_without_markers_does_not_block():
    v = _triage_verdict("PolicyDocument", _t(DocumentKind.MENU_OR_PRICE_LIST, markers=[]), 0.80)
    assert v == TriageVerdict.UNVERIFIED

def test_wrong_kind_below_confidence_does_not_block():
    v = _triage_verdict("PolicyDocument", _t(DocumentKind.MENU_OR_PRICE_LIST, confidence=0.79), 0.80)
    assert v == TriageVerdict.UNVERIFIED

def test_unreadable_and_unknown_never_block():
    assert _triage_verdict("PolicyDocument", _t(DocumentKind.UNREADABLE, confidence=1.0), 0.80) == TriageVerdict.UNVERIFIED
    assert _triage_verdict("GovtID", _t(DocumentKind.UNKNOWN, confidence=1.0), 0.80) == TriageVerdict.UNVERIFIED

def test_anchor_rescue_downgrades_would_be_mismatch():
    # Model mislabels a real policy's kind, but insurance anchors are present.
    v = _triage_verdict("PolicyDocument", _t(DocumentKind.MARKETING_OR_OTHER_COMMERCIAL, ins=True), 0.80)
    assert v == TriageVerdict.UNVERIFIED

def test_misfiled_invoice_is_not_a_blocking_role():
    # A menu in the Invoice slot is a mismatch, but Invoice never fails intake.
    v = _triage_verdict("Invoice", _t(DocumentKind.MENU_OR_PRICE_LIST), 0.80)
    assert v == TriageVerdict.MISMATCH  # verdict is mismatch...
    # ...but the node only turns BLOCKING_ROLES mismatches into gate reasons.
    from agentic_pipeline.graph import BLOCKING_ROLES
    assert "Invoice" not in BLOCKING_ROLES

def test_swapped_policy_into_id_slot_blocks():
    v = _triage_verdict("GovtID", _t(DocumentKind.POLICY_SCHEDULE), 0.80)
    assert v == TriageVerdict.MISMATCH


# --- node body with a mocked LLM + real temp files ---

class TriageLLM:
    def __init__(self, mapping):
        self.mapping = mapping  # document_id -> LLMDocumentTriage
    def invoke(self, messages, config=None):
        return DocumentTriageOutput(documents=list(self.mapping.values()))


def _doc(tmp_path, doc_id, doc_type, name="f.pdf"):
    p = tmp_path / f"{doc_id}_{name}"
    p.write_bytes(b"bytes-of-" + doc_id.encode())
    return DocumentRecord(document_id=doc_id, document_type=doc_type,
                          file_ref=str(p), uploaded_at=datetime.now(timezone.utc))


def test_menu_as_policy_fails_intake_end_to_end(monkeypatch, tmp_path):
    doc = _doc(tmp_path, "DOC-POL-1", "PolicyDocument")
    triage = LLMDocumentTriage(document_id="DOC-POL-1", doc_kind=DocumentKind.MENU_OR_PRICE_LIST,
                               legible=True, confidence=0.96, markers=["Paneer Tikka 220"])
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: TriageLLM({"DOC-POL-1": triage}))

    state = ClaimState(policy={}, event={}, documents=[doc])
    out = document_triage_node(state)
    assert out["doc_gate_reasons"], "a menu-as-policy must produce a blocking reason"
    assert any("menu" in r for r in out["doc_gate_reasons"])
    # And that reason must fail the intake gate.
    state2 = state.model_copy(update={"documents": out["documents"], "doc_gate_reasons": out["doc_gate_reasons"]})
    assert intake_node(state2)["intake_ok"] is False


def test_llm_failure_fails_open(monkeypatch, tmp_path):
    doc = _doc(tmp_path, "DOC-POL-1", "PolicyDocument")
    class Boom:
        def invoke(self, m, config=None): raise RuntimeError("provider down")
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: Boom())
    out = document_triage_node(ClaimState(policy={}, event={}, documents=[doc]))
    assert out.get("doc_gate_reasons", []) == []
    assert any("unavailable" in w for w in out.get("warnings", []))


def test_image_load_failure_fails_open(monkeypatch, tmp_path):
    doc = DocumentRecord(document_id="DOC-POL-1", document_type="PolicyDocument",
                         file_ref=str(tmp_path / "does_not_exist.pdf"),
                         uploaded_at=datetime.now(timezone.utc))
    out = document_triage_node(ClaimState(policy={}, event={}, documents=[doc]))
    assert out.get("doc_gate_reasons", []) == []
    assert any("Could not open" in w for w in out.get("warnings", []))


def test_missing_returned_entry_defaults_to_unverified(monkeypatch, tmp_path):
    d1 = _doc(tmp_path, "DOC-POL-1", "PolicyDocument")
    d2 = _doc(tmp_path, "DOC-KYC-1", "GovtID")
    # Model returns only one of the two.
    triage = LLMDocumentTriage(document_id="DOC-POL-1", doc_kind=DocumentKind.POLICY_SCHEDULE, confidence=0.9)
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: TriageLLM({"DOC-POL-1": triage}))
    out = document_triage_node(ClaimState(policy={}, event={}, documents=[d1, d2]))
    assert out.get("doc_gate_reasons", []) == []  # the silent one is unverified, not mismatch
    kyc = {d.document_id: d for d in out["documents"]}
    assert kyc["DOC-KYC-1"].classification_verdict == TriageVerdict.UNVERIFIED


def test_unrequested_document_id_is_ignored(monkeypatch, tmp_path):
    doc = _doc(tmp_path, "DOC-POL-1", "PolicyDocument")
    # Model echoes a doc that was never sent; it must not create a verdict.
    ghost = LLMDocumentTriage(document_id="GHOST", doc_kind=DocumentKind.MENU_OR_PRICE_LIST, confidence=0.99)
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: TriageLLM({"GHOST": ghost}))
    out = document_triage_node(ClaimState(policy={}, event={}, documents=[doc]))
    assert out.get("doc_gate_reasons", []) == []


def test_declared_type_never_reaches_the_prompt(monkeypatch, tmp_path):
    # The regression that would silently void the entire control: if the claimed
    # document_type leaks into the model's input, the classifier just confirms it.
    captured = {}
    triage = LLMDocumentTriage(document_id="DOC-POL-1", doc_kind=DocumentKind.POLICY_SCHEDULE, confidence=0.9)
    class Spy:
        def invoke(self, messages, config=None):
            captured["messages"] = messages
            return DocumentTriageOutput(documents=[triage])
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: Spy())
    doc = _doc(tmp_path, "DOC-POL-1", "PolicyDocument")
    document_triage_node(ClaimState(policy={}, event={}, documents=[doc]))
    # The claimed slot lives on the HUMAN message's per-document labels. The
    # system prompt legitimately defines kinds like "policy schedule"; what must
    # NEVER appear is the claimant's asserted role token next to the image.
    human = next(m for m in captured["messages"] if m.__class__.__name__ == "HumanMessage")
    serialized = str(human.content)
    for forbidden in ["PolicyDocument", "GovtID", "Invoice", "declared", "expected as"]:
        assert forbidden not in serialized, f"triage human message leaked claimed type '{forbidden}'"
    # It DOES carry the opaque id so the model can echo it back.
    assert "DOC-POL-1" in serialized


def test_warn_only_mode_never_blocks(monkeypatch, tmp_path):
    from agentic_pipeline.config import settings
    monkeypatch.setattr(settings, "doc_gate_mode", "warn_only")
    doc = _doc(tmp_path, "DOC-POL-1", "PolicyDocument")
    triage = LLMDocumentTriage(document_id="DOC-POL-1", doc_kind=DocumentKind.MENU_OR_PRICE_LIST,
                               confidence=0.96, markers=["Paneer Tikka"])
    monkeypatch.setattr(g, "get_structured_llm", lambda schema, **k: TriageLLM({"DOC-POL-1": triage}))
    out = document_triage_node(ClaimState(policy={}, event={}, documents=[doc]))
    assert out["doc_gate_reasons"] == []
    assert any("menu" in w for w in out.get("warnings", []))
