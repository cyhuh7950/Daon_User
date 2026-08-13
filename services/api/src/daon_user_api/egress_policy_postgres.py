"""PostgreSQL repository for versioned egress policies and bindings."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Mapping, cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .egress_policy import (
    EgressPolicyBindingView,
    EgressPolicyContext,
    EgressPolicyError,
    EgressPolicyPayload,
)


class PostgresEgressPolicyRepository:
    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _cloud_context(context: EgressPolicyContext, capability: str) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, capability,
        )

    @staticmethod
    def _workspace(context: EgressPolicyContext, scope_type: str) -> str | None:
        if scope_type == "organization":
            return None
        if scope_type == "workspace":
            return context.workspace_id
        raise EgressPolicyError("EGRESS_POLICY_SCOPE_INVALID")

    @staticmethod
    def _payload(value: Mapping[str, Any]) -> EgressPolicyPayload:
        try:
            return EgressPolicyPayload(
                mode=str(value["mode"]),
                allowed_provider_kinds=tuple(str(item) for item in value["allowed_provider_kinds"]),
                allowed_destinations=tuple(str(item) for item in value["allowed_destinations"]),
                classification=str(value["classification"]),
                max_bytes=int(value["max_bytes"]),
                masking_required=cast(bool, value["masking_required"]),
                redaction_required=cast(bool, value["redaction_required"]),
                required_approver=str(value["required_approver"]),
            )
        except (KeyError, TypeError, ValueError):
            raise EgressPolicyError("EGRESS_POLICY_STALE", 503) from None

    @classmethod
    def _view(cls, row: tuple[Any, ...] | None) -> EgressPolicyBindingView:
        if row is None:
            raise EgressPolicyError("EGRESS_POLICY_UNAVAILABLE", 503)
        return EgressPolicyBindingView(
            tenant_id=str(row[0]), organization_id=str(row[1]),
            workspace_id=None if row[2] is None else str(row[2]), scope_type=str(row[3]),
            policy_version_id=str(row[4]), policy_version=int(row[5]), policy_state=str(row[6]),
            binding_id=str(row[7]), binding_version=int(row[8]), active=bool(row[9]),
            current=bool(row[10]), payload=cls._payload(cast(Mapping[str, Any], row[11])),
        )

    @staticmethod
    def _select_current(
        connection: Connection[tuple[Any, ...]], context: EgressPolicyContext,
        scope_type: str, workspace_id: str | None, *, for_update: bool = False,
    ) -> tuple[Any, ...] | None:
        suffix = " FOR UPDATE" if for_update else ""
        return connection.execute(
            "SELECT binding.tenant_id,binding.organization_id,binding.workspace_id,"
            "binding.scope_type,policy.policy_version_id,policy.policy_version,policy.state,"
            "binding.binding_id,binding.binding_version,binding.active,binding.current,"
            "policy.canonical_json FROM egress_policy_bindings AS binding "
            "JOIN egress_policy_versions AS policy ON policy.tenant_id=binding.tenant_id "
            "AND policy.policy_version_id=binding.policy_version_id "
            "WHERE binding.organization_id=%s AND binding.scope_type=%s "
            "AND binding.workspace_id IS NOT DISTINCT FROM %s AND binding.current=true" + suffix,
            (context.organization_id, scope_type, workspace_id),
        ).fetchone()

    @staticmethod
    def _select_ids(
        connection: Connection[tuple[Any, ...]], policy_version_id: str, binding_id: str,
    ) -> tuple[Any, ...] | None:
        return connection.execute(
            "SELECT binding.tenant_id,binding.organization_id,binding.workspace_id,"
            "binding.scope_type,policy.policy_version_id,policy.policy_version,policy.state,"
            "binding.binding_id,binding.binding_version,binding.active,binding.current,"
            "policy.canonical_json FROM egress_policy_bindings AS binding "
            "JOIN egress_policy_versions AS policy ON policy.tenant_id=binding.tenant_id "
            "AND policy.policy_version_id=binding.policy_version_id "
            "WHERE binding.policy_version_id=%s AND binding.binding_id=%s",
            (policy_version_id, binding_id),
        ).fetchone()

    def current(self, context: EgressPolicyContext, scope_type: str) -> EgressPolicyBindingView:
        workspace_id = self._workspace(context, scope_type)
        cloud_context = self._cloud_context(context, "egress_policy.read")
        with self._store._transaction(cloud_context) as connection:
            row = self._select_current(connection, context, scope_type, workspace_id)
        return self._view(row)

    @staticmethod
    def _identifier(prefix: str, *parts: str) -> str:
        return prefix + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]

    def create_and_activate(
        self, context: EgressPolicyContext, scope_type: str, payload: EgressPolicyPayload,
        expected_etag: str, idempotency_key: str,
    ) -> EgressPolicyBindingView:
        if not idempotency_key or len(idempotency_key) > 255:
            raise EgressPolicyError("IDEMPOTENCY_KEY_INVALID")
        workspace_id = self._workspace(context, scope_type)
        scope_key = context.organization_id if scope_type == "organization" else context.workspace_id
        operation = f"egress_policy.{scope_type}.activate"
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "scope_type": scope_type, "payload": payload.as_dict(), "expected_etag": expected_etag,
        })).hexdigest()
        cloud_context = self._cloud_context(context, "egress_policy.write")
        with self._store._transaction(cloud_context) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{context.tenant_id}|{scope_key}|{context.actor_id}|{operation}|{idempotency_key}",),
            )
            replay = connection.execute(
                "SELECT request_fingerprint,result FROM idempotency_records "
                "WHERE workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
                (context.workspace_id, context.actor_id, operation, idempotency_key),
            ).fetchone()
            if replay is not None:
                if str(replay[0]) != fingerprint:
                    raise EgressPolicyError("IDEMPOTENCY_KEY_REUSED", 409)
                result = cast(Mapping[str, Any], replay[1])
                return self._view(self._select_ids(
                    connection, str(result["policy_version_id"]), str(result["binding_id"]),
                ))

            current = self._view(self._select_current(
                connection, context, scope_type, workspace_id, for_update=True,
            ))
            if current.etag != expected_etag:
                raise EgressPolicyError("VERSION_CONFLICT", 409)

            policy_version = current.policy_version + 1
            binding_version = current.binding_version + 1
            policy_version_id = self._identifier(
                "egress-policy-", context.tenant_id, scope_key, scope_type,
                str(policy_version), payload.digest_sha256,
            )
            binding_id = self._identifier(
                "egress-binding-", context.tenant_id, scope_key,
                scope_type, str(binding_version), policy_version_id,
            )
            connection.execute(
                "INSERT INTO egress_policy_versions "
                "(tenant_id,organization_id,workspace_id,policy_version_id,scope_type,"
                "policy_version,state,canonical_json,canonical_text,digest_sha256,created_by,trace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,'active',%s,%s,%s,%s,%s)",
                (context.tenant_id, context.organization_id, workspace_id, policy_version_id,
                 scope_type, policy_version, Jsonb(payload.as_dict()), payload.canonical_text,
                 payload.digest_sha256, context.actor_id, context.trace_id),
            )
            connection.execute(
                "UPDATE egress_policy_bindings SET active=false,current=false "
                "WHERE binding_id=%s AND active=true AND current=true",
                (current.binding_id,),
            )
            row = connection.execute(
                "INSERT INTO egress_policy_bindings "
                "(tenant_id,organization_id,workspace_id,binding_id,scope_type,policy_version_id,"
                "binding_version,active,current,created_by,trace_id) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,true,true,%s,%s) "
                "RETURNING tenant_id,organization_id,workspace_id,scope_type,policy_version_id,"
                "%s,'active',binding_id,binding_version,active,current,%s",
                (context.tenant_id, context.organization_id, workspace_id, binding_id, scope_type,
                 policy_version_id, binding_version, context.actor_id, context.trace_id,
                 policy_version, Jsonb(payload.as_dict())),
            ).fetchone()
            stored = self._view(row)
            result = {"policy_version_id": policy_version_id, "binding_id": binding_id}
            connection.execute(
                "INSERT INTO idempotency_records "
                "(tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,"
                "result,status,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
                (context.tenant_id, context.workspace_id, context.actor_id, operation,
                 idempotency_key, fingerprint, Jsonb(result),
                 datetime.now(timezone.utc) + timedelta(hours=24)),
            )
            self._audit(connection, context, "egress_policy.activate", binding_id, "succeeded", {
                "scope_type": scope_type, "policy_version_id": policy_version_id,
                "binding_version": binding_version,
            })
            return stored

    @staticmethod
    def _audit(
        connection: Connection[tuple[Any, ...]], context: EgressPolicyContext,
        action: str, target_id: str, outcome: str, metadata: Mapping[str, Any],
    ) -> None:
        event_id = PostgresEgressPolicyRepository._identifier(
            "audit-egress-", context.tenant_id, context.workspace_id,
            context.actor_id, action, context.trace_id, target_id, outcome,
        )
        connection.execute(
            "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,"
            "target_type,target_id,outcome,trace_id,policy_version,metadata) "
            "VALUES (%s,%s,%s,%s,%s,'EgressPolicyBinding',%s,%s,%s,%s,%s)",
            (event_id, context.tenant_id, context.workspace_id, context.actor_id, action,
             target_id, outcome, context.trace_id, context.authorization_policy_version,
             Jsonb(dict(metadata))),
        )

    def record_denial(self, context: EgressPolicyContext, action: str, code: str) -> None:
        cloud_context = self._cloud_context(context, "egress_policy.write")
        with self._store._transaction(cloud_context) as connection:
            self._audit(connection, context, action, context.workspace_id, "denied", {
                "safe_error_code": code,
            })
