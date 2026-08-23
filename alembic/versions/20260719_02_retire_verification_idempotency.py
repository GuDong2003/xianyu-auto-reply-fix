"""Retire idempotency keys held by terminal verification sessions."""

from __future__ import annotations

from alembic import op

revision = "20260719_02"
down_revision = "20260719_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE account_verification_sessions
        SET idempotency_key = idempotency_key || ':retired:' || session_id
        WHERE state IN ('succeeded', 'failed', 'expired', 'cancelled', 'manual_device_required')
        """
    )


def downgrade() -> None:
    # Archived keys remain valid under the previous scoped uniqueness constraint.
    pass
