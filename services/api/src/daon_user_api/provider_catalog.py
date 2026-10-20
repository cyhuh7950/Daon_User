from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Sequence


RoutingOwner = Literal["provider", "gateway", "local_runtime"]
MAX_COMPATIBLE_PROBE_MODELS = 4


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
    catalog_origin: Literal["upstream", "logical"] = "upstream"


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


def _ollama_catalog_projection(payload: Mapping[object, object]) -> Mapping[object, object]:
    """Remove only typed tokenizer switches at the exact show.model_info path.

    All other fields still pass through the unchanged recursive secret guard.
    Do not mutate the upstream payload or accept string/integer token values.
    """
    raw_models = payload.get("models")
    if not isinstance(raw_models, list):
        return payload
    options = {"tokenizer.ggml.add_eos_token", "tokenizer.ggml.add_bos_token",
               "tokenizer.ggml.add_padding_token", "tokenizer.ggml.add_unknown_token"}
    rows = []
    for raw in raw_models:
        row = dict(raw) if isinstance(raw, Mapping) else raw
        metadata = row.get("native_metadata") if isinstance(row, Mapping) else None
        info = metadata.get("model_info") if isinstance(metadata, Mapping) else None
        if isinstance(info, Mapping):
            row["native_metadata"] = {**metadata, "model_info": {
                key: value for key, value in info.items()
                if not (key in options and type(value) is bool)
            }}
        rows.append(row)
    return {**payload, "models": rows}


class ProviderCatalog:
    @staticmethod
    def from_native_asr_metadata(connection_id: str, payload: object) -> tuple[DiscoveredModel, ...]:
        """설치된 CPU base의 메타데이터만 허용하며 실제 전사 성공을 뜻하지 않는다."""
        expected = {
            "model_id": "faster-whisper-base",
            "model_digest": "d01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9",
            "revision": "ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66",
            "capability": "speech_to_text", "runtime": "cpu-int8-local",
        }
        if not isinstance(payload, Mapping) or payload != expected:
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        return (DiscoveredModel(connection_id, "CUSTOM", "faster-whisper-base",
                                ("speech_to_text",), "local_runtime", False),)

    @staticmethod
    def logical_auto_model(connection_id: str, provider_code: str, auto_model_id: str) -> DiscoveredModel:
        return DiscoveredModel(
            connection_id=connection_id, provider_code=provider_code,
            model_id=_valid_model_id(auto_model_id),
            reported_capabilities=("text_generation",),
            routing_owner="gateway" if provider_code == "OMNIROUTE" else "provider",
            daon_fallback_allowed=provider_code != "OMNIROUTE",
            catalog_origin="logical",
        )

    @staticmethod
    def usable_omniroute_rows(payload: object, credential: str) -> list[Mapping[object, object]]:
        """Keep selectable catalog IDs without weakening stored model-ID validation."""
        if not isinstance(payload, Mapping) or _contains_sensitive_key(payload):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        usable: list[Mapping[object, object]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            model_id = row.get("id")
            if isinstance(model_id, str) and credential in model_id:
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            try:
                _valid_model_id(model_id)
            except ProviderCatalogError:
                continue
            usable.append(row)
        return usable

    @staticmethod
    def from_verified_text_models(
        connection_id: str,
        provider_code: str,
        model_ids: Sequence[str],
    ) -> tuple[DiscoveredModel, ...]:
        if provider_code != "CUSTOM" or not model_ids or len(model_ids) > MAX_COMPATIBLE_PROBE_MODELS:
            raise ProviderCatalogError("PROVIDER_MODEL_IDS_INVALID")
        try:
            normalized = tuple(_valid_model_id(model_id) for model_id in model_ids)
        except ProviderCatalogError:
            raise ProviderCatalogError("PROVIDER_MODEL_IDS_INVALID") from None
        if len(set(normalized)) != len(normalized):
            raise ProviderCatalogError("PROVIDER_MODEL_IDS_INVALID")
        return tuple(
            DiscoveredModel(
                connection_id=connection_id,
                provider_code=provider_code,
                model_id=model_id,
                reported_capabilities=("text_generation",),
                routing_owner="provider",
                daon_fallback_allowed=True,
            )
            for model_id in normalized
        )

    @staticmethod
    def from_payload(
        connection_id: str,
        provider_code: str,
        payload: object,
    ) -> tuple[DiscoveredModel, ...]:
        if not isinstance(payload, Mapping):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        if provider_code == "OLLAMA":
            payload = _ollama_catalog_projection(payload)
        if _contains_sensitive_key(payload):
            raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
        if provider_code == "OLLAMA":
            return ProviderCatalog._from_ollama(connection_id, payload)
        if provider_code == "OPENROUTER":
            return ProviderCatalog._from_openrouter(connection_id, payload)
        if provider_code in {"GROQ", "MISTRAL", "UPSTAGE", "MEDIA_BRIDGE", "CUSTOM"}:
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
        if provider_code not in {"OMNIROUTE", "EOUL_GATEWAY", "SENTENCE_TRANSFORMERS"} or not model_ids:
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
                reported_capabilities=(
                    ("embedding",) if provider_code == "SENTENCE_TRANSFORMERS"
                    else ("text_generation",)
                ),
                routing_owner=(
                    "local_runtime" if provider_code == "SENTENCE_TRANSFORMERS" else "gateway"
                ),
                daon_fallback_allowed=False,
                catalog_origin="logical",
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
        vision_ids: set[str] = set()
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                raise ProviderCatalogError("PROVIDER_CATALOG_RESPONSE_INVALID")
            model_ids.append(_valid_model_id(raw_model.get("name") or raw_model.get("model")))
            metadata = raw_model.get("native_metadata")
            digest = raw_model.get("digest")
            if (isinstance(metadata, Mapping) and isinstance(metadata.get("capabilities"), list)
                    and "vision" in metadata["capabilities"] and isinstance(metadata.get("model_info"), Mapping)
                    and metadata["model_info"] and isinstance(digest, str)
                    and len(digest.removeprefix("sha256:")) == 64
                    and all(c in "0123456789abcdef" for c in digest.removeprefix("sha256:"))
                    and not any(raw_model.get(k) or metadata.get(k) or metadata["model_info"].get(k)
                                for k in ("remote_host", "remote_model", "remote", "cloud"))):
                vision_ids.add(model_ids[-1])
        return tuple(
            DiscoveredModel(
                connection_id=connection_id,
                provider_code="OLLAMA",
                model_id=model_id,
                reported_capabilities=("image_understanding", "text_generation") if model_id in vision_ids else ("text_generation",),
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
