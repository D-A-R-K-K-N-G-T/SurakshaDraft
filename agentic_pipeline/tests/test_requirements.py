"""The LOR engine: narrowing an exhaustive master list, and checking it off.

Pure functions only — no live LLM. The claim-type classifier node lives in
test_claim_type.py.

The asymmetry these tests pin down: a claimant is told "you still owe us X"
only when X is genuinely absent. A file we received but could not read is
UNVERIFIED, never MISSING, and never blocks.
"""
from datetime import datetime, timezone

import pytest

from agentic_pipeline import requirements as reqs
from agentic_pipeline.schemas import (
    ClaimTypeSection,
    DocumentKind,
    DocumentRecord,
    RequirementCondition,
    RequirementRule,
    RequirementRuleSet,
    RequirementSeverity,
    RequirementStatus,
    RequirementVerification,
    TriageVerdict,
)

NOW = datetime.now(timezone.utc)


def _rule(rid, *, claim_types=(), accepts=(), severity="blocking",
          verification="classified", **cond):
    return RequirementRule(
        requirement_id=rid,
        label=rid.replace("REQ-", "").title(),
        claim_types=list(claim_types),
        verification=RequirementVerification(verification),
        accepts=[DocumentKind(a) for a in accepts],
        severity=RequirementSeverity(severity),
        applies_when=RequirementCondition(**cond),
    )


def _doc(doc_id, doc_type="Supporting", *, kind=None, verdict=None,
         legible=None, done=False, requirement_id=None):
    return DocumentRecord(
        document_id=doc_id, document_type=doc_type, file_ref=f"/tmp/{doc_id}",
        uploaded_at=NOW, requirement_id=requirement_id,
        classification_kind=DocumentKind(kind) if kind else None,
        classification_verdict=TriageVerdict(verdict) if verdict else None,
        classification_legible=legible, classification_done=done,
    )


def _ruleset(*rules, sections=("fire", "burglary")):
    return RequirementRuleSet(
        ruleset_id="test", version="1",
        claim_types=[ClaimTypeSection(id=s, label=s.title()) for s in sections],
        rules=list(rules),
    )


FACTS = reqs.ClaimFacts(categories=["Stock"], item_type="Widgets", account_type="commercial")


# --------------------------------------------------------------------------
# Stage 1: section select
# --------------------------------------------------------------------------

def test_universal_rules_apply_with_no_claim_type():
    rs = _ruleset(_rule("REQ-U"), _rule("REQ-FIRE", claim_types=["fire"]))
    got = reqs.narrow(rs, FACTS)
    assert [r.requirement_id for r in got] == ["REQ-U"]


def test_section_rules_apply_only_under_their_claim_type():
    rs = _ruleset(
        _rule("REQ-U"),
        _rule("REQ-FIRE", claim_types=["fire"]),
        _rule("REQ-BURG", claim_types=["burglary"]),
    )
    got = {r.requirement_id for r in reqs.narrow(rs, FACTS, claim_type="burglary")}
    assert got == {"REQ-U", "REQ-BURG"}


def test_rule_shared_by_two_sections_applies_to_both():
    rs = _ruleset(_rule("REQ-BOTH", claim_types=["fire", "burglary"]))
    for ct in ("fire", "burglary"):
        assert [r.requirement_id for r in reqs.narrow(rs, FACTS, claim_type=ct)] == ["REQ-BOTH"]


# --------------------------------------------------------------------------
# Stage 2: condition filter
# --------------------------------------------------------------------------

def test_condition_ors_within_a_field():
    rs = _ruleset(_rule("REQ-C", categories=["Stock", "Electronics"]))
    assert reqs.narrow(rs, reqs.ClaimFacts(categories=["Electronics"]))
    assert not reqs.narrow(rs, reqs.ClaimFacts(categories=["Vehicle"]))


def test_condition_ands_across_fields():
    rs = _ruleset(_rule("REQ-C", categories=["Stock"], account_types=["commercial"]))
    assert reqs.narrow(rs, FACTS)                                        # both match
    personal = reqs.ClaimFacts(categories=["Stock"], account_type="personal")
    assert not reqs.narrow(rs, personal)                                 # one fails -> excluded


def test_condition_matching_is_case_insensitive():
    rs = _ruleset(_rule("REQ-C", categories=["stock"]))
    assert reqs.narrow(rs, reqs.ClaimFacts(categories=["STOCK"]))


def test_empty_condition_never_constrains():
    rs = _ruleset(_rule("REQ-C"))
    assert reqs.narrow(rs, reqs.ClaimFacts())


# --------------------------------------------------------------------------
# Ambiguous classification: widen the list, block on nothing new
# --------------------------------------------------------------------------

def test_ambiguous_unions_sections_and_demotes_them_to_advisory():
    rs = _ruleset(
        _rule("REQ-U"),
        _rule("REQ-FIRE", claim_types=["fire"]),
        _rule("REQ-BURG", claim_types=["burglary"]),
    )
    got = reqs.narrow(rs, FACTS, claim_type="fire",
                      candidate_types=["fire", "burglary"], ambiguous=True)
    by_id = {r.requirement_id: r for r in got}
    assert set(by_id) == {"REQ-U", "REQ-FIRE", "REQ-BURG"}, "claimant sees every possibility"
    assert by_id["REQ-U"].severity == RequirementSeverity.BLOCKING, "universal rules still bind"
    assert by_id["REQ-FIRE"].severity == RequirementSeverity.ADVISORY
    assert by_id["REQ-BURG"].severity == RequirementSeverity.ADVISORY


def test_ambiguous_blocks_only_on_universal_rules():
    rs = _ruleset(
        _rule("REQ-U", accepts=["govt_id"]),
        _rule("REQ-FIRE", claim_types=["fire"], accepts=["fire_brigade_report"]),
    )
    rules = reqs.narrow(rs, FACTS, claim_type="fire",
                        candidate_types=["fire"], ambiguous=True)
    pack = reqs.evaluate(rules, [], reqs.BASIS_NARROWED, ruleset=rs, ambiguous=True)
    assert pack.blocking_missing == ["REQ-U"]


def test_demotion_does_not_mutate_the_cached_ruleset():
    # narrow() copies before demoting; a cached ruleset is shared across claims,
    # so mutating it would leak one claimant's ambiguity into everyone else's.
    rs = _ruleset(_rule("REQ-FIRE", claim_types=["fire"]))
    reqs.narrow(rs, FACTS, claim_type="fire", candidate_types=["fire"], ambiguous=True)
    assert rs.rules[0].severity == RequirementSeverity.BLOCKING


# --------------------------------------------------------------------------
# Matching: classified tier
# --------------------------------------------------------------------------

def test_actual_kind_satisfies_even_from_an_unrelated_slot():
    # An invoice uploaded into the "supporting" box still satisfies an invoice
    # requirement — we judge the file, not the box it arrived in.
    rule = _rule("REQ-INV", accepts=["tax_invoice"])
    doc = _doc("D1", "Supporting", kind="tax_invoice", verdict="match", done=True)
    pack = reqs.evaluate([rule], [doc], reqs.BASIS_NARROWED)
    assert [r.requirement_id for r in pack.satisfied] == ["REQ-INV"]
    assert pack.blocking_missing == []


def test_any_one_of_accepts_satisfies():
    rule = _rule("REQ-STOCK", accepts=["tax_invoice", "stock_register"])
    doc = _doc("D1", kind="stock_register", verdict="match", done=True)
    assert reqs.evaluate([rule], [doc], reqs.BASIS_NARROWED).satisfied


def test_mismatch_document_does_not_satisfy():
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    doc = _doc("D1", "PolicyDocument", kind="policy_schedule",
               verdict="mismatch", done=True)
    pack = reqs.evaluate([rule], [doc], reqs.BASIS_NARROWED)
    assert not pack.satisfied
    assert pack.blocking_missing == ["REQ-POL"]


def test_unreadable_upload_is_unverified_not_missing():
    """The distinction the whole claimant-facing wording rests on."""
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    doc = _doc("D1", "PolicyDocument", kind="unreadable",
               verdict="unverified", legible=False, done=True)
    pack = reqs.evaluate([rule], [doc], reqs.BASIS_NARROWED)
    assert [r.requirement_id for r in pack.unverified] == ["REQ-POL"]
    assert pack.missing == []
    assert pack.blocking_missing == [], "a blurry scan must never halt a claim"
    assert "could not read" in pack.unverified[0].message


def test_nothing_uploaded_is_missing():
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    pack = reqs.evaluate([rule], [], reqs.BASIS_NARROWED)
    assert [r.requirement_id for r in pack.missing] == ["REQ-POL"]
    assert pack.blocking_missing == ["REQ-POL"]


def test_advisory_missing_never_blocks():
    rule = _rule("REQ-BANK", accepts=["bank_proof"], severity="advisory")
    pack = reqs.evaluate([rule], [], reqs.BASIS_NARROWED)
    assert pack.missing and pack.blocking_missing == []


# --------------------------------------------------------------------------
# Matching: attested tier (the long tail of an exhaustive master LOR)
# --------------------------------------------------------------------------

def test_attested_satisfied_only_by_a_tagged_upload():
    rule = _rule("REQ-SUBROG", verification="attested")
    tagged = _doc("D1", requirement_id="REQ-SUBROG")
    pack = reqs.evaluate([rule], [tagged], reqs.BASIS_NARROWED)
    assert [r.requirement_id for r in pack.satisfied] == ["REQ-SUBROG"]


def test_attested_wording_never_claims_verification():
    rule = _rule("REQ-SUBROG", verification="attested")
    pack = reqs.evaluate([rule], [_doc("D1", requirement_id="REQ-SUBROG")], reqs.BASIS_NARROWED)
    assert "not verified" in pack.satisfied[0].message.lower()


def test_untagged_upload_does_not_satisfy_an_attested_rule():
    rule = _rule("REQ-SUBROG", verification="attested")
    untagged = _doc("D1", kind="tax_invoice", verdict="match", done=True)
    pack = reqs.evaluate([rule], [untagged], reqs.BASIS_NARROWED)
    assert pack.blocking_missing == ["REQ-SUBROG"]


def test_tag_for_a_different_requirement_does_not_satisfy():
    rule = _rule("REQ-SUBROG", verification="attested")
    pack = reqs.evaluate([rule], [_doc("D1", requirement_id="REQ-OTHER")], reqs.BASIS_NARROWED)
    assert pack.blocking_missing == ["REQ-SUBROG"]


def test_classified_wording_does_claim_verification():
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    doc = _doc("D1", kind="policy_schedule", verdict="match", done=True)
    pack = reqs.evaluate([rule], [doc], reqs.BASIS_NARROWED)
    assert "verified" in pack.satisfied[0].message.lower()


# --------------------------------------------------------------------------
# Damage photos arrive as evidence, not documents
# --------------------------------------------------------------------------

def test_photo_requirement_is_satisfied_by_evidence():
    """Regression: photos go into ClaimState.evidence, never into documents. A
    photograph requirement matched against documents alone can NEVER be met, and
    since it is universal that would stall every single claim."""
    rule = _rule("REQ-PHOTOS", accepts=["damage_photograph"])
    pack = reqs.evaluate([rule], [], reqs.BASIS_NARROWED, evidence_ids=["IMG-1"])
    assert [r.requirement_id for r in pack.satisfied] == ["REQ-PHOTOS"]
    assert pack.satisfied[0].satisfied_by == ["IMG-1"]
    assert pack.blocking_missing == []


def test_photo_requirement_without_evidence_is_still_missing():
    rule = _rule("REQ-PHOTOS", accepts=["damage_photograph"])
    pack = reqs.evaluate([rule], [], reqs.BASIS_NARROWED, evidence_ids=[])
    assert pack.blocking_missing == ["REQ-PHOTOS"]


def test_evidence_does_not_satisfy_unrelated_requirements():
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    pack = reqs.evaluate([rule], [], reqs.BASIS_NARROWED, evidence_ids=["IMG-1"])
    assert pack.blocking_missing == ["REQ-POL"]


def test_build_lor_accepts_evidence_objects_and_dicts():
    # Inside the graph these are EvidenceRecords; in the service they are the
    # plain dicts read back out of the claim store.
    for evidence in ([{"evidence_id": "IMG-1"}],
                     [type("E", (), {"evidence_id": "IMG-1"})()]):
        pack = reqs.build_lor({}, {}, [], reqs.BASIS_UNIVERSAL, evidence=evidence)
        row = next(r for r in pack.satisfied if r.requirement_id == "REQ-PHOTOS")
        assert row.satisfied_by == ["IMG-1"]


# --------------------------------------------------------------------------
# Provisional (rev.1) vs confirmed (rev.2) basis
# --------------------------------------------------------------------------

def test_provisional_trusts_the_declared_slot():
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    doc = _doc("D1", "PolicyDocument")           # no triage has run yet
    pack = reqs.evaluate([rule], [doc], reqs.BASIS_UNIVERSAL)
    assert pack.satisfied and pack.blocking_missing == []


def test_confirmed_basis_overturns_a_provisional_tick():
    """A menu uploaded as a policy passes rev.1 and fails rev.2. This is the
    whole reason two revisions exist."""
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    claimed = _doc("D1", "PolicyDocument")
    assert reqs.evaluate([rule], [claimed], reqs.BASIS_UNIVERSAL).satisfied

    triaged = _doc("D1", "PolicyDocument", kind="menu_or_price_list",
                   verdict="mismatch", done=True)
    pack2 = reqs.evaluate([rule], [triaged], reqs.BASIS_NARROWED)
    assert pack2.blocking_missing == ["REQ-POL"]


def test_untriaged_document_is_unverified_not_missing_on_confirmed_basis():
    # classification_done=False: triage failed open, so we know nothing yet.
    rule = _rule("REQ-POL", accepts=["policy_schedule"])
    doc = _doc("D1", "PolicyDocument", done=False)
    pack = reqs.evaluate([rule], [doc], reqs.BASIS_NARROWED)
    assert [r.requirement_id for r in pack.unverified] == ["REQ-POL"]
    assert pack.blocking_missing == []


# --------------------------------------------------------------------------
# Ruleset loading and the fail-open contract
# --------------------------------------------------------------------------

def test_slugify():
    assert reqs.slugify("ICICI Lombard General Insurance") == "icici-lombard-general-insurance"
    assert reqs.slugify("") == "default"


def test_unknown_insurer_falls_back_to_default():
    rs = reqs.load_ruleset("No Such Insurer Ltd")
    assert rs is not None and rs.ruleset_id == "default"


def test_missing_ruleset_directory_produces_a_usable_empty_pack(monkeypatch):
    monkeypatch.setattr(reqs, "RULESET_DIR", "/nonexistent/path")
    pack = reqs.build_lor({"insurer": "X"}, {}, [], reqs.BASIS_UNIVERSAL)
    assert pack.blocking_missing == [], "no ruleset must never block a claim"
    assert pack.notes


def test_malformed_ruleset_file_fails_open(monkeypatch, tmp_path):
    (tmp_path / "default.json").write_text("{ this is not json", encoding="utf-8")
    monkeypatch.setattr(reqs, "RULESET_DIR", str(tmp_path))
    assert reqs.load_ruleset(None) is None
    pack = reqs.build_lor({}, {}, [], reqs.BASIS_UNIVERSAL)
    assert pack.blocking_missing == []


def test_ruleset_cache_picks_up_an_edit(monkeypatch, tmp_path):
    import json, os
    path = tmp_path / "default.json"
    base = {"ruleset_id": "default", "version": "1", "claim_types": [], "rules": []}
    path.write_text(json.dumps(base), encoding="utf-8")
    monkeypatch.setattr(reqs, "RULESET_DIR", str(tmp_path))
    reqs._CACHE.clear()
    assert reqs.load_ruleset(None).version == "1"

    base["version"] = "2"
    path.write_text(json.dumps(base), encoding="utf-8")
    os.utime(path, (0, 0))  # force a different mtime
    assert reqs.load_ruleset(None).version == "2"


# --------------------------------------------------------------------------
# The shipped default ruleset
# --------------------------------------------------------------------------

def test_default_ruleset_is_valid_and_self_consistent():
    rs = reqs.load_ruleset(None)
    assert rs is not None
    section_ids = {s.id for s in rs.claim_types}
    ids = set()
    for rule in rs.rules:
        assert rule.requirement_id not in ids, f"duplicate id {rule.requirement_id}"
        ids.add(rule.requirement_id)
        unknown = set(rule.claim_types) - section_ids
        assert not unknown, f"{rule.requirement_id} references unknown sections {unknown}"
        if rule.verification == RequirementVerification.ATTESTED:
            assert not rule.accepts, f"{rule.requirement_id}: attested rules cannot name kinds"
        else:
            assert rule.accepts, f"{rule.requirement_id}: classified rules need accepts"


def test_default_ruleset_theft_claim_asks_for_an_fir():
    policy = {"insurer": None, "asset_categories": ["Electronics"]}
    event = {"description": "laptops stolen overnight", "account_type": "commercial"}
    pack = reqs.build_lor(policy, event, [], reqs.BASIS_NARROWED,
                          revision=2, claim_type="burglary")
    assert "REQ-BURG-FIR" in pack.blocking_missing
    assert "REQ-FIRE-BRIGADE" not in pack.blocking_missing, "other sections must not leak in"


def test_default_ruleset_rev1_covers_universal_only():
    pack = reqs.build_lor({}, {}, [], reqs.BASIS_UNIVERSAL, revision=1)
    every = {r.requirement_id for r in pack.satisfied + pack.unverified + pack.missing}
    assert every == {"REQ-POLICY", "REQ-ID", "REQ-PHOTOS", "REQ-BANK", "REQ-CLAIMFORM"}
