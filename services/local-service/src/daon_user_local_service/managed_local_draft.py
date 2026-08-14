"""Compatibility imports for the common Provider draft boundary.

The product model lifecycle is owned by configured Provider deployments. Ollama
models are discovered and invoked through its internal HTTP API.
"""

from .knowledge_context import OfflineStudioError
from .provider_draft import (
    DraftGenerationPort,
    LocalDraftGeneratorPort,
    ManagedModelDescriptor,
    ModelCatalogPort,
    ModelSelectionSnapshot,
    OllamaDraftGenerationAdapter,
    OllamaModelCatalog,
    ProviderModelDescriptor,
)


ManagedModelCatalog = OllamaModelCatalog
ManagedLocalDraftGenerator = OllamaDraftGenerationAdapter

__all__ = (
    "DraftGenerationPort",
    "LocalDraftGeneratorPort",
    "ManagedLocalDraftGenerator",
    "ManagedModelCatalog",
    "ManagedModelDescriptor",
    "ModelCatalogPort",
    "ModelSelectionSnapshot",
    "OfflineStudioError",
    "OllamaDraftGenerationAdapter",
    "OllamaModelCatalog",
    "ProviderModelDescriptor",
)
