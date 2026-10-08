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
    # Schema/ACL recovery only: Notebook rows deleted while 0056 was active
    # cannot be restored by a migration. Alembic stamps 0055 in this transaction.
    op.execute(Path(__file__).with_name("0056_notebook_delete_acl_downgrade.sql").read_text(encoding="utf-8"))
