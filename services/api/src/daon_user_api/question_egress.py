"""Pre-provider Question Run policy freeze and decision persistence."""

from __future__ import annotations

import hashlib
import json
from typing import Mapping
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from .cloud_storage import PostgresCloudStore
from .data_canon import canonical_json_bytes
from .egress_policy import EgressPolicyContext, EgressPolicyService
from .question_answering import TextModelSelection
from .question_answering_postgres import PostgresQuestionAnsweringRepository, QuestionContext
from .question_answering_service import QuestionAnsweringError, is_general_conversation_intent
from .routing import CandidateDeployment, RoutingContext, route_single_model


class _FrozenRunMismatch(RuntimeError):
    pass


class PostgresQuestionEgressAuthorizer:
    def __init__(self, cloud_store: PostgresCloudStore, policy_service: EgressPolicyService) -> None:
        self._cloud_store = cloud_store
        self._policy_service = policy_service

    @staticmethod
    def _id(prefix: str, *values: str) -> str:
        return PostgresQuestionAnsweringRepository._opaque_id(prefix, *values)

    def prepare_payload(
        self, context: QuestionContext, provider_payload: bytes,
    ) -> bytes:
        effective = self._policy_service.get_effective(EgressPolicyContext(
            context.tenant_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version,
        ))
        if not (effective.masking_required or effective.redaction_required):
            return provider_payload
        try:
            payload = json.loads(provider_payload)
            if not isinstance(payload, dict):
                raise ValueError
            messages = payload.get("messages")
            if not isinstance(messages, list):
                raise ValueError
            user_messages = [
                item for item in messages
                if isinstance(item, dict) and item.get("role") == "user"
            ]
            if len(user_messages) != 1 or not isinstance(user_messages[0].get("content"), str):
                raise ValueError
            try:
                grounded = json.loads(user_messages[0]["content"])
            except json.JSONDecodeError:
                if not is_general_conversation_intent(user_messages[0]["content"]):
                    raise ValueError
                user_messages[0]["content"] = "[MASKED]"
                return canonical_json_bytes(payload)
            if (
                not isinstance(grounded, dict)
                or not isinstance(grounded.get("question"), str)
                or not isinstance(grounded.get("evidence"), list)
                or not grounded["evidence"]
            ):
                raise ValueError
            evidence = grounded["evidence"]
            if any(
                not isinstance(item, dict) or not isinstance(item.get("text"), str)
                for item in evidence
            ):
                raise ValueError
            grounded["question"] = "[MASKED]"
            for item in evidence:
                item["text"] = "[MASKED]"
            user_messages[0]["content"] = canonical_json_bytes(grounded).decode("utf-8")
            return canonical_json_bytes(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise QuestionAnsweringError("EGRESS_TRANSFORMATION_FAILED", status=403) from None

    def authorize(
        self, context: QuestionContext, *, run_id: str, source_id: str | None,
        source_version_id: str | None, selection: TextModelSelection, provider_payload: bytes,
        no_external_payload: bool = False,
        approved_authorization: Mapping[str, str] | None = None,
    ) -> Mapping[str, object]:
        policy_context = EgressPolicyContext(
            context.tenant_id, context.tenant_id, context.workspace_id,
            context.actor_id, context.trace_id, context.policy_version,
        )
        effective = self._policy_service.get_effective(policy_context)
        destination = (urlsplit(selection.base_url).hostname or "").casefold()
        provider_kind = selection.provider_kind
        external = provider_kind == "external_api" and not no_external_payload
        payload_bytes = len(provider_payload)
        payload_fingerprint = "sha256:" + hashlib.sha256(provider_payload).hexdigest()
        transformation_unavailable = False
        approval_exact = approved_authorization is not None and all((
            approved_authorization.get("policy_fingerprint") == effective.fingerprint,
            approved_authorization.get("provider_payload_fingerprint") == payload_fingerprint,
            approved_authorization.get("provider_kind") == provider_kind,
            approved_authorization.get("deployment_id") == selection.deployment_id,
        ))
        approver_unavailable = external and not approval_exact
        policy_allowed = (
            not external
            or (
                effective.mode == "allow_approved_external"
                and provider_kind in effective.allowed_provider_kinds
                and destination in {item.casefold() for item in effective.allowed_destinations}
                and payload_bytes <= effective.max_bytes
                and not transformation_unavailable
                and not approver_unavailable
            )
        )
        routing = route_single_model(
            RoutingContext(
                actor_id=context.actor_id, tenant_id=context.tenant_id,
                workspace_id=context.workspace_id, mode="pinned", required_role="text",
                data_realm="cloud_sync", external_egress_allowed=policy_allowed,
                policy_version=effective.fingerprint, cost_limit=0.0,
                estimated_cost=0.0, payload_bytes=payload_bytes,
            ),
            [CandidateDeployment(
                deployment_id=selection.deployment_id,
                artifact_digest=self._id("artifact-digest", selection.provider_code, selection.model_id),
                role="text", data_realm="cloud_sync", health="ready",
                provider_kind=provider_kind,
            )],
        )
        allowed = policy_allowed and routing.status == "selected"
        reason = (
            "no_external_payload" if no_external_payload and allowed
            else "approved_policy_binding" if allowed
            else "egress_transformation_unavailable" if transformation_unavailable
            else "egress_approval_required" if approver_unavailable
            else routing.code or "effective_policy_denied"
        )
        provider_id = self._id("provider", selection.profile_id, str(selection.binding_version))
        artifact_id = self._id("artifact", selection.provider_code, selection.model_id)
        deployment_id = self._id("deployment", selection.deployment_id, str(selection.binding_version))
        routing_policy_id = self._id("routing-policy", str(selection.binding_version), selection.deployment_id)
        egress_decision_id = self._id("egress", run_id, effective.fingerprint, payload_fingerprint)
        routing_decision_id = self._id("routing", run_id)
        frozen = {
            **effective.frozen_context(),
            "payload_fingerprint": payload_fingerprint,
            "payload_bytes": payload_bytes,
            "provider_kind": provider_kind,
            "destination": destination,
            "approved_request_fingerprint": None if approved_authorization is None
            else approved_authorization.get("request_fingerprint"),
        }
        cloud_context = PostgresQuestionAnsweringRepository._cloud_context(context, "question.route")
        try:
            with self._cloud_store._transaction(cloud_context) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("question-egress|" + context.tenant_id + "|" + context.workspace_id + "|" + run_id,),
                )
                existing = connection.execute(
                    "SELECT canonical_json->'frozen_routing_context' FROM runs WHERE record_id=%s",
                    (run_id,),
                ).fetchone()
                if existing is not None and existing[0] != frozen:
                    raise _FrozenRunMismatch
                if existing is not None:
                    replay = connection.execute(
                        "SELECT canonical_json->>'allowed',canonical_json->>'reason' "
                        "FROM egress_decisions WHERE record_id=%s AND run_id=%s",
                        (egress_decision_id, run_id),
                    ).fetchone()
                    if replay is None:
                        raise _FrozenRunMismatch
                    if str(replay[0]).casefold() != "true":
                        raise QuestionAnsweringError("EGRESS_POLICY_DENIED", status=403)
                    return {
                        "egress_decision_id": egress_decision_id,
                        "routing_decision_id": routing_decision_id,
                        "routing_policy_version_id": routing_policy_id,
                        "policy_fingerprint": effective.fingerprint,
                        "frozen_routing_context": frozen,
                    }
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "provider_profiles", provider_id, {
                    "configured_profile_id": selection.profile_id,
                    "provider_code": selection.provider_code,
                    "binding_version": selection.binding_version,
                })
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "model_artifacts", artifact_id, {
                    "provider_code": selection.provider_code, "model_id": selection.model_id,
                })
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "model_deployments", deployment_id, {
                    "configured_deployment_id": selection.deployment_id,
                    "model_id": selection.model_id, "role": "text",
                    "binding_version": selection.binding_version,
                }, extra_columns=("provider_profile_id", "model_artifact_id"),
                    extra_values=(provider_id, artifact_id))
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "routing_policy_versions", routing_policy_id, {
                    "binding_version": selection.binding_version,
                    "candidate_order": [deployment_id], "role": "text",
                    "egress_policy_fingerprint": effective.fingerprint,
                })
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "runs", run_id, {
                    "question_fingerprint": "sha256:" + hashlib.sha256(payload_fingerprint.encode()).hexdigest(),
                    "source_id": source_id, "source_version_id": source_version_id,
                    "frozen_routing_context": frozen,
                }, state="accepted")
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "egress_decisions", egress_decision_id, {
                    "run_id": run_id, "provider_profile_id": provider_id,
                    "allowed": allowed, "reason": reason, "frozen_routing_context": frozen,
                }, extra_columns=("run_id", "provider_profile_id"), extra_values=(run_id, provider_id))
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "routing_decisions", routing_decision_id, {
                    "run_id": run_id, "candidate_order": [deployment_id], "reason": reason,
                    "egress_decision_id": egress_decision_id,
                    "routing_status": routing.status, "routing_code": routing.code,
                    "selected_deployment_id": routing.deployment_id,
                }, extra_columns=("run_id", "routing_policy_version_id", "egress_decision_id"),
                    extra_values=(run_id, routing_policy_id, egress_decision_id))
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,"
                    "target_type,target_id,outcome,trace_id,policy_version,metadata) "
                    "VALUES (%s,%s,%s,%s,'question.egress.authorize','Run',%s,%s,%s,%s,%s)",
                    (
                        self._id("audit-egress", run_id, egress_decision_id), context.tenant_id,
                        context.workspace_id, context.actor_id, run_id,
                        "succeeded" if allowed else "denied", context.trace_id, context.policy_version,
                        Jsonb({
                            "egress_decision_id": egress_decision_id,
                            "routing_decision_id": routing_decision_id,
                            "policy_fingerprint": effective.fingerprint,
                            "safe_reason": reason,
                        }),
                    ),
                )
        except _FrozenRunMismatch:
            self._record_retry_denial(context, run_id, effective.fingerprint)
            raise QuestionAnsweringError("QUESTION_NEW_RUN_REQUIRED", status=409)
        if not allowed:
            raise QuestionAnsweringError("EGRESS_POLICY_DENIED", status=403)
        return {
            "egress_decision_id": egress_decision_id,
            "routing_decision_id": routing_decision_id,
            "routing_policy_version_id": routing_policy_id,
            "policy_fingerprint": effective.fingerprint,
            "frozen_routing_context": frozen,
        }

    def _record_retry_denial(
        self, context: QuestionContext, run_id: str, policy_fingerprint: str,
    ) -> None:
        cloud_context = PostgresQuestionAnsweringRepository._cloud_context(
            context, "question.route",
        )
        with self._cloud_store._transaction(cloud_context) as connection:
            connection.execute(
                "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,"
                "target_type,target_id,outcome,trace_id,policy_version,metadata) "
                "VALUES (%s,%s,%s,%s,'question.egress.retry_denied','Run',%s,'denied',%s,%s,%s) "
                "ON CONFLICT (event_id) DO NOTHING",
                (
                    self._id("audit-egress-retry", run_id, policy_fingerprint),
                    context.tenant_id, context.workspace_id, context.actor_id, run_id,
                    context.trace_id, context.policy_version,
                    Jsonb({"safe_error_code": "QUESTION_NEW_RUN_REQUIRED"}),
                ),
            )
