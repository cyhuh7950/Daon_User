"""연결별 명시 추론 계약. 기존 연결은 미확인으로 보존한다."""
from pathlib import Path
from alembic import op

revision = "0074"
down_revision = "0073"
branch_labels = None
depends_on = None


def upgrade():
    op.get_bind().execution_options(no_parameters=True).exec_driver_sql(
        Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade():
    # 운영 설정을 지우는 downgrade 대신 명시적인 forward 복구를 요구한다.
    raise RuntimeError("PROVIDER_REASONING_FORWARD_RECOVERY_REQUIRED")
