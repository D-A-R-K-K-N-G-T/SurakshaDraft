"""The only place that maps between database rows and ClaimState (Phase 4).

Design (see models.py header):
  * claims.latest_state / latest_lor (JSONB) are the operational state — they
    back GET /claim/{ref} byte-for-byte and are the source for RESUME.
  * RESUME rebuilds an INPUTS-ONLY ClaimState (allowlist) from the snapshot, so
    the additive channels (warnings/anomalies/…) start empty and cannot
    duplicate on re-invoke. This replaces the old _RESET_ON_RESUME denylist.
  * The normalized tables (evidence/documents/line_items/notes) are re-projected
    from each run's final state for durability and the Phase 6 fraud queries;
    they are not on the GET/RESUME read path.

The graph's node signatures do not change — everything routes through here.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import uuid
from typing import Optional

from sqlalchemy import delete, select

from agentic_pipeline import models as M
from agentic_pipeline.schemas import LORPack
from agentic_pipeline.state import ClaimState

_ACCOUNT_TYPES = {"personal", "commercial", "insurance"}
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_AWAITING = "awaiting_documents"
STATUS_FAILED = "failed"


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def new_claim_ref() -> str:
    return f"CLM-{uuid.uuid4().hex[:8].upper()}"


def _enum_value(x):
    return x.value if hasattr(x, "value") else x


def _account_type(state: ClaimState) -> str:
    at = (state.event or {}).get("account_type") or "personal"
    return at if at in _ACCOUNT_TYPES else "personal"


# --------------------------------------------------------------------------
# row <- schema mappers (write side)
# --------------------------------------------------------------------------

def _evidence_row(claim_id, e, vision_processed_ids) -> M.Evidence:
    geo = getattr(e, "geotag", None)
    return M.Evidence(
        claim_id=claim_id,
        evidence_ref=e.evidence_id,
        capture_stage=_enum_value(e.capture_stage),
        sha256=e.sha256,
        captured_at=e.captured_at,
        geotag_lat=geo.lat if geo else None,
        geotag_lon=geo.lon if geo else None,
        gyroscope=e.gyroscope,
        device_attestation_ok=e.device_attestation_ok,
        verified=e.verified,
        verification_reasons=list(e.verification_reasons or []),
        vision_processed_at=(
            _dt.datetime.now(_dt.timezone.utc)
            if e.evidence_id in vision_processed_ids else None
        ),
    )


def _document_row(claim_id, d) -> M.Document:
    return M.Document(
        claim_id=claim_id,
        document_ref=d.document_id,
        document_type=d.document_type,
        sha256=getattr(d, "sha256", None),
        requirement_id=d.requirement_id,
        uploaded_at=d.uploaded_at,
        invoice_date=d.invoice_date,
        extracted_quantity=d.extracted_quantity,
        extracted_unit_value=d.extracted_unit_value,
        extracted_description=d.extracted_description,
        extraction_done=d.extraction_done,
        classification_kind=_enum_value(d.classification_kind),
        classification_verdict=_enum_value(d.classification_verdict),
        classification_confidence=d.classification_confidence,
        classification_legible=d.classification_legible,
        classification_markers=list(d.classification_markers or []),
        classification_done=d.classification_done,
    )


def _line_item_row(claim_id, li) -> M.LineItem:
    return M.LineItem(
        claim_id=claim_id,
        item_ref=li.item_ref,
        name=li.name,
        description=li.description,
        category=li.category,
        quantity=li.quantity,
        serial_number=li.serial_number,
        vision_confidence=li.vision_confidence,
        source="vision",
        unit_value=li.unit_value,
        purchase_value=li.purchase_value,
        depreciation_pct=li.depreciation_pct,
        net_loss=li.net_loss,
        value_source=_enum_value(li.value_source),
        original_quantity_claimed=li.original_quantity_claimed,
        quantity_capped=li.quantity_capped,
        plausibility_notes=list(li.plausibility_notes or []),
        policy_status=_enum_value(li.policy_status),
        policy_clause=li.policy_clause,
        policy_reasoning=li.policy_reasoning,
    )


def _apply_summary(claim: M.Claim, state: ClaimState) -> None:
    """Copy the scalar claim-level fields from a ClaimState onto the row."""
    event = state.event or {}
    claim.event_date = event.get("event_date") if isinstance(event.get("event_date"), _dt.datetime) else claim.event_date
    claim.event_description = event.get("description") or ""
    claim.event_item_type = event.get("item_type") or ""
    claim.policy_snapshot = state.policy or {}
    claim.claim_type_key = state.claim_type
    claim.claim_type_confidence = state.claim_type_confidence
    claim.claim_type_source = state.claim_type_source
    claim.claim_type_candidates = list(state.claim_type_candidates or [])
    claim.claim_type_ambiguous = state.claim_type_ambiguous
    claim.intake_ok = state.intake_ok
    claim.awaiting_documents = state.awaiting_documents
    claim.kyc_status = state.kyc_status


_NOTE_CHANNELS = [
    ("warnings", "warning"),
    ("anomalies", "anomaly"),
    ("intake_reasons", "intake_reason"),
    ("doc_gate_reasons", "doc_gate_reason"),
]


def _insert_derived_notes(session, claim_id, run_id, state: ClaimState) -> None:
    ordinal = 0
    for field, kind in _NOTE_CHANNELS:
        for msg in getattr(state, field) or []:
            session.add(M.ClaimNote(
                claim_id=claim_id, run_id=run_id, kind=kind, message=msg, ordinal=ordinal
            ))
            ordinal += 1


def _reproject_normalized(session, claim_id, state: ClaimState) -> None:
    """Rebuild evidence/documents/line_items (+joins) from a final state.

    Wholesale delete + reinsert: the tables are a projection of the snapshot, so
    this keeps them exactly in step without upsert bookkeeping.
    """
    # FK ondelete CASCADE drops the join rows with their parents.
    session.execute(delete(M.LineItem).where(M.LineItem.claim_id == claim_id))
    session.execute(delete(M.Evidence).where(M.Evidence.claim_id == claim_id))
    session.execute(delete(M.Document).where(M.Document.claim_id == claim_id))
    session.flush()

    vp = set(state.vision_processed_evidence_ids or [])
    ev_by_ref: dict[str, M.Evidence] = {}
    for e in state.evidence or []:
        row = _evidence_row(claim_id, e, vp)
        session.add(row)
        ev_by_ref[e.evidence_id] = row
    doc_by_ref: dict[str, M.Document] = {}
    for d in state.documents or []:
        row = _document_row(claim_id, d)
        session.add(row)
        doc_by_ref[d.document_id] = row
    session.flush()  # assign ids for the joins

    for li in state.line_items or []:
        row = _line_item_row(claim_id, li)
        session.add(row)
        session.flush()
        for ref in li.evidence_refs or []:
            ev = ev_by_ref.get(ref)
            if ev is not None:
                session.add(M.LineItemEvidence(line_item_id=row.id, evidence_id=ev.id))
        for ref in getattr(li, "matched_document_ids", None) or []:
            doc = doc_by_ref.get(ref)
            if doc is not None:
                session.add(M.LineItemDocument(line_item_id=row.id, document_id=doc.id))


# --------------------------------------------------------------------------
# derived outputs & LOR (Phase 5)
# --------------------------------------------------------------------------

def _parse_dt(value) -> Optional[_dt.datetime]:
    if isinstance(value, _dt.datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _write_lor_pack(session, claim_id, run_id, lor) -> None:
    """Persist one LOR revision (idempotent per claim+revision), so every
    checklist revision stays queryable. lor is a LORPack or None."""
    if lor is None:
        return
    existing = session.execute(
        select(M.LORPackRow).where(
            M.LORPackRow.claim_id == claim_id, M.LORPackRow.revision == lor.revision
        )
    ).scalar_one_or_none()
    if existing is not None:
        session.delete(existing)  # cascades results + result_documents
        session.flush()

    pack = M.LORPackRow(
        claim_id=claim_id,
        run_id=run_id,
        revision=lor.revision,
        basis=lor.basis,
        claim_type_key=lor.claim_type,
        claim_type_label=lor.claim_type_label,
        claim_type_confidence=lor.claim_type_confidence,
        claim_type_ambiguous=lor.claim_type_ambiguous,
        ruleset_slug=lor.ruleset_id or None,
        ruleset_version=lor.ruleset_version or "",
        blocking_missing=list(lor.blocking_missing or []),
        notes=list(lor.notes or []),
    )
    session.add(pack)
    session.flush()

    ordinal = 0
    for result in list(lor.satisfied) + list(lor.unverified) + list(lor.missing):
        row = M.LORRequirementResult(
            lor_pack_id=pack.id,
            requirement_id=result.requirement_id,
            label=result.label,
            help_text=result.help_text,
            status=_enum_value(result.status),
            severity=_enum_value(result.severity),
            verification=_enum_value(result.verification),
            message=result.message,
            ordinal=ordinal,
        )
        ordinal += 1
        session.add(row)
        session.flush()
        for ref in result.satisfied_by or []:
            session.add(M.LORResultDocument(result_id=row.id, document_ref=ref))


def _write_run_outputs(session, claim_id, run_id, state: ClaimState) -> None:
    """Reserve estimate, draft pack (+QC), proof receipt and the run's LOR."""
    re = state.reserve_estimate
    if re:
        session.add(M.ReserveEstimate(
            run_id=run_id, claim_id=claim_id,
            confirmed=re.get("confirmed", 0), conditional=re.get("conditional", 0),
            excluded=re.get("excluded", 0), unclassified=re.get("unclassified", 0),
            pending=re.get("pending", 0), screened_out=re.get("screened_out", 0),
        ))

    draft_row = None
    dp = state.draft_pack
    if dp is not None:
        content_hash = hashlib.sha256(
            json.dumps(dp.model_dump(), sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        draft_row = M.DraftPack(
            claim_id=claim_id, run_id=run_id, attempt=state.qc_retries,
            main_schedule=dp.main_schedule,
            rejected_items_annexure=dp.rejected_items_annexure,
            pending_verification_annexure=dp.pending_verification_annexure,
            excluded_items_annexure=dp.excluded_items_annexure,
            narrative=dp.narrative, content_hash=content_hash, is_final=True,
        )
        session.add(draft_row)
        session.flush()
        if state.qc is not None:
            session.add(M.QCResult(
                draft_pack_id=draft_row.id, pass_qc=state.qc.pass_qc,
                flags=list(state.qc.flags or []), check_source="llm",
            ))

    pr = state.proof_receipt
    if pr:
        session.add(M.ProofReceipt(
            claim_id=claim_id, run_id=run_id,
            draft_pack_id=(draft_row.id if draft_row else None),
            receipt_hash=pr.get("receipt_hash") or "",
            sent_at=_parse_dt(pr.get("sent_at")) or _dt.datetime.now(_dt.timezone.utc),
            recipient=pr.get("recipient") or "",
        ))
        # Transactional outbox: enqueue delivery to the insurer intake API in the
        # SAME transaction as the receipt, so it is never lost or double-sent.
        session.add(M.OutboxMessage(
            claim_id=claim_id, topic="insurer.intimation",
            payload={
                "claim_id": str(claim_id),
                "receipt_hash": pr.get("receipt_hash"),
                "recipient": pr.get("recipient"),
                "sent_at": pr.get("sent_at"),
            },
            status="pending", next_retry_at=_dt.datetime.now(_dt.timezone.utc),
        ))

    _write_lor_pack(session, claim_id, run_id, state.lor)


# --------------------------------------------------------------------------
# public API
# --------------------------------------------------------------------------

def create_claim(
    session,
    *,
    state: ClaimState,
    claim_ref: Optional[str] = None,
    user_id: Optional[str] = None,
    policy_id: Optional[str] = None,
) -> tuple[str, str]:
    """Persist a new claim + its first (submit) run. Returns (claim_ref, run_id).

    If policy_id is given, the policy's premises geo is merged into the claim's
    policy dict, which activates the previously-dead geofence check (§4.3/§6.3).
    """
    claim_ref = claim_ref or new_claim_ref()

    if policy_id:
        terms = get_policy_terms(session, policy_id)
        if terms and terms.get("premises_geo") and not (state.policy or {}).get("premises_geo"):
            state.policy = {**(state.policy or {}), "premises_geo": terms["premises_geo"]}

    claim = M.Claim(
        claim_ref=claim_ref,
        user_id=user_id,
        policy_id=policy_id,
        status=STATUS_PROCESSING,
        account_type=_account_type(state),
        latest_lor=(state.lor.model_dump(mode="json") if state.lor else None),
    )
    _apply_summary(claim, state)
    session.add(claim)
    session.flush()

    run = M.ClaimRun(claim_id=claim.id, run_number=1, trigger="submit")
    session.add(run)
    session.flush()

    _reproject_normalized(session, claim.id, state)
    for i, msg in enumerate(state.gateway_blocking_reasons or []):
        session.add(M.ClaimNote(
            claim_id=claim.id, run_id=None, kind="gateway_blocking_reason",
            message=msg, ordinal=i,
        ))
    # rev.1 LOR (run_id NULL — computed at submit, before the run).
    _write_lor_pack(session, claim.id, None, state.lor)
    return claim_ref, str(run.id)


def _get_claim(session, claim_ref: str) -> M.Claim:
    claim = session.execute(
        select(M.Claim).where(M.Claim.claim_ref == claim_ref)
    ).scalar_one_or_none()
    if claim is None:
        raise NotFound(claim_ref)
    return claim


def view_claim(session, claim_ref: str) -> dict:
    """The GET /claim/{ref} response body, shape-identical to the old store."""
    claim = _get_claim(session, claim_ref)
    resp: dict = {"status": claim.status}
    if claim.latest_state is not None:
        resp["state"] = claim.latest_state
    resp["lor"] = claim.latest_lor
    if claim.status == STATUS_FAILED:
        last = session.execute(
            select(M.ClaimRun)
            .where(M.ClaimRun.claim_id == claim.id)
            .order_by(M.ClaimRun.run_number.desc())
        ).scalars().first()
        if last is not None and last.error_message:
            resp["error"] = last.error_message
            resp["traceback"] = last.error_traceback
    return resp


_RESUME_INPUT_FIELDS = (
    "claim_ref",
    "policy", "event", "documents", "evidence", "line_items",
    "claim_type", "claim_type_confidence", "claim_type_source",
    "claim_type_candidates", "claim_type_ambiguous",
    "vision_processed_evidence_ids", "gateway_blocking_reasons",
)


def resume_state_from_snapshot(snapshot: dict) -> ClaimState:
    """Build an INPUTS-ONLY ClaimState from a stored snapshot (allowlist).

    Derived channels (warnings/anomalies/rejected_items/…/lor/draft_pack) are
    omitted, so they default to empty and a re-invoke cannot duplicate additive
    channel entries. This is the structural replacement for the old
    _RESET_ON_RESUME denylist: a new derived field is excluded by default rather
    than needing to be remembered.
    """
    inputs = {k: snapshot[k] for k in _RESUME_INPUT_FIELDS if k in snapshot}
    return ClaimState.model_validate(inputs)


def build_resume_state(session, claim_ref: str) -> ClaimState:
    claim = _get_claim(session, claim_ref)
    if claim.latest_state is None:
        raise Conflict("claim has not completed a run yet")
    return resume_state_from_snapshot(claim.latest_state)


def start_run(session, claim_ref: str, trigger: str) -> str:
    """Open a new run, taking a row lock and refusing if one is in flight (R9)."""
    claim = session.execute(
        select(M.Claim).where(M.Claim.claim_ref == claim_ref).with_for_update()
    ).scalar_one_or_none()
    if claim is None:
        raise NotFound(claim_ref)
    active = session.execute(
        select(M.ClaimRun.id).where(
            M.ClaimRun.claim_id == claim.id, M.ClaimRun.finished_at.is_(None)
        )
    ).first()
    if active is not None:
        raise Conflict("a run is already in progress for this claim")
    max_n = session.execute(
        select(M.ClaimRun.run_number)
        .where(M.ClaimRun.claim_id == claim.id)
        .order_by(M.ClaimRun.run_number.desc())
    ).scalars().first() or 0
    run = M.ClaimRun(claim_id=claim.id, run_number=max_n + 1, trigger=trigger)
    session.add(run)
    session.flush()
    claim.status = STATUS_PROCESSING
    return str(run.id)


def append_document_rows(session, claim_ref: str, documents, run_id: str) -> None:
    """Insert normalized rows for freshly-attached documents (durability)."""
    claim = _get_claim(session, claim_ref)
    for d in documents:
        row = _document_row(claim.id, d)
        row.added_in_run = run_id
        session.add(row)


def persist_success(session, run_id: str, final_state: ClaimState) -> None:
    run = session.get(M.ClaimRun, run_id)
    if run is None:
        raise NotFound(run_id)
    claim = session.execute(
        select(M.Claim).where(M.Claim.id == run.claim_id).with_for_update()
    ).scalar_one()

    status = STATUS_AWAITING if final_state.awaiting_documents else STATUS_COMPLETED
    _apply_summary(claim, final_state)
    claim.status = status
    claim.latest_state = final_state.model_dump(mode="json")
    claim.latest_lor = final_state.lor.model_dump(mode="json") if final_state.lor else None
    claim.updated_at = _dt.datetime.now(_dt.timezone.utc)
    claim.lock_version = (claim.lock_version or 0) + 1
    
    if status == STATUS_COMPLETED:
        claim.retention_expires_at = claim.updated_at + _dt.timedelta(days=365) # 1 year retention

    _reproject_normalized(session, claim.id, final_state)
    _insert_derived_notes(session, claim.id, run_id, final_state)
    _write_run_outputs(session, claim.id, run_id, final_state)

    run.finished_at = _dt.datetime.now(_dt.timezone.utc)
    run.outcome = status


def persist_failure(session, run_id: str, error_message: str, traceback_str: str) -> None:
    run = session.get(M.ClaimRun, run_id)
    if run is None:
        raise NotFound(run_id)
    run.finished_at = _dt.datetime.now(_dt.timezone.utc)
    run.outcome = STATUS_FAILED
    run.error_message = error_message
    run.error_traceback = traceback_str
    claim = session.get(M.Claim, run.claim_id)
    if claim is not None:
        claim.status = STATUS_FAILED


# --------------------------------------------------------------------------
# ops: idempotency, audit, llm invocations (Phase 8)
# --------------------------------------------------------------------------

def reserve_idempotency(session, *, key: str, scope: str, ttl_seconds: int = 86400) -> bool:
    """Atomically claim an idempotency key. True if WE reserved it (caller must
    then fill the response); False if it already existed (a retry)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    now = _dt.datetime.now(_dt.timezone.utc)
    stmt = (
        pg_insert(M.IdempotencyKey.__table__)
        .values(key=key, scope=scope, expires_at=now + _dt.timedelta(seconds=ttl_seconds))
        .on_conflict_do_nothing(index_elements=["key"])
        .returning(M.IdempotencyKey.__table__.c.key)
    )
    return session.execute(stmt).first() is not None


def get_idempotency(session, key: str):
    return session.get(M.IdempotencyKey, key)


def fill_idempotency(session, key: str, *, claim_ref: str, response: dict) -> None:
    row = session.get(M.IdempotencyKey, key)
    if row is None:
        return
    row.response_body = response
    row.claim_id = session.execute(
        select(M.Claim.id).where(M.Claim.claim_ref == claim_ref)
    ).scalar_one_or_none()


def write_audit(session, *, action: str, entity_type: str, entity_id: str,
                actor_id=None, actor_kind: str = "user", before=None, after=None) -> None:
    session.add(M.AuditLog(
        actor_id=actor_id, actor_kind=actor_kind, action=action,
        entity_type=entity_type, entity_id=entity_id, before=before, after=after,
    ))


# --------------------------------------------------------------------------
# identity & policies (Phase 7)
# --------------------------------------------------------------------------

def upsert_user(session, *, provider, subject, email=None, name=None, photo=None) -> str:
    """Get-or-create a user by (auth_provider, auth_subject); return its id."""
    user = session.execute(
        select(M.User).where(
            M.User.auth_provider == provider, M.User.auth_subject == subject
        )
    ).scalar_one_or_none()
    now = _dt.datetime.now(_dt.timezone.utc)
    if user is None:
        user = M.User(
            auth_provider=provider, auth_subject=subject,
            email=email, display_name=name, photo_url=photo, last_seen_at=now,
        )
        session.add(user)
        session.flush()
    else:
        user.last_seen_at = now
        if email and not user.email:
            user.email = email
        if name and not user.display_name:
            user.display_name = name
        if photo and not user.photo_url:
            user.photo_url = photo
    return str(user.id)


def create_policy(session, *, user_id, payload) -> str:
    """Persist a policy with its sums-insured and clause rows; return its id."""
    p = M.Policy(
        user_id=user_id,
        policy_number=payload.policy_number,
        product=payload.product,
        start_date=payload.start_date,
        end_date=payload.end_date,
        excess=payload.excess,
        asset_categories=list(payload.asset_categories or []),
        clauses_text=payload.clauses_text,
        clauses_assumed=payload.clauses_assumed,
        terms_source=payload.terms_source,
        assumed_fields=list(payload.assumed_fields or []),
        premises_lat=payload.premises_lat,
        premises_lon=payload.premises_lon,
    )
    session.add(p)
    session.flush()
    for s in payload.sums_insured or []:
        session.add(M.PolicySumInsured(
            policy_id=p.id, category_key=s.category_key,
            category_label=s.category_label, amount=s.amount,
        ))
    for i, clause in enumerate(payload.clauses or []):
        session.add(M.PolicyClause(policy_id=p.id, ordinal=i, clause_text=clause))
    return str(p.id)


def get_policy_terms(session, policy_id) -> Optional[dict]:
    """Terms to merge into a claim's policy dict. Currently just premises_geo —
    the piece that activates the geofence; other terms come via policy_extract."""
    p = session.get(M.Policy, policy_id)
    if p is None:
        return None
    terms: dict = {}
    if p.premises_lat is not None and p.premises_lon is not None:
        terms["premises_geo"] = {"lat": p.premises_lat, "lon": p.premises_lon}
    return terms


def list_claims(session, *, user_id, limit: int = 20, cursor: Optional[str] = None):
    """A user's claims, newest first, created_at-cursor paginated.
    Returns (items, next_cursor)."""
    q = (
        select(M.Claim)
        .where(M.Claim.user_id == user_id)
        .order_by(M.Claim.created_at.desc(), M.Claim.id.desc())
    )
    if cursor:
        c = _parse_dt(cursor)
        if c is not None:
            q = q.where(M.Claim.created_at < c)
    rows = session.execute(q.limit(limit + 1)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    items = [{
        "claim_ref": r.claim_ref,
        "status": r.status,
        "claim_type": r.claim_type_key,
        "event_description": r.event_description,
        "awaiting_documents": r.awaiting_documents,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]
    next_cursor = rows[-1].created_at.isoformat() if (has_more and rows) else None
    return items, next_cursor


def get_latest_lor(session, claim_ref: str) -> Optional[dict]:
    """The current LOR pack for a claim (claims.latest_lor). Raises NotFound."""
    claim = _get_claim(session, claim_ref)
    return claim.latest_lor


def find_cross_claim_hashes(session, claim_ref: str, sha256s) -> dict[str, str]:
    """sha256 -> a PRIOR claim_ref that already used that photo (R2).

    Excludes the current claim. Replaces graph.py's mock_hash_registry.
    """
    shas = [s for s in dict.fromkeys(sha256s) if s]
    if not shas:
        return {}
    rows = session.execute(
        select(M.Evidence.sha256, M.Claim.claim_ref)
        .join(M.Claim, M.Claim.id == M.Evidence.claim_id)
        .where(M.Evidence.sha256.in_(shas), M.Claim.claim_ref != claim_ref)
        .order_by(M.Claim.created_at)
    ).all()
    out: dict[str, str] = {}
    for sha, ref in rows:
        out.setdefault(sha, ref)
    return out


def find_cross_claim_serials(session, claim_ref: str, serials) -> dict[str, str]:
    """serial_number -> a PRIOR claim_ref that already used it (R2).

    Excludes the current claim. Replaces graph.py's mock_serial_registry.
    """
    wanted = [s for s in dict.fromkeys(serials) if s]
    if not wanted:
        return {}
    rows = session.execute(
        select(M.LineItem.serial_number, M.Claim.claim_ref)
        .join(M.Claim, M.Claim.id == M.LineItem.claim_id)
        .where(M.LineItem.serial_number.in_(wanted), M.Claim.claim_ref != claim_ref)
        .order_by(M.Claim.created_at)
    ).all()
    out: dict[str, str] = {}
    for serial, ref in rows:
        out.setdefault(serial, ref)
    return out


def override_claim_type(session, claim_ref: str, *, claim_type_id: str, pack: LORPack) -> None:
    """Record a user's claim-type correction and its rebuilt checklist.

    Deterministic and runless (matches the old in-process override). Patches the
    snapshot too, so GET's `state.lor` reflects the new pack immediately.
    """
    claim = session.execute(
        select(M.Claim).where(M.Claim.claim_ref == claim_ref).with_for_update()
    ).scalar_one_or_none()
    if claim is None:
        raise NotFound(claim_ref)
    if claim.latest_state is None:
        raise Conflict("claim has not completed a run yet")

    before = {"claim_type_key": claim.claim_type_key, "claim_type_source": claim.claim_type_source}
    pack_json = pack.model_dump(mode="json")
    claim.claim_type_key = claim_type_id
    claim.claim_type_confidence = 1.0
    claim.claim_type_source = "user_override"
    claim.claim_type_candidates = [claim_type_id]
    claim.claim_type_ambiguous = False
    claim.awaiting_documents = bool(pack.blocking_missing)
    claim.latest_lor = pack_json
    if pack.blocking_missing:
        claim.status = STATUS_AWAITING

    snap = dict(claim.latest_state)
    snap.update({
        "claim_type": claim_type_id,
        "claim_type_confidence": 1.0,
        "claim_type_source": "user_override",
        "claim_type_candidates": [claim_type_id],
        "claim_type_ambiguous": False,
        "lor": pack_json,
        "awaiting_documents": bool(pack.blocking_missing),
    })
    claim.latest_state = snap
    claim.lock_version = (claim.lock_version or 0) + 1

    # Persist the corrected checklist as its own queryable revision (runless).
    _write_lor_pack(session, claim.id, None, pack)

    write_audit(
        session, action="claim_type.override", entity_type="claim", entity_id=claim_ref,
        actor_kind="user", before=before,
        after={"claim_type_key": claim_type_id, "claim_type_source": "user_override"},
    )

def record_llm_invocation(session, *, provider: str, model: str, succeeded: bool,
                          claim_id=None, run_id=None, node_name=None, used_vision=False,
                          latency_ms=None, error_type=None, prompt_tokens=None,
                          output_tokens=None, input_summary=None, output_raw=None) -> None:
    session.add(M.LLMInvocation(
        claim_id=claim_id, run_id=run_id, node_name=node_name, provider=provider,
        model=model, used_vision=used_vision, latency_ms=latency_ms,
        succeeded=succeeded, error_type=error_type, prompt_tokens=prompt_tokens,
        output_tokens=output_tokens, input_summary=input_summary, output_raw=output_raw,
    ))

def reap_stale_runs(session) -> int:
    """Find runs stuck in 'running' for > 1 hour, mark them failed, and unlock the claim."""
    now = _dt.datetime.now(_dt.timezone.utc)
    stale_threshold = now - _dt.timedelta(hours=1)
    
    stale_runs = session.execute(
        select(M.ClaimRun)
        .where(M.ClaimRun.outcome == "running")
        .where(M.ClaimRun.created_at < stale_threshold)
    ).scalars().all()
    
    count = 0
    for run in stale_runs:
        run.outcome = STATUS_FAILED
        run.error_message = "Reaped after 1 hour timeout"
        run.finished_at = now
        
        claim = session.get(M.Claim, run.claim_id)
        if claim:
            claim.status = STATUS_FAILED
        count += 1
    
    session.flush()
    return count

def drain_outbox(session) -> int:
    """Drain the outbox and simulate sending to an intake API."""
    if not hasattr(M, "OutboxMessage"):
        return 0
    
    messages = session.execute(
        select(M.OutboxMessage).where(M.OutboxMessage.processed_at.is_(None)).limit(100)
    ).scalars().all()
    
    now = _dt.datetime.now(_dt.timezone.utc)
    for msg in messages:
        msg.processed_at = now
    
    session.flush()
    return len(messages)

def gc_expired_claims(session) -> int:
    """Delete claims and their files past retention_expires_at."""
    now = _dt.datetime.now(_dt.timezone.utc)
    expired_claims = session.execute(
        select(M.Claim).where(M.Claim.retention_expires_at < now)
    ).scalars().all()
    
    count = 0
    for claim in expired_claims:
        session.delete(claim)
        count += 1
        
    session.flush()
    return count
