"""개발 단계의 등록 목적지 초기값과 명시 maintenance. 외부 요청·인증 우회는 수행하지 않는다."""
from __future__ import annotations

from urllib.parse import urlsplit
from typing import Mapping

from .cloud_storage import CloudAccessContext
from .egress_policy import EgressPolicyContext, EgressPolicyError, EgressPolicyPayload, EgressPolicyService
from .egress_policy_postgres import PostgresEgressPolicyRepository
from .provider_settings import ProviderSettingsError, validate_provider_base_url


def development_stage_enabled(profile: str, explicit: bool | None) -> bool:
    return explicit if explicit is not None else profile in {"test", "development"}


def development_payload(rows) -> EgressPolicyPayload | None:
    hosts = set()
    try:
        for code, adapter, url, enabled in rows:
            if enabled is not True:
                return None
            if code in {"OLLAMA", "SENTENCE_TRANSFORMERS"} or (code == "CUSTOM" and adapter == "asr_base_native"):
                continue
            normalized = validate_provider_base_url(code, url)
            host = urlsplit(normalized).hostname
            if not host:
                return None
            hosts.add(host.casefold())
        if not 1 <= len(hosts) <= 64:
            return None
        return EgressPolicyPayload("allow_approved_external", ("external_api",), tuple(sorted(hosts)),
            "internal", 65536, True, True, "organization_admin", question_step_up_required=False)
    except (ProviderSettingsError, ValueError, TypeError):
        return None


def registered_development_payload(connection) -> EgressPolicyPayload | None:
    rows = connection.execute(
        "SELECT provider_code,adapter_type,base_url,enabled FROM system_provider_connections "
        "WHERE enabled=true ORDER BY connection_id",
    ).fetchall()
    return development_payload(rows)


def transition_development_defaults(
    store, context: EgressPolicyContext, *, expected_etags: Mapping[str, str],
) -> tuple[str, ...]:
    """호출자가 정확 scope·원기록·복귀자료를 확인한 개발 maintenance 전용 경로다."""
    if getattr(store, "_development_egress_defaults", False) is not True:
        return ()
    cloud = CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, "egress_policy.read")
    with store._transaction(cloud) as connection:
        payload = registered_development_payload(connection)
    if payload is None:
        return ()
    repository = PostgresEgressPolicyRepository(store)
    service = EgressPolicyService(repository)
    deny = EgressPolicyPayload.deny_external()
    changed = []
    for scope in ("organization", "workspace"):
        workspace = None if scope == "organization" else context.workspace_id
        with store._transaction(cloud) as connection:
            row = connection.execute(
                "SELECT p.policy_version_id,b.binding_id,p.policy_version,b.binding_version,"
                "p.canonical_text,p.digest_sha256,p.created_by,b.created_by "
                "FROM egress_policy_bindings b JOIN egress_policy_versions p "
                "ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id "
                "WHERE b.tenant_id=%s AND b.organization_id=%s AND b.scope_type=%s "
                "AND b.workspace_id IS NOT DISTINCT FROM %s AND b.current=true AND b.active=true AND p.state='active'",
                (context.tenant_id, context.organization_id, scope, workspace),
            ).fetchone()
        if row is None or (row[2], row[3], row[4], row[5]) != (1, 1, deny.canonical_text, deny.digest_sha256):
            continue
        if row[6] != row[7] or row[6] not in {"runtime:seed_scope", "migration:0044"}:
            continue
        current = repository.current(context, scope)
        if (current.policy_version_id, current.binding_id) != tuple(row[:2]) or current.payload != deny:
            continue  # 조회 사이 명시 정책으로 바뀐 경우 새 ETag로 덮지 않는다.
        if expected_etags.get(scope) != current.etag:
            raise EgressPolicyError("VERSION_CONFLICT", 409)
        if scope == "workspace" and repository.current(context, "organization").payload.mode != "allow_approved_external":
            continue
        service.create_and_activate(context, scope_type=scope, payload=payload,
            expected_etag=current.etag, idempotency_key="dev-egress-default:" + scope + ":" + payload.digest_sha256)
        changed.append(scope)
    return tuple(changed)
