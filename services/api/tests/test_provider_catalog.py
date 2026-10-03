from __future__ import annotations

import pytest

from daon_user_api.provider_catalog import ProviderCatalog, ProviderCatalogError


def test_ollama_catalog_preserves_connection_scope_and_normalizes_duplicate_models() -> None:
    models = ProviderCatalog.from_payload(
        "ollama-lan",
        "OLLAMA",
        {"models": [{"name": "qwen3"}, {"model": "qwen3"}]},
    )

    assert [(model.connection_id, model.provider_code, model.model_id) for model in models] == [
        ("ollama-lan", "OLLAMA", "qwen3")
    ]
    assert models[0].reported_capabilities == ("text_generation",)


def test_openrouter_catalog_uses_provider_modalities_without_model_name_guessing() -> None:
    models = ProviderCatalog.from_payload(
        "openrouter-primary",
        "OPENROUTER",
        {
            "data": [
                {
                    "id": "vendor/opaque-model-id",
                    "architecture": {
                        "input_modalities": ["text", "image"],
                        "output_modalities": ["text"],
                    },
                }
            ]
        },
    )

    assert models[0].model_id == "vendor/opaque-model-id"
    assert models[0].reported_capabilities == (
        "image_understanding",
        "text_generation",
    )


def test_openrouter_catalog_accepts_non_secret_tokenizer_metadata() -> None:
    models = ProviderCatalog.from_payload(
        "openrouter-primary",
        "OPENROUTER",
        {
            "data": [
                {
                    "id": "vendor/text-model",
                    "tokenizer": "ExampleTokenizer",
                    "supported_parameters": ["max_tokens"],
                    "architecture": {
                        "input_modalities": ["text"],
                        "output_modalities": ["text"],
                    },
                }
            ]
        },
    )

    assert models[0].model_id == "vendor/text-model"


@pytest.mark.parametrize("code", ["OMNIROUTE", "EOUL_GATEWAY"])
def test_gateway_logical_models_are_explicit_and_never_provider_native(
    code: str,
) -> None:
    models = ProviderCatalog.from_logical_models(
        "gateway-primary",
        code,
        ("assistant-default",),
    )

    assert [(model.model_id, model.routing_owner) for model in models] == [
        ("assistant-default", "gateway")
    ]
    assert models[0].daon_fallback_allowed is False


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"models": "not-a-list"},
        {"models": [{"name": ""}]},
        {"models": [{"name": "qwen3", "unexpected_secret": "fixture-value"}]},
    ],
)
def test_malformed_ollama_catalog_fails_closed_with_safe_error(payload: object) -> None:
    with pytest.raises(
        ProviderCatalogError,
        match="^PROVIDER_CATALOG_RESPONSE_INVALID$",
    ) as captured:
        ProviderCatalog.from_payload("ollama-lan", "OLLAMA", payload)

    assert "fixture-value" not in repr(captured.value)


def test_duplicate_logical_model_is_rejected_instead_of_silently_reordered() -> None:
    with pytest.raises(
        ProviderCatalogError,
        match="^PROVIDER_LOGICAL_MODEL_INVALID$",
    ):
        ProviderCatalog.from_logical_models(
            "eoul-primary",
            "EOUL_GATEWAY",
            ("assistant-default", "assistant-default"),
        )


def test_verified_manual_models_are_scoped_text_models_in_given_order() -> None:
    models = ProviderCatalog.from_verified_text_models(
        "custom-primary", "CUSTOM", ("manual-b", "manual-a"),
    )

    assert [(item.connection_id, item.provider_code, item.model_id) for item in models] == [
        ("custom-primary", "CUSTOM", "manual-b"),
        ("custom-primary", "CUSTOM", "manual-a"),
    ]
    assert all(item.reported_capabilities == ("text_generation",) for item in models)
    assert all(item.routing_owner == "provider" and item.daon_fallback_allowed for item in models)


@pytest.mark.parametrize("model_ids", [(), ("",), ("manual id",), ("same", "same")])
def test_verified_manual_models_reject_empty_invalid_or_duplicate_ids(model_ids: tuple[str, ...]) -> None:
    with pytest.raises(ProviderCatalogError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        ProviderCatalog.from_verified_text_models("custom-primary", "CUSTOM", model_ids)


def test_verified_manual_models_reject_non_custom_provider() -> None:
    with pytest.raises(ProviderCatalogError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        ProviderCatalog.from_verified_text_models("router", "OPENROUTER", ("manual",))


def test_verified_manual_models_limit_probe_count() -> None:
    model_ids = tuple(f"manual-{index}" for index in range(5))
    with pytest.raises(ProviderCatalogError, match="^PROVIDER_MODEL_IDS_INVALID$"):
        ProviderCatalog.from_verified_text_models("custom-primary", "CUSTOM", model_ids)

    assert len(ProviderCatalog.from_verified_text_models(
        "custom-primary", "CUSTOM", model_ids[:4],
    )) == 4
