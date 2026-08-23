"""Add manual verification session, audit, and access-token tables."""

from __future__ import annotations

from alembic import op

from xianyu_connector.infrastructure.schema import VERIFICATION_SCHEMA


revision = "20260718_01"
down_revision = "20260717_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in VERIFICATION_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_verification_tokens_session")
    op.execute("DROP TABLE IF EXISTS verification_access_tokens")
    op.execute("DROP INDEX IF EXISTS idx_verification_events_session_time")
    op.execute("DROP TABLE IF EXISTS account_verification_events")
    op.execute("DROP INDEX IF EXISTS idx_verification_active_account")
    op.execute("DROP TABLE IF EXISTS account_verification_sessions")
