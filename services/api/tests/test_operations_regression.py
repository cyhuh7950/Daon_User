from __future__ import annotations

import unittest

from daon_user_api.operations_regression import OperationsState, OperationsError


class OperationsRegressionTests(unittest.TestCase):
    def test_expiry_and_degraded_dependencies_are_explicit(self) -> None:
        state = OperationsState()
        self.assertEqual(state.source_expired(), "source_expired")
        self.assertEqual(state.index_outage(), "retrieval_degraded")
        self.assertEqual(state.model_outage(), "model_unavailable")

    def test_evidence_blocked_prevents_grounded_result(self) -> None:
        with self.assertRaisesRegex(OperationsError, "EVIDENCE_BLOCKED"):
            OperationsState().grounded_result(evidence_available=False)

    def test_reconnect_recovers_pending_operations(self) -> None:
        state = OperationsState()
        self.assertEqual(state.disconnect(), "recovery_pending")
        self.assertEqual(state.reconnect(), "recovered")


if __name__ == "__main__":
    unittest.main()
