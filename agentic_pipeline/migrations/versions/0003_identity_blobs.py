"""identity & blobs: file_blobs, insurers, users

The Phase 1 tables (DB plan §4.2). Constraint names are written to match the
declarative naming_convention exactly so `alembic check` reports no drift.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003_identity_blobs"
down_revision: Union[str, None] = "0002_enums"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "file_blobs",
        sa.Column("sha256", pg.CHAR(64), nullable=False),
        sa.Column("storage_uri", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            pg.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("sha256", name="pk_file_blobs"),
        # Raw token only: the naming_convention (ck_%(table_name)s_%(constraint_name)s)
        # expands this to ck_file_blobs_byte_size_positive, matching the model.
        sa.CheckConstraint("byte_size > 0", name="byte_size_positive"),
    )

    op.create_table(
        "insurers",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            pg.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_insurers"),
        sa.UniqueConstraint("slug", name="uq_insurers_slug"),
    )

    op.create_table(
        "users",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("auth_provider", sa.Text(), nullable=False),
        sa.Column("auth_subject", sa.Text(), nullable=False),
        sa.Column("email", pg.CITEXT(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("photo_url", sa.Text(), nullable=True),
        sa.Column(
            "account_type",
            # Type already created by 0002_enums — do not re-create it here.
            pg.ENUM(
                "personal", "commercial", "insurance",
                name="account_type", create_type=False,
            ),
            server_default=sa.text("'personal'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            pg.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_seen_at", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("auth_provider", "auth_subject", name="auth_identity"),
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("insurers")
    op.drop_table("file_blobs")
