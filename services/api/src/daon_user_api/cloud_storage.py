"""PostgreSQL cloud source-of-truth adapter with transaction-local RLS context."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Sequence, cast

from psycopg import Connection, Error, connect
from psycopg_pool import ConnectionPool, PoolTimeout


_SAFE_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_EXPECTED_SCHEMA_REVISION = "0067"
_COMPATIBLE_SCHEMA_REVISIONS = frozenset({"0062", "0063", "0064", "0066", "0067"})
_DEFAULT_DENY_CANONICAL_TEXT = '{"allowed_destinations":[],"allowed_provider_kinds":[],"classification":"restricted","masking_required":true,"max_bytes":0,"mode":"deny_external","redaction_required":true,"required_approver":"organization_admin"}'
_DEFAULT_DENY_DIGEST = "caf695f3de7e3e05feb024b3ff4b8b14cbfad5318b885ac15d8e4da25b819d7f"

# exact0070 SQL(E23AFAB1) 정적 계약. catalog 자신을 expected 기준으로 삼지 않는다.
_REGISTERED_RESTORE_FUNCTIONS={
    "registered_restore_actor_allowed(text,text,text)":("9f9b33a6afc90ad3f05a124f4e5dde24",True,"s","sql"),
    "guard_registered_restore_insert()":("b6941d60f955b001034d0ee5e163d562",True,"v","plpgsql"),
}
_R2_RES="registered_restore_target_reservations"
_R2_PRE="registered_restore_previews"
_R2_PHASE="registered_restore_phase_receipts"
_REGISTERED_RESTORE_COLUMN_NAMES={
    _R2_RES:"registry_generation_id destination_id tenant_id workspace_id request_id actor_id destination_version backup_id manifest_version source_manifest_digest physical_receipt_digest target_database_fingerprint target_bucket_fingerprint owner_operation_id reserved_at",
    _R2_PRE:"tenant_id workspace_id request_id preview_version actor_id registry_generation_id destination_id destination_version backup_id manifest_version source_manifest_digest closure_fingerprint included_roots_fingerprint excluded_roots_fingerprint source_policy_version retention_watermark expires_at encrypted_preview_receipt created_at",
    _R2_PHASE:"tenant_id workspace_id request_id sequence actor_id registry_generation_id destination_id destination_version preview_version operation_id idempotency_key phase outcome target_digest result_digest trace_id created_at",
}
_REGISTERED_RESTORE_COLUMNS={table:[(column,"integer" if column in {"destination_version","manifest_version","preview_version","phase"}
    else "bigint" if column=="sequence" else "timestamp with time zone" if column in {"created_at","reserved_at","expires_at"}
    else "bytea" if column=="encrypted_preview_receipt" else "text",column!="result_digest",
    "clock_timestamp()" if column in {"created_at","reserved_at"} else None,True)
    for column in fields.split()] for table,fields in _REGISTERED_RESTORE_COLUMN_NAMES.items()}
def _r2_key(kind,columns,table="",foreign=""):
    return (kind,columns.split(),table,foreign.split(),True,False,False,True,"aas" if kind=="f" else "")
_R2_SCOPE="tenant_id workspace_id request_id"
_R2_RES_KEY=_R2_SCOPE+" registry_generation_id destination_id destination_version backup_id manifest_version source_manifest_digest actor_id"
_R2_PRE_KEY=_R2_SCOPE+" preview_version registry_generation_id destination_id destination_version actor_id"
_REGISTERED_RESTORE_KEYS={
    _R2_RES:[_r2_key("p","registry_generation_id destination_id"),_r2_key("u","target_database_fingerprint"),
        _r2_key("u","target_bucket_fingerprint"),_r2_key("u",_R2_SCOPE),_r2_key("u",_R2_RES_KEY),
        _r2_key("f",_R2_SCOPE,"public.restore_requests",_R2_SCOPE),
        _r2_key("f","tenant_id request_id","public.restore_request_locator","tenant_id request_id"),
        _r2_key("f","tenant_id backup_id","public.backup_record_locator","tenant_id backup_id"),
        _r2_key("f","tenant_id workspace_id backup_id manifest_version","public.backup_manifests","tenant_id workspace_id backup_id manifest_version")],
    _R2_PRE:[_r2_key("p",_R2_SCOPE+" preview_version"),_r2_key("u",_R2_PRE_KEY),_r2_key("f",_R2_RES_KEY,"public."+_R2_RES,_R2_RES_KEY)],
    _R2_PHASE:[_r2_key("p",_R2_SCOPE+" sequence"),_r2_key("u",_R2_SCOPE+" operation_id phase outcome"),_r2_key("f",_R2_PRE_KEY,"public."+_R2_PRE,_R2_PRE_KEY)],
}
_REGISTERED_RESTORE_POLICIES={}
for _r2_table,_r2_name,_r2_parent,_r2_alias,_r2_extra in (
    (_R2_RES,"registered_restore_reservations_scope","restore_requests","r",()),
    (_R2_PRE,"registered_restore_previews_scope",_R2_RES,"r",("registry_generation_id","destination_id")),
    (_R2_PHASE,"registered_restore_phase_scope",_R2_PRE,"p",("preview_version",))):
    _r2_condition="registered_restore_actor_allowed(tenant_id,workspace_id,actor_id) AND EXISTS(SELECT 1 FROM "+_r2_parent+" "+_r2_alias+" WHERE "+" AND ".join(
        _r2_alias+"."+col+"="+_r2_table+"."+col for col in ("tenant_id","workspace_id","request_id","actor_id",*_r2_extra))+")"
    _REGISTERED_RESTORE_POLICIES[_r2_table]=[(_r2_name,"*",True,["PUBLIC"],_r2_condition,_r2_condition)]
_r2_owner_scope="tenant_id=nullif(current_setting('app.tenant_id'::text,true),''::text) AND workspace_id=nullif(current_setting('app.workspace_id'::text,true),''::text)"
_REGISTERED_RESTORE_POLICIES[_R2_RES].append(("registered_restore_reservations_owner_read","r",True,["daon_notebook_delete"],_r2_owner_scope,None))
_REGISTERED_RESTORE_TRIGGERS=sorted([(table,"registered_restore_"+name+"_"+operation,kind,"O","public.guard_registered_restore_insert","",False,False,"",None,"",True)
    for table,name in ((_R2_RES,"reservations"),(_R2_PRE,"previews"),(_R2_PHASE,"phases"))
    for operation,kind in (("insert",7),("immutable",27),("truncate",34))]+[
    ("restore_previews","registered_restore_fixture_mode_guard",7,"O","public.guard_registered_restore_insert","",False,False,"",None,"",True)])

# exact0071 M2-3 draft(D9C92DE3) 정적 기대값. 실행 DB에서 expected를 만들지 않는다.
_TELEMETRY_FUNCTIONS={
    "guard_operations_worker_observation()":("658b9ad82e1fbd19dba2c626b6a5e8dd",False,False,"trigger",False),
    "record_operations_worker_sample(text,text,text,timestamptz,bigint,timestamptz,timestamptz,timestamptz,timestamptz,text,bigint,bigint,bigint,bigint,integer)":("4e29d3c57a8ac264d5e650ad90e2fb5e",True,False,"public.operations_worker_observations",True),
    "read_operations_worker_samples(text[])":("a754dff46f3b80ad25e058c7cc02344f",True,True,"public.operations_worker_observations",True),
    "guard_operations_receipt()":("bfbe4cc2475dad0574a3de3903c3f0a1",True,False,"trigger",False),
    "record_operations_cost_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text)":("f88ad2e21ea4b9c3b9b8195baba817c1",True,False,"text",True),
    "read_operations_cost_receipts(text[],text)":("0f885165486bb9ce623fee2ae8fd0daf",True,True,"record",True),
    "read_operations_deployment_receipt()":("7ed340f6be464de0d0bb0434702f65c0",True,True,"public.operations_deployment_receipts",True),
    "record_operations_deployment_receipt(text,text,text,text,text,text,timestamptz,text,text)":("7fd7148b1158c4c65204ecfe210cece1",True,False,"text",False),
}
# 0072 미적용 초안에서 고정한 catalog 기대값. 실PG 출력 수집/적용 증거가 아니다.
_READINESS_FUNCTIONS={
    "document_readiness_parent_allowed(text,text,text,text,text,text,text)":["721cff7670b94399b70c09f563af69c3","boolean",False,"s","sql",False,["t","w","a","n","s","v","r"],[]],
    "guard_document_readiness_immutable()":["877013325240a6a0b181747fac02a22a","trigger",False,"v","plpgsql",False,[],[]],
    "record_document_role_wait(jsonb)":["68308ccb50ed39d92a7c00dc82d6633e","jsonb",False,"v","plpgsql",True,["p"],[]],
    "record_document_readiness_observation(jsonb,text)":["39d7fd2bb70dd3f44bda4a534f20fb54","jsonb",False,"v","plpgsql",True,["p","canonical"],[]],
    "discover_document_role_waits(jsonb,text[],integer)":["bd25f6b0ad444e27bbee92aa72a54817","record",True,"v","plpgsql",True,["scopes","after_cursor","maximum","tenant_id","workspace_id","actor_id","notebook_id","source_id","source_version_id","processing_run_id","capability_slot"],["jsonb","text[]","integer","text","text","text","text","text","text","text","text"]],
    "read_document_role_wait(text,text,text,text)":["e87f318c231d2a5f452ba77e9f9ca274","jsonb",False,"s","sql",True,["t","w","r","c"],[]],
    "read_latest_document_readiness_observation(text)":["864b2fdc8e112f30d2433bbd417820d2","jsonb",False,"s","sql",True,["c"],[]],
    "read_document_reprocess_history(text,text,text,text,text,text,text,text,text)":["019f4fcf0e6570104ccfeabf8d10ff18","jsonb",True,"s","sql",True,["t","w","a","n","s","v","r","c","d"],[]],
    "record_document_reprocess_receipt(jsonb)":["4bfacfd7d5d3c6344c222d9b04dfe047","jsonb",False,"v","plpgsql",True,["p"],[]],
    "read_document_blocked_parent(text,text,text,text,text,text,text,text)":["7efb6dbc7c83ab2f7e6779efbd7d8110","boolean",False,"s","sql",True,["t","w","a","n","s","v","r","new_r"],[]],
    "cleanup_document_readiness(text,text,integer)":["65ebea2779f68df002b45fe4e006b42d","integer",False,"v","plpgsql",False,["t","w","maximum"],[]],
    "read_document_readiness_metrics(text,text,text,text)":["4ef2e11da71811b4cc91ab885e26d655","record",True,"v","plpgsql",True,["t","w","a","scope","metric_id","value","has_records","coverage_partial","checked_at","window_start","window_end"],["text","text","text","text","text","bigint","boolean","boolean","timestamp with time zone","timestamp with time zone","timestamp with time zone"]]
}
_READINESS_PARENT_HELPER_MD5="63999ad4790fef4f01f5ee6ebfc9f587"
_READINESS_CHECKS={
    "document_role_waits":["tenant_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","workspace_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","actor_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","notebook_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","source_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","source_version_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","processing_run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","capability IN ('text_generation','image_understanding','document_parsing','speech_to_text')","capability_slot=coalesce(capability,'unknown')","jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384","content_digest ~ '^[0-9a-f]{64}$'"],
    "document_readiness_observations":["event_id ~ '^rdy-[0-9a-f]{32}$'","connection_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","adapter_type ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","capability IN ('text_generation','image_understanding','document_parsing','speech_to_text')","model_catalog_digest ~ '^[0-9a-f]{64}$'","configuration_version>0","probe_kind IN ('provider_connection_check','native_asr_ready','model_catalog_refresh')","status IN ('healthy','not_ready','unavailable')","content_digest ~ '^[0-9a-f]{64}$'","jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384","probe_kind<>'provider_connection_check' OR capability IS NULL","probe_kind<>'native_asr_ready' OR NOT (capability IS DISTINCT FROM 'speech_to_text')","observed_at<=received_at+'00:00:05'::interval"],
    "document_reprocess_receipts":["frozen_context_digest ~ '^[0-9a-f]{64}$'","phase IN ('deferred','final')","outcome IN ('enqueued','blocked_cost','blocked_policy','denied','suppressed','expired','deferred')","safe_code IN ('READY_REPROCESS_ALLOWED','READINESS_SCOPE_NOT_CONFIGURED',\n   'READINESS_MODE_UNCONFIRMED','READINESS_MODE_MANUAL','READINESS_ROLE_UNKNOWN','READINESS_DEPENDENCY_CHANGED',\n   'READINESS_NOT_READY','READINESS_OBSERVATION_EXPIRED','READINESS_ACCESS_DENIED','READINESS_SOURCE_CHANGED',\n   'READINESS_POLICY_BLOCKED','COST_LIMIT_EXCEEDED','READINESS_COST_UNCONFIRMED','READINESS_APPROVAL_MISSING',\n   'READINESS_ACTIVE_RUN_EXISTS','READINESS_CONTEXT_SUPPRESSED','READINESS_BACKOFF_PENDING')","new_run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$'","jsonb_typeof(payload)='object' AND octet_length(payload::text)<=16384","content_digest ~ '^[0-9a-f]{64}$'","(phase='deferred')=(outcome='deferred')","(new_run_id IS NOT NULL)=(outcome IN ('enqueued','blocked_cost','blocked_policy'))","(outcome='enqueued' AND safe_code='READY_REPROCESS_ALLOWED')\n   OR (outcome='blocked_cost' AND safe_code='COST_LIMIT_EXCEEDED')\n   OR (outcome='blocked_policy' AND safe_code='READINESS_POLICY_BLOCKED')\n   OR (outcome='denied' AND safe_code='READINESS_SCOPE_NOT_CONFIGURED')\n   OR (outcome='denied' AND safe_code='READINESS_ACCESS_DENIED')\n   OR (outcome='denied' AND safe_code='READINESS_SOURCE_CHANGED')\n   OR (outcome='suppressed' AND safe_code='READINESS_MODE_MANUAL')\n   OR (outcome='suppressed' AND safe_code='READINESS_ACTIVE_RUN_EXISTS')\n   OR (outcome='suppressed' AND safe_code='READINESS_CONTEXT_SUPPRESSED')\n   OR (outcome='expired' AND safe_code='READINESS_OBSERVATION_EXPIRED')\n   OR (outcome='deferred' AND safe_code='READINESS_MODE_UNCONFIRMED')\n   OR (outcome='deferred' AND safe_code='READINESS_ROLE_UNKNOWN')\n   OR (outcome='deferred' AND safe_code='READINESS_DEPENDENCY_CHANGED')\n   OR (outcome='deferred' AND safe_code='READINESS_NOT_READY')\n   OR (outcome='deferred' AND safe_code='READINESS_COST_UNCONFIRMED')\n   OR (outcome='deferred' AND safe_code='READINESS_APPROVAL_MISSING')\n   OR (outcome='deferred' AND safe_code='READINESS_BACKOFF_PENDING')"]
}
_READINESS_COLUMNS={
    "document_role_waits":[["tenant_id","text",True,None,""],["workspace_id","text",True,None,""],["actor_id","text",True,None,""],["notebook_id","text",True,None,""],["source_id","text",True,None,""],["source_version_id","text",True,None,""],["processing_run_id","text",True,None,""],["capability","text",False,None,""],["capability_slot","text",True,None,""],["payload","jsonb",True,None,""],["content_digest","text",True,None,""],["created_at","timestamp with time zone",True,"clock_timestamp()",""]],
    "document_readiness_observations":[["event_id","text",True,None,""],["connection_id","text",True,None,""],["adapter_type","text",True,None,""],["capability","text",False,None,""],["model_catalog_digest","text",True,None,""],["configuration_version","bigint",True,None,""],["observed_at","timestamp with time zone",True,None,""],["received_at","timestamp with time zone",True,"clock_timestamp()",""],["probe_kind","text",True,None,""],["status","text",True,None,""],["observation_sequence","bigint",True,None,"a"],["content_digest","text",True,None,""],["payload","jsonb",True,None,""]],
    "document_reprocess_receipts":[["tenant_id","text",True,None,""],["workspace_id","text",True,None,""],["actor_id","text",True,None,""],["notebook_id","text",True,None,""],["source_id","text",True,None,""],["source_version_id","text",True,None,""],["processing_run_id","text",True,None,""],["capability_slot","text",True,None,""],["event_id","text",True,None,""],["frozen_context_digest","text",True,None,""],["phase","text",True,None,""],["outcome","text",True,None,""],["safe_code","text",True,None,""],["new_run_id","text",False,None,""],["received_at","timestamp with time zone",True,"clock_timestamp()",""],["payload","jsonb",True,None,""],["content_digest","text",True,None,""]]
}
_READINESS_KEYS={
    "document_role_waits":[_r2_key("p","tenant_id workspace_id processing_run_id capability_slot"),
        _r2_key("u","tenant_id workspace_id processing_run_id capability_slot actor_id notebook_id source_id source_version_id")],
    "document_readiness_observations":[_r2_key("p","event_id"),_r2_key("u","observation_sequence")],
    "document_reprocess_receipts":[_r2_key("p","tenant_id workspace_id processing_run_id capability_slot event_id phase"),
        _r2_key("f","event_id","public.document_readiness_observations","event_id"),
        _r2_key("f","tenant_id workspace_id processing_run_id capability_slot actor_id notebook_id source_id source_version_id",
            "public.document_role_waits","tenant_id workspace_id processing_run_id capability_slot actor_id notebook_id source_id source_version_id")],
}
_READINESS_INDEXES=[
    ("document_readiness_latest",False,["connection_id","observation_sequence"],[0,3],None),
    ("document_reprocess_first_suppression",True,["tenant_id","workspace_id","actor_id","source_version_id","capability_slot","frozen_context_digest","safe_code"],[0]*7,"outcome=\'suppressed\'"),
    ("document_reprocess_one_run_context",True,["tenant_id","workspace_id","actor_id","source_version_id","capability_slot","frozen_context_digest"],[0]*6,"new_run_id IS NOT NULL"),
]
_TM_WORKER="operations_worker_observations"
_TM_DEPLOY="operations_deployment_receipts"
_TM_COST="operations_cost_receipts"
_TELEMETRY_COLUMN_NAMES={
    _TM_WORKER:"worker_id boot_id worker_kind process_started_at sequence observed_at received_at poll_started_at poll_finished_at active_started_at last_safe_code cpu_quota_millicores memory_limit_bytes memory_current_bytes runtime_available_bytes producer_version",
    _TM_DEPLOY:"operation_id operation code_revision image_digest previous_image_digest state observed_at received_at safe_code receipt_digest",
    _TM_COST:"tenant_id workspace_id run_id receipt_id actor_id provider_connection_id model_attempt_id basis amount currency usage_digest price_version state observed_at received_at receipt_digest",
}
_TELEMETRY_COLUMNS={table:[(column,"timestamp with time zone" if column.endswith("_at") else "bigint" if column in {"sequence","cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes"}
    else "integer" if column=="producer_version" else "numeric(24,9)" if column=="amount" else "text",
    column not in ({"poll_started_at","poll_finished_at","active_started_at","last_safe_code","cpu_quota_millicores","memory_limit_bytes","memory_current_bytes","runtime_available_bytes"} if table==_TM_WORKER
        else {"code_revision","image_digest","previous_image_digest"} if table==_TM_DEPLOY
        else {"provider_connection_id","model_attempt_id","amount","currency","usage_digest","price_version"}),
    "clock_timestamp()" if column=="received_at" and table!=_TM_WORKER else None,True)
    for column in names.split()] for table,names in _TELEMETRY_COLUMN_NAMES.items()}
_TELEMETRY_KEYS={
    _TM_WORKER:[_r2_key("p","worker_id boot_id")],_TM_DEPLOY:[_r2_key("p","operation_id")],
    _TM_COST:[_r2_key("p","tenant_id workspace_id run_id receipt_id"),
        _r2_key("f","provider_connection_id","public.system_provider_connections","connection_id"),
        _r2_key("f","tenant_id workspace_id run_id","public.runs","tenant_id workspace_id record_id"),
        _r2_key("f","tenant_id workspace_id model_attempt_id","public.model_attempts","tenant_id workspace_id record_id")],
}
_TELEMETRY_CHECKS={
    _TM_WORKER:["worker_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'","boot_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'",
        "worker_kind IN ('document','studio','object')","sequence>0","last_safe_code IN ('WORKER_STOPPED','WORKER_POLL_FAILED')",
        "cpu_quota_millicores>=0","memory_limit_bytes>=0","memory_current_bytes>=0","runtime_available_bytes>=0","producer_version=1",
        "process_started_at<=observed_at","poll_started_at IS NULL OR (poll_started_at>=process_started_at AND poll_started_at<=observed_at)",
        "poll_finished_at IS NULL OR (poll_started_at IS NOT NULL AND poll_finished_at>=process_started_at AND poll_finished_at<=observed_at)",
        "active_started_at IS NULL OR (poll_started_at IS NOT NULL AND active_started_at>=poll_started_at AND active_started_at<=observed_at)",
        "last_safe_code IS DISTINCT FROM 'WORKER_STOPPED' OR active_started_at IS NULL"],
    _TM_DEPLOY:["operation_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'","operation IN ('deploy','rollback')",
        "code_revision ~ '^[0-9a-f]{40}$'","image_digest ~ '^sha256:[0-9a-f]{64}$'","previous_image_digest ~ '^sha256:[0-9a-f]{64}$'",
        "state IN ('succeeded','failed','uncertain')","safe_code ~ '^[A-Z][A-Z0-9_]{0,127}$'","receipt_digest ~ '^[0-9a-f]{64}$'",
        "state<>'succeeded' OR (code_revision IS NOT NULL AND image_digest IS NOT NULL)","operation<>'rollback' OR state<>'succeeded' OR previous_image_digest IS NOT NULL"],
    _TM_COST:["receipt_id ~ '^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$'","basis IN ('provider_reported','price_card')","currency ~ '^[A-Z]{3}$'",
        "usage_digest ~ '^[0-9a-f]{64}$'",
        "state IN ('known','unknown','uncertain')","receipt_digest ~ '^[0-9a-f]{64}$'",
        "(state='known' AND amount IS NOT NULL AND amount>=0::numeric AND currency IS NOT NULL AND provider_connection_id IS NOT NULL) OR (state IN ('unknown','uncertain') AND amount IS NULL AND currency IS NULL)",
        "basis<>'price_card' OR (price_version IS NOT NULL AND length(price_version)>=1 AND length(price_version)<=128)"],
}
_TELEMETRY_SCOPE="tenant_id=nullif(current_setting('app.tenant_id'::text,true),''::text) AND workspace_id=nullif(current_setting('app.workspace_id'::text,true),''::text) AND actor_id=nullif(current_setting('app.actor_id'::text,true),''::text)"
_TELEMETRY_POLICIES={_TM_WORKER:[],_TM_DEPLOY:[],_TM_COST:[("operations_cost_receipts_scope","*",True,["PUBLIC"],_TELEMETRY_SCOPE,_TELEMETRY_SCOPE)]}
_TELEMETRY_POLICIES[_TM_COST].append(("operations_cost_receipts_manager_select","r",True,["daon_notebook_delete"],
    "tenant_id=nullif(current_setting('app.tenant_id'::text,true),''::text) AND workspace_id=nullif(current_setting('app.workspace_id'::text,true),''::text) "
    "AND current_setting('app.operations_cost_scope'::text,true)='workspace_visible'::text "
    "AND registered_restore_actor_allowed(tenant_id,workspace_id,nullif(current_setting('app.actor_id'::text,true),''::text))",None))
_TELEMETRY_TRIGGERS=sorted((table,table+suffix,kind,"O","public."+function,"",False,False,"",None,"",True)
    for table,function in ((_TM_WORKER,"guard_operations_worker_observation"),(_TM_DEPLOY,"guard_operations_receipt"),(_TM_COST,"guard_operations_receipt"))
    for suffix,kind in (("_guard",31),("_no_truncate",34)))

# 0073 정본 SQL에서 고정한 기대값이다. 실행 DB의 관측으로 기대값을 만들지 않는다.
_COST_EVENT='operations_run_cost_events'
_HOST_SAMPLE='operations_host_capacity_samples'
_COST_CAPACITY_NAMES={_COST_EVENT:'tenant_id workspace_id actor_id run_kind run_id sequence attempt_key state amount currency policy input_token_upper_bound output_token_limit price_version usage_digest receipt_id observed_at received_at',
    _HOST_SAMPLE:'host_ref boot_id sequence observed_at received_at cpu_logical_millicores cpu_usage_basis_points memory_total_bytes memory_available_bytes cpu_counters'}
_COST_CAPACITY_COLUMNS={table:[(name,'timestamp with time zone' if name.endswith('_at') else
    'bigint' if name in {'sequence','input_token_upper_bound','cpu_logical_millicores','memory_total_bytes','memory_available_bytes'} else
    'integer' if name in {'output_token_limit','cpu_usage_basis_points'} else 'numeric(24,9)' if name=='amount' else
    'jsonb' if name=='policy' else 'bigint[]' if name=='cpu_counters' else 'text',
    name not in {'amount','currency','usage_digest','receipt_id','cpu_usage_basis_points'},
    'clock_timestamp()' if name=='received_at' else None,True)
    for name in names.split()] for table,names in _COST_CAPACITY_NAMES.items()}
_COST_CAPACITY_KEYS={_COST_EVENT:[_r2_key('p','tenant_id workspace_id run_kind run_id sequence'),
    _r2_key('u','tenant_id workspace_id run_kind run_id attempt_key state'),
    _r2_key('u','tenant_id workspace_id run_kind run_id receipt_id')],_HOST_SAMPLE:[_r2_key('p','host_ref')]}
_COST_CAPACITY_CHECKS={_COST_EVENT:["run_kind IN ('question','document')",
    "run_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'",'sequence>0',
    "attempt_key ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'","state IN ('reserved','known','uncertain')",
    "jsonb_typeof(policy)='object' AND octet_length(policy::text)<=16384",
    'input_token_upper_bound>=0 AND input_token_upper_bound<=2147483647',
    'output_token_limit>=1 AND output_token_limit<=512',
    "price_version ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'","usage_digest ~ '^[0-9a-f]{64}$'",
    "receipt_id ~ '^cost-[0-9a-f]{64}$'",
    "(state='uncertain' AND amount IS NULL AND currency IS NULL) OR (state IN ('reserved','known') AND amount>=0::numeric AND amount IS NOT NULL AND currency='USD')",
    "(state='reserved' AND receipt_id IS NULL AND usage_digest IS NULL) OR (state='known' AND receipt_id IS NOT NULL AND usage_digest IS NOT NULL) OR (state='uncertain' AND receipt_id IS NOT NULL)"],
    _HOST_SAMPLE:["host_ref='wsl-server-daon-user'","boot_id ~ '^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'",'sequence>0',
    '(cpu_logical_millicores>=1000 AND cpu_logical_millicores<=32768000) AND cpu_logical_millicores%(1000)::bigint=0',
    'cpu_usage_basis_points>=0 AND cpu_usage_basis_points<=10000','memory_total_bytes>0',
    'memory_available_bytes>=0 AND memory_available_bytes<=memory_total_bytes',
    'cardinality(cpu_counters)=8 AND array_position(cpu_counters,NULL::bigint) IS NULL AND 0<=ALL(cpu_counters)']}
_COST_CAPACITY_POLICIES={_COST_EVENT:[('operations_run_cost_scope','*',True,['PUBLIC'],_TELEMETRY_SCOPE,_TELEMETRY_SCOPE)],
    _HOST_SAMPLE:[('operations_host_capacity_fixed_host','*',True,['daon_notebook_delete'],"host_ref='wsl-server-daon-user'","host_ref='wsl-server-daon-user'")]}
# MD5, security-definer, volatility, language, returns-set, app-execute, return-type, argument names.
_COST_CAPACITY_FUNCTIONS={
 'operations_run_cost_parent_allowed(text,text,text,text,text)':('ec89852fe88607cf5e529c43add69647',True,'s','sql',False,False,'boolean','t w a k r'),
 'guard_operations_run_cost_event()':('b25a05e7e03efcc470ed465d61869e13',True,'v','plpgsql',False,False,'trigger',''),
 'reserve_operations_run_cost(text,text,text,text,text,text,jsonb,bigint,integer)':('e376d5dcd04baad2e3abb7e369f9f776',True,'v','plpgsql',True,True,'record','t w a k r i p ib ob state safe_code sequence maximum_amount currency price_version observed_at'),
 'settle_operations_run_cost(text,text,text,text,text,text,bigint,text,numeric,text,text,text,text,timestamptz)':('8744bb39d0c3d84d7ba653849345accc',True,'v','plpgsql',False,True,'bigint','t w a k r i q s n u p h d o'),
 'record_operations_price_card_receipt(text,text,text,text,text,text,text,text,numeric,text,text,text,text,timestamptz,text)':('79b3cb11d078c32cf6350a07fdb43364',True,'v','plpgsql',False,True,'text','t w r a i c m b n u h p s o d'),
 'guard_operations_host_capacity()':('5f8c46897c60842e5c8e9c264641dddd',False,'v','plpgsql',False,False,'trigger',''),
 'record_operations_host_capacity(text,text,bigint,timestamptz,bigint,integer,bigint,bigint,bigint[])':('2852d0b5b4579a6aef1c25a39b0d8ddd',True,'v','plpgsql',False,True,'public.operations_host_capacity_samples','h b q o c x m v j'),
 'read_operations_host_capacity()':('433d5fda439dd0aee62744ed3438a1cc',True,'s','sql',True,True,'public.operations_host_capacity_samples','')}
_COST_CAPACITY_TRIGGERS=sorted((table,prefix+suffix,kind,'O','public.'+function,'',False,False,'',None,'',True)
    for table,prefix,function in ((_COST_EVENT,'operations_run_cost_events','guard_operations_run_cost_event'),
        (_HOST_SAMPLE,'operations_host_capacity','guard_operations_host_capacity'))
    for suffix,kind in (('_guard',31),('_no_truncate',34)))


def _telemetry_expression(expression):
    """기존 원자식 정규화를 재사용하되 AND/OR의 괄호 의미는 유지한다."""
    from .notification_postgres import _normalize_expression
    if expression is None:return None
    def parse(parts):
        while len(parts)>=2 and parts[0]=="(" and parts[-1]==")":
            depth=0;outer=True
            for token in parts[:-1]:
                depth+=1 if token=="(" else -1 if token==")" else 0
                if depth==0:outer=False;break
            if not outer:break
            parts=parts[1:-1]
        for operator in ("OR","AND"):
            depth=0;start=0;groups=[]
            for index,token in enumerate(parts):
                depth+=1 if token=="(" else -1 if token==")" else 0
                if depth==0 and token.upper()==operator:groups.append(parse(parts[start:index]));start=index+1
            if groups:
                children=[]
                for child in [*groups,parse(parts[start:])]:
                    # 같은 Boolean 연산자의 결합만 펼친다. 순서/개수/원자식은 유지.
                    if isinstance(child,tuple) and child[0]==operator:children.extend(child[1])
                    else:children.append(child)
                return operator,tuple(children)
        return _normalize_expression("".join(parts).replace("public.",""))
    # 단어 경계를 보존한 tokenization으로 식별자의 'or'를 연산자로 오인하지 않는다.
    tokens=re.findall(r"'(?:''|[^'])*'|[A-Za-z_][A-Za-z_0-9]*|[()]|[^A-Za-z_'()]",expression)
    return parse([token for token in tokens if not token.isspace()])


class _PoolAvailabilityLogFilter(logging.Filter):
    """Keep connection targets and driver failures out of operational logs."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.msg if isinstance(record.msg, str) else ""
        return not message.startswith(("error connecting", "reconnection attempt"))


_pool_logger = logging.getLogger("psycopg.pool")
if not any(isinstance(item, _PoolAvailabilityLogFilter) for item in _pool_logger.filters):
    _pool_logger.addFilter(_PoolAvailabilityLogFilter())


class CloudDatabaseError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


def classify_database_error(sqlstate: str | None) -> CloudDatabaseError:
    if sqlstate == "57014":
        return CloudDatabaseError("DATABASE_TIMEOUT", retryable=True)
    if sqlstate in {"40P01", "40001"}:
        return CloudDatabaseError("DATABASE_RETRYABLE_CONFLICT", retryable=True)
    if sqlstate is not None and sqlstate.startswith("23"):
        return CloudDatabaseError("DATABASE_CONSTRAINT_VIOLATION", retryable=False)
    if sqlstate in {"42501", "55000"}:
        return CloudDatabaseError("DATABASE_ACCESS_DENIED", retryable=False)
    return CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)


@dataclass(frozen=True, slots=True)
class CloudAccessContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    capability: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.capability):
            if not isinstance(value, str) or not _SAFE_SCOPE.fullmatch(value):
                raise ValueError("CLOUD_ACCESS_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class CloudReadiness:
    ready: bool
    schema_revision: str | None
    vector_version: str | None


@dataclass(frozen=True, slots=True)
class CloudNotification:
    notification_id: str
    version: int
    read_at: datetime | None


class PostgresCloudStore:
    """A bounded pool and explicit repository port; callers supply verified scope."""

    def __init__(self, dsn: str, *, min_size: int = 1, max_size: int = 4, organization_admin_v2_enabled: bool = False,
                 notification_inbox_enabled: bool = False, development_egress_defaults: bool = False) -> None:
        if not isinstance(dsn, str) or not dsn:
            raise ValueError("CLOUD_DATABASE_DSN_REQUIRED")
        self._dsn = dsn
        if type(organization_admin_v2_enabled) is not bool:
            raise ValueError("ORGANIZATION_ADMIN_V2_FLAG_INVALID")
        self._organization_admin_v2_enabled = organization_admin_v2_enabled
        if type(notification_inbox_enabled) is not bool:
            raise ValueError("NOTIFICATION_INBOX_FLAG_INVALID")
        # L1 내부 준비 상태 경계다. 공개 환경변수나 운영 발행 경로를 신설하지 않는다.
        self._notification_inbox_enabled = notification_inbox_enabled
        if type(development_egress_defaults) is not bool:
            raise ValueError("DEVELOPMENT_EGRESS_DEFAULTS_FLAG_INVALID")
        self._development_egress_defaults = development_egress_defaults
        self._open_lock = threading.Lock()
        self._pool = ConnectionPool[tuple[Any, ...]](
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            kwargs={"autocommit": False},
            timeout=2.0,
            reconnect_timeout=5.0,
            open=False,
        )

    def _ensure_pool_open(self) -> None:
        if not self._pool.closed:
            return
        with self._open_lock:
            if self._pool.closed:
                self._pool.open(wait=False)

    def close(self) -> None:
        self._pool.close()

    @contextmanager
    def _transaction(self, context: CloudAccessContext) -> Iterator[Connection[tuple[Any, ...]]]:
        try:
            self._ensure_pool_open()
            with self._pool.connection(timeout=2.0) as connection:
                with connection.transaction():
                    connection.execute("SELECT set_config('app.tenant_id', %s, true)", (context.tenant_id,))
                    connection.execute("SELECT set_config('app.workspace_id', %s, true)", (context.workspace_id,))
                    connection.execute("SELECT set_config('app.actor_id', %s, true)", (context.actor_id,))
                    connection.execute("SELECT set_config('app.capability', %s, true)", (context.capability,))
                    connection.execute("SELECT set_config('app.notification_inbox_enabled', %s, true)",
                        ('true' if getattr(self, '_notification_inbox_enabled', False) else 'false',))
                    yield connection
        except CloudDatabaseError:
            raise
        except PoolTimeout:
            raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            raise classify_database_error(error.sqlstate) from None

    def readiness(self) -> CloudReadiness:
        def compatible(connection, revision, vector):
            if vector is None:
                return False
            if revision == "0073":
                return (self._organization_storage_is_protected(connection)
                    and self._notification_storage_is_protected(connection)
                    and self._registered_restore_storage_is_protected(connection,expected_revision="0073")
                    and self._telemetry_storage_is_protected(connection,expected_revision="0073")
                    and self._document_readiness_storage_is_protected(connection,expected_revision="0073")
                    and self._operations_cost_capacity_storage_is_protected(connection))
            if revision == "0072":
                return (self._organization_storage_is_protected(connection)
                    and self._notification_storage_is_protected(connection)
                    and self._registered_restore_storage_is_protected(connection,expected_revision="0072")
                    and self._telemetry_storage_is_protected(connection,expected_revision="0072")
                    and self._document_readiness_storage_is_protected(connection))
            if revision == "0071":
                return (self._organization_storage_is_protected(connection)
                    and self._notification_storage_is_protected(connection)
                    and self._registered_restore_storage_is_protected(connection,expected_revision="0071")
                    and self._telemetry_storage_is_protected(connection))
            if revision == "0070":
                return (self._organization_storage_is_protected(connection)
                    and self._notification_storage_is_protected(connection)
                    and self._registered_restore_storage_is_protected(connection))
            if revision == "0069":
                return self._organization_storage_is_protected(connection) and self._notification_storage_is_protected(connection)
            if getattr(self, "_notification_inbox_enabled", False):
                return False
            if revision == "0068":
                return self._organization_storage_is_protected(connection)
            return not getattr(self, "_organization_admin_v2_enabled", False) and revision in _COMPATIBLE_SCHEMA_REVISIONS
        try:
            if self._pool.closed:
                with connect(self._dsn, connect_timeout=1) as connection:
                    revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                    vector_row = connection.execute(
                        "SELECT extversion FROM pg_extension WHERE extname = %s", ("vector",)
                    ).fetchone()
                    revision = None if revision_row is None else str(revision_row[0])
                    vector_version = None if vector_row is None else str(vector_row[0])
                    ready = compatible(connection, revision, vector_version)
                if ready:
                    self._ensure_pool_open()
                return CloudReadiness(ready, revision, vector_version)
            with self._pool.connection(timeout=2.0) as connection:
                revision_row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
                vector_row = connection.execute(
                    "SELECT extversion FROM pg_extension WHERE extname = %s", ("vector",)
                ).fetchone()
                revision = None if revision_row is None else str(revision_row[0])
                vector_version = None if vector_row is None else str(vector_row[0])
                ready = compatible(connection, revision, vector_version)
            return CloudReadiness(
                ready,
                revision,
                vector_version,
            )
        except (Error, PoolTimeout, OSError):
            return CloudReadiness(False, None, None)

    @staticmethod
    def _registered_restore_storage_is_protected(connection,*,expected_revision="0070") -> bool:
        """0070 원 보호를 정확0071에서도 재검사. 구 revision 의미·DB 변경0."""
        if expected_revision not in {"0070","0071","0072","0073"}:return False
        from .notification_postgres import _normalize_expression
        def values(row):return tuple(row.values()) if isinstance(row,dict) else tuple(row)
        def normalize(value):return None if value is None else _normalize_expression(value.replace("public.",""))
        try:
            row=connection.execute("""/* r4:role */ SELECT count(*)=1 AND bool_and(NOT rolcanlogin AND rolinherit
                AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
                AND NOT pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
                AND (SELECT count(*)=1 AND min(version_num)=%s FROM public.alembic_version)
                FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'""",(expected_revision,)).fetchone()
            if row is None or values(row)!=(True,):return False
            for table,columns in _REGISTERED_RESTORE_COLUMNS.items():
                qualified="public."+table
                row=connection.execute("""/* r4:table */ SELECT c.relkind='r' AND c.relpersistence='p'
                    AND c.relowner='daon_notebook_delete'::regrole AND c.relrowsecurity AND c.relforcerowsecurity
                    AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                        WHERE a.grantee<>c.relowner OR a.is_grantable)
                    FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(%s)""",(qualified,)).fetchone()
                if row is None or values(row)!=(True,):return False
                rows=connection.execute("""/* r4:columns */ SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
                    a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid),
                    a.attidentity='' AND a.attgenerated='' AND a.attcollation=t.typcollation
                    AND pg_catalog.has_column_privilege('daon_app',a.attrelid,a.attnum,'SELECT')
                    AND pg_catalog.has_column_privilege('daon_app',a.attrelid,a.attnum,'INSERT')=(a.attname NOT IN ('created_at','reserved_at'))
                    AND NOT pg_catalog.has_column_privilege('daon_app',a.attrelid,a.attnum,'UPDATE,REFERENCES')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl ELSE NULL::pg_catalog.aclitem[] END) x
                        WHERE x.grantee<>'daon_app'::regrole OR x.is_grantable OR x.privilege_type NOT IN ('SELECT','INSERT'))
                    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                    WHERE a.attrelid=pg_catalog.to_regclass('public.'||%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",(table,)).fetchall()
                if [values(r) for r in rows]!=columns:return False
                rows=connection.execute("""/* r4:constraints */ SELECT c.contype,
                    ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n,o) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o),
                    CASE WHEN c.contype='f' THEN n.nspname||'.'||r.relname ELSE '' END,
                    ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(n,o) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.o),
                    c.convalidated,c.condeferrable,c.condeferred,
                    CASE WHEN c.contype IN ('p','u') THEN i.indisvalid AND i.indisready AND i.indimmediate
                        AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=cardinality(c.conkey) AND i.indnatts=i.indnkeyatts ELSE true END,
                    CASE WHEN c.contype='f' THEN c.confupdtype::text||c.confdeltype::text||c.confmatchtype::text ELSE '' END
                    FROM pg_catalog.pg_constraint c LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
                    LEFT JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
                    WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype IN ('p','u','f')""",(table,)).fetchall()
                if sorted(map(values,rows),key=repr)!=sorted(_REGISTERED_RESTORE_KEYS[table],key=repr):return False
                rows=connection.execute("""/* r4:policy */ SELECT p.polname,p.polcmd,p.polpermissive,
                    ARRAY(SELECT CASE WHEN role_id=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_id)::text END FROM unnest(p.polroles) role_id ORDER BY 1),
                    pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
                    FROM pg_catalog.pg_policy p WHERE p.polrelid=pg_catalog.to_regclass('public.'||%s)""",(table,)).fetchall()
                actual=[(*values(r)[:4],normalize(values(r)[4]),normalize(values(r)[5])) for r in rows]
                expected=[(*r[:4],normalize(r[4]),normalize(r[5])) for r in _REGISTERED_RESTORE_POLICIES[table]]
                if sorted(actual,key=repr)!=sorted(expected,key=repr):return False
            for signature,expected in _REGISTERED_RESTORE_FUNCTIONS.items():
                row=connection.execute("""/* r4:function */ SELECT md5(p.prosrc),p.prosecdef,p.provolatile,l.lanname,
                    p.proowner='daon_notebook_delete'::regrole AND p.prokind='f' AND NOT p.proretset AND NOT p.proisstrict AND NOT p.proleakproof AND p.proparallel='u'
                        AND p.prorettype=CASE WHEN p.proname='registered_restore_actor_allowed' THEN 'boolean'::regtype ELSE 'trigger'::regtype END,
                    p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[],
                    NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
                        WHERE a.grantee NOT IN (p.proowner,'daon_app'::regrole) OR a.is_grantable OR a.privilege_type<>'EXECUTE'),
                    pg_catalog.has_function_privilege('daon_app',p.oid,'EXECUTE')=(p.proname='registered_restore_actor_allowed')
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
                    WHERE p.oid=pg_catalog.to_regprocedure(%s)""",("public."+signature,)).fetchone()
                if row is None or values(row)!=(*expected,True,True,True,True):return False
            rows=connection.execute("""/* r4:trigger */ SELECT c.relname,t.tgname,t.tgtype,t.tgenabled,
                n.nspname||'.'||p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),t.tgdeferrable,t.tginitdeferred,
                encode(t.tgargs,'hex'),pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),t.tgattr::text,t.tgconstraint=0 AND t.tgparentid=0
                FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE cn.nspname='public' AND NOT t.tgisinternal
                AND (c.relname=ANY(%s::text[]) OR (c.relname='restore_previews' AND t.tgname='registered_restore_fixture_mode_guard'))
                ORDER BY c.relname,t.tgname""",(list(_REGISTERED_RESTORE_COLUMNS),)).fetchall()
            return [values(r) for r in rows]==_REGISTERED_RESTORE_TRIGGERS
        except (Error,TypeError,ValueError,KeyError,AttributeError):return False

    @staticmethod
    def _telemetry_storage_is_protected(connection,*,expected_revision="0071") -> bool:
        """0071 읽기 전용 catalog 검증. 0071SQL/권한/원장 생성·수정0."""
        if expected_revision not in {"0071","0072","0073"}:return False
        def values(row):return tuple(row.values()) if isinstance(row,dict) else tuple(row)
        def yes(row):return row is not None and values(row)==(True,)
        try:
            row=connection.execute("""/* m2:role */ SELECT
                (SELECT count(*)=1 AND min(version_num)=%s FROM public.alembic_version)
                AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
                    AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
                AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_app' AND NOT rolsuper AND NOT rolbypassrls)
                AND NOT pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
                AND (SELECT count(*)=8 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.proname=ANY(%s::text[]))""",(expected_revision,[s.split("(")[0] for s in _TELEMETRY_FUNCTIONS])).fetchone()
            if not yes(row):return False
            for table,columns in _TELEMETRY_COLUMNS.items():
                row=connection.execute("""/* m2:table */ SELECT c.relkind='r' AND c.relpersistence='p'
                    AND c.relowner='daon_notebook_delete'::regrole AND c.relrowsecurity=%s AND c.relforcerowsecurity=%s
                    AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                        WHERE a.grantee<>c.relowner OR a.is_grantable)
                    FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(%s)""",(table==_TM_COST,table==_TM_COST,"public."+table)).fetchone()
                if not yes(row):return False
                rows=connection.execute("""/* m2:columns */ SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
                    a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid),
                    a.attidentity='' AND a.attgenerated='' AND a.attcollation=t.typcollation
                    AND NOT pg_catalog.has_column_privilege('daon_app',a.attrelid,a.attnum,'SELECT,INSERT,UPDATE,REFERENCES')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl ELSE NULL::pg_catalog.aclitem[] END))
                    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                    WHERE a.attrelid=pg_catalog.to_regclass('public.'||%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",(table,)).fetchall()
                if [values(r) for r in rows]!=columns:return False
                rows=connection.execute("""/* m2:constraints */ SELECT c.contype,
                    ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n,o) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o),
                    CASE WHEN c.contype='f' THEN n.nspname||'.'||r.relname ELSE '' END,
                    ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(n,o) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.o),
                    c.convalidated AND (c.contype<>'f' OR (SELECT count(*)=4 AND bool_and(t.tgenabled='O') FROM pg_catalog.pg_trigger t WHERE t.tgconstraint=c.oid)),c.condeferrable,c.condeferred,
                    CASE WHEN c.contype IN ('p','u') THEN i.indisvalid AND i.indisready AND i.indimmediate
                        AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=cardinality(c.conkey) AND i.indnatts=i.indnkeyatts ELSE true END,
                    CASE WHEN c.contype='f' THEN c.confupdtype::text||c.confdeltype::text||c.confmatchtype::text ELSE '' END
                    FROM pg_catalog.pg_constraint c LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
                    LEFT JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
                    WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype IN ('p','u','f')""",(table,)).fetchall()
                if sorted(map(values,rows),key=repr)!=sorted(_TELEMETRY_KEYS[table],key=repr):return False
                rows=connection.execute("""/* m2:checks */ SELECT pg_catalog.pg_get_expr(c.conbin,c.conrelid),c.convalidated,c.connoinherit
                    FROM pg_catalog.pg_constraint c WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype='c'""",(table,)).fetchall()
                if any(values(r)[1:]!=(True,False) for r in rows):return False
                if sorted((_telemetry_expression(values(r)[0]) for r in rows),key=repr)!=sorted(map(_telemetry_expression,_TELEMETRY_CHECKS[table]),key=repr):return False
                rows=connection.execute("""/* m2:policy */ SELECT p.polname,p.polcmd,p.polpermissive,
                    ARRAY(SELECT CASE WHEN role_id=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_id)::text END FROM unnest(p.polroles) role_id ORDER BY 1),
                    pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
                    FROM pg_catalog.pg_policy p WHERE p.polrelid=pg_catalog.to_regclass('public.'||%s)""",(table,)).fetchall()
                actual=[(*values(r)[:4],_telemetry_expression(values(r)[4]),_telemetry_expression(values(r)[5])) for r in rows]
                expected=[(*r[:4],_telemetry_expression(r[4]),_telemetry_expression(r[5])) for r in _TELEMETRY_POLICIES[table]]
                if sorted(actual,key=repr)!=sorted(expected,key=repr):return False
            for signature,expected in _TELEMETRY_FUNCTIONS.items():
                row=connection.execute("""/* m2:function */ SELECT md5(p.prosrc),p.prosecdef,p.proretset,
                    p.prorettype=pg_catalog.to_regtype(%s),pg_catalog.has_function_privilege('daon_app',p.oid,'EXECUTE'),
                    p.proowner='daon_notebook_delete'::regrole AND p.prokind='f' AND p.pronargdefaults=0 AND NOT p.proisstrict
                        AND NOT p.proleakproof AND p.proparallel='u' AND p.provolatile='v' AND l.lanname='plpgsql'
                        AND (p.proname<>'read_operations_cost_receipts' OR (
                            p.proallargtypes=ARRAY['text[]'::regtype,'text'::regtype,'text'::regtype,'text'::regtype,
                                'text'::regtype,'text'::regtype,'numeric'::regtype,'text'::regtype,'timestamptz'::regtype,'timestamptz'::regtype]::oid[]
                            AND p.proargmodes=ARRAY['i','i','t','t','t','t','t','t','t','t']::"char"[]
                            AND p.proargnames=ARRAY['ids','scope','run_id','actor_id','model_attempt_id','state','amount','currency','observed_at','received_at']::text[])),
                    p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[],
                    NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
                        WHERE a.grantee NOT IN (p.proowner,'daon_app'::regrole) OR a.is_grantable OR a.privilege_type<>'EXECUTE')
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
                    WHERE p.oid=pg_catalog.to_regprocedure(%s)""",(expected[3],"public."+signature)).fetchone()
                if row is None or values(row)!=(expected[0],expected[1],expected[2],True,expected[4],True,True,True):return False
            rows=connection.execute("""/* m2:trigger */ SELECT c.relname,t.tgname,t.tgtype,t.tgenabled,
                n.nspname||'.'||p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),t.tgdeferrable,t.tginitdeferred,
                encode(t.tgargs,'hex'),pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),t.tgattr::text,t.tgconstraint=0 AND t.tgparentid=0
                FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE cn.nspname='public' AND NOT t.tgisinternal AND c.relname=ANY(%s::text[]) ORDER BY c.relname,t.tgname""",(list(_TELEMETRY_COLUMNS),)).fetchall()
            if [values(r) for r in rows]!=_TELEMETRY_TRIGGERS:return False
            row=connection.execute("""/* m2:parents */ SELECT count(*)=3 AND bool_and(c.relkind='r' AND c.relpersistence='p'
                AND c.relowner=CASE WHEN c.relname='model_attempts' THEN 'daon_notebook_delete'::regrole ELSE 'daon_app'::regrole END
                AND c.relrowsecurity AND c.relforcerowsecurity
                AND pg_catalog.has_table_privilege('daon_notebook_delete',c.oid,'SELECT')
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                    WHERE a.grantee=0 OR a.is_grantable OR a.grantee NOT IN ('daon_app'::regrole,'daon_notebook_delete'::regrole))
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_attribute a WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped AND a.attacl IS NOT NULL)
                AND (SELECT count(*)=2 FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                    WHERE t.tgrelid=c.oid AND NOT t.tgisinternal AND t.tgenabled='O' AND t.tgqual IS NULL AND t.tgnargs=0 AND NOT t.tgdeferrable
                    AND ((t.tgtype=7 AND p.oid=pg_catalog.to_regprocedure('public.validate_canon_insert()') AND md5(p.prosrc)='f5cde9f1dd02bbab70ceaabf5590f8ba')
                        OR (t.tgtype=27 AND p.oid=CASE WHEN c.relname='runs' THEN pg_catalog.to_regprocedure('public.guard_canon_state_mutation()') ELSE pg_catalog.to_regprocedure('public.reject_canon_immutable_mutation()') END
                            AND md5(p.prosrc)=CASE WHEN c.relname='runs' THEN 'fda7bf31006011d2ce3d6b5cca168ecf' ELSE '85dbf2028dbced19cb0bdf00c391d4be' END))))
                FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname='public' AND c.relname IN ('runs','routing_decisions','model_attempts')""").fetchone()
            return yes(row)
        except (Error,TypeError,ValueError,KeyError,AttributeError):return False

    @staticmethod
    def _operations_cost_capacity_storage_is_protected(connection) -> bool:
        """0073 비용/호스트 보호 구조를 정본 기대값과 대조한다. 읽기 전용."""
        def values(row):return tuple(row.values()) if isinstance(row,dict) else tuple(row)
        def yes(row):return row is not None and values(row)==(True,)
        try:
            row=connection.execute("""/* capacity:role */ SELECT
                (SELECT count(*)=1 AND min(version_num)=%s FROM public.alembic_version)
                AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
                    AND NOT rolcanlogin AND rolinherit AND NOT rolsuper AND NOT rolbypassrls AND NOT rolcreaterole AND NOT rolcreatedb)
                AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_app' AND NOT rolsuper AND NOT rolbypassrls)
                AND NOT pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
                AND (SELECT count(*)=8 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.proname=ANY(%s::text[]))""",("0073",[s.split("(")[0] for s in _COST_CAPACITY_FUNCTIONS])).fetchone()
            if not yes(row):return False
            for table,columns in _COST_CAPACITY_COLUMNS.items():
                row=connection.execute("""/* capacity:table */ SELECT c.relkind='r' AND c.relpersistence='p'
                    AND c.relowner='daon_notebook_delete'::regrole AND c.relrowsecurity AND c.relforcerowsecurity
                    AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                        WHERE a.grantee<>c.relowner OR a.is_grantable)
                    FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass(%s)""",("public."+table,)).fetchone()
                if not yes(row):return False
                rows=connection.execute("""/* capacity:columns */ SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
                    a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid),
                    a.attidentity='' AND a.attgenerated='' AND a.attcollation=t.typcollation
                    AND NOT pg_catalog.has_column_privilege('daon_app',a.attrelid,a.attnum,'SELECT,INSERT,UPDATE,REFERENCES')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(CASE WHEN cardinality(a.attacl)>0 THEN a.attacl ELSE NULL::pg_catalog.aclitem[] END))
                    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                    WHERE a.attrelid=pg_catalog.to_regclass('public.'||%s) AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",(table,)).fetchall()
                if [values(r) for r in rows]!=columns:return False
                rows=connection.execute("""/* capacity:constraints */ SELECT c.contype,
                    ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n,o) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o),
                    CASE WHEN c.contype='f' THEN n.nspname||'.'||r.relname ELSE '' END,
                    ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(n,o) JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.o),
                    c.convalidated AND (c.contype<>'f' OR (SELECT count(*)=4 AND bool_and(t.tgenabled='O') FROM pg_catalog.pg_trigger t WHERE t.tgconstraint=c.oid)),c.condeferrable,c.condeferred,
                    CASE WHEN c.contype IN ('p','u') THEN i.indisvalid AND i.indisready AND i.indimmediate
                        AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=cardinality(c.conkey) AND i.indnatts=i.indnkeyatts ELSE true END,
                    CASE WHEN c.contype='f' THEN c.confupdtype::text||c.confdeltype::text||c.confmatchtype::text ELSE '' END
                    FROM pg_catalog.pg_constraint c LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
                    LEFT JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
                    WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype<>'c'""",(table,)).fetchall()
                if sorted(map(values,rows),key=repr)!=sorted(_COST_CAPACITY_KEYS[table],key=repr):return False
                rows=connection.execute("""/* capacity:checks */ SELECT pg_catalog.pg_get_expr(c.conbin,c.conrelid),c.convalidated,c.connoinherit
                    FROM pg_catalog.pg_constraint c WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype='c'""",(table,)).fetchall()
                if any(values(r)[1:]!=(True,False) for r in rows):return False
                if sorted((_telemetry_expression(values(r)[0]) for r in rows),key=repr)!=sorted(map(_telemetry_expression,_COST_CAPACITY_CHECKS[table]),key=repr):return False
                rows=connection.execute("""/* capacity:policy */ SELECT p.polname,p.polcmd,p.polpermissive,
                    ARRAY(SELECT CASE WHEN role_id=0 THEN 'PUBLIC' ELSE pg_catalog.pg_get_userbyid(role_id)::text END FROM unnest(p.polroles) role_id ORDER BY 1),
                    pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
                    FROM pg_catalog.pg_policy p WHERE p.polrelid=pg_catalog.to_regclass('public.'||%s)""",(table,)).fetchall()
                actual=[(*values(r)[:4],_telemetry_expression(values(r)[4]),_telemetry_expression(values(r)[5])) for r in rows]
                expected=[(*r[:4],_telemetry_expression(r[4]),_telemetry_expression(r[5])) for r in _COST_CAPACITY_POLICIES[table]]
                if sorted(actual,key=repr)!=sorted(expected,key=repr):return False
            for signature,expected in _COST_CAPACITY_FUNCTIONS.items():
                row=connection.execute("""/* capacity:function */ SELECT md5(p.prosrc),p.prosecdef,p.provolatile,
                    l.lanname,p.proretset,pg_catalog.has_function_privilege('daon_app',p.oid,'EXECUTE'),
                    p.prorettype=pg_catalog.to_regtype(%s),coalesce(p.proargnames,ARRAY[]::text[]),
                    ARRAY(SELECT t::regtype::text FROM unnest(p.proallargtypes) WITH ORDINALITY a(t,o) ORDER BY o),
                    coalesce(p.proargmodes,ARRAY[]::"char"[]),
                    p.proowner='daon_notebook_delete'::regrole AND p.prokind='f' AND p.pronargdefaults=0
                        AND p.provariadic=0 AND NOT p.proisstrict AND NOT p.proleakproof AND p.proparallel='u',
                    p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[],
                    NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
                        WHERE a.grantee NOT IN (p.proowner,'daon_app'::regrole) OR a.is_grantable OR a.privilege_type<>'EXECUTE')
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
                    WHERE p.oid=pg_catalog.to_regprocedure(%s)""",(expected[6],"public."+signature)).fetchone()
                all_types=(['text']*6+['jsonb','bigint','integer','text','text','bigint','numeric','text','text','timestamp with time zone']
                    if signature.startswith('reserve_operations_run_cost(') else [])
                modes=['i']*9+['t']*7 if all_types else []
                target=(*expected[:6],True,expected[7].split(),all_types,modes,True,True,True)
                if row is None or values(row)!=target:return False
            rows=connection.execute("""/* capacity:trigger */ SELECT c.relname,t.tgname,t.tgtype,t.tgenabled,
                n.nspname||'.'||p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),t.tgdeferrable,t.tginitdeferred,
                encode(t.tgargs,'hex'),pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),t.tgattr::text,t.tgconstraint=0 AND t.tgparentid=0
                FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE cn.nspname='public' AND NOT t.tgisinternal AND c.relname=ANY(%s::text[]) ORDER BY c.relname,t.tgname""",(list(_COST_CAPACITY_COLUMNS),)).fetchall()
            if [values(r) for r in rows]!=_COST_CAPACITY_TRIGGERS:return False
            return True
        except (Error,TypeError,ValueError,KeyError,AttributeError):return False

    @staticmethod
    def _document_readiness_storage_is_protected(connection,*,expected_revision="0072") -> bool:
        """0072 초안의 닫힌 catalog 계약. 조회만 수행하며 실제 적용/PG 시험을 대신하지 않는다."""
        if expected_revision not in {"0072","0073"}:return False
        def values(row):return tuple(row.values()) if isinstance(row,dict) else tuple(row)
        def yes(row):return row is not None and values(row)==(True,)
        normalize=_telemetry_expression
        tables=list(_READINESS_COLUMNS)
        try:
            row=connection.execute("""/* readiness:role */ SELECT
                (SELECT count(*)=1 AND min(version_num)=%s FROM public.alembic_version)
                AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_notebook_delete'
                    AND rolinherit AND NOT rolcanlogin AND NOT rolsuper AND NOT rolbypassrls
                    AND NOT rolcreaterole AND NOT rolcreatedb AND NOT rolreplication)
                AND EXISTS(SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='daon_app' AND NOT rolsuper AND NOT rolbypassrls)
                AND pg_catalog.pg_has_role('daon_notebook_delete','daon_app','MEMBER')
                AND NOT pg_catalog.pg_has_role('daon_app','daon_notebook_delete','MEMBER')
                AND (SELECT count(*)=12 FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                    WHERE n.nspname='public' AND p.proname=ANY(%s::text[]))""",
                (expected_revision,[name.split('(')[0] for name in _READINESS_FUNCTIONS])).fetchone()
            if not yes(row):return False
            for table,columns in _READINESS_COLUMNS.items():
                row=connection.execute("""/* readiness:table */ SELECT c.relkind='r' AND c.relpersistence='p'
                    AND c.relowner='daon_notebook_delete'::regrole AND c.relrowsecurity AND c.relforcerowsecurity
                    AND NOT pg_catalog.has_table_privilege('daon_app',c.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault('r',c.relowner))) a
                        WHERE a.grantee<>c.relowner OR a.is_grantable)
                    FROM pg_catalog.pg_class c WHERE c.oid=pg_catalog.to_regclass('public.'||%s)""",(table,)).fetchone()
                if not yes(row):return False
                rows=connection.execute("""/* readiness:columns */ SELECT a.attname,pg_catalog.format_type(a.atttypid,a.atttypmod),
                    a.attnotnull,pg_catalog.pg_get_expr(d.adbin,d.adrelid),a.attidentity,
                    a.attgenerated='' AND a.attcollation=t.typcollation
                    AND NOT pg_catalog.has_column_privilege('daon_app',a.attrelid,a.attnum,'SELECT,INSERT,UPDATE,REFERENCES')
                    AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(a.attacl))
                    FROM pg_catalog.pg_attribute a JOIN pg_catalog.pg_type t ON t.oid=a.atttypid
                    LEFT JOIN pg_catalog.pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                    WHERE a.attrelid=pg_catalog.to_regclass('public.'||%s) AND a.attnum>0 AND NOT a.attisdropped
                    ORDER BY a.attnum""",(table,)).fetchall()
                if [values(row) for row in rows]!=[(*row,True) for row in columns]:return False
                rows=connection.execute("""/* readiness:keys */ SELECT c.contype,
                    ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n,o)
                        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.conrelid AND a.attnum=k.n ORDER BY k.o),
                    CASE WHEN c.contype='f' THEN n.nspname||'.'||r.relname ELSE '' END,
                    ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(n,o)
                        JOIN pg_catalog.pg_attribute a ON a.attrelid=c.confrelid AND a.attnum=k.n ORDER BY k.o),
                    c.convalidated AND (c.contype<>'f' OR (SELECT count(*)=4 AND bool_and(t.tgenabled='O')
                        FROM pg_catalog.pg_trigger t WHERE t.tgconstraint=c.oid)),c.condeferrable,c.condeferred,
                    CASE WHEN c.contype IN ('p','u') THEN i.indisvalid AND i.indisready AND i.indimmediate AND NOT i.indnullsnotdistinct ELSE true END,
                    CASE WHEN c.contype='f' THEN c.confupdtype::text||c.confdeltype::text||c.confmatchtype::text ELSE '' END
                    FROM pg_catalog.pg_constraint c LEFT JOIN pg_catalog.pg_class r ON r.oid=c.confrelid
                    LEFT JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.conindid
                    WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype<>'c'""",(table,)).fetchall()
                if sorted(map(values,rows),key=repr)!=sorted(_READINESS_KEYS[table],key=repr):return False
                rows=connection.execute("""/* readiness:checks */ SELECT pg_catalog.pg_get_expr(c.conbin,c.conrelid),
                    c.convalidated AND c.conislocal AND c.coninhcount=0 AND NOT c.connoinherit
                    FROM pg_catalog.pg_constraint c WHERE c.conrelid=pg_catalog.to_regclass('public.'||%s) AND c.contype='c'""",(table,)).fetchall()
                if any(values(row)[1] is not True for row in rows):return False
                if sorted((normalize(values(row)[0]) for row in rows),key=repr)!=sorted(map(normalize,_READINESS_CHECKS[table]),key=repr):return False
                rows=connection.execute("""/* readiness:policies */ SELECT p.polname,p.polcmd,p.polpermissive,
                    ARRAY(SELECT r.rolname::text FROM unnest(p.polroles) role_oid JOIN pg_catalog.pg_roles r ON r.oid=role_oid ORDER BY r.rolname),
                    pg_catalog.pg_get_expr(p.polqual,p.polrelid),pg_catalog.pg_get_expr(p.polwithcheck,p.polrelid)
                    FROM pg_catalog.pg_policy p WHERE p.polrelid=pg_catalog.to_regclass('public.'||%s)""",(table,)).fetchall()
                if [(*values(row)[:4],normalize(values(row)[4]),normalize(values(row)[5])) for row in rows]!=[
                    (table+'_owner','*',True,['daon_notebook_delete'],normalize('true'),normalize('true'))]:return False
            functions={**_READINESS_FUNCTIONS,'source_access_notification_access(text,text)':[
                _READINESS_PARENT_HELPER_MD5,'boolean',False,'v','plpgsql',False,['p_version','p_notebook'],[]]}
            for signature,expected in functions.items():
                row=connection.execute("""/* readiness:function */ SELECT md5(p.prosrc),p.prorettype::regtype::text,p.proretset,
                    p.provolatile,l.lanname,pg_catalog.has_function_privilege('daon_app',p.oid,'EXECUTE'),
                    coalesce(p.proargnames,ARRAY[]::text[]),
                    ARRAY(SELECT t::regtype::text FROM unnest(p.proallargtypes) WITH ORDINALITY a(t,o) ORDER BY o),
                    p.proowner='daon_notebook_delete'::regrole AND p.prosecdef=%s AND p.prokind='f'
                        AND p.pronargdefaults=0 AND p.provariadic=0 AND NOT p.proisstrict AND NOT p.proleakproof AND p.proparallel='u',
                    p.proconfig=ARRAY['search_path=pg_catalog, pg_temp']::text[],
                    NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a
                        WHERE a.grantee NOT IN (p.proowner,'daon_app'::regrole) OR a.is_grantable OR a.privilege_type<>'EXECUTE'),
                    coalesce(p.proargmodes,ARRAY[]::"char"[])
                    FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang
                    WHERE p.oid=pg_catalog.to_regprocedure(%s)""",(signature!='guard_document_readiness_immutable()','public.'+signature)).fetchone()
                inputs=signature.split('(',1)[1][:-1]
                input_count=0 if not inputs else len(inputs.split(','))
                modes=['i']*input_count+['t']*(len(expected[7])-input_count) if expected[7] else []
                if row is None or values(row)!=(*expected,True,True,True,modes):return False
            rows=connection.execute("""/* readiness:indexes */ SELECT ci.relname,i.indisunique,
                ARRAY(SELECT a.attname::text FROM unnest(i.indkey) WITH ORDINALITY k(n,o)
                    JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.n ORDER BY k.o),i.indoption::smallint[],
                pg_catalog.pg_get_expr(i.indpred,i.indrelid),i.indisvalid AND i.indisready AND i.indimmediate
                    AND NOT i.indnullsnotdistinct AND i.indexprs IS NULL AND i.indnkeyatts=i.indnatts
                    AND am.amname='btree'
                    AND NOT EXISTS(SELECT 1 FROM unnest(i.indclass,i.indcollation,i.indkey) k(op,coll,att)
                        JOIN pg_catalog.pg_opclass oc ON oc.oid=k.op
                        JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.att
                        WHERE NOT oc.opcdefault OR k.coll<>a.attcollation)
                FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class ct ON ct.oid=i.indrelid
                JOIN pg_catalog.pg_namespace n ON n.oid=ct.relnamespace JOIN pg_catalog.pg_class ci ON ci.oid=i.indexrelid
                JOIN pg_catalog.pg_am am ON am.oid=ci.relam
                WHERE n.nspname='public' AND ct.relname=ANY(%s::text[])
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.pg_constraint c WHERE c.conindid=i.indexrelid)
                ORDER BY ci.relname""",(tables,)).fetchall()
            if [(*values(row)[:4],normalize(values(row)[4]),values(row)[5]) for row in rows]!=[
                (*row[:4],normalize(row[4]),True) for row in _READINESS_INDEXES]:return False
            rows=connection.execute("""/* readiness:triggers */ SELECT c.relname,t.tgname,t.tgtype,t.tgenabled,
                n.nspname||'.'||p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),t.tgdeferrable,t.tginitdeferred,
                encode(t.tgargs,'hex'),pg_catalog.pg_get_expr(t.tgqual,t.tgrelid),t.tgattr::text,t.tgconstraint=0 AND t.tgparentid=0
                FROM pg_catalog.pg_trigger t JOIN pg_catalog.pg_class c ON c.oid=t.tgrelid
                JOIN pg_catalog.pg_namespace cn ON cn.oid=c.relnamespace JOIN pg_catalog.pg_proc p ON p.oid=t.tgfoid
                JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace
                WHERE cn.nspname='public' AND c.relname=ANY(%s::text[]) AND NOT t.tgisinternal ORDER BY c.relname,t.tgname""",(tables,)).fetchall()
            expected_triggers=sorted((table,table+suffix,kind,'O','public.guard_document_readiness_immutable','',False,False,'',None,'',True)
                for table in tables for suffix,kind in (('_guard',31),('_no_truncate',34)))
            if [values(row) for row in rows]!=expected_triggers:return False
            row=connection.execute("""/* readiness:sequence */ SELECT s.relkind='S' AND s.relpersistence='p' AND s.relowner='daon_notebook_delete'::regrole
                AND pg_catalog.pg_get_serial_sequence('public.document_readiness_observations','observation_sequence')::regclass=s.oid
                AND q.seqtypid='bigint'::regtype AND q.seqstart=1 AND q.seqincrement=1 AND q.seqmin=1
                AND q.seqmax=9223372036854775807 AND q.seqcache=1 AND NOT q.seqcycle
                AND NOT pg_catalog.has_sequence_privilege('daon_app',s.oid,'USAGE,SELECT,UPDATE')
                AND NOT EXISTS(SELECT 1 FROM pg_catalog.aclexplode(COALESCE(s.relacl,pg_catalog.acldefault('s',s.relowner))) a
                    WHERE a.grantee<>s.relowner OR a.is_grantable)
                FROM pg_catalog.pg_class s JOIN pg_catalog.pg_sequence q ON q.seqrelid=s.oid
                WHERE s.oid=pg_catalog.to_regclass('public.document_readiness_observations_observation_sequence_seq')""").fetchone()
            return yes(row)
        except (Error,TypeError,ValueError,KeyError,AttributeError):return False

    @staticmethod
    def _notification_storage_is_protected(connection) -> bool:
        from .notification_postgres import notification_storage_is_protected
        return notification_storage_is_protected(connection)

    @staticmethod
    def _organization_storage_is_protected(connection) -> bool:
        from .postgres_adapters import organization_management_storage_is_protected
        return organization_management_storage_is_protected(connection)

    def context_is_clear(self) -> bool:
        try:
            self._ensure_pool_open()
            with self._pool.connection(timeout=5.0) as connection:
                row = connection.execute(
                    "SELECT nullif(current_setting('app.tenant_id', true), '') AS tenant_id, "
                    "nullif(current_setting('app.workspace_id', true), '') AS workspace_id"
                ).fetchone()
            return row is not None and row[0] is None and row[1] is None
        except PoolTimeout:
            raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True) from None
        except Error as error:
            raise classify_database_error(error.sqlstate) from None

    def seed_development_scope(self, context: CloudAccessContext) -> None:
        """Seed only the identity/workspace rows needed by the dev bypass.

        Development QA may run against a legacy database whose egress RLS/FK
        policy bootstrap is not repairable by the application role. Notebook
        creation itself only needs the core scope rows; keep the full
        ``seed_scope`` contract unchanged for normal flows.
        """
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.tenant_id),
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id, workspace_id, display_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.workspace_id),
            )
            connection.execute(
                "INSERT INTO user_accounts (tenant_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.actor_id),
            )
            connection.execute(
                "INSERT INTO memberships (tenant_id, workspace_id, user_id, role) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.actor_id, "member"),
            )

    def seed_scope(self, context: CloudAccessContext) -> None:
        with self._transaction(context) as connection:
            canonical_text, digest = _DEFAULT_DENY_CANONICAL_TEXT, _DEFAULT_DENY_DIGEST
            if getattr(self, "_development_egress_defaults", False) is True:
                from .development_egress_defaults import registered_development_payload
                payload = registered_development_payload(connection)
                if payload is not None:
                    canonical_text, digest = payload.canonical_text, payload.digest_sha256
            connection.execute(
                "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.tenant_id),
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id, workspace_id, display_name) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.workspace_id),
            )
            connection.execute(
                "INSERT INTO user_accounts (tenant_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.actor_id),
            )
            connection.execute(
                "INSERT INTO memberships (tenant_id, workspace_id, user_id, role) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (context.tenant_id, context.workspace_id, context.actor_id, "member"),
            )
            connection.execute(
                "INSERT INTO egress_policy_versions "
                "(tenant_id, organization_id, workspace_id, policy_version_id, scope_type, "
                "policy_version, state, canonical_json, canonical_text, digest_sha256, created_by, trace_id) "
                "SELECT %s, %s, NULL, 'egress-backfill-policy:' || md5(%s || ':organization'), "
                "'organization', 1, 'active', %s::jsonb, %s, %s, 'runtime:seed_scope', "
                "'runtime:seed_scope:' || md5(%s || ':organization') "
                "WHERE NOT EXISTS (SELECT 1 FROM egress_policy_bindings AS binding "
                "WHERE binding.tenant_id = %s AND binding.organization_id = %s "
                "AND binding.workspace_id IS NULL AND binding.scope_type = 'organization' "
                "AND binding.current) ON CONFLICT DO NOTHING",
                (
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                    canonical_text,
                    canonical_text,
                    digest,
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                ),
            )
            connection.execute(
                "INSERT INTO egress_policy_bindings "
                "(tenant_id, organization_id, workspace_id, binding_id, scope_type, "
                "policy_version_id, binding_version, active, current, created_by, trace_id) "
                "SELECT %s, %s, NULL, 'egress-backfill-binding:' || md5(%s || ':organization'), "
                "'organization', 'egress-backfill-policy:' || md5(%s || ':organization'), "
                "1, true, true, 'runtime:seed_scope', "
                "'runtime:seed_scope:' || md5(%s || ':organization') "
                "WHERE NOT EXISTS (SELECT 1 FROM egress_policy_bindings AS binding "
                "WHERE binding.tenant_id = %s AND binding.organization_id = %s "
                "AND binding.workspace_id IS NULL AND binding.scope_type = 'organization' "
                "AND binding.current) ON CONFLICT DO NOTHING",
                (
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                    context.tenant_id,
                ),
            )
            connection.execute(
                "INSERT INTO egress_policy_versions "
                "(tenant_id, organization_id, workspace_id, policy_version_id, scope_type, "
                "policy_version, state, canonical_json, canonical_text, digest_sha256, created_by, trace_id) "
                "SELECT %s, %s, %s, 'egress-backfill-policy:' || md5(%s || ':' || %s), "
                "'workspace', 1, 'active', %s::jsonb, %s, %s, 'runtime:seed_scope', "
                "'runtime:seed_scope:' || md5(%s || ':' || %s) "
                "WHERE NOT EXISTS (SELECT 1 FROM egress_policy_bindings AS binding "
                "WHERE binding.tenant_id = %s AND binding.organization_id = %s "
                "AND binding.workspace_id = %s AND binding.scope_type = 'workspace' "
                "AND binding.current) ON CONFLICT DO NOTHING",
                (
                    context.tenant_id,
                    context.tenant_id,
                    context.workspace_id,
                    context.tenant_id,
                    context.workspace_id,
                    canonical_text,
                    canonical_text,
                    digest,
                    context.tenant_id,
                    context.workspace_id,
                    context.tenant_id,
                    context.tenant_id,
                    context.workspace_id,
                ),
            )
            connection.execute(
                "INSERT INTO egress_policy_bindings "
                "(tenant_id, organization_id, workspace_id, binding_id, scope_type, "
                "policy_version_id, binding_version, active, current, created_by, trace_id) "
                "SELECT %s, %s, %s, 'egress-backfill-binding:' || md5(%s || ':' || %s), "
                "'workspace', 'egress-backfill-policy:' || md5(%s || ':' || %s), "
                "1, true, true, 'runtime:seed_scope', "
                "'runtime:seed_scope:' || md5(%s || ':' || %s) "
                "WHERE NOT EXISTS (SELECT 1 FROM egress_policy_bindings AS binding "
                "WHERE binding.tenant_id = %s AND binding.organization_id = %s "
                "AND binding.workspace_id = %s AND binding.scope_type = 'workspace' "
                "AND binding.current) ON CONFLICT DO NOTHING",
                (
                    context.tenant_id,
                    context.tenant_id,
                    context.workspace_id,
                    context.tenant_id,
                    context.workspace_id,
                    context.tenant_id,
                    context.workspace_id,
                    context.tenant_id,
                    context.workspace_id,
                    context.tenant_id,
                    context.tenant_id,
                    context.workspace_id,
                ),
            )

    def put_vector(self, context: CloudAccessContext, vector_id: str, embedding: Sequence[float]) -> None:
        if len(embedding) != 3 or not _SAFE_SCOPE.fullmatch(vector_id):
            raise ValueError("VECTOR_INPUT_INVALID")
        literal = "[" + ",".join(str(float(value)) for value in embedding) + "]"
        with self._transaction(context) as connection:
            connection.execute(
                "INSERT INTO vector_entries (tenant_id, workspace_id, vector_id, embedding) "
                "VALUES (%s, %s, %s, %s::vector) "
                "ON CONFLICT (tenant_id, workspace_id, vector_id) DO UPDATE SET embedding = EXCLUDED.embedding",
                (context.tenant_id, context.workspace_id, vector_id, literal),
            )

    def get_vector(self, context: CloudAccessContext, vector_id: str) -> tuple[float, ...] | None:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT embedding::text AS embedding FROM vector_entries WHERE vector_id = %s",
                (vector_id,),
            ).fetchone()
        if row is None:
            return None
        return tuple(float(value) for value in str(row[0]).strip("[]").split(","))

    def create_notification(
        self, context: CloudAccessContext, notification_id: str, source_event_id: str
    ) -> CloudNotification:
        with self._transaction(context) as connection:
            row = connection.execute(
                "INSERT INTO notifications "
                "(tenant_id, workspace_id, notification_id, recipient_id, source_event_id) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING notification_id, version, read_at",
                (context.tenant_id, context.workspace_id, notification_id, context.actor_id, source_event_id),
            ).fetchone()
        if row is None:
            raise CloudDatabaseError("DATABASE_UNAVAILABLE", retryable=True)
        return CloudNotification(str(row[0]), int(row[1]), cast(datetime | None, row[2]))

    def get_notification(self, context: CloudAccessContext, notification_id: str) -> CloudNotification:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT notification_id, version, read_at FROM notifications WHERE notification_id = %s",
                (notification_id,),
            ).fetchone()
        if row is None:
            raise CloudDatabaseError("DATABASE_ACCESS_DENIED", retryable=False)
        return CloudNotification(str(row[0]), int(row[1]), cast(datetime | None, row[2]))

    def mark_notification_read(
        self,
        context: CloudAccessContext,
        notification_id: str,
        expected_version: int,
        idempotency_key: str,
        *,
        force_audit_failure: bool = False,
    ) -> CloudNotification:
        fingerprint = hashlib.sha256(
            f"{notification_id}|{expected_version}|read".encode("utf-8")
        ).hexdigest()
        operation = "notification.read"
        with self._transaction(context) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{operation}|{idempotency_key}",),
            )
            replay = connection.execute(
                "SELECT request_fingerprint, result FROM idempotency_records "
                "WHERE workspace_id = %s AND actor_id = %s AND operation = %s AND idempotency_key = %s",
                (context.workspace_id, context.actor_id, operation, idempotency_key),
            ).fetchone()
            if replay is not None:
                if replay[0] != fingerprint:
                    raise CloudDatabaseError("IDEMPOTENCY_CONFLICT", retryable=False)
                result = cast(Mapping[str, Any], replay[1])
                return CloudNotification(
                    str(result["notification_id"]), int(result["version"]),
                    datetime.fromisoformat(str(result["read_at"])),
                )
            row = connection.execute(
                "UPDATE notifications SET read_at = now(), version = version + 1 "
                "WHERE notification_id = %s AND recipient_id = %s AND version = %s AND read_at IS NULL "
                "RETURNING notification_id, version, read_at",
                (notification_id, context.actor_id, expected_version),
            ).fetchone()
            if row is None:
                raise CloudDatabaseError("VERSION_CONFLICT", retryable=False)
            if force_audit_failure:
                connection.execute(
                    "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, "
                    "target_type, target_id, outcome, trace_id, policy_version, metadata) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (None, context.tenant_id, context.workspace_id, context.actor_id, operation,
                     "notification", notification_id, "succeeded", "forced-failure", "test", "{}"),
                )
            event_id = "audit-" + hashlib.sha256(
                f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}".encode("utf-8")
            ).hexdigest()[:24]
            connection.execute(
                "INSERT INTO audit_events (event_id, tenant_id, workspace_id, actor_id, action, "
                "target_type, target_id, outcome, trace_id, policy_version, after_value, metadata) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (event_id, context.tenant_id, context.workspace_id, context.actor_id, operation,
                 "notification", notification_id, "succeeded", event_id, "cloud-policy-v1",
                 json.dumps({"version": int(row[1])}), json.dumps({"idempotency_key_hash": hashlib.sha256(idempotency_key.encode()).hexdigest()})),
            )
            result = {
                "notification_id": str(row[0]),
                "version": int(row[1]),
                "read_at": cast(datetime, row[2]).isoformat(),
            }
            connection.execute(
                "INSERT INTO idempotency_records (tenant_id, workspace_id, actor_id, operation, "
                "idempotency_key, request_fingerprint, result, status, expires_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'completed', %s)",
                (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key,
                 fingerprint, json.dumps(result), datetime.now(timezone.utc) + timedelta(hours=24)),
            )
            return CloudNotification(result["notification_id"], result["version"], cast(datetime, row[2]))

    def audit_count(self, context: CloudAccessContext, action: str) -> int:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM audit_events WHERE action = %s", (action,)
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def idempotency_count(self, context: CloudAccessContext, idempotency_key: str) -> int:
        with self._transaction(context) as connection:
            row = connection.execute(
                "SELECT count(*) AS count FROM idempotency_records "
                "WHERE workspace_id = %s AND actor_id = %s AND idempotency_key = %s",
                (context.workspace_id, context.actor_id, idempotency_key),
            ).fetchone()
        return int(row[0]) if row is not None else 0
