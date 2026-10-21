from __future__ import annotations

import re
import os
import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Sequence, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .provider_credentials import (
    PERSONAL_CREDENTIAL_SCHEMA_VERSION,
    EncryptedCredential,
    ProviderCredentialCipher,
    ProviderCredentialError,
)
from .user_provider_credentials import PostgresUserProviderCredentialService
from .provider_settings import ProviderSettingsError, provider_requires_credential, validate_provider_base_url
from .data_canon import canonical_json_bytes
from .document_readiness import (CurrentReprocessEligibility, RoleWaitEvidence, ReadinessDispatcherSettings,
    InternalCostExclusionPolicy)
from .document_understanding_adapter import DocumentUnderstandingError


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ACTIVE_PROVIDERS = {
    "text_generation": frozenset({
        "OLLAMA", "GROQ", "MISTRAL", "UPSTAGE", "OPENROUTER", "OMNIROUTE", "EOUL_GATEWAY", "CUSTOM",
    }),
    "image_understanding": frozenset({"UPSTAGE", "OLLAMA"}),
    "document_parsing": frozenset({"UPSTAGE"}),
    "embedding": frozenset({"SENTENCE_TRANSFORMERS"}),
}
_ROUTING_GATEWAYS = frozenset({"EOUL_GATEWAY", "OMNIROUTE"})
_ACTIVE_CAPABILITIES = frozenset(_ACTIVE_PROVIDERS)


def _qa_asr_enabled(workspace_id: str) -> bool:
    target = os.environ.get("DAON_QA_STRUCTURED_OLLAMA_WORKSPACE_ID", "")
    return bool(target and _SAFE_ID.fullmatch(target) and workspace_id == target)


class WorkspaceModelUnavailable(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True, repr=False)
class ReadinessPolicySnapshot:
    """DB metadata 시점의 판정과 정확 preimage. 최종 인가/Run 저장 증명은 아니다."""
    eligibility: CurrentReprocessEligibility
    frozen_context_json: str

    def __post_init__(self):
        try:
            if type(self.eligibility) is not CurrentReprocessEligibility or type(self.frozen_context_json) is not str:
                raise ValueError
            body = json.loads(self.frozen_context_json)
            encoded = canonical_json_bytes(body)
            current = self.eligibility
            if (encoded.decode("utf-8") != self.frozen_context_json
                    or hashlib.sha256(encoded).hexdigest() != current.frozen_context_digest
                    or type(body) is not dict or set(body) != {"schema_version", "scope", "mode", "role",
                        "authorization", "workspace_policy", "binding", "catalog", "egress", "cost"}
                    or type(body["schema_version"]) is not int or body["schema_version"] != 1
                    or body["scope"] != dict(zip(("tenant_id", "workspace_id", "actor_id", "notebook_id", "source_version_id"),
                        (current.tenant_id, current.workspace_id, current.actor_id, current.notebook_id, current.source_version_id)))
                    or body["role"] != current.capability):
                raise ValueError
            cost = body["cost"]
            if current.cost_state != "allowed":
                if cost != {"state": current.cost_state}:
                    raise ValueError
            else:
                if (type(cost) is not dict or set(cost) != {"state", "basis", "policy"}
                        or cost["state"] != "allowed" or cost["basis"] != "internal_operating_cost_excluded"
                        or type(cost["policy"]) is not dict
                        or not all((current.scope_enabled, current.authorized, current.source_current,
                            current.policy_allowed, current.local_only_allowed, current.candidate_available))):
                    raise ValueError
                policy = InternalCostExclusionPolicy(**cost["policy"])
                if (body["binding"]["connection_id"] != current.dependency_connection_id
                        or policy.as_dict() != _internal_cost_target(current, body["binding"])
                        or not _internal_cost_binding_allowed(body["binding"], current.capability, body["catalog"])):
                    raise ValueError
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError, DocumentUnderstandingError):
            raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503) from None


def _internal_cost_target(current, binding):
    return {**{key: getattr(current, key) for key in (
        "tenant_id", "workspace_id", "actor_id", "source_version_id", "capability")},
        "connection_id": current.dependency_connection_id, "model_id": binding["model_id"],
        "configuration_version": current.configuration_version, "endpoint": binding["endpoint"],
        "policy_version": "internal-operating-cost-excluded-v1"}


def _internal_cost_binding_allowed(binding, capability, catalog):
    if not isinstance(binding, dict) or not isinstance(catalog, dict):
        return False
    empty_reference = {"source": "system", "present": False, "nonce_present": False,
        "nonce_size": None, "version": 0, "schema_version": None, "key_version": None}
    if (binding.get("enabled") is not True or binding.get("verification_status") != "verified"
            or binding.get("access_mode") != "public" or binding.get("credential_requirement") != "none"
            or binding.get("credential_reference") != empty_reference):
        return False
    ollama = (binding.get("provider_code") == binding.get("adapter_type") == "OLLAMA"
        and binding.get("provider_kind") == "local" and capability in {"text_generation", "image_understanding"})
    from .audio_document_understanding import _ASR_REVISION, _ASR_DIGEST
    asr = (capability == "speech_to_text" and binding.get("provider_code") == "CUSTOM"
        and binding.get("adapter_type") == "asr_base_native" and binding.get("provider_kind") == "server_internal"
        and binding.get("endpoint") == "http://asr-base:8080" and binding.get("model_id") == "faster-whisper-base"
        and binding.get("qa_scope_enabled") is True
        and binding.get("native_asr_contract") == {"revision": _ASR_REVISION, "model_digest": _ASR_DIGEST})
    models = catalog.get("models")
    if not (ollama or asr) or type(models) is not list or catalog.get("connection_id") != binding.get("connection_id"):
        return False
    return any(type(m) is dict and m.get("model_id") == binding.get("model_id") and m.get("allowed") is True
        and m.get("catalog_status") == "ready" and type(m.get("effective_capabilities")) is list
        and capability in m["effective_capabilities"] for m in models)


class WorkspaceModelDefaultsError(RuntimeError):
    def __init__(self, code: str, status: int = 400, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WorkspaceModelDefaultsContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None
            for value in (
                self.tenant_id, self.workspace_id, self.actor_id,
                self.trace_id, self.policy_version,
            )
        ):
            raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_DEFAULTS_CONTEXT_INVALID")


class PostgresWorkspaceModelDefaultsService:
    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _cloud(context: WorkspaceModelDefaultsContext, operation: str) -> CloudAccessContext:
        return CloudAccessContext(context.tenant_id, context.workspace_id, context.actor_id, operation)

    @staticmethod
    def _snapshot(connection: Any, context: WorkspaceModelDefaultsContext) -> dict[str, object]:
        models = connection.execute(
            "SELECT c.connection_id,c.provider_code,c.display_name,c.encrypted_credential IS NOT NULL,"
            "c.credential_version,c.verification_status,c.version,m.model_id,m.effective_capabilities,"
            "m.catalog_status,m.catalog_version,c.adapter_type,c.base_url,c.credential_requirement FROM system_provider_connections c "
            "JOIN system_provider_models m ON m.connection_id=c.connection_id "
            "JOIN system_provider_allowed_models a ON a.connection_id=m.connection_id AND a.model_id=m.model_id "
            "LEFT JOIN user_provider_credentials u ON u.connection_id=c.connection_id "
            "AND u.tenant_id=%s AND u.user_id=%s AND u.verification_status='verified' "
            f"AND u.credential_schema_version={PERSONAL_CREDENTIAL_SCHEMA_VERSION} "
            "WHERE c.enabled=true AND m.catalog_status='ready' "
            "AND ((c.access_mode='public' AND c.verification_status='verified') "
            "OR (c.access_mode='personal' AND u.connection_id IS NOT NULL)) "
            "ORDER BY c.display_name,c.connection_id,m.model_id",
            (context.tenant_id, context.actor_id),
        ).fetchall()
        defaults = connection.execute(
            "SELECT capability,connection_id,model_id,version FROM workspace_model_defaults "
            "WHERE tenant_id=%s AND workspace_id=%s ORDER BY capability",
            (context.tenant_id, context.workspace_id),
        ).fetchall()
        active = _ACTIVE_CAPABILITIES | ({"speech_to_text"} if _qa_asr_enabled(context.workspace_id) else set())
        def capabilities(row):
            items = set(row[8] or ()) & active
            if "speech_to_text" in items and not (
                    len(row) >= 14 and str(row[1]) == "CUSTOM" and str(row[7]) == "faster-whisper-base"
                    and str(row[11]) == "asr_base_native" and str(row[12]) == "http://asr-base:8080"
                    and str(row[13]) == "none" and not bool(row[3]) and int(row[4]) == 0):
                items.remove("speech_to_text")
            return [str(item) for item in (row[8] or ()) if item in items]
        available = [
            {
                "connection_id": str(row[0]), "provider_code": str(row[1]),
                "display_name": str(row[2]), "configured": bool(row[3]),
                "credential_version": int(row[4]), "verification_status": str(row[5]),
                "connection_version": int(row[6]), "model_id": str(row[7]),
                "effective_capabilities": capabilities(row),
                "catalog_status": str(row[9]), "catalog_version": int(row[10]),
            }
            for row in models
            if capabilities(row)
        ]
        default_views = [
            {"capability": str(row[0]), "connection_id": str(row[1]),
             "model_id": str(row[2]), "version": int(row[3])}
            for row in defaults if str(row[0]) in active
        ]
        etag_payload = {"available_models": available, "defaults": default_views}
        etag = '"workspace-model-defaults-' + hashlib.sha256(
            canonical_json_bytes(etag_payload)
        ).hexdigest()[:24] + '"'
        return {
            "workspace_id": context.workspace_id,
            "available_models": available,
            "defaults": default_views,
            "version": max((item["version"] for item in default_views), default=0),
            "etag": etag,
        }

    def read(self, context: WorkspaceModelDefaultsContext) -> dict[str, object]:
        try:
            with self._store._transaction(self._cloud(context, "workspace_model_defaults.read")) as connection:
                return self._snapshot(connection, context)
        except CloudDatabaseError as error:
            raise WorkspaceModelDefaultsError(
                "WORKSPACE_MODEL_DEFAULTS_UNAVAILABLE", 503, retryable=error.retryable,
            ) from None

    def save(
        self, context: WorkspaceModelDefaultsContext, *, capability: str,
        connection_id: str, model_id: str, expected_version: int,
        expected_etag: str, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        native_asr = capability == "speech_to_text" and _qa_asr_enabled(context.workspace_id)
        if capability not in _ACTIVE_CAPABILITIES and not native_asr:
            raise WorkspaceModelDefaultsError("PROVIDER_CAPABILITY_UNSUPPORTED", 409)
        operation = "workspace_model_defaults.save"
        fingerprint = hashlib.sha256(canonical_json_bytes({
            "capability": capability, "connection_id": connection_id, "model_id": model_id,
            "expected_version": expected_version, "expected_etag": expected_etag,
        })).hexdigest()
        try:
            with self._store._transaction(self._cloud(context, operation)) as connection:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{context.tenant_id}|{context.workspace_id}|{context.actor_id}|{operation}|{idempotency_key}",),
                )
                replay = connection.execute(
                    "SELECT request_fingerprint,result FROM idempotency_records "
                    "WHERE tenant_id=%s AND workspace_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation, idempotency_key),
                ).fetchone()
                if replay is not None:
                    if str(replay[0]) != fingerprint:
                        raise WorkspaceModelDefaultsError("IDEMPOTENCY_KEY_REUSED", 409)
                    return cast(dict[str, object], replay[1]), True
                current_snapshot = self._snapshot(connection, context)
                if current_snapshot["etag"] != expected_etag:
                    raise WorkspaceModelDefaultsError("VERSION_CONFLICT", 409)
                current = connection.execute(
                    "SELECT version FROM workspace_model_defaults WHERE tenant_id=%s AND workspace_id=%s AND capability=%s FOR UPDATE",
                    (context.tenant_id, context.workspace_id, capability),
                ).fetchone()
                current_version = 0 if current is None else int(current[0])
                if current_version != expected_version:
                    raise WorkspaceModelDefaultsError("VERSION_CONFLICT", 409)
                native_filter = (
                    " AND c.provider_code='CUSTOM' AND c.adapter_type='asr_base_native' "
                    "AND c.base_url='http://asr-base:8080' AND c.credential_requirement='none' "
                    "AND c.access_mode='public' AND c.encrypted_credential IS NULL AND c.credential_version=0 "
                    "AND m.model_id='faster-whisper-base'" if native_asr else ""
                )
                allowed = connection.execute(
                    "SELECT 1 FROM system_provider_connections c JOIN system_provider_models m ON m.connection_id=c.connection_id "
                    "JOIN system_provider_allowed_models a ON a.connection_id=m.connection_id AND a.model_id=m.model_id "
                    "LEFT JOIN user_provider_credentials u ON u.connection_id=c.connection_id "
                    "AND u.tenant_id=%s AND u.user_id=%s AND u.verification_status='verified' "
                    f"AND u.credential_schema_version={PERSONAL_CREDENTIAL_SCHEMA_VERSION} "
                    "WHERE c.connection_id=%s AND m.model_id=%s AND c.enabled=true "
                    "AND ((c.access_mode='public' AND c.verification_status='verified') "
                    "OR (c.access_mode='personal' AND u.connection_id IS NOT NULL)) "
                    "AND m.catalog_status='ready' AND %s=ANY(m.effective_capabilities)" + native_filter,
                    (context.tenant_id, context.actor_id, connection_id, model_id, capability),
                ).fetchone()
                if allowed is None:
                    raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE", 409)
                next_version = current_version + 1
                connection.execute(
                    "INSERT INTO workspace_model_defaults (tenant_id,workspace_id,capability,connection_id,model_id,version,updated_by,trace_id,policy_version) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (tenant_id,workspace_id,capability) DO UPDATE SET "
                    "connection_id=excluded.connection_id,model_id=excluded.model_id,version=excluded.version,updated_by=excluded.updated_by,"
                    "trace_id=excluded.trace_id,policy_version=excluded.policy_version,updated_at=now()",
                    (context.tenant_id, context.workspace_id, capability, connection_id, model_id,
                     next_version, context.actor_id, context.trace_id, context.policy_version),
                )
                result = self._snapshot(connection, context)
                connection.execute(
                    "INSERT INTO idempotency_records (tenant_id,workspace_id,actor_id,operation,idempotency_key,request_fingerprint,result,status,expires_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,'completed',%s)",
                    (context.tenant_id, context.workspace_id, context.actor_id, operation,
                     idempotency_key, fingerprint, Jsonb(result), datetime.now(timezone.utc) + timedelta(hours=24)),
                )
                return result, False
        except WorkspaceModelDefaultsError:
            raise
        except CloudDatabaseError as error:
            raise WorkspaceModelDefaultsError(
                "WORKSPACE_MODEL_DEFAULTS_UNAVAILABLE", 503, retryable=error.retryable,
            ) from None


@dataclass(frozen=True, slots=True)
class WorkspaceModelContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    credential_source: str = "system"

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
        )) or self.credential_source not in {"system", "user"}:
            raise WorkspaceModelUnavailable("WORKSPACE_MODEL_CONTEXT_INVALID")


@dataclass(slots=True)
class ResolvedModel:
    connection_id: str
    provider_code: str
    model_id: str
    capability: str
    base_url: str
    credential_version: int
    default_version: int
    catalog_version: int
    provider_kind: str
    routing_owner: str
    daon_fallback_allowed: bool
    _credential: bytearray | None = field(repr=False, compare=False)
    _released: bool = field(default=False, repr=False, compare=False)
    adapter_type: str = ""

    @property
    def profile_id(self) -> str:
        return self.connection_id

    @property
    def deployment_id(self) -> str:
        return f"{self.connection_id}:{self.model_id}"

    @property
    def binding_version(self) -> int:
        return self.default_version

    def credential_text(self, *, required: bool = True) -> str | None:
        if self._released:
            raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_RELEASED")
        if self._credential is None:
            if required:
                raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_REQUIRED")
            return None
        try:
            return bytes(self._credential).decode("utf-8")
        except UnicodeDecodeError:
            raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_INVALID") from None

    def release(self) -> None:
        if self._credential is not None:
            self._credential[:] = b"\0" * len(self._credential)
        self._released = True


class PostgresWorkspaceModelResolver:
    supports_user_credential_fallback = True

    def __init__(
        self, store: PostgresCloudStore, cipher: ProviderCredentialCipher,
        user_credentials: PostgresUserProviderCredentialService | None = None,
    ) -> None:
        self._store = store
        self._cipher = cipher
        self._user_credentials = user_credentials

    @staticmethod
    def _cloud(context: WorkspaceModelContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            "workspace_model.resolve",
        )

    @contextmanager
    def resolve(
        self, context: WorkspaceModelContext, capability: str,
    ) -> Iterator[ResolvedModel]:
        native_asr = capability == "speech_to_text" and _qa_asr_enabled(context.workspace_id)
        providers = frozenset({"CUSTOM"}) if native_asr else _ACTIVE_PROVIDERS.get(capability)
        if providers is None:
            raise WorkspaceModelUnavailable("PROVIDER_EXECUTION_NOT_READY")
        try:
            with self._store._transaction(self._cloud(context)) as connection:
                row = connection.execute(
                    "SELECT d.version,d.connection_id,d.model_id,c.provider_code,c.base_url,"
                    "c.encrypted_credential,c.credential_nonce,c.encryption_key_version,"
                    "c.credential_schema_version,c.credential_version,c.enabled,c.verification_status,"
                    "m.effective_capabilities,m.catalog_status,m.catalog_version,"
                    "c.access_mode,c.credential_requirement,c.adapter_type "
                    "FROM workspace_model_defaults d "
                    "JOIN system_provider_connections c ON c.connection_id=d.connection_id "
                    "JOIN system_provider_models m ON m.connection_id=d.connection_id AND m.model_id=d.model_id "
                    "JOIN system_provider_allowed_models a ON a.connection_id=m.connection_id AND a.model_id=m.model_id "
                    "WHERE d.tenant_id=%s AND d.workspace_id=%s AND d.capability=%s",
                    (context.tenant_id, context.workspace_id, capability),
                ).fetchone()
        except CloudDatabaseError as error:
            raise WorkspaceModelUnavailable(
                "WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", retryable=error.retryable,
            ) from None
        with self._resolved_row(context, capability, row) as resolved:
            yield resolved

    @contextmanager
    def resolve_candidate(self, context: WorkspaceModelContext, candidate) -> Iterator[ResolvedModel]:
        """Auto-only exact connection/model/source. Never consult a Workspace default."""
        try:
            with self._store._transaction(self._cloud(context)) as connection:
                row = connection.execute(
                    "SELECT c.version,c.connection_id,m.model_id,c.provider_code,c.base_url,"
                    "c.encrypted_credential,c.credential_nonce,c.encryption_key_version,"
                    "c.credential_schema_version,c.credential_version,c.enabled,c.verification_status,"
                    "m.effective_capabilities,m.catalog_status,m.catalog_version,"
                    "c.access_mode,c.credential_requirement,c.adapter_type "
                    "FROM system_provider_connections c JOIN system_provider_models m ON m.connection_id=c.connection_id "
                    "JOIN system_provider_allowed_models a ON a.connection_id=m.connection_id AND a.model_id=m.model_id "
                    "WHERE c.connection_id=%s AND m.model_id=%s FOR SHARE OF c,m,a",
                    (candidate.connection_id,candidate.model_id)).fetchone()
        except CloudDatabaseError as error:
            raise WorkspaceModelUnavailable("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE",retryable=error.retryable) from None
        if row is None:
            raise WorkspaceModelUnavailable("PROVIDER_MODEL_NOT_READY")
        expected_mode="personal" if candidate.credential_source=="personal" else "public"
        if str(row[15])!=expected_mode or (int(row[0]),int(row[14]),int(row[0]))!=(
                candidate.connection_version,candidate.catalog_version,candidate.endpoint_version):
            raise WorkspaceModelUnavailable("ROUTING_CANDIDATE_CHANGED")
        with self._resolved_row(context,"text_generation",row) as selected:
            if selected.credential_version!=candidate.credential_version:
                raise WorkspaceModelUnavailable("ROUTING_CANDIDATE_CHANGED")
            yield selected

    @contextmanager
    def _resolved_row(self, context: WorkspaceModelContext, capability: str, row) -> Iterator[ResolvedModel]:
        native_asr = capability == "speech_to_text" and _qa_asr_enabled(context.workspace_id)
        providers = frozenset({"CUSTOM"}) if native_asr else _ACTIVE_PROVIDERS.get(capability)
        if providers is None:
            raise WorkspaceModelUnavailable("PROVIDER_EXECUTION_NOT_READY")
        if row is None:
            raise WorkspaceModelUnavailable("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE")
        values: Sequence[object] = row
        connection_id = str(values[1])
        model_id = str(values[2])
        provider_code = str(values[3])
        if provider_code not in providers:
            raise WorkspaceModelUnavailable("PROVIDER_EXECUTION_NOT_READY")
        access_mode = str(values[15])
        if not bool(values[10]) or (access_mode == "public" and str(values[11]) != "verified"):
            raise WorkspaceModelUnavailable("PROVIDER_CONNECTION_UNAVAILABLE")
        if str(values[13]) != "ready":
            raise WorkspaceModelUnavailable("PROVIDER_MODEL_NOT_READY")
        if capability not in set(values[12] or ()):
            raise WorkspaceModelUnavailable("PROVIDER_CAPABILITY_UNSUPPORTED")
        try:
            if native_asr:
                if (str(values[17]) != "asr_base_native" or str(values[4]) != "http://asr-base:8080"
                        or model_id != "faster-whisper-base" or access_mode != "public"
                        or str(values[16]) != "none" or values[5] is not None
                        or any(value is not None for value in values[6:9]) or int(values[9]) != 0):
                    raise WorkspaceModelUnavailable("PROVIDER_CONNECTION_UNAVAILABLE")
                base_url = "http://asr-base:8080"
            else:
                base_url = validate_provider_base_url(provider_code, str(values[4]))
        except ProviderSettingsError:
            raise WorkspaceModelUnavailable("PROVIDER_CONNECTION_UNAVAILABLE") from None

        credential_version = int(values[9])
        credential: bytearray | None = None
        encrypted = values[5]
        system_credential_failed = encrypted is None
        if encrypted is not None and access_mode == "public":
            if any(value is None for value in values[6:9]) or credential_version < 1:
                raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_INVALID")
            sealed = EncryptedCredential(
                ciphertext=bytes(encrypted), nonce=bytes(values[6]),
                encryption_key_version=int(values[7]), credential_version=credential_version,
                schema_version=int(values[8]),
            )
            try:
                credential = bytearray(self._cipher.decrypt(
                    connection_id, provider_code, credential_version, sealed,
                ))
            except ProviderCredentialError:
                system_credential_failed = True

        if access_mode == "personal":
            if self._user_credentials is None:
                raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_REQUIRED")
            personal = self._user_credentials.resolve_credential(
                tenant_id=context.tenant_id, user_id=context.actor_id, connection_id=connection_id,
            )
            if personal.source != "user" or personal.credential is None:
                raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_REQUIRED")
            credential = bytearray(personal.credential)
            credential_version = personal.credential_version
            system_credential_failed = False
        credential_required = (
            str(values[16]) == "required" if provider_code == "CUSTOM"
            else provider_requires_credential(provider_code, base_url)
        )
        if system_credential_failed and credential_required:
            raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_REQUIRED")

        resolved = ResolvedModel(
            connection_id=connection_id, provider_code=provider_code, model_id=model_id,
            capability=capability, base_url=base_url, credential_version=credential_version,
            default_version=int(values[0]), catalog_version=int(values[14]),
            provider_kind="server_internal" if native_asr else "local" if provider_code in {"OLLAMA", "SENTENCE_TRANSFORMERS"} else "external_api",
            routing_owner=(
                "local_runtime" if native_asr else "gateway" if provider_code in _ROUTING_GATEWAYS
                else "local_runtime" if provider_code == "SENTENCE_TRANSFORMERS"
                else "provider"
            ),
            daon_fallback_allowed=not native_asr and provider_code not in _ROUTING_GATEWAYS | {"SENTENCE_TRANSFORMERS"},
            _credential=credential,
            adapter_type=str(values[17]),
        )
        try:
            yield resolved
        finally:
            resolved.release()


def _readiness_metadata_object(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503)
    return value


def _readiness_metadata_integer(value, minimum=0, nullable=False):
    if nullable and value is None:
        return
    if type(value) is not int or value < minimum:
        raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503)


@dataclass(frozen=True, slots=True, repr=False)
class ReadinessCatalogSnapshot:
    canonical_json: str
    digest_sha256: str

    def __post_init__(self):
        try:
            if type(self.canonical_json) is not str or type(self.digest_sha256) is not str:
                raise ValueError
            body = json.loads(self.canonical_json)
            if (type(body) is not dict or set(body) != {"schema_version", "connection_id", "models"}
                    or type(body["schema_version"]) is not int or body["schema_version"] != 1
                    or type(body["connection_id"]) is not str or not _SAFE_ID.fullmatch(body["connection_id"])
                    or type(body["models"]) is not list or canonical_json_bytes(body).decode() != self.canonical_json
                    or hashlib.sha256(self.canonical_json.encode()).hexdigest() != self.digest_sha256):
                raise ValueError
        except (ValueError, TypeError, KeyError, RecursionError):
            raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503) from None


def read_readiness_catalog(connection, connection_id: str) -> ReadinessCatalogSnapshot:
    """현재 판정/향후 관측 observer의 동일 DB effective catalog·allowlist canonical.

    Provider raw 응답/ETag/발견시각/단순 catalog_version은 preimage가 아니다.
    GET 목록 검증의 내용이며 모델 generation-ready 증명도 아니다.
    """
    try:
        if type(connection_id) is not str or not _SAFE_ID.fullmatch(connection_id):
            raise ValueError
        rows = connection.execute(
            "SELECT /* readiness-metadata-catalog */ jsonb_build_object('model_id',m.model_id,'effective_capabilities',m.effective_capabilities,"
            "'catalog_status',m.catalog_status,'catalog_version',m.catalog_version,'allowed',a.model_id IS NOT NULL) "
            "FROM public.system_provider_models m LEFT JOIN public.system_provider_allowed_models a "
            "ON a.connection_id=m.connection_id AND a.model_id=m.model_id WHERE m.connection_id=%s ORDER BY m.model_id",
            (connection_id,)).fetchall()
        models = []
        for (value,) in rows:
            _readiness_metadata_object(value, ("model_id", "effective_capabilities", "catalog_status", "catalog_version", "allowed"))
            _readiness_metadata_integer(value["catalog_version"], 1)
            if (type(value["model_id"]) is not str or not 1 <= len(value["model_id"]) <= 256
                    or any(ord(c) < 32 for c in value["model_id"])
                    or type(value["effective_capabilities"]) is not list or type(value["allowed"]) is not bool
                    or any(type(c) is not str or not _SAFE_ID.fullmatch(c) for c in value["effective_capabilities"])
                    or value["catalog_status"] not in ("ready", "stale", "unavailable")):
                raise ValueError
            models.append({"model_id": value["model_id"], "effective_capabilities": sorted(set(value["effective_capabilities"])),
                           "catalog_status": value["catalog_status"], "allowed": value["allowed"]})
        models.sort(key=lambda value: value["model_id"])
        if len({v["model_id"] for v in models}) != len(models):
            raise ValueError
        canonical = canonical_json_bytes({"schema_version": 1, "connection_id": connection_id, "models": models})
        return ReadinessCatalogSnapshot(canonical.decode(), hashlib.sha256(canonical).hexdigest())
    except WorkspaceModelDefaultsError:
        raise
    except Exception:
        raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503) from None


def _readiness_credential_reference(value, *, source):
    """원문/cipher/nonce bytes를 읽지 않는다. metadata 유효성과 복호화 성공은 별개다."""
    if value is None:
        return None, False
    for key in ("has_credential", "has_nonce"):
        if type(value[key]) is not bool:
            raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503)
    for key in ("nonce_size", "encryption_key_version", "credential_schema_version"):
        _readiness_metadata_integer(value[key], nullable=True)
    _readiness_metadata_integer(value["credential_version"])
    empty = (not value["has_credential"] and not value["has_nonce"] and value["credential_version"] == 0
             and all(value[k] is None for k in ("nonce_size", "encryption_key_version", "credential_schema_version")))
    expected = PERSONAL_CREDENTIAL_SCHEMA_VERSION if source == "user" else 1
    valid = (value["has_credential"] and value["has_nonce"] and value["nonce_size"] == 12
        and value["credential_version"] > 0 and value["credential_schema_version"] == expected
        and value["encryption_key_version"] is not None and value["encryption_key_version"] > 0)
    reference = {"source": source, "present": value["has_credential"], "nonce_present": value["has_nonce"],
        "nonce_size": value["nonce_size"], "version": value["credential_version"],
        "schema_version": value["credential_schema_version"], "key_version": value["encryption_key_version"]}
    return reference, bool(valid or (source == "system" and empty))


def read_document_readiness_policy(connection, context, wait, *, settings=None) -> ReadinessPolicySnapshot:
    """동일 Tx metadata-only producer. resolver/Key/Provider 호출·원장 쓰기는 하지 않는다.

    반환 bool은 R2의 잠금 아래 현재 인가/부모/관측/정책 재조회 대체물이 아니다.
    비용 producer와 Source/Run별 외부 원문 승인은 없으므로 추론 허용을 발명하지 않는다.
    """
    from types import SimpleNamespace
    from .authorization import Action, Role, Permission, _checked_id
    from .document_index_postgres import _source_access_allowed
    from .notification_source_access import _special_source_access_allowed
    from .open_law_source_access import _current_auth_grant, _AuthReadConnection, _CurrentAuthorizationRules, OpenLawAccessError
    from .organization_access import OrganizationAccessBridge
    from .organization_membership import OrganizationWorkflowError
    from .egress_policy import EgressPolicyPayload, resolve_effective_payload

    try:
        settings = ReadinessDispatcherSettings() if settings is None else settings
        if type(wait) is not RoleWaitEvidence or type(settings) is not ReadinessDispatcherSettings:
            raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_DEFAULTS_CONTEXT_INVALID")
        if (context.tenant_id, context.workspace_id, context.actor_id) != (wait.tenant_id, wait.workspace_id, wait.actor_id):
            raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_DEFAULTS_CONTEXT_INVALID")
        scoped = SimpleNamespace(tenant_id=context.tenant_id, workspace_id=context.workspace_id,
            actor_id=context.actor_id, trace_id=context.trace_id, policy_version=context.policy_version,
            notebook_id=wait.notebook_id)
        edit = _current_auth_grant(scoped, connection, Action.EDIT)
        analyze = _current_auth_grant(scoped, connection, Action.ANALYZE)
        for grant, action in ((edit, Action.EDIT), (analyze, Action.ANALYZE)):
            if (grant.allowed is not True or (grant.tenant_id, grant.workspace_id, grant.actor_id) !=
                    (wait.tenant_id, wait.workspace_id, wait.actor_id) or grant.action is not action):
                raise OpenLawAccessError("OPEN_LAW_ACCESS_DENIED")
        OrganizationAccessBridge(postgres=True).assert_organization_active(connection=_AuthReadConnection(connection),
            tenant_id=context.tenant_id, user_id=context.actor_id)
        if (not _source_access_allowed(connection, scoped, wait.source_version_id)
                or not _special_source_access_allowed(connection, scoped, wait.source_version_id)):
            raise OpenLawAccessError("OPEN_LAW_ACCESS_DENIED")
        source = connection.execute(
            "SELECT /* readiness-metadata-source */ sv.source_id FROM public.sources s "
            "JOIN public.source_versions sv ON (sv.tenant_id,sv.workspace_id,sv.source_id)=(s.tenant_id,s.workspace_id,s.record_id) "
            "JOIN public.notebooks n ON (n.tenant_id,n.workspace_id,n.notebook_id)=(s.tenant_id,s.workspace_id,%s) "
            "JOIN public.notebook_bindings b ON (b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)="
            "(n.tenant_id,n.workspace_id,n.notebook_id,s.record_id,sv.record_id) AND b.binding_kind='source' "
            "WHERE (s.tenant_id,s.workspace_id,s.record_id,sv.record_id)=(%s,%s,%s,%s) "
            "AND (s.created_by=%s OR %s) AND s.state NOT IN ('disabled','expired','deleting','deleted') "
            "AND NOT EXISTS(SELECT 1 FROM public.source_versions newer WHERE "
            "(newer.tenant_id,newer.workspace_id,newer.source_id)=(sv.tenant_id,sv.workspace_id,sv.source_id) AND newer.version>sv.version) "
            "AND NOT EXISTS(SELECT 1 FROM public.notebook_source_unbindings u WHERE "
            "(u.tenant_id,u.workspace_id,u.notebook_id,u.source_id,u.source_version_id)=(b.tenant_id,b.workspace_id,b.notebook_id,b.record_id,b.version_id)) "
            "AND NOT EXISTS(SELECT 1 FROM public.notebook_deletion_requests d WHERE "
            "(d.tenant_id,d.workspace_id,d.notebook_id)=(n.tenant_id,n.workspace_id,n.notebook_id)) "
            "AND NOT EXISTS(SELECT 1 FROM public.deletion_requests d WHERE "
            "(d.tenant_id,d.workspace_id,d.source_id)=(s.tenant_id,s.workspace_id,s.record_id) AND NOT d.source_active)",
            (wait.notebook_id, wait.tenant_id, wait.workspace_id, wait.source_id, wait.source_version_id, wait.actor_id,
             edit.role in {Role.PERSONAL_OWNER, Role.ORGANIZATION_ADMIN, Role.WORKSPACE_ADMIN}),
        ).fetchone()
        if source is None or source[0] != wait.source_id:
            raise WorkspaceModelDefaultsError("SOURCE_NOT_FOUND", 404)
        row = connection.execute(
            "SELECT /* readiness-metadata-workspace */ jsonb_build_object('workspace_kind',workspace_kind,"
            "'data_area',data_area,'cost_limit_cents',cost_limit_cents) FROM public.identity_auth_workspaces "
            "WHERE tenant_id=%s AND workspace_id=%s", (wait.tenant_id, wait.workspace_id)).fetchone()
        workspace = _readiness_metadata_object(None if row is None else row[0],
                                               ("workspace_kind", "data_area", "cost_limit_cents"))
        if workspace["workspace_kind"] not in ("personal", "organization"):
            raise ValueError
        # 기존 authorization의 1..128자 safe ID 계약을 그대로 사용한다. 새 영역 enum은 없다.
        workspace["data_area"] = _checked_id(workspace["data_area"])
        _readiness_metadata_integer(workspace["cost_limit_cents"])
        rules = connection.execute(
            "SELECT /* readiness-metadata-rules */ 'tenant',permission,effect,locked FROM public.identity_auth_tenant_permission_rules WHERE tenant_id=%s "
            "UNION ALL SELECT 'workspace',permission,effect,locked FROM public.identity_auth_workspace_permission_rules "
            "WHERE tenant_id=%s AND workspace_id=%s ORDER BY 1,2", (wait.tenant_id, wait.tenant_id, wait.workspace_id)).fetchall()
        normalized_rules = []
        for scope, permission, effect, locked in rules:
            if (scope not in ("tenant", "workspace") or type(permission) is not str or not _SAFE_ID.fullmatch(permission)
                    or effect not in ("grant", "deny") or type(locked) is not int or locked not in (0, 1)):
                raise ValueError
            normalized_rules.append([scope, permission, effect, bool(locked)])
        if len({(r[0], r[1]) for r in normalized_rules}) != len(normalized_rules):
            raise ValueError
        normalized_rules.sort()
        policy_rows = connection.execute(
            "SELECT /* readiness-metadata-egress */ b.scope_type,p.canonical_json FROM public.egress_policy_bindings b "
            "JOIN public.egress_policy_versions p ON p.tenant_id=b.tenant_id AND p.policy_version_id=b.policy_version_id "
            "WHERE b.tenant_id=%s AND b.organization_id=%s AND b.current=true AND b.active=true AND p.state='active' "
            "AND ((b.scope_type='organization' AND b.workspace_id IS NULL) OR (b.scope_type='workspace' AND b.workspace_id=%s))",
            (wait.tenant_id, wait.tenant_id, wait.workspace_id)).fetchall()
        egress = {"organization": None, "workspace": None}
        for scope, payload in policy_rows:
            if scope not in egress or egress[scope] is not None:
                raise ValueError
            policy_keys = ("mode", "allowed_provider_kinds", "allowed_destinations", "classification",
                "max_bytes", "masking_required", "redaction_required", "required_approver")
            if isinstance(payload, dict) and "question_step_up_required" in payload:
                policy_keys += ("question_step_up_required",)
            _readiness_metadata_object(payload, policy_keys)
            if type(payload["allowed_provider_kinds"]) is not list or type(payload["allowed_destinations"]) is not list:
                raise ValueError
            value = EgressPolicyPayload(**{**payload, "allowed_provider_kinds": tuple(sorted(payload["allowed_provider_kinds"])),
                                            "allowed_destinations": tuple(sorted(payload["allowed_destinations"]))})
            egress[scope] = value
        effective = None if any(v is None for v in egress.values()) else resolve_effective_payload(
            egress["organization"], egress["workspace"])[0]
        binding = None
        catalog = None
        catalog_digest = None
        candidate = False
        local = False
        dependency = None
        configuration_version = 0
        if wait.capability is not None:
            row = connection.execute(
                "SELECT /* readiness-metadata-binding */ jsonb_build_object('connection_id',d.connection_id,'model_id',d.model_id,"
                "'default_version',d.version,'provider_code',c.provider_code,'base_url',c.base_url,'enabled',c.enabled,"
                "'verification_status',c.verification_status,'configuration_version',c.version,'access_mode',c.access_mode,"
                "'credential_requirement',c.credential_requirement,'adapter_type',c.adapter_type,"
                "'has_credential',c.encrypted_credential IS NOT NULL,'has_nonce',c.credential_nonce IS NOT NULL,"
                "'nonce_size',octet_length(c.credential_nonce),'encryption_key_version',c.encryption_key_version,"
                "'credential_schema_version',c.credential_schema_version,'credential_version',c.credential_version) "
                "FROM public.workspace_model_defaults d JOIN public.system_provider_connections c ON c.connection_id=d.connection_id "
                "WHERE d.tenant_id=%s AND d.workspace_id=%s AND d.capability=%s",
                (wait.tenant_id, wait.workspace_id, wait.capability)).fetchone()
            if row is not None:
                raw = _readiness_metadata_object(row[0], ("connection_id", "model_id", "default_version", "provider_code", "base_url",
                    "enabled", "verification_status", "configuration_version", "access_mode", "credential_requirement", "adapter_type",
                    "has_credential", "has_nonce", "nonce_size", "encryption_key_version", "credential_schema_version", "credential_version"))
                if (any(type(raw[k]) is not str or not _SAFE_ID.fullmatch(raw[k]) for k in ("connection_id", "provider_code", "adapter_type"))
                        or type(raw["model_id"]) is not str or not 1 <= len(raw["model_id"]) <= 256
                        or any(ord(c) < 32 for c in raw["model_id"]) or type(raw["enabled"]) is not bool
                        or raw["verification_status"] not in ("verified", "failed", "unverified")
                        or raw["access_mode"] not in ("public", "personal") or raw["credential_requirement"] not in ("required", "none")):
                    raise ValueError
                _readiness_metadata_integer(raw["default_version"], 1)
                _readiness_metadata_integer(raw["configuration_version"], 1)
                dependency, configuration_version = raw["connection_id"], raw["configuration_version"]
                credential, credential_valid = _readiness_credential_reference(raw, source="system")
                if raw["access_mode"] == "personal":
                    row = connection.execute(
                        "SELECT /* readiness-metadata-personal */ jsonb_build_object('provider_code',u.provider_code,"
                        "'verification_status',u.verification_status,'has_credential',u.encrypted_credential IS NOT NULL,"
                        "'has_nonce',u.credential_nonce IS NOT NULL,'nonce_size',octet_length(u.credential_nonce),"
                        "'encryption_key_version',u.encryption_key_version,'credential_schema_version',u.credential_schema_version,"
                        "'credential_version',u.credential_version) FROM public.user_provider_credentials u "
                        "WHERE u.tenant_id=%s AND u.user_id=%s AND u.connection_id=%s", (wait.tenant_id, wait.actor_id, dependency)).fetchone()
                    personal = None if row is None else _readiness_metadata_object(row[0], ("provider_code", "verification_status",
                        "has_credential", "has_nonce", "nonce_size", "encryption_key_version", "credential_schema_version", "credential_version"))
                    credential, credential_valid = _readiness_credential_reference(personal, source="user")
                    credential_valid = bool(credential_valid and personal["provider_code"] == raw["provider_code"]
                                            and personal["verification_status"] == "verified")
                    if credential is not None:
                        credential["verification_status"] = personal["verification_status"]
                current_catalog = read_readiness_catalog(connection, dependency)
                catalog, catalog_digest = json.loads(current_catalog.canonical_json), current_catalog.digest_sha256
                selected = next((v for v in catalog["models"] if v["model_id"] == raw["model_id"]), None)
                asr = wait.capability == "speech_to_text"
                providers = {"CUSTOM"} if asr else _ACTIVE_PROVIDERS.get(wait.capability, ())
                if asr:
                    from .audio_document_understanding import _ASR_REVISION, _ASR_DIGEST
                    # 고정 ASR 주소 비교 전에 기존 URL 검사로 userinfo/query/fragment를 거부한다.
                    endpoint = validate_provider_base_url("OLLAMA", raw["base_url"])
                    asr_valid = (_qa_asr_enabled(wait.workspace_id) and raw["adapter_type"] == "asr_base_native"
                        and raw["base_url"] == endpoint == "http://asr-base:8080" and raw["model_id"] == "faster-whisper-base"
                        and raw["access_mode"] == "public" and raw["credential_requirement"] == "none"
                        and not raw["has_credential"] and not raw["has_nonce"] and raw["credential_version"] == 0
                        and all(raw[k] is None for k in ("nonce_size", "encryption_key_version", "credential_schema_version")))
                else:
                    endpoint = validate_provider_base_url(raw["provider_code"], raw["base_url"])
                    asr_valid = True
                local = bool(asr and asr_valid or not asr and raw["provider_code"] in {"OLLAMA", "SENTENCE_TRANSFORMERS"})
                kind = "server_internal" if asr else "local" if local else "external_api"
                required = (raw["credential_requirement"] == "required" if raw["provider_code"] == "CUSTOM"
                            else provider_requires_credential(raw["provider_code"], endpoint))
                key_available = bool(credential_valid and (not required or credential is not None and credential["present"]))
                candidate = bool(raw["provider_code"] in providers and raw["enabled"]
                    and (raw["access_mode"] == "personal" or raw["verification_status"] == "verified")
                    and key_available and asr_valid and selected is not None and selected["allowed"]
                    and selected["catalog_status"] == "ready" and wait.capability in selected["effective_capabilities"]
                    and (wait.mode.mode != "local_only" or local))
                binding = {"connection_id": dependency, "model_id": raw["model_id"], "provider_code": raw["provider_code"],
                    "adapter_type": raw["adapter_type"], "endpoint": endpoint, "enabled": raw["enabled"],
                    "verification_status": raw["verification_status"], "access_mode": raw["access_mode"],
                    "credential_requirement": raw["credential_requirement"], "credential_reference": credential,
                    "provider_kind": kind, "qa_scope_enabled": _qa_asr_enabled(wait.workspace_id) if asr else None,
                    "routing_owner": "local_runtime" if asr else "gateway" if raw["provider_code"] in _ROUTING_GATEWAYS
                        else "local_runtime" if raw["provider_code"] == "SENTENCE_TRANSFORMERS" else "provider",
                    "daon_fallback_allowed": not asr and raw["provider_code"] not in _ROUTING_GATEWAYS | {"SENTENCE_TRANSFORMERS"},
                    "native_asr_contract": None if not asr else {
                        "revision": _ASR_REVISION, "model_digest": _ASR_DIGEST}}
        execution_permission = None
        if binding is not None:
            permission = (Permission.LOCAL_INTERNAL_LLM if binding["provider_kind"] in
                          {"local", "server_internal", "local_runtime"} else Permission.EXTERNAL_LLM)
            execution_permission = _CurrentAuthorizationRules()._effective_permission(
                _AuthReadConnection(connection), role=edit.role, tenant_id=wait.tenant_id,
                workspace_id=wait.workspace_id, permission=permission, requested=True)
        # binding 미확인은 LLM 허용이 아니다. EDIT/ANALYZE만 확인된 상태로서
        # dependency=None/candidate=False이므로 기존 미확인 판정(새 Run0)을 유지한다.
        execution_allowed = execution_permission is None or execution_permission.effective
        execution_body = None if execution_permission is None else {
            "permission": execution_permission.permission.value, "requested": execution_permission.requested,
            "effective": execution_permission.effective, "locked_by": execution_permission.locked_by,
            "reason_code": execution_permission.reason}
        egress_state = "allowed" if local else "blocked" if effective is not None and effective.mode == "deny_external" else "approval_missing"
        cost = {"state": "unknown"}
        scope_enabled = (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.source_version_id) in settings.scopes
        if scope_enabled and execution_allowed and candidate and local and _internal_cost_binding_allowed(binding, wait.capability, catalog):
            target = _internal_cost_target(SimpleNamespace(tenant_id=wait.tenant_id, workspace_id=wait.workspace_id,
                actor_id=wait.actor_id, source_version_id=wait.source_version_id, capability=wait.capability,
                dependency_connection_id=dependency, configuration_version=configuration_version), binding)
            for policy in settings.internal_cost_policies:
                if policy.as_dict() == target:
                    cost = {"state": "allowed", "basis": "internal_operating_cost_excluded", "policy": target}
                    break
        # 관측용 catalog digest와 재큐 억제용 semantic digest는 용도가 다르다.
        # 관측은 전체 DB catalog helper, 억제 조건은 현재 역할의 실제 허용 후보만 결속한다.
        semantic_catalog = None if catalog is None else {**catalog, "models": [value for value in catalog["models"]
            if value["allowed"] and wait.capability in value["effective_capabilities"]]}
        actions = [{"action": grant.action.value, "permissions": sorted([
            {"permission": value.permission.value, "requested": value.requested, "effective": value.effective,
             "locked_by": value.locked_by, "reason_code": value.reason_code} for value in grant.effective_permissions
        ], key=lambda value: value["permission"])} for grant in (edit, analyze)]
        body = {"schema_version": 1, "scope": dict(zip(("tenant_id", "workspace_id", "actor_id", "notebook_id", "source_version_id"),
                (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id, wait.source_version_id))),
            "mode": {"mode": wait.mode.mode, "origin": wait.mode.origin}, "role": wait.capability,
            "authorization": {"role": edit.role.value, "role_scope": edit.role_scope.value, "rules": normalized_rules,
                              "actions": actions, "execution_permission": execution_body},
            "workspace_policy": workspace, "binding": binding, "catalog": semantic_catalog,
            "egress": {**{key: None if value is None else value.as_dict() for key, value in egress.items()},
                       "effective": None if effective is None else effective.as_dict()}, "cost": cost}
        canonical = canonical_json_bytes(body)
        current = CurrentReprocessEligibility(wait.tenant_id, wait.workspace_id, wait.actor_id, wait.notebook_id,
            wait.source_version_id, wait.capability, hashlib.sha256(canonical).hexdigest(), dependency,
            catalog_digest, configuration_version, 0,
            (wait.tenant_id, wait.workspace_id, wait.actor_id, wait.source_version_id) in settings.scopes,
            # EDIT/ANALYZE 실검사 결과다. egress 누락은 확정 policy 차단으로 꾸미지 않는다.
            execution_allowed, True, True, local and execution_allowed, candidate, False, False, False,
            cost["state"], egress_state, wait.processing_run_id)
        return ReadinessPolicySnapshot(current, canonical.decode("utf-8"))
    except WorkspaceModelDefaultsError:
        raise
    except (OpenLawAccessError, OrganizationWorkflowError):
        raise WorkspaceModelDefaultsError("CURRENT_ACCESS_DENIED", 403) from None
    except Exception:
        raise WorkspaceModelDefaultsError("WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", 503) from None
