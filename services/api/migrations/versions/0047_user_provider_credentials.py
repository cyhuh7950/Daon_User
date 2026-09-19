"""Add user-owned Provider credentials and the remaining provider catalog entries."""

from alembic import op


revision = "0047"
down_revision = "0046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE system_provider_connections
          DROP CONSTRAINT IF EXISTS system_provider_connections_provider_code_check;
        ALTER TABLE system_provider_connections
          ADD CONSTRAINT system_provider_connections_provider_code_check CHECK (provider_code IN (
            'CEREBRAS','GROQ','MISTRAL','OPENAI','UPSTAGE','GEMINI','OPENROUTER','ANTHROPIC',
            'OLLAMA','OMNIROUTE','EOUL_GATEWAY','MEDIA_BRIDGE','SENTENCE_TRANSFORMERS'
          ));

        CREATE TABLE user_provider_credentials (
          tenant_id text NOT NULL,
          user_id text NOT NULL,
          connection_id text NOT NULL REFERENCES system_provider_connections(connection_id) ON DELETE CASCADE,
          provider_code text NOT NULL,
          encrypted_credential bytea NOT NULL,
          credential_nonce bytea NOT NULL CHECK (octet_length(credential_nonce) = 12),
          encryption_key_version integer NOT NULL CHECK (encryption_key_version > 0),
          credential_schema_version integer NOT NULL CHECK (credential_schema_version = 1),
          credential_version integer NOT NULL CHECK (credential_version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, user_id, connection_id)
        );

        GRANT SELECT, INSERT, UPDATE, DELETE ON user_provider_credentials TO daon_app;
        ALTER TABLE user_provider_credentials OWNER TO daon_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("USER_PROVIDER_CREDENTIALS_DOWNGRADE_BLOCKED")
