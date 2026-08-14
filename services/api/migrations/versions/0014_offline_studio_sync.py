"""Offline Knowledge copy grants and versioned Studio sync items."""

from __future__ import annotations

from alembic import op


revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


_SYNC_ITEM_TABLES = ("sync_preview_items", "sync_manifest_items")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE offline_knowledge_copy_grants (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          copy_id text NOT NULL,
          package_id text NOT NULL,
          device_id text NOT NULL,
          actor_id text NOT NULL,
          knowledge_registration_id text NOT NULL,
          output_version_id text NOT NULL,
          producer text NOT NULL CHECK (producer IN ('daon2','daon2_5','daon3')),
          producer_version text NOT NULL,
          package_digest text NOT NULL CHECK (package_digest ~ '^[0-9a-f]{64}$'),
          byte_size bigint NOT NULL CHECK (byte_size > 0 AND byte_size <= 8388608),
          content_type text NOT NULL,
          step_up_authorization_digest text NOT NULL CHECK (
            step_up_authorization_digest ~ '^[0-9a-f]{64}$'
          ),
          state text NOT NULL CHECK (state IN ('approved','revoked','expired')),
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
          approved_at timestamptz NOT NULL,
          expires_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id,workspace_id,copy_id),
          UNIQUE (tenant_id,workspace_id,device_id,package_id,package_digest),
          UNIQUE (tenant_id,workspace_id,actor_id,idempotency_key),
          FOREIGN KEY (tenant_id,workspace_id)
            REFERENCES workspaces(tenant_id,workspace_id),
          FOREIGN KEY (tenant_id,workspace_id,knowledge_registration_id)
            REFERENCES knowledge_registrations(tenant_id,workspace_id,record_id),
          FOREIGN KEY (tenant_id,workspace_id,output_version_id)
            REFERENCES output_versions(tenant_id,workspace_id,record_id),
          CHECK (expires_at > approved_at)
        );

        CREATE OR REPLACE FUNCTION validate_sync_dependency_item_ids()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE normalized text[];
        BEGIN
          SELECT coalesce(array_agg(value ORDER BY value), '{}')
            INTO normalized
            FROM (SELECT DISTINCT unnest(NEW.dependency_item_ids) AS value) valueset
            WHERE value ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$';
          IF NEW.item_kind = 'source_version' AND cardinality(NEW.dependency_item_ids) <> 0 THEN
            RAISE EXCEPTION 'SYNC_ITEM_INVALID' USING ERRCODE = '23514';
          END IF;
          IF NEW.item_kind = 'output_version' AND (
            cardinality(NEW.dependency_item_ids) = 0 OR normalized <> NEW.dependency_item_ids
          ) THEN
            RAISE EXCEPTION 'SYNC_ITEM_INVALID' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END;
        $$;
    """)
    for table in _SYNC_ITEM_TABLES:
        op.execute(f"""
            ALTER TABLE {table}
              ADD COLUMN item_kind text NOT NULL DEFAULT 'source_version',
              ALTER COLUMN source_version_id DROP NOT NULL,
              ADD COLUMN output_version_id text,
              ADD COLUMN dependency_item_ids text[] NOT NULL DEFAULT '{{}}',
              ADD CONSTRAINT {table}_item_kind_check
                CHECK (item_kind IN ('source_version','output_version')),
              ADD CONSTRAINT {table}_version_exact_one_check CHECK (
                (item_kind='source_version' AND source_version_id IS NOT NULL
                  AND output_version_id IS NULL)
                OR
                (item_kind='output_version' AND source_version_id IS NULL
                  AND output_version_id IS NOT NULL)
              );
            CREATE TRIGGER {table}_dependency_guard
              BEFORE INSERT ON {table}
              FOR EACH ROW EXECUTE FUNCTION validate_sync_dependency_item_ids();
        """)
    op.execute("""
        ALTER TABLE sync_target_versions
          ADD COLUMN item_kind text NOT NULL DEFAULT 'source_version',
          ADD COLUMN target_output_version_id text,
          ADD CONSTRAINT sync_target_versions_item_kind_check
            CHECK (item_kind IN ('source_version','output_version')),
          ADD CONSTRAINT sync_target_versions_output_target_check CHECK (
            (item_kind='source_version' AND target_output_version_id IS NULL)
            OR
            (item_kind='output_version' AND target_output_version_id=target_version_id)
          ),
          ADD CONSTRAINT sync_target_versions_output_fk
            FOREIGN KEY (tenant_id,workspace_id,target_output_version_id)
            REFERENCES output_versions(tenant_id,workspace_id,record_id);

        CREATE OR REPLACE FUNCTION reject_offline_knowledge_copy_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'OFFLINE_KNOWLEDGE_COPY_IMMUTABLE' USING ERRCODE = '55000';
        END;
        $$;
        CREATE TRIGGER offline_knowledge_copy_grants_immutable
          BEFORE UPDATE OR DELETE ON offline_knowledge_copy_grants
          FOR EACH ROW EXECUTE FUNCTION reject_offline_knowledge_copy_mutation();

        ALTER TABLE offline_knowledge_copy_grants ENABLE ROW LEVEL SECURITY;
        ALTER TABLE offline_knowledge_copy_grants FORCE ROW LEVEL SECURITY;
        CREATE POLICY offline_knowledge_copy_grants_scope
          ON offline_knowledge_copy_grants
          USING (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')
            AND workspace_id = nullif(current_setting('app.workspace_id', true), '')
          )
          WITH CHECK (
            tenant_id = nullif(current_setting('app.tenant_id', true), '')
            AND workspace_id = nullif(current_setting('app.workspace_id', true), '')
          );
        GRANT SELECT, INSERT ON offline_knowledge_copy_grants TO daon_app;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM offline_knowledge_copy_grants LIMIT 1)
             OR EXISTS (SELECT 1 FROM sync_preview_items WHERE item_kind='output_version' LIMIT 1)
             OR EXISTS (SELECT 1 FROM sync_manifest_items WHERE item_kind='output_version' LIMIT 1)
             OR EXISTS (SELECT 1 FROM sync_target_versions WHERE item_kind='output_version' LIMIT 1)
          THEN
            RAISE EXCEPTION 'OFFLINE_STUDIO_DOWNGRADE_BLOCKED' USING ERRCODE = '55000';
          END IF;
        END;
        $$;
        DROP TRIGGER IF EXISTS offline_knowledge_copy_grants_immutable
          ON offline_knowledge_copy_grants;
        DROP TABLE offline_knowledge_copy_grants;
        DROP FUNCTION IF EXISTS reject_offline_knowledge_copy_mutation();
        ALTER TABLE sync_target_versions
          DROP CONSTRAINT sync_target_versions_output_fk,
          DROP CONSTRAINT sync_target_versions_output_target_check,
          DROP CONSTRAINT sync_target_versions_item_kind_check,
          DROP COLUMN target_output_version_id,
          DROP COLUMN item_kind;
    """)
    for table in reversed(_SYNC_ITEM_TABLES):
        op.execute(f"""
            DROP TRIGGER IF EXISTS {table}_dependency_guard ON {table};
            ALTER TABLE {table}
              DROP CONSTRAINT {table}_version_exact_one_check,
              DROP CONSTRAINT {table}_item_kind_check,
              DROP COLUMN dependency_item_ids,
              DROP COLUMN output_version_id,
              DROP COLUMN item_kind,
              ALTER COLUMN source_version_id SET NOT NULL;
        """)
    op.execute("DROP FUNCTION IF EXISTS validate_sync_dependency_item_ids()")
