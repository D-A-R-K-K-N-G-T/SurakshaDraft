"""
Turn a client's master Letter of Requirement into a reviewable JSON ruleset.

Runs ONCE per client at onboarding, offline. The claim-time path never reads a
master LOR document — it reads only the JSON this produces, after a human has
looked it over.

    python -m agentic_pipeline.ingest_requirements --pdf master_lor.pdf --insurer "ICICI Lombard"
      -> agentic_pipeline/rulesets/icici-lombard.json
      -> agentic_pipeline/rulesets/icici-lombard.coverage.md

Two extraction passes (sections, then requirements), because they are different
problems and the second is much more accurate once the section ids exist. The
model names each document in the insurer's own words; PYTHON then decides
whether that name maps onto a DocumentKind we can actually verify. The model
never picks the verification tier and never picks a DocumentKind, so `accepts`
is provably inside the enum — the same split that keeps the verdict out of the
model's hands in _triage_verdict.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date

from langchain_core.messages import HumanMessage, SystemMessage

from agentic_pipeline.images import build_image_block, load_image_as_data_url
from agentic_pipeline.llm import get_structured_llm, invoke_structured
from agentic_pipeline.prompts import (
    LOR_REQUIREMENTS_HUMAN_PROMPT_TEMPLATE,
    LOR_REQUIREMENTS_SYSTEM_PROMPT,
    LOR_SECTIONS_HUMAN_PROMPT_TEMPLATE,
    LOR_SECTIONS_SYSTEM_PROMPT,
)
from agentic_pipeline.requirements import RULESET_DIR, slugify
from agentic_pipeline.schemas import (
    ClaimTypeSection,
    ClaimTypeSectionsOutput,
    DocumentKind,
    RequirementCondition,
    RequirementRule,
    RequirementRuleSet,
    RequirementsOutput,
    RequirementVerification,
)

# Document names (as insurers write them) -> the kind triage can actually
# identify. Deliberately conservative: a name that is not clearly one of these
# becomes an `attested` requirement rather than being force-fitted onto a kind,
# because a wrong mapping produces a checklist row that can never be ticked.
# Matched against the lowercased document name; longest pattern wins.
_KIND_SYNONYMS: list[tuple[str, DocumentKind]] = [
    ("policy schedule", DocumentKind.POLICY_SCHEDULE),
    ("policy copy", DocumentKind.POLICY_SCHEDULE),
    ("policy certificate", DocumentKind.POLICY_SCHEDULE),
    ("certificate of insurance", DocumentKind.POLICY_SCHEDULE),
    ("premium receipt", DocumentKind.PREMIUM_RECEIPT),
    ("premium payment", DocumentKind.PREMIUM_RECEIPT),
    ("kyc", DocumentKind.GOVT_ID),
    ("identity proof", DocumentKind.GOVT_ID),
    ("id proof", DocumentKind.GOVT_ID),
    ("photo id", DocumentKind.GOVT_ID),
    ("aadhaar", DocumentKind.GOVT_ID),
    ("pan card", DocumentKind.GOVT_ID),
    ("driving licence", DocumentKind.DRIVING_LICENCE),
    ("driving license", DocumentKind.DRIVING_LICENCE),
    ("registration certificate", DocumentKind.VEHICLE_RC),
    ("rc book", DocumentKind.VEHICLE_RC),
    ("rc copy", DocumentKind.VEHICLE_RC),
    ("tax invoice", DocumentKind.TAX_INVOICE),
    ("purchase invoice", DocumentKind.TAX_INVOICE),
    ("purchase bill", DocumentKind.TAX_INVOICE),
    ("original invoice", DocumentKind.TAX_INVOICE),
    ("bill of sale", DocumentKind.TAX_INVOICE),
    ("stock register", DocumentKind.STOCK_REGISTER),
    ("stock statement", DocumentKind.STOCK_REGISTER),
    ("inventory register", DocumentKind.STOCK_REGISTER),
    ("fir", DocumentKind.FIR_REPORT),
    ("first information report", DocumentKind.FIR_REPORT),
    ("police complaint", DocumentKind.FIR_REPORT),
    ("police report", DocumentKind.FIR_REPORT),
    ("station diary", DocumentKind.FIR_REPORT),
    ("fire brigade report", DocumentKind.FIRE_BRIGADE_REPORT),
    ("fire service report", DocumentKind.FIRE_BRIGADE_REPORT),
    ("repair estimate", DocumentKind.REPAIR_ESTIMATE),
    ("estimate of repair", DocumentKind.REPAIR_ESTIMATE),
    ("repair quotation", DocumentKind.REPAIR_ESTIMATE),
    ("quotation", DocumentKind.REPAIR_ESTIMATE),
    ("repair bill", DocumentKind.REPAIR_BILL),
    ("repair invoice", DocumentKind.REPAIR_BILL),
    ("final bill", DocumentKind.REPAIR_BILL),
    ("cancelled cheque", DocumentKind.BANK_PROOF),
    ("canceled cheque", DocumentKind.BANK_PROOF),
    ("bank passbook", DocumentKind.BANK_PROOF),
    ("bank details", DocumentKind.BANK_PROOF),
    ("neft mandate", DocumentKind.BANK_PROOF),
    ("medical certificate", DocumentKind.MEDICAL_CERTIFICATE),
    ("doctor's certificate", DocumentKind.MEDICAL_CERTIFICATE),
    ("fitness certificate", DocumentKind.MEDICAL_CERTIFICATE),
    ("photograph", DocumentKind.DAMAGE_PHOTOGRAPH),
    ("photographs of the damage", DocumentKind.DAMAGE_PHOTOGRAPH),
    ("damage photo", DocumentKind.DAMAGE_PHOTOGRAPH),
]


def map_to_kind(document_name: str, label: str = "") -> DocumentKind | None:
    """Longest matching synonym wins, so "repair bill" beats a bare "bill"."""
    haystack = f"{document_name} {label}".lower()
    best: tuple[int, DocumentKind] | None = None
    for pattern, kind in _KIND_SYNONYMS:
        if pattern in haystack and (best is None or len(pattern) > best[0]):
            best = (len(pattern), kind)
    return best[1] if best else None


def _safe_requirement_id(raw: str, fallback_index: int) -> str:
    cleaned = re.sub(r"[^A-Z0-9-]+", "-", (raw or "").upper()).strip("-")
    if not cleaned:
        return f"REQ-{fallback_index}"
    return cleaned if cleaned.startswith("REQ-") else f"REQ-{cleaned}"


def extract_sections(block: dict) -> list[ClaimTypeSection]:
    llm = get_structured_llm(ClaimTypeSectionsOutput, want_vision=True)
    result: ClaimTypeSectionsOutput = invoke_structured(llm, [
        SystemMessage(content=LOR_SECTIONS_SYSTEM_PROMPT),
        HumanMessage(content=[
            {"type": "text", "text": LOR_SECTIONS_HUMAN_PROMPT_TEMPLATE},
            block,
        ]),
    ])
    return [
        ClaimTypeSection(
            id=re.sub(r"[^a-z0-9_]+", "_", s.id.lower()).strip("_") or f"section_{i}",
            label=s.label,
            description=s.description,
            aliases=s.aliases,
        )
        for i, s in enumerate(result.claim_types)
    ]


def extract_requirements(block: dict, sections: list[ClaimTypeSection]) -> RequirementsOutput:
    catalogue = "\n".join(f"- id={s.id} | {s.label}" for s in sections) or "(none — one flat list)"
    llm = get_structured_llm(RequirementsOutput, want_vision=True)
    return invoke_structured(llm, [
        SystemMessage(content=LOR_REQUIREMENTS_SYSTEM_PROMPT),
        HumanMessage(content=[
            {"type": "text", "text": LOR_REQUIREMENTS_HUMAN_PROMPT_TEMPLATE.format(
                claim_types=catalogue
            )},
            block,
        ]),
    ])


def build_ruleset(
    pdf_path: str, insurer: str, sections: list[ClaimTypeSection], extracted: RequirementsOutput
) -> tuple[RequirementRuleSet, list[dict]]:
    valid_ids = {s.id for s in sections}
    rules: list[RequirementRule] = []
    coverage: list[dict] = []
    seen: set[str] = set()

    for i, r in enumerate(extracted.requirements, start=1):
        rid = _safe_requirement_id(r.requirement_id, i)
        while rid in seen:
            rid = f"{rid}-{i}"
        seen.add(rid)

        kind = map_to_kind(r.document_name, r.label)
        if kind is not None:
            verification = RequirementVerification.CLASSIFIED
            accepts = [kind]
        else:
            # No reliable classifier for this document. It still reaches the
            # claimant's checklist; we simply never claim to have verified it.
            verification = RequirementVerification.ATTESTED
            accepts = []

        rules.append(RequirementRule(
            requirement_id=rid,
            label=r.label,
            help_text=r.help_text,
            claim_types=[c for c in r.claim_types if c in valid_ids],
            verification=verification,
            accepts=accepts,
            severity=r.severity,
            applies_when=RequirementCondition(
                categories=r.categories,
                item_types=r.item_types,
                account_types=r.account_types,
            ),
        ))
        coverage.append({
            "requirement_id": rid,
            "label": r.label,
            "document_name": r.document_name,
            "tier": verification.value,
            "kind": kind.value if kind else None,
            "claim_types": r.claim_types,
            "severity": r.severity.value,
        })

    ruleset = RequirementRuleSet(
        ruleset_id=slugify(insurer),
        version=date.today().isoformat(),
        source=os.path.basename(pdf_path),
        claim_types=sections,
        rules=rules,
    )
    return ruleset, coverage


def write_coverage_report(path: str, ruleset: RequirementRuleSet, coverage: list[dict]) -> None:
    classified = [c for c in coverage if c["tier"] == "classified"]
    attested = [c for c in coverage if c["tier"] == "attested"]
    lines = [
        f"# Ingestion coverage — {ruleset.ruleset_id}",
        "",
        f"Source: `{ruleset.source}`  ",
        f"Extracted {len(coverage)} requirements across {len(ruleset.claim_types)} claim types.",
        "",
        f"- **{len(classified)}** machine-verifiable (`classified`) — triage confirms what the file is.",
        f"- **{len(attested)}** accepted on upload (`attested`) — listed on the checklist, contents not verified.",
        "",
        "Review both tables before this ruleset goes live. In particular, check that",
        "every `blocking` requirement really is mandatory: a wrong one stalls real claims.",
        "",
        "## Machine-verified",
        "",
        "| Requirement | Document name in the LOR | Kind | Claim types | Severity |",
        "|---|---|---|---|---|",
    ]
    for c in classified:
        lines.append(
            f"| {c['requirement_id']} — {c['label']} | {c['document_name']} | `{c['kind']}` "
            f"| {', '.join(c['claim_types']) or 'universal'} | {c['severity']} |"
        )
    lines += [
        "",
        "## Accepted on upload, contents not verified",
        "",
        "These document names did not map onto a `DocumentKind`. That is expected for",
        "the long tail, but names appearing here often across clients are the backlog",
        "for new `DocumentKind` members (add the enum value AND a describing line in",
        "`DOC_TRIAGE_SYSTEM_PROMPT`, or the model will never emit it).",
        "",
        "| Requirement | Document name in the LOR | Claim types | Severity |",
        "|---|---|---|---|",
    ]
    for c in attested:
        lines.append(
            f"| {c['requirement_id']} — {c['label']} | {c['document_name']} "
            f"| {', '.join(c['claim_types']) or 'universal'} | {c['severity']} |"
        )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Ingest a client's master Letter of Requirement into a ruleset."
    )
    parser.add_argument("--pdf", required=True, help="Path to the master LOR (PDF or image).")
    parser.add_argument("--insurer", required=True, help='Insurer name, e.g. "ICICI Lombard".')
    parser.add_argument("--out-dir", default=RULESET_DIR)
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite an existing ruleset for this insurer.",
    )
    args = parser.parse_args(argv)

    if not os.path.exists(args.pdf):
        print(f"No such file: {args.pdf}", file=sys.stderr)
        return 1

    slug = slugify(args.insurer)
    os.makedirs(args.out_dir, exist_ok=True)
    out_path = os.path.join(args.out_dir, f"{slug}.json")
    if os.path.exists(out_path) and not args.force:
        print(
            f"{out_path} already exists. It may carry hand-made corrections — "
            f"pass --force to overwrite.",
            file=sys.stderr,
        )
        return 1

    block = build_image_block(load_image_as_data_url(args.pdf))

    print(f"Reading claim-type sections from {args.pdf} ...")
    sections = extract_sections(block)
    print(f"  found {len(sections)}: {', '.join(s.id for s in sections) or '(none)'}")

    print("Reading requirements ...")
    extracted = extract_requirements(block, sections)
    print(f"  found {len(extracted.requirements)}")

    ruleset, coverage = build_ruleset(args.pdf, args.insurer, sections, extracted)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ruleset.model_dump(mode="json"), fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    coverage_path = os.path.join(args.out_dir, f"{slug}.coverage.md")
    write_coverage_report(coverage_path, ruleset, coverage)

    n_classified = sum(1 for c in coverage if c["tier"] == "classified")
    print(f"\nWrote {out_path}")
    print(f"Wrote {coverage_path}")
    print(
        f"  {n_classified} machine-verified, {len(coverage) - n_classified} accepted on upload."
    )
    print("\nReview both files before using this ruleset — especially the `blocking` rows.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
