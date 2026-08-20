from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError
from .data_canon import canonical_json_bytes
from .egress_policy import EgressPolicyPayload, resolve_effective_payload
from .studio_export import export_studio_output
from .studio_workspace import StudioContext, StudioError, StudioGenerationRequest, build_structured_output, FORMATS
from .object_queue import ObjectKeyPolicy, ObjectQueueError, ObjectStorageError


class PostgresStudioWorkspaceRepository:
    def __init__(self, cloud_store, object_storage=None, creation_enforcer=None) -> None:  # type: ignore[no-untyped-def]
        self._cloud_store = cloud_store
        self._object_storage = object_storage
        self._creation_enforcer = creation_enforcer

    @property
    def creation_license_authoritative(self) -> bool:
        return self._creation_enforcer is not None

    @staticmethod
    def _policy_projection(connection, context: StudioContext, *, run_id: str | None = None) -> dict[str, object]:
        row = connection.execute(
            "SELECT (SELECT canonical_json FROM workspace_policies WHERE tenant_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1),(SELECT canonical_json FROM ruleset_bindings WHERE tenant_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1),(SELECT canonical_json FROM weight_profiles WHERE tenant_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1),(SELECT canonical_json FROM knowledge_scopes WHERE tenant_id=%s AND workspace_id=%s ORDER BY version DESC LIMIT 1),(SELECT jsonb_build_object('active',ob.active AND wb.active,'current',ob.current AND wb.current,'workspace_id',%s::text,'version',greatest(ob.binding_version,wb.binding_version),'decision',CASE WHEN op.canonical_json->>'mode'='deny_external' OR wp.canonical_json->>'mode'='deny_external' THEN 'deny_external' ELSE 'allow_approved_external' END,'organization_policy_version_id',op.policy_version_id,'organization_binding_id',ob.binding_id,'workspace_policy_version_id',wp.policy_version_id,'workspace_binding_id',wb.binding_id,'organization_policy',op.canonical_json,'workspace_policy',wp.canonical_json) FROM egress_policy_bindings ob JOIN egress_policy_versions op ON op.tenant_id=ob.tenant_id AND op.policy_version_id=ob.policy_version_id JOIN egress_policy_bindings wb ON wb.tenant_id=ob.tenant_id AND wb.organization_id=ob.organization_id AND wb.scope_type='workspace' AND wb.workspace_id=%s AND wb.current=true JOIN egress_policy_versions wp ON wp.tenant_id=wb.tenant_id AND wp.policy_version_id=wb.policy_version_id WHERE ob.tenant_id=%s AND ob.scope_type='organization' AND ob.workspace_id IS NULL AND ob.current=true)",
            (context.tenant_id, context.workspace_id) * 4
            + (context.workspace_id, context.workspace_id, context.tenant_id),
        ).fetchone()
        raw_values = tuple(row or ())
        if len(raw_values) != 5 or any(not isinstance(item, Mapping) for item in raw_values):
            raise StudioError("POLICY_PROJECTION_UNAVAILABLE", 409)
        values = [dict(cast(Mapping[str, object], item)) for item in raw_values]
        for item in values:
            if item.get("active") is not True or item.get("current") is not True or item.get("workspace_id") != context.workspace_id or not isinstance(item.get("version"), int) or int(item["version"]) < 1:
                raise StudioError("POLICY_PROJECTION_UNAVAILABLE", 409)
        policy, ruleset, weight, scope, egress = values
        try:
            organization_egress = EgressPolicyPayload(**{
                **cast(dict[str, object], egress["organization_policy"]),
                "allowed_provider_kinds": tuple(cast(list[str], cast(dict[str, object], egress["organization_policy"])["allowed_provider_kinds"])),
                "allowed_destinations": tuple(cast(list[str], cast(dict[str, object], egress["organization_policy"])["allowed_destinations"])),
            })
            workspace_egress = EgressPolicyPayload(**{
                **cast(dict[str, object], egress["workspace_policy"]),
                "allowed_provider_kinds": tuple(cast(list[str], cast(dict[str, object], egress["workspace_policy"])["allowed_provider_kinds"])),
                "allowed_destinations": tuple(cast(list[str], cast(dict[str, object], egress["workspace_policy"])["allowed_destinations"])),
            })
            effective_egress, parent_locked = resolve_effective_payload(organization_egress, workspace_egress)
            egress["effective_policy"] = effective_egress.as_dict()
            egress["parent_locked"] = parent_locked
            egress["decision"] = effective_egress.mode
        except (KeyError, TypeError, ValueError):
            raise StudioError("POLICY_PROJECTION_UNAVAILABLE", 409) from None
        required_values = (
            policy.get("data_area"), policy.get("authority_policy"),
            ruleset.get("ruleset_version_snapshot_id") or ruleset.get("ruleset_version_id"),
            weight.get("profile"), scope.get("scope"), egress.get("decision"),
            egress.get("organization_policy_version_id"),
            egress.get("organization_binding_id"),
            egress.get("workspace_policy_version_id"),
            egress.get("workspace_binding_id"),
        )
        if any(not isinstance(value, str) or not value.strip() for value in required_values):
            raise StudioError("POLICY_PROJECTION_UNAVAILABLE", 409)
        originating_run: dict[str, object] | None = None
        if run_id is not None:
            run_row = connection.execute(
                "SELECT r.canonical_json,ed.record_id,ed.canonical_json,rd.record_id,rd.canonical_json "
                "FROM runs r JOIN egress_decisions ed ON ed.tenant_id=r.tenant_id AND ed.workspace_id=r.workspace_id AND ed.run_id=r.record_id "
                "JOIN routing_decisions rd ON rd.tenant_id=r.tenant_id AND rd.workspace_id=r.workspace_id AND rd.run_id=r.record_id AND rd.egress_decision_id=ed.record_id "
                "WHERE r.tenant_id=%s AND r.workspace_id=%s AND r.record_id=%s",
                (context.tenant_id, context.workspace_id, run_id),
            ).fetchone()
            if run_row is None or len(run_row) != 5:
                raise StudioError("ORIGINATING_RUN_POLICY_UNAVAILABLE", 409)
            run_payload, egress_id, egress_payload, routing_id, routing_payload = run_row
            if not all(isinstance(item, Mapping) for item in (run_payload, egress_payload, routing_payload)):
                raise StudioError("ORIGINATING_RUN_POLICY_UNAVAILABLE", 409)
            frozen = cast(Mapping[str, object], cast(Mapping[str, object], run_payload).get("frozen_routing_context"))
            expected_frozen = {
                "organization_policy_version_id": egress["organization_policy_version_id"],
                "organization_binding_id": egress["organization_binding_id"],
                "workspace_policy_version_id": egress["workspace_policy_version_id"],
                "workspace_binding_id": egress["workspace_binding_id"],
                **effective_egress.as_dict(),
            }
            expected_fingerprint = "sha256:" + hashlib.sha256(canonical_json_bytes(expected_frozen)).hexdigest()
            if (
                not isinstance(frozen, Mapping)
                or any(frozen.get(key) != value for key, value in expected_frozen.items())
                or frozen.get("fingerprint") != expected_fingerprint
                or cast(Mapping[str, object], egress_payload).get("run_id") != run_id
                or cast(Mapping[str, object], egress_payload).get("frozen_routing_context") != frozen
                or cast(Mapping[str, object], routing_payload).get("run_id") != run_id
                or cast(Mapping[str, object], routing_payload).get("egress_decision_id") != str(egress_id)
            ):
                raise StudioError("ORIGINATING_RUN_POLICY_MISMATCH", 409)
            routing_payload_map = cast(Mapping[str, object], routing_payload)
            selected_deployment_id = routing_payload_map.get("selected_deployment_id")
            if not isinstance(selected_deployment_id, str) or not selected_deployment_id:
                raise StudioError("ORIGINATING_RUN_MODEL_UNAVAILABLE", 409)
            model_row = connection.execute(
                "SELECT pp.canonical_json,md.canonical_json,ma.canonical_json "
                "FROM model_deployments md "
                "JOIN provider_profiles pp ON pp.tenant_id=md.tenant_id "
                "AND pp.workspace_id=md.workspace_id AND pp.record_id=md.provider_profile_id "
                "JOIN model_artifacts ma ON ma.tenant_id=md.tenant_id "
                "AND ma.workspace_id=md.workspace_id AND ma.record_id=md.model_artifact_id "
                "WHERE md.tenant_id=%s AND md.workspace_id=%s AND md.record_id=%s",
                (context.tenant_id, context.workspace_id, selected_deployment_id),
            ).fetchone()
            if (
                model_row is None or len(model_row) != 3
                or any(not isinstance(item, Mapping) for item in model_row)
            ):
                raise StudioError("ORIGINATING_RUN_MODEL_UNAVAILABLE", 409)
            profile_payload = cast(Mapping[str, object], model_row[0])
            deployment_payload = cast(Mapping[str, object], model_row[1])
            artifact_payload = cast(Mapping[str, object], model_row[2])
            provider_code = profile_payload.get("provider_code")
            model_id = deployment_payload.get("model_id")
            binding_version = deployment_payload.get("binding_version")
            if (
                not isinstance(provider_code, str) or not provider_code
                or provider_code != artifact_payload.get("provider_code")
                or not isinstance(model_id, str) or not model_id
                or model_id != artifact_payload.get("model_id")
                or not isinstance(binding_version, int) or binding_version < 1
                or binding_version != profile_payload.get("binding_version")
                or not isinstance(profile_payload.get("configured_profile_id"), str)
                or not isinstance(deployment_payload.get("configured_deployment_id"), str)
            ):
                raise StudioError("ORIGINATING_RUN_MODEL_UNAVAILABLE", 409)
            model_selection = {
                "provider_code": provider_code,
                "provider_kind": (
                    "server_internal" if provider_code == "OLLAMA" else "external_api"
                ),
                "profile_id": profile_payload["configured_profile_id"],
                "deployment_id": deployment_payload["configured_deployment_id"],
                "deployment_record_id": selected_deployment_id,
                "model_id": model_id,
                "binding_version": binding_version,
                "routing_decision_id": str(routing_id),
            }
            originating_run = {
                "run_id": run_id,
                "egress_decision_id": str(egress_id),
                "routing_decision_id": str(routing_id),
                "policy_fingerprint": expected_fingerprint,
                "frozen_routing_context": dict(frozen),
                "model_selection": model_selection,
            }
        locks = [
            {"field": "reviewCondition", "value": "review_required", "reason": "WORKSPACE_POLICY"},
            {"field": "dataArea", "value": str(policy["data_area"]), "reason": "WORKSPACE_POLICY"},
            {"field": "authorityPolicy", "value": str(policy["authority_policy"]), "reason": "WORKSPACE_POLICY"},
            {"field": "weightProfile", "value": str(weight["profile"]), "reason": "WEIGHT_PROFILE"},
            {"field": "egressPolicy", "value": str(egress["decision"]), "reason": "EGRESS_POLICY"},
        ]
        ruleset_version = ruleset.get("ruleset_version_snapshot_id") or ruleset.get("ruleset_version_id")
        locks.append({"field": "rulesetVersionId", "value": str(ruleset_version) if ruleset_version else None, "reason": "RULESET_BINDING"})
        authoritative_values = {"workspace_policy": policy, "ruleset_binding": ruleset, "weight_profile": weight, "knowledge_scope": scope, "egress_policy": egress}
        if originating_run is not None:
            authoritative_values["originating_run"] = originating_run
        return {"locks": locks, "ruleset_version_id": ruleset_version, "review_condition": "review_required", "authoritative_values": authoritative_values}

    @staticmethod
    def generation_contract_sql() -> str:
        return """
        BEGIN;
        SELECT tenant_id, workspace_id FROM source_versions WHERE tenant_id=%s AND workspace_id=%s;
        INSERT INTO generation_settings_snapshots (...);
        INSERT INTO generation_requests (...);
        INSERT INTO studio_outputs (...);
        INSERT INTO output_versions (...);
        INSERT INTO evidence_references (...);
        INSERT INTO audit_events (...);
        INSERT INTO idempotency_records (...);
        COMMIT;
        """

    @staticmethod
    def _cloud(context: StudioContext, capability: str) -> CloudAccessContext:
        return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, capability)

    @staticmethod
    def _opaque(prefix: str, *parts: str) -> str:
        return f"{prefix}-{hashlib.sha256('|'.join(parts).encode()).hexdigest()[:32]}"

    @staticmethod
    def _insert(connection, context: StudioContext, table: str, record_id: str,
                payload: Mapping[str, object], *, state: str | None = None,
                extra_columns: tuple[str, ...] = (), extra_values: tuple[object, ...] = ()) -> None:
        text = canonical_json_bytes(payload).decode()
        columns = (
            "tenant_id", "workspace_id", "record_id", "aggregate_id", "version", "schema_version",
            "canonical_json", "canonical_text", "digest_sha256", *(("state",) if state else ()),
            "created_by", "trace_id", *extra_columns,
        )
        values = (
            context.tenant_id, context.workspace_id, record_id, record_id, 1, 1, Jsonb(dict(payload)), text,
            hashlib.sha256(text.encode()).hexdigest(), *((state,) if state else ()), context.actor_id,
            context.trace_id, *extra_values,
        )
        connection.execute(
            f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join(('%s',) * len(columns))})", values,
        )

    @staticmethod
    def _transition(connection, context: StudioContext, entity: str, record_id: str,
                    version: int, target: str, transition_id: str) -> int:
        row = connection.execute(
            "SELECT state,version,outcome,error_code FROM transition_canon_state(%s,%s,%s,%s,%s,%s,%s,%s)",
            (entity, record_id, version, target, transition_id, "STUDIO_WORKSPACE", context.trace_id, context.policy_version),
        ).fetchone()
        if row is None or str(row[2]) != "succeeded" or str(row[0]) != target:
            raise StudioError("STUDIO_STATE_INVALID", 409)
        return int(row[1])

    def _replay(self, connection, context: StudioContext, operation: str, key: str, fingerprint: str):  # type: ignore[no-untyped-def]
        row = connection.execute(
            "SELECT request_fingerprint,result FROM idempotency_records WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
            (context.tenant_id, context.workspace_id, context.actor_id, operation, key),
        ).fetchone()
        if row is None:
            return None
        if str(row[0]) != fingerprint:
            raise StudioError("IDEMPOTENCY_CONFLICT", 409)
        return dict(cast(Mapping[str, object], row[1]))

    def _finish(self, connection, context: StudioContext, operation: str, key: str,
                fingerprint: str, result: Mapping[str, object], target_type: str, target_id: str) -> None:
        connection.execute(
            "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,outcome,trace_id,policy_version,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s,'succeeded',%s,%s,%s)",
            (self._opaque("audit", context.tenant_id, context.workspace_id, context.actor_id, operation, key),
             context.tenant_id, context.workspace_id, context.actor_id, operation, target_type, target_id,
             context.trace_id, context.policy_version, json.dumps({"result_id": target_id})),
        )
        connection.execute(
            "INSERT INTO idempotency_records (tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
            (context.tenant_id, context.workspace_id, context.actor_id, operation, key, fingerprint,
             json.dumps(dict(result)), datetime.now(timezone.utc) + timedelta(hours=24)),
        )

    def create_generation(self, context: StudioContext, request: StudioGenerationRequest, idempotency_key: str):
        if self._cloud_store is None:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
        request_payload = {
            "notebook_id": context.notebook_id,
            "output_type": request.output_type, "source_id": request.source_id,
            "source_version_ids": list(request.source_version_ids), "run_id": request.run_id,
            "run_result_id": request.run_result_id, "purpose": request.purpose, "audience": request.audience,
            "ruleset_version_id": request.ruleset_version_id, "length": request.length,
            "structure": request.structure, "output_format": request.output_format,
            "review_condition": request.review_condition,
        }
        fingerprint = hashlib.sha256(canonical_json_bytes(request_payload)).hexdigest()
        operation = "studio.generation.create"
        scope = (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.create")) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("|".join(scope),))
                replay = self._replay(connection, context, operation, idempotency_key, fingerprint)
                if replay is not None:
                    return replay, True
                if context.notebook_id is None:
                    raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
                source_count = connection.execute(
                    "SELECT count(*) FROM notebook_bindings WHERE tenant_id=%s AND workspace_id=%s "
                    "AND notebook_id=%s AND binding_kind='source' AND version_id=ANY(%s)",
                    (
                        context.tenant_id, context.workspace_id, context.notebook_id,
                        list(request.source_version_ids),
                    ),
                ).fetchone()
                run_bound = connection.execute(
                    "SELECT 1 FROM runs r JOIN notebook_bindings nb ON nb.tenant_id=r.tenant_id "
                    "AND nb.workspace_id=r.workspace_id AND nb.binding_kind='conversation_thread' "
                    "AND nb.record_id=r.conversation_id WHERE r.tenant_id=%s AND r.workspace_id=%s "
                    "AND r.record_id=%s AND nb.notebook_id=%s",
                    (context.tenant_id, context.workspace_id, request.run_id, context.notebook_id),
                ).fetchone()
                if source_count is None or int(source_count[0]) != len(set(request.source_version_ids)) or run_bound is None:
                    raise StudioError("NOTEBOOK_SCOPE_MISMATCH", 409)
                if self._creation_enforcer is not None:
                    self._creation_enforcer(
                        connection, context.tenant_id, "studio.generate",
                        {"generation_runs": 1, "studio_outputs": 1},
                    )
                projection = self._policy_projection(connection, context, run_id=request.run_id)
                if projection["review_condition"] != request.review_condition:
                    raise StudioError("POLICY_PROJECTION_MISMATCH", 409)
                if projection["ruleset_version_id"] and projection["ruleset_version_id"] != request.ruleset_version_id:
                    raise StudioError("POLICY_PROJECTION_MISMATCH", 409)
                lineage = connection.execute(
                    "SELECT rr.canonical_json,count(DISTINCT sv.record_id),bool_and(s.state='ready'),bool_or(s.record_id=%s) FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id AND sv.record_id=ANY(%s) JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id WHERE r.record_id=%s AND rr.record_id=%s AND r.tenant_id=%s AND r.workspace_id=%s GROUP BY rr.canonical_json",
                    (request.source_id, list(request.source_version_ids), request.run_id, request.run_result_id,
                     context.tenant_id, context.workspace_id),
                ).fetchone()
                if lineage is None or int(lineage[1]) != len(set(request.source_version_ids)) or lineage[2] is not True or lineage[3] is not True or bool(cast(Mapping[str, object], lineage[0]).get("insufficient")):
                    raise StudioError("RESOURCE_UNAVAILABLE", 404)
                citations = connection.execute(
                    "SELECT record_id,source_version_id,evidence_span_id,canonical_json FROM citations WHERE tenant_id=%s AND workspace_id=%s AND run_result_id=%s AND source_version_id=ANY(%s) ORDER BY record_id",
                    (context.tenant_id, context.workspace_id, request.run_result_id, list(request.source_version_ids)),
                ).fetchall()
                if not citations:
                    raise StudioError("EVIDENCE_REQUIRED", 409)
                if {str(row[1]) for row in citations} != set(request.source_version_ids):
                    raise StudioError("EVIDENCE_COVERAGE_INCOMPLETE", 409)
                answer = str(cast(Mapping[str, object], lineage[0]).get("answer", "")).strip()
                if not answer:
                    raise StudioError("EVIDENCE_REQUIRED", 409)
                settings_id = self._opaque("settings", *scope); generation_id = self._opaque("generation", *scope)
                output_id = self._opaque("output", *scope); version_id = self._opaque("output-version", *scope)
                settings = {key: value for key, value in request_payload.items() if key not in {"source_id", "run_id", "run_result_id"}}
                settings["server_policy_projection"] = projection
                settings["model_selection"] = cast(
                    Mapping[str, object],
                    cast(Mapping[str, object], projection["authoritative_values"])["originating_run"],
                )["model_selection"]
                self._insert(connection, context, "generation_settings_snapshots", settings_id, settings)
                self._insert(connection, context, "generation_requests", generation_id, request_payload, state="configuring", extra_columns=("generation_settings_snapshot_id",), extra_values=(settings_id,))
                generation_version = self._transition(connection, context, "GenerationRequest", generation_id, 1, "confirmed", self._opaque("transition", *scope, "confirmed"))
                self._transition(connection, context, "GenerationRequest", generation_id, generation_version, "submitted", self._opaque("transition", *scope, "submitted"))
                self._insert(connection, context, "studio_outputs", output_id, {"output_type": request.output_type, "title": request.purpose, "purpose": request.purpose}, extra_columns=("generation_request_id",), extra_values=(generation_id,))
                citation_payloads = [{"citation_id": str(row[0]), "source_version_id": str(row[1]), "evidence_span_id": str(row[2]), **dict(cast(Mapping[str, object], row[3]))} for row in citations]
                content = build_structured_output(request, answer, citation_payloads, generation_id)
                version_payload = {**request_payload, "content": content, "previous_version_id": None, "revision_type": "initial", "change_reason": "initial_generation", "approval_required": True}
                self._insert(connection, context, "output_versions", version_id, version_payload, state="generating", extra_columns=("studio_output_id", "generation_settings_snapshot_id"), extra_values=(output_id, settings_id))
                self._transition(connection, context, "OutputVersion", version_id, 1, "draft", self._opaque("transition", *scope, "draft"))
                for row in citations:
                    citation_payload = dict(cast(Mapping[str, object], row[3]))
                    evidence_id = self._opaque("evidence-reference", *scope, str(row[0]))
                    self._insert(connection, context, "evidence_references", evidence_id, {"citation_id": str(row[0]), "source_version_id": str(row[1]), "evidence_span_id": str(row[2]), **citation_payload}, extra_columns=("output_version_id", "source_version_id", "evidence_span_id"), extra_values=(version_id, str(row[1]), str(row[2])))
                connection.execute(
                    "WITH inserted AS (INSERT INTO notebook_bindings "
                    "(tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) VALUES "
                    "(%s,%s,%s,'generation_settings',%s,NULL,%s,now()),"
                    "(%s,%s,%s,'studio_output',%s,NULL,%s,now()),"
                    "(%s,%s,%s,'output_version',%s,NULL,%s,now()) ON CONFLICT DO NOTHING RETURNING 1) "
                    "INSERT INTO notebook_activities (tenant_id,workspace_id,notebook_id,sequence,activity_kind,actor_id,occurred_at) "
                    "SELECT %s,%s,%s,coalesce((SELECT max(sequence) FROM notebook_activities WHERE "
                    "tenant_id=%s AND workspace_id=%s AND notebook_id=%s),0)+1,'context_bound',%s,now() "
                    "WHERE EXISTS (SELECT 1 FROM inserted)",
                    (
                        context.tenant_id, context.workspace_id, context.notebook_id, settings_id, context.actor_id,
                        context.tenant_id, context.workspace_id, context.notebook_id, output_id, context.actor_id,
                        context.tenant_id, context.workspace_id, context.notebook_id, version_id, context.actor_id,
                        context.tenant_id, context.workspace_id, context.notebook_id,
                        context.tenant_id, context.workspace_id, context.notebook_id, context.actor_id,
                    ),
                )
                result = {"studio_output_id": output_id, "output_version_id": version_id, "output_type": request.output_type, "title": request.purpose, "status": "draft", "content": content, "settings_snapshot_id": settings_id, "citations": citation_payloads}
                self._finish(connection, context, operation, idempotency_key, fingerprint, result, "StudioOutput", output_id)
                return result, False
        except StudioError:
            raise
        except CloudDatabaseError as error:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503) from error

    def list_outputs(self, context: StudioContext):
        if self._cloud_store is None:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.read")) as connection:
                projection = self._policy_projection(connection, context)
                if context.notebook_id is None:
                    raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
                rows = connection.execute(
                    "SELECT so.record_id,so.canonical_json,ov.record_id,ov.state,ov.canonical_json FROM studio_outputs so "
                    "JOIN notebook_bindings nb ON nb.tenant_id=so.tenant_id AND nb.workspace_id=so.workspace_id "
                    "AND nb.binding_kind='studio_output' AND nb.record_id=so.record_id AND nb.notebook_id=%s "
                    "JOIN LATERAL (SELECT record_id,state,canonical_json FROM output_versions WHERE tenant_id=so.tenant_id "
                    "AND workspace_id=so.workspace_id AND studio_output_id=so.record_id ORDER BY content_version DESC LIMIT 1) ov ON true "
                    "WHERE so.tenant_id=%s AND so.workspace_id=%s ORDER BY so.created_at DESC",
                    (context.notebook_id, context.tenant_id, context.workspace_id),
                ).fetchall()
        except CloudDatabaseError as error:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503) from error
        return {"outputs": tuple({"studio_output_id": str(row[0]), "output_version_id": str(row[2]), "status": str(row[3]), **dict(cast(Mapping[str, object], row[1])), "version": dict(cast(Mapping[str, object], row[4]))} for row in rows), "studio_locks": projection["locks"]}

    def list_versions(self, context: StudioContext, output_id: str):
        if self._cloud_store is None:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.read")) as connection:
                if context.notebook_id is None:
                    raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
                rows = connection.execute(
                    "SELECT ov.record_id,ov.content_version,ov.previous_version_id,ov.state,ov.canonical_json,ov.generation_settings_snapshot_id,"
                    "COALESCE((SELECT jsonb_agg(er.canonical_json || jsonb_build_object('citation_id',er.canonical_json->>'citation_id','source_version_id',er.source_version_id,'evidence_span_id',er.evidence_span_id) ORDER BY er.record_id) FROM evidence_references er WHERE er.tenant_id=ov.tenant_id AND er.workspace_id=ov.workspace_id AND er.output_version_id=ov.record_id),'[]'::jsonb),"
                    "(SELECT record_id FROM review_requests WHERE tenant_id=ov.tenant_id AND workspace_id=ov.workspace_id AND output_version_id=ov.record_id ORDER BY created_at DESC,record_id DESC LIMIT 1),"
                    "(SELECT record_id FROM approval_requests WHERE tenant_id=ov.tenant_id AND workspace_id=ov.workspace_id AND output_version_id=ov.record_id ORDER BY created_at DESC,record_id DESC LIMIT 1),"
                    "(SELECT record_id FROM approvals WHERE tenant_id=ov.tenant_id AND workspace_id=ov.workspace_id AND output_version_id=ov.record_id AND decision='approved' ORDER BY created_at DESC,record_id DESC LIMIT 1),"
                    "(SELECT record_id FROM deliveries WHERE tenant_id=ov.tenant_id AND workspace_id=ov.workspace_id AND output_version_id=ov.record_id ORDER BY created_at DESC,record_id DESC LIMIT 1),"
                    "(SELECT record_id FROM knowledge_registrations WHERE tenant_id=ov.tenant_id AND workspace_id=ov.workspace_id AND output_version_id=ov.record_id AND state='registered' ORDER BY created_at DESC,record_id DESC LIMIT 1) "
                    "FROM output_versions ov JOIN studio_outputs so ON so.tenant_id=ov.tenant_id AND so.workspace_id=ov.workspace_id AND so.record_id=ov.studio_output_id "
                    "WHERE ov.tenant_id=%s AND ov.workspace_id=%s AND ov.studio_output_id=%s "
                    "AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=ov.tenant_id "
                    "AND nb.workspace_id=ov.workspace_id AND nb.notebook_id=%s AND nb.binding_kind='studio_output' "
                    "AND nb.record_id=ov.studio_output_id) ORDER BY ov.content_version DESC,ov.record_id DESC",
                    (context.tenant_id, context.workspace_id, output_id, context.notebook_id),
                ).fetchall()
                if not rows:
                    raise StudioError("RESOURCE_UNAVAILABLE", 404)
        except StudioError:
            raise
        except CloudDatabaseError as error:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503) from error
        result = []
        for row in rows:
            payload = dict(cast(Mapping[str, object], row[4]))
            citations = []
            for raw_citation in cast(list[Mapping[str, object]], row[6]):
                citation = dict(raw_citation)
                locator = citation.get("locator")
                if not isinstance(locator, Mapping):
                    locator = {"kind": "page", "value": str(citation.get("page", 1))}
                citations.append({
                    "citation_id": str(citation.get("citation_id", "")),
                    "source_version_id": str(citation.get("source_version_id", "")),
                    "evidence_span_id": str(citation.get("evidence_span_id", "")),
                    "origin": str(citation.get("origin", "raw_source")),
                    "locator": dict(locator),
                })
            result.append({
                "output_version_id": str(row[0]), "content_version": int(row[1]),
                "previous_version_id": str(row[2]) if row[2] is not None else None,
                "status": str(row[3]), "content": payload.get("content", ""),
                "revision_type": str(payload.get("revision_type", "initial")),
                "change_reason": str(payload.get("change_reason", "initial_generation")),
                "settings_snapshot_id": str(row[5]),
                "citations": citations,
                "review_request_id": str(row[7]) if row[7] is not None else None,
                "approval_request_id": str(row[8]) if row[8] is not None else None,
                "approval_id": str(row[9]) if row[9] is not None else None,
                "delivery_id": str(row[10]) if row[10] is not None else None,
                "knowledge_registration_id": str(row[11]) if row[11] is not None else None,
                "output_format": str(payload.get("output_format", "")),
            })
        return tuple(result)

    def create_version(self, context: StudioContext, output_id: str, revision: Mapping[str, object], idempotency_key: str):
        if self._cloud_store is None:
            raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
        operation = "studio.version.create"; fingerprint = hashlib.sha256(canonical_json_bytes(dict(revision))).hexdigest()
        scope = (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.edit")) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("|".join(scope),))
                replay = self._replay(connection, context, operation, idempotency_key, fingerprint)
                if replay is not None: return replay, True
                if context.notebook_id is None:
                    raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
                previous = connection.execute(
                    "SELECT aggregate_id,content_version,generation_settings_snapshot_id,canonical_json,state FROM output_versions "
                    "WHERE tenant_id=%s AND workspace_id=%s AND studio_output_id=%s AND record_id=%s "
                    "AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=output_versions.tenant_id "
                    "AND nb.workspace_id=output_versions.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='output_version' AND nb.record_id=output_versions.record_id)",
                    (context.tenant_id, context.workspace_id, output_id, revision["previous_version_id"], context.notebook_id),
                ).fetchone()
                if previous is None: raise StudioError("RESOURCE_UNAVAILABLE", 404)
                version_id = self._opaque("output-version", *scope)
                settings_id = str(previous[2]); generation_id = None
                generated_citations: tuple[object, ...] = ()
                generated_content: Mapping[str, object] | None = None
                if revision["revision_type"] == "user_edit":
                    generated_citations = tuple(connection.execute(
                        "SELECT record_id,source_version_id,evidence_span_id,canonical_json FROM evidence_references WHERE tenant_id=%s AND workspace_id=%s AND output_version_id=%s ORDER BY record_id",
                        (context.tenant_id, context.workspace_id, revision["previous_version_id"]),
                    ).fetchall())
                if revision["revision_type"] in {"ai_regeneration", "settings_change"}:
                    previous_payload = dict(cast(Mapping[str, object], previous[3]))
                    supplied = revision.get("settings") if revision["revision_type"] == "settings_change" else None
                    effective = dict(cast(Mapping[str, object], supplied)) if isinstance(supplied, Mapping) else {
                        key: previous_payload.get(key) for key in ("purpose", "audience", "source_version_ids", "ruleset_version_id", "length", "structure", "output_format", "review_condition")
                    }
                    generation_request = StudioGenerationRequest(
                        str(previous_payload["output_type"]), str(previous_payload["source_id"]), tuple(cast(list[str], effective["source_version_ids"])),
                        str(previous_payload["run_id"]), str(previous_payload["run_result_id"]), str(effective["purpose"]), str(effective["audience"]),
                        cast(str | None, effective.get("ruleset_version_id")), str(effective["length"]), str(effective["structure"]),
                        str(effective["output_format"]), str(effective["review_condition"]),
                    )
                    projection = self._policy_projection(connection, context, run_id=generation_request.run_id)
                    if projection["review_condition"] != generation_request.review_condition or projection["ruleset_version_id"] != generation_request.ruleset_version_id:
                        raise StudioError("POLICY_PROJECTION_MISMATCH", 409)
                    lineage = connection.execute(
                        "SELECT rr.canonical_json,count(DISTINCT sv.record_id),bool_and(s.state='ready'),bool_or(s.record_id=%s) FROM runs r JOIN run_results rr ON rr.tenant_id=r.tenant_id AND rr.workspace_id=r.workspace_id AND rr.run_id=r.record_id JOIN source_versions sv ON sv.tenant_id=r.tenant_id AND sv.workspace_id=r.workspace_id AND sv.record_id=ANY(%s) JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id WHERE r.record_id=%s AND rr.record_id=%s AND r.tenant_id=%s AND r.workspace_id=%s GROUP BY rr.canonical_json",
                        (generation_request.source_id, list(generation_request.source_version_ids),
                         generation_request.run_id, generation_request.run_result_id,
                         context.tenant_id, context.workspace_id),
                    ).fetchone()
                    generated_citations = tuple(connection.execute(
                        "SELECT record_id,source_version_id,evidence_span_id,canonical_json FROM citations WHERE tenant_id=%s AND workspace_id=%s AND run_result_id=%s AND source_version_id=ANY(%s) ORDER BY record_id",
                        (context.tenant_id, context.workspace_id, generation_request.run_result_id, list(generation_request.source_version_ids)),
                    ).fetchall())
                    if lineage is None or int(lineage[1]) != len(set(generation_request.source_version_ids)) or lineage[2] is not True or lineage[3] is not True or not generated_citations or {str(row[1]) for row in generated_citations} != set(generation_request.source_version_ids):
                        raise StudioError("EVIDENCE_COVERAGE_INCOMPLETE", 409)
                    settings_id = self._opaque("settings", *scope)
                    generation_id = self._opaque("generation", *scope)
                    settings_payload = {
                        **effective, "output_type": generation_request.output_type,
                        "revision_type": revision["revision_type"], "change_reason": revision["change_reason"],
                        "previous_settings_snapshot_id": str(previous[2]), "server_policy_projection": projection,
                        "model_selection": cast(
                            Mapping[str, object],
                            cast(Mapping[str, object], projection["authoritative_values"])["originating_run"],
                        )["model_selection"],
                    }
                    self._insert(connection, context, "generation_settings_snapshots", settings_id, settings_payload)
                    self._insert(connection, context, "generation_requests", generation_id, settings_payload, state="configuring", extra_columns=("generation_settings_snapshot_id",), extra_values=(settings_id,))
                    generation_version = self._transition(connection, context, "GenerationRequest", generation_id, 1, "confirmed", self._opaque("transition", *scope, "confirmed"))
                    self._transition(connection, context, "GenerationRequest", generation_id, generation_version, "submitted", self._opaque("transition", *scope, "submitted"))
                    answer = str(cast(Mapping[str, object], lineage[0]).get("answer", "")).strip()
                    if not answer or bool(cast(Mapping[str, object], lineage[0]).get("insufficient")): raise StudioError("EVIDENCE_REQUIRED", 409)
                    citation_payloads = [{"citation_id": str(row[0]), "source_version_id": str(row[1]), "evidence_span_id": str(row[2]), **dict(cast(Mapping[str, object], row[3]))} for row in generated_citations]
                    generated_content = build_structured_output(generation_request, answer, citation_payloads, generation_id)
                payload = {**dict(cast(Mapping[str, object], previous[3])), **dict(revision), "content": generated_content if generated_content is not None else revision["content"], "approval_required": True, **({"generation_request_id": generation_id} if generation_id else {})}
                text = canonical_json_bytes(payload).decode(); next_version = int(previous[1]) + 1
                connection.execute(
                    "INSERT INTO output_versions (tenant_id,workspace_id,record_id,aggregate_id,version,content_version,previous_version_id,schema_version,canonical_json,canonical_text,digest_sha256,state,created_by,trace_id,studio_output_id,generation_settings_snapshot_id) VALUES (%s,%s,%s,%s,1,%s,%s,1,%s,%s,%s,'generating',%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, version_id, str(previous[0]), next_version, revision["previous_version_id"], Jsonb(payload), text, hashlib.sha256(text.encode()).hexdigest(), context.actor_id, context.trace_id, output_id, settings_id),
                )
                self._transition(connection, context, "OutputVersion", version_id, 1, "draft", self._opaque("transition", *scope, "draft"))
                connection.execute(
                    "INSERT INTO notebook_bindings (tenant_id,workspace_id,notebook_id,binding_kind,record_id,version_id,created_by,created_at) "
                    "VALUES (%s,%s,%s,'output_version',%s,NULL,%s,now()) ON CONFLICT DO NOTHING",
                    (context.tenant_id, context.workspace_id, context.notebook_id, version_id, context.actor_id),
                )
                for row in generated_citations:
                    evidence_id = self._opaque("evidence-reference", *scope, str(row[0]))
                    prior_payload = dict(cast(Mapping[str, object], row[3]))
                    evidence_payload = {"citation_id": str(prior_payload.get("citation_id", row[0])), "source_version_id": str(row[1]), "evidence_span_id": str(row[2]), **prior_payload}
                    self._insert(connection, context, "evidence_references", evidence_id, evidence_payload, extra_columns=("output_version_id", "source_version_id", "evidence_span_id"), extra_values=(version_id, str(row[1]), str(row[2])))
                result = {"output_version_id": version_id, "previous_version_id": revision["previous_version_id"], "status": "draft", "content": payload["content"], "revision_type": revision["revision_type"], "change_reason": revision["change_reason"], "approval_required": True, "settings_snapshot_id": settings_id, "generation_request_id": generation_id, "resubmission_of_rejected_version": str(previous[4]) == "revision_requested"}
                self._finish(connection, context, operation, idempotency_key, fingerprint, result, "OutputVersion", version_id)
                return result, False
        except StudioError: raise
        except CloudDatabaseError as error: raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503) from error

    def record_action(self, context: StudioContext, action: str, payload: Mapping[str, object], idempotency_key: str):
        if self._cloud_store is None: raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
        operation = f"studio.{action}"; clean = {key: value for key, value in payload.items() if key != "step_up_verified"}; fingerprint = hashlib.sha256(canonical_json_bytes(clean)).hexdigest()
        scope = (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key)
        table = {"review": "review_requests", "approval_request": "approval_requests", "approval": "approvals", "delivery": "deliveries", "knowledge_registration": "knowledge_registrations"}[action]
        try:
            with self._cloud_store._transaction(self._cloud(context, operation)) as connection:
                connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", ("|".join(scope),))
                replay = self._replay(connection, context, operation, idempotency_key, fingerprint)
                if replay is not None: return replay, True
                if context.notebook_id is None:
                    raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
                version = connection.execute(
                    "SELECT state,version FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s "
                    "AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=output_versions.tenant_id "
                    "AND nb.workspace_id=output_versions.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='output_version' AND nb.record_id=output_versions.record_id)",
                    (context.tenant_id, context.workspace_id, clean["output_version_id"], context.notebook_id),
                ).fetchone()
                if version is None: raise StudioError("RESOURCE_UNAVAILABLE", 404)
                if action in {"delivery", "knowledge_registration"} and str(version[0]) != "approved": raise StudioError("APPROVAL_REQUIRED", 409)
                record_id = self._opaque(action.replace("_", "-"), *scope)
                registered_source_version_id = self._opaque("source-version", *scope) if action == "knowledge_registration" else None
                extra_columns = ("output_version_id",); extra_values: tuple[object, ...] = (clean["output_version_id"],)
                state = None
                if action == "approval_request": state = "pending"; extra_columns += ("review_request_id",); extra_values += (clean.get("review_request_id"),)
                elif action == "approval": extra_columns += ("approval_request_id", "decision"); extra_values += (clean.get("approval_request_id"), clean.get("decision", "approved"))
                elif action == "delivery": extra_columns += ("approval_id",); extra_values += (clean.get("approval_id"),)
                elif action == "knowledge_registration": state = "requested"; extra_columns += ("registered_source_version_id",); extra_values += (registered_source_version_id,)
                if action == "approval_request":
                    review = connection.execute(
                        "SELECT 1 FROM review_requests WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND output_version_id=%s",
                        (context.tenant_id, context.workspace_id, clean["review_request_id"], clean["output_version_id"]),
                    ).fetchone()
                    if review is None: raise StudioError("REVIEW_REQUEST_REQUIRED", 409)
                if action == "knowledge_registration":
                    output_payload = connection.execute(
                        "SELECT canonical_json FROM output_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s",
                        (context.tenant_id, context.workspace_id, clean["output_version_id"]),
                    ).fetchone()
                    if output_payload is None: raise StudioError("RESOURCE_UNAVAILABLE", 404)
                    source_versions = list(cast(Mapping[str, object], output_payload[0]).get("source_version_ids", []))
                    cycle = connection.execute(
                        "SELECT 1 FROM source_versions WHERE tenant_id=%s AND workspace_id=%s AND record_id=ANY(%s) AND canonical_json->>'derived_output_version_id'=%s",
                        (context.tenant_id, context.workspace_id, source_versions, clean["output_version_id"]),
                    ).fetchone()
                    if cycle is not None: raise StudioError("KNOWLEDGE_CYCLE_DETECTED", 409)
                    source_id = self._opaque("source", *scope)
                    output_version_payload = dict(cast(Mapping[str, object], output_payload[0]))
                    if "content" not in output_version_payload:
                        raise StudioError("KNOWLEDGE_CONTENT_UNAVAILABLE", 409)
                    knowledge_text = canonical_json_bytes(output_version_payload["content"]).decode("utf-8")
                    if not knowledge_text.strip():
                        raise StudioError("KNOWLEDGE_CONTENT_UNAVAILABLE", 409)
                    source_payload = {
                        "kind": "studio_output",
                        "derived_output_version_id": clean["output_version_id"],
                        "filename": f"{clean['output_version_id']}.knowledge.json",
                        "searchable": True,
                    }
                    self._insert(connection, context, "sources", source_id, source_payload, state="registered")
                    source_state_version = 1
                    for target in ("security_check", "processing", "indexing", "ready"):
                        source_state_version = self._transition(connection, context, "Source", source_id, source_state_version, target, self._opaque("transition", *scope, f"source-{target}"))
                    self._insert(connection, context, "source_versions", cast(str, registered_source_version_id), source_payload, extra_columns=("source_id",), extra_values=(source_id,))
                    chunk_id = self._opaque("knowledge-chunk", *scope)
                    evidence_span_id = self._opaque("knowledge-span", *scope)
                    evidence_payload = {
                        "source_id": source_id,
                        "source_version_id": registered_source_version_id,
                        "output_version_id": clean["output_version_id"],
                        "page": 1,
                        "text": knowledge_text,
                        "kind": "approved_knowledge_snapshot",
                    }
                    self._insert(
                        connection, context, "evidence_spans", evidence_span_id,
                        evidence_payload,
                        extra_columns=("source_version_id",),
                        extra_values=(registered_source_version_id,),
                    )
                    index_id = self._opaque("index-version", *scope)
                    self._insert(connection, context, "index_versions", index_id, {
                        "source_id": source_id,
                        "source_version_id": registered_source_version_id,
                        "strategy": "approved_knowledge_snapshot",
                        "chunks": [{
                            "chunk_id": chunk_id,
                            "source_id": source_id,
                            "source_version_id": registered_source_version_id,
                            "page": 1,
                            "text": knowledge_text,
                            "evidence_span_id": evidence_span_id,
                        }],
                        "lineage": {
                            "output_version_id": clean["output_version_id"],
                            "knowledge_registration_id": record_id,
                        },
                    }, extra_columns=("source_version_id",), extra_values=(registered_source_version_id,))
                self._insert(connection, context, table, record_id, clean, state=state, extra_columns=extra_columns, extra_values=extra_values)
                output_state = str(version[0]); output_version = int(version[1])
                if action == "review" and output_state == "draft":
                    output_version = self._transition(connection, context, "OutputVersion", clean["output_version_id"], output_version, "review_requested", self._opaque("transition", *scope, "review-requested"))
                    self._transition(connection, context, "OutputVersion", clean["output_version_id"], output_version, "in_review", self._opaque("transition", *scope, "in-review"))
                elif action == "approval":
                    approval_request = connection.execute(
                        "SELECT state,version FROM approval_requests WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND output_version_id=%s",
                        (context.tenant_id, context.workspace_id, clean["approval_request_id"], clean["output_version_id"]),
                    ).fetchone()
                    if approval_request is None or str(approval_request[0]) != "pending": raise StudioError("APPROVAL_REQUEST_REQUIRED", 409)
                    decision = str(clean["decision"])
                    self._transition(connection, context, "ApprovalRequest", clean["approval_request_id"], int(approval_request[1]), decision, self._opaque("transition", *scope, "approval-request", decision))
                    target_state = "approved" if decision == "approved" else "revision_requested"
                    self._transition(connection, context, "OutputVersion", clean["output_version_id"], output_version, target_state, self._opaque("transition", *scope, "output-version", target_state))
                elif action == "delivery":
                    approval = connection.execute(
                        "SELECT decision FROM approvals WHERE tenant_id=%s AND workspace_id=%s AND record_id=%s AND output_version_id=%s",
                        (context.tenant_id, context.workspace_id, clean["approval_id"], clean["output_version_id"]),
                    ).fetchone()
                    if approval is None or str(approval[0]) != "approved": raise StudioError("APPROVAL_REQUIRED", 409)
                    self._transition(connection, context, "OutputVersion", clean["output_version_id"], output_version, "delivered", self._opaque("transition", *scope, "delivered"))
                elif action == "knowledge_registration":
                    self._transition(connection, context, "KnowledgeRegistration", record_id, 1, "registered", self._opaque("transition", *scope, "registered"))
                result = {"record_id": record_id, "action": action, "status": "accepted", "output_version_id": clean["output_version_id"]}
                if registered_source_version_id is not None:
                    result = {**result, "status": "registered", "registered_source_version_id": registered_source_version_id, "searchable": True}
                self._finish(connection, context, operation, idempotency_key, fingerprint, result, table, record_id)
                return result, False
        except StudioError: raise
        except CloudDatabaseError as error: raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503) from error

    def export_output(self, context: StudioContext, output_id: str, version_id: str, format_name: str):
        if self._cloud_store is None: raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503)
        if context.notebook_id is None: raise StudioError("NOTEBOOK_SCOPE_REQUIRED", 400)
        try:
            with self._cloud_store._transaction(self._cloud(context, "studio.export")) as connection:
                row = connection.execute(
                    "SELECT ov.canonical_json,ov.created_at,count(er.record_id),ov.state FROM output_versions ov JOIN studio_outputs so ON so.tenant_id=ov.tenant_id AND so.workspace_id=ov.workspace_id AND so.record_id=ov.studio_output_id LEFT JOIN evidence_references er ON er.tenant_id=ov.tenant_id AND er.workspace_id=ov.workspace_id AND er.output_version_id=ov.record_id WHERE ov.tenant_id=%s AND ov.workspace_id=%s AND ov.studio_output_id=%s AND ov.record_id=%s AND EXISTS (SELECT 1 FROM notebook_bindings nb WHERE nb.tenant_id=ov.tenant_id AND nb.workspace_id=ov.workspace_id AND nb.notebook_id=%s AND nb.binding_kind='output_version' AND nb.record_id=ov.record_id) GROUP BY ov.canonical_json,ov.created_at,ov.state",
                    (context.tenant_id, context.workspace_id, output_id, version_id, context.notebook_id),
                ).fetchone()
                if row is None: raise StudioError("RESOURCE_UNAVAILABLE", 404)
                if str(row[3]) != "approved": raise StudioError("APPROVAL_REQUIRED", 409)
                payload = dict(cast(Mapping[str, object], row[0]))
                output_type = str(payload.get("output_type", ""))
                if format_name not in FORMATS.get(output_type, frozenset()): raise StudioError("EXPORT_FORMAT_UNSUPPORTED")
                exported = export_studio_output(format_name, str(payload.get("purpose", "Studio 산출물")), cast(Mapping[str, object], payload.get("content", {})), {"output_version_id": version_id, "created_at": row[1].isoformat(), "knowledge_scope": ",".join(cast(list[str], payload.get("source_version_ids", []))), "evidence_appendix": f"EvidenceReference {row[2]}건"}, output_type=output_type)
                if self._object_storage is None: raise StudioError("OBJECT_STORAGE_UNAVAILABLE", 503)
                scope = self._cloud(context, "studio.export")
                object_id = hashlib.sha256(f"{context.tenant_id}|{context.workspace_id}|{version_id}|{format_name}".encode()).hexdigest()[:32]
                keys = ObjectKeyPolicy()
                staged = self._object_storage.put_staged(keys.staging_key(scope, "output", object_id), exported.content, exported.media_type, exported.checksum_sha256)
                stored = self._object_storage.promote(staged, keys.final_key(scope, "output", object_id), expected_digest=exported.checksum_sha256, expected_size=len(exported.content), content_type=exported.media_type)
                content = self._object_storage.get(stored.key)
                if hashlib.sha256(content).hexdigest() != stored.digest_sha256 or len(content) != stored.byte_size:
                    raise StudioError("OBJECT_CHECKSUM_MISMATCH", 503)
                return type(exported)(content, exported.media_type, exported.filename, stored.digest_sha256)
        except StudioError: raise
        except (ObjectQueueError, ObjectStorageError) as error: raise StudioError("OBJECT_STORAGE_UNAVAILABLE", 503) from error
        except CloudDatabaseError as error: raise StudioError("STUDIO_DATABASE_UNAVAILABLE", 503) from error
