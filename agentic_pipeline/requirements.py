"""
The Letter of Requirement (LOR) engine: narrow an insurer's exhaustive master
requirement list down to the handful that apply to one claim, then check them
against what the claimant actually uploaded.

Contains NO LLM calls. The master LOR is parsed by a model once at onboarding
(see ingest_requirements.py) and the claim type is inferred by a model inside
the graph, but every decision that can tell a claimant "you still owe us
document X" is made by the deterministic code below. Same split as
_triage_verdict in graph.py: the model observes, Python decides.

Narrowing happens on two axes, in order:

  1. SECTION SELECT — by claim type. Master LORs are sectioned by peril
     ("Fire & Allied Perils", "Burglary & Theft"). A rule with an empty
     `claim_types` is UNIVERSAL and applies to every claim; that subset is what
     the instant rev.1 LOR is built from, before the claim type is known.
  2. CONDITION FILTER — by `applies_when` (category / item type / account type).

Known limitation: this runs before valuation, so `applies_when` cannot key off
claim amount. Amount-conditioned requirements ("surveyor report above 1L") need
a second evaluation pass after valuation_agent; RequirementCondition leaves room
for that key to be added.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Iterable, Optional

from pydantic import BaseModel, Field, ValidationError

from agentic_pipeline.config import settings
from agentic_pipeline.schemas import (
    ClaimTypeSection,
    DocumentKind,
    DocumentRecord,
    LORPack,
    RequirementCondition,
    RequirementResult,
    RequirementRule,
    RequirementRuleSet,
    RequirementSeverity,
    RequirementStatus,
    RequirementVerification,
    TriageVerdict,
)

_log = logging.getLogger(__name__)

RULESET_DIR = os.path.join(os.path.dirname(__file__), "rulesets")
DEFAULT_RULESET_ID = "default"

# Basis values for LORPack.basis.
BASIS_UNIVERSAL = "universal_only"
BASIS_NARROWED = "claim_type_narrowed"


# Which document kinds legitimately belong in each client-declared slot.
# Canonical home for this map: graph.py imports it from here for the slot-mismatch
# gate, and the provisional matcher below uses it to guess, before any triage has
# run, which requirements an upload was probably meant to answer.
# A slot absent from this map is never gated and never provisionally matched —
# "Supporting" is deliberately absent, since such files are judged by their
# actual kind or by their requirement_id tag instead.
ROLE_EXPECTED_KINDS = {
    "PolicyDocument": {DocumentKind.POLICY_SCHEDULE},
    "GovtID": {DocumentKind.GOVT_ID},
    "Invoice": {DocumentKind.TAX_INVOICE, DocumentKind.PREMIUM_RECEIPT, DocumentKind.STOCK_REGISTER},
}

# The model's explicit "I cannot tell" answers. A requirement is never marked
# MISSING on the strength of one of these.
INCONCLUSIVE_KINDS = {DocumentKind.UNKNOWN, DocumentKind.UNREADABLE}


class ClaimFacts(BaseModel):
    """The claim-side inputs to narrowing, all known before valuation runs."""
    categories: list[str] = Field(default_factory=list)
    item_type: str = ""
    account_type: str = ""


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def slugify(name: str) -> str:
    """"ICICI Lombard General Insurance" -> "icici-lombard-general-insurance"."""
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or DEFAULT_RULESET_ID


# --------------------------------------------------------------------------
# Ruleset loading
# --------------------------------------------------------------------------

# path -> (mtime, parsed ruleset). Rulesets are edited by hand after ingestion,
# so keying on mtime picks up a correction without a process restart.
_CACHE: dict[str, tuple[float, RequirementRuleSet]] = {}


def ruleset_path(slug: str) -> str:
    return os.path.join(RULESET_DIR, f"{slug}.json")


def _load_file(path: str) -> Optional[RequirementRuleSet]:
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    cached = _CACHE.get(path)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            ruleset = RequirementRuleSet.model_validate(json.load(fh))
    except (OSError, ValueError, ValidationError) as exc:
        # Fail open. A malformed ruleset must never block a real claimant.
        _log.error("Could not load ruleset %s: %s: %s", path, type(exc).__name__, exc)
        return None
    _CACHE[path] = (mtime, ruleset)
    return ruleset


# DB source cache: slug -> (fetched_at monotonic, ruleset). Rulesets are
# immutable per (slug, version) with at most one active version, so a short TTL
# is enough to pick up an `activate` without a restart.
_DB_CACHE: dict[str, tuple[float, RequirementRuleSet]] = {}
_DB_TTL_SECONDS = 30.0


def _load_db(slug: str) -> Optional[RequirementRuleSet]:
    """Reconstruct the active ruleset for `slug` from the catalogue tables.

    Fail-open: any DB/import error logs and returns None, exactly like a missing
    file — a database outage must never block a flood claimant.
    """
    import time

    cached = _DB_CACHE.get(slug)
    if cached and (time.monotonic() - cached[0]) < _DB_TTL_SECONDS:
        return cached[1]

    try:
        from sqlalchemy import select

        from agentic_pipeline import models as M
        from agentic_pipeline.db import SessionLocal

        with SessionLocal() as session:
            row = session.execute(
                select(M.Ruleset).where(
                    M.Ruleset.ruleset_slug == slug,
                    M.Ruleset.status == "active",
                )
            ).scalar_one_or_none()
            if row is None:
                return None

            sections = sorted(row.claim_types, key=lambda c: c.ordinal)
            section_ord = {c.section_key: c.ordinal for c in sections}
            claim_types = [
                ClaimTypeSection(
                    id=c.section_key,
                    label=c.label,
                    description=c.description,
                    aliases=list(c.aliases),
                )
                for c in sections
            ]

            rules: list[RequirementRule] = []
            for r in sorted(row.rules, key=lambda r: r.ordinal):
                # Order a rule's sections by their section ordinal so the
                # reconstruction is byte-identical to the authored JSON.
                cts = sorted(
                    (link.claim_type.section_key for link in r.claim_type_links),
                    key=lambda k: section_ord.get(k, 1_000_000),
                )
                rules.append(
                    RequirementRule(
                        requirement_id=r.requirement_id,
                        label=r.label,
                        help_text=r.help_text,
                        claim_types=cts,
                        verification=RequirementVerification(r.verification),
                        accepts=[DocumentKind(a) for a in r.accepts],
                        severity=RequirementSeverity(r.severity),
                        applies_when=RequirementCondition(
                            categories=list(r.when_categories),
                            item_types=list(r.when_item_types),
                            account_types=list(r.when_account_types),
                        ),
                    )
                )

            ruleset = RequirementRuleSet(
                ruleset_id=row.ruleset_slug,
                version=row.version,
                source=row.source or "",
                claim_types=claim_types,
                rules=rules,
            )
    except Exception as exc:  # fail-open — never raise into a claim
        _log.error("Could not load ruleset %r from DB: %s: %s", slug, type(exc).__name__, exc)
        return None

    _DB_CACHE[slug] = (time.monotonic(), ruleset)
    return ruleset


def _load_by_slug(slug: str) -> Optional[RequirementRuleSet]:
    if settings.ruleset_source == "db":
        return _load_db(slug)
    return _load_file(ruleset_path(slug))


def load_ruleset(insurer: Optional[str]) -> Optional[RequirementRuleSet]:
    """Select the client's ruleset by insurer name, falling back to default.

    Returns None only when even the default is unusable — callers treat that as
    "no requirements known" and let the claim proceed.
    """
    if insurer:
        found = _load_by_slug(slugify(insurer))
        if found is not None:
            return found
    return _load_by_slug(DEFAULT_RULESET_ID)


def _list_db() -> list[str]:
    try:
        from sqlalchemy import select

        from agentic_pipeline import models as M
        from agentic_pipeline.db import SessionLocal

        with SessionLocal() as session:
            slugs = session.execute(
                select(M.Ruleset.ruleset_slug)
                .where(M.Ruleset.status == "active")
                .distinct()
            ).scalars().all()
        return sorted(slugs)
    except Exception as exc:
        _log.error("Could not list rulesets from DB: %s: %s", type(exc).__name__, exc)
        return []


def list_ruleset_ids() -> list[str]:
    if settings.ruleset_source == "db":
        return _list_db()
    try:
        return sorted(
            f[:-5] for f in os.listdir(RULESET_DIR)
            if f.endswith(".json") and not f.endswith(".coverage.json")
        )
    except OSError:
        return []


# --------------------------------------------------------------------------
# Stage 1 + 2: narrowing
# --------------------------------------------------------------------------

def _condition_matches(cond: RequirementCondition, facts: ClaimFacts) -> bool:
    """AND across fields, OR within a field's list. Empty field = no constraint."""
    if cond.categories:
        wanted = {_norm(c) for c in cond.categories}
        if not wanted & {_norm(c) for c in facts.categories}:
            return False
    if cond.item_types:
        if _norm(facts.item_type) not in {_norm(t) for t in cond.item_types}:
            return False
    if cond.account_types:
        if _norm(facts.account_type) not in {_norm(t) for t in cond.account_types}:
            return False
    return True


def narrow(
    ruleset: RequirementRuleSet,
    facts: ClaimFacts,
    claim_type: Optional[str] = None,
    candidate_types: Iterable[str] = (),
    ambiguous: bool = False,
) -> list[RequirementRule]:
    """Cut the exhaustive master list down to what applies to this one claim.

    With no claim_type (and not ambiguous) this returns UNIVERSAL rules only —
    the instant rev.1 case, where the claim type has not been inferred yet.

    When `ambiguous`, the classifier could not settle on one section, so we take
    the UNION of the candidates and demote every non-universal blocking rule to
    advisory. The claimant is told about everything that might be needed; the
    pipeline is not halted on a coin-flip.
    """
    sections = {t for t in candidate_types if t} if ambiguous else set()
    if claim_type:
        sections.add(claim_type)

    out: list[RequirementRule] = []
    for rule in ruleset.rules:
        universal = not rule.claim_types
        if not universal and not (set(rule.claim_types) & sections):
            continue
        if not _condition_matches(rule.applies_when, facts):
            continue
        if ambiguous and not universal and rule.severity == RequirementSeverity.BLOCKING:
            rule = rule.model_copy(update={"severity": RequirementSeverity.ADVISORY})
        out.append(rule)
    return out


# --------------------------------------------------------------------------
# Matching documents against requirements
# --------------------------------------------------------------------------

def _tagged_for(rule: RequirementRule, documents: Iterable[DocumentRecord]) -> list[DocumentRecord]:
    return [d for d in documents if d.requirement_id and d.requirement_id == rule.requirement_id]


def _addresses_rule(d: DocumentRecord, rule: RequirementRule) -> bool:
    """Was this upload plausibly meant to answer this requirement?

    Used to tell "you never sent it" apart from "you sent it and we couldn't
    read it" — a distinction the claimant-facing wording depends on.
    """
    if d.requirement_id and d.requirement_id == rule.requirement_id:
        return True
    return bool(ROLE_EXPECTED_KINDS.get(d.document_type, set()) & set(rule.accepts))


def _satisfying(
    rule: RequirementRule, documents: list[DocumentRecord], basis: str
) -> list[DocumentRecord]:
    if rule.verification == RequirementVerification.ATTESTED:
        # We assert only that something was supplied against this row — never
        # what it is. Presence of the tag is the whole test.
        return _tagged_for(rule, documents)

    accepts = set(rule.accepts)
    if not accepts:
        return []

    if basis == BASIS_UNIVERSAL:
        # No triage has run yet: fall back to the slot the claimant declared.
        return [d for d in documents if ROLE_EXPECTED_KINDS.get(d.document_type, set()) & accepts]

    # Confirmed basis: only what triage independently identified counts. Note
    # this ignores the declared slot entirely — an invoice uploaded into the
    # wrong box still satisfies an invoice requirement.
    return [
        d for d in documents
        if d.classification_kind in accepts
        and d.classification_verdict != TriageVerdict.MISMATCH
    ]


def _inconclusive(
    rule: RequirementRule, documents: list[DocumentRecord], basis: str
) -> list[DocumentRecord]:
    """Uploads aimed at this requirement that we could not read or classify."""
    if basis == BASIS_UNIVERSAL:
        return _tagged_for(rule, documents)
    out = []
    for d in documents:
        if not _addresses_rule(d, rule):
            continue
        if (
            not d.classification_done
            or d.classification_kind is None
            or d.classification_kind in INCONCLUSIVE_KINDS
            or d.classification_legible is False
        ):
            out.append(d)
    return out


def _message(rule: RequirementRule, status: RequirementStatus, docs: list[DocumentRecord]) -> str:
    attested = rule.verification == RequirementVerification.ATTESTED
    if status == RequirementStatus.SATISFIED:
        if attested:
            # Never imply we checked something we did not.
            return "Received. We have not verified its contents."
        return "Received and verified."
    if status == RequirementStatus.UNVERIFIED:
        return (
            "We received a file for this but could not read it clearly. "
            "Please upload a sharper copy — a straight, well-lit photo of the "
            "whole page, or the original PDF."
        )
    return rule.help_text or f"Please upload your {rule.label.lower()}."


def evaluate(
    rules: list[RequirementRule],
    documents: list[DocumentRecord],
    basis: str,
    ruleset: Optional[RequirementRuleSet] = None,
    revision: int = 1,
    claim_type: Optional[str] = None,
    claim_type_confidence: Optional[float] = None,
    ambiguous: bool = False,
    notes: Optional[list[str]] = None,
    evidence_ids: Optional[list[str]] = None,
) -> LORPack:
    """Check each narrowed requirement against what the claimant supplied.

    `evidence_ids` are the damage photos. They arrive through the app's camera
    flow into ClaimState.evidence rather than into `documents`, so a requirement
    for photographs would otherwise be permanently unsatisfiable — and, being
    near-universal, would stall every claim. Their capture path already
    establishes what they are (capture stage, hash, geotag), so their presence
    satisfies a rule that accepts DAMAGE_PHOTOGRAPH.
    """
    documents = list(documents or [])
    evidence_ids = list(evidence_ids or [])
    pack = LORPack(
        revision=revision,
        basis=basis,
        claim_type=claim_type,
        claim_type_confidence=claim_type_confidence,
        claim_type_ambiguous=ambiguous,
        ruleset_id=ruleset.ruleset_id if ruleset else "",
        ruleset_version=ruleset.version if ruleset else "",
        notes=list(notes or []),
    )
    if ruleset and claim_type:
        for section in ruleset.claim_types:
            if section.id == claim_type:
                pack.claim_type_label = section.label
                break

    for rule in rules:
        hit_ids = [d.document_id for d in _satisfying(rule, documents, basis)]
        if not hit_ids and evidence_ids and DocumentKind.DAMAGE_PHOTOGRAPH in set(rule.accepts):
            hit_ids = list(evidence_ids)

        if hit_ids:
            status = RequirementStatus.SATISFIED
            hits = []
        else:
            hits = _inconclusive(rule, documents, basis)
            hit_ids = [d.document_id for d in hits]
            status = RequirementStatus.UNVERIFIED if hits else RequirementStatus.MISSING

        result = RequirementResult(
            requirement_id=rule.requirement_id,
            label=rule.label,
            help_text=rule.help_text,
            status=status,
            severity=rule.severity,
            verification=rule.verification,
            message=_message(rule, status, hits),
            satisfied_by=hit_ids,
        )
        if status == RequirementStatus.SATISFIED:
            pack.satisfied.append(result)
        elif status == RequirementStatus.UNVERIFIED:
            pack.unverified.append(result)
        else:
            pack.missing.append(result)
            # Only a genuinely absent BLOCKING requirement halts the claim.
            # UNVERIFIED never does: we do not stall a real claim because a
            # scan was blurry, we ask for a better copy while work continues.
            if rule.severity == RequirementSeverity.BLOCKING:
                pack.blocking_missing.append(rule.requirement_id)

    return pack


# --------------------------------------------------------------------------
# Convenience entry points used by the graph and the service layer
# --------------------------------------------------------------------------

def facts_from_state(policy: dict, event: dict) -> ClaimFacts:
    categories = (policy or {}).get("asset_categories") or []
    if isinstance(categories, str):
        categories = [categories]
    return ClaimFacts(
        categories=list(categories),
        item_type=(event or {}).get("item_type") or "",
        account_type=(event or {}).get("account_type") or "",
    )


def build_lor(
    policy: dict,
    event: dict,
    documents: list[DocumentRecord],
    basis: str,
    revision: int = 1,
    claim_type: Optional[str] = None,
    claim_type_confidence: Optional[float] = None,
    candidate_types: Iterable[str] = (),
    ambiguous: bool = False,
    evidence: Optional[list] = None,
) -> LORPack:
    """Load, narrow and evaluate in one call. Never raises."""
    notes: list[str] = []
    ruleset = load_ruleset((policy or {}).get("insurer"))
    if ruleset is None:
        return LORPack(
            revision=revision,
            basis=basis,
            notes=["No requirement list is configured, so no document checklist could be produced."],
        )
    facts = facts_from_state(policy, event)
    rules = narrow(
        ruleset, facts,
        claim_type=claim_type, candidate_types=candidate_types, ambiguous=ambiguous,
    )
    if ambiguous:
        notes.append(
            "We could not tell with confidence what kind of claim this is, so this "
            "list covers every possibility. Nothing here is holding your claim up — "
            "correct the claim type to narrow it down."
        )
    # Accept EvidenceRecord objects or raw dicts — build_lor is called both from
    # inside the graph (objects) and from the service on stored JSON (dicts).
    evidence_ids = []
    for e in (evidence or []):
        eid = getattr(e, "evidence_id", None) or (e.get("evidence_id") if isinstance(e, dict) else None)
        if eid:
            evidence_ids.append(eid)

    return evaluate(
        rules, documents, basis,
        ruleset=ruleset, revision=revision,
        claim_type=claim_type, claim_type_confidence=claim_type_confidence,
        ambiguous=ambiguous, notes=notes, evidence_ids=evidence_ids,
    )
