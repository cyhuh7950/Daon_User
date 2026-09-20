from __future__ import annotations

import re
import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Mapping, Sequence, cast

from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .provider_credentials import EncryptedCredential, ProviderCredentialCipher, ProviderCredentialError
from .user_provider_credentials import PostgresUserProviderCredentialService
from .provider_settings import ProviderSettingsError, provider_requires_credential, validate_provider_base_url
from .data_canon import canonical_json_bytes


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ACTIVE_PROVIDERS = {
    "text_generation": frozenset({
        "OLLAMA", "GROQ", "MISTRAL", "UPSTAGE", "OPENROUTER", "OMNIROUTE", "EOUL_GATEWAY",
    }),
    "image_understanding": frozenset({"UPSTAGE"}),
    "document_parsing": frozenset({"UPSTAGE"}),
    "embedding": frozenset({"SENTENCE_TRANSFORMERS"}),
}
_ROUTING_GATEWAYS = frozenset({"EOUL_GATEWAY", "OMNIROUTE"})
_ACTIVE_CAPABILITIES = frozenset(_ACTIVE_PROVIDERS)


class WorkspaceModelUnavailable(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


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
            "m.catalog_status,m.catalog_version FROM system_provider_connections c "
            "JOIN system_provider_models m ON m.connection_id=c.connection_id "
            "WHERE c.enabled=true AND c.verification_status='verified' AND m.catalog_status='ready' "
            "ORDER BY c.display_name,c.connection_id,m.model_id"
        ).fetchall()
        defaults = connection.execute(
            "SELECT capability,connection_id,model_id,version FROM workspace_model_defaults "
            "WHERE tenant_id=%s AND workspace_id=%s ORDER BY capability",
            (context.tenant_id, context.workspace_id),
        ).fetchall()
        available = [
            {
                "connection_id": str(row[0]), "provider_code": str(row[1]),
                "display_name": str(row[2]), "configured": bool(row[3]),
                "credential_version": int(row[4]), "verification_status": str(row[5]),
                "connection_version": int(row[6]), "model_id": str(row[7]),
                "effective_capabilities": [
                    str(item) for item in (row[8] or ()) if str(item) in _ACTIVE_CAPABILITIES
                ],
                "catalog_status": str(row[9]), "catalog_version": int(row[10]),
            }
            for row in models
            if set(row[8] or ()) & _ACTIVE_CAPABILITIES
        ]
        default_views = [
            {"capability": str(row[0]), "connection_id": str(row[1]),
             "model_id": str(row[2]), "version": int(row[3])}
            for row in defaults if str(row[0]) in _ACTIVE_CAPABILITIES
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
        if capability not in _ACTIVE_CAPABILITIES:
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
                allowed = connection.execute(
                    "SELECT 1 FROM system_provider_connections c JOIN system_provider_models m ON m.connection_id=c.connection_id "
                    "WHERE c.connection_id=%s AND m.model_id=%s AND c.enabled=true AND c.verification_status='verified' "
                    "AND m.catalog_status='ready' AND %s=ANY(m.effective_capabilities)",
                    (connection_id, model_id, capability),
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
        providers = _ACTIVE_PROVIDERS.get(capability)
        if providers is None:
            raise WorkspaceModelUnavailable("PROVIDER_EXECUTION_NOT_READY")
        try:
            with self._store._transaction(self._cloud(context)) as connection:
                row = connection.execute(
                    "SELECT d.version,d.connection_id,d.model_id,c.provider_code,c.base_url,"
                    "c.encrypted_credential,c.credential_nonce,c.encryption_key_version,"
                    "c.credential_schema_version,c.credential_version,c.enabled,c.verification_status,"
                    "m.effective_capabilities,m.catalog_status,m.catalog_version "
                    "FROM workspace_model_defaults d "
                    "JOIN system_provider_connections c ON c.connection_id=d.connection_id "
                    "JOIN system_provider_models m ON m.connection_id=d.connection_id AND m.model_id=d.model_id "
                    "WHERE d.capability=%s",
                    (capability,),
                ).fetchone()
        except CloudDatabaseError as error:
            raise WorkspaceModelUnavailable(
                "WORKSPACE_MODEL_RESOLUTION_UNAVAILABLE", retryable=error.retryable,
            ) from None
        if row is None:
            raise WorkspaceModelUnavailable("WORKSPACE_MODEL_DEFAULT_UNAVAILABLE")
        values: Sequence[object] = row
        connection_id = str(values[1])
        model_id = str(values[2])
        provider_code = str(values[3])
        if provider_code not in providers:
            raise WorkspaceModelUnavailable("PROVIDER_EXECUTION_NOT_READY")
        if not bool(values[10]) or str(values[11]) != "verified":
            raise WorkspaceModelUnavailable("PROVIDER_CONNECTION_UNAVAILABLE")
        if str(values[13]) != "ready":
            raise WorkspaceModelUnavailable("PROVIDER_MODEL_NOT_READY")
        if capability not in set(values[12] or ()):
            raise WorkspaceModelUnavailable("PROVIDER_CAPABILITY_UNSUPPORTED")
        try:
            base_url = validate_provider_base_url(provider_code, str(values[4]))
        except ProviderSettingsError:
            raise WorkspaceModelUnavailable("PROVIDER_CONNECTION_UNAVAILABLE") from None

        credential_version = int(values[9])
        credential: bytearray | None = None
        encrypted = values[5]
        system_credential_failed = encrypted is None or context.credential_source == "user"
        if encrypted is not None and context.credential_source != "user":
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

        if system_credential_failed and self._user_credentials is not None:
            fallback = self._user_credentials.resolve_credential(
                tenant_id=context.tenant_id, user_id=context.actor_id, connection_id=connection_id,
                prefer_user=context.credential_source == "user",
            )
            if fallback.credential is not None:
                credential = bytearray(fallback.credential)
                credential_version = fallback.credential_version
                system_credential_failed = False
        if system_credential_failed and provider_requires_credential(provider_code, base_url):
            raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_REQUIRED")

        resolved = ResolvedModel(
            connection_id=connection_id, provider_code=provider_code, model_id=model_id,
            capability=capability, base_url=base_url, credential_version=credential_version,
            default_version=int(values[0]), catalog_version=int(values[14]),
            provider_kind="local" if provider_code in {"OLLAMA", "SENTENCE_TRANSFORMERS"} else "external_api",
            routing_owner=(
                "gateway" if provider_code in _ROUTING_GATEWAYS
                else "local_runtime" if provider_code == "SENTENCE_TRANSFORMERS"
                else "provider"
            ),
            daon_fallback_allowed=provider_code not in _ROUTING_GATEWAYS | {"SENTENCE_TRANSFORMERS"},
            _credential=credential,
        )
        try:
            yield resolved
        finally:
            resolved.release()
