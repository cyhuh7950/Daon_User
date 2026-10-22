-- 0072 기준선에 대한 독립 DDL 검증이 가능하다. revision stamp는 수정하지 않는다.
ALTER TABLE public.system_provider_connections
  ADD COLUMN reasoning_contract text NOT NULL DEFAULT 'unknown',
  ADD CONSTRAINT system_provider_connections_reasoning_contract_check
    CHECK (reasoning_contract IN ('unknown','unsupported','router_effort_v1'));
