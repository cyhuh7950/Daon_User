-- 0056 -> 0055 schema/ACL recovery only; previously deleted Notebook data is
-- not restored. Execute only as superuser in the same transaction as the
-- guarded alembic_version 0056 -> 0055 stamp. Do not use an old API image
-- until both the reverse DDL and version stamp have committed.
DO $notebook_acl_reverse$
DECLARE
  v_table text;
  v_fk_tables text[];
  v_fk_count integer;
  v_revision text;
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
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_SUPERUSER_REQUIRED' USING ERRCODE = '42501';
  END IF;
  SELECT version_num INTO STRICT v_revision FROM public.alembic_version;
  IF v_revision <> '0056' THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_REVISION_DRIFT:%', v_revision
      USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles
                 WHERE rolname = 'daon_notebook_delete' AND NOT rolcanlogin
                   AND rolinherit AND NOT rolbypassrls AND NOT rolcreaterole) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_ROLE_DRIFT' USING ERRCODE = '55000';
  END IF;
  IF (SELECT count(*) FROM unnest(v_delete_tables) AS t(name)) <> 27 OR
     (SELECT count(DISTINCT name) FROM unnest(v_delete_tables) AS t(name)) <> 27 THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_TARGET_DRIFT' USING ERRCODE = '55000';
  END IF;
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
     AND pg_catalog.pg_get_userbyid(child.relowner) = 'daon_notebook_delete';
  v_fk_count := coalesce(array_length(v_fk_tables, 1), 0);
  IF v_fk_count <> 19 OR
     (SELECT count(*) FROM pg_catalog.pg_class AS c
       JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace
      WHERE n.nspname = 'public' AND c.relkind = 'r'
        AND pg_catalog.pg_get_userbyid(c.relowner) = 'daon_notebook_delete') <> 19 THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_FK_OWNER_DRIFT:%', v_fk_count
      USING ERRCODE = '55000';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_proc
     WHERE oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure
       AND prosecdef AND pg_catalog.pg_get_userbyid(proowner) = 'daon_notebook_delete'
  ) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_FUNCTION_DRIFT' USING ERRCODE = '55000';
  END IF;
  FOREACH v_table IN ARRAY v_fk_tables LOOP
    IF NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'SELECT') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'INSERT') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'TRUNCATE') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'REFERENCES') OR
       NOT pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'TRIGGER') OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'UPDATE') OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'DELETE') THEN
      RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_APP_ACL_DRIFT:%', v_table
        USING ERRCODE = '55000';
    END IF;
  END LOOP;

  ALTER FUNCTION public.delete_notebook_scope(text,text,text) OWNER TO daon_app;
  ALTER FUNCTION public.delete_notebook_scope(text,text,text) SET search_path TO public;
  REVOKE ALL ON FUNCTION public.delete_notebook_scope(text,text,text) FROM PUBLIC;
  REVOKE ALL ON FUNCTION public.delete_notebook_scope(text,text,text) FROM daon_notebook_delete;
  REVOKE ALL ON FUNCTION public.delete_notebook_scope(text,text,text) FROM daon_app;
  GRANT EXECUTE ON FUNCTION public.delete_notebook_scope(text,text,text) TO daon_app;

  FOREACH v_table IN ARRAY v_fk_tables LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO daon_app', v_table);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM daon_app', v_table);
    EXECUTE format(
      'GRANT SELECT, INSERT, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.%I TO daon_app',
      v_table
    );
    -- One FK child (external_references) is outside the 27 DELETE targets.
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM daon_notebook_delete', v_table);
  END LOOP;
  FOREACH v_table IN ARRAY v_delete_tables LOOP
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM daon_notebook_delete', v_table);
  END LOOP;
  REVOKE daon_app FROM daon_notebook_delete;
  DROP ROLE daon_notebook_delete;

  FOREACH v_table IN ARRAY v_fk_tables LOOP
    IF pg_catalog.pg_get_userbyid(
      (SELECT relowner FROM pg_catalog.pg_class
        WHERE oid = format('public.%I', v_table)::regclass)
    ) <> 'daon_app' OR
       (SELECT relacl::text FROM pg_catalog.pg_class
         WHERE oid = format('public.%I', v_table)::regclass)
         <> '{daon_app=arDxt/daon_app}' OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'UPDATE') OR
       pg_catalog.has_table_privilege('daon_app', format('public.%I', v_table), 'DELETE') THEN
      RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_TABLE_POSTCONDITION:%', v_table
        USING ERRCODE = '55000';
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'daon_notebook_delete') OR
     NOT EXISTS (
       SELECT 1 FROM pg_catalog.pg_proc
        WHERE oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure
          AND prosecdef AND pg_catalog.pg_get_userbyid(proowner) = 'daon_app'
          AND proconfig @> ARRAY['search_path=public']
          AND pg_catalog.has_function_privilege('daon_app', oid, 'EXECUTE')
     ) OR EXISTS (
       SELECT 1 FROM pg_catalog.pg_proc AS p,
         LATERAL pg_catalog.aclexplode(
           coalesce(p.proacl, pg_catalog.acldefault('f', p.proowner))
         ) AS acl
       WHERE p.oid = 'public.delete_notebook_scope(text,text,text)'::regprocedure
         AND acl.grantee = 0 AND acl.privilege_type = 'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'NOTEBOOK_DELETE_ACL_REVERSE_POSTCONDITION_FAILED'
      USING ERRCODE = '55000';
  END IF;
END
$notebook_acl_reverse$;
