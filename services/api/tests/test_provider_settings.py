from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from daon_user_api.provider_settings import (
    MODEL_ROLES,
    PROVIDER_CODES,
    ProviderSettingsContext,
    ProviderSettingsError,
    ProviderSettingsService,
    ReferenceProviderSettingsRepository,
    ServerCredentialPresenceResolver,
)


class ProviderSettingsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = ProviderSettingsContext(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            actor_id="admin-a",
            trace_id="trace-provider-a",
            policy_version="policy-v1",
        )
        self.service = ProviderSettingsService(
            ReferenceProviderSettingsRepository(),
            ServerCredentialPresenceResolver(),
        )

    def test_snapshot_contains_all_approved_providers_without_secret_values(self) -> None:
        secret = "must-never-leave-the-server"
        with patch.dict(os.environ, {"OPENAI_API_KEY": secret}, clear=False):
            snapshot = self.service.snapshot(self.context)
        self.assertEqual({item.provider_code for item in snapshot.profiles}, set(PROVIDER_CODES))
        openai = next(item for item in snapshot.profiles if item.provider_code == "OPENAI")
        self.assertTrue(openai.credential_configured)
        self.assertNotIn(secret, repr(snapshot))
        self.assertFalse(any(hasattr(item, "api_key") or hasattr(item, "secret") for item in snapshot.profiles))

    def test_ollama_configuration_uses_the_approved_server_environment_name(self) -> None:
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": "http://ollama.internal:11434"}, clear=False):
            snapshot = self.service.snapshot(self.context)
        ollama = next(item for item in snapshot.profiles if item.provider_code == "OLLAMA")
        self.assertTrue(ollama.credential_configured)

    def test_profile_deployment_and_role_bindings_are_persisted_and_tenant_scoped(self) -> None:
        self.service.save_profile(
            self.context, provider_code="UPSTAGE", base_url="https://api.upstage.ai/v1",
            active=True, expected_version=0,
        )
        deployment = self.service.save_deployment(
            self.context, deployment_id="deployment-upstage-solar",
            provider_code="UPSTAGE", model_id="solar-pro3",
            roles=("text", "vision"), active=True, selected=True, expected_version=0,
        )
        self.service.save_role_bindings(
            self.context,
            bindings={"text": deployment.deployment_id, "vision": deployment.deployment_id},
            expected_version=0,
        )
        snapshot = self.service.snapshot(self.context)
        self.assertEqual(snapshot.role_bindings, {"text": deployment.deployment_id, "vision": deployment.deployment_id})
        self.assertEqual(snapshot.deployments[0].model_id, "solar-pro3")
        other = ProviderSettingsContext(
            tenant_id="tenant-b", workspace_id="workspace-a", actor_id="admin-b",
            trace_id="trace-provider-b", policy_version="policy-v1",
        )
        self.assertEqual(self.service.snapshot(other).deployments, ())
        self.assertEqual(self.service.snapshot(other).role_bindings, {})

    def test_invalid_provider_role_and_version_fail_closed(self) -> None:
        with self.assertRaisesRegex(ProviderSettingsError, "PROVIDER_CODE_UNSUPPORTED"):
            self.service.save_profile(
                self.context, provider_code="UNKNOWN", base_url="https://example.invalid",
                active=True, expected_version=0,
            )
        self.service.save_profile(
            self.context, provider_code="OPENAI", base_url="https://api.openai.com/v1",
            active=True, expected_version=0,
        )
        with self.assertRaisesRegex(ProviderSettingsError, "MODEL_ROLE_UNSUPPORTED"):
            self.service.save_deployment(
                self.context, deployment_id="deployment-openai", provider_code="OPENAI",
                model_id="gpt-test", roles=("administrator",), active=True,
                selected=False, expected_version=0,
            )
        self.assertEqual(set(MODEL_ROLES), {"text", "vision", "document_parser", "audio_understanding", "speech_to_text", "embedding", "reranker"})


if __name__ == "__main__":
    unittest.main()
