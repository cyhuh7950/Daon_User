from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Callable, Protocol, cast
from urllib.parse import urlsplit

import httpx

from .knowledge_context import (
    KnowledgeContextSnapshot,
    OfflineStudioError,
    canonical_json_bytes,
    context_item_lineage,
)


_MAX_CATALOG_BYTES = 1 * 1024 * 1024
_MAX_PROVIDER_BYTES = 2 * 1024 * 1024
_SAFE_TEXT_TOKEN = re.compile(r"[0-9A-Za-z가-힣]{4,}")

DRAFT_OUTPUT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "integer", "const": 1},
        "sections": {
            "type": "array",
            "minItems": 1,
            "maxItems": 50,
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "body": {"type": "string", "minLength": 1, "maxLength": 1048576},
                    "citation_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "unverified": {"type": "boolean"},
                },
                "required": ["title", "body", "citation_ids", "unverified"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["schema_version", "sections"],
    "additionalProperties": False,
}
DRAFT_OUTPUT_SCHEMA_DIGEST = hashlib.sha256(
    canonical_json_bytes(DRAFT_OUTPUT_SCHEMA)
).hexdigest()


@dataclass(frozen=True, slots=True)
class ProviderModelDescriptor:
    provider_code: str
    provider_kind: str
    profile_id: str
    deployment_id: str
    model_id: str
    model_digest: str
    deployment_digest: str
    binding_version: int
    policy_version: str = "offline-provider-policy-v1"

    @property
    def model_version(self) -> str:
        """Safe compatibility label for the existing Local models projection."""
        return self.model_digest


@dataclass(frozen=True, slots=True)
class ModelSelectionSnapshot:
    provider_code: str
    provider_kind: str
    profile_id: str
    deployment_id: str
    model_id: str
    model_digest: str
    binding_version: int
    deployment_digest: str
    temperature: float
    max_output_tokens: int
    output_schema_digest: str
    selection_actor_id: str = "local-session"
    selected_at: str = "1970-01-01T00:00:00Z"
    policy_version: str = "offline-provider-policy-v1"


class ModelCatalogPort(Protocol):
    def list_eligible(
        self, *, workspace_id: str
    ) -> tuple[ProviderModelDescriptor, ...]: ...

    def select(
        self,
        *,
        workspace_id: str,
        deployment_id: str,
        temperature: float = 0.1,
        max_output_tokens: int = 1024,
        selection_actor_id: str = "local-session",
        selected_at: str = "1970-01-01T00:00:00Z",
    ) -> ModelSelectionSnapshot: ...


class DraftGenerationPort(Protocol):
    def generate(
        self,
        *,
        selection: ModelSelectionSnapshot,
        context: KnowledgeContextSnapshot,
        request: dict[str, object],
        timeout_seconds: float = 90.0,
    ) -> dict[str, object]: ...


class ProviderJsonTransport(Protocol):
    def get_json(
        self, *, url: str, timeout_seconds: float, max_response_bytes: int
    ) -> dict[str, object]: ...

    def post_json(
        self,
        *,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
        api_key: str | None = None,
    ) -> dict[str, object]: ...


class HttpxProviderJsonTransport:
    """Bounded server-side JSON transport that never exposes response bodies in errors."""

    @staticmethod
    def _request_json(
        method: str,
        *,
        url: str,
        timeout_seconds: float,
        max_response_bytes: int,
        payload: dict[str, object] | None = None,
        api_key: str | None = None,
    ) -> dict[str, object]:
        if not 0 < timeout_seconds <= 120 or not 1 <= max_response_bytes <= _MAX_PROVIDER_BYTES:
            raise OfflineStudioError("PROVIDER_TRANSPORT_INVALID")
        headers = {"Accept": "application/json"}
        if api_key is not None:
            if not api_key or "\r" in api_key or "\n" in api_key:
                raise OfflineStudioError("PROVIDER_CREDENTIAL_UNAVAILABLE")
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            with httpx.Client(timeout=timeout_seconds, follow_redirects=False) as client:
                with client.stream(method, url, headers=headers, json=payload) as response:
                    if response.status_code < 200 or response.status_code >= 300:
                        raise OfflineStudioError("PROVIDER_UNAVAILABLE")
                    length = response.headers.get("content-length")
                    if length is not None and int(length) > max_response_bytes:
                        raise OfflineStudioError("PROVIDER_RESPONSE_TOO_LARGE")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > max_response_bytes:
                            raise OfflineStudioError("PROVIDER_RESPONSE_TOO_LARGE")
        except OfflineStudioError:
            raise
        except (httpx.HTTPError, OSError, ValueError):
            raise OfflineStudioError("PROVIDER_UNAVAILABLE") from None
        try:
            decoded = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OfflineStudioError("PROVIDER_RESPONSE_INVALID") from None
        if not isinstance(decoded, dict):
            raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
        return cast(dict[str, object], decoded)

    def get_json(
        self, *, url: str, timeout_seconds: float, max_response_bytes: int
    ) -> dict[str, object]:
        return self._request_json(
            "GET", url=url, timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes,
        )

    def post_json(
        self,
        *,
        url: str,
        payload: dict[str, object],
        timeout_seconds: float,
        max_response_bytes: int,
        api_key: str | None = None,
    ) -> dict[str, object]:
        return self._request_json(
            "POST", url=url, timeout_seconds=timeout_seconds,
            max_response_bytes=max_response_bytes, payload=payload, api_key=api_key,
        )


def _ollama_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise OfflineStudioError("OLLAMA_ENDPOINT_INVALID")
    return value.rstrip("/")


def _external_base_url(provider_code: str, value: str) -> str:
    parsed = urlsplit(value)
    exact = {
        "GROQ": ("api.groq.com", "/openai/v1"),
        "UPSTAGE": ("api.upstage.ai", "/v1"),
    }.get(provider_code)
    if (
        exact is None
        or parsed.scheme != "https"
        or parsed.hostname != exact[0]
        or parsed.path.rstrip("/") != exact[1]
        or parsed.port is not None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise OfflineStudioError(f"{provider_code}_ENDPOINT_INVALID")
    return value.rstrip("/")


class OllamaModelCatalog:
    def __init__(
        self,
        *,
        base_url: str,
        transport: ProviderJsonTransport,
        descriptors: dict[str, ProviderModelDescriptor],
        descriptor_resolver: Callable[
            [str, dict[str, str]], dict[str, ProviderModelDescriptor]
        ] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = _ollama_base_url(base_url)
        self._transport = transport
        self._descriptors = dict(descriptors)
        self._descriptor_resolver = descriptor_resolver
        self._timeout_seconds = timeout_seconds
        if not 0 < timeout_seconds <= 30:
            raise OfflineStudioError("PROVIDER_TRANSPORT_INVALID")
        if any(
            key != value.deployment_id
            or value.provider_code != "OLLAMA"
            or value.provider_kind != "server_internal"
            or value.binding_version < 1
            for key, value in self._descriptors.items()
        ):
            raise OfflineStudioError("MODEL_DEPLOYMENT_INVALID")

    def _installed(self) -> dict[str, str]:
        response = self._transport.get_json(
            url=f"{self._base_url}/api/tags",
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=_MAX_CATALOG_BYTES,
        )
        models = response.get("models")
        if not isinstance(models, list) or len(models) > 512:
            raise OfflineStudioError("LOCAL_MODEL_CATALOG_INVALID")
        installed: dict[str, str] = {}
        for value in models:
            if not isinstance(value, dict):
                raise OfflineStudioError("LOCAL_MODEL_CATALOG_INVALID")
            name, digest = value.get("name"), value.get("digest")
            if not isinstance(name, str) or not name or not isinstance(digest, str) or not digest:
                raise OfflineStudioError("LOCAL_MODEL_CATALOG_INVALID")
            if value.get("remote_host") or value.get("remote_model"):
                continue
            installed[name] = digest
        return installed

    def _has_completion(self, model_id: str) -> bool:
        response = self._transport.post_json(
            url=f"{self._base_url}/api/show",
            payload={"model": model_id},
            timeout_seconds=self._timeout_seconds,
            max_response_bytes=_MAX_CATALOG_BYTES,
        )
        capabilities = response.get("capabilities")
        return (
            isinstance(capabilities, list)
            and all(isinstance(value, str) for value in capabilities)
            and "completion" in capabilities
        )

    def list_eligible(
        self, *, workspace_id: str
    ) -> tuple[ProviderModelDescriptor, ...]:
        if not workspace_id:
            raise OfflineStudioError("WORKSPACE_INVALID")
        installed = self._installed()
        descriptors = dict(self._descriptors)
        if self._descriptor_resolver is not None:
            descriptors.update(self._descriptor_resolver(workspace_id, installed))
        if any(
            key != value.deployment_id
            or value.provider_code != "OLLAMA"
            or value.provider_kind != "server_internal"
            or value.binding_version < 1
            for key, value in descriptors.items()
        ):
            raise OfflineStudioError("MODEL_DEPLOYMENT_INVALID")
        eligible = []
        for descriptor in sorted(descriptors.values(), key=lambda value: value.deployment_id):
            if descriptor.model_id.lower().endswith(":cloud"):
                continue
            if installed.get(descriptor.model_id) != descriptor.model_digest:
                continue
            if not self._has_completion(descriptor.model_id):
                continue
            eligible.append(descriptor)
        return tuple(eligible)

    def select(
        self,
        *,
        workspace_id: str,
        deployment_id: str,
        temperature: float = 0.1,
        max_output_tokens: int = 1024,
        selection_actor_id: str = "local-session",
        selected_at: str = "1970-01-01T00:00:00Z",
    ) -> ModelSelectionSnapshot:
        if not 0 <= temperature <= 2 or not 1 <= max_output_tokens <= 32768:
            raise OfflineStudioError("GENERATION_SETTINGS_INVALID")
        descriptor = next(
            (
                value
                for value in self.list_eligible(workspace_id=workspace_id)
                if value.deployment_id == deployment_id
            ),
            None,
        )
        if descriptor is None:
            raise OfflineStudioError("LOCAL_MODEL_UNAVAILABLE")
        return ModelSelectionSnapshot(
            descriptor.provider_code,
            descriptor.provider_kind,
            descriptor.profile_id,
            descriptor.deployment_id,
            descriptor.model_id,
            descriptor.model_digest,
            descriptor.binding_version,
            descriptor.deployment_digest,
            temperature,
            max_output_tokens,
            DRAFT_OUTPUT_SCHEMA_DIGEST,
            selection_actor_id,
            selected_at,
            descriptor.policy_version,
        )


def _content_tokens(value: str) -> set[str]:
    return {token.casefold() for token in _SAFE_TEXT_TOKEN.findall(value)}


def validate_draft_output(
    value: object,
    *,
    context: KnowledgeContextSnapshot,
    request: dict[str, object],
) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"schema_version", "sections"}:
        raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
    sections = value.get("sections")
    if value.get("schema_version") != 1 or not isinstance(sections, list) or not 1 <= len(sections) <= 50:
        raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
    context_ids = {item.item_id for item in context.items}
    raw_evidence = request.get("evidence", [])
    if not isinstance(raw_evidence, list):
        raise OfflineStudioError("PROVIDER_REQUEST_INVALID")
    evidence: dict[str, str] = {}
    for item in raw_evidence:
        if (
            not isinstance(item, dict)
            or set(item) != {"item_id", "text"}
            or not isinstance(item.get("item_id"), str)
            or not isinstance(item.get("text"), str)
        ):
            raise OfflineStudioError("PROVIDER_REQUEST_INVALID")
        if (
            not str(item["text"]).strip()
            or str(item["item_id"]) in evidence
            or len(str(item["text"]).encode("utf-8")) > 2 * 1024 * 1024
        ):
            raise OfflineStudioError("PROVIDER_REQUEST_INVALID")
        evidence[str(item["item_id"])] = str(item["text"])
    if not evidence or set(evidence) != context_ids:
        raise OfflineStudioError("PROVIDER_GROUNDING_INVALID")
    validated: list[dict[str, object]] = []
    for section in sections:
        if not isinstance(section, dict) or set(section) != {
            "title", "body", "citation_ids", "unverified"
        }:
            raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
        title, body = section.get("title"), section.get("body")
        citations, unverified = section.get("citation_ids"), section.get("unverified")
        if (
            not isinstance(title, str)
            or not 1 <= len(title) <= 200
            or not isinstance(body, str)
            or not 1 <= len(body.encode("utf-8")) <= 1_048_576
            or not isinstance(citations, list)
            or any(not isinstance(item, str) for item in citations)
            or len(citations) != len(set(citations))
            or not isinstance(unverified, bool)
        ):
            raise OfflineStudioError("PROVIDER_RESPONSE_INVALID")
        citation_set = set(cast(list[str], citations))
        if not citation_set.issubset(context_ids) or (not citation_set and not unverified):
            raise OfflineStudioError("PROVIDER_GROUNDING_INVALID")
        if context.mode.value == "raw_only" and not unverified:
            raise OfflineStudioError("PROVIDER_GROUNDING_INVALID")
        if not citation_set.issubset(evidence):
            raise OfflineStudioError("PROVIDER_GROUNDING_INVALID")
        support = _content_tokens(" ".join(evidence[item] for item in citation_set))
        if citation_set and not (_content_tokens(body) & support):
            raise OfflineStudioError("PROVIDER_GROUNDING_INVALID")
        validated.append(
            {
                "title": title,
                "body": body,
                "citation_ids": list(cast(list[str], citations)),
                "unverified": unverified,
            }
        )
    return {"schema_version": 1, "sections": validated}


def _provider_request(
    *,
    selection: ModelSelectionSnapshot,
    context: KnowledgeContextSnapshot,
    request: dict[str, object],
) -> list[dict[str, str]]:
    evidence = request.get("evidence", [])
    if not isinstance(evidence, list):
        raise OfflineStudioError("PROVIDER_REQUEST_INVALID")
    supplied = {
        "purpose": request.get("purpose", ""),
        "context": {
            "snapshot_id": context.snapshot_id,
            "digest": context.digest,
            "mode": context.mode.value,
            "items": [context_item_lineage(item) for item in context.items],
        },
        "evidence": evidence,
    }
    return [
        {
            "role": "system",
            "content": (
                "Create a grounded business draft only from supplied evidence. "
                "Cite only supplied context item_id values and follow the JSON schema exactly."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(supplied, ensure_ascii=False, separators=(",", ":")),
        },
    ]


def _chat_content(response: dict[str, object], *, ollama: bool) -> object:
    try:
        if ollama:
            message = cast(dict[str, object], response["message"])
        else:
            choices = cast(list[object], response["choices"])
            message = cast(dict[str, object], cast(dict[str, object], choices[0])["message"])
        raw = message["content"]
        return json.loads(raw) if isinstance(raw, str) else raw
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        raise OfflineStudioError("PROVIDER_RESPONSE_INVALID") from None


class OllamaDraftGenerationAdapter:
    def __init__(
        self,
        *,
        base_url: str,
        transport: ProviderJsonTransport,
        catalog: OllamaModelCatalog,
    ) -> None:
        self._base_url = _ollama_base_url(base_url)
        self._transport = transport
        self._catalog = catalog

    def generate(
        self,
        *,
        selection: ModelSelectionSnapshot,
        context: KnowledgeContextSnapshot,
        request: dict[str, object],
        timeout_seconds: float = 90.0,
    ) -> dict[str, object]:
        if (
            selection.provider_code != "OLLAMA"
            or selection.provider_kind != "server_internal"
            or selection.model_id.lower().endswith(":cloud")
            or selection.output_schema_digest != DRAFT_OUTPUT_SCHEMA_DIGEST
        ):
            raise OfflineStudioError("LOCAL_MODEL_UNAVAILABLE")
        current = self._catalog.select(
            workspace_id=context.workspace_id,
            deployment_id=selection.deployment_id,
            temperature=selection.temperature,
            max_output_tokens=selection.max_output_tokens,
            selection_actor_id=selection.selection_actor_id,
            selected_at=selection.selected_at,
        )
        if current != selection:
            raise OfflineStudioError("MODEL_SELECTION_STALE")
        response = self._transport.post_json(
            url=f"{self._base_url}/api/chat",
            payload={
                "model": selection.model_id,
                "stream": False,
                "format": DRAFT_OUTPUT_SCHEMA,
                "options": {
                    "temperature": selection.temperature,
                    "num_predict": selection.max_output_tokens,
                },
                "messages": _provider_request(
                    selection=selection, context=context, request=request
                ),
            },
            timeout_seconds=timeout_seconds,
            max_response_bytes=_MAX_PROVIDER_BYTES,
        )
        return validate_draft_output(
            _chat_content(response, ollama=True), context=context, request=request
        )


class _ExternalDraftGenerationAdapter:
    provider_code = ""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        transport: ProviderJsonTransport,
    ) -> None:
        if not api_key or "\r" in api_key or "\n" in api_key:
            raise OfflineStudioError("PROVIDER_CREDENTIAL_UNAVAILABLE")
        self._base_url = _external_base_url(self.provider_code, base_url)
        self._api_key = api_key
        self._transport = transport

    def __repr__(self) -> str:
        return f"{type(self).__name__}(credential_configured=True)"

    def generate(
        self,
        *,
        selection: ModelSelectionSnapshot,
        context: KnowledgeContextSnapshot,
        request: dict[str, object],
        timeout_seconds: float = 90.0,
    ) -> dict[str, object]:
        if (
            selection.provider_code != self.provider_code
            or selection.provider_kind != "external_api"
            or selection.output_schema_digest != DRAFT_OUTPUT_SCHEMA_DIGEST
        ):
            raise OfflineStudioError("PROVIDER_SELECTION_INVALID")
        response = self._transport.post_json(
            url=f"{self._base_url}/chat/completions",
            payload={
                "model": selection.model_id,
                "temperature": selection.temperature,
                "max_tokens": selection.max_output_tokens,
                "messages": _provider_request(
                    selection=selection, context=context, request=request
                ),
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "daon_offline_studio_draft",
                        "strict": True,
                        "schema": DRAFT_OUTPUT_SCHEMA,
                    },
                },
            },
            timeout_seconds=timeout_seconds,
            max_response_bytes=_MAX_PROVIDER_BYTES,
            api_key=self._api_key,
        )
        return validate_draft_output(
            _chat_content(response, ollama=False), context=context, request=request
        )


class GroqDraftGenerationAdapter(_ExternalDraftGenerationAdapter):
    provider_code = "GROQ"


class UpstageDraftGenerationAdapter(_ExternalDraftGenerationAdapter):
    provider_code = "UPSTAGE"


# Compatibility names keep existing internal imports stable while the old executable
# implementation is removed. They do not restore an executable product boundary.
LocalDraftGeneratorPort = DraftGenerationPort
ManagedModelDescriptor = ProviderModelDescriptor
