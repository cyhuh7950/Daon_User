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


def test_custom_openai_compatible_endpoint_uses_non_generating_probe() -> None:
    transport = FakeTransport()
    transport.responses["https://models.example.com/v1/models"] = TransportResponse(
        200, {"data": [{"id": "sample-model"}]},
    )
    profile = ProviderConnection(
        "custom-1", "CUSTOM", "My provider", "https://models.example.com/v1",
        None, True, 1,
    )

    result = AdapterRegistry(transport).adapter("CUSTOM").verify(profile, "".join(chr(n) for n in range(97, 101)))

    assert result.status == "ready"
    assert [(item.method, item.url) for item in transport.requests] == [
        ("GET", "https://models.example.com/v1/models")
    ]
    with pytest.raises(ProviderSettingsError, match="^PROVIDER_BASE_URL_INVALID$"):
        validate_provider_base_url("CUSTOM", "http://127.0.0.1:9000/v1")


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

    assert [(request.method, request.url, request.headers) for request in fake_transport.requests] == [
        ("GET", "http://media-bridge.internal:8080/v1/models", {})
    ]
    assert result.status == "ready"


def test_media_bridge_failed_probe_never_returns_verified(fake_transport: FakeTransport) -> None:
    local_endpoint = "http://127.0.0.1:8642/v1/models"
    fake_transport.responses[local_endpoint] = TimeoutError(
        TEST_CREDENTIAL
    )
    adapter = AdapterRegistry(fake_transport).adapter("MEDIA_BRIDGE")

    with pytest.raises(AdapterError, match="^PROVIDER_CATALOG_UNAVAILABLE$") as captured:
        adapter.verify(connection("MEDIA_BRIDGE", base_url="http://127.0.0.1:8642/v1"), None)

    assert len(fake_transport.requests) == 1
    assert fake_transport.requests[0].url == local_endpoint
    assert TEST_CREDENTIAL not in repr(captured.value)


def test_media_bridge_malformed_probe_response_is_not_verified(fake_transport: FakeTransport) -> None:
    fake_transport.responses["http://media-bridge.internal:8080/v1/models"] = TransportResponse(200, {})
    adapter = AdapterRegistry(fake_transport).adapter("MEDIA_BRIDGE")

    with pytest.raises(AdapterError, match="^PROVIDER_CATALOG_RESPONSE_INVALID$"):
        adapter.verify(connection("MEDIA_BRIDGE"), None)


def test_eoul_gateway_empty_discovery_cannot_be_verified(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = FakeTransport()
    adapter = AdapterRegistry(transport, logical_models={"eoul_gateway": ("assistant-default",)}).adapter("EOUL_GATEWAY")
    monkeypatch.setattr(adapter, "discover_models", lambda _connection, _credential: ())

    with pytest.raises(AdapterError, match="^PROVIDER_LOGICAL_MODEL_INVALID$"):
        adapter.verify(connection("EOUL_GATEWAY"), TEST_CREDENTIAL)

    assert transport.requests == []


def test_omniroute_verification_requires_api_key_even_without_provider_models(
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    with pytest.raises(AdapterError, match="^PROVIDER_CREDENTIAL_REQUIRED$"):
        registry.adapter("OMNIROUTE").verify(connection("OMNIROUTE"), None)

    assert fake_transport.requests == []


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


def test_media_bridge_owns_its_models_and_does_not_query_a_remote_catalog(
    registry: AdapterRegistry,
    fake_transport: FakeTransport,
) -> None:
    models = registry.adapter("MEDIA_BRIDGE").discover_models(
        connection("MEDIA_BRIDGE"), TEST_CREDENTIAL
    )

    assert models == ()
    assert fake_transport.requests == []


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


@pytest.mark.parametrize(
    ("adapter_type", "expected_headers"),
    [
        ("openai_compatible", {"authorization": f"Bearer {TEST_CREDENTIAL}"}),
        ("anthropic_compatible", {"x-api-key": TEST_CREDENTIAL, "anthropic-version": "2023-06-01"}),
    ],
)
def test_compatible_catalog_uses_protocol_headers_and_models_path(
    adapter_type: str, expected_headers: dict[str, str], fake_transport: FakeTransport,
) -> None:
    fake_transport.responses["https://models.example/v1/models"] = TransportResponse(
        200, {"data": [{"id": "listed-model"}]},
    )
    profile = connection("CUSTOM", base_url="https://models.example/v1")

    models = AdapterRegistry(fake_transport).adapter("CUSTOM", adapter_type).discover_models(
        profile, TEST_CREDENTIAL,
    )

    assert [item.model_id for item in models] == ["listed-model"]
    assert [(item.method, item.url, item.headers, item.follow_redirects) for item in fake_transport.requests] == [
        ("GET", "https://models.example/v1/models", expected_headers, False),
    ]


@pytest.mark.parametrize(
    ("adapter_type", "probe_path", "probe_payload", "expected_headers"),
    [
        (
            "openai_compatible", "/chat/completions",
            {"choices": [{"message": {"content": "ready"}}]},
            {"authorization": f"Bearer {TEST_CREDENTIAL}"},
        ),
        (
            "anthropic_compatible", "/messages",
            {"content": [{"type": "text", "text": "ready"}]},
            {"x-api-key": TEST_CREDENTIAL, "anthropic-version": "2023-06-01"},
        ),
    ],
)
@pytest.mark.parametrize("catalog_status", [404, 405])
def test_manual_model_works_without_catalog(
    adapter_type: str, probe_path: str, probe_payload: object,
    expected_headers: dict[str, str], catalog_status: int, fake_transport: FakeTransport,
) -> None:
    fake_transport.responses["https://models.example/v1/models"] = TransportResponse(catalog_status, {})
    fake_transport.responses[f"https://models.example/v1{probe_path}"] = TransportResponse(200, probe_payload)
    profile = connection("CUSTOM", base_url="https://models.example/v1")
    adapter = AdapterRegistry(fake_transport).adapter("CUSTOM", adapter_type)

    with pytest.raises(AdapterError):
        adapter.discover_models(profile, TEST_CREDENTIAL)
    models = adapter.verify_models(profile, TEST_CREDENTIAL, ("manual-model",))

    assert [item.model_id for item in models] == ["manual-model"]
    probe = fake_transport.requests[-1]
    assert (probe.method, probe.url, probe.headers) == (
        "POST", f"https://models.example/v1{probe_path}", expected_headers,
    )
    assert probe.timeout_seconds == 5.0 and probe.follow_redirects is False
    assert probe.body is not None
    assert probe.body["model"] == "manual-model"
    assert probe.body["messages"] == [{"role": "user", "content": "connection test"}]
    assert probe.body["max_tokens"] == 16
    assert probe.body["stream"] is False


@pytest.mark.parametrize(
    ("adapter_type", "path", "bad_payload"),
    [
        ("openai_compatible", "/chat/completions", {"choices": [{"message": {"content": ""}}]}),
        ("openai_compatible", "/chat/completions", {"choices": []}),
        ("anthropic_compatible", "/messages", {"content": [{"type": "tool_use", "text": "hidden"}]}),
        ("anthropic_compatible", "/messages", {"content": [{"type": "text", "text": " "}]}),
    ],
)
def test_compatible_probe_rejects_2xx_without_nonempty_text(
    adapter_type: str, path: str, bad_payload: object, fake_transport: FakeTransport,
) -> None:
    fake_transport.responses[f"https://models.example/v1{path}"] = TransportResponse(200, bad_payload)
    adapter = AdapterRegistry(fake_transport).adapter("CUSTOM", adapter_type)

    with pytest.raises(AdapterError, match="^PROVIDER_PROBE_RESPONSE_INVALID$"):
        adapter.verify_models(connection("CUSTOM", base_url="https://models.example/v1"), TEST_CREDENTIAL, ("manual",))

    assert len(fake_transport.requests) == 1


def test_probe_all_models_fails_closed() -> None:
    class SecondModelFails(FakeTransport):
        def request(self, method, url, headers, body, timeout_seconds, *, follow_redirects):
            self.requests.append(RequestRecord(method, url, dict(headers), dict(body) if body else None,
                                               timeout_seconds, follow_redirects))
            if body and body["model"] == "second":
                return TransportResponse(401, {"error": TEST_CREDENTIAL})
            return TransportResponse(200, {"choices": [{"message": {"content": "ready"}}]})

    transport = SecondModelFails()
    adapter = AdapterRegistry(transport).adapter("CUSTOM", "openai_compatible")

    with pytest.raises(AdapterError, match="^PROVIDER_AUTHENTICATION_FAILED$") as captured:
        adapter.verify_models(connection("CUSTOM", base_url="https://models.example/v1"), TEST_CREDENTIAL,
                              ("first", "second"))

    assert [item.body["model"] for item in transport.requests if item.body] == ["first", "second"]
    assert TEST_CREDENTIAL not in repr(captured.value)


@pytest.mark.parametrize(
    ("adapter_type", "path"),
    [("openai_compatible", "/chat/completions"), ("anthropic_compatible", "/messages")],
)
def test_compatible_probe_auth_failure_is_safe(
    adapter_type: str, path: str, fake_transport: FakeTransport,
) -> None:
    fake_transport.responses[f"https://models.example/v1{path}"] = TransportResponse(
        401, {"error": TEST_CREDENTIAL},
    )
    adapter = AdapterRegistry(fake_transport).adapter("CUSTOM", adapter_type)

    with pytest.raises(AdapterError, match="^PROVIDER_AUTHENTICATION_FAILED$") as captured:
        adapter.verify_models(connection("CUSTOM", base_url="https://models.example/v1"),
                              TEST_CREDENTIAL, ("manual",))

    assert len(fake_transport.requests) == 1
    assert TEST_CREDENTIAL not in repr(captured.value)


@pytest.mark.parametrize("status", [302, 307])
def test_compatible_probe_blocks_redirect_without_retry(status: int, fake_transport: FakeTransport) -> None:
    fake_transport.responses["https://models.example/v1/messages"] = TransportResponse(status, {})
    adapter = AdapterRegistry(fake_transport).adapter("CUSTOM", "anthropic_compatible")

    with pytest.raises(AdapterError, match="^PROVIDER_REDIRECT_BLOCKED$"):
        adapter.verify_models(connection("CUSTOM", base_url="https://models.example/v1"), TEST_CREDENTIAL, ("manual",))

    assert len(fake_transport.requests) == 1
    assert fake_transport.requests[0].follow_redirects is False


def test_compatible_probe_timeout_is_safe_and_does_not_retry(fake_transport: FakeTransport) -> None:
    fake_transport.responses["https://models.example/v1/chat/completions"] = TimeoutError(TEST_CREDENTIAL)
    adapter = AdapterRegistry(fake_transport).adapter("CUSTOM", "openai_compatible")

    with pytest.raises(AdapterError, match="^PROVIDER_CATALOG_UNAVAILABLE$") as captured:
        adapter.verify_models(connection("CUSTOM", base_url="https://models.example/v1"), TEST_CREDENTIAL, ("manual",))

    assert len(fake_transport.requests) == 1
    assert TEST_CREDENTIAL not in repr(captured.value)


@pytest.mark.parametrize("base_url", ["http://127.0.0.1:9000/v1", "https://169.254.169.254/v1"])
def test_compatible_probe_rejects_ssrf_before_request(base_url: str, fake_transport: FakeTransport) -> None:
    adapter = AdapterRegistry(fake_transport).adapter("CUSTOM", "anthropic_compatible")

    with pytest.raises(AdapterError, match="^PROVIDER_BASE_URL_INVALID$"):
        adapter.verify_models(connection("CUSTOM", base_url=base_url), TEST_CREDENTIAL, ("manual",))

    assert fake_transport.requests == []


def test_unsupported_adapter_cannot_verify_models(registry: AdapterRegistry) -> None:
    with pytest.raises(AdapterError, match="^PROVIDER_ADAPTER_UNSUPPORTED$"):
        registry.adapter("OPENROUTER").verify_models(connection("OPENROUTER"), TEST_CREDENTIAL, ("manual",))


def test_unknown_custom_protocol_cannot_fall_back_to_openai(registry: AdapterRegistry) -> None:
    with pytest.raises(AdapterError, match="^PROVIDER_ADAPTER_UNSUPPORTED$"):
        registry.adapter("CUSTOM", "unknown_protocol")
