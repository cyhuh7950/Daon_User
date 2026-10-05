from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Mapping, Protocol, Sequence
import urllib.error
import urllib.request

from .provider_catalog import DiscoveredModel, ProviderCatalog, ProviderCatalogError
from .provider_credentials import ProviderConnection
from .provider_settings import ProviderSettingsError, validate_provider_base_url


_TIMEOUT_SECONDS = 5.0
_MAX_RESPONSE_BYTES = 1024 * 1024


class AdapterError(ValueError):
    def __init__(self, code: str, status: int = 400, *, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    connection_id: str
    provider_code: str
    status: str
    checked_at: str
    routing_owner: str
    daon_fallback_allowed: bool


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    payload: object


class AdapterTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object] | None,
        timeout_seconds: float,
        *,
        follow_redirects: bool,
    ) -> TransportResponse: ...


class ConnectionAdapter(Protocol):
    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult: ...

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]: ...

    def verify_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes,
        model_ids: Sequence[str],
    ) -> tuple[DiscoveredModel, ...]: ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibAdapterTransport:
    def request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object] | None,
        timeout_seconds: float,
        *,
        follow_redirects: bool,
    ) -> TransportResponse:
        if follow_redirects:
            raise ValueError("PROVIDER_REDIRECT_POLICY_INVALID")
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request_headers = dict(headers)
        if encoded is not None:
            request_headers["content-type"] = "application/json"
        request = urllib.request.Request(url, data=encoded, headers=request_headers, method=method)
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                status_code = int(response.status)
        except urllib.error.HTTPError as error:
            return TransportResponse(int(error.code), {})
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise ValueError("PROVIDER_RESPONSE_TOO_LARGE")
        payload = json.loads(raw.decode("utf-8")) if raw else {}
        return TransportResponse(status_code, payload)


def _credential_text(credential: str | bytes | None) -> str:
    if isinstance(credential, bytes):
        try:
            value = credential.decode("utf-8")
        except UnicodeDecodeError:
            raise AdapterError("PROVIDER_CREDENTIAL_INVALID") from None
    elif isinstance(credential, str):
        value = credential
    else:
        value = ""
    if not value or value != value.strip():
        raise AdapterError("PROVIDER_CREDENTIAL_REQUIRED", 409)
    return value


def _append_path(base_url: str, path: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/v1") and path.startswith("/v1/"):
        return base + path[3:]
    return base + path


class _BaseAdapter:
    provider_code: str
    routing_owner = "provider"
    daon_fallback_allowed = True

    def __init__(self, transport: AdapterTransport) -> None:
        self._transport = transport

    def verify_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes,
        model_ids: Sequence[str],
    ) -> tuple[DiscoveredModel, ...]:
        raise AdapterError("PROVIDER_ADAPTER_UNSUPPORTED")

    def _validate_connection(self, connection: ProviderConnection) -> str:
        if connection.provider_code != self.provider_code or not connection.enabled:
            raise AdapterError("PROVIDER_CONNECTION_INVALID")
        try:
            return validate_provider_base_url(connection.provider_code, connection.base_url)
        except ProviderSettingsError:
            raise AdapterError("PROVIDER_BASE_URL_INVALID") from None

    def _request(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object] | None = None,
    ) -> object:
        try:
            response = self._transport.request(
                method,
                url,
                headers,
                body,
                _TIMEOUT_SECONDS,
                follow_redirects=False,
            )
        except AdapterError:
            raise
        except Exception:
            raise AdapterError(
                "PROVIDER_CATALOG_UNAVAILABLE",
                503,
                retryable=True,
            ) from None
        status = response.status_code
        if 300 <= status < 400:
            raise AdapterError("PROVIDER_REDIRECT_BLOCKED", 503)
        if status in {401, 403}:
            raise AdapterError("PROVIDER_AUTHENTICATION_FAILED", 401)
        if status == 429:
            raise AdapterError("PROVIDER_RATE_LIMITED", 503, retryable=True)
        if status >= 500:
            raise AdapterError("PROVIDER_CATALOG_UNAVAILABLE", 503, retryable=True)
        if status < 200 or status >= 300:
            raise AdapterError("PROVIDER_CONNECTION_FAILED", 503)
        return response.payload

    def _ready(self, connection: ProviderConnection) -> VerificationResult:
        checked_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        return VerificationResult(
            connection_id=connection.connection_id,
            provider_code=connection.provider_code,
            status="ready",
            checked_at=checked_at,
            routing_owner=self.routing_owner,
            daon_fallback_allowed=self.daon_fallback_allowed,
        )


class OllamaAdapter(_BaseAdapter):
    provider_code = "OLLAMA"

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]:
        base = self._validate_connection(connection)
        payload = self._request("GET", _append_path(base, "/api/tags"), {})
        try:
            return ProviderCatalog.from_payload(connection.connection_id, self.provider_code, payload)
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 503) from None

    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult:
        self.discover_models(connection, credential)
        return self._ready(connection)


class OpenRouterAdapter(_BaseAdapter):
    provider_code = "OPENROUTER"

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]:
        base = self._validate_connection(connection)
        secret = _credential_text(credential)
        payload = self._request(
            "GET",
            _append_path(base, "/models"),
            {"authorization": f"Bearer {secret}"},
        )
        try:
            return ProviderCatalog.from_payload(connection.connection_id, self.provider_code, payload)
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 503) from None

    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult:
        self.discover_models(connection, credential)
        return self._ready(connection)


class _CustomCompatibleAdapter(OpenRouterAdapter):
    provider_code = "CUSTOM"

    def _headers(self, secret: str | None) -> Mapping[str, str]:
        raise NotImplementedError

    def _probe_path(self) -> str:
        raise NotImplementedError

    def _has_text(self, payload: object) -> bool:
        raise NotImplementedError

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]:
        base = self._validate_connection(connection)
        secret = _credential_text(credential) if credential is not None else None
        payload = self._request("GET", _append_path(base, "/models"), self._headers(secret))
        try:
            return ProviderCatalog.from_payload(connection.connection_id, self.provider_code, payload)
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 503) from None

    def verify_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
        model_ids: Sequence[str],
    ) -> tuple[DiscoveredModel, ...]:
        base = self._validate_connection(connection)
        try:
            models = ProviderCatalog.from_verified_text_models(
                connection.connection_id, self.provider_code, model_ids,
            )
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 409) from None
        headers = self._headers(_credential_text(credential) if credential is not None else None)
        url = _append_path(base, self._probe_path())
        for model in models:
            payload = self._request(
                "POST", url, headers,
                {
                    "model": model.model_id,
                    "messages": [{"role": "user", "content": "connection test"}],
                    "max_tokens": 16,
                    "stream": False,
                },
            )
            if not self._has_text(payload):
                raise AdapterError("PROVIDER_PROBE_RESPONSE_INVALID", 503)
        return models


class CustomOpenAICompatibleAdapter(_CustomCompatibleAdapter):
    def _headers(self, secret: str | None) -> Mapping[str, str]:
        return {"authorization": f"Bearer {secret}"} if secret is not None else {}

    def _probe_path(self) -> str:
        return "/chat/completions"

    def _has_text(self, payload: object) -> bool:
        if not isinstance(payload, Mapping):
            return False
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            return False
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            return False
        content = message.get("content")
        return isinstance(content, str) and bool(content.strip())


class CustomAnthropicCompatibleAdapter(_CustomCompatibleAdapter):
    def _headers(self, secret: str | None) -> Mapping[str, str]:
        return {"x-api-key": secret, "anthropic-version": "2023-06-01"} if secret is not None else {"anthropic-version": "2023-06-01"}

    def _probe_path(self) -> str:
        return "/messages"

    def _has_text(self, payload: object) -> bool:
        if not isinstance(payload, Mapping):
            return False
        content = payload.get("content")
        return isinstance(content, list) and any(
            isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
            and bool(block["text"].strip())
            for block in content
        )


class FixedOpenAICompatibleAdapter(OpenRouterAdapter):
    _OFFICIAL_BASE_URLS = {
        "GROQ": "https://api.groq.com/openai/v1",
        "MISTRAL": "https://api.mistral.ai/v1",
        "UPSTAGE": "https://api.upstage.ai/v1",
    }

    def _validate_connection(self, connection: ProviderConnection) -> str:
        base = super()._validate_connection(connection)
        if base != self._OFFICIAL_BASE_URLS[self.provider_code]:
            raise AdapterError("PROVIDER_BASE_URL_INVALID")
        return base


class GroqAdapter(FixedOpenAICompatibleAdapter):
    provider_code = "GROQ"


class MistralAdapter(FixedOpenAICompatibleAdapter):
    provider_code = "MISTRAL"


class UpstageAdapter(FixedOpenAICompatibleAdapter):
    provider_code = "UPSTAGE"


class _RoutingGatewayAdapter(_BaseAdapter):
    routing_owner = "gateway"
    daon_fallback_allowed = False

    def __init__(
        self,
        transport: AdapterTransport,
        logical_models: Mapping[str, Sequence[str]],
    ) -> None:
        super().__init__(transport)
        self._logical_models = logical_models

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]:
        self._validate_connection(connection)
        try:
            return ProviderCatalog.from_logical_models(
                connection.connection_id,
                self.provider_code,
                self._logical_models.get(connection.connection_id, ()),
            )
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 409) from None

    def _probe(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
        path: str,
        body: Mapping[str, object],
    ) -> VerificationResult:
        base = self._validate_connection(connection)
        secret = _credential_text(credential)
        self._request(
            "POST",
            _append_path(base, path),
            {"authorization": f"Bearer {secret}"},
            body,
        )
        return self._ready(connection)


class OmniRouteAdapter(_RoutingGatewayAdapter):
    provider_code = "OMNIROUTE"

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]:
        models, _rejected_ids = self.discover_models_with_rejected_ids(connection, credential)
        return models

    def discover_models_with_rejected_ids(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[tuple[DiscoveredModel, ...], tuple[str, ...]]:
        base = self._validate_connection(connection)
        secret = _credential_text(credential)
        payload = self._request(
            "GET", _append_path(base, "/v1/models"),
            {"authorization": f"Bearer {secret}"},
        )
        try:
            rows = ProviderCatalog.usable_omniroute_rows(payload, secret)
            listed = ProviderCatalog.from_payload(connection.connection_id, "CUSTOM", {"data": rows})
            specialty_ids = {
                row["id"] for row in rows
                if row.get("type") not in (None, "chat", "combo")
                or row.get("subtype") not in (None, "")
            }
            # OpenAI-compatible catalogs often omit type, so untyped rows stay
            # visible. Discovery never grants allowlist access: every selected
            # model must pass a text-producing /v1/responses probe before use.
            chat_ids = tuple(model.model_id for model in listed if model.model_id not in specialty_ids)
            models = (
                tuple(replace(model, catalog_origin="upstream") for model in ProviderCatalog.from_logical_models(
                    connection.connection_id, self.provider_code, chat_ids,
                )) if chat_ids else ()
            )
            return models, tuple(sorted(specialty_ids))
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 503) from None

    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult:
        base = self._validate_connection(connection)
        secret = _credential_text(credential)
        try:
            configured = self._logical_models.get(connection.connection_id, ())
            # New create/update requests enforce the four-model cap before
            # reaching this adapter. Stored legacy allowlists may be larger or
            # contain auto beside explicit IDs; every runnable ID must be
            # probed, including auto, during Key replacement and health checks.
            models = (
                ProviderCatalog.from_logical_models(connection.connection_id, self.provider_code, configured)
                if configured else ()
            )
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 409) from None
        for model in models or (None,):
            body: dict[str, object] = {"input": "Reply OK.", "max_output_tokens": 16, "stream": False}
            if model is not None:
                body["model"] = model.model_id
            payload = self._request(
                "POST", _append_path(base, "/v1/responses"),
                {"authorization": f"Bearer {secret}"},
                body,
            )
            if (not isinstance(payload, Mapping)
                    or not isinstance(payload.get("output_text"), str)
                    or not payload["output_text"].strip()):
                raise AdapterError("PROVIDER_PROBE_RESPONSE_INVALID", 503)
        return self._ready(connection)


class EoulGatewayAdapter(_RoutingGatewayAdapter):
    provider_code = "EOUL_GATEWAY"

    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult:
        models = self.discover_models(connection, credential)
        if not models:
            raise AdapterError("PROVIDER_LOGICAL_MODEL_INVALID", 409)
        return self._probe(
            connection,
            credential,
            "/v1/chat/completions",
            {
                "model": models[0].model_id,
                "messages": [{"role": "user", "content": "health-check"}],
                "max_tokens": 1,
                "stream": False,
            },
        )


class MediaBridgeAdapter(OpenRouterAdapter):
    """Read Media Bridge's OpenAI-compatible model catalog."""

    provider_code = "MEDIA_BRIDGE"

    def discover_models(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> tuple[DiscoveredModel, ...]:
        base = self._validate_connection(connection)
        headers = {} if credential is None else {
            "authorization": f"Bearer {_credential_text(credential)}"
        }
        payload = self._request("GET", _append_path(base, "/models"), headers)
        try:
            return ProviderCatalog.from_payload(connection.connection_id, self.provider_code, payload)
        except ProviderCatalogError as error:
            raise AdapterError(error.args[0], 503) from None

    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult:
        self.discover_models(connection, credential)
        return self._ready(connection)


class SentenceTransformersAdapter(_RoutingGatewayAdapter):
    """Local embedding runtime with an explicitly configured logical model list."""

    provider_code = "SENTENCE_TRANSFORMERS"
    routing_owner = "local_runtime"
    daon_fallback_allowed = False

    def verify(
        self,
        connection: ProviderConnection,
        credential: str | bytes | None,
    ) -> VerificationResult:
        self.discover_models(connection, credential)
        return self._ready(connection)


class AdapterRegistry:
    def __init__(
        self,
        transport: AdapterTransport | None = None,
        *,
        logical_models: Mapping[str, Sequence[str]] | None = None,
    ) -> None:
        actual_transport = transport or UrllibAdapterTransport()
        configured_models = logical_models or {}
        self._adapters: dict[str, ConnectionAdapter] = {
            "GROQ": GroqAdapter(actual_transport),
            "MISTRAL": MistralAdapter(actual_transport),
            "UPSTAGE": UpstageAdapter(actual_transport),
            "OLLAMA": OllamaAdapter(actual_transport),
            "OPENROUTER": OpenRouterAdapter(actual_transport),
            "CUSTOM": CustomOpenAICompatibleAdapter(actual_transport),
            "OMNIROUTE": OmniRouteAdapter(actual_transport, configured_models),
            "EOUL_GATEWAY": EoulGatewayAdapter(actual_transport, configured_models),
            "MEDIA_BRIDGE": MediaBridgeAdapter(actual_transport),
            "SENTENCE_TRANSFORMERS": SentenceTransformersAdapter(actual_transport, configured_models),
        }
        self._custom_anthropic = CustomAnthropicCompatibleAdapter(actual_transport)

    def adapter(self, provider_code: str, adapter_type: str = "") -> ConnectionAdapter:
        if provider_code == "CUSTOM":
            if adapter_type == "anthropic_compatible":
                return self._custom_anthropic
            if adapter_type not in {"", "CUSTOM", "openai_compatible"}:
                raise AdapterError("PROVIDER_ADAPTER_UNSUPPORTED")
        try:
            return self._adapters[provider_code]
        except KeyError:
            raise AdapterError("PROVIDER_ADAPTER_UNSUPPORTED") from None
