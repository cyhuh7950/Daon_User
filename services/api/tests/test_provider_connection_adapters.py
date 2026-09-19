from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pytest

from daon_user_api.provider_connection_adapters import (
    AdapterError,
    AdapterRegistry,
    TransportResponse,
)
from daon_user_api.provider_credentials import ProviderConnection
from daon_user_api.provider_settings import ProviderSettingsError, validate_provider_base_url


TEST_CREDENTIAL = "fixture-credential-never-real"


def connection(
    provider_code: str,
    *,
    connection_id: str | None = None,
    base_url: str | None = None,
) -> ProviderConnection:
    defaults = {
        "GROQ": "https://api.groq.com/openai/v1",
        "MISTRAL": "https://api.mistral.ai/v1",
        "UPSTAGE": "https://api.upstage.ai/v1",
        "OLLAMA": "http://192.168.220.180:11434",
        "OPENROUTER": "https://openrouter.ai/api/v1",
        "OMNIROUTE": "https://omniroute.example/v1",
        "EOUL_GATEWAY": "http://eoul-gateway:8080",
        "MEDIA_BRIDGE": "http://media-bridge.internal:8080/v1",
        "SENTENCE_TRANSFORMERS": "http://sentence-transformers:8080",
    }
    return ProviderConnection(
        connection_id=connection_id or provider_code.lower(),
        provider_code=provider_code,
        display_name=connection_id or provider_code,
        base_url=base_url or defaults[provider_code],
        credential=None,
        enabled=True,
        version=1,
    )


@dataclass(frozen=True)
class RequestRecord:
    method: str
    url: str
    headers: Mapping[str, str]
    body: Mapping[str, object] | None
    timeout_seconds: float
    follow_redirects: bool


class FakeTransport:
    def __init__(self) -> None:
        self.requests: list[RequestRecord] = []
        self.responses: dict[str, TransportResponse | Exception] = {
            "http://192.168.220.180:11434/api/tags": TransportResponse(
                200,
                {"models": [{"name": "qwen3"}]},
            ),
            "https://ollama-api.sinsan.kr/api/tags": TransportResponse(
                200,
                {"models": [{"name": "qwen3"}]},
            ),
            "https://openrouter.ai/api/v1/models": TransportResponse(
                200,
                {
                    "data": [
                        {
                            "id": "openai/gpt-4.1",
                            "architecture": {
                                "input_modalities": ["text", "image"],
                                "output_modalities": ["text"],
                            },
                        }
                    ]
                },
            ),
            "https://api.groq.com/openai/v1/models": TransportResponse(200, {"data": [{"id": "llama-3.3-70b-versatile"}]}),
            "https://api.mistral.ai/v1/models": TransportResponse(200, {"data": [{"id": "mistral-large-latest"}]}),
            "https://api.upstage.ai/v1/models": TransportResponse(200, {"data": [{"id": "solar-pro3"}]}),
            "https://omniroute.example/v1/responses": TransportResponse(200, {}),
            "http://eoul-gateway:8080/v1/chat/completions": TransportResponse(200, {}),
            "http://media-bridge.internal:8080/v1/models": TransportResponse(
                200, {"data": [{"id": "media-bridge-vision"}]}
            ),
        }

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
        self.requests.append(
            RequestRecord(
                method,
                url,
                dict(headers),
                dict(body) if body is not None else None,
                timeout_seconds,
                follow_redirects,
            )
        )
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def fake_transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def registry(fake_transport: FakeTransport) -> AdapterRegistry:
    return AdapterRegistry(
        fake_transport,
        logical_models={
            "omniroute": ("assistant-route",),
            "eoul_gateway": ("assistant-default",),
        },
    )


@pytest.mark.parametrize("code", ["OPENROUTER", "OMNIROUTE", "EOUL_GATEWAY", "MEDIA_BRIDGE"])
def test_gateway_credentials_never_escape_safe_result(
    code: str,
    registry: AdapterRegistry,
) -> None:
    result = registry.adapter(code).verify(connection(code), TEST_CREDENTIAL)

    assert result.status == "ready"
    assert TEST_CREDENTIAL not in repr(result)


def test_ollama_catalog_is_scoped_by_named_connection(
    registry: AdapterRegistry,
) -> None:
    left = registry.adapter("OLLAMA").discover_models(
        connection("OLLAMA", connection_id="lan"),
        None,
    )
    right = registry.adapter("OLLAMA").discover_models(
        connection(
            "OLLAMA",
            connection_id="public",
            base_url="https://ollama-api.sinsan.kr",
        ),
        None,
    )

    assert {(model.connection_id, model.model_id) for model in left + right} == {
        ("lan", "qwen3"),
        ("public", "qwen3"),
    }


def test_media_bridge_catalog_and_verification_do_not_require_api_key(
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    result = registry.adapter("MEDIA_BRIDGE").verify(connection("MEDIA_BRIDGE"), None)

    assert fake_transport.requests == []
    assert result.status == "ready"


def test_omniroute_verification_does_not_require_provider_models_or_api_key(
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    result = registry.adapter("OMNIROUTE").verify(connection("OMNIROUTE"), None)

    assert fake_transport.requests == []
    assert result.status == "ready"


@pytest.mark.parametrize(
    ("code", "expected_url", "expected_model"),
    [
        ("OMNIROUTE", "https://omniroute.example/v1/responses", "assistant-route"),
        ("EOUL_GATEWAY", "http://eoul-gateway:8080/v1/chat/completions", "assistant-default"),
    ],
)
def test_routing_gateway_uses_data_api_logical_model_and_owns_fallback(
    code: str,
    expected_url: str,
    expected_model: str,
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    adapter = registry.adapter(code)

    result = adapter.verify(connection(code), TEST_CREDENTIAL)

    request = fake_transport.requests[-1]
    assert request.method == "POST"
    assert request.url == expected_url
    assert request.body is not None and request.body["model"] == expected_model
    assert request.headers == {"authorization": f"Bearer {TEST_CREDENTIAL}"}
    assert "cookie" not in {name.lower() for name in request.headers}
    assert request.timeout_seconds == 5.0
    assert request.follow_redirects is False
    assert result.routing_owner == "gateway"
    assert result.daon_fallback_allowed is False


def test_openrouter_catalog_probe_is_bounded_and_does_not_follow_redirects(
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    models = registry.adapter("OPENROUTER").discover_models(
        connection("OPENROUTER"),
        TEST_CREDENTIAL,
    )

    request = fake_transport.requests[-1]
    assert request.url == "https://openrouter.ai/api/v1/models"
    assert request.timeout_seconds == 5.0
    assert request.follow_redirects is False
    assert models[0].model_id == "openai/gpt-4.1"


def test_media_bridge_is_an_openai_compatible_named_connection(
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    models = registry.adapter("MEDIA_BRIDGE").discover_models(
        connection("MEDIA_BRIDGE"), TEST_CREDENTIAL
    )

    assert models[0].model_id == "media-bridge-vision"
    assert fake_transport.requests[-1].url == "http://media-bridge.internal:8080/v1/models"
    assert fake_transport.requests[-1].headers == {
        "authorization": f"Bearer {TEST_CREDENTIAL}"
    }


@pytest.mark.parametrize(
    ("code", "endpoint"),
    [
        ("GROQ", "https://api.groq.com/openai/v1/models"),
        ("MISTRAL", "https://api.mistral.ai/v1/models"),
        ("UPSTAGE", "https://api.upstage.ai/v1/models"),
    ],
)
def test_migrated_provider_credential_verification_uses_fixed_official_endpoint(
    code: str, endpoint: str, registry: AdapterRegistry, fake_transport: FakeTransport,
) -> None:
    result = registry.adapter(code).verify(connection(code), TEST_CREDENTIAL)
    assert result.status == "ready"
    assert fake_transport.requests[-1].url == endpoint
    assert fake_transport.requests[-1].headers == {"authorization": f"Bearer {TEST_CREDENTIAL}"}
    with pytest.raises(AdapterError, match="^PROVIDER_BASE_URL_INVALID$"):
        registry.adapter(code).verify(connection(code, base_url="https://attacker.example/v1"), TEST_CREDENTIAL)


@pytest.mark.parametrize(
    ("status", "safe_code"),
    [
        (302, "PROVIDER_REDIRECT_BLOCKED"),
        (401, "PROVIDER_AUTHENTICATION_FAILED"),
        (429, "PROVIDER_RATE_LIMITED"),
        (503, "PROVIDER_CATALOG_UNAVAILABLE"),
    ],
)
def test_adapter_maps_upstream_status_to_safe_error_without_secret(
    status: int,
    safe_code: str,
    fake_transport: FakeTransport,
) -> None:
    fake_transport.responses["https://openrouter.ai/api/v1/models"] = TransportResponse(
        status,
        {"error": TEST_CREDENTIAL},
    )
    adapter = AdapterRegistry(fake_transport).adapter("OPENROUTER")

    with pytest.raises(AdapterError, match=f"^{safe_code}$") as captured:
        adapter.verify(connection("OPENROUTER"), TEST_CREDENTIAL)

    assert TEST_CREDENTIAL not in repr(captured.value)


def test_transport_timeout_is_fail_closed_without_secret(
    fake_transport: FakeTransport,
) -> None:
    fake_transport.responses["https://openrouter.ai/api/v1/models"] = TimeoutError(
        TEST_CREDENTIAL
    )
    adapter = AdapterRegistry(fake_transport).adapter("OPENROUTER")

    with pytest.raises(AdapterError, match="^PROVIDER_CATALOG_UNAVAILABLE$") as captured:
        adapter.verify(connection("OPENROUTER"), TEST_CREDENTIAL)

    assert TEST_CREDENTIAL not in repr(captured.value)


def test_endpoint_validation_allows_named_ollama_and_internal_gateway_but_blocks_ssrf() -> None:
    assert (
        validate_provider_base_url("OLLAMA", "http://192.168.220.180:11434")
        == "http://192.168.220.180:11434"
    )
    assert (
        validate_provider_base_url("OLLAMA", "https://ollama-api.sinsan.kr/")
        == "https://ollama-api.sinsan.kr"
    )
    assert (
        validate_provider_base_url("EOUL_GATEWAY", "http://eoul-gateway:8080")
        == "http://eoul-gateway:8080"
    )
    assert (
        validate_provider_base_url("MEDIA_BRIDGE", "http://127.0.0.1:8642/v1")
        == "http://127.0.0.1:8642/v1"
    )
    for value in (
        "http://127.0.0.1:11434",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.220.180:0",
        "http://192.168.220.180:bad",
        "http://gateway.example.com",
        "http://user:password@ollama.internal:11434",
        "https://gateway.example/path?client_key=value",
    ):
        with pytest.raises(ProviderSettingsError, match="^PROVIDER_BASE_URL_INVALID$"):
            validate_provider_base_url("OLLAMA", value)


def test_sentence_transformers_is_a_local_logical_model_provider() -> None:
    adapter = AdapterRegistry(
        logical_models={"sentence-local": ("all-MiniLM-L6-v2",)},
    ).adapter("SENTENCE_TRANSFORMERS")

    connection_value = connection("SENTENCE_TRANSFORMERS", connection_id="sentence-local")
    assert adapter.verify(connection_value, None).routing_owner == "local_runtime"
    assert adapter.discover_models(connection_value, None)[0].model_id == "all-MiniLM-L6-v2"
