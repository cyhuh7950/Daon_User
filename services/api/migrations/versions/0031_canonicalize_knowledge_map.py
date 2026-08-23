"""Canonicalize the Studio output type to knowledge_map."""

from alembic import op


revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE workspace_output_version_settings
        SET default_formats =
          (default_formats - 'knowledge_graph') ||
          jsonb_build_object(
            'knowledge_map',
            COALESCE(default_formats -> 'knowledge_map', default_formats -> 'knowledge_graph')
          )
        WHERE default_formats ? 'knowledge_graph'
    """)
    op.execute("""
        ALTER TABLE workspace_output_version_settings
        DROP CONSTRAINT IF EXISTS workspace_output_version_settings_default_formats_check
    """)
    op.execute("""
        ALTER TABLE workspace_output_version_settings
        ADD CONSTRAINT workspace_output_version_settings_default_formats_check
        CHECK (
          jsonb_typeof(default_formats) = 'object'
          AND default_formats ?& ARRAY['evidence_report','compliance_checklist','comparison_table','knowledge_map','business_draft']
          AND NOT (default_formats ? 'knowledge_graph')
        )
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE workspace_output_version_settings
        DROP CONSTRAINT IF EXISTS workspace_output_version_settings_default_formats_check
    """)
