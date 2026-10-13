"""Restore canonical state transitions after the 0056 table owner split.

The existing SECURITY DEFINER function locks and updates canonical rows. 0056
made its owner (daon_app) unable to lock rows in the affected tables. Transfer
only this function to the existing non-login owner of those tables; keep the
function body, RLS policies and caller grants intact.
"""

from alembic import op


revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


_SIGNATURE = "public.transition_canon_state(text,text,integer,text,text,text,text,text)"


def upgrade() -> None:
    op.execute(f"""
DO $canon_owner$
DECLARE
  v_function oid := '{_SIGNATURE}'::regprocedure;
  v_table text;
BEGIN
  IF (SELECT version_num FROM public.alembic_version) <> '0057' THEN
    RAISE EXCEPTION 'CANON_OWNER_REVISION_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
    WHERE rolname = 'daon_notebook_delete' AND NOT rolcanlogin AND rolinherit
      AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members AS m
    JOIN pg_catalog.pg_roles AS parent ON parent.oid = m.roleid
    JOIN pg_catalog.pg_roles AS child ON child.oid = m.member
    WHERE parent.rolname = 'daon_app' AND child.rolname = 'daon_notebook_delete'
  ) THEN
    RAISE EXCEPTION 'CANON_OWNER_ROLE_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
    WHERE oid = v_function AND prosecdef
      AND pg_catalog.pg_get_userbyid(proowner) = 'daon_app'
      AND pg_get_functiondef(oid) LIKE '%CANON_SCOPE_DENIED%'
      AND proconfig @> ARRAY['search_path=pg_catalog, public']
      AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc AS p,
      LATERAL pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
      ) AS acl
    WHERE p.oid = v_function AND acl.grantee = 0
      AND acl.privilege_type = 'EXECUTE'
  ) THEN
    RAISE EXCEPTION 'CANON_OWNER_FUNCTION_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  FOREACH v_table IN ARRAY ARRAY[
    'sources', 'processing_runs', 'runs', 'generation_requests',
    'output_versions', 'approval_requests', 'knowledge_registrations'
  ] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_class AS c
      JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relname = v_table AND c.relkind = 'r'
        AND c.relrowsecurity AND c.relforcerowsecurity
        AND pg_catalog.pg_get_userbyid(c.relowner)
          IN ('daon_app', 'daon_notebook_delete')
    ) THEN
      RAISE EXCEPTION 'CANON_OWNER_TABLE_PRECONDITION:%', v_table USING ERRCODE = '55000';
    END IF;
  END LOOP;
  FOREACH v_table IN ARRAY ARRAY['sources', 'processing_runs'] LOOP
    IF pg_catalog.pg_get_userbyid(
      (SELECT relowner FROM pg_catalog.pg_class
       WHERE oid = format('public.%I', v_table)::regclass)
    ) <> 'daon_notebook_delete' OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'UPDATE') OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'DELETE') THEN
      RAISE EXCEPTION 'CANON_OWNER_DIRECT_WRITE_PRECONDITION:%', v_table
        USING ERRCODE = '55000';
    END IF;
  END LOOP;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_class AS c
    WHERE c.oid = 'public.identity_android_auth_transactions'::regclass
      AND c.relkind = 'r'
      AND pg_catalog.pg_get_userbyid(c.relowner) IN ('daon_app', 'postgres')
  ) THEN
    RAISE EXCEPTION 'ANDROID_AUTH_TABLE_PRECONDITION' USING ERRCODE = '55000';
  END IF;

  ALTER FUNCTION {_SIGNATURE}
  OWNER TO daon_notebook_delete;
  -- ALTER OWNER removes the old owner's implicit EXECUTE. Keep the original
  -- caller contract explicit, without granting direct table writes.
  REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC;
  GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO daon_app;
  -- 0057 creates this identity table after the original app grants; restore
  -- only the CRUD operations used by the Android transaction repository.
  REVOKE ALL ON TABLE public.identity_android_auth_transactions FROM PUBLIC;
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.identity_android_auth_transactions TO daon_app;
  IF NOT (
    pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'SELECT')
    AND pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'INSERT')
    AND pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'UPDATE')
    AND pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'DELETE')
  ) THEN
    RAISE EXCEPTION 'ANDROID_AUTH_TABLE_GRANT_POSTCONDITION' USING ERRCODE = '55000';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_catalog.pg_class AS c
    JOIN LATERAL pg_catalog.aclexplode(
      coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))
    ) AS acl ON TRUE
    WHERE c.oid = 'public.identity_android_auth_transactions'::regclass
      AND acl.grantee = 0
  ) THEN
    RAISE EXCEPTION 'ANDROID_AUTH_TABLE_PUBLIC_ACCESS' USING ERRCODE = '55000';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
    WHERE oid = v_function AND prosecdef
      AND pg_catalog.pg_get_userbyid(proowner) = 'daon_notebook_delete'
      AND pg_get_functiondef(oid) LIKE '%CANON_SCOPE_DENIED%'
      AND proconfig @> ARRAY['search_path=pg_catalog, public']
      AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc AS p,
      LATERAL pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
      ) AS acl
    WHERE p.oid = v_function AND acl.grantee = 0
      AND acl.privilege_type = 'EXECUTE'
  ) THEN
    RAISE EXCEPTION 'CANON_OWNER_FUNCTION_POSTCONDITION' USING ERRCODE = '55000';
  END IF;
END
$canon_owner$;
""")


def downgrade() -> None:
    op.execute(f"""
DO $canon_owner_reverse$
DECLARE
  v_function oid := '{_SIGNATURE}'::regprocedure;
BEGIN
  IF (SELECT version_num FROM public.alembic_version) <> '0058' OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
    WHERE oid = v_function AND prosecdef
      AND pg_catalog.pg_get_userbyid(proowner) = 'daon_notebook_delete'
      AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
  ) THEN
    RAISE EXCEPTION 'CANON_OWNER_REVERSE_PRECONDITION' USING ERRCODE = '55000';
  END IF;
  ALTER FUNCTION {_SIGNATURE}
  OWNER TO daon_app;
  REVOKE ALL ON FUNCTION {_SIGNATURE} FROM PUBLIC;
  GRANT EXECUTE ON FUNCTION {_SIGNATURE} TO daon_app;
  REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLE public.identity_android_auth_transactions FROM daon_app;
  IF (SELECT pg_catalog.pg_get_userbyid(relowner) FROM pg_catalog.pg_class
      WHERE oid = 'public.identity_android_auth_transactions'::regclass) <> 'daon_app' AND (
     pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'SELECT')
     OR pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'INSERT')
     OR pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'UPDATE')
     OR pg_catalog.has_table_privilege('daon_app', 'public.identity_android_auth_transactions', 'DELETE')) THEN
    RAISE EXCEPTION 'ANDROID_AUTH_TABLE_REVERSE_POSTCONDITION' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
    WHERE oid = v_function AND prosecdef
      AND pg_catalog.pg_get_userbyid(proowner) = 'daon_app'
      AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc AS p,
      LATERAL pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
      ) AS acl
    WHERE p.oid = v_function AND acl.grantee = 0
      AND acl.privilege_type = 'EXECUTE'
  ) THEN
    RAISE EXCEPTION 'CANON_OWNER_REVERSE_POSTCONDITION' USING ERRCODE = '55000';
  END IF;
END
$canon_owner_reverse$;
""")
