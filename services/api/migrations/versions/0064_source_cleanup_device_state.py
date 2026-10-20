"""Source cleanup: normal Identity states and exact outbox manifest identity."""
from pathlib import Path

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    raise RuntimeError("SOURCE_CLEANUP_FORWARD_RECOVERY_REQUIRED")
