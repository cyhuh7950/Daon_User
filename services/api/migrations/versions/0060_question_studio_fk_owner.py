"""Repair only question/Studio FK-parent ownership after the 0056 ACL split.

Run with the existing migration administrator in transactional DDL. The
non-login owner can take FK key-share locks; daon_app still cannot mutate
canonical rows directly. No row, policy, trigger or schema definition changes.
Downgrade restores the prior owners/ACLs and consequently the old FK failure.
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def _definitions(into: str) -> str:
    # Exclude only owner/ACL metadata which this migration intentionally changes.
    return f"""
  SELECT jsonb_agg(jsonb_build_object(
    'table', c.relname, 'rls', c.relrowsecurity, 'force', c.relforcerowsecurity,
    'columns', (SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum)
      FROM pg_catalog.pg_attribute a WHERE a.attrelid = c.oid),
    'defaults', (SELECT jsonb_agg(to_jsonb(d) ORDER BY d.adnum)
      FROM pg_catalog.pg_attrdef d WHERE d.adrelid = c.oid),
    'constraints', (SELECT jsonb_agg(jsonb_build_array(k.oid,
      pg_catalog.pg_get_constraintdef(k.oid), k.convalidated) ORDER BY k.oid)
      FROM pg_catalog.pg_constraint k WHERE k.conrelid = c.oid),
    'triggers', (SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid)
      FROM pg_catalog.pg_trigger t WHERE t.tgrelid = c.oid),
    'policies', (SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid)
      FROM pg_catalog.pg_policy p WHERE p.polrelid = c.oid)
  ) ORDER BY c.relname) INTO {into}
  FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
  WHERE n.nspname = 'public' AND c.relname = ANY(v_tables);
"""


def _owner_sql(expected_revision: str, old_owner: str, new_owner: str) -> str:
    owner_acl = (
        "GRANT UPDATE ON TABLE public.%I TO daon_notebook_delete"
        if new_owner == "daon_notebook_delete"
        else "REVOKE ALL ON TABLE public.%I FROM daon_notebook_delete"
    )
    return f"""
DO $question_studio_fk_owner$
DECLARE
  v_tables text[] := ARRAY[
    'model_attempts', 'run_results', 'generation_requests', 'studio_outputs',
    'output_versions', 'review_requests', 'approval_requests'
  ];
  v_table text;
  v_relation oid;
  v_privilege text;
  v_guard text;
  v_fk record;
  v_definitions_before jsonb;
  v_definitions_after jsonb;
BEGIN
  IF (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '{expected_revision}'
    OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = current_user AND rolsuper)
    OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'daon_app'
      AND NOT rolsuper AND NOT rolbypassrls)
    OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'daon_notebook_delete'
      AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls
      AND NOT rolcreaterole AND NOT rolcreatedb)
    OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m
      JOIN pg_catalog.pg_roles parent ON parent.oid = m.roleid
      JOIN pg_catalog.pg_roles child ON child.oid = m.member
      WHERE parent.rolname = 'daon_app' AND child.rolname = 'daon_notebook_delete'
        AND NOT m.admin_option)
    OR pg_catalog.pg_has_role('daon_app', 'daon_notebook_delete', 'MEMBER')
  THEN
    RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:revision/role' USING ERRCODE = '55000';
  END IF;

  FOR v_guard IN SELECT name FROM (VALUES
    ('validate_canon_insert', 'f5cde9f1dd02bbab70ceaabf5590f8ba'),
    ('reject_canon_immutable_mutation', '85dbf2028dbced19cb0bdf00c391d4be'),
    ('guard_canon_state_mutation', 'fda7bf31006011d2ce3d6b5cca168ecf')
  ) AS expected(name, digest)
  WHERE NOT EXISTS (SELECT 1 FROM pg_catalog.pg_proc p
    JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace
    WHERE n.nspname = 'public' AND p.proname = expected.name AND p.pronargs = 0
      AND p.prorettype = 'pg_catalog.trigger'::regtype AND NOT p.prosecdef
      AND pg_catalog.md5(p.prosrc) = expected.digest)
  LOOP
    RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:guard:%', v_guard USING ERRCODE = '55000';
  END LOOP;

  FOREACH v_table IN ARRAY v_tables LOOP
    SELECT c.oid INTO v_relation FROM pg_catalog.pg_class c
    JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
    WHERE n.nspname = 'public' AND c.relname = v_table AND c.relkind = 'r'
      AND c.relrowsecurity AND c.relforcerowsecurity
      AND pg_catalog.pg_get_userbyid(c.relowner) = '{old_owner}';
    IF v_relation IS NULL OR EXISTS (SELECT 1 FROM pg_catalog.pg_attribute a
      WHERE a.attrelid = v_relation AND a.attacl IS NOT NULL)
      OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c,
        LATERAL pg_catalog.aclexplode(coalesce(c.relacl, pg_catalog.acldefault('r', c.relowner))) acl
        WHERE c.oid = v_relation AND (acl.grantee = 0 OR acl.is_grantable
          OR acl.grantee NOT IN ('daon_app'::regrole, 'daon_notebook_delete'::regrole)))
      OR pg_catalog.has_table_privilege('daon_app', v_relation, 'UPDATE')
      OR pg_catalog.has_table_privilege('daon_app', v_relation, 'DELETE')
    THEN
      RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:table/ACL:%', v_table USING ERRCODE = '55000';
    END IF;
    FOREACH v_privilege IN ARRAY ARRAY['SELECT','INSERT','TRUNCATE','REFERENCES','TRIGGER'] LOOP
      IF NOT pg_catalog.has_table_privilege('daon_app', v_relation, v_privilege) THEN
        RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:privilege:%:%', v_table, v_privilege USING ERRCODE = '55000';
      END IF;
    END LOOP;
    IF current_setting('server_version_num')::integer >= 170000
      AND NOT pg_catalog.has_table_privilege('daon_app', v_relation, 'MAINTAIN') THEN
      RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:maintain:%', v_table USING ERRCODE = '55000';
    END IF;
    v_guard := CASE WHEN v_table IN ('generation_requests','output_versions','approval_requests')
      THEN 'guard_canon_state_mutation' ELSE 'reject_canon_immutable_mutation' END;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
      WHERE t.tgrelid = v_relation AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A')
        AND t.tgtype = 7 AND p.oid = 'public.validate_canon_insert()'::regprocedure)
      OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid = t.tgfoid
      WHERE t.tgrelid = v_relation AND NOT t.tgisinternal AND t.tgenabled IN ('O', 'A')
        AND t.tgtype = 27 AND p.oid = to_regprocedure('public.' || v_guard || '()'))
    THEN
      RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:trigger:%', v_table USING ERRCODE = '55000';
    END IF;
  END LOOP;

  FOR v_fk IN SELECT * FROM (VALUES
    ('run_results', 'selected_model_attempt_id', 'model_attempts', 'run_results_selected_model_attempt_id_fk'),
    ('citations', 'run_result_id', 'run_results', 'citations_run_result_id_fk'),
    ('studio_outputs', 'generation_request_id', 'generation_requests', 'studio_outputs_generation_request_id_fk'),
    ('output_versions', 'studio_output_id', 'studio_outputs', 'output_versions_studio_output_id_fk'),
    ('evidence_references', 'output_version_id', 'output_versions', 'evidence_references_output_version_id_fk'),
    ('review_requests', 'output_version_id', 'output_versions', 'review_requests_output_version_id_fk'),
    ('approval_requests', 'review_request_id', 'review_requests', 'approval_requests_review_request_id_fk'),
    ('approvals', 'approval_request_id', 'approval_requests', 'approvals_approval_request_id_fk')
  ) AS expected(child, column_name, parent, constraint_name) LOOP
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_constraint fk
      WHERE fk.conrelid = to_regclass('public.' || v_fk.child)
        AND fk.confrelid = to_regclass('public.' || v_fk.parent)
        AND fk.conname = v_fk.constraint_name
        AND fk.contype = 'f' AND fk.convalidated
        AND fk.conkey = ARRAY(SELECT a.attnum FROM unnest(ARRAY['tenant_id','workspace_id',v_fk.column_name])
          WITH ORDINALITY names(name, position) JOIN pg_catalog.pg_attribute a
          ON a.attrelid = fk.conrelid AND a.attname = names.name ORDER BY names.position)
        AND fk.confkey = ARRAY(SELECT a.attnum FROM unnest(ARRAY['tenant_id','workspace_id','record_id'])
          WITH ORDINALITY names(name, position) JOIN pg_catalog.pg_attribute a
          ON a.attrelid = fk.confrelid AND a.attname = names.name ORDER BY names.position))
    THEN
      RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_PRECONDITION:FK:%', v_fk.child USING ERRCODE = '55000';
    END IF;
  END LOOP;
  {_definitions('v_definitions_before')}
  FOREACH v_table IN ARRAY v_tables LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO {new_owner}', v_table);
    EXECUTE format('REVOKE ALL ON TABLE public.%I FROM daon_app', v_table);
    IF current_setting('server_version_num')::integer >= 170000 THEN
      EXECUTE format('GRANT SELECT, INSERT, TRUNCATE, REFERENCES, TRIGGER, MAINTAIN ON TABLE public.%I TO daon_app', v_table);
    ELSE
      EXECUTE format('GRANT SELECT, INSERT, TRUNCATE, REFERENCES, TRIGGER ON TABLE public.%I TO daon_app', v_table);
    END IF;
    EXECUTE format('{owner_acl}', v_table);
    v_relation := to_regclass('public.' || v_table);
    IF pg_catalog.has_table_privilege('daon_app', v_relation, 'UPDATE')
      OR pg_catalog.has_table_privilege('daon_app', v_relation, 'DELETE')
      OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c WHERE c.oid = v_relation
        AND pg_catalog.pg_get_userbyid(c.relowner) = '{new_owner}'
        AND c.relrowsecurity AND c.relforcerowsecurity)
      OR EXISTS (SELECT 1 FROM pg_catalog.pg_class c,
        LATERAL pg_catalog.aclexplode(c.relacl) acl WHERE c.oid = v_relation AND acl.grantee = 0)
      OR pg_catalog.has_table_privilege('daon_notebook_delete', v_relation, 'UPDATE')
        IS DISTINCT FROM {str(new_owner == 'daon_notebook_delete').lower()}
    THEN
      RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_POSTCONDITION:%', v_table USING ERRCODE = '55000';
    END IF;
  END LOOP;
  {_definitions('v_definitions_after')}
  IF v_definitions_after IS DISTINCT FROM v_definitions_before THEN
    RAISE EXCEPTION 'QUESTION_STUDIO_FK_OWNER_POSTCONDITION:definitions' USING ERRCODE = '55000';
  END IF;
END
$question_studio_fk_owner$;
"""


def upgrade() -> None:
    op.execute(_owner_sql("0059", "daon_app", "daon_notebook_delete"))


def downgrade() -> None:
    op.execute(_owner_sql("0060", "daon_notebook_delete", "daon_app"))
