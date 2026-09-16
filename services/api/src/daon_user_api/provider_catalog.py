from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence


RoutingOwner = Literal["provider", "gateway"]


class ProviderCatalogError(ValueError):
    """Safe catalog failure that never includes an upstream payload."""


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    connection_id: str
    provider_code: str
    model_id: str
    reported_capabilities: tuple[str, ...]
    routing_owner: RoutingOwner
    daon_fallback_allowed: bool


def _valid_model_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or not value
        or len(value) > 256
        or any(character.isspace() or ord(character) < 32 for character in value)
    ):
        raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
    return value


def _contains_sensitive_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower().replace("-", "_")
            if (
                "secret" in normalized
                or "credential" in normalized
                or "password" in normalized
                or normalized in {"api_key", "apikey", "authorization", "client_key", "cookie"}
                or normalized.endswith("_token")
            ):
                return True
            if _contains_sensitive_key(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


class ProviderCatalog:
    @staticmethod
    def from_payload(
        connection_id: str,
        provider_code: str,
        payload: object,
    ) -> tuple[DiscoveredModel, ...]:
        if not isinstance(payload, Mapping) or _contains_sensitive_key(payload):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        if provider_code == "OLLAMA":
            return ProviderCatalog._from_ollama(connection_id, payload)
        if provider_code == "OPENROUTER":
            return ProviderCatalog._from_openrouter(connection_id, payload)
        if provider_code in {"GROQ", "MISTRAL", "UPSTAGE"}:
            return ProviderCatalog._from_openai_compatible(
                connection_id, provider_code, payload,
            )
        raise ProviderCatalogError("PROVIDER_CATALOG_UNSUPPORTED")

    @staticmethod
    def from_logical_models(
        connection_id: str,
        provider_code: str,
        model_ids: Sequence[str],
    ) -> tuple[DiscoveredModel, ...]:
        if provider_code not in {"OMNIROUTE", "EOUL_GATEWAY"} or not model_ids:
            raise ProviderCatalogError("PROVIDER_LOGICAL_MODEL_INVALID")
        try:
            normalized = tuple(_valid_model_id(model_id) for model_id in model_ids)
        except ProviderCatalogError:
            raise ProviderCatalogError("PROVIDER_LOGICAL_MODEL_INVALID") from None
        if len(set(normalized)) != len(normalized):
            raise ProviderCatalogError("PROVIDER_LOGICAL_MODEL_INVALID")
        return tuple(
            DiscoveredModel(
                connection_id=connection_id,
                provider_code=provider_code,
                model_id=model_id,
                reported_capabilities=("text_generation",),
                routing_owner="gateway",
                daon_fallback_allowed=False,
            )
            for model_id in normalized
        )

    @staticmethod
    def _from_ollama(
        connection_id: str,
        payload: Mapping[object, object],
    ) -> tuple[DiscoveredModel, ...]:
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        model_ids: list[str] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            model_ids.append(_valid_model_id(raw_model.get("name") or raw_model.get("model")))
        return tuple(
            DiscoveredModel(
                connection_id=connection_id,
                provider_code="OLLAMA",
                model_id=model_id,
                reported_capabilities=("text_generation",),
                routing_owner="provider",
                daon_fallback_allowed=True,
            )
            for model_id in sorted(set(model_ids))
        )

    @staticmethod
    def _from_openrouter(
        connection_id: str,
        payload: Mapping[object, object],
    ) -> tuple[DiscoveredModel, ...]:
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        discovered: dict[str, DiscoveredModel] = {}
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            model_id = _valid_model_id(raw_model.get("id"))
            architecture = raw_model.get("architecture", {})
            if not isinstance(architecture, Mapping):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            inputs = architecture.get("input_modalities", [])
            outputs = architecture.get("output_modalities", [])
            if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            if not isinstance(outputs, list) or not all(isinstance(item, str) for item in outputs):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            capabilities: set[str] = set()
            if "text" in outputs:
                capabilities.add("text_generation")
            if "image" in inputs:
                capabilities.add("image_understanding")
            if "file" in inputs:
                capabilities.add("document_parsing")
            if "audio" in inputs:
                capabilities.add("audio_understanding")
            if "video" in inputs:
                capabilities.add("video_understanding")
            discovered[model_id] = DiscoveredModel(
                connection_id=connection_id,
                provider_code="OPENROUTER",
                model_id=model_id,
                reported_capabilities=tuple(sorted(capabilities)),
                routing_owner="provider",
                daon_fallback_allowed=True,
            )
        return tuple(discovered[model_id] for model_id in sorted(discovered))

    @staticmethod
    def _from_openai_compatible(
        connection_id: str,
        provider_code: str,
        payload: Mapping[object, object],
    ) -> tuple[DiscoveredModel, ...]:
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        model_ids: list[str] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            model_ids.append(_valid_model_id(raw_model.get("id")))
        return tuple(
            DiscoveredModel(
                connection_id=connection_id,
                provider_code=provider_code,
                model_id=model_id,
                reported_capabilities=("text_generation",),
                routing_owner="provider",
                daon_fallback_allowed=True,
            )
            for model_id in sorted(set(model_ids))
        )
