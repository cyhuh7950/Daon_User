"""S16 forward-only scoped Source cleanup ledger (administrator migration)."""
from pathlib import Path

from alembic import op

revision = "0063"
down_revision = "0062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("SOURCE_CLEANUP_FORWARD_RECOVERY_REQUIRED")
