"""Workspace-scoped Provider Profile, Model Deployment and role binding settings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
import re
from threading import RLock
from typing import Mapping, Protocol, cast
from urllib.parse import urlsplit
import urllib.error
import urllib.request

from .cloud_storage import CloudAccessContext, CloudDatabaseError, PostgresCloudStore


PROVIDER_CODES = (
    "CEREBRAS", "GROQ", "MISTRAL", "OPENAI", "UPSTAGE", "GEMINI",
    "OPENROUTER", "ANTHROPIC", "OLLAMA",
)
MODEL_ROLES = (
    "text", "vision", "document_parser", "audio_understanding", "speech_to_text", "embedding", "reranker",
)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CREDENTIAL_ENV = {
    "CEREBRAS": "CEREBRAS_API_KEY",
    "GROQ": "GROQ_API_KEY",
    "MISTRAL": "MISTRAL_API_KEY",
    "OPENAI": "OPENAI_API_KEY",
    "UPSTAGE": "UPSTAGE_API_KEY",
    "GEMINI": "GEMINI_API_KEY",
    "OPENROUTER": "OPENROUTER_API_KEY",
    "ANTHROPIC": "ANTHROPIC_API_KEY",
    "OLLAMA": "OLLAMA_BASE_URL",
}
_PROVIDER_BASE_URLS = {
    "CEREBRAS": "https://api.cerebras.ai/v1",
    "GROQ": "https://api.groq.com/openai/v1",
    "MISTRAL": "https://api.mistral.ai/v1",
    "OPENAI": "https://api.openai.com/v1",
    "UPSTAGE": "https://api.upstage.ai/v1",
    "GEMINI": "https://generativelanguage.googleapis.com/v1beta",
    "OPENROUTER": "https://openrouter.ai/api/v1",
    "ANTHROPIC": "https://api.anthropic.com/v1",
}


class ProviderSettingsError(ValueError):
    def __init__(self, code: str, status: int = 400, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProviderSettingsContext:
    tenant_id: str
    workspace_id: str
    actor_id: str
    trace_id: str
    policy_version: str

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.workspace_id, self.actor_id, self.trace_id, self.policy_version):
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise ProviderSettingsError("PROVIDER_SETTINGS_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class ProviderProfileView:
    profile_id: str
    provider_code: str
    provider_kind: str
    base_url: str
    active: bool
    credential_configured: bool
    version: int

    @property
    def etag(self) -> str:
        return f'"provider-profile:{self.profile_id}:{self.version}"'


@dataclass(frozen=True, slots=True)
class ModelDeploymentView:
    deployment_id: str
    profile_id: str
    provider_code: str
    model_id: str
    roles: tuple[str, ...]
    active: bool
    selected: bool
    version: int

    @property
    def etag(self) -> str:
        return f'"model-deployment:{self.deployment_id}:{self.version}"'


@dataclass(frozen=True, slots=True)
class ProviderSettingsSnapshot:
    workspace_id: str
    profiles: tuple[ProviderProfileView, ...]
    deployments: tuple[ModelDeploymentView, ...]
    role_bindings: dict[str, str]
    binding_version: int


@dataclass(frozen=True, slots=True)
class ProviderConnectionStatus:
    provider_code: str
    status: str
    checked_at: str

    def __post_init__(self) -> None:
        _validate_provider(self.provider_code)
        if self.status not in {"ready", "unconfigured", "unavailable"}:
            raise ProviderSettingsError("PROVIDER_CONNECTION_STATUS_INVALID")


class CredentialPresenceResolver:
    def configured(self, provider_code: str) -> bool: ...
    def resolve(self, provider_code: str) -> str | None: ...


class ServerCredentialPresenceResolver:
    """Reads server environment only and returns a boolean, never the value or variable name."""

    def configured(self, provider_code: str) -> bool:
        name = _CREDENTIAL_ENV.get(provider_code)
        return bool(name and os.environ.get(name, "").strip())

    def resolve(self, provider_code: str) -> str | None:
        name = _CREDENTIAL_ENV.get(provider_code)
        value = os.environ.get(name, "").strip() if name else ""
        return value or None


class ProviderConnectionChecker(Protocol):
    def check(self, profile: ProviderProfileView, credential: str | None) -> ProviderConnectionStatus: ...


class ProviderConnectionTransport(Protocol):
    def get_status(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> int: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibProviderConnectionTransport:
    def get_status(self, url: str, headers: Mapping[str, str], timeout_seconds: float) -> int:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        with opener.open(request, timeout=timeout_seconds) as response:
            return int(response.status)


class HttpProviderConnectionChecker:
    """Server-only bounded readiness probe. Response bodies and credentials never leave this port."""

    def __init__(self, transport: ProviderConnectionTransport | None = None) -> None:
        self._transport = transport or UrllibProviderConnectionTransport()

    @staticmethod
    def _request(profile: ProviderProfileView, credential: str | None) -> tuple[str, dict[str, str]]:
        base = profile.base_url.rstrip("/")
        if profile.provider_code == "OLLAMA":
            return f"{base}/api/tags", {}
        if profile.provider_code == "GEMINI":
            return f"{base}/models", {"x-goog-api-key": credential or ""}
        if profile.provider_code == "ANTHROPIC":
            return f"{base}/models", {"x-api-key": credential or "", "anthropic-version": "2023-06-01"}
        return f"{base}/models", {"authorization": f"Bearer {credential or ''}"}

    def check(self, profile: ProviderProfileView, credential: str | None) -> ProviderConnectionStatus:
        url, headers = self._request(profile, credential)
        try:
            if self._transport.get_status(url, headers, 5.0) != 200:
                raise ProviderSettingsError("PROVIDER_CONNECTION_UNAVAILABLE", 503, retryable=True)
        except ProviderSettingsError:
            raise
        except (urllib.error.URLError, OSError, ValueError):
            raise ProviderSettingsError("PROVIDER_CONNECTION_UNAVAILABLE", 503, retryable=True) from None
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return ProviderConnectionStatus(profile.provider_code, "ready", checked_at)


class ProviderSettingsRepository(Protocol):
    def list_profiles(self, context: ProviderSettingsContext) -> tuple[ProviderProfileView, ...]: ...
    def save_profile(self, context: ProviderSettingsContext, profile: ProviderProfileView,
                     expected_version: int) -> ProviderProfileView: ...
    def list_deployments(self, context: ProviderSettingsContext) -> tuple[ModelDeploymentView, ...]: ...
    def save_deployment(self, context: ProviderSettingsContext, deployment: ModelDeploymentView,
                        expected_version: int) -> ModelDeploymentView: ...
    def get_role_bindings(self, context: ProviderSettingsContext) -> tuple[dict[str, str], int]: ...
    def save_role_bindings(self, context: ProviderSettingsContext, bindings: Mapping[str, str],
                           expected_version: int) -> tuple[dict[str, str], int]: ...


def _validate_provider(provider_code: str) -> str:
    if provider_code not in PROVIDER_CODES:
        raise ProviderSettingsError("PROVIDER_CODE_UNSUPPORTED")
    return provider_code


def _validate_base_url(provider_code: str, value: str) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > 2048:
        raise ProviderSettingsError("PROVIDER_BASE_URL_INVALID")
    parsed = urlsplit(value)
    allowed_schemes = {"http", "https"} if provider_code == "OLLAMA" else {"https"}
    if (parsed.scheme not in allowed_schemes or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ProviderSettingsError("PROVIDER_BASE_URL_INVALID")
    normalized = value.rstrip("/")
    if provider_code == "OLLAMA":
        configured = os.environ.get("OLLAMA_BASE_URL", "").strip().rstrip("/")
        if not configured or normalized != configured:
            raise ProviderSettingsError("PROVIDER_BASE_URL_INVALID")
    elif normalized != _PROVIDER_BASE_URLS[provider_code]:
        raise ProviderSettingsError("PROVIDER_BASE_URL_INVALID")
    return normalized


def _validate_deployment(deployment_id: str, model_id: str, roles: tuple[str, ...]) -> None:
    if not _SAFE_ID.fullmatch(deployment_id) or not model_id.strip() or len(model_id) > 256:
        raise ProviderSettingsError("MODEL_DEPLOYMENT_INVALID")
    if not roles or len(set(roles)) != len(roles) or any(role not in MODEL_ROLES for role in roles):
        raise ProviderSettingsError("MODEL_ROLE_UNSUPPORTED")


class ReferenceProviderSettingsRepository:
    def __init__(self) -> None:
        self._lock = RLock()
        self._profiles: dict[tuple[str, str, str], ProviderProfileView] = {}
        self._deployments: dict[tuple[str, str, str], ModelDeploymentView] = {}
        self._bindings: dict[tuple[str, str], tuple[dict[str, str], int]] = {}

    @staticmethod
    def _scope(context: ProviderSettingsContext) -> tuple[str, str]:
        return context.tenant_id, context.workspace_id

    def list_profiles(self, context: ProviderSettingsContext) -> tuple[ProviderProfileView, ...]:
        scope = self._scope(context)
        with self._lock:
            return tuple(value for key, value in self._profiles.items() if key[:2] == scope)

    def save_profile(self, context: ProviderSettingsContext, profile: ProviderProfileView,
                     expected_version: int) -> ProviderProfileView:
        key = (*self._scope(context), profile.provider_code)
        with self._lock:
            current = self._profiles.get(key)
            actual = 0 if current is None else current.version
            if actual != expected_version:
                raise ProviderSettingsError("VERSION_CONFLICT", 409)
            stored = ProviderProfileView(
                profile.profile_id, profile.provider_code, profile.provider_kind, profile.base_url,
                profile.active, profile.credential_configured, actual + 1,
            )
            self._profiles[key] = stored
            return stored

    def list_deployments(self, context: ProviderSettingsContext) -> tuple[ModelDeploymentView, ...]:
        scope = self._scope(context)
        with self._lock:
            return tuple(value for key, value in self._deployments.items() if key[:2] == scope)

    def save_deployment(self, context: ProviderSettingsContext, deployment: ModelDeploymentView,
                        expected_version: int) -> ModelDeploymentView:
        key = (*self._scope(context), deployment.deployment_id)
        with self._lock:
            current = self._deployments.get(key)
            actual = 0 if current is None else current.version
            if actual != expected_version:
                raise ProviderSettingsError("VERSION_CONFLICT", 409)
            stored = ModelDeploymentView(
                deployment.deployment_id, deployment.profile_id, deployment.provider_code,
                deployment.model_id, deployment.roles, deployment.active, deployment.selected,
                actual + 1,
            )
            self._deployments[key] = stored
            return stored

    def get_role_bindings(self, context: ProviderSettingsContext) -> tuple[dict[str, str], int]:
        with self._lock:
            bindings, version = self._bindings.get(self._scope(context), ({}, 0))
            return dict(bindings), version

    def save_role_bindings(self, context: ProviderSettingsContext, bindings: Mapping[str, str],
                           expected_version: int) -> tuple[dict[str, str], int]:
        scope = self._scope(context)
        with self._lock:
            _current, actual = self._bindings.get(scope, ({}, 0))
            if actual != expected_version:
                raise ProviderSettingsError("VERSION_CONFLICT", 409)
            value = (dict(bindings), actual + 1)
            self._bindings[scope] = value
            return dict(value[0]), value[1]


class PostgresProviderSettingsRepository:
    def __init__(self, cloud_store: PostgresCloudStore) -> None:
        self._cloud_store = cloud_store

    def _transaction(self, context: ProviderSettingsContext, capability: str):
        return self._cloud_store._transaction(CloudAccessContext(
            context.tenant_id, context.workspace_id, context.actor_id, capability,
        ))

    @staticmethod
    def _profile(row: tuple[object, ...]) -> ProviderProfileView:
        return ProviderProfileView(str(row[0]), str(row[1]), str(row[2]), str(row[3]), bool(row[4]), False, int(cast(int, row[5])))

    @staticmethod
    def _deployment(row: tuple[object, ...]) -> ModelDeploymentView:
        return ModelDeploymentView(str(row[0]), str(row[1]), str(row[2]), str(row[3]), tuple(cast(list[str], row[4])), bool(row[5]), bool(row[6]), int(cast(int, row[7])))

    def list_profiles(self, context: ProviderSettingsContext) -> tuple[ProviderProfileView, ...]:
        try:
            with self._transaction(context, "model.settings.read") as connection:
                rows = connection.execute("SELECT profile_id,provider_code,provider_kind,base_url,active,version FROM provider_setting_profiles ORDER BY provider_code").fetchall()
            return tuple(self._profile(cast(tuple[object, ...], row)) for row in rows)
        except CloudDatabaseError as error:
            raise ProviderSettingsError(error.code, 503, retryable=error.retryable) from None

    def save_profile(self, context: ProviderSettingsContext, profile: ProviderProfileView,
                     expected_version: int) -> ProviderProfileView:
        try:
            with self._transaction(context, "model.settings.write") as connection:
                if expected_version == 0:
                    row = connection.execute(
                        "INSERT INTO provider_setting_profiles (tenant_id,workspace_id,profile_id,provider_code,provider_kind,base_url,active,version,updated_by,policy_version,trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s) RETURNING profile_id,provider_code,provider_kind,base_url,active,version",
                        (context.tenant_id, context.workspace_id, profile.profile_id, profile.provider_code, profile.provider_kind, profile.base_url, profile.active, context.actor_id, context.policy_version, context.trace_id),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "UPDATE provider_setting_profiles SET base_url=%s,active=%s,version=version+1,updated_by=%s,policy_version=%s,trace_id=%s,updated_at=now() WHERE provider_code=%s AND version=%s RETURNING profile_id,provider_code,provider_kind,base_url,active,version",
                        (profile.base_url, profile.active, context.actor_id, context.policy_version, context.trace_id, profile.provider_code, expected_version),
                    ).fetchone()
                if row is None:
                    raise ProviderSettingsError("VERSION_CONFLICT", 409)
                return self._profile(cast(tuple[object, ...], row))
        except ProviderSettingsError:
            raise
        except CloudDatabaseError as error:
            raise ProviderSettingsError(error.code, 503, retryable=error.retryable) from None

    def list_deployments(self, context: ProviderSettingsContext) -> tuple[ModelDeploymentView, ...]:
        try:
            with self._transaction(context, "model.settings.read") as connection:
                rows = connection.execute("SELECT d.deployment_id,d.profile_id,p.provider_code,d.model_id,d.roles,d.active,d.selected,d.version FROM provider_setting_deployments d JOIN provider_setting_profiles p USING (tenant_id,workspace_id,profile_id) ORDER BY p.provider_code,d.deployment_id").fetchall()
            return tuple(self._deployment(cast(tuple[object, ...], row)) for row in rows)
        except CloudDatabaseError as error:
            raise ProviderSettingsError(error.code, 503, retryable=error.retryable) from None

    def save_deployment(self, context: ProviderSettingsContext, deployment: ModelDeploymentView,
                        expected_version: int) -> ModelDeploymentView:
        try:
            with self._transaction(context, "model.settings.write") as connection:
                if expected_version == 0:
                    row = connection.execute(
                        "INSERT INTO provider_setting_deployments (tenant_id,workspace_id,deployment_id,profile_id,model_id,roles,active,selected,version,updated_by,policy_version,trace_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s) RETURNING deployment_id,profile_id,(SELECT provider_code FROM provider_setting_profiles WHERE profile_id=%s),model_id,roles,active,selected,version",
                        (context.tenant_id, context.workspace_id, deployment.deployment_id, deployment.profile_id, deployment.model_id, list(deployment.roles), deployment.active, deployment.selected, context.actor_id, context.policy_version, context.trace_id, deployment.profile_id),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "UPDATE provider_setting_deployments d SET model_id=%s,roles=%s,active=%s,selected=%s,version=version+1,updated_by=%s,policy_version=%s,trace_id=%s,updated_at=now() FROM provider_setting_profiles p WHERE d.tenant_id=p.tenant_id AND d.workspace_id=p.workspace_id AND d.profile_id=p.profile_id AND d.deployment_id=%s AND d.version=%s RETURNING d.deployment_id,d.profile_id,p.provider_code,d.model_id,d.roles,d.active,d.selected,d.version",
                        (deployment.model_id, list(deployment.roles), deployment.active, deployment.selected, context.actor_id, context.policy_version, context.trace_id, deployment.deployment_id, expected_version),
                    ).fetchone()
                if row is None:
                    raise ProviderSettingsError("VERSION_CONFLICT", 409)
                return self._deployment(cast(tuple[object, ...], row))
        except ProviderSettingsError:
            raise
        except CloudDatabaseError as error:
            raise ProviderSettingsError(error.code, 503, retryable=error.retryable) from None

    def get_role_bindings(self, context: ProviderSettingsContext) -> tuple[dict[str, str], int]:
        try:
            with self._transaction(context, "model.settings.read") as connection:
                rows = connection.execute("SELECT role,deployment_id,version FROM provider_setting_role_bindings ORDER BY role").fetchall()
            bindings = {str(row[0]): str(row[1]) for row in rows}
            version = max((int(cast(int, row[2])) for row in rows), default=0)
            return bindings, version
        except CloudDatabaseError as error:
            raise ProviderSettingsError(error.code, 503, retryable=error.retryable) from None

    def save_role_bindings(self, context: ProviderSettingsContext, bindings: Mapping[str, str],
                           expected_version: int) -> tuple[dict[str, str], int]:
        try:
            with self._transaction(context, "model.settings.write") as connection:
                rows = connection.execute("SELECT role,deployment_id,version FROM provider_setting_role_bindings FOR UPDATE").fetchall()
                actual = max((int(cast(int, row[2])) for row in rows), default=0)
                if actual != expected_version:
                    raise ProviderSettingsError("VERSION_CONFLICT", 409)
                if bindings:
                    valid = connection.execute("SELECT deployment_id,roles,active FROM provider_setting_deployments WHERE deployment_id = ANY(%s)", (list(set(bindings.values())),)).fetchall()
                    capabilities = {str(row[0]): (set(cast(list[str], row[1])), bool(row[2])) for row in valid}
                    if any(deployment not in capabilities or not capabilities[deployment][1] or role not in capabilities[deployment][0] for role, deployment in bindings.items()):
                        raise ProviderSettingsError("MODEL_BINDING_INVALID")
                connection.execute("DELETE FROM provider_setting_role_bindings")
                next_version = actual + 1
                now = datetime.now(timezone.utc)
                for role, deployment_id in bindings.items():
                    connection.execute("INSERT INTO provider_setting_role_bindings (tenant_id,workspace_id,role,deployment_id,version,updated_by,policy_version,trace_id,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)", (context.tenant_id, context.workspace_id, role, deployment_id, next_version, context.actor_id, context.policy_version, context.trace_id, now))
                return dict(bindings), next_version
        except ProviderSettingsError:
            raise
        except CloudDatabaseError as error:
            raise ProviderSettingsError(error.code, 503, retryable=error.retryable) from None


class ProviderSettingsService:
    def __init__(self, repository: ProviderSettingsRepository,
                 credential_resolver: CredentialPresenceResolver,
                 connection_checker: ProviderConnectionChecker | None = None) -> None:
        self._repository = repository
        self._credentials = credential_resolver
        self._connection_checker = connection_checker or HttpProviderConnectionChecker()

    def snapshot(self, context: ProviderSettingsContext) -> ProviderSettingsSnapshot:
        stored = {item.provider_code: item for item in self._repository.list_profiles(context)}
        profiles = tuple(
            ProviderProfileView(
                (stored[code].profile_id if code in stored else f"provider-{code.lower()}"),
                code,
                (stored[code].provider_kind if code in stored else ("server_internal" if code == "OLLAMA" else "external_api")),
                (stored[code].base_url if code in stored else ""),
                (stored[code].active if code in stored else False),
                self._credentials.configured(code),
                (stored[code].version if code in stored else 0),
            ) for code in PROVIDER_CODES
        )
        bindings, binding_version = self._repository.get_role_bindings(context)
        return ProviderSettingsSnapshot(
            context.workspace_id, profiles, self._repository.list_deployments(context),
            bindings, binding_version,
        )

    def save_profile(self, context: ProviderSettingsContext, *, provider_code: str,
                     base_url: str, active: bool, expected_version: int) -> ProviderProfileView:
        code = _validate_provider(provider_code)
        profile = ProviderProfileView(
            f"provider-{code.lower()}", code,
            "server_internal" if code == "OLLAMA" else "external_api",
            _validate_base_url(code, base_url), bool(active),
            self._credentials.configured(code), 0,
        )
        stored = self._repository.save_profile(context, profile, expected_version)
        return ProviderProfileView(
            stored.profile_id, stored.provider_code, stored.provider_kind, stored.base_url,
            stored.active, self._credentials.configured(code), stored.version,
        )

    def check_connection(self, context: ProviderSettingsContext, provider_code: str) -> ProviderConnectionStatus:
        code = _validate_provider(provider_code)
        profiles = {item.provider_code: item for item in self._repository.list_profiles(context)}
        profile = profiles.get(code)
        if profile is None:
            raise ProviderSettingsError("PROVIDER_PROFILE_REQUIRED", 409)
        if not profile.active:
            raise ProviderSettingsError("PROVIDER_PROFILE_INACTIVE", 409)
        credential = self._credentials.resolve(code)
        if code != "OLLAMA" and credential is None:
            raise ProviderSettingsError("PROVIDER_CREDENTIAL_REQUIRED", 409)
        return self._connection_checker.check(profile, credential)

    def save_deployment(self, context: ProviderSettingsContext, *, deployment_id: str,
                        provider_code: str, model_id: str, roles: tuple[str, ...], active: bool,
                        selected: bool, expected_version: int) -> ModelDeploymentView:
        code = _validate_provider(provider_code)
        _validate_deployment(deployment_id, model_id, roles)
        profiles = {item.provider_code: item for item in self._repository.list_profiles(context)}
        if code not in profiles:
            raise ProviderSettingsError("PROVIDER_PROFILE_REQUIRED", 409)
        deployment = ModelDeploymentView(
            deployment_id, profiles[code].profile_id, code, model_id.strip(), roles,
            bool(active), bool(selected), 0,
        )
        return self._repository.save_deployment(context, deployment, expected_version)

    def save_role_bindings(self, context: ProviderSettingsContext, *, bindings: Mapping[str, str],
                           expected_version: int) -> tuple[dict[str, str], int]:
        if any(role not in MODEL_ROLES for role in bindings):
            raise ProviderSettingsError("MODEL_ROLE_UNSUPPORTED")
        deployments = {item.deployment_id: item for item in self._repository.list_deployments(context)}
        if any(deployment_id not in deployments or not deployments[deployment_id].active or role not in deployments[deployment_id].roles for role, deployment_id in bindings.items()):
            raise ProviderSettingsError("MODEL_BINDING_INVALID")
        return self._repository.save_role_bindings(context, bindings, expected_version)
