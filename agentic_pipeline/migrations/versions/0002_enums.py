"""enums: all closed-set PG enum types

Creates every native enum type up front (DB plan §4.1 / §5 ordering). Only
account_type is attached to a column in Phase 1 (users.account_type); the rest
are created now so later phases attach columns without a type-creation step.

Frozen snapshot on purpose: the labels are written literally, not imported from
the app, so a future edit to the Python enums cannot silently rewrite history.
test_enum_parity asserts these live types still match the Python enums.

Adding a value later is its own revision (ALTER TYPE ... ADD VALUE) and is NOT
reversible in a transaction on older PG — never edit this file to add members.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0002_enums"
down_revision: Union[str, None] = "0001_extensions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# pg type name -> ordered labels. Mirrors agentic_pipeline.models.ENUM_LABELS.
ENUMS: dict[str, tuple[str, ...]] = {
    "capture_stage": ("scene", "zone", "item"),
    "policy_status": ("covered", "excluded", "review"),
    "value_source": ("invoice_matched", "catalog_estimate", "unvalued"),
    "triage_verdict": ("match", "unverified", "mismatch"),
    "req_severity": ("blocking", "advisory"),
    "req_verification": ("classified", "attested"),
    "req_status": ("satisfied", "unverified", "missing"),
    "claim_status": ("processing", "completed", "awaiting_documents", "failed"),
    "account_type": ("personal", "commercial", "insurance"),
    "note_kind": (
        "warning",
        "anomaly",
        "intake_reason",
        "doc_gate_reason",
        "gateway_blocking_reason",
    ),
    "document_kind": (
        "policy_schedule",
        "premium_receipt",
        "govt_id",
        "tax_invoice",
        "stock_register",
        "menu_or_price_list",
        "marketing_or_other_commercial",
        "damage_photograph",
        "fir_report",
        "fire_brigade_report",
        "repair_estimate",
        "repair_bill",
        "bank_proof",
        "medical_certificate",
        "driving_licence",
        "vehicle_rc",
        "unreadable",
        "unknown",
    ),
}


def upgrade() -> None:
    for name, labels in ENUMS.items():
        rendered = ", ".join(f"'{label}'" for label in labels)
        op.execute(f"CREATE TYPE {name} AS ENUM ({rendered})")


def downgrade() -> None:
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
