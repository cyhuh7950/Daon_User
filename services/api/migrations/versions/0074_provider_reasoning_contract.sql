-- 0072+exact DDL 뒤 정식 계보가 재실행해도 정확한 기존 정의에만 수렴한다.
-- revision stamp/기존 데이터는 수정하지 않으며 잘못된 부분 적용을 보정하지 않는다.
DO $reasoning_contract$
DECLARE
  target regclass := pg_catalog.to_regclass('public.system_provider_connections');
  column_definition record;
  check_definition record;
BEGIN
  IF target IS NULL THEN
    RAISE EXCEPTION 'PROVIDER_REASONING_SCHEMA_CONFLICT' USING ERRCODE='55000';
  END IF;
  LOCK TABLE public.system_provider_connections IN ACCESS EXCLUSIVE MODE;
  IF NOT EXISTS (
    SELECT 1 FROM pg_catalog.pg_attribute
    WHERE attrelid=target AND attname='reasoning_contract' AND NOT attisdropped
  ) THEN
    IF EXISTS (SELECT 1 FROM pg_catalog.pg_constraint
      WHERE conrelid=target AND conname='system_provider_connections_reasoning_contract_check') THEN
      RAISE EXCEPTION 'PROVIDER_REASONING_SCHEMA_CONFLICT' USING ERRCODE='55000';
    END IF;
    ALTER TABLE public.system_provider_connections
      ADD COLUMN reasoning_contract text NOT NULL DEFAULT 'unknown',
      ADD CONSTRAINT system_provider_connections_reasoning_contract_check
        CHECK (reasoning_contract IN ('unknown','unsupported','router_effort_v1'));
  END IF;

  SELECT a.attnum,a.atttypid,a.atttypmod,a.attnotnull,a.attidentity,a.attgenerated,
    pg_catalog.pg_get_expr(d.adbin,d.adrelid,false) AS default_expression
  INTO column_definition
  FROM pg_catalog.pg_attribute a
  LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
  WHERE a.attrelid=target AND a.attname='reasoning_contract' AND NOT a.attisdropped;
  IF NOT FOUND OR column_definition.atttypid IS DISTINCT FROM 'pg_catalog.text'::regtype
    OR column_definition.atttypmod IS DISTINCT FROM -1
    OR column_definition.attnotnull IS DISTINCT FROM true
    OR column_definition.attidentity IS DISTINCT FROM ''
    OR column_definition.attgenerated IS DISTINCT FROM ''
    OR column_definition.default_expression IS DISTINCT FROM '''unknown''::text' THEN
    RAISE EXCEPTION 'PROVIDER_REASONING_SCHEMA_CONFLICT' USING ERRCODE='55000';
  END IF;

  SELECT c.contype,c.convalidated,c.connoinherit,c.conislocal,c.coninhcount,
    c.condeferrable,c.condeferred,c.conkey,
    pg_catalog.pg_get_expr(c.conbin,c.conrelid,false) AS check_expression
  INTO check_definition
  FROM pg_catalog.pg_constraint c
  WHERE c.conrelid=target AND c.conname='system_provider_connections_reasoning_contract_check';
  IF NOT FOUND OR check_definition.contype IS DISTINCT FROM 'c'
    OR check_definition.convalidated IS DISTINCT FROM true
    OR check_definition.connoinherit IS DISTINCT FROM false
    OR check_definition.conislocal IS DISTINCT FROM true
    OR check_definition.coninhcount IS DISTINCT FROM 0
    OR check_definition.condeferrable IS DISTINCT FROM false
    OR check_definition.condeferred IS DISTINCT FROM false
    OR check_definition.conkey IS DISTINCT FROM ARRAY[column_definition.attnum]::smallint[]
    OR check_definition.check_expression IS DISTINCT FROM
      '(reasoning_contract = ANY (ARRAY[''unknown''::text, ''unsupported''::text, ''router_effort_v1''::text]))' THEN
    RAISE EXCEPTION 'PROVIDER_REASONING_SCHEMA_CONFLICT' USING ERRCODE='55000';
  END IF;
END
$reasoning_contract$;
