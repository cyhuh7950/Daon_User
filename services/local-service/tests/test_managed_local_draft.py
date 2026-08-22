from __future__ import annotations

import inspect

from daon_user_local_service import managed_local_draft
from daon_user_local_service.provider_draft import (
    DraftGenerationPort,
    OllamaDraftGenerationAdapter,
    OllamaModelCatalog,
    ProviderModelDescriptor,
)


def test_legacy_module_is_a_non_executable_provider_compatibility_boundary() -> None:
    source = inspect.getsource(managed_local_draft)

    assert managed_local_draft.ManagedModelDescriptor is ProviderModelDescriptor
    assert managed_local_draft.ManagedModelCatalog is OllamaModelCatalog
    assert managed_local_draft.ManagedLocalDraftGenerator is OllamaDraftGenerationAdapter
    assert managed_local_draft.LocalDraftGeneratorPort is DraftGenerationPort
    assert all(value not in source for value in ("Popen", "taskkill", "shell=False"))


def test_fixture_programs_are_not_referenced_by_product_compatibility_module() -> None:
    source = inspect.getsource(managed_local_draft)

    assert "fixtures" not in source
    assert "managed_model_fixture" not in source
