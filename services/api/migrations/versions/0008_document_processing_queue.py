"""Document parser role and durable cross-workspace processing queue."""

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


TABLES = ("document_processing_jobs", "document_processing_job_attempts")


def upgrade() -> None:
    op.execute("""
        ALTER TABLE provider_setting_deployments
          DROP CONSTRAINT provider_setting_deployments_roles_check,
          ADD CONSTRAINT provider_setting_deployments_roles_check CHECK (
            cardinality(roles) > 0 AND roles <@ ARRAY[
              'text','vision','document_parser','audio_understanding',
              'speech_to_text','embedding','reranker'
            ]::text[]
          );
        ALTER TABLE provider_setting_role_bindings
          DROP CONSTRAINT provider_setting_role_bindings_role_check,
          ADD CONSTRAINT provider_setting_role_bindings_role_check CHECK (role IN (
            'text','vision','document_parser','audio_understanding',
            'speech_to_text','embedding','reranker'
          ));

        CREATE TABLE document_processing_jobs (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          job_id text NOT NULL,
          source_id text NOT NULL,
          source_version_id text NOT NULL,
          processing_run_id text NOT NULL,
          state text NOT NULL CHECK (state IN (
            'pending','leased','retry_wait','completed','dead_letter'
          )),
          attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
          max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
          next_attempt_at timestamptz NOT NULL DEFAULT now(),
          lease_owner text,
          lease_until timestamptz,
          last_safe_error_code text,
          created_by text NOT NULL,
          trace_id text NOT NULL,
          policy_version text NOT NULL,
          version integer NOT NULL DEFAULT 1 CHECK (version > 0),
          created_at timestamptz NOT NULL DEFAULT now(),
          completed_at timestamptz,
          PRIMARY KEY (tenant_id, workspace_id, job_id),
          UNIQUE (tenant_id, workspace_id, processing_run_id),
          FOREIGN KEY (tenant_id, workspace_id, source_id)
            REFERENCES sources(tenant_id, workspace_id, record_id),
          FOREIGN KEY (tenant_id, workspace_id, source_version_id)
            REFERENCES source_versions(tenant_id, workspace_id, record_id),
          FOREIGN KEY (tenant_id, workspace_id, processing_run_id)
            REFERENCES processing_runs(tenant_id, workspace_id, record_id),
          CHECK ((state = 'leased') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
        );
        CREATE INDEX document_processing_jobs_claim_idx
          ON document_processing_jobs (state, next_attempt_at, created_at, job_id)
          WHERE state IN ('pending','retry_wait');

        CREATE TABLE document_processing_job_attempts (
          tenant_id text NOT NULL,
          workspace_id text NOT NULL,
          job_id text NOT NULL,
          attempt_number integer NOT NULL CHECK (attempt_number > 0),
          worker_id text NOT NULL,
          outcome text NOT NULL CHECK (outcome IN (
            'completed','retry_wait','dead_letter','lease_lost'
          )),
          safe_error_code text,
          trace_id text NOT NULL,
          started_at timestamptz NOT NULL,
          finished_at timestamptz NOT NULL,
          PRIMARY KEY (tenant_id, workspace_id, job_id, attempt_number),
          FOREIGN KEY (tenant_id, workspace_id, job_id)
            REFERENCES document_processing_jobs(tenant_id, workspace_id, job_id)
        );
        CREATE TRIGGER document_processing_job_attempts_immutable
          BEFORE UPDATE OR DELETE ON document_processing_job_attempts
          FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
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

    op.execute("""
        CREATE FUNCTION claim_document_processing_job(
          requested_worker_id text, requested_lease_seconds integer
        ) RETURNS TABLE (
          tenant_id text, workspace_id text, job_id text, source_id text,
          source_version_id text, processing_run_id text, state text,
          attempt integer, max_attempts integer, lease_owner text,
          lease_until timestamptz, trace_id text, policy_version text,
          created_by text, created_at timestamptz, version integer
        ) LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        BEGIN
          IF requested_worker_id IS NULL
             OR requested_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'
             OR requested_lease_seconds NOT BETWEEN 10 AND 600 THEN
            RAISE EXCEPTION 'DOCUMENT_WORKER_CLAIM_INVALID' USING ERRCODE = '22023';
          END IF;
          RETURN QUERY
          WITH candidate AS (
            SELECT job.tenant_id,job.workspace_id,job.job_id
            FROM public.document_processing_jobs AS job
            WHERE job.state IN ('pending','retry_wait')
              AND job.next_attempt_at <= clock_timestamp()
            ORDER BY job.next_attempt_at,job.created_at,job.job_id
            FOR UPDATE SKIP LOCKED LIMIT 1
          )
          UPDATE public.document_processing_jobs AS job
          SET state='leased',attempt=job.attempt+1,
              lease_owner=requested_worker_id,
              lease_until=clock_timestamp()+make_interval(secs => requested_lease_seconds),
              last_safe_error_code=NULL,version=job.version+1
          FROM candidate
          WHERE job.tenant_id=candidate.tenant_id
            AND job.workspace_id=candidate.workspace_id
            AND job.job_id=candidate.job_id
          RETURNING job.tenant_id,job.workspace_id,job.job_id,job.source_id,
            job.source_version_id,job.processing_run_id,job.state,job.attempt,
            job.max_attempts,job.lease_owner,job.lease_until,job.trace_id,
            job.policy_version,job.created_by,job.created_at,job.version;
        END $$;
        REVOKE ALL ON FUNCTION claim_document_processing_job(text,integer) FROM PUBLIC;
        GRANT EXECUTE ON FUNCTION claim_document_processing_job(text,integer) TO daon_app;
        GRANT SELECT,INSERT,UPDATE,DELETE ON document_processing_jobs TO daon_app;
        GRANT SELECT,INSERT ON document_processing_job_attempts TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS claim_document_processing_job(text,integer)")
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    op.execute("""
        ALTER TABLE provider_setting_deployments
          DROP CONSTRAINT provider_setting_deployments_roles_check,
          ADD CONSTRAINT provider_setting_deployments_roles_check CHECK (
            cardinality(roles) > 0 AND roles <@ ARRAY[
              'text','vision','audio_understanding','speech_to_text','embedding','reranker'
            ]::text[]
          );
        ALTER TABLE provider_setting_role_bindings
          DROP CONSTRAINT provider_setting_role_bindings_role_check,
          ADD CONSTRAINT provider_setting_role_bindings_role_check CHECK (role IN (
            'text','vision','audio_understanding','speech_to_text','embedding','reranker'
          ));
    """)
