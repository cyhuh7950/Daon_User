"""Application service for immutable multi-source grounded question runs."""

from __future__ import annotations

import json
import logging
import hashlib
import re
import time
import unicodedata
from dataclasses import dataclass, replace
from typing import ContextManager, Mapping, Protocol, cast

from .data_canon import canonical_json_bytes
from .document_processing import DocumentProcessingContext
from .document_index_postgres import IndexedEvidenceChunk
from .document_understanding_adapter import DocumentUnderstandingError, _evidence_anchors
from .question_answering import (
    AnthropicMessagesTextGenerationAdapter,
    GeneralConversationRequest, GroundedQuestionRequest, GroundedTextResult, TextGenerationTransport,
    OllamaTextGenerationAdapter, OpenAICompatibleTextGenerationAdapter,
    TextModelSelection,
    _append_provider_path,
)
from .question_answering_postgres import (
    PostgresQuestionAnsweringRepository, QuestionContext, QuestionRepositoryError,
    StoredQuestionAnswer, OpenLawQuestionAccessError,
)
from .workspace_model_defaults import (
    ResolvedModel, WorkspaceModelContext, WorkspaceModelUnavailable,
)


_GENERAL_CONVERSATION_INTENTS = frozenset({
    "안녕", "안녕하세요", "반가워", "반갑습니다", "고마워", "고마워요", "감사합니다",
    "도움말", "daon 사용법 알려줘", "daon 사용법을 알려줘", "다온 사용법 알려줘",
    "다온 사용법을 알려줘", "이 제품 사용법 알려줘", "이 제품 사용법을 알려줘",
})


def _normalized_intent(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    if normalized != value:
        return ""
    normalized = normalized.strip().casefold()
    normalized = re.sub(r"[.!?。！？]+$", "", normalized).strip()
    return re.sub(r"\s+", " ", normalized)


def is_general_conversation_intent(value: str) -> bool:
    return isinstance(value, str) and _normalized_intent(value) in _GENERAL_CONVERSATION_INTENTS


def question_request_fingerprint(
    context: QuestionContext, *, run_id: str, idempotency_key: str,
    request_payload: Mapping[str, object],
) -> str:
    """Bind replay only to the safe logical request and its exact scoped identity."""
    return "sha256:" + hashlib.sha256(canonical_json_bytes({
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "notebook_id": context.notebook_id,
        "actor_id": context.actor_id,
        "run_id": run_id,
        "idempotency_key": idempotency_key,
        "request": dict(request_payload),
    })).hexdigest()
class CredentialResolver(Protocol):
    def resolve(self, provider_code: str) -> str: ...


class WorkspaceModelResolver(Protocol):
    def resolve(
        self, context: WorkspaceModelContext, capability: str,
    ) -> ContextManager[ResolvedModel]: ...


class DocumentIndexPort(Protocol):
    def search(self, context: DocumentProcessingContext, **kwargs): ...  # type: ignore[no-untyped-def]


class QuestionEgressPort(Protocol):
    def authorize(self, context: QuestionContext, **kwargs): ...  # type: ignore[no-untyped-def]


class QuestionAdapterRegistry:
    """Selects the grounded generator; local fixture is explicit and never calls a provider."""

    @staticmethod
    def _validate_custom_adapter_type(selection: ResolvedModel) -> None:
        if selection.provider_code == "CUSTOM" and selection.adapter_type not in {
            "", "CUSTOM", "openai_compatible", "anthropic_compatible",
        }:
            raise ValueError("TEXT_PROVIDER_UNAVAILABLE")

    @dataclass(frozen=True, slots=True)
    class Prepared:
        request: GroundedQuestionRequest | GeneralConversationRequest
        selection: object
        adapter: object
        provider_payload: dict[str, object]

    def prepare(
        self, selection: ResolvedModel, evidence, question: str, trace_id: str,  # type: ignore[no-untyped-def]
        transport: TextGenerationTransport,
    ) -> "QuestionAdapterRegistry.Prepared":
        request = GroundedQuestionRequest(question.strip(), evidence, trace_id)
        self._validate_custom_adapter_type(selection)
        if selection.provider_code == "OLLAMA":
            adapter = OllamaTextGenerationAdapter(transport=transport)
            return self.Prepared(request, selection, adapter, adapter.provider_payload(request, selection))
        if selection.provider_code == "OMNIROUTE":
            adapter = _OmniRouteTextGenerationAdapter(
                transport=transport, api_key=cast(str, selection.credential_text()),
            )
            return self.Prepared(request, selection, adapter, adapter.provider_payload(request, selection))
        if selection.provider_code not in {
            "GROQ", "MISTRAL", "UPSTAGE", "OPENROUTER", "EOUL_GATEWAY", "CUSTOM",
        }:
            raise ValueError("TEXT_PROVIDER_UNAVAILABLE")
        api_key = selection.credential_text(required=selection.provider_code != "CUSTOM")
        adapter_type = (
            AnthropicMessagesTextGenerationAdapter
            if selection.provider_code == "CUSTOM" and selection.adapter_type == "anthropic_compatible"
            else OpenAICompatibleTextGenerationAdapter
        )
        external_adapter = adapter_type(
            transport=transport, api_key=api_key
        )
        return self.Prepared(
            request,
            selection,
            external_adapter,
            external_adapter.provider_payload(request, selection),
        )

    def generate_prepared(self, prepared: "QuestionAdapterRegistry.Prepared") -> GroundedTextResult:
        return prepared.adapter.generate(  # type: ignore[attr-defined]
            prepared.request, prepared.selection, provider_payload=prepared.provider_payload,
        )

    def prepare_general(
        self, selection: ResolvedModel, question: str, trace_id: str,
        transport: TextGenerationTransport,
    ) -> "QuestionAdapterRegistry.Prepared":
        request = GeneralConversationRequest(question.strip(), trace_id)
        self._validate_custom_adapter_type(selection)
        if selection.provider_code == "OLLAMA":
            adapter = OllamaTextGenerationAdapter(transport=transport)
        elif selection.provider_code == "OMNIROUTE":
            adapter = _OmniRouteTextGenerationAdapter(
                transport=transport, api_key=cast(str, selection.credential_text()),
            )
        elif selection.provider_code in {
            "GROQ", "MISTRAL", "UPSTAGE", "OPENROUTER", "EOUL_GATEWAY", "CUSTOM",
        }:
            adapter_type = (
                AnthropicMessagesTextGenerationAdapter
                if selection.provider_code == "CUSTOM" and selection.adapter_type == "anthropic_compatible"
                else OpenAICompatibleTextGenerationAdapter
            )
            adapter = adapter_type(
                transport=transport, api_key=selection.credential_text(required=selection.provider_code != "CUSTOM"),
            )
        else:
            raise ValueError("TEXT_PROVIDER_UNAVAILABLE")
        return self.Prepared(
            request, selection, adapter, adapter.general_provider_payload(request, selection),
        )

    def generate_general(self, prepared: "QuestionAdapterRegistry.Prepared") -> GroundedTextResult:
        return prepared.adapter.generate_general(  # type: ignore[attr-defined]
            prepared.request, prepared.selection, provider_payload=prepared.provider_payload,
        )


class _OmniRouteTextGenerationAdapter(OpenAICompatibleTextGenerationAdapter):
    """Single-attempt OpenAI Responses adapter for the OmniRoute gateway."""

    @staticmethod
    def _url(selection: ResolvedModel) -> str:
        return _append_provider_path(selection.base_url, "/v1/responses")

    @classmethod
    def provider_payload(cls, request, selection):  # type: ignore[no-untyped-def]
        chat = super().provider_payload(request, selection)
        return {
            "model": selection.model_id, "input": chat["messages"],
            "text": {"format": cast(dict[str, object], chat["response_format"])["json_schema"]},
        }

    @classmethod
    def general_provider_payload(cls, request, selection):  # type: ignore[no-untyped-def]
        chat = super().general_provider_payload(request, selection)
        return {
            "model": selection.model_id, "input": chat["messages"],
            "text": {"format": cast(dict[str, object], chat["response_format"])["json_schema"]},
        }

    def _call(self, selection: ResolvedModel, payload: dict[str, object]) -> dict[str, object]:
        return self._transport.post_json(
            url=self._url(selection), api_key=self._api_key, payload=payload,
            timeout_seconds=self._timeout_seconds,
        )

    @staticmethod
    def _parsed(response: Mapping[str, object]) -> dict[str, object]:
        raw = response.get("output_text")
        try:
            value = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID") from None
        if not isinstance(value, dict):
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID")
        return cast(dict[str, object], value)

    def generate_general(self, request, selection, *, provider_payload=None):  # type: ignore[no-untyped-def]
        parsed = self._parsed(self._call(
            selection, provider_payload or self.general_provider_payload(request, selection),
        ))
        if set(parsed) != {"answer"}:
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID")
        answer = str(parsed["answer"]).strip()
        if not answer or len(answer) > 8_000:
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID")
        return GroundedTextResult(answer, (), False, {})

    def generate(self, request, selection, *, provider_payload=None):  # type: ignore[no-untyped-def]
        parsed = self._parsed(self._call(
            selection, provider_payload or self.provider_payload(request, selection),
        ))
        try:
            answer = str(parsed["answer"]).strip()
            cited = tuple(str(item) for item in cast(list[object], parsed["cited_chunk_ids"]))
            insufficient = bool(parsed["insufficient"])
        except (KeyError, TypeError):
            raise ValueError("TEXT_GENERATION_RESPONSE_INVALID") from None
        allowed = {item.chunk_id for item in request.evidence}
        cited_text = "\n".join(item.text for item in request.evidence if item.chunk_id in set(cited))
        if (
            not answer or len(answer) > 8_000 or len(cited) != len(set(cited))
            or not set(cited).issubset(allowed) or (insufficient and cited)
            or (not insufficient and not cited)
            or not _evidence_anchors(answer).issubset(_evidence_anchors(cited_text))
        ):
            raise ValueError("TEXT_GENERATION_GROUNDING_INVALID")
        return GroundedTextResult(answer, cited, insufficient, {})


class QuestionAnsweringError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PreparedQuestionAuthorization:
    selection: object
    provider_payload: bytes
    evidence_count: int


class _QaMediaBridgeDiagnosticAdapter(OpenAICompatibleTextGenerationAdapter):
    """Transient, exact-QA response structure only; never retain response values."""

    _INSUFFICIENT_ANSWER = "선택한 원문 근거에 질문에 답할 정보가 없습니다."
    _RESULT_SCHEMA = {
        "type": "object", "properties": {"result": {"anyOf": [
            {"type": "object", "properties": {
                "answer": {"type": "string", "enum": [_INSUFFICIENT_ANSWER]},
                "cited_chunk_ids": {"type": "array", "items": {"type": "string"}, "maxItems": 0},
                "insufficient": {"type": "boolean", "enum": [True]},
            }, "required": ["answer", "cited_chunk_ids", "insufficient"], "additionalProperties": False},
            {"type": "object", "properties": {
                "answer": {"type": "string"},
                "cited_chunk_ids": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                "insufficient": {"type": "boolean", "enum": [False]},
            }, "required": ["answer", "cited_chunk_ids", "insufficient"], "additionalProperties": False},
        ]}}, "required": ["result"], "additionalProperties": False,
    }

    def __init__(
        self, *, transport: TextGenerationTransport, api_key: str | None,
        timeout_seconds: float,
        evidence: tuple[IndexedEvidenceChunk, ...] | None,
    ) -> None:
        super().__init__(transport=transport, api_key=api_key, timeout_seconds=timeout_seconds)
        self._diagnostic_evidence = evidence

    def _grounding_failure(self, parsed: object) -> str:
        # Diagnostic only: the inherited generate() still executes its original
        # parser and validator. Use its actual request evidence, not masked wire text.
        if self._diagnostic_evidence is None:
            return "not_grounded"
        try:
            value = cast(dict[str, object], parsed)
            answer = str(value["answer"]).strip()
            cited = tuple(str(item) for item in cast(list[object], value["cited_chunk_ids"]))
            insufficient = bool(value["insufficient"])
        except (KeyError, IndexError, TypeError):
            return "response_invalid"
        if not answer:
            return "answer_empty"
        if len(answer) > 8_000:
            return "answer_too_long"
        if len(cited) != len(set(cited)):
            return "duplicate_citation"
        if not set(cited).issubset({item.chunk_id for item in self._diagnostic_evidence}):
            return "unknown_citation"
        if insufficient and cited:
            return "insufficient_with_citations"
        if not insufficient and not cited:
            return "sufficient_without_citations"
        cited_text = "\n".join(
            item.text for item in self._diagnostic_evidence if item.chunk_id in set(cited)
        )
        if not _evidence_anchors(answer).issubset(_evidence_anchors(cited_text)):
            return "unsupported_anchor"
        return "none"

    @staticmethod
    def _shape(value: object) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, dict):
            return "object"
        if isinstance(value, list):
            return "array"
        if isinstance(value, (int, float)):
            return "number"
        return "other"

    @staticmethod
    def _count(value: object) -> int:
        return value if type(value) is int and 0 <= value <= 2_147_483_647 else -1

    def _post(self, selection, payload):  # type: ignore[no-untyped-def]
        request_bytes = len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        if request_bytes > 65536:
            logging.getLogger(__name__).warning(
                "s18_media_bridge_structure cap=%d request_bytes=%d response_type=not_sent",
                self._count(payload.get("max_tokens")), request_bytes,
            )
            raise ValueError("TEXT_GENERATION_FAILED")
        try:
            response = super()._post(selection, payload)
        except Exception:
            logging.getLogger(__name__).warning(
                "s18_media_bridge_structure cap=%d request_bytes=%d response_type=unavailable",
                self._count(payload.get("max_tokens")), request_bytes,
            )
            raise
        choices = response.get("choices") if isinstance(response, dict) else None
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        reason = first.get("finish_reason") if isinstance(first, dict) else None
        finish = (reason if isinstance(reason, str) and reason in {
            "stop", "length", "tool_calls", "content_filter", "function_call",
        } else "none" if reason is None else "other")
        parsed = content
        json_ok = False
        if isinstance(content, str):
            try:
                parsed = json.loads(content)
                json_ok = True
            except (ValueError, RecursionError):
                parsed = None
        envelope_valid = True
        if self._diagnostic_evidence is not None:
            inner = parsed.get("result") if isinstance(parsed, dict) else None
            envelope_valid = bool(
                isinstance(parsed, dict) and set(parsed) == {"result"}
                and isinstance(inner, dict) and set(inner) == {"answer", "cited_chunk_ids", "insufficient"}
                and isinstance(inner["answer"], str)
                and isinstance(inner["cited_chunk_ids"], list)
                and all(isinstance(item, str) for item in inner["cited_chunk_ids"])
                and type(inner["insufficient"]) is bool
            )
            parsed = inner
        usage = response.get("usage") if isinstance(response, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        parsed_object = parsed if isinstance(parsed, dict) else {}
        answer = parsed_object.get("answer")
        cited = parsed_object.get("cited_chunk_ids")
        insufficient = parsed_object.get("insufficient")
        insufficient_answer_invalid = bool(
            self._diagnostic_evidence is not None and envelope_valid
            and insufficient is True and cited == [] and answer != self._INSUFFICIENT_ANSWER
        )
        # Every interpolated value is a fixed enum, bool, or bounded count.
        # No trace/input/model/credential/URL, arbitrary key, or exception text.
        logging.getLogger(__name__).warning(
            "s18_media_bridge_structure cap=%d request_bytes=%d response_type=%s choices_type=%s choices_count=%d "
            "finish_reason=%s message_type=%s content_type=%s content_chars=%d json_ok=%s json_type=%s "
            "answer_type=%s answer_chars=%d cited_count=%d insufficient_type=%s "
            "insufficient_value=%s failure_reason=%s "
            "prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            self._count(payload.get("max_tokens")), request_bytes, self._shape(response), self._shape(choices),
            len(choices) if isinstance(choices, list) else -1, finish,
            self._shape(message), self._shape(content), len(content) if isinstance(content, str) else -1,
            json_ok, self._shape(parsed), self._shape(answer), len(answer) if isinstance(answer, str) else -1,
            len(cited) if isinstance(cited, list) else -1, self._shape(insufficient),
            insufficient if type(insufficient) is bool else "unknown",
            "response_invalid" if not envelope_valid else "insufficient_answer_invalid"
            if insufficient_answer_invalid else self._grounding_failure(parsed),
            self._count(usage.get("prompt_tokens")), self._count(usage.get("completion_tokens")),
            self._count(usage.get("total_tokens")),
        )
        if self._diagnostic_evidence is not None:
            if not envelope_valid or insufficient_answer_invalid:
                raise ValueError("TEXT_GENERATION_RESPONSE_INVALID")
            # Unwrap only the transport envelope. Do not repair answers/citations
            # or mutate the original response. Inherited generate() still applies
            # all grounding checks against the original request evidence.
            return {**response, "choices": [
                {**first, "message": {**message, "content": json.dumps(parsed)}},
                *choices[1:],
            ]}
        return response


@dataclass(frozen=True, slots=True)
class QuestionInputSource:
    origin: str
    context_item_id: str
    source_id: str
    source_version_id: str
    digest_sha256: str | None = None
    knowledge_registration_id: str | None = None

    def __post_init__(self) -> None:
        if self.origin not in {"raw_source", "daon_knowledge"}:
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        if not all((self.context_item_id, self.source_id, self.source_version_id)):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")


@dataclass(frozen=True, slots=True)
class _FinalQuestionFailure:
    code: str
    selection: TextModelSelection
    authorization: Mapping[str, object]
    sources: tuple[QuestionInputSource, ...]
    actual_context_mode: str


class QuestionAnsweringService:
    def __init__(
        self, model_resolver: WorkspaceModelResolver,
        repository: PostgresQuestionAnsweringRepository,
        document_index: DocumentIndexPort, credential_resolver: CredentialResolver,
        transport: TextGenerationTransport, egress: QuestionEgressPort,
        *, adapter_registry: QuestionAdapterRegistry | None = None,
        concurrent_wait_seconds: float = 2.0,
        concurrent_poll_seconds: float = 0.02,
        qa_structured_ollama_workspace_id: str = "",
        run_cost_sessions=None,
    ) -> None:
        self._model_resolver = model_resolver
        self._repository = repository
        self._document_index = document_index
        # Retained in the constructor until callers are migrated; credentials are
        # resolved exclusively from the selected encrypted connection.
        del credential_resolver
        self._transport = transport
        self._egress = egress
        self._adapter_registry = adapter_registry or QuestionAdapterRegistry()
        self._concurrent_wait_seconds = max(0.0, concurrent_wait_seconds)
        self._concurrent_poll_seconds = max(0.001, concurrent_poll_seconds)
        self._qa_structured_ollama_workspace_id = qa_structured_ollama_workspace_id
        self._run_cost_sessions = run_cost_sessions

    def _generate_with_run_cost(self,prepared,*,general,session):
        try:
            result=(self._adapter_registry.generate_general(prepared) if general
                else self._adapter_registry.generate_prepared(prepared))
        except BaseException as error:
            if session is not None:
                session.finish(outcome='failed' if isinstance(error,Exception) else 'cancelled')
            raise
        if session is not None:
            receipt=session.finish(outcome='succeeded')
            if receipt is None:raise ValueError('RUN_COST_CALL_UNCONFIRMED')
            result=replace(result,price_card_receipt=receipt)
        return result

    def _qa_media_selection(self, context: QuestionContext, selection: object) -> bool:
        return bool(
            self._qa_structured_ollama_workspace_id == context.workspace_id
            and (context.tenant_id, context.workspace_id, context.actor_id) == (
                "tenant-PvH_pofxPyDtfk3wWBrn3ru3", "workspace-2edb96cb84a252046e67307d",
                "usr-fwYlLlWlcP_GvvpPoZIkadvr",
            )
            and getattr(selection, "provider_code", None) == "CUSTOM"
            and getattr(selection, "provider_kind", None) == "external_api"
            and getattr(selection, "capability", None) == "text_generation"
            and getattr(selection, "connection_id", None) == "provider-90efb3fd4ea99fd3ff24edc767ca2800"
            and getattr(selection, "adapter_type", None) == "openai_compatible"
            and getattr(selection, "base_url", None) == "https://media-bridge-gateway.sinsan.kr/v1"
            and getattr(selection, "model_id", None) == "uss/solar-pro4"
        )

    def _prepare_qa_structured_payload(
        self, context: QuestionContext, prepared: QuestionAdapterRegistry.Prepared,
    ) -> QuestionAdapterRegistry.Prepared:
        cost_sessions=getattr(self,'_run_cost_sessions',None)
        if cost_sessions is not None and cost_sessions.applies(
            context,prepared.selection,run_kind='question'):
            prepared=replace(prepared,provider_payload={**prepared.provider_payload,
                'max_tokens':prepared.provider_payload.get('max_tokens',512)})
        if (
            self._qa_media_selection(context, getattr(prepared, "selection", None))
            and isinstance(prepared.request, (GroundedQuestionRequest, GeneralConversationRequest))
            and type(prepared.adapter) is OpenAICompatibleTextGenerationAdapter
        ):
            payload = {**prepared.provider_payload, "max_tokens": 512}
            payload.pop("max_completion_tokens", None)
            if isinstance(prepared.request, GroundedQuestionRequest):
                response_format = payload["response_format"]
                payload["response_format"] = {**response_format, "json_schema": {
                    **response_format["json_schema"], "schema": _QaMediaBridgeDiagnosticAdapter._RESULT_SCHEMA,
                }}
            schema = payload["response_format"]["json_schema"]["schema"]
            instruction = (
                "\n\nReturn ONLY JSON object; no markdown, prose, or code fences. "
                "Use exactly the keys and value types in this existing JSON Schema; no additional keys. "
                "JSON Schema:\n" + canonical_json_bytes(schema).decode("utf-8")
            )
            if isinstance(prepared.request, GroundedQuestionRequest):
                instruction += (
                    "\nThe root result envelope takes precedence over all earlier flat output-shape instructions; "
                    "put exactly answer, cited_chunk_ids, and insufficient inside result. "
                    "Preserve the supplied evidence and question; decide sufficiency from that evidence. "
                    "When you choose insufficient=true, use the exact insufficient answer in the schema and no citations. "
                    "\nCite only supplied chunk_id values. If the supplied evidence cannot answer the question, "
                    "return insufficient=true and cited_chunk_ids=[]; explain the evidence limitation "
                    "briefly without inventing facts; do not repeat question or Source numbers or identifiers "
                    "in that insufficient answer. Decide sufficiency from the supplied evidence."
                    " Only when you choose insufficient=true, this rule takes precedence over the earlier Source scope explanation: "
                    "return cited_chunk_ids=[] and a brief information-absence notice only, "
                    "without restating Source scope, question details, numbers or identifiers. "
                    "For example: 선택한 원문 근거에 질문에 답할 정보가 없습니다. "
                    "If evidence supports an answer, answer normally and cite only supplied chunk_id values."
                )
            payload["messages"] = [
                {**message, "content": message["content"] + instruction}
                if message["role"] == "system" else message
                for message in payload["messages"]
            ]
            adapter = _QaMediaBridgeDiagnosticAdapter(
                transport=prepared.adapter._transport, api_key=prepared.adapter._api_key,
                timeout_seconds=prepared.adapter._timeout_seconds,
                evidence=prepared.request.evidence if isinstance(prepared.request, GroundedQuestionRequest) else None,
            )
            return replace(prepared, provider_payload=payload, adapter=adapter)
        if (
            self._qa_structured_ollama_workspace_id
            and context.workspace_id == self._qa_structured_ollama_workspace_id
            and isinstance(prepared.adapter, OllamaTextGenerationAdapter)
            and isinstance(prepared.request, GroundedQuestionRequest)
        ):
            try:
                return replace(prepared, provider_payload=prepared.adapter.prepare_qa_structured_payload(
                    prepared.request, prepared.selection, prepared.provider_payload,
                ))
            except ValueError:
                raise QuestionAnsweringError("TEXT_GENERATION_RESPONSE_INVALID", status=502) from None
        return prepared

    def _wait_for_provider_owner(
        self, context: QuestionContext, run_id: str, request_fingerprint: str,
    ) -> StoredQuestionAnswer:
        deadline = time.monotonic() + self._concurrent_wait_seconds
        while True:
            completed = self._repository.load_completed(context, run_id)
            if completed is not None:
                replay = self._repository.load_completed_for_replay(
                    context, run_id, request_fingerprint,
                )
                if replay is None:
                    raise QuestionAnsweringError(
                        "QUESTION_RESULT_INVALID", status=500,
                    )
                return replay
            failed_replay = getattr(self._repository, "load_failed_for_replay", None)
            if callable(failed_replay) and failed_replay(context, run_id, request_fingerprint):
                raise QuestionAnsweringError("QUESTION_NEW_RUN_REQUIRED", status=409, retryable=True)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise QuestionAnsweringError(
                    "QUESTION_NEW_RUN_REQUIRED", status=409, retryable=True,
                )
            time.sleep(min(self._concurrent_poll_seconds, remaining))

    @staticmethod
    def _context_sources(
        source_id: str, source_version_id: str,
        context_sources: tuple[QuestionInputSource, ...] | None,
    ) -> tuple[QuestionInputSource, ...]:
        return context_sources or (
            QuestionInputSource(
                "raw_source", source_id, source_id, source_version_id,
            ),
        )

    def _search_context(
        self, context: QuestionContext, sources: tuple[QuestionInputSource, ...], question: str,
    ) -> tuple[IndexedEvidenceChunk, ...]:
        processing_context = DocumentProcessingContext(
            context.tenant_id, context.workspace_id, context.actor_id,
            context.trace_id, context.policy_version,
        )
        chunks: dict[str, IndexedEvidenceChunk] = {}
        for item in sources:
            self._repository.load_ready_source(context, item.source_id, item.source_version_id)
            try:
                evidence = self._document_index.search(
                    processing_context, source_id=item.source_id,
                    source_version_id=item.source_version_id, query=question, limit=10,
                )
            except DocumentUnderstandingError as error:
                if error.code == 'SOURCE_NOT_FOUND' and error.status == 404:
                    raise QuestionAnsweringError('QUESTION_SOURCE_UNAVAILABLE', status=404) from None
                raise
            for chunk in evidence:
                chunks.setdefault(chunk.chunk_id, chunk)
        return tuple(sorted(
            chunks.values(),
            key=lambda item: (-float(item.score), int(item.page), str(item.chunk_id)),
        )[:10])

    def _ready_context_sources(
        self, context: QuestionContext, sources: tuple[QuestionInputSource, ...],
    ) -> tuple[QuestionInputSource, ...]:
        """Keep unavailable sources visible to the caller, but never search them.

        A stale browser selection can legitimately contain a source that became
        unavailable after the list was loaded.  That source must not poison a
        mixed context: only the ready subset is sent to retrieval and grounding.
        Database failures and other repository errors still propagate.
        """
        ready: list[QuestionInputSource] = []
        for item in sources:
            try:
                self._repository.load_ready_source(
                    context, item.source_id, item.source_version_id,
                )
            except QuestionRepositoryError as error:
                if isinstance(error, OpenLawQuestionAccessError):
                    raise QuestionAnsweringError(error.code, status=error.status) from None
                if error.code == "QUESTION_SOURCE_UNAVAILABLE":
                    continue
                raise
            ready.append(item)
        return tuple(ready)

    def prepare_authorization(
        self, context: QuestionContext, *, source_id: str | None,
        source_version_id: str | None, question: str,
        context_mode: str = "raw_only",
        context_sources: tuple[QuestionInputSource, ...] | None = None,
    ) -> PreparedQuestionAuthorization:
        general = is_general_conversation_intent(question) or (source_id is None and source_version_id is None)
        if (source_id is None) != (source_version_id is None):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        sources = () if general and source_id is None else self._context_sources(
            source_id or "", source_version_id or "", context_sources,
        )
        if not general:
            sources = self._ready_context_sources(context, sources)
            if not sources:
                general = True
        evidence = ()
        if not general:
            # Freeze authorization against the same evidence that `ask` will
            # use.  Otherwise the later grounded payload changes its digest
            # and the egress authorizer rejects an otherwise valid approval.
            evidence = self._search_context(context, sources, question)
            if not evidence:
                general = True
        model_context = WorkspaceModelContext(
            context.tenant_id, context.workspace_id, context.actor_id,
        )
        try:
            with self._model_resolver.resolve(model_context, "text_generation") as selection:
                prepared = (
                    self._adapter_registry.prepare_general(
                        selection, question, context.trace_id, self._transport,
                    )
                    if general else self._adapter_registry.prepare(
                        selection, evidence, question, context.trace_id, self._transport,
                    )
                )
                prepared = self._prepare_qa_structured_payload(context, prepared)
                wire = canonical_json_bytes(prepared.provider_payload)
                transformer = getattr(self._egress, "prepare_payload", None)
                if callable(transformer):
                    wire = transformer(context, wire)
                return PreparedQuestionAuthorization(selection, wire, len(evidence))
        except WorkspaceModelUnavailable as error:
            raise QuestionAnsweringError(error.code, status=503, retryable=error.retryable) from None

    def ask(
        self, context: QuestionContext, *, source_id: str | None,
        source_version_id: str | None, question: str, run_id: str,
        approved_authorization: Mapping[str, str] | None = None,
        context_mode: str = "raw_only",
        context_sources: tuple[QuestionInputSource, ...] | None = None,
        request_fingerprint: str | None = None,
        replay_checked: bool = False,
        request_payload: Mapping[str, object] | None = None,
        idempotency_key: str | None = None,
    ) -> StoredQuestionAnswer:
        if (request_payload is None) != (idempotency_key is None):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        logical_payload = dict(request_payload) if request_payload is not None else None
        logical_key = idempotency_key
        if request_fingerprint is None:
            if logical_payload is None:
                # 기존 직접 호출의 지문 생성에 실제 사용하던 body/key만 결속한다.
                logical_payload = {
                    "question": question.strip(), "source_id": source_id,
                    "source_version_id": source_version_id, "context_mode": context_mode,
                }
                logical_key = run_id
            request_fingerprint = question_request_fingerprint(
                context, run_id=run_id, idempotency_key=cast(str, logical_key),
                request_payload=logical_payload,
            )
        if not replay_checked:
            replay = self.replay(
                context, run_id=run_id, request_fingerprint=request_fingerprint,
            )
            if replay is not None:
                return replay
        general = is_general_conversation_intent(question) or (source_id is None and source_version_id is None)
        if general and ((source_id is None) != (source_version_id is None)):
            raise QuestionAnsweringError("QUESTION_CONTEXT_INVALID")
        sources = () if general and source_id is None else self._context_sources(
            source_id or "", source_version_id or "", context_sources,
        )
        resolved_sources = sources
        if not general:
            sources = self._ready_context_sources(context, sources)
            if not sources:
                general = True
                source_id = None
                source_version_id = None
            else:
                source_id = sources[0].source_id
                source_version_id = sources[0].source_version_id
        model_context = WorkspaceModelContext(
            context.tenant_id, context.workspace_id, context.actor_id,
        )
        # 명시 지문만 받은 호출은 원 body/key를 추정하지 않는다. 선택 자료가 있는 general/zero-evidence는 후속 범위다.
        final_failure: list[_FinalQuestionFailure] = []
        raw_resources = logical_payload.get("knowledge_context") if logical_payload is not None else None
        raw_resource_items = raw_resources.get("resources") if isinstance(raw_resources, Mapping) else None
        raw_resources_match = (
            isinstance(raw_resources, Mapping) and set(raw_resources) == {"mode", "resources"}
            and raw_resources.get("mode") == "raw_only" and isinstance(raw_resource_items, list)
            and 1 <= len(raw_resource_items) <= 8
            and all(isinstance(item, Mapping) and set(item) == {"resource_kind", "resource_id", "version_id"}
                and item.get("resource_kind") == "source"
                and all(isinstance(item.get(key), str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", item[key])
                    for key in ("resource_id", "version_id")) for item in raw_resource_items)
            and len({(item["resource_id"], item["version_id"]) for item in raw_resource_items}) == len(raw_resource_items)
            and not ({"source_id", "source_version_id", "context_mode"} & set(logical_payload))
            and tuple(QuestionInputSource("raw_source", item["resource_id"], item["resource_id"], item["version_id"])
                for item in raw_resource_items) == resolved_sources
            and context_mode == "raw_only" and not general
        )
        raw_used_subset_match = (
            raw_resources_match and bool(sources)
            and tuple(item for item in resolved_sources if item in sources) == sources
        )
        capture = final_failure if (
            logical_payload is not None and logical_key is not None
            and logical_payload.get("context_mode", "raw_only") in ("raw_only", "general_ungrounded")
            and (logical_payload.get("context_mode", "raw_only") == "raw_only" or general)
            and (logical_payload.get("knowledge_context") is None or raw_resources_match)
            and (general or sources == resolved_sources or raw_used_subset_match)
            and (general or raw_resources_match or len(sources) == 1)
            and (not general or source_id is None and source_version_id is None and not sources and not context_sources
                and logical_payload.get("source_id") is None and logical_payload.get("source_version_id") is None)
            and callable(getattr(self._repository, "persist_failed", None))
        ) else None
        try:
            with self._model_resolver.resolve(model_context, "text_generation") as selection:
                try:
                    return self._ask_resolved(
                        context, selection=selection, sources=sources, general=general,
                        source_id=source_id, source_version_id=source_version_id,
                        question=question, run_id=run_id,
                        approved_authorization=approved_authorization,
                        context_mode=context_mode, request_fingerprint=request_fingerprint,
                        failure_capture=capture,
                    )
                except QuestionAnsweringError as error:
                    if self._run_cost_sessions is not None and self._run_cost_sessions.applies(
                        context,selection,run_kind='question'):
                        # 명시 Run 비용 경로는 실패/불확정 뒤 자동 credential 재전송을 하지 않는다.
                        raise
                    if not getattr(self._model_resolver, "supports_user_credential_fallback", False) or error.code not in {
                        "TEXT_PROVIDER_UNAVAILABLE", "TEXT_GENERATION_PROVIDER_UNAVAILABLE",
                        "TEXT_GENERATION_FAILED",
                    }:
                        raise
                    user_context = WorkspaceModelContext(
                        context.tenant_id, context.workspace_id, context.actor_id, "user",
                    )
                    try:
                        with self._model_resolver.resolve(user_context, "text_generation") as fallback:
                            return self._ask_resolved(
                                context, selection=fallback, sources=sources, general=general,
                                source_id=source_id, source_version_id=source_version_id,
                                question=question, run_id=run_id,
                                approved_authorization=approved_authorization,
                                context_mode=context_mode, request_fingerprint=request_fingerprint,
                                failure_capture=capture,
                            )
                    except WorkspaceModelUnavailable:
                        raise error
        except WorkspaceModelUnavailable as error:
            raise QuestionAnsweringError(error.code, status=503, retryable=error.retryable) from None
        except QuestionAnsweringError as error:
            # 기존 fallback 판단/성공 경로가 모두 끝난 뒤에만 한 번 저장한다.
            persist_failed = getattr(self._repository, "persist_failed", None)
            if final_failure and callable(persist_failed):
                failed = final_failure[0]
                if failed.code == error.code and not error.retryable:
                    persist_failed(
                        context, run_id=run_id, request_fingerprint=request_fingerprint,
                        request_payload=logical_payload, idempotency_key=logical_key,
                        context_sources=failed.sources, selection=failed.selection,
                        egress_authorization=failed.authorization,
                        question=question,
                        actual_context_mode=failed.actual_context_mode,
                        **({"resolved_context_sources": resolved_sources} if raw_resources_match else {}),
                    )
            raise

    def _ask_resolved(
        self, context: QuestionContext, *, selection: ResolvedModel,
        sources: tuple[QuestionInputSource, ...], general: bool,
        source_id: str | None, source_version_id: str | None,
        question: str, run_id: str,
        approved_authorization: Mapping[str, str] | None,
        context_mode: str, request_fingerprint: str,
        failure_capture: list[_FinalQuestionFailure] | None = None,
    ) -> StoredQuestionAnswer:
        cost_session=None if self._run_cost_sessions is None else self._run_cost_sessions.for_call(
            context,selection,run_kind='question',run_id=run_id,
            attempt_key=PostgresQuestionAnsweringRepository._opaque_id('attempt',run_id,selection.deployment_id),
            transport=self._transport)
        transport=cost_session if cost_session is not None else self._transport
        if failure_capture is not None:
            failure_capture.clear()
        versions = tuple(dict.fromkeys(
            item.source_version_id for item in sources
        ))
        revalidate = getattr(self._repository, 'revalidate_open_law_sources', None)
        def recheck():
            try:
                if callable(revalidate):
                    revalidate(context, versions)
            except QuestionRepositoryError as error:
                raise QuestionAnsweringError(error.code, status=error.status) from None
        # General fallback deliberately stores no evidence, but the selected
        # sources still need an access lock in its result-commit transaction.
        access_options = {'access_source_version_ids': versions} if versions else {}
        if general:
            try:
                prepare_general = getattr(self._adapter_registry, "prepare_general", None)
                prepared = (
                    prepare_general(
                        selection, question, context.trace_id, transport,
                    )
                    if callable(prepare_general)
                    else self._adapter_registry.prepare(
                        selection, (), question, context.trace_id, transport,
                    )
                )
            except (ValueError, DocumentUnderstandingError) as error:
                raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
                raise QuestionAnsweringError(
                    raw_code if raw_code.startswith("TEXT_") else "TEXT_GENERATION_FAILED",
                    status=503 if raw_code.startswith("TEXT_PROVIDER_") else 502,
                ) from None
            prepared = self._prepare_qa_structured_payload(context, prepared)
            frozen_bytes = canonical_json_bytes(prepared.provider_payload)
            transformer = getattr(self._egress, "prepare_payload", None)
            if callable(transformer):
                frozen_bytes = transformer(context, frozen_bytes)
                prepared = replace(prepared, provider_payload=json.loads(frozen_bytes))
            egress_authorization = self._egress.authorize(
                context, run_id=run_id, source_id=None, source_version_id=None,
                selection=selection, provider_payload=frozen_bytes,
                approved_authorization=approved_authorization,
                question=question, context_mode="general_ungrounded",
                request_fingerprint=request_fingerprint,
            )
            if egress_authorization.get("provider_owner") is False:
                return self._wait_for_provider_owner(
                    context, run_id, request_fingerprint,
                )
            recheck()
            try:
                generated = self._generate_with_run_cost(prepared,general=True,session=cost_session)
            except (ValueError, DocumentUnderstandingError) as error:
                raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
                if (failure_capture is not None and egress_authorization.get("provider_owner") is True
                    and source_id is None and source_version_id is None and not sources
                    and raw_code in {"TEXT_GENERATION_RESPONSE_INVALID", "TEXT_GENERATION_GROUNDING_INVALID"}):
                    # 보관하는 것은 운영 DTO뿐이다. ResolvedModel/credential/adapter/원예외는 capture하지 않는다.
                    safe_selection = TextModelSelection(
                        provider_code=selection.provider_code, base_url=selection.base_url,
                        profile_id=selection.profile_id, deployment_id=selection.deployment_id,
                        model_id=selection.model_id, binding_version=selection.binding_version,
                        provider_kind=selection.provider_kind, connection_id=selection.connection_id,
                        credential_version=selection.credential_version, catalog_version=selection.catalog_version,
                        routing_owner=selection.routing_owner, daon_fallback_allowed=selection.daon_fallback_allowed,
                    )
                    failure_capture.append(_FinalQuestionFailure(
                        raw_code,safe_selection,dict(egress_authorization),(),"general_ungrounded",
                    ))
                raise QuestionAnsweringError(
                    raw_code if raw_code.startswith("TEXT_") else "TEXT_GENERATION_FAILED", status=502,
                ) from None
            recheck()
            return self._repository.persist_completed(
                context, run_id=run_id, source_id=None, source_version_id=None,
                question=question, selection=selection, evidence=(), result=generated,
                provider_called=True, egress_authorization=egress_authorization,
                context_mode="general_ungrounded", context_sources=(),
                request_fingerprint=request_fingerprint,
                **access_options,
            )
        evidence = self._search_context(context, sources, question)
        if not evidence:
            prepare_general = getattr(self._adapter_registry, "prepare_general", None)
            # Legacy/custom evidence adapters do not advertise an ungrounded
            # route; freeze an empty decision without sending the question.
            if not callable(prepare_general):
                recheck()
                egress_authorization = self._egress.authorize(
                    context, run_id=run_id, source_id=source_id,
                    source_version_id=source_version_id, selection=selection,
                    provider_payload=b"", no_external_payload=True,
                    approved_authorization=approved_authorization,
                    question=question, context_mode=context_mode,
                    request_fingerprint=request_fingerprint,
                )
                return self._repository.persist_completed(
                    context, run_id=run_id, source_id=source_id,
                    source_version_id=source_version_id, question=question,
                    selection=selection, evidence=(),
                    result=GroundedTextResult("", (), False, {"no_evidence": True}),
                    provider_called=False, egress_authorization=egress_authorization,
                    context_mode=context_mode, context_sources=sources,
                    request_fingerprint=request_fingerprint,
                )
            prepared = self._prepare_qa_structured_payload(context, prepare_general(
                selection, question, context.trace_id, transport,
            ))
            # 전송할 실제 prepared payload를 기존 변환에 통과시키고 같은 표현의 canonical bytes를 승인한다.
            fallback_wire = canonical_json_bytes(prepared.provider_payload)
            transformer = getattr(self._egress, "prepare_payload", None)
            if callable(transformer):
                transformed = transformer(context, fallback_wire)
                prepared = replace(prepared, provider_payload=json.loads(transformed))
                fallback_wire = canonical_json_bytes(prepared.provider_payload)
            egress_authorization = self._egress.authorize(
                context, run_id=run_id, source_id=None, source_version_id=None,
                selection=selection, provider_payload=fallback_wire,
                approved_authorization=approved_authorization,
                question=question, context_mode="general_ungrounded",
                request_fingerprint=request_fingerprint,
            )
            if egress_authorization.get("provider_owner") is False:
                return self._wait_for_provider_owner(context, run_id, request_fingerprint)
            recheck()
            generated = self._generate_with_run_cost(prepared,general=True,session=cost_session)
            recheck()
            return self._repository.persist_completed(
                context, run_id=run_id, source_id=None, source_version_id=None,
                question=question, selection=selection, evidence=(), result=generated,
                provider_called=True, egress_authorization=egress_authorization,
                context_mode="general_ungrounded", context_sources=(),
                request_fingerprint=request_fingerprint,
                **access_options,
            )
        try:
            prepared = self._adapter_registry.prepare(
                selection, evidence, question, context.trace_id, transport,
            )
        except (ValueError, DocumentUnderstandingError) as error:
            raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
            raise QuestionAnsweringError(
                raw_code if raw_code.startswith("TEXT_") else "TEXT_GENERATION_FAILED",
                status=503 if raw_code.startswith("TEXT_PROVIDER_") else 502,
            ) from None
        original_format = prepared.provider_payload.get("format")
        prepared = self._prepare_qa_structured_payload(context, prepared)
        # Only the existing verified QA adapter changes section evidence to
        # this choice schema (law); do not repeat its canonical ID/metadata checks.
        qa_law_schema = prepared.provider_payload.get("format") if (
            isinstance(prepared.request, GroundedQuestionRequest)
            and all(item.locator_kind == "section" for item in prepared.request.evidence)
            and prepared.provider_payload.get("format") != original_format
        ) else None
        frozen_bytes = canonical_json_bytes(prepared.provider_payload)
        transformer = getattr(self._egress, "prepare_payload", None)
        if callable(transformer):
            frozen_bytes = transformer(context, frozen_bytes)
            prepared = replace(prepared, provider_payload=json.loads(frozen_bytes))
        egress_authorization = self._egress.authorize(
            context, run_id=run_id, source_id=source_id,
            source_version_id=source_version_id, selection=selection,
            provider_payload=frozen_bytes,
            approved_authorization=approved_authorization,
            question=question, context_mode=context_mode,
            request_fingerprint=request_fingerprint,
        )
        if egress_authorization.get("provider_owner") is False:
            return self._wait_for_provider_owner(
                context, run_id, request_fingerprint,
            )
        if (
            self._qa_structured_ollama_workspace_id
            and context.workspace_id == self._qa_structured_ollama_workspace_id
            and isinstance(prepared.adapter, OllamaTextGenerationAdapter)
            and isinstance(prepared.request, GroundedQuestionRequest)
            and 1 <= len(prepared.request.evidence) <= 5
            and (all(item.locator_kind == "docx_block" for item in prepared.request.evidence)
                 or all(item.locator_kind == "pptx_block" for item in prepared.request.evidence)
                 or all(item.locator_kind == "audio_segment" for item in prepared.request.evidence)
                 or (qa_law_schema is not None and prepared.provider_payload.get("format") == qa_law_schema))
            and len({(item.source_id, item.source_version_id) for item in prepared.request.evidence}) == 1
        ):
            # Canonical authorization remains sorted and immutable. Restore only
            # grammar property order after transformations, immediately before HTTP.
            schema = prepared.provider_payload.get("format")
            branches = schema.get("anyOf") if isinstance(schema, dict) else None
            order = ("insufficient", "cited_chunk_ids", "answer")
            if (isinstance(branches, list) and len(branches) == 2
                    and all(isinstance(branch, dict) and isinstance(branch.get("properties"), dict)
                            and set(branch["properties"]) == set(order) for branch in branches)):
                ordered_schema = {**schema, "anyOf": [
                    {**branch, "properties": {key: branch["properties"][key] for key in order}}
                    for branch in branches]}
                prepared = replace(prepared, provider_payload={
                    **prepared.provider_payload, "format": ordered_schema})
        recheck()
        try:
            generated = self._generate_with_run_cost(prepared,general=False,session=cost_session)
        except (ValueError, DocumentUnderstandingError) as error:
            raw_code = error.code if isinstance(error, DocumentUnderstandingError) else str(error)
            code = (
                "TEXT_PROVIDER_UNAVAILABLE"
                if raw_code.startswith("UNDERSTANDING_PROVIDER_")
                or raw_code == "PROVIDER_CREDENTIAL_NOT_CONFIGURED"
                else raw_code
            )
            retryable = code in {
                "TEXT_PROVIDER_UNAVAILABLE", "TEXT_GENERATION_PROVIDER_UNAVAILABLE",
            }
            if (failure_capture is not None and egress_authorization.get("provider_owner") is True
                and context_mode == "raw_only" and 1 <= len(sources) <= 8
                and all(item.origin == "raw_source" for item in sources)
                and raw_code in {"TEXT_GENERATION_RESPONSE_INVALID", "TEXT_GENERATION_GROUNDING_INVALID"}):
                # credential/adapter/prepared/원예외는 보관하지 않고 운영 식별 필드만 복사한다.
                safe_selection = TextModelSelection(
                    provider_code=selection.provider_code, base_url=selection.base_url,
                    profile_id=selection.profile_id, deployment_id=selection.deployment_id,
                    model_id=selection.model_id, binding_version=selection.binding_version,
                    provider_kind=selection.provider_kind, connection_id=selection.connection_id,
                    credential_version=selection.credential_version, catalog_version=selection.catalog_version,
                    routing_owner=selection.routing_owner, daon_fallback_allowed=selection.daon_fallback_allowed,
                )
                failure_capture.append(_FinalQuestionFailure(
                    raw_code, safe_selection, dict(egress_authorization), sources,"raw_only",
                ))
            raise QuestionAnsweringError(
                code if code.startswith("TEXT_") else "TEXT_GENERATION_FAILED",
                status=503 if retryable else 502, retryable=retryable,
            ) from None
        recheck()
        return self._repository.persist_completed(
            context, run_id=run_id, source_id=source_id,
            source_version_id=source_version_id, question=question,
            selection=selection, evidence=evidence, result=generated,
            provider_called=True,
            egress_authorization=egress_authorization,
            context_mode=context_mode, context_sources=sources,
            request_fingerprint=request_fingerprint,
        )

    def replay(
        self, context: QuestionContext, *, run_id: str, request_fingerprint: str,
    ) -> StoredQuestionAnswer | None:
        completed = self._repository.load_completed_for_replay(
            context, run_id, request_fingerprint,
        )
        if completed is not None:
            return completed
        failed_replay = getattr(self._repository, "load_failed_for_replay", None)
        if callable(failed_replay) and failed_replay(context, run_id, request_fingerprint):
            raise QuestionAnsweringError("QUESTION_NEW_RUN_REQUIRED", status=409, retryable=True)
        return None
