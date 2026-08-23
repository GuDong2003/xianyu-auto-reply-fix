"""Add production connector runtime tables."""

from __future__ import annotations

from alembic import op

from xianyu_connector.infrastructure.schema import CONNECTOR_SCHEMA


revision = "20260717_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    for statement in CONNECTOR_SCHEMA.split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS account_secrets")
    op.execute("DROP INDEX IF EXISTS idx_account_commands_queue")
    op.execute("DROP TABLE IF EXISTS account_commands")
    op.execute("DROP INDEX IF EXISTS idx_runtime_events_account_time")
    op.execute("DROP TABLE IF EXISTS account_runtime_events")
    op.execute("DROP TABLE IF EXISTS account_runtime_states")
