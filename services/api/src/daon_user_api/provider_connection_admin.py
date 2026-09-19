"""System-scoped provider connection administration and durable mutation replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .provider_connection_adapters import AdapterError, AdapterRegistry
from .provider_credentials import (
    EncryptedCredential,
    ProviderConnection,
    ProviderCredentialCipher,
    ProviderCredentialError,
)
from .provider_settings import ProviderSettingsError, validate_provider_base_url


DEFAULT_PROVIDER_ENDPOINTS = {
    "CEREBRAS": "https://api.cerebras.ai/v1",
    "GROQ": "https://api.groq.com/openai/v1",
    "MISTRAL": "https://api.mistral.ai/v1",
    "OPENAI": "https://api.openai.com/v1",
    "UPSTAGE": "https://api.upstage.ai/v1",
    "GEMINI": "https://generativelanguage.googleapis.com/v1beta",
    "OPENROUTER": "https://openrouter.ai/api/v1",
    "ANTHROPIC": "https://api.anthropic.com/v1",
    "OLLAMA": "http://localhost:11434",
    "OMNIROUTE": "http://localhost:20128/v1",
    "EOUL_GATEWAY": "http://localhost:8660",
    "MEDIA_BRIDGE": "http://127.0.0.1:8642/v1",
    "SENTENCE_TRANSFORMERS": "http://localhost:8000",
}


class ProviderConnectionAdminError(RuntimeError):
    def __init__(self, code: str, status: int = 400, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderConnectionAdminContext:
    tenant_id: str
    actor_id: str
    trace_id: str
    policy_version: str


@dataclass(frozen=True, slots=True)
class ProviderAdminAudit:
    action: str
    target_type: str
    target_id: str
    version: int | None = None


@dataclass(frozen=True, slots=True)
class ProviderConnectionCreateCommand:
    connection_id: str
    provider_code: str
    display_name: str
    base_url: str
    credential: str | None = field(repr=False)
    logical_model_ids: tuple[str, ...]
    enabled: bool
    expected_version: int


@dataclass(frozen=True, slots=True)
class ProviderConnectionUpdateCommand:
    display_name: str
    base_url: str
    credential: str | None = field(repr=False)
    logical_model_ids: tuple[str, ...]
    enabled: bool
    expected_version: int


@dataclass(frozen=True, slots=True)
class ProviderCapabilityCommand:
    effective_capabilities: tuple[str, ...]
    expected_version: int


@dataclass(frozen=True, slots=True)
class ProviderCredentialReplaceCommand:
    credential: str = field(repr=False)
    expected_version: int


def normalized_connection_fingerprint_payload(
    *, connection_id: str, provider_code: str | None, display_name: str,
    base_url: str, enabled: bool, expected_version: int,
    logical_model_ids: Sequence[str], credential_fingerprint: str | None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "connection_id": connection_id,
        "provider_code": provider_code,
        "display_name": display_name,
        "base_url": base_url,
        "enabled": enabled,
        "expected_version": expected_version,
        "logical_model_ids": list(logical_model_ids),
        "credential_action": "replace" if credential_fingerprint is not None else "preserve",
    }
    if credential_fingerprint is not None:
        payload["credential_fingerprint"] = credential_fingerprint
    return payload


_Result = TypeVar("_Result", bound=Mapping[str, object])


class PostgresProviderAdminMutationRepository:
    """Runs one system mutation, replay record, and audit outbox write atomically."""

    def __init__(self, store: PostgresCloudStore) -> None:
        self._store = store

    @staticmethod
    def _cloud(context: ProviderConnectionAdminContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, f"system:{context.tenant_id}", context.actor_id,
            "provider_admin.write",
        )

    def run(
        self, context: ProviderConnectionAdminContext, *, operation: str,
        idempotency_key: str, fingerprint_payload: Mapping[str, object],
        mutation: Callable[[Connection[tuple[Any, ...]]], _Result],
        audit: ProviderAdminAudit,
    ) -> tuple[_Result, bool]:
        fingerprint = hashlib.sha256(canonical_json_bytes(fingerprint_payload)).hexdigest()
        with self._store._transaction(self._cloud(context)) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{context.tenant_id}|{context.actor_id}|{operation}|{idempotency_key}",),
            )
            replay = connection.execute(
                "SELECT request_fingerprint,result FROM system_provider_admin_idempotency "
                "WHERE tenant_id=%s AND actor_id=%s AND operation=%s AND idempotency_key=%s",
                (context.tenant_id, context.actor_id, operation, idempotency_key),
            ).fetchone()
            if replay is not None:
                if str(replay[0]) != fingerprint:
                    raise ProviderConnectionAdminError("IDEMPOTENCY_KEY_REUSED", 409)
                return cast(_Result, replay[1]), True

            result = mutation(connection)
            self._validate_safe_mapping(result)
            connection.execute(
                "INSERT INTO system_provider_admin_idempotency "
                "(tenant_id,actor_id,operation,idempotency_key,request_fingerprint,result) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (context.tenant_id, context.actor_id, operation, idempotency_key,
                 fingerprint, Jsonb(dict(result))),
            )
            audit_payload = {
                **({} if audit.version is None else {"version": audit.version}),
                "reason_code": "SYSTEM_PROVIDER_CONFIGURATION_CHANGED",
            }
            event_id = "provider-outbox-" + hashlib.sha256(
                f"{context.tenant_id}|{context.actor_id}|{operation}|{idempotency_key}".encode("utf-8")
            ).hexdigest()[:32]
            connection.execute(
                "INSERT INTO system_provider_audit_outbox "
                "(event_id,tenant_id,actor_id,action,target_type,target_id,trace_id,policy_version,audit_payload) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (event_id, context.tenant_id, context.actor_id, audit.action,
                 audit.target_type, audit.target_id, context.trace_id,
                 context.policy_version, Jsonb(audit_payload)),
            )
            return result, False

    @classmethod
    def _validate_safe_mapping(cls, value: object) -> None:
        forbidden = {"credential", "base_url", "endpoint", "api_key", "secret", "response_body"}
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).lower() in forbidden:
                    raise ProviderConnectionAdminError("PROVIDER_ADMIN_RESULT_UNSAFE", 500)
                cls._validate_safe_mapping(nested)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._validate_safe_mapping(item)


_ADMIN_CAPABILITIES = frozenset({"text_generation", "image_understanding", "document_parsing"})


class PostgresProviderConnectionService:
    def __init__(self, store: PostgresCloudStore, cipher: ProviderCredentialCipher) -> None:
        self._store = store
        self._cipher = cipher
        self._mutations = PostgresProviderAdminMutationRepository(store)

    @staticmethod
    def _cloud(context: ProviderConnectionAdminContext) -> CloudAccessContext:
        return CloudAccessContext(
            context.tenant_id, f"system:{context.tenant_id}", context.actor_id,
            "provider_admin.read",
        )

    @staticmethod
    def _model_view(row: Sequence[object]) -> dict[str, object]:
        return {
            "model_id": str(row[0]), "reported_capabilities": list(row[1] or ()),
            "effective_capabilities": list(row[2] or ()), "override_applied": bool(row[3]),
            "catalog_status": str(row[4]), "catalog_version": int(row[5]),
        }

    @classmethod
    def _safe_connection(
        cls, connection: Connection[tuple[Any, ...]], row: Sequence[object],
    ) -> dict[str, object]:
        model_rows = connection.execute(
            "SELECT model_id,reported_capabilities,effective_capabilities,override_applied,"
            "catalog_status,catalog_version FROM system_provider_models "
            "WHERE connection_id=%s ORDER BY model_id", (row[0],),
        ).fetchall()
        return {
            "connection_id": str(row[0]), "provider_code": str(row[1]),
            "display_name": str(row[2]), "base_url": str(row[3]), "enabled": bool(row[4]),
            "configured": row[5] is not None, "credential_version": int(row[6]),
            "verification_status": str(row[7]),
            "verified_at": None if row[8] is None else cast(datetime, row[8]).isoformat(),
            "version": int(row[9]), "catalog_status": "stale" if row[10] is None else str(row[10]),
            "catalog_version": 0 if row[11] is None else int(row[11]),
            "models": [cls._model_view(item) for item in model_rows],
        }

    @staticmethod
    def _select_summary(
        connection: Connection[tuple[Any, ...]], connection_id: str | None = None,
    ) -> list[tuple[Any, ...]]:
        where = "" if connection_id is None else "WHERE c.connection_id=%s "
        params: tuple[object, ...] = () if connection_id is None else (connection_id,)
        return connection.execute(
            "SELECT c.connection_id,c.provider_code,c.display_name,c.base_url,c.enabled,c.encrypted_credential,"
            "c.credential_version,c.verification_status,c.verified_at,c.version,"
            "max(m.catalog_status),max(m.catalog_version) FROM system_provider_connections c "
            "LEFT JOIN system_provider_models m ON m.connection_id=c.connection_id " + where +
            "GROUP BY c.connection_id ORDER BY c.display_name,c.connection_id", params,
        ).fetchall()

    def list_connections(self, context: ProviderConnectionAdminContext) -> list[dict[str, object]]:
        with self._store._transaction(self._cloud(context)) as connection:
            items = [self._safe_connection(connection, row) for row in self._select_summary(connection)]
            registered = {str(item["provider_code"]) for item in items}
            for provider_code, base_url in DEFAULT_PROVIDER_ENDPOINTS.items():
                if provider_code in registered:
                    continue
                items.append({
                    "connection_id": f"provider-{provider_code.lower()}",
                    "provider_code": provider_code,
                    "display_name": provider_code,
                    "base_url": base_url,
                    "enabled": False,
                    "configured": False,
                    "credential_version": 0,
                    "verification_status": "unverified",
                    "verified_at": None,
                    "version": 0,
                    "catalog_status": "stale",
                    "catalog_version": 0,
                    "models": [],
                })
            return items

    @staticmethod
    def _load(connection: Connection[tuple[Any, ...]], connection_id: str) -> tuple[Any, ...]:
        row = connection.execute(
            "SELECT connection_id,provider_code,display_name,base_url,encrypted_credential,"
            "credential_nonce,encryption_key_version,credential_schema_version,credential_version,"
            "enabled,verification_status,verified_at,version FROM system_provider_connections "
            "WHERE connection_id=%s", (connection_id,),
        ).fetchone()
        if row is None:
            raise ProviderConnectionAdminError("PROVIDER_CONNECTION_NOT_FOUND", 404)
        return tuple(row)

    @staticmethod
    def _sealed(row: Sequence[object]) -> EncryptedCredential | None:
        if row[4] is None:
            return None
        return EncryptedCredential(
            ciphertext=bytes(row[4]), nonce=bytes(row[5]), encryption_key_version=int(row[6]),
            credential_version=int(row[8]), schema_version=int(row[7]),
        )

    def _prepare(
        self, *, connection_id: str, provider_code: str, display_name: str, base_url: str,
        credential: str | bytes | None, logical_model_ids: Sequence[str], enabled: bool,
        version: int, previous_sealed: EncryptedCredential | None = None,
        discover_models: bool = True,
    ) -> tuple[ProviderConnection, EncryptedCredential | None, tuple[object, ...]]:
        try:
            normalized_url = validate_provider_base_url(provider_code, base_url)
            raw = credential
            if raw is None and previous_sealed is not None:
                raw = self._cipher.decrypt(
                    connection_id, provider_code, previous_sealed.credential_version, previous_sealed,
                )
            profile = ProviderConnection(
                connection_id, provider_code, display_name, normalized_url, previous_sealed, True, version,
            )
            adapter = AdapterRegistry(logical_models={connection_id: logical_model_ids}).adapter(provider_code)
            if provider_code == "OMNIROUTE" and (raw is None or not str(raw).strip()):
                raise AdapterError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            if credential is not None and (not isinstance(credential, (str, bytes)) or not str(credential).strip()):
                raise AdapterError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            if discover_models:
                adapter.verify(profile, raw)
            models = adapter.discover_models(profile, raw) if discover_models else ()
            sealed = previous_sealed
            if credential is not None:
                next_version = 1 if previous_sealed is None else previous_sealed.credential_version + 1
                secret_buffer = bytearray(credential.encode("utf-8") if isinstance(credential, str) else credential)
                try:
                    sealed = self._cipher.encrypt(connection_id, provider_code, next_version, bytes(secret_buffer))
                finally:
                    secret_buffer[:] = b"\0" * len(secret_buffer)
            return profile, sealed, models
        except (AdapterError, ProviderCredentialError, ProviderSettingsError) as error:
            raise ProviderConnectionAdminError(
                str(getattr(error, "code", str(error))), int(getattr(error, "status", 400)),
                retryable=bool(getattr(error, "retryable", False)),
            ) from None

    @staticmethod
    def _replace_models(
        connection: Connection[tuple[Any, ...]], context: ProviderConnectionAdminContext,
        connection_id: str, models: Sequence[object], catalog_version: int,
    ) -> None:
        model_ids = [model.model_id for model in models]
        if model_ids:
            connection.execute(
                "DELETE FROM system_provider_models WHERE connection_id=%s AND NOT (model_id=ANY(%s))",
                (connection_id, model_ids),
            )
        else:
            connection.execute(
                "DELETE FROM system_provider_models WHERE connection_id=%s", (connection_id,),
            )
        for model in models:
            connection.execute(
                "INSERT INTO system_provider_models (connection_id,model_id,reported_capabilities,"
                "effective_capabilities,override_applied,catalog_status,catalog_version,discovered_at,updated_by) "
                "VALUES (%s,%s,%s,%s,false,'ready',%s,now(),%s) "
                "ON CONFLICT (connection_id,model_id) DO UPDATE SET "
                "reported_capabilities=excluded.reported_capabilities,"
                "effective_capabilities=CASE WHEN system_provider_models.override_applied "
                "THEN system_provider_models.effective_capabilities ELSE excluded.effective_capabilities END,"
                "catalog_status='ready',catalog_version=excluded.catalog_version,discovered_at=now(),"
                "updated_at=now(),updated_by=excluded.updated_by",
                (connection_id, model.model_id, list(model.reported_capabilities),
                 list(model.reported_capabilities), catalog_version, context.actor_id),
            )

    def _safe_by_id(
        self, connection: Connection[tuple[Any, ...]], connection_id: str,
    ) -> dict[str, object]:
        rows = self._select_summary(connection, connection_id)
        if not rows:
            raise ProviderConnectionAdminError("PROVIDER_CONNECTION_NOT_FOUND", 404)
        return self._safe_connection(connection, rows[0])

    def create_connection(
        self, context: ProviderConnectionAdminContext, command: ProviderConnectionCreateCommand,
        idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        payload = normalized_connection_fingerprint_payload(
            connection_id=command.connection_id, provider_code=command.provider_code,
            display_name=command.display_name, base_url=command.base_url, enabled=command.enabled,
            expected_version=command.expected_version, logical_model_ids=command.logical_model_ids,
            credential_fingerprint=(
                None if command.credential is None
                else self._cipher.idempotency_fingerprint(
                    command.connection_id, command.credential.encode("utf-8")
                )
            ),
        )

        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            if command.expected_version != 0 or connection.execute(
                "SELECT 1 FROM system_provider_connections WHERE connection_id=%s", (command.connection_id,),
            ).fetchone() is not None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            profile, sealed, models = self._prepare(
                connection_id=command.connection_id, provider_code=command.provider_code,
                display_name=command.display_name, base_url=command.base_url,
                credential=command.credential, logical_model_ids=command.logical_model_ids,
                enabled=command.enabled, version=1, discover_models=False,
            )
            connection.execute(
                "INSERT INTO system_provider_connections (connection_id,provider_code,display_name,base_url,"
                "encrypted_credential,credential_nonce,encryption_key_version,credential_schema_version,"
                "credential_version,enabled,verification_status,verified_at,version,updated_by,trace_id,policy_version) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'verified',now(),1,%s,%s,%s)",
                (profile.connection_id, profile.provider_code, profile.display_name, profile.base_url,
                 None if sealed is None else sealed.ciphertext, None if sealed is None else sealed.nonce,
                 None if sealed is None else sealed.encryption_key_version,
                 None if sealed is None else sealed.schema_version,
                 0 if sealed is None else sealed.credential_version, command.enabled,
                 context.actor_id, context.trace_id, context.policy_version),
            )
            self._replace_models(connection, context, command.connection_id, models, 1)
            return self._safe_by_id(connection, command.connection_id)

        return self._mutations.run(
            context, operation="provider_connection.create", idempotency_key=idempotency_key,
            fingerprint_payload=payload, mutation=mutation,
            audit=ProviderAdminAudit("provider_connection.created", "provider_connection", command.connection_id, 1),
        )

    def update_connection(
        self, context: ProviderConnectionAdminContext, connection_id: str,
        command: ProviderConnectionUpdateCommand, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        payload = normalized_connection_fingerprint_payload(
            connection_id=connection_id, provider_code=None, display_name=command.display_name,
            base_url=command.base_url, enabled=command.enabled, expected_version=command.expected_version,
            logical_model_ids=command.logical_model_ids,
            credential_fingerprint=(
                None if command.credential is None
                else self._cipher.idempotency_fingerprint(
                    connection_id, command.credential.encode("utf-8")
                )
            ),
        )

        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            current = self._load(connection, connection_id)
            if int(current[12]) != command.expected_version:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            profile, sealed, models = self._prepare(
                connection_id=connection_id, provider_code=str(current[1]),
                display_name=command.display_name, base_url=command.base_url,
                credential=command.credential, logical_model_ids=command.logical_model_ids,
                enabled=command.enabled, version=command.expected_version + 1,
                previous_sealed=self._sealed(current), discover_models=False,
            )
            row = connection.execute(
                "UPDATE system_provider_connections SET display_name=%s,base_url=%s,encrypted_credential=%s,"
                "credential_nonce=%s,encryption_key_version=%s,credential_schema_version=%s,credential_version=%s,"
                "enabled=%s,verification_status='verified',verified_at=now(),version=version+1,updated_at=now(),"
                "updated_by=%s,trace_id=%s,policy_version=%s WHERE connection_id=%s AND version=%s RETURNING version",
                (profile.display_name, profile.base_url, None if sealed is None else sealed.ciphertext,
                 None if sealed is None else sealed.nonce, None if sealed is None else sealed.encryption_key_version,
                 None if sealed is None else sealed.schema_version, 0 if sealed is None else sealed.credential_version,
                 command.enabled, context.actor_id, context.trace_id, context.policy_version,
                 connection_id, command.expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            self._replace_models(connection, context, connection_id, models, int(row[0]))
            return self._safe_by_id(connection, connection_id)

        return self._mutations.run(
            context, operation="provider_connection.update", idempotency_key=idempotency_key,
            fingerprint_payload=payload, mutation=mutation,
            audit=ProviderAdminAudit("provider_connection.updated", "provider_connection", connection_id, command.expected_version + 1),
        )

    def delete_connection(
        self, context: ProviderConnectionAdminContext, connection_id: str,
        expected_version: int, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            row = connection.execute(
                "UPDATE system_provider_connections SET encrypted_credential=NULL,credential_nonce=NULL,"
                "encryption_key_version=NULL,credential_schema_version=NULL,"
                "credential_version=0,verification_status='unverified',verified_at=NULL,"
                "version=version+1,updated_at=now(),updated_by=%s,trace_id=%s,policy_version=%s "
                "WHERE connection_id=%s AND version=%s RETURNING connection_id,version",
                (context.actor_id, context.trace_id, context.policy_version, connection_id, expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            return self._safe_by_id(connection, connection_id)

        return self._mutations.run(
            context, operation="provider_connection.credential.delete", idempotency_key=idempotency_key,
            fingerprint_payload={"connection_id": connection_id, "expected_version": expected_version},
            mutation=mutation,
            audit=ProviderAdminAudit(
                "provider_connection.credential_deleted", "provider_connection", connection_id,
                expected_version + 1,
            ),
        )

    def replace_credential(
        self, context: ProviderConnectionAdminContext, connection_id: str,
        command: ProviderCredentialReplaceCommand, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        credential_fingerprint = self._cipher.idempotency_fingerprint(
            connection_id, command.credential.encode("utf-8")
        )

        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            current = self._load(connection, connection_id)
            if int(current[12]) != command.expected_version:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            model_rows = connection.execute(
                "SELECT model_id FROM system_provider_models WHERE connection_id=%s ORDER BY model_id",
                (connection_id,),
            ).fetchall()
            profile, sealed, models = self._prepare(
                connection_id=connection_id, provider_code=str(current[1]),
                display_name=str(current[2]), base_url=str(current[3]),
                credential=command.credential,
                logical_model_ids=[str(row[0]) for row in model_rows], enabled=bool(current[9]),
                version=command.expected_version + 1, previous_sealed=self._sealed(current),
                discover_models=False,
            )
            row = connection.execute(
                "UPDATE system_provider_connections SET encrypted_credential=%s,credential_nonce=%s,"
                "encryption_key_version=%s,credential_schema_version=%s,credential_version=%s,"
                "verification_status='verified',verified_at=now(),version=version+1,updated_at=now(),"
                "updated_by=%s,trace_id=%s,policy_version=%s WHERE connection_id=%s AND version=%s RETURNING version",
                (sealed.ciphertext if sealed else None, sealed.nonce if sealed else None,
                 sealed.encryption_key_version if sealed else None, sealed.schema_version if sealed else None,
                 sealed.credential_version if sealed else 0, context.actor_id, context.trace_id,
                 context.policy_version, connection_id, command.expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            return self._safe_by_id(connection, connection_id)

        return self._mutations.run(
            context, operation="provider_connection.credential.replace",
            idempotency_key=idempotency_key,
            fingerprint_payload={
                "connection_id": connection_id, "expected_version": command.expected_version,
                "credential_action": "replace", "credential_fingerprint": credential_fingerprint,
            },
            mutation=mutation,
            audit=ProviderAdminAudit(
                "provider_connection.credential_replaced", "provider_connection", connection_id,
                command.expected_version + 1,
            ),
        )

    def refresh_catalog(
        self, context: ProviderConnectionAdminContext, connection_id: str,
        expected_version: int, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            current = self._load(connection, connection_id)
            if int(current[12]) != expected_version:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            model_rows = connection.execute(
                "SELECT model_id FROM system_provider_models WHERE connection_id=%s ORDER BY model_id",
                (connection_id,),
            ).fetchall()
            profile, _sealed_value, models = self._prepare(
                connection_id=connection_id, provider_code=str(current[1]), display_name=str(current[2]),
                base_url=str(current[3]), credential=None,
                logical_model_ids=[str(row[0]) for row in model_rows], enabled=bool(current[9]),
                version=expected_version + 1, previous_sealed=self._sealed(current),
            )
            row = connection.execute(
                "UPDATE system_provider_connections SET verification_status='verified',verified_at=now(),"
                "version=version+1,updated_at=now(),updated_by=%s,trace_id=%s,policy_version=%s "
                "WHERE connection_id=%s AND version=%s RETURNING version",
                (context.actor_id, context.trace_id, context.policy_version, connection_id, expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            self._replace_models(connection, context, profile.connection_id, models, int(row[0]))
            return self._safe_by_id(connection, connection_id)

        return self._mutations.run(
            context, operation="provider_catalog.refresh", idempotency_key=idempotency_key,
            fingerprint_payload={"connection_id": connection_id, "expected_version": expected_version},
            mutation=mutation,
            audit=ProviderAdminAudit("provider_catalog.refreshed", "provider_connection", connection_id, expected_version + 1),
        )

    def correct_capabilities(
        self, context: ProviderConnectionAdminContext, connection_id: str, model_id: str,
        command: ProviderCapabilityCommand, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        capabilities = tuple(dict.fromkeys(command.effective_capabilities))
        if len(capabilities) != len(command.effective_capabilities) or not set(capabilities) <= _ADMIN_CAPABILITIES:
            raise ProviderConnectionAdminError("PROVIDER_CAPABILITY_UNSUPPORTED", 409)

        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            row = connection.execute(
                "UPDATE system_provider_models SET effective_capabilities=%s,override_applied=true,"
                "catalog_version=catalog_version+1,updated_at=now(),updated_by=%s "
                "WHERE connection_id=%s AND model_id=%s AND catalog_version=%s "
                "RETURNING model_id,reported_capabilities,effective_capabilities,override_applied,"
                "catalog_status,catalog_version",
                (list(capabilities), context.actor_id, connection_id, model_id, command.expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            return {"connection_id": connection_id, **self._model_view(row)}

        target_id = f"provider-model:{connection_id}:{model_id}"
        return self._mutations.run(
            context, operation="provider_model.capabilities.update", idempotency_key=idempotency_key,
            fingerprint_payload={"connection_id": connection_id, "model_id": model_id,
                                 "effective_capabilities": list(capabilities),
                                 "expected_version": command.expected_version},
            mutation=mutation,
            audit=ProviderAdminAudit("provider_model.capabilities_updated", "provider_model", target_id, command.expected_version + 1),
        )
