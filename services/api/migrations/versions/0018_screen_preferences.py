"""Persist user-scoped screen preferences independently of Notebook data."""

from alembic import op


revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(r"""
        CREATE TABLE user_screen_preferences (
          tenant_id text NOT NULL,
          actor_id text NOT NULL,
          theme text NOT NULL CHECK (theme IN ('system','light','dark')),
          updated_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id,actor_id)
        );
        ALTER TABLE user_screen_preferences ENABLE ROW LEVEL SECURITY;
        ALTER TABLE user_screen_preferences FORCE ROW LEVEL SECURITY;
        CREATE POLICY user_screen_preferences_scope ON user_screen_preferences
          USING (
            tenant_id=nullif(current_setting('app.tenant_id',true),'')
            AND actor_id=nullif(current_setting('app.actor_id',true),'')
          )
          WITH CHECK (
            tenant_id=nullif(current_setting('app.tenant_id',true),'')
            AND actor_id=nullif(current_setting('app.actor_id',true),'')
          );
        GRANT SELECT,INSERT,UPDATE ON user_screen_preferences TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE user_screen_preferences")
