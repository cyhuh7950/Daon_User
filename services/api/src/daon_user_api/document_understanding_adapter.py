"""Provider-independent original-document understanding with validation-only parsing."""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping, Protocol, cast
from urllib.parse import urlsplit

from .provider_settings import ProviderSettingsSnapshot


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$")
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CREDENTIAL_ENV = {
    "CEREBRAS": "CEREBRAS_API_KEY", "GROQ": "GROQ_API_KEY",
    "MISTRAL": "MISTRAL_API_KEY", "OPENAI": "OPENAI_API_KEY",
    "UPSTAGE": "UPSTAGE_API_KEY", "GEMINI": "GEMINI_API_KEY",
    "OPENROUTER": "OPENROUTER_API_KEY", "ANTHROPIC": "ANTHROPIC_API_KEY",
    "OLLAMA": "OLLAMA_BASE_URL",
}


class DocumentUnderstandingError(RuntimeError):
    def __init__(self, code: str, *, status: int = 400, retryable: bool = False) -> None:
        self.code = code
        self.status = status
        self.retryable = retryable
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DocumentUnderstandingRequest:
    source_id: str
    source_version_id: str
    filename: str
    content: bytes
    trace_id: str
    prompt_version: str
    policy_version: str

    def __post_init__(self) -> None:
        identifiers = (
            self.source_id, self.source_version_id, self.trace_id,
            self.prompt_version, self.policy_version,
        )
        if any(not isinstance(value, str) or not _SAFE_ID.fullmatch(value) for value in identifiers):
            raise DocumentUnderstandingError("UNDERSTANDING_REQUEST_INVALID")
        if (
            not isinstance(self.filename, str) or not self.filename.lower().endswith(".pdf")
            or not isinstance(self.content, bytes) or not self.content.startswith(b"%PDF-")
        ):
            raise DocumentUnderstandingError("UNDERSTANDING_PDF_INVALID")


@dataclass(frozen=True, slots=True)
class DocumentModelSelection:
    provider_code: str
    base_url: str
    semantic_deployment_id: str
    semantic_model_id: str
    parser_deployment_id: str
    parser_model_id: str
    binding_version: int


@dataclass(frozen=True, slots=True)
class SemanticUnderstanding:
    title: str
    summary: str
    key_facts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParserValidation:
    text: str
    markdown: str
    html: str
    pages: tuple[int, ...]
    page_texts: tuple[tuple[int, str], ...] = ()
    role: str = "validation_only"


@dataclass(frozen=True, slots=True)
class DocumentUnderstandingResult:
    source_id: str
    source_version_id: str
    status: str
    substates: tuple[str, ...]
    semantic: SemanticUnderstanding
    parser: ParserValidation
    lineage: Mapping[str, str]
    conflict: str | None = None


class ProviderCredentialResolver(Protocol):
    def resolve(self, provider_code: str) -> str: ...


class ServerProviderCredentialResolver:
    """Resolve secrets only inside the server process; never serialize them."""

    def resolve(self, provider_code: str) -> str:
        variable = _CREDENTIAL_ENV.get(provider_code)
        value = "" if variable is None else os.environ.get(variable, "").strip()
        if not value:
            raise DocumentUnderstandingError("PROVIDER_CREDENTIAL_NOT_CONFIGURED", status=503)
        return value


def resolve_document_model_selection(snapshot: ProviderSettingsSnapshot) -> DocumentModelSelection:
    semantic_id = snapshot.role_bindings.get("vision")
    parser_id = snapshot.role_bindings.get("document_parser")
    if not semantic_id:
        raise DocumentUnderstandingError("UNDERSTANDING_MODEL_NOT_SELECTED", status=409)
    if not parser_id:
        raise DocumentUnderstandingError("DOCUMENT_PARSER_MODEL_NOT_SELECTED", status=409)
    deployments = {item.deployment_id: item for item in snapshot.deployments}
    semantic = deployments.get(semantic_id)
    parser = deployments.get(parser_id)
    if semantic is None or not semantic.active or "vision" not in semantic.roles:
        raise DocumentUnderstandingError("UNDERSTANDING_MODEL_UNAVAILABLE", status=409)
    if parser is None or not parser.active or "document_parser" not in parser.roles:
        raise DocumentUnderstandingError("DOCUMENT_PARSER_MODEL_UNAVAILABLE", status=409)
    if semantic.provider_code != parser.provider_code:
        raise DocumentUnderstandingError("DOCUMENT_MODEL_PROVIDER_MISMATCH", status=409)
    profiles = {item.profile_id: item for item in snapshot.profiles}
    profile = profiles.get(semantic.profile_id)
    if (
        profile is None or profile.profile_id != parser.profile_id or not profile.active
        or not profile.credential_configured
    ):
        raise DocumentUnderstandingError("DOCUMENT_PROVIDER_UNAVAILABLE", status=409)
    return DocumentModelSelection(
        semantic.provider_code, profile.base_url, semantic.deployment_id, semantic.model_id,
        parser.deployment_id, parser.model_id, snapshot.binding_version,
    )


class DocumentUnderstandingTransport(Protocol):
    def post_json(self, *, url: str, api_key: str, payload: dict[str, object], timeout_seconds: float) -> dict[str, object]: ...
    def post_multipart(self, *, url: str, api_key: str, fields: dict[str, str], filename: str, content: bytes, timeout_seconds: float) -> dict[str, object]: ...


class UrlLibDocumentUnderstandingTransport:
    """Bounded HTTPS transport with stable, secret-free upstream errors."""

    @staticmethod
    def _request(request: urllib.request.Request, timeout_seconds: float) -> dict[str, object]:
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            retryable = error.code in {408, 429} or error.code >= 500
            code = "UNDERSTANDING_PROVIDER_UNAVAILABLE" if retryable else "UNDERSTANDING_PROVIDER_REJECTED"
            raise DocumentUnderstandingError(code, status=503 if retryable else 502, retryable=retryable) from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise DocumentUnderstandingError(
                "UNDERSTANDING_PROVIDER_UNAVAILABLE", status=503, retryable=True,
            ) from None
        if len(raw) > _MAX_RESPONSE_BYTES:
            raise DocumentUnderstandingError("UNDERSTANDING_PROVIDER_RESPONSE_TOO_LARGE", status=502)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise DocumentUnderstandingError("UNDERSTANDING_PROVIDER_RESPONSE_INVALID", status=502) from None
        if not isinstance(parsed, dict):
            raise DocumentUnderstandingError("UNDERSTANDING_PROVIDER_RESPONSE_INVALID", status=502)
        return cast(dict[str, object], parsed)

    def post_json(
        self, *, url: str, api_key: str, payload: dict[str, object], timeout_seconds: float,
    ) -> dict[str, object]:
        request = urllib.request.Request(
            url, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        return self._request(request, timeout_seconds)

    def post_multipart(
        self, *, url: str, api_key: str, fields: dict[str, str], filename: str,
        content: bytes, timeout_seconds: float,
    ) -> dict[str, object]:
        boundary = f"----daon-{secrets.token_hex(16)}"
        parts: list[bytes] = []
        for name, value in fields.items():
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
            )
        safe_filename = filename.replace('"', "_").replace("\r", "_").replace("\n", "_")
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="document"; filename="{safe_filename}"\r\nContent-Type: application/pdf\r\n\r\n'.encode("utf-8")
            + content + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode("ascii"))
        request = urllib.request.Request(
            url, data=b"".join(parts),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        return self._request(request, timeout_seconds)


class UpstageDocumentUnderstandingAdapter:
    _SUBSTATES = (
        "vision_llm_understanding", "parser_ocr_validation", "evidence_reconciliation",
    )
    _SCHEMA: dict[str, object] = {
        "type": "json_schema",
        "json_schema": {
            "name": "daon_document_understanding",
            "schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Document title"},
                    "summary": {"type": "string", "description": "Semantic summary"},
                    "key_facts": {
                        "type": "array", "items": {"type": "string"},
                        "description": "Key facts supported by the original document",
                    },
                },
                "required": ["title", "summary", "key_facts"],
                "additionalProperties": False,
            },
        },
    }

    def __init__(
        self, *, transport: DocumentUnderstandingTransport, api_key: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        if not api_key or not 1 <= timeout_seconds <= 120:
            raise DocumentUnderstandingError("UNDERSTANDING_ADAPTER_CONFIG_INVALID")
        self._transport = transport
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds

    def __repr__(self) -> str:
        return f"{type(self).__name__}(credential_configured=True, timeout_seconds={self._timeout_seconds})"

    @staticmethod
    def _base_url(selection: DocumentModelSelection) -> str:
        parsed = urlsplit(selection.base_url)
        if (
            selection.provider_code != "UPSTAGE" or parsed.scheme != "https"
            or parsed.hostname != "api.upstage.ai" or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
        ):
            raise DocumentUnderstandingError("UPSTAGE_ENDPOINT_INVALID", status=409)
        return selection.base_url.rstrip("/")

    @staticmethod
    def _semantic(response: Mapping[str, object]) -> tuple[SemanticUnderstanding, str]:
        try:
            choices = cast(list[object], response["choices"])
            message = cast(dict[str, object], cast(dict[str, object], choices[0])["message"])
            raw_content = message["content"]
            content = json.loads(raw_content) if isinstance(raw_content, str) else raw_content
            value = cast(dict[str, object], content)
            title = str(value["title"]).strip()
            summary = str(value["summary"]).strip()
            facts = tuple(str(item).strip() for item in cast(list[object], value["key_facts"]))
            revision = str(response["model"]).strip()
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError):
            raise DocumentUnderstandingError("UNDERSTANDING_PROVIDER_RESPONSE_INVALID", status=502) from None
        if not title or not summary or not facts or any(not fact for fact in facts) or not revision:
            raise DocumentUnderstandingError("UNDERSTANDING_PROVIDER_RESPONSE_INVALID", status=502)
        return SemanticUnderstanding(title, summary, facts), revision

    @staticmethod
    def _parser(response: Mapping[str, object]) -> tuple[ParserValidation, str]:
        try:
            content = cast(dict[str, object], response["content"])
            text = str(content["text"]).strip()
            markdown = str(content.get("markdown", ""))
            html = str(content.get("html", ""))
            elements = cast(list[object], response["elements"])
            pages = tuple(sorted({
                int(cast(dict[str, object], element)["page"])
                for element in elements if isinstance(element, dict) and "page" in element
            }))
            page_parts: dict[int, list[str]] = {}
            for element in elements:
                if not isinstance(element, dict) or "page" not in element:
                    continue
                element_content = element.get("content")
                if not isinstance(element_content, dict):
                    continue
                part = str(element_content.get("text", "")).strip()
                if part:
                    page_parts.setdefault(int(element["page"]), []).append(part)
            revision = str(response["model"]).strip()
        except (KeyError, TypeError, ValueError):
            raise DocumentUnderstandingError("PARSER_VALIDATION_RESPONSE_INVALID", status=502) from None
        if not text or not pages or any(page < 1 for page in pages) or not revision:
            raise DocumentUnderstandingError("PARSER_VALIDATION_RESPONSE_INVALID", status=502)
        page_texts = (
            ((pages[0], text),)
            if len(pages) == 1
            else tuple(
                (page, "\n".join(page_parts[page]))
                for page in pages if page_parts.get(page)
            )
        )
        return ParserValidation(text, markdown, html, pages, page_texts), revision

    def understand(
        self, request: DocumentUnderstandingRequest, selection: DocumentModelSelection,
    ) -> DocumentUnderstandingResult:
        base_url = self._base_url(selection)
        encoded = base64.b64encode(request.content).decode("ascii")
        semantic_response = self._transport.post_json(
            url=f"{base_url}/information-extraction/chat/completions",
            api_key=self._api_key,
            payload={
                "model": selection.semantic_model_id,
                "messages": [{"role": "user", "content": [{
                    "type": "image_url",
                    "image_url": {"url": f"data:application/pdf;base64,{encoded}"},
                }]}],
                "response_format": self._SCHEMA,
            },
            timeout_seconds=self._timeout_seconds,
        )
        semantic, semantic_revision = self._semantic(semantic_response)
        parser_response = self._transport.post_multipart(
            url=f"{base_url}/document-digitization", api_key=self._api_key,
            fields={"model": selection.parser_model_id, "ocr": "force"},
            filename=request.filename, content=request.content,
            timeout_seconds=self._timeout_seconds,
        )
        parser, parser_revision = self._parser(parser_response)
        normalized_parser = " ".join(parser.text.casefold().split())
        unsupported = tuple(fact for fact in semantic.key_facts if " ".join(fact.casefold().split()) not in normalized_parser)
        conflict = "UNDERSTANDING_PARSER_CONFLICT" if unsupported else None
        return DocumentUnderstandingResult(
            request.source_id, request.source_version_id,
            "needs_review" if conflict else "ready", self._SUBSTATES, semantic, parser,
            {
                "provider_code": selection.provider_code,
                "semantic_deployment_id": selection.semantic_deployment_id,
                "semantic_model_id": selection.semantic_model_id,
                "semantic_model_revision": semantic_revision,
                "parser_deployment_id": selection.parser_deployment_id,
                "parser_model_id": selection.parser_model_id,
                "parser_model_revision": parser_revision,
                "parser_role": "validation_only",
                "binding_version": str(selection.binding_version),
                "prompt_version": request.prompt_version,
                "policy_version": request.policy_version,
                "trace_id": request.trace_id,
            },
            conflict,
        )
