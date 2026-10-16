-- Administrator-only 0063 -> 0064. No data, role, ACL, or unrelated function change.
-- An external psql caller stamps exactly one 0063 row in this transaction.
DO $source_cleanup_device_state$
DECLARE
  fn oid := pg_catalog.to_regprocedure('public.assert_source_cleanup_authorization(text,text,text,text)');
  before_meta jsonb; after_meta jsonb; old_body text; new_body text; definition text;
  needle text := 'AND d.tenant_id=t AND d.user_id=a AND d.state=''active''';
  replacement text := 'AND d.tenant_id=t AND d.user_id=a AND d.state IN (''registered'',''trusted'')';
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '0063'
    OR fn IS NULL THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_DEVICE_PRECONDITION' USING ERRCODE='55000';
  END IF;
  SELECT to_jsonb(p)-'prosrc',p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
    INTO before_meta,old_body,definition FROM pg_catalog.pg_proc p WHERE p.oid=fn;
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
      JOIN pg_catalog.pg_language lang ON lang.oid=p.prolang
      WHERE p.oid=fn AND owner.rolsuper AND lang.lanname='plpgsql' AND NOT p.prosecdef
        AND cardinality(p.proconfig)=1
        AND regexp_replace(p.proconfig[1],'\s','','g')='search_path=pg_catalog,pg_temp')
    OR pg_catalog.md5(btrim(replace(old_body,chr(13)||chr(10),chr(10)),E' \t\r\n'))<>'2efa2f9ab828ae5f1311eea6c07ccca2'
    OR length(old_body)-length(replace(old_body,needle,''))<>length(needle)
    OR pg_catalog.has_function_privilege('daon_app',fn,'EXECUTE')
    OR NOT pg_catalog.has_function_privilege('daon_notebook_delete',fn,'EXECUTE')
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,
        LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) acl
      WHERE p.oid=fn AND (acl.grantee NOT IN (p.proowner,'daon_notebook_delete'::regrole::oid)
        OR acl.privilege_type<>'EXECUTE'
        OR (acl.grantee='daon_notebook_delete'::regrole::oid AND acl.is_grantable))) THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_DEVICE_PRECONDITION' USING ERRCODE='55000';
  END IF;
  EXECUTE replace(definition,needle,replacement);
  SELECT to_jsonb(p)-'prosrc',p.prosrc INTO after_meta,new_body
    FROM pg_catalog.pg_proc p WHERE p.oid=fn;
  IF before_meta IS DISTINCT FROM after_meta
    OR new_body IS DISTINCT FROM replace(old_body,needle,replacement) THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_DEVICE_POSTCONDITION' USING ERRCODE='55000';
  END IF;
END $source_cleanup_device_state$;

-- Separately approved queue-row identity repair: an outbox row's object_id is
-- not its manifest event_id. All other tables keep their exact old key logic.
DO $source_cleanup_outbox_identity$
DECLARE
  fn oid := pg_catalog.to_regprocedure('public.guard_source_cleanup_delete()');
  before_meta jsonb; after_meta jsonb; old_body text; new_body text; definition text;
  needle text := 'IF TG_TABLE_NAME=''notebook_bindings'' THEN k:=v->>''record_id''; END IF;';
  replacement text;
BEGIN
  IF NOT (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
    OR (SELECT version_num FROM public.alembic_version) IS DISTINCT FROM '0063'
    OR fn IS NULL THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_OUTBOX_PRECONDITION' USING ERRCODE='55000';
  END IF;
  SELECT to_jsonb(p)-'prosrc',p.prosrc,pg_catalog.pg_get_functiondef(p.oid)
    INTO before_meta,old_body,definition FROM pg_catalog.pg_proc p WHERE p.oid=fn;
  IF NOT EXISTS(SELECT 1 FROM pg_catalog.pg_proc p
      JOIN pg_catalog.pg_roles owner ON owner.oid=p.proowner
      JOIN pg_catalog.pg_language lang ON lang.oid=p.prolang
      WHERE p.oid=fn AND owner.rolsuper AND lang.lanname='plpgsql' AND NOT p.prosecdef
        AND cardinality(p.proconfig)=1
        AND regexp_replace(p.proconfig[1],'\s','','g')='search_path=pg_catalog,pg_temp')
    OR pg_catalog.md5(btrim(replace(old_body,chr(13)||chr(10),chr(10)),E' \t\r\n'))<>'68c1c18ecbe3564c754ae61ba521917d'
    OR length(old_body)-length(replace(old_body,needle,''))<>length(needle)
    OR pg_catalog.has_function_privilege('daon_app',fn,'EXECUTE')
    OR EXISTS(SELECT 1 FROM pg_catalog.pg_proc p,
        LATERAL pg_catalog.aclexplode(coalesce(p.proacl,pg_catalog.acldefault('f',p.proowner))) acl
      WHERE p.oid=fn AND (acl.grantee<>p.proowner OR acl.privilege_type<>'EXECUTE')) THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_OUTBOX_PRECONDITION' USING ERRCODE='55000';
  END IF;
  replacement := needle || E'\n  IF TG_TABLE_NAME=''object_outbox_events'' THEN k:=v->>''event_id''; END IF;';
  EXECUTE replace(definition,needle,replacement);
  SELECT to_jsonb(p)-'prosrc',p.prosrc INTO after_meta,new_body
    FROM pg_catalog.pg_proc p WHERE p.oid=fn;
  IF before_meta IS DISTINCT FROM after_meta
    OR new_body IS DISTINCT FROM replace(old_body,needle,replacement) THEN
    RAISE EXCEPTION 'SOURCE_CLEANUP_OUTBOX_POSTCONDITION' USING ERRCODE='55000';
  END IF;
END $source_cleanup_outbox_identity$;
