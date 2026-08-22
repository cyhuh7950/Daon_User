"""Durable asynchronous Studio generation jobs."""

from alembic import op


revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE studio_generation_jobs (
        tenant_id text NOT NULL,
        workspace_id text NOT NULL,
        job_id text NOT NULL CHECK (job_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'),
        actor_id text NOT NULL,
        trace_id text NOT NULL,
        policy_version text NOT NULL,
        idempotency_key text NOT NULL,
        request_json jsonb NOT NULL CHECK (jsonb_typeof(request_json) = 'object'),
        state text NOT NULL CHECK (state IN ('queued','leased','completed','failed','unavailable')),
        attempt integer NOT NULL DEFAULT 0 CHECK (attempt >= 0),
        lease_owner text,
        lease_until timestamptz,
        safe_error_code text,
        studio_output_id text,
        output_version_id text,
        created_at timestamptz NOT NULL DEFAULT now(),
        completed_at timestamptz,
        version integer NOT NULL DEFAULT 1 CHECK (version > 0),
        PRIMARY KEY (tenant_id, workspace_id, job_id),
        UNIQUE (tenant_id, workspace_id, actor_id, idempotency_key),
        CHECK ((state = 'leased') = (lease_owner IS NOT NULL AND lease_until IS NOT NULL))
      );
      CREATE INDEX studio_generation_jobs_claim_idx
        ON studio_generation_jobs (state, created_at, job_id)
        WHERE state IN ('queued','leased');
      GRANT SELECT,INSERT,UPDATE ON studio_generation_jobs TO daon_app;
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
          WHERE (j.state='queued' OR (j.state='leased' AND j.lease_until < now()))
          ORDER BY j.created_at, j.job_id
          FOR UPDATE SKIP LOCKED LIMIT 1
        )
        UPDATE studio_generation_jobs j
           SET state='leased', lease_owner=p_worker_id,
               lease_until=now() + make_interval(secs => p_lease_seconds),
               attempt=j.attempt+1, version=j.version+1
          FROM candidate c
         WHERE j.tenant_id=c.tenant_id AND j.workspace_id=c.workspace_id AND j.job_id=c.job_id
        RETURNING j.tenant_id,j.workspace_id,j.job_id,j.actor_id,j.trace_id,j.policy_version,
                  j.idempotency_key,j.request_json,j.attempt,j.version;
      END $$;
      REVOKE ALL ON FUNCTION claim_studio_generation_job(text,integer) FROM PUBLIC;
      GRANT EXECUTE ON FUNCTION claim_studio_generation_job(text,integer) TO daon_app;
    """)


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS claim_studio_generation_job(text,integer)")
    op.execute("DROP TABLE IF EXISTS studio_generation_jobs")
