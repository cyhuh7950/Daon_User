"""Complete the Studio generation job state and retry contract."""

from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE studio_generation_jobs
        DROP CONSTRAINT IF EXISTS studio_generation_jobs_state_check;
      ALTER TABLE studio_generation_jobs
        ADD CONSTRAINT studio_generation_jobs_state_check
        CHECK (state IN ('queued','leased','generating','completed','failed','unavailable'));
      ALTER TABLE studio_generation_jobs
        ADD COLUMN IF NOT EXISTS max_attempts integer NOT NULL DEFAULT 3
          CHECK (max_attempts BETWEEN 1 AND 10),
        ADD COLUMN IF NOT EXISTS next_attempt_at timestamptz NOT NULL DEFAULT now();
      DROP INDEX IF EXISTS studio_generation_jobs_claim_idx;
      CREATE INDEX studio_generation_jobs_claim_idx
        ON studio_generation_jobs (state, next_attempt_at, created_at, job_id)
        WHERE state IN ('queued','leased');
      CREATE OR REPLACE FUNCTION claim_studio_generation_job(p_worker_id text, p_lease_seconds integer)
      RETURNS TABLE(tenant_id text, workspace_id text, job_id text, actor_id text, trace_id text,
                    policy_version text, idempotency_key text, request_json jsonb, attempt integer, version integer)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
      BEGIN
        IF p_worker_id IS NULL OR p_worker_id !~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'
           OR p_lease_seconds < 10 OR p_lease_seconds > 3600 THEN
          RAISE EXCEPTION 'STUDIO_JOB_CLAIM_INVALID' USING ERRCODE='22023';
        END IF;
        RETURN QUERY
        WITH candidate AS (
          SELECT j.tenant_id, j.workspace_id, j.job_id
          FROM studio_generation_jobs j
          WHERE (j.state='queued' AND j.next_attempt_at <= now())
             OR (j.state IN ('leased','generating') AND j.lease_until < now())
          ORDER BY j.next_attempt_at, j.created_at, j.job_id
          FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE studio_generation_jobs j
           SET state='leased', lease_owner=p_worker_id,
               lease_until=now() + make_interval(secs => p_lease_seconds),
               attempt=j.attempt+1, version=j.version+1
          FROM candidate c
         WHERE j.tenant_id=c.tenant_id AND j.workspace_id=c.workspace_id AND j.job_id=c.job_id
           AND j.attempt < j.max_attempts
        RETURNING j.tenant_id,j.workspace_id,j.job_id,j.actor_id,j.trace_id,j.policy_version,
                  j.idempotency_key,j.request_json,j.attempt,j.version;
      END $$;
    """)


def downgrade() -> None:
    op.execute("""
      DROP FUNCTION IF EXISTS claim_studio_generation_job(text,integer);
      ALTER TABLE studio_generation_jobs DROP COLUMN IF EXISTS next_attempt_at;
      ALTER TABLE studio_generation_jobs DROP COLUMN IF EXISTS max_attempts;
      ALTER TABLE studio_generation_jobs DROP CONSTRAINT IF EXISTS studio_generation_jobs_state_check;
      ALTER TABLE studio_generation_jobs ADD CONSTRAINT studio_generation_jobs_state_check
        CHECK (state IN ('queued','leased','completed','failed','unavailable'));
      CREATE OR REPLACE FUNCTION claim_studio_generation_job(p_worker_id text, p_lease_seconds integer)
      RETURNS TABLE(tenant_id text, workspace_id text, job_id text, actor_id text, trace_id text,
                    policy_version text, idempotency_key text, request_json jsonb, attempt integer, version integer)
      LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$
      BEGIN
        RETURN QUERY
        WITH candidate AS (
          SELECT j.tenant_id, j.workspace_id, j.job_id FROM studio_generation_jobs j
          WHERE j.state='queued' OR (j.state='leased' AND j.lease_until < now())
          ORDER BY j.created_at,j.job_id FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE studio_generation_jobs j SET state='leased',lease_owner=p_worker_id,
          lease_until=now()+make_interval(secs=>p_lease_seconds),attempt=j.attempt+1,version=j.version+1
        FROM candidate c WHERE j.tenant_id=c.tenant_id AND j.workspace_id=c.workspace_id AND j.job_id=c.job_id
        RETURNING j.tenant_id,j.workspace_id,j.job_id,j.actor_id,j.trace_id,j.policy_version,j.idempotency_key,j.request_json,j.attempt,j.version;
      END $$;
    """)
