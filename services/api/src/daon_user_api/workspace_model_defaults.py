from __future__ import annotations

import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Iterator, Sequence

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore
from .provider_credentials import EncryptedCredential, ProviderCredentialCipher, ProviderCredentialError
from .provider_settings import ProviderSettingsError, validate_provider_base_url


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_ACTIVE_PROVIDERS = {
    "text_generation": frozenset({
        "OLLAMA", "GROQ", "MISTRAL", "UPSTAGE", "OPENROUTER", "OMNIROUTE", "EOUL_GATEWAY",
    }),
    "image_understanding": frozenset({"UPSTAGE"}),
    "document_parsing": frozenset({"UPSTAGE"}),
}
_ROUTING_GATEWAYS = frozenset({"EOUL_GATEWAY", "OMNIROUTE"})


class WorkspaceModelUnavailable(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class WorkspaceModelContext:
    tenant_id: str
    workspace_id: str
    actor_id: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None for value in (
            self.tenant_id, self.workspace_id, self.actor_id,
        )):
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
    def __init__(self, store: PostgresCloudStore, cipher: ProviderCredentialCipher) -> None:
        self._store = store
        self._cipher = cipher

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
        if encrypted is None:
            if provider_code != "OLLAMA":
                raise WorkspaceModelUnavailable("PROVIDER_CREDENTIAL_REQUIRED")
        else:
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
            except ProviderCredentialError as error:
                raise WorkspaceModelUnavailable(str(error)) from None

        resolved = ResolvedModel(
            connection_id=connection_id, provider_code=provider_code, model_id=model_id,
            capability=capability, base_url=base_url, credential_version=credential_version,
            default_version=int(values[0]), catalog_version=int(values[14]),
            provider_kind="local" if provider_code == "OLLAMA" else "external_api",
            routing_owner="gateway" if provider_code in _ROUTING_GATEWAYS else "provider",
            daon_fallback_allowed=provider_code not in _ROUTING_GATEWAYS,
            _credential=credential,
        )
        try:
            yield resolved
        finally:
            resolved.release()
