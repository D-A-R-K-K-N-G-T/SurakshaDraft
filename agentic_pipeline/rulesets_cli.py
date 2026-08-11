"""Ruleset catalogue CLI (Phase 2).

Manages versioned master-LOR rulesets in the database, replacing the
`--force`-overwrite path in ingest_requirements.py. Draft rows are imported
first, then promoted with an explicit, separate `activate` step so hand
corrections are protected structurally rather than by an error message.

    python -m agentic_pipeline.rulesets_cli import-json agentic_pipeline/rulesets/default.json
    python -m agentic_pipeline.rulesets_cli activate default 2026-08-09
    python -m agentic_pipeline.rulesets_cli list
    python -m agentic_pipeline.rulesets_cli diff default 2026-08-09 2026-09-01

Requires DATABASE_URL to point at a migrated database (alembic upgrade head).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys

from sqlalchemy import select

from agentic_pipeline import models as M
from agentic_pipeline.db import session_scope
from agentic_pipeline.requirements import slugify
from agentic_pipeline.schemas import RequirementRuleSet


def _load_json(path: str) -> RequirementRuleSet:
    with open(path, "r", encoding="utf-8") as fh:
        return RequirementRuleSet.model_validate(json.load(fh))


def cmd_import_json(args) -> int:
    data = _load_json(args.path)
    slug = slugify(data.ruleset_id)
    version = data.version or ""
    if not version:
        print("ERROR: ruleset has no version; refusing to import.", file=sys.stderr)
        return 2

    with session_scope() as s:
        existing = s.execute(
            select(M.Ruleset).where(
                M.Ruleset.ruleset_slug == slug, M.Ruleset.version == version
            )
        ).scalar_one_or_none()
        if existing is not None:
            if not args.replace:
                print(
                    f"ERROR: {slug} v{version} already exists (status={existing.status}). "
                    f"Use --replace to overwrite a DRAFT, or bump the version.",
                    file=sys.stderr,
                )
                return 2
            if existing.status != "draft":
                print(
                    f"ERROR: {slug} v{version} is {existing.status}, not a draft; refusing --replace.",
                    file=sys.stderr,
                )
                return 2
            s.delete(existing)  # cascades to children
            s.flush()

        insurer_id = None
        if slug != "default":
            insurer = s.execute(
                select(M.Insurer).where(M.Insurer.slug == slug)
            ).scalar_one_or_none()
            insurer_id = insurer.id if insurer else None

        ruleset = M.Ruleset(
            insurer_id=insurer_id,
            ruleset_slug=slug,
            version=version,
            source=data.source or None,
            status="draft",
        )
        s.add(ruleset)
        s.flush()

        section_by_key: dict[str, M.RulesetClaimType] = {}
        for i, ct in enumerate(data.claim_types):
            row = M.RulesetClaimType(
                ruleset_id=ruleset.id,
                section_key=ct.id,
                label=ct.label,
                description=ct.description,
                aliases=list(ct.aliases),
                ordinal=i,
            )
            s.add(row)
            section_by_key[ct.id] = row
        s.flush()

        skipped_links = 0
        for i, rule in enumerate(data.rules):
            rr = M.RequirementRuleRow(
                ruleset_id=ruleset.id,
                requirement_id=rule.requirement_id,
                label=rule.label,
                help_text=rule.help_text,
                verification=rule.verification.value,
                accepts=[a.value for a in rule.accepts],
                severity=rule.severity.value,
                when_categories=list(rule.applies_when.categories),
                when_item_types=list(rule.applies_when.item_types),
                when_account_types=list(rule.applies_when.account_types),
                ordinal=i,
            )
            s.add(rr)
            s.flush()
            for key in rule.claim_types:
                section = section_by_key.get(key)
                if section is None:
                    skipped_links += 1
                    continue
                s.add(
                    M.RequirementRuleClaimType(
                        rule_id=rr.id, claim_type_id=section.id
                    )
                )

        print(
            f"Imported DRAFT {slug} v{version}: "
            f"{len(data.claim_types)} claim types, {len(data.rules)} rules."
        )
        if skipped_links:
            print(f"  WARNING: {skipped_links} rule->claim_type link(s) referenced unknown sections.")
        print(f"  Activate with: python -m agentic_pipeline.rulesets_cli activate {slug} {version}")
    return 0


def cmd_activate(args) -> int:
    with session_scope() as s:
        target = s.execute(
            select(M.Ruleset).where(
                M.Ruleset.ruleset_slug == args.slug, M.Ruleset.version == args.version
            )
        ).scalar_one_or_none()
        if target is None:
            print(f"ERROR: {args.slug} v{args.version} not found.", file=sys.stderr)
            return 2
        if target.status == "active":
            print(f"{args.slug} v{args.version} is already active.")
            return 0

        # Retire the currently-active version first — the rulesets_one_active
        # partial unique index forbids two active rows for one slug.
        current = s.execute(
            select(M.Ruleset).where(
                M.Ruleset.ruleset_slug == args.slug, M.Ruleset.status == "active"
            )
        ).scalar_one_or_none()
        if current is not None:
            current.status = "retired"
            s.flush()

        target.status = "active"
        target.activated_at = dt.datetime.now(dt.timezone.utc)
        print(
            f"Activated {args.slug} v{args.version}"
            + (f" (retired v{current.version})." if current else ".")
        )
    return 0


def cmd_list(args) -> int:
    with session_scope() as s:
        rows = s.execute(
            select(M.Ruleset).order_by(M.Ruleset.ruleset_slug, M.Ruleset.ingested_at)
        ).scalars().all()
        if not rows:
            print("(no rulesets imported)")
            return 0
        for r in rows:
            marker = "*" if r.status == "active" else " "
            print(f"{marker} {r.ruleset_slug:<24} v{r.version:<14} {r.status}")
    return 0


def cmd_diff(args) -> int:
    def _load(version):
        # Reuse the reconstruction path by temporarily looking up the row.
        with session_scope() as s:
            row = s.execute(
                select(M.Ruleset).where(
                    M.Ruleset.ruleset_slug == args.slug, M.Ruleset.version == version
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            return {rr.requirement_id: rr for rr in row.rules}

    a = _load(args.version_a)
    b = _load(args.version_b)
    if a is None or b is None:
        missing = args.version_a if a is None else args.version_b
        print(f"ERROR: {args.slug} v{missing} not found.", file=sys.stderr)
        return 2

    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    common = sorted(set(a) & set(b))
    changed = [
        rid for rid in common
        if (a[rid].severity, a[rid].verification, tuple(a[rid].accepts))
        != (b[rid].severity, b[rid].verification, tuple(b[rid].accepts))
    ]
    print(f"diff {args.slug}: v{args.version_a} -> v{args.version_b}")
    for rid in added:
        print(f"  + {rid}")
    for rid in removed:
        print(f"  - {rid}")
    for rid in changed:
        print(f"  ~ {rid} (severity/verification/accepts changed)")
    if not (added or removed or changed):
        print("  (no requirement-level changes)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="rulesets_cli", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import-json", help="Import a ruleset JSON as a DRAFT")
    imp.add_argument("path")
    imp.add_argument("--replace", action="store_true", help="Overwrite an existing DRAFT of the same version")
    imp.set_defaults(func=cmd_import_json)

    act = sub.add_parser("activate", help="Promote a version to ACTIVE (retires the previous)")
    act.add_argument("slug")
    act.add_argument("version")
    act.set_defaults(func=cmd_activate)

    lst = sub.add_parser("list", help="List all imported rulesets")
    lst.set_defaults(func=cmd_list)

    dif = sub.add_parser("diff", help="Compare two versions of a slug")
    dif.add_argument("slug")
    dif.add_argument("version_a")
    dif.add_argument("version_b")
    dif.set_defaults(func=cmd_diff)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
