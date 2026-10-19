"""Pre-provider Question Run policy freeze and decision persistence."""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Mapping
from urllib.parse import urlsplit

from psycopg.types.json import Jsonb

from .cloud_storage import PostgresCloudStore
from .data_canon import canonical_json_bytes
from .egress_policy import EffectiveEgressPolicy, EgressPolicyContext, EgressPolicyService
from .question_answering import TextModelSelection
from .question_answering_postgres import (
    PostgresQuestionAnsweringRepository, QuestionContext, QuestionRepositoryError,
)
from .question_answering_service import QuestionAnsweringError, question_request_fingerprint
from .routing import CandidateDeployment, RoutingContext, route_single_model

_LOG=logging.getLogger(__name__)


class _FrozenRunMismatch(RuntimeError):
    pass


def external_question_policy_matches(
    effective: EffectiveEgressPolicy | object, *, provider_kind: str,
    destination: str, payload_bytes: int, classification: str,
    masking_required: bool, redaction_required: bool,
) -> bool:
    allowed_kinds = getattr(effective, "allowed_provider_kinds", ())
    allowed_destinations = getattr(effective, "allowed_destinations", ())
    max_bytes = getattr(effective, "max_bytes", None)
    return bool(
        getattr(effective, "mode", None) == "allow_approved_external"
        and isinstance(allowed_kinds, (tuple, list))
        and provider_kind in allowed_kinds
        and isinstance(allowed_destinations, (tuple, list))
        and destination.casefold() in {
            item.casefold() for item in allowed_destinations if isinstance(item, str)
        }
        and isinstance(max_bytes, int) and not isinstance(max_bytes, bool)
        and 0 <= payload_bytes <= max_bytes
        and getattr(effective, "classification", None) == classification
        and getattr(effective, "masking_required", None) is masking_required
        and getattr(effective, "redaction_required", None) is redaction_required
    )


class PostgresQuestionEgressAuthorizer:
    def __init__(
        self,
        cloud_store: PostgresCloudStore,
        policy_service: EgressPolicyService,
        *,
        development_auth_bypass: bool = False,
    ) -> None:
        self._cloud_store = cloud_store
        self._policy_service = policy_service
        self._development_auth_bypass = development_auth_bypass

    @staticmethod
    def _id(prefix: str, *values: str) -> str:
        return PostgresQuestionAnsweringRepository._opaque_id(prefix, *values)

    @staticmethod
    def _observation_scope(connection,context,run_id):
        from .operations_audit_scope import workspace_grant_metadata,egress_denial_correlation
        from .operations_telemetry_postgres import PostgresOperationsTelemetryRepository
        PostgresOperationsTelemetryRepository._authorize(connection,context)
        return dict(operations_scope=workspace_grant_metadata(context.workspace_id),
            deny_correlation_id=egress_denial_correlation(context.tenant_id,context.workspace_id,context.actor_id,run_id))

    def _record_early_denial(self,context,run_id):
        """선택적 관측 실패는 필수 보안 판단을 바꾸지 않는다. 원403은 호출자가 유지한다."""
        try:
            cloud_context=PostgresQuestionAnsweringRepository._cloud_context(context,"question.egress.observation")
            with self._cloud_store._transaction(cloud_context) as connection:
                metadata=self._observation_scope(connection,context,run_id)
                connection.execute("INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,"
                    "target_type,target_id,outcome,trace_id,policy_version,metadata) "
                    "VALUES (%s,%s,%s,%s,'operations.egress.denied','workspace',%s,'denied',%s,%s,%s) "
                    "ON CONFLICT (tenant_id,event_id) DO NOTHING",
                    (metadata["deny_correlation_id"],context.tenant_id,context.workspace_id,context.actor_id,
                        context.workspace_id,context.trace_id,context.policy_version,Jsonb(dict(metadata,safe_error_code="EGRESS_POLICY_DENIED"))))
        except Exception:_LOG.warning("OPERATIONS_EGRESS_OBSERVATION_UNAVAILABLE")

    @staticmethod
    def _safe_model_context(selection: TextModelSelection) -> Mapping[str, object]:
        return {
            "connection_id": selection.connection_id,
            "provider_code": selection.provider_code,
            "model_id": selection.model_id,
            "credential_version": selection.credential_version,
            "default_version": selection.binding_version,
            "catalog_version": selection.catalog_version,
            "routing_owner": selection.routing_owner,
            "daon_fallback_allowed": selection.daon_fallback_allowed,
        }

    @staticmethod
    def _selection_request(
        context: QuestionContext, *, run_id: str, question: str,
        request_fingerprint: str, request_payload: Mapping[str, object],
        idempotency_key: str, context_sources: tuple[object, ...],
        source_id: str | None, source_version_id: str | None,
    ) -> dict[str, object]:
        """원 logical body와 서버 resolved 선택을 대조한다. 질문/body/key 자체는 저장하지 않는다."""
        if (
            not isinstance(request_payload, Mapping)
            or not isinstance(idempotency_key, str) or not idempotency_key
            or not isinstance(context_sources, tuple) or len(context_sources) > 8
            or context.notebook_id is None
            or request_payload.get("notebook_id") != context.notebook_id
            or set(request_payload) - {
                "notebook_id", "question", "source_id", "source_version_id", "knowledge_context",
            }
        ):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
        logical_question = request_payload.get("question")
        if (
            not isinstance(logical_question, str)
            or not (logical_question == question or (
                logical_question == logical_question.strip() and question.strip() == logical_question
            ))
        ):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
        if question_request_fingerprint(
            context, run_id=run_id, idempotency_key=idempotency_key, request_payload=request_payload,
        ) != request_fingerprint:
            raise QuestionAnsweringError("IDEMPOTENCY_KEY_REUSED", status=409)
        knowledge = request_payload.get("knowledge_context")
        legacy_source = request_payload.get("source_id")
        legacy_version = request_payload.get("source_version_id")
        if knowledge is not None:
            if (legacy_source is not None or legacy_version is not None
                or not isinstance(knowledge, Mapping) or set(knowledge) != {"mode", "resources"}):
                raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
            mode = knowledge.get("mode")
            original = knowledge.get("resources")
            if not isinstance(original, list) or not 1 <= len(original) <= 8:
                raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
            resources = []
            for resource in original:
                if not isinstance(resource, Mapping):
                    raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
                kind = resource.get("resource_kind")
                if (kind not in {"source", "knowledge_package"}
                    or set(resource) != ({"resource_kind", "resource_id", "version_id"}
                        if kind == "source" else {"resource_kind", "resource_id"})
                    or any(not isinstance(value, str) or not value for value in resource.values())):
                    raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
                resources.append(dict(resource))
            packages = [item for item in resources if item["resource_kind"] == "knowledge_package"]
            raw = [item for item in resources if item["resource_kind"] == "source"]
            if not (
                mode == "raw_only" and raw and not packages
                or mode == "daon_priority" and packages
                or mode == "mixed" and raw and packages
            ):
                raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
        else:
            if (legacy_source is None) != (legacy_version is None):
                raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
            if legacy_source is None:
                mode, resources, packages, raw = "general_ungrounded", [], [], []
            else:
                if not all(isinstance(value, str) and value for value in (legacy_source, legacy_version)):
                    raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
                mode = "raw_only"
                resources = [{"resource_kind": "source", "resource_id": legacy_source, "version_id": legacy_version}]
                packages, raw = [], resources
        identities = [(item["resource_kind"], item["resource_id"], item.get("version_id")) for item in resources]
        if len(set(identities)) != len(identities) or len(context_sources) != len(resources):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
        # runtime의 기존 resolved 순서(지식 먼저/raw 뒤)를 보존한다. 원 resources 순서는 별도로 보존한다.
        for item, resource in zip(context_sources, packages + raw):
            if resource["resource_kind"] == "knowledge_package":
                matches = (getattr(item, "origin", None) == "daon_knowledge"
                    and getattr(item, "context_item_id", None) == resource["resource_id"])
            else:
                matches = (getattr(item, "origin", None) == "raw_source"
                    and getattr(item, "context_item_id", None) == resource["resource_id"]
                    and getattr(item, "source_id", None) == resource["resource_id"]
                    and getattr(item, "source_version_id", None) == resource["version_id"])
            if not matches:
                raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
        if ((source_id is None) != (source_version_id is None)
            or source_id is not None and (source_id, source_version_id) not in {
                (getattr(item, "source_id", None), getattr(item, "source_version_id", None)) for item in context_sources
            }):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID", status=400)
        return {"schema_version": 1, "request_fingerprint": request_fingerprint,
            "requested_mode": mode, "resources": resources}

    @staticmethod
    def _approval_matches(
        approved: Mapping[str, str] | None, selection: TextModelSelection,
        *, policy_fingerprint: str, payload_fingerprint: str,
    ) -> bool:
        return approved is not None and all((
            approved.get("policy_fingerprint") == policy_fingerprint,
            approved.get("provider_payload_fingerprint") == payload_fingerprint,
            approved.get("provider_kind") == selection.provider_kind,
            approved.get("deployment_id") == selection.deployment_id,
            selection.connection_id is None or (
                approved.get("connection_id") == selection.connection_id
                and approved.get("credential_version") == str(selection.credential_version)
                and approved.get("default_version") == str(selection.binding_version)
                and approved.get("catalog_version") == str(selection.catalog_version)
            ),
        ))

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
                # A Source-free question is a normal LLM/work consultation.
                # It has no Source evidence to redact, so preserve the actual
                # user question. Grounded JSON below remains strictly masked.
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
            # Preserve the query and evidence text for the provider.  The
            # grounded payload is the only useful context the model receives;
            # replacing the entire fields with a placeholder makes every
            # Source question unanswerable.  Sensitive-data handling belongs
            # to the document redaction pipeline, before indexing, rather
            # than destroying the retrieval context at this boundary.
            user_messages[0]["content"] = canonical_json_bytes(grounded).decode("utf-8")
            return canonical_json_bytes(payload)
        except (TypeError, ValueError, json.JSONDecodeError):
            raise QuestionAnsweringError("EGRESS_TRANSFORMATION_FAILED", status=403) from None

    def authorize(
        self, context: QuestionContext, *, run_id: str, source_id: str | None,
        source_version_id: str | None, selection: TextModelSelection, provider_payload: bytes,
        no_external_payload: bool = False,
        approved_authorization: Mapping[str, str] | None = None,
        question: str | None = None, context_mode: str | None = None,
        request_fingerprint: str | None = None,
        request_payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
        context_sources: tuple[object, ...] | None = None,
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
        development_auth_bypass = (
            approved_authorization is not None
            and approved_authorization.get("authorization_mode") == "development_auth_bypass"
        )
        approval_exact = development_auth_bypass or self._approval_matches(
            approved_authorization, selection,
            policy_fingerprint=effective.fingerprint,
            payload_fingerprint=payload_fingerprint,
        )
        approver_unavailable = external and not approval_exact and not self._development_auth_bypass
        external_scope_allowed = external_question_policy_matches(
            effective, provider_kind=provider_kind, destination=destination,
            payload_bytes=payload_bytes, classification="internal",
            masking_required=True, redaction_required=True,
        )
        if external and (
            not external_scope_allowed or transformation_unavailable or approver_unavailable
        ):
            self._record_early_denial(context,run_id)
            raise QuestionAnsweringError("EGRESS_POLICY_DENIED", status=403)
        policy_allowed = not external or external_scope_allowed
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
            **self._safe_model_context(selection),
            "destination": destination,
            "approved_request_fingerprint": None if approved_authorization is None
            else approved_authorization.get("request_fingerprint"),
            "authorization_mode": "development_auth_bypass" if development_auth_bypass
            else "step_up_approved" if approval_exact else "none",
        }
        if (
            not isinstance(question, str) or not question.strip()
            or context_mode not in {
                "raw_only", "daon_priority", "mixed", "general_ungrounded",
            }
            or not isinstance(request_fingerprint, str)
        ):
            raise QuestionAnsweringError("QUESTION_RESULT_INVALID", status=400)
        selection_request = None
        if any(value is not None for value in (request_payload, idempotency_key, context_sources)):
            selection_request = self._selection_request(
                context, run_id=run_id, question=question, request_fingerprint=request_fingerprint,
                request_payload=request_payload, idempotency_key=idempotency_key,
                context_sources=context_sources, source_id=source_id, source_version_id=source_version_id,
            )
        run_payload = PostgresQuestionAnsweringRepository._run_canonical_payload(
            question=question, source_id=source_id,
            source_version_id=source_version_id, context_mode=context_mode,
            request_fingerprint=request_fingerprint, selection=selection,
            frozen_scope=frozen,
        )
        cloud_context = PostgresQuestionAnsweringRepository._cloud_context(context, "question.route")
        try:
            with self._cloud_store._transaction(cloud_context) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                    ("question-egress|" + context.tenant_id + "|" + context.workspace_id + "|" + run_id,),
                )
                existing = connection.execute(
                    "SELECT canonical_json FROM runs WHERE record_id=%s",
                    (run_id,),
                ).fetchone()
                if existing is not None:
                    if existing[0] != run_payload:
                        raise QuestionAnsweringError(
                            "IDEMPOTENCY_KEY_REUSED", status=409,
                        )
                    replay = connection.execute(
                        "SELECT canonical_json->>'allowed',canonical_json->>'reason'"
                        + (",canonical_json" if selection_request is not None else "")
                        + " FROM egress_decisions WHERE record_id=%s AND run_id=%s",
                        (egress_decision_id, run_id),
                    ).fetchone()
                    if replay is None:
                        raise _FrozenRunMismatch
                    if str(replay[0]).casefold() != "true":
                        raise QuestionAnsweringError("EGRESS_POLICY_DENIED", status=403)
                    if selection_request is not None:
                        stored_selection = replay[2].get("access_selection")
                        if (not isinstance(stored_selection, Mapping)
                            or type(stored_selection.get("schema_version")) is not int):
                            raise QuestionAnsweringError("QUESTION_NEW_RUN_REQUIRED", status=409)
                        resolved_items = PostgresQuestionAnsweringRepository._knowledge_selections(
                            connection, context, context_sources, write=False,
                        )
                        if stored_selection != {**selection_request, "resolved_items": list(resolved_items)}:
                            raise QuestionAnsweringError("IDEMPOTENCY_KEY_REUSED", status=409)
                    return {
                        "egress_decision_id": egress_decision_id,
                        "routing_decision_id": routing_decision_id,
                        "routing_policy_version_id": routing_policy_id,
                        "policy_fingerprint": effective.fingerprint,
                        "frozen_routing_context": frozen,
                        "provider_owner": False,
                    }
                access_selection = None
                if allowed and selection_request is not None:
                    resolved_items = PostgresQuestionAnsweringRepository.bind_knowledge_selections(
                        connection, context, context_sources,
                    )
                    access_selection = {**selection_request, "resolved_items": list(resolved_items)}
                conversation_id = self._id("conversation", run_id)
                run_result_id = self._id("run-result", run_id)
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
                PostgresQuestionAnsweringRepository._insert_canon(
                    connection, context, "conversations", conversation_id,
                    {"current_run_id": run_id, "current_run_result_id": run_result_id},
                )
                PostgresQuestionAnsweringRepository._insert_canon(
                    connection, context, "runs", run_id,
                    run_payload, state="accepted", extra_columns=("conversation_id",),
                    extra_values=(conversation_id,),
                )
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "egress_decisions", egress_decision_id, {
                    "run_id": run_id, "provider_profile_id": provider_id,
                    "allowed": allowed, "reason": reason, "frozen_routing_context": frozen,
                    **({"access_selection": access_selection} if access_selection is not None else {}),
                }, extra_columns=("run_id", "provider_profile_id"), extra_values=(run_id, provider_id))
                PostgresQuestionAnsweringRepository._insert_canon(connection, context, "routing_decisions", routing_decision_id, {
                    "run_id": run_id, "candidate_order": [deployment_id], "reason": reason,
                    "egress_decision_id": egress_decision_id,
                    "routing_status": routing.status, "routing_code": routing.code,
                    "selected_deployment_id": routing.deployment_id,
                }, extra_columns=("run_id", "routing_policy_version_id", "egress_decision_id"),
                    extra_values=(run_id, routing_policy_id, egress_decision_id))
                observation={}
                if not allowed:
                    # SAVEPOINT 격리: 선택적 인가 조회 오류가 기존 필수 감사 Tx를 오염시키지 않는다.
                    try:
                        with connection.transaction():observation=self._observation_scope(connection,context,run_id)
                    except Exception:_LOG.warning("OPERATIONS_EGRESS_OBSERVATION_UNAVAILABLE")
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
                            **self._safe_model_context(selection),
                            **observation,
                        }),
                    ),
                )
        except QuestionRepositoryError as error:
            raise QuestionAnsweringError(error.code, status=error.status, retryable=error.retryable) from None
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
            "provider_owner": True,
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
