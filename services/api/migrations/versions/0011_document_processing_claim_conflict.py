"""Use the named attempt key when ignoring duplicate lease-loss history."""

from alembic import op


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _claim_function() -> str:
    return """
        CREATE OR REPLACE FUNCTION claim_document_processing_job(
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
          WITH candidate AS MATERIALIZED (
            SELECT job.tenant_id,job.workspace_id,job.job_id,job.state,job.attempt,
              job.lease_owner,job.trace_id,job.created_at
            FROM public.document_processing_jobs AS job
            WHERE (job.state IN ('pending','retry_wait')
                   AND job.next_attempt_at <= clock_timestamp())
               OR (job.state = 'leased' AND job.lease_until <= clock_timestamp())
            ORDER BY job.next_attempt_at,job.created_at,job.job_id
            FOR UPDATE SKIP LOCKED LIMIT 1
          ), lease_loss AS (
            INSERT INTO public.document_processing_job_attempts
              (tenant_id,workspace_id,job_id,attempt_number,worker_id,outcome,
               safe_error_code,trace_id,started_at,finished_at)
            SELECT candidate.tenant_id,candidate.workspace_id,candidate.job_id,
              candidate.attempt,candidate.lease_owner,'lease_lost',
              'DOCUMENT_WORKER_LEASE_EXPIRED',candidate.trace_id,
              candidate.created_at,clock_timestamp()
            FROM candidate AS candidate WHERE candidate.state='leased'
            ON CONFLICT ON CONSTRAINT document_processing_job_attempts_pkey DO NOTHING
            RETURNING 1
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
            AND (SELECT count(*) FROM lease_loss) >= 0
          RETURNING job.tenant_id,job.workspace_id,job.job_id,job.source_id,
            job.source_version_id,job.processing_run_id,job.state,job.attempt,
            job.max_attempts,job.lease_owner,job.lease_until,job.trace_id,
            job.policy_version,job.created_by,job.created_at,job.version;
        END $$;
    """


def upgrade() -> None:
    op.execute(_claim_function())


def downgrade() -> None:
    # Keep the non-ambiguous conflict target during a revision rollback.
    op.execute(_claim_function())
