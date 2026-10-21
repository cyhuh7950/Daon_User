"""A2 review draft only: execute by a separately approved migration procedure."""
from pathlib import Path
from alembic import op
revision='0076'
down_revision='0075'
branch_labels=None
depends_on=None

def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(Path(__file__).with_suffix('.sql').read_text(encoding='utf-8'))

def downgrade():
    raise RuntimeError('ROUTING_BUDGET_FORWARD_RECOVERY_REQUIRED')
