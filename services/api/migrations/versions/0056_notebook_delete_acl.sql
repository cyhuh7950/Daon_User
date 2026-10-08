-- 0056: execute only as a PostgreSQL superuser on the approved 0055 database.
-- Alembic owns its version stamp. A psql -1 caller must stamp 0055->0056 in
-- the same transaction and reject an update that affects anything but one row.
DO $notebook_acl$
DECLARE
  v_table text;
  v_relation regclass;
  v_owner text;
  v_fk_tables text[];
  v_denied_tables text[];
  v_fk_table_count integer;
  v_denied_delete_count integer;
  v_revision text;
  v_existing boolean;
  v_delete_tables text[] := ARRAY[
    'citations', 'document_processing_job_attempts', 'document_processing_jobs',
    'durable_jobs', 'evidence_references', 'evidence_spans',
    'extraction_evidence', 'index_versions', 'job_attempts',
    'knowledge_registrations', 'notebook_activities', 'notebook_bindings',
    'notebook_idempotency', 'notebook_metadata_versions',
    'notebook_source_unbinding_idempotency', 'notebook_source_unbindings',
    'notebooks', 'object_outbox_events', 'object_records', 'processing_runs',
    'source_versions', 'sources', 'sync_target_versions', 'transcript_segments',
    'transcript_versions', 'transcription_runs', 'understanding_results'
  ];
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname = current_user) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_SUPERUSER_REQUIRED' USING ERRCODE = '42501';
  END IF;
  SELECT version_num INTO STRICT v_revision FROM public.alembic_version;
  IF v_revision NOT IN ('0055', '0056') THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVISION_PRECONDITION_FAILED:%', v_revision
      USING ERRCODE = '55000';
  END IF;
  SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'daon_notebook_delete')
    INTO v_existing;
  IF v_existing AND v_revision <> '0056' THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_PARTIAL_ROLE_STATE' USING ERRCODE = '55000';
  END IF;
  IF NOT v_existing AND v_revision <> '0055' THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_ROLE_MISSING_AT_0056' USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
    WHERE rolname = 'daon_app' AND NOT rolbypassrls AND NOT rolcreaterole
  ) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_APP_ROLE_PRECONDITION_FAILED'
      USING ERRCODE = '55000';
  END IF;
  IF (SELECT count(*) FROM unnest(v_delete_tables) AS t(name)) <> 27 OR
     (SELECT count(DISTINCT name) FROM unnest(v_delete_tables) AS t(name)) <> 27 THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_TARGET_SET_INVALID' USING ERRCODE = '55000';
  END IF;

  -- The 19 referencing tables use the identical pre-0056 owner ACL. FK RI
  -- checks execute as the referencing table's owner, so that owner must change.
  SELECT array_agg(DISTINCT child.relname ORDER BY child.relname)
    INTO v_fk_tables
    FROM pg_catalog.pg_constraint AS fk
    JOIN pg_catalog.pg_class AS child ON child.oid = fk.conrelid
    JOIN pg_catalog.pg_namespace AS child_ns ON child_ns.oid = child.relnamespace
    JOIN pg_catalog.pg_class AS parent ON parent.oid = fk.confrelid
    JOIN pg_catalog.pg_namespace AS parent_ns ON parent_ns.oid = parent.relnamespace
   WHERE fk.contype = 'f'
     AND child_ns.nspname = 'public' AND parent_ns.nspname = 'public'
     AND parent.relname = ANY(v_delete_tables)
     AND (
       child.relacl::text = '{daon_app=arDxt/daon_app}'
       OR (v_existing AND pg_catalog.pg_get_userbyid(child.relowner) = 'daon_notebook_delete')
     );
  v_fk_table_count := coalesce(array_length(v_fk_tables, 1), 0);
  IF v_fk_table_count <> 19 THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_FK_SET_DRIFT:%', v_fk_table_count
      USING ERRCODE = '55000';
  END IF;

  SELECT array_agg(name ORDER BY name) INTO v_denied_tables
    FROM unnest(v_delete_tables) AS t(name)
   WHERE NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', name), 'DELETE');
  v_denied_delete_count := coalesce(array_length(v_denied_tables, 1), 0);
  IF v_denied_delete_count <> 20 THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_DELETE_SET_DRIFT:%', v_denied_delete_count
      USING ERRCODE = '55000';
  END IF;

  IF NOT v_existing THEN
    FOREACH v_table IN ARRAY v_delete_tables LOOP
      SELECT c.oid::regclass, pg_catalog.pg_get_userbyid(c.relowner)
        INTO v_relation, v_owner
        FROM pg_catalog.pg_class AS c
        JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
       WHERE n.nspname = 'public' AND c.relname = v_table
         AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity;
      IF NOT FOUND OR v_owner <> 'daon_app' THEN
        RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_TABLE_PRECONDITION_FAILED:%', v_table
          USING ERRCODE = '55000';
      END IF;
    END LOOP;
    FOREACH v_table IN ARRAY v_fk_tables LOOP
      IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute AS a
         WHERE a.attrelid = format('public.%I', v_table)::regclass
           AND a.attacl IS NOT NULL
      ) THEN
        RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_COLUMN_ACL_DRIFT:%', v_table
          USING ERRCODE = '55000';
      END IF;
    END LOOP;
    IF NOT EXISTS (
      SELECT 1 FROM pg_catalog.pg_proc
       WHERE oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure
         AND prosecdef AND pg_catalog.pg_get_userbyid(proowner) = 'daon_app'
    ) THEN
      RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_FUNCTION_PRECONDITION_FAILED'
        USING ERRCODE = '55000';
    END IF;

    CREATE ROLE daon_notebook_delete NOLOGIN INHERIT NOSUPERUSER
      NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    -- PostgreSQL 15 role membership uses the member's INHERIT attribute.
    -- daon_app is not a member of this role, so API sessions cannot SET ROLE.
    GRANT daon_app TO daon_notebook_delete;
    FOREACH v_table IN ARRAY v_fk_tables LOOP
      EXECUTE format('ALTER TABLE public.%I OWNER TO daon_notebook_delete', v_table);
      EXECUTE format('REVOKE ALL ON TABLE public.%I FROM daon_app', v_table);
      EXECUTE format(
        'GRANT SELECT, INSERT, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.%I TO daon_app',
        v_table
      );
      -- The internal FK row lock now runs as this table's dedicated owner.
      EXECUTE format('GRANT UPDATE ON TABLE public.%I TO daon_notebook_delete', v_table);
    END LOOP;
    FOREACH v_table IN ARRAY v_denied_tables LOOP
      EXECUTE format('GRANT DELETE ON TABLE public.%I TO daon_notebook_delete', v_table);
    END LOOP;
    ALTER FUNCTION public.delete_notebook_scope(text,text,text)
      OWNER TO daon_notebook_delete;
    ALTER FUNCTION public.delete_notebook_scope(text,text,text)
      SET search_path TO pg_catalog, public, pg_temp;
    REVOKE ALL ON FUNCTION public.delete_notebook_scope(text,text,text) FROM PUBLIC;
    GRANT EXECUTE ON FUNCTION public.delete_notebook_scope(text,text,text) TO daon_app;
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_roles
     WHERE rolname = 'daon_notebook_delete' AND NOT rolcanlogin AND rolinherit
       AND NOT rolsuper AND NOT rolcreaterole AND NOT rolbypassrls
  ) OR NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members AS m
    JOIN pg_catalog.pg_roles AS parent ON parent.oid = m.roleid
    JOIN pg_catalog.pg_roles AS child ON child.oid = m.member
    WHERE parent.rolname = 'daon_app' AND child.rolname = 'daon_notebook_delete'
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_auth_members AS m
    JOIN pg_catalog.pg_roles AS parent ON parent.oid = m.roleid
    JOIN pg_catalog.pg_roles AS child ON child.oid = m.member
    WHERE parent.rolname = 'daon_notebook_delete' AND child.rolname = 'daon_app'
  ) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_ROLE_POSTCONDITION_FAILED'
      USING ERRCODE = '55000';
  END IF;
  FOREACH v_table IN ARRAY v_fk_tables LOOP
    IF pg_catalog.pg_get_userbyid(
      (SELECT relowner FROM pg_catalog.pg_class
        WHERE oid = format('public.%I', v_table)::regclass)
    ) <> 'daon_notebook_delete' OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'SELECT') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'INSERT') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'TRUNCATE') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'REFERENCES') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'TRIGGER') OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'UPDATE') OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'DELETE') THEN
      RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_TABLE_POSTCONDITION_FAILED:%', v_table
        USING ERRCODE = '55000';
    END IF;
  END LOOP;
  FOREACH v_table IN ARRAY v_delete_tables LOOP
    IF NOT pg_catalog.has_table_privilege(
      'daon_notebook_delete', format('public.%I', v_table), 'DELETE'
    ) THEN
      RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_DEFINER_DELETE_MISSING:%', v_table
        USING ERRCODE = '55000';
    END IF;
  END LOOP;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
     WHERE oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure
       AND prosecdef AND pg_catalog.pg_get_userbyid(proowner) = 'daon_notebook_delete'
       AND pg_catalog.has_function_privilege(
         'daon_app', oid, 'EXECUTE'
       )
  ) OR EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc AS p,
      LATERAL pg_catalog.aclexplode(
        coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
      ) AS acl
    WHERE p.oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure
      AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
  ) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_FUNCTION_POSTCONDITION_FAILED'
      USING ERRCODE = '55000';
  END IF;
END
$notebook_acl$;
