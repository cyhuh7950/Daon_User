"""Object records, transactional outbox and durable leased jobs."""

from __future__ import annotations

from alembic import op


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


TABLES = ("object_records", "object_outbox_events", "durable_jobs", "job_attempts")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE object_records (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          object_id text NOT NULL,
          area text NOT NULL CHECK (area IN ('source','output')),
          staging_key text NOT NULL,
          object_key text NOT NULL,
          digest_sha256 text NOT NULL CHECK (digest_sha256 ~ '^[0-9a-f]{64}$'),
          byte_size bigint NOT NULL CHECK (byte_size >= 0),
          content_type text NOT NULL CHECK (length(content_type) BETWEEN 1 AND 255),
          status text NOT NULL CHECK (status IN ('pending','completed','failed')),
          storage_etag text,
          storage_version_id text,
          cleanup_pending boolean NOT NULL DEFAULT true,
          created_by text NOT NULL,
          trace_id text NOT NULL,
          idempotency_key text NOT NULL,
          request_fingerprint text NOT NULL,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL,
          completed_at timestamptz,
          PRIMARY KEY (tenant_id, workspace_id, object_id),
          UNIQUE (tenant_id, workspace_id, object_key),
          UNIQUE (tenant_id, workspace_id, created_by, idempotency_key),
          FOREIGN KEY (tenant_id, workspace_id) REFERENCES workspaces(tenant_id, workspace_id)
        );

        CREATE TABLE object_outbox_events (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          event_id text NOT NULL,
          object_id text NOT NULL,
          event_kind text NOT NULL CHECK (event_kind IN ('object.promote')),
          payload_reference jsonb NOT NULL CHECK (jsonb_typeof(payload_reference) = 'object'),
          schema_version integer NOT NULL CHECK (schema_version = 1),
          status text NOT NULL CHECK (status IN ('pending','dispatched','completed','failed')),
          trace_id text NOT NULL,
          created_at timestamptz NOT NULL,
          completed_at timestamptz,
          PRIMARY KEY (tenant_id, workspace_id, event_id),
          FOREIGN KEY (tenant_id, workspace_id, object_id)
            REFERENCES object_records(tenant_id, workspace_id, object_id)
        );

        CREATE TABLE durable_jobs (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          job_id text NOT NULL,
          event_id text NOT NULL,
          job_kind text NOT NULL CHECK (job_kind IN ('object.promote')),
          payload_reference jsonb NOT NULL CHECK (jsonb_typeof(payload_reference) = 'object'),
          payload_schema_version integer NOT NULL CHECK (payload_schema_version = 1),
          deduplication_key text NOT NULL,
          state text NOT NULL CHECK (state IN ('pending','leased','retry_wait','completed','dead_letter')),
          attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          max_attempts integer NOT NULL CHECK (max_attempts BETWEEN 1 AND 100),
          next_attempt_at timestamptz NOT NULL,
          lease_owner text,
          lease_until timestamptz,
          last_safe_error_code text,
          retry_of_job_id text,
          created_by text NOT NULL,
          trace_id text NOT NULL,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL,
          completed_at timestamptz,
          PRIMARY KEY (tenant_id, workspace_id, job_id),
          UNIQUE (tenant_id, workspace_id, job_kind, deduplication_key),
          FOREIGN KEY (tenant_id, workspace_id, event_id)
            REFERENCES object_outbox_events(tenant_id, workspace_id, event_id),
          FOREIGN KEY (tenant_id, workspace_id, retry_of_job_id)
            REFERENCES durable_jobs(tenant_id, workspace_id, job_id),
          CHECK ((state = 'leased') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
        );

        CREATE TABLE job_attempts (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          job_id text NOT NULL,
          attempt_number integer NOT NULL CHECK (attempt_number > 0),
          worker_id text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN ('completed','retry_wait','dead_letter','lease_lost')),
          safe_error_code text,
          trace_id text NOT NULL,
          started_at timestamptz NOT NULL,
          finished_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, job_id, attempt_number),
          FOREIGN KEY (tenant_id, workspace_id, job_id)
            REFERENCES durable_jobs(tenant_id, workspace_id, job_id)
        );

        CREATE INDEX durable_jobs_claim_idx
          ON durable_jobs (tenant_id, workspace_id, state, next_attempt_at, created_at);
        CREATE INDEX durable_jobs_lease_idx
          ON durable_jobs (tenant_id, workspace_id, lease_until)
          WHERE state = 'leased';
        CREATE INDEX object_records_digest_idx
          ON object_records (tenant_id, workspace_id, area, digest_sha256);
        CREATE TRIGGER job_attempts_immutable BEFORE UPDATE OR DELETE ON job_attempts
          FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
    """)

    for table in TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        predicate = (
            "tenant_id = nullif(current_setting('app.tenant_id', true), '') "
            "AND workspace_id = nullif(current_setting('app.workspace_id', true), '')"
        )
        op.execute(
            f"CREATE POLICY {table}_scope ON {table} USING ({predicate}) WITH CHECK ({predicate})"
        )

    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON object_records, object_outbox_events, durable_jobs "
        "TO daon_app"
    )
    op.execute("GRANT SELECT, INSERT ON job_attempts TO daon_app")


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
