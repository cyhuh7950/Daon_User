from __future__ import annotations

import unittest

from daon_user_api.routing import (
    CandidateDeployment,
    RoutingContext,
    route_single_model,
)


class RoutingEgressTests(unittest.TestCase):
    def context(self, **changes: object) -> RoutingContext:
        values = {
            "actor_id": "actor-001",
            "tenant_id": "tenant-001",
            "workspace_id": "workspace-001",
            "mode": "pinned",
            "required_role": "vision",
            "data_realm": "cloud_sync",
            "external_egress_allowed": True,
            "policy_version": "policy-001",
            "cost_limit": 1.0,
            "estimated_cost": 0.2,
            "payload_bytes": 128,
        }
        values.update(changes)
        return RoutingContext(**values)

    def candidate(self, **changes: object) -> CandidateDeployment:
        values = {
            "deployment_id": "deployment-vision-001",
            "artifact_digest": "sha256:" + "a" * 64,
            "role": "vision",
            "data_realm": "cloud_sync",
            "health": "ready",
            "provider_kind": "server_internal",
        }
        values.update(changes)
        return CandidateDeployment(**values)

    def test_local_private_blocks_external_egress(self) -> None:
        result = route_single_model(
            self.context(data_realm="local_private", external_egress_allowed=False),
            [self.candidate(provider_kind="external_api")],
        )
        self.assertEqual(result.status, "policy_blocked")
        self.assertEqual(result.code, "EXTERNAL_EGRESS_DENIED")

    def test_cost_limit_blocks_before_attempt(self) -> None:
        result = route_single_model(
            self.context(estimated_cost=1.1),
            [self.candidate()],
        )
        self.assertEqual(result.status, "policy_blocked")
        self.assertEqual(result.code, "COST_LIMIT_EXCEEDED")
        self.assertIsNone(result.deployment_id)

    def test_frozen_pinned_route_does_not_fallback(self) -> None:
        result = route_single_model(
            self.context(mode="pinned"),
            [self.candidate(health="unhealthy"), self.candidate(deployment_id="deployment-vision-002")],
        )
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.code, "NO_AVAILABLE_DEPLOYMENT")

    def test_allowed_route_records_egress_and_attempt_lineage(self) -> None:
        result = route_single_model(self.context(), [self.candidate()])
        self.assertEqual(result.status, "selected")
        self.assertEqual(result.deployment_id, "deployment-vision-001")
        self.assertEqual(result.egress.destination, "server_internal")
        self.assertEqual(result.egress.payload_bytes, 128)
        self.assertEqual(result.policy_version, "policy-001")


if __name__ == "__main__":
    unittest.main()
