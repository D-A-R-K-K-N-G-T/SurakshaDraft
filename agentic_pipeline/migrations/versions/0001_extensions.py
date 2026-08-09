"""extensions: pgcrypto (gen_random_uuid) + citext (case-insensitive email)

Revision ID: 0001_extensions
Revises:
Create Date: 2026-08-09
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0001_extensions"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # pgcrypto provides gen_random_uuid() used as every UUID PK's default.
    # citext backs the case-insensitive users.email column.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS pgcrypto")
