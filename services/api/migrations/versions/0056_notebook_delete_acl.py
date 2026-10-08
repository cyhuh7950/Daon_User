"""Give the scoped Notebook deletion function a separate, non-login owner.

This revision requires a PostgreSQL superuser. Alembic normally stamps 0056
in the same transactional DDL connection. For the approved socket-only admin
path, run the adjacent SQL with psql -1 and stamp 0055 -> 0056 in that SAME
psql -1 invocation; never run the SQL and stamp in separate transactions.
"""

from pathlib import Path

from alembic import op

revision = "0056"
down_revision = "0055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # The function may already have permanently deleted Notebook data. Reverting
    # ownership cannot recover those rows or object-store content, and a 0055
    # API image is not ready against a 0056 database.
    raise RuntimeError("NOTEBOOK_DELETE_ACL_DOWNGRADE_BLOCKED")
