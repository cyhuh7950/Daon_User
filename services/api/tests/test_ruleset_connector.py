from __future__ import annotations

import unittest

from daon_user_api.ruleset_connector import RuleSetConnector, RuleSetError


class RuleSetConnectorTests(unittest.TestCase):
    def test_optional_binding_warns_and_preserves_feature_when_unavailable(self) -> None:
        connector = RuleSetConnector()
        result = connector.evaluate("feature-a", binding_mode="optional", ruleset_id="missing")
        self.assertEqual(result.status, "warn_and_skip")
        self.assertEqual(result.audit_reason, "RULESET_UNAVAILABLE")

    def test_forced_binding_blocks_without_valid_snapshot(self) -> None:
        connector = RuleSetConnector()
        with self.assertRaisesRegex(RuleSetError, "RULESET_UNAVAILABLE"):
            connector.evaluate("feature-a", binding_mode="forced", ruleset_id="missing")

    def test_snapshot_version_and_revoke_are_enforced(self) -> None:
        connector = RuleSetConnector()
        connector.publish("rs-1", version=4, expires_at="2026-12-31T00:00:00Z")
        result = connector.evaluate("feature-a", binding_mode="forced", ruleset_id="rs-1")
        self.assertEqual(result.version, 4)
        connector.revoke("rs-1")
        with self.assertRaisesRegex(RuleSetError, "RULESET_UNAVAILABLE"):
            connector.evaluate("feature-a", binding_mode="forced", ruleset_id="rs-1")


if __name__ == "__main__":
    unittest.main()
