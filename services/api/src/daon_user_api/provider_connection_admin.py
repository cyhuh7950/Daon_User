"""System-scoped provider connection administration and durable mutation replay."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any, Callable, Mapping, Sequence, TypeVar, cast

from psycopg import Connection
from psycopg.types.json import Jsonb

from .cloud_storage import CloudAccessContext, PostgresCloudStore
from .data_canon import canonical_json_bytes
from .provider_connection_adapters import AdapterError, AdapterRegistry, OmniRouteAdapter
from .provider_catalog import ProviderCatalog, ProviderCatalogError
from .provider_credentials import (
    EncryptedCredential,
    ProviderConnection,
    ProviderCredentialCipher,
    ProviderCredentialError,
)
from .provider_settings import ProviderSettingsError, provider_requires_credential, validate_provider_base_url


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


def _omniroute_models(provider_code: str, allowed: Sequence[str], logical: Sequence[str]) -> tuple[str, ...]:
    if provider_code != "OMNIROUTE":
        return tuple(allowed)
    selected = tuple(allowed or logical or ("auto",))
    model_ids = tuple(model for model in selected if model != "auto") or ("auto",)
    if len(model_ids) > 4:
        raise ProviderConnectionAdminError("PROVIDER_MODEL_IDS_INVALID", 409)
    return model_ids


class ProviderConnectionAdminError(RuntimeError):
    def __init__(
        self, code: str, status: int = 400, *, retryable: bool = False,
        references: Mapping[str, int] | None = None,
    ) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        self.references = dict(references or {})
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
    access_mode: str = "public"
    credential_requirement: str = "required"
    short_code: str = ""
    adapter_type: str = ""
    allowed_model_ids: tuple[str, ...] = ()
    provider_name: str = ""
    test_credential: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class ProviderConnectionUpdateCommand:
    display_name: str
    base_url: str
    credential: str | None = field(repr=False)
    logical_model_ids: tuple[str, ...]
    enabled: bool
    expected_version: int
    access_mode: str = "public"
    credential_requirement: str = "required"
    short_code: str = ""
    adapter_type: str = ""
    allowed_model_ids: tuple[str, ...] = ()
    provider_name: str = ""
    test_credential: str | None = field(default=None, repr=False)


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
    access_mode: str | None = None, credential_requirement: str | None = None,
    short_code: str | None = None, adapter_type: str | None = None,
    allowed_model_ids: Sequence[str] = (),
    provider_name: str | None = None,
    test_credential_fingerprint: str | None = None,
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
        "access_mode": access_mode, "credential_requirement": credential_requirement,
        "short_code": short_code, "adapter_type": adapter_type,
        "allowed_model_ids": list(allowed_model_ids),
        "provider_name": provider_name,
    }
    if credential_fingerprint is not None:
        payload["credential_fingerprint"] = credential_fingerprint
    if test_credential_fingerprint is not None:
        payload["test_credential_fingerprint"] = test_credential_fingerprint
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
        # The connection response intentionally exposes the normalized endpoint
        # so the admin form can reload it.  Only credentials and raw upstream
        # response material are forbidden from durable mutation results.
        forbidden = {"credential", "api_key", "secret", "response_body"}
        if isinstance(value, Mapping):
            for key, nested in value.items():
                if str(key).lower() in forbidden:
                    raise ProviderConnectionAdminError("PROVIDER_ADMIN_RESULT_UNSAFE", 500)
                cls._validate_safe_mapping(nested)
        elif isinstance(value, (list, tuple)):
            for item in value:
                cls._validate_safe_mapping(item)


_ADMIN_CAPABILITIES = frozenset({"text_generation", "image_understanding", "document_parsing"})
_SHORT_CODE = re.compile(r"^[A-Z]{2}$")


def validate_connection_policy(
    provider_code: str, access_mode: str, credential_requirement: str,
    short_code: str, adapter_type: str, base_url: str | None = None,
    *, allow_existing_legacy_custom: bool = False,
) -> str:
    if _SHORT_CODE.fullmatch(short_code) is None or (short_code == "OR" and provider_code != "OPENROUTER"):
        raise ProviderConnectionAdminError("PROVIDER_SHORT_CODE_INVALID", 409)
    if credential_requirement not in {"required", "none"} or access_mode not in {"public", "personal"}:
        raise ProviderConnectionAdminError("PROVIDER_ACCESS_MODE_INVALID", 409)
    if credential_requirement == "none" and access_mode != "public":
        raise ProviderConnectionAdminError("PROVIDER_ACCESS_MODE_INVALID", 409)
    if base_url is not None and provider_code != "CUSTOM" and credential_requirement != (
        "required" if provider_requires_credential(provider_code, base_url) else "none"
    ):
        raise ProviderConnectionAdminError("PROVIDER_ACCESS_MODE_INVALID", 409)
    if provider_code == "OLLAMA" and credential_requirement != "none":
        raise ProviderConnectionAdminError("PROVIDER_ACCESS_MODE_INVALID", 409)
    if (provider_code == "CUSTOM" and adapter_type not in (
        {"openai_compatible", "anthropic_compatible", "CUSTOM"}
        if allow_existing_legacy_custom else {"openai_compatible", "anthropic_compatible"}
    )) or (
        provider_code != "CUSTOM" and adapter_type != provider_code
    ):
        raise ProviderConnectionAdminError("PROVIDER_ADAPTER_UNSUPPORTED", 409)
    try:
        if provider_code == "CUSTOM":
            AdapterRegistry().adapter(provider_code, adapter_type)
        else:
            AdapterRegistry().adapter(provider_code)
    except AdapterError:
        raise ProviderConnectionAdminError("PROVIDER_ADAPTER_UNSUPPORTED", 409) from None
    return access_mode


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
        allowed_rows = connection.execute(
            "SELECT model_id FROM system_provider_allowed_models WHERE connection_id=%s ORDER BY model_id",
            (row[0],),
        ).fetchall()
        catalog_status = "stale" if row[10] is None else str(row[10])
        if str(row[1]) == "OMNIROUTE":
            allowed_ids = {str(item[0]) for item in allowed_rows}
            catalog_status = "ready" if any(
                str(model[0]) in allowed_ids and str(model[4]) == "ready"
                and "text_generation" in (model[2] or ()) for model in model_rows
            ) else "stale"
        return {
            "connection_id": str(row[0]), "provider_code": str(row[1]),
            "display_name": str(row[2]), "base_url": str(row[3]), "enabled": bool(row[4]),
            "configured": row[5] is not None, "credential_version": int(row[6]),
            "verification_status": str(row[7]),
            "verified_at": None if row[8] is None else cast(datetime, row[8]).isoformat(),
            "version": int(row[9]), "catalog_status": catalog_status,
            "catalog_version": 0 if row[11] is None else int(row[11]),
            "models": [cls._model_view(item) for item in model_rows],
            "access_mode": str(row[12]), "credential_requirement": str(row[13]),
            "short_code": str(row[14]), "adapter_type": str(row[15]),
            "provider_name": str(row[16]),
            "allowed_model_ids": [str(item[0]) for item in allowed_rows],
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
            "max(m.catalog_status),max(m.catalog_version),c.access_mode,c.credential_requirement,"
            "c.short_code,c.adapter_type,c.provider_name FROM system_provider_connections c "
            "LEFT JOIN system_provider_models m ON m.connection_id=c.connection_id " + where +
            "GROUP BY c.connection_id ORDER BY c.display_name,c.connection_id", params,
        ).fetchall()

    def list_connections(self, context: ProviderConnectionAdminContext) -> list[dict[str, object]]:
        with self._store._transaction(self._cloud(context)) as connection:
            return [self._safe_connection(connection, row) for row in self._select_summary(connection)]

    def preview_models(
        self, context: ProviderConnectionAdminContext, *, connection_id: str,
        provider_code: str, adapter_type: str, base_url: str, credential: str | None,
    ) -> tuple[str, ...]:
        if provider_code != "CUSTOM" or adapter_type not in {"openai_compatible", "anthropic_compatible"}:
            raise ProviderConnectionAdminError("PROVIDER_ADAPTER_UNSUPPORTED", 409)
        try:
            normalized_url = validate_provider_base_url(provider_code, base_url)
            profile = ProviderConnection(
                connection_id, provider_code, connection_id, normalized_url, None, True, 0,
            )
            models = AdapterRegistry().adapter(provider_code, adapter_type).discover_models(profile, credential)
            if credential and any(credential in model.model_id for model in models):
                raise ProviderConnectionAdminError("PROVIDER_CATALOG_RESPONSE_INVALID", 503)
            return tuple(model.model_id for model in models)
        except (AdapterError, ProviderSettingsError) as error:
            raise ProviderConnectionAdminError(
                str(getattr(error, "code", str(error))), int(getattr(error, "status", 400)),
                retryable=bool(getattr(error, "retryable", False)),
            ) from None

    def list_active_connection_ids(self, context: ProviderConnectionAdminContext) -> tuple[str, ...]:
        with self._store._transaction(self._cloud(context)) as connection:
            rows = connection.execute(
                "SELECT connection_id FROM system_provider_connections "
                "WHERE enabled=true ORDER BY connection_id",
            ).fetchall()
            return tuple(str(row[0]) for row in rows)

    def check_active_connection(
        self, context: ProviderConnectionAdminContext, connection_id: str,
    ) -> dict[str, object]:
        checked_at = datetime.now().astimezone().isoformat()
        try:
            with self._store._transaction(self._cloud(context)) as connection:
                row = self._load(connection, connection_id)
                if not bool(row[9]) or str(row[13]) == "personal" or str(row[1]) == "CUSTOM":
                    return {"connection_id": connection_id, "status": "skipped"}
                sealed = self._sealed(row)
                credential = None if sealed is None else self._cipher.decrypt(
                    connection_id, str(row[1]), sealed.credential_version, sealed,
                )
                profile = ProviderConnection(
                    connection_id, str(row[1]), str(row[2]), str(row[3]), sealed,
                    True, int(row[12]), str(row[10]), row[11],
                )
                if str(row[1]) == "OMNIROUTE":
                    allowed = tuple(str(item[0]) for item in connection.execute(
                        "SELECT model_id FROM system_provider_allowed_models "
                        "WHERE connection_id=%s ORDER BY model_id", (connection_id,),
                    ).fetchall())
                    adapter = AdapterRegistry(logical_models={connection_id: allowed or ("auto",)}).adapter("OMNIROUTE")
                else:
                    adapter = AdapterRegistry().adapter(str(row[1]))
                adapter.verify(profile, credential)
                status = "verified"
                connection.execute(
                    "UPDATE system_provider_connections SET verification_status='verified',"
                    "verified_at=now(),updated_at=now() WHERE connection_id=%s",
                    (connection_id,),
                )
                return {"connection_id": connection_id, "status": status, "checked_at": checked_at}
        except (AdapterError, ProviderCredentialError, ProviderSettingsError, Exception):
            with self._store._transaction(self._cloud(context)) as connection:
                connection.execute(
                    "UPDATE system_provider_connections SET verification_status='failed',"
                    "verified_at=now(),updated_at=now() WHERE connection_id=%s AND enabled=true",
                    (connection_id,),
                )
            return {"connection_id": connection_id, "status": "failed", "checked_at": checked_at}

    @staticmethod
    def _load(connection: Connection[tuple[Any, ...]], connection_id: str) -> tuple[Any, ...]:
        row = connection.execute(
            "SELECT connection_id,provider_code,display_name,base_url,encrypted_credential,"
            "credential_nonce,encryption_key_version,credential_schema_version,credential_version,"
            "enabled,verification_status,verified_at,version,access_mode,credential_requirement,"
            "short_code,adapter_type,provider_name FROM system_provider_connections "
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

    @staticmethod
    def _assert_short_code_available(
        connection: Connection[tuple[Any, ...]], short_code: str, connection_id: str,
    ) -> None:
        taken = connection.execute(
            "SELECT connection_id FROM system_provider_connections "
            "WHERE short_code=%s AND connection_id<>%s",
            (short_code, connection_id),
        ).fetchone()
        if taken is not None:
            raise ProviderConnectionAdminError("PROVIDER_SHORT_CODE_CONFLICT", 409)

    def _prepare(
        self, *, connection_id: str, provider_code: str, display_name: str, base_url: str,
        credential: str | bytes | None, logical_model_ids: Sequence[str], enabled: bool,
        version: int, previous_sealed: EncryptedCredential | None = None,
        discover_models: bool = True, verify_required: bool = True,
        adapter_type: str = "", verified_model_ids: Sequence[str] | None = None,
        test_credential: str | None = None, rejected_model_ids: list[str] | None = None,
    ) -> tuple[ProviderConnection, EncryptedCredential | None, tuple[object, ...]]:
        try:
            normalized_url = validate_provider_base_url(provider_code, base_url)
            raw = credential
            if raw is None and previous_sealed is not None and (verify_required or discover_models or verified_model_ids is not None):
                raw = self._cipher.decrypt(
                    connection_id, provider_code, previous_sealed.credential_version, previous_sealed,
                )
            profile = ProviderConnection(
                connection_id, provider_code, display_name, normalized_url, previous_sealed, True, version,
            )
            registry = AdapterRegistry(logical_models={connection_id: logical_model_ids})
            adapter = registry.adapter(provider_code, adapter_type) if provider_code == "CUSTOM" else registry.adapter(provider_code)
            if credential is not None and (not isinstance(credential, (str, bytes)) or not str(credential).strip()):
                raise AdapterError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            if verified_model_ids is not None:
                probe_credential = test_credential if test_credential is not None else raw
                if probe_credential is None and provider_code != "CUSTOM":
                    raise AdapterError("PROVIDER_CREDENTIAL_REQUIRED", 409)
                models = adapter.verify_models(profile, probe_credential, verified_model_ids)
            elif discover_models and provider_code == "OMNIROUTE" and rejected_model_ids is not None:
                # Refresh must distinguish a missing manually probed ID from
                # an explicitly listed non-text ID before preserving allowed rows.
                models, rejected = cast(OmniRouteAdapter, adapter).discover_models_with_rejected_ids(profile, raw)
                rejected_model_ids.extend(rejected)
            elif discover_models and provider_code in {"CUSTOM", "OMNIROUTE"}:
                # A catalog lookup is preview data, never evidence of model generation.
                models = adapter.discover_models(profile, raw)
            elif discover_models:
                adapter.verify(profile, raw)
                models = adapter.discover_models(profile, raw)
            elif verify_required:
                adapter.verify(profile, raw)
                models = (
                    ProviderCatalog.from_logical_models(connection_id, provider_code, logical_model_ids)
                    if provider_code == "OMNIROUTE" else ()
                )
            elif provider_code in {"OMNIROUTE", "EOUL_GATEWAY", "SENTENCE_TRANSFORMERS"} and logical_model_ids:
                # Explicit logical selections are cataloged without a remote lookup.
                models = (
                    ProviderCatalog.from_logical_models(connection_id, provider_code, logical_model_ids)
                    if provider_code == "OMNIROUTE" else adapter.discover_models(profile, raw)
                )
            else:
                # Saving a connection must not delete a previously discovered
                # catalog. Catalog refresh is the operation that owns it.
                models = ()
            sealed = previous_sealed
            if credential is not None:
                next_version = 1 if previous_sealed is None else previous_sealed.credential_version + 1
                secret_buffer = bytearray(credential.encode("utf-8") if isinstance(credential, str) else credential)
                try:
                    sealed = self._cipher.encrypt(connection_id, provider_code, next_version, bytes(secret_buffer))
                finally:
                    secret_buffer[:] = b"\0" * len(secret_buffer)
            return profile, sealed, models
        except (AdapterError, ProviderCatalogError, ProviderCredentialError, ProviderSettingsError) as error:
            raise ProviderConnectionAdminError(
                str(getattr(error, "code", str(error))), int(getattr(error, "status", 400)),
                retryable=bool(getattr(error, "retryable", False)),
            ) from None

    @staticmethod
    def _replace_models(
        connection: Connection[tuple[Any, ...]], context: ProviderConnectionAdminContext,
        connection_id: str, models: Sequence[object], catalog_version: int,
        *, mark_missing_stale: bool = True, keep_model_ids: Sequence[str] = (),
        rejected_model_ids: Sequence[str] = (),
    ) -> None:
        model_ids = [model.model_id for model in models]
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
        if mark_missing_stale:
            connection.execute(
                "UPDATE system_provider_models SET catalog_status='stale',updated_at=now() "
                "WHERE connection_id=%s AND NOT (model_id=ANY(%s))",
                (connection_id, list(dict.fromkeys((*model_ids, *keep_model_ids)))),
            )
        if rejected_model_ids:
            # A typed specialty row overrides an earlier successful manual
            # text probe. Stale status blocks resolver use even if overridden.
            connection.execute(
                "UPDATE system_provider_models SET reported_capabilities=%s,"
                "effective_capabilities=%s,override_applied=false,catalog_status='stale',"
                "catalog_version=%s,updated_at=now() "
                "WHERE connection_id=%s AND model_id=ANY(%s)",
                ([], [], catalog_version, connection_id, list(rejected_model_ids)),
            )

    @staticmethod
    def _set_allowed_models(
        connection: Connection[tuple[Any, ...]], context: ProviderConnectionAdminContext,
        connection_id: str, model_ids: Sequence[str],
    ) -> None:
        normalized = tuple(dict.fromkeys(model_ids))
        if len(normalized) != len(model_ids) or any(not item or len(item) > 256 for item in normalized):
            raise ProviderConnectionAdminError("PROVIDER_ALLOWED_MODEL_INVALID", 409)
        catalog = connection.execute(
            "SELECT model_id FROM system_provider_models WHERE connection_id=%s AND model_id=ANY(%s)",
            (connection_id, list(normalized)),
        ).fetchall()
        if {str(row[0]) for row in catalog} != set(normalized):
            raise ProviderConnectionAdminError("PROVIDER_ALLOWED_MODEL_UNKNOWN", 409)
        referenced = connection.execute(
            "SELECT count(*) FROM workspace_model_defaults WHERE connection_id=%s "
            "AND NOT (model_id=ANY(%s))",
            (connection_id, list(normalized)),
        ).fetchone()
        if referenced is not None and int(referenced[0]) > 0:
            raise ProviderConnectionAdminError("PROVIDER_MODEL_DEFAULT_REFERENCED", 409)
        connection.execute(
            "DELETE FROM system_provider_allowed_models WHERE connection_id=%s "
            "AND NOT (model_id=ANY(%s))",
            (connection_id, list(normalized)),
        )
        for model_id in normalized:
            connection.execute(
                "INSERT INTO system_provider_allowed_models (connection_id,model_id,updated_by) "
                "VALUES (%s,%s,%s) ON CONFLICT (connection_id,model_id) DO NOTHING",
                (connection_id, model_id, context.actor_id),
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
        validate_connection_policy(
            command.provider_code, command.access_mode, command.credential_requirement,
            command.short_code, command.adapter_type, command.base_url,
        )
        provider_name = command.provider_name.strip() or command.provider_code
        if (command.provider_name and not command.provider_name.strip()) or len(provider_name) > 256:
            raise ProviderConnectionAdminError("PROVIDER_NAME_INVALID", 409)
        if command.test_credential is not None and (command.provider_code != "CUSTOM" or command.access_mode != "personal"):
            raise ProviderConnectionAdminError("PROVIDER_TEST_CREDENTIAL_FORBIDDEN", 409)
        pending_custom = (
            command.provider_code == "CUSTOM" and command.access_mode == "personal"
            and command.credential is None and command.test_credential is None
            and not command.allowed_model_ids and not command.logical_model_ids
        )
        if command.provider_code == "CUSTOM" and command.access_mode == "personal" and command.test_credential is None and not pending_custom:
            raise ProviderConnectionAdminError("PROVIDER_CREDENTIAL_REQUIRED", 409)
        route_models = _omniroute_models(command.provider_code, command.allowed_model_ids, command.logical_model_ids)
        payload = normalized_connection_fingerprint_payload(
            connection_id=command.connection_id, provider_code=command.provider_code,
            display_name=command.display_name, base_url=command.base_url, enabled=command.enabled,
            expected_version=command.expected_version, logical_model_ids=command.logical_model_ids,
            access_mode=command.access_mode, credential_requirement=command.credential_requirement,
            short_code=command.short_code, adapter_type=command.adapter_type,
            allowed_model_ids=route_models,
            provider_name=provider_name,
            credential_fingerprint=(
                None if command.credential is None
                else self._cipher.idempotency_fingerprint(
                    command.connection_id, command.credential.encode("utf-8")
                )
            ),
            test_credential_fingerprint=(
                None if command.test_credential is None
                else self._cipher.idempotency_fingerprint(
                    command.connection_id, command.test_credential.encode("utf-8")
                )
            ),
        )

        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            if command.expected_version != 0 or connection.execute(
                "SELECT 1 FROM system_provider_connections WHERE connection_id=%s", (command.connection_id,),
            ).fetchone() is not None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            self._assert_short_code_available(connection, command.short_code, command.connection_id)
            if command.access_mode == "personal" and command.credential is not None:
                raise ProviderConnectionAdminError("PROVIDER_PERSONAL_SYSTEM_KEY_FORBIDDEN", 409)
            if command.access_mode == "public" and command.credential_requirement == "required" and command.credential is None:
                raise ProviderConnectionAdminError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            profile, sealed, models = self._prepare(
                connection_id=command.connection_id, provider_code=command.provider_code,
                display_name=command.display_name, base_url=command.base_url,
                credential=command.credential, logical_model_ids=route_models if command.provider_code == "OMNIROUTE" else command.logical_model_ids,
                enabled=command.enabled, version=1, discover_models=False,
                verify_required=command.access_mode == "public",
                adapter_type=command.adapter_type,
                verified_model_ids=command.allowed_model_ids if command.provider_code == "CUSTOM" and not pending_custom else None,
                test_credential=command.test_credential,
            )
            connection.execute(
                "INSERT INTO system_provider_connections (connection_id,provider_code,display_name,base_url,"
                "encrypted_credential,credential_nonce,encryption_key_version,credential_schema_version,"
                "credential_version,enabled,verification_status,verified_at,version,updated_by,trace_id,policy_version,"
                "access_mode,credential_requirement,short_code,adapter_type,provider_name) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s,%s,%s,%s,%s)",
                (profile.connection_id, profile.provider_code, profile.display_name, profile.base_url,
                 None if sealed is None else sealed.ciphertext, None if sealed is None else sealed.nonce,
                 None if sealed is None else sealed.encryption_key_version,
                 None if sealed is None else sealed.schema_version,
                 0 if sealed is None else sealed.credential_version, command.enabled,
                 "verified" if command.access_mode == "public" else "unverified",
                 datetime.now().astimezone() if command.access_mode == "public" else None,
                 context.actor_id, context.trace_id, context.policy_version,
                 command.access_mode, command.credential_requirement, command.short_code, command.adapter_type,
                 provider_name),
            )
            if models:
                self._replace_models(connection, context, command.connection_id, models, 1)
            self._set_allowed_models(connection, context, command.connection_id, route_models)
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
        if command.test_credential is not None and command.access_mode != "personal":
            raise ProviderConnectionAdminError("PROVIDER_TEST_CREDENTIAL_FORBIDDEN", 409)
        payload = normalized_connection_fingerprint_payload(
            connection_id=connection_id, provider_code=None, display_name=command.display_name,
            base_url=command.base_url, enabled=command.enabled, expected_version=command.expected_version,
            logical_model_ids=command.logical_model_ids,
            access_mode=command.access_mode, credential_requirement=command.credential_requirement,
            short_code=command.short_code, adapter_type=command.adapter_type,
            allowed_model_ids=command.allowed_model_ids,
            provider_name=command.provider_name,
            credential_fingerprint=(
                None if command.credential is None
                else self._cipher.idempotency_fingerprint(
                    connection_id, command.credential.encode("utf-8")
                )
            ),
            test_credential_fingerprint=(
                None if command.test_credential is None
                else self._cipher.idempotency_fingerprint(
                    connection_id, command.test_credential.encode("utf-8")
                )
            ),
        )

        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            current = self._load(connection, connection_id)
            if int(current[12]) != command.expected_version:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            if str(current[1]) == "CUSTOM" and str(current[16]) != command.adapter_type:
                raise ProviderConnectionAdminError("PROVIDER_ADAPTER_IMMUTABLE", 409)
            if str(current[1]) != "CUSTOM" and command.test_credential is not None:
                raise ProviderConnectionAdminError("PROVIDER_TEST_CREDENTIAL_FORBIDDEN", 409)
            validate_connection_policy(
                str(current[1]), command.access_mode, command.credential_requirement,
                command.short_code, command.adapter_type, command.base_url,
                allow_existing_legacy_custom=(str(current[1]) == "CUSTOM" and str(current[16]) == "CUSTOM"),
            )
            provider_name = command.provider_name.strip() or str(current[17])
            if (command.provider_name and not command.provider_name.strip()) or not provider_name or len(provider_name) > 256:
                raise ProviderConnectionAdminError("PROVIDER_NAME_INVALID", 409)
            self._assert_short_code_available(connection, command.short_code, connection_id)
            if command.access_mode == "personal" and command.credential is not None:
                raise ProviderConnectionAdminError("PROVIDER_PERSONAL_SYSTEM_KEY_FORBIDDEN", 409)
            if (command.access_mode == "public" and command.credential_requirement == "required"
                    and command.credential is None and current[4] is None):
                raise ProviderConnectionAdminError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            custom = str(current[1]) == "CUSTOM"
            route = str(current[1]) == "OMNIROUTE"
            current_allowed = (
                tuple(str(row[0]) for row in connection.execute(
                    "SELECT model_id FROM system_provider_allowed_models WHERE connection_id=%s ORDER BY model_id",
                    (connection_id,),
                ).fetchall()) if custom or route else ()
            )
            route_models_unchanged = route and bool(current_allowed) and (
                len(command.allowed_model_ids) == len(current_allowed)
                and set(command.allowed_model_ids) == set(current_allowed)
                and (not command.logical_model_ids or (
                    len(command.logical_model_ids) == len(current_allowed)
                    and set(command.logical_model_ids) == set(current_allowed)
                ))
            )
            route_models = (
                current_allowed if route_models_unchanged
                else _omniroute_models(str(current[1]), command.allowed_model_ids, command.logical_model_ids)
            )
            route_models_changed = route and set(route_models) != set(current_allowed)
            custom_probe = custom and (
                command.base_url != str(current[3]) or command.credential is not None
                or command.test_credential is not None or command.access_mode != str(current[13])
                or set(command.allowed_model_ids) != set(current_allowed)
                or (command.enabled and not bool(current[9]))
            )
            if custom_probe and command.access_mode == "personal" and command.test_credential is None:
                raise ProviderConnectionAdminError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            verify_required = not custom and command.access_mode == "public" and (
                command.base_url != str(current[3]) or command.credential is not None
                or str(current[13]) != "public" or str(current[14]) != command.credential_requirement
                or route_models_changed
            )
            profile, sealed, models = self._prepare(
                connection_id=connection_id, provider_code=str(current[1]),
                display_name=command.display_name, base_url=command.base_url,
                credential=command.credential,
                logical_model_ids=(route_models if verify_required or route_models_changed else ()) if route else command.logical_model_ids,
                enabled=command.enabled, version=command.expected_version + 1,
                previous_sealed=None if command.access_mode == "personal" else self._sealed(current), discover_models=False,
                verify_required=verify_required,
                adapter_type=command.adapter_type,
                verified_model_ids=command.allowed_model_ids if custom_probe else None,
                test_credential=command.test_credential,
            )
            verification_status = (
                "unverified" if command.access_mode == "personal"
                else "verified" if verify_required or custom_probe else str(current[10])
            )
            verified_at = (
                None if command.access_mode == "personal"
                else datetime.now().astimezone() if verify_required or custom_probe else current[11]
            )
            row = connection.execute(
                "UPDATE system_provider_connections SET display_name=%s,base_url=%s,encrypted_credential=%s,"
                "credential_nonce=%s,encryption_key_version=%s,credential_schema_version=%s,credential_version=%s,"
                "enabled=%s,verification_status=%s,verified_at=%s,access_mode=%s,credential_requirement=%s,"
                "short_code=%s,adapter_type=%s,provider_name=%s,version=version+1,updated_at=now(),"
                "updated_by=%s,trace_id=%s,policy_version=%s WHERE connection_id=%s AND version=%s RETURNING version",
                (profile.display_name, profile.base_url, None if sealed is None else sealed.ciphertext,
                 None if sealed is None else sealed.nonce, None if sealed is None else sealed.encryption_key_version,
                 None if sealed is None else sealed.schema_version, 0 if sealed is None else sealed.credential_version,
                 command.enabled, verification_status, verified_at, command.access_mode,
                 command.credential_requirement, command.short_code, command.adapter_type,
                 provider_name,
                 context.actor_id, context.trace_id, context.policy_version,
                 connection_id, command.expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            if models:
                self._replace_models(connection, context, connection_id, models, int(row[0]))
            if not route_models_unchanged:
                self._set_allowed_models(connection, context, connection_id, route_models)
            if route and command.access_mode == "personal" and (
                route_models_changed or profile.base_url != str(current[3])
            ):
                connection.execute(
                    "UPDATE user_provider_credentials SET verification_status='unverified',"
                    "verified_at=NULL,updated_at=now() "
                    "WHERE connection_id=%s AND verification_status='verified'",
                    (connection_id,),
                )
            return self._safe_by_id(connection, connection_id)

        return self._mutations.run(
            context, operation="provider_connection.update", idempotency_key=idempotency_key,
            fingerprint_payload=payload, mutation=mutation,
            audit=ProviderAdminAudit("provider_connection.updated", "provider_connection", connection_id, command.expected_version + 1),
        )

    def delete_credential(
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

    def delete_connection(
        self, context: ProviderConnectionAdminContext, connection_id: str,
        expected_version: int, idempotency_key: str,
    ) -> tuple[dict[str, object], bool]:
        def mutation(connection: Connection[tuple[Any, ...]]) -> dict[str, object]:
            row = connection.execute(
                "SELECT version FROM system_provider_connections WHERE connection_id=%s FOR UPDATE",
                (connection_id,),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("PROVIDER_CONNECTION_NOT_FOUND", 404)
            if int(row[0]) != expected_version:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            user_keys = connection.execute(
                "SELECT count(*) FROM user_provider_credentials WHERE connection_id=%s",
                (connection_id,),
            ).fetchone()
            defaults = connection.execute(
                "SELECT count(*) FROM workspace_model_defaults WHERE connection_id=%s",
                (connection_id,),
            ).fetchone()
            references = {
                "user_credentials": int(user_keys[0]) if user_keys else 0,
                "workspace_defaults": int(defaults[0]) if defaults else 0,
            }
            if any(references.values()):
                raise ProviderConnectionAdminError(
                    "PROVIDER_CONNECTION_REFERENCED", 409, references=references,
                )
            connection.execute(
                "DELETE FROM system_provider_connections WHERE connection_id=%s AND version=%s",
                (connection_id, expected_version),
            )
            return {"connection_id": connection_id, "deleted": True, "version": expected_version}

        return self._mutations.run(
            context, operation="provider_connection.delete", idempotency_key=idempotency_key,
            fingerprint_payload={"connection_id": connection_id, "expected_version": expected_version},
            mutation=mutation,
            audit=ProviderAdminAudit(
                "provider_connection.deleted", "provider_connection", connection_id, expected_version,
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
            if str(current[13]) != "public":
                raise ProviderConnectionAdminError("PROVIDER_PERSONAL_SYSTEM_KEY_FORBIDDEN", 409)
            model_rows = connection.execute(
                "SELECT model_id FROM " + (
                    "system_provider_allowed_models" if str(current[1]) in {"CUSTOM", "OMNIROUTE"} else "system_provider_models"
                ) + " WHERE connection_id=%s ORDER BY model_id",
                (connection_id,),
            ).fetchall()
            profile, sealed, models = self._prepare(
                connection_id=connection_id, provider_code=str(current[1]),
                display_name=str(current[2]), base_url=str(current[3]),
                credential=command.credential,
                logical_model_ids=[str(row[0]) for row in model_rows], enabled=bool(current[9]),
                version=command.expected_version + 1, previous_sealed=self._sealed(current),
                discover_models=False,
                adapter_type=str(current[16]),
                verified_model_ids=[str(row[0]) for row in model_rows] if str(current[1]) == "CUSTOM" else None,
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
            if str(current[13]) == "personal":
                raise ProviderConnectionAdminError("PROVIDER_CREDENTIAL_REQUIRED", 409)
            model_rows = connection.execute(
                "SELECT model_id FROM system_provider_models WHERE connection_id=%s ORDER BY model_id",
                (connection_id,),
            ).fetchall()
            rejected_model_ids: list[str] = []
            profile, _sealed_value, models = self._prepare(
                connection_id=connection_id, provider_code=str(current[1]), display_name=str(current[2]),
                base_url=str(current[3]), credential=None,
                logical_model_ids=[str(row[0]) for row in model_rows], enabled=bool(current[9]),
                version=expected_version + 1, previous_sealed=self._sealed(current),
                adapter_type=str(current[16]), rejected_model_ids=rejected_model_ids,
            )
            verification_sql = (
                "" if str(current[1]) in {"CUSTOM", "OMNIROUTE"}
                else "verification_status='verified',verified_at=now(),"
            )
            row = connection.execute(
                "UPDATE system_provider_connections SET " + verification_sql +
                "version=version+1,updated_at=now(),updated_by=%s,trace_id=%s,policy_version=%s "
                "WHERE connection_id=%s AND version=%s RETURNING version",
                (context.actor_id, context.trace_id, context.policy_version, connection_id, expected_version),
            ).fetchone()
            if row is None:
                raise ProviderConnectionAdminError("VERSION_CONFLICT", 409)
            allowed_model_ids = (
                tuple(str(item[0]) for item in connection.execute(
                    "SELECT model_id FROM system_provider_allowed_models WHERE connection_id=%s ORDER BY model_id",
                    (connection_id,),
                ).fetchall()) if str(current[1]) == "OMNIROUTE" else ()
            )
            self._replace_models(
                connection, context, profile.connection_id, models, int(row[0]),
                mark_missing_stale=str(current[1]) != "CUSTOM",
                keep_model_ids=tuple(model for model in allowed_model_ids if model not in rejected_model_ids),
                rejected_model_ids=rejected_model_ids,
            )
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
