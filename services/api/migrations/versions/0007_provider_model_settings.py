"""Workspace Provider Profile, Model Deployment and role binding persistence."""

from alembic import op


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


TABLES = (
    "provider_setting_profiles",
    "provider_setting_deployments",
    "provider_setting_role_bindings",
)


def upgrade() -> None:
    op.execute("""
        CREATE TABLE provider_setting_profiles (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          profile_id text NOT NULL,
          provider_code text NOT NULL CHECK (provider_code IN (
            'CEREBRAS','GROQ','MISTRAL','OPENAI','UPSTAGE','GEMINI',
            'OPENROUTER','ANTHROPIC','OLLAMA'
          )),
          provider_kind text NOT NULL CHECK (provider_kind IN (
            'local_runtime','server_internal','external_api'
          )),
          base_url text NOT NULL CHECK (length(base_url) BETWEEN 1 AND 2048),
          active boolean NOT NULL DEFAULT false,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_by text NOT NULL,
          policy_version text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, profile_id),
          UNIQUE (tenant_id, workspace_id, provider_code),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id)
        );
        CREATE TABLE provider_setting_deployments (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          deployment_id text NOT NULL,
          profile_id text NOT NULL,
          model_id text NOT NULL CHECK (length(model_id) BETWEEN 1 AND 256),
          roles text[] NOT NULL CHECK (
            cardinality(roles) > 0 AND roles <@ ARRAY[
              'text','vision','audio_understanding','speech_to_text','embedding','reranker'
            ]::text[]
          ),
          active boolean NOT NULL DEFAULT false,
          selected boolean NOT NULL DEFAULT false,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_by text NOT NULL,
          policy_version text NOT NULL,
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, deployment_id),
          FOREIGN KEY (tenant_id, workspace_id, profile_id)
            REFERENCES provider_setting_profiles(tenant_id, workspace_id, profile_id)
        );
        CREATE TABLE provider_setting_role_bindings (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          role text NOT NULL CHECK (role IN (
            'text','vision','audio_understanding','speech_to_text','embedding','reranker'
          )),
          deployment_id text NOT NULL,
          version integer NOT NULL CHECK (version > 0),
          updated_by text NOT NULL,
          policy_version text NOT NULL,
          trace_id text NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, role),
          FOREIGN KEY (tenant_id, workspace_id, deployment_id)
            REFERENCES provider_setting_deployments(tenant_id, workspace_id, deployment_id)
        );
    """)
    predicate = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '') AND "
        "workspace_id = nullif(current_setting('app.workspace_id', true), '')"
    )
    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({predicate}) "
            f"WITH CHECK ({predicate})"
        )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON " + ", ".join(TABLES) + " TO daon_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
