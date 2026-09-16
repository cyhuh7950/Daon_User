"""Add system provider connections, model catalog, and workspace defaults."""

from __future__ import annotations

from alembic import op


revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        DECLARE unsupported_role text;
        BEGIN
          SELECT role INTO unsupported_role
          FROM provider_setting_role_bindings
          WHERE role NOT IN ('text','vision','document_parser')
          ORDER BY role
          LIMIT 1;
          IF unsupported_role IS NOT NULL THEN
            RAISE EXCEPTION 'PROVIDER_DEFAULT_MIGRATION_UNSUPPORTED_ROLE:%', unsupported_role
              USING ERRCODE = '55000';
          END IF;
        END $$;

        CREATE TABLE system_provider_connections (
          connection_id text PRIMARY KEY,
          provider_code text NOT NULL CHECK (provider_code IN (
            'CEREBRAS','GROQ','MISTRAL','OPENAI','UPSTAGE','GEMINI',
            'OPENROUTER','ANTHROPIC','OLLAMA','OMNIROUTE','EOUL_GATEWAY'
          )),
          display_name text NOT NULL CHECK (length(display_name) BETWEEN 1 AND 256),
          base_url text NOT NULL CHECK (length(base_url) BETWEEN 1 AND 2048),
          encrypted_credential bytea,
          credential_nonce bytea,
          encryption_key_version integer,
          credential_schema_version integer,
          credential_version integer NOT NULL DEFAULT 0 CHECK (credential_version >= 0),
          enabled boolean NOT NULL DEFAULT false,
          verification_status text NOT NULL DEFAULT 'unverified' CHECK (
            verification_status IN ('verified','failed','unverified')
          ),
          verified_at timestamptz,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_by text NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          updated_at timestamptz NOT NULL DEFAULT now(),
          CHECK (
            (
              encrypted_credential IS NULL
              AND credential_nonce IS NULL
              AND encryption_key_version IS NULL
              AND credential_schema_version IS NULL
              AND credential_version = 0
            ) OR (
              encrypted_credential IS NOT NULL
              AND octet_length(encrypted_credential) >= 16
              AND credential_nonce IS NOT NULL
              AND octet_length(credential_nonce) = 12
              AND encryption_key_version > 0
              AND credential_schema_version = 1
              AND credential_version > 0
            )
          )
        );

        CREATE TABLE system_provider_models (
          connection_id text NOT NULL REFERENCES system_provider_connections(connection_id)
            ON DELETE CASCADE,
          model_id text NOT NULL CHECK (length(model_id) BETWEEN 1 AND 256),
          reported_capabilities text[] NOT NULL DEFAULT '{}'::text[],
          effective_capabilities text[] NOT NULL DEFAULT '{}'::text[],
          override_applied boolean NOT NULL DEFAULT false,
          catalog_status text NOT NULL DEFAULT 'stale' CHECK (
            catalog_status IN ('ready','stale','unavailable')
          ),
          catalog_version integer NOT NULL DEFAULT 1 CHECK (catalog_version > 0),
          discovered_at timestamptz,
          updated_at timestamptz NOT NULL DEFAULT now(),
          updated_by text NOT NULL,
          CHECK (reported_capabilities <@ ARRAY[
            'text_generation','image_understanding','document_parsing',
            'audio_understanding','speech_to_text','video_understanding',
            'embedding','reranking','image_generation','text_to_speech',
            'audio_generation','video_generation'
          ]::text[]),
          CHECK (effective_capabilities <@ ARRAY[
            'text_generation','image_understanding','document_parsing',
            'audio_understanding','speech_to_text','video_understanding',
            'embedding','reranking','image_generation','text_to_speech',
            'audio_generation','video_generation'
          ]::text[]),
          PRIMARY KEY (connection_id, model_id)
        );

        CREATE TABLE workspace_model_defaults (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          capability text NOT NULL CHECK (capability IN (
            'text_generation','image_understanding','document_parsing',
            'audio_understanding','speech_to_text','video_understanding',
            'embedding','reranking','image_generation','text_to_speech',
            'audio_generation','video_generation'
          )),
          connection_id text NOT NULL,
          model_id text NOT NULL,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          updated_by text NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          updated_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, workspace_id, capability),
          FOREIGN KEY (tenant_id, workspace_id)
            REFERENCES workspaces(tenant_id, workspace_id),
          FOREIGN KEY (connection_id, model_id)
            REFERENCES system_provider_models(connection_id, model_id)
        );

        CREATE TABLE system_provider_admin_idempotency (
          tenant_id text NOT NULL,
          actor_id text NOT NULL,
          operation text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          result jsonb NOT NULL,
          created_at timestamptz NOT NULL DEFAULT now(),
          PRIMARY KEY (tenant_id, actor_id, operation, idempotency_key)
        );

        CREATE FUNCTION reject_system_provider_idempotency_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'SYSTEM_PROVIDER_IDEMPOTENCY_IMMUTABLE' USING ERRCODE = '55000';
        END $$;

        CREATE TRIGGER system_provider_admin_idempotency_immutable
          BEFORE UPDATE OR DELETE ON system_provider_admin_idempotency
          FOR EACH ROW EXECUTE FUNCTION reject_system_provider_idempotency_mutation();

        CREATE TABLE system_provider_audit_outbox (
          event_id text PRIMARY KEY,
          tenant_id text NOT NULL,
          actor_id text NOT NULL,
          action text NOT NULL,
          target_type text NOT NULL,
          target_id text NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          audit_payload jsonb NOT NULL,
          occurred_at timestamptz NOT NULL DEFAULT now(),
          published_at timestamptz
        );

        INSERT INTO system_provider_connections (
          connection_id, provider_code, display_name, base_url,
          encrypted_credential, credential_nonce, encryption_key_version,
          credential_schema_version, credential_version, enabled,
          verification_status, version,
          updated_by, trace_id, policy_version, created_at, updated_at
        )
        SELECT
          'legacy-' || substr(md5(
            profile.tenant_id || E'\\x1f' || profile.workspace_id || E'\\x1f' || profile.profile_id
          ), 1, 32),
          profile.provider_code,
          profile.provider_code || ' · migrated · ' || substr(md5(
            profile.tenant_id || E'\\x1f' || profile.workspace_id || E'\\x1f' || profile.profile_id
          ), 1, 12),
          profile.base_url,
          NULL, NULL, NULL, NULL, 0,
          profile.active,
          'unverified',
          profile.version,
          profile.updated_by,
          profile.trace_id,
          profile.policy_version,
          profile.created_at,
          profile.updated_at
        FROM provider_setting_profiles AS profile;

        WITH normalized_models AS (
          SELECT
            'legacy-' || substr(md5(
              deployment.tenant_id || E'\\x1f' || deployment.workspace_id || E'\\x1f' || deployment.profile_id
            ), 1, 32) AS connection_id,
            deployment.model_id,
            CASE legacy_role
              WHEN 'text' THEN 'text_generation'
              WHEN 'vision' THEN 'image_understanding'
              WHEN 'document_parser' THEN 'document_parsing'
              WHEN 'audio_understanding' THEN 'audio_understanding'
              WHEN 'speech_to_text' THEN 'speech_to_text'
              WHEN 'embedding' THEN 'embedding'
              WHEN 'reranker' THEN 'reranking'
            END AS capability,
            deployment.version
          FROM provider_setting_deployments AS deployment
          CROSS JOIN LATERAL unnest(deployment.roles) AS role_values(legacy_role)
        )
        INSERT INTO system_provider_models (
          connection_id, model_id, reported_capabilities, effective_capabilities,
          override_applied, catalog_status, catalog_version, updated_by
        )
        SELECT
          connection_id,
          model_id,
          array_agg(DISTINCT capability ORDER BY capability),
          array_agg(DISTINCT capability ORDER BY capability),
          false,
          'stale',
          max(version),
          'migration:0040'
        FROM normalized_models
        GROUP BY connection_id, model_id;

        INSERT INTO workspace_model_defaults (
          tenant_id, workspace_id, capability, connection_id, model_id,
          version, updated_by, trace_id, policy_version, updated_at
        )
        SELECT
          binding.tenant_id,
          binding.workspace_id,
          CASE binding.role
            WHEN 'text' THEN 'text_generation'
            WHEN 'vision' THEN 'image_understanding'
            WHEN 'document_parser' THEN 'document_parsing'
          END,
          'legacy-' || substr(md5(
            deployment.tenant_id || E'\\x1f' || deployment.workspace_id || E'\\x1f' || deployment.profile_id
          ), 1, 32),
          deployment.model_id,
          binding.version,
          binding.updated_by,
          binding.trace_id,
          binding.policy_version,
          binding.updated_at
        FROM provider_setting_role_bindings AS binding
        JOIN provider_setting_deployments AS deployment
          ON deployment.tenant_id = binding.tenant_id
         AND deployment.workspace_id = binding.workspace_id
         AND deployment.deployment_id = binding.deployment_id
        JOIN provider_setting_profiles AS profile
          ON profile.tenant_id = deployment.tenant_id
         AND profile.workspace_id = deployment.workspace_id
         AND profile.profile_id = deployment.profile_id;

        DO $$
        BEGIN
          IF (SELECT count(*) FROM workspace_model_defaults)
             <> (SELECT count(*) FROM provider_setting_role_bindings) THEN
            RAISE EXCEPTION 'PROVIDER_DEFAULT_MIGRATION_COUNT_MISMATCH'
              USING ERRCODE = '55000';
          END IF;
        END $$;

        ALTER TABLE workspace_model_defaults ENABLE ROW LEVEL SECURITY;
        ALTER TABLE workspace_model_defaults FORCE ROW LEVEL SECURITY;
        CREATE POLICY workspace_model_defaults_scope ON workspace_model_defaults
          USING (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')
            AND workspace_id = nullif(current_setting('app.workspace_id', true), '')
          )
          WITH CHECK (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')
            AND workspace_id = nullif(current_setting('app.workspace_id', true), '')
          );

        GRANT SELECT, INSERT, UPDATE, DELETE ON system_provider_connections TO daon_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON system_provider_models TO daon_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON workspace_model_defaults TO daon_app;
        GRANT SELECT, INSERT ON system_provider_admin_idempotency TO daon_app;
        GRANT SELECT, INSERT, UPDATE (published_at) ON system_provider_audit_outbox TO daon_app;
        ALTER TABLE system_provider_connections OWNER TO daon_app;
        ALTER TABLE system_provider_models OWNER TO daon_app;
        ALTER TABLE workspace_model_defaults OWNER TO daon_app;
        ALTER TABLE system_provider_admin_idempotency OWNER TO daon_app;
        ALTER TABLE system_provider_audit_outbox OWNER TO daon_app;
        ALTER FUNCTION reject_system_provider_idempotency_mutation() OWNER TO daon_app;
        """
    )


def downgrade() -> None:
    raise RuntimeError("PROVIDER_CONNECTIONS_DOWNGRADE_BLOCKED")
