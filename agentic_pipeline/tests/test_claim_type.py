"""Claim-type inference, the requirement gate, and the re-upload/resume path.

The claimant never confirms the inferred claim type, so the tests here mostly
pin down what happens when the classifier is UNSURE: it must widen the checklist
rather than confidently route someone to the wrong one, and it must never be the
reason a claim halts.
"""
from datetime import datetime, timezone

import pytest

import agentic_pipeline.graph as g
from agentic_pipeline import requirements as reqs
from agentic_pipeline.config import settings
from agentic_pipeline.graph import (
    awaiting_documents_node,
    check_requirements,
    claim_type_classify_node,
    requirements_node,
)
from agentic_pipeline.schemas import (
    ClaimTypeAlternate,
    ClaimTypeClassification,
    ClaimTypeSection,
    DocumentKind,
    DocumentRecord,
    RequirementCondition,
    RequirementRule,
    RequirementRuleSet,
    RequirementSeverity,
    RequirementVerification,
    TriageVerdict,
)
from agentic_pipeline.state import ClaimState

NOW = datetime.now(timezone.utc)


class FakeLLM:
    def __init__(self, result):
        self.result = result
    def invoke(self, messages, config=None):
        return self.result


class BoomLLM:
    def invoke(self, messages, config=None):
        raise RuntimeError("provider down")


def _ruleset():
    return RequirementRuleSet(
        ruleset_id="test", version="1",
        claim_types=[
            ClaimTypeSection(id="fire", label="Fire & Allied Perils"),
            ClaimTypeSection(id="burglary", label="Burglary & Theft"),
        ],
        rules=[
            RequirementRule(requirement_id="REQ-ID", label="Government ID",
                            verification=RequirementVerification.CLASSIFIED,
                            accepts=[DocumentKind.GOVT_ID],
                            severity=RequirementSeverity.BLOCKING),
            RequirementRule(requirement_id="REQ-FIR", label="Police FIR",
                            claim_types=["burglary"],
                            verification=RequirementVerification.CLASSIFIED,
                            accepts=[DocumentKind.FIR_REPORT],
                            severity=RequirementSeverity.BLOCKING),
        ],
    )


@pytest.fixture(autouse=True)
def _use_test_ruleset(monkeypatch):
    monkeypatch.setattr(reqs, "load_ruleset", lambda insurer: _ruleset())


def _state(**kw):
    base = dict(policy={"insurer": "Test"}, event={"description": "something happened"})
    base.update(kw)
    return ClaimState(**base)


def _classification(cid, conf, alternates=()):
    return ClaimTypeClassification(
        claim_type_id=cid, confidence=conf, rationale="because",
        alternates=[ClaimTypeAlternate(claim_type_id=a, confidence=c) for a, c in alternates],
    )


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------

def test_confident_classification_is_not_ambiguous(monkeypatch):
    monkeypatch.setattr(g, "get_structured_llm",
                        lambda s, **k: FakeLLM(_classification("burglary", 0.95)))
    out = claim_type_classify_node(_state())
    assert out["claim_type"] == "burglary"
    assert out["claim_type_ambiguous"] is False
    assert out["claim_type_source"] == "inferred"


def test_low_confidence_is_ambiguous(monkeypatch):
    monkeypatch.setattr(g, "get_structured_llm",
                        lambda s, **k: FakeLLM(_classification("fire", 0.3)))
    out = claim_type_classify_node(_state())
    assert out["claim_type_ambiguous"] is True
    assert out["warnings"]


def test_close_runner_up_is_ambiguous_even_at_high_confidence(monkeypatch):
    # 0.80 clears the confidence bar, but the alternate is right behind it.
    monkeypatch.setattr(g, "get_structured_llm", lambda s, **k: FakeLLM(
        _classification("fire", 0.80, alternates=[("burglary", 0.75)])))
    out = claim_type_classify_node(_state())
    assert out["claim_type_ambiguous"] is True
    assert set(out["claim_type_candidates"]) == {"fire", "burglary"}


def test_distant_runner_up_is_not_ambiguous(monkeypatch):
    monkeypatch.setattr(g, "get_structured_llm", lambda s, **k: FakeLLM(
        _classification("fire", 0.95, alternates=[("burglary", 0.05)])))
    assert claim_type_classify_node(_state())["claim_type_ambiguous"] is False


def test_unknown_claim_type_id_is_rejected(monkeypatch):
    """The prompt constrains the model to the ruleset's ids; never trust that."""
    monkeypatch.setattr(g, "get_structured_llm",
                        lambda s, **k: FakeLLM(_classification("marine_cargo", 0.99)))
    out = claim_type_classify_node(_state())
    assert "claim_type" not in out
    assert out["warnings"]


def test_alternates_outside_the_ruleset_are_dropped(monkeypatch):
    monkeypatch.setattr(g, "get_structured_llm", lambda s, **k: FakeLLM(
        _classification("fire", 0.95, alternates=[("not_a_section", 0.9)])))
    out = claim_type_classify_node(_state())
    assert out["claim_type_candidates"] == ["fire"]
    assert out["claim_type_ambiguous"] is False, "a bogus alternate must not create doubt"


def test_alternate_echoing_the_primary_does_not_fake_ambiguity(monkeypatch):
    """Regression, seen against a live model: it returned its own answer as its
    own alternate. Undeduped, runner_up == confidence, so the margin check made
    a unanimous answer look like a coin-flip and needlessly widened the LOR."""
    monkeypatch.setattr(g, "get_structured_llm", lambda s, **k: FakeLLM(
        _classification("fire", 0.95, alternates=[("fire", 0.95)])))
    out = claim_type_classify_node(_state())
    assert out["claim_type_candidates"] == ["fire"]
    assert out["claim_type_ambiguous"] is False


def test_repeated_alternates_are_deduped(monkeypatch):
    monkeypatch.setattr(g, "get_structured_llm", lambda s, **k: FakeLLM(
        _classification("fire", 0.95, alternates=[("burglary", 0.1), ("burglary", 0.1)])))
    assert claim_type_classify_node(_state())["claim_type_candidates"] == ["fire", "burglary"]


def test_classifier_failure_falls_open_to_universal_only(monkeypatch):
    monkeypatch.setattr(g, "get_structured_llm", lambda s, **k: BoomLLM())
    out = claim_type_classify_node(_state())
    assert "claim_type" not in out
    assert any("could not determine" in w.lower() for w in out["warnings"])


def test_user_override_is_never_reclassified(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("the classifier must not run over a user override")
    monkeypatch.setattr(g, "get_structured_llm", explode)
    out = claim_type_classify_node(_state(claim_type="fire", claim_type_source="user_override"))
    assert out == {}


def test_sectionless_ruleset_skips_classification(monkeypatch):
    monkeypatch.setattr(reqs, "load_ruleset", lambda insurer: RequirementRuleSet(
        ruleset_id="flat", claim_types=[], rules=[]))
    def explode(*a, **k):
        raise AssertionError("nothing to classify into")
    monkeypatch.setattr(g, "get_structured_llm", explode)
    assert claim_type_classify_node(_state()) == {}


# --------------------------------------------------------------------------
# The gate
# --------------------------------------------------------------------------

def _govt_id():
    return DocumentRecord(
        document_id="D-ID", document_type="GovtID", file_ref="/tmp/id", uploaded_at=NOW,
        classification_kind=DocumentKind.GOVT_ID, classification_verdict=TriageVerdict.MATCH,
        classification_done=True,
    )


def _fir():
    return DocumentRecord(
        document_id="D-FIR", document_type="Supporting", file_ref="/tmp/fir", uploaded_at=NOW,
        classification_kind=DocumentKind.FIR_REPORT, classification_verdict=TriageVerdict.MATCH,
        classification_done=True,
    )


def test_missing_blocking_requirement_pauses_the_claim():
    out = requirements_node(_state(claim_type="burglary", documents=[_govt_id()]))
    assert out["awaiting_documents"] is True
    assert out["lor"].blocking_missing == ["REQ-FIR"]
    assert check_requirements(_state(awaiting_documents=True)) == "awaiting_documents"


def test_complete_claim_proceeds():
    out = requirements_node(_state(claim_type="burglary", documents=[_govt_id(), _fir()]))
    assert out["awaiting_documents"] is False
    assert out["lor"].blocking_missing == []
    assert check_requirements(_state(**out)) == "proceed"


def test_warn_only_mode_never_pauses(monkeypatch):
    monkeypatch.setattr(settings, "lor_gate_mode", "warn_only")
    out = requirements_node(_state(claim_type="burglary", documents=[_govt_id()]))
    assert out["awaiting_documents"] is False
    assert out["lor"].blocking_missing == ["REQ-FIR"], "still reported, just not enforced"
    assert out["warnings"]


def test_requirements_node_failure_lets_the_claim_through(monkeypatch):
    monkeypatch.setattr(reqs, "build_lor", lambda **k: (_ for _ in ()).throw(RuntimeError("boom")))
    out = requirements_node(_state(claim_type="fire"))
    assert out.get("awaiting_documents") is not True
    assert out["warnings"]


def test_revision_increments_on_each_run():
    first = requirements_node(_state(claim_type="fire"))["lor"]
    assert first.revision == 2, "revision 1 is the instant pack built at submit"
    second = requirements_node(_state(claim_type="fire", lor=first))["lor"]
    assert second.revision == 3


def test_awaiting_documents_is_not_a_rejection():
    """The app renders intake_reasons under "Why this claim was not accepted".
    Nothing has been refused here, so that channel must stay empty."""
    pack = requirements_node(_state(claim_type="burglary", documents=[_govt_id()]))["lor"]
    out = awaiting_documents_node(_state(lor=pack, awaiting_documents=True))
    assert "intake_reasons" not in out
    assert "draft_pack" not in out
    assert any("Police FIR" in w for w in out["warnings"])


# --------------------------------------------------------------------------
# Resume after a re-upload
# --------------------------------------------------------------------------

def test_resume_reset_clears_derived_channels_but_keeps_inputs():
    # Phase 4: resume rebuilds an INPUTS-ONLY state via an allowlist, replacing
    # the old _RESET_ON_RESUME denylist.
    from agentic_pipeline.repository import _RESUME_INPUT_FIELDS

    # Additive/derived channels are NOT inputs, so they reset on resume.
    for derived in ("warnings", "anomalies", "rejected_items", "pending_verification", "lor", "draft_pack"):
        assert derived not in _RESUME_INPUT_FIELDS

    # These are inputs and must survive, or a resume re-does work or loses input.
    for preserved in ("documents", "line_items", "evidence", "policy", "event",
                      "vision_processed_evidence_ids", "claim_type", "claim_type_source"):
        assert preserved in _RESUME_INPUT_FIELDS, f"{preserved} must survive a resume"


def test_resume_does_not_duplicate_warnings():
    from agentic_pipeline.repository import resume_state_from_snapshot

    snap = _state(claim_type="burglary", documents=[_govt_id()],
                  warnings=["first run said this"]).model_dump(mode="json")
    resumed = resume_state_from_snapshot(snap)
    assert resumed.warnings == []
    assert len(resumed.documents) == 1, "documents survive the reset"


def test_already_classified_documents_are_not_re_triaged():
    """document_triage_node's existing classification_done guard is what makes
    a resume cheap — only the newly uploaded files cost an LLM call."""
    already = _govt_id()
    fresh = DocumentRecord(document_id="D-NEW", document_type="Supporting",
                           file_ref="/tmp/new", uploaded_at=NOW)
    candidates = [d for d in [already, fresh]
                  if d.file_ref and d.file_ref != "system-generated" and not d.classification_done]
    assert [d.document_id for d in candidates] == ["D-NEW"]


def test_graph_halts_at_awaiting_documents_and_skips_assessment(monkeypatch):
    """Full-graph: a claim missing a blocking document must stop right after the
    gate, before any of the expensive assessment nodes run."""
    from agentic_pipeline.graph import graph
    from agentic_pipeline.schemas import DocumentTriageOutput, VisionOutput

    called = []

    def spy(schema, **kwargs):
        called.append(schema)
        if schema is ClaimTypeClassification:
            return FakeLLM(_classification("burglary", 0.95))
        if schema is DocumentTriageOutput:
            return FakeLLM(DocumentTriageOutput(documents=[]))
        raise AssertionError(f"{schema.__name__} must not run before the gate clears")

    monkeypatch.setattr(g, "get_structured_llm", spy)
    state = ClaimState(policy={"insurer": "Test"},
                       event={"description": "laptops stolen overnight"},
                       documents=[_govt_id()])

    result = graph.invoke(state.model_dump())

    assert result["awaiting_documents"] is True
    assert result["lor"].blocking_missing == ["REQ-FIR"]
    assert result["draft_pack"] is None, "no draft is produced for a paused claim"
    assert VisionOutput not in called, "vision must not run on an incomplete claim"


def test_graph_proceeds_once_requirements_are_met(monkeypatch):
    """The same claim with the FIR attached clears the gate and moves on."""
    from agentic_pipeline.graph import graph
    from agentic_pipeline.schemas import DocumentTriageOutput

    reached_policy_extract = []

    def spy(schema, **kwargs):
        if schema is ClaimTypeClassification:
            return FakeLLM(_classification("burglary", 0.95))
        if schema is DocumentTriageOutput:
            return FakeLLM(DocumentTriageOutput(documents=[]))
        reached_policy_extract.append(schema)
        raise RuntimeError("stop here — we only need to prove the gate opened")

    monkeypatch.setattr(g, "get_structured_llm", spy)
    state = ClaimState(policy={"insurer": "Test"},
                       event={"description": "laptops stolen overnight"},
                       documents=[_govt_id(), _fir()])

    result = graph.invoke(state.model_dump())
    assert result["awaiting_documents"] is False
    assert result["lor"].blocking_missing == []


def test_user_override_survives_a_resume(monkeypatch):
    from agentic_pipeline.repository import resume_state_from_snapshot

    snap = _state(claim_type="fire", claim_type_source="user_override").model_dump(mode="json")
    resumed = resume_state_from_snapshot(snap)
    assert resumed.claim_type_source == "user_override"

    def explode(*a, **k):
        raise AssertionError("override must not be reclassified after a resume")
    monkeypatch.setattr(g, "get_structured_llm", explode)
    assert claim_type_classify_node(resumed) == {}
