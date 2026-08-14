"""Provider-independent grounded question answering over retrieved evidence."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping, Protocol, cast
from urllib.parse import urlsplit

from .document_index_postgres import IndexedEvidenceChunk
from .document_understanding_adapter import _evidence_anchors
from .provider_settings import ProviderSettingsSnapshot


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")


@dataclass(frozen=True, slots=True)
class TextModelSelection:
    provider_code: str
    base_url: str
    profile_id: str
    deployment_id: str
    model_id: str
    binding_version: int
    provider_kind: str = "external_api"


@dataclass(frozen=True, slots=True)
class GroundedQuestionRequest:
    question: str
    evidence: tuple[IndexedEvidenceChunk, ...]
    trace_id: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.question, str) or not self.question.strip()
            or len(self.question) > 2_000 or not _SAFE_ID.fullmatch(self.trace_id)
            or not self.evidence or len(self.evidence) > 10
        ):
            raise ValueError("QUESTION_REQUEST_INVALID")


@dataclass(frozen=True, slots=True)
class GroundedTextResult:
    answer: str
    cited_chunk_ids: tuple[str, ...]
    insufficient: bool
    usage: Mapping[str, int]


class TextGenerationTransport(Protocol):
    def post_json(
        self, *, url: str, api_key: str, payload: dict[str, object],
        timeout_seconds: float,
    ) -> dict[str, object]: ...
    def post_json_no_auth(
        self, *, url: str, payload: dict[str, object], timeout_seconds: float,
    ) -> dict[str, object]: ...


def resolve_text_model_selection(snapshot: ProviderSettingsSnapshot) -> TextModelSelection:
    deployment_id = snapshot.role_bindings.get("text")
    if not deployment_id:
        raise ValueError("TEXT_MODEL_NOT_SELECTED")
    deployment = next(
        (item for item in snapshot.deployments if item.deployment_id == deployment_id), None,
    )
    if deployment is None or not deployment.active or "text" not in deployment.roles:
        raise ValueError("TEXT_MODEL_UNAVAILABLE")
    profile = next(
        (item for item in snapshot.profiles if item.profile_id == deployment.profile_id), None,
    )
    if profile is None or not profile.active or not profile.credential_configured:
        raise ValueError("TEXT_PROVIDER_UNAVAILABLE")
    return TextModelSelection(
        deployment.provider_code, profile.base_url, profile.profile_id,
        deployment.deployment_id, deployment.model_id, snapshot.binding_version,
        profile.provider_kind,
    )


class OpenAICompatibleTextGenerationAdapter:
    """Grounded JSON adapter for approved OpenAI-compatible external providers."""

    _BASE_URLS = {
        "GROQ": "https://api.groq.com/openai/v1",
        "MISTRAL": "https://api.mistral.ai/v1",
        "UPSTAGE": "https://api.upstage.ai/v1",
    }
    _SCHEMA: dict[str, object] = {
        "type": "json_schema",
        "json_schema": {
            "name": "daon_grounded_answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "answer": {"type": "string"},
                    "cited_chunk_ids": {"type": "array", "items": {"type": "string"}},
                    "insufficient": {"type": "boolean"},
                },
                "required": ["answer", "cited_chunk_ids", "insufficient"],
                "additionalProperties": False,
            },
        },
    }

    def __init__(
        self, *, transport: TextGenerationTransport, api_key: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key or not 1 <= timeout_seconds <= 120:
            raise ValueError("TEXT_ADAPTER_CONFIG_INVALID")
        self._transport = transport
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        return f"{type(self).__name__}(credential_configured=True, timeout_seconds={self._timeout_seconds})"

    @staticmethod
    def _base_url(selection: TextModelSelection) -> str:
        parsed = urlsplit(selection.base_url)
        if (
            selection.provider_code not in OpenAICompatibleTextGenerationAdapter._BASE_URLS
            or selection.base_url.rstrip("/")
            != OpenAICompatibleTextGenerationAdapter._BASE_URLS.get(selection.provider_code)
            or parsed.scheme != "https" or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
        ):
            raise ValueError("TEXT_PROVIDER_ENDPOINT_INVALID")
        return selection.base_url.rstrip("/")

    @classmethod
    def provider_payload(cls, request: GroundedQuestionRequest, selection: TextModelSelection) -> dict[str, object]:
        evidence = [
            {"chunk_id": item.chunk_id, "page": item.page, "text": item.text}
            for item in request.evidence
        ]
        return {
                "model": selection.model_id,
                "messages": [
                    {"role": "system", "content": (
                        "Answer only from the supplied evidence JSON. Return insufficient=true "
                        "when it does not support an answer. Cite only supplied chunk_id values."
                    )},
                    {"role": "user", "content": json.dumps(
                        {"question": request.question, "evidence": evidence},
                        ensure_ascii=False, separators=(",", ":"),
                    )},
                ],
                "response_format": cls._SCHEMA,
            }

    def generate(
        self, request: GroundedQuestionRequest, selection: TextModelSelection,
        *, provider_payload: dict[str, object] | None = None,
    ) -> GroundedTextResult:
        response = self._transport.post_json(
            url=f"{self._base_url(selection)}/chat/completions",
            api_key=self._api_key,
            payload=provider_payload or self.provider_payload(request, selection),
            timeout_seconds=self._timeout_seconds,
        )
        try:
            choices = cast(list[object], response["choices"])
            message = cast(dict[str, object], cast(dict[str, object], choices[0])["message"])
            raw = message["content"]
            value = json.loads(raw) if isinstance(raw, str) else raw
            parsed = cast(dict[str, object], value)
            answer = str(parsed["answer"]).strip()
            cited = tuple(str(item) for item in cast(list[object], parsed["cited_chunk_ids"]))
            insufficient = bool(parsed["insufficient"])
            raw_usage = cast(dict[str, object], response.get("usage", {}))
            usage = {key: int(value) for key, value in raw_usage.items() if isinstance(value, int)}
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID") from None
        allowed = {item.chunk_id for item in request.evidence}
        cited_text = "\n".join(
            item.text for item in request.evidence if item.chunk_id in set(cited)
        )
        material_anchors_supported = _evidence_anchors(answer).issubset(
            _evidence_anchors(cited_text)
        )
        if (
            not answer or len(answer) > 8_000 or len(cited) != len(set(cited))
            or not set(cited).issubset(allowed)
            or (insufficient and cited) or (not insufficient and not cited)
            or not material_anchors_supported
        ):
            raise ValueError("TEXT_GENERATION_GROUNDING_INVALID")
        return GroundedTextResult(answer, cited, insufficient, usage)


class UpstageTextGenerationAdapter(OpenAICompatibleTextGenerationAdapter):
    """Backward-compatible name for the approved Upstage adapter contract."""


class OllamaTextGenerationAdapter:
    """Server-only Ollama chat adapter using the selected model and grounded JSON."""

    def __init__(self, *, transport: TextGenerationTransport, timeout_seconds: float = 90.0) -> None:
        if not 1 <= timeout_seconds <= 120:
            raise ValueError("TEXT_ADAPTER_CONFIG_INVALID")
        self._transport = transport
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _base_url(selection: TextModelSelection) -> str:
        parsed = urlsplit(selection.base_url)
        if (
            selection.provider_code != "OLLAMA" or parsed.scheme not in {"http", "https"}
            or not parsed.hostname or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
        ):
            raise ValueError("OLLAMA_ENDPOINT_INVALID")
        return selection.base_url.rstrip("/")

    @staticmethod
    def provider_payload(request: GroundedQuestionRequest, selection: TextModelSelection) -> dict[str, object]:
        evidence = [
            {"chunk_id": item.chunk_id, "page": item.page, "text": item.text}
            for item in request.evidence
        ]
        return {
                "model": selection.model_id,
                "stream": False,
                "keep_alive": "5m",
                "options": {"num_predict": 64, "temperature": 0},
                "format": UpstageTextGenerationAdapter._SCHEMA["json_schema"]["schema"],
                "messages": [
                    {"role": "system", "content": (
                        "Answer only from evidence JSON. Return answer, cited_chunk_ids and insufficient."
                    )},
                    {"role": "user", "content": json.dumps(
                        {"question": request.question, "evidence": evidence},
                        ensure_ascii=False, separators=(",", ":"),
                    )},
                ],
            }

    def generate(
        self, request: GroundedQuestionRequest, selection: TextModelSelection,
        *, provider_payload: dict[str, object] | None = None,
    ) -> GroundedTextResult:
        response = self._transport.post_json_no_auth(
            url=f"{self._base_url(selection)}/api/chat",
            payload=provider_payload or self.provider_payload(request, selection),
            timeout_seconds=self._timeout_seconds,
        )
        try:
            message = cast(dict[str, object], response["message"])
            raw = message["content"]
            parsed = cast(dict[str, object], json.loads(raw) if isinstance(raw, str) else raw)
            answer = str(parsed["answer"]).strip()
            cited = tuple(str(item) for item in cast(list[object], parsed["cited_chunk_ids"]))
            insufficient = bool(parsed["insufficient"])
            usage = {
                "prompt_tokens": int(response.get("prompt_eval_count", 0)),
                "completion_tokens": int(response.get("eval_count", 0)),
            }
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID") from None
        allowed = {item.chunk_id for item in request.evidence}
        cited_text = "\n".join(
            item.text for item in request.evidence if item.chunk_id in set(cited)
        )
        if (
            not answer or len(answer) > 8_000 or len(cited) != len(set(cited))
            or not set(cited).issubset(allowed) or (insufficient and cited)
            or (not insufficient and not cited)
            or not _evidence_anchors(answer).issubset(_evidence_anchors(cited_text))
        ):
            raise ValueError("TEXT_GENERATION_GROUNDING_INVALID")
        return GroundedTextResult(answer, cited, insufficient, usage)
