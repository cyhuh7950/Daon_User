from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
from typing import Callable, Final


_DIGEST: Final = re.compile(r"^sha256:[0-9a-f]{64}$")


class AdapterUnavailable(RuntimeError):
    def __init__(self, code: str, message: str = "model deployment unavailable") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ModelArtifact:
    artifact_id: str
    digest: str
    contract_version: str


@dataclass(frozen=True)
class ModelDeployment:
    deployment_id: str
    artifact_id: str
    role: str
    data_realm: str
    status: str = "registered"
    artifact_digest: str | None = None
    health_checked_at: str | None = None


@dataclass(frozen=True)
class ModelBinding:
    binding_id: str
    workspace_id: str
    deployment_id: str
    role: str
    allowed_data_realm: str


class ModelRegistry:
    def __init__(self) -> None:
        self._artifacts: dict[str, ModelArtifact] = {}
        self._deployments: dict[str, ModelDeployment] = {}
        self._bindings: dict[str, ModelBinding] = {}

    def register_artifact(self, artifact: ModelArtifact) -> ModelArtifact:
        if not _DIGEST.fullmatch(artifact.digest):
            raise ValueError("invalid model artifact digest")
        self._artifacts[artifact.artifact_id] = artifact
        return artifact

    def register_deployment(self, deployment: ModelDeployment) -> ModelDeployment:
        artifact = self._artifacts.get(deployment.artifact_id)
        if artifact is None or deployment.role not in {"vision", "document_input"}:
            raise ValueError("model deployment is not approved")
        if deployment.data_realm not in {"local_private", "cloud_sync"}:
            raise ValueError("unsupported model data realm")
        stored = replace(deployment, status="validating", artifact_digest=artifact.digest)
        self._deployments[deployment.deployment_id] = stored
        return stored

    def mark_ready(self, deployment_id: str, *, health_ok: bool) -> ModelDeployment:
        deployment = self._deployments[deployment_id]
        if not health_ok or not deployment.artifact_digest:
            failed = replace(deployment, status="unhealthy")
            self._deployments[deployment_id] = failed
            raise ValueError("model deployment health check failed")
        ready = replace(
            deployment,
            status="ready",
            health_checked_at=datetime.now(timezone.utc).isoformat(),
        )
        self._deployments[deployment_id] = ready
        return ready

    def bind(self, binding: ModelBinding) -> ModelBinding:
        deployment = self._deployments.get(binding.deployment_id)
        if deployment is None or deployment.status != "ready":
            raise ValueError("model deployment is not ready")
        if deployment.role != binding.role or deployment.data_realm != binding.allowed_data_realm:
            raise ValueError("model binding data realm or role mismatch")
        self._bindings[binding.binding_id] = binding
        return binding

    def resolve_binding(self, binding_id: str) -> tuple[ModelBinding, ModelDeployment]:
        binding = self._bindings[binding_id]
        deployment = self._deployments[binding.deployment_id]
        if deployment.status != "ready" or not deployment.artifact_digest:
            raise AdapterUnavailable("NO_AVAILABLE_DEPLOYMENT")
        return binding, deployment


class RegistryModelAdapter:
    def __init__(self, registry: ModelRegistry, handler: Callable[[bytes], object] | None = None) -> None:
        self._registry = registry
        self._handler = handler

    def understand(self, binding_id: str, payload: bytes) -> dict[str, object]:
        _binding, deployment = self._registry.resolve_binding(binding_id)
        if not payload:
            raise ValueError("model input must not be empty")
        if self._handler is None:
            raise AdapterUnavailable("NO_AVAILABLE_DEPLOYMENT")
        return {
            "deployment_id": deployment.deployment_id,
            "artifact_digest": deployment.artifact_digest,
            "input_size": len(payload),
            "output": self._handler(payload),
        }
