"""Persist forced-password-change and one-time admin bootstrap state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "identity_users",
        sa.Column(
            "password_change_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "identity_bootstrap_state",
        sa.Column("marker_key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("identity_bootstrap_state")
    op.drop_column("identity_users", "password_change_required")
