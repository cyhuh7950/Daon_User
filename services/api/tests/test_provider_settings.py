from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import psycopg

from daon_user_api.provider_settings import (
    MODEL_ROLES,
    PROVIDER_CODES,
    ProviderSettingsContext,
    ProviderSettingsError,
    ProviderSettingsService,
    ReferenceProviderSettingsRepository,
    ServerCredentialPresenceResolver,
    ProviderConnectionStatus,
    HttpProviderConnectionChecker,
    PostgresProviderSettingsRepository,
)
from daon_user_api.cloud_storage import PostgresCloudStore


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
        with self.assertRaisesRegex(ProviderSettingsError, "PROVIDER_BASE_URL_INVALID"):
            self.service.save_profile(
                self.context, provider_code="OPENAI",
                base_url="https://169.254.169.254/latest/meta-data",
                active=True, expected_version=0,
            )
        with self.assertRaisesRegex(ProviderSettingsError, "MODEL_ROLE_UNSUPPORTED"):
            self.service.save_deployment(
                self.context, deployment_id="deployment-openai", provider_code="OPENAI",
                model_id="gpt-test", roles=("administrator",), active=True,
                selected=False, expected_version=0,
            )
        self.assertEqual(set(MODEL_ROLES), {"text", "vision", "document_parser", "audio_understanding", "speech_to_text", "embedding", "reranker"})

    def test_connection_check_returns_only_safe_status_and_never_falls_back(self) -> None:
        class Resolver:
            def configured(self, provider_code: str) -> bool:
                return provider_code == "UPSTAGE"
            def resolve(self, provider_code: str) -> str | None:
                return "secret-upstage" if provider_code == "UPSTAGE" else None

        class Checker:
            def __init__(self) -> None:
                self.calls: list[str] = []
            def check(self, profile, credential):
                self.calls.append(profile.provider_code)
                self.assertion = credential == "secret-upstage"
                return ProviderConnectionStatus(profile.provider_code, "ready", "2026-08-14T00:00:00Z")

        checker = Checker()
        service = ProviderSettingsService(ReferenceProviderSettingsRepository(), Resolver(), checker)
        service.save_profile(
            self.context, provider_code="UPSTAGE", base_url="https://api.upstage.ai/v1",
            active=True, expected_version=0,
        )
        status = service.check_connection(self.context, "UPSTAGE")
        self.assertEqual((status.provider_code, status.status), ("UPSTAGE", "ready"))
        self.assertEqual(checker.calls, ["UPSTAGE"])
        self.assertTrue(checker.assertion)
        self.assertNotIn("secret-upstage", repr(status))
        service.save_profile(
            self.context, provider_code="GROQ", base_url="https://api.groq.com/openai/v1",
            active=True, expected_version=0,
        )
        with self.assertRaisesRegex(ProviderSettingsError, "PROVIDER_CREDENTIAL_REQUIRED"):
            service.check_connection(self.context, "GROQ")
        self.assertEqual(checker.calls, ["UPSTAGE"])

    def test_http_connection_checker_uses_fixed_provider_paths_and_never_follows_redirects(self) -> None:
        provider_bases = {
            "CEREBRAS": "https://api.cerebras.ai/v1",
            "GROQ": "https://api.groq.com/openai/v1",
            "MISTRAL": "https://api.mistral.ai/v1",
            "OPENAI": "https://api.openai.com/v1",
            "UPSTAGE": "https://api.upstage.ai/v1",
            "GEMINI": "https://generativelanguage.googleapis.com/v1beta",
            "OPENROUTER": "https://openrouter.ai/api/v1",
            "ANTHROPIC": "https://api.anthropic.com/v1",
            "OLLAMA": "http://ollama.internal:11434",
        }
        expected_paths = [
            "/v1/models", "/openai/v1/models", "/v1/models", "/v1/models",
            "/v1/models", "/v1beta/models", "/api/v1/models", "/v1/models",
            "/api/tags",
        ]
        seen: list[tuple[str, str]] = []

        class Transport:
            def get_status(inner_self, url, headers, timeout_seconds):
                parsed = urlsplit(url)
                seen.append((parsed.hostname or "", parsed.path))
                self.assertNotIn("test-secret", url)
                self.assertEqual(timeout_seconds, 5.0)
                return 200

        checker = HttpProviderConnectionChecker(Transport())
        with patch.dict(os.environ, {"OLLAMA_BASE_URL": provider_bases["OLLAMA"]}, clear=False):
            for code, base_url in provider_bases.items():
                profile = self.service.save_profile(
                    self.context, provider_code=code, base_url=base_url,
                    active=True, expected_version=0,
                )
                status = checker.check(profile, None if code == "OLLAMA" else "test-secret")
                self.assertEqual(status.status, "ready")
        self.assertEqual([path for _host, path in seen], expected_paths)

        class RedirectingTransport:
            def get_status(self, url, headers, timeout_seconds):
                return 302

        redirecting = HttpProviderConnectionChecker(RedirectingTransport())
        with self.assertRaisesRegex(ProviderSettingsError, "PROVIDER_CONNECTION_UNAVAILABLE"):
            redirecting.check(profile, "test-secret")


@unittest.skipUnless(
    os.environ.get("DAON_PROVIDER_TEST_DSN") and os.environ.get("DAON_PROVIDER_ADMIN_DSN"),
    "actual PostgreSQL DSNs absent",
)
class ProviderSettingsPostgresContractTests(unittest.TestCase):
    def test_profile_deployment_binding_and_rls_are_actual(self) -> None:
        dsn = os.environ["DAON_PROVIDER_TEST_DSN"]
        admin_dsn = os.environ["DAON_PROVIDER_ADMIN_DSN"]
        with psycopg.connect(admin_dsn) as connection:
            connection.execute(
                "INSERT INTO tenants (tenant_id,display_name) VALUES "
                "('tenant-provider-it','Provider IT'),('tenant-provider-other','Provider Other')"
            )
            connection.execute(
                "INSERT INTO workspaces (tenant_id,workspace_id,display_name) VALUES "
                "('tenant-provider-it','workspace-provider-it','Provider IT'),"
                "('tenant-provider-other','workspace-provider-other','Provider Other')"
            )
        store = PostgresCloudStore(dsn)
        try:
            service = ProviderSettingsService(
                PostgresProviderSettingsRepository(store), ServerCredentialPresenceResolver(),
            )
            context = ProviderSettingsContext(
                "tenant-provider-it", "workspace-provider-it", "actor-provider-it",
                "trace-provider-it", "policy-provider-it",
            )
            with patch.dict(os.environ, {"UPSTAGE_API_KEY": "actual-test-secret"}, clear=False):
                profile = service.save_profile(
                    context, provider_code="UPSTAGE", base_url="https://api.upstage.ai/v1",
                    active=True, expected_version=0,
                )
                deployment = service.save_deployment(
                    context, deployment_id="deployment-provider-it", provider_code="UPSTAGE",
                    model_id="solar-pro4", roles=("text",), active=True, selected=True,
                    expected_version=0,
                )
                bindings, version = service.save_role_bindings(
                    context, bindings={"text": deployment.deployment_id}, expected_version=0,
                )
                snapshot = service.snapshot(context)
            self.assertEqual(profile.version, 1)
            self.assertEqual((bindings, version), ({"text": "deployment-provider-it"}, 1))
            self.assertEqual(snapshot.role_bindings, bindings)
            self.assertNotIn("actual-test-secret", repr(snapshot))
            other = ProviderSettingsContext(
                "tenant-provider-other", "workspace-provider-other", "actor-provider-other",
                "trace-provider-other", "policy-provider-it",
            )
            self.assertEqual(service.snapshot(other).deployments, ())
            with psycopg.connect(admin_dsn) as connection:
                with self.assertRaises(psycopg.errors.InsufficientPrivilege):
                    with connection.transaction():
                        connection.execute("SET LOCAL ROLE daon_app")
                        connection.execute("SELECT set_config('app.tenant_id','tenant-provider-other',true)")
                        connection.execute("SELECT set_config('app.workspace_id','workspace-provider-other',true)")
                        connection.execute(
                            "INSERT INTO provider_setting_profiles "
                            "(tenant_id,workspace_id,profile_id,provider_code,provider_kind,base_url,active,version,updated_by,policy_version,trace_id) "
                            "VALUES ('tenant-provider-it','workspace-provider-it','cross-write','GROQ','external_api','https://api.groq.com/openai/v1',true,1,'actor','policy','trace')"
                        )
                count = connection.execute(
                    "SELECT count(*) FROM provider_setting_profiles WHERE profile_id='cross-write'"
                ).fetchone()[0]
                self.assertEqual(count, 0)
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
