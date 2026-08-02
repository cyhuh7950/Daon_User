from __future__ import annotations

import unittest

from daon_user_api.model_routing_expansion import ExpandedModelRouter, RoutingFailure


class ExpandedModelRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.candidates = [
            {"deployment_id": "local-v", "role": "vision", "realm": "local", "cost": 0.1, "available": True},
            {"deployment_id": "cloud-v", "role": "vision", "realm": "external", "cost": 1.0, "available": True},
            {"deployment_id": "text-1", "role": "text", "realm": "internal", "cost": 0.2, "available": True},
        ]

    def test_auto_fallback_stays_within_requested_role(self) -> None:
        result = ExpandedModelRouter().select("vision", "auto", self.candidates, budget=2.0)
        self.assertEqual(result.deployment_id, "local-v")
        self.assertEqual(result.attempted_roles, ("vision",))

    def test_local_only_and_pinned_enforce_policy(self) -> None:
        router = ExpandedModelRouter()
        self.assertEqual(router.select("vision", "local_only", self.candidates, budget=2.0).deployment_id, "local-v")
        self.assertEqual(router.select("vision", "pinned", self.candidates, budget=2.0, pinned_id="cloud-v").deployment_id, "cloud-v")
        with self.assertRaisesRegex(RoutingFailure, "PINNED_DEPLOYMENT_REQUIRED"):
            router.select("vision", "pinned", self.candidates, budget=2.0)

    def test_cost_limit_and_waiting_model_are_distinct(self) -> None:
        router = ExpandedModelRouter()
        with self.assertRaisesRegex(RoutingFailure, "COST_LIMIT_EXCEEDED"):
            router.select("vision", "auto", self.candidates, budget=0.01)
        unavailable = [{**self.candidates[0], "available": False}]
        with self.assertRaisesRegex(RoutingFailure, "WAITING_MODEL"):
            router.select("vision", "auto", unavailable, budget=2.0)


if __name__ == "__main__":
    unittest.main()
