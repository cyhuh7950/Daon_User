"""Restore the existing global document-worker lease entry point.

The app-owned definer is subject to FORCE RLS without a tenant context. Move
only the bounded claim function to the existing postgres migration role.
The 0011 body, table policies, and app role attributes remain unchanged.
"""

from alembic import op


revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None

_SIGNATURE = "public.claim_document_processing_job(text,integer)"
# Fingerprint of the exact 0011 prosrc, including its whitespace. This guards
# the qualified tables, worker/lease validation, one-row SKIP LOCKED selection,
# and tenant/workspace/job key equality before elevating the definer owner.
_BODY_MD5 = "c1a1d0d9cc56ecdd9483e43b3ab9d6dc"


def _owner_sql(expected_revision: str, old_owner: str, new_owner: str) -> str:
    return f"""
DO $document_claim_owner$
DECLARE
  v_function oid := '{_SIGNATURE}'::regprocedure;
  v_body_before text;
  v_table text;
BEGIN
  IF (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '{expected_revision}' THEN
    RAISE EXCEPTION 'DOCUMENT_CLAIM_REVISION_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'postgres' AND rolsuper
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
    WHERE rolname = 'daon_app' AND NOT rolsuper AND NOT rolbypassrls
  ) THEN
    RAISE EXCEPTION 'DOCUMENT_CLAIM_ROLE_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  FOREACH v_table IN ARRAY ARRAY[
    'document_processing_jobs', 'document_processing_job_attempts'
  ] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class AS c
      JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relname = v_table AND c.relkind = 'r'
        AND c.relrowsecurity AND c.relforcerowsecurity
    ) THEN
      RAISE EXCEPTION 'DOCUMENT_CLAIM_TABLE_PRECONDITION:%', v_table USING ERRCODE = '55000';
    END IF;
  END LOOP;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
    WHERE oid = v_function AND prosecdef
      AND pg_catalog.pg_get_userbyid(proowner) = '{old_owner}'
      AND proconfig @> ARRAY['search_path=pg_catalog, public']
      AND pg_catalog.md5(prosrc) = '{_BODY_MD5}'
      AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc AS p,
      LATERAL pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) AS acl
    WHERE p.oid = v_function AND acl.grantee = 0
  ) THEN
    RAISE EXCEPTION 'DOCUMENT_CLAIM_FUNCTION_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  SELECT prosrc INTO v_body_before FROM pg_catalog.pg_proc WHERE oid = v_function;

  ALTER FUNCTION {_SIGNATURE} OWNER TO {new_owner};
  REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC;
  GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO daon_app;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
    WHERE oid = v_function AND prosecdef AND prosrc = v_body_before
      AND pg_catalog.pg_get_userbyid(proowner) = '{new_owner}'
      AND proconfig @> ARRAY['search_path=pg_catalog, public']
      AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc AS p,
      LATERAL pg_catalog.aclexplode(coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))) AS acl
    WHERE p.oid = v_function AND acl.grantee = 0
  ) THEN
    RAISE EXCEPTION 'DOCUMENT_CLAIM_FUNCTION_POSTCONDITION' USING ERRCODE = '55000';
  END IF;
END
$document_claim_owner$;
"""


def upgrade() -> None:
    op.execute(_owner_sql("0058", "daon_app", "postgres"))


def downgrade() -> None:
    # Restores the old metadata and its FORCE-RLS limitation; no queue rows or
    # attempt history are removed. Prefer forward recovery for active workers.
    op.execute(_owner_sql("0059", "postgres", "daon_app"))
