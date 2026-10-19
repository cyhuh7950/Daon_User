"""PostgreSQL/Object Storage repository for auditable document processing."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Mapping, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .document_processing import (
    DocumentProcessingContext, DocumentProcessingStatus, StoredSourceDocument, text_source_format, image_source_format, xlsx_source_format, docx_source_format, pptx_source_format, audio_source_format,
    _PROCESSING_MODE_UNSET, _reprocessing_readiness_context,
)
from .document_understanding_adapter import (
    DocumentFailureEvidence,
    DocumentUnderstandingError,
    DocumentUnderstandingResult,
)
from .object_queue import ObjectKeyPolicy, ObjectStorageError, ObjectStoragePort


_SAFE_NOTEBOOK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


class PostgresDocumentProcessingRepository:
    def __init__(self, cloud_store: PostgresCloudStore, object_storage: ObjectStoragePort, *, readiness_port=None, readiness_settings=None) -> None:
        self._cloud_store = cloud_store
        self._object_storage = object_storage
        # 미주입이면 기존 0071 실패 경로다. runtime/worker 주입은 별도 접합 단계다.
        self._readiness_port = readiness_port
        from .document_readiness import ReadinessDispatcherSettings
        self._readiness_settings = ReadinessDispatcherSettings() if readiness_settings is None else readiness_settings
        if type(self._readiness_settings) is not ReadinessDispatcherSettings:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")

    def authorize_readiness_run(self, context, source_id, processing_run_id, *, model=None, capability=None):
        """원문/모델 전 현재 자동 Run의 근거를 읽는다. 기존 수동 Run은 신규 원장 없이 통과한다."""
        from .document_readiness_postgres import _wait, PostgresDocumentReadinessRepository
        from .workspace_model_defaults import read_document_readiness_policy, ReadinessPolicySnapshot
        from .authorization import Permission, Role
        from .open_law_source_access import _CurrentAuthorizationRules, _AuthReadConnection
        safe_to_fail = False
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                saved = connection.execute(
                    "SELECT /* readiness-execution-run */ jsonb_build_object('trigger_type',pr.trigger_type,"
                    "'payload',pr.canonical_json,'actor_id',pr.created_by,'source_version_id',pr.source_version_id,"
                    "'state',pr.state,'source_state',s.state,'latest',"
                    "pr.record_id=(SELECT p.record_id FROM public.processing_runs p WHERE "
                    "(p.tenant_id,p.workspace_id,p.source_version_id)=(pr.tenant_id,pr.workspace_id,pr.source_version_id) "
                    "ORDER BY p.created_at DESC,p.record_id DESC LIMIT 1) AND NOT EXISTS("
                    "SELECT 1 FROM public.source_versions newer WHERE "
                    "(newer.tenant_id,newer.workspace_id,newer.source_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
                    "AND newer.version>sv.version)) FROM public.processing_runs pr "
                    "JOIN public.source_versions sv ON (sv.tenant_id,sv.workspace_id,sv.record_id)="
                    "(pr.tenant_id,pr.workspace_id,pr.source_version_id) JOIN public.sources s "
                    "ON (s.tenant_id,s.workspace_id,s.record_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
                    "WHERE (pr.tenant_id,pr.workspace_id,pr.record_id,s.record_id)=(%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, processing_run_id, source_id)).fetchone()
                if saved is None or type(saved[0]) is not dict:
                    raise DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)
                row = saved[0]
                if row.get("trigger_type") != "readiness_event":
                    return False
                if (row.get("actor_id") != context.actor_id or row.get("latest") is not True
                        or row.get("state") in {"completed", "failed", "policy_blocked"}
                        or row.get("source_state") != "processing"):
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                safe_to_fail = True
                version = row["source_version_id"]
                if (context.tenant_id, context.workspace_id, context.actor_id, version) not in self._readiness_settings.scopes:
                    raise DocumentUnderstandingError("READINESS_SCOPE_NOT_CONFIGURED", status=403)
                payload = row["payload"]
                proof = payload["readiness_context"]
                if (type(proof) is not dict or set(proof) != {"version", "mode", "mode_origin", "frozen_context",
                        "frozen_context_digest", "wait_processing_run_id"}
                        or type(proof["version"]) is not int or proof["version"] != 1
                        or proof["mode"] not in {"auto", "local_only"} or proof["mode_origin"] != "request"):
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                body = proof["frozen_context"]
                digest = hashlib.sha256(canonical_json_bytes(body)).hexdigest()
                if digest != proof["frozen_context_digest"] or payload["source_version_id"] != version:
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                wait_row = connection.execute("SELECT public.read_document_role_wait(%s,%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, proof["wait_processing_run_id"], body["role"])).fetchone()
                if wait_row is None or wait_row[0] is None:
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                wait = _wait(wait_row[0])
                if ((wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id, wait.source_id,
                     wait.source_version_id, wait.processing_run_id, wait.capability)
                        != (context.tenant_id, context.workspace_id, context.actor_id, payload["notebook_id"],
                            source_id, version, proof["wait_processing_run_id"], body["role"])
                        or body["mode"] != {"mode": wait.mode.mode, "origin": wait.mode.origin}
                        or (proof["mode"], proof["mode_origin"]) != (wait.mode.mode, wait.mode.origin)):
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                records = connection.execute(
                    "SELECT value FROM public.read_document_reprocess_history(%s,%s,%s,%s,%s,%s,%s,%s,%s) AS h(value)",
                    (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id, wait.source_id,
                     wait.source_version_id, wait.processing_run_id, wait.capability, digest)).fetchall()
                expected = {"tenant_id": wait.tenant_id, "workspace_id": wait.workspace_id, "actor_id": wait.actor_id,
                    "notebook_id": wait.notebook_id, "source_id": wait.source_id, "source_version_id": wait.source_version_id,
                    "processing_run_id": wait.processing_run_id, "capability_slot": wait.capability,
                    "event_id": payload["trigger_event_id"], "frozen_context_digest": digest,
                    "phase": "final", "outcome": "enqueued", "new_run_id": processing_run_id}
                matched = False
                for record in records:
                    receipt = record[0]
                    PostgresDocumentReadinessRepository._receipt(receipt)
                    if all(receipt.get(key) == value for key, value in expected.items()):
                        matched = True
                if not matched:
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                current = read_document_readiness_policy(connection, context, wait, settings=self._readiness_settings)
                if type(current) is not ReadinessPolicySnapshot:
                    raise DocumentUnderstandingError("CURRENT_ACCESS_UNAVAILABLE", status=503)
                eligibility = current.eligibility
                if (not all((eligibility.authorized, eligibility.source_current, eligibility.policy_allowed,
                             eligibility.candidate_available)) or eligibility.cost_state != "allowed"
                        or eligibility.egress_state != "allowed"
                        or (proof["mode"] == "local_only" and not eligibility.local_only_allowed)):
                    raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                if current.frozen_context_json != canonical_json_bytes(body).decode("utf-8"):
                    raise DocumentUnderstandingError("SOURCE_VERSION_CONFLICT", status=409)
                if model is not None:
                    if capability not in {"text_generation", "image_understanding", "document_parsing", "speech_to_text"}:
                        raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                    selected_current = current
                    if capability != wait.capability:
                        # 다른 역할의 현재 선택만 별도로 읽는다. 원wait/영속 FrozenContext는 바꾸지 않는다.
                        selected_current = read_document_readiness_policy(connection, context,
                            replace(wait, capability=capability), settings=self._readiness_settings)
                    if type(selected_current) is not ReadinessPolicySnapshot:
                        raise DocumentUnderstandingError("CURRENT_ACCESS_UNAVAILABLE", status=503)
                    selected = selected_current.eligibility
                    if (not all((selected.authorized, selected.source_current, selected.policy_allowed,
                                 selected.candidate_available)) or selected.cost_state != "allowed"
                            or selected.egress_state != "allowed"
                            or (proof["mode"] == "local_only" and not selected.local_only_allowed)):
                        raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                    local = model.provider_kind in {"local", "server_internal", "local_runtime"}
                    if proof["mode"] == "local_only" and not local:
                        raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                    permission = Permission.LOCAL_INTERNAL_LLM if local else Permission.EXTERNAL_LLM
                    role = Role(body["authorization"]["role"])
                    decision = _CurrentAuthorizationRules()._effective_permission(_AuthReadConnection(connection),
                        role=role, tenant_id=context.tenant_id, workspace_id=context.workspace_id,
                        permission=permission, requested=True)
                    if not decision.effective:
                        raise DocumentUnderstandingError("READINESS_ACCESS_DENIED", status=403)
                    binding = json.loads(selected_current.frozen_context_json)["binding"]
                    if ((model.connection_id, model.model_id, model.provider_code, model.provider_kind, model.base_url)
                            != (binding["connection_id"], binding["model_id"], binding["provider_code"],
                                binding["provider_kind"], binding["endpoint"])
                            or model.credential_version != binding["credential_reference"]["version"]):
                        raise DocumentUnderstandingError("SOURCE_VERSION_CONFLICT", status=409)
                return True
        except Exception as error:
            if safe_to_fail:
                # 현재 소유한 최신 Run만 기존 실패 전이로 정리한다. 신규 wait 수집은 하지 않는다.
                self._fail_readiness_guard(context, processing_run_id)
            raise self._database_error(error) from None

    def _fail_readiness_guard(self, context, processing_run_id):
        # 판정 뒤 도착한 새 Version/Run도 기존 B2b 잠금/현재 부모 재검사로 보호한다.
        # 자동 실행의 취소는 모델 가용성 실패가 아니므로 unknown·재시도 없음이다.
        self._fail(context, processing_run_id, "READINESS_ACCESS_DENIED", retryable=False,
                   evidence=DocumentFailureEvidence(None, None, "unknown"))

    def authorize_audio_source(self, context, document, processing_run_id) -> None:
        """전송 직전 기존 역할·정책과 Run/Notebook/최신 원본 결속을 다시 읽는다."""
        from .authorization import Action, Permission, Role, ROLE_ACTION_MATRIX, ROLE_PERMISSION_DEFAULTS
        try:
            with self._cloud_store._transaction(self._cloud_context(context, 'source.process')) as connection:
                row = connection.execute(
                    "SELECT CASE WHEN tr.user_id IS NOT NULL THEN CASE WHEN "
                    "(w.workspace_kind='personal' AND tr.role='personal_owner') OR "
                    "(w.workspace_kind='organization' AND tr.role='organization_admin') THEN tr.role END ELSE m.role END,"
                    "s.created_by,tp.effect,tp.locked,wp.effect,o.digest_sha256,o.byte_size,sv.canonical_json->>'filename' "
                    "FROM processing_runs pr JOIN source_versions sv ON sv.tenant_id=pr.tenant_id "
                    "AND sv.workspace_id=pr.workspace_id AND sv.record_id=pr.source_version_id "
                    "JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                    "JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id AND o.object_id=sv.object_id "
                    "JOIN notebooks n ON n.tenant_id=pr.tenant_id AND n.workspace_id=pr.workspace_id "
                    "AND n.notebook_id=pr.canonical_json->>'notebook_id' "
                    "JOIN notebook_bindings nb ON nb.tenant_id=n.tenant_id AND nb.workspace_id=n.workspace_id "
                    "AND nb.notebook_id=n.notebook_id AND nb.binding_kind='source' AND nb.record_id=s.record_id AND nb.version_id=sv.record_id "
                    "JOIN identity_auth_workspaces w ON w.tenant_id=pr.tenant_id AND w.workspace_id=pr.workspace_id "
                    "LEFT JOIN identity_auth_tenant_roles tr ON tr.tenant_id=pr.tenant_id AND tr.user_id=pr.created_by AND tr.state='active' "
                    "LEFT JOIN identity_auth_memberships m ON m.tenant_id=pr.tenant_id AND m.workspace_id=pr.workspace_id "
                    "AND m.user_id=pr.created_by AND m.state='active' "
                    "LEFT JOIN identity_auth_tenant_permission_rules tp ON tp.tenant_id=pr.tenant_id AND tp.permission='local_internal_llm' "
                    "LEFT JOIN identity_auth_workspace_permission_rules wp ON wp.tenant_id=pr.tenant_id "
                    "AND wp.workspace_id=pr.workspace_id AND wp.permission='local_internal_llm' "
                    "WHERE pr.tenant_id=%s AND pr.workspace_id=%s AND pr.record_id=%s AND pr.created_by=%s "
                    "AND sv.record_id=%s AND s.record_id=%s AND pr.state='speech_to_text' AND s.state='processing' "
                    "AND o.status='completed' AND o.content_type='audio/wav' "
                    "AND NOT EXISTS (SELECT 1 FROM source_versions newer WHERE newer.tenant_id=sv.tenant_id "
                    "AND newer.workspace_id=sv.workspace_id AND newer.source_id=sv.source_id AND newer.version>sv.version) "
                    "AND NOT EXISTS (SELECT 1 FROM identity_auth_source_access sa WHERE sa.tenant_id=pr.tenant_id "
                    "AND sa.workspace_id=pr.workspace_id AND sa.user_id=pr.created_by AND sa.source_version_id=sv.record_id AND sa.allowed=0) "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=nb.tenant_id "
                    "AND u.workspace_id=nb.workspace_id AND u.notebook_id=nb.notebook_id AND u.source_id=s.record_id AND u.source_version_id=sv.record_id) "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_deletion_requests nd WHERE nd.tenant_id=n.tenant_id "
                    "AND nd.workspace_id=n.workspace_id AND nd.notebook_id=n.notebook_id) "
                    "AND NOT EXISTS (SELECT 1 FROM deletion_requests dr WHERE dr.tenant_id=s.tenant_id "
                    "AND dr.workspace_id=s.workspace_id AND dr.source_id=s.record_id AND dr.source_active=false)",
                    (context.tenant_id, context.workspace_id, processing_run_id, context.actor_id, document.source_version_id, document.source_id),
                ).fetchone()
            if row is None: raise ValueError
            role = Role(str(row[0]))
            if Action.ANALYZE not in ROLE_ACTION_MATRIX[role]: raise ValueError
            if str(row[1]) != context.actor_id and role not in {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN}: raise ValueError
            # Same precedence as AuthorizationService: tenant lock/deny, then workspace rule,
            # then ordinary tenant grant, then the exported role default. No new policy.
            if row[2] is not None and (bool(row[3]) or row[2] == 'deny'):
                allowed = row[2] == 'grant'
            elif row[4] is not None:
                allowed = row[4] == 'grant'
            elif row[2] is not None:
                allowed = row[2] == 'grant'
            else:
                allowed = Permission.LOCAL_INTERNAL_LLM in ROLE_PERMISSION_DEFAULTS[role]
            if (not allowed or str(row[5]).removeprefix('sha256:') != hashlib.sha256(document.content).hexdigest()
                    or int(row[6]) != len(document.content) or str(row[7]) != document.filename): raise ValueError
        except (ValueError, TypeError, KeyError):
            raise DocumentUnderstandingError('CURRENT_ACCESS_DENIED', status=403) from None
        except CloudDatabaseError:
            raise DocumentUnderstandingError('CURRENT_ACCESS_UNAVAILABLE', status=503, retryable=True) from None

    @staticmethod
    def _cloud_context(context: DocumentProcessingContext, capability: str) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, capability,
        )

    @staticmethod
    def _opaque_id(prefix: str, *values: str) -> str:
        digest = hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:32]
        return f"{prefix}-{digest}"

    @staticmethod
    def _snapshot(payload: Mapping[str, object]) -> tuple[str, str]:
        text = canonical_json_bytes(payload).decode("utf-8")
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _transition(
        connection, context: DocumentProcessingContext, *, entity_type: str,
        aggregate_id: str,
        expected_version: int, target_state: str, reason_code: str,
    ) -> int:
        transition_id = PostgresDocumentProcessingRepository._opaque_id(
            "tr", entity_type, aggregate_id, target_state, str(expected_version),
        )
        row = connection.execute(
            "SELECT state,version,outcome,error_code FROM transition_canon_state"
            "(%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                entity_type, aggregate_id, expected_version, target_state,
                transition_id, reason_code, context.trace_id, context.policy_version,
            ),
        ).fetchone()
        if row is None or str(row[2]) != "succeeded" or row[1] is None:
            code = "CANON_TRANSITION_INVALID" if row is None or row[3] is None else str(row[3])
            raise DocumentUnderstandingError(code, status=409)
        return int(row[1])

    @staticmethod
    def _database_error(error: Exception) -> DocumentUnderstandingError:
        if isinstance(error, DocumentUnderstandingError):
            return error
        if isinstance(error, CloudDatabaseError):
            return DocumentUnderstandingError(
                "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503,
                retryable=error.retryable,
            )
        return DocumentUnderstandingError(
            "DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503, retryable=True,
        )

    def load_source_document(
        self, context: DocumentProcessingContext, source_id: str,
        *, processing_run_id: str | None = None,
    ) -> StoredSourceDocument:
        bound_verified = False
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.read")) as connection:
                select = (
                    "SELECT sv.record_id,sv.object_id,sv.canonical_json,o.object_key,"
                    "o.digest_sha256,o.byte_size,o.content_type,o.status "
                    "FROM source_versions sv "
                )
                if processing_run_id is not None:
                    select += (
                        "JOIN processing_runs pr ON pr.tenant_id=sv.tenant_id "
                        "AND pr.workspace_id=sv.workspace_id AND pr.source_version_id=sv.record_id "
                        "JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id "
                        "AND s.record_id=sv.source_id "
                    )
                select += ("LEFT JOIN " if processing_run_id is not None else "JOIN ") + (
                    "object_records o ON "
                    "o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id "
                    "AND o.object_id=sv.object_id "
                )
                if processing_run_id is not None:
                    select += (
                        "WHERE pr.record_id=%s AND sv.source_id=%s "
                        "AND pr.tenant_id=%s AND pr.workspace_id=%s"
                    )
                    params = (processing_run_id, source_id, context.tenant_id, context.workspace_id)
                else:
                    select += "WHERE sv.source_id=%s ORDER BY sv.version DESC LIMIT 1"
                    params = (source_id,)
                row = connection.execute(select, params).fetchone()
            if row is None:
                raise DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)
            # Only an exact scoped Run -> SourceVersion -> Source match can
            # authorize failure handling after original-object validation.
            # fail() rechecks current Run state, including terminal protection.
            bound_verified = processing_run_id is not None
            payload = cast(Mapping[str, object], row[2])
            filename = str(payload.get("filename", ""))
            object_key = str(row[3])
            content_type = str(row[6])
            original_format = (
                (content_type == "application/pdf" and filename.lower().endswith(".pdf"))
                or text_source_format(filename, content_type) is not None
                or image_source_format(filename, content_type)
                or xlsx_source_format(filename, content_type)
                or docx_source_format(filename, content_type)
                or pptx_source_format(filename, content_type)
                or audio_source_format(filename, content_type)
            )
            if (
                str(row[7]) != "completed" or not original_format
            ):
                raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409)
            object_context = self._cloud_context(context, "object.read")
            ObjectKeyPolicy().validate_final(object_context, "source", object_key)
            content = self._object_storage.get(object_key)
            expected_digest = str(row[4])
            if docx_source_format(filename, content_type) or pptx_source_format(filename, content_type) or audio_source_format(filename, content_type):
                expected_digest = expected_digest.removeprefix('sha256:')
            if (
                len(content) != int(row[5])
                or hashlib.sha256(content).hexdigest() != expected_digest
                or (content_type == "application/pdf" and not content.startswith(b"%PDF-"))
            ):
                raise DocumentUnderstandingError("SOURCE_OBJECT_CHECKSUM_MISMATCH", status=409)
            if image_source_format(filename, content_type):
                from .image_document_understanding import normalize_image
                try:
                    normalize_image(content, content_type)
                except DocumentUnderstandingError:
                    raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409) from None
            if xlsx_source_format(filename, content_type):
                from .xlsx_source import read_xlsx
                from .source_ingest import SourceRejected
                try:
                    read_xlsx(content)
                except SourceRejected:
                    raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409) from None
            if docx_source_format(filename, content_type):
                from .docx_source import read_docx
                from .source_ingest import SourceRejected
                try:
                    read_docx(content)
                except SourceRejected:
                    raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409) from None
            if pptx_source_format(filename, content_type):
                from .pptx_source import read_pptx
                from .source_ingest import SourceRejected
                try:
                    read_pptx(content)
                except SourceRejected:
                    raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409) from None
            if audio_source_format(filename, content_type):
                from .audio_source import read_wav
                from .source_ingest import SourceRejected
                try:
                    read_wav(content)
                except SourceRejected:
                    raise DocumentUnderstandingError('SOURCE_OBJECT_NOT_READY', status=409) from None
            return StoredSourceDocument(source_id, str(row[0]), filename, content, content_type)
        except Exception as error:
            mapped = (
                DocumentUnderstandingError(
                    error.code, status=503 if error.retryable else 409, retryable=error.retryable,
                ) if isinstance(error, ObjectStorageError) else self._database_error(error)
            )
            if bound_verified:
                self.fail(context, cast(str, processing_run_id), mapped.code, retryable=mapped.retryable)
            raise mapped from None

    def reprocess(
        self, context: DocumentProcessingContext, source_id: str, source_version_id: str,
        *, notebook_id: str, idempotency_key: str, authorization_snapshot: Mapping[str, object],
        processing_mode: object = _PROCESSING_MODE_UNSET,
    ) -> dict[str, object]:
        operation = "source.manual_reprocess"
        readiness_context = _reprocessing_readiness_context(processing_mode)
        explicit_mode = processing_mode is not _PROCESSING_MODE_UNSET
        identity = {"source_id": source_id, "source_version_id": source_version_id, "notebook_id": notebook_id}
        if explicit_mode:
            identity["processing_mode"] = cast(str, processing_mode)
        _, fingerprint = self._snapshot(identity)
        run_id = self._opaque_id("pr", context.tenant_id, context.workspace_id,
                                 context.actor_id, operation, idempotency_key)
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                # Use the same locks as Notebook deletion/unbinding and Source
                # retention, so those boundaries cannot change during submission.
                for lock in (
                    f"reprocess-key|{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{idempotency_key}",
                    f"notebook-delete|{context.tenant_id}|{context.workspace_id}|{notebook_id}",
                    f"unbind-notebook|{context.tenant_id}|{context.workspace_id}|{notebook_id}",
                    f"retention-source|{context.tenant_id}|{context.workspace_id}|{source_id}",
                ):
                    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock,))
                source = connection.execute(
                    "SELECT s.record_id,s.state,s.version,latest.record_id,"
                    "latest.canonical_json->>'filename',o.content_type "
                    "FROM sources s JOIN LATERAL (SELECT sv.record_id,sv.object_id,sv.canonical_json "
                    "FROM source_versions sv WHERE sv.tenant_id=s.tenant_id AND sv.workspace_id=s.workspace_id "
                    "AND sv.source_id=s.record_id ORDER BY sv.version DESC LIMIT 1) latest ON true "
                    "JOIN object_records o ON o.tenant_id=s.tenant_id AND o.workspace_id=s.workspace_id "
                    "AND o.object_id=latest.object_id AND o.status='completed' "
                    "JOIN notebooks n ON n.tenant_id=s.tenant_id AND n.workspace_id=s.workspace_id AND n.notebook_id=%s "
                    "JOIN notebook_bindings nb ON nb.tenant_id=n.tenant_id AND nb.workspace_id=n.workspace_id "
                    "AND nb.notebook_id=n.notebook_id AND nb.binding_kind='source' "
                    "AND nb.record_id=s.record_id AND nb.version_id=latest.record_id "
                    "WHERE s.tenant_id=%s AND s.workspace_id=%s AND s.record_id=%s "
                    "AND (s.created_by=%s OR %s) "
                    "AND NOT EXISTS (SELECT 1 FROM identity_auth_source_access sa WHERE sa.tenant_id=s.tenant_id "
                    "AND sa.workspace_id=s.workspace_id AND sa.source_version_id=latest.record_id AND sa.user_id=%s AND sa.allowed=0) "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=nb.tenant_id "
                    "AND u.workspace_id=nb.workspace_id AND u.notebook_id=nb.notebook_id "
                    "AND u.source_id=nb.record_id AND u.source_version_id=nb.version_id) "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_deletion_requests nd WHERE nd.tenant_id=n.tenant_id "
                    "AND nd.workspace_id=n.workspace_id AND nd.notebook_id=n.notebook_id) "
                    "AND NOT EXISTS (SELECT 1 FROM deletion_requests dr WHERE dr.tenant_id=s.tenant_id "
                    "AND dr.workspace_id=s.workspace_id AND dr.source_id=s.record_id AND dr.source_active=false)",
                    (notebook_id, context.tenant_id, context.workspace_id, source_id, context.actor_id,
                     authorization_snapshot.get("role") in {"personal_owner", "organization_admin", "workspace_admin"}, context.actor_id),
                ).fetchone()
                if source is None or str(source[1]) in {"disabled", "expired", "deleting", "deleted"}:
                    raise DocumentUnderstandingError("SOURCE_NOT_FOUND", status=404)
                if str(source[3]) != source_version_id:
                    raise DocumentUnderstandingError("SOURCE_VERSION_CONFLICT", status=409)
                filename, mime = str(source[4]), str(source[5])
                if not ((mime == "application/pdf" and filename.lower().endswith(".pdf"))
                        or text_source_format(filename, mime) is not None or image_source_format(filename, mime) or xlsx_source_format(filename, mime) or docx_source_format(filename, mime) or pptx_source_format(filename, mime) or audio_source_format(filename, mime)):
                    raise DocumentUnderstandingError("SOURCE_OBJECT_NOT_READY", status=409)
                replay = connection.execute(
                    "SELECT request_fingerprint,result FROM idempotency_records "
                    "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise DocumentUnderstandingError("IDEMPOTENCY_KEY_REUSED", status=409)
                    result = {**cast(dict[str, object], replay[1]), "replayed": True}
                    if explicit_mode:
                        result["processing_mode"] = self._saved_reprocessing_mode(connection, context,
                            cast(str, result["processing_run_id"]), source_version_id, notebook_id, cast(str, processing_mode))
                    return result
                active = connection.execute(
                    "SELECT 1 FROM processing_runs pr LEFT JOIN document_processing_jobs job "
                    "ON job.tenant_id=pr.tenant_id AND job.workspace_id=pr.workspace_id AND job.processing_run_id=pr.record_id "
                    "WHERE pr.source_version_id=%s AND (pr.state NOT IN ('completed','failed','policy_blocked') "
                    "OR job.state IN ('pending','leased','retry_wait')) LIMIT 1",
                    (source_version_id,),
                ).fetchone()
                if active is not None:
                    raise DocumentUnderstandingError("SOURCE_PROCESSING_ACTIVE", status=409)
                previous = connection.execute(
                    "SELECT pr.record_id,pr.state,job.state FROM processing_runs pr "
                    "JOIN document_processing_jobs job ON job.tenant_id=pr.tenant_id AND job.workspace_id=pr.workspace_id "
                    "AND job.processing_run_id=pr.record_id WHERE pr.source_version_id=%s "
                    "ORDER BY pr.created_at DESC,pr.record_id DESC LIMIT 1", (source_version_id,),
                ).fetchone()
                failed_retry = (str(source[1]) in {"failed", "waiting_model"} and previous is not None
                                and str(previous[1]) == "failed" and str(previous[2]) == "dead_letter")
                review_retry = False
                if ((docx_source_format(filename, mime) or pptx_source_format(filename, mime)) and str(source[1]) == "needs_review"
                        and previous is not None and str(previous[1]) == "completed"
                        and str(previous[2]) == "completed"):
                    review = connection.execute(
                        "SELECT ur.canonical_json FROM understanding_results ur "
                        "JOIN source_versions sv ON sv.tenant_id=ur.tenant_id "
                        "AND sv.workspace_id=ur.workspace_id AND sv.record_id=ur.source_version_id "
                        "WHERE ur.tenant_id=%s AND ur.workspace_id=%s AND ur.processing_run_id=%s "
                        "AND ur.source_version_id=%s AND sv.source_id=%s",
                        (context.tenant_id, context.workspace_id, str(previous[0]), source_version_id, source_id),
                    ).fetchone()
                    value = review[0] if review is not None else None
                    lineage = value.get("lineage") if isinstance(value, dict) else None
                    review_retry = (isinstance(value, dict) and value.get("source_id") == source_id
                                    and value.get("source_version_id") == source_version_id
                                    and value.get("status") == "needs_review"
                                    and value.get("conflict") == "UNDERSTANDING_PARSER_CONFLICT"
                                    and isinstance(lineage, dict) and lineage.get("source_format") == ("pptx" if pptx_source_format(filename, mime) else "docx")
                                    and lineage.get("reconciliation") == "mismatch")
                if not (failed_retry or review_retry):
                    raise DocumentUnderstandingError("SOURCE_REPROCESSING_STATE_INVALID", status=409)
                policy = connection.execute(
                    "SELECT data_area,cost_limit_cents,acl_version FROM identity_auth_workspaces WHERE tenant_id=%s AND workspace_id=%s",
                    (context.tenant_id, context.workspace_id),
                ).fetchone()
                if policy is None or int(policy[2]) != authorization_snapshot.get("acl_version"):
                    raise DocumentUnderstandingError("CURRENT_ACCESS_DENIED", status=403)
                previous_id = str(previous[0])
                audio_source = audio_source_format(filename, mime)
                initial_state = 'speech_to_text' if audio_source else 'vision_llm_understanding'
                payload = {"source_version_id": source_version_id, "modality": "document",
                           "trigger_type": "manual_request", "retry_of_processing_run_id": previous_id,
                           "trigger_event_id": run_id, "notebook_id": notebook_id,
                           "vision_llm_first": True, "parser_role": "validation_only",
                           "readiness_context": readiness_context,
                           "authorization_snapshot": dict(authorization_snapshot),
                           "policy_snapshot": {"data_area": str(policy[0]), "cost_limit_cents": int(policy[1]),
                                               "routing_policy_version": context.policy_version}}
                if audio_source:
                    payload['modality'] = 'audio'
                    payload['asr_then_llm'] = True
                    payload.pop('vision_llm_first')
                text, digest = self._snapshot(payload)
                connection.execute(
                    "INSERT INTO processing_runs (tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                    "canonical_json,canonical_text,digest_sha256,state,created_by,trace_id,source_version_id,"
                    "modality,trigger_type,retry_of_processing_run_id,trigger_event_id) "
                    "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,'accepted',%s,%s,%s,%s,'manual_request',%s,%s)",
                    (context.tenant_id, context.workspace_id, run_id, run_id, Jsonb(payload), text, digest,
                     context.actor_id, context.trace_id, source_version_id, 'audio' if audio_source else 'document', previous_id, run_id),
                )
                self._transition(connection, context, entity_type="Source", aggregate_id=source_id,
                                 expected_version=int(source[2]), target_state="processing", reason_code="MANUAL_REPROCESSING_REQUESTED")
                self._transition(connection, context, entity_type="ProcessingRun", aggregate_id=run_id,
                                 expected_version=1, target_state=initial_state, reason_code="MANUAL_REPROCESSING_STARTED")
                connection.execute(
                    "INSERT INTO document_processing_jobs (tenant_id,workspace_id,job_id,source_id,source_version_id,"
                    "processing_run_id,state,created_by,trace_id,policy_version) VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s)",
                    (context.tenant_id, context.workspace_id, self._opaque_id("dpj", run_id), source_id,
                     source_version_id, run_id, context.actor_id, context.trace_id, context.policy_version),
                )
                result = {"processing_run_id": run_id, "source_id": source_id, "source_version_id": source_version_id,
                          "retry_of_processing_run_id": previous_id, "processing_state": initial_state,
                          "source_state": "processing", "job_state": "pending", "replayed": False}
                if explicit_mode:
                    result["processing_mode"] = self._saved_reprocessing_mode(connection, context, run_id,
                        source_version_id, notebook_id, cast(str, processing_mode))
                connection.execute(
                    "INSERT INTO audit_events (event_id,tenant_id,workspace_id,actor_id,action,target_type,target_id,"
                    "outcome,trace_id,policy_version,metadata) VALUES (%s,%s,%s,%s,'source.manual_reprocess',"
                    "'processing_run',%s,'succeeded',%s,%s,%s)",
                    (self._opaque_id("audit", run_id), context.tenant_id, context.workspace_id, context.actor_id,
                     run_id, context.trace_id, context.policy_version, Jsonb({"retry_of_processing_run_id": previous_id,
                                                                            "source_version_id": source_version_id, "trigger_type": "manual_request"})),
                )
                connection.execute(
                    "INSERT INTO idempotency_records (tenant_id,workspace_id,actor_id,operation,idempotency_key,"
                    "request_fingerprint,result,status,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key,
                     fingerprint, Jsonb(result), datetime.now(timezone.utc) + timedelta(hours=24)),
                )
                return result
        except Exception as error:
            raise self._database_error(error) from None

    @staticmethod
    def _saved_reprocessing_mode(connection, context, run_id, source_version_id, notebook_id, requested_mode):
        # 캐시/입력 Mode가 아닌 같은 transaction의 실제 Run canonical 저장값을 읽는다.
        row = connection.execute(
            "SELECT pr.canonical_json FROM processing_runs pr WHERE pr.tenant_id=%s AND pr.workspace_id=%s "
            "AND pr.record_id=%s AND pr.created_by=%s AND pr.source_version_id=%s "
            "AND pr.canonical_json->>'notebook_id'=%s",
            (context.tenant_id, context.workspace_id, run_id, context.actor_id, source_version_id, notebook_id),
        ).fetchone()
        payload = None if row is None else row[0]
        proof = payload.get("readiness_context") if type(payload) is dict else None
        if (type(proof) is not dict or set(proof) != {"version", "mode", "mode_origin"}
                or type(proof["version"]) is not int or proof["version"] != 1
                or proof["mode_origin"] != "request" or type(proof["mode"]) is not str
                or proof["mode"] not in ("auto", "local_only", "pinned") or proof["mode"] != requested_mode):
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
        return proof["mode"]

    def start(
        self, context: DocumentProcessingContext, source_version_id: str,
        *, enqueue: bool = False, notebook_id: str | None = None,
    ) -> str:
        processing_run_id = self._opaque_id(
            "pr", context.tenant_id, context.workspace_id, source_version_id, "initial",
        )
        payload: dict[str, object] = {
            "source_version_id": source_version_id,
            "modality": "document",
            "trigger_type": "initial",
            "vision_llm_first": True,
            "parser_role": "validation_only",
        }
        if notebook_id is not None:
            if not isinstance(notebook_id, str) or not _SAFE_NOTEBOOK_ID.fullmatch(notebook_id):
                raise DocumentUnderstandingError('DOCUMENT_PROCESSING_CONTEXT_INVALID')
            payload['notebook_id'] = notebook_id
        text, digest = self._snapshot(payload)
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                source_row = connection.execute(
                    "SELECT sv.source_id,s.state,s.version,sv.canonical_json,o.content_type FROM source_versions sv "
                    "JOIN sources s ON s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                    "JOIN object_records o ON o.tenant_id=sv.tenant_id AND o.workspace_id=sv.workspace_id AND o.object_id=sv.object_id "
                    "WHERE sv.record_id=%s", (source_version_id,),
                ).fetchone()
                if source_row is None:
                    raise DocumentUnderstandingError('SOURCE_NOT_FOUND', status=404)
                audio_source = (len(source_row) == 5 and isinstance(source_row[3], dict)
                    and audio_source_format(str(source_row[3].get('filename', '')), str(source_row[4])))
                initial_state = 'speech_to_text' if audio_source else 'vision_llm_understanding'
                if audio_source:
                    payload = {'source_version_id': source_version_id, 'modality': 'audio',
                               'trigger_type': 'initial', 'asr_then_llm': True, 'parser_role': 'validation_only'}
                    if notebook_id is not None:
                        payload['notebook_id'] = notebook_id
                    text, digest = self._snapshot(payload)
                connection.execute(
                    "INSERT INTO processing_runs "
                    "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                    "canonical_json,canonical_text,digest_sha256,state,created_by,trace_id,"
                    "source_version_id,modality,trigger_type) "
                    "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,'accepted',%s,%s,%s,%s,'initial') "
                    "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                    (
                        context.tenant_id, context.workspace_id, processing_run_id,
                        processing_run_id, Jsonb(payload), text, digest, context.actor_id,
                        context.trace_id, source_version_id, 'audio' if audio_source else 'document',
                    ),
                )
                row = connection.execute(
                    "SELECT state,version,source_version_id FROM processing_runs WHERE record_id=%s",
                    (processing_run_id,),
                ).fetchone()
                if row is None or str(row[2]) != source_version_id:
                    raise DocumentUnderstandingError("PROCESSING_RUN_CONFLICT", status=409)
                source_id, source_state, source_version = (
                    str(source_row[0]), str(source_row[1]), int(source_row[2])
                )
                # A digest replay can find a Source that was already fully
                # processed.  Reusing that Source must be idempotent: do not
                # try to transition a ready Source back into processing or
                # restart its completed ProcessingRun.
                replayed_completed = source_state == "ready" and str(row[0]) == "completed"
                if source_state == "registered":
                    source_version = self._transition(
                        connection, context, entity_type="Source", aggregate_id=source_id,
                        expected_version=source_version, target_state="security_check",
                        reason_code="PDF_SECURITY_CHECK_PASSED",
                    )
                    source_state = "security_check"
                if source_state == "security_check":
                    self._transition(
                        connection, context, entity_type="Source", aggregate_id=source_id,
                        expected_version=source_version, target_state="processing",
                        reason_code="DOCUMENT_PROCESSING_STARTED",
                    )
                elif source_state in {"waiting_model", "partial_understanding", "needs_review", "failed"}:
                    self._transition(
                        connection, context, entity_type="Source", aggregate_id=source_id,
                        expected_version=source_version, target_state="processing",
                        reason_code="DOCUMENT_PROCESSING_RESTARTED",
                    )
                elif source_state == "ready" and not replayed_completed:
                    raise DocumentUnderstandingError("SOURCE_PROCESSING_STATE_INVALID", status=409)
                elif source_state != "processing" and not replayed_completed:
                    raise DocumentUnderstandingError("SOURCE_PROCESSING_STATE_INVALID", status=409)
                if replayed_completed:
                    pass
                elif str(row[0]) == "accepted" and int(row[1]) == 1:
                    self._transition(
                        connection, context, entity_type="ProcessingRun",
                        aggregate_id=processing_run_id,
                        expected_version=1, target_state=initial_state,
                        reason_code="AUDIO_TRANSCRIPTION_STARTED" if audio_source else "ORIGINAL_PDF_UNDERSTANDING_STARTED",
                    )
                elif str(row[0]) != initial_state:
                    raise DocumentUnderstandingError("PROCESSING_RUN_CONFLICT", status=409)
                if enqueue:
                    job_id = self._opaque_id("dpj", processing_run_id)
                    connection.execute(
                        "INSERT INTO document_processing_jobs "
                        "(tenant_id,workspace_id,job_id,source_id,source_version_id,"
                        "processing_run_id,state,created_by,trace_id,policy_version) "
                        "VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s,%s) "
                        "ON CONFLICT (tenant_id,workspace_id,processing_run_id) DO NOTHING",
                        (
                            context.tenant_id, context.workspace_id, job_id, source_id,
                            source_version_id, processing_run_id, context.actor_id,
                            context.trace_id, context.policy_version,
                        ),
                    )
            return processing_run_id
        except Exception as error:
            raise self._database_error(error) from None

    def get_status(
        self, context: DocumentProcessingContext, processing_run_id: str, *, notebook_id: str,
    ) -> DocumentProcessingStatus:
        if not isinstance(notebook_id, str) or _SAFE_NOTEBOOK_ID.fullmatch(notebook_id) is None:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.read")) as connection:
                row = connection.execute(
                    "SELECT pr.record_id,sv.source_id,pr.source_version_id,pr.state,s.state,"
                    "job.state,job.last_safe_error_code FROM processing_runs pr "
                    "JOIN source_versions sv ON sv.tenant_id=pr.tenant_id AND "
                    "sv.workspace_id=pr.workspace_id AND sv.record_id=pr.source_version_id "
                    "JOIN sources s ON s.tenant_id=sv.tenant_id AND "
                    "s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                    "JOIN notebook_bindings nb ON nb.tenant_id=sv.tenant_id AND "
                    "nb.workspace_id=sv.workspace_id AND nb.notebook_id=%s "
                    "AND nb.binding_kind='source' AND nb.record_id=sv.source_id "
                    "AND nb.version_id=sv.record_id "
                    "AND NOT EXISTS (SELECT 1 FROM notebook_source_unbindings u WHERE u.tenant_id=nb.tenant_id "
                    "AND u.workspace_id=nb.workspace_id AND u.notebook_id=nb.notebook_id "
                    "AND u.source_id=nb.record_id AND u.source_version_id=nb.version_id) "
                    "AND NOT EXISTS (SELECT 1 FROM deletion_requests dr WHERE dr.tenant_id=s.tenant_id "
                    "AND dr.workspace_id=s.workspace_id AND dr.source_id=s.record_id AND dr.source_active=false) "
                    "LEFT JOIN document_processing_jobs job ON job.tenant_id=pr.tenant_id AND "
                    "job.workspace_id=pr.workspace_id AND job.processing_run_id=pr.record_id "
                    "WHERE pr.record_id=%s",
                    (notebook_id, processing_run_id),
                ).fetchone()
            if row is None:
                raise DocumentUnderstandingError("PROCESSING_RUN_NOT_FOUND", status=404)
            return DocumentProcessingStatus(
                str(row[0]), str(row[1]), str(row[2]), str(row[3]), str(row[4]),
                None if row[5] is None else str(row[5]),
                None if row[6] is None else str(row[6]),
            )
        except DocumentUnderstandingError:
            raise
        except Exception as error:
            raise self._database_error(error) from None

    def complete(
        self, context: DocumentProcessingContext, processing_run_id: str,
        result: DocumentUnderstandingResult,
    ) -> None:
        cell_source = result.lineage.get('source_format') == 'xlsx'
        block_source = result.lineage.get('source_format') == 'docx'
        slide_source = result.lineage.get('source_format') == 'pptx'
        audio_source = result.lineage.get('source_format') == 'wav'
        if audio_source:
            from .audio_document_understanding import read_audio_result_transcript
            original = self.load_source_document(context, result.source_id, processing_run_id=processing_run_id)
            read_audio_result_transcript(original, result)
            if result.lineage.get('policy_version') != context.policy_version or result.lineage.get('trace_id') != context.trace_id:
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
        if block_source:
            if result.parser.pages or result.parser.page_texts or result.parser.regions or result.parser.cell_evidence or result.parser.role != 'validation_only':
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
            if result.status == 'ready':
                from .docx_document_understanding import validate_block_evidence
                validate_block_evidence(result.parser.block_evidence, result.semantic.key_facts)
        if slide_source:
            if result.parser.pages or result.parser.page_texts or result.parser.regions or result.parser.cell_evidence or result.parser.block_evidence or result.parser.role != 'validation_only':
                raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
            if result.status == 'ready':
                from .pptx_document_understanding import validate_slide_evidence
                validate_slide_evidence(result.parser.slide_evidence, result.semantic.key_facts)
        if cell_source and result.status == 'ready':
            from .xlsx_document_understanding import validate_cell_evidence
            validate_cell_evidence(result.parser.cell_evidence, result.semantic.key_facts)
        understanding_id = self._opaque_id("ur", processing_run_id, result.source_version_id)
        payload: dict[str, object] = {
            "source_id": result.source_id,
            "source_version_id": result.source_version_id,
            "status": result.status,
            "semantic": {
                "title": result.semantic.title, "summary": result.semantic.summary,
                "key_facts": list(result.semantic.key_facts),
            },
            "parser_validation": {
                "text": result.parser.text, "markdown": result.parser.markdown,
                "html": result.parser.html, "pages": list(result.parser.pages),
                "page_texts": [
                    {"page": page, "text": page_text}
                    for page, page_text in result.parser.page_texts
                ],
                "role": result.parser.role,
                "regions": list(result.parser.regions),
            },
            "lineage": dict(result.lineage),
            "conflict": result.conflict,
        }
        text, digest = self._snapshot(payload)
        if cell_source:
            payload['parser_validation']['cell_evidence'] = list(result.parser.cell_evidence)
            text, digest = self._snapshot(payload)
        if block_source:
            payload['parser_validation']['block_evidence'] = list(result.parser.block_evidence)
            text, digest = self._snapshot(payload)
        if slide_source:
            payload['parser_validation']['slide_evidence'] = list(result.parser.slide_evidence)
            text, digest = self._snapshot(payload)
        if audio_source:
            payload['parser_validation']['audio_evidence'] = list(result.parser.audio_evidence)
            text, digest = self._snapshot(payload)
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                if cell_source or block_source or slide_source or audio_source:
                    bound = connection.execute(
                        "SELECT pr.state,pr.version,pr.source_version_id,sv.source_id FROM processing_runs pr "
                        "JOIN source_versions sv ON sv.tenant_id=pr.tenant_id AND sv.workspace_id=pr.workspace_id "
                        "AND sv.record_id=pr.source_version_id WHERE pr.record_id=%s",
                        (processing_run_id,),
                    ).fetchone()
                    if bound is None or str(bound[0]) != ('speech_to_text' if audio_source else 'vision_llm_understanding') or int(bound[1]) != 2 or str(bound[2]) != result.source_version_id or str(bound[3]) != result.source_id:
                        raise DocumentUnderstandingError('UNDERSTANDING_RESULT_MISMATCH', status=409)
                version = self._transition(
                    connection, context, entity_type="ProcessingRun",
                    aggregate_id=processing_run_id,
                    expected_version=2, target_state="llm_semantic_understanding" if audio_source else "parser_ocr_validation",
                    reason_code="SEMANTIC_UNDERSTANDING_COMPLETED",
                )
                if audio_source:
                    version = self._transition(connection, context, entity_type='ProcessingRun', aggregate_id=processing_run_id,
                        expected_version=version, target_state='transcript_timecode_validation', reason_code='AUDIO_TRANSCRIPT_VALIDATED')
                version = self._transition(
                    connection, context, entity_type="ProcessingRun",
                    aggregate_id=processing_run_id,
                    expected_version=version, target_state="evidence_reconciliation",
                    reason_code="PARSER_VALIDATION_COMPLETED",
                )
                connection.execute(
                    "INSERT INTO understanding_results "
                    "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                    "canonical_json,canonical_text,digest_sha256,created_by,trace_id,"
                    "processing_run_id,source_version_id) "
                    "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s,%s) "
                    "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                    (
                        context.tenant_id, context.workspace_id, understanding_id,
                        understanding_id, Jsonb(payload), text, digest, context.actor_id,
                        context.trace_id, processing_run_id, result.source_version_id,
                    ),
                )
                evidence_items = [{'source_version_id': result.source_version_id,
                    'understanding_result_id': understanding_id, 'parser_role': 'validation_only',
                    'cell_evidence': list(result.parser.cell_evidence)}] if cell_source else [{
                        "page": page, "source_version_id": result.source_version_id,
                        "understanding_result_id": understanding_id,
                        "parser_role": "validation_only",
                    } for page in result.parser.pages]
                if block_source:
                    evidence_items = [{'source_version_id': result.source_version_id,
                        'understanding_result_id': understanding_id, 'parser_role': 'validation_only',
                        'block_evidence': list(result.parser.block_evidence)}]
                if slide_source:
                    evidence_items = [{'source_version_id': result.source_version_id,
                        'understanding_result_id': understanding_id, 'parser_role': 'validation_only',
                        'slide_evidence': list(result.parser.slide_evidence)}]
                if audio_source:
                    evidence_items = [{'source_version_id': result.source_version_id,
                        'understanding_result_id': understanding_id, 'parser_role': 'validation_only',
                        'transcript_sha256': result.lineage['transcript_sha256'], 'audio_evidence': list(result.parser.audio_evidence)}]
                for evidence in evidence_items:
                    evidence_id = self._opaque_id("ev", understanding_id, 'audio' if audio_source else 'slides' if slide_source else 'blocks' if block_source else 'cells' if cell_source else str(evidence['page']))
                    if result.parser.regions:
                        evidence["regions"] = list(result.parser.regions)
                    evidence_text, evidence_digest = self._snapshot(evidence)
                    connection.execute(
                        "INSERT INTO extraction_evidence "
                        "(tenant_id,workspace_id,record_id,aggregate_id,version,schema_version,"
                        "canonical_json,canonical_text,digest_sha256,created_by,trace_id,"
                        "understanding_result_id,source_version_id) "
                        "VALUES (%s,%s,%s,%s,1,1,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (tenant_id,workspace_id,record_id) DO NOTHING",
                        (
                            context.tenant_id, context.workspace_id, evidence_id, evidence_id,
                            Jsonb(evidence), evidence_text, evidence_digest, context.actor_id,
                            context.trace_id, understanding_id, result.source_version_id,
                        ),
                    )
                completed_from_version = version
                self._transition(
                    connection, context, entity_type="ProcessingRun",
                    aggregate_id=processing_run_id,
                    expected_version=version, target_state="completed",
                    reason_code="EVIDENCE_RECONCILIATION_COMPLETED",
                )
                if result.status == "needs_review":
                    source_row = connection.execute(
                        "SELECT sv.source_id,s.state,s.version FROM source_versions sv "
                        "JOIN sources s ON s.tenant_id=sv.tenant_id AND "
                        "s.workspace_id=sv.workspace_id AND s.record_id=sv.source_id "
                        "WHERE sv.record_id=%s",
                        (result.source_version_id,),
                    ).fetchone()
                    if source_row is None or str(source_row[1]) != "processing":
                        raise DocumentUnderstandingError("SOURCE_PROCESSING_STATE_INVALID", status=409)
                    self._transition(
                        connection, context, entity_type="Source",
                        aggregate_id=str(source_row[0]), expected_version=int(source_row[2]),
                        target_state="needs_review",
                        reason_code="UNDERSTANDING_PARSER_CONFLICT",
                    )
                if getattr(self._cloud_store, '_notification_inbox_enabled', False):
                    from .notification_events import enqueue_completed_event
                    enqueue_completed_event(connection, context, origin_event_id=self._opaque_id(
                        'tr', 'ProcessingRun', processing_run_id, 'completed', str(completed_from_version)))
        except Exception as error:
            raise self._database_error(error) from None

    def fail(
        self, context: DocumentProcessingContext, processing_run_id: str,
        code: str, *, retryable: bool,
    ) -> None:
        self._fail(context, processing_run_id, code, retryable=retryable)

    def fail_with_readiness(self, context, processing_run_id, code, *, retryable, failure_evidence):
        if self._readiness_port is None:
            return self.fail(context, processing_run_id, code, retryable=retryable)
        if failure_evidence is None:
            failure_evidence = DocumentFailureEvidence(None, None, "unknown")
        if type(failure_evidence) is not DocumentFailureEvidence:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        return self._fail(context, processing_run_id, code, retryable=retryable, evidence=failure_evidence)

    @staticmethod
    def _failure_parent(connection, context, processing_run_id):
        return connection.execute(
            "SELECT /* readiness-failure-parent */ pr.state,pr.version,sv.source_id,s.state,s.version,"
            "pr.canonical_json,pr.created_by,pr.source_version_id,latest.record_id,"
            "NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE "
            "(newer.tenant_id,newer.workspace_id,newer.source_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
            "AND newer.version>sv.version) "
            "FROM public.processing_runs pr JOIN public.source_versions sv ON "
            "(sv.tenant_id,sv.workspace_id,sv.record_id)=(pr.tenant_id,pr.workspace_id,pr.source_version_id) "
            "JOIN public.sources s ON (s.tenant_id,s.workspace_id,s.record_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) "
            "JOIN LATERAL(SELECT p.record_id FROM public.processing_runs p WHERE "
            "(p.tenant_id,p.workspace_id,p.source_version_id)=(pr.tenant_id,pr.workspace_id,pr.source_version_id) "
            "ORDER BY p.created_at DESC,p.record_id DESC LIMIT 1) latest ON true "
            "WHERE pr.tenant_id=%s AND pr.workspace_id=%s AND pr.record_id=%s AND pr.created_by=%s",
            (context.tenant_id, context.workspace_id, processing_run_id, context.actor_id),
        ).fetchone()

    def _record_failure_wait(self, connection, context, processing_run_id, row, mode, evidence, reason):
        from .document_readiness import RoleWaitEvidence, ReadinessDispatcherSettings
        from .workspace_model_defaults import read_document_readiness_policy, WorkspaceModelDefaultsError
        settings = self._readiness_port.settings
        if type(settings) is not ReadinessDispatcherSettings:
            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_CONTEXT_INVALID")
        reader_source_gone = False
        try:
            # 현재 인가 철회/아래 B1의 식별된 자료 자격 소멸만 격리한다.
            # SQL/보호 함수의 실패는 원 Tx까지 전파한다.
            with connection.transaction():
                now = connection.execute("SELECT clock_timestamp()").fetchone()[0]
                provisional = RoleWaitEvidence(context.tenant_id, context.workspace_id, context.actor_id,
                    row[5]["notebook_id"], row[2], row[7], processing_run_id, evidence.capability,
                    evidence.failure_kind, mode, evidence.dependency_connection_id, "0" * 64, reason,
                    now, now + timedelta(seconds=settings.backoff_seconds))
                try:
                    snapshot = read_document_readiness_policy(connection, context, provisional, settings=settings)
                except WorkspaceModelDefaultsError as error:
                    reader_source_gone = error.code == "SOURCE_NOT_FOUND" and error.status == 404
                    raise DocumentUnderstandingError(error.code, status=error.status) from None
                # provisional은 타입 구성 전용이며 저장 불가. 실제 B1 본문 digest로만 영속화한다.
                wait = replace(provisional, frozen_context_digest=snapshot.eligibility.frozen_context_digest)
                self._readiness_port.record_wait(connection, context, processing_run_id, wait)
        except DocumentUnderstandingError as error:
            if not ((error.code == "CURRENT_ACCESS_DENIED" and error.status == 403)
                    or (reader_source_gone and error.code == "SOURCE_NOT_FOUND" and error.status == 404)):
                raise

    def _fail(self, context, processing_run_id, code, *, retryable, evidence=None):
        reason = code if code.isascii() and code.replace("_", "").isalnum() else (
            "RETRYABLE_PROVIDER_FAILURE" if retryable else "DOCUMENT_PROCESSING_FAILED"
        )
        try:
            with self._cloud_store._transaction(self._cloud_context(context, "source.process")) as connection:
                mode, wait_eligible, collect_wait, source_current = None, False, False, True
                if evidence is not None:
                    from .document_readiness import ReadinessModeProof
                    before = self._failure_parent(connection, context, processing_run_id)
                    if before is None:
                        return
                    payload = before[5]
                    if (before[6] != context.actor_id or before[8] != processing_run_id
                            or before[0] in {"completed", "failed", "policy_blocked"}):
                        return
                    if type(before[9]) is not bool:
                        raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
                    if before[9] and before[3] != "processing":
                        return
                    if type(payload) is not dict or payload.get("source_version_id") != before[7]:
                        raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
                    notebook_id = payload.get("notebook_id")
                    if notebook_id is not None and (type(notebook_id) is not str
                            or _SAFE_NOTEBOOK_ID.fullmatch(notebook_id) is None):
                        raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
                    proof = payload.get("readiness_context")
                    if "readiness_context" not in payload:
                        mode = ReadinessModeProof("legacy_unknown", "legacy_unknown")
                    else:
                        fields = {"version", "mode", "mode_origin"}
                        if payload.get("trigger_type") == "readiness_event":
                            fields |= {"frozen_context", "frozen_context_digest", "wait_processing_run_id"}
                        if (type(proof) is not dict or set(proof) != fields
                                or type(proof["version"]) is not int or proof["version"] != 1):
                            raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
                        mode = ReadinessModeProof(proof["mode"], proof["mode_origin"])
                    # Notebook 근거 없는 구 Run은 수집 불가다. 새 Notebook/Mode를 추정하지 않는다.
                    locks = [] if notebook_id is None else [
                        f"notebook-delete|{context.tenant_id}|{context.workspace_id}|{notebook_id}",
                        f"unbind-notebook|{context.tenant_id}|{context.workspace_id}|{notebook_id}"]
                    locks.append(f"retention-source|{context.tenant_id}|{context.workspace_id}|{before[2]}")
                    for lock in locks:
                        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (lock,))
                    # app 직접 UPDATE/rowlock 권한은 없다. rowlock/경합 거부는 기존
                    # SECDEF transition_canon_state의 expected_version 검사가 소유한다.
                    row = self._failure_parent(connection, context, processing_run_id)
                    if row is None or row[6] != context.actor_id or row[8] != processing_run_id:
                        return
                    if row[0] in {"completed", "failed", "policy_blocked"}:
                        return
                    if type(row[9]) is not bool:
                        raise DocumentUnderstandingError("DOCUMENT_PROCESSING_DATABASE_UNAVAILABLE", status=503)
                    source_current = row[9]
                    if source_current and row[3] != "processing":
                        return
                    if row[2] != before[2] or row[5] != payload or row[7] != before[7]:
                        raise DocumentUnderstandingError("SOURCE_PROCESSING_STATE_INVALID", status=409)
                    # 구 Version의 실제 실패 정리/알림은 유지하되 새 Version의 Source를
                    # waiting_model/failed로 바꾸거나 B1의 자격 소멸 격리로 commit하지 않는다.
                    collect_wait = source_current and notebook_id is not None
                    wait_eligible = (collect_wait and mode.mode in {"auto", "local_only"}
                                     and evidence.capability is not None
                                     and evidence.failure_kind in {"connection_availability", "role_runtime"})
                else:
                    row = connection.execute(
                        "SELECT pr.state,pr.version,sv.source_id,s.state,s.version "
                        "FROM processing_runs pr JOIN source_versions sv ON "
                        "sv.tenant_id=pr.tenant_id AND sv.workspace_id=pr.workspace_id "
                        "AND sv.record_id=pr.source_version_id JOIN sources s ON "
                        "s.tenant_id=sv.tenant_id AND s.workspace_id=sv.workspace_id "
                        "AND s.record_id=sv.source_id WHERE pr.record_id=%s",
                        (processing_run_id,),
                    ).fetchone()
                if row is None:
                    raise DocumentUnderstandingError("PROCESSING_RUN_NOT_FOUND", status=404)
                if str(row[0]) in {"completed", "failed", "policy_blocked"}:
                    # A late notification for an old Run must not fail a Source
                    # that a newer manual Run is currently processing.
                    return
                if str(row[0]) != "failed":
                    self._transition(
                        connection, context, entity_type="ProcessingRun",
                        aggregate_id=processing_run_id,
                        expected_version=int(row[1]), target_state="failed",
                        reason_code=reason,
                    )
                source_target = "waiting_model" if retryable or wait_eligible else "failed"
                if source_current and str(row[3]) == "processing":
                    self._transition(
                        connection, context, entity_type="Source",
                        aggregate_id=str(row[2]), expected_version=int(row[4]),
                        target_state=source_target, reason_code=reason,
                    )
                if collect_wait and source_target == "waiting_model":
                    self._record_failure_wait(connection, context, processing_run_id, row, mode, evidence, reason)
                if getattr(self._cloud_store, '_notification_inbox_enabled', False):
                    from .notification_events import enqueue_completed_event
                    enqueue_completed_event(connection, context, origin_event_id=self._opaque_id(
                        'tr', 'ProcessingRun', processing_run_id, 'failed', str(row[1])))
        except Exception as error:
            raise self._database_error(error) from None
