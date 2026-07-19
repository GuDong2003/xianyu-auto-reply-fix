"""Add one-time local verification handoff grants."""

from __future__ import annotations

from alembic import op
from xianyu_connector.infrastructure.schema import LOCAL_VERIFICATION_HANDOFF_SCHEMA

revision = "20260718_02"
down_revision = "20260718_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in LOCAL_VERIFICATION_HANDOFF_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_local_handoff_grants_account")
    op.execute("DROP TABLE IF EXISTS local_verification_handoff_grants")
