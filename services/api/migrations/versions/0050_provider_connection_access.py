"""Add explicit Provider access policy, unique badge, and allowed models."""

from __future__ import annotations

from alembic import op


revision = "0050"
down_revision = "0049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE system_provider_connections
          ADD COLUMN access_mode text,
          ADD COLUMN credential_requirement text,
          ADD COLUMN short_code text,
          ADD COLUMN adapter_type text,
          ADD COLUMN provider_name text;

        UPDATE system_provider_connections
        SET credential_requirement = CASE
          WHEN provider_code='OLLAMA' OR (
            provider_code IN ('EOUL_GATEWAY','MEDIA_BRIDGE')
            AND base_url ~* '^https?://(localhost|127[.]0[.]0[.]1|\\[::1\\])(:|/|$)'
          ) THEN 'none' ELSE 'required' END,
          adapter_type=provider_code,
          provider_name=provider_code;

        UPDATE system_provider_connections
        SET access_mode=CASE
          WHEN credential_requirement='none' OR encrypted_credential IS NOT NULL
          THEN 'public' ELSE 'personal' END;

        UPDATE system_provider_connections
        SET short_code='OR'
        WHERE connection_id=(
          SELECT connection_id FROM system_provider_connections
          WHERE provider_code='OPENROUTER' ORDER BY connection_id LIMIT 1
        );

        DO $$
        DECLARE remaining_count integer;
        BEGIN
          SELECT count(*) INTO remaining_count
          FROM system_provider_connections WHERE short_code IS NULL;
          IF remaining_count > 675 THEN
            RAISE EXCEPTION 'PROVIDER_SHORT_CODE_CAPACITY_EXCEEDED' USING ERRCODE='55000';
          END IF;
        END $$;

        WITH numbered AS (
          SELECT connection_id, row_number() OVER (ORDER BY connection_id)-1 AS position
          FROM system_provider_connections WHERE short_code IS NULL
        ), allocated AS (
          SELECT connection_id, position + CASE WHEN position >= 381 THEN 1 ELSE 0 END AS code_number
          FROM numbered
        )
        UPDATE system_provider_connections AS target
        SET short_code = chr(65 + (allocated.code_number / 26)::integer)
          || chr(65 + (allocated.code_number % 26)::integer)
        FROM allocated WHERE target.connection_id=allocated.connection_id;

        ALTER TABLE system_provider_connections
          ALTER COLUMN access_mode SET NOT NULL,
          ALTER COLUMN credential_requirement SET NOT NULL,
          ALTER COLUMN short_code SET NOT NULL,
          ALTER COLUMN adapter_type SET NOT NULL,
          ALTER COLUMN provider_name SET NOT NULL;
        ALTER TABLE system_provider_connections
          ADD CONSTRAINT system_provider_connections_access_mode_check
            CHECK (access_mode IN ('public','personal')),
          ADD CONSTRAINT system_provider_connections_credential_requirement_check
            CHECK (credential_requirement IN ('required','none')),
          ADD CONSTRAINT system_provider_connections_keyless_public_check
            CHECK (credential_requirement <> 'none' OR access_mode='public'),
          ADD CONSTRAINT system_provider_connections_short_code_check
            CHECK (short_code ~ '^[A-Z]{2}$'),
          ADD CONSTRAINT system_provider_connections_adapter_type_check
            CHECK (adapter_type ~ '^[A-Za-z][A-Za-z0-9_]{0,63}$'),
          ADD CONSTRAINT system_provider_connections_provider_name_check
            CHECK (length(btrim(provider_name)) BETWEEN 1 AND 256);
        CREATE UNIQUE INDEX system_provider_connections_short_code_uq
          ON system_provider_connections(short_code);

        ALTER TABLE system_provider_connections
          DROP CONSTRAINT system_provider_connections_provider_code_check;
        ALTER TABLE system_provider_connections
          ADD CONSTRAINT system_provider_connections_provider_code_check
            CHECK (provider_code IN (
              'CEREBRAS','GROQ','MISTRAL','OPENAI','UPSTAGE','GEMINI','OPENROUTER',
              'ANTHROPIC','OLLAMA','OMNIROUTE','EOUL_GATEWAY','MEDIA_BRIDGE',
              'SENTENCE_TRANSFORMERS','CUSTOM'
            ));

        CREATE TABLE system_provider_allowed_models (
          connection_id text NOT NULL,
          model_id text NOT NULL,
          allowed_at timestamptz NOT NULL DEFAULT now(),
          updated_by text NOT NULL,
          PRIMARY KEY (connection_id, model_id),
          FOREIGN KEY (connection_id, model_id)
            REFERENCES system_provider_models(connection_id, model_id) ON DELETE CASCADE
        );
        INSERT INTO system_provider_allowed_models (connection_id,model_id,updated_by)
        SELECT connection_id,model_id,updated_by FROM system_provider_models;
        ALTER TABLE system_provider_allowed_models OWNER TO daon_app;
        GRANT SELECT, INSERT, DELETE ON system_provider_allowed_models TO daon_app;

        ALTER TABLE user_provider_credentials
          ADD COLUMN verification_status text NOT NULL DEFAULT 'unverified',
          ADD COLUMN verified_at timestamptz;
        ALTER TABLE user_provider_credentials
          ADD CONSTRAINT user_provider_credentials_verification_status_check
            CHECK (verification_status IN ('verified','unverified'));
        """
    )


def downgrade() -> None:
    raise RuntimeError("PROVIDER_CONNECTION_ACCESS_DOWNGRADE_BLOCKED")
