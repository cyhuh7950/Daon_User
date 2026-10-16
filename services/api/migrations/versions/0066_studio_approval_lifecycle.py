"""S24 tenant TTL versions and requester-only persistent approval notices."""
from pathlib import Path

from alembic import op

revision = "0066"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(Path(__file__).with_suffix(".sql").read_text(encoding="utf-8"))


def downgrade() -> None:
    # A deployed lifecycle is forward-only once any policy/notice/request uses it.
    op.execute("""
    DO $$ BEGIN
      IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
        OR (SELECT count(*) FROM public.alembic_version)<>1
        OR (SELECT version_num FROM public.alembic_version)<>'0066' THEN
        RAISE EXCEPTION 'STUDIO_APPROVAL_DOWNGRADE_PRECONDITION' USING ERRCODE='55000';
      END IF;
      LOCK TABLE public.approvals,public.approval_requests,public.studio_approval_policy_versions,
        public.studio_approval_notices IN ACCESS EXCLUSIVE MODE;
      IF EXISTS(SELECT 1 FROM public.studio_approval_policy_versions)
        OR EXISTS(SELECT 1 FROM public.studio_approval_notices)
        OR EXISTS(SELECT 1 FROM public.approval_requests)
        OR EXISTS(SELECT 1 FROM public.approvals) THEN
        RAISE EXCEPTION 'STUDIO_APPROVAL_FORWARD_RECOVERY_REQUIRED' USING ERRCODE='55000';
      END IF;
    END $$;
    """)
    op.execute("""
    DO $restore_owner$
    DECLARE relation oid:='public.approvals'::regclass; before_definition jsonb; after_definition jsonb;
      privileges text[]; privilege text; guard record;
    BEGIN
      IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
          AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
        OR pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid=relation AND relowner='daon_notebook_delete'::regrole AND relrowsecurity AND relforcerowsecurity)
        OR pg_catalog.has_table_privilege('daon_app',relation,'UPDATE') OR pg_catalog.has_table_privilege('daon_app',relation,'DELETE')
        OR NOT pg_catalog.has_table_privilege('daon_notebook_delete',relation,'UPDATE')
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_attribute WHERE attrelid=relation AND attacl IS NOT NULL)
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c,LATERAL pg_catalog.aclexplode(c.relacl) acl
          WHERE c.oid=relation AND (acl.grantee=0 OR acl.is_grantable OR acl.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole))) THEN
        RAISE EXCEPTION 'STUDIO_APPROVAL_DOWNGRADE_OWNER_PRECONDITION' USING ERRCODE='55000';
      END IF;
      FOR guard IN SELECT * FROM (VALUES
        ('validate_canon_insert','f5cde9f1dd02bbab70ceaabf5590f8ba',7),
        ('reject_canon_immutable_mutation','85dbf2028dbced19cb0bdf00c391d4be',27)
      ) expected(name,digest,trigger_type) LOOP
        IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_trigger t ON t.tgfoid=p.oid
          WHERE p.oid=pg_catalog.to_regprocedure('public.'||guard.name||'()') AND NOT p.prosecdef
          AND md5(p.prosrc)=guard.digest AND t.tgrelid=relation AND NOT t.tgisinternal AND t.tgenabled IN ('O','A') AND t.tgtype=guard.trigger_type) THEN
          RAISE EXCEPTION 'STUDIO_APPROVAL_DOWNGRADE_GUARD_PRECONDITION' USING ERRCODE='55000';
        END IF;
      END LOOP;
      SELECT array_agg(p) INTO privileges FROM unnest(ARRAY['SELECT','INSERT','TRUNCATE','REFERENCES','TRIGGER']) p
        WHERE pg_catalog.has_table_privilege('daon_app',relation,p);
      IF current_setting('server_version_num')::integer>=170000 AND pg_catalog.has_table_privilege('daon_app',relation,'MAINTAIN') THEN
        privileges:=privileges||'MAINTAIN'::text;
      END IF;
      IF NOT ('SELECT'=ANY(privileges) AND 'INSERT'=ANY(privileges)) THEN
        RAISE EXCEPTION 'STUDIO_APPROVAL_DOWNGRADE_ACL_PRECONDITION' USING ERRCODE='55000';
      END IF;
      SELECT jsonb_build_object('rls',c.relrowsecurity,'force',c.relforcerowsecurity,
        'columns',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid),
        'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.adnum) FROM pg_catalog.pg_attrdef d WHERE d.adrelid=c.oid),
        'constraints',(SELECT jsonb_agg(jsonb_build_array(k.oid,pg_catalog.pg_get_constraintdef(k.oid),k.convalidated) ORDER BY k.oid) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid OR k.confrelid=c.oid),
        'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid),
        'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid))
        INTO before_definition FROM pg_catalog.pg_class c WHERE c.oid=relation;
      ALTER TABLE public.approvals OWNER TO daon_app;
      REVOKE ALL ON public.approvals FROM daon_app,daon_notebook_delete;
      FOREACH privilege IN ARRAY privileges LOOP
        EXECUTE format('GRANT %s ON TABLE public.approvals TO daon_app',privilege);
      END LOOP;
      SELECT jsonb_build_object('rls',c.relrowsecurity,'force',c.relforcerowsecurity,
        'columns',(SELECT jsonb_agg(to_jsonb(a) ORDER BY a.attnum) FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid),
        'defaults',(SELECT jsonb_agg(to_jsonb(d) ORDER BY d.adnum) FROM pg_catalog.pg_attrdef d WHERE d.adrelid=c.oid),
        'constraints',(SELECT jsonb_agg(jsonb_build_array(k.oid,pg_catalog.pg_get_constraintdef(k.oid),k.convalidated) ORDER BY k.oid) FROM pg_catalog.pg_constraint k WHERE k.conrelid=c.oid OR k.confrelid=c.oid),
        'triggers',(SELECT jsonb_agg(to_jsonb(t) ORDER BY t.oid) FROM pg_catalog.pg_trigger t WHERE t.tgrelid=c.oid),
        'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.oid) FROM pg_catalog.pg_policy p WHERE p.polrelid=c.oid))
        INTO after_definition FROM pg_catalog.pg_class c WHERE c.oid=relation;
      IF before_definition IS DISTINCT FROM after_definition
        OR NOT EXISTS(SELECT 1 FROM pg_catalog.pg_class WHERE oid=relation AND relowner='daon_app'::regrole AND relrowsecurity AND relforcerowsecurity)
        OR pg_catalog.has_table_privilege('daon_app',relation,'UPDATE') OR pg_catalog.has_table_privilege('daon_app',relation,'DELETE')
        OR pg_catalog.has_table_privilege('daon_notebook_delete',relation,'UPDATE')
        OR EXISTS(SELECT 1 FROM pg_catalog.pg_class c,LATERAL pg_catalog.aclexplode(c.relacl) acl WHERE c.oid=relation AND acl.grantee=0) THEN
        RAISE EXCEPTION 'STUDIO_APPROVAL_DOWNGRADE_OWNER_POSTCONDITION' USING ERRCODE='55000';
      END IF;
      FOREACH privilege IN ARRAY privileges LOOP
        IF NOT pg_catalog.has_table_privilege('daon_app',relation,privilege) THEN
          RAISE EXCEPTION 'STUDIO_APPROVAL_DOWNGRADE_ACL_POSTCONDITION' USING ERRCODE='55000';
        END IF;
      END LOOP;
    END $restore_owner$;
    """)
    op.execute("""
    DROP TRIGGER studio_approval_deadline_insert ON public.approval_requests;
    DROP TRIGGER studio_approval_deadline_transition ON public.approval_requests;
    DROP FUNCTION public.sweep_studio_approval_lifecycle(text,integer);
    DROP FUNCTION public.expire_studio_approval_request(text);
    DROP FUNCTION public.record_studio_approval_notice(text,text);
    DROP FUNCTION public.guard_studio_approval_request();
    DROP TABLE public.studio_approval_notices;
    DROP TABLE public.studio_approval_policy_versions;
    DROP FUNCTION public.guard_studio_approval_notice();
    DROP FUNCTION public.guard_studio_approval_policy();
    DROP FUNCTION public.studio_approval_deadline(jsonb,timestamptz);
    DROP FUNCTION public.lock_studio_approval_version(text,text);
    """)
