from __future__ import annotations

import unittest

from daon_user_api.model_registry import (
    AdapterUnavailable,
    ModelArtifact,
    ModelBinding,
    ModelDeployment,
    ModelRegistry,
    RegistryModelAdapter,
)


class ModelRegistryContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ModelRegistry()
        self.artifact = ModelArtifact(
            artifact_id="artifact-vision-001",
            digest="sha256:" + "a" * 64,
            contract_version="vision-understanding.v1",
        )
        self.deployment = ModelDeployment(
            deployment_id="deployment-vision-001",
            artifact_id=self.artifact.artifact_id,
            role="vision",
            data_realm="cloud_sync",
        )

    def test_ready_deployment_requires_health_and_digest(self) -> None:
        self.registry.register_artifact(self.artifact)
        self.registry.register_deployment(self.deployment)
        with self.assertRaises(ValueError):
            self.registry.mark_ready(self.deployment.deployment_id, health_ok=False)
        ready = self.registry.mark_ready(self.deployment.deployment_id, health_ok=True)
        self.assertEqual(ready.status, "ready")
        self.assertEqual(ready.artifact_digest, self.artifact.digest)

    def test_binding_rejects_unhealthy_or_wrong_realm(self) -> None:
        self.registry.register_artifact(self.artifact)
        self.registry.register_deployment(self.deployment)
        self.registry.mark_ready(self.deployment.deployment_id, health_ok=True)
        binding = ModelBinding(
            binding_id="binding-001",
            workspace_id="workspace-001",
            deployment_id=self.deployment.deployment_id,
            role="vision",
            allowed_data_realm="local_private",
        )
        with self.assertRaises(ValueError):
            self.registry.bind(binding)

    def test_adapter_fail_closes_without_available_provider(self) -> None:
        self.registry.register_artifact(self.artifact)
        self.registry.register_deployment(self.deployment)
        self.registry.mark_ready(self.deployment.deployment_id, health_ok=True)
        binding = ModelBinding(
            binding_id="binding-001",
            workspace_id="workspace-001",
            deployment_id=self.deployment.deployment_id,
            role="vision",
            allowed_data_realm="cloud_sync",
        )
        self.registry.bind(binding)
        with self.assertRaises(AdapterUnavailable) as error:
            RegistryModelAdapter(self.registry).understand(binding.binding_id, b"pdf")
        self.assertEqual(error.exception.code, "NO_AVAILABLE_DEPLOYMENT")


if __name__ == "__main__":
    unittest.main()
