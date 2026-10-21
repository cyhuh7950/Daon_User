"""Unverified routing price sources draft; real application requires separate approval."""
from pathlib import Path
from alembic import op
revision='0077'
down_revision='0076'
branch_labels=None
depends_on=None
def upgrade():op.execute(Path(__file__).with_suffix('.sql').read_text(encoding='utf8'))
def downgrade():raise RuntimeError('Forward recovery required; immutable price evidence must be preserved')
