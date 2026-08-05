from __future__ import annotations

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[3]


class ProviderSettingsSurfaceContractTests(unittest.TestCase):
    def test_migration_0007_has_rls_and_no_secret_columns(self) -> None:
        migration = (ROOT / "services/api/migrations/versions/0007_provider_model_settings.py").read_text("utf-8")
        for table in ("provider_profiles", "model_deployments", "model_role_bindings"):
            self.assertIn(f"CREATE TABLE {table}", migration)
            self.assertIn(f'"{table}"', migration)
        self.assertIn('f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"', migration)
        self.assertIn('f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"', migration)
        self.assertIn("current_setting('app.tenant_id', true)", migration)
        self.assertIn("current_setting('app.workspace_id', true)", migration)
        lowered = migration.lower()
        for forbidden in ("api_key", "secret_value", "access_token"):
            self.assertNotIn(forbidden, lowered)

    def test_openapi_exposes_specific_safe_provider_contract(self) -> None:
        document = json.loads((ROOT / "packages/contracts/openapi/v1/openapi.json").read_text("utf-8"))
        paths = document["paths"]
        self.assertIn("post", paths["/api/v1/model-deployments"])
        self.assertEqual(paths["/api/v1/model-profiles"]["get"]["x-implementation-owner"], "R1-M6-10-C01")
        schemas = document["components"]["schemas"]
        rendered = json.dumps({key: value for key, value in schemas.items() if key.startswith("Provider") or key.startswith("Model")})
        self.assertIn("credential_configured", rendered)
        for forbidden in ("api_key", "secret_value", "access_token"):
            self.assertNotIn(forbidden, rendered.lower())


if __name__ == "__main__":
    unittest.main()
