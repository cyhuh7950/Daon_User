from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


class SecurityAuditPersistenceContractTests(unittest.TestCase):
    def test_dedicated_postgres_security_audit_store_preserves_full_event_contract(self) -> None:
        from daon_user_api.audit import PostgresSecurityAuditStore

        for method_name in ("append", "read", "list", "verify_integrity", "close"):
            self.assertTrue(
                callable(getattr(PostgresSecurityAuditStore, method_name, None)),
                method_name,
            )

    def test_migration_is_append_only_rls_scoped_and_downgrade_safe(self) -> None:
        source = (
            ROOT
            / "services/api/migrations/versions/0015_security_audit_step_up_idempotency.py"
        ).read_text(encoding="utf-8")
        for token in (
            'revision = "0015"',
            'down_revision = "0014"',
            "CREATE TABLE security_audit_events",
            "FORCE ROW LEVEL SECURITY",
            "security_audit_events_immutable",
            "SECURITY_AUDIT_DOWNGRADE_BLOCKED",
            "REVOKE UPDATE, DELETE",
        ):
            self.assertIn(token, source)
        self.assertNotIn("CREATE TABLE step_up_idempotency_ledger", source)
        identity_source = (ROOT / "services/api/src/daon_user_api/identity.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("CREATE TABLE IF NOT EXISTS step_up_idempotency", identity_source)
        self.assertIn("CREATE TABLE IF NOT EXISTS step_up_consumptions", identity_source)

    def test_all_runtime_sensitive_mutations_bind_step_up_to_idempotency(self) -> None:
        source = (ROOT / "services/api/src/daon_user_api/runtime.py").read_text(
            encoding="utf-8"
        )
        for operation in (
            "question.external_transfer",
            "studio.{action_name}",
            "knowledge.offline_copy",
            "sync.approve",
            "retention.purge",
            "retention.legal_hold.apply",
            "retention.legal_hold.release",
            "egress_policy.{scope_type}.activate",
            "recovery.restore_preview",
            "recovery.restore_execute",
        ):
            self.assertIn(f'operation=f"{operation}"' if "{" in operation else f'operation="{operation}"', source)


if __name__ == "__main__":
    unittest.main()
