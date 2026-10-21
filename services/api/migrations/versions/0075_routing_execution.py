"""B2 append-only authorization/claim/attempt draft. Apply only by separate approval."""
from pathlib import Path
from alembic import op
revision='0075'
down_revision='0074'
branch_labels=None
depends_on=None


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))


def downgrade():
    raise RuntimeError('ROUTING_EXECUTION_FORWARD_RECOVERY_REQUIRED')
